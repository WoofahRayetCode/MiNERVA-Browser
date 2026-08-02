import sys
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import urllib.request
import urllib.parse
import webbrowser
import struct
import queue
import uuid
import pathlib
import shutil
import subprocess
import re
import zipfile
from html.parser import HTMLParser

BASE_URL = "https://minerva-archive.org"
BROWSE_ROOT = "/browse/"
HASHES_DB_URL = "https://minerva-archive.org/assets/hashes.db"
TRACKERS = "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce&tr=udp%3A%2F%2F9.rarbg.com%3A2810%2Fannounce&tr=udp%3A%2F%2Ftracker.openbittorrent.com%3A6969%2Fannounce&tr=http%3A%2F%2Ftracker.openbittorrent.com%3A80%2Fannounce&tr=http%3A%2F%2F95.107.48.115%3A80%2Fannounce&tr=http%3A%2F%2Fopen.acgnxtracker.com%3A80%2Fannounce&tr=http%3A%2F%2Ft.acg.rip%3A6699%2Fannounce&tr=http%3A%2F%2Ft.nyaatracker.com%3A80%2Fannounce&tr=http%3A%2F%2Ftracker.bt4g.com%3A2095%2Fannounce&tr=http%3A%2F%2Ftracker.files.fm%3A6969%2Fannounce&tr=http%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce&tr=http%3A%2F%2Fvps02.net.orel.ru%3A80%2Fannounce&tr=https%3A%2F%2F1337.abcvg.info%3A443%2Fannounce&tr=https%3A%2F%2Fopentracker.i2p.rocks%3A443%2Fannounce&tr=https%3A%2F%2Ftracker.nanoha.org%3A443%2Fannounce&tr=https%3A%2F%2Ftracker.sloppyta.co%3A443%2Fannounce&tr=udp%3A%2F%2F208.83.20.20%3A6969%2Fannounce&tr=udp%3A%2F%2F37.235.174.46%3A2710%2Fannounce&tr=udp%3A%2F%2F75.127.14.224%3A2710%2Fannounce&tr=udp%3A%2F%2Fexodus.desync.com%3A6969%2Fannounce&tr=udp%3A%2F%2Fexplodie.org%3A6969%2Fannounce&tr=udp%3A%2F%2Ffe.dealclub.de%3A6969%2Fannounce&tr=udp%3A%2F%2Fipv4.tracker.harry.lu%3A80%2Fannounce&tr=udp%3A%2F%2Fmovies.zsw.ca%3A6969%2Fannounce&tr=udp%3A%2F%2Fopen.demonii.com%3A1337%2Fannounce&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce&tr=udp%3A%2F%2Fopentracker.i2p.rocks%3A6969%2Fannounce&tr=udp%3A%2F%2Fp4p.arenabg.com%3A1337%2Fannounce&tr=udp%3A%2F%2Fpublic.tracker.vraphim.com%3A6969%2Fannounce&tr=udp%3A%2F%2Fretracker.lanta-net.ru%3A2710%2Fannounce&tr=udp%3A%2F%2Ftracker.0x.tf%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.dler.org%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.filemail.com%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.moeking.me%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.pomf.se%3A80%2Fannounce&tr=udp%3A%2F%2Ftracker.swateam.org.uk%3A2710%2Fannounce&tr=udp%3A%2F%2Ftracker.tiny-vps.com%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce&tr=https%3A%2F%2Ftracker1.ctix.cn%3A443%2Fannounce&tr=https%3A%2F%2Ftracker.loligirl.cn%3A443%2Fannounce&tr=udp%3A%2F%2Ftracker-udp.gbitt.info%3A80%2Fannounce&tr=https%3A%2F%2Ftracker.gbitt.info%3A443%2Fannounce&tr=http%3A%2F%2Ftracker.gbitt.info%3A80%2Fannounce&tr=udp%3A%2F%2Ftracker.therarbg.to%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.therarbg.com%3A6969%2Fannounce&tr=udp%3A%2F%2Fopentracker.io%3A6969%2Fannounce&tr=udp%3A%2F%2Fnew-line.net%3A6969%2Fannounce&tr=udp%3A%2F%2Fmoonburrow.club%3A6969%2Fannounce&tr=udp%3A%2F%2Fepider.me%3A6969%2Fannounce&tr=udp%3A%2F%2Fbt1.archive.org%3A6969%2Fannounce&tr=udp%3A%2F%2Fbt.ktrackers.com%3A6666%2Fannounce"

BG = "#1e1e2e"
PANEL = "#2a2a3e"
ACCENT = "#7c6af7"
FG = "#cdd6f4"
FG_DIM = "#a6adc8"
SEL_BG = "#45475a"
ENTRY_BG = "#313244"


def get_default_download_dir() -> str:
    """Return the folder next to the exe (frozen) or next to this script (dev)."""
    if getattr(sys, "frozen", False):
        return str(pathlib.Path(sys.executable).parent)
    return str(pathlib.Path(__file__).parent)


def get_torrent_dir() -> pathlib.Path:
    """Return (and create) the torrentfiles/ folder next to the exe / script."""
    base = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else pathlib.Path(__file__).parent
    d = base / "torrentfiles"
    d.mkdir(exist_ok=True)
    return d


def find_7zip_executable() -> str | None:
    """Find a usable 7-Zip executable on PATH or in common Windows install locations."""
    candidates = [
        shutil.which("7z"),
        shutil.which("7z.exe"),
        shutil.which("7za"),
        shutil.which("7za.exe"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).exists():
            return candidate
    return None


class EntryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries = []
        self._in_entry = False
        self._skip = False
        self._href = None
        self._name = None
        self._size = None
        self._in_span = False
        self._entry_classes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "entry" in attrs.get("class", "").split():
            classes = attrs.get("class", "").split()
            if "search_back" in classes or "search_dir" in classes:
                self._skip = True
            else:
                self._skip = False
                self._in_entry = True
                self._href = None
                self._name = attrs.get("data-name", "")
                self._size = ""
        elif self._in_entry and not self._skip and tag == "a":
            self._href = attrs.get("href", "")
        elif self._in_entry and not self._skip and tag == "span":
            self._in_span = True

    def handle_endtag(self, tag):
        if tag == "div" and self._in_entry:
            if not self._skip and self._href:
                is_folder = self._href.endswith("/")
                self.entries.append({
                    "name": self._name or urllib.parse.unquote(self._href.rstrip("/").split("/")[-1]),
                    "href": self._href,
                    "size": self._size.strip(),
                    "is_folder": is_folder,
                })
            self._in_entry = False
            self._skip = False
            self._href = None
            self._name = None
            self._size = None
        elif tag == "span":
            self._in_span = False

    def handle_data(self, data):
        if self._in_entry and not self._skip and self._in_span:
            self._size = (self._size or "") + data


def fetch_entries(path):
    url = BASE_URL + path
    req = urllib.request.Request(url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    parser = EntryParser()
    parser.feed(html)
    return parser.entries


class SQLiteHTTP:
    """Minimal HTTP-range SQLite reader. Only reads pages needed to answer a lookup by index."""

    def __init__(self, url: str):
        self.url = url
        raw = self._http_range(0, 99)
        magic = raw[:16]
        if magic != b"SQLite format 3\x00":
            raise ValueError("Not a SQLite database")
        page_size_raw = struct.unpack_from(">H", raw, 16)[0]
        self.page_size = 65536 if page_size_raw == 1 else page_size_raw
        page1 = self._http_range(0, self.page_size - 1)
        self._page_cache = {1: page1}
        self._files_root, self._index_root = self._parse_schema(page1)

    def _http_range(self, start: int, end: int) -> bytes:
        req = urllib.request.Request(
            self.url,
            headers={"Range": f"bytes={start}-{end}", "User-Agent": "MiNERVA-Browser/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()

    def _fetch_page(self, n: int) -> bytes:
        if n in self._page_cache:
            return self._page_cache[n]
        if len(self._page_cache) >= 64:
            oldest = next(iter(self._page_cache))
            if oldest != 1:
                del self._page_cache[oldest]
        start = (n - 1) * self.page_size
        end = n * self.page_size - 1
        data = self._http_range(start, end)
        self._page_cache[n] = data
        return data

    def _read_varint(self, data: bytes, offset: int):
        result = 0
        for i in range(9):
            if offset >= len(data):
                raise ValueError(f"varint read past end of buffer at offset {offset}")
            b = data[offset]
            offset += 1
            if i == 8:
                result = (result << 8) | b
            else:
                result = (result << 7) | (b & 0x7F)
                if not (b & 0x80):
                    break
        return result, offset

    def _parse_schema(self, page1: bytes):
        files_root = None
        index_root = None
        rows = self._read_leaf_table_page(page1, page_offset=100, page_num=1)
        for row in rows:
            if len(row) < 5:
                continue
            typ = row[0]
            name = row[1]
            rootpage = row[3]
            if typ == "table" and name == "files":
                files_root = rootpage
            elif typ == "index" and name == "idx_full_path":
                index_root = rootpage
        if files_root is None or index_root is None:
            raise ValueError(
                f"Could not find schema entries: files_root={files_root}, index_root={index_root}"
            )
        return files_root, index_root

    def _parse_record(self, data: bytes, offset: int):
        start = offset
        header_size, offset = self._read_varint(data, offset)
        header_end = start + header_size
        serial_types = []
        pos = offset
        while pos < header_end:
            st, pos = self._read_varint(data, pos)
            serial_types.append(st)
        data_pos = header_end
        values = []
        for st in serial_types:
            if st == 0:
                values.append(None)
            elif st == 1:
                values.append(struct.unpack_from(">b", data, data_pos)[0])
                data_pos += 1
            elif st == 2:
                values.append(struct.unpack_from(">h", data, data_pos)[0])
                data_pos += 2
            elif st == 3:
                b = data[data_pos:data_pos + 3]
                data_pos += 3
                v = struct.unpack(">i", b"\x00" + b)[0]
                if b[0] & 0x80:
                    v -= 1 << 24
                values.append(v)
            elif st == 4:
                values.append(struct.unpack_from(">i", data, data_pos)[0])
                data_pos += 4
            elif st == 5:
                b = data[data_pos:data_pos + 6]
                data_pos += 6
                values.append(int.from_bytes(b, "big", signed=True))
            elif st == 6:
                values.append(struct.unpack_from(">q", data, data_pos)[0])
                data_pos += 8
            elif st == 7:
                values.append(struct.unpack_from(">d", data, data_pos)[0])
                data_pos += 8
            elif st == 8:
                values.append(0)
            elif st == 9:
                values.append(1)
            elif st >= 12 and st % 2 == 0:
                size = (st - 12) // 2
                values.append(data[data_pos:data_pos + size])
                data_pos += size
            elif st >= 13 and st % 2 == 1:
                size = (st - 13) // 2
                values.append(data[data_pos:data_pos + size].decode("utf-8", errors="replace"))
                data_pos += size
            else:
                values.append(None)
        return values

    def _get_cell_payload(self, page_data: bytes, cell_offset: int, page_num: int, is_index_leaf: bool):
        pos = cell_offset
        if is_index_leaf:
            payload_size, pos = self._read_varint(page_data, pos)
            rowid = None
        else:
            payload_size, pos = self._read_varint(page_data, pos)
            rowid, pos = self._read_varint(page_data, pos)

        usable = self.page_size - 5
        if is_index_leaf:
            max_local = usable - 35
            min_local = ((usable - 12) * 32 // 255) - 23
        else:
            max_local = usable - 35

        if payload_size <= max_local:
            payload = page_data[pos:pos + payload_size]
        else:
            if is_index_leaf:
                local_size = min_local + ((payload_size - min_local) % (usable - 4))
                if local_size > max_local:
                    local_size = min_local
            else:
                local_size = max_local
            payload = bytearray(page_data[pos:pos + local_size])
            overflow_page = struct.unpack_from(">I", page_data, pos + local_size)[0]
            while overflow_page != 0 and len(payload) < payload_size:
                op_data = self._fetch_page(overflow_page)
                next_overflow = struct.unpack_from(">I", op_data, 0)[0]
                chunk = op_data[4:4 + (usable - 4)]
                needed = payload_size - len(payload)
                payload.extend(chunk[:needed])
                overflow_page = next_overflow
            payload = bytes(payload)

        return payload, rowid

    def _read_leaf_table_page(self, page_data: bytes, page_offset: int = 0, page_num: int = 1):
        hdr_start = page_offset
        page_type = page_data[hdr_start]
        if page_type != 13:
            return []
        num_cells = struct.unpack_from(">H", page_data, hdr_start + 3)[0]
        cell_ptr_array_start = hdr_start + 8
        rows = []
        for i in range(num_cells):
            ptr_offset = cell_ptr_array_start + i * 2
            cell_offset = struct.unpack_from(">H", page_data, ptr_offset)[0]
            payload, rowid = self._get_cell_payload(page_data, cell_offset, page_num, is_index_leaf=False)
            values = self._parse_record(payload, 0)
            rows.append(values)
        return rows

    def _lookup_rowid_in_index(self, full_path: str) -> int | None:
        page_num = self._index_root
        while True:
            page_data = self._fetch_page(page_num)
            page_type = page_data[0]
            num_cells = struct.unpack_from(">H", page_data, 3)[0]
            cell_ptr_array_start = 12 if page_type == 2 else 8

            if page_type == 10:
                for i in range(num_cells):
                    ptr_offset = cell_ptr_array_start + i * 2
                    cell_offset = struct.unpack_from(">H", page_data, ptr_offset)[0]
                    payload, _ = self._get_cell_payload(page_data, cell_offset, page_num, is_index_leaf=True)
                    values = self._parse_record(payload, 0)
                    if len(values) >= 1 and values[0] == full_path:
                        if len(values) >= 2:
                            return values[-1]
                return None

            if page_type == 2:
                rightmost = struct.unpack_from(">I", page_data, 8)[0]
                chosen_child = rightmost
                for i in range(num_cells):
                    ptr_offset = cell_ptr_array_start + i * 2
                    cell_offset = struct.unpack_from(">H", page_data, ptr_offset)[0]
                    child_page = struct.unpack_from(">I", page_data, cell_offset)[0]
                    payload, _ = self._get_cell_payload(page_data, cell_offset + 4, page_num, is_index_leaf=True)
                    values = self._parse_record(payload, 0)
                    key = values[0] if values else None
                    if key is not None and full_path < key:
                        chosen_child = child_page
                        break
                page_num = chosen_child
                continue

            return None

    def _lookup_row_by_rowid(self, rowid: int) -> list | None:
        page_num = self._files_root
        while True:
            page_data = self._fetch_page(page_num)
            page_type = page_data[0]
            num_cells = struct.unpack_from(">H", page_data, 3)[0]

            if page_type == 13:
                cell_ptr_array_start = 8
                for i in range(num_cells):
                    ptr_offset = cell_ptr_array_start + i * 2
                    cell_offset = struct.unpack_from(">H", page_data, ptr_offset)[0]
                    payload, row_rowid = self._get_cell_payload(page_data, cell_offset, page_num, is_index_leaf=False)
                    if row_rowid == rowid:
                        return self._parse_record(payload, 0)
                return None

            if page_type == 5:
                cell_ptr_array_start = 12
                rightmost = struct.unpack_from(">I", page_data, 8)[0]
                chosen = rightmost
                for i in range(num_cells):
                    ptr_offset = cell_ptr_array_start + i * 2
                    cell_offset = struct.unpack_from(">H", page_data, ptr_offset)[0]
                    child_page = struct.unpack_from(">I", page_data, cell_offset)[0]
                    key_rowid, _ = self._read_varint(page_data, cell_offset + 4)
                    if rowid <= key_rowid:
                        chosen = child_page
                        break
                page_num = chosen
                continue

            return None

    def lookup(self, full_path: str) -> dict | None:
        rowid = self._lookup_rowid_in_index(full_path)
        if rowid is None:
            return None
        row = self._lookup_row_by_rowid(rowid)
        if row is None:
            return None
        cols = [
            "id",
            "full_path",
            "file_name",
            "size",
            "md5",
            "sha1",
            "sha256",
            "crc32",
            "torrents",
            "so_id",
            "magnet",
        ]
        result = {}
        for i, col in enumerate(cols):
            result[col] = row[i] if i < len(row) else None
        return result


try:
    import libtorrent as lt
    _LT_AVAILABLE = True
except ImportError:
    _LT_AVAILABLE = False


class TorrentEngine:
    def __init__(self):
        if not _LT_AVAILABLE:
            raise RuntimeError("libtorrent not available")
        settings = {
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
        }
        self._session = lt.session(settings)
        self._session.add_dht_router("router.bittorrent.com", 6881)
        self._session.add_dht_router("router.utorrent.com", 6881)
        self._handles = {}
        self._meta = {}
        self._finished_ids: set[str] = set()  # prevent duplicate finished events
        self.events = queue.Queue()
        self._running = True
        self._thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._thread.start()

    def add_download(self, torrent_source: str, so_id: int, file_name: str, save_path: str,
                     download_id: str = None, auto_extract: bool = False, delete_archive: bool = False) -> str:
        """Add a download. torrent_source is a local .torrent file path, a .torrent URL, or a magnet URI."""
        download_id = download_id or str(uuid.uuid4())

        def _do():
            try:
                if torrent_source.startswith("magnet:"):
                    params = lt.parse_magnet_uri(torrent_source)
                    params.save_path = save_path
                    handle = self._session.add_torrent(params)
                    self._handles[download_id] = handle
                    self._meta[download_id] = {
                        "name": file_name,
                        "so_id": so_id,
                        "save_path": save_path,
                        "auto_extract": auto_extract,
                        "delete_archive": delete_archive,
                        "waiting_metadata": True,
                    }
                else:
                    # Local file path or remote URL
                    local_path = pathlib.Path(torrent_source)
                    if local_path.exists():
                        torrent_data = local_path.read_bytes()
                    else:
                        req = urllib.request.Request(
                            torrent_source, headers={"User-Agent": "MiNERVA-Browser/1.0"}
                        )
                        with urllib.request.urlopen(req, timeout=30) as resp:
                            torrent_data = resp.read()
                    ti = lt.torrent_info(lt.bdecode(torrent_data))
                    num_files = ti.num_files()
                    priorities = [0] * num_files
                    if 0 <= so_id < num_files:
                        priorities[so_id] = 4
                    params = lt.add_torrent_params()
                    params.ti = ti
                    params.save_path = save_path
                    params.file_priorities = priorities
                    handle = self._session.add_torrent(params)
                    self._handles[download_id] = handle
                    self._meta[download_id] = {
                        "name": file_name,
                        "so_id": so_id,
                        "save_path": save_path,
                        "auto_extract": auto_extract,
                        "delete_archive": delete_archive,
                        "waiting_metadata": False,
                    }
            except Exception as e:
                self.events.put({"type": "error", "id": download_id, "msg": str(e)})

        threading.Thread(target=_do, daemon=True).start()
        return download_id

    def _set_single_file_priority(self, handle, so_id: int):
        try:
            ti = handle.torrent_file()
            if ti is None:
                return
            num_files = ti.num_files()
            priorities = [0] * num_files
            if 0 <= so_id < num_files:
                priorities[so_id] = 4
            handle.prioritize_files(priorities)
        except Exception:
            pass

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
                                self._set_single_file_priority(handle, meta["so_id"])
                                meta["waiting_metadata"] = False
            for did, handle in list(self._handles.items()):
                if not handle.is_valid():
                    continue
                try:
                    s = handle.status()
                    state_map = {
                        lt.torrent_status.checking_files: "Checking",
                        lt.torrent_status.downloading_metadata: "Metadata",
                        lt.torrent_status.downloading: "Downloading",
                        lt.torrent_status.finished: "Seeding",
                        lt.torrent_status.seeding: "Seeding",
                        lt.torrent_status.allocating: "Allocating",
                        lt.torrent_status.checking_resume_data: "Checking",
                    }
                    state_str = state_map.get(s.state, str(s.state))
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
                        "paused": s.paused,
                        "error": s.errc.message() if s.errc else "",
                    })
                    if state_str in ("Seeding", "Finished") and not s.paused:
                        if did not in self._finished_ids:
                            self._finished_ids.add(did)
                            self.events.put({"type": "finished", "id": did})
                except Exception:
                    pass

    def get_all_statuses(self) -> dict:
        statuses = {}
        for did, handle in list(self._handles.items()):
            if not handle.is_valid():
                continue
            try:
                s = handle.status()
                state_map = {
                    lt.torrent_status.checking_files: "Checking",
                    lt.torrent_status.downloading_metadata: "Metadata",
                    lt.torrent_status.downloading: "Downloading",
                    lt.torrent_status.finished: "Seeding",
                    lt.torrent_status.seeding: "Seeding",
                    lt.torrent_status.allocating: "Allocating",
                    lt.torrent_status.checking_resume_data: "Checking",
                }
                state_str = state_map.get(s.state, str(s.state))
                statuses[did] = {
                    "name": self._meta[did]["name"],
                    "progress": s.progress,
                    "download_rate": s.download_rate,
                    "upload_rate": s.upload_rate,
                    "state": state_str,
                    "num_peers": s.num_peers,
                    "total_done": s.total_done,
                    "total": s.total_wanted,
                    "paused": s.paused,
                    "error": s.errc.message() if s.errc else "",
                }
            except Exception:
                pass
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
        except Exception:
            pass


class DownloadQueue:
    """
    Manages a list of pending + active downloads.
    Automatically starts the next queued item when a slot frees up.
    """
    def __init__(self, engine: "TorrentEngine", max_active: int = 3):
        self.engine = engine
        self.max_active = max_active
        # Ordered dicts so insertion order = queue order
        self._pending: dict[str, dict] = {}   # id -> {name, source, so_id, save_path}
        self._active: dict[str, dict] = {}    # id -> same dict
        self._done: dict[str, dict] = {}      # id -> {name, save_path, status:'done'|'error', error:''}
        self._lock = threading.Lock()

    def enqueue(self, download_id: str, name: str, source: str, so_id: int, save_path: str,
                auto_extract: bool = False, delete_archive: bool = False):
        item = {"id": download_id, "name": name, "source": source, "so_id": so_id,
                "save_path": save_path, "auto_extract": auto_extract, "delete_archive": delete_archive}
        with self._lock:
            self._pending[download_id] = item
        self._try_advance()

    def _try_advance(self):
        with self._lock:
            while len(self._active) < self.max_active and self._pending:
                did, item = next(iter(self._pending.items()))
                del self._pending[did]
                self._active[did] = item
                self.engine.add_download(
                    item["source"], item["so_id"], item["name"], item["save_path"],
                    download_id=did,
                    auto_extract=item.get("auto_extract", False),
                    delete_archive=item.get("delete_archive", False),
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
        if was_active:
            self.engine.remove_handle(download_id)
        self._try_advance()

    def clear_done(self):
        with self._lock:
            self._done.clear()

    def set_max_active(self, n: int):
        self.max_active = max(1, n)
        self._try_advance()

    def has_name(self, name: str) -> bool:
        """Return True if a download with this file name is pending, active, or done."""
        with self._lock:
            for d in (*self._pending.values(), *self._active.values(), *self._done.values()):
                if d.get("name") == name:
                    return True
        return False

    def snapshot(self) -> dict:
        """Return a shallow copy of queue state for UI rendering (thread-safe)."""
        with self._lock:
            return {
                "pending": list(self._pending.values()),
                "active": list(self._active.keys()),
                "done": list(self._done.values()),
            }




class MinervaApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MiNERVA Archive Browser")
        self.geometry("1100x650")
        self.configure(bg=BG)
        self._current_path = BROWSE_ROOT
        self._all_entries = []
        self._torrent_engine: TorrentEngine | None = None
        self._download_queue: DownloadQueue | None = None
        self._download_dir = tk.StringVar(value=get_default_download_dir())
        self._auto_extract_var = tk.BooleanVar(value=False)
        self._delete_archive_var = tk.BooleanVar(value=False)
        self._seven_zip_path = find_7zip_executable()
        self._extract_tool_var = tk.StringVar(
            value=(
                f"7-Zip detected: {self._seven_zip_path}"
                if self._seven_zip_path else
                "7-Zip not found; using Python extraction for ZIPs"
            )
        )
        self._extract_status_var = tk.StringVar(value="")
        # per-download extraction progress: id -> {pct, status}
        self._extract_progress: dict[str, dict] = {}
        # track per-id widget dicts for active and done rows
        self._dl_active_widgets: dict[str, dict] = {}
        self._dl_done_widgets: dict[str, dict] = {}
        self._checked_hrefs: set[str] = set()
        self._setup_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_left_tree()
        self._navigate(BROWSE_ROOT)

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=ENTRY_BG,
                        troughcolor=PANEL, bordercolor=PANEL, darkcolor=PANEL,
                        lightcolor=PANEL, selectbackground=SEL_BG, selectforeground=FG,
                        font=("TkDefaultFont", 10))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Dim.TLabel", background=BG, foreground=FG_DIM)
        style.configure("Status.TLabel", background=PANEL, foreground=FG_DIM, font=("TkDefaultFont", 9))
        style.configure("Loading.TLabel", background=BG, foreground=ACCENT, font=("TkDefaultFont", 10, "italic"))
        style.configure("Breadcrumb.TLabel", background=BG, foreground=FG_DIM, font=("TkDefaultFont", 10))
        style.configure("BreadcrumbLink.TLabel", background=BG, foreground=ACCENT,
                        font=("TkDefaultFont", 10, "underline"), cursor="hand2")
        style.configure("Toolbar.TFrame", background=PANEL)
        style.configure("Toolbar.TButton", background=PANEL, foreground=FG,
                        bordercolor=ACCENT, focuscolor=ACCENT, padding=(8, 4))
        style.map("Toolbar.TButton", background=[("active", SEL_BG)])
        style.configure("Left.Treeview", background=PANEL, foreground=FG,
                        fieldbackground=PANEL, borderwidth=0, rowheight=24)
        style.map("Left.Treeview", background=[("selected", SEL_BG)], foreground=[("selected", FG)])
        style.configure("Left.Treeview.Heading", background=PANEL, foreground=ACCENT,
                        font=("TkDefaultFont", 10, "bold"))
        style.configure("Right.Treeview", background=PANEL, foreground=FG,
                        fieldbackground=PANEL, borderwidth=0, rowheight=24)
        style.map("Right.Treeview", background=[("selected", SEL_BG)], foreground=[("selected", FG)])
        style.configure("Right.Treeview.Heading", background=PANEL, foreground=ACCENT,
                        font=("TkDefaultFont", 10, "bold"))
        style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=FG,
                        insertcolor=FG, bordercolor=ACCENT, relief="flat", padding=4)
        style.configure("TScrollbar", background=PANEL, troughcolor=BG,
                        arrowcolor=FG_DIM, bordercolor=PANEL, darkcolor=PANEL, lightcolor=PANEL)

    def _build_ui(self):
        toolbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(8, 4))
        toolbar.pack(fill="x", side="top")
        ttk.Label(toolbar, text="\U0001f5c2  MiNERVA Archive Browser",
                  background=PANEL, foreground=ACCENT,
                  font=("TkDefaultFont", 12, "bold")).pack(side="left", padx=(0, 16))
        self._open_btn = ttk.Button(toolbar, text="\U0001f310 Open in Browser",
                                    style="Toolbar.TButton", command=self._open_in_browser)
        self._open_btn.pack(side="left", padx=4)
        self._loading_label = ttk.Label(toolbar, text="", style="Loading.TLabel", background=PANEL)
        self._loading_label.pack(side="right", padx=8)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=0, pady=0)

        left_frame = ttk.Frame(paned, style="Panel.TFrame", width=250)
        left_frame.pack_propagate(False)
        paned.add(left_frame, weight=0)
        ttk.Label(left_frame, text="Categories", background=PANEL, foreground=ACCENT,
                  font=("TkDefaultFont", 11, "bold"), padding=(8, 6)).pack(fill="x")
        left_scroll = ttk.Scrollbar(left_frame, orient="vertical")
        self._left_tree = ttk.Treeview(left_frame, style="Left.Treeview",
                                       yscrollcommand=left_scroll.set,
                                       show="tree", selectmode="browse")
        left_scroll.config(command=self._left_tree.yview)
        left_scroll.pack(side="right", fill="y")
        self._left_tree.pack(fill="both", expand=True)
        self._left_tree.bind("<<TreeviewSelect>>", self._on_left_select)

        right_frame = ttk.Frame(paned, style="TFrame")
        paned.add(right_frame, weight=1)

        self._breadcrumb_frame = ttk.Frame(right_frame, padding=(8, 4))
        self._breadcrumb_frame.pack(fill="x")
        self._update_breadcrumb()

        search_frame = ttk.Frame(right_frame, padding=(8, 2))
        search_frame.pack(fill="x")
        ttk.Label(search_frame, text="\U0001f50d", background=BG, foreground=FG_DIM).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)
        search_entry = ttk.Entry(search_frame, textvariable=self._search_var, style="TEntry")
        search_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))

        cols = ("check", "name", "size")
        right_scroll_y = ttk.Scrollbar(right_frame, orient="vertical")
        right_scroll_x = ttk.Scrollbar(right_frame, orient="horizontal")
        self._right_tree = ttk.Treeview(right_frame, style="Right.Treeview",
                                        columns=cols, show="headings",
                                        yscrollcommand=right_scroll_y.set,
                                        xscrollcommand=right_scroll_x.set,
                                        selectmode="extended")
        right_scroll_y.config(command=self._right_tree.yview)
        right_scroll_x.config(command=self._right_tree.xview)
        self._right_tree.heading("check", text="")
        self._right_tree.column("check", width=28, stretch=False, anchor="center", minwidth=28)
        self._right_tree.heading("name", text="Name")
        self._right_tree.column("name", stretch=True, minwidth=200)
        self._right_tree.heading("size", text="Size")
        self._right_tree.column("size", width=100, stretch=False, anchor="e")
        right_scroll_y.pack(side="right", fill="y")
        right_scroll_x.pack(side="bottom", fill="x")
        self._right_tree.pack(fill="both", expand=True, padx=(8, 0), pady=(4, 0))
        self._right_tree.bind("<Double-1>", self._on_right_double_click)
        self._right_tree.bind("<Button-1>", self._on_right_click)

        # Inline selection action bar (hidden until items are checked)
        self._sel_bar = tk.Frame(right_frame, bg=PANEL, pady=4)
        # Don't pack it yet — shown dynamically

        self._sel_count_lbl = tk.Label(
            self._sel_bar, text="", bg=PANEL, fg=FG,
            font=("TkDefaultFont", 10)
        )
        self._sel_count_lbl.pack(side="left", padx=(10, 8))

        self._sel_queue_btn = ttk.Button(
            self._sel_bar, text="⬇ Queue Downloads",
            style="Toolbar.TButton",
            command=self._queue_checked_downloads
        )
        self._sel_queue_btn.pack(side="left", padx=4)

        ttk.Button(
            self._sel_bar, text="✕ Clear Selection",
            style="Toolbar.TButton",
            command=self._clear_checked
        ).pack(side="left", padx=4)

        self._downloads_visible = False

        # Downloads container (hidden by default)
        self._downloads_frame = tk.Frame(self, bg=PANEL)

        # ── header row inside the panel ────────────────────────────────────
        hdr = tk.Frame(self._downloads_frame, bg=PANEL)
        hdr.pack(fill="x", padx=6, pady=(4, 2))

        tk.Label(hdr, text="Max concurrent:", bg=PANEL, fg=FG_DIM,
                 font=("TkDefaultFont", 9)).pack(side="left")
        self._max_concurrent_var = tk.IntVar(value=3)
        max_spin = tk.Spinbox(hdr, from_=1, to=10, width=3,
                              textvariable=self._max_concurrent_var,
                              command=self._on_max_concurrent_change,
                              bg=ENTRY_BG, fg=FG, buttonbackground=PANEL,
                              relief="flat", font=("TkDefaultFont", 9))
        max_spin.pack(side="left", padx=(2, 12))

        tk.Label(hdr, text="Save to:", bg=PANEL, fg=FG_DIM,
                 font=("TkDefaultFont", 9)).pack(side="left")
        dir_entry = tk.Entry(hdr, textvariable=self._download_dir, width=32,
                             bg=ENTRY_BG, fg=FG, insertbackground=FG,
                             relief="flat", font=("TkDefaultFont", 9))
        dir_entry.pack(side="left", padx=(2, 2))
        ttk.Button(hdr, text="Browse…", style="Toolbar.TButton",
                   command=self._browse_download_dir).pack(side="left", padx=(0, 8))

        ttk.Button(hdr, text="Clear Completed", style="Toolbar.TButton",
                   command=self._clear_completed).pack(side="right", padx=4)

        tk.Checkbutton(
            hdr,
            text="Auto extract",
            variable=self._auto_extract_var,
            bg=PANEL,
            fg=FG,
            selectcolor=PANEL,
            activebackground=PANEL,
            activeforeground=FG,
            relief="flat",
            command=self._on_extract_options_change,
        ).pack(side="right", padx=(0, 8))

        tk.Checkbutton(
            hdr,
            text="Delete archive after extract",
            variable=self._delete_archive_var,
            bg=PANEL,
            fg=FG,
            selectcolor=PANEL,
            activebackground=PANEL,
            activeforeground=FG,
            relief="flat",
            command=self._on_extract_options_change,
        ).pack(side="right", padx=(0, 8))

        info_row = tk.Frame(self._downloads_frame, bg=PANEL)
        info_row.pack(fill="x", padx=6, pady=(0, 2))
        tk.Label(
            info_row,
            textvariable=self._extract_tool_var,
            bg=PANEL,
            fg=FG_DIM,
            font=("TkDefaultFont", 8),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            info_row,
            textvariable=self._extract_status_var,
            bg=PANEL,
            fg=ACCENT,
            font=("TkDefaultFont", 8),
            anchor="e",
        ).pack(side="right")

        # Separator
        tk.Frame(self._downloads_frame, bg=SEL_BG, height=1).pack(fill="x", padx=4)

        # Scrollable inner area
        dl_canvas_frame = tk.Frame(self._downloads_frame, bg=PANEL)
        dl_canvas_frame.pack(fill="both", expand=True)
        dl_canvas = tk.Canvas(dl_canvas_frame, bg=PANEL, bd=0,
                              highlightthickness=0, height=180)
        dl_scrollbar = ttk.Scrollbar(dl_canvas_frame, orient="vertical",
                                     command=dl_canvas.yview)
        dl_scrollbar.pack(side="right", fill="y")
        dl_canvas.pack(side="left", fill="both", expand=True)
        dl_canvas.configure(yscrollcommand=dl_scrollbar.set)

        self._dl_inner = tk.Frame(dl_canvas, bg=PANEL)
        self._dl_canvas_window = dl_canvas.create_window(
            (0, 0), window=self._dl_inner, anchor="nw"
        )
        self._dl_inner.bind("<Configure>",
            lambda e: dl_canvas.configure(scrollregion=dl_canvas.bbox("all")))
        dl_canvas.bind("<Configure>",
            lambda e: dl_canvas.itemconfig(self._dl_canvas_window, width=e.width))

        # The toggle button sits just above status bar
        self._downloads_toggle_btn = ttk.Button(
            self,
            text="📥 Downloads",
            style="Toolbar.TButton",
            command=self._toggle_downloads,
        )
        self._downloads_toggle_btn.pack(side="bottom", fill="x")

        self._poll_downloads()

        self._status_var = tk.StringVar(value="")
        status_bar = ttk.Label(self, textvariable=self._status_var, style="Status.TLabel",
                               relief="flat", padding=(8, 3))
        status_bar.pack(fill="x", side="bottom")

    def _update_breadcrumb(self):
        for w in self._breadcrumb_frame.winfo_children():
            w.destroy()
        parts = [p for p in self._current_path.split("/") if p]
        paths = []
        cumulative = "/"
        for p in parts:
            cumulative = cumulative.rstrip("/") + "/" + p + "/"
            paths.append((urllib.parse.unquote(p), cumulative))

        for i, (label, path) in enumerate(paths):
            display = label if label != "browse" else "\U0001f3e0 Home"
            lbl = ttk.Label(self._breadcrumb_frame, text=display, style="BreadcrumbLink.TLabel")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, p=path: self._navigate(p))
            if i < len(paths) - 1:
                ttk.Label(self._breadcrumb_frame, text=" \u203a ", style="Breadcrumb.TLabel").pack(side="left")

    def _set_loading(self, loading: bool):
        self._loading_label.config(text="\u23f3 Loading\u2026" if loading else "")

    def _load_left_tree(self):
        self._set_loading(True)

        def worker():
            try:
                entries = fetch_entries(BROWSE_ROOT)
            except Exception as e:
                entries = []
                self.after(0, lambda: self._show_error(str(e)))
            self.after(0, lambda: self._populate_left_tree(entries))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_left_tree(self, entries):
        self._set_loading(False)
        self._left_tree.delete(*self._left_tree.get_children())
        for e in entries:
            if e["is_folder"]:
                display = "\U0001f4c1 " + e["name"]
                iid = e["href"]
                self._left_tree.insert("", "end", iid=iid, text=display, tags=("folder",))

    def _on_left_select(self, event):
        sel = self._left_tree.selection()
        if sel:
            path = sel[0]
            self._navigate(path)

    def _navigate(self, path):
        self._current_path = path
        self._search_var.set("")
        self._update_breadcrumb()
        self._set_loading(True)
        self._right_tree.delete(*self._right_tree.get_children())
        self._checked_hrefs.clear()
        self._status_var.set("Loading\u2026")

        def worker():
            try:
                entries = fetch_entries(path)
                self.after(0, lambda: self._populate_right(entries))
            except Exception as e:
                self.after(0, lambda: self._show_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_right(self, entries):
        self._set_loading(False)
        self._all_entries = entries
        self._checked_hrefs.clear()
        self._right_tree.delete(*self._right_tree.get_children())
        for e in entries:
            icon = "\U0001f4c1 " if e["is_folder"] else "\U0001f4c4 "
            self._right_tree.insert("", "end", iid=e["href"],
                                    values=("", icon + e["name"], e["size"]),
                                    tags=("folder" if e["is_folder"] else "file",))
        self._update_sel_bar()
        self._update_status(entries)

    def _update_status(self, entries):
        folders = sum(1 for e in entries if e["is_folder"])
        files = sum(1 for e in entries if not e["is_folder"])
        parts = []
        if folders:
            parts.append(f"{folders} folder{'s' if folders != 1 else ''}")
        if files:
            parts.append(f"{files} file{'s' if files != 1 else ''}")
        total = len(entries)
        self._status_var.set(f"{', '.join(parts)} ({total} items total)  |  {self._current_path}")

    def _on_search_change(self, *_):
        query = self._search_var.get().lower()
        self._right_tree.delete(*self._right_tree.get_children())
        filtered = [e for e in self._all_entries if query in e["name"].lower()] if query else self._all_entries
        for e in filtered:
            icon = "\U0001f4c1 " if e["is_folder"] else "\U0001f4c4 "
            self._right_tree.insert("", "end", iid=e["href"],
                                    values=("", icon + e["name"], e["size"]),
                                    tags=("folder" if e["is_folder"] else "file",))
        self._update_sel_bar()
        self._update_status(filtered)

    def _on_right_double_click(self, event):
        sel = self._right_tree.selection()
        if not sel:
            return
        href = sel[0]
        entry = next((e for e in self._all_entries if e["href"] == href), None)
        if entry is None:
            return
        if entry["is_folder"]:
            self._navigate(href)
        else:
            # Queue the file for download inline instead of opening in browser
            if not _LT_AVAILABLE:
                messagebox.showinfo(
                    "libtorrent required",
                    "Install libtorrent to enable downloads:\n  pip install libtorrent",
                )
                return
            full_path = urllib.parse.unquote(href.split("name=")[1])
            file_name = full_path.split("/")[-1]
            save_path = self.get_download_dir()
            download_id = str(uuid.uuid4())
            threading.Thread(
                target=self._lookup_and_enqueue,
                args=(download_id, full_path, file_name, save_path),
                daemon=True,
            ).start()
            if not self._downloads_visible:
                self._toggle_downloads()

    def _open_in_browser(self):
        sel = self._right_tree.selection()
        if sel:
            href = sel[0]
            webbrowser.open(BASE_URL + href)
        else:
            webbrowser.open(BASE_URL + self._current_path)

    def get_torrent_engine(self) -> "TorrentEngine | None":
        if not _LT_AVAILABLE:
            messagebox.showinfo(
                "libtorrent required",
                "Install libtorrent to enable downloads:\n  pip install libtorrent",
            )
            return None
        if self._torrent_engine is None:
            try:
                self._torrent_engine = TorrentEngine()
                self._download_queue = DownloadQueue(self._torrent_engine, max_active=3)
            except Exception as e:
                messagebox.showerror("Engine Error", f"Could not start torrent engine:\n{e}")
                return None
        return self._torrent_engine

    def get_download_dir(self) -> str:
        return self._download_dir.get() or get_default_download_dir()

    def enqueue_download(self, download_id: str, name: str, source: str, so_id: int, save_path: str):
        engine = self.get_torrent_engine()
        if engine is None:
            return
        auto_extract = bool(self._auto_extract_var.get())
        delete_archive = bool(self._delete_archive_var.get())
        self._download_queue.enqueue(download_id, name, source, so_id, save_path,
                                     auto_extract=auto_extract, delete_archive=delete_archive)
        if not self._downloads_visible:
            self._toggle_downloads()

    def _toggle_downloads(self):
        self._downloads_visible = not self._downloads_visible
        if self._downloads_visible:
            self._downloads_frame.pack(side="bottom", fill="x", before=self._downloads_toggle_btn)
        else:
            self._downloads_frame.pack_forget()

    def _refresh_toggle_label(self):
        if self._download_queue is None:
            self._downloads_toggle_btn.config(text="📥 Downloads")
            return
        snap = self._download_queue.snapshot()
        n_active = len(snap["active"])
        n_pending = len(snap["pending"])
        n_done = len(snap["done"])
        parts = []
        if n_active: parts.append(f"{n_active} active")
        if n_pending: parts.append(f"{n_pending} queued")
        if n_done: parts.append(f"{n_done} done")
        label = "📥 Downloads"
        if parts:
            label += "  (" + "  •  ".join(parts) + ")"
        self._downloads_toggle_btn.config(text=label)

    def _poll_downloads(self):
        if self._torrent_engine is not None and self._download_queue is not None:
            # Drain engine events
            while True:
                try:
                    event = self._torrent_engine.events.get_nowait()
                except queue.Empty:
                    break
                etype = event.get("type")
                did = event.get("id", "")
                if etype == "finished":
                    self._download_queue.on_finished(did)
                    # Extract if checkbox is on OR if the download was queued with auto_extract=True
                    meta = self._torrent_engine._meta.get(did) if self._torrent_engine else None
                    should_extract = self._auto_extract_var.get() or bool(meta and meta.get("auto_extract"))
                    if should_extract:
                        self._extract_download(did)
                elif etype == "error":
                    self._download_queue.on_finished(did, error=event.get("msg", "Unknown error"))

            # Rebuild the inner panel from queue snapshot
            snap = self._download_queue.snapshot()
            self._rebuild_dl_panel(snap)
            self._refresh_toggle_label()

        self.after(500, self._poll_downloads)

    def _rebuild_dl_panel(self, snap: dict):
        active_ids = set(snap["active"])
        statuses = self._torrent_engine.get_all_statuses() if self._torrent_engine else {}

        # ── remove widgets for ids no longer present ──
        gone = [did for did in list(self._dl_active_widgets) if did not in active_ids]
        for did in gone:
            w = self._dl_active_widgets.pop(did)
            w["frame"].destroy()

        # ── active downloads ──
        for did in snap["active"]:
            st = statuses.get(did, {})
            if did not in self._dl_active_widgets:
                self._make_active_row(did, st.get("name", did))
            self._update_active_row(did, st)

        # ── queued items (re-render each poll cycle — list is usually short) ──
        if not hasattr(self, "_dl_queued_frame"):
            self._dl_queued_frame = tk.Frame(self._dl_inner, bg=PANEL)
            self._dl_queued_frame.pack(fill="x")
        for w in self._dl_queued_frame.winfo_children():
            w.destroy()

        if snap["pending"]:
            tk.Label(self._dl_queued_frame, text="  QUEUED",
                     bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 8, "bold")).pack(anchor="w", padx=6)
            for item in snap["pending"]:
                self._make_queued_row(self._dl_queued_frame, item)

        # ── completed ──
        if not hasattr(self, "_dl_done_frame"):
            self._dl_done_frame = tk.Frame(self._dl_inner, bg=PANEL)
            self._dl_done_frame.pack(fill="x")

        done_ids = {item["id"] for item in snap["done"]}

        # Remove rows no longer in done list
        gone_done = [did for did in list(self._dl_done_widgets) if did not in done_ids]
        for did in gone_done:
            w = self._dl_done_widgets.pop(did)
            w["frame"].destroy()

        # Add/update done rows
        if snap["done"]:
            if not hasattr(self, "_dl_done_header") or not self._dl_done_header.winfo_exists():
                self._dl_done_header = tk.Label(
                    self._dl_done_frame, text="  COMPLETED",
                    bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 8, "bold"))
                self._dl_done_header.pack(anchor="w", padx=6)
            for item in snap["done"]:
                did = item["id"]
                if did not in self._dl_done_widgets:
                    self._make_done_row(self._dl_done_frame, item)
        else:
            # No done items — destroy header if present
            if hasattr(self, "_dl_done_header") and self._dl_done_header.winfo_exists():
                self._dl_done_header.destroy()
                del self._dl_done_header

    def _make_active_row(self, did: str, name: str):
        row = tk.Frame(self._dl_inner, bg=PANEL)
        row.pack(fill="x", before=self._dl_queued_frame if hasattr(self, "_dl_queued_frame") else None)

        name_lbl = tk.Label(row, text="📄 " + (name[:42] + "…" if len(name) > 42 else name),
                            bg=PANEL, fg=FG, font=("TkDefaultFont", 9), anchor="w", width=46)
        name_lbl.pack(side="left", padx=(6, 4))

        pv = tk.DoubleVar(value=0)
        pb = ttk.Progressbar(row, variable=pv, maximum=100,
                             mode="determinate", length=140)
        pb.pack(side="left", padx=(0, 4))

        pct_lbl = tk.Label(row, text="0%", bg=PANEL, fg=FG,
                           font=("TkDefaultFont", 9), width=5, anchor="e")
        pct_lbl.pack(side="left")

        speed_lbl = tk.Label(row, text="↓ —", bg=PANEL, fg=FG_DIM,
                             font=("TkDefaultFont", 9), width=11)
        speed_lbl.pack(side="left", padx=4)

        state_lbl = tk.Label(row, text="—", bg=PANEL, fg=FG,
                             font=("TkDefaultFont", 9), width=11)
        state_lbl.pack(side="left")

        pause_btn = ttk.Button(row, text="⏸", width=2,
                               command=lambda d=did: self._toggle_pause(d))
        pause_btn.pack(side="left", padx=2)

        cancel_btn = ttk.Button(row, text="✕", width=2,
                                command=lambda d=did: self._cancel_download(d))
        cancel_btn.pack(side="left", padx=(0, 4))

        self._dl_active_widgets[did] = {
            "frame": row,
            "pv": pv,
            "pct_lbl": pct_lbl,
            "speed_lbl": speed_lbl,
            "state_lbl": state_lbl,
            "pause_btn": pause_btn,
        }

    def _update_active_row(self, did: str, st: dict):
        w = self._dl_active_widgets.get(did)
        if not w:
            return
        pct = st.get("progress", 0) * 100
        w["pv"].set(pct)
        w["pct_lbl"].config(text=f"{pct:.0f}%")
        rate = st.get("download_rate", 0)
        w["speed_lbl"].config(text=self._fmt_rate(rate))
        state = st.get("state", "—")
        w["state_lbl"].config(text=state)
        paused = st.get("paused", False)
        w["pause_btn"].config(text="▶" if paused else "⏸")

    def _make_queued_row(self, parent: tk.Frame, item: dict):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x")
        name = item["name"]
        tk.Label(row, text="🕐 " + (name[:48] + "…" if len(name) > 48 else name),
                 bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 9), anchor="w").pack(side="left", padx=(6, 4))
        tk.Label(row, text="Queued", bg=PANEL, fg=FG_DIM,
                 font=("TkDefaultFont", 9), anchor="w").pack(side="left")
        ttk.Button(row, text="✕", width=2,
                   command=lambda d=item["id"]: self._cancel_download(d)
                   ).pack(side="right", padx=4)

    def _make_done_row(self, parent: tk.Frame, item: dict):
        did = item["id"]
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x")

        icon = "✅" if item["status"] == "done" else "❌"
        name = item["name"]
        clr = FG if item["status"] == "done" else "#f38ba8"
        lbl_text = f"{icon} " + (name[:40] + "…" if len(name) > 40 else name)
        if item.get("error"):
            lbl_text += f"  ({item['error'][:36]})"
        tk.Label(row, text=lbl_text, bg=PANEL, fg=clr,
                 font=("TkDefaultFont", 9), anchor="w", width=50).pack(side="left", padx=(6, 4))

        # Extraction progress bar — shown when auto_extract is active
        ext_pv = tk.DoubleVar(value=0)
        ext_pb = ttk.Progressbar(row, variable=ext_pv, maximum=100,
                                 mode="determinate", length=100)
        ext_pb.pack(side="left", padx=(0, 2))

        ext_lbl = tk.Label(row, text="", bg=PANEL, fg=ACCENT,
                           font=("TkDefaultFont", 8), width=16, anchor="w")
        ext_lbl.pack(side="left", padx=(0, 4))

        self._dl_done_widgets[did] = {
            "frame": row,
            "ext_pv": ext_pv,
            "ext_pb": ext_pb,
            "ext_lbl": ext_lbl,
        }

        # Apply any already-known extraction progress
        if did in self._extract_progress:
            self._apply_extract_progress(did)

    def _refresh_extract_rows(self):
        """Called from the main thread to update extraction progress in done rows."""
        for did in list(self._extract_progress.keys()):
            self._apply_extract_progress(did)

    def _apply_extract_progress(self, did: str):
        w = self._dl_done_widgets.get(did)
        info = self._extract_progress.get(did)
        if not w or not info:
            return
        pct = info.get("pct", 0)
        status = info.get("status", "")
        try:
            w["ext_pv"].set(pct)
            w["ext_lbl"].config(text=status)
        except tk.TclError:
            pass

    @staticmethod
    def _fmt_rate(r: float) -> str:
        if r <= 0:
            return "↓ —"
        if r < 1024:
            return f"↓ {r:.0f} B/s"
        if r < 1024 * 1024:
            return f"↓ {r / 1024:.1f} KB/s"
        return f"↓ {r / 1024 / 1024:.1f} MB/s"

    def _on_max_concurrent_change(self):
        if self._download_queue is not None:
            try:
                n = int(self._max_concurrent_var.get())
                self._download_queue.set_max_active(n)
            except (ValueError, tk.TclError):
                pass

    def _browse_download_dir(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self, initialdir=self.get_download_dir())
        if path:
            self._download_dir.set(path)

    def _clear_completed(self):
        if self._download_queue:
            self._download_queue.clear_done()
        # Remove done widgets
        for did, w in list(self._dl_done_widgets.items()):
            try:
                w["frame"].destroy()
            except tk.TclError:
                pass
        self._dl_done_widgets.clear()
        # Clear extraction progress for done items
        self._extract_progress.clear()
        if hasattr(self, "_dl_done_header") and self._dl_done_header.winfo_exists():
            self._dl_done_header.destroy()
            del self._dl_done_header

    def _on_extract_options_change(self):
        enabled = bool(self._auto_extract_var.get())
        delete_archive = bool(self._delete_archive_var.get())
        if self._download_queue:
            snap = self._download_queue.snapshot()
            for did in snap["active"]:
                if self._torrent_engine:
                    self._torrent_engine.set_auto_extract(did, enabled)
                    self._torrent_engine.set_delete_archive(did, delete_archive)
            for item in snap["pending"]:
                if self._torrent_engine:
                    self._torrent_engine.set_auto_extract(item["id"], enabled)
                    self._torrent_engine.set_delete_archive(item["id"], delete_archive)

    def _toggle_pause(self, download_id: str):
        engine = self._torrent_engine
        if engine is None:
            return
        statuses = engine.get_all_statuses()
        s = statuses.get(download_id)
        if s and s.get("paused"):
            engine.resume(download_id)
        else:
            engine.pause(download_id)

    def _cancel_download(self, download_id: str):
        if self._download_queue:
            self._download_queue.cancel(download_id)
        w = self._dl_active_widgets.pop(download_id, None)
        if w:
            w["frame"].destroy()
        self._refresh_toggle_label()

    def _on_right_click(self, event):
        col = self._right_tree.identify_column(event.x)
        if col == "#1":
            iid = self._right_tree.identify_row(event.y)
            if iid and "/rom?name=" in iid:
                if iid in self._checked_hrefs:
                    self._checked_hrefs.discard(iid)
                    self._right_tree.set(iid, "check", "")
                else:
                    self._checked_hrefs.add(iid)
                    self._right_tree.set(iid, "check", "✓")
                self._update_sel_bar()
                return "break"

    def _update_sel_bar(self):
        n = len(self._checked_hrefs)
        if n == 0:
            self._sel_bar.pack_forget()
        else:
            if not self._sel_bar.winfo_ismapped():
                self._sel_bar.pack(fill="x")
            self._sel_count_lbl.config(text=f"✓ {n} file{'s' if n != 1 else ''} selected")
            self._sel_queue_btn.config(text=f"⬇ Queue {n} Download{'s' if n != 1 else ''}")

    def _clear_checked(self):
        for href in list(self._checked_hrefs):
            try:
                self._right_tree.set(href, "check", "")
            except tk.TclError:
                pass
        self._checked_hrefs.clear()
        self._update_sel_bar()

    def _queue_checked_downloads(self):
        if not _LT_AVAILABLE:
            messagebox.showinfo(
                "libtorrent required",
                "Install libtorrent to enable downloads:\n  pip install libtorrent",
            )
            return
        hrefs = list(self._checked_hrefs)
        if not hrefs:
            return
        save_path = self.get_download_dir()
        for href in hrefs:
            full_path = urllib.parse.unquote(href.split("name=")[1])
            file_name = full_path.split("/")[-1]
            download_id = str(uuid.uuid4())
            threading.Thread(
                target=self._lookup_and_enqueue,
                args=(download_id, full_path, file_name, save_path),
                daemon=True
            ).start()
        self._clear_checked()
        if not self._downloads_visible:
            self._toggle_downloads()

    def _lookup_and_enqueue(self, download_id: str, full_path: str, file_name: str, save_path: str):
        """Background thread: look up file in hashes.db, download the .torrent file, then enqueue."""
        # Deduplicate: skip if already pending/active/done
        if self._download_queue and self._download_queue.has_name(file_name):
            return

        # The href from the site is already the full DB key, e.g.:
        # ./No-Intro/Nintendo - Nintendo 64 (ByteSwapped)/Game.zip
        # Pass it directly — no stripping needed.
        self.after(0, lambda: self._status_var.set(f"Looking up: {file_name}…"))

        try:
            db = SQLiteHTTP(HASHES_DB_URL)
            row = db.lookup(full_path)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                "DB Lookup Failed", f"Could not look up {file_name}:\n{e}"
            ))
            return

        if row is None:
            self.after(0, lambda: messagebox.showwarning(
                "Not Found", f"{file_name} was not found in the database."
            ))
            return

        so_id = row.get("so_id") or 0

        # Prefer .torrent file; fall back to magnet
        torrent_url = None
        if row.get("torrents"):
            # URL-encode the path component (spaces etc.) but keep the base URL intact
            encoded_path = urllib.parse.quote(row["torrents"], safe="/")
            torrent_url = "https://minerva-archive.org/assets/" + encoded_path

        if torrent_url:
            try:
                torrent_dir = get_torrent_dir()
                # Use a safe filename derived from the torrent path
                torrent_filename = row["torrents"].replace("/", "_").replace("\\", "_")
                torrent_local = torrent_dir / torrent_filename
                if not torrent_local.exists():
                    req = urllib.request.Request(
                        torrent_url, headers={"User-Agent": "MiNERVA-Browser/1.0"}
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        torrent_data = resp.read()
                    torrent_local.write_bytes(torrent_data)
                torrent_source = str(torrent_local)
            except Exception as e:
                # Fall back to magnet if torrent file download fails
                torrent_source = None
                if row.get("magnet"):
                    torrent_source = row["magnet"] + TRACKERS
                if torrent_source is None:
                    self.after(0, lambda: messagebox.showerror(
                        "Torrent Download Failed", f"Could not download torrent for {file_name}:\n{e}"
                    ))
                    return
        elif row.get("magnet"):
            torrent_source = row["magnet"] + TRACKERS
        else:
            self.after(0, lambda: messagebox.showwarning(
                "No Torrent", f"No torrent info found for {file_name}."
            ))
            return

        self.after(0, lambda: self.enqueue_download(
            download_id=download_id,
            name=file_name,
            source=torrent_source,
            so_id=so_id,
            save_path=save_path,
        ))

    def _find_downloaded_file(self, save_path: pathlib.Path, file_name: str) -> pathlib.Path | None:
        """Locate the downloaded file under save_path. libtorrent may place it in a subdirectory."""
        # Direct path first
        direct = save_path / file_name
        if direct.exists():
            return direct
        # Search up to 3 levels deep for the file name
        for depth in range(1, 4):
            pattern = "/".join(["*"] * depth) + f"/{file_name}"
            matches = list(save_path.glob(pattern))
            if matches:
                return matches[0]
        return None

    def _extract_download(self, download_id: str):
        if not self._torrent_engine:
            return
        meta = self._torrent_engine._meta.get(download_id)
        if not meta:
            return

        save_path = pathlib.Path(meta["save_path"])
        torrent_dir = save_path / "extracted"
        torrent_dir.mkdir(parents=True, exist_ok=True)
        delete_archive = bool(meta.get("delete_archive"))
        seven_zip = self._seven_zip_path
        file_name = meta["name"]

        def _set_progress(pct: int, status: str):
            self._extract_progress[download_id] = {"pct": pct, "status": status}
            self.after(0, self._refresh_extract_rows)

        def worker():
            try:
                src = self._find_downloaded_file(save_path, file_name)
                if src is None:
                    _set_progress(0, f"Missing: {file_name}")
                    return

                _set_progress(0, "Extracting…")
                extracted_ok = False

                if seven_zip:
                    out_dir = torrent_dir / src.stem
                    out_dir.mkdir(parents=True, exist_ok=True)
                    cmd = [seven_zip, "x", "-y", "-bd", "-bso1", "-bsp1", f"-o{out_dir}", str(src)]
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                    )
                    last_pct = -1
                    if proc.stdout is not None:
                        for line in proc.stdout:
                            m = re.search(r"(\d{1,3})%", line)
                            if m:
                                pct = min(100, int(m.group(1)))
                                if pct != last_pct:
                                    last_pct = pct
                                    _set_progress(pct, f"Extracting… {pct}%")
                    rc = proc.wait()
                    if rc != 0:
                        raise RuntimeError(f"7-Zip exited with code {rc}")
                    extracted_ok = True

                elif src.suffix.lower() == ".zip":
                    out_dir = torrent_dir / src.stem
                    out_dir.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(src, "r") as zf:
                        members = zf.infolist()
                        total = max(1, len(members))
                        for i, member in enumerate(members, start=1):
                            zf.extract(member, out_dir)
                            pct = int(i * 100 / total)
                            _set_progress(pct, f"Extracting… {pct}%")
                    extracted_ok = True

                else:
                    shutil.copy2(src, torrent_dir / src.name)
                    extracted_ok = True

                if extracted_ok and delete_archive and src.exists():
                    src.unlink()

                _set_progress(100, "Extracted ✓" if extracted_ok else "Failed")

            except Exception as e:
                _set_progress(0, f"Error: {str(e)[:40]}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self):
        if self._torrent_engine is not None:
            try:
                self._torrent_engine.shutdown()
            except Exception:
                pass
        self.destroy()

    def _show_error(self, msg):
        self._set_loading(False)
        self._right_tree.delete(*self._right_tree.get_children())
        self._status_var.set(f"Error: {msg}")
        messagebox.showerror("Error", f"Failed to load page:\n{msg}")


if __name__ == "__main__":
    app = MinervaApp()
    app.mainloop()
