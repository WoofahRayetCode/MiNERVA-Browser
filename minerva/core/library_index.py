"""Match keys for files already on disk, maintained off the UI thread.

The browse list shows a check mark next to titles that are already downloaded.  Building
that key set needs two ``rglob`` passes over the download folder, which used to run on the
Tk thread and was thrown away (and re-run) every time a download finished.  This index
scans in a worker thread, swaps the finished result in atomically, and lets finished
downloads be added incrementally instead of triggering a rescan.
"""
from __future__ import annotations

import pathlib
import threading
from typing import Callable, Iterable

from minerva.constants import log_error
from minerva.core.extractors import collect_library_match_keys, library_keys_for_name


class LibraryIndex:
    def __init__(
        self,
        scan_fn: Callable[[pathlib.Path], Iterable[str]] = collect_library_match_keys,
        key_fn: Callable[[str], Iterable[str]] = library_keys_for_name,
    ):
        self._scan_fn = scan_fn
        self._key_fn = key_fn
        self._lock = threading.Lock()
        self._keys: frozenset[str] = frozenset()
        self._root: str | None = None
        self._generation = 0
        self._added_since_scan: set[str] = set()
        self._scanning = False

    @property
    def keys(self) -> frozenset[str]:
        """Current keys.  Immutable, so callers can hold and compare it without a lock."""
        return self._keys

    @property
    def scanning(self) -> bool:
        return self._scanning

    @property
    def root(self) -> str | None:
        return self._root

    def ensure(self, root: str, on_change: Callable[[], None] | None = None) -> frozenset[str]:
        """Return the current keys, starting a background scan the first time ``root`` is seen."""
        with self._lock:
            needs_scan = root != self._root
        if needs_scan:
            self.rescan(root, on_change)
        return self._keys

    def rescan(self, root: str, on_change: Callable[[], None] | None = None) -> None:
        """Rebuild the index for ``root`` in a worker thread; a newer rescan supersedes this one."""
        with self._lock:
            if root != self._root:
                self._keys = frozenset()  # keys for another folder would be misleading
            self._root = root
            self._generation += 1
            generation = self._generation
            self._added_since_scan = set()
            self._scanning = True

        def work():
            try:
                scanned = frozenset(self._scan_fn(pathlib.Path(root)))
            except Exception as e:
                log_error(f"LibraryIndex scan failed for {root}", e)
                scanned = None
            with self._lock:
                if generation != self._generation:
                    return  # superseded by a newer rescan; let that one publish
                self._scanning = False
                if scanned is None:
                    return
                self._keys = scanned | self._added_since_scan
            if on_change is not None:
                try:
                    on_change()
                except Exception as e:
                    log_error("LibraryIndex on_change callback failed", e)

        threading.Thread(target=work, daemon=True, name="library-index-scan").start()

    def add_names(self, names: Iterable[str]) -> bool:
        """Add finished files without rescanning; returns True when the key set grew."""
        new_keys: set[str] = set()
        for name in names:
            if name:
                new_keys |= set(self._key_fn(name))
        if not new_keys:
            return False
        with self._lock:
            self._added_since_scan |= new_keys
            if new_keys <= self._keys:
                return False
            self._keys = self._keys | new_keys
            return True
