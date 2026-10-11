"""Process start-up: allow one running copy per data folder, then run the app.

The folder is claimed before the UI is imported, so starting a second copy never loads libtorrent or Tk.
"""
from __future__ import annotations

import sys

from minerva.constants import get_runtime_base_dir, log_activity
from minerva.core.single_instance import BUSY, SHOWN, claim

BUSY_MESSAGE = (
    "MiNERVA Archive Browser is already running from this folder:\n{folder}\n\n"
    "It did not answer a request to show itself (it may still be starting). Look for it in the "
    "taskbar or system tray. If it is stuck, end that process and start again."
)


def _tell_user_it_is_running(folder) -> None:
    message = BUSY_MESSAGE.format(folder=folder)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showinfo("MiNERVA Archive Browser", message, parent=root)
        finally:
            root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def _create_app():
    from minerva.ui.app import MinervaApp  # imported late: a second copy never needs the UI

    return MinervaApp()


def main(
    argv=None,
    *,
    folder=None,
    patience: float = 3.0,
    create_app=_create_app,
    notify=_tell_user_it_is_running,
    claim_folder=claim,
) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    folder = get_runtime_base_dir() if folder is None else folder
    # "--minimized" is how the login autostart starts the app: a duplicate of that must not pop a
    # window up or show a message, it just steps aside.
    background = "--minimized" in argv
    outcome, guard = claim_folder(
        folder,
        ask_running_copy_to_show=not background,
        patience=0.0 if background else patience,
    )
    if outcome == SHOWN:
        log_activity("app.second_copy: the running copy was asked to show its window")
        return 0
    if outcome == BUSY:
        log_activity(f"app.second_copy: already running from {folder}; not starting")
        if not background:
            notify(folder)
        return 0
    try:
        log_activity("app.launch")
        app = create_app()
        app.attach_single_instance(guard)
        log_activity("app.mainloop.start")
        app.mainloop()
        log_activity("app.mainloop.exit")
    finally:
        guard.release()
    return 0
