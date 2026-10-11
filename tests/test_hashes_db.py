import random
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from minerva.core.hashes_db import HashesDb, HashesDbError, parse_table_columns
from minerva.core.http import RateGate
from tests.hashes_db_fixtures import RangeServer, build_db, game_name, reference_row

NO_GATE = RateGate(rate_per_sec=0)  # these tests talk to localhost; no politeness delay needed


def no_backoff():
    return mock.patch("minerva.core.http.backoff_delay", return_value=0.0)

# (SQLite page size, block size the reader fetches): blocks hold several small pages or exactly one big one.
LAYOUTS = ((512, 4096), (1024, 4096), (4096, 4096), (4096, 65536), (65536, 65536))

_tmp: Path
_dbs: dict[int, dict] = {}


def setUpModule():
    global _tmp
    _tmp = Path(tempfile.mkdtemp(prefix="hashes-db-test-"))
    for page_size, rows in ((512, 12000), (1024, 12000), (4096, 12000), (65536, 12000)):
        _dbs[page_size] = build_db(_tmp / f"db-{page_size}.sqlite", rows=rows, page_size=page_size)


def tearDownModule():
    shutil.rmtree(_tmp, ignore_errors=True)


def make_db(server, **kw):
    kw.setdefault("gate", NO_GATE)
    kw.setdefault("timeout", 5.0)
    kw.setdefault("retries", 0)
    return HashesDb(server.url, **kw)


class ServerCase(unittest.TestCase):
    def serve(self, path):
        server = RangeServer(path)
        self.addCleanup(server.close)
        return server


class TestParseTableColumns(unittest.TestCase):
    def test_the_archives_schema_including_alter_appended_columns(self):
        sql = (
            "CREATE TABLE files(id INTEGER PRIMARY KEY, full_path TEXT NOT NULL, file_name TEXT NOT NULL, "
            "size INTEGER NOT NULL, md5 TEXT, sha1 TEXT, sha256 TEXT, crc32 TEXT, torrents TEXT, "
            "so_id INTEGER, magnet TEXT)"
        )
        names, alias = parse_table_columns(sql)
        self.assertEqual(names, ["id", "full_path", "file_name", "size", "md5", "sha1", "sha256", "crc32",
                                 "torrents", "so_id", "magnet"])
        self.assertEqual(alias, 0)

    def test_quoted_names_constraints_and_nested_parentheses(self):
        sql = '''CREATE TABLE "files" (
            "Row Id" INTEGER PRIMARY KEY AUTOINCREMENT,
            `name` TEXT DEFAULT (lower('A,B')),
            [size] INTEGER CHECK (size > 0 AND size < (1<<40)),
            PRIMARY KEY ("Row Id"),
            UNIQUE (name, size)
        )'''
        names, alias = parse_table_columns(sql)
        self.assertEqual(names, ["row id", "name", "size"])
        self.assertEqual(alias, 0)

    def test_no_rowid_alias_when_the_key_is_not_an_integer(self):
        names, alias = parse_table_columns("CREATE TABLE t(code TEXT PRIMARY KEY, n INTEGER)")
        self.assertEqual(names, ["code", "n"])
        self.assertIsNone(alias)

    def test_without_rowid_tables_and_garbage_are_rejected(self):
        with self.assertRaises(HashesDbError):
            parse_table_columns("CREATE TABLE t(a INTEGER PRIMARY KEY, b TEXT) WITHOUT ROWID")
        with self.assertRaises(HashesDbError):
            parse_table_columns("CREATE VIRTUAL TABLE t USING fts5")


class TestAgainstRealSqlite(ServerCase):
    def check_ids(self, db, path, ids):
        for rowid in ids:
            with self.subTest(rowid=rowid):
                self.assertEqual(db.lookup_id(rowid), reference_row(path, rowid))

    def test_every_page_and_block_size_matches_sqlite3(self):
        for page_size, block_size in LAYOUTS:
            info = _dbs[page_size]
            ids = info["ids"]
            rng = random.Random(page_size)
            sample = {ids[0], ids[1], ids[-1], ids[-2], *rng.sample(ids, 150)}
            with self.subTest(page_size=page_size, block_size=block_size):
                server = self.serve(info["path"])
                db = make_db(server, block_size=block_size)
                self.check_ids(db, info["path"], sorted(sample))

    def test_ids_that_are_not_in_the_database_return_none(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        db = make_db(server)
        ids = info["ids"]
        # even ids are gaps between rows; the rest are before the first / after the last row
        for rowid in (0, 2, 4, ids[500] + 1, ids[-1] + 1, ids[-1] + 1000, -5, 2**40):
            with self.subTest(rowid=rowid):
                self.assertIsNone(db.lookup_id(rowid))
                self.assertIsNone(reference_row(info["path"], rowid))

    def test_rows_that_predate_the_alter_have_null_for_the_new_columns(self):
        info = _dbs[1024]
        server = self.serve(info["path"])
        db = make_db(server, block_size=4096)
        row = db.lookup_id(info["ids"][3])  # one of the first 50 rows, written before the ALTERs
        self.assertEqual(row["file_name"], game_name("A", 3))
        self.assertIsNone(row["torrents"])
        self.assertIsNone(row["so_id"])
        self.assertIsNone(row["magnet"])
        self.assertEqual(row, reference_row(info["path"], info["ids"][3]))

    def test_rowid_alias_column_is_filled_from_the_rowid(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        row = make_db(server).lookup_id(info["ids"][700])
        self.assertEqual(row["id"], info["ids"][700])

    def test_values_that_need_overflow_pages_are_reassembled(self):
        for page_size, block_size in ((512, 4096), (4096, 4096)):
            info = _dbs[page_size]
            server = self.serve(info["path"])
            db = make_db(server, block_size=block_size)
            # every 97th row carries a ~9000 character magnet link
            for i in (97, 194, 970, 9700):
                with self.subTest(page_size=page_size, i=i):
                    row = db.lookup_id(info["ids"][i])
                    self.assertGreater(len(row["magnet"]), 9000)
                    self.assertEqual(row, reference_row(info["path"], info["ids"][i]))

    def test_unicode_floats_blobs_and_every_integer_width_decode_like_sqlite(self):
        info = _dbs[1024]
        server = self.serve(info["path"])
        db = make_db(server, block_size=4096)
        kinds = set()
        for i in range(len(info["ids"])):
            if i % 211 == 0 or i % 307 == 0 or i % 401 == 0 or i < 30 or i % 97 == 1:
                row = db.lookup_id(info["ids"][i])
                self.assertEqual(row, reference_row(info["path"], info["ids"][i]), f"row {i}")
                kinds.update(type(v).__name__ for v in row.values())
        self.assertTrue({"int", "float", "bytes", "str", "NoneType"} <= kinds, kinds)

    def test_a_table_whose_root_is_not_the_first_table_is_found_by_name(self):
        # build_db adds a decoy 'meta' table before 'files', and an index after it
        info = _dbs[4096]
        server = self.serve(info["path"])
        self.assertEqual(make_db(server).lookup_id(info["ids"][5])["file_name"], game_name("A", 5))


class TestRequests(ServerCase):
    def test_blocks_are_cached_so_repeat_lookups_cost_nothing(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        db = make_db(server, block_size=4096)
        db.lookup_id(info["ids"][1234])
        first = server.request_count
        self.assertGreaterEqual(first, 3)  # header, schema, interior pages, leaf
        self.assertLessEqual(first, 8)
        db.lookup_id(info["ids"][1234])
        self.assertEqual(server.request_count, first)

    def test_neighbouring_rows_share_blocks(self):
        info = _dbs[1024]
        server = self.serve(info["path"])
        db = make_db(server, block_size=65536)  # 64 pages per block
        db.lookup_id(info["ids"][2000])
        warm = server.request_count
        for i in range(2001, 2010):
            db.lookup_id(info["ids"][i])
        self.assertLessEqual(server.request_count - warm, 2)

    def test_only_aligned_ranges_of_at_most_one_block_are_requested(self):
        info = _dbs[512]
        server = self.serve(info["path"])
        db = make_db(server, block_size=4096)
        for rowid in random.Random(1).sample(info["ids"], 40):
            db.lookup_id(rowid)
        self.assertTrue(server.requests)
        for start, end in server.requests:
            self.assertEqual(start % 4096, 0)
            self.assertEqual(end - start + 1, 4096)

    def test_cache_is_bounded(self):
        info = _dbs[512]
        server = self.serve(info["path"])
        db = make_db(server, block_size=4096, cache_blocks=4)
        for rowid in random.Random(2).sample(info["ids"], 60):
            self.assertEqual(db.lookup_id(rowid)["id"], rowid)
        with db._lock:
            version = db._version
        self.assertLessEqual(len(version._cache), 4)


class TestMisbehavingServers(ServerCase):
    def test_a_server_that_ignores_range_is_refused_before_the_whole_file_is_read(self):
        info = _dbs[4096]  # several MB: far bigger than one block
        server = self.serve(info["path"])
        server.mode = "ignore_range"
        db = make_db(server, block_size=4096)
        with self.assertRaises(HashesDbError) as ctx:
            db.lookup_id(info["ids"][10])
        self.assertFalse(ctx.exception.retryable)
        self.assertIn("too large", str(ctx.exception))

    def test_a_small_file_answered_with_200_is_still_not_accepted_as_a_range(self):
        path = _tmp / "tiny.sqlite"
        build_db(path, rows=20, page_size=512, old_rows=2, big_magnet_every=0)
        server = self.serve(path)
        server.mode = "ignore_range"
        db = make_db(server, block_size=65536)  # the whole file fits in one block
        with self.assertRaises(HashesDbError) as ctx:
            db.lookup_id(1)
        self.assertIn("ignored the range", str(ctx.exception))
        self.assertFalse(ctx.exception.retryable)

    def test_a_truncated_body_is_a_retryable_error(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        server.mode = "short"
        with self.assertRaises(HashesDbError) as ctx:
            make_db(server).lookup_id(info["ids"][10])
        self.assertTrue(ctx.exception.retryable)

    def test_a_content_range_for_other_bytes_is_rejected(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        server.mode = "wrong_start"
        db = make_db(server, block_size=4096)
        # block 0 is requested first and legitimately starts at 0; the first *other* block exposes the lie
        with self.assertRaises(HashesDbError) as ctx:
            db.lookup_id(info["ids"][-1])
        self.assertIn("Content-Range", str(ctx.exception))

    def test_transient_5xx_is_retried(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        server.fail_first = 2
        with no_backoff():
            row = make_db(server, retries=3).lookup_id(info["ids"][42])
        self.assertEqual(row["id"], info["ids"][42])

    def test_persistent_5xx_becomes_a_retryable_error(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        server.fail_first = 999
        with no_backoff(), self.assertRaises(HashesDbError) as ctx:
            make_db(server, retries=1).lookup_id(info["ids"][42])
        self.assertTrue(ctx.exception.retryable)

    def test_a_missing_file_is_a_permanent_error(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        server.mode = "not_found"
        with self.assertRaises(HashesDbError) as ctx:
            make_db(server).lookup_id(1)
        self.assertFalse(ctx.exception.retryable)

    def test_nothing_listening_is_a_retryable_error(self):
        db = HashesDb("http://127.0.0.1:1/hashes.db", gate=NO_GATE, timeout=2.0, retries=0)
        with self.assertRaises(HashesDbError) as ctx:
            db.lookup_id(1)
        self.assertTrue(ctx.exception.retryable)

    def test_a_failed_lookup_can_succeed_later(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        server.fail_first = 1
        db = make_db(server)
        with no_backoff(), self.assertRaises(HashesDbError):
            db.lookup_id(info["ids"][9])
        self.assertEqual(db.lookup_id(info["ids"][9])["id"], info["ids"][9])


class TestChangedDatabase(ServerCase):
    def test_a_replaced_database_is_noticed_and_never_mixed_with_the_old_one(self):
        a = build_db(_tmp / "gen-a.sqlite", rows=6000, page_size=1024, tag="A")
        b = build_db(_tmp / "gen-b.sqlite", rows=6000, page_size=1024, tag="B")
        server = self.serve(a["path"])
        db = make_db(server, block_size=4096)
        with mock.patch("minerva.core.hashes_db.log_activity") as log:
            self.assertEqual(db.lookup_id(a["ids"][10])["file_name"], game_name("A", 10))
            server.swap(b["path"])
            # a row whose blocks are not cached forces a request, which reveals the new ETag
            last = db.lookup_id(a["ids"][-1])
            self.assertEqual(last["file_name"], game_name("B", 5999))
            # the old file's cached blocks are gone, so an earlier row now comes from the new file
            self.assertEqual(db.lookup_id(a["ids"][10])["file_name"], game_name("B", 10))
            self.assertTrue(log.called)

    def test_a_database_that_never_stops_changing_gives_up(self):
        a = build_db(_tmp / "flap-a.sqlite", rows=3000, page_size=1024, tag="A")
        server = self.serve(a["path"])
        db = make_db(server, block_size=4096)
        real_answer = server._answer

        def flapping(handler):
            server.swap(a["path"])  # same file, new ETag on every response
            real_answer(handler)

        server._answer = flapping
        with mock.patch("minerva.core.hashes_db.log_activity"), self.assertRaises(HashesDbError) as ctx:
            db.lookup_id(a["ids"][5])
        self.assertTrue(ctx.exception.retryable)


class TestConcurrency(ServerCase):
    def test_many_threads_get_correct_rows(self):
        info = _dbs[1024]
        server = self.serve(info["path"])
        db = make_db(server, block_size=4096)
        errors, wrong = [], []

        def worker(seed):
            rng = random.Random(seed)
            try:
                for rowid in rng.sample(info["ids"], 60):
                    if db.lookup_id(rowid) != reference_row(info["path"], rowid):
                        wrong.append(rowid)
            except Exception as e:  # noqa: BLE001 - report any failure from a worker
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(wrong, [])

    def test_simultaneous_lookups_of_the_same_row_share_every_block_fetch(self):
        info = _dbs[1024]
        target = info["ids"][8000]
        solo = self.serve(info["path"])
        make_db(solo, block_size=4096).lookup_id(target)
        needed = solo.request_count

        server = self.serve(info["path"])
        server.delay = 0.05  # keep the fetches in flight long enough for the threads to pile up
        db = make_db(server, block_size=4096)
        barrier = threading.Barrier(10)
        results = []

        def worker():
            barrier.wait()
            results.append(db.lookup_id(target))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(len(results), 10)
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(server.request_count, needed)

    def test_one_failure_is_shared_instead_of_every_waiting_thread_retrying_in_turn(self):
        info = _dbs[4096]
        server = self.serve(info["path"])
        server.mode = "not_found"
        server.hold = threading.Event()  # the first request stays in flight while the others queue up
        db = make_db(server)
        outcomes = []
        barrier = threading.Barrier(6)

        def worker():
            barrier.wait()
            try:
                db.lookup_id(info["ids"][3])
                outcomes.append("row")
            except HashesDbError as e:
                outcomes.append(e)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 5
        while server.request_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.5)  # let the other five reach the lock behind the one that is fetching
        server.hold.set()
        for t in threads:
            t.join(30)
        self.assertEqual(len(outcomes), 6)
        self.assertTrue(all(isinstance(o, HashesDbError) for o in outcomes), outcomes)
        self.assertEqual(server.request_count, 1)


class TestCorruptAndUnexpectedFiles(ServerCase):
    def copy_of(self, name, page_size=4096):
        dest = _tmp / name
        shutil.copyfile(_dbs[page_size]["path"], dest)
        return dest

    def test_a_file_that_is_not_sqlite_is_rejected(self):
        path = _tmp / "notdb.bin"
        path.write_bytes(b"<html>nope</html>" * 2000)
        with self.assertRaises(HashesDbError) as ctx:
            make_db(self.serve(path)).lookup_id(1)
        self.assertIn("not a SQLite", str(ctx.exception))

    def test_a_schema_without_the_needed_columns_is_reported(self):
        path = _tmp / "wrongschema.sqlite"
        build_db(path, columns_sql="CREATE TABLE files(id INTEGER PRIMARY KEY, name TEXT)")
        with self.assertRaises(HashesDbError) as ctx:
            make_db(self.serve(path)).lookup_id(1)
        self.assertIn("missing columns", str(ctx.exception))
        self.assertIn("so_id", str(ctx.exception))

    def test_a_database_without_a_files_table_is_reported(self):
        path = _tmp / "nofiles.sqlite"
        build_db(path, columns_sql="CREATE TABLE other(id INTEGER PRIMARY KEY, x TEXT)")
        with self.assertRaises(HashesDbError) as ctx:
            make_db(self.serve(path)).lookup_id(1)
        self.assertIn("no 'files' table", str(ctx.exception))

    def test_utf16_databases_are_refused_instead_of_misdecoded(self):
        path = _tmp / "utf16.sqlite"
        build_db(path, columns_sql=(
            "CREATE TABLE files(id INTEGER PRIMARY KEY, full_path TEXT, file_name TEXT, size INTEGER, "
            "torrents TEXT, so_id INTEGER, magnet TEXT)"), encoding="UTF-16le")
        with self.assertRaises(HashesDbError) as ctx:
            make_db(self.serve(path)).lookup_id(1)
        self.assertIn("UTF-8", str(ctx.exception))

    def test_a_damaged_leaf_page_fails_that_lookup_but_not_others(self):
        path = self.copy_of("damaged.sqlite")
        info = _dbs[4096]
        victim, healthy = 4000, 100
        # full_path is immediately followed by file_name only in the table's own leaf (the index has no copy)
        needle = f"Redump/Sony/{game_name('A', victim)}{game_name('A', victim)}".encode()
        data = bytearray(path.read_bytes())
        offset = data.find(needle)
        self.assertGreater(offset, 0)
        data[(offset // 4096) * 4096] = 0xFF  # the page-type byte
        path.write_bytes(bytes(data))
        db = make_db(self.serve(path))
        with self.assertRaises(HashesDbError) as ctx:
            db.lookup_id(info["ids"][victim])
        self.assertIn("corrupt", str(ctx.exception))
        self.assertEqual(db.lookup_id(info["ids"][healthy])["file_name"], game_name("A", healthy))

    def test_a_file_cut_short_fails_cleanly_for_rows_in_the_missing_part(self):
        path = self.copy_of("cut.sqlite")
        info = _dbs[4096]
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])  # interior pages sit at ~38% and ~75%
        db = make_db(self.serve(path))
        found = failed = 0
        for rowid in random.Random(4).sample(info["ids"], 60):
            try:
                row = db.lookup_id(rowid)
            except HashesDbError as e:
                self.assertIn("out of range", str(e))
                failed += 1
            else:
                self.assertEqual(row, reference_row(info["path"], rowid))  # never wrong data, only a failure
                found += 1
        self.assertGreater(found, 0)
        self.assertGreater(failed, 0)

    def broken_overflow_outcomes(self, name, patch):
        """Corrupt the head of an overflow chain with ``patch(head_page_number)`` -> 4 pointer bytes, then look
        up every row that has a 20 KB magnet. Returns ({row index: row or error}, {row index: true row})."""
        path = _tmp / name
        info = build_db(path, rows=300, page_size=1024, big_magnet_every=50, big_magnet_chars=20000, old_rows=10)
        big = range(50, 300, 50)  # each of these is spread over ~20 overflow pages
        expected = {i: reference_row(path, info["ids"][i]) for i in big}
        data = bytearray(path.read_bytes())
        # an overflow page is a 4-byte "next page" pointer followed by nothing but magnet bytes
        head = next(n for n in range(2, len(data) // 1024) if data[n * 1024 + 4:n * 1024 + 104] == b"m" * 100)
        data[head * 1024:head * 1024 + 4] = patch(head + 1)  # page numbers count from 1
        path.write_bytes(bytes(data))
        db = make_db(self.serve(path))
        outcomes = {}
        for i in big:
            try:
                outcomes[i] = db.lookup_id(info["ids"][i])
            except HashesDbError as e:
                outcomes[i] = e
        return outcomes, expected

    def assert_exactly_one_row_fails(self, outcomes, expected, message):
        broken = [i for i, outcome in outcomes.items() if isinstance(outcome, HashesDbError)]
        self.assertEqual(len(broken), 1, {i: type(o).__name__ for i, o in outcomes.items()})
        self.assertIn(message, str(outcomes[broken[0]]))
        for i, outcome in outcomes.items():
            if i not in broken:
                self.assertEqual(outcome, expected[i])  # the damage must not leak into other rows

    def test_an_overflow_chain_that_loops_is_detected(self):
        outcomes, expected = self.broken_overflow_outcomes("loop.sqlite", lambda self_page: self_page.to_bytes(4, "big"))
        self.assert_exactly_one_row_fails(outcomes, expected, "overflow chain loops")

    def test_an_overflow_chain_that_ends_too_early_is_detected(self):
        outcomes, expected = self.broken_overflow_outcomes("short.sqlite", lambda _page: b"\x00\x00\x00\x00")
        self.assert_exactly_one_row_fails(outcomes, expected, "overflow chain ended early")


if __name__ == "__main__":
    unittest.main()
