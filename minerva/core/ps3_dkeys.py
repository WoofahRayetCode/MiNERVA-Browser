"""Locate Redump PS3 disc-key (.dkey) zips that match queued PS3 ISOs."""

from __future__ import annotations

import threading
import time
import urllib.parse
from minerva.constants import log_activity, log_error
from minerva.core.sqlite_http import fetch_entries, extract_rom_id

PS3_DISC_KEYS_TXT_PATH = "/browse/./Redump/Sony%20-%20PlayStation%203%20-%20Disc%20Keys%20TXT/"
_CACHE_TTL_SEC = 6 * 60 * 60

_cache_lock = threading.Lock()
_cache_entries: list[dict] | None = None
_cache_index: dict[str, dict] | None = None
_cache_fetched_at = 0.0


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


def _build_index(entries: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for entry in entries:
        if entry.get("is_folder"):
            continue
        name = entry.get("name") or ""
        href = entry.get("href") or ""
        if not name or not extract_rom_id(href):
            continue
        index[_normalize_key(name)] = entry
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


def find_dkey_entry(file_name: str, *, force_refresh: bool = False) -> dict | None:
    """Return the Disc Keys TXT listing entry matching a PS3 ISO zip name."""
    zip_name = dkey_zip_name_for_rom(file_name)
    if not zip_name:
        return None
    index = _load_catalog(force_refresh=force_refresh)
    return index.get(_normalize_key(zip_name))


def reset_dkey_catalog_cache():
    global _cache_entries, _cache_index, _cache_fetched_at
    with _cache_lock:
        _cache_entries = None
        _cache_index = None
        _cache_fetched_at = 0.0
