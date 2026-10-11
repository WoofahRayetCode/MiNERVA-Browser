"""The DLC/updates prompt must never be left on screen half built (an empty window), whatever goes wrong."""
import tkinter as tk
import unittest
from tkinter import ttk
from unittest import mock

from minerva.core.companions import KIND_DLC, KIND_UPDATE, Companion
from minerva.ui.components import companion_dialog, modal
from minerva.ui.components.companion_dialog import prompt_companions
from minerva.ui.components.tools_dialog import ToolsDialog
from minerva.ui.theme import setup_modern_styles


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def toplevels(root):
    return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]


def labels(dlg):
    return [w.cget("text") for w in descendants(dlg) if isinstance(w, tk.Label)]


def button(dlg, text):
    return next(w for w in descendants(dlg) if isinstance(w, ttk.Button) and w.cget("text") == text)


ITEMS = [
    Companion("Game (USA) (v1.04) (Update).zip", "/rom?id=1", "120 MB", KIND_UPDATE, "Updates"),
    Companion("Game (USA) (v1.05) (Update).zip", "/rom?id=2", "130 MB", KIND_UPDATE, "Updates"),
    Companion("Game (USA) (DLC Pack).zip", "/rom?id=3", "40 MB", KIND_DLC, "DLC"),
]


class TestCompanionDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"no display available: {exc}")
        cls.root.withdraw()
        setup_modern_styles(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        log = mock.patch.object(modal, "log_activity")
        log.start()
        self.addCleanup(log.stop)
        # If a test's driver never finds the dialog, do not hang the whole run.
        self._watchdog = self.root.after(8000, self._close_everything)
        self.addCleanup(lambda: self.root.after_cancel(self._watchdog))
        self.addCleanup(self._close_everything)

    def _close_everything(self):
        for w in toplevels(self.root):
            w.destroy()

    def drive(self, action):
        """Run ``action(dialog)`` once the dialog exists; the call to prompt_companions blocks until it closes."""
        def run():
            (dlg,) = toplevels(self.root)
            action(dlg)
        self.root.after(50, run)

    def test_every_item_is_listed_and_download_selected_returns_the_checked_ones(self):
        seen = {}

        def action(dlg):
            seen["labels"] = labels(dlg)
            button(dlg, "Download selected").invoke()

        self.drive(action)
        picked = prompt_companions(self.root, "Game (USA).zip", ITEMS)
        shown = "\n".join(seen["labels"])
        self.assertIn("Found extra content for:\nGame (USA).zip", shown)
        for item in ITEMS:
            self.assertIn(item.name, shown)
        self.assertEqual([i.name for i in picked], [ITEMS[0].name, ITEMS[1].name])  # updates are pre-checked, DLC is not

    def test_download_all_and_skip(self):
        self.drive(lambda dlg: button(dlg, "Download all").invoke())
        self.assertEqual(prompt_companions(self.root, "Game", ITEMS), ITEMS)
        self.drive(lambda dlg: button(dlg, "Skip").invoke())
        self.assertEqual(prompt_companions(self.root, "Game", ITEMS), [])

    def test_closing_the_window_returns_none(self):
        self.drive(lambda dlg: dlg.tk.call(dlg.protocol("WM_DELETE_WINDOW")))
        self.assertIsNone(prompt_companions(self.root, "Game", ITEMS))

    def test_a_refused_grab_does_not_leave_the_dialog_empty(self):
        seen = {}

        def action(dlg):
            seen["labels"] = labels(dlg)
            button(dlg, "Download all").invoke()

        self.drive(action)
        # The window counts as on screen, so the grab is attempted at once - and refused.
        with mock.patch.object(tk.Toplevel, "winfo_viewable", return_value=True), \
                mock.patch.object(tk.Toplevel, "grab_set", side_effect=tk.TclError("grab failed: window not viewable")):
            picked = prompt_companions(self.root, "Game (USA).zip", ITEMS)
        self.assertEqual(picked, ITEMS)
        self.assertTrue(any("Found extra content for" in text for text in seen["labels"]))

    def test_a_failure_while_building_leaves_no_window_behind(self):
        with mock.patch.object(companion_dialog, "_row", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                prompt_companions(self.root, "Game", ITEMS)
        self.root.update()
        self.assertEqual(toplevels(self.root), [])

    def test_the_window_stays_withdrawn_until_its_content_exists(self):
        states = []
        real_row = companion_dialog._row

        def spying_row(parent, item, var):
            states.append(parent.winfo_toplevel().state())
            return real_row(parent, item, var)

        self.drive(lambda dlg: button(dlg, "Skip").invoke())
        with mock.patch.object(companion_dialog, "_row", side_effect=spying_row):
            prompt_companions(self.root, "Game", ITEMS)
        self.assertEqual(states, ["withdrawn"] * len(ITEMS))


class TestToolsDialogGrab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"no display available: {exc}")
        cls.root.withdraw()
        setup_modern_styles(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def test_the_tools_dialog_is_still_built_when_the_grab_is_refused(self):
        with mock.patch.object(modal, "log_activity"), \
                mock.patch.object(tk.Toplevel, "winfo_viewable", return_value=True), \
                mock.patch.object(tk.Toplevel, "grab_set", side_effect=tk.TclError("grab failed: window not viewable")):
            dlg = ToolsDialog(self.root, {}, "7-Zip", "chdman")
        try:
            self.assertTrue(list(descendants(dlg)), "the dialog must have its content")
        finally:
            dlg.destroy()


if __name__ == "__main__":
    unittest.main()
