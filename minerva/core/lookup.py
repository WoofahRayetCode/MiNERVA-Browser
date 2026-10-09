"""Turn a browse row into something the download queue can start.

For each selected file the app needs the file's collection torrent and its index inside
that torrent.  This module does that work off the UI thread and reports problems as
:class:`LookupFailure` so a bulk selection can show *one* summary instead of one modal
dialog per failed file.
"""
from __future__ import annotations

import threading
import urllib.parse
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Callable

from minerva.constants import get_default_trackers, log_error
from minerva.core.http import HttpError
from minerva.core.torrent_cache import TorrentCache, TorrentInvalid

KIND_TITLES = {
    "lookup": "Lookup Failed",
    "not_found": "Not Found",
    "no_torrent": "No Torrent",
    "torrent_fetch": "Torrent Download Failed",
    "index_mismatch": "Torrent Mismatch",
    "unexpected": "Download Failed",
}


class LookupFailure(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class QueuedDownload:
    """A fully resolved download waiting to be added to the queue by the UI thread."""

    download_id: str
    name: str
    source: str
    so_id: int
    save_path: str


@dataclass(frozen=True)
class ResolvedDownload:
    source: str      # local .torrent path, or a magnet link as a fallback
    so_id: int       # verified file index inside the torrent
    full_path: str


def strip_default_trackers(magnet: str) -> str:
    """Drop the app's built-in tracker list from a magnet link.

    Older versions appended ~2.6 KB of trackers to every magnet that was then persisted for
    every queued item; the engine adds the defaults itself when a torrent starts.
    """
    if not magnet or not magnet.startswith("magnet:") or "&tr=" not in magnet:
        return magnet
    defaults = set(get_default_trackers())
    head, _, query = magnet.partition("?")
    kept = []
    for part in query.split("&"):
        if part.startswith("tr="):
            if urllib.parse.unquote(part[3:]) in defaults:
                continue
        kept.append(part)
    return head + "?" + "&".join(kept) if kept else head


class RomResolver:
    def __init__(
        self,
        torrent_cache: TorrentCache,
        fetch_rom: Callable[[str], dict | None],
        *,
        cache_size: int = 2048,
    ):
        self._cache = torrent_cache
        self._fetch_rom = fetch_rom
        self._cache_size = cache_size
        self._rows: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def row_for(self, rom_id: str) -> dict:
        with self._lock:
            row = self._rows.get(rom_id)
            if row is not None:
                self._rows.move_to_end(rom_id)
                return row
        try:
            row = self._fetch_rom(rom_id)
        except Exception as e:  # HttpError after retries, parse errors, ...
            raise LookupFailure("lookup", str(e)) from e
        if row is None:
            raise LookupFailure("not_found", "was not found on the server")
        with self._lock:
            self._rows[rom_id] = row
            while len(self._rows) > self._cache_size:
                self._rows.popitem(last=False)
        return row

    def resolve(self, rom_id: str, file_name: str) -> ResolvedDownload:
        row = self.row_for(rom_id)
        try:
            so_id = int(row.get("so_id") or 0)
        except (TypeError, ValueError):
            raise LookupFailure("lookup", f"server returned an invalid file index: {row.get('so_id')!r}")
        full_path = row.get("full_path") or file_name
        magnet = strip_default_trackers(row.get("magnet") or "")
        rel = row.get("torrents")
        if rel:
            try:
                path = self._cache.ensure(rel)
                index = self._cache.resolve_index(path, so_id, file_name)
                if index is None:
                    # The cached collection torrent may predate the server's database:
                    # fetch a fresh copy once before deciding the index is wrong.
                    path = self._cache.ensure(rel, refresh=True)
                    index = self._cache.resolve_index(path, so_id, file_name, allow_name_fallback=True)
                if index is None:
                    raise LookupFailure(
                        "index_mismatch",
                        f"file {so_id} of the collection torrent is not '{file_name}'",
                    )
                return ResolvedDownload(str(path), index, full_path)
            except LookupFailure:
                raise
            except (HttpError, TorrentInvalid, OSError, ValueError) as e:
                log_error(f"RomResolver torrent fetch failed for {file_name}", e)
                if magnet:
                    return ResolvedDownload(magnet, so_id, full_path)
                raise LookupFailure("torrent_fetch", str(e)) from e
        if magnet:
            return ResolvedDownload(magnet, so_id, full_path)
        raise LookupFailure("no_torrent", "no torrent information is available")


@dataclass(frozen=True)
class LookupError_:
    kind: str
    name: str
    message: str


class LookupErrors:
    """Thread-safe collector so a bulk queue reports one summary, not N dialogs."""

    def __init__(self, max_keep: int = 500):
        self._lock = threading.Lock()
        self._errors: list[LookupError_] = []
        self._dropped = 0
        self._max_keep = max_keep

    def add(self, kind: str, name: str, message: str) -> None:
        with self._lock:
            if len(self._errors) >= self._max_keep:
                self._dropped += 1
            else:
                self._errors.append(LookupError_(kind, name, message))

    def take(self) -> list[LookupError_]:
        with self._lock:
            errors, dropped = self._errors, self._dropped
            self._errors, self._dropped = [], 0
        if dropped:
            errors.append(LookupError_("unexpected", f"{dropped} more", "additional failures not listed"))
        return errors

    @staticmethod
    def format(errors: list[LookupError_], max_examples: int = 5) -> tuple[str, str]:
        """Return ``(title, body)`` for a message box."""
        if len(errors) == 1:
            e = errors[0]
            title = KIND_TITLES.get(e.kind, "Download Failed")
            if e.kind == "not_found":
                return title, f"{e.name} {e.message}."
            if e.kind in ("lookup", "torrent_fetch"):
                return title, f"Could not {'look up' if e.kind == 'lookup' else 'download the torrent for'} {e.name}:\n{e.message}"
            return title, f"{e.name}: {e.message}"
        counts = Counter(KIND_TITLES.get(e.kind, e.kind) for e in errors)
        lines = [f"{len(errors)} files could not be queued:"]
        lines += [f"  • {label}: {n}" for label, n in counts.most_common()]
        lines.append("")
        for e in errors[:max_examples]:
            lines.append(f"{e.name} — {e.message.splitlines()[0][:120]}")
        if len(errors) > max_examples:
            lines.append(f"…and {len(errors) - max_examples} more (see minerva_error.log).")
        return "Some downloads could not be queued", "\n".join(lines)
