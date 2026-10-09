"""A tiny thread pool whose workers are daemon threads.

``concurrent.futures.ThreadPoolExecutor`` joins its (non-daemon) workers when the interpreter
exits, so closing the window while a lookup or torrent fetch is mid-request would hang until
that request times out.  These workers never block exit; ``shutdown`` also drops queued work.
"""
from __future__ import annotations

import queue
import threading

from minerva.constants import log_error


class DaemonPool:
    def __init__(self, workers: int, name: str = "worker"):
        self._workers = max(1, int(workers))
        self._name = name
        self._jobs: queue.Queue = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, fn, *args, **kwargs) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("pool is shut down")
            if len(self._threads) < self._workers:
                thread = threading.Thread(
                    target=self._run, name=f"{self._name}-{len(self._threads)}", daemon=True
                )
                self._threads.append(thread)
                thread.start()
            self._jobs.put((fn, args, kwargs))

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            fn, args, kwargs = job
            try:
                fn(*args, **kwargs)
            except Exception as e:  # a failing job must never kill its worker
                log_error(f"DaemonPool job failed in {self._name}", e)

    def shutdown(self, cancel_pending: bool = True) -> None:
        with self._lock:
            self._closed = True
            if cancel_pending:
                try:
                    while True:
                        self._jobs.get_nowait()
                except queue.Empty:
                    pass
            for _ in self._threads:
                self._jobs.put(None)
