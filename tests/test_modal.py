import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest import mock

from minerva.ui.components import modal


class FakeWindow:
    """Just enough of a Toplevel to drive grab_when_mapped without a window manager."""

    def __init__(self, viewable=False, refuse=0):
        self.viewable = viewable
        self.refuse = refuse        # the first N grab_set() calls raise TclError, like a refused grab
        self.attempts = 0
        self.grabbed = 0
        self.exists = True
        self.bindings = {}
        self.scheduled = []

    def winfo_viewable(self):
        if self.viewable is None:
            raise tk.TclError("bad window path name")
        return self.viewable

    def winfo_exists(self):
        return self.exists

    def grab_set(self):
        self.attempts += 1
        if self.attempts <= self.refuse:
            raise tk.TclError("grab failed: window not viewable")
        self.grabbed += 1

    def bind(self, sequence, func, add=None):
        funcid = f"bind{len(self.bindings)}"
        self.bindings[funcid] = (sequence, func)
        return funcid

    def unbind(self, sequence, funcid):
        self.bindings.pop(funcid, None)

    def after(self, ms, func):
        self.scheduled.append((ms, func))

    # test helpers
    def map(self, widget=None):
        event = SimpleNamespace(widget=self if widget is None else widget)
        for sequence, func in list(self.bindings.values()):
            if sequence == "<Map>":
                func(event)

    def run_scheduled(self):
        due, self.scheduled = self.scheduled, []
        for _ms, func in due:
            func()


class TestGrabWhenMapped(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(modal, "log_activity")
        self.log = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_window_that_is_already_on_screen_is_grabbed_at_once(self):
        win = FakeWindow(viewable=True)
        modal.grab_when_mapped(win)
        self.assertEqual(win.grabbed, 1)
        self.assertEqual(win.bindings, {})

    def test_a_window_that_is_not_on_screen_yet_is_grabbed_when_it_is_mapped(self):
        win = FakeWindow(viewable=False)
        modal.grab_when_mapped(win)
        self.assertEqual(win.attempts, 0)  # nothing is attempted while the window cannot be grabbed
        win.map()
        self.assertEqual(win.grabbed, 1)

    def test_only_the_window_itself_counts_not_its_children(self):
        win = FakeWindow(viewable=False)
        modal.grab_when_mapped(win)
        win.map(widget=object())
        self.assertEqual(win.attempts, 0)
        win.map()
        self.assertEqual(win.grabbed, 1)

    def test_it_fires_only_once(self):
        win = FakeWindow(viewable=False)
        modal.grab_when_mapped(win)
        win.map()
        win.map()
        self.assertEqual(win.attempts, 1)
        self.assertEqual(win.bindings, {})

    def test_a_refused_grab_is_retried_shortly_and_does_not_raise(self):
        win = FakeWindow(viewable=True, refuse=1)
        modal.grab_when_mapped(win)
        self.assertEqual(win.grabbed, 0)
        self.assertEqual([ms for ms, _ in win.scheduled], [modal.GRAB_RETRY_MS])
        win.run_scheduled()
        self.assertEqual(win.grabbed, 1)
        self.log.assert_not_called()

    def test_a_grab_that_keeps_being_refused_gives_up_quietly_and_logs_it(self):
        win = FakeWindow(viewable=True, refuse=99)
        modal.grab_when_mapped(win)
        for _ in range(10):
            win.run_scheduled()
        self.assertEqual(win.attempts, 1 + modal.GRAB_RETRIES)
        self.assertEqual(win.grabbed, 0)
        self.log.assert_called_once()
        self.assertIn("grab failed", self.log.call_args[0][0])

    def test_a_window_destroyed_before_the_retry_is_left_alone(self):
        win = FakeWindow(viewable=True, refuse=1)
        modal.grab_when_mapped(win)
        win.exists = False
        win.run_scheduled()
        self.assertEqual(win.attempts, 1)
        self.log.assert_not_called()

    def test_a_window_that_is_already_gone_is_ignored(self):
        modal.grab_when_mapped(FakeWindow(viewable=None))  # must not raise


if __name__ == "__main__":
    unittest.main()
