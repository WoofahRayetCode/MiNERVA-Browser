"""Per-entry data for the browse list, computed once per directory listing.

The browse list used to recompute release tags, regions, parsed sizes and library-match keys
for every entry on every keystroke, on the Tk thread.  :func:`enrich_entries` does it once, in
the worker thread that fetched the listing, and the UI reads the cached fields.
"""
from __future__ import annotations

import re

from minerva.core.extractors import library_keys_for_name


def parse_size_bytes(size_str: str) -> int:
    """Convert human readable size string (e.g. '1.5 GB', '250 MB') to bytes."""
    if not isinstance(size_str, str) or not size_str.strip():
        return 0
    s = size_str.strip().upper()
    try:
        parts = s.split()
        if len(parts) == 2:
            num = float(parts[0])
            unit = parts[1]
            multipliers = {
                "B": 1,
                "KB": 1024,
                "MB": 1024**2,
                "GB": 1024**3,
                "TB": 1024**4,
                "KIB": 1024,
                "MIB": 1024**2,
                "GIB": 1024**3,
            }
            return int(num * multipliers.get(unit, 1))
        return int(float(s))
    except Exception:
        return 0


_TRAILING = re.compile(r"[\s\-_]([a-z]+)(?:\.[a-z0-9]{1,5})?$")
_GROUP_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_SPLIT_RE = re.compile(r"\s*(?:,|/|\+|&|\band\b)\s*")
_WORD_RE = re.compile(r"[a-z0-9+]+")


def _groups(name_lower: str) -> list[str]:
    return [g.strip() for g in _GROUP_RE.findall(name_lower)]


def detect_release_tags(name_lower: str) -> set[str]:
    """Release flavours (demo, beta, ...) from the (...) / [...] groups of a file name.

    Matching is word-based: the previous substring checks tagged "Demolition Man" as a demo
    and "(Review)" as a revision.
    """
    tags: set[str] = set()
    for group in _groups(name_lower):
        words = _WORD_RE.findall(group)
        if not words:
            continue
        first = words[0]
        if "demo" in words:
            tags.add("demo")
        if "beta" in words:
            tags.add("beta")
        if first in ("rev", "revision"):
            tags.add("revision")
        if any(w.startswith("proto") for w in words):
            tags.add("proto")
        if "unl" in words or "unlicensed" in words:
            tags.add("unlicensed")
        if any(w.startswith("hack") for w in words):
            tags.add("hack")
        if first == "translation" or group.startswith(("t+", "t-")):
            tags.add("translation")
    # Un-bracketed hints such as "Game Demo.zip" or "Game - Beta.zip": only a trailing word, so
    # titles like "The Beta Fish" or "Demolition Man" are left alone.
    if not tags:
        for word, tag in (("demo", "demo"), ("beta", "beta"), ("prototype", "proto"), ("unlicensed", "unlicensed")):
            if _TRAILING.search(name_lower) and _TRAILING.search(name_lower).group(1) == word:
                tags.add(tag)
    return tags


# Full names are unambiguous anywhere in a (...) group; short codes are only trusted when they
# are the whole group, because "(En,Fr,De)" is a language list, not France and Germany.
_REGION_NAMES = {
    "usa": "usa", "europe": "europe", "japan": "japan", "world": "world", "global": "world",
    "asia": "asia", "korea": "korea", "china": "china", "australia": "australia",
    "canada": "canada", "brazil": "brazil", "france": "france", "germany": "germany",
    "italy": "italy", "spain": "spain", "netherlands": "netherlands", "sweden": "sweden",
    "russia": "russia", "taiwan": "taiwan", "hong kong": "hong_kong",
}
_REGION_CODES = {
    "us": "usa", "eu": "europe", "jp": "japan", "kr": "korea", "cn": "china",
    "au": "australia", "ca": "canada", "br": "brazil", "fr": "france", "de": "germany",
    "it": "italy", "es": "spain", "nl": "netherlands", "se": "sweden", "sw": "sweden",
    "ru": "russia", "tw": "taiwan", "hk": "hong_kong",
}
_REGION_LETTERS = {
    "u": "usa", "e": "europe", "j": "japan", "w": "world", "a": "asia", "k": "korea",
    "c": "china", "f": "france", "g": "germany", "i": "italy", "s": "spain",
}
_LETTER_COMBOS = {"ue", "uj", "uw", "ej", "ew", "jw", "uej", "uew", "ujw", "ejw", "uejw"}


def detect_regions(name_lower: str) -> set[str]:
    """Regions named in a file name's (...) groups, e.g. ``(USA, Europe)`` or ``(UE)``."""
    regions: set[str] = set()
    for group in _groups(name_lower):
        tokens = [t for t in _SPLIT_RE.split(group) if t]
        for token in tokens:
            region = _REGION_NAMES.get(token)
            if region:
                regions.add(region)
        if len(tokens) == 1:
            token = tokens[0]
            region = _REGION_CODES.get(token) or _REGION_LETTERS.get(token)
            if region:
                regions.add(region)
            elif token in _LETTER_COMBOS:
                regions.update(_REGION_LETTERS[ch] for ch in token)
    # A trailing region word outside brackets, e.g. "Game - USA.zip" (principal regions only).
    if not regions:
        trailing = _TRAILING.search(name_lower)
        if trailing and trailing.group(1) in ("usa", "europe", "japan", "world"):
            regions.add(trailing.group(1))
    if not regions:
        regions.add("other")
    return regions


def enrich_entries(entries: list[dict]) -> list[dict]:
    """Add ``lname``, ``tags``, ``regions``, ``size_bytes`` and ``keys`` to every entry (in place)."""
    empty: frozenset = frozenset()
    for entry in entries:
        name = entry.get("name", "")
        low = name.lower()
        entry["lname"] = low
        if entry.get("is_folder", False):
            entry["tags"] = entry["regions"] = entry["keys"] = empty
            entry["size_bytes"] = 0
            continue
        entry["tags"] = frozenset(detect_release_tags(low))
        entry["regions"] = frozenset(detect_regions(low))
        entry["size_bytes"] = parse_size_bytes(entry.get("size", ""))
        entry["keys"] = library_keys_for_name(name)
    return entries
