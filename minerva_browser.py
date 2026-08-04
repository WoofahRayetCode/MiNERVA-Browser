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
import time
import zipfile
import hashlib
import traceback
import json
from datetime import datetime
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
_LOG_LOCK = threading.Lock()


def get_default_download_dir() -> str:
    """Return the folder next to the exe (frozen) or next to this script (dev)."""
    if getattr(sys, "frozen", False):
        return str(pathlib.Path(sys.executable).parent)
    return str(pathlib.Path(__file__).parent)


def get_runtime_base_dir() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).parent
    return pathlib.Path(__file__).parent


def get_torrent_dir() -> pathlib.Path:
    """Return (and create) the torrentfiles/ folder next to the exe / script."""
    base = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else pathlib.Path(__file__).parent
    d = base / "torrentfiles"
    d.mkdir(exist_ok=True)
    return d


def get_error_log_path() -> pathlib.Path:
    base = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else pathlib.Path(__file__).parent
    return base / "minerva_error.log"


def get_settings_path() -> pathlib.Path:
    base = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else pathlib.Path(__file__).parent
    return base / "minerva_settings.json"


def log_error(context: str, exc: Exception | None = None):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"[{ts}] {context}"]
        if exc is not None:
            lines.append(f"Exception: {repr(exc)}")
            lines.append(traceback.format_exc().rstrip())
        line = "\n".join(lines) + "\n\n"
        log_path = get_error_log_path()
        with _LOG_LOCK:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def log_activity(message: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}\n"
        log_path = get_error_log_path()
        with _LOG_LOCK:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def load_app_settings() -> dict:
    path = get_settings_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        raise ValueError("Settings file must contain a JSON object")
    except Exception as e:
        log_error(f"load_app_settings failed for {path}", e)
        return {}


def save_app_settings(settings: dict):
    path = get_settings_path()
    tmp_path = path.with_suffix(".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        tmp_path.replace(path)
    except Exception as e:
        log_error(f"save_app_settings failed for {path}", e)


def find_archive_extractors() -> list[dict]:
    """Find supported external archive tools in preferred order."""
    tools: list[dict] = []

    def _add_tool(kind: str, label: str, exe: str | None):
        if not exe:
            return
        p = pathlib.Path(exe)
        if not p.exists():
            return
        if any(t["kind"] == kind and pathlib.Path(t["exe"]).resolve() == p.resolve() for t in tools):
            return
        tools.append({"kind": kind, "label": label, "exe": str(p)})

    # 1) Native 7-Zip CLI
    for candidate in [
        shutil.which("7z"),
        shutil.which("7z.exe"),
        shutil.which("7za"),
        shutil.which("7za.exe"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]:
        _add_tool("7zip", "7-Zip", candidate)

    # 2) PeaZip's bundled 7z backend (CLI compatible with 7-Zip switches)
    for candidate in [
        r"C:\Program Files\PeaZip\res\bin\7z\7z.exe",
        r"C:\Program Files (x86)\PeaZip\res\bin\7z\7z.exe",
        r"C:\Program Files\PeaZip\res\7z\7z.exe",
        r"C:\Program Files (x86)\PeaZip\res\7z\7z.exe",
    ]:
        _add_tool("peazip", "PeaZip", candidate)

    # 3) WinRAR CLI
    for candidate in [
        shutil.which("winrar"),
        shutil.which("winrar.exe"),
        shutil.which("rar"),
        shutil.which("rar.exe"),
        r"C:\Program Files\WinRAR\WinRAR.exe",
        r"C:\Program Files (x86)\WinRAR\WinRAR.exe",
    ]:
        _add_tool("winrar", "WinRAR", candidate)

    return tools


def format_extractor_status(extractors: list[dict]) -> str:
    if not extractors:
        return "No external extractor found; using Python extraction for ZIPs"
    labels = [f"{tool['label']}: {tool['exe']}" for tool in extractors]
    return "Extractors detected: " + " | ".join(labels)


def find_chdman_executable() -> str | None:
    managed = get_runtime_base_dir() / "tools" / "chdman" / "chdman.exe"
    candidates = [
        str(managed),
        shutil.which("chdman"),
        shutil.which("chdman.exe"),
        str(pathlib.Path.home() / "scoop" / "apps" / "mame" / "current" / "chdman.exe"),
        r"C:\Program Files\MAME\chdman.exe",
        r"C:\Program Files (x86)\MAME\chdman.exe",
    ]
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).exists():
            return str(pathlib.Path(candidate))
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
                     download_id: str = None) -> str:
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
                        "delete_archive": False,
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
                    self._add_file_priority(handle, so_id)
                    self._handles[download_id] = handle
                    self._meta[download_id] = {
                        "name": file_name,
                        "so_id": so_id,
                        "save_path": save_path,
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
                priorities[so_id] = max(priorities[so_id], 4)
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
        """Return True only when the selected file index has all bytes downloaded."""
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
                    is_complete = self._is_target_file_complete(did, handle, s) or (
                        (state_str in ("Seeding", "Finished")) and s.progress >= 0.999
                    )
                    has_file = self._has_downloaded_file(did)
                    if is_complete and has_file and state_str in ("Seeding", "Finished") and not s.paused:
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
        """Remove torrent handle once download is complete, but keep metadata for post-processing."""
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
        except Exception:
            pass


class DownloadQueue:
    """
    Manages a list of pending + active downloads.
    Pending downloads do not start automatically until explicitly selected.
    """
    def __init__(self, engine: "TorrentEngine", max_active: int = 3):
        self.engine = engine
        self.max_active = max_active
        # Ordered dicts so insertion order = queue order
        self._pending: dict[str, dict] = {}   # id -> {name, source, so_id, save_path}
        self._active: dict[str, dict] = {}    # id -> same dict
        self._done: dict[str, dict] = {}      # id -> {name, save_path, status:'done'|'error', error:''}
        self._lock = threading.Lock()

    def enqueue(self, download_id: str, name: str, source: str, so_id: int, save_path: str):
        item = {"id": download_id, "name": name, "source": source, "so_id": so_id,
                "save_path": save_path, "start_requested": False}
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
                    None
                )
                if next_item is None:
                    break
                did, item = next_item
                del self._pending[did]
                self._active[did] = item
                self.engine.add_download(
                    item["source"], item["so_id"], item["name"], item["save_path"],
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

    def export_for_persistence(self) -> list[dict]:
        """Export queue items that can be restored on next app launch."""
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
        self._settings = load_app_settings()
        saved_download_dir = self._settings.get("download_dir")
        if not isinstance(saved_download_dir, str) or not saved_download_dir.strip():
            saved_download_dir = get_default_download_dir()
        self._download_dir = tk.StringVar(value=saved_download_dir)
        self._auto_extract_default_var = tk.BooleanVar(
            value=bool(self._settings.get("auto_extract_default", True))
        )
        self._delete_archive_default_var = tk.BooleanVar(
            value=bool(self._settings.get("delete_archive_default", True))
        )
        self._compress_ps1_chd_var = tk.BooleanVar(
            value=bool(self._settings.get("compress_ps1_chd", False))
        )
        self._show_tag_specs = [
            ("demo", "Demo"),
            ("beta", "Beta"),
            ("revision", "Revision"),
            ("proto", "Proto"),
            ("unlicensed", "Unlicensed"),
            ("hack", "Hack"),
            ("translation", "Translation"),
        ]
        self._show_region_specs = [
            ("usa", "USA"),
            ("europe", "Europe"),
            ("japan", "Japan"),
            ("world", "World"),
            ("asia", "Asia"),
            ("korea", "Korea"),
            ("china", "China"),
            ("australia", "Australia"),
            ("canada", "Canada"),
            ("brazil", "Brazil"),
            ("france", "France"),
            ("germany", "Germany"),
            ("italy", "Italy"),
            ("spain", "Spain"),
            ("netherlands", "Netherlands"),
            ("sweden", "Sweden"),
            ("russia", "Russia"),
            ("taiwan", "Taiwan"),
            ("hong_kong", "Hong Kong"),
            ("other", "Other"),
        ]
        saved_hidden_tags = self._settings.get("hidden_tags", [])
        if not isinstance(saved_hidden_tags, list):
            saved_hidden_tags = []
        saved_hidden_tags = set(t for t in saved_hidden_tags if isinstance(t, str))
        saved_regions = self._settings.get("show_regions", [])
        if not isinstance(saved_regions, list):
            saved_regions = []
        saved_regions = set(r for r in saved_regions if isinstance(r, str))
        self._show_tag_vars = {
            key: tk.BooleanVar(value=key in saved_hidden_tags)
            for key, _ in self._show_tag_specs
        }
        self._show_region_vars = {
            key: tk.BooleanVar(value=key in saved_regions)
            for key, _ in self._show_region_specs
        }
        self._extractors = find_archive_extractors()
        self._chdman_path = find_chdman_executable()
        self._extract_tool_var = tk.StringVar(value=format_extractor_status(self._extractors))
        self._extract_status_var = tk.StringVar(value="")
        self._chd_progress_var = tk.DoubleVar(value=0.0)
        # per-download extraction progress: id -> {pct, status}
        self._extract_progress: dict[str, dict] = {}
        self._extract_request_queue: queue.Queue[str | None] = queue.Queue()
        self._extract_pending_ids: set[str] = set()
        self._extract_pending_lock = threading.Lock()
        self._queued_selected_ids: set[str] = set()
        self._left_loaded_nodes: set[str] = set()
        self._left_loading_nodes: set[str] = set()
        self._dl_speed_samples: dict[str, tuple[int, float]] = {}
        self._chd_download_in_progress = False
        self._chd_compress_in_progress = False
        # track per-id widget dicts for active and done rows
        self._dl_active_widgets: dict[str, dict] = {}
        self._dl_queued_widgets: dict[str, dict] = {}
        self._dl_done_widgets: dict[str, dict] = {}
        self._checked_hrefs: set[str] = set()
        self._setup_styles()
        self._build_ui()
        self._download_dir.trace_add("write", self._on_download_dir_change)
        if self._compress_ps1_chd_var.get() and not self._chdman_path:
            self._ensure_chdman_available_async()
        self._restore_persisted_queue()
        self._extract_worker_thread = threading.Thread(target=self._extract_worker_loop, daemon=True)
        self._extract_worker_thread.start()
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
        style.configure("Header.TButton", background=PANEL, foreground=FG,
                        bordercolor=ACCENT, focuscolor=ACCENT, padding=(5, 2),
                        font=("TkDefaultFont", 9))
        style.map("Header.TButton", background=[("active", SEL_BG)])
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
        style.configure(
            "TScrollbar",
            background=SEL_BG,
            troughcolor=PANEL,
            arrowcolor=FG,
            bordercolor=SEL_BG,
            darkcolor=SEL_BG,
            lightcolor=SEL_BG,
            arrowsize=14,
        )
        style.map(
            "TScrollbar",
            background=[("active", ACCENT)],
            arrowcolor=[("active", FG)],
        )
        style.configure(
            "Visible.Vertical.TScrollbar",
            background=SEL_BG,
            troughcolor=PANEL,
            arrowcolor=FG,
            bordercolor=SEL_BG,
            darkcolor=SEL_BG,
            lightcolor=SEL_BG,
            arrowsize=14,
        )
        style.configure(
            "Visible.Horizontal.TScrollbar",
            background=SEL_BG,
            troughcolor=PANEL,
            arrowcolor=FG,
            bordercolor=SEL_BG,
            darkcolor=SEL_BG,
            lightcolor=SEL_BG,
            arrowsize=14,
        )
        style.map(
            "Visible.Vertical.TScrollbar",
            background=[("active", ACCENT)],
            arrowcolor=[("active", FG)],
        )
        style.map(
            "Visible.Horizontal.TScrollbar",
            background=[("active", ACCENT)],
            arrowcolor=[("active", FG)],
        )

    def _build_ui(self):
        toolbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(10, 6))
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
        self._main_paned = paned
        paned.pack(fill="both", expand=True, padx=0, pady=(2, 0))

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
        self._left_tree.bind("<<TreeviewOpen>>", self._on_left_open)

        right_frame = ttk.Frame(paned, style="TFrame")
        paned.add(right_frame, weight=1)

        self._breadcrumb_frame = ttk.Frame(right_frame, padding=(10, 6))
        self._breadcrumb_frame.pack(fill="x")
        self._update_breadcrumb()

        search_frame = ttk.Frame(right_frame, padding=(10, 2))
        search_frame.pack(fill="x")
        ttk.Label(search_frame, text="\U0001f50d", background=BG, foreground=FG_DIM).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)
        search_entry = ttk.Entry(search_frame, textvariable=self._search_var, style="TEntry")
        search_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))

        filter_tags_row = tk.Frame(right_frame, bg=BG)
        filter_tags_row.pack(fill="x", padx=10, pady=(4, 0))
        tk.Label(
            filter_tags_row,
            text="Hide tags:",
            bg=BG,
            fg=FG_DIM,
            font=("TkDefaultFont", 9)
        ).pack(side="left")
        for key, label in self._show_tag_specs:
            tk.Checkbutton(
                filter_tags_row,
                text=label,
                variable=self._show_tag_vars[key],
                bg=BG,
                fg=FG,
                selectcolor=BG,
                activebackground=BG,
                activeforeground=FG,
                relief="flat",
                command=self._on_filter_change
            ).pack(side="left", padx=(6, 0))

        filter_regions_row = tk.Frame(right_frame, bg=BG)
        filter_regions_row.pack(fill="x", padx=10, pady=(2, 4))
        tk.Label(
            filter_regions_row,
            text="Show only regions:",
            bg=BG,
            fg=FG_DIM,
            font=("TkDefaultFont", 9)
        ).pack(anchor="w")
        filter_regions_options = tk.Frame(filter_regions_row, bg=BG)
        filter_regions_options.pack(fill="x")
        for idx, (key, label) in enumerate(self._show_region_specs):
            tk.Checkbutton(
                filter_regions_options,
                text=label,
                variable=self._show_region_vars[key],
                bg=BG,
                fg=FG,
                selectcolor=BG,
                activebackground=BG,
                activeforeground=FG,
                relief="flat",
                command=self._on_filter_change
            ).grid(row=idx // 6, column=idx % 6, sticky="w", padx=(0, 10), pady=(0, 2))

        cols = ("check", "name", "size")
        right_scroll_y = ttk.Scrollbar(
            right_frame, orient="vertical", style="Visible.Vertical.TScrollbar"
        )
        right_scroll_x = ttk.Scrollbar(
            right_frame, orient="horizontal", style="Visible.Horizontal.TScrollbar"
        )
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
        right_scroll_y.pack(side="right", fill="y", padx=(0, 8))
        right_scroll_x.pack(side="bottom", fill="x", padx=(10, 8))
        self._right_tree.pack(fill="both", expand=True, padx=(10, 0), pady=(4, 0))
        self._right_tree.bind("<Double-1>", self._on_right_double_click)
        self._right_tree.bind("<Button-1>", self._on_right_click)

        # Inline selection action bar (hidden until items are checked)
        self._sel_bar = tk.Frame(right_frame, bg=PANEL, pady=6)
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

        self._downloads_visible = bool(self._settings.get("downloads_panel_open", False))

        # Downloads container (hidden by default)
        self._downloads_frame = tk.Frame(self, bg=PANEL)

        # ── header rows inside the panel ───────────────────────────────────
        hdr = tk.Frame(self._downloads_frame, bg=PANEL)
        hdr.pack(fill="x", padx=10, pady=(6, 2))

        tk.Label(hdr, text="Max concurrent:", bg=PANEL, fg=FG_DIM,
                 font=("TkDefaultFont", 9)).pack(side="left", padx=(0, 2))
        self._max_concurrent_var = tk.IntVar(value=self._get_saved_max_concurrent())
        max_spin = tk.Spinbox(hdr, from_=1, to=10, width=3,
                              textvariable=self._max_concurrent_var,
                              command=self._on_max_concurrent_change,
                              bg=ENTRY_BG, fg=FG, buttonbackground=PANEL,
                              relief="flat", font=("TkDefaultFont", 9))
        max_spin.pack(side="left", padx=(0, 10))

        tk.Label(hdr, text="Save to:", bg=PANEL, fg=FG_DIM,
                 font=("TkDefaultFont", 9)).pack(side="left", padx=(0, 4))
        dir_entry = tk.Entry(hdr, textvariable=self._download_dir, width=1,
                             bg=ENTRY_BG, fg=FG, insertbackground=FG,
                             relief="flat", font=("TkDefaultFont", 9))
        dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(hdr, text="Browse…", style="Header.TButton",
                   command=self._browse_download_dir).pack(side="left", padx=(0, 6))
        tk.Checkbutton(
            hdr,
            text="Auto extract",
            variable=self._auto_extract_default_var,
            bg=PANEL,
            fg=FG,
            selectcolor=PANEL,
            activebackground=PANEL,
            activeforeground=FG,
            relief="flat",
            command=self._on_extract_defaults_change,
        ).pack(side="left", padx=(4, 0))
        tk.Checkbutton(
            hdr,
            text="Delete archive",
            variable=self._delete_archive_default_var,
            bg=PANEL,
            fg=FG,
            selectcolor=PANEL,
            activebackground=PANEL,
            activeforeground=FG,
            relief="flat",
            command=self._on_extract_defaults_change,
        ).pack(side="left", padx=(4, 0))
        tk.Checkbutton(
            hdr,
            text="Compress PS1 to CHD",
            variable=self._compress_ps1_chd_var,
            bg=PANEL,
            fg=FG,
            selectcolor=PANEL,
            activebackground=PANEL,
            activeforeground=FG,
            relief="flat",
            command=self._on_extract_defaults_change,
        ).pack(side="left", padx=(6, 0))

        hdr_actions = tk.Frame(self._downloads_frame, bg=PANEL)
        hdr_actions.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Button(
            hdr_actions,
            text="Open Downloads",
            style="Header.TButton",
            command=self._open_current_downloads_folder
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            hdr_actions,
            text="Open Extracted",
            style="Header.TButton",
            command=self._open_current_extracted_folder
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            hdr_actions,
            text="Verify Extracted",
            style="Header.TButton",
            command=self._verify_extracted_button_click
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            hdr_actions,
            text="Compress PS1→CHD",
            style="Header.TButton",
            command=self._compress_ps1_button_click
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            hdr_actions,
            text="Clean BIN/CUE",
            style="Header.TButton",
            command=self._clean_bin_cue_button_click
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            hdr_actions,
            text="Clean Names",
            style="Header.TButton",
            command=self._clean_chd_names_button_click
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            hdr_actions,
            text="Delete BINs",
            style="Header.TButton",
            command=self._force_delete_bins_button_click
        ).pack(side="left", padx=(0, 6))
        ttk.Button(hdr_actions, text="Pause/Resume", style="Header.TButton",
                   command=self._toggle_pause_all_active).pack(side="left", padx=(8, 6))
        ttk.Button(hdr_actions, text="Start All Queued", style="Header.TButton",
                   command=self._start_all_queued).pack(side="left", padx=(0, 6))
        ttk.Button(hdr_actions, text="Start Selected", style="Header.TButton",
                   command=self._start_selected_queued).pack(side="left", padx=(0, 6))
        ttk.Button(hdr_actions, text="Clear Completed", style="Header.TButton",
                   command=self._clear_completed).pack(side="left", padx=(0, 0))

        info_row = tk.Frame(self._downloads_frame, bg=PANEL)
        info_row.pack(fill="x", padx=10, pady=(0, 4))
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
        ttk.Progressbar(
            info_row,
            variable=self._chd_progress_var,
            maximum=100,
            mode="determinate",
            length=180,
        ).pack(side="right", padx=(0, 8))

        # Separator
        tk.Frame(self._downloads_frame, bg=SEL_BG, height=1).pack(fill="x", padx=8)

        # Scrollable inner area
        dl_canvas_frame = tk.Frame(self._downloads_frame, bg=PANEL)
        dl_canvas_frame.pack(fill="both", expand=True)
        dl_canvas = tk.Canvas(dl_canvas_frame, bg=PANEL, bd=0,
                              highlightthickness=0, height=180)
        dl_scrollbar = ttk.Scrollbar(
            dl_canvas_frame,
            orient="vertical",
            style="Visible.Vertical.TScrollbar",
            command=dl_canvas.yview
        )
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

        # The toggle button sits near the top for quicker access
        self._downloads_toggle_btn = ttk.Button(
            self,
            text="📥 Downloads",
            style="Toolbar.TButton",
            command=self._toggle_downloads,
        )
        self._downloads_toggle_btn.pack(side="top", fill="x", padx=8, pady=(6, 0), before=self._main_paned)
        if self._downloads_visible:
            self._downloads_frame.pack(side="top", fill="x", pady=(6, 0), before=self._main_paned)

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
                log_error("MinervaApp._load_left_tree failed", e)
                self.after(0, lambda: self._show_error(str(e)))
            self.after(0, lambda: self._populate_left_tree(entries))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_left_tree(self, entries):
        self._set_loading(False)
        self._left_tree.delete(*self._left_tree.get_children())
        self._left_loaded_nodes.clear()
        self._left_loading_nodes.clear()
        self._left_loaded_nodes.add(BROWSE_ROOT)
        for e in entries:
            if e["is_folder"]:
                self._insert_left_folder("", e)

    def _on_left_select(self, event):
        sel = self._left_tree.selection()
        if sel:
            path = sel[0]
            self._expand_left_path(path)
            self._navigate(path)

    def _on_left_open(self, event):
        path = self._left_tree.focus()
        if path:
            self._expand_left_path(path)

    def _insert_left_folder(self, parent_iid: str, entry: dict):
        display = "\U0001f4c1 " + entry["name"]
        iid = entry["href"]
        if self._left_tree.exists(iid):
            return
        self._left_tree.insert(parent_iid, "end", iid=iid, text=display, tags=("folder",))
        # Add placeholder so tree item can be expanded before children are loaded.
        self._left_tree.insert(iid, "end", text="")

    def _expand_left_path(self, path: str):
        if path in self._left_loaded_nodes or path in self._left_loading_nodes:
            return
        if not self._left_tree.exists(path):
            return
        self._left_loading_nodes.add(path)

        def worker():
            try:
                entries = fetch_entries(path)
                self.after(0, lambda: self._populate_left_children(path, entries))
            except Exception as e:
                log_error(f"MinervaApp._expand_left_path failed for path={path}", e)
                self.after(0, lambda: self._left_loading_nodes.discard(path))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_left_children(self, parent_path: str, entries: list[dict]):
        self._left_loading_nodes.discard(parent_path)
        if not self._left_tree.exists(parent_path):
            return
        self._left_tree.delete(*self._left_tree.get_children(parent_path))
        for e in entries:
            if e.get("is_folder"):
                self._insert_left_folder(parent_path, e)
        self._left_loaded_nodes.add(parent_path)

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
                log_error(f"MinervaApp._navigate failed for path={path}", e)
                self.after(0, lambda: self._show_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_right(self, entries):
        self._set_loading(False)
        self._all_entries = entries
        self._render_right_list()

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
        self._render_right_list()

    def _on_filter_change(self):
        self._render_right_list()
        self._save_settings()

    def _render_right_list(self):
        query = self._search_var.get().lower()
        filtered = [e for e in self._all_entries if self._entry_matches_filters(e, query)]
        visible_files = [e for e in filtered if not e.get("is_folder", False)]
        self._right_tree.delete(*self._right_tree.get_children())
        visible_hrefs = {e["href"] for e in visible_files}
        self._checked_hrefs.intersection_update(visible_hrefs)
        for e in visible_files:
            icon = "\U0001f4c4 "
            self._right_tree.insert("", "end", iid=e["href"],
                                    values=("", icon + e["name"], e["size"]),
                                    tags=("file",))
            if e["href"] in self._checked_hrefs:
                self._right_tree.set(e["href"], "check", "✓")
        self._update_sel_bar()
        self._update_status(visible_files)

    def _entry_matches_filters(self, entry: dict, query: str) -> bool:
        name = entry.get("name", "")
        low = name.lower()
        if query and query not in low:
            return False

        if not entry.get("is_folder", False):
            selected_tags = {key for key, var in self._show_tag_vars.items() if var.get()}
            selected_regions = {key for key, var in self._show_region_vars.items() if var.get()}

            if selected_tags:
                tags = self._detect_release_tags(low)
                if tags.intersection(selected_tags):
                    return False
            if selected_regions:
                regions = self._detect_regions(low)
                if not regions.intersection(selected_regions):
                    return False

        return True

    @staticmethod
    def _detect_release_tags(name_lower: str) -> set[str]:
        tags = set()
        if "(demo" in name_lower or " demo" in name_lower:
            tags.add("demo")
        if "(beta" in name_lower or " beta" in name_lower:
            tags.add("beta")
        if "(rev" in name_lower or "(revision" in name_lower:
            tags.add("revision")
        if "(proto" in name_lower or "prototype" in name_lower:
            tags.add("proto")
        if "(unl" in name_lower or "unlicensed" in name_lower:
            tags.add("unlicensed")
        if "(hack" in name_lower or "hack)" in name_lower:
            tags.add("hack")
        if "(translation" in name_lower or "(t+" in name_lower:
            tags.add("translation")
        return tags

    @staticmethod
    def _detect_regions(name_lower: str) -> set[str]:
        regions = set()

        def has_any(*needles: str) -> bool:
            return any(n in name_lower for n in needles)

        if has_any("(usa", "(us", "(u)", "usa/", "/usa", "usa,"):
            regions.add("usa")
        if has_any("(europe", "(eu", "(e)", "europe/", "/europe", "europe,"):
            regions.add("europe")
        if has_any("(japan", "(jp", "(j)", "japan/", "/japan", "japan,"):
            regions.add("japan")
        if has_any("(world", "(w)", "(global"):
            regions.add("world")
        if has_any("(asia", "(a)"):
            regions.add("asia")
        if has_any("(korea", "(kr", "(k)"):
            regions.add("korea")
        if has_any("(china", "(cn", "(c)"):
            regions.add("china")
        if has_any("(australia", "(au"):
            regions.add("australia")
        if has_any("(canada", "(ca"):
            regions.add("canada")
        if has_any("(brazil", "(br"):
            regions.add("brazil")
        if has_any("(france", "(fr", "(f)"):
            regions.add("france")
        if has_any("(germany", "(de", "(g)"):
            regions.add("germany")
        if has_any("(italy", "(it", "(i)"):
            regions.add("italy")
        if has_any("(spain", "(es", "(s)"):
            regions.add("spain")
        if has_any("(netherlands", "(nl"):
            regions.add("netherlands")
        if has_any("(sweden", "(se", "(sw)"):
            regions.add("sweden")
        if has_any("(russia", "(ru"):
            regions.add("russia")
        if has_any("(taiwan", "(tw"):
            regions.add("taiwan")
        if has_any("(hong kong", "(hk"):
            regions.add("hong_kong")

        # Handle compact combo region codes commonly seen in ROM sets, e.g. (UE), (JU), (JUE)
        for grp in re.findall(r"\(([^)]*)\)", name_lower):
            compact = re.sub(r"[^a-z]", "", grp)
            if compact in {"u", "e", "j", "w", "ue", "uj", "uw", "ej", "ew", "jw", "uej", "uew", "ujw", "ejw", "uejw"}:
                if "u" in compact:
                    regions.add("usa")
                if "e" in compact:
                    regions.add("europe")
                if "j" in compact:
                    regions.add("japan")
                if "w" in compact:
                    regions.add("world")

        if not regions:
            regions.add("other")
        return regions

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

    def get_torrent_engine(self, show_errors: bool = True) -> "TorrentEngine | None":
        if not _LT_AVAILABLE:
            if show_errors:
                messagebox.showinfo(
                    "libtorrent required",
                    "Install libtorrent to enable downloads:\n  pip install libtorrent",
                )
            return None
        if self._torrent_engine is None:
            try:
                self._torrent_engine = TorrentEngine()
                self._download_queue = DownloadQueue(
                    self._torrent_engine,
                    max_active=self._get_current_max_concurrent()
                )
            except Exception as e:
                log_error("MinervaApp.get_torrent_engine failed to start engine", e)
                if show_errors:
                    messagebox.showerror("Engine Error", f"Could not start torrent engine:\n{e}")
                return None
        return self._torrent_engine

    def get_download_dir(self) -> str:
        return self._download_dir.get() or get_default_download_dir()

    def enqueue_download(self, download_id: str, name: str, source: str, so_id: int, save_path: str):
        engine = self.get_torrent_engine()
        if engine is None:
            return
        self._download_queue.enqueue(download_id, name, source, so_id, save_path)
        self._save_settings()
        if not self._downloads_visible:
            self._toggle_downloads()

    def _toggle_downloads(self):
        self._downloads_visible = not self._downloads_visible
        if self._downloads_visible:
            self._downloads_frame.pack(side="top", fill="x", pady=(6, 0), before=self._main_paned)
        else:
            self._downloads_frame.pack_forget()
        self._save_settings()

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
            finished_ids = []
            queue_changed = False
            while True:
                try:
                    event = self._torrent_engine.events.get_nowait()
                except queue.Empty:
                    break
                etype = event.get("type")
                did = event.get("id", "")
                if etype == "finished":
                    self._normalize_downloaded_file_location(did)
                    self._torrent_engine.stop_seeding(did)
                    self._download_queue.on_finished(did)
                    finished_ids.append(did)
                    queue_changed = True
                elif etype == "error":
                    self._download_queue.on_finished(did, error=event.get("msg", "Unknown error"))
                    queue_changed = True
            if finished_ids:
                self._prompt_post_download_actions_batch(finished_ids)
            if queue_changed:
                self._save_settings()

            # Rebuild the inner panel from queue snapshot
            snap = self._download_queue.snapshot()
            self._rebuild_dl_panel(snap)
            self._refresh_toggle_label()

        self.after(500, self._poll_downloads)

    def _normalize_downloaded_file_location(self, download_id: str):
        """Move completed archive from mirrored category folders to save_path root."""
        if not self._torrent_engine:
            return
        meta = self._torrent_engine._meta.get(download_id)
        if not meta:
            return
        file_name = meta.get("name", "")
        if not file_name:
            return
        save_path = pathlib.Path(meta.get("save_path", ""))
        if not str(save_path):
            return
        target = save_path / file_name
        if target.exists():
            return
        try:
            src = None
            for _ in range(10):
                src = self._find_downloaded_file(save_path, file_name)
                if src is not None:
                    break
                time.sleep(1)
            if src is None or src == target:
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            src.replace(target)
            log_activity(f"download.flatten id={download_id} src='{src}' dst='{target}'")
        except Exception as e:
            log_error(
                f"MinervaApp._normalize_downloaded_file_location failed for {file_name}",
                e
            )

    def _rebuild_dl_panel(self, snap: dict):
        active_ids = set(snap["active"])
        pending_ids = {item["id"] for item in snap["pending"]}
        self._queued_selected_ids.intersection_update(pending_ids)
        statuses = self._torrent_engine.get_all_statuses() if self._torrent_engine else {}

        # ── remove widgets for ids no longer present ──
        gone = [did for did in list(self._dl_active_widgets) if did not in active_ids]
        for did in gone:
            w = self._dl_active_widgets.pop(did)
            w["frame"].destroy()
            self._dl_speed_samples.pop(did, None)

        # ── active downloads ──
        for did in snap["active"]:
            st = statuses.get(did, {})
            if did not in self._dl_active_widgets:
                self._make_active_row(did, st.get("name", did))
            self._update_active_row(did, st)

        # ── queued items ──
        if not hasattr(self, "_dl_queued_frame"):
            self._dl_queued_frame = tk.Frame(self._dl_inner, bg=PANEL)
            self._dl_queued_frame.pack(fill="x")
        pending_map = {item["id"]: item for item in snap["pending"]}
        gone_pending = [did for did in list(self._dl_queued_widgets) if did not in pending_map]
        for did in gone_pending:
            w = self._dl_queued_widgets.pop(did)
            w["frame"].destroy()

        if pending_map:
            if not hasattr(self, "_dl_queued_header") or not self._dl_queued_header.winfo_exists():
                self._dl_queued_header = tk.Label(
                    self._dl_queued_frame, text="  QUEUED",
                    bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 8, "bold")
                )
                self._dl_queued_header.pack(anchor="w", padx=6)
            for item in snap["pending"]:
                did = item["id"]
                if did not in self._dl_queued_widgets:
                    self._make_queued_row(self._dl_queued_frame, item)
                self._update_queued_row(did, item)
        else:
            if hasattr(self, "_dl_queued_header") and self._dl_queued_header.winfo_exists():
                self._dl_queued_header.destroy()
                del self._dl_queued_header

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

        pause_btn = ttk.Button(row, text="⏸", width=3,
                               command=lambda d=did: self._toggle_pause(d))
        pause_btn.pack(side="left", padx=(2, 3))

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
        state = st.get("state", "—")
        paused = st.get("paused", False)
        total_done = int(st.get("total_done", 0) or 0)
        now = time.monotonic()
        sample = self._dl_speed_samples.get(did)
        calc_rate = 0.0
        if sample is not None:
            prev_done, prev_ts = sample
            delta_bytes = max(0, total_done - prev_done)
            delta_t = max(1e-6, now - prev_ts)
            calc_rate = delta_bytes / delta_t
        self._dl_speed_samples[did] = (total_done, now)
        raw_rate = float(st.get("download_rate", 0) or 0.0)
        rate = calc_rate if (state == "Downloading" and not paused and calc_rate > 0) else raw_rate
        w["speed_lbl"].config(text=self._fmt_rate(rate))
        w["state_lbl"].config(text=state)
        w["pause_btn"].config(text="▶" if paused else "⏸")

    def _make_queued_row(self, parent: tk.Frame, item: dict):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x")
        did = item["id"]
        is_selected = did in self._queued_selected_ids
        sel_var = tk.BooleanVar(value=is_selected)
        tk.Checkbutton(
            row,
            variable=sel_var,
            bg=PANEL,
            fg=FG,
            selectcolor=PANEL,
            activebackground=PANEL,
            activeforeground=FG,
            relief="flat",
            command=lambda d=did, v=sel_var: self._set_queued_selected(d, v.get())
        ).pack(side="left", padx=(4, 2))
        name = item["name"]
        tk.Label(row, text="🕐 " + (name[:48] + "…" if len(name) > 48 else name),
                 bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 9), anchor="w").pack(side="left", padx=(2, 4))
        state_lbl = tk.Label(row, text="Queued", bg=PANEL, fg=FG_DIM,
                             font=("TkDefaultFont", 9), anchor="w")
        state_lbl.pack(side="left")
        ttk.Button(row, text="Start", width=6,
                   command=lambda d=did: self._start_specific_queued(d)
                   ).pack(side="right", padx=(0, 4))
        ttk.Button(row, text="✕", width=2,
                   command=lambda d=did: self._cancel_download(d)
                   ).pack(side="right", padx=4)
        self._dl_queued_widgets[did] = {
            "frame": row,
            "sel_var": sel_var,
            "state_lbl": state_lbl,
        }

    def _update_queued_row(self, download_id: str, item: dict):
        w = self._dl_queued_widgets.get(download_id)
        if not w:
            return
        try:
            w["sel_var"].set(download_id in self._queued_selected_ids)
            status = "Ready" if item.get("start_requested") else "Queued"
            w["state_lbl"].config(text=status)
        except tk.TclError:
            pass

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

        open_btn = ttk.Button(
            row,
            text="Open",
            width=5,
            command=lambda p=item.get("save_path", ""): self._open_folder(pathlib.Path(p))
        )
        open_btn.pack(side="right", padx=(0, 4))

        open_extracted_btn = ttk.Button(
            row,
            text="Extracted",
            width=9,
            command=lambda p=item.get("save_path", ""): self._open_folder(pathlib.Path(p) / "extracted")
        )
        open_extracted_btn.pack(side="right", padx=(0, 4))

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
        self._save_settings()

    @staticmethod
    def _sanitize_max_concurrent(value, fallback: int = 3) -> int:
        try:
            return max(1, min(10, int(value)))
        except (TypeError, ValueError):
            return fallback

    def _get_saved_max_concurrent(self) -> int:
        return self._sanitize_max_concurrent(self._settings.get("max_concurrent"), fallback=3)

    def _get_current_max_concurrent(self) -> int:
        try:
            return self._sanitize_max_concurrent(self._max_concurrent_var.get(), fallback=3)
        except tk.TclError:
            return self._get_saved_max_concurrent()

    def _collect_settings(self) -> dict:
        hidden_tags = [key for key, var in self._show_tag_vars.items() if var.get()]
        show_regions = [key for key, var in self._show_region_vars.items() if var.get()]
        return {
            "download_dir": self.get_download_dir(),
            "max_concurrent": self._get_current_max_concurrent(),
            "hidden_tags": hidden_tags,
            "show_regions": show_regions,
            "downloads_panel_open": bool(self._downloads_visible),
            "auto_extract_default": bool(self._auto_extract_default_var.get()),
            "delete_archive_default": bool(self._delete_archive_default_var.get()),
            "compress_ps1_chd": bool(self._compress_ps1_chd_var.get()),
            "download_queue": self._get_persisted_queue_for_settings(),
        }

    def _save_settings(self):
        settings = self._collect_settings()
        save_app_settings(settings)
        self._settings = settings

    @staticmethod
    def _normalize_queue_item(raw: dict) -> dict | None:
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        source = raw.get("source")
        save_path = raw.get("save_path")
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(source, str) or not source.strip():
            return None
        if not isinstance(save_path, str) or not save_path.strip():
            return None
        try:
            so_id = int(raw.get("so_id"))
        except (TypeError, ValueError):
            return None
        if so_id < 0:
            return None
        download_id = raw.get("id")
        if not isinstance(download_id, str) or not download_id.strip():
            download_id = str(uuid.uuid4())
        return {
            "id": download_id,
            "name": name,
            "source": source,
            "so_id": so_id,
            "save_path": save_path,
            "start_requested": bool(raw.get("start_requested", False)),
        }

    def _get_persisted_queue_for_settings(self) -> list[dict]:
        if self._download_queue is not None:
            return self._download_queue.export_for_persistence()
        existing = self._settings.get("download_queue", [])
        if not isinstance(existing, list):
            return []
        cleaned: list[dict] = []
        for raw in existing:
            item = self._normalize_queue_item(raw)
            if item is not None:
                cleaned.append(item)
        return cleaned

    def _restore_persisted_queue(self):
        saved = self._settings.get("download_queue", [])
        if not isinstance(saved, list) or not saved:
            return
        if not _LT_AVAILABLE:
            return
        engine = self.get_torrent_engine(show_errors=False)
        if engine is None or self._download_queue is None:
            return

        requested_ids: list[str] = []
        seen_ids: set[str] = set()
        restored_any = False
        for raw in saved:
            item = self._normalize_queue_item(raw)
            if item is None:
                continue
            did = item["id"]
            while did in seen_ids:
                did = str(uuid.uuid4())
            seen_ids.add(did)
            item["id"] = did
            self._download_queue.enqueue(
                did,
                item["name"],
                item["source"],
                item["so_id"],
                item["save_path"],
            )
            restored_any = True
            if item["start_requested"]:
                requested_ids.append(did)

        if requested_ids:
            self._download_queue.start_selected(requested_ids)
        if restored_any:
            self._refresh_toggle_label()
            self._save_settings()

    def _on_download_dir_change(self, *_):
        self._save_settings()

    def _on_extract_defaults_change(self):
        if self._compress_ps1_chd_var.get() and not self._chdman_path:
            self._extract_status_var.set("PS1→CHD enabled but chdman.exe not found")
            self._ensure_chdman_available_async()
        elif self._chdman_path:
            self._extract_status_var.set(f"CHD tool: {self._chdman_path}")
        else:
            self._extract_status_var.set("")
        self._save_settings()

    def _ensure_chdman_available_async(self):
        if self._chd_download_in_progress or self._chdman_path:
            return
        self._chd_download_in_progress = True
        self._extract_status_var.set("Installing CHD tool (chdman)…")

        def worker():
            path = self._auto_install_chdman()
            self.after(0, lambda p=path: self._finish_chdman_install(p))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_install_chdman(self) -> str | None:
        found = find_chdman_executable()
        if found:
            return found

        base = get_runtime_base_dir()
        tmp_root = base / "_chdman_install_tmp"
        pkg_path = tmp_root / "mame_release_windows_x64.exe"
        extract_dir = tmp_root / "extracted"
        out_dir = base / "tools" / "chdman"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "chdman.exe"

        try:
            if tmp_root.exists():
                shutil.rmtree(tmp_root, ignore_errors=True)
            tmp_root.mkdir(parents=True, exist_ok=True)
            extract_dir.mkdir(parents=True, exist_ok=True)

            release_url = "https://www.mamedev.org/release.html"
            req = urllib.request.Request(release_url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            links = re.findall(r'href="([^"]*mame\d+b_(?:x64|64bit)\.exe[^"]*)"', html, flags=re.IGNORECASE)
            if not links:
                raise RuntimeError("Could not find Windows x64 MAME binary link on release page")
            mame_url = urllib.parse.urljoin(release_url, links[0].split('"')[0])
            log_activity(f"chd.install.download url='{mame_url}'")

            dl_req = urllib.request.Request(mame_url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
            with urllib.request.urlopen(dl_req, timeout=120) as resp, pkg_path.open("wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)

            if not pkg_path.exists() or pkg_path.stat().st_size <= 0:
                raise RuntimeError("Downloaded MAME package is empty")

            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 6  # SW_MINIMIZE

            extracted_ok = False
            last_err = ""
            for tool in self._extractors:
                if tool["kind"] in ("7zip", "peazip"):
                    cmd = [tool["exe"], "x", "-y", "-aoa", f"-o{extract_dir}", str(pkg_path)]
                elif tool["kind"] == "winrar":
                    cmd = [tool["exe"], "x", "-y", "-o+", str(pkg_path), str(extract_dir) + "\\"]
                else:
                    continue
                log_activity(f"chd.install.extract tool={tool['label']} cmd={' '.join(cmd)}")
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    startupinfo=startupinfo,
                )
                if proc.returncode == 0:
                    extracted_ok = True
                    break
                tail = " | ".join((proc.stdout or "").splitlines()[-3:])
                last_err = f"{tool['label']} rc={proc.returncode}" + (f" ({tail})" if tail else "")

            if not extracted_ok:
                raise RuntimeError(f"Failed to extract MAME package ({last_err or 'no extractor available'})")

            found_chd = next((p for p in extract_dir.rglob("chdman.exe") if p.is_file()), None)
            if found_chd is None:
                raise RuntimeError("chdman.exe was not found in extracted MAME package")

            shutil.copy2(found_chd, out_path)
            if not out_path.exists() or out_path.stat().st_size <= 0:
                raise RuntimeError("Failed to place chdman.exe in tools folder")
            log_activity(f"chd.install.ok copied='{out_path}'")
            return str(out_path)
        except Exception as e:
            log_error("MinervaApp._auto_install_chdman failed", e)
            return find_chdman_executable()
        finally:
            try:
                if tmp_root.exists():
                    shutil.rmtree(tmp_root, ignore_errors=True)
            except Exception:
                pass

    def _finish_chdman_install(self, path: str | None):
        self._chd_download_in_progress = False
        self._chdman_path = path
        if path:
            self._extract_status_var.set(f"CHD tool ready: {path}")
            log_activity(f"chd.install.ok path='{path}'")
        else:
            self._extract_status_var.set("Could not auto-install chdman. Install MAME and retry.")
            log_activity("chd.install.fail no_path")

    def _start_selected_queued(self):
        if not self._download_queue or not self._queued_selected_ids:
            return
        selected_ids = list(self._queued_selected_ids)
        self._download_queue.start_selected(selected_ids)
        for did in selected_ids:
            self._queued_selected_ids.discard(did)
        self._save_settings()

    def _start_specific_queued(self, download_id: str):
        if not self._download_queue:
            return
        self._download_queue.start_selected([download_id])
        self._queued_selected_ids.discard(download_id)
        self._save_settings()

    def _start_all_queued(self):
        if not self._download_queue:
            return
        self._download_queue.start_all_pending()
        self._save_settings()

    def _set_queued_selected(self, download_id: str, selected: bool):
        if selected:
            self._queued_selected_ids.add(download_id)
        else:
            self._queued_selected_ids.discard(download_id)

    def _toggle_pause_all_active(self):
        if not self._download_queue or not self._torrent_engine:
            return
        snap = self._download_queue.snapshot()
        active_ids = list(snap["active"])
        if not active_ids:
            return
        statuses = self._torrent_engine.get_all_statuses()
        should_pause = any(not statuses.get(did, {}).get("paused", False) for did in active_ids)
        for did in active_ids:
            if should_pause:
                self._torrent_engine.pause(did)
            else:
                self._torrent_engine.resume(did)

    def _browse_download_dir(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self, initialdir=self.get_download_dir())
        if path:
            self._download_dir.set(path)
            self._save_settings()

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

    def _prompt_post_download_actions_batch(self, download_ids: list[str]):
        if not self._torrent_engine:
            return
        valid_items: list[tuple[str, dict]] = []
        for did in download_ids:
            meta = self._torrent_engine._meta.get(did)
            if meta:
                valid_items.append((did, meta))

        if not valid_items:
            return

        if not self._auto_extract_default_var.get():
            return

        delete_archive = bool(self._delete_archive_default_var.get())
        for did, meta in valid_items:
            meta["auto_extract"] = True
            meta["delete_archive"] = bool(delete_archive)
            self._extract_download(did)

    def _open_current_downloads_folder(self):
        self._open_folder(pathlib.Path(self.get_download_dir()))

    def _open_current_extracted_folder(self):
        self._open_folder(pathlib.Path(self.get_download_dir()) / "extracted")

    def _verify_extracted_button_click(self):
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo("Verify Extracted", "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror("Verify Extracted", "Extracted path exists but is not a folder.")
            return

        targets = [d for d in base.iterdir() if d.is_dir()]
        if not targets:
            messagebox.showinfo("Verify Extracted", "No extracted game folders found.")
            return

        ok = 0
        failed: list[str] = []
        for d in targets:
            try:
                self._verify_extracted_output(d, d.name)
                ok += 1
            except Exception as e:
                failed.append(f"{d.name}: {e}")

        total = len(targets)
        if not failed:
            msg = f"Verified {ok}/{total} extracted folders successfully."
            self._extract_status_var.set(msg)
            messagebox.showinfo("Verify Extracted", msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = f"Verified {ok}/{total}. Failed: {len(failed)}.\n\n{preview}{more}"
        self._extract_status_var.set(f"Verify failed: {len(failed)} folder(s)")
        messagebox.showwarning("Verify Extracted", msg)

    def _compress_ps1_button_click(self):
        if self._chd_compress_in_progress:
            messagebox.showinfo("Compress PS1 to CHD", "CHD compression is already running.")
            return
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists() or not base.is_dir():
            messagebox.showinfo("Compress PS1 to CHD", "No extracted folder found yet.")
            return
        if not self._chdman_path:
            self._ensure_chdman_available_async()
            messagebox.showinfo(
                "Compress PS1 to CHD",
                "chdman is not installed yet. Installation has started in the background."
            )
            return

        targets = [d for d in base.iterdir() if d.is_dir()]
        if not targets:
            messagebox.showinfo("Compress PS1 to CHD", "No extracted game folders found.")
            return
        self._chd_compress_in_progress = True
        self._chd_progress_var.set(0.0)
        self._extract_status_var.set("CHD compression running…")

        def worker():
            converted = 0
            failed: list[str] = []
            total_done = 0
            total_planned = 0
            for d in targets:
                cues = sorted(p for p in d.rglob("*.cue") if p.is_file())
                total_planned += len(cues)

            for d in targets:
                try:
                    def _manual_progress(done: int, total: int, cue_name: str):
                        display_done = total_done + done
                        display_total = max(total_planned, display_done)
                        self.after(
                            0,
                            lambda dd=display_done, dt=display_total, cn=cue_name:
                                self._update_chd_progress(dd, dt, cn)
                        )

                    made = self._compress_ps1_to_chd(d, progress_cb=_manual_progress)
                    converted += made
                    total_done += made
                except Exception as e:
                    failed.append(f"{d.name}: {e}")
            self.after(0, lambda c=converted, f=failed, t=len(targets): self._finish_manual_chd_batch(c, f, t))

        threading.Thread(target=worker, daemon=True).start()

    def _clean_bin_cue_button_click(self):
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo("Clean BIN/CUE", "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror("Clean BIN/CUE", "Extracted path exists but is not a folder.")
            return

        chd_files = [p for p in base.rglob("*.chd") if p.is_file()]
        if not chd_files:
            messagebox.showinfo("Clean BIN/CUE", "No CHD files found under extracted folder.")
            return

        removed_bins = 0
        removed_cues = 0
        failed: list[str] = []
        for chd in chd_files:
            cue = chd.with_suffix(".cue")
            bin_file = chd.with_suffix(".bin")
            if cue.exists():
                try:
                    cue.unlink()
                    removed_cues += 1
                except Exception as e:
                    failed.append(f"{cue.name}: {e}")
            if bin_file.exists():
                try:
                    bin_file.unlink()
                    removed_bins += 1
                except Exception as e:
                    failed.append(f"{bin_file.name}: {e}")

        if not failed:
            msg = f"Cleanup complete. Removed {removed_bins} BIN and {removed_cues} CUE files."
            self._extract_status_var.set(msg)
            messagebox.showinfo("Clean BIN/CUE", msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = (
            f"Cleanup completed with issues.\n"
            f"Removed {removed_bins} BIN and {removed_cues} CUE files.\n"
            f"Failed: {len(failed)}\n\n{preview}{more}"
        )
        self._extract_status_var.set(f"BIN/CUE cleanup issues: {len(failed)} file(s)")
        messagebox.showwarning("Clean BIN/CUE", msg)

    @staticmethod
    def _normalize_chd_stem(stem: str) -> str:
        s = stem
        removable_parenthetical = re.compile(
            r"\(([^()]*)\)",
            flags=re.IGNORECASE
        )

        def _is_important_descriptor(text: str) -> bool:
            t = text.strip().lower()
            if not t:
                return False
            return bool(
                re.match(r"^(disc|disk|cd|track)\s*[-#:]?\s*\d+[a-z]?$", t)
                or re.match(r"^side\s*[a-d]$", t)
                or re.match(r"^part\s*\d+[a-z]?$", t)
            )

        def _is_removable_descriptor(text: str) -> bool:
            t = text.strip().lower()
            if not t:
                return False
            if _is_important_descriptor(t):
                return False
            if re.match(r"^(rev|revision)\s*[a-z0-9.]+$", t):
                return True
            if re.match(r"^v\d+([._]\d+)*$", t):
                return True
            if re.match(r"^(usa|europe|japan|world|korea|asia|australia|germany|france|italy|spain|sweden|netherlands|brazil|canada|uk|uae)$", t):
                return True
            tokens = [tok.strip(" .,_-/") for tok in re.split(r"[,+/&]", t) if tok.strip(" .,_-/")]
            if tokens and all(tok in {"en", "fr", "de", "es", "it", "pt", "nl", "sv", "no", "da", "fi", "pl", "ru", "jp", "ja", "zh", "ko"} for tok in tokens):
                return True
            if t in {"unl", "proto", "prototype", "beta", "demo", "sample", "alt"}:
                return True
            return False

        def _replace(match: re.Match) -> str:
            inside = match.group(1)
            # Fast path: the whole content is a single removable descriptor
            if _is_removable_descriptor(inside):
                return " "
            # Split compound groups (e.g. "USA, En" or "En, Disc 1") and filter
            tokens = [tok.strip(" .,_-/") for tok in re.split(r"[,+/&]", inside) if tok.strip(" .,_-/")]
            if len(tokens) > 1:
                kept = [tok for tok in tokens if not _is_removable_descriptor(tok)]
                if len(kept) < len(tokens):
                    # At least one token was removed — rebuild with only kept tokens
                    return f" ({', '.join(kept)}) " if kept else " "
            return match.group(0)

        last = None
        while last != s:
            last = s
            s = removable_parenthetical.sub(_replace, s)

        s = re.sub(r"\s*-\s*(rev|revision)\s*[a-z0-9.]+\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"\s+([)\]])", r"\1", s)
        s = re.sub(r"([(\[])\s+", r"\1", s)
        return s

    def _clean_chd_names_button_click(self):
        TITLE = "Clean Names"
        FILE_EXTS = {".chd", ".bin", ".cue", ".iso", ".img", ".mdf", ".mds"}
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo(TITLE, "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror(TITLE, "Extracted path exists but is not a folder.")
            return

        renamed = 0
        unchanged = 0
        failed: list[str] = []

        def _try_rename(path: pathlib.Path, new_name: str) -> bool:
            """Rename path to new_name in the same directory. Returns True on success."""
            nonlocal renamed, unchanged
            if new_name == path.name:
                unchanged += 1
                return True
            target = path.parent / new_name
            if target.exists():
                failed.append(f"{path.name}: target exists ({new_name})")
                return False
            try:
                path.rename(target)
                renamed += 1
                return True
            except Exception as e:
                failed.append(f"{path.name}: {e}")
                return False

        # Rename files first (deepest first so folder renames don't break paths)
        all_files = sorted(
            (p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in FILE_EXTS),
            key=lambda p: (-len(p.parts), p.name),
        )
        for f in all_files:
            new_stem = self._normalize_chd_stem(f.stem)
            if not new_stem:
                unchanged += 1
                continue
            _try_rename(f, new_stem + f.suffix)

        # Rename game sub-folders (immediate children of base only, deepest first)
        game_dirs = sorted(
            (p for p in base.rglob("*") if p.is_dir() and p != base),
            key=lambda p: -len(p.parts),
        )
        for d in game_dirs:
            new_name = self._normalize_chd_stem(d.name)
            if not new_name:
                unchanged += 1
                continue
            _try_rename(d, new_name)

        if not failed:
            msg = f"Name cleanup complete. Renamed {renamed}, unchanged {unchanged}."
            self._extract_status_var.set(msg)
            messagebox.showinfo(TITLE, msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = (
            f"Name cleanup completed with issues.\n"
            f"Renamed: {renamed}, Unchanged: {unchanged}, Failed: {len(failed)}\n\n"
            f"{preview}{more}"
        )
        self._extract_status_var.set(f"Name cleanup issues: {len(failed)} file(s)")
        messagebox.showwarning(TITLE, msg)

    def _force_delete_bins_button_click(self):
        TITLE = "Delete BINs"
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo(TITLE, "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror(TITLE, "Extracted path exists but is not a folder.")
            return

        bin_files = [p for p in base.rglob("*.bin") if p.is_file()]
        if not bin_files:
            messagebox.showinfo(TITLE, "No BIN files found under extracted folder.")
            return

        if not messagebox.askyesno(
            TITLE,
            f"Permanently delete {len(bin_files)} BIN file(s) under:\n{base}\n\nThis cannot be undone.",
        ):
            return

        deleted = 0
        failed: list[str] = []
        for f in bin_files:
            try:
                f.unlink()
                deleted += 1
                log_activity(f"force_delete_bin removed='{f}'")
            except Exception as e:
                failed.append(f"{f.name}: {e}")

        if not failed:
            msg = f"Deleted {deleted} BIN file(s)."
            self._extract_status_var.set(msg)
            messagebox.showinfo(TITLE, msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = f"Deleted {deleted}, failed {len(failed)}:\n\n{preview}{more}"
        self._extract_status_var.set(f"Delete BINs: {len(failed)} failed")
        messagebox.showwarning(TITLE, msg)

    def _update_chd_progress(self, done: int, total: int, cue_name: str):
        self._extract_status_var.set(f"CHD converting {done}/{total}: {cue_name}")
        self._chd_progress_var.set(0.0 if total <= 0 else (done * 100.0 / total))

    def _finish_manual_chd_batch(self, converted: int, failed: list[str], total_folders: int):
        self._chd_compress_in_progress = False
        self._chd_progress_var.set(100.0)
        if not failed:
            msg = f"CHD compression finished: {converted} file(s) converted across {total_folders} folder(s)."
            self._extract_status_var.set(msg)
            messagebox.showinfo("Compress PS1 to CHD", msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = (
            f"CHD conversion completed with issues.\n"
            f"Converted: {converted} file(s), Failed folders: {len(failed)}.\n\n"
            f"{preview}{more}"
        )
        self._extract_status_var.set(f"CHD conversion issues: {len(failed)} folder(s)")
        messagebox.showwarning("Compress PS1 to CHD", msg)

    def _open_folder(self, path: pathlib.Path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer", str(path)])
        except Exception as e:
            log_error(f"MinervaApp._open_folder failed for {path}", e)
            messagebox.showerror("Open Folder Failed", f"Could not open folder:\n{e}")

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
        self._queued_selected_ids.discard(download_id)
        if self._download_queue:
            self._download_queue.cancel(download_id)
        w = self._dl_active_widgets.pop(download_id, None)
        if w:
            w["frame"].destroy()
        self._refresh_toggle_label()
        self._save_settings()

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
            log_error(f"MinervaApp._lookup_and_enqueue db lookup failed for {file_name}", e)
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
                # Use a unique local filename per queued file so each queue item has its own torrent file.
                source_key = hashlib.sha1(full_path.encode("utf-8", errors="ignore")).hexdigest()[:10]
                torrent_filename = (
                    row["torrents"].replace("/", "_").replace("\\", "_") + f"__{source_key}.torrent"
                )
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
                log_error(f"MinervaApp._lookup_and_enqueue torrent fetch failed for {file_name}", e)
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
        # Fallback: if torrent renamed output, pick largest likely archive-like file.
        archive_exts = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso", ".chd"}
        candidates: list[tuple[int, pathlib.Path]] = []
        for depth in range(0, 4):
            pattern = "*"
            if depth > 0:
                pattern = "/".join(["*"] * depth) + "/*"
            for p in save_path.glob(pattern):
                if not p.is_file() or p.suffix.lower() not in archive_exts:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if size > 0:
                    candidates.append((size, p))
        if candidates:
            candidates.sort(key=lambda t: t[0], reverse=True)
            chosen = candidates[0][1]
            log_activity(f"extract.lookup fallback picked '{chosen}' for requested '{file_name}'")
            return chosen
        return None

    def _extract_worker_loop(self):
        while True:
            download_id = self._extract_request_queue.get()
            if download_id is None:
                self._extract_request_queue.task_done()
                break
            try:
                self._extract_download_sync(download_id)
            finally:
                with self._extract_pending_lock:
                    self._extract_pending_ids.discard(download_id)
                self._extract_request_queue.task_done()

    def _extract_download(self, download_id: str):
        with self._extract_pending_lock:
            if download_id in self._extract_pending_ids:
                return
            self._extract_pending_ids.add(download_id)
        self._extract_progress[download_id] = {"pct": 0, "status": "Queued for extraction…"}
        self.after(0, self._refresh_extract_rows)
        self._extract_request_queue.put(download_id)

    @staticmethod
    def _is_likely_rom_file(path: pathlib.Path) -> bool:
        rom_exts = {
            ".cue", ".bin", ".iso", ".chd", ".cso", ".pbp", ".img", ".ccd", ".mdf", ".nrg",
            ".gdi", ".cdi", ".zip", ".7z", ".rar", ".z64", ".n64", ".v64", ".smc", ".sfc",
            ".nes", ".gb", ".gbc", ".gba", ".nds", ".3ds", ".cia", ".xci", ".nsp", ".md",
            ".gen", ".32x", ".gg", ".sms", ".pce", ".ws", ".wsc", ".ngp", ".ngc", ".a26",
            ".a78", ".lnx", ".jag", ".m3u"
        }
        return path.suffix.lower() in rom_exts

    def _verify_extracted_output(self, out_dir: pathlib.Path, source_name: str):
        if not out_dir.exists() or not out_dir.is_dir():
            raise RuntimeError("Extraction output folder was not created")
        files = [p for p in out_dir.rglob("*") if p.is_file()]
        if not files:
            raise RuntimeError("No files were extracted")
        meaningful: list[pathlib.Path] = []
        metadata_exts = {".txt", ".nfo", ".sfv", ".md5", ".sha1", ".sha256", ".json"}
        for p in files:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue
            if p.suffix.lower() in metadata_exts:
                continue
            meaningful.append(p)
        if not meaningful:
            raise RuntimeError("Extracted output contains no usable ROM files")
        if not any(self._is_likely_rom_file(p) for p in meaningful):
            sample = ", ".join(sorted({p.suffix.lower() or "<no-ext>" for p in meaningful[:6]}))
            raise RuntimeError(
                f"Extracted files from {source_name} do not look like ROM content ({sample})"
            )

    def _compress_ps1_to_chd(
        self,
        extracted_dir: pathlib.Path,
        progress_cb=None
    ) -> int:
        if not self._compress_ps1_chd_var.get():
            return 0
        chdman = self._chdman_path
        if not chdman:
            log_activity("chd.skip reason=no_chdman")
            return 0
        cue_files = sorted(p for p in extracted_dir.rglob("*.cue") if p.is_file())
        if not cue_files:
            return 0

        converted = 0

        total = len(cue_files)
        cpu_threads = max(1, (os.cpu_count() or 1))
        for idx, cue in enumerate(cue_files, start=1):
            out_chd = cue.with_suffix(".chd")
            if progress_cb is not None:
                try:
                    progress_cb(idx - 1, total, cue.name)
                except Exception:
                    pass
            if out_chd.exists():
                if progress_cb is not None:
                    try:
                        progress_cb(idx, total, cue.name)
                    except Exception:
                        pass
                continue
            cmd = [chdman, "createcd", "-np", str(cpu_threads), "-i", str(cue), "-o", str(out_chd)]
            log_activity(f"chd.run cmd={' '.join(cmd)}")
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 6
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
            )
            if proc.returncode != 0:
                tail = " | ".join((proc.stdout or "").splitlines()[-3:])
                raise RuntimeError(
                    f"CHD conversion failed for {cue.name} (rc={proc.returncode})"
                    + (f" ({tail})" if tail else "")
                )
            if not out_chd.exists() or out_chd.stat().st_size <= 0:
                raise RuntimeError(f"CHD output missing for {cue.name}")
            log_activity(f"chd.ok cue='{cue}' chd='{out_chd}'")
            # Remove all BIN files referenced by this CUE sheet
            try:
                cue_text = cue.read_text(encoding="utf-8", errors="replace")
                referenced_bins = [
                    cue.parent / m.group(1)
                    for m in re.finditer(r'^\s*FILE\s+"?([^"]+\.bin)"?\s+BINARY', cue_text, re.IGNORECASE | re.MULTILINE)
                ]
            except Exception:
                referenced_bins = []
            # Fall back: find BINs in the same folder whose stem matches the parent folder name
            if not referenced_bins:
                folder_name = cue.parent.name
                referenced_bins = [
                    p for p in cue.parent.iterdir()
                    if p.suffix.lower() == ".bin" and p.stem.lower().startswith(folder_name.lower())
                ]
            # Last resort: the single-stem BIN matching the CUE
            if not referenced_bins:
                referenced_bins = [cue.with_suffix(".bin")]
            for bin_path in referenced_bins:
                if bin_path.exists():
                    try:
                        bin_path.unlink()
                        log_activity(f"chd.cleanup.bin removed='{bin_path}'")
                    except Exception as e:
                        log_activity(f"chd.cleanup.bin failed='{bin_path}' err='{e}'")
            cue.unlink()
            log_activity(f"chd.cleanup.cue removed='{cue}'")
            converted += 1
            if progress_cb is not None:
                try:
                    progress_cb(idx, total, cue.name)
                except Exception:
                    pass
        return converted

    def _extract_download_sync(self, download_id: str):
        if not self._torrent_engine:
            return
        meta = self._torrent_engine._meta.get(download_id)
        if not meta:
            return

        save_path = pathlib.Path(meta["save_path"])
        torrent_dir = save_path / "extracted"
        torrent_dir.mkdir(parents=True, exist_ok=True)
        delete_archive = bool(meta.get("delete_archive"))
        extractors = list(self._extractors)
        file_name = meta["name"]
        log_activity(f"extract.start id={download_id} file='{file_name}' save_path='{save_path}'")

        def _set_progress(pct: int, status: str):
            self._extract_progress[download_id] = {"pct": pct, "status": status}
            self.after(0, self._refresh_extract_rows)

        self.after(0, lambda: self._chd_progress_var.set(0.0))

        try:
            src = None
            for _ in range(20):
                src = self._find_downloaded_file(save_path, file_name)
                if src is not None:
                    break

                _set_progress(0, "Waiting for downloaded file…")
                time.sleep(1)

            if src is None:
                log_activity(f"extract.missing id={download_id} file='{file_name}'")
                _set_progress(0, f"Missing downloaded file: {file_name}")
                return
            log_activity(f"extract.source id={download_id} src='{src}' size={src.stat().st_size if src.exists() else -1}")

            # Give the filesystem a brief moment to finish writes/locks before extraction.
            stable_count = 0
            last_size = -1
            for _ in range(10):
                try:
                    current_size = src.stat().st_size
                except OSError:
                    current_size = -1
                if current_size > 0 and current_size == last_size:
                    stable_count += 1
                    if stable_count >= 2:
                        break
                else:
                    stable_count = 0
                last_size = current_size
                time.sleep(1)

            _set_progress(0, "Extracting…")
            extracted_ok = False
            extracted_dir: pathlib.Path | None = None

            last_tool_error = ""
            for tool in extractors:
                if extracted_ok:
                    break
                out_dir = torrent_dir / src.stem
                out_dir.mkdir(parents=True, exist_ok=True)

                if tool["kind"] in ("7zip", "peazip"):
                    cmd = [tool["exe"], "x", "-y", "-aoa", "-bd", "-bso1", "-bsp1", f"-o{out_dir}", str(src)]
                elif tool["kind"] == "winrar":
                    cmd = [tool["exe"], "x", "-y", "-o+", str(src), str(out_dir) + "\\"]
                else:
                    continue

                log_activity(
                    f"extract.tool.run id={download_id} tool={tool['label']} cmd={' '.join(cmd)}"
                )
                last_lines = []
                rc = 1
                for attempt in range(1, 4):
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 6  # SW_MINIMIZE
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        startupinfo=startupinfo,
                    )
                    last_pct = -1
                    if proc.stdout is not None:
                        for line in proc.stdout:
                            line = line.strip()
                            if line:
                                last_lines.append(line)
                                if len(last_lines) > 20:
                                    last_lines.pop(0)
                            m = re.search(r"(\d{1,3})%", line)
                            if m:
                                pct = min(100, int(m.group(1)))
                                if pct != last_pct:
                                    last_pct = pct
                                    _set_progress(pct, f"Extracting… {pct}%")
                    rc = proc.wait()
                    if rc == 0:
                        log_activity(
                            f"extract.tool.ok id={download_id} tool={tool['label']} attempt={attempt}"
                        )
                        extracted_ok = True
                        extracted_dir = out_dir
                        break
                    if attempt < 3:
                        log_activity(
                            f"extract.tool.retry id={download_id} tool={tool['label']} attempt={attempt} rc={rc}"
                        )
                        _set_progress(0, f"{tool['label']} retry {attempt}/2…")
                        time.sleep(2)

                if not extracted_ok:
                    tail = " | ".join(last_lines[-3:]) if last_lines else ""
                    last_tool_error = f"{tool['label']} exited with code {rc}" + (f" ({tail})" if tail else "")
                    log_activity(
                        f"extract.tool.fail id={download_id} tool={tool['label']} rc={rc} tail={tail}"
                    )

            if not extracted_ok and src.suffix.lower() == ".zip":
                if extractors:
                    _set_progress(0, "External extractor failed, trying ZIP fallback…")
                else:
                    _set_progress(0, "Using Python ZIP fallback…")
            elif not extracted_ok and extractors:
                raise RuntimeError(last_tool_error or "All external extractors failed")

            if not extracted_ok and src.suffix.lower() == ".zip":
                if zipfile.is_zipfile(src):
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
                    extracted_dir = out_dir
                    log_activity(f"extract.zip.ok id={download_id} src='{src}'")
                else:
                    log_activity(f"extract.zip.invalid id={download_id} src='{src}'")
                    raise RuntimeError(
                        "Downloaded .zip is not a valid ZIP archive. Download may be incomplete."
                    )

            elif not extracted_ok:
                out_dir = torrent_dir / src.stem
                out_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, out_dir / src.name)
                extracted_ok = True
                extracted_dir = out_dir
                log_activity(f"extract.copy_passthrough id={download_id} src='{src}'")

            if extracted_ok and extracted_dir is not None:
                self._verify_extracted_output(extracted_dir, src.name)
                def _chd_progress(done: int, total: int, cue_name: str):
                    if total <= 0:
                        return
                    pct = 90 + int((done / total) * 9)
                    pct = max(90, min(99, pct))
                    self.after(0, lambda d=done, t=total: self._chd_progress_var.set(d * 100.0 / t))
                    _set_progress(pct, f"Converting to CHD ({done}/{total}): {cue_name}")

                self._compress_ps1_to_chd(
                    extracted_dir,
                    progress_cb=_chd_progress
                )
                log_activity(f"extract.verify.ok id={download_id} dir='{extracted_dir}'")

            if extracted_ok and delete_archive and src.exists():
                src.unlink()
                log_activity(f"extract.delete_archive id={download_id} src='{src}'")

            _set_progress(100, "Extracted ✓" if extracted_ok else "Failed")
            self.after(0, lambda: self._chd_progress_var.set(100.0 if extracted_ok else 0.0))
            log_activity(f"extract.done id={download_id} ok={extracted_ok}")

        except Exception as e:
            log_error(f"MinervaApp._extract_download_sync failed for {file_name}", e)
            log_activity(f"extract.error id={download_id} file='{file_name}' err={repr(e)}")
            _set_progress(0, f"Error: {str(e)[:40]}")
            self.after(0, lambda: self._chd_progress_var.set(0.0))

    def _on_close(self):
        self._save_settings()
        if self._torrent_engine is not None:
            try:
                self._torrent_engine.shutdown()
            except Exception:
                log_error("MinervaApp._on_close engine shutdown failed")
        try:
            self._extract_request_queue.put_nowait(None)
        except Exception as e:
            log_error("MinervaApp._on_close extraction queue shutdown failed", e)
        self.destroy()

    def _show_error(self, msg):
        log_error(f"MinervaApp._show_error: {msg}")
        self._set_loading(False)
        self._right_tree.delete(*self._right_tree.get_children())
        self._status_var.set(f"Error: {msg}")
        messagebox.showerror("Error", f"Failed to load page:\n{msg}")


if __name__ == "__main__":
    log_activity("app.launch")
    app = MinervaApp()
    log_activity("app.mainloop.start")
    app.mainloop()
    log_activity("app.mainloop.exit")
