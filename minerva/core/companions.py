"""Match DLC and updates for a queued base game from nearby archive folders."""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from minerva.core.extractors import normalize_chd_stem
from minerva.constants import log_activity, log_error

KIND_BASE = "base"
KIND_DLC = "dlc"
KIND_UPDATE = "update"
KIND_IGNORE = "ignore"

_CACHE_TTL_SEC = 6 * 60 * 60
_MIN_TITLE_KEY_LEN = 8

_DLC_FLAG = re.compile(r"[\(\[]\s*DLC\s*[\)\]]", re.IGNORECASE)
_UPDATE_FLAG = re.compile(r"[\(\[]\s*Updates?\s*[\)\]]", re.IGNORECASE)
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
    r"\s*[\(\[]\s*(Digital|CDN|PSN|DLC|Updates?|Content|eShop|Deprecated|Pre-Install|"
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
    stem = _SWITCH_TID.sub(" ", stem)
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
    return bool(a & b)


def names_match_companion(base_name: str, candidate_name: str) -> bool:
    if not base_name or not candidate_name:
        return False
    if base_name.strip().lower() == candidate_name.strip().lower():
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

    parent = parent_browse_path(browse_path)
    siblings = load_folder_entries(parent, fetch_fn)
    search_paths = companion_folder_paths(browse_path, siblings)

    found: list[Companion] = []
    seen: set[str] = set()
    for folder_path in search_paths:
        entries = load_folder_entries(
            folder_path,
            fetch_fn,
            current_entries=current_entries,
            current_path=browse_path,
        )
        folder_label = folder_name_from_path(folder_path)
        for entry in entries:
            if entry.get("is_folder"):
                continue
            name = entry.get("name") or ""
            href = entry.get("href") or ""
            kind = classify_release(name, folder_label)
            if kind not in (KIND_DLC, KIND_UPDATE):
                continue
            if not names_match_companion(base_name, name):
                continue
            key = href.lower() or name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(
                Companion(
                    name=name,
                    href=href,
                    size=(entry.get("size") or "").strip(),
                    kind=kind,
                    folder=folder_label,
                    version_key=version_key(name),
                )
            )

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
