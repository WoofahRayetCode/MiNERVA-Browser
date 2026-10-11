"""One running copy of the app per data folder.

The app keeps its settings, resume data, torrent cache and logs next to the executable, and two copies
sharing them overwrite each other's state.  The running copy holds an exclusive OS lock on ``minerva.lock``
in that folder for as long as it lives.  The OS drops the lock when the process ends, however it ends, so a
crash can never leave a stale lock behind.  A second copy that cannot take the lock asks the running one to
show its window (a small ``minerva.wake`` file the running copy polls) and exits.

The guard fails open: if the lock file cannot be created or the file system does not support locking, the
app starts anyway.  A problem with the guard must never stop the app from running.
"""
from __future__ import annotations

import errno
import os
import time
from pathlib import Path
from typing import Callable

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

from minerva.constants import log_activity

LOCK_NAME = "minerva.lock"
WAKE_NAME = "minerva.wake"
_BINARY = getattr(os, "O_BINARY", 0)  # Windows: os.open() is text mode unless asked

OWNER = "owner"  # this process holds the folder and may run
SHOWN = "shown"  # another copy runs and took our request to show its window
BUSY = "busy"    # another copy runs and did not answer

# errno values meaning "somebody else holds the lock" (as opposed to "locking does not work here")
_HELD = {
    errno.EACCES,
    errno.EAGAIN,
    errno.EWOULDBLOCK,
    getattr(errno, "EDEADLK", errno.EACCES),
    getattr(errno, "EDEADLOCK", errno.EACCES),
}


def _lock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        raise OSError(errno.ENOSYS, "no file locking on this platform")


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class SingleInstance:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.lock_path = self.folder / LOCK_NAME
        self.wake_path = self.folder / WAKE_NAME
        self.guarded = False  # True only while we really hold the lock (False after failing open)
        self._fd: int | None = None

    # -- the lock -------------------------------------------------------------------------------
    def acquire(self) -> bool:
        """True if this process may run: it now holds the folder, or locking is unavailable here.
        False if another copy holds the folder."""
        if self._fd is not None:
            return True
        try:
            # Python opens this non-inheritable, so helper programs (7-Zip, chdman, ...) never
            # inherit the lock and keep the folder "in use" after the app has exited.
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | _BINARY, 0o644)
        except OSError as e:
            log_activity(f"single_instance.unavailable: cannot open {self.lock_path}: {e}")
            return True
        try:
            _lock(fd)
        except OSError as e:
            os.close(fd)
            if e.errno in _HELD:
                return False
            log_activity(f"single_instance.unavailable: cannot lock {self.lock_path}: {e}")
            return True
        self._fd = fd
        self.guarded = True
        self._discard_request()  # a request left behind by a crashed run, or ours from waiting for it
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        self.guarded = False
        if fd is None:
            return
        try:
            _unlock(fd)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    # -- asking the running copy to show itself ---------------------------------------------------
    def request_show(self) -> bool:
        """A second copy asks the running one to bring its window to the front.
        False if the request could not be left (so nobody can answer it)."""
        try:
            fd = os.open(self.wake_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _BINARY, 0o644)
            try:
                os.write(fd, str(time.time()).encode("ascii"))
            finally:
                os.close(fd)
        except OSError as e:
            log_activity(f"single_instance.request_failed: {e}")
            return False
        return True

    def show_request_pending(self) -> bool:
        return self.wake_path.exists()

    def take_show_request(self) -> bool:
        """The running copy: True once per request (and clears it)."""
        try:
            os.unlink(self.wake_path)
        except OSError:  # nothing was asked, or the asker still has the file open (retry on the next poll)
            return False
        return True

    def _discard_request(self) -> None:
        try:
            os.unlink(self.wake_path)
        except OSError:
            pass


def claim(
    folder,
    *,
    ask_running_copy_to_show: bool = True,
    patience: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, SingleInstance]:
    """Take ``folder`` for this process, or deal with the copy that already has it.

    Returns ``(OWNER, guard)`` when this process may run (the caller must ``release()`` the guard at exit),
    ``(SHOWN, guard)`` when the running copy took our request to show its window, or ``(BUSY, guard)`` when
    it is running but did not answer within ``patience`` seconds.  While waiting, the lock is tried again,
    so a copy that is just closing (an update restarting the app) is replaced rather than refused.
    """
    guard = SingleInstance(folder)
    if guard.acquire():
        return OWNER, guard
    asked = ask_running_copy_to_show and guard.request_show()
    deadline = clock() + patience
    while clock() < deadline:
        sleep(0.1)
        if guard.acquire():
            return OWNER, guard
        if asked and not guard.show_request_pending():
            return SHOWN, guard
    return BUSY, guard
