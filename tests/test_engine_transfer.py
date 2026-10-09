"""End-to-end engine tests against a real libtorrent seeder on localhost."""
import pathlib
import queue
import shutil
import time
import types
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


    def test_a_stale_cleanup_never_deletes_a_file_that_was_requeued_and_finished(self):
        # G is cancelled while B keeps the torrent alive (G's partial path is queued for cleanup),
        # then G is queued again and finishes.  When the group finally closes, the old cleanup
        # job must not delete the fresh download.
        engine = self.make_engine()
        self.add(engine, "b", BETA, 1)
        self.add(engine, "g", "Gamma.bin", 2)
        wait_until(lambda: len(engine._groups) == 1 and len(next(iter(engine._groups.values())).members) == 2, 5)
        engine.remove_handle("g")
        self.add(engine, "g2", "Gamma.bin", 2)
        self.assertTrue(self.wait_finished(engine, ["b", "g2"]), self.events)
        wait_until(lambda: not self.torrents(engine), 5)
        wait_until(lambda: False, 1.0)  # let any (wrong) cleanup job run
        gamma = [p for p in self.save.rglob("Gamma.bin")]
        self.assertEqual(len(gamma), 1, gamma)
        self.assertEqual(gamma[0].read_bytes(), self.seeder.files["Gamma.bin"])

    def test_requeueing_a_deleted_file_into_a_running_torrent_downloads_it_again(self):
        # With seeding on the torrent stays alive after A finishes.  If the user deletes A and
        # queues it again, libtorrent still "has" the pieces; without a recheck the new member
        # would finish at once with an empty file.
        engine = self.make_engine()
        engine.set_seeding(True, ratio=100.0, hours=24)
        self.add(engine, "a", ALPHA, 0)
        self.assertTrue(self.wait_finished(engine, ["a"]))
        engine.stop_seeding("a")
        (self.save / ALPHA).unlink()
        self.add(engine, "a2", ALPHA, 0)
        self.assertTrue(self.wait_finished(engine, ["a2"], timeout=40), self.events)
        self.assertEqual((self.save / ALPHA).read_bytes(), self.seeder.files["Games/Alpha (USA).bin"])


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


    def test_pause_issued_before_the_torrent_is_added_is_honoured(self):
        # Pause-all can land while a large .torrent is still being parsed (no group yet).
        engine = self.make_engine()
        real = engine._load_torrent_info

        def slow(source):
            time.sleep(0.6)
            return real(source)

        engine._load_torrent_info = slow
        self.add(engine, "a", BETA, 1)
        engine.pause("a")  # the add worker has not created the group yet
        self.assertTrue(wait_until(lambda: len(self.torrents(engine)) == 1, 10))
        for _ in range(10):
            self.connect(engine)
            wait_until(lambda: False, 0.15)
        self.assertEqual(self.finished_ids(engine), set())
        self.assertTrue(engine.get_all_statuses()["a"]["paused"])
        self.assertFalse((self.save / BETA).exists() and (self.save / BETA).stat().st_size
                         and (self.save / BETA).read_bytes() == self.seeder.files["Games/Beta (USA).bin"])
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


    def test_turning_seeding_off_releases_torrents_that_only_stayed_to_seed(self):
        engine = self.make_engine()
        engine.set_seeding(True, ratio=100.0, hours=24)
        self.add(engine, "a", BETA, 1)
        self.assertTrue(self.wait_finished(engine, ["a"]))
        self.assertEqual(len(self.torrents(engine)), 1)  # seeding
        engine.set_seeding(False)
        self.assertTrue(wait_until(lambda: not self.torrents(engine), 5))
        self.assertEqual((self.save / BETA).read_bytes(), self.seeder.files["Games/Beta (USA).bin"])


class TestMagnet(EngineCase):
    def test_magnet_metadata_then_only_the_wanted_file(self):
        engine = self.make_engine()
        engine.add_download(f"magnet:?xt=urn:btih:{self.seeder.info_hash}", 1, BETA, str(self.save), "m")
        self.assertTrue(self.wait_finished(engine, ["m"], timeout=30), self.events)
        self.assertEqual([p.name for p in self.save.rglob("*") if p.is_file()], [BETA])
        self.assertEqual((self.save / BETA).read_bytes(), self.seeder.files["Games/Beta (USA).bin"])


    def test_magnet_resumed_with_saved_metadata_does_not_wait_for_metadata(self):
        # A resume blob saved with the info dict hands libtorrent the metadata up front, so no
        # metadata_received_alert follows.  The member must not sit in "Metadata" forever.
        engine = self.make_engine()
        info = lt.torrent_info(lt.bdecode(self.seeder.torrent_bytes))

        def merge_with_info_dict(info_hash, params, ti):
            params.ti = info  # what read_resume_data returns for a blob saved with save_info_dict
            return params

        engine._merge_resume = merge_with_info_dict
        engine.add_download(f"magnet:?xt=urn:btih:{self.seeder.info_hash}", 1, BETA, str(self.save), "m")
        group = wait_until(lambda: next(iter(engine._groups.values()), None), 5)
        self.assertIsNotNone(group)
        self.assertIsNotNone(group.ti)  # known immediately, before any peer is connected
        status = wait_until(lambda: engine.get_all_statuses().get("m"), 5)
        self.assertNotEqual(status["state"], "Metadata")
        self.assertTrue(self.wait_finished(engine, ["m"]), self.events)
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


    def _stub_group(self, engine, **member_kw):
        group = te._Group(info_hash="ab" * 20, save_path=str(self.save), ti=object())
        group.handle = mock.Mock()
        member = te._Member("a", "A.bin", 0, str(self.save), "src", {}, group=group, file_idx=0, size=100, **member_kw)
        group.members["a"] = member
        return group, member

    def test_a_member_is_not_stalled_while_the_torrent_is_receiving_data_for_a_sibling(self):
        engine = self.make_engine()
        group, member = self._stub_group(engine)
        now = time.monotonic()
        member.last_progress_at = now - 10_000  # this file has been untouched for hours...
        group.last_activity = now               # ...but bytes for another file just arrived
        with mock.patch.object(engine, "_fail") as fail:
            engine._watchdog(group, member, now)
        fail.assert_not_called()
        group.last_activity = now - 10_000      # now the whole torrent is quiet
        with mock.patch.object(engine, "_fail") as fail:
            engine._watchdog(group, member, now)
        fail.assert_called_once()
        self.assertTrue(fail.call_args.kwargs["retryable"])

    def test_scan_records_torrent_wide_download_activity(self):
        engine = self.make_engine()
        group, member = self._stub_group(engine)
        group.handle.file_progress.return_value = [0]
        group.status = types.SimpleNamespace(state=0, download_rate=0, upload_rate=0, num_peers=0,
                                             total_done=0, all_time_download=500)
        old = time.monotonic() - 1000
        group.last_activity = old
        now = time.monotonic()
        engine._scan_group(group, now, {})
        self.assertEqual((group.last_downloaded, group.last_activity), (500, now))
        engine._scan_group(group, now + 5, {})  # nothing new arrived: activity time must not move
        self.assertEqual(group.last_activity, now)


    def _two_member_group(self, engine):
        group = te._Group(info_hash="cd" * 20, save_path=str(self.save), ti=object())
        group.handle = mock.Mock()
        members = []
        for did, name, idx in (("a", "A.bin", 0), ("b", "B.bin", 1)):
            m = te._Member(did, name, idx, str(self.save), "src", {}, group=group, file_idx=idx, size=100, flat_name=name)
            group.members[did] = m
            engine._members[did] = m
            members.append(m)
        return group, members

    @staticmethod
    def _err(value, text="boom"):
        return types.SimpleNamespace(value=lambda: value, message=lambda: text)

    def test_a_full_disk_marks_every_running_member(self):
        engine = self.make_engine()
        group, (a, b) = self._two_member_group(engine)
        engine._on_storage_error(group, self._err(next(iter(te.DISK_FULL_ERRNOS))), "")
        for m in (a, b):
            self.assertEqual(m.state_override, "Disk full")
            self.assertIn("Disk full", m.error)
            self.assertTrue(m.paused)

    def test_a_file_error_only_marks_the_member_whose_file_failed(self):
        engine = self.make_engine()
        group, (a, b) = self._two_member_group(engine)
        engine._on_storage_error(group, self._err(5, "Input/output error"), str(self.save / "B.bin"))
        self.assertEqual((a.error, a.paused), ("", False))  # the healthy sibling is untouched
        self.assertEqual(b.state_override, "Error")
        self.assertIn("B.bin", b.error)
        self.assertNotIn("bound method", b.error)

    def test_a_file_error_naming_no_known_file_marks_nobody(self):
        engine = self.make_engine()
        group, (a, b) = self._two_member_group(engine)
        engine._on_storage_error(group, self._err(5), str(self.save / "deleted-archive.zip"))
        self.assertEqual([(m.error, m.paused) for m in (a, b)], [("", False), ("", False)])

    def test_resume_after_a_storage_error_really_restarts_the_torrent(self):
        engine = self.make_engine()
        group, (a, b) = self._two_member_group(engine)
        engine._on_storage_error(group, self._err(5), str(self.save / "A.bin"))
        group.handle.reset_mock()
        engine.resume("a")
        group.handle.clear_error.assert_called()
        group.handle.unset_flags.assert_called_with(lt.torrent_flags.upload_mode)  # libtorrent parks it there
        group.handle.resume.assert_called()
        self.assertEqual((a.error, a.paused), ("", False))

    def test_retryable_failures_keep_the_partial_file_and_final_ones_delete_it(self):
        engine = self.make_engine()
        group, (a, b) = self._two_member_group(engine)
        with mock.patch.object(engine, "_detach") as detach:
            engine._fail(a, "Stalled", retryable=True)
            engine._fail(b, "Invalid torrent", retryable=False)
        self.assertEqual([c.kwargs["delete_partial"] for c in detach.call_args_list], [False, True])

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
