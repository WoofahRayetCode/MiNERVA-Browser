"""Background, latest-wins writer for the settings file.

``_save_settings()`` runs on the Tk thread after almost every action (navigation, filter
change, queue change).  Serialising the whole queue and fsync-ing it there stalls the UI as
soon as the queue is large.  The caller now only builds the settings dict (cheap) and hands it
here; a single worker thread writes the newest one.  A burst of 100 requests costs at most two
writes, and :meth:`flush` makes shutdown deterministic.
"""
from __future__ import annotations

import threading
from typing import Callable

from minerva.constants import log_error, save_app_settings


class AsyncSettingsWriter:
    def __init__(self, save_fn: Callable[[dict], object] = save_app_settings):
        self._save_fn = save_fn
        self._cond = threading.Condition()
        self._pending: dict | None = None
        self._busy = False
        self._closed = False
        self.writes = 0
        self._thread = threading.Thread(target=self._run, name="settings-writer", daemon=True)
        self._thread.start()

    def submit(self, settings: dict) -> None:
        with self._cond:
            if self._closed:
                self._write(settings)  # after close(): fall back to writing inline
                return
            self._pending = settings
            self._cond.notify()

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until everything submitted so far is on disk; False on timeout."""
        with self._cond:
            return self._cond.wait_for(lambda: self._pending is None and not self._busy, timeout)

    def close(self, timeout: float = 5.0) -> None:
        self.flush(timeout)
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._thread.join(timeout=1.0)

    def _write(self, settings: dict) -> None:
        try:
            self._save_fn(settings)
            self.writes += 1
        except Exception as e:  # pragma: no cover - save_app_settings already logs its own errors
            log_error("AsyncSettingsWriter write failed", e)

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._pending is not None or self._closed)
                if self._pending is None:
                    return  # closed and drained
                settings, self._pending = self._pending, None
                self._busy = True
            try:
                self._write(settings)
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()
