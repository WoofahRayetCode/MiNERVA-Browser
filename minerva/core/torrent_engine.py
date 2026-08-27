import pathlib
import threading
import queue
import uuid
import urllib.request
import sys
from minerva.constants import log_error, get_default_trackers

try:
    import libtorrent as lt
    _LT_AVAILABLE = True
except ImportError:
    lt = None
    _LT_AVAILABLE = False


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


def _status_is_paused(status) -> bool:
    if hasattr(status, "paused"):
        return bool(status.paused)
    flags = getattr(status, "flags", None)
    paused_flag = getattr(getattr(lt, "torrent_flags", None), "paused", None)
    if flags is not None and paused_flag is not None:
        return bool(flags & paused_flag)
    return False


def _get_optimized_session_settings() -> dict:
    """Return tuned libtorrent session settings for maximum throughput and rapid peer discovery."""
    return {
        # Concurrency & Connections
        "connections_limit": 800,
        "connection_speed": 100,
        "torrent_connect_boost": 50,
        "unchoke_slots_limit": 80,
        "num_optimistic_unchoke_slots": 8,
        "max_peerlist_size": 4000,
        "max_paused_peerlist_size": 1000,
        "max_pex_peers": 100,

        # Pipelining & Request Queues
        "request_queue_time": 3,
        "max_out_request_queue": 1500,
        "max_allowed_in_request_queue": 2000,
        "whole_pieces_threshold": 20,
        "piece_timeout": 20,
        "request_timeout": 30,
        "peer_timeout": 30,
        "peer_connect_timeout": 15,
        "inactivity_timeout": 20,
        "min_reconnect_time": 2,

        # Socket & Network Buffers
        "recv_socket_buffer_size": 2 * 1024 * 1024,
        "send_socket_buffer_size": 2 * 1024 * 1024,
        "max_peer_recv_buffer_size": 4 * 1024 * 1024,

        # Protocols & Discovery
        "enable_dht": True,
        "enable_lsd": True,
        "enable_upnp": True,
        "enable_natpmp": True,
        "enable_outgoing_tcp": True,
        "enable_incoming_tcp": True,
        "enable_outgoing_utp": True,
        "enable_incoming_utp": True,
        "use_dht_as_fallback": False,
        "dht_aggressive_lookups": True,
        "dht_bootstrap_nodes": (
            "router.bittorrent.com:6881,"
            "router.utorrent.com:6881,"
            "dht.transmissionbt.com:6881,"
            "dht.libtorrent.org:25401,"
            "router.bitcomet.com:6881,"
            "dht.aelitis.com:6881"
        ),

        # Unlimited session limits so libtorrent does not throttle tasks managed by DownloadQueue
        "active_downloads": -1,
        "active_seeds": -1,
        "active_limit": -1,
        "active_tracker_limit": -1,
        "active_dht_limit": -1,
        "active_lsd_limit": -1,

        # Tracker settings
        "tracker_completion_timeout": 30,
        "tracker_receive_timeout": 15,
        "stop_tracker_timeout": 1,

        # Disk cache & IO
        "coalesce_reads": True,
        "coalesce_writes": True,
        "guided_read_cache": True,
        "volatile_read_cache": False,
        "close_redundant_connections": True,
        "allow_multiple_connections_per_ip": True,
    }


class TorrentEngine:
    def __init__(self):
        if not _LT_AVAILABLE or lt is None:
            raise RuntimeError("libtorrent not available")
        settings = _get_optimized_session_settings()
        try:
            self._session = lt.session(settings)
        except Exception:
            self._session = lt.session()
            try:
                self._session.apply_settings(settings)
            except Exception as e:
                log_error("TorrentEngine.__init__ fallback apply_settings failed", e)

        dht_routers = (
            ("router.bittorrent.com", 6881),
            ("router.utorrent.com", 6881),
            ("dht.transmissionbt.com", 6881),
            ("dht.libtorrent.org", 25401),
            ("router.bitcomet.com", 6881),
            ("dht.aelitis.com", 6881),
        )
        for host, port in dht_routers:
            try:
                self._session.add_dht_router(host, port)
            except AttributeError:
                try:
                    self._session.add_dht_node((host, port))
                except Exception as e:
                    log_error(f"TorrentEngine.__init__ add_dht_node failed for {host}", e)
        self._handles: dict = {}
        self._meta: dict = {}
        self._finished_ids: set[str] = set()
        self.events: queue.Queue = queue.Queue()
        self._running = True
        self._thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._thread.start()

    def add_download(self, torrent_source: str, so_id: int, file_name: str, save_path: str,
                      download_id: str | None = None) -> str:
        download_id = download_id or str(uuid.uuid4())
        resolved_save_path = str(pathlib.Path(save_path).resolve())
        try:
            pathlib.Path(resolved_save_path).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        def _do():
            try:
                default_trackers = get_default_trackers()
                if torrent_source.startswith("magnet:"):
                    params = lt.parse_magnet_uri(torrent_source)
                    params.save_path = resolved_save_path
                    handle = self._session.add_torrent(params)
                    for tr in default_trackers:
                        try:
                            handle.add_tracker({"url": tr, "tier": 0})
                        except Exception:
                            pass
                    try:
                        handle.force_reannounce()
                        handle.force_dht_announce()
                    except Exception:
                        pass
                    self._handles[download_id] = handle
                    self._meta[download_id] = {
                        "name": file_name,
                        "so_id": so_id,
                        "save_path": resolved_save_path,
                        "delete_archive": False,
                        "waiting_metadata": True,
                    }
                else:
                    local_path = pathlib.Path(torrent_source)
                    if local_path.exists():
                        torrent_data = local_path.read_bytes()
                    else:
                        req = urllib.request.Request(
                            torrent_source, headers={"User-Agent": "MiNERVA-Browser/1.0"}
                        )
                        with urllib.request.urlopen(req, timeout=30) as resp:
                            torrent_data = resp.read()
                    try:
                        ti = lt.torrent_info(
                            torrent_data,
                            {"max_decode_depth": 100, "max_decode_tokens": 5_000_000},
                        )
                    except TypeError:
                        ti = lt.torrent_info(lt.bdecode(torrent_data))
                    num_files = ti.num_files()
                    priorities = [0] * num_files
                    if 0 <= so_id < num_files:
                        priorities[so_id] = 7  # Maximum priority in libtorrent
                    params = lt.add_torrent_params()
                    params.ti = ti
                    params.save_path = resolved_save_path
                    params.file_priorities = priorities
                    handle = self._session.add_torrent(params)
                    self._add_file_priority(handle, so_id)
                    for tr in default_trackers:
                        try:
                            handle.add_tracker({"url": tr, "tier": 0})
                        except Exception:
                            pass
                    try:
                        handle.force_reannounce()
                        handle.force_dht_announce()
                    except Exception:
                        pass
                    self._handles[download_id] = handle
                    self._meta[download_id] = {
                        "name": file_name,
                        "so_id": so_id,
                        "save_path": resolved_save_path,
                        "delete_archive": False,
                        "waiting_metadata": False,
                    }
            except Exception as e:
                log_error(f"TorrentEngine.add_download failed for {file_name}", e)
                self.events.put({"type": "error", "id": download_id, "msg": str(e)})

        threading.Thread(target=_do, daemon=True).start()
        return download_id

    def _add_file_priority(self, handle, so_id: int):
        try:
            ti = handle.torrent_file()
            if ti is None:
                return
            num_files = ti.num_files()
            existing = []
            try:
                existing = list(handle.file_priorities())
            except Exception:
                existing = []
            priorities = (existing + [0] * max(0, num_files - len(existing)))[:num_files]
            if 0 <= so_id < num_files:
                priorities[so_id] = max(priorities[so_id], 7)
            handle.prioritize_files(priorities)
        except Exception as e:
            log_error("TorrentEngine._add_file_priority failed", e)

    def _has_downloaded_file(self, download_id: str) -> bool:
        meta = self._meta.get(download_id)
        if not meta:
            return False
        save_path = pathlib.Path(meta.get("save_path", ""))
        file_name = meta.get("name", "")
        if not file_name:
            return False

        direct = save_path / file_name
        if direct.exists():
            return True

        for depth in range(1, 4):
            pattern = "/".join(["*"] * depth) + f"/{file_name}"
            if any(save_path.glob(pattern)):
                return True
        return False

    def _is_target_file_complete(self, download_id: str, handle, status) -> bool:
        meta = self._meta.get(download_id)
        if not meta:
            return False
        so_id = meta.get("so_id")
        if not isinstance(so_id, int) or so_id < 0:
            return False
        ti = handle.torrent_file()
        if ti is None:
            return False
        num_files = ti.num_files()
        if so_id >= num_files:
            return False
        target_size = ti.files().file_size(so_id)
        if target_size <= 0:
            return False
        progress = list(handle.file_progress())
        if so_id >= len(progress):
            return False
        target_done = progress[so_id]
        if target_done >= target_size:
            return True
        return status.total_wanted == target_size and status.total_done >= status.total_wanted

    def _alert_loop(self):
        while self._running:
            self._session.wait_for_alert(500)
            alerts = self._session.pop_alerts()
            for alert in alerts:
                alert_type = type(alert).__name__
                if alert_type == "metadata_received_alert":
                    for did, handle in list(self._handles.items()):
                        meta = self._meta.get(did, {})
                        if meta.get("waiting_metadata") and handle.is_valid():
                            if handle.info_hash() == alert.handle.info_hash():
                                self._add_file_priority(handle, meta["so_id"])
                                meta["waiting_metadata"] = False
            for did, handle in list(self._handles.items()):
                if not handle.is_valid():
                    continue
                try:
                    s = handle.status()
                    state_str = _TORRENT_STATE_MAP.get(s.state, str(s.state))
                    self.events.put({
                        "type": "status",
                        "id": did,
                        "name": self._meta[did]["name"],
                        "progress": s.progress,
                        "download_rate": s.download_rate,
                        "upload_rate": s.upload_rate,
                        "state": state_str,
                        "num_peers": s.num_peers,
                        "total_done": s.total_done,
                        "total": s.total_wanted,
                        "paused": _status_is_paused(s),
                        "error": s.errc.message() if s.errc else "",
                    })
                    is_complete = self._is_target_file_complete(did, handle, s) or (
                        (state_str in ("Seeding", "Finished")) and s.progress >= 0.999
                    )
                    has_file = self._has_downloaded_file(did)
                    if is_complete and has_file and state_str in ("Seeding", "Finished") and not _status_is_paused(s):
                        if did not in self._finished_ids:
                            self._finished_ids.add(did)
                            self.events.put({"type": "finished", "id": did})
                except Exception as e:
                    log_error(f"TorrentEngine._alert_loop status processing failed for {did}", e)

    def get_all_statuses(self) -> dict:
        statuses = {}
        for did, handle in list(self._handles.items()):
            if not handle.is_valid():
                continue
            try:
                s = handle.status()
                state_str = _TORRENT_STATE_MAP.get(s.state, str(s.state))
                statuses[did] = {
                    "name": self._meta[did]["name"],
                    "progress": s.progress,
                    "download_rate": s.download_rate,
                    "upload_rate": s.upload_rate,
                    "state": state_str,
                    "num_peers": s.num_peers,
                    "total_done": s.total_done,
                    "total": s.total_wanted,
                    "paused": _status_is_paused(s),
                    "error": s.errc.message() if s.errc else "",
                }
            except Exception as e:
                log_error(f"TorrentEngine.get_all_statuses failed for {did}", e)
        return statuses

    def pause(self, download_id: str):
        h = self._handles.get(download_id)
        if h and h.is_valid():
            h.pause()

    def resume(self, download_id: str):
        h = self._handles.get(download_id)
        if h and h.is_valid():
            h.resume()

    def cancel(self, download_id: str):
        h = self._handles.get(download_id)
        if h and h.is_valid():
            self._session.remove_torrent(h)
        self._handles.pop(download_id, None)
        self._meta.pop(download_id, None)

    def remove_handle(self, download_id: str):
        h = self._handles.get(download_id)
        if h and h.is_valid():
            self._session.remove_torrent(h)
        self._handles.pop(download_id, None)
        self._meta.pop(download_id, None)
        self._finished_ids.discard(download_id)

    def stop_seeding(self, download_id: str):
        h = self._handles.get(download_id)
        if h and h.is_valid():
            self._session.remove_torrent(h)
        self._handles.pop(download_id, None)
        self._finished_ids.discard(download_id)

    def set_auto_extract(self, download_id: str, enabled: bool):
        meta = self._meta.get(download_id)
        if meta is not None:
            meta["auto_extract"] = bool(enabled)

    def set_delete_archive(self, download_id: str, enabled: bool):
        meta = self._meta.get(download_id)
        if meta is not None:
            meta["delete_archive"] = bool(enabled)

    def shutdown(self):
        self._running = False
        try:
            self._session.pause()
        except Exception as e:
            log_error("TorrentEngine.shutdown pause failed", e)


class DownloadQueue:
    def __init__(self, engine: "TorrentEngine", max_active: int = 3):
        self.engine = engine
        self.max_active = max_active
        self._pending: dict[str, dict] = {}
        self._active: dict[str, dict] = {}
        self._done: dict[str, dict] = {}
        self._lock = threading.Lock()

    def enqueue(self, download_id: str, name: str, source: str, so_id: int, save_path: str):
        resolved_save_path = str(pathlib.Path(save_path).resolve())
        item = {
            "id": download_id,
            "name": name,
            "source": source,
            "so_id": so_id,
            "save_path": resolved_save_path,
            "start_requested": False,
        }
        with self._lock:
            self._pending[download_id] = item

    def start_selected(self, download_ids: list[str]):
        with self._lock:
            for did in download_ids:
                item = self._pending.get(did)
                if item:
                    item["start_requested"] = True
        self._try_advance()

    def start_all_pending(self):
        with self._lock:
            for item in self._pending.values():
                item["start_requested"] = True
        self._try_advance()

    def _try_advance(self):
        with self._lock:
            while len(self._active) < self.max_active and self._pending:
                next_item = next(
                    ((did, item) for did, item in self._pending.items() if item.get("start_requested")),
                    None,
                )
                if next_item is None:
                    break
                did, item = next_item
                del self._pending[did]
                self._active[did] = item
                if self.engine is not None:
                    self.engine.add_download(
                        item["source"],
                        item["so_id"],
                        item["name"],
                        item["save_path"],
                        download_id=did,
                    )

    def on_finished(self, download_id: str, error: str = ""):
        with self._lock:
            item = self._active.pop(download_id, None) or self._pending.pop(download_id, None)
            if item:
                self._done[download_id] = {
                    "id": download_id,
                    "name": item["name"],
                    "save_path": item["save_path"],
                    "status": "error" if error else "done",
                    "error": error,
                }
        self._try_advance()

    def cancel(self, download_id: str):
        with self._lock:
            was_active = download_id in self._active
            self._pending.pop(download_id, None)
            self._active.pop(download_id, None)
        if was_active and self.engine is not None:
            self.engine.remove_handle(download_id)
        self._try_advance()

    def clear_done(self):
        with self._lock:
            self._done.clear()

    def set_max_active(self, n: int):
        self.max_active = max(1, n)
        self._try_advance()

    def has_name(self, name: str) -> bool:
        with self._lock:
            for d in (*self._pending.values(), *self._active.values(), *self._done.values()):
                if d.get("name") == name:
                    return True
        return False

    def move_up(self, download_id: str):
        with self._lock:
            keys = list(self._pending.keys())
            if download_id not in keys:
                return
            idx = keys.index(download_id)
            if idx > 0:
                keys[idx - 1], keys[idx] = keys[idx], keys[idx - 1]
                self._pending = {k: self._pending[k] for k in keys}

    def move_down(self, download_id: str):
        with self._lock:
            keys = list(self._pending.keys())
            if download_id not in keys:
                return
            idx = keys.index(download_id)
            if idx < len(keys) - 1:
                keys[idx + 1], keys[idx] = keys[idx], keys[idx + 1]
                self._pending = {k: self._pending[k] for k in keys}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "pending": list(self._pending.values()),
                "active": list(self._active.keys()),
                "done": list(self._done.values()),
            }

    def export_for_persistence(self) -> list[dict]:
        with self._lock:
            items: list[dict] = []
            for item in self._active.values():
                items.append({
                    "id": item["id"],
                    "name": item["name"],
                    "source": item["source"],
                    "so_id": item["so_id"],
                    "save_path": item["save_path"],
                    "start_requested": True,
                })
            for item in self._pending.values():
                items.append({
                    "id": item["id"],
                    "name": item["name"],
                    "source": item["source"],
                    "so_id": item["so_id"],
                    "save_path": item["save_path"],
                    "start_requested": bool(item.get("start_requested", False)),
                })
            return items
