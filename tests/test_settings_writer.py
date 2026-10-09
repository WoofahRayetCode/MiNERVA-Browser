import threading
import time
import unittest

from minerva.core.settings_writer import AsyncSettingsWriter


class TestAsyncSettingsWriter(unittest.TestCase):
    def test_flush_waits_for_the_latest_settings_to_be_written(self):
        saved = []
        writer = AsyncSettingsWriter(saved.append)
        self.addCleanup(writer.close)
        writer.submit({"v": 1})
        self.assertTrue(writer.flush(2))
        self.assertEqual(saved, [{"v": 1}])

    def test_a_burst_collapses_to_the_newest_value_with_few_writes(self):
        gate = threading.Event()
        saved = []

        def slow_save(settings):
            gate.wait(2)
            saved.append(settings["v"])

        writer = AsyncSettingsWriter(slow_save)
        self.addCleanup(writer.close)
        for i in range(100):
            writer.submit({"v": i})  # never blocks the caller, even while a write is in flight
        gate.set()
        self.assertTrue(writer.flush(3))
        self.assertEqual(saved[-1], 99)
        self.assertLessEqual(len(saved), 3)

    def test_submit_does_not_block_on_a_slow_disk(self):
        gate = threading.Event()
        writer = AsyncSettingsWriter(lambda s: gate.wait(2))
        self.addCleanup(writer.close)
        start = time.perf_counter()
        for i in range(1000):
            writer.submit({"v": i})
        self.assertLess(time.perf_counter() - start, 1.0)
        gate.set()

    def test_flush_times_out_instead_of_hanging(self):
        gate = threading.Event()
        writer = AsyncSettingsWriter(lambda s: gate.wait(5))
        writer.submit({"v": 1})
        self.assertFalse(writer.flush(0.2))
        gate.set()
        writer.close()

    def test_errors_in_save_do_not_kill_the_writer(self):
        calls = []

        def flaky(settings):
            calls.append(settings["v"])
            if settings["v"] == 1:
                raise OSError("disk hiccup")

        writer = AsyncSettingsWriter(flaky)
        self.addCleanup(writer.close)
        writer.submit({"v": 1})
        writer.flush(2)
        writer.submit({"v": 2})
        self.assertTrue(writer.flush(2))
        self.assertEqual(calls, [1, 2])

    def test_close_flushes_and_later_submits_write_inline(self):
        saved = []
        writer = AsyncSettingsWriter(saved.append)
        writer.submit({"v": 1})
        writer.close()
        self.assertEqual(saved, [{"v": 1}])
        writer.submit({"v": 2})
        self.assertEqual(saved[-1], {"v": 2})


if __name__ == "__main__":
    unittest.main()
