import errno
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from minerva.core import single_instance as si
from minerva.core.single_instance import BUSY, OWNER, SHOWN, SingleInstance, claim
from tests.fake_time import FakeTime

ROOT = Path(__file__).resolve().parents[1]

# A separate process that takes a folder, says so, then waits to be killed.
HOLDER = (
    "import sys, time\n"
    "from minerva.core.single_instance import SingleInstance\n"
    "g = SingleInstance(sys.argv[1])\n"
    "assert g.acquire() and g.guarded\n"
    "print('locked', flush=True)\n"
    "time.sleep(120)\n"
)


class FolderCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        patcher = mock.patch.object(si, "log_activity")
        self.log = patcher.start()
        self.addCleanup(patcher.stop)

    def guard(self, folder=None):
        guard = SingleInstance(folder or self.folder)
        self.addCleanup(guard.release)
        return guard


class TestLock(FolderCase):
    def test_the_first_copy_takes_the_folder_and_a_second_is_refused(self):
        first, second = self.guard(), self.guard()
        self.assertTrue(first.acquire())
        self.assertTrue(first.guarded)
        self.assertFalse(second.acquire())
        self.assertFalse(second.guarded)

    def test_the_folder_is_free_again_once_the_first_copy_lets_go(self):
        first, second = self.guard(), self.guard()
        first.acquire()
        first.release()
        self.assertFalse(first.guarded)
        self.assertTrue(second.acquire())

    def test_acquiring_twice_and_releasing_twice_is_harmless(self):
        guard = self.guard()
        self.assertTrue(guard.acquire())
        self.assertTrue(guard.acquire())
        guard.release()
        guard.release()
        SingleInstance(self.folder).release()  # never acquired

    def test_different_folders_do_not_get_in_each_others_way(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        first, second = self.guard(), self.guard(other.name)
        self.assertTrue(first.acquire())
        self.assertTrue(second.acquire())

    def test_the_lock_cannot_be_inherited_by_helper_programs(self):
        guard = self.guard()
        guard.acquire()
        self.assertFalse(os.get_inheritable(guard._fd))  # 7-Zip & co. must never keep the folder "in use"

    def test_a_copy_in_another_process_is_seen_and_a_crash_leaves_no_stale_lock(self):
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(self.folder)],
            cwd=ROOT, stdout=subprocess.PIPE, text=True,
        )
        watchdog = threading.Timer(30, holder.kill)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "locked")
        mine = self.guard()
        self.assertFalse(mine.acquire())  # held by the other process
        holder.kill()  # no chance to clean up, like a crash or a task-manager kill
        holder.wait(10)
        self.assertTrue(mine.acquire())
        self.assertTrue(mine.guarded)


class TestWindowsLocking(FolderCase):
    """The Windows branch (msvcrt) driven through a fake, so its wiring is checked on every platform."""

    def fake_msvcrt(self, error=None):
        fake = mock.Mock()
        fake.LK_NBLCK, fake.LK_UNLCK = 2, 0
        fake.locking.side_effect = error
        return fake

    def test_one_byte_at_the_start_of_the_file_is_locked_and_given_back(self):
        fake = self.fake_msvcrt()
        guard = self.guard()
        with mock.patch.object(si, "fcntl", None), mock.patch.object(si, "msvcrt", fake):
            self.assertTrue(guard.acquire())
            self.assertTrue(guard.guarded)
            fd = guard._fd
            fake.locking.assert_called_once_with(fd, fake.LK_NBLCK, 1)
            guard.release()
        self.assertEqual(fake.locking.call_args_list[-1].args, (fd, fake.LK_UNLCK, 1))

    def test_a_refused_lock_means_a_second_copy_whichever_error_windows_uses(self):
        for code in (errno.EACCES, getattr(errno, "EDEADLOCK", errno.EACCES)):
            fake = self.fake_msvcrt(OSError(code, "locked"))
            with mock.patch.object(si, "fcntl", None), mock.patch.object(si, "msvcrt", fake):
                self.assertFalse(self.guard().acquire())

    def test_a_platform_with_no_locking_at_all_lets_the_app_run(self):
        guard = self.guard()
        with mock.patch.object(si, "fcntl", None), mock.patch.object(si, "msvcrt", None):
            self.assertTrue(guard.acquire())
        self.assertFalse(guard.guarded)


class TestFailsOpen(FolderCase):
    """A problem with the guard itself must never stop the app from starting."""

    def test_an_unwritable_or_missing_folder_lets_the_app_run_unguarded(self):
        for error in (PermissionError(errno.EACCES, "read-only"), FileNotFoundError(errno.ENOENT, "gone")):
            guard = self.guard()
            with mock.patch.object(si.os, "open", side_effect=error):
                self.assertTrue(guard.acquire())
            self.assertFalse(guard.guarded)

    def test_a_file_system_without_locking_lets_the_app_run_unguarded(self):
        guard = self.guard()
        with mock.patch.object(si, "_lock", side_effect=OSError(errno.ENOLCK, "no locks available")), \
                mock.patch.object(si.os, "close", wraps=os.close) as close:
            self.assertTrue(guard.acquire())
        self.assertFalse(guard.guarded)
        close.assert_called_once()  # the file descriptor is not leaked
        self.log.assert_called()

    def test_only_a_lock_held_by_somebody_else_counts_as_a_second_copy(self):
        for code in (errno.EWOULDBLOCK, errno.EACCES, errno.EAGAIN):
            guard = self.guard()
            with mock.patch.object(si, "_lock", side_effect=OSError(code, "held")):
                self.assertFalse(guard.acquire())


class TestShowRequests(FolderCase):
    def test_a_request_is_taken_exactly_once(self):
        runner, second = self.guard(), self.guard()
        runner.acquire()
        self.assertFalse(runner.take_show_request())  # nobody asked
        second.request_show()
        self.assertTrue(second.show_request_pending())
        self.assertTrue(runner.take_show_request())
        self.assertFalse(second.show_request_pending())
        self.assertFalse(runner.take_show_request())

    def test_a_request_left_by_a_crashed_run_is_discarded_at_start(self):
        SingleInstance(self.folder).request_show()
        guard = self.guard()
        guard.acquire()
        self.assertFalse(guard.show_request_pending())  # it must not pop a window up at startup

    def test_a_request_that_cannot_be_cleared_yet_is_retried_later(self):
        guard = self.guard()
        guard.request_show()
        with mock.patch.object(si.os, "unlink", side_effect=PermissionError(errno.EACCES, "still open")):
            self.assertFalse(guard.take_show_request())
        self.assertTrue(guard.take_show_request())  # the next poll gets it

    def test_a_request_that_cannot_be_written_is_logged_not_raised_and_reported(self):
        guard = self.guard()
        with mock.patch.object(si.os, "open", side_effect=PermissionError(errno.EACCES, "read-only")):
            self.assertFalse(guard.request_show())
        self.log.assert_called_once()
        self.assertTrue(guard.request_show())


class TestClaim(FolderCase):
    def test_a_free_folder_is_ours(self):
        outcome, guard = claim(self.folder)
        self.addCleanup(guard.release)
        self.assertEqual(outcome, OWNER)
        self.assertTrue(guard.guarded)

    def test_the_running_copy_is_asked_to_show_itself_and_we_step_aside_when_it_answers(self):
        runner = self.guard()
        runner.acquire()
        time = FakeTime(on_sleep=lambda n: runner.take_show_request() if n == 3 else None)
        outcome, guard = claim(self.folder, patience=3.0, sleep=time.sleep, clock=time.clock)
        self.assertEqual(outcome, SHOWN)
        self.assertFalse(guard.guarded)
        self.assertEqual(time.sleeps, 3)

    def test_a_running_copy_that_never_answers_is_reported_busy_after_the_patience_runs_out(self):
        runner = self.guard()
        runner.acquire()
        time = FakeTime()
        outcome, guard = claim(self.folder, patience=3.0, sleep=time.sleep, clock=time.clock)
        self.assertEqual(outcome, BUSY)
        self.assertGreaterEqual(time.now, 3.0)
        self.assertTrue(runner.show_request_pending())  # if it recovers it will still come to the front

    def test_a_copy_that_is_just_closing_is_replaced_not_refused(self):
        runner = self.guard()
        runner.acquire()
        time = FakeTime(on_sleep=lambda n: runner.release() if n == 2 else None)  # e.g. an update restarting the app
        outcome, guard = claim(self.folder, patience=3.0, sleep=time.sleep, clock=time.clock)
        self.addCleanup(guard.release)
        self.assertEqual(outcome, OWNER)
        self.assertTrue(guard.guarded)
        self.assertFalse(guard.show_request_pending())  # our own request is not left behind to pop up later

    def test_when_the_request_cannot_even_be_left_it_is_not_mistaken_for_an_answer(self):
        runner = self.guard()
        runner.acquire()
        time = FakeTime()
        real_open = os.open

        def refuse_the_wake_file(path, *args, **kw):
            if str(path).endswith(si.WAKE_NAME):
                raise PermissionError(errno.EACCES, "cannot create files here")
            return real_open(path, *args, **kw)

        with mock.patch.object(si.os, "open", side_effect=refuse_the_wake_file):
            outcome, _guard = claim(self.folder, patience=1.0, sleep=time.sleep, clock=time.clock)
        self.assertEqual(outcome, BUSY)  # not SHOWN: nobody took anything

    def test_a_background_start_steps_aside_at_once_without_asking_for_anything(self):
        runner = self.guard()
        runner.acquire()
        time = FakeTime()
        outcome, _guard = claim(
            self.folder, ask_running_copy_to_show=False, patience=0.0, sleep=time.sleep, clock=time.clock)
        self.assertEqual(outcome, BUSY)
        self.assertEqual(time.sleeps, 0)
        self.assertFalse(runner.show_request_pending())


if __name__ == "__main__":
    unittest.main()
