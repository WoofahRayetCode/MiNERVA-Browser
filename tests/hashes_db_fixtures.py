"""Test helpers: build a ``hashes.db``-shaped SQLite file and serve it over HTTP with Range support."""
from __future__ import annotations

import http.server
import os
import random
import re
import sqlite3
import threading

TORRENT = "Minerva_Myrient_v0.3/Minerva_Myrient - Redump - Sony - PlayStation 3.torrent"
MAGNET = "magnet:?xt=urn:btih:626a5e05f498e435145c97352aa2fecf7e95a668&dn=Minerva_Myrient"

# Same shape as the archive's file: the last three columns were appended with ALTER TABLE, so rows written
# before that are shorter than the column list.
_OLD_COLUMNS = (
    "CREATE TABLE files(id INTEGER PRIMARY KEY, full_path TEXT NOT NULL, file_name TEXT NOT NULL, "
    "size INTEGER NOT NULL, md5 TEXT, sha1 TEXT, sha256 TEXT, crc32 TEXT)"
)
_ALTERS = (
    "ALTER TABLE files ADD COLUMN torrents TEXT",
    "ALTER TABLE files ADD COLUMN so_id INTEGER",
    "ALTER TABLE files ADD COLUMN magnet TEXT",
)

_ODD_SIZES = (0, 1, 127, 128, 32767, 32768, 8388607, 8388608, 2**31 - 1, 2**31, 2**40, 2**47, 2**62, -1, -129)


def game_name(tag: str, i: int) -> str:
    return f"{tag} Game {i:06d} (USA).iso"


def build_db(
    path,
    *,
    rows: int = 2000,
    page_size: int = 4096,
    old_rows: int = 50,
    tag: str = "A",
    big_magnet_every: int = 97,
    big_magnet_chars: int = 9000,
    encoding: str | None = None,
    columns_sql: str | None = None,
) -> dict:
    """Write a database of ``rows`` rows with odd ids only (even ids are gaps).

    Returns {"ids": [...], "path": path}.  ``old_rows`` rows are inserted before the ALTERs so they are
    stored with short records; every ``big_magnet_every``-th row has a magnet long enough to need overflow
    pages; sizes cycle through every SQLite integer width; a few rows hold a float, a blob and unicode.
    """
    rng = random.Random(sum(map(ord, tag)) + 7)  # deterministic: str hashes are randomised per process
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"PRAGMA page_size={page_size}")
        if encoding:
            conn.execute(f"PRAGMA encoding='{encoding}'")
        conn.execute("CREATE TABLE meta(k TEXT, v TEXT)")  # a decoy so 'files' is not the first table
        conn.execute("INSERT INTO meta VALUES('built','test')")
        if columns_sql is not None:
            conn.execute(columns_sql)
            conn.commit()
            return {"ids": [], "path": str(path)}
        conn.execute(_OLD_COLUMNS)
        ids = [2 * i + 1 for i in range(rows)]

        def hexstr(n):
            return f"{rng.getrandbits(4 * n):0{n}x}"

        old = [
            (ids[i], f"Redump/Sony/{game_name(tag, i)}", game_name(tag, i), _ODD_SIZES[i % len(_ODD_SIZES)],
             hexstr(32), hexstr(40), hexstr(64), hexstr(8))
            for i in range(min(old_rows, rows))
        ]
        conn.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", old)
        for statement in _ALTERS:
            conn.execute(statement)
        new = []
        for i in range(len(old), rows):
            name = game_name(tag, i)
            size = _ODD_SIZES[i % len(_ODD_SIZES)]
            md5 = hexstr(32)
            magnet = MAGNET
            if big_magnet_every and i % big_magnet_every == 0:
                magnet = MAGNET + "&x=" + "m" * big_magnet_chars
            if i % 211 == 0:
                name = f"{tag} ゼルダの伝説 é{i} (Japan).iso"
            if i % 307 == 0:
                size = 1.5  # stored as a REAL
            if i % 401 == 0:
                md5 = b"\x00\x01\x02blob"  # stored as a BLOB
            so_id = (0, 1, i)[i % 3]  # 0 and 1 use SQLite's zero-width integer serial types
            new.append(
                (ids[i], f"Redump/Sony/{name}", name, size, md5, hexstr(40), hexstr(64), hexstr(8),
                 TORRENT, so_id, magnet)
            )
        conn.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)", new)
        conn.execute("CREATE INDEX idx_full_path ON files(full_path)")
        conn.commit()
    finally:
        conn.close()
    return {"ids": ids, "path": str(path)}


def reference_row(path, rowid: int) -> dict | None:
    """What real SQLite returns for this id (the oracle the reader is compared against)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM files WHERE id = ?", (rowid,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


class RangeServer:
    """Serves one file at ``/hashes.db`` with Range support; behaviour can be bent per test.

    ``mode``: normal | ignore_range | short | wrong_start | not_found
    """

    def __init__(self, path):
        self.path = str(path)
        self.mode = "normal"
        self.fail_first = 0            # answer this many requests with 503 first
        self.delay = 0.0               # seconds to wait before answering
        self.hold = None               # threading.Event: block every answer until set
        self.requests: list[tuple[int, int]] = []
        self._version = 1
        self._lock = threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                outer._answer(self)

        class Quiet(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):  # clients that hang up mid-body are expected
                pass

        self._httpd = Quiet(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_port}/hashes.db"

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self.requests)

    def swap(self, path) -> None:
        """Replace the served file; the ETag changes like it does when the archive republishes the database."""
        with self._lock:
            self.path = str(path)
            self._version += 1

    def close(self) -> None:
        if self.hold is not None:
            self.hold.set()
        self._httpd.shutdown()
        self._httpd.server_close()

    # -- request handling -------------------------------------------------------------------------
    def _answer(self, h) -> None:
        match = re.match(r"bytes=(\d+)-(\d+)", h.headers.get("Range", ""))
        with self._lock:
            self.requests.append((int(match.group(1)), int(match.group(2))) if match else (-1, -1))
            index = len(self.requests)
            path, version = self.path, self._version
        if self.hold is not None:
            self.hold.wait(30)
        if self.delay:
            threading.Event().wait(self.delay)
        if index <= self.fail_first:
            return self._send(h, 503, b"busy")
        if self.mode == "not_found":
            return self._send(h, 404, b"gone")
        total = os.path.getsize(path)
        etag = f'W/"v{version}-{total:x}"'
        with open(path, "rb") as f:
            if not match or self.mode == "ignore_range":
                return self._send(h, 200, f.read(), {"ETag": etag})
            start, end = int(match.group(1)), int(match.group(2))
            if start >= total:
                return self._send(h, 416, b"", {"Content-Range": f"bytes */{total}"})
            end = min(end, total - 1)
            f.seek(start)
            body = f.read(end - start + 1)
        headers = {"Content-Range": f"bytes {start}-{end}/{total}", "ETag": etag, "Accept-Ranges": "bytes"}
        if self.mode == "short":
            body = body[: len(body) // 2]  # promises the whole range, delivers half (with a matching length)
        elif self.mode == "wrong_start":
            headers["Content-Range"] = f"bytes 0-{end - start}/{total}"
        self._send(h, 206, body, headers)

    @staticmethod
    def _send(h, status, body, headers=None) -> None:
        h.send_response(status)
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
