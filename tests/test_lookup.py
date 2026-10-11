import pathlib
import tempfile
import unittest

from minerva.constants import TRACKERS
from minerva.core import lookup
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

    def test_take_empties_the_collector_and_bounds_memory(self):
        errors = lookup.LookupErrors(max_keep=3)
        for i in range(10):
            errors.add("lookup", str(i), "x")
        taken = errors.take()
        self.assertEqual(len(taken), 4)  # 3 kept + a 'N more' marker
        self.assertEqual(errors.take(), [])


if __name__ == "__main__":
    unittest.main()
