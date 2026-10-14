"""Match DLC and updates for a queued base game from nearby archive folders."""

from __future__ import annotations

import os
import pathlib
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from minerva.core.extractors import normalize_chd_stem
from minerva.core.entries import detect_release_tags
from minerva.constants import log_activity, log_error

KIND_BASE = "base"
KIND_DLC = "dlc"
KIND_UPDATE = "update"
KIND_IGNORE = "ignore"

_CACHE_TTL_SEC = 6 * 60 * 60
_MIN_TITLE_KEY_LEN = 8

_DLC_FLAG = re.compile(r"[\(\[]\s*DLC\s*[\)\]]", re.IGNORECASE)
_UPDATE_FLAG = re.compile(r"[\(\[]\s*(?:Title\s+)?(?:Updates?|Patch(?:es)?)\s*[\)\]]", re.IGNORECASE)
_MEDIA_FLAG = re.compile(
    r"[\(\[]\s*(?:Disc|Disk|Digital|PSN|Cartridge|eShop)\s*[\)\]]",
    re.IGNORECASE,
)
_HEX_ID = re.compile(r"[\(\[]\s*[0-9A-Fa-f]{8}\s*[\)\]]")  # e.g. Xbox 360 title-update hash "(1F887267)"
_BIOS_FLAG = re.compile(r"\[BIOS\]|\(System\s+(Application|Module|Firmware|Data Archive)", re.IGNORECASE)
_SWITCH_TID = re.compile(r"\b(01[0-9A-Fa-f]{14})\b")
_PS_SERIAL = re.compile(
    r"\b((?:B[CL][AEUJK][A-Z]|NP[UEJ][AB]|ULUS|ULES|ULJS|UCUS|UCES|NPUH|NPEH|NPJH)"
    r"\s*-?\s*\d{5})\b",
    re.IGNORECASE,
)
_VERSION_BRACKET = re.compile(r"\[v(\d+)\]", re.IGNORECASE)
_VERSION_PAREN = re.compile(r"\(\s*v(\d+(?:\.\d+)*)\s*\)", re.IGNORECASE)
_FOLDER_STRIP = re.compile(
    r"\s*[\(\[]\s*(Digital|CDN|PSN|DLC|Title\s+Updates?|Updates?|Content|eShop|Deprecated|Pre-Install|Decrypted|Encrypted|"
    r"SpotPass|Dev(?:elopment)?(?:\s+ROMs)?|Lotcheck)[^)\]]*[\)\]]",
    re.IGNORECASE,
)
_COMPANION_FOLDER_HINTS = (
    "dlc", "update", "updates", "digital", "psn", "cdn", "eshop", "content",
)

_cache_lock = threading.Lock()
_listing_cache: dict[str, tuple[float, list[dict]]] = {}


@dataclass
class Companion:
    name: str
    href: str
    size: str
    kind: str
    folder: str
    version_key: tuple = field(default_factory=tuple)

    @property
    def rom_id(self) -> str | None:
        from minerva.core.sqlite_http import extract_rom_id
        return extract_rom_id(self.href)


def classify_release(name: str, folder: str = "") -> str:
    """Return base, dlc, update, or ignore."""
    n = name or ""
    if _BIOS_FLAG.search(n):
        return KIND_IGNORE
    if _DLC_FLAG.search(n):
        return KIND_DLC
    if _UPDATE_FLAG.search(n):
        return KIND_UPDATE
    tid = switch_title_id(n)
    if tid:
        last = int(tid[-4:], 16)
        if last & 0x800:
            return KIND_UPDATE
        if last & 0x1000:
            return KIND_DLC
    folder_l = (folder or "").lower()
    if re.search(r"\bdlc\b", folder_l) and not re.search(r"\bupdates?\b", folder_l):
        return KIND_DLC
    if re.search(r"\bupdates?\b", folder_l) and not re.search(r"\bdlc\b", folder_l):
        return KIND_UPDATE
    return KIND_BASE


def title_key(name: str) -> str:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = _DLC_FLAG.sub(" ", stem)
    stem = _UPDATE_FLAG.sub(" ", stem)
    stem = _HEX_ID.sub(" ", stem)
    stem = _SWITCH_TID.sub(" ", stem)
    stem = _MEDIA_FLAG.sub(" ", stem)
    cleaned = normalize_chd_stem(stem) or stem
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def switch_title_id(name: str) -> str | None:
    match = _SWITCH_TID.search(name or "")
    return match.group(1).upper() if match else None


def serials_from_name(name: str) -> list[str]:
    found: list[str] = []
    for match in _PS_SERIAL.finditer(name or ""):
        compact = re.sub(r"[\s-]", "", match.group(1)).upper()
        if compact not in found:
            found.append(compact)
    tid = switch_title_id(name)
    if tid:
        found.append(tid)
    return found


def switch_ids_related(left: str, right: str) -> bool:
    a = (left or "").upper()
    b = (right or "").upper()
    if len(a) != 16 or len(b) != 16:
        return False
    return a[:12] == b[:12]


def version_key(name: str) -> tuple:
    """Higher tuple sorts as newer. Prefer [vN] title-version, then (v1.04)."""
    bracket = _VERSION_BRACKET.search(name or "")
    if bracket:
        return (1, int(bracket.group(1)))
    paren = _VERSION_PAREN.search(name or "")
    if paren:
        parts = tuple(int(p) for p in paren.group(1).split("."))
        return (0,) + parts
    return (0, 0)


def regions_from_name(name: str) -> set[str]:
    low = (name or "").lower()
    regions: set[str] = set()
    pairs = (
        ("usa", ("(usa", "(us)", "(u)")),
        ("europe", ("(europe", "(eu)", "(e)")),
        ("japan", ("(japan", "(jp)", "(j)")),
        ("world", ("(world", "(w)", "(global")),
        ("asia", ("(asia",)),
        ("korea", ("(korea", "(kr)")),
        ("china", ("(china", "(cn)")),
        ("australia", ("(australia", "(au)")),
        ("canada", ("(canada", "(ca)")),
        ("brazil", ("(brazil", "(br)")),
        ("france", ("(france",)),
        ("germany", ("(germany",)),
        ("italy", ("(italy",)),
        ("spain", ("(spain",)),
    )
    for key, needles in pairs:
        if any(n in low for n in needles):
            regions.add(key)
    return regions


def regions_compatible(left: str, right: str) -> bool:
    a = regions_from_name(left)
    b = regions_from_name(right)
    if not a or not b:
        return True
    if "world" in a or "world" in b:
        return True  # a (World) release is the same build for every region
    return bool(a & b)


_TEST_TAGS = frozenset({"beta", "demo", "proto"})


def release_tags_compatible(base_name: str, candidate_name: str) -> bool:
    """Pre-release test updates (beta/demo/proto) should not attach to stable retail base games."""
    base_tags = detect_release_tags(base_name.lower())
    cand_tags = detect_release_tags(candidate_name.lower())
    if (cand_tags & _TEST_TAGS) and not (base_tags & cand_tags & _TEST_TAGS):
        return False
    return True


def names_match_companion(base_name: str, candidate_name: str) -> bool:
    if not base_name or not candidate_name:
        return False
    if base_name.strip().lower() == candidate_name.strip().lower():
        return False
    if not release_tags_compatible(base_name, candidate_name):
        return False
    base_ids = serials_from_name(base_name)
    cand_ids = serials_from_name(candidate_name)
    switch_base = [i for i in base_ids if len(i) == 16 and i.startswith("01")]
    switch_cand = [i for i in cand_ids if len(i) == 16 and i.startswith("01")]
    if switch_base and switch_cand:
        if any(switch_ids_related(a, b) for a in switch_base for b in switch_cand):
            return regions_compatible(base_name, candidate_name)
        return False
    ps_base = [i for i in base_ids if len(i) != 16]
    ps_cand = [i for i in cand_ids if len(i) != 16]
    if ps_base and ps_cand:
        if set(ps_base) & set(ps_cand):
            return regions_compatible(base_name, candidate_name)
        # PSN DLC often uses a different NPUB id; fall through to title.
    key_a = title_key(base_name)
    key_b = title_key(candidate_name)
    if not key_a or not key_b:
        return False
    if key_a != key_b:
        return False
    flagged = bool(_DLC_FLAG.search(candidate_name) or _UPDATE_FLAG.search(candidate_name))
    min_len = 4 if flagged else _MIN_TITLE_KEY_LEN
    if len(key_a) < min_len and not (ps_base or switch_base):
        return False
    return regions_compatible(base_name, candidate_name)


def system_key(folder_name: str) -> str:
    s = urllib.parse.unquote(folder_name or "")
    s = s.replace("\\", "/").rstrip("/")
    s = s.split("/")[-1]
    s = re.sub(r"^\s*unofficial\s*-\s*", "", s, flags=re.IGNORECASE)  # No-Intro "Unofficial - Xbox 360 (Title Updates)"
    prev = None
    while prev != s:
        prev = s
        s = _FOLDER_STRIP.sub("", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def folder_looks_companion(folder_name: str) -> bool:
    low = urllib.parse.unquote(folder_name or "").lower()
    return any(hint in low for hint in _COMPANION_FOLDER_HINTS)


def parent_browse_path(path: str) -> str:
    raw = (path or "").replace("\\", "/").rstrip("/")
    if not raw:
        return "/browse/"
    parent = raw.rsplit("/", 1)[0]
    if parent in {"", "/browse", "/browse/."}:
        return "/browse/"
    return parent + "/"


def folder_name_from_path(path: str) -> str:
    raw = urllib.parse.unquote((path or "").replace("\\", "/").rstrip("/"))
    return raw.split("/")[-1]


KNOWN_SYSTEM_BROWSE_PATHS: dict[str, str] = {
    "Sony - PlayStation 3": "/browse/./Redump/Sony - PlayStation 3/",
    "Nintendo - Nintendo Switch": "/browse/./No-Intro/Nintendo - Nintendo Switch/",
    "Sony - PlayStation Vita": "/browse/./No-Intro/Sony - PlayStation Vita/",
    "Nintendo - Nintendo 3DS": "/browse/./No-Intro/Nintendo - Nintendo 3DS/",
    "Nintendo - Nintendo Wii U": "/browse/./No-Intro/Nintendo - Nintendo Wii U/",
    "Microsoft - Xbox 360": "/browse/./Redump/Microsoft - Xbox 360/",
    "Sony - PlayStation 4": "/browse/./Redump/Sony - PlayStation 4/",
    "Sony - PlayStation": "/browse/./Redump/Sony - PlayStation/",
    "Sony - PlayStation 2": "/browse/./Redump/Sony - PlayStation 2/",
    "Sony - PlayStation Portable": "/browse/./Redump/Sony - PlayStation Portable/",
}

_PS3_SERIAL_RE = re.compile(r"\bB[CL][AEUJK][A-Z]\s*-?\s*\d{5}\b", re.IGNORECASE)
_PSVITA_SERIAL_RE = re.compile(r"\b(PCSE|PCSB|PCSG|PCSF|PCSH|PCSC|PCSA)\s*-?\s*\d{5}\b", re.IGNORECASE)
_PS4_SERIAL_RE = re.compile(r"\b(CUSA|CUSE)\s*-?\s*\d{5}\b", re.IGNORECASE)
_PS1_SERIAL_RE = re.compile(r"\b(SLUS|SLES|SCES|SCUS)\s*-?\s*0\d{4}\b", re.IGNORECASE)
_PS2_SERIAL_RE = re.compile(r"\b(SLUS|SLES|SCES|SCUS)\s*-?\s*[25]\d{4}\b", re.IGNORECASE)


def detect_system_browse_path(path_or_name: str | os.PathLike) -> str | None:
    """Infer the collection browse path for a local file name or path."""
    text = str(path_or_name)
    name = pathlib.Path(text).name
    lower_full = text.lower()

    # 1. Serial and Title ID detection
    if _PS3_SERIAL_RE.search(name):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 3"]
    if _PSVITA_SERIAL_RE.search(name):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation Vita"]
    if _PS4_SERIAL_RE.search(name):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 4"]
    tid = switch_title_id(name)
    if tid:
        return KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Switch"]
    if _PS1_SERIAL_RE.search(name):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation"]
    if _PS2_SERIAL_RE.search(name):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 2"]

    # 2. File extension detection
    ext = pathlib.Path(name).suffix.lower()
    if ext in (".nsp", ".xci"):
        return KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Switch"]
    if ext == ".vpk":
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation Vita"]
    if ext in (".cia", ".3ds"):
        return KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo 3DS"]
    if ext in (".wux", ".rpx"):
        return KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Wii U"]
    if ext == ".xex" or name.lower().endswith(".xiso.iso"):
        return KNOWN_SYSTEM_BROWSE_PATHS["Microsoft - Xbox 360"]

    # 3. Path / name console keywords
    if re.search(r"\b(playstation\s*3|ps3)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 3"]
    if re.search(r"\b(playstation\s*vita|ps\s*vita|psvita)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation Vita"]
    if re.search(r"\b(switch|nintendo\s*switch)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Switch"]
    if re.search(r"\b(wii\s*u|wiiu)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Wii U"]
    if re.search(r"\b(xbox\s*360|x360)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Microsoft - Xbox 360"]
    if re.search(r"\b(3ds|nintendo\s*3ds)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo 3DS"]
    if re.search(r"\b(playstation\s*4|ps4)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 4"]
    if re.search(r"\b(playstation\s*2|ps2)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 2"]
    if re.search(r"\b(psp|playstation\s*portable)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation Portable"]
    if re.search(r"\b(ps1|psx|playstation)\b", lower_full):
        return KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation"]

    return None



def companion_folder_paths(browse_path: str, sibling_folders: list[dict]) -> list[str]:
    """Current folder plus sibling collections that look like DLC/digital/update sets."""
    current = (browse_path or "").replace("\\", "/")
    if current and not current.endswith("/"):
        current += "/"
    paths = [current] if current else []
    key = system_key(folder_name_from_path(browse_path))
    if not key:
        return paths
    for entry in sibling_folders:
        if not entry.get("is_folder"):
            continue
        href = entry.get("href") or ""
        name = entry.get("name") or href
        if system_key(name) != key:
            continue
        if href.rstrip("/") == current.rstrip("/"):
            continue
        if not folder_looks_companion(name) and not folder_looks_companion(href):
            continue
        if href not in paths:
            paths.append(href)
    return paths


def _cache_get(path: str) -> list[dict] | None:
    with _cache_lock:
        hit = _listing_cache.get(path)
        if not hit:
            return None
        fetched_at, entries = hit
        if time.time() - fetched_at > _CACHE_TTL_SEC:
            _listing_cache.pop(path, None)
            return None
        return list(entries)


def _cache_put(path: str, entries: list[dict]) -> None:
    with _cache_lock:
        _listing_cache[path] = (time.time(), list(entries))


def reset_companion_cache() -> None:
    with _cache_lock:
        _listing_cache.clear()
    with _index_lock:
        _index_cache.clear()


def load_folder_entries(path: str, fetch_fn, current_entries: list[dict] | None = None,
                        current_path: str = "") -> list[dict]:
    norm = (path or "").replace("\\", "/")
    cur = (current_path or "").replace("\\", "/")
    if current_entries is not None and norm.rstrip("/") == cur.rstrip("/"):
        return list(current_entries)
    cached = _cache_get(norm)
    if cached is not None:
        return cached
    try:
        entries = fetch_fn(path)
    except Exception as e:
        log_error(f"companion listing failed for {path}", e)
        return []
    _cache_put(norm, entries)
    return entries


#: Collections whose sibling folders may hold update/DLC sets for a base game that lives in another
#: collection (PS3 base games are in Redump; their updates are in No-Intro "(PSN) (Updates)").
COLLECTION_ROOTS = ("/browse/./No-Intro/", "/browse/./Redump/")


def _path_key(path: str) -> str:
    return urllib.parse.unquote((path or "").replace("\\", "/")).rstrip("/").lower()


def discover_companion_paths(browse_path: str, fetch_fn) -> list[str]:
    """Folders to search for DLC/updates: sibling collections here *and* in the other collections."""
    current = (browse_path or "").replace("\\", "/")
    if current and not current.endswith("/"):
        current += "/"
    paths: list[str] = [current] if current else []
    seen = {_path_key(current)}
    parent = parent_browse_path(browse_path)
    roots = [parent]
    roots += [r for r in COLLECTION_ROOTS if _path_key(r) != _path_key(parent)]
    for root in roots:
        for path in companion_folder_paths(browse_path, load_folder_entries(root, fetch_fn)):
            key = _path_key(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
    return paths


class CompanionIndex:
    """DLC/update entries of one folder, indexed so a lookup does not rescan the folder.

    Building costs one regex pass over the listing (the PS3 updates folder is >10 MB); after that,
    finding the companions of a base game is a few dictionary lookups.
    """

    def __init__(self) -> None:
        self.by_title: dict[str, list[Companion]] = {}
        self.by_serial: dict[str, list[Companion]] = {}
        self.by_switch: dict[str, list[Companion]] = {}
        self.size = 0

    @classmethod
    def build(cls, entries: list[dict], folder_label: str) -> "CompanionIndex":
        index = cls()
        for entry in entries:
            if entry.get("is_folder"):
                continue
            name = entry.get("name") or ""
            kind = classify_release(name, folder_label)
            if kind not in (KIND_DLC, KIND_UPDATE):
                continue
            companion = Companion(
                name=name,
                href=entry.get("href") or "",
                size=(entry.get("size") or "").strip(),
                kind=kind,
                folder=folder_label,
                version_key=version_key(name),
            )
            index.size += 1
            key = title_key(name)
            if key:
                index.by_title.setdefault(key, []).append(companion)
            for ident in serials_from_name(name):
                if len(ident) == 16 and ident.startswith("01"):
                    index.by_switch.setdefault(ident[:12], []).append(companion)
                elif len(ident) != 16:
                    index.by_serial.setdefault(ident, []).append(companion)
        return index

    def candidates(self, base_name: str) -> list[Companion]:
        """Entries that *could* belong to ``base_name`` (callers confirm with names_match_companion)."""
        found: list[Companion] = []
        seen: set[int] = set()

        def add(items):
            for item in items or ():
                if id(item) not in seen:
                    seen.add(id(item))
                    found.append(item)

        add(self.by_title.get(title_key(base_name)))
        for ident in serials_from_name(base_name):
            if len(ident) == 16 and ident.startswith("01"):
                add(self.by_switch.get(ident[:12]))
            elif len(ident) != 16:
                add(self.by_serial.get(ident))
        return found


_index_lock = threading.Lock()
_index_cache: dict[str, tuple[float, CompanionIndex]] = {}


def get_companion_index(folder_path: str, fetch_fn, *, current_entries: list[dict] | None = None,
                        current_path: str = "") -> CompanionIndex:
    key = _path_key(folder_path)
    live = current_entries is not None and key == _path_key(current_path)
    if not live:
        with _index_lock:
            hit = _index_cache.get(key)
            if hit and time.time() - hit[0] <= _CACHE_TTL_SEC:
                return hit[1]
    entries = load_folder_entries(folder_path, fetch_fn, current_entries=current_entries, current_path=current_path)
    index = CompanionIndex.build(entries, folder_name_from_path(folder_path))
    if not live:
        with _index_lock:
            _index_cache[key] = (time.time(), index)
    return index


def find_companions(
    base_name: str,
    browse_path: str,
    *,
    current_entries: list[dict] | None = None,
    fetch_fn=None,
    latest_update_only: bool = True,
) -> list[Companion]:
    """Return DLC/update files that belong with base_name."""
    if classify_release(base_name, folder_name_from_path(browse_path)) != KIND_BASE:
        return []
    if fetch_fn is None:
        from minerva.core.sqlite_http import fetch_entries
        fetch_fn = fetch_entries

    found: list[Companion] = []
    seen: set[str] = set()
    for folder_path in discover_companion_paths(browse_path, fetch_fn):
        index = get_companion_index(
            folder_path, fetch_fn, current_entries=current_entries, current_path=browse_path
        )
        for companion in index.candidates(base_name):
            key = companion.href.lower() or companion.name.lower()
            if key in seen or not names_match_companion(base_name, companion.name):
                continue
            seen.add(key)
            found.append(companion)

    if latest_update_only:
        found = _keep_latest_updates(found)
    found.sort(key=lambda c: (0 if c.kind == KIND_UPDATE else 1, c.name.lower()))
    if found:
        log_activity(
            f"companions.found base='{base_name}' dlc={sum(1 for c in found if c.kind == KIND_DLC)} "
            f"updates={sum(1 for c in found if c.kind == KIND_UPDATE)}"
        )
    return found


def _keep_latest_updates(items: list[Companion]) -> list[Companion]:
    latest: dict[str, Companion] = {}
    kept: list[Companion] = []
    for item in items:
        if item.kind != KIND_UPDATE:
            kept.append(item)
            continue
        key = title_key(item.name) or item.name.lower()
        prev = latest.get(key)
        if prev is None or item.version_key >= prev.version_key:
            latest[key] = item
    kept.extend(latest.values())
    return kept
