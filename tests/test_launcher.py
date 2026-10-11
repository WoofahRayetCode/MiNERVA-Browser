import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from minerva import launcher
from minerva.core import single_instance as si
from minerva.core.single_instance import SingleInstance, claim
from tests.fake_time import FakeTime


class FakeApp:
    def __init__(self, folder, crash=None):
        self.folder = folder
        self.crash = crash
        self.guard = None
        self.ran = False
        self.guard_held_while_running = None
        self.others_were_kept_out = None

    def attach_single_instance(self, guard):
        self.guard = guard

    def mainloop(self):
        self.ran = True
        self.guard_held_while_running = self.guard.guarded
        other = SingleInstance(self.folder)
        self.others_were_kept_out = not other.acquire()  # while we run, nobody else can take the folder
        other.release()
        if self.crash:
            raise self.crash


class TestLauncher(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        for target in (launcher, si):
            patcher = mock.patch.object(target, "log_activity")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.apps, self.notes = [], []
        self.time = FakeTime()

    def create_app(self, crash=None):
        app = FakeApp(self.folder, crash)
        self.apps.append(app)
        return app

    def run_main(self, argv=(), *, create_app=None, **kw):
        # waiting costs no real time: the claim uses the fake clock
        claim_folder = lambda folder, **ckw: claim(folder, sleep=self.time.sleep, clock=self.time.clock, **ckw)  # noqa: E731
        return launcher.main(
            list(argv), folder=self.folder, create_app=create_app or self.create_app,
            notify=self.notes.append, claim_folder=claim_folder, **kw,
        )

    def running_copy(self):
        runner = SingleInstance(self.folder)
        self.addCleanup(runner.release)
        self.assertTrue(runner.acquire())
        return runner

    def test_a_first_start_runs_the_app_holding_the_folder_and_frees_it_afterwards(self):
        self.assertEqual(self.run_main(), 0)
        (app,) = self.apps
        self.assertTrue(app.ran)
        self.assertTrue(app.others_were_kept_out)
        self.assertTrue(app.guard_held_while_running)  # the app was handed the lock to hold for its whole life
        self.assertFalse(app.guard.guarded)  # and it was let go once the app exited
        other = SingleInstance(self.folder)
        self.addCleanup(other.release)
        self.assertTrue(other.acquire() and other.guarded)  # released once the app has exited

    def test_the_folder_is_freed_even_if_the_app_crashes(self):
        with self.assertRaises(RuntimeError):
            self.run_main(create_app=lambda: self.create_app(crash=RuntimeError("boom")))
        other = SingleInstance(self.folder)
        self.addCleanup(other.release)
        self.assertTrue(other.acquire() and other.guarded)

    def test_a_second_start_asks_the_running_copy_to_show_itself_and_never_builds_the_ui(self):
        runner = self.running_copy()
        self.time.on_sleep = lambda n: runner.take_show_request() if n == 2 else None
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.apps, [])  # no second window, no second engine
        self.assertEqual(self.notes, [])  # and no message: the running copy's window coming up is the answer

    def test_a_second_start_that_gets_no_answer_says_so(self):
        self.running_copy()
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.apps, [])
        self.assertEqual(self.notes, [self.folder])

    def test_a_second_start_replaces_a_copy_that_is_just_closing(self):
        runner = self.running_copy()
        self.time.on_sleep = lambda n: runner.release() if n == 2 else None
        self.assertEqual(self.run_main(), 0)
        (app,) = self.apps
        self.assertTrue(app.ran)  # started normally once the old copy was gone
        self.assertEqual(self.notes, [])

    def test_a_login_autostart_that_finds_a_copy_running_steps_aside_silently(self):
        runner = self.running_copy()
        self.assertEqual(self.run_main(["--minimized"]), 0)
        self.assertEqual(self.apps, [])
        self.assertEqual(self.notes, [])  # no message at login
        self.assertFalse(runner.show_request_pending())  # and the running window is not dragged to the front
        self.assertEqual(self.time.sleeps, 0)

    def test_a_login_autostart_on_a_free_folder_runs_normally(self):
        self.assertEqual(self.run_main(["--minimized"]), 0)
        self.assertTrue(self.apps[0].ran)


class TestMessage(unittest.TestCase):
    def test_the_message_names_the_folder(self):
        self.assertIn("/some/folder", launcher.BUSY_MESSAGE.format(folder="/some/folder"))

    def test_without_a_display_the_message_goes_to_stderr(self):
        err = io.StringIO()
        with mock.patch("tkinter.Tk", side_effect=RuntimeError("no display")), contextlib.redirect_stderr(err):
            launcher._tell_user_it_is_running("/some/folder")
        self.assertIn("already running from this folder", err.getvalue())
        self.assertIn("/some/folder", err.getvalue())


if __name__ == "__main__":
    unittest.main()
