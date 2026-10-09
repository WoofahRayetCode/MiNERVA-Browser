import json
import pathlib
import tempfile
import threading
import unittest
from unittest import mock

from minerva import constants


class TestSettingsDurability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / "minerva_settings.json"
        # The .bak refresh is rate limited per process; make every test start fresh.
        patcher = mock.patch.object(constants, "_last_bak_refresh", float("-inf"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _leftovers(self):
        return sorted(p.name for p in self.path.parent.iterdir() if p.name.endswith(".tmp"))

    def test_roundtrip(self):
        self.assertTrue(constants.save_app_settings({"a": 1, "ünï": "x"}, self.path))
        result = constants.load_app_settings_ex(self.path)
        self.assertEqual(result.source, "primary")
        self.assertEqual(result.data, {"a": 1, "ünï": "x"})
        self.assertEqual(self._leftovers(), [])

    def test_missing_file_gives_defaults(self):
        result = constants.load_app_settings_ex(self.path)
        self.assertEqual((result.source, result.data), ("defaults", {}))

    def test_missing_primary_does_not_resurrect_backup(self):
        # A user who deletes the settings file expects a reset.
        constants.save_app_settings({"a": 1}, self.path)
        constants.save_app_settings({"a": 2}, self.path)
        self.path.unlink()
        self.assertEqual(constants.load_app_settings_ex(self.path).source, "defaults")

    def test_corrupt_primary_is_quarantined_and_backup_used(self):
        constants.save_app_settings({"queue": [1, 2, 3]}, self.path)
        constants.save_app_settings({"queue": [1, 2, 3, 4]}, self.path)  # refreshes .bak with the first
        self.path.write_text("{ truncated", encoding="utf-8")

        result = constants.load_app_settings_ex(self.path)

        self.assertEqual(result.source, "backup")
        self.assertEqual(result.data, {"queue": [1, 2, 3]})
        self.assertIsNotNone(result.quarantined)
        self.assertEqual(result.quarantined.read_text(encoding="utf-8"), "{ truncated")
        self.assertFalse(self.path.exists())  # next save recreates it; the bad copy is kept aside

    def test_corrupt_primary_without_backup_still_preserves_the_file(self):
        self.path.write_text("not json", encoding="utf-8")
        result = constants.load_app_settings_ex(self.path)
        self.assertEqual((result.source, result.data), ("defaults", {}))
        self.assertTrue(result.quarantined and result.quarantined.exists())

    def test_non_object_json_is_treated_as_corrupt(self):
        self.path.write_text("[1, 2]", encoding="utf-8")
        result = constants.load_app_settings_ex(self.path)
        self.assertEqual(result.source, "defaults")
        self.assertIsNotNone(result.quarantined)

    def test_backup_is_not_overwritten_by_a_corrupt_primary(self):
        constants.save_app_settings({"good": True}, self.path)
        constants.save_app_settings({"good": "newer"}, self.path)  # .bak == first save
        bak = self.path.with_name(self.path.name + ".bak")
        self.assertEqual(json.loads(bak.read_text(encoding="utf-8")), {"good": True})

        self.path.write_text("garbage", encoding="utf-8")
        with mock.patch.object(constants, "_last_bak_refresh", float("-inf")):
            constants.save_app_settings({"good": "after"}, self.path)
        self.assertEqual(json.loads(bak.read_text(encoding="utf-8")), {"good": True})

    def test_backup_refresh_is_rate_limited(self):
        constants.save_app_settings({"v": 1}, self.path)
        constants.save_app_settings({"v": 2}, self.path)  # creates .bak (v1)
        constants.save_app_settings({"v": 3}, self.path)  # within the interval: .bak untouched
        bak = self.path.with_name(self.path.name + ".bak")
        self.assertEqual(json.loads(bak.read_text(encoding="utf-8")), {"v": 1})

    def test_failed_write_keeps_old_file_and_cleans_tmp(self):
        constants.save_app_settings({"ok": 1}, self.path)
        with mock.patch.object(constants.json, "dump", side_effect=RuntimeError("disk full")):
            self.assertFalse(constants.save_app_settings({"ok": 2}, self.path))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"ok": 1})
        self.assertEqual(self._leftovers(), [])

    def test_concurrent_saves_never_leave_a_torn_file(self):
        errors = []

        def worker(n):
            for i in range(25):
                if not constants.save_app_settings({"writer": n, "i": i, "pad": "x" * 2000}, self.path):
                    errors.append((n, i))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("writer", data)
        self.assertEqual(self._leftovers(), [])

    def test_load_app_settings_wrapper_returns_dict(self):
        with mock.patch.object(constants, "get_settings_path", return_value=self.path):
            constants.save_app_settings({"k": "v"})
            self.assertEqual(constants.load_app_settings(), {"k": "v"})


if __name__ == "__main__":
    unittest.main()


class TestLogRotation(unittest.TestCase):
    def test_log_rotates_once_it_grows_past_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "minerva_error.log"
            with mock.patch.dict("os.environ", {"MINERVA_ERROR_LOG": str(log)}), \
                    mock.patch.object(constants, "MAX_LOG_BYTES", 200):
                for i in range(30):
                    constants.log_activity(f"line {i} " + "x" * 20)
                constants.log_error("something failed", RuntimeError("boom"))
            rotated = log.with_name(log.name + ".1")
            self.assertTrue(rotated.exists())
            self.assertTrue(log.exists())
            self.assertLess(log.stat().st_size, 400)
            self.assertIn("something failed", log.read_text(encoding="utf-8") + rotated.read_text(encoding="utf-8"))
