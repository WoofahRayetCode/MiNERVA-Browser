"""Filename and path safety for names that come from the network."""
from __future__ import annotations

import re
from pathlib import Path

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_leaf_name(name: str, fallback: str = "download") -> str:
    """A single path component that is safe to create on Windows and POSIX."""
    leaf = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    leaf = _INVALID.sub("_", leaf).strip().rstrip(". ")
    if leaf in ("", ".", ".."):
        return fallback
    if leaf.split(".")[0].upper() in _RESERVED:
        leaf = "_" + leaf
    return leaf[:200]


def is_safe_leaf_name(name: str) -> bool:
    """True when ``name`` can be used as-is as a file name (no separators, traversal, reserved names)."""
    return bool(name) and safe_leaf_name(name) == name


def is_within(base: Path, candidate: Path) -> bool:
    """True when ``candidate`` resolves to ``base`` or somewhere inside it."""
    try:
        Path(candidate).resolve().relative_to(Path(base).resolve())
        return True
    except (ValueError, OSError):
        return False
