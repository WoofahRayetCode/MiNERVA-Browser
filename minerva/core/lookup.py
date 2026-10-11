"""Turn a browse row into something the download queue can start.

For each selected file the app needs the file's collection torrent and its index inside
that torrent.  This module does that work off the UI thread and reports problems as
:class:`LookupFailure` so a bulk selection can show *one* summary instead of one modal
dialog per failed file.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Callable, Protocol

from minerva.constants import get_default_trackers, log_activity, log_error
from minerva.core.hashes_db import HashesDbError
from minerva.core.http import HttpError
from minerva.core.torrent_cache import TorrentCache, TorrentInvalid, names_match

KIND_TITLES = {
    "lookup": "Lookup Failed",
    "not_found": "Not Found",
    "no_torrent": "No Torrent",
    "torrent_fetch": "Torrent Download Failed",
    "index_mismatch": "Torrent Mismatch",
    "unexpected": "Download Failed",
}


def failure_text(kind: str, message: str) -> str:
    """What the downloads list shows for a file that could not be queued: "<what failed>: <why>"."""
    if kind == "not_found":
        return "Not found on the server"
    title = KIND_TITLES.get(kind, "Download Failed")
    return f"{title}: {message}" if message else title


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
    browse_path: str = ""  # archive folder it was queued from (lets updates be found later)


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


OUTAGE_MESSAGE = (
    "minerva-archive.org is not answering requests for ROM details right now (timed out). "
    "Retry in a few minutes."
)

_URL_TAIL = re.compile(r"\s*(?:for\s+)?\(?https?://[^\s)]+\)?\s*$")


def describe_error(error: BaseException) -> str:
    """An error's message without the trailing URL ('... timed out (https://host/rom?id=1)'), which only
    makes a list of failures unreadable."""
    return _URL_TAIL.sub("", str(error)).strip() or type(error).__name__


class RomDatabase(Protocol):
    """What the resolver needs from ``hashes.db``: the ``files`` row with this id, or None."""

    def lookup_id(self, rowid: int) -> dict | None: ...


class _Breaker:
    """Stops asking a source that keeps failing.

    ``threshold`` consecutive failures open it; for ``cooldown`` seconds callers skip the source and fail
    immediately instead of each waiting out its own timeout.  One probe is let through afterwards, and any
    success closes it again.
    """

    def __init__(self, threshold: int, cooldown: float, clock: Callable[[], float]):
        self._threshold = threshold
        self._cooldown = cooldown
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0

    def blocked(self) -> bool:
        with self._lock:
            return self._clock() < self._open_until

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._open_until = self._clock() + self._cooldown
                self._failures = self._threshold - 1  # one failed probe re-opens it

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0


class RomResolver:
    """Resolve a browse row to a verified torrent source.

    A row (collection torrent, index inside it, magnet) is looked up in this order:

    1. rows already fetched this session;
    2. ``hashes.db`` (``rom_db``), which is answered from a static file and keeps working when the
       archive's ROM page does not.  Its row is only used when its ``file_name`` matches the listing's
       name, so a stale snapshot can never queue the wrong file;
    3. the archive's ROM page (``fetch_rom``).

    ``outage_threshold`` consecutive failures of either source open a circuit breaker for
    ``outage_cooldown`` seconds so a bulk queue fails fast instead of waiting out every timeout.
    """

    def __init__(
        self,
        torrent_cache: TorrentCache,
        fetch_rom: Callable[[str], dict | None],
        *,
        rom_db: RomDatabase | None = None,
        cache_size: int = 2048,
        outage_threshold: int = 3,
        outage_cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._cache = torrent_cache
        self._fetch_rom = fetch_rom
        self._db = rom_db
        self._cache_size = cache_size
        self._page_breaker = _Breaker(outage_threshold, outage_cooldown, clock)
        self._db_breaker = _Breaker(outage_threshold, outage_cooldown, clock)
        self._rows: OrderedDict[str, tuple[dict, str]] = OrderedDict()  # rom id -> (row, "db" | "page")
        self._lock = threading.Lock()

    # -- finding the row ------------------------------------------------------------------------
    def row_for(self, rom_id: str, file_name: str | None = None) -> dict:
        return self._lookup(rom_id, file_name)[0]

    def _lookup(self, rom_id: str, file_name: str | None) -> tuple[dict, str]:
        with self._lock:
            hit = self._rows.get(rom_id)
            if hit is not None:
                self._rows.move_to_end(rom_id)
                return hit
        notes: list[str] = []
        row = self._row_from_db(rom_id, file_name, notes)
        source = "db"
        if row is None:
            row, source = self._row_from_page(rom_id, notes), "page"
        self._remember(rom_id, row, source)
        return row, source

    def _remember(self, rom_id: str, row: dict, source: str) -> None:
        with self._lock:
            self._rows[rom_id] = (row, source)
            self._rows.move_to_end(rom_id)
            while len(self._rows) > self._cache_size:
                self._rows.popitem(last=False)

    def _row_from_db(self, rom_id: str, file_name: str | None, notes: list[str]) -> dict | None:
        if self._db is None:
            return None
        try:
            key = int(rom_id)
        except (TypeError, ValueError):
            return None
        if self._db_breaker.blocked():
            notes.append("file database unavailable")
            return None
        try:
            row = self._db.lookup_id(key)
        except HashesDbError as e:
            self._db_breaker.failure()
            log_activity(f"lookup: file database failed for id {rom_id}: {e}")
            notes.append(f"file database: {describe_error(e)}")
            return None
        except Exception as e:  # a bug in the reader must never stop a download from being queued
            self._db_breaker.failure()
            log_error(f"RomResolver file database lookup crashed for id {rom_id}", e)
            notes.append("file database: unexpected error")
            return None
        self._db_breaker.success()
        if row is None:
            notes.append("not in the file database")
            return None
        if file_name is not None and not names_match(str(row.get("file_name") or ""), file_name):
            log_activity(f"lookup: file database lists {row.get('file_name')!r} for id {rom_id}, not {file_name!r}")
            notes.append("the file database lists a different file for this id")
            return None
        if row.get("so_id") is None or not (row.get("torrents") or row.get("magnet")):
            notes.append("the file database entry has no torrent information")
            return None
        return row

    def _row_from_page(self, rom_id: str, notes: list[str]) -> dict:
        if self._page_breaker.blocked():
            raise LookupFailure("lookup", _with_notes(OUTAGE_MESSAGE, notes))
        try:
            row = self._fetch_rom(rom_id)
        except Exception as e:  # HttpError after retries, parse errors, ...
            if getattr(e, "retryable", False):
                self._page_breaker.failure()
            raise LookupFailure("lookup", _with_notes(describe_error(e), notes)) from e
        self._page_breaker.success()
        if row is None:
            raise LookupFailure("not_found", "was not found on the server")
        return row

    # -- from row to torrent --------------------------------------------------------------------
    def resolve(self, rom_id: str, file_name: str) -> ResolvedDownload:
        row, source = self._lookup(rom_id, file_name)
        return self._resolve_row(rom_id, row, file_name, may_ask_page=(source == "db"))

    def _resolve_row(self, rom_id: str, row: dict, file_name: str, *, may_ask_page: bool) -> ResolvedDownload:
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
                if may_ask_page:
                    fresh = self._newer_row_from_page(rom_id, row, e)
                    if fresh is not None:
                        return self._resolve_row(rom_id, fresh, file_name, may_ask_page=False)
                if magnet:
                    return ResolvedDownload(magnet, so_id, full_path)
                raise LookupFailure("torrent_fetch", describe_error(e)) from e
        if magnet:
            return ResolvedDownload(magnet, so_id, full_path)
        raise LookupFailure("no_torrent", "no torrent information is available")

    def _newer_row_from_page(self, rom_id: str, row: dict, error: Exception) -> dict | None:
        """The ROM page's row, when the database snapshot points at a torrent the archive no longer serves."""
        if not (isinstance(error, HttpError) and error.status in (404, 410)):
            return None
        try:
            fresh = self._row_from_page(rom_id, [])
        except LookupFailure:
            return None  # the page is down too; the caller falls back to the magnet link
        if not fresh.get("torrents") or fresh.get("torrents") == row.get("torrents"):
            return None
        self._remember(rom_id, fresh, "page")
        return fresh


def _with_notes(message: str, notes: list[str]) -> str:
    return f"{message} ({'; '.join(notes)})" if notes else message


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
            lines.append(f"…and {len(errors) - max_examples} more.")
        lines += ["", "They are listed under Errors in the downloads list; use Retry once the archive answers."]
        return "Some downloads could not be queued", "\n".join(lines)
