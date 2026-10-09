"""Single-flight, validated cache of collection ``.torrent`` files.

Queueing N files from one collection used to download the (often multi-megabyte) collection
torrent N times: the cache key included a hash of each *file's* path.  Here the key is the
collection torrent's own path, concurrent requests share one fetch, every file is validated
before it is trusted, and downloads are written atomically so a crash can never leave a
truncated ``.torrent`` that looks valid forever.
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
import threading
import time
import unicodedata
import urllib.parse
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import libtorrent as lt
except ImportError:  # downloads are disabled without libtorrent; structure checks only
    lt = None

from minerva.constants import BASE_URL, log_activity, log_error
from minerva.core.http import SITE_GATE, RateGate, atomic_write_bytes, get_bytes

ASSETS_URL = BASE_URL.rstrip("/") + "/assets/"
MAX_TORRENT_BYTES = 256 * 1024 * 1024
REFRESH_COOLDOWN = 600.0  # a collection torrent fetched this recently is not fetched again
_STALE_PART_SECONDS = 3600
_INVALID_NAME_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


class TorrentInvalid(ValueError):
    """The bytes are not a usable ``.torrent`` (HTML error page, truncated download, ...)."""


@dataclass(frozen=True)
class TorrentSummary:
    num_files: int
    #: Base name of every file in torrent order, or ``None`` when libtorrent is unavailable.
    names: tuple[str, ...] | None


def torrent_file_storage(ti):
    """File layout of a ``torrent_info`` without the deprecated ``files()`` on libtorrent 2.1+."""
    layout = getattr(ti, "layout", None)
    return layout() if callable(layout) else ti.files()


def parse_torrent_bytes(data: bytes) -> TorrentSummary:
    """Validate ``data`` as a torrent and return its file names; raises :class:`TorrentInvalid`."""
    if not data or data[:1] != b"d" or b"4:info" not in data:
        raise TorrentInvalid("not a bencoded torrent")
    if lt is None:
        return TorrentSummary(num_files=-1, names=None)
    try:
        try:
            ti = lt.torrent_info(data, {"max_decode_depth": 100, "max_decode_tokens": 5_000_000})
        except TypeError:  # older bindings without the limits argument
            ti = lt.torrent_info(lt.bdecode(data))
    except Exception as e:
        raise TorrentInvalid(f"libtorrent rejected the torrent: {e}") from e
    count = ti.num_files()
    if count <= 0:
        raise TorrentInvalid("torrent contains no files")
    storage = torrent_file_storage(ti)
    names = tuple(os.path.basename(storage.file_path(i)) for i in range(count))
    return TorrentSummary(num_files=count, names=names)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class TorrentCache:
    def __init__(
        self,
        cache_dir: Path,
        *,
        base_url: str = ASSETS_URL,
        fetch: Callable[[str], bytes] | None = None,
        gate: RateGate | None = None,
        parse: Callable[[bytes], TorrentSummary] = parse_torrent_bytes,
        memo_size: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url
        self._gate = gate if gate is not None else SITE_GATE
        self._fetch = fetch or self._default_fetch
        self._parse = parse
        self._memo_size = memo_size
        self._clock = clock
        self._fetched_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, bool], Future] = {}
        self._memo: OrderedDict[Path, tuple[tuple[int, int], TorrentSummary]] = OrderedDict()
        self._remove_stale_parts()

    # -- naming ---------------------------------------------------------------------------------
    @staticmethod
    def _flat(rel_path: str) -> str:
        return rel_path.replace("/", "_").replace("\\", "_")

    def path_for(self, rel_path: str) -> Path:
        safe = _INVALID_NAME_CHARS.sub("_", self._flat(rel_path))[:150]
        digest = hashlib.sha1(rel_path.encode("utf-8", errors="ignore")).hexdigest()[:8]
        return self.cache_dir / f"{safe}__{digest}.torrent"

    # -- public API -----------------------------------------------------------------------------
    def ensure(self, rel_path: str, *, refresh: bool = False) -> Path:
        """Return the validated local path for ``rel_path``, fetching it at most once at a time.

        ``refresh=True`` discards the cached copy first (use when the file index the server
        reported does not match the cached torrent, i.e. the collection was updated).
        """
        key = (rel_path, bool(refresh))
        with self._lock:
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[key] = future
        if not owner:
            return future.result()
        try:
            path = self._ensure_uncached(rel_path, refresh)
        except BaseException as e:
            future.set_exception(e)
            raise
        else:
            future.set_result(path)
            return path
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def summary(self, path: Path) -> TorrentSummary:
        """Parsed file list for a cached torrent (memoised per file stamp)."""
        path = Path(path)
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = self._memo.get(path)
            if hit is not None and hit[0] == stamp:
                self._memo.move_to_end(path)
                return hit[1]
        summary = self._parse(path.read_bytes())
        self._remember(path, stamp, summary)
        return summary

    def resolve_index(
        self, path: Path, so_id: int, file_name: str, *, allow_name_fallback: bool = False
    ) -> int | None:
        """Confirm ``so_id`` really is ``file_name`` in this torrent.

        Returns the verified index, or ``None`` on mismatch.  A mismatch usually means the
        cached torrent is older than the server's database; callers should ``ensure(...,
        refresh=True)`` once and retry, only then passing ``allow_name_fallback=True`` to accept
        a unique name match at a different index.
        """
        summary = self.summary(path)
        names = summary.names
        if names is None:
            return so_id if so_id >= 0 else None  # cannot verify without libtorrent
        wanted = _nfc(file_name)
        if 0 <= so_id < len(names) and _nfc(names[so_id]) == wanted:
            return so_id
        if allow_name_fallback:
            matches = [i for i, n in enumerate(names) if _nfc(n) == wanted]
            if len(matches) == 1:
                return matches[0]
        return None

    # -- internals ------------------------------------------------------------------------------
    def _default_fetch(self, url: str) -> bytes:
        return get_bytes(url, timeout=30, retries=4, max_bytes=MAX_TORRENT_BYTES, gate=self._gate)

    def _remember(self, path: Path, stamp: tuple[int, int], summary: TorrentSummary) -> None:
        with self._lock:
            self._memo[path] = (stamp, summary)
            self._memo.move_to_end(path)
            while len(self._memo) > self._memo_size:
                self._memo.popitem(last=False)

    def _evict(self, path: Path) -> None:
        with self._lock:
            self._memo.pop(path, None)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log_error(f"TorrentCache could not remove {path}", e)

    def _is_valid(self, path: Path) -> bool:
        try:
            self.summary(path)
            return True
        except (TorrentInvalid, OSError) as e:
            log_activity(f"torrent_cache.corrupt path='{path.name}' err={e}")
            return False

    def _ensure_uncached(self, rel_path: str, refresh: bool) -> Path:
        dest = self.path_for(rel_path)
        if refresh:
            fetched = self._fetched_at.get(rel_path)
            if fetched is not None and self._clock() - fetched < REFRESH_COOLDOWN and self._is_valid(dest):
                # We downloaded this torrent moments ago, so a mismatch is the server's index
                # being wrong, not our copy being stale; fetching the same bytes again (up to
                # 256 MB, once per queued file) would only hammer the server.
                return dest
            # The cached copy stays in place until a fresh one has downloaded and validated.
        elif dest.exists():
            if self._is_valid(dest):
                return dest
            self._evict(dest)  # truncated / HTML error page cached by an older version
        if not refresh and self._adopt_legacy(rel_path, dest):
            return dest
        url = self._base_url + urllib.parse.quote(rel_path, safe="/")
        data = self._fetch(url)
        holder: list[TorrentSummary] = []

        def validate(blob: bytes) -> None:
            holder.append(self._parse(blob))

        atomic_write_bytes(dest, data, validate)
        self._fetched_at[rel_path] = self._clock()
        st = dest.stat()
        self._remember(dest, (st.st_mtime_ns, st.st_size), holder[0])
        return dest

    def _adopt_legacy(self, rel_path: str, dest: Path) -> bool:
        """Reuse a per-ROM copy written by older versions instead of fetching again."""
        pattern = glob.escape(self._flat(rel_path) + "__") + "*.torrent"
        try:
            candidates = sorted(
                (p for p in self.cache_dir.glob(pattern) if p != dest),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return False
        for legacy in candidates:
            try:
                data = legacy.read_bytes()
                summary = self._parse(data)
                atomic_write_bytes(dest, data)
            except (TorrentInvalid, OSError):
                continue
            st = dest.stat()
            self._remember(dest, (st.st_mtime_ns, st.st_size), summary)
            log_activity(f"torrent_cache.adopted legacy='{legacy.name}' -> '{dest.name}'")
            return True
        return False

    def _remove_stale_parts(self) -> None:
        cutoff = time.time() - _STALE_PART_SECONDS
        try:
            for part in self.cache_dir.glob("*.part"):
                try:
                    if part.stat().st_mtime < cutoff:
                        part.unlink()
                except OSError:
                    pass
        except OSError:
            pass
