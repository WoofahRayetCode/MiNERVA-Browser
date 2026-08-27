import struct
import urllib.request
import urllib.parse
import json
import re
from html.parser import HTMLParser
from minerva.constants import BASE_URL, log_error

_ROM_JS_RE = re.compile(r"window\.rom\s*=\s*(\{.*?\});", re.DOTALL)


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
        elif self._in_entry and not self._skip and tag == "a" and self._href is None:
            self._href = attrs.get("href", "")
        elif self._in_entry and not self._skip and tag == "span":
            self._in_span = True

    def handle_endtag(self, tag):
        if tag == "div" and self._in_entry:
            if (
                not self._skip
                and self._href
                and not self._href.lower().startswith("javascript:")
            ):
                is_folder = self._href.endswith("/")
                self.entries.append({
                    "name": self._name or urllib.parse.unquote(self._href.rstrip("/").split("/")[-1]),
                    "href": self._href,
                    "size": (self._size or "").strip(),
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


def fetch_entries(path: str) -> list[dict]:
    url = BASE_URL + path
    req = urllib.request.Request(url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    parser = EntryParser()
    parser.feed(html)
    return parser.entries


def fetch_rom_info(rom_id: str) -> dict | None:
    url = f"{BASE_URL}/rom?id={rom_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    match = _ROM_JS_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception as e:
        log_error(f"fetch_rom_info json parsing error for {rom_id}", e)
        return None


def extract_rom_id(href: str) -> str | None:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    values = qs.get("id")
    if not values:
        return None
    return values[0]


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
        try:
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
        except Exception as e:
            log_error(f"SQLiteHTTP.lookup failed for {full_path}", e)
            return None
