"""On-disk persistence of libtorrent resume data and session (DHT) state.

Resume blobs let a restart skip re-hashing and keep tracker/peer knowledge; the DHT state
makes the first peer lookups after launch fast.  Everything is written atomically (a crash
mid-write leaves the previous blob intact), and a corrupt blob is dropped rather than trusted.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from minerva.constants import log_error
from minerva.core.http import atomic_write_bytes

_HASH = re.compile(r"^[0-9a-fA-F]{40,64}$")


class ResumeStore:
    SESSION_FILE = "session.state"

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, info_hash: str) -> Path:
        if not _HASH.match(info_hash):
            raise ValueError(f"not an info-hash: {info_hash!r}")
        return self.directory / f"{info_hash.lower()}.fastresume"

    def load(self, info_hash: str) -> bytes | None:
        try:
            data = self._path(info_hash).read_bytes()
        except (FileNotFoundError, ValueError):
            return None
        except OSError as e:
            log_error(f"ResumeStore.load failed for {info_hash}", e)
            return None
        return data or None

    def save(self, info_hash: str, data: bytes) -> bool:
        if not data:
            return False
        try:
            atomic_write_bytes(self._path(info_hash), data)
            return True
        except (OSError, ValueError) as e:
            log_error(f"ResumeStore.save failed for {info_hash}", e)
            return False

    def delete(self, info_hash: str) -> None:
        try:
            self._path(info_hash).unlink()
        except (FileNotFoundError, ValueError):
            pass
        except OSError as e:
            log_error(f"ResumeStore.delete failed for {info_hash}", e)

    def load_session_state(self) -> bytes | None:
        try:
            return (self.directory / self.SESSION_FILE).read_bytes() or None
        except OSError:
            return None

    def save_session_state(self, data: bytes) -> bool:
        try:
            atomic_write_bytes(self.directory / self.SESSION_FILE, data)
            return True
        except OSError as e:
            log_error("ResumeStore.save_session_state failed", e)
            return False

    def prune(self, max_age_days: float = 30.0, *, now: float | None = None) -> int:
        """Delete resume blobs (and stray ``.part`` files) nobody has touched for a long time."""
        cutoff = (time.time() if now is None else now) - max_age_days * 86400
        removed = 0
        try:
            for path in self.directory.iterdir():
                if path.suffix not in (".fastresume", ".part"):
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        return removed
