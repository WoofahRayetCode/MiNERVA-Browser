"""Helpers for modal dialogs."""
from __future__ import annotations

import tkinter as tk

from minerva.constants import log_activity

GRAB_RETRY_MS = 150
GRAB_RETRIES = 3


def _attempt_grab(win, retries_left: int) -> None:
    try:
        if not win.winfo_exists():
            return
        win.grab_set()
        return
    except tk.TclError as e:
        error = e
    if retries_left > 0:
        try:
            win.after(GRAB_RETRY_MS, lambda: _attempt_grab(win, retries_left - 1))
            return
        except tk.TclError:
            return
    log_activity(f"modal.grab_failed: {error}")  # the dialog still works, it is just not modal


def grab_when_mapped(win) -> None:
    """Make ``win`` modal once it is on screen.  Never raises and never blocks.

    A grab on a window that is not mapped yet can fail ("grab failed: window not viewable"), for example
    while a mouse button is held down.  Done right after creating a dialog, that used to abort the dialog
    half way and leave an empty window behind.  Tk's own dialogs wait for the window to be visible first;
    this does the same without a blocking wait (which could hang for a window that is never shown), and
    retries briefly if the grab is refused.
    """
    try:
        viewable = bool(win.winfo_viewable())
    except tk.TclError:
        return  # the window is already gone
    if viewable:
        _attempt_grab(win, GRAB_RETRIES)
        return
    binding: dict[str, str] = {}

    def on_map(event) -> None:
        if event.widget is not win:
            return  # a child widget being mapped
        try:
            win.unbind("<Map>", binding["id"])
        except (tk.TclError, KeyError):
            pass
        _attempt_grab(win, GRAB_RETRIES)

    binding["id"] = win.bind("<Map>", on_map, add="+")
