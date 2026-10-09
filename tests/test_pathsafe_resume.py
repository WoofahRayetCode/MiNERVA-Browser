import os
import pathlib
import tempfile
import time
import unittest

from minerva.core.pathsafe import is_safe_leaf_name, is_within, safe_leaf_name
from minerva.core.resume_store import ResumeStore

H = "ab" * 20


class TestPathSafe(unittest.TestCase):
    def test_safe_names_pass_through_unchanged(self):
        for name in ("Game (USA) (En,Fr).zip", "Pokémon Red.gb", "a b.7z", "x" * 150):
            self.assertEqual(safe_leaf_name(name), name)
            self.assertTrue(is_safe_leaf_name(name), name)

    def test_traversal_and_separators_are_neutralised(self):
        self.assertEqual(safe_leaf_name("../../etc/passwd"), "passwd")
        self.assertEqual(safe_leaf_name("..\\..\\boot.ini"), "boot.ini")
        self.assertEqual(safe_leaf_name("/abs/path/file.zip"), "file.zip")
        for bad in ("../x.zip", "dir/x.zip", "..", ".", "", "C:\\x.zip"):
            self.assertFalse(is_safe_leaf_name(bad), bad)

    def test_windows_invalid_and_reserved_names(self):
        self.assertEqual(safe_leaf_name('we:ird*na"me?.zip'), "we_ird_na_me_.zip")
        self.assertEqual(safe_leaf_name("CON.txt"), "_CON.txt")
        self.assertEqual(safe_leaf_name("trailing dot. "), "trailing dot")
        self.assertEqual(safe_leaf_name("\x00\x01"), "__")
        self.assertEqual(safe_leaf_name("", fallback="fb"), "fb")

    def test_length_is_capped(self):
        self.assertLessEqual(len(safe_leaf_name("a" * 500 + ".zip")), 200)

    def test_is_within(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            self.assertTrue(is_within(base, base / "a" / "b.zip"))
            self.assertTrue(is_within(base, base))
            self.assertFalse(is_within(base, base / ".." / "x"))
            self.assertFalse(is_within(base, pathlib.Path(tmp).parent))


class TestResumeStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ResumeStore(pathlib.Path(self._tmp.name) / "resume")

    def test_roundtrip_and_delete(self):
        self.assertIsNone(self.store.load(H))
        self.assertTrue(self.store.save(H, b"blob"))
        self.assertEqual(self.store.load(H), b"blob")
        self.assertEqual(self.store.load(H.upper()), b"blob")  # case-insensitive key
        self.store.delete(H)
        self.assertIsNone(self.store.load(H))
        self.store.delete(H)  # idempotent

    def test_rejects_non_hash_keys_so_names_cannot_escape_the_directory(self):
        self.assertIsNone(self.store.load("../../etc/passwd"))
        self.assertFalse(self.store.save("../evil", b"x"))
        self.assertEqual([p.name for p in self.store.directory.iterdir()], [])

    def test_empty_blobs_are_not_stored_or_returned(self):
        self.assertFalse(self.store.save(H, b""))
        (self.store.directory / f"{H}.fastresume").write_bytes(b"")
        self.assertIsNone(self.store.load(H))

    def test_save_is_atomic_overwrite(self):
        self.store.save(H, b"one")
        self.store.save(H, b"two")
        self.assertEqual(self.store.load(H), b"two")
        self.assertEqual(sorted(p.name for p in self.store.directory.iterdir()), [f"{H}.fastresume"])

    def test_session_state(self):
        self.assertIsNone(self.store.load_session_state())
        self.assertTrue(self.store.save_session_state(b"dht"))
        self.assertEqual(self.store.load_session_state(), b"dht")

    def test_prune_removes_only_old_blobs(self):
        self.store.save(H, b"old")
        fresh = "cd" * 20
        self.store.save(fresh, b"new")
        old_time = time.time() - 40 * 86400
        os.utime(self.store.directory / f"{H}.fastresume", (old_time, old_time))
        self.store.save_session_state(b"dht")
        os.utime(self.store.directory / "session.state", (old_time, old_time))
        self.assertEqual(self.store.prune(30), 1)
        self.assertIsNone(self.store.load(H))
        self.assertEqual(self.store.load(fresh), b"new")
        self.assertEqual(self.store.load_session_state(), b"dht")  # session state is never pruned


if __name__ == "__main__":
    unittest.main()
