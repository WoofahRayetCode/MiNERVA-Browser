import unittest
from minerva.core.torrent_engine import DownloadQueue


class MockTorrentEngine:
    def __init__(self):
        self.added = []
        self.removed = []

    def add_download(self, source, so_id, name, save_path, download_id=None):
        self.added.append({"id": download_id, "name": name, "source": source})

    def remove_handle(self, download_id):
        self.removed.append(download_id)


class TestDownloadQueue(unittest.TestCase):
    def setUp(self):
        self.engine = MockTorrentEngine()
        self.queue = DownloadQueue(self.engine, max_active=2)

    def test_enqueue_and_manual_start(self):
        self.queue.enqueue("id-1", "game1.zip", "torrent1", 0, "/tmp")
        self.queue.enqueue("id-2", "game2.zip", "torrent2", 0, "/tmp")
        self.queue.enqueue("id-3", "game3.zip", "torrent3", 0, "/tmp")

        # Not started automatically
        self.assertEqual(len(self.engine.added), 0)
        snap = self.queue.snapshot()
        self.assertEqual(len(snap["pending"]), 3)
        self.assertEqual(len(snap["active"]), 0)

        # Start selected
        self.queue.start_selected(["id-1", "id-2"])
        self.assertEqual(len(self.engine.added), 2)
        snap = self.queue.snapshot()
        self.assertEqual(len(snap["active"]), 2)
        self.assertEqual(len(snap["pending"]), 1)

    def test_advance_on_finished(self):
        self.queue.enqueue("id-1", "game1.zip", "torrent1", 0, "/tmp")
        self.queue.enqueue("id-2", "game2.zip", "torrent2", 0, "/tmp")
        self.queue.enqueue("id-3", "game3.zip", "torrent3", 0, "/tmp")

        self.queue.start_all_pending()
        # Max active is 2, so id-1 and id-2 start
        self.assertEqual(len(self.queue.snapshot()["active"]), 2)

        # id-1 finishes -> id-3 starts automatically
        self.queue.on_finished("id-1")
        snap = self.queue.snapshot()
        self.assertEqual(len(snap["done"]), 1)
        self.assertEqual(len(snap["active"]), 2)
        self.assertIn("id-3", snap["active"])

    def test_has_name(self):
        self.queue.enqueue("id-1", "Chrono Trigger (USA).zip", "torrent", 0, "/tmp")
        self.assertTrue(self.queue.has_name("Chrono Trigger (USA).zip"))
        self.assertFalse(self.queue.has_name("EarthBound (USA).zip"))

    def test_move_up_and_down(self):
        self.queue.enqueue("id-1", "game1.zip", "torrent1", 0, "/tmp")
        self.queue.enqueue("id-2", "game2.zip", "torrent2", 0, "/tmp")
        self.queue.enqueue("id-3", "game3.zip", "torrent3", 0, "/tmp")

        self.queue.move_up("id-3")
        pending_ids = [item["id"] for item in self.queue.snapshot()["pending"]]
        self.assertEqual(pending_ids, ["id-1", "id-3", "id-2"])

        self.queue.move_down("id-1")
        pending_ids = [item["id"] for item in self.queue.snapshot()["pending"]]
        self.assertEqual(pending_ids, ["id-3", "id-1", "id-2"])

    def test_get_default_trackers(self):
        from minerva.constants import get_default_trackers
        trackers = get_default_trackers()
        self.assertGreater(len(trackers), 10)
        self.assertTrue(any("opentrackr.org" in t for t in trackers))
        for t in trackers:
            self.assertTrue(t.startswith("udp://") or t.startswith("http://") or t.startswith("https://"))

    def test_optimized_session_settings(self):
        from minerva.core.torrent_engine import _get_optimized_session_settings
        settings = _get_optimized_session_settings()
        self.assertGreaterEqual(settings["connections_limit"], 500)
        self.assertTrue(settings["enable_dht"])
        self.assertTrue(settings["dht_aggressive_lookups"])
        self.assertIn("dht.transmissionbt.com", settings["dht_bootstrap_nodes"])


if __name__ == "__main__":
    unittest.main()
