import time
import tkinter as tk
import unittest

from minerva.core.download_queue import DownloadQueue
from minerva.ui.theme import setup_modern_styles
from minerva.ui import downloads_panel as dp


class _Engine:
    def add_download(self, *a, **k):
        pass

    def remove_handle(self, did):
        pass


def make_queue(pending=0, active=0, done=0, errors=0, retry=0, max_active=10):
    q = DownloadQueue(_Engine(), max_active=max_active, retry_delays=(30,))
    n = pending + active + done + errors + retry
    items = []
    for i in range(n):
        items.append({"id": f"id{i}", "name": f"Game {i}.zip", "source": "s", "so_id": i, "save_path": "/tmp",
                      "start_requested": False})
    q.enqueue_many(items)
    idx = 0
    # finished + errors + retry first (they must pass through active), then the live ones
    for _ in range(done + errors + retry):
        did = f"id{idx}"
        q.start_selected([did])
        if idx < done:
            q.on_finished(did)
        elif idx < done + errors:
            q.on_finished(did, error="CRC mismatch\nmore")
        else:
            q.on_failed(did, "stalled", retryable=True)
        idx += 1
    q.start_selected([f"id{idx + k}" for k in range(active)])
    return q


def statuses_for(snapshot, progress=0.25):
    return {it["id"]: {"name": it["name"], "progress": progress, "download_rate": 1_000_000, "eta": 90,
                       "num_peers": 4, "total": 1 << 30, "state": "Downloading", "paused": False, "error": ""}
            for it in snapshot["active_items"]}


class PanelCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"no display: {exc}")
        cls.root.withdraw()
        setup_modern_styles(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.calls = []
        a = dp.PanelActions(
            start_now=lambda ids: self.calls.append(("start_now", list(ids))),
            toggle_pause=lambda ids: self.calls.append(("toggle_pause", list(ids))),
            cancel=lambda ids: self.calls.append(("cancel", list(ids))),
            remove=lambda ids: self.calls.append(("remove", list(ids))),
            retry=lambda ids: self.calls.append(("retry", list(ids))),
            move=lambda ids, where: self.calls.append(("move", list(ids), where)),
            open_folder=lambda ids, extracted: self.calls.append(("open_folder", list(ids), extracted)),
        )
        self.panel = dp.DownloadsPanel(self.root, a)
        self.panel.pack(fill="both", expand=True)
        self.addCleanup(self.panel.destroy)

    def children(self):
        return list(self.panel.tree.get_children(""))

    def sync(self, q, **kw):
        snap = q.snapshot()
        self.panel.sync(snap, kw.get("statuses", statuses_for(snap)), kw.get("extract", {}))
        return snap


class TestStructure(PanelCase):
    def test_rows_appear_in_state_order_and_chips_show_counts(self):
        q = make_queue(pending=3, active=2, done=2, errors=1, retry=1)
        self.sync(q)
        kinds = [self.panel.kind_of(i) for i in self.children()]
        self.assertEqual(kinds, ["active"] * 2 + ["pending"] * 3 + ["retry"] + ["error"] + ["done"] * 2)
        self.assertEqual(self.panel._chips["all"].cget("text"), "All (9)")
        self.assertEqual(self.panel._chips["active"].cget("text"), "Active (2)")
        self.assertEqual(self.panel._chips["queued"].cget("text"), "Queued (4)")
        self.assertEqual(self.panel._chips["errors"].cget("text"), "Errors (1)")

    def test_row_moves_between_sections_without_being_recreated(self):
        q = make_queue(pending=3, active=1, max_active=2)  # id0 active; id1..id3 pending
        self.sync(q)
        before = self.children()
        self.assertEqual(self.panel.kind_of("id0"), "active")
        q.start_selected(["id2"])  # a pending row becomes active
        self.sync(q)
        self.assertEqual(self.panel.kind_of("id2"), "active")
        self.assertEqual(self.children()[:2], ["id0", "id2"])
        self.assertEqual(set(self.children()), set(before))

    def test_finished_rows_leave_active_and_new_rows_appear(self):
        q = make_queue(pending=2, active=2)
        self.sync(q)
        q.on_finished("id2")
        q.enqueue("late", "Late.zip", "s", 0, "/tmp")
        self.sync(q)
        self.assertEqual(self.panel.kind_of("id2"), "done")
        self.assertEqual(self.panel.kind_of("late"), "pending")

    def test_reordering_the_queue_reorders_the_rows(self):
        q = make_queue(pending=4)
        self.sync(q)
        q.move(["id3"], "top")
        self.sync(q)
        self.assertEqual(self.children(), ["id3", "id0", "id1", "id2"])

    def test_removed_rows_disappear(self):
        q = make_queue(pending=3, done=2)  # id0,id1 done; id2..id4 pending
        self.sync(q)
        self.assertEqual(len(self.children()), 5)
        q.clear_done()
        q.cancel("id4")
        self.sync(q)
        self.assertEqual(set(self.children()), {"id2", "id3"})

    def test_selection_survives_updates(self):
        q = make_queue(pending=5, active=2)
        self.sync(q)
        self.panel.tree.selection_set(["id2", "id3"])
        q.cancel("id6")
        self.sync(q)
        self.assertEqual(sorted(self.panel.tree.selection()), ["id2", "id3"])


class TestDynamicUpdates(PanelCase):
    def test_active_rows_update_without_structural_rebuild(self):
        q = make_queue(pending=200, active=3)
        snap = q.snapshot()
        self.panel.sync(snap, statuses_for(snap, 0.10), {})
        calls = []
        original = self.panel._sync_structure
        self.panel._sync_structure = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        self.panel.sync(snap, statuses_for(snap, 0.60), {})
        self.assertEqual(calls, [])  # same queue version: dynamic path only
        self.assertIn("60%", self.panel.tree.set("id0", "progress"))  # id0 is an active row

    def test_extraction_progress_shows_on_finished_rows(self):
        q = make_queue(done=2)
        snap = q.snapshot()
        self.panel.sync(snap, {}, {})
        self.panel.sync(snap, {}, {"id0": {"pct": 40, "status": "Extracting…"}})
        self.assertEqual(self.panel.tree.set("id0", "state"), "Extracting…")
        self.assertIn("40%", self.panel.tree.set("id0", "progress"))
        self.assertEqual(self.panel.tree.set("id1", "state"), "Done")

    def test_retry_countdown_ticks(self):
        q = make_queue(retry=1)
        snap = q.snapshot()
        item = snap["retry"][0]
        self.panel.sync(snap, {}, {}, now=item["retry_at"] - 30)
        first = self.panel.tree.set("id0", "state")
        self.panel.sync(snap, {}, {}, now=item["retry_at"] - 10)
        second = self.panel.tree.set("id0", "state")
        self.assertIn("Retry in 30s", first)
        self.assertIn("Retry in 10s", second)


class TestFilters(PanelCase):
    def test_filter_chip_limits_rows_and_keeps_counts(self):
        q = make_queue(pending=3, active=1, done=2, errors=2)
        self.sync(q)
        self.panel.set_filter("errors")
        self.sync(q)
        self.assertEqual({self.panel.kind_of(i) for i in self.children()}, {"error"})
        self.assertEqual(len(self.children()), 2)
        self.assertEqual(self.panel._chips["all"].cget("text"), "All (8)")
        self.panel.set_filter("all")
        self.sync(q)
        self.assertEqual(len(self.children()), 8)


class TestInteractions(PanelCase):
    def setUp(self):
        super().setUp()
        # ids: id0 done, id1 error, id2+id3 active, id4..id6 pending
        self.q = make_queue(pending=3, active=2, done=1, errors=1)
        self.sync(self.q)

    def test_delete_cancels_live_rows_and_removes_finished_ones(self):
        self.panel.tree.selection_set(["id0", "id1", "id2", "id4"])
        self.panel.key_delete()
        by_action = {name: sorted(ids) for name, ids in self.calls}
        self.assertEqual(by_action, {"cancel": ["id2", "id4"], "remove": ["id0", "id1"]})

    def test_space_toggles_pause_only_for_active_rows(self):
        self.panel.tree.selection_set(["id2", "id4"])
        self.panel.key_space()
        self.assertEqual(self.calls, [("toggle_pause", ["id2"])])

    def test_alt_arrows_move_only_pending_rows(self):
        self.panel.tree.selection_set(["id5", "id2"])
        self.panel.key_move("up")
        self.assertEqual(self.calls, [("move", ["id5"], "up")])

    def test_r_retries_error_rows_only(self):
        self.panel.tree.selection_set(["id1", "id0", "id4"])
        self.panel.key_retry()
        self.assertEqual(self.calls, [("retry", ["id1"])])

    def test_ctrl_a_selects_everything(self):
        self.panel.select_all()
        self.assertEqual(len(self.panel.tree.selection()), 7)

    def test_enter_opens_the_folder_of_the_first_selected_row(self):
        self.panel.tree.selection_set(["id0", "id1"])
        self.panel.key_open()
        self.assertEqual(self.calls, [("open_folder", ["id1"], False)])  # first in display order (newest finished)

    def test_nothing_is_called_for_an_empty_selection(self):
        self.panel.tree.selection_set([])
        for fn in (self.panel.key_delete, self.panel.key_space, self.panel.key_retry, self.panel.key_open):
            fn()
        self.panel.key_move("up")
        self.assertEqual(self.calls, [])

    def test_real_key_events_reach_the_handlers_when_the_window_has_focus(self):
        self.root.deiconify()
        self.addCleanup(self.root.withdraw)
        self.panel.tree.selection_set(["id2"])
        self.panel.tree.focus_force()
        self.root.update()
        if self.root.focus_get() is not self.panel.tree:
            self.skipTest("window manager did not grant focus")
        self.panel.tree.event_generate("<space>")
        self.root.update()
        self.assertEqual(self.calls, [("toggle_pause", ["id2"])])


class TestScale(PanelCase):
    def test_five_thousand_rows_load_and_tick_within_budget(self):
        q = make_queue(pending=5000, active=10, done=500)
        snap = q.snapshot()
        statuses = statuses_for(snap, 0.1)

        start = time.perf_counter()
        self.panel.sync(snap, statuses, {})
        self.panel.update()
        load = time.perf_counter() - start
        self.assertEqual(len(self.children()), 5510)
        self.assertLess(load, 5.0, f"initial load of 5.5k rows took {load:.2f}s")

        start = time.perf_counter()
        for i in range(10):
            self.panel.sync(snap, statuses_for(snap, 0.1 + i / 100), {})
        tick = (time.perf_counter() - start) / 10
        self.assertLess(tick, 0.4, f"steady tick with 10 active rows took {tick * 1000:.0f} ms")


if __name__ == "__main__":
    unittest.main()
