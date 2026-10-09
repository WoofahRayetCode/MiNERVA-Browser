import os
import pathlib
import tempfile
import threading
import time
import unittest

from minerva.core import torrent_cache as tc
from minerva.core.http import HttpError

try:
    import libtorrent  # noqa: F401
    from tests.lt_fixtures import make_torrent_bytes
    LT = True
except ImportError:
    LT = False


def _fake_parse(data: bytes) -> tc.TorrentSummary:
    """libtorrent-free parser: any bencoded-looking blob is 'valid' with 3 files."""
    if not data.startswith(b"d") or b"4:info" not in data:
        raise tc.TorrentInvalid("not a torrent")
    return tc.TorrentSummary(3, ("a.zip", "b.zip", "c.zip"))


GOOD = b"d8:announce3:x:y4:infod4:name1:xee"


class _Fetch:
    def __init__(self, payload=GOOD, delay=0.0, error=None):
        self.payload, self.delay, self.error = payload, delay, error
        self.urls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, url):
        with self._lock:
            self.urls.append(url)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.payload


class TestTorrentCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def cache(self, fetch, **kw):
        return tc.TorrentCache(self.dir, fetch=fetch, parse=_fake_parse, base_url="https://x.test/assets/", **kw)

    def test_concurrent_requests_share_one_fetch(self):
        fetch = _Fetch(delay=0.2)
        cache = self.cache(fetch)
        results, errors = [], []

        def worker():
            try:
                results.append(cache.ensure("torrents/Sony - PS1.torrent"))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        [t.start() for t in threads]
        [t.join() for t in threads]

        self.assertEqual(errors, [])
        self.assertEqual(len(fetch.urls), 1)
        self.assertEqual(len(set(results)), 1)
        self.assertTrue(results[0].is_file())
        self.assertEqual(fetch.urls[0], "https://x.test/assets/torrents/Sony%20-%20PS1.torrent")

    def test_second_call_is_served_from_disk(self):
        fetch = _Fetch()
        cache = self.cache(fetch)
        a = cache.ensure("t/one.torrent")
        b = cache.ensure("t/one.torrent")
        self.assertEqual(a, b)
        self.assertEqual(len(fetch.urls), 1)

    def test_cache_survives_a_new_instance(self):
        self.cache(_Fetch()).ensure("t/one.torrent")
        fetch = _Fetch()
        self.cache(fetch).ensure("t/one.torrent")
        self.assertEqual(fetch.urls, [])

    def test_html_error_page_is_rejected_and_never_cached(self):
        cache = self.cache(_Fetch(payload=b"<html>503 Service Unavailable</html>"))
        with self.assertRaises(tc.TorrentInvalid):
            cache.ensure("t/one.torrent")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_corrupt_cached_file_is_evicted_and_refetched(self):
        fetch = _Fetch()
        cache = self.cache(fetch)
        path = cache.path_for("t/one.torrent")
        path.write_bytes(b"<html>truncated")  # what the old non-atomic cache could leave behind
        self.assertEqual(cache.ensure("t/one.torrent"), path)
        self.assertEqual(len(fetch.urls), 1)
        self.assertTrue(path.read_bytes().startswith(b"d"))

    def test_refresh_replaces_an_old_cached_copy(self):
        now = [0.0]
        fetch = _Fetch()
        cache = self.cache(fetch, clock=lambda: now[0])
        cache.ensure("t/one.torrent")
        now[0] += tc.REFRESH_COOLDOWN + 1  # the copy is old enough that a refresh is meaningful
        cache.ensure("t/one.torrent", refresh=True)
        self.assertEqual(len(fetch.urls), 2)


    def test_refresh_right_after_a_fetch_does_not_download_the_same_torrent_again(self):
        now = [0.0]
        fetch = _Fetch()
        cache = self.cache(fetch, clock=lambda: now[0])
        cache.ensure("t/one.torrent")
        for _ in range(5):  # five queued files whose index "mismatches"
            cache.ensure("t/one.torrent", refresh=True)
        self.assertEqual(len(fetch.urls), 1)
        now[0] += tc.REFRESH_COOLDOWN + 1
        cache.ensure("t/one.torrent", refresh=True)  # a genuinely stale copy is refreshed
        self.assertEqual(len(fetch.urls), 2)

    def test_a_failed_refresh_keeps_the_good_cached_copy(self):
        now = [0.0]
        fetch = _Fetch()
        cache = self.cache(fetch, clock=lambda: now[0])
        path = cache.ensure("t/one.torrent")
        now[0] += tc.REFRESH_COOLDOWN + 1
        fetch.error = HttpError("HTTP 503", status=503, retryable=True)
        with self.assertRaises(HttpError):
            cache.ensure("t/one.torrent", refresh=True)
        self.assertTrue(path.is_file())  # not deleted before the replacement arrived
        fetch.error = None
        self.assertEqual(cache.ensure("t/one.torrent"), path)
        self.assertEqual(len(fetch.urls), 2)  # served from disk, no extra fetch

    def test_failure_reaches_every_waiter_and_is_not_sticky(self):
        fetch = _Fetch(delay=0.1, error=HttpError("HTTP 503", status=503, retryable=True))
        cache = self.cache(fetch)
        outcomes = []

        def worker():
            try:
                cache.ensure("t/one.torrent")
            except HttpError as e:
                outcomes.append(e.status)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(outcomes, [503] * 5)
        self.assertEqual(len(fetch.urls), 1)

        fetch.error = None  # server recovered; the next call must retry
        self.assertTrue(cache.ensure("t/one.torrent").is_file())

    def test_legacy_per_rom_copy_is_adopted_without_fetching(self):
        legacy = self.dir / "torrents_Sony - PS1.torrent__0123456789.torrent"
        legacy.write_bytes(GOOD)
        fetch = _Fetch()
        path = self.cache(fetch).ensure("torrents/Sony - PS1.torrent")
        self.assertEqual(fetch.urls, [])
        self.assertEqual(path.read_bytes(), GOOD)
        self.assertNotEqual(path, legacy)

    def test_corrupt_legacy_copy_is_skipped(self):
        (self.dir / "torrents_PS1.torrent__0123456789.torrent").write_bytes(b"<html>")
        fetch = _Fetch()
        self.cache(fetch).ensure("torrents/PS1.torrent")
        self.assertEqual(len(fetch.urls), 1)

    def test_stale_part_files_are_removed_on_startup_but_fresh_ones_kept(self):
        stale = self.dir / "a.torrent.deadbeef.part"
        fresh = self.dir / "b.torrent.cafef00d.part"
        stale.write_bytes(b"x")
        fresh.write_bytes(b"x")
        old = time.time() - 7200
        os.utime(stale, (old, old))
        self.cache(_Fetch())
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_path_for_is_stable_flat_and_windows_safe(self):
        cache = self.cache(_Fetch())
        p = cache.path_for('torrents/we:ird*na"me?.torrent')
        self.assertEqual(p, cache.path_for('torrents/we:ird*na"me?.torrent'))
        self.assertEqual(p.parent, self.dir)
        self.assertFalse(any(c in p.name for c in '<>:"|?*'))
        self.assertNotEqual(p, cache.path_for("torrents/other.torrent"))

    def test_resolve_index_exact_mismatch_and_fallback(self):
        cache = self.cache(_Fetch())
        path = cache.ensure("t/one.torrent")
        self.assertEqual(cache.resolve_index(path, 1, "b.zip"), 1)
        self.assertIsNone(cache.resolve_index(path, 0, "b.zip"))  # stale index
        self.assertIsNone(cache.resolve_index(path, 99, "b.zip"))
        self.assertEqual(cache.resolve_index(path, 0, "b.zip", allow_name_fallback=True), 1)
        self.assertIsNone(cache.resolve_index(path, 0, "nope.zip", allow_name_fallback=True))


@unittest.skipUnless(LT, "libtorrent not installed")
class TestWithRealParser(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def test_parse_returns_basenames_in_torrent_order(self):
        data = make_torrent_bytes(["Games/Alpha (USA).zip", "Games/Beta (USA).zip", "Gamma.zip"])
        summary = tc.parse_torrent_bytes(data)
        self.assertEqual(summary.num_files, 3)
        self.assertEqual(summary.names, ("Alpha (USA).zip", "Beta (USA).zip", "Gamma.zip"))

    def test_truncated_and_html_inputs_are_rejected(self):
        data = make_torrent_bytes(["a.zip"])
        for bad in (b"", b"<html></html>", data[: len(data) // 2], b"d4:infoe"):
            with self.assertRaises(tc.TorrentInvalid):
                tc.parse_torrent_bytes(bad)

    def test_cache_end_to_end_with_real_validation_and_unicode_names(self):
        data = make_torrent_bytes(["Pok\u00e9mon (USA).zip", "Other.zip"])  # NFC in the torrent
        cache = tc.TorrentCache(self.dir, fetch=lambda url: data, base_url="https://x.test/")
        path = cache.ensure("t/c.torrent")
        decomposed = "Poke\u0301mon (USA).zip"  # NFD form, as some filesystems/listings return it
        self.assertNotEqual(decomposed, "Pok\u00e9mon (USA).zip")
        self.assertEqual(cache.resolve_index(path, 0, decomposed), 0)
        with self.assertRaises(tc.TorrentInvalid):
            tc.TorrentCache(self.dir / "b", fetch=lambda url: data[:50], base_url="x/").ensure("t/d.torrent")

    def test_parse_of_large_collection_is_fast(self):
        names = [f"dir{i // 500}/File Number {i} (USA).zip" for i in range(20000)]
        data = make_torrent_bytes(names, file_size=16384)
        start = time.perf_counter()
        summary = tc.parse_torrent_bytes(data)
        self.assertEqual(summary.num_files, 20000)
        self.assertLess(time.perf_counter() - start, 3.0)


if __name__ == "__main__":
    unittest.main()
