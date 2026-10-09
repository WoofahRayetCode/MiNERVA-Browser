import subprocess
import sys
import textwrap
import threading
import time
import unittest

from minerva.core.workers import DaemonPool


class TestDaemonPool(unittest.TestCase):
    def test_runs_jobs_with_bounded_concurrency(self):
        pool = DaemonPool(3, "t")
        self.addCleanup(pool.shutdown)
        gauge = {"now": 0, "peak": 0}
        lock = threading.Lock()
        done = threading.Event()
        count = [0]

        def job():
            with lock:
                gauge["now"] += 1
                gauge["peak"] = max(gauge["peak"], gauge["now"])
            time.sleep(0.01)
            with lock:
                gauge["now"] -= 1
                count[0] += 1
                if count[0] == 40:
                    done.set()

        for _ in range(40):
            pool.submit(job)
        self.assertTrue(done.wait(5))
        self.assertLessEqual(gauge["peak"], 3)

    def test_a_failing_job_does_not_kill_the_worker(self):
        pool = DaemonPool(1, "t")
        self.addCleanup(pool.shutdown)
        ran = threading.Event()

        def boom():
            raise ValueError("x")

        pool.submit(boom)
        pool.submit(ran.set)
        self.assertTrue(ran.wait(3))

    def test_shutdown_drops_queued_work_and_rejects_new_work(self):
        pool = DaemonPool(1, "t")
        gate = threading.Event()
        ran = []
        pool.submit(gate.wait, 5)
        for i in range(5):
            pool.submit(ran.append, i)
        pool.shutdown()
        gate.set()
        time.sleep(0.2)
        self.assertEqual(ran, [])
        with self.assertRaises(RuntimeError):
            pool.submit(ran.append, 99)

    def test_a_blocked_job_does_not_keep_the_interpreter_alive(self):
        code = textwrap.dedent(
            """
            import sys, threading, time
            sys.path.insert(0, ".")
            from minerva.core.workers import DaemonPool
            pool = DaemonPool(2, "t")
            started = threading.Event()
            pool.submit(lambda: (started.set(), time.sleep(60)))
            started.wait(5)
            print("exiting")
            """
        )
        start = time.time()
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertIn("exiting", out.stdout)
        self.assertLess(time.time() - start, 15)  # a ThreadPoolExecutor here would take ~60 s


if __name__ == "__main__":
    unittest.main()
