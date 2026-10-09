"""libtorrent engine: one libtorrent torrent per *collection*, many downloads per torrent.

MiNERVA downloads single files out of large collection torrents, so several queued items
usually share one info-hash.  libtorrent returns the *same* handle for a duplicate add, and
the old engine treated every queue item as owning a handle: cancelling or finishing one item
removed the torrent from under its siblings.  Here the unit of ownership is a ``_Group``
(one per info-hash) with ``_Member``s (one per queue item); the torrent is removed only when
its last member leaves.

Threads
  * UI thread        - public API (add/pause/resume/cancel/get_all_statuses); never blocks on libtorrent.
  * add workers      - parse .torrent files / fetch URLs, then attach the member to its group.
  * dispatcher       - the only consumer of libtorrent alerts and the only place that polls
                       ``status()`` / ``file_progress()``.  Publishes an immutable snapshot that
                       the UI reads without touching libtorrent.
"""
from __future__ import annotations

import os
import pathlib
import queue
import shutil
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from minerva.constants import get_default_trackers, log_activity, log_error
from minerva.core.download_queue import DownloadQueue  # noqa: F401  (re-exported for callers/tests)
from minerva.core.http import get_bytes
from minerva.core.lt_settings import (
    build_session_settings,
    compute_alert_mask,
    optimized_session_settings,
)
from minerva.core.pathsafe import is_safe_leaf_name
from minerva.core.resume_store import ResumeStore
from minerva.core.torrent_cache import torrent_file_storage
from minerva.core.workers import DaemonPool

try:
    import libtorrent as lt
    _LT_AVAILABLE = True
except ImportError:
    lt = None
    _LT_AVAILABLE = False

FILE_PRIORITY = 4              # libtorrent priorities are 0 (skip) .. 7; one level is enough here
SCAN_INTERVAL = 0.5            # seconds between status/completion scans
RESUME_INTERVAL = 60.0         # seconds between periodic resume-data saves
REMOVE_WAIT = 10.0             # how long a re-add waits for the previous removal to finish
REMOVE_TIMEOUT = 20.0          # give up waiting for torrent_removed_alert after this long
DISK_RESERVE = 64 * 1024 * 1024
STALL_REANNOUNCE_AFTER = 300.0 # no bytes for this long -> force a tracker/DHT announce
STALL_FAIL_AFTER = 600.0       # ...and still nothing -> fail with a retryable error
METADATA_TIMEOUT = 120.0       # magnet link without metadata for this long -> retryable error
DISK_FULL_ERRNOS = {28, 39, 112}  # ENOSPC, ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL


def _build_torrent_state_map() -> dict:
    if not _LT_AVAILABLE or lt is None:
        return {}
    labels = {
        "checking_files": "Checking",
        "downloading_metadata": "Metadata",
        "downloading": "Downloading",
        "finished": "Seeding",
        "seeding": "Seeding",
        "allocating": "Allocating",
        "checking_resume_data": "Checking",
    }
    state_map = {}
    for attr, label in labels.items():
        value = getattr(lt.torrent_status, attr, None)
        if value is not None:
            state_map[value] = label
    return state_map


_TORRENT_STATE_MAP = _build_torrent_state_map()


def _get_optimized_session_settings() -> dict:
    """Return tuned libtorrent session settings (see :mod:`minerva.core.lt_settings`)."""
    mask = compute_alert_mask(lt) if _LT_AVAILABLE and lt is not None else None
    return optimized_session_settings(mask)


def _prepare_add_params(params, *, defer_download: bool = False):
    """Make a torrent start immediately and stay under DownloadQueue's control.

    libtorrent adds torrents ``paused | auto_managed`` by default.  Auto-managed torrents
    are started and stopped by libtorrent's own queue (and ``pause()`` is not sticky for
    them), which would fight DownloadQueue, so both flags are cleared.  Magnet links also
    get ``default_dont_download`` so nothing is fetched between metadata arrival and the
    per-file priority call.
    """
    flags = lt.torrent_flags
    params.flags &= ~(flags.paused | flags.auto_managed)
    if defer_download:
        dont_download = getattr(flags, "default_dont_download", None)
        if dont_download is not None:
            params.flags |= dont_download
    return params


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class _MemberError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class _Member:
    did: str
    name: str
    so_id: int
    save_path: str
    source: str
    meta: dict
    group: "_Group | None" = None
    file_idx: int = -1
    size: int = 0
    done: int = 0
    paused: bool = False
    cancelled: bool = False
    finished: bool = False
    error: str = ""
    state_override: str = ""
    flat_name: str | None = None
    preexisting: bool = False   # the file was already on disk when we attached: never delete it
    last_done: int = 0
    last_progress_at: float = field(default_factory=time.monotonic)
    stall_stage: int = 0


@dataclass
class _Group:
    info_hash: str
    save_path: str
    handle: object = None
    ti: object = None
    status: object = None
    members: dict = field(default_factory=dict)
    removing: bool = False
    removing_since: float = 0.0
    removed: threading.Event = field(default_factory=threading.Event)
    pending_events: list = field(default_factory=list)
    cleanup_paths: list = field(default_factory=list)
    metadata_since: float = field(default_factory=time.monotonic)
    checked: bool = False
    seeding_since: float = 0.0
    handle_paused: bool = False


class TorrentEngine:
    def __init__(
        self,
        settings_overrides: dict | None = None,
        *,
        state_dir: pathlib.Path | str | None = None,
        default_trackers: list[str] | None = None,
    ):
        if not _LT_AVAILABLE or lt is None:
            raise RuntimeError("libtorrent not available")
        base_settings = _get_optimized_session_settings()
        settings, skipped = build_session_settings(
            lt.default_settings().keys(), base_settings, settings_overrides
        )
        if skipped:
            # Unknown keys used to abort the whole pack and silently leave libtorrent on
            # its defaults; now only the offending keys are dropped.
            log_activity(f"TorrentEngine: ignored unsupported libtorrent settings: {', '.join(skipped)}")
        self._store = ResumeStore(pathlib.Path(state_dir)) if state_dir else None
        if self._store is not None:
            self._store.prune()
        self._session = self._make_session(settings)
        self._default_trackers = list(get_default_trackers() if default_trackers is None else default_trackers)

        self._lock = threading.RLock()
        self._members: dict[str, _Member] = {}
        self._meta: dict[str, dict] = {}
        self._groups: dict[str, _Group] = {}
        self._cleanup: list[dict] = []
        self._torrent_infos: OrderedDict = OrderedDict()
        self._seeding = {"enabled": False, "ratio": 1.0, "hours": 24.0}
        self._snapshot: tuple[int, dict] = (0, {})
        self.events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._final_save_done = threading.Event()
        self._executor = DaemonPool(2, "lt-add")
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="lt-dispatch", daemon=True)
        self._dispatcher.start()

    # -- session ---------------------------------------------------------------------------------
    def _make_session(self, settings: dict):
        params = lt.session_params(settings)
        data = self._store.load_session_state() if self._store is not None else None
        if data:
            try:
                saved = lt.read_session_params(data)
                params.dht_state = saved.dht_state  # only the DHT routing table; settings stay ours
            except Exception as e:
                log_error("TorrentEngine could not restore DHT state", e)
        try:
            return lt.session(params)
        except Exception as e:
            log_error("TorrentEngine.__init__ lt.session(params) failed; using defaults", e)
            return lt.session()

    def set_seeding(self, enabled: bool, ratio: float = 1.0, hours: float = 24.0) -> None:
        """Keep finished torrents alive and seed until ``ratio`` or ``hours`` is reached.

        Off by default.  Never persisted by the engine: after a restart nothing seeds until
        the app turns it back on and the user downloads again.
        """
        with self._lock:
            self._seeding = {"enabled": bool(enabled), "ratio": float(ratio), "hours": float(hours)}

    # -- public API ------------------------------------------------------------------------------
    def add_download(self, torrent_source: str, so_id: int, file_name: str, save_path: str,
                     download_id: str | None = None) -> str:
        download_id = download_id or str(uuid.uuid4())
        resolved = str(pathlib.Path(save_path).resolve())
        meta = {"name": file_name, "so_id": so_id, "save_path": resolved, "delete_archive": False}
        member = _Member(download_id, file_name, int(so_id), resolved, torrent_source, meta)
        with self._lock:
            self._members[download_id] = member
            self._meta[download_id] = meta
        try:
            self._executor.submit(self._add_worker, member)
        except RuntimeError:  # engine already shut down
            self._fail(member, "The download engine is shutting down", retryable=False)
        return download_id

    def get_meta(self, download_id: str) -> dict | None:
        """The live metadata dict for a download (callers may add their own keys)."""
        return self._meta.get(download_id)

    def update_meta(self, download_id: str, **values) -> None:
        with self._lock:
            meta = self._meta.get(download_id)
            if meta is not None:
                meta.update(values)

    def set_auto_extract(self, download_id: str, enabled: bool):
        self.update_meta(download_id, auto_extract=bool(enabled))

    def set_delete_archive(self, download_id: str, enabled: bool):
        self.update_meta(download_id, delete_archive=bool(enabled))

    def get_all_statuses(self) -> dict:
        """Latest per-download status.  Reads a snapshot; never calls into libtorrent."""
        return dict(self._snapshot[1])

    def snapshot(self) -> tuple[int, dict]:
        return self._snapshot

    def aggregate(self) -> dict:
        statuses = self._snapshot[1]
        live = [s for s in statuses.values() if s.get("state") not in ("Finished", "Error")]
        rate = sum(s.get("download_rate", 0) for s in live)
        progress = sum(s.get("progress", 0.0) for s in live) / len(live) if live else 0.0
        return {"active": len(live), "download_rate": rate, "progress": progress}

    def pause(self, download_id: str):
        self._set_paused(download_id, True)

    def resume(self, download_id: str):
        self._set_paused(download_id, False)

    def cancel(self, download_id: str):
        self.remove_handle(download_id)

    def remove_handle(self, download_id: str):
        """Cancel a download: drop the member and delete only *its* partial data."""
        with self._lock:
            member = self._members.pop(download_id, None)
            self._meta.pop(download_id, None)
            if member is None:
                return
            member.cancelled = True
            self._drop_from_snapshot(download_id)
            group = member.group
            if group is None:
                return  # the add worker notices ``cancelled`` and backs out
            self._detach(group, member, delete_partial=not member.finished)

    def stop_seeding(self, download_id: str):
        """Release a *finished* download's claim on its torrent (metadata is kept)."""
        with self._lock:
            member = self._members.pop(download_id, None)
            self._drop_from_snapshot(download_id)
            if member is None or member.group is None:
                return
            self._detach(member.group, member, delete_partial=False)

    def _drop_from_snapshot(self, download_id: str) -> None:
        """Make a cancelled/finished download vanish from statuses now, not at the next scan."""
        version, statuses = self._snapshot
        if download_id in statuses:
            self._snapshot = (version + 1, {k: v for k, v in statuses.items() if k != download_id})

    def shutdown(self, timeout: float = 5.0):
        """Stop the engine, saving resume data for everything still in flight."""
        self._stop.set()
        self._final_save_done.wait(timeout)
        self._dispatcher.join(timeout=1.0)
        self._executor.shutdown()

    # -- adding ----------------------------------------------------------------------------------
    def _load_torrent_info(self, source: str):
        path = pathlib.Path(source)
        stamp = None
        if path.exists():
            st = path.stat()
            stamp = (str(path), st.st_mtime_ns, st.st_size)
            with self._lock:
                cached = self._torrent_infos.get(stamp)
                if cached is not None:
                    self._torrent_infos.move_to_end(stamp)
                    return cached
            data = path.read_bytes()
        elif source.startswith(("http://", "https://")):
            data = get_bytes(source, timeout=30, retries=3)
        else:
            raise FileNotFoundError(f"Torrent file not found: {source}")
        try:
            ti = lt.torrent_info(data, {"max_decode_depth": 100, "max_decode_tokens": 5_000_000})
        except TypeError:
            ti = lt.torrent_info(lt.bdecode(data))
        if stamp is not None:
            with self._lock:
                self._torrent_infos[stamp] = ti
                while len(self._torrent_infos) > 4:
                    self._torrent_infos.popitem(last=False)
        return ti

    def _add_worker(self, member: _Member):
        try:
            os.makedirs(member.save_path, exist_ok=True)
            if member.source.startswith("magnet:"):
                params = lt.parse_magnet_uri(member.source)
                ti = None
                info_hash = str(params.info_hashes.get_best())
            else:
                ti = self._load_torrent_info(member.source)
                params = lt.add_torrent_params()
                params.ti = ti
                info_hash = str(ti.info_hashes().get_best())
            for _ in range(3):
                with self._lock:
                    if member.cancelled:
                        return
                    group = self._groups.get(info_hash)
                    if group is None:
                        self._create_group(info_hash, params, ti, member)
                        return
                    if not group.removing:
                        self._attach(group, member)
                        return
                    waiter = group.removed
                # The previous owner of this torrent is being removed; wait for libtorrent to
                # finish before adding the same info-hash again.
                waiter.wait(REMOVE_WAIT)
            raise _MemberError("The torrent is still shutting down; retrying shortly", retryable=True)
        except _MemberError as e:
            self._fail(member, str(e), e.retryable)
        except FileNotFoundError as e:
            self._fail(member, str(e), retryable=False)
        except Exception as e:
            log_error(f"TorrentEngine.add_download failed for {member.name}", e)
            self._fail(member, str(e), retryable=isinstance(e, OSError) and not isinstance(e, PermissionError))

    def _resolve_index(self, ti, member: _Member) -> int:
        """Return the file index for ``member``, verifying it really is the requested file."""
        count = ti.num_files()
        storage = torrent_file_storage(ti)
        idx = member.so_id
        wanted = _nfc(member.name)
        if 0 <= idx < count and _nfc(os.path.basename(storage.file_path(idx))) == wanted:
            return idx
        matches = [i for i in range(count) if _nfc(os.path.basename(storage.file_path(i))) == wanted]
        if len(matches) == 1:
            log_activity(f"engine.index_corrected name='{member.name}' requested={idx} actual={matches[0]}")
            return matches[0]
        if not (0 <= idx < count):
            raise _MemberError(f"File index {idx} is outside this torrent ({count} files)")
        raise _MemberError(
            f"File {idx} of the torrent is not '{member.name}' (the archive's index is out of date)"
        )

    def _flat_name(self, group: _Group, member: _Member, idx: int, ti) -> str | None:
        """File name to flatten to (``save_path/name``) or None to leave libtorrent's own layout."""
        flat = member.name
        if not is_safe_leaf_name(flat):
            return None
        if torrent_file_storage(ti).file_path(idx) == flat:
            return None  # already at the top level
        if any(o.flat_name == flat for o in group.members.values() if o is not member):
            return None  # two files with the same name in one torrent: keep the second nested
        return flat

    @staticmethod
    def _file_on_disk(save_path: str, member: _Member, ti) -> bool:
        """True when this download's target already exists (flat or in libtorrent's own layout)."""
        base = pathlib.Path(save_path)
        try:
            nested = base / torrent_file_storage(ti).file_path(member.file_idx)
            flat = base / member.name
            return nested.exists() or flat.exists()
        except OSError:
            return True  # be safe: if we cannot tell, never delete

    def _preflight_disk(self, member: _Member, size: int):
        """Fail early with a useful message instead of stalling forever on a full disk."""
        if (pathlib.Path(member.save_path) / member.name).exists():
            return  # resuming/complete: the space is mostly allocated already
        try:
            free = shutil.disk_usage(member.save_path).free
        except OSError:
            return
        if free < size + DISK_RESERVE:
            raise _MemberError(
                f"Not enough free disk space in {member.save_path}: "
                f"{free / 1e9:.1f} GB free, about {(size + DISK_RESERVE) / 1e9:.1f} GB needed"
            )

    def _trackers_into(self, params, ti):
        """Put the built-in trackers in a tier after the torrent's own (all are announced)."""
        if not self._default_trackers:
            return
        tiers = [t.tier for t in ti.trackers()] if ti is not None else []
        tiers += list(params.tracker_tiers)
        tier = (max(tiers) + 1) if tiers else 0
        known = set(params.trackers)
        if ti is not None:
            known |= {t.url for t in ti.trackers()}
        extra = [t for t in self._default_trackers if t not in known]
        params.trackers = list(params.trackers) + extra
        params.tracker_tiers = list(params.tracker_tiers) + [tier] * len(extra)

    def _merge_resume(self, info_hash: str, params, ti):
        """Use saved resume data for progress only; everything security-sensitive stays ours."""
        if self._store is None:
            return params
        blob = self._store.load(info_hash)
        if not blob:
            return params
        try:
            saved = lt.read_resume_data(blob)
            if str(saved.info_hashes.get_best()) != info_hash:
                raise ValueError("info-hash mismatch")
        except Exception as e:
            log_activity(f"engine.resume_discarded hash={info_hash} err={e}")
            self._store.delete(info_hash)
            return params
        if ti is not None:
            saved.ti = params.ti
        saved.save_path = params.save_path
        saved.trackers = list(params.trackers)
        saved.tracker_tiers = list(params.tracker_tiers)
        saved.url_seeds = []
        saved.http_seeds = []
        saved.flags = params.flags
        return saved

    def _create_group(self, info_hash: str, params, ti, member: _Member):
        group = _Group(info_hash=info_hash, save_path=member.save_path, ti=ti)
        params.save_path = member.save_path
        _prepare_add_params(params, defer_download=ti is None)
        self._trackers_into(params, ti)
        if ti is not None:
            idx = self._resolve_index(ti, member)
            member.file_idx = idx
            member.size = torrent_file_storage(ti).file_size(idx)
            self._preflight_disk(member, member.size)
            member.flat_name = self._flat_name(group, member, idx, ti)
            member.preexisting = self._file_on_disk(group.save_path, member, ti)
        params = self._merge_resume(info_hash, params, ti)
        if ti is not None:
            # Resume data must never decide what we download or where it lands.
            priorities = [0] * ti.num_files()
            priorities[member.file_idx] = FILE_PRIORITY
            params.file_priorities = priorities
            params.renamed_files = {member.file_idx: member.flat_name} if member.flat_name else {}
        group.handle = self._session.add_torrent(params)
        group.members[member.did] = member
        member.group = group
        member.state_override = "" if ti is not None else "Metadata"
        self._groups[info_hash] = group

    def _attach(self, group: _Group, member: _Member):
        if group.save_path != member.save_path:
            raise _MemberError(
                f"This collection is already downloading to {group.save_path}; retrying when it is free",
                retryable=True,
            )
        group.members[member.did] = member
        member.group = group
        if group.ti is None:
            member.state_override = "Metadata"
            return
        self._apply_member(group, member)

    def _apply_member(self, group: _Group, member: _Member):
        """Give a member its file in an already-running torrent (also after magnet metadata)."""
        ti = group.ti
        try:
            idx = self._resolve_index(ti, member)
            size = torrent_file_storage(ti).file_size(idx)
            self._preflight_disk(member, size)
        except _MemberError as e:
            group.members.pop(member.did, None)
            member.group = None
            self._fail(member, str(e), e.retryable)
            self._maybe_remove_group(group)
            return
        member.file_idx, member.size, member.state_override = idx, size, ""
        member.preexisting = self._file_on_disk(group.save_path, member, ti)
        flat = self._flat_name(group, member, idx, ti)
        if flat and not (pathlib.Path(group.save_path) / flat).exists():
            try:
                group.handle.rename_file(idx, flat)
                member.flat_name = flat
            except Exception as e:
                log_error(f"TorrentEngine rename_file failed for {member.name}", e)
        self._sync_priority(group, idx)
        self._sync_handle_pause(group)

    # -- pausing / priorities ----------------------------------------------------------------------
    @staticmethod
    def _wanted(group: _Group, idx: int) -> bool:
        return any(
            m.file_idx == idx and not (m.cancelled or m.paused or m.finished)
            for m in group.members.values()
        )

    def _sync_priority(self, group: _Group, idx: int):
        try:
            group.handle.file_priority(idx, FILE_PRIORITY if self._wanted(group, idx) else 0)
        except Exception as e:
            log_error("TorrentEngine file_priority failed", e)

    def _sync_handle_pause(self, group: _Group):
        """Stop the whole torrent while every member is paused; wake it when any resumes."""
        live = [m for m in group.members.values() if not (m.finished or m.cancelled)]
        should_pause = bool(live) and all(m.paused for m in live)
        if should_pause == group.handle_paused:
            return
        try:
            if should_pause:
                group.handle.pause()
            else:
                group.handle.resume()
                # Pausing dropped every peer; ask trackers and the DHT again so resuming is quick.
                group.handle.force_reannounce()
                group.handle.force_dht_announce()
            group.handle_paused = should_pause
        except Exception as e:
            log_error("TorrentEngine pause/resume failed", e)

    def _set_paused(self, download_id: str, paused: bool):
        with self._lock:
            member = self._members.get(download_id)
            if member is None:
                return
            member.paused = paused
            group = member.group
            if not paused:
                member.last_progress_at = time.monotonic()
                member.stall_stage = 0
                if member.state_override in ("Disk full", "Error"):
                    member.error, member.state_override = "", ""
                    if group is not None:
                        try:
                            group.handle.clear_error()
                        except Exception:
                            pass
            if group is not None and member.file_idx >= 0:
                self._sync_priority(group, member.file_idx)
                self._sync_handle_pause(group)

    # -- leaving a group ---------------------------------------------------------------------------
    def _detach(self, group: _Group, member: _Member, *, delete_partial: bool):
        """Remove ``member`` from ``group``; drop the torrent when nobody needs it any more."""
        group.members.pop(member.did, None)
        member.group = None
        shared = any(o.file_idx == member.file_idx for o in group.members.values())
        if (delete_partial and member.file_idx >= 0 and member.done < member.size
                and not member.preexisting and not shared):
            group.cleanup_paths.extend(self._member_paths(group, member))
        if member.file_idx >= 0 and not group.removing:
            self._sync_priority(group, member.file_idx)
        self._maybe_remove_group(group)

    @staticmethod
    def _member_paths(group: _Group, member: _Member) -> list[pathlib.Path]:
        base = pathlib.Path(group.save_path)
        if member.flat_name:
            return [base / member.flat_name]
        if group.ti is not None and member.file_idx >= 0:
            return [base / torrent_file_storage(group.ti).file_path(member.file_idx)]
        return []

    def _group_needed(self, group: _Group) -> bool:
        if any(not (m.finished or m.cancelled) for m in group.members.values()):
            return True
        return bool(self._seeding["enabled"]) and any(m.finished for m in group.members.values())

    def _maybe_remove_group(self, group: _Group, events: list | None = None):
        if group.removing or self._group_needed(group):
            return
        self._remove_group(group, events)

    def _remove_group(self, group: _Group, events: list | None = None):
        group.removing = True
        group.removing_since = time.monotonic()
        group.pending_events = list(events or [])
        try:
            group.handle.flush_cache()
        except Exception:
            pass
        try:
            # delete_partfile only: delete_files would also delete finished siblings.
            self._session.remove_torrent(group.handle, lt.session.delete_partfile)
        except Exception as e:
            log_error("TorrentEngine remove_torrent failed", e)
            self._finish_removal(group)

    def _finish_removal(self, group: _Group):
        """Called once libtorrent confirmed the torrent is gone (or we gave up waiting)."""
        with self._lock:
            if self._groups.get(group.info_hash) is group:
                del self._groups[group.info_hash]
            group.removed.set()
            for event in group.pending_events:
                self.events.put(event)
            group.pending_events = []
            for path in group.cleanup_paths:
                self._cleanup.append({"path": path, "base": pathlib.Path(group.save_path), "tries": 0})
            group.cleanup_paths = []
            if self._store is not None:
                self._store.delete(group.info_hash)

    # -- failures ----------------------------------------------------------------------------------
    def _fail(self, member: _Member, message: str, retryable: bool = False):
        with self._lock:
            if member.cancelled or member.finished:
                return
            group = member.group
            if group is not None:
                self._detach(group, member, delete_partial=True)
            self._members.pop(member.did, None)
            member.error = message
        log_activity(f"engine.error id={member.did} name='{member.name}' retryable={retryable} msg={message}")
        self.events.put({"type": "error", "id": member.did, "msg": message, "retryable": retryable})

    # -- dispatcher --------------------------------------------------------------------------------
    def _dispatch_loop(self):
        next_scan = 0.0
        next_resume = time.monotonic() + RESUME_INTERVAL
        try:
            while not self._stop.is_set():
                self._session.wait_for_alert(250)
                for alert in self._session.pop_alerts():
                    try:
                        self._handle_alert(alert)
                    except Exception as e:
                        log_error(f"TorrentEngine alert handling failed ({type(alert).__name__})", e)
                now = time.monotonic()
                if now >= next_scan:
                    next_scan = now + SCAN_INTERVAL
                    try:
                        self._session.post_torrent_updates()
                        self._scan(now)
                    except Exception as e:
                        log_error("TorrentEngine scan failed", e)
                try:
                    if now >= next_resume:
                        next_resume = now + RESUME_INTERVAL
                        self._request_resume_data(only_modified=True)
                    self._process_cleanup()
                except Exception as e:
                    log_error("TorrentEngine housekeeping failed", e)
            self._final_save()
        except Exception as e:  # pragma: no cover - last-resort guard so shutdown() never hangs
            log_error("TorrentEngine dispatcher crashed", e)
        finally:
            self._final_save_done.set()

    def _group_of(self, handle) -> "_Group | None":
        try:
            return self._groups.get(str(handle.info_hashes().get_best()))
        except Exception:
            return None

    def _handle_alert(self, alert):
        if isinstance(alert, lt.state_update_alert):
            for st in alert.status:
                group = self._groups.get(str(st.info_hashes.get_best()))
                if group is not None:
                    group.status = st
        elif isinstance(alert, lt.torrent_removed_alert):
            group = self._groups.get(str(alert.info_hashes.get_best()))
            if group is not None and group.removing:
                self._finish_removal(group)
        elif isinstance(alert, lt.metadata_received_alert):
            group = self._group_of(alert.handle)
            if group is not None:
                with self._lock:
                    group.ti = alert.handle.torrent_file()
                    for member in list(group.members.values()):
                        if member.file_idx < 0 and not member.cancelled:
                            self._apply_member(group, member)
        elif isinstance(alert, lt.metadata_failed_alert):
            group = self._group_of(alert.handle)
            if group is not None:
                for member in list(group.members.values()):
                    self._fail(member, f"Invalid torrent metadata: {alert.error.message()}", retryable=True)
        elif isinstance(alert, lt.torrent_checked_alert):
            group = self._group_of(alert.handle)
            if group is not None:
                group.checked = True
        elif isinstance(alert, lt.save_resume_data_alert):
            self._store_resume(alert)
        elif isinstance(alert, lt.file_error_alert):
            self._on_storage_error(self._group_of(alert.handle), alert.error, str(alert.filename))
        elif isinstance(alert, lt.torrent_error_alert):
            group = self._group_of(alert.handle)
            if group is not None and alert.error.value():
                self._on_storage_error(group, alert.error, "")
        elif isinstance(alert, lt.add_torrent_alert):
            if alert.error.value():
                with self._lock:
                    group = self._group_of(alert.handle)
                    for member in list((group.members if group else {}).values()):
                        self._fail(member, f"libtorrent rejected the torrent: {alert.error.message()}")

    def _on_storage_error(self, group: "_Group | None", error, filename: str):
        if group is None:
            return
        disk_full = error.value() in DISK_FULL_ERRNOS
        text = "Disk full" if disk_full else f"Disk error: {error.message()}"
        where = f" ({filename})" if filename else ""
        with self._lock:
            for member in group.members.values():
                if member.finished or member.cancelled:
                    continue
                member.error = f"{text} in {group.save_path}{where}"
                member.state_override = "Disk full" if disk_full else "Error"
                member.paused = True  # libtorrent has stopped the torrent; resume() clears this
            log_activity(f"engine.storage_error hash={group.info_hash} {text} {error.message()}{where}")

    # -- scanning ----------------------------------------------------------------------------------
    def _scan(self, now: float):
        statuses: dict[str, dict] = {}
        with self._lock:
            for group in list(self._groups.values()):
                if group.handle is None:
                    continue
                if group.removing:
                    if now - group.removing_since > REMOVE_TIMEOUT:
                        log_activity(f"engine.removal_timeout hash={group.info_hash}")
                        self._finish_removal(group)
                    continue
                self._scan_group(group, now, statuses)
            for did, member in self._members.items():
                if did not in statuses and member.group is None:
                    statuses[did] = self._status_dict(member, None, None, 1)
        self._snapshot = (self._snapshot[0] + 1, statuses)

    def _scan_group(self, group: _Group, now: float, statuses: dict):
        st = group.status
        if st is None:
            try:
                st = group.status = group.handle.status()
            except Exception:
                st = None
        unfinished = [m for m in group.members.values() if not (m.finished or m.cancelled)]
        progress_list = None
        if group.ti is not None and any(m.file_idx >= 0 for m in unfinished):
            try:
                progress_list = group.handle.file_progress(flags=lt.torrent_handle.piece_granularity)
            except Exception as e:
                log_error("TorrentEngine file_progress failed", e)
        finishing: list[_Member] = []
        for member in unfinished:
            if progress_list is not None and 0 <= member.file_idx < len(progress_list):
                member.done = min(int(progress_list[member.file_idx]), member.size) if member.size else 0
                if (member.size > 0 and member.done >= member.size) or (member.size == 0 and group.checked):
                    finishing.append(member)
                    continue
            if member.done > member.last_done:
                member.last_done = member.done
                member.last_progress_at = now
                member.stall_stage = 0
            self._watchdog(group, member, now)
        share = max(1, len([m for m in unfinished if not m.paused and not m.finished]))
        for member in finishing:
            self._complete_member(group, member)
        for member in list(group.members.values()):
            if not member.cancelled:
                statuses[member.did] = self._status_dict(member, group, st, share)
        self._check_seeding_limits(group, st, now)

    def _watchdog(self, group: _Group, member: _Member, now: float):
        if member.paused or member.error or member.finished or member.cancelled:
            return
        if group.ti is None:
            if now - group.metadata_since > METADATA_TIMEOUT:
                self._fail(member, "Timed out waiting for the torrent metadata (no peers?)", retryable=True)
            return
        st = group.status
        if st is not None and _TORRENT_STATE_MAP.get(st.state) == "Checking":
            member.last_progress_at = now
            return
        idle = now - member.last_progress_at
        if idle > STALL_FAIL_AFTER:
            self._fail(member, "Stalled: no data received for 10 minutes", retryable=True)
        elif idle > STALL_REANNOUNCE_AFTER and member.stall_stage == 0:
            member.stall_stage = 1
            log_activity(f"engine.stalled id={member.did} name='{member.name}'; re-announcing")
            try:
                group.handle.force_reannounce()
                group.handle.force_dht_announce()
            except Exception:
                pass

    def _complete_member(self, group: _Group, member: _Member):
        member.finished = True
        member.done = member.size
        event = {"type": "finished", "id": member.did}
        others_live = any(not (m.finished or m.cancelled) for m in group.members.values())
        if others_live or self._seeding["enabled"]:
            if self._seeding["enabled"] and not group.seeding_since:
                group.seeding_since = time.monotonic()
            self._sync_priority(group, member.file_idx)
            self.events.put(event)
            return
        # Last file out: let libtorrent close everything (and drop its part file) first, so the
        # app never sees "finished" for a file that is still open or half-flushed.
        self._remove_group(group, [event])

    def _check_seeding_limits(self, group: _Group, st, now: float):
        if group.removing or not self._seeding["enabled"] or not group.seeding_since:
            return
        if any(not (m.finished or m.cancelled) for m in group.members.values()):
            return
        done = max(1, int(getattr(st, "total_done", 0) or 0)) if st is not None else 1
        uploaded = int(getattr(st, "all_time_upload", 0) or 0) if st is not None else 0
        if uploaded >= self._seeding["ratio"] * done or now - group.seeding_since >= self._seeding["hours"] * 3600:
            self._remove_group(group)

    @staticmethod
    def _status_dict(member: _Member, group: "_Group | None", st, share: int) -> dict:
        size = member.size
        done = member.done
        rate = int(getattr(st, "download_rate", 0) or 0) if st is not None else 0
        upload = int(getattr(st, "upload_rate", 0) or 0) if st is not None else 0
        if member.error and member.state_override in ("Error", "Disk full"):
            state = member.state_override
        elif member.finished:
            state = "Finished"
        elif member.paused:
            state = "Paused"
        elif group is None:
            state = "Queued"
        elif member.state_override:
            state = member.state_override
        elif st is None:
            state = "Checking"
        else:
            state = _TORRENT_STATE_MAP.get(st.state, "Downloading")
            if state == "Seeding":
                state = "Downloading"  # torrent-level 'finished' shows up a scan before the member completes
        member_rate = rate // max(1, share) if not (member.paused or member.finished) else 0
        remaining = max(0, size - done)
        return {
            "name": member.name,
            "progress": (done / size) if size else (1.0 if member.finished else 0.0),
            "download_rate": member_rate,
            "upload_rate": upload // max(1, share),
            "state": state,
            "num_peers": int(getattr(st, "num_peers", 0) or 0) if st is not None else 0,
            "total_done": done,
            "total": size,
            "paused": member.paused,
            "error": member.error,
            "eta": int(remaining / member_rate) if member_rate > 0 else -1,
        }

    # -- cleanup of cancelled partial files ----------------------------------------------------------
    def _process_cleanup(self):
        if not self._cleanup:
            return
        with self._lock:
            pending, self._cleanup = self._cleanup, []
        keep = []
        for job in pending:
            path: pathlib.Path = job["path"]
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                job["tries"] += 1
                if job["tries"] < 30:  # Windows may still hold the file for a moment
                    keep.append(job)
                else:
                    log_activity(f"engine.cleanup_gave_up path='{path}'")
                continue
            parent, base = path.parent, job["base"]
            while parent != base and base in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        if keep:
            with self._lock:
                self._cleanup.extend(keep)

    # -- resume data ---------------------------------------------------------------------------------
    def _request_resume_data(self, only_modified: bool) -> int:
        if self._store is None:
            return 0
        flags = lt.torrent_handle.save_info_dict
        flags |= lt.torrent_handle.only_if_modified if only_modified else lt.torrent_handle.flush_disk_cache
        requested = 0
        with self._lock:
            for group in self._groups.values():
                if group.removing or group.handle is None:
                    continue
                try:
                    if only_modified and not group.handle.need_save_resume_data():
                        continue
                    group.handle.save_resume_data(flags)
                    requested += 1
                except Exception as e:
                    log_error("TorrentEngine save_resume_data request failed", e)
        return requested

    def _store_resume(self, alert):
        try:
            ih = str(alert.handle.info_hashes().get_best())
            group = self._groups.get(ih)
            if self._store is None or group is None or group.removing:
                return
            self._store.save(ih, lt.write_resume_data_buf(alert.params))
        except Exception as e:
            log_error("TorrentEngine could not store resume data", e)

    def _final_save(self):
        """Dispatcher-thread shutdown: persist resume data and DHT state, then stop."""
        try:
            self._session.pause()
        except Exception as e:
            log_error("TorrentEngine.shutdown pause failed", e)
        expected = self._request_resume_data(only_modified=False)
        deadline = time.monotonic() + 4.0
        received = 0
        while received < expected and time.monotonic() < deadline:
            self._session.wait_for_alert(200)
            for alert in self._session.pop_alerts():
                if isinstance(alert, lt.save_resume_data_alert):
                    self._store_resume(alert)
                    received += 1
                elif isinstance(alert, lt.save_resume_data_failed_alert):
                    received += 1
        if self._store is not None:
            try:
                self._store.save_session_state(lt.write_session_params_buf(self._session.session_state()))
            except Exception as e:
                log_error("TorrentEngine could not save DHT state", e)
