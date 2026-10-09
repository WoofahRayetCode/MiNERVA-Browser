"""The real MinervaApp driving the real TorrentEngine against a localhost seeder."""
import pathlib
import tempfile
import time
import tkinter as tk
import unittest
from unittest import mock

from tests.app_harness import isolated_app_patches

try:
    import libtorrent  # noqa: F401
    from tests.lt_fixtures import OFFLINE_SETTINGS, Seeder, wait_until
    import minerva.ui.app as app_module
    from minerva.core import torrent_engine as te
    from minerva.core.lookup import LookupFailure, ResolvedDownload
    READY = True
except Exception:  # pragma: no cover - no libtorrent / tkinter
    READY = False

ALPHA, BETA = "Alpha (USA).bin", "Beta (USA).bin"
NAMES = [f"Games/{ALPHA}", f"Games/{BETA}", "Gamma.bin"]


@unittest.skipUnless(READY, "libtorrent and tkinter are required")
class TestAppWithRealEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = pathlib.Path(cls._tmp.name)
        cls.seeder = Seeder(NAMES)
        entries = [
            {"name": ALPHA, "href": "/rom?id=1", "size": "48 KB", "is_folder": False},
            {"name": BETA, "href": "/rom?id=2", "size": "48 KB", "is_folder": False},
            {"name": "Gamma.bin", "href": "/rom?id=3", "size": "48 KB", "is_folder": False},
        ]
        real_engine = te.TorrentEngine

        def offline_engine(**kw):
            return real_engine(settings_overrides=OFFLINE_SETTINGS, default_trackers=[], **kw)

        cls._patches = isolated_app_patches(app_module, cls.base, lambda path: list(entries)) + [
            mock.patch.object(app_module, "TorrentEngine", offline_engine),
        ]
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
        cls.seeder.close()
        cls._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app._quitting = True
            if cls.app._torrent_engine is not None:
                cls.app._torrent_engine.shutdown(timeout=3)
            cls.app.destroy()
        except Exception:
            pass
        cls._cleanup()

    def _run_in_mainloop(self, fn):
        failure = []

        def runner():
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001
                failure.append(exc)
            finally:
                self.app.quit()

        self.app.after(300, runner)
        self.app.mainloop()
        if failure:
            raise failure[0]

    def _pump_until(self, predicate, timeout=30.0):
        engine = self.app._torrent_engine

        def tick():
            self.app.update()
            if engine is not None:
                self.seeder.connect_to(engine._session.listen_port())
                for group in list(engine._groups.values()):
                    try:
                        group.handle.connect_peer(("127.0.0.1", self.seeder.session.listen_port()))
                    except Exception:
                        pass

        return wait_until(predicate, timeout, interval=0.02, tick=tick)

    def test_select_queue_start_download_finish(self):
        def scenario():
            app = self.app
            self._pump_until(lambda: len(app._right_tree.get_children()) == 3, 5)

            class Resolver:
                def resolve(self_inner, rom_id, name):
                    index = {"1": 0, "2": 1}.get(rom_id)
                    if index is None:
                        raise LookupFailure("not_found", "was not found on the server")
                    return ResolvedDownload(str(self.seeder.torrent_path), index, name)

            app._rom_resolver = Resolver()
            extracted, dialogs = [], []
            app._prompt_post_download_actions_batch = lambda ids: extracted.extend(ids)
            with mock.patch.object(app_module.messagebox, "showwarning", side_effect=lambda t, b: dialogs.append((t, b))), \
                    mock.patch.object(app_module.messagebox, "showerror", side_effect=lambda t, b: dialogs.append((t, b))):
                app._checked_hrefs = {"/rom?id=1", "/rom?id=2", "/rom?id=3"}  # id 3 has no torrent
                app._queue_checked_downloads()
                self.assertTrue(self._pump_until(lambda: len(app._download_queue.snapshot()["pending"]) == 2, 10))
                self.assertEqual(len(dialogs), 1)  # the one failure is reported once
                self.assertIn("Gamma.bin", dialogs[0][1])

                app._download_queue.start_all_pending()
                done = lambda: len(app._download_queue.snapshot()["done"]) == 2  # noqa: E731
                self.assertTrue(self._pump_until(done, 40), app._download_queue.snapshot())

            snap = app._download_queue.snapshot()
            self.assertEqual({d["name"] for d in snap["done"]}, {ALPHA, BETA})
            self.assertTrue(all(d["status"] == "done" for d in snap["done"]), snap["done"])
            download_dir = pathlib.Path(app.get_download_dir())
            self.assertEqual((download_dir / ALPHA).read_bytes(), self.seeder.files[f"Games/{ALPHA}"])
            self.assertEqual((download_dir / BETA).read_bytes(), self.seeder.files[f"Games/{BETA}"])
            self.assertEqual(len(extracted), 2)  # post-download processing was triggered for each

            # The browse list now shows both titles as downloaded, with no disk rescan.
            # The icon refresh is coalesced (~40 ms) and the two downloads may finish in different
            # poll ticks, so wait for both rows rather than just the first.
            self._pump_until(lambda: app._right_tree.set("/rom?id=1", "dlstat") == "✓"
                             and app._right_tree.set("/rom?id=2", "dlstat") == "✓", 10)
            self.assertEqual(app._right_tree.set("/rom?id=1", "dlstat"), "✓")
            self.assertEqual(app._right_tree.set("/rom?id=2", "dlstat"), "✓")
            self.assertEqual(app._right_tree.set("/rom?id=3", "dlstat"), "")
            self.assertFalse(app._torrent_engine._session.get_torrents())  # torrent released

        self._run_in_mainloop(scenario)


if __name__ == "__main__":
    unittest.main()
