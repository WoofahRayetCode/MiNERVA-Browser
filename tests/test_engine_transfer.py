"""End-to-end engine tests against a real libtorrent seeder on localhost."""
import pathlib
import queue
import shutil
import tempfile
import threading
import unittest
from unittest import mock

try:
    import libtorrent as lt  # noqa: F401
    from tests.lt_fixtures import OFFLINE_SETTINGS, Seeder, payload, wait_until
    LT = True
except ImportError:
    LT = False

if LT:
    from minerva.core import torrent_engine as te
    from minerva.core.torrent_engine import TorrentEngine

NAMES = ["Games/Alpha (USA).bin", "Games/Beta (USA).bin", "Gamma.bin"]
BETA = "Beta (USA).bin"
ALPHA = "Alpha (USA).bin"


def drain(engine, kinds=("finished", "error")):
    out = []
    while True:
        try:
            ev = engine.events.get_nowait()
        except queue.Empty:
            return out
        if ev.get("type") in kinds:
            out.append(ev)


@unittest.skipUnless(LT, "libtorrent not installed")
class EngineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        self.save = self.tmp / "downloads"
        self.state = self.tmp / "resume"
        self.seeder = Seeder(NAMES)
        self.addCleanup(self.seeder.close)
        self.engines = []
        self.events = []

    def make_engine(self, **kw):
        kw.setdefault("state_dir", self.state)
        kw.setdefault("default_trackers", [])
        engine = TorrentEngine(settings_overrides=OFFLINE_SETTINGS, **kw)
        self.engines.append(engine)
        self.addCleanup(self._shutdown, engine)
        return engine

    @staticmethod
    def _shutdown(engine):
        try:
            engine.shutdown(timeout=3)
        except Exception:
            pass

    def connect(self, engine):
        """Link seeder and engine from both ends (either side may be in a reconnect back-off)."""
        self.seeder.connect_to(engine._session.listen_port())
        port = self.seeder.session.listen_port()
        for group in list(engine._groups.values()):
            try:
                group.handle.connect_peer(("127.0.0.1", port))
            except Exception:
                pass

    def collect(self, engine):
        self.events.extend(drain(engine))
        return self.events

    def finished_ids(self, engine):
        return {e["id"] for e in self.collect(engine) if e["type"] == "finished"}

    def wait_finished(self, engine, ids, timeout=25.0):
        ids = set(ids)
        return wait_until(lambda: ids <= self.finished_ids(engine), timeout, tick=lambda: self.connect(engine))

    def torrents(self, engine):
        return engine._session.get_torrents()

    def add(self, engine, did, name, idx, save=None):
        return engine.add_download(str(self.seeder.torrent_path), idx, name, str(save or self.save), did)


class TestSingleDownload(EngineCase):
    def test_downloads_flattens_and_cleans_up(self):
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        self.assertTrue(self.wait_finished(engine, ["a"]), self.events)

        target = self.save / BETA
        self.assertEqual(target.read_bytes(), self.seeder.files["Games/Beta (USA).bin"])
        # flattened: no nested collection folder, no leftover libtorrent part file
        self.assertEqual(sorted(p.name for p in self.save.iterdir()), [BETA])
        # the torrent is gone from the session once its last file finished
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))
        self.assertEqual(engine.get_meta("a")["name"], BETA)  # meta survives for the extractor

    def test_only_the_requested_file_is_downloaded(self):
        engine = self.make_engine()
        self.add(engine, "g", "Gamma.bin", 2)
        self.assertTrue(self.wait_finished(engine, ["g"]))
        self.assertEqual([p.name for p in self.save.rglob("*") if p.is_file()], ["Gamma.bin"])

    def test_statuses_come_from_the_snapshot(self):
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        status = wait_until(lambda: (engine.get_all_statuses().get("a") or {}).get("total") and engine.get_all_statuses()["a"], 5)
        self.assertTrue(status, "no status with a known size appeared")
        for key in ("name", "progress", "download_rate", "upload_rate", "state", "num_peers",
                    "total_done", "total", "paused", "error", "eta"):
            self.assertIn(key, status)
        self.assertEqual(status["name"], BETA)
        self.assertEqual(status["total"], len(self.seeder.files["Games/Beta (USA).bin"]))

    def test_wrong_index_is_reported_not_downloaded(self):
        engine = self.make_engine()
        self.add(engine, "bad", "Not In Torrent.bin", 1)
        errors = wait_until(lambda: [e for e in self.collect(engine) if e["type"] == "error"], 10)
        self.assertEqual(len(errors), 1)
        self.assertFalse(errors[0]["retryable"])
        self.assertIn("Not In Torrent.bin", errors[0]["msg"])
        self.assertFalse(self.torrents(engine))

    def test_index_is_corrected_when_the_name_is_unique(self):
        engine = self.make_engine()
        self.add(engine, "fix", BETA, 0)  # server says 0, but Beta is file 1
        self.assertTrue(self.wait_finished(engine, ["fix"]))
        self.assertEqual((self.save / BETA).read_bytes(), self.seeder.files["Games/Beta (USA).bin"])

    def test_missing_torrent_file_is_a_clean_error(self):
        engine = self.make_engine()
        engine.add_download(str(self.tmp / "missing.torrent"), 0, "x.bin", str(self.save), "m")
        errors = wait_until(lambda: [e for e in self.collect(engine) if e["type"] == "error"], 10)
        self.assertEqual(errors[0]["id"], "m")
        self.assertFalse(errors[0]["retryable"])


class TestSharedTorrent(EngineCase):
    def test_siblings_share_one_torrent_and_both_finish(self):
        engine = self.make_engine()
        self.add(engine, "a", ALPHA, 0)
        self.add(engine, "b", BETA, 1)
        self.assertTrue(wait_until(lambda: len(self.torrents(engine)) >= 1, 5))
        self.assertEqual(len(self.torrents(engine)), 1)  # one libtorrent torrent for two items
        self.assertTrue(self.wait_finished(engine, ["a", "b"]), self.events)
        self.assertEqual((self.save / ALPHA).read_bytes(), self.seeder.files["Games/Alpha (USA).bin"])
        self.assertEqual((self.save / BETA).read_bytes(), self.seeder.files["Games/Beta (USA).bin"])
        self.assertEqual(sorted(p.name for p in self.save.iterdir()), [ALPHA, BETA])
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))

    def test_cancelling_one_sibling_does_not_disturb_the_other(self):
        engine = self.make_engine()
        self.add(engine, "a", ALPHA, 0)
        self.add(engine, "b", BETA, 1)
        wait_until(lambda: len(engine._groups) == 1 and len(next(iter(engine._groups.values())).members) == 2, 5)

        engine.remove_handle("a")  # nothing has transferred yet (seeder not connected)

        self.assertEqual(len(self.torrents(engine)), 1)  # the torrent survives for the sibling
        self.assertNotIn("a", engine.get_all_statuses())
        self.assertTrue(self.wait_finished(engine, ["b"]), self.events)
        self.assertNotIn("a", self.finished_ids(engine))
        self.assertTrue(wait_until(lambda: not (self.save / ALPHA).exists(), 5))
        self.assertEqual((self.save / BETA).read_bytes(), self.seeder.files["Games/Beta (USA).bin"])

    def test_cancelling_the_last_member_removes_the_torrent_and_partial_data(self):
        engine = self.make_engine()
        self.add(engine, "a", ALPHA, 0)
        self.assertTrue(wait_until(lambda: len(self.torrents(engine)) == 1, 5))
        engine.remove_handle("a")
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))
        self.assertTrue(wait_until(lambda: not [p for p in self.save.rglob("*") if p.is_file()], 5))
        self.assertEqual(self.collect(engine), [])  # a cancel is not a failure or a completion

    def test_finished_file_survives_when_a_sibling_is_cancelled_later(self):
        engine = self.make_engine()
        self.add(engine, "a", ALPHA, 0)
        self.assertTrue(self.wait_finished(engine, ["a"]))
        self.add(engine, "b", BETA, 1)
        self.assertTrue(wait_until(lambda: len(self.torrents(engine)) == 1, 5))
        engine.remove_handle("b")
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))
        self.assertEqual((self.save / ALPHA).read_bytes(), self.seeder.files["Games/Alpha (USA).bin"])

    def test_sequential_downloads_from_the_same_collection(self):
        engine = self.make_engine()
        self.add(engine, "a", ALPHA, 0)
        self.assertTrue(self.wait_finished(engine, ["a"]))
        engine.stop_seeding("a")
        self.add(engine, "b", BETA, 1)  # same info-hash, re-added right after the removal
        self.assertTrue(self.wait_finished(engine, ["b"]), self.events)
        self.assertEqual((self.save / ALPHA).exists() and (self.save / BETA).exists(), True)

    def test_a_different_save_folder_waits_and_is_retryable(self):
        engine = self.make_engine()
        self.add(engine, "a", ALPHA, 0)
        self.assertTrue(wait_until(lambda: len(engine._groups) == 1, 5))
        self.add(engine, "b", BETA, 1, save=self.tmp / "elsewhere")
        errors = wait_until(lambda: [e for e in self.collect(engine) if e["type"] == "error"], 10)
        self.assertEqual(errors[0]["id"], "b")
        self.assertTrue(errors[0]["retryable"])

    def test_cancel_never_deletes_a_file_that_was_already_on_disk(self):
        # The title was downloaded earlier; queueing it again and cancelling right away (before
        # the engine has recorded progress) must not delete the user's existing file.
        self.save.mkdir(parents=True, exist_ok=True)
        existing = self.save / BETA
        existing.write_bytes(self.seeder.files["Games/Beta (USA).bin"])
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        self.assertTrue(wait_until(lambda: len(self.torrents(engine)) == 1, 5))
        engine.remove_handle("a")
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))
        wait_until(lambda: False, 1.0)  # give any (wrong) cleanup job time to run
        self.assertEqual(existing.read_bytes(), self.seeder.files["Games/Beta (USA).bin"])

    def test_cancel_keeps_a_file_that_another_member_still_needs(self):
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        self.add(engine, "b", BETA, 1)  # same file requested twice (e.g. a redownload race)
        wait_until(lambda: len(engine._groups) == 1 and len(next(iter(engine._groups.values())).members) == 2, 5)
        engine.remove_handle("a")
        self.assertTrue(self.wait_finished(engine, ["b"]), self.events)
        self.assertEqual((self.save / BETA).read_bytes(), self.seeder.files["Games/Beta (USA).bin"])


class TestPauseResume(EngineCase):
    def test_paused_member_does_not_download_until_resumed(self):
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        wait_until(lambda: engine.get_all_statuses().get("a", {}).get("state") not in (None, "Queued", "Metadata"), 5)
        engine.pause("a")
        for _ in range(12):  # keep the seeder pushing for ~1.5 s
            self.connect(engine)
            wait_until(lambda: False, 0.12)
        status = engine.get_all_statuses()["a"]
        self.assertTrue(status["paused"])
        self.assertEqual(status["state"], "Paused")
        self.assertEqual(self.finished_ids(engine), set())

        engine.resume("a")
        self.assertTrue(self.wait_finished(engine, ["a"]), self.events)

    def test_pausing_one_sibling_leaves_the_other_running(self):
        engine = self.make_engine()
        self.add(engine, "a", ALPHA, 0)
        self.add(engine, "b", BETA, 1)
        wait_until(lambda: len(engine._groups) == 1 and len(next(iter(engine._groups.values())).members) == 2, 5)
        engine.pause("a")
        self.assertTrue(self.wait_finished(engine, ["b"]), self.events)
        self.assertNotIn("a", self.finished_ids(engine))
        engine.resume("a")
        self.assertTrue(self.wait_finished(engine, ["a"]), self.events)


class TestSeeding(EngineCase):
    def test_torrent_is_removed_by_default_and_kept_when_seeding_is_enabled(self):
        engine = self.make_engine()
        engine.set_seeding(True, ratio=100.0, hours=24)
        self.add(engine, "a", BETA, 1)
        self.assertTrue(self.wait_finished(engine, ["a"]))
        self.assertEqual(len(self.torrents(engine)), 1)  # still alive, seeding
        engine.stop_seeding("a")
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))

    def test_seeding_stops_at_the_ratio_limit(self):
        engine = self.make_engine()
        engine.set_seeding(True, ratio=0.0, hours=24)  # already satisfied
        self.add(engine, "a", BETA, 1)
        self.assertTrue(self.wait_finished(engine, ["a"]))
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))


class TestMagnet(EngineCase):
    def test_magnet_metadata_then_only_the_wanted_file(self):
        engine = self.make_engine()
        engine.add_download(f"magnet:?xt=urn:btih:{self.seeder.info_hash}", 1, BETA, str(self.save), "m")
        self.assertTrue(self.wait_finished(engine, ["m"], timeout=30), self.events)
        self.assertEqual([p.name for p in self.save.rglob("*") if p.is_file()], [BETA])
        self.assertEqual((self.save / BETA).read_bytes(), self.seeder.files["Games/Beta (USA).bin"])


class TestGuards(EngineCase):
    def test_not_enough_disk_space_fails_early_with_a_clear_message(self):
        engine = self.make_engine()
        fake = shutil._ntuple_diskusage(100, 100, 0)
        with mock.patch.object(te.shutil, "disk_usage", return_value=fake):
            self.add(engine, "a", BETA, 1)
            errors = wait_until(lambda: [e for e in self.collect(engine) if e["type"] == "error"], 10)
        self.assertIn("Not enough free disk space", errors[0]["msg"])
        self.assertFalse(errors[0]["retryable"])
        self.assertFalse(self.torrents(engine))

    def test_stalled_download_re_announces_then_fails_retryably(self):
        engine = self.make_engine()
        with mock.patch.object(te, "STALL_REANNOUNCE_AFTER", 0.4), mock.patch.object(te, "STALL_FAIL_AFTER", 1.2):
            self.add(engine, "a", BETA, 1)  # the seeder never connects
            errors = wait_until(lambda: [e for e in self.collect(engine) if e["type"] == "error"], 15)
        self.assertEqual(errors[0]["id"], "a")
        self.assertTrue(errors[0]["retryable"])
        self.assertIn("Stalled", errors[0]["msg"])
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))

    def test_events_are_only_finished_or_error(self):
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        self.assertTrue(self.wait_finished(engine, ["a"]))
        self.assertTrue(all(e["type"] in ("finished", "error") for e in self.events))


class TestResumeData(EngineCase):
    def test_shutdown_saves_resume_data_and_dht_state_and_a_restart_continues(self):
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        self.assertTrue(wait_until(lambda: len(self.torrents(engine)) == 1, 5))
        engine.shutdown(timeout=6)
        blobs = sorted(p.name for p in self.state.glob("*.fastresume"))
        self.assertEqual(blobs, [f"{self.seeder.info_hash}.fastresume"])
        self.assertTrue((self.state / "session.state").is_file())

        engine2 = self.make_engine()
        self.add(engine2, "a", BETA, 1)
        self.assertTrue(self.wait_finished(engine2, ["a"]), self.events)
        self.assertEqual((self.save / BETA).read_bytes(), self.seeder.files["Games/Beta (USA).bin"])
        self.assertFalse(list(self.state.glob("*.fastresume")))  # dropped once the torrent is done

    def test_resume_data_cannot_redirect_the_download_folder(self):
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        self.assertTrue(wait_until(lambda: len(self.torrents(engine)) == 1, 5))
        engine.shutdown(timeout=6)

        other = self.tmp / "other-folder"
        engine2 = self.make_engine()
        engine2.add_download(str(self.seeder.torrent_path), 1, BETA, str(other), "a")
        self.assertTrue(self.wait_finished(engine2, ["a"]), self.events)
        self.assertTrue((other / BETA).is_file())
        self.assertFalse((self.save / BETA).exists())

    def test_corrupt_resume_blob_is_discarded(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / f"{self.seeder.info_hash}.fastresume").write_bytes(b"garbage")
        engine = self.make_engine()
        self.add(engine, "a", BETA, 1)
        self.assertTrue(self.wait_finished(engine, ["a"]), self.events)


if __name__ == "__main__":
    unittest.main()
