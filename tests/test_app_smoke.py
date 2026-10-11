"""Headless smoke tests for the real MinervaApp window.

Network, tray and update checks are patched out and all runtime files go to a temp
directory.  The scenario runs inside ``mainloop()`` because the app's worker threads
marshal results back with ``after()``, which Tk only allows while the loop is running.
"""
import pathlib
import queue
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

from tests.app_harness import isolated_app_patches

try:
    import minerva.ui.app as app_module
    from minerva.core.extractors import library_keys_for_name
    from minerva.core.lookup import LookupFailure, ResolvedDownload
    from minerva.core.torrent_engine import DownloadQueue
except Exception as exc:  # pragma: no cover - environment without tkinter
    app_module = None
    _IMPORT_ERROR = exc

ROWS = 5000


def _fake_entries(_path):
    return [
        {"name": f"Game {i} (USA).zip", "href": f"/rom?id={i}", "size": "1.2 GB", "is_folder": False}
        for i in range(ROWS)
    ]


class _FakeEngine:
    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self._meta: dict = {}
        self.stopped: list[str] = []

    def get_all_statuses(self):
        return {}

    def get_meta(self, download_id):
        return self._meta.get(download_id)

    def add_download(self, *args, **kwargs):
        pass

    def stop_seeding(self, download_id):
        self.stopped.append(download_id)

    def remove_handle(self, download_id):
        pass


@unittest.skipIf(app_module is None, "tkinter UI not importable")
class TestAppSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls._tmp.name)
        cls._patches = isolated_app_patches(app_module, base, _fake_entries)
        for p in cls._patches:
            p.start()
        try:
            cls.app = app_module.MinervaApp()
        except tk.TclError as exc:
            cls._cleanup()
            raise unittest.SkipTest(f"no display available: {exc}")

    @classmethod
    def _cleanup(cls):
        for p in reversed(cls._patches):
            p.stop()
        cls._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app._quitting = True
            cls.app.destroy()
        except Exception:
            pass
        cls._cleanup()

    def _pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            time.sleep(0.005)

    def _wait_for_lookups(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.update()
            with self.app._lookup_lock:
                pending = self.app._lookup_pending
            if pending == 0 and self.app._lookup_pump_after_id is None and self.app._lookup_results.empty():
                return
            time.sleep(0.01)
        self.fail("lookups did not finish")

    def _run_in_mainloop(self, fn):
        """Run ``fn`` once the loop is up, re-raise whatever it raises, then stop the loop."""
        failure = []

        def runner():
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 - reported to unittest below
                failure.append(exc)
            finally:
                self.app.quit()

        self.app.after(300, runner)
        self.app.mainloop()
        if failure:
            raise failure[0]

    def test_large_list_with_large_queue_stays_responsive(self):
        def scenario():
            app = self.app
            self._pump(1.0)
            self.assertEqual(len(app._right_tree.get_children()), ROWS)
            self.assertEqual(len(app._row_keys), ROWS)

            engine = _FakeEngine()
            app._torrent_engine = engine
            app._download_queue = DownloadQueue(engine, max_active=3)
            app._extract_download = lambda did: None

            started = time.perf_counter()
            for i in range(500):
                app._download_queue.enqueue(f"id{i}", f"Game {i * 3} (USA).zip", "src", 0, "/tmp")
                app._request_icon_refresh()  # 500 requests must coalesce
            self._pump(0.4)
            queued = [iid for iid in app._right_tree.get_children()
                      if app._right_tree.set(iid, "dlstat") == "⬇"]
            self.assertEqual(len(queued), 500)
            self.assertLess(time.perf_counter() - started, 8.0)

            # Hidden drawer: ticks must not rebuild row widgets.
            app._downloads_visible = False
            with mock.patch.object(app, "_rebuild_dl_panel") as rebuild:
                for _ in range(20):
                    app._poll_downloads_once()
            rebuild.assert_not_called()

            # Visible drawer: a steady tick over 500 queued rows is cheap.
            app._downloads_visible = True
            app._rebuild_dl_panel(app._download_queue.snapshot())
            start = time.perf_counter()
            for _ in range(10):
                app._poll_downloads_once()
            per_tick = (time.perf_counter() - start) / 10
            self.assertLess(per_tick, 0.6, f"steady poll tick took {per_tick * 1000:.0f} ms")

        self._run_in_mainloop(scenario)

    def test_finished_event_updates_icon_without_blocking_or_rescan(self):
        def scenario():
            app = self.app
            self._pump(0.8)
            engine = _FakeEngine()
            app._torrent_engine = engine
            app._download_queue = DownloadQueue(engine, max_active=3)
            app._download_queue.enqueue("done1", "Game 7 (USA).zip", "src", 0, "/tmp")
            app._download_queue.start_all_pending()
            engine._meta["done1"] = {"name": "Game 7 (USA).zip", "save_path": "/tmp", "so_id": 0}
            engine.events.put({"type": "finished", "id": "done1"})
            started_post_actions = []
            app._prompt_post_download_actions_batch = lambda ids: started_post_actions.extend(ids)

            with mock.patch.object(app, "_normalize_downloaded_file_location") as normalize, \
                    mock.patch.object(app._library_index, "rescan") as rescan:
                start = time.perf_counter()
                app._poll_downloads_once()
                elapsed = time.perf_counter() - start
                self._pump(0.2)

            normalize.assert_not_called()  # moved to the extract worker thread
            rescan.assert_not_called()     # incremental add instead of an rglob rescan
            self.assertLess(elapsed, 1.5)
            self.assertEqual(engine.stopped, ["done1"])
            self.assertEqual(started_post_actions, ["done1"])
            self.assertEqual(app._right_tree.set("/rom?id=7", "dlstat"), "✓")

        self._run_in_mainloop(scenario)

    def test_bulk_queue_is_bounded_batched_deduplicated_and_reports_one_summary(self):
        def scenario():
            app = self.app
            self._pump(0.8)
            engine = _FakeEngine()
            app._torrent_engine = engine
            app._download_queue = DownloadQueue(engine, max_active=3, key_fn=library_keys_for_name)
            gauge = {"now": 0, "peak": 0}
            gauge_lock = threading.Lock()

            class Resolver:
                def resolve(self_inner, rom_id, name):
                    with gauge_lock:
                        gauge["now"] += 1
                        gauge["peak"] = max(gauge["peak"], gauge["now"])
                    time.sleep(0.005)
                    with gauge_lock:
                        gauge["now"] -= 1
                    if int(rom_id) % 10 == 0:
                        raise LookupFailure("lookup", "HTTP 503")
                    return ResolvedDownload(f"src-{rom_id}", int(rom_id), name)

            app._rom_resolver = Resolver()
            saves, dialogs = [], []
            with mock.patch.object(app, "_save_settings", side_effect=lambda: saves.append(1)), \
                    mock.patch.object(app_module.messagebox, "showwarning",
                                      side_effect=lambda title, body: dialogs.append((title, body))), \
                    mock.patch.object(app_module.messagebox, "showerror",
                                      side_effect=lambda title, body: dialogs.append((title, body))):
                for i in range(300):
                    app._submit_lookup(f"id{i}", str(i), f"Game {i}.zip", "/tmp", "/browse/")
                for i in range(1, 10):  # the same titles selected again while lookups are in flight
                    app._submit_lookup(f"dup{i}", str(i), f"Game {i}.zip", "/tmp", "/browse/")
                deadline = time.time() + 30
                while time.time() < deadline:
                    app.update()
                    with app._lookup_lock:
                        pending = app._lookup_pending
                    if pending == 0 and app._lookup_pump_after_id is None and app._lookup_results.empty():
                        break
                    time.sleep(0.01)

            snap = app._download_queue.snapshot()
            self.assertLessEqual(gauge["peak"], 5)  # was: one thread per file
            self.assertEqual(len(snap["pending"]), 270)  # 300 minus the 30 that failed
            self.assertEqual(len({i["name"] for i in snap["pending"]}), 270)  # no duplicate titles
            self.assertLess(len(saves), 30, f"settings saved {len(saves)} times for one batch")
            self.assertEqual(len(dialogs), 1)  # one summary, not 30 modal dialogs
            self.assertEqual(dialogs[0][0], "Some downloads could not be queued")
            self.assertIn("30 files could not be queued", dialogs[0][1])

        self._run_in_mainloop(scenario)

    def test_failed_lookups_stay_in_the_list_as_errors_and_retry_queues_them(self):
        def scenario():
            app = self.app
            self._pump(0.8)
            engine = _FakeEngine()
            app._torrent_engine = engine
            app._download_queue = DownloadQueue(engine, max_active=3, key_fn=library_keys_for_name)
            archive_down = [True]

            class Resolver:
                def resolve(self_inner, rom_id, name):
                    if archive_down[0] and int(rom_id) % 2 == 0:
                        raise LookupFailure("lookup", "ROM page: timed out (file database: unreachable)")
                    return ResolvedDownload(f"src-{rom_id}", int(rom_id), name)

            app._rom_resolver = Resolver()
            dialogs = []
            with mock.patch.object(app, "_save_settings"), \
                    mock.patch.object(app_module.messagebox, "showwarning",
                                      side_effect=lambda title, body: dialogs.append((title, body))), \
                    mock.patch.object(app_module.messagebox, "showerror",
                                      side_effect=lambda title, body: dialogs.append((title, body))):
                for i in range(1, 7):
                    app._submit_lookup(f"id{i}", str(i), f"Game {i}.zip", "/tmp", "/browse/Redump/")
                self._wait_for_lookups()

                snap = app._download_queue.snapshot()
                self.assertEqual(sorted(d["name"] for d in snap["pending"]), ["Game 1.zip", "Game 3.zip", "Game 5.zip"])
                failed = {d["name"]: d for d in snap["done"]}
                self.assertEqual(sorted(failed), ["Game 2.zip", "Game 4.zip", "Game 6.zip"])  # nothing silently lost
                row = failed["Game 2.zip"]
                self.assertEqual(row["status"], "error")
                self.assertTrue(row["error"].startswith("Lookup Failed:"))
                self.assertEqual(row["lookup"]["rom_id"], "2")
                self.assertEqual(row["lookup"]["browse_path"], "/browse/Redump/")
                self.assertEqual(len(dialogs), 1)
                self.assertIn("under Errors", dialogs[0][1])

                # Picking the same titles again is not ignored just because a failed row exists...
                app._submit_lookup("again2", "2", "Game 2.zip", "/tmp", "/browse/Redump/")
                self._wait_for_lookups()
                self.assertEqual([d["name"] for d in app._download_queue.snapshot()["done"]].count("Game 2.zip"), 1)

                # ...and Retry on the Errors rows runs the lookups again; successes replace the rows.
                archive_down[0] = False
                app._panel_retry([d["id"] for d in app._download_queue.snapshot()["done"]])
                self._wait_for_lookups()

            snap = app._download_queue.snapshot()
            self.assertEqual(len(snap["pending"]), 6)
            self.assertEqual(snap["done"], [])
            self.assertEqual(len(dialogs), 2)  # the second attempt at Game 2 failed too, and said so

        self._run_in_mainloop(scenario)

    def test_redownload_on_a_failed_lookup_retries_it_without_asking_to_delete_anything(self):
        def scenario():
            app = self.app
            self._pump(0.8)
            engine = _FakeEngine()
            app._torrent_engine = engine
            app._download_queue = DownloadQueue(engine, max_active=3, key_fn=library_keys_for_name)
            app._download_queue.add_failed("f1", "Game 8.zip", "Lookup Failed: x", save_path="/tmp",
                                           rom_id="8", browse_path="/browse/Redump/")

            class Resolver:
                def resolve(self_inner, rom_id, name):
                    return ResolvedDownload(f"src-{rom_id}", int(rom_id), name)

            app._rom_resolver = Resolver()
            with mock.patch.object(app, "_save_settings"), \
                    mock.patch.object(app_module.messagebox, "askyesno",
                                      side_effect=AssertionError("must not ask to delete files")):
                app._panel_redownload(["f1"])
                self._wait_for_lookups()
            snap = app._download_queue.snapshot()
            self.assertEqual([d["name"] for d in snap["pending"]], ["Game 8.zip"])
            self.assertEqual(snap["done"], [])

        self._run_in_mainloop(scenario)

    def _companion_item(self):
        from minerva.core.companions import KIND_UPDATE, Companion
        return Companion("Game (USA) (v1.04) (Update).zip", "/rom?id=5", "1 MB", KIND_UPDATE, "Updates")

    def test_a_failing_companion_dialog_is_logged_retried_once_and_does_not_strand_later_prompts(self):
        def scenario():
            app = self.app
            self._pump(0.3)
            app.COMPANION_PROMPT_GAP_MS = 20  # shadows the class constant for this app only
            item = self._companion_item()
            calls, enqueued = [], []

            def fake_prompt(parent, name, items, **kw):
                calls.append(name)
                if name == "A" and calls.count("A") == 1:
                    app._show_companion_prompt("B", items, "/b/", "/tmp")  # arrives while A's dialog is open
                    raise tk.TclError("grab failed: window not viewable")
                return list(items)

            try:
                with mock.patch.object(app_module, "prompt_companions", side_effect=fake_prompt), \
                        mock.patch.object(app, "_enqueue_companion_items",
                                          side_effect=lambda items, bp, sp: enqueued.append(len(items))), \
                        mock.patch.object(app_module, "log_error") as log:
                    app._show_companion_prompt("A", [item], "/b/", "/tmp")
                    self._pump(0.6)
            finally:
                del app.COMPANION_PROMPT_GAP_MS
            self.assertEqual(sorted(calls), ["A", "A", "B"])  # A failed, was retried, and B was not lost
            self.assertEqual(enqueued, [1, 1])
            self.assertFalse(app._companion_dialog_open)
            self.assertTrue(app._companion_prompt_queue.empty())
            (failure,) = log.call_args_list
            self.assertIn("companion dialog failed for A", failure.args[0])

        self._run_in_mainloop(scenario)

    def test_a_companion_dialog_that_fails_twice_leaves_a_hint_and_a_free_flag(self):
        def scenario():
            app = self.app
            self._pump(0.3)
            app.COMPANION_PROMPT_GAP_MS = 20
            calls = []

            def fake_prompt(parent, name, items, **kw):
                calls.append(name)
                raise tk.TclError("grab failed: window not viewable")

            try:
                with mock.patch.object(app_module, "prompt_companions", side_effect=fake_prompt), \
                        mock.patch.object(app_module, "log_error") as log:
                    app._show_companion_prompt("A", [self._companion_item()], "/b/", "/tmp")
                    self._pump(0.5)
            finally:
                del app.COMPANION_PROMPT_GAP_MS
            self.assertEqual(calls, ["A", "A"])  # one retry, not an endless loop
            self.assertEqual(log.call_count, 2)
            self.assertFalse(app._companion_dialog_open)
            self.assertIn("Check Downloaded Games", app._status_var.get())

        self._run_in_mainloop(scenario)

    def test_queued_companion_prompts_open_a_moment_apart(self):
        def scenario():
            app = self.app
            self._pump(0.3)
            app.COMPANION_PROMPT_GAP_MS = 120
            item = self._companion_item()
            times = []

            def fake_prompt(parent, name, items, **kw):
                times.append(time.monotonic())
                if len(times) == 1:  # two more prompts arrive while the first dialog is open
                    app._show_companion_prompt("B", items, "/b/", "/tmp")
                    app._show_companion_prompt("C", items, "/b/", "/tmp")
                return []

            try:
                with mock.patch.object(app_module, "prompt_companions", side_effect=fake_prompt):
                    app._show_companion_prompt("A", [item], "/b/", "/tmp")
                    self._pump(0.8)
            finally:
                del app.COMPANION_PROMPT_GAP_MS
            self.assertEqual(len(times), 3)
            self.assertGreaterEqual(times[1] - times[0], 0.09)  # a double-click cannot reach the next prompt
            self.assertGreaterEqual(times[2] - times[1], 0.09)

        self._run_in_mainloop(scenario)

    def test_tk_callback_exceptions_reach_the_error_log_once_per_minute(self):
        def scenario():
            import sys
            from minerva.constants import get_error_log_path

            app = self.app

            def raised(message):
                try:
                    raise ValueError(message)
                except ValueError:
                    return sys.exc_info()

            first, second = raised("boom one"), raised("boom two")
            with mock.patch.object(tk.Tk, "report_callback_exception") as printed:
                app.report_callback_exception(*first)
                app.report_callback_exception(*first)  # a callback that keeps failing must not flood the log
                app.report_callback_exception(*second)
            text = get_error_log_path().read_text(encoding="utf-8")
            self.assertEqual(text.count("ValueError: boom one"), 1)
            self.assertEqual(text.count("ValueError: boom two"), 1)
            self.assertIn("Tk callback exception", text)
            self.assertIn("Traceback (most recent call last)", text)  # the traceback itself, not just the message
            self.assertIn("raise ValueError(message)", text)
            self.assertEqual(printed.call_count, 3)  # terminal runs still print every one

        self._run_in_mainloop(scenario)

    def test_search_typing_is_debounced_into_one_render(self):
        def scenario():
            app = self.app
            self._pump(0.8)
            original = app._render_right_list
            renders = []

            def counting():
                renders.append(1)
                original()

            with mock.patch.object(app, "_render_right_list", side_effect=counting):
                for text in ("g", "ga", "gam", "game", "game ", "game 1", "game 12"):
                    app._search_var.set(text)  # as fast as a user typing
                self._pump(0.6)
            self.assertEqual(len(renders), 1)
            shown = len(app._right_tree.get_children())
            expected = sum(1 for i in range(ROWS) if "game 12" in f"game {i} (usa).zip")
            self.assertEqual(shown, expected)
            app._search_var.set("")
            self._pump(0.5)
            self.assertEqual(len(app._right_tree.get_children()), ROWS)

        self._run_in_mainloop(scenario)

    def test_slow_stale_navigation_response_is_ignored(self):
        def scenario():
            app = self.app
            self._pump(0.8)

            def fetch(path):
                if path.endswith("/slow/"):
                    time.sleep(0.4)  # answers after the user already moved on
                    return [{"name": "Stale (USA).zip", "href": "/rom?id=901", "size": "1 MB", "is_folder": False}]
                return [{"name": "Fresh (USA).zip", "href": "/rom?id=902", "size": "1 MB", "is_folder": False}]

            with mock.patch.object(app_module, "fetch_entries", fetch):
                app._navigate("/browse/slow/")
                app._navigate("/browse/fast/")
                self._pump(1.0)
            self.assertEqual([e["name"] for e in app._all_entries], ["Fresh (USA).zip"])
            self.assertEqual(app._right_tree.get_children(), ("/rom?id=902",))
            self.assertEqual(app._current_path, "/browse/fast/")

        self._run_in_mainloop(scenario)

    def test_filters_use_precomputed_fields_and_read_checkboxes_once(self):
        def scenario():
            app = self.app
            self._pump(0.8)
            gets = []
            for var in list(app._show_tag_vars.values()) + list(app._show_region_vars.values()):
                original_get = var.get
                var.get = lambda original_get=original_get: (gets.append(1), original_get())[1]
            try:
                app._render_right_list()
            finally:
                for var in list(app._show_tag_vars.values()) + list(app._show_region_vars.values()):
                    del var.get
            checkboxes = len(app._show_tag_vars) + len(app._show_region_vars)
            self.assertLessEqual(len(gets), checkboxes + 2, f"{len(gets)} Tk reads for {ROWS} rows")

        self._run_in_mainloop(scenario)

    def test_header_summary_and_window_title_show_live_speed(self):
        def scenario():
            app = self.app
            self._pump(0.6)
            engine = _FakeEngine()
            engine.get_all_statuses = lambda: {
                "a1": {"name": "A.zip", "progress": 0.5, "download_rate": 3 * 1024 * 1024, "state": "Downloading",
                       "paused": False, "total_done": 5, "total": 10, "num_peers": 3, "error": "", "eta": 10},
            }
            app._torrent_engine = engine
            app._download_queue = DownloadQueue(engine, max_active=3)
            app._download_queue.enqueue("a1", "A.zip", "s", 0, "/tmp")
            app._download_queue.start_selected(["a1"])
            app._downloads_visible = True
            # Some window managers leave the test window iconified, which skips the panel rebuild.
            with mock.patch.object(app, "state", return_value="normal"):
                app._poll_downloads_once()  # used to raise NameError once a download was active
            self.assertIn("3.0 MB/s", app._dl_summary_lbl.cget("text"))
            self.assertIn("50% @ 3.0 MB/s", app.title())
            self.assertEqual(app._downloads_panel.kind_of("a1"), "active")

        self._run_in_mainloop(scenario)

    def test_history_remembers_the_archive_folder_and_keeps_it_when_requeued_without_one(self):
        app = self.app
        app._download_history.clear()
        app._remember_download("Game (USA).zip", "src", 0, "/tmp", "/browse/./Redump/Sony%20-%20PlayStation%203/")
        app._remember_download("Game (USA).zip", "src2", 0, "/tmp")  # e.g. a redownload
        self.assertEqual(app._download_history["Game (USA).zip"]["browse_path"], "/browse/./Redump/Sony%20-%20PlayStation%203/")
        reloaded = app._load_download_history  # persisted shape survives a settings round trip
        app._settings["download_history"] = list(app._download_history.values())
        self.assertEqual(reloaded()["Game (USA).zip"]["browse_path"], "/browse/./Redump/Sony%20-%20PlayStation%203/")
        app._download_history.clear()

    def test_check_for_updates_tells_you_when_no_game_has_a_known_folder(self):
        def scenario():
            app = self.app
            app._download_history.clear()
            app._download_history["Old (USA).zip"] = {"name": "Old (USA).zip", "source": "s", "so_id": 0, "save_path": "/tmp"}
            shown = []
            with mock.patch.object(app_module.messagebox, "showinfo", side_effect=lambda t, m: shown.append(m)):
                app._check_library_updates_click()
            self.assertEqual(len(shown), 1)
            self.assertIn("No downloaded games with a known archive folder", shown[0])
            self.assertFalse(getattr(app, "_update_check_running", False))
            app._download_history.clear()

        self._run_in_mainloop(scenario)

    def test_check_for_updates_offers_and_queues_what_was_found(self):
        def scenario():
            app = self.app
            self._pump(0.5)
            from minerva.core.companions import KIND_UPDATE, Companion
            from minerva.core.update_check import UpdateCheckResult, UpdateReport

            item = Companion("Game (USA) (v1.04) (Update).zip", "/rom?id=77", "1 MB", KIND_UPDATE, "Updates")
            result = UpdateCheckResult(reports=[UpdateReport("Game (USA).zip", "/browse/x/", [item])], checked=1)
            app._download_history.clear()
            app._download_history["Game (USA).zip"] = {"name": "Game (USA).zip", "source": "s", "so_id": 0,
                                                        "save_path": "/tmp", "browse_path": "/browse/x/"}
            offered, queued = [], []
            with mock.patch.object(app_module, "check_library_updates", return_value=result), \
                    mock.patch.object(app_module, "prompt_companions",
                                      side_effect=lambda parent, title, items, **kw: (offered.extend(items), list(items))[1]), \
                    mock.patch.object(app, "_enqueue_companion_items", side_effect=lambda items, bp, sp: queued.extend(items)):
                app._check_library_updates_click()
                deadline = time.time() + 10
                while time.time() < deadline and not queued:
                    app.update()
                    time.sleep(0.02)
            self.assertEqual([i.name for i in offered], [item.name])
            self.assertEqual([i.name for i in queued], [item.name])
            self.assertFalse(app._update_check_running)
            app._download_history.clear()

        self._run_in_mainloop(scenario)

    def test_poll_loop_rearms_after_an_exception(self):
        def scenario():
            app = self.app
            with mock.patch.object(app, "_poll_downloads_once", side_effect=RuntimeError("boom")), \
                    mock.patch.object(app, "after") as after:
                app._poll_error_logged_at = time.monotonic()  # keep the log quiet
                app._poll_downloads()
            after.assert_called_once_with(500, app._poll_downloads)

        self._run_in_mainloop(scenario)


@unittest.skipIf(app_module is None, "tkinter UI not importable")
class TestDeleteFileBestEffort(unittest.TestCase):
    """Deleting the archive after extraction must never turn a good download into an error."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / "game.zip"
        self.path.write_bytes(b"x")

    def test_deletes_a_normal_file(self):
        self.assertTrue(app_module.MinervaApp._delete_file_best_effort(self.path, delay=0))
        self.assertFalse(self.path.exists())

    def test_missing_file_counts_as_deleted(self):
        self.path.unlink()
        self.assertTrue(app_module.MinervaApp._delete_file_best_effort(self.path, delay=0))

    def test_retries_while_the_file_is_locked_then_succeeds(self):
        real_unlink = pathlib.Path.unlink
        calls = []

        def flaky(p, *a, **k):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("in use by another process")
            return real_unlink(p, *a, **k)

        with mock.patch.object(pathlib.Path, "unlink", flaky):
            self.assertTrue(app_module.MinervaApp._delete_file_best_effort(self.path, delay=0))
        self.assertEqual(len(calls), 3)

    def test_gives_up_quietly_when_the_file_stays_locked(self):
        with mock.patch.object(pathlib.Path, "unlink", side_effect=PermissionError("locked")):
            self.assertFalse(app_module.MinervaApp._delete_file_best_effort(self.path, attempts=3, delay=0))
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
