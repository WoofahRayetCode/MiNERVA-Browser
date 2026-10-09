import threading
import time
import unittest

from minerva.core.extractors import (
    library_keys_for_name,
    library_status_for_name,
    queued_match_keys,
    status_from_keys,
)
from minerva.core.library_index import LibraryIndex


class _Scan:
    """Controllable scan function: blocks until released so races can be exercised."""

    def __init__(self, result):
        self.result = result
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, root):
        self.calls.append(str(root))
        self.gate.wait(5)
        return set(self.result)


def _wait(predicate, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class TestStatusFromKeys(unittest.TestCase):
    NAMES = [
        "Chrono Trigger (USA).zip",
        "N3 - Ninety-Nine Nights (USA) (En,Fr).zip",
        "Final Fantasy VII (USA) (Disc 1).zip",
        "Final Fantasy VII (USA) (Disc 2).zip",
        "Missing Game.zip",
        "Persona 5 (USA) (En,Fr) (Special Edition).iso",
        "Game [b1].zip",
        "",
    ]

    def test_matches_original_per_row_implementation_semantics(self):
        disk = frozenset(
            library_keys_for_name("Chrono Trigger (USA).chd")
            | library_keys_for_name("Final Fantasy VII (USA) (Disc 1).chd")
        )
        queued_names = {"N3 - Ninety-Nine Nights (USA) (En,Fr).zip", "Persona 5 (USA).iso"}
        queued_keys = queued_match_keys(queued_names)
        expected = {
            "Chrono Trigger (USA).zip": "downloaded",
            "N3 - Ninety-Nine Nights (USA) (En,Fr).zip": "queued",
            "Final Fantasy VII (USA) (Disc 1).zip": "downloaded",
            "Final Fantasy VII (USA) (Disc 2).zip": "",
            "Missing Game.zip": "",
            "": "",
        }
        for name, status in expected.items():
            self.assertEqual(
                status_from_keys(library_keys_for_name(name), queued_keys, disk), status, name
            )
            self.assertEqual(library_status_for_name(name, queued_names, set(disk)), status, name)

    def test_downloaded_wins_over_queued(self):
        keys = library_keys_for_name("Chrono Trigger (USA).zip")
        self.assertEqual(status_from_keys(keys, keys, keys), "downloaded")

    def test_keys_are_cached_and_immutable(self):
        a = library_keys_for_name("Some Game (USA).zip")
        self.assertIs(a, library_keys_for_name("Some Game (USA).zip"))
        self.assertIsInstance(a, frozenset)

    def test_icon_pass_for_5k_rows_and_500_queued_is_fast(self):
        rows = [library_keys_for_name(f"Game {i} (USA).zip") for i in range(5000)]
        queued = queued_match_keys([f"Game {i * 7} (USA).zip" for i in range(500)])
        disk = frozenset(library_keys_for_name(f"Game {i * 11} (USA).chd") for i in range(500))
        disk = frozenset().union(*disk)
        start = time.perf_counter()
        results = [status_from_keys(k, queued, disk) for k in rows]
        elapsed = time.perf_counter() - start
        self.assertIn("queued", results)
        self.assertIn("downloaded", results)
        # The per-row regex recompute this replaced cost ~8.6 ms/row at this queue size.
        self.assertLess(elapsed, 0.75)


class TestLibraryIndex(unittest.TestCase):
    def test_first_use_scans_in_background_and_notifies(self):
        scan = _Scan({"a", "b"})
        scan.gate.clear()
        index = LibraryIndex(scan_fn=scan)
        changed = threading.Event()

        keys = index.ensure("/lib", on_change=changed.set)

        self.assertEqual(keys, frozenset())  # never blocks the caller on the scan
        self.assertTrue(index.scanning)
        scan.gate.set()
        self.assertTrue(changed.wait(3))
        self.assertEqual(index.keys, frozenset({"a", "b"}))
        self.assertFalse(index.scanning)

    def test_ensure_same_root_does_not_rescan(self):
        scan = _Scan({"a"})
        index = LibraryIndex(scan_fn=scan)
        done = threading.Event()
        index.ensure("/lib", on_change=done.set)
        self.assertTrue(done.wait(3))
        index.ensure("/lib")
        index.ensure("/lib")
        self.assertEqual(scan.calls, ["/lib"])

    def test_changing_root_drops_stale_keys_immediately(self):
        scan = _Scan({"a"})
        index = LibraryIndex(scan_fn=scan)
        done = threading.Event()
        index.ensure("/one", on_change=done.set)
        self.assertTrue(done.wait(3))
        scan.gate.clear()
        self.assertEqual(index.ensure("/two"), frozenset())
        scan.gate.set()

    def test_add_names_is_incremental_and_survives_an_in_flight_scan(self):
        scan = _Scan({"scanned"})
        scan.gate.clear()
        index = LibraryIndex(scan_fn=scan)
        done = threading.Event()
        index.rescan("/lib", on_change=done.set)

        grew = index.add_names(["Chrono Trigger (USA).zip"])  # finished while scanning
        self.assertTrue(grew)
        self.assertIn("chrono trigger (usa).zip", index.keys)

        scan.gate.set()
        self.assertTrue(done.wait(3))
        self.assertIn("scanned", index.keys)
        self.assertIn("chrono trigger (usa).zip", index.keys)  # not lost when the scan published

    def test_add_names_reports_no_growth_for_known_names(self):
        index = LibraryIndex(scan_fn=_Scan(set()))
        self.assertTrue(index.add_names(["X (USA).zip"]))
        self.assertFalse(index.add_names(["X (USA).zip"]))
        self.assertFalse(index.add_names(["", None]))

    def test_superseded_scan_does_not_publish(self):
        scan = _Scan({"old"})
        scan.gate.clear()
        index = LibraryIndex(scan_fn=scan)
        notified = []
        index.rescan("/lib", on_change=lambda: notified.append("first"))
        scan2 = _Scan({"new"})
        index._scan_fn = scan2
        done = threading.Event()
        index.rescan("/lib", on_change=lambda: (notified.append("second"), done.set()))
        self.assertTrue(done.wait(3))
        scan.gate.set()  # release the stale scan only after the new one published
        time.sleep(0.1)
        self.assertEqual(index.keys, frozenset({"new"}))
        self.assertEqual(notified, ["second"])

    def test_scan_failure_is_logged_not_raised(self):
        def boom(_root):
            raise OSError("denied")

        index = LibraryIndex(scan_fn=boom)
        index.rescan("/lib")
        self.assertTrue(_wait(lambda: not index.scanning))
        self.assertEqual(index.keys, frozenset())


if __name__ == "__main__":
    unittest.main()
