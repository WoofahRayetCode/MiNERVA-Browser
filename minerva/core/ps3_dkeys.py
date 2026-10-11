"""Locate Redump PS3 disc-key (.dkey) zips that match queued PS3 ISOs."""

from __future__ import annotations

import pathlib
import re
import shutil
import threading
import time
import urllib.parse
import zipfile
from dataclasses import dataclass
from minerva.constants import log_activity, log_error
from minerva.core.sqlite_http import fetch_entries, extract_rom_id
from minerva.core.entries import detect_regions
from minerva.core.extractors import is_archive_path, normalize_chd_stem

PS3_DISC_KEYS_TXT_PATH = "/browse/./Redump/Sony%20-%20PlayStation%203%20-%20Disc%20Keys%20TXT/"
_CACHE_TTL_SEC = 6 * 60 * 60

_cache_lock = threading.Lock()
_cache_entries: list[dict] | None = None
_cache_index: dict[str, dict] | None = None
_cache_by_serial: dict[str, list[dict]] = {}
_cache_by_cleaned: dict[str, list[dict]] = {}
_cache_fetched_at = 0.0

_SERIAL_RE = re.compile(
    r"\b(B[CL][AEUJK][A-Z])\s*-?\s*(\d{5})\b",
    re.IGNORECASE,
)
_TITLE_ID_BYTES_RE = re.compile(rb"B[CL][AEUJK][A-Z]\d{5}")


def is_ps3_iso_browse_path(path: str | None) -> bool:
    """True when the current browse folder is Redump PS3 ISOs (not disc keys)."""
    if not path:
        return False
    decoded = urllib.parse.unquote(path).replace("\\", "/").lower()
    if "disc keys" in decoded:
        return False
    if "psn" in decoded:
        return False
    return "playstation 3" in decoded


def dkey_zip_name_for_rom(file_name: str) -> str:
    """Disc Keys TXT listings use the same zip stem as the ISO zip."""
    name = (file_name or "").strip()
    if not name:
        return ""
    lower = name.lower()
    if lower.endswith(".iso"):
        return name[:-4] + ".zip"
    return name


def _normalize_key(name: str) -> str:
    return urllib.parse.unquote(name).strip().lower()


def serial_from_name(name: str) -> str | None:
    """Return a compact TITLE_ID like BLUS30808 from a Redump-style filename."""
    match = _SERIAL_RE.search(name or "")
    if not match:
        return None
    return (match.group(1) + match.group(2)).upper()


def _cleaned_key(name: str) -> str:
    stem = pathlib.Path(dkey_zip_name_for_rom(name) or name).stem
    return _normalize_key(normalize_chd_stem(stem) or stem)


def extract_title_ids_from_bytes(data: bytes) -> list[str]:
    """Find PS3 TITLE_IDs in ISO/SFB/SFO bytes (works on many encrypted Redump headers)."""
    found: list[str] = []
    seen: set[str] = set()

    def _add_from(chunk: bytes):
        for match in _TITLE_ID_BYTES_RE.finditer(chunk):
            value = match.group(0).decode("ascii", errors="ignore").upper()
            if value not in seen:
                seen.add(value)
                found.append(value)

    marker = data.find(b"PlayStation3")
    if marker >= 0:
        _add_from(data[max(0, marker - 96) : marker + 160])
    title_id_key = data.find(b"TITLE_ID")
    if title_id_key >= 0:
        _add_from(data[title_id_key : title_id_key + 96])
    if not found:
        _add_from(data[: min(len(data), 512 * 1024)])
    return found


def extract_title_ids_from_path(path: pathlib.Path) -> list[str]:
    try:
        suffix = path.suffix.lower()
        if suffix == ".zip" and zipfile.is_zipfile(path):
            try:
                with zipfile.ZipFile(path, "r") as zf:
                    for info in zf.infolist():
                        inner = info.filename.replace("\\", "/").upper()
                        if inner.endswith("PS3_DISC.SFB") or inner.endswith("PARAM.SFO"):
                            with zf.open(info) as handle:
                                ids = extract_title_ids_from_bytes(handle.read(65536))
                            if ids:
                                return ids
            except zipfile.BadZipFile:
                pass
        with path.open("rb") as handle:
            header = handle.read(2 * 1024 * 1024)
        return extract_title_ids_from_bytes(header)
    except OSError as e:
        log_error(f"extract_title_ids_from_path failed for {path}", e)
        return []
    except Exception as e:
        log_activity(f"extract_title_ids_from_path skipped '{path.name}' err={e}")
        return []


def _build_index(entries: list[dict]) -> dict[str, dict]:
    global _cache_by_serial, _cache_by_cleaned
    index: dict[str, dict] = {}
    by_serial: dict[str, list[dict]] = {}
    by_cleaned: dict[str, list[dict]] = {}
    for entry in entries:
        if entry.get("is_folder"):
            continue
        name = entry.get("name") or ""
        href = entry.get("href") or ""
        if not name or not extract_rom_id(href):
            continue
        index[_normalize_key(name)] = entry
        serial = serial_from_name(name)
        if serial:
            by_serial.setdefault(serial, []).append(entry)
        cleaned = _cleaned_key(name)
        if cleaned:
            by_cleaned.setdefault(cleaned, []).append(entry)
    _cache_by_serial = by_serial
    _cache_by_cleaned = by_cleaned
    return index


def _load_catalog(force_refresh: bool = False) -> dict[str, dict]:
    global _cache_entries, _cache_index, _cache_fetched_at
    now = time.monotonic()
    with _cache_lock:
        if (
            not force_refresh
            and _cache_index is not None
            and (now - _cache_fetched_at) < _CACHE_TTL_SEC
        ):
            return _cache_index
    try:
        entries = fetch_entries(PS3_DISC_KEYS_TXT_PATH)
    except Exception as e:
        log_error("ps3_dkeys catalog fetch failed", e)
        with _cache_lock:
            return _cache_index or {}
    index = _build_index(entries)
    with _cache_lock:
        _cache_entries = entries
        _cache_index = index
        _cache_fetched_at = time.monotonic()
    log_activity(f"ps3_dkeys.catalog loaded {len(index)} keys")
    return index


_VERSION_RE = re.compile(r"\bv(\d{1,2}\.\d{2})\b", re.IGNORECASE)
_EDITIONS = ("playstation 3 the best", "fukikaeban", "jimakuban", "greatest hits", "platinum")
_ambiguous_lock = threading.Lock()
_ambiguous: dict[str, list[str]] = {}


@dataclass(frozen=True)
class DkeyResolution:
    entry: dict | None
    #: Catalog names that tied for best when no single entry could be chosen (never guessed).
    ambiguous: tuple[str, ...] = ()


def _loose_key(name: str) -> str:
    """Case/punctuation-insensitive identity: 'DiRT 2.zip' == 'Dirt-2.zip'."""
    stem = re.sub(r"\.zip$", "", urllib.parse.unquote(name or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", " ", stem.lower()).strip()


def _edition_tags(name: str) -> frozenset[str]:
    low = (name or "").lower()
    return frozenset(e for e in _EDITIONS if e in low)


def _score_candidate(query_name: str, query_ids: set[str], candidate_name: str) -> int | None:
    """Higher is a better match; ``None`` means the candidate contradicts the ROM and is dropped."""
    score = 0
    cand_serial = serial_from_name(candidate_name)
    query_serial = serial_from_name(query_name)
    if cand_serial:
        known = set(query_ids) | ({query_serial} if query_serial else set())
        if known:
            if cand_serial in known:
                score += 100
            else:
                return None  # its serial belongs to another release
    q_ver, c_ver = _VERSION_RE.search(query_name or ""), _VERSION_RE.search(candidate_name)
    if q_ver and c_ver:
        if q_ver.group(1) != c_ver.group(1):
            return None
        score += 20
    q_reg = detect_regions((query_name or "").lower())
    c_reg = detect_regions(candidate_name.lower())
    if q_reg != {"other"} and c_reg != {"other"}:
        if q_reg.isdisjoint(c_reg):
            return None
        score += 10 if q_reg == c_reg else 5
    q_ed, c_ed = _edition_tags(query_name), _edition_tags(candidate_name)
    score += 5 if q_ed == c_ed else -3
    return score


def resolve_dkey_entry(
    file_name: str,
    *,
    force_refresh: bool = False,
    title_ids: list[str] | None = None,
) -> DkeyResolution:
    """Pick the catalog entry for a ROM, or report that several are equally plausible.

    An exact name wins.  Otherwise every entry sharing the ROM's serial or cleaned title is a
    candidate; those that contradict the ROM (other serial, other version, other region) are
    dropped, and the rest are ranked.  A winner must beat the runner-up outright.
    """
    zip_name = dkey_zip_name_for_rom(file_name)
    ids = {t.upper() for t in (title_ids or [])}
    if not zip_name and not ids:
        return DkeyResolution(None)
    index = _load_catalog(force_refresh=force_refresh)
    if zip_name:
        exact = index.get(_normalize_key(zip_name))
        if exact:
            return DkeyResolution(exact)
    pool: dict[int, dict] = {}
    for serial in ids | ({serial_from_name(zip_name)} if zip_name else set()):
        if serial:
            for entry in _cache_by_serial.get(serial, []):
                pool[id(entry)] = entry
    if zip_name:
        for entry in _cache_by_cleaned.get(_cleaned_key(zip_name), []):
            pool[id(entry)] = entry
    by_loose: dict[str, tuple[int, dict]] = {}  # same release spelled two ways counts once
    for entry in sorted(pool.values(), key=lambda e: e.get("name") or ""):
        name = entry.get("name") or ""
        score = _score_candidate(zip_name or "", ids, name)
        if score is None:
            continue
        key = _loose_key(name)
        if key not in by_loose or score > by_loose[key][0]:
            by_loose[key] = (score, entry)
    ranked = sorted(by_loose.values(), key=lambda t: t[0], reverse=True)
    if not ranked:
        return DkeyResolution(None)
    if len(ranked) == 1 or ranked[0][0] > ranked[1][0]:
        return DkeyResolution(ranked[0][1])
    tied = tuple(e.get("name") or "" for sc, e in ranked if sc == ranked[0][0])
    return DkeyResolution(None, tied)


def find_dkey_entry(
    file_name: str,
    *,
    force_refresh: bool = False,
    title_ids: list[str] | None = None,
) -> dict | None:
    """Match a ROM to the Disc Keys TXT catalog (see :func:`resolve_dkey_entry`)."""
    result = resolve_dkey_entry(file_name, force_refresh=force_refresh, title_ids=title_ids)
    if result.ambiguous:
        with _ambiguous_lock:
            _ambiguous[file_name] = list(result.ambiguous)
        log_activity(f"ps3_dkeys.ambiguous file='{file_name}' candidates={list(result.ambiguous)}")
    return result.entry


def pop_ambiguous() -> dict[str, list[str]]:
    """ROMs that matched several equally plausible keys since the last call (and clear the list)."""
    with _ambiguous_lock:
        found = dict(_ambiguous)
        _ambiguous.clear()
    return found


def find_dkey_entry_for_path(path: pathlib.Path, *, force_refresh: bool = False) -> dict | None:
    """Match a file on disk, including cleaned names, via filename then TITLE_ID in the image."""
    entry = find_dkey_entry(path.name, force_refresh=force_refresh)
    if entry:
        return entry
    title_ids = extract_title_ids_from_path(path)
    if not title_ids:
        return None
    return find_dkey_entry(path.name, title_ids=title_ids)


def reset_dkey_catalog_cache():
    global _cache_entries, _cache_index, _cache_by_serial, _cache_by_cleaned, _cache_fetched_at
    with _cache_lock:
        _cache_entries = None
        _cache_index = None
        _cache_by_serial = {}
        _cache_by_cleaned = {}
        _cache_fetched_at = 0.0


DKEY_DIR_NAME = "dkeys"
DKEY_ZIP_MAX_BYTES = 8192
PS3_ROM_MIN_BYTES = 1024 * 1024
_DKEY_HEX = re.compile(r"^[0-9A-Fa-f]{32}$")


def get_dkey_save_dir(download_dir: str | pathlib.Path) -> pathlib.Path:
    d = pathlib.Path(download_dir) / DKEY_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def is_dkey_save_path(save_path: str | pathlib.Path | None) -> bool:
    if not save_path:
        return False
    # Split on both separators so a Windows-style path saved in settings still
    # matches when evaluated on Linux (pathlib only honours the host separator).
    parts = [p.lower() for p in re.split(r"[\\/]+", str(save_path)) if p]
    return DKEY_DIR_NAME in parts


def dkey_stems_for_rom(file_name: str) -> list[str]:
    zip_name = dkey_zip_name_for_rom(file_name)
    if not zip_name:
        return []
    stem = pathlib.Path(zip_name).stem
    cleaned = normalize_chd_stem(stem) or stem
    stems: list[str] = []
    for value in (stem, cleaned):
        if value and value not in stems:
            stems.append(value)
    return stems


def is_valid_dkey_file(path: pathlib.Path) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 32 or size > 256:
        return False
    try:
        text = path.read_text(encoding="ascii", errors="ignore").strip()
    except OSError:
        return False
    return bool(_DKEY_HEX.fullmatch(text))


def find_local_dkey(download_dir: pathlib.Path, rom_name: str) -> pathlib.Path | None:
    """Return a valid sidecar .dkey/.key for this ROM if one exists on disk."""
    stems = {s.lower() for s in dkey_stems_for_rom(rom_name)}
    entry = find_dkey_entry(rom_name)
    if entry and entry.get("name"):
        stems.update(s.lower() for s in dkey_stems_for_rom(entry["name"]))
    rom_cleaned = _cleaned_key(rom_name)
    rom_serial = serial_from_name(rom_name) or (serial_from_name(entry["name"]) if entry else None)
    if not stems and not rom_cleaned:
        return None
    roots = [download_dir, download_dir / DKEY_DIR_NAME, download_dir / "extracted"]
    cleaned_hits: list[pathlib.Path] = []
    for root in roots:
        if not root.exists():
            continue
        key_files: list[pathlib.Path] = []
        for pattern in ("*.dkey", "*.key"):
            key_files.extend(root.rglob(pattern))
        for path in key_files:
            if not path.is_file():
                continue
            if not is_valid_dkey_file(path):
                continue
            stem_l = path.stem.lower()
            if stem_l in stems:
                return path
            key_serial = serial_from_name(path.stem)
            if rom_serial and key_serial == rom_serial:
                return path
            if rom_cleaned and _cleaned_key(path.name) == rom_cleaned:
                cleaned_hits.append(path)
    unique_hits = list({h.resolve(): h for h in cleaned_hits}.values())  # roots overlap (dkeys/ is inside)
    if len(unique_hits) == 1:
        return unique_hits[0]
    return None


def find_local_dkey_zip(download_dir: pathlib.Path, rom_name: str) -> pathlib.Path | None:
    names: list[str] = []
    entry = find_dkey_entry(rom_name)
    if entry and entry.get("name"):
        names.append(entry["name"])
    zip_name = dkey_zip_name_for_rom(rom_name)
    if zip_name:
        names.append(zip_name)
    cleaned = normalize_chd_stem(pathlib.Path(rom_name).stem)
    if cleaned:
        names.append(cleaned + ".zip")
    dkey_dir = download_dir / DKEY_DIR_NAME
    seen: set[str] = set()
    for name in names:
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        candidate = dkey_dir / name
        if candidate.is_file():
            return candidate
    return None


def collect_local_ps3_rom_names(download_dir: pathlib.Path) -> list[str]:
    """ROM archives/ISOs that map to the Disc Keys TXT catalog (including cleaned names)."""
    if not download_dir.exists():
        return []
    names: list[str] = []
    seen: set[str] = set()
    catalog = _load_catalog()
    if not catalog:
        return []
    skip_dirs = {DKEY_DIR_NAME, "torrentfiles", "tools", "build", "dist"}
    scan_suffixes = {".iso", ".zip", ".7z", ".rar"}
    for path in download_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(download_dir)
        except ValueError:
            continue
        if any(part.lower() in skip_dirs for part in rel.parts[:-1]):
            continue
        if path.suffix.lower() not in scan_suffixes and not is_archive_path(path):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < PS3_ROM_MIN_BYTES and path.suffix.lower() != ".iso":
            continue
        entry = find_dkey_entry_for_path(path)
        if entry is None:
            continue
        catalog_name = entry.get("name") or ""
        key = _normalize_key(catalog_name)
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(catalog_name)
    return sorted(names)


KEY_MATCH, KEY_MISMATCH, KEY_UNKNOWN = "match", "mismatch", "unknown"


def key_matches_iso(iso_path: pathlib.Path, key_path: pathlib.Path) -> str:
    """Decide whether a disc key belongs to an ISO, using identity data only.

    A key found by title alone ignores region, so a USA key can end up beside a Europe ISO.
    Checks, strongest first: the ISO's embedded TITLE_ID vs the serial in the key's name (when it
    has one), the serial in the ISO's name, and region tags in both names.  Returns ``match``,
    ``mismatch`` or ``unknown`` (not enough information either way).
    """
    iso_path, key_path = pathlib.Path(iso_path), pathlib.Path(key_path)
    key_serial = serial_from_name(key_path.stem)
    if key_serial:
        iso_serial = serial_from_name(iso_path.name)
        if iso_serial and iso_serial != key_serial:
            return KEY_MISMATCH
        embedded = extract_title_ids_from_path(iso_path)
        if embedded and key_serial not in embedded:
            return KEY_MISMATCH
    iso_regions = detect_regions(iso_path.stem.lower())
    key_regions = detect_regions(key_path.stem.lower())
    if iso_regions != {"other"} and key_regions != {"other"} and iso_regions.isdisjoint(key_regions):
        return KEY_MISMATCH
    if key_serial or key_path.stem.lower() == iso_path.stem.lower():
        return KEY_MATCH
    return KEY_UNKNOWN


def place_dkeys_beside_isos(download_dir: str | pathlib.Path) -> dict[str, int]:
    """Copy each ISO's matching disc key next to it as ``<iso name>.dkey``.

    RPCS3 loads an encrypted ISO's key from a sidecar file with the ISO's own base name, so a key
    sitting in ``dkeys/`` under the Redump zip name is not picked up.  The copy is never destructive:
    a valid key that already sits next to the ISO is left alone (even if it differs), and the
    original in ``dkeys/`` is kept.  A key that :func:`key_matches_iso` rejects is not placed.
    Returns counts: placed / present / no_key / mismatch.
    """
    base = pathlib.Path(download_dir)
    counts = {"placed": 0, "present": 0, "no_key": 0, "mismatch": 0}
    if not base.exists():
        return counts
    skip_dirs = {DKEY_DIR_NAME, "torrentfiles", "tools", "build", "dist"}
    for iso in sorted(base.rglob("*.iso")):
        try:
            rel = iso.relative_to(base)
        except ValueError:
            continue
        if not iso.is_file() or any(part.lower() in skip_dirs for part in rel.parts[:-1]):
            continue
        target = iso.with_suffix(".dkey")
        if target.is_file() and is_valid_dkey_file(target):
            if key_matches_iso(iso, target) == KEY_MISMATCH:
                counts["mismatch"] += 1  # the user's own sidecar key: report it, never touch it
                log_activity(f"ps3_dkeys.sidecar_mismatch iso='{iso.name}'")
            else:
                counts["present"] += 1
            continue
        key = find_local_dkey(base, iso.name)
        if key is None or key.resolve() == target.resolve():
            counts["no_key"] += 1
            continue
        if key_matches_iso(iso, key) == KEY_MISMATCH:
            counts["mismatch"] += 1
            log_activity(f"ps3_dkeys.mismatch iso='{iso.name}' key='{key.name}' (not placed)")
            continue
        tmp = target.with_name(target.name + ".part")
        try:
            shutil.copyfile(key, tmp)
            tmp.replace(target)
            counts["placed"] += 1
            log_activity(f"ps3_dkeys.placed iso='{iso.name}' key='{key.name}'")
        except OSError as e:
            log_error(f"place_dkeys_beside_isos failed for {iso}", e)
            try:
                tmp.unlink()
            except OSError:
                pass
    return counts
