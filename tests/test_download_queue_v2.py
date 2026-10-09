import threading
import time
import unittest

from minerva.core.download_queue import DownloadQueue


class _Engine:
    def __init__(self):
        self.added = []
        self.removed = []

    def add_download(self, source, so_id, name, save_path, download_id=None):
        self.added.append(download_id)

    def remove_handle(self, download_id):
        self.removed.append(download_id)


def _item(i, **kw):
    return {"id": f"id-{i}", "name": f"Game {i}.zip", "source": f"src-{i}", "so_id": i, "save_path": "/tmp", **kw}


class _Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class TestReservations(unittest.TestCase):
    def setUp(self):
        self.queue = DownloadQueue(_Engine(), max_active=2)

    def test_reserve_is_atomic_check_and_set(self):
        self.assertTrue(self.queue.reserve("A.zip"))
        self.assertFalse(self.queue.reserve("A.zip"))
        self.assertTrue(self.queue.has_name("A.zip"))  # visible to dedupe while the lookup runs
        self.queue.release("A.zip")
        self.assertFalse(self.queue.has_name("A.zip"))
        self.assertTrue(self.queue.reserve("A.zip"))

    def test_reserve_fails_for_queued_and_finished_names(self):
        self.queue.enqueue("1", "A.zip", "s", 0, "/tmp")
        self.assertFalse(self.queue.reserve("A.zip"))
        self.queue.start_all_pending()
        self.queue.on_finished("1")
        self.assertFalse(self.queue.reserve("A.zip"))  # done items still count, as before

    def test_enqueue_converts_reservation_into_a_real_entry(self):
        self.assertTrue(self.queue.reserve("A.zip"))
        self.queue.enqueue("1", "A.zip", "s", 0, "/tmp")
        self.queue.cancel("1")
        self.assertFalse(self.queue.has_name("A.zip"))  # no leaked reservation blocking a retry

    def test_concurrent_reserve_lets_exactly_one_thread_win(self):
        wins = []

        def worker():
            if self.queue.reserve("Same.zip"):
                wins.append(1)

        threads = [threading.Thread(target=worker) for _ in range(40)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(wins), 1)


class TestIndexesAndVersion(unittest.TestCase):
    def setUp(self):
        self.engine = _Engine()
        self.queue = DownloadQueue(self.engine, max_active=1)

    def test_name_index_tracks_every_transition(self):
        self.queue.enqueue_many([_item(1), _item(2)])
        self.assertTrue(self.queue.has_name("Game 1.zip"))
        self.queue.start_selected(["id-1"])  # pending -> active
        self.assertEqual(self.queue.find_by_name("Game 1.zip")["id"], "id-1")
        self.queue.on_finished("id-1")  # active -> done
        self.assertTrue(self.queue.has_name("Game 1.zip"))
        self.queue.pop_done("id-1")
        self.assertFalse(self.queue.has_name("Game 1.zip"))
        self.queue.cancel("id-2")
        self.assertFalse(self.queue.has_name("Game 2.zip"))
        self.queue.enqueue("id-3", "Game 3.zip", "s", 0, "/tmp")
        self.queue.start_all_pending()
        self.queue.on_finished("id-3")
        self.queue.clear_done()
        self.assertFalse(self.queue.has_name("Game 3.zip"))

    def test_find_by_name_prefers_live_entry_over_finished_one(self):
        self.queue.enqueue("old", "Dup.zip", "s", 0, "/tmp")
        self.queue.start_all_pending()
        self.queue.on_finished("old", error="CRC failed")
        self.queue.enqueue("new", "Dup.zip", "s2", 0, "/tmp")
        self.assertEqual(self.queue.find_by_name("Dup.zip")["id"], "new")

    def test_version_bumps_only_on_real_changes(self):
        v0 = self.queue.version
        self.queue.enqueue("a", "A.zip", "s", 0, "/tmp")
        v1 = self.queue.version
        self.assertGreater(v1, v0)
        self.queue.has_name("A.zip")
        self.queue.snapshot()
        self.assertEqual(self.queue.version, v1)
        self.queue.start_selected(["a"])
        self.assertGreater(self.queue.version, v1)
        v2 = self.queue.version
        self.queue.start_selected(["a"])  # already started: nothing changed
        self.assertEqual(self.queue.version, v2)

    def test_queued_keys_cached_per_version_and_uses_key_fn(self):
        calls = []

        def key_fn(name):
            calls.append(name)
            return {name.lower(), name.lower().replace(".zip", "")}

        queue = DownloadQueue(_Engine(), max_active=1, key_fn=key_fn)
        queue.enqueue_many([_item(1), _item(2)])
        keys = queue.queued_keys()
        self.assertEqual(keys, frozenset({"game 1.zip", "game 1", "game 2.zip", "game 2"}))
        n = len(calls)
        self.assertIs(queue.queued_keys(), keys)
        self.assertEqual(len(calls), n)  # served from cache
        queue.cancel("id-1")
        self.assertEqual(queue.queued_keys(), frozenset({"game 2.zip", "game 2"}))

    def test_finished_items_are_not_in_queued_keys(self):
        self.queue.enqueue("a", "A.zip", "s", 0, "/tmp")
        self.queue.start_all_pending()
        self.queue.on_finished("a")
        self.assertEqual(self.queue.queued_keys(), frozenset())

    def test_enqueue_does_not_clobber_started_items(self):
        self.queue.enqueue("a", "A.zip", "s", 0, "/tmp")
        self.queue.start_all_pending()
        self.queue.enqueue("a", "A.zip", "other-source", 5, "/tmp")
        snap = self.queue.snapshot()
        self.assertEqual(snap["active"], ["a"])
        self.assertEqual(snap["pending"], [])


class TestOrdering(unittest.TestCase):
    def setUp(self):
        self.engine = _Engine()
        self.queue = DownloadQueue(self.engine, max_active=1)
        self.queue.enqueue_many([_item(i) for i in range(1, 6)])

    def order(self):
        return [i["id"] for i in self.queue.snapshot()["pending"]]

    def test_move_top_bottom_keeps_relative_order_of_selection(self):
        self.queue.move(["id-4", "id-2"], "top")
        self.assertEqual(self.order(), ["id-2", "id-4", "id-1", "id-3", "id-5"])
        self.queue.move(["id-2", "id-1"], "bottom")
        self.assertEqual(self.order(), ["id-4", "id-3", "id-5", "id-2", "id-1"])

    def test_move_up_down_as_block(self):
        self.queue.move(["id-3", "id-4"], "up")
        self.assertEqual(self.order(), ["id-1", "id-3", "id-4", "id-2", "id-5"])
        self.queue.move(["id-3", "id-4"], "down")
        self.assertEqual(self.order(), ["id-1", "id-2", "id-3", "id-4", "id-5"])
        self.queue.move(["id-1"], "up")  # already first: no-op
        self.assertEqual(self.order()[0], "id-1")

    def test_move_rejects_unknown_target(self):
        with self.assertRaises(ValueError):
            self.queue.move(["id-1"], "sideways")

    def test_reorder_changes_which_item_starts_next(self):
        self.queue.start_all_pending()  # id-1 starts (max_active=1); 2..5 are flagged
        self.queue.move(["id-5"], "top")
        self.queue.on_finished("id-1")
        self.assertEqual(self.engine.added, ["id-1", "id-5"])

    def test_cancelled_pending_items_are_skipped_when_advancing(self):
        self.queue.start_all_pending()
        self.queue.cancel("id-2")
        self.queue.cancel("id-3")
        self.queue.on_finished("id-1")
        self.assertEqual(self.engine.added, ["id-1", "id-4"])

    def test_start_selected_follows_click_order(self):
        self.queue.start_selected(["id-4"])
        self.queue.start_selected(["id-2"])
        self.queue.on_finished("id-4")
        self.assertEqual(self.engine.added, ["id-4", "id-2"])


class TestRetry(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.engine = _Engine()
        self.queue = DownloadQueue(self.engine, max_active=1, clock=self.clock, retry_delays=(30, 120))
        self.queue.enqueue_many([_item(1, start_requested=True), _item(2, start_requested=True)])
        self.queue._try_advance()

    def test_retryable_failure_frees_the_slot_and_waits_out_the_backoff(self):
        delay = self.queue.on_failed("id-1", "HTTP 503", retryable=True)
        self.assertEqual(delay, 30)
        snap = self.queue.snapshot()
        self.assertEqual([i["id"] for i in snap["retry"]], ["id-1"])
        self.assertEqual(snap["active"], ["id-2"])  # next item took the slot
        self.assertTrue(self.queue.has_name("Game 1.zip"))
        self.assertIn("game 1.zip", self.queue.queued_keys())

    def test_tick_requeues_only_due_items_at_the_front(self):
        self.queue.on_failed("id-1", "boom")
        self.queue.enqueue("id-9", "Other.zip", "s", 0, "/tmp")
        self.clock.now += 29
        self.assertEqual(self.queue.tick(), [])
        self.clock.now += 2
        self.assertEqual(self.queue.tick(), ["id-1"])
        self.assertEqual([i["id"] for i in self.queue.snapshot()["pending"]][0], "id-1")
        self.queue.on_finished("id-2")  # frees the slot -> retried item is started first
        self.assertEqual(self.engine.added[-1], "id-1")

    def test_gives_up_after_the_last_delay_and_reports_the_error(self):
        self.assertEqual(self.queue.on_failed("id-1", "e1"), 30)
        self.clock.now += 31
        self.queue.tick()
        self.queue.on_finished("id-2")  # id-1 runs again
        self.assertEqual(self.queue.on_failed("id-1", "e2"), 120)
        self.clock.now += 121
        self.queue.tick()
        self.assertIsNone(self.queue.on_failed("id-1", "e3"))
        done = self.queue.snapshot()["done"]
        self.assertEqual([(d["id"], d["status"], d["error"]) for d in done if d["id"] == "id-1"],
                         [("id-1", "error", "e3")])

    def test_non_retryable_failure_goes_straight_to_done(self):
        self.assertIsNone(self.queue.on_failed("id-1", "invalid torrent", retryable=False))
        self.assertEqual(self.queue.snapshot()["retry"], [])
        self.assertEqual(self.queue.snapshot()["done"][0]["status"], "error")

    def test_retry_items_survive_persistence_as_started(self):
        self.queue.on_failed("id-1", "boom")
        persisted = {i["id"]: i for i in self.queue.export_for_persistence()}
        self.assertTrue(persisted["id-1"]["start_requested"])

    def test_cancel_removes_retry_item(self):
        self.queue.on_failed("id-1", "boom")
        self.queue.cancel("id-1")
        self.assertEqual(self.queue.snapshot()["retry"], [])
        self.assertFalse(self.queue.has_name("Game 1.zip"))


class TestDoneCap(unittest.TestCase):
    def test_oldest_successes_are_trimmed_but_errors_are_kept(self):
        queue = DownloadQueue(_Engine(), max_active=100, max_done=3)
        queue.enqueue_many([_item(i, start_requested=True) for i in range(6)])
        queue._try_advance()
        queue.on_finished("id-0", error="CRC failed")
        for i in range(1, 6):
            queue.on_finished(f"id-{i}")
        ids = [d["id"] for d in queue.snapshot()["done"]]
        self.assertEqual(len(ids), 3)
        self.assertIn("id-0", ids)  # the failure the user may want to retry survives
        self.assertEqual(ids[-2:], ["id-4", "id-5"])
        self.assertFalse(queue.has_name("Game 1.zip"))  # trimmed names leave the index


class TestCancelAndStartFailures(unittest.TestCase):
    def test_cancel_many_does_not_start_items_that_are_about_to_be_cancelled(self):
        engine = _Engine()
        queue = DownloadQueue(engine, max_active=2)
        queue.enqueue_many([_item(i, start_requested=True) for i in range(6)])
        queue._try_advance()
        self.assertEqual(engine.added, ["id-0", "id-1"])
        queue.cancel_many([f"id-{i}" for i in range(6)])  # "select all, Delete"
        self.assertEqual(engine.added, ["id-0", "id-1"])  # nothing new started
        self.assertEqual(sorted(engine.removed), ["id-0", "id-1"])
        snap = queue.snapshot()
        self.assertEqual((snap["pending"], snap["active"]), ([], []))

    def test_a_failing_engine_start_does_not_strand_the_item_or_the_rest(self):
        class Flaky(_Engine):
            def add_download(self, source, so_id, name, save_path, download_id=None):
                if download_id == "id-0":
                    raise RuntimeError("engine is shutting down")
                super().add_download(source, so_id, name, save_path, download_id)

        engine = Flaky()
        queue = DownloadQueue(engine, max_active=3)
        queue.enqueue_many([_item(i, start_requested=True) for i in range(3)])
        queue._try_advance()
        snap = queue.snapshot()
        self.assertEqual(snap["active"], ["id-1", "id-2"])
        self.assertEqual(engine.added, ["id-1", "id-2"])
        failed = [d for d in snap["done"] if d["id"] == "id-0"]
        self.assertEqual(failed[0]["status"], "error")
        self.assertIn("Could not start", failed[0]["error"])


class TestScale(unittest.TestCase):
    N = 10_000

    def test_enqueue_start_and_finish_ten_thousand_items(self):
        engine = _Engine()
        queue = DownloadQueue(engine, max_active=10)
        start = time.perf_counter()
        queue.enqueue_many([_item(i) for i in range(self.N)])
        enqueue_time = time.perf_counter() - start
        self.assertLess(enqueue_time, 3.0)

        start = time.perf_counter()
        queue.start_all_pending()
        for i in range(self.N):
            queue.on_finished(f"id-{i}")
        drain_time = time.perf_counter() - start
        self.assertLess(drain_time, 5.0, f"draining took {drain_time:.2f}s")
        self.assertEqual(len(engine.added), self.N)
        self.assertEqual(engine.added[:10], [f"id-{i}" for i in range(10)])  # order preserved
        self.assertEqual(queue.snapshot()["active"], [])

    def test_has_name_stays_constant_time(self):
        queue = DownloadQueue(_Engine(), max_active=1)
        queue.enqueue_many([_item(i) for i in range(self.N)])
        start = time.perf_counter()
        for i in range(100_000):
            queue.has_name(f"Game {i % (2 * self.N)}.zip")
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.5, f"100k has_name took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
