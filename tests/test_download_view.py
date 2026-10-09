import time
import unittest

from minerva.core import download_view as dv
from minerva.core.download_queue import DownloadQueue


class _Engine:
    def add_download(self, *a, **k):
        pass

    def remove_handle(self, did):
        pass


class TestFormatting(unittest.TestCase):
    def test_size(self):
        self.assertEqual(dv.format_size(0), dv.DASH)
        self.assertEqual(dv.format_size(None), dv.DASH)
        self.assertEqual(dv.format_size("x"), dv.DASH)
        self.assertEqual(dv.format_size(512), "512 B")
        self.assertEqual(dv.format_size(1536), "1.5 KB")
        self.assertEqual(dv.format_size(5 * 1024 ** 2), "5.0 MB")
        self.assertEqual(dv.format_size(int(2.5 * 1024 ** 3)), "2.5 GB")
        self.assertEqual(dv.format_size(3 * 1024 ** 4), "3.0 TB")

    def test_rate(self):
        self.assertEqual(dv.format_rate(0), dv.DASH)
        self.assertEqual(dv.format_rate(-5), dv.DASH)
        self.assertEqual(dv.format_rate(900), "900 B/s")
        self.assertEqual(dv.format_rate(2048), "2.0 KB/s")
        self.assertEqual(dv.format_rate(3 * 1024 ** 2), "3.0 MB/s")

    def test_eta(self):
        self.assertEqual(dv.format_eta(-1), dv.DASH)
        self.assertEqual(dv.format_eta(None), dv.DASH)
        self.assertEqual(dv.format_eta(45), "45s")
        self.assertEqual(dv.format_eta(200), "3m 20s")
        self.assertEqual(dv.format_eta(7500), "2h 05m")
        self.assertEqual(dv.format_eta(90000), "1d 1h")

    def test_progress_text_clamps(self):
        self.assertEqual(dv.progress_text(0), "░░░░░░░░░░ 0%")
        self.assertEqual(dv.progress_text(0.4), "████░░░░░░ 40%")
        self.assertEqual(dv.progress_text(1), "██████████ 100%")
        self.assertEqual(dv.progress_text(7), "██████████ 100%")
        self.assertEqual(dv.progress_text(-3), "░░░░░░░░░░ 0%")
        self.assertEqual(dv.progress_text("nope"), "░░░░░░░░░░ 0%")


class TestRows(unittest.TestCase):
    def snapshot(self):
        q = DownloadQueue(_Engine(), max_active=1, retry_delays=(30,))
        q.enqueue_many([
            {"id": "a", "name": "Retry.zip", "source": "s", "so_id": 0, "save_path": "/tmp", "start_requested": True},
            {"id": "p", "name": "Pending.zip", "source": "s", "so_id": 1, "save_path": "/tmp"},
            {"id": "r", "name": "Done1.zip", "source": "s", "so_id": 2, "save_path": "/tmp", "start_requested": True},
            {"id": "d", "name": "Done2.zip", "source": "s", "so_id": 3, "save_path": "/tmp", "start_requested": True},
            {"id": "e", "name": "Err.zip", "source": "s", "so_id": 4, "save_path": "/tmp", "start_requested": True},
            {"id": "x", "name": "Active.zip", "source": "s", "so_id": 5, "save_path": "/tmp", "start_requested": True},
        ])
        q._try_advance()                                   # a runs
        q.on_failed("a", "HTTP 503 stalled\nmore", retryable=True)  # a waits; r runs
        q.on_finished("r")
        q.on_finished("d")
        q.on_finished("e", error="CRC mismatch\ndetails")   # x runs and stays active
        return q.snapshot()

    def test_rows_are_ordered_by_state_with_newest_done_first(self):
        rows = dv.build_rows(self.snapshot(), {}, {}, now=0.0)
        self.assertEqual([(r.iid, r.kind) for r in rows], [
            ("x", dv.KIND_ACTIVE),
            ("p", dv.KIND_PENDING),
            ("a", dv.KIND_RETRY),
            ("e", dv.KIND_ERROR),   # finished most recently -> first
            ("d", dv.KIND_DONE),
            ("r", dv.KIND_DONE),
        ])
        self.assertTrue(all(len(r.values) == len(dv.COLUMNS) for r in rows))

    def test_active_row_shows_live_status(self):
        snap = {"active_items": [{"id": "a", "name": "Game.zip"}], "pending": [], "retry": [], "done": []}
        status = {"a": {"name": "Game.zip", "progress": 0.5, "download_rate": 2 * 1024 ** 2, "eta": 125,
                        "num_peers": 7, "total": 4 * 1024 ** 3, "state": "Downloading", "paused": False, "error": ""}}
        (row,) = dv.build_rows(snap, status, {})
        self.assertEqual(row.values, ("📄 Game.zip", "4.0 GB", "█████░░░░░ 50%", "2.0 MB/s", "2m 05s", "7", "Downloading"))

    def test_paused_and_error_states_are_tagged(self):
        snap = {"active_items": [{"id": "a", "name": "G.zip"}], "pending": [], "retry": [], "done": []}
        paused = {"a": {"state": "Paused", "paused": True, "progress": 0.1}}
        self.assertIn("paused", dv.build_rows(snap, paused, {})[0].tags)
        broken = {"a": {"state": "Disk full", "paused": True, "error": "Disk full in /data", "progress": 0.1}}
        row = dv.build_rows(snap, broken, {})[0]
        self.assertIn("problem", row.tags)
        self.assertIn("Disk full in /data", row.values[-1])

    def test_active_row_without_status_yet_is_safe(self):
        snap = {"active_items": [{"id": "a", "name": "G.zip"}], "pending": [], "retry": [], "done": []}
        (row,) = dv.build_rows(snap, {}, {})
        self.assertEqual(row.values[0], "📄 G.zip")
        self.assertEqual(row.values[-1], "Starting")

    def test_retry_row_counts_down_and_shows_the_reason(self):
        item = {"id": "r", "name": "R.zip", "retry_at": 130.0, "attempts": 1, "last_error": "Stalled: no data\nlog"}
        row = dv.build_row(dv.KIND_RETRY, item, now=100.0)
        self.assertIn("Retry in 30s", row.values[-1])
        self.assertIn("attempt 2", row.values[-1])
        self.assertIn("Stalled: no data", row.values[-1])
        self.assertNotIn("log", row.values[-1])
        self.assertIn("Retry in 0s", dv.build_row(dv.KIND_RETRY, item, now=500.0).values[-1])

    def test_done_rows_show_extraction_progress_and_errors_show_the_first_line(self):
        done = {"id": "d", "name": "D.zip", "status": "done", "error": ""}
        plain = dv.build_row(dv.KIND_DONE, done)
        self.assertEqual((plain.kind, plain.values[-1]), (dv.KIND_DONE, "Done"))
        extracting = dv.build_row(dv.KIND_DONE, done, extract={"pct": 40, "status": "Extracting…"})
        self.assertEqual((extracting.values[2], extracting.values[-1]), ("████░░░░░░ 40%", "Extracting…"))
        failed = dv.build_row(dv.KIND_ERROR, {"id": "e", "name": "E.zip", "status": "error", "error": "CRC mismatch\nlong"})
        self.assertEqual((failed.kind, failed.values[-1]), (dv.KIND_ERROR, "CRC mismatch"))

    def test_filters_and_counts(self):
        rows = dv.build_rows(self.snapshot(), {}, {})
        counts = dv.count_by_filter(rows)
        self.assertEqual(counts["all"], len(rows))
        self.assertEqual(counts["errors"], 1)
        self.assertEqual(counts["queued"], 2)
        self.assertEqual((counts["active"], counts["done"]), (1, 2))
        self.assertEqual({r.kind for r in dv.filter_rows(rows, "errors")}, {dv.KIND_ERROR})
        self.assertEqual({r.kind for r in dv.filter_rows(rows, "queued")}, {dv.KIND_PENDING, dv.KIND_RETRY})
        self.assertIs(dv.filter_rows(rows, "all"), rows)
        self.assertEqual(dv.filter_rows(rows, "nonsense"), rows)

    def test_building_ten_thousand_rows_is_fast(self):
        snap = {"active_items": [], "retry": [],
                "pending": [{"id": f"p{i}", "name": f"Game {i}.zip", "start_requested": i % 2 == 0} for i in range(10_000)],
                "done": [{"id": f"d{i}", "name": f"Done {i}.zip", "status": "done"} for i in range(5_000)]}
        start = time.perf_counter()
        rows = dv.build_rows(snap, {}, {})
        elapsed = time.perf_counter() - start
        self.assertEqual(len(rows), 15_000)
        self.assertLess(elapsed, 2.0, f"building 15k rows took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
