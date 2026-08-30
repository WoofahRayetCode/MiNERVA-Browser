"""Locate Redump PS3 disc-key (.dkey) zips that match queued PS3 ISOs."""

from __future__ import annotations

import pathlib
import re
import threading
import time
import urllib.parse
import zipfile
from minerva.constants import log_activity, log_error
from minerva.core.sqlite_http import fetch_entries, extract_rom_id
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


def find_dkey_entry(
    file_name: str,
    *,
    force_refresh: bool = False,
    title_ids: list[str] | None = None,
) -> dict | None:
    """Match a ROM to the Disc Keys TXT catalog by original name, serial, or cleaned title."""
    zip_name = dkey_zip_name_for_rom(file_name)
    if not zip_name and not title_ids:
        return None
    index = _load_catalog(force_refresh=force_refresh)
    if zip_name:
        exact = index.get(_normalize_key(zip_name))
        if exact:
            return exact
        serial = serial_from_name(zip_name)
        if serial:
            hits = _cache_by_serial.get(serial) or []
            if len(hits) == 1:
                return hits[0]
        cleaned = _cleaned_key(zip_name)
        cleaned_hits = list(_cache_by_cleaned.get(cleaned) or [])
        if serial:
            serial_hits = [h for h in cleaned_hits if serial_from_name(h.get("name") or "") == serial]
            if len(serial_hits) == 1:
                return serial_hits[0]
            if len(_cache_by_serial.get(serial) or []) == 1:
                return _cache_by_serial[serial][0]
        if len(cleaned_hits) == 1:
            return cleaned_hits[0]
    for title_id in title_ids or []:
        hits = _cache_by_serial.get(title_id.upper()) or []
        if len(hits) == 1:
            return hits[0]
        if zip_name:
            cleaned = _cleaned_key(zip_name)
            narrowed = [h for h in hits if _cleaned_key(h.get("name") or "") == cleaned]
            if len(narrowed) == 1:
                return narrowed[0]
    return None


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
    parts = [p.lower() for p in pathlib.Path(save_path).parts]
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
    if len(cleaned_hits) == 1:
        return cleaned_hits[0]
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
