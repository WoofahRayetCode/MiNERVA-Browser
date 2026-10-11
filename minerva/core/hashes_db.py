"""Look up single rows of the archive's ``hashes.db`` over HTTP range requests.

``hashes.db`` (~1.7 GB SQLite) maps every file in the archive to its collection torrent and its index
inside that torrent (``files.id`` is the number in ``/rom?id=N``).  Downloading it is out of the question,
but a SQLite file is a B-tree of fixed-size pages, so one row is found by reading about four of them.
This reader fetches 64 KB blocks with ``Range`` requests, keeps them in a small cache and walks the
table B-tree itself, so it needs no third-party SQLite VFS.  It exists because the archive's
``/rom?id=N`` page can stop answering entirely, which used to make every queue attempt fail.

Only what is needed is implemented: ``SELECT * FROM files WHERE id = ?``.  Everything is validated; a
corrupt or unexpected file raises :class:`HashesDbError` instead of returning wrong data, and a response
that does not honour ``Range`` is rejected before it can pull the whole database.
"""
from __future__ import annotations

import re
import struct
import threading
from collections import OrderedDict

from minerva.constants import BASE_URL, log_activity
from minerva.core.http import SITE_GATE, HttpError, RateGate, get_response

HASHES_DB_URL = BASE_URL.rstrip("/") + "/assets/hashes.db"
BLOCK_SIZE = 64 * 1024   # a multiple of every legal SQLite page size, so a page never straddles blocks
MAX_DEPTH = 16           # B-tree depth bound (the real database is 4 deep)
MAX_PAYLOAD = 4 * 1024 * 1024
MIN_USABLE = 480         # SQLite's own lower bound for a page's usable size
REQUIRED_COLUMNS = ("full_path", "file_name", "torrents", "so_id", "magnet")

_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$")
_COLUMN_NAME = re.compile(r"""\s*(?:"([^"]+)"|'([^']+)'|`([^`]+)`|\[([^\]]+)\]|(\w+))""")
_TABLE_CONSTRAINTS = frozenset({"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"})
_INT_WIDTH = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8}


class HashesDbError(Exception):
    """The database could not be read (network trouble, unexpected format, corruption)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class _DbChanged(Exception):
    """The server replaced the database while we were reading it."""


def _varint(buf: bytes, offset: int) -> tuple[int, int]:
    result = 0
    for i in range(9):
        if offset >= len(buf):
            raise HashesDbError("corrupt database: varint past end of page")
        byte = buf[offset]
        offset += 1
        if i == 8:
            return (result << 8) | byte, offset
        result = (result << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return result, offset
    raise AssertionError("unreachable")


def _decode_record(payload: bytes) -> list:
    header_size, offset = _varint(payload, 0)
    if header_size > len(payload):
        raise HashesDbError("corrupt database: record header too large")
    serial_types: list[int] = []
    while offset < header_size:
        st, offset = _varint(payload, offset)
        serial_types.append(st)
    values: list = []
    offset = header_size
    for st in serial_types:
        if st == 0:
            values.append(None)
        elif st in _INT_WIDTH:
            width = _INT_WIDTH[st]
            values.append(int.from_bytes(payload[offset:offset + width], "big", signed=True))
            offset += width
        elif st == 7:
            values.append(struct.unpack_from(">d", payload, offset)[0])
            offset += 8
        elif st == 8:
            values.append(0)
        elif st == 9:
            values.append(1)
        elif st >= 12 and st % 2 == 0:
            length = (st - 12) // 2
            values.append(bytes(payload[offset:offset + length]))
            offset += length
        elif st >= 13:
            length = (st - 13) // 2
            values.append(payload[offset:offset + length].decode("utf-8", "replace"))
            offset += length
        else:
            raise HashesDbError(f"corrupt database: reserved serial type {st}")
        if offset > len(payload):
            raise HashesDbError("corrupt database: record body truncated")
    return values


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'`[":
            quote = "]" if ch == "[" else ch
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def parse_table_columns(create_sql: str) -> tuple[list[str], int | None]:
    """Lower-cased column names (in storage order) and the index of the rowid alias, from CREATE TABLE."""
    start, end = create_sql.find("("), create_sql.rfind(")")
    if start < 0 or end <= start:
        raise HashesDbError("unexpected schema: cannot parse CREATE TABLE")
    if re.search(r"\bWITHOUT\s+ROWID\b", create_sql[end:], re.IGNORECASE):
        raise HashesDbError("unexpected schema: WITHOUT ROWID tables are not supported")
    names: list[str] = []
    alias: int | None = None
    for segment in _split_top_level(create_sql[start + 1:end]):
        match = _COLUMN_NAME.match(segment)
        if not match:
            continue
        quoted = [g for g in match.groups()[:4] if g]
        name = quoted[0] if quoted else match.group(5)
        if not quoted and name.upper() in _TABLE_CONSTRAINTS:
            continue  # a table constraint, not a column
        if re.search(r"\bINTEGER\s+PRIMARY\s+KEY\b", segment[match.end():], re.IGNORECASE):
            alias = len(names)
        names.append(name.lower())
    return names, alias


class _Inflight:
    """A block fetch in progress; threads that need the same block wait for its outcome."""

    __slots__ = ("event", "error")

    def __init__(self):
        self.event = threading.Event()
        self.error: Exception | None = None


class _Version:
    """Reads one version of the remote file; the whole object is dropped if the server replaces the file,
    so blocks (and the layout parsed from them) of two different files can never be mixed."""

    def __init__(self, url, gate, cache_blocks, timeout, retries, block_size):
        self._url = url
        self._gate = gate
        self._cache_blocks = cache_blocks
        self._timeout = timeout
        self._retries = retries
        self._block_size = block_size
        self._wait_limit = timeout * (retries + 1) + 30.0
        self._lock = threading.Lock()
        self._open_lock = threading.Lock()
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._inflight: dict[int, _Inflight] = {}
        self._identity: tuple[str, int] | None = None   # (validator, total size) of the file being read
        self._total = 0
        self._opened = False
        self._open_attempts = 0
        self._open_error: Exception | None = None
        self._page_size = 0
        self._usable = 0
        self._page_count = 0
        self._root = 0
        self._columns: list[str] = []
        self._alias: int | None = None

    # -- transport ------------------------------------------------------------------------------
    def _fetch_block(self, index: int) -> bytes:
        start = index * self._block_size
        end = start + self._block_size - 1
        try:
            response = get_response(
                self._url,
                headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"},
                timeout=self._timeout,
                retries=self._retries,
                max_bytes=self._block_size,  # a server that ignores Range must not send us 1.7 GB
                gate=self._gate,
            )
        except HttpError as e:
            raise HashesDbError(str(e), retryable=e.retryable) from e
        if response.status != 206:
            raise HashesDbError(f"server ignored the range request (HTTP {response.status})")
        match = _CONTENT_RANGE.match(response.headers.get("content-range", "").strip())
        if not match or int(match.group(1)) != start:
            raise HashesDbError("unexpected Content-Range in range response")
        total = int(match.group(3))
        if len(response.body) != min(end, total - 1) - start + 1:
            raise HashesDbError("short range response", retryable=True)
        validator = response.headers.get("etag") or response.headers.get("last-modified", "")
        with self._lock:
            if self._identity is None:
                self._identity = (validator, total)
                self._total = total
            elif self._identity != (validator, total):
                raise _DbChanged()
        return response.body

    def _block(self, index: int) -> bytes:
        with self._lock:
            data = self._cache.get(index)
            if data is not None:
                self._cache.move_to_end(index)
                return data
            pending = self._inflight.get(index)
            owner = pending is None
            if owner:
                pending = self._inflight[index] = _Inflight()
        if not owner:
            if not pending.event.wait(self._wait_limit):
                raise HashesDbError("timed out waiting for a concurrent range request", retryable=True)
            if pending.error is not None:
                raise pending.error  # share the owner's failure instead of every waiter retrying in turn
            return self._block(index)
        try:
            data = self._fetch_block(index)
            with self._lock:
                self._cache[index] = data
                while len(self._cache) > self._cache_blocks:
                    self._cache.popitem(last=False)
            return data
        except (HashesDbError, _DbChanged) as e:
            pending.error = e
            raise
        finally:
            with self._lock:
                self._inflight.pop(index, None)
            pending.event.set()

    def _page(self, number: int) -> bytes:
        if number < 1 or number > self._page_count:
            raise HashesDbError(f"corrupt database: page {number} out of range")
        offset = (number - 1) * self._page_size
        index = offset // self._block_size
        block = self._block(index)
        start = offset - index * self._block_size
        page = block[start:start + self._page_size]
        if len(page) != self._page_size:
            raise HashesDbError("corrupt database: truncated page")
        return page

    # -- opening: header + schema ---------------------------------------------------------------
    def _open(self) -> None:
        seen = self._open_attempts
        with self._open_lock:
            if self._opened:
                return
            if self._open_error is not None and self._open_attempts > seen:
                raise self._open_error  # the attempt we queued behind failed; share it, don't repeat it
            error: Exception | None = None
            try:
                self._load_layout()
            except Exception as e:
                error = e
            self._open_error = error
            self._open_attempts += 1
            if error is not None:
                raise error

    def _load_layout(self) -> None:
        head = self._block(0)
        if head[:16] != b"SQLite format 3\x00":
            raise HashesDbError("not a SQLite database")
        page_size = struct.unpack_from(">H", head, 16)[0]
        if page_size == 1:
            page_size = 65536
        if page_size < 512 or page_size & (page_size - 1) or page_size > self._block_size:
            raise HashesDbError(f"unsupported page size {page_size}")
        if struct.unpack_from(">I", head, 56)[0] != 1:
            raise HashesDbError("unsupported text encoding (only UTF-8)")
        usable = page_size - head[20]
        if usable < MIN_USABLE:
            raise HashesDbError("corrupt database: implausible reserved space")
        self._page_size, self._usable = page_size, usable
        self._page_count = self._total // page_size
        if self._page_count < 2:
            raise HashesDbError("database is empty")
        sql, root = self._read_schema()
        columns, alias = parse_table_columns(sql)
        missing = [c for c in REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise HashesDbError(f"schema changed: missing columns {missing}")
        self._columns, self._alias, self._root = columns, alias, root
        self._opened = True

    def _read_schema(self) -> tuple[str, int]:
        for row in self._scan_table(1):
            if len(row) >= 5 and row[0] == "table" and isinstance(row[1], str) and row[1].lower() == "files":
                return row[4], int(row[3])
        raise HashesDbError("schema changed: no 'files' table")

    def _scan_table(self, root: int):
        """Every row of a (small) table, by walking its B-tree; used for sqlite_master only."""
        stack = [(root, 0)]
        while stack:
            number, depth = stack.pop()
            if depth > MAX_DEPTH:
                raise HashesDbError("corrupt database: tree too deep")
            page = self._page(number)
            header = 100 if number == 1 else 0
            kind = page[header]
            cells = struct.unpack_from(">H", page, header + 3)[0]
            if kind == 0x05:
                pointers = struct.unpack_from(f">{cells}H", page, header + 12)
                children = [struct.unpack_from(">I", page, off)[0] for off in pointers]
                children.append(struct.unpack_from(">I", page, header + 8)[0])
                stack.extend((child, depth + 1) for child in reversed(children))
            elif kind == 0x0D:
                for off in struct.unpack_from(f">{cells}H", page, header + 8):
                    yield _decode_record(self._leaf_payload(page, off))
            else:
                raise HashesDbError(f"corrupt database: unexpected page type {kind:#x}")

    # -- lookup ---------------------------------------------------------------------------------
    def _leaf_payload(self, page: bytes, offset: int) -> bytes:
        size, pos = _varint(page, offset)
        _, pos = _varint(page, pos)  # the rowid
        if size > MAX_PAYLOAD:
            raise HashesDbError("corrupt database: payload too large")
        usable = self._usable
        max_local = usable - 35
        if size <= max_local:
            return bytes(page[pos:pos + size])
        min_local = ((usable - 12) * 32 // 255) - 23
        local = min_local + ((size - min_local) % (usable - 4))
        if local > max_local:
            local = min_local
        payload = bytearray(page[pos:pos + local])
        overflow = struct.unpack_from(">I", page, pos + local)[0]
        visited: set[int] = set()
        while overflow and len(payload) < size:
            if overflow in visited:  # a cycle would otherwise return the same bytes again as if they were data
                raise HashesDbError("corrupt database: overflow chain loops")
            visited.add(overflow)
            chunk = self._page(overflow)
            overflow = struct.unpack_from(">I", chunk, 0)[0]
            payload += chunk[4:4 + min(usable - 4, size - len(payload))]
        if len(payload) != size:
            raise HashesDbError("corrupt database: overflow chain ended early")
        return bytes(payload)

    def _find_row(self, rowid: int) -> list | None:
        number = self._root
        for _ in range(MAX_DEPTH):
            page = self._page(number)
            kind = page[0]
            cells = struct.unpack_from(">H", page, 3)[0]
            if kind == 0x05:
                number = struct.unpack_from(">I", page, 8)[0]  # right-most child unless a key says otherwise
                for off in struct.unpack_from(f">{cells}H", page, 12):
                    key, _ = _varint(page, off + 4)
                    if rowid <= key:
                        number = struct.unpack_from(">I", page, off)[0]
                        break
            elif kind == 0x0D:
                for off in struct.unpack_from(f">{cells}H", page, 8):
                    _, pos = _varint(page, off)
                    found, _ = _varint(page, pos)
                    if found == rowid:
                        return _decode_record(self._leaf_payload(page, off))
                return None
            else:
                raise HashesDbError(f"corrupt database: unexpected page type {kind:#x}")
        raise HashesDbError("corrupt database: tree too deep")

    def lookup(self, rowid: int) -> dict | None:
        try:
            self._open()
            values = self._find_row(rowid)
        except (struct.error, IndexError, ValueError, TypeError) as e:
            raise HashesDbError(f"corrupt database: {e}") from e
        if values is None:
            return None
        values = values + [None] * (len(self._columns) - len(values))  # rows that predate an ALTER ADD COLUMN
        row = dict(zip(self._columns, values))
        if self._alias is not None:
            row[self._columns[self._alias]] = rowid  # a rowid alias is stored as NULL
        return row


class HashesDb:
    """Thread-safe ``files`` lookup by id against the remote database (see the module docstring)."""

    def __init__(
        self,
        url: str = HASHES_DB_URL,
        *,
        gate: RateGate | None = None,
        cache_blocks: int = 256,
        timeout: float = 15.0,
        retries: int = 1,
        block_size: int = BLOCK_SIZE,
    ):
        self.url = url
        self._options = (gate if gate is not None else SITE_GATE, cache_blocks, timeout, retries, block_size)
        self._lock = threading.Lock()
        self._version: _Version | None = None

    def lookup_id(self, rowid: int) -> dict | None:
        """The ``files`` row with this id as a dict (every column), or None if the database has no such id.

        Raises :class:`HashesDbError` when the database cannot be read at all."""
        rowid = int(rowid)
        for _ in range(2):
            with self._lock:
                version = self._version
                if version is None:
                    version = self._version = _Version(self.url, *self._options)
            try:
                return version.lookup(rowid)
            except _DbChanged:
                with self._lock:
                    if self._version is version:
                        self._version = None
                log_activity("hashes_db.changed: the archive replaced hashes.db; starting over")
        raise HashesDbError("hashes.db keeps changing while it is being read", retryable=True)
