import pathlib
import tempfile
import unittest
import unittest.mock

from minerva.constants import TRACKERS
from minerva.core import lookup
from minerva.core.hashes_db import HashesDbError
from minerva.core.http import HttpError
from minerva.core.torrent_cache import TorrentCache, TorrentSummary, TorrentInvalid

NAMES = ("a.zip", "b.zip", "c.zip")


def _parse(data):
    if not data.startswith(b"d"):
        raise TorrentInvalid("bad")
    return TorrentSummary(len(NAMES), NAMES)


class _Fetcher:
    def __init__(self, payloads=None):
        self.payloads = list(payloads or [b"d4:infode"])
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        item = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


class TestRomResolver(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rows = {}
        self.rom_fetches = []
        self.torrent_fetch = _Fetcher()
        self.now = [0.0]
        self.cache = TorrentCache(pathlib.Path(self._tmp.name), fetch=self.torrent_fetch, parse=_parse,
                                  base_url="https://x.test/", clock=lambda: self.now[0])
        self.resolver = lookup.RomResolver(self.cache, self._fetch_rom)

    def _fetch_rom(self, rom_id):
        self.rom_fetches.append(rom_id)
        row = self.rows.get(rom_id)
        if isinstance(row, Exception):
            raise row
        return row

    def test_resolves_to_cached_torrent_path_and_verified_index(self):
        self.rows["1"] = {"so_id": 1, "torrents": "t/coll.torrent", "full_path": "x/b.zip"}
        result = self.resolver.resolve("1", "b.zip")
        self.assertEqual(result.so_id, 1)
        self.assertTrue(pathlib.Path(result.source).is_file())
        self.assertEqual(result.full_path, "x/b.zip")

    def test_many_files_from_one_collection_fetch_the_torrent_once(self):
        for i, name in enumerate(NAMES):
            self.rows[str(i)] = {"so_id": i, "torrents": "t/coll.torrent"}
        for i, name in enumerate(NAMES):
            self.resolver.resolve(str(i), name)
        self.assertEqual(self.torrent_fetch.calls, 1)

    def test_rom_rows_are_cached(self):
        self.rows["1"] = {"so_id": 0, "torrents": "t/coll.torrent"}
        self.resolver.resolve("1", "a.zip")
        self.resolver.resolve("1", "a.zip")
        self.assertEqual(self.rom_fetches, ["1"])

    def test_stale_cached_torrent_triggers_one_refresh(self):
        self.rows["1"] = {"so_id": 2, "torrents": "t/coll.torrent"}
        self.resolver.resolve("1", "c.zip")  # populates the cache
        stale_calls = self.torrent_fetch.calls
        self.now[0] += 3600  # the cached copy is an hour old, so a refresh is allowed
        # Server renumbered: 'a.zip' is now at index 2 in the *fresh* torrent, but the cache says index 0.
        self.rows["2"] = {"so_id": 1, "torrents": "t/coll.torrent"}
        result = self.resolver.resolve("2", "a.zip")  # idx 1 != 'a.zip' -> refresh -> name fallback -> 0
        self.assertEqual(result.so_id, 0)
        self.assertEqual(self.torrent_fetch.calls, stale_calls + 1)

    def test_a_mismatch_right_after_fetching_does_not_refetch_the_torrent(self):
        self.rows["1"] = {"so_id": 1, "torrents": "t/coll.torrent"}
        result = self.resolver.resolve("1", "a.zip")  # server says 1, torrent says a.zip is 0
        self.assertEqual(result.so_id, 0)  # fixed by name from the torrent we just downloaded
        self.assertEqual(self.torrent_fetch.calls, 1)

    def test_index_mismatch_after_refresh_is_reported(self):
        self.rows["1"] = {"so_id": 0, "torrents": "t/coll.torrent"}
        with self.assertRaises(lookup.LookupFailure) as ctx:
            self.resolver.resolve("1", "missing.zip")
        self.assertEqual(ctx.exception.kind, "index_mismatch")

    def test_not_found_and_lookup_errors(self):
        self.rows["gone"] = None
        with self.assertRaises(lookup.LookupFailure) as ctx:
            self.resolver.resolve("gone", "a.zip")
        self.assertEqual(ctx.exception.kind, "not_found")
        self.rows["boom"] = HttpError("HTTP 503", status=503, retryable=True)
        with self.assertRaises(lookup.LookupFailure) as ctx:
            self.resolver.resolve("boom", "a.zip")
        self.assertEqual(ctx.exception.kind, "lookup")

    def test_torrent_failure_falls_back_to_magnet_without_default_trackers(self):
        self.torrent_fetch.payloads = [HttpError("HTTP 503", status=503, retryable=True)]
        magnet = "magnet:?xt=urn:btih:abc123&dn=Game" + TRACKERS
        self.rows["1"] = {"so_id": 1, "torrents": "t/coll.torrent", "magnet": magnet}
        result = self.resolver.resolve("1", "b.zip")
        self.assertEqual(result.source, "magnet:?xt=urn:btih:abc123&dn=Game")
        self.assertEqual(result.so_id, 1)

    def test_torrent_failure_without_magnet_is_a_torrent_fetch_error(self):
        self.torrent_fetch.payloads = [HttpError("HTTP 503", status=503, retryable=True)]
        self.rows["1"] = {"so_id": 1, "torrents": "t/coll.torrent"}
        with self.assertRaises(lookup.LookupFailure) as ctx:
            self.resolver.resolve("1", "b.zip")
        self.assertEqual(ctx.exception.kind, "torrent_fetch")

    def test_magnet_only_row_and_no_torrent_row(self):
        self.rows["m"] = {"so_id": 0, "magnet": "magnet:?xt=urn:btih:zzz"}
        self.assertEqual(self.resolver.resolve("m", "a.zip").source, "magnet:?xt=urn:btih:zzz")
        self.rows["n"] = {"so_id": 0}
        with self.assertRaises(lookup.LookupFailure) as ctx:
            self.resolver.resolve("n", "a.zip")
        self.assertEqual(ctx.exception.kind, "no_torrent")

    def test_invalid_so_id_is_reported_not_raised_as_valueerror(self):
        self.rows["1"] = {"so_id": "abc", "torrents": "t/coll.torrent"}
        with self.assertRaises(lookup.LookupFailure):
            self.resolver.resolve("1", "a.zip")


class _FakeDb:
    """Stands in for HashesDb: ``rows`` maps id -> row dict, None (unknown id) or an exception to raise."""

    def __init__(self):
        self.rows = {}
        self.calls = []

    def lookup_id(self, rowid):
        self.calls.append(rowid)
        row = self.rows.get(rowid)
        if isinstance(row, Exception):
            raise row
        return row


def _db_row(rowid, name="b.zip", **extra):
    row = {"id": rowid, "full_path": f"dir/{name}", "file_name": name, "so_id": 1,
           "torrents": "t/coll.torrent", "magnet": "magnet:?xt=urn:btih:abc"}
    row.update(extra)
    return row


class TestRomResolverWithDatabase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.now = [0.0]
        self.torrent_fetch = _Fetcher()
        self.cache = TorrentCache(pathlib.Path(self._tmp.name), fetch=self.torrent_fetch, parse=_parse,
                                  base_url="https://x.test/", clock=lambda: self.now[0])
        self.db = _FakeDb()
        self.page_rows = {}
        self.page_calls = []
        self.page_error = None
        self.resolver = lookup.RomResolver(self.cache, self._fetch_page, rom_db=self.db,
                                           clock=lambda: self.now[0], outage_threshold=3, outage_cooldown=60.0)

    def _fetch_page(self, rom_id):
        self.page_calls.append(rom_id)
        if self.page_error is not None:
            raise self.page_error
        return self.page_rows.get(rom_id)

    def test_the_database_answers_without_touching_the_rom_page(self):
        self.db.rows[10] = _db_row(10)
        result = self.resolver.resolve("10", "b.zip")
        self.assertEqual(result.so_id, 1)
        self.assertTrue(pathlib.Path(result.source).is_file())
        self.assertEqual(result.full_path, "dir/b.zip")
        self.assertEqual(self.page_calls, [])

    def test_a_dead_rom_page_does_not_matter_when_the_database_has_the_rows(self):
        self.page_error = HttpError("The read operation timed out", retryable=True)
        for i in range(30):
            self.db.rows[i] = _db_row(i, name=NAMES[i % 3], so_id=i % 3)
        for i in range(30):
            self.assertEqual(self.resolver.resolve(str(i), NAMES[i % 3]).so_id, i % 3)
        self.assertEqual(self.page_calls, [])

    def test_names_are_compared_ignoring_case(self):
        self.db.rows[10] = _db_row(10, name="inFamous (USA).iso", so_id=0)
        # the listing only knows the lowercase form; the torrent index check is case-insensitive as well
        self.resolver.row_for("10", "infamous (usa).iso")
        self.assertEqual(self.page_calls, [])

    def test_rows_for_a_different_file_are_not_used(self):
        self.db.rows[10] = _db_row(10, name="other.zip")
        self.page_rows["10"] = {"so_id": 1, "torrents": "t/coll.torrent", "full_path": "page/b.zip"}
        result = self.resolver.resolve("10", "b.zip")
        self.assertEqual(result.full_path, "page/b.zip")  # came from the ROM page
        self.assertEqual(self.page_calls, ["10"])

    def test_unknown_ids_and_incomplete_rows_fall_back_to_the_rom_page(self):
        self.page_rows["11"] = {"so_id": 1, "torrents": "t/coll.torrent", "full_path": "page/11"}
        self.page_rows["12"] = {"so_id": 1, "torrents": "t/coll.torrent", "full_path": "page/12"}
        self.page_rows["13"] = {"so_id": 1, "torrents": "t/coll.torrent", "full_path": "page/13"}
        self.db.rows[12] = _db_row(12, so_id=None)             # older row without a file index
        self.db.rows[13] = _db_row(13, torrents=None, magnet=None)
        for rom_id in ("11", "12", "13"):
            self.assertEqual(self.resolver.resolve(rom_id, "b.zip").full_path, f"page/{rom_id}")
        self.assertEqual(self.page_calls, ["11", "12", "13"])

    def test_a_non_numeric_id_skips_the_database(self):
        self.page_rows["abc"] = {"so_id": 1, "torrents": "t/coll.torrent"}
        self.resolver.resolve("abc", "b.zip")
        self.assertEqual(self.db.calls, [])

    def test_a_database_error_falls_back_to_the_page_and_the_page_wins(self):
        self.db.rows[10] = HashesDbError("The read operation timed out", retryable=True)
        self.page_rows["10"] = {"so_id": 1, "torrents": "t/coll.torrent", "full_path": "page/b.zip"}
        self.assertEqual(self.resolver.resolve("10", "b.zip").full_path, "page/b.zip")

    def test_a_crashing_reader_never_blocks_queueing(self):
        self.db.rows[10] = RuntimeError("bug in the reader")
        self.page_rows["10"] = {"so_id": 1, "torrents": "t/coll.torrent", "full_path": "page/b.zip"}
        with unittest.mock.patch("minerva.core.lookup.log_error"):
            self.assertEqual(self.resolver.resolve("10", "b.zip").full_path, "page/b.zip")

    def test_the_database_breaker_stops_asking_a_broken_database(self):
        for i in range(10):
            self.db.rows[i] = HashesDbError("timed out", retryable=True)
            self.page_rows[str(i)] = {"so_id": 1, "torrents": "t/coll.torrent"}
        for i in range(10):
            self.resolver.resolve(str(i), "b.zip")
        self.assertEqual(len(self.db.calls), 3)  # tripped after three failures
        self.now[0] += 61
        self.db.rows[20] = _db_row(20)
        self.resolver.resolve("20", "b.zip")  # the probe after the cooldown
        self.assertEqual(len(self.db.calls), 4)
        self.assertEqual(self.page_calls.count("20"), 0)  # and it worked, so the page was not needed

    def test_unknown_ids_do_not_count_as_database_failures(self):
        for i in range(10):
            self.page_rows[str(i)] = {"so_id": 1, "torrents": "t/coll.torrent"}
            self.resolver.resolve(str(i), "b.zip")
        self.assertEqual(len(self.db.calls), 10)

    def test_both_sources_failing_gives_one_readable_message(self):
        self.db.rows[10] = HashesDbError("timed out", retryable=True)
        self.page_error = HttpError("The read operation timed out (https://x.test/rom?id=10)", retryable=True)
        with self.assertRaises(lookup.LookupFailure) as ctx:
            self.resolver.resolve("10", "b.zip")
        message = ctx.exception.message
        self.assertEqual(ctx.exception.kind, "lookup")
        self.assertIn("timed out", message)
        self.assertIn("file database", message)
        self.assertNotIn("http", message)  # no URLs in the message

    def test_an_id_that_exists_nowhere_is_not_found_not_a_lookup_failure(self):
        with self.assertRaises(lookup.LookupFailure) as ctx:
            self.resolver.resolve("99", "b.zip")
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_rows_are_remembered_so_each_id_is_looked_up_once(self):
        self.db.rows[10] = _db_row(10)
        self.resolver.resolve("10", "b.zip")
        self.resolver.resolve("10", "b.zip")
        self.assertEqual(self.db.calls, [10])

    def test_a_torrent_the_snapshot_points_to_but_the_archive_no_longer_serves_uses_the_live_row(self):
        self.torrent_fetch.payloads = [HttpError("HTTP 404", status=404), b"d4:infode"]
        self.db.rows[10] = _db_row(10, torrents="old/coll.torrent")
        self.page_rows["10"] = {"so_id": 1, "torrents": "new/coll.torrent", "full_path": "page/b.zip"}
        result = self.resolver.resolve("10", "b.zip")
        self.assertTrue(pathlib.Path(result.source).is_file())  # a real torrent file, not the magnet
        self.assertEqual(result.full_path, "page/b.zip")
        self.assertEqual(self.page_calls, ["10"])

    def test_a_gone_torrent_with_the_page_down_falls_back_to_the_magnet(self):
        self.torrent_fetch.payloads = [HttpError("HTTP 404", status=404)]
        self.page_error = HttpError("timed out", retryable=True)
        self.db.rows[10] = _db_row(10, torrents="old/coll.torrent")
        result = self.resolver.resolve("10", "b.zip")
        self.assertEqual(result.source, "magnet:?xt=urn:btih:abc")

    def test_a_transient_torrent_error_does_not_ask_the_page(self):
        self.torrent_fetch.payloads = [HttpError("HTTP 503", status=503, retryable=True)]
        self.db.rows[10] = _db_row(10)
        result = self.resolver.resolve("10", "b.zip")
        self.assertEqual(result.source, "magnet:?xt=urn:btih:abc")
        self.assertEqual(self.page_calls, [])


class TestDescribeError(unittest.TestCase):
    def test_urls_are_stripped_from_the_end_of_messages(self):
        for raw, expected in (
            ("The read operation timed out (https://minerva-archive.org/rom?id=1070219)", "The read operation timed out"),
            ("HTTP 503 for https://minerva-archive.org/rom?id=1", "HTTP 503"),
            ("Response too large (1759227904 bytes)", "Response too large (1759227904 bytes)"),
            ("server ignored the range request (HTTP 200)", "server ignored the range request (HTTP 200)"),
        ):
            self.assertEqual(lookup.describe_error(Exception(raw)), expected)

    def test_an_empty_message_falls_back_to_the_type_name(self):
        self.assertEqual(lookup.describe_error(RuntimeError("")), "RuntimeError")


class TestOutageBreaker(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.fetches = []
        self.mode = "timeout"
        self.cache = TorrentCache(pathlib.Path(tempfile.mkdtemp()), fetch=lambda url: b"d4:infode", parse=_parse,
                                  base_url="https://x.test/")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.cache.cache_dir, ignore_errors=True))
        self.resolver = lookup.RomResolver(self.cache, self._fetch, clock=lambda: self.now[0],
                                           outage_threshold=3, outage_cooldown=60.0)

    def _fetch(self, rom_id):
        self.fetches.append(rom_id)
        if self.mode == "timeout":
            raise HttpError("The read operation timed out", retryable=True)
        if self.mode == "404":
            raise HttpError("HTTP 404", status=404, retryable=False)
        return {"so_id": 0, "magnet": "magnet:?xt=urn:btih:abc"}

    def lookup(self, rom_id="1"):
        try:
            self.resolver.row_for(rom_id)
            return "ok"
        except lookup.LookupFailure as e:
            return e.message

    def test_repeated_timeouts_trip_the_breaker_and_later_lookups_fail_instantly(self):
        for i in range(3):
            self.assertIn("timed out", self.lookup(str(i)))
        before = len(self.fetches)
        for i in range(10, 20):
            self.assertEqual(self.lookup(str(i)), lookup.OUTAGE_MESSAGE)
        self.assertEqual(len(self.fetches), before)  # no request was made while the site is down

    def test_one_probe_is_allowed_after_the_cooldown_and_success_closes_the_breaker(self):
        for i in range(3):
            self.lookup(str(i))
        self.now[0] += 61
        self.mode = "ok"
        self.assertEqual(self.lookup("100"), "ok")
        self.assertEqual(self.lookup("101"), "ok")  # fully closed again

    def test_a_failed_probe_reopens_the_breaker_immediately(self):
        for i in range(3):
            self.lookup(str(i))
        self.now[0] += 61
        self.assertIn("timed out", self.lookup("100"))  # the probe fails...
        self.assertEqual(self.lookup("101"), lookup.OUTAGE_MESSAGE)  # ...so it is open again

    def test_permanent_errors_do_not_count_as_an_outage(self):
        self.mode = "404"
        for i in range(10):
            self.assertIn("404", self.lookup(str(i)))
        self.mode = "ok"
        self.assertEqual(self.lookup("200"), "ok")

    def test_rows_already_cached_are_still_served_during_an_outage(self):
        self.mode = "ok"
        self.assertEqual(self.lookup("7"), "ok")
        self.mode = "timeout"
        for i in range(3):
            self.lookup(str(100 + i))
        self.assertEqual(self.lookup("7"), "ok")


class TestStripDefaultTrackers(unittest.TestCase):
    def test_removes_only_default_trackers(self):
        magnet = "magnet:?xt=urn:btih:abc&dn=Name&tr=udp%3A%2F%2Fcustom.example%3A1%2Fannounce" + TRACKERS
        out = lookup.strip_default_trackers(magnet)
        self.assertEqual(out, "magnet:?xt=urn:btih:abc&dn=Name&tr=udp%3A%2F%2Fcustom.example%3A1%2Fannounce")

    def test_plain_and_non_magnet_inputs_pass_through(self):
        self.assertEqual(lookup.strip_default_trackers("magnet:?xt=urn:btih:abc"), "magnet:?xt=urn:btih:abc")
        self.assertEqual(lookup.strip_default_trackers("/some/file.torrent"), "/some/file.torrent")
        self.assertEqual(lookup.strip_default_trackers(""), "")

    def test_shrinks_a_persisted_magnet_dramatically(self):
        magnet = "magnet:?xt=urn:btih:abc" + TRACKERS
        self.assertGreater(len(magnet), 2000)
        self.assertEqual(lookup.strip_default_trackers(magnet), "magnet:?xt=urn:btih:abc")


class TestLookupErrors(unittest.TestCase):
    def test_single_error_keeps_the_specific_dialog_wording(self):
        errors = lookup.LookupErrors()
        errors.add("not_found", "Game.zip", "was not found on the server")
        title, body = lookup.LookupErrors.format(errors.take())
        self.assertEqual(title, "Not Found")
        self.assertEqual(body, "Game.zip was not found on the server.")

    def test_bulk_errors_become_one_summary(self):
        errors = lookup.LookupErrors()
        for i in range(40):
            errors.add("lookup", f"Game {i}.zip", "HTTP 503 for https://x\nsecond line")
        errors.add("not_found", "Gone.zip", "was not found on the server")
        title, body = lookup.LookupErrors.format(errors.take())
        self.assertEqual(title, "Some downloads could not be queued")
        self.assertIn("41 files could not be queued", body)
        self.assertIn("Lookup Failed: 40", body)
        self.assertIn("Not Found: 1", body)
        self.assertIn("…and 36 more", body)
        self.assertEqual(body.count("Game "), 5)  # only a handful of examples
        self.assertIn("under Errors", body)  # failures stay visible and retryable in the downloads list

    def test_take_empties_the_collector_and_bounds_memory(self):
        errors = lookup.LookupErrors(max_keep=3)
        for i in range(10):
            errors.add("lookup", str(i), "x")
        taken = errors.take()
        self.assertEqual(len(taken), 4)  # 3 kept + a 'N more' marker
        self.assertEqual(errors.take(), [])


if __name__ == "__main__":
    unittest.main()
