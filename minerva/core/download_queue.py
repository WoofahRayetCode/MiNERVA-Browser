"""Download queue: pending -> active -> done, with retry/backoff state.

Public API is a superset of the original ``DownloadQueue`` that lived in ``torrent_engine``.
What changed for scale (thousands of items):

* name lookups (``has_name`` / ``find_by_name``) and "where is this id" are O(1) instead of
  scanning every list;
* starting the next item no longer scans ``pending``: a deque of startable ids is kept, and
  only rebuilt after a reorder;
* ``version`` bumps on every structural change so the UI can skip work when nothing changed,
  and ``queued_keys()`` is cached per version;
* ``reserve``/``release`` make "already queued?" an atomic check-and-set *before* a slow
  network lookup, closing the duplicate race;
* ``enqueue_many`` takes the lock once for a whole batch;
* failed items can wait out a backoff in ``retry`` state (``on_failed`` / ``tick``);
* the done list is capped so a long session cannot grow without bound.
"""
from __future__ import annotations

import pathlib
import threading
import time
from collections import deque
from typing import Callable, Iterable

DEFAULT_RETRY_DELAYS = (30.0, 120.0, 600.0)
DEFAULT_MAX_DONE = 500

_PENDING, _ACTIVE, _DONE, _RETRY = "pending", "active", "done", "retry"


class DownloadQueue:
    def __init__(
        self,
        engine,
        max_active: int = 3,
        *,
        key_fn: Callable[[str], Iterable[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_done: int = DEFAULT_MAX_DONE,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    ):
        self.engine = engine
        self.max_active = max_active
        self._key_fn = key_fn
        self._clock = clock
        self._max_done = max_done
        self._retry_delays = retry_delays
        self._pending: dict[str, dict] = {}
        self._active: dict[str, dict] = {}
        self._done: dict[str, dict] = {}
        self._retry: dict[str, dict] = {}
        self._where: dict[str, str] = {}
        self._by_name: dict[str, dict[str, None]] = {}
        self._reserved: set[str] = set()
        self._startable: deque[str] = deque()
        self._startable_dirty = False
        self._version = 0
        self._queued_keys_cache: tuple[int, frozenset[str]] | None = None
        self._lock = threading.Lock()

    # -- bookkeeping (call with the lock held) ---------------------------------------------------
    def _bump(self) -> None:
        self._version += 1

    def _store(self, where: str) -> dict[str, dict]:
        return {_PENDING: self._pending, _ACTIVE: self._active, _DONE: self._done, _RETRY: self._retry}[where]

    def _place(self, where: str, item: dict) -> None:
        did = item["id"]
        self._store(where)[did] = item
        self._where[did] = where
        name = item.get("name", "")
        if name:
            self._by_name.setdefault(name, {})[did] = None
            self._reserved.discard(name)  # the reservation has been converted into a real entry

    def _remove(self, did: str) -> dict | None:
        where = self._where.pop(did, None)
        if where is None:
            return None
        item = self._store(where).pop(did, None)
        if item is not None:
            name = item.get("name", "")
            ids = self._by_name.get(name)
            if ids is not None:
                ids.pop(did, None)
                if not ids:
                    del self._by_name[name]
        return item

    def _move_to(self, did: str, where: str) -> dict | None:
        item = self._remove(did)
        if item is not None:
            self._place(where, item)
        return item

    # -- enqueue ---------------------------------------------------------------------------------
    @staticmethod
    def _make_item(download_id: str, name: str, source: str, so_id: int, save_path: str,
                   start_requested: bool = False) -> dict:
        return {
            "id": download_id,
            "name": name,
            "source": source,
            "so_id": so_id,
            "save_path": str(pathlib.Path(save_path).resolve()),
            "start_requested": start_requested,
            "attempts": 0,
        }

    def enqueue(self, download_id: str, name: str, source: str, so_id: int, save_path: str):
        item = self._make_item(download_id, name, source, so_id, save_path)
        with self._lock:
            if self._where.get(download_id) not in (None, _PENDING):
                return  # never clobber an item that already started or finished
            self._remove(download_id)
            self._place(_PENDING, item)
            self._bump()

    def enqueue_many(self, items: Iterable[dict]) -> int:
        """Enqueue a batch under one lock acquisition; returns how many were added.

        Each item needs ``id``, ``name``, ``source``, ``so_id``, ``save_path`` and may set
        ``start_requested``.
        """
        added = 0
        with self._lock:
            for raw in items:
                did = raw["id"]
                if did in self._where:
                    continue
                item = self._make_item(did, raw["name"], raw["source"], int(raw["so_id"]),
                                       raw["save_path"], bool(raw.get("start_requested", False)))
                self._place(_PENDING, item)
                if item["start_requested"]:
                    self._startable.append(did)
                added += 1
            if added:
                self._bump()
        return added

    def reserve(self, name: str) -> bool:
        """Atomically claim ``name`` before a slow lookup; False if it is already queued/reserved."""
        with self._lock:
            if name in self._by_name or name in self._reserved:
                return False
            self._reserved.add(name)
            return True

    def release(self, name: str) -> None:
        with self._lock:
            self._reserved.discard(name)

    # -- starting --------------------------------------------------------------------------------
    def start_selected(self, download_ids: list[str]):
        with self._lock:
            changed = False
            for did in download_ids:
                item = self._pending.get(did)
                if item is not None and not item.get("start_requested"):
                    item["start_requested"] = True
                    self._startable.append(did)
                    changed = True
            if changed:
                self._bump()
        self._try_advance()

    def start_all_pending(self):
        with self._lock:
            changed = False
            for did, item in self._pending.items():
                if not item.get("start_requested"):
                    item["start_requested"] = True
                    self._startable.append(did)
                    changed = True
            if changed:
                self._bump()
        self._try_advance()

    def _ensure_startable(self) -> None:
        stale = len(self._startable) > 2 * len(self._pending) + 64
        if self._startable_dirty or stale:
            self._startable = deque(did for did, it in self._pending.items() if it.get("start_requested"))
            self._startable_dirty = False

    def _try_advance(self):
        to_start: list[dict] = []
        with self._lock:
            self._ensure_startable()
            while len(self._active) < self.max_active and self._startable:
                did = self._startable.popleft()
                item = self._pending.get(did)
                if item is None or not item.get("start_requested"):
                    continue  # cancelled or already moved since it was queued here
                self._move_to(did, _ACTIVE)
                to_start.append(item)
            if to_start:
                self._bump()
        if self.engine is None:
            return
        for item in to_start:
            self.engine.add_download(
                item["source"],
                item["so_id"],
                item["name"],
                item["save_path"],
                download_id=item["id"],
            )

    def set_max_active(self, n: int):
        self.max_active = max(1, n)
        self._try_advance()

    # -- finishing -------------------------------------------------------------------------------
    def _to_done(self, did: str, item: dict, error: str) -> None:
        done = {
            "id": did,
            "name": item["name"],
            "source": item.get("source", ""),
            "so_id": item.get("so_id", 0),
            "save_path": item["save_path"],
            "status": "error" if error else "done",
            "error": error,
        }
        self._place(_DONE, done)
        while len(self._done) > self._max_done:
            victim = next((k for k, v in self._done.items() if v.get("status") == "done"), None)
            if victim is None:
                victim = next(iter(self._done))
            self._remove(victim)

    def on_finished(self, download_id: str, error: str = ""):
        with self._lock:
            item = self._remove(download_id) if self._where.get(download_id) in (_ACTIVE, _PENDING, _RETRY) else None
            if item:
                self._to_done(download_id, item, error)
                self._bump()
        self._try_advance()

    def on_failed(self, download_id: str, error: str, retryable: bool = True) -> float | None:
        """Record a failure.  Returns the retry delay in seconds, or None if it is now final.

        Retryable failures release their slot and wait in ``retry`` state; ``tick`` re-queues
        them when the delay elapses.  After the last delay the item lands in ``done`` as an error.
        """
        delay: float | None = None
        with self._lock:
            where = self._where.get(download_id)
            if where in (_ACTIVE, _PENDING, _RETRY):
                item = self._remove(download_id)
                attempts = int(item.get("attempts", 0))
                if retryable and attempts < len(self._retry_delays):
                    delay = self._retry_delays[attempts]
                    item["attempts"] = attempts + 1
                    item["last_error"] = error
                    item["retry_at"] = self._clock() + delay
                    item["start_requested"] = True
                    self._place(_RETRY, item)
                else:
                    self._to_done(download_id, item, error)
                self._bump()
        self._try_advance()
        return delay

    def tick(self, now: float | None = None) -> list[str]:
        """Re-queue retry items whose backoff has elapsed (at the front); returns their ids."""
        now = self._clock() if now is None else now
        due: list[str] = []
        with self._lock:
            ready = [did for did, it in self._retry.items() if it.get("retry_at", 0.0) <= now]
            for did in reversed(ready):
                item = self._move_to(did, _PENDING)
                if item is None:
                    continue
                item.pop("retry_at", None)
                item["start_requested"] = True
                # Put it first in pending so it is not starved by the rest of the queue.
                rest = {k: v for k, v in self._pending.items() if k != did}
                self._pending = {did: item, **rest}
                self._startable.appendleft(did)
                due.append(did)
            if due:
                self._bump()
        if due:
            self._try_advance()
        due.reverse()
        return due

    def mark_error(self, download_id: str, error: str):
        with self._lock:
            item = self._done.get(download_id)
            if item:
                item["status"] = "error"
                item["error"] = error
                self._bump()

    def pop_done(self, download_id: str) -> dict | None:
        with self._lock:
            if self._where.get(download_id) != _DONE:
                return None
            item = self._remove(download_id)
            self._bump()
            return item

    def requeue_done(self, download_id: str, new_id: str, start: bool = True) -> dict | None:
        item = self.pop_done(download_id)
        if item is None:
            return None
        self.enqueue(
            new_id,
            item["name"],
            item.get("source", ""),
            int(item.get("so_id") or 0),
            item.get("save_path", ""),
        )
        if start:
            self.start_selected([new_id])
        return item

    def cancel(self, download_id: str):
        with self._lock:
            where = self._where.get(download_id)
            was_active = where == _ACTIVE
            if where in (_PENDING, _ACTIVE, _RETRY):
                self._remove(download_id)
                self._bump()
        if was_active and self.engine is not None:
            self.engine.remove_handle(download_id)
        self._try_advance()

    def clear_done(self):
        with self._lock:
            for did in list(self._done):
                self._remove(did)
            self._bump()

    # -- queries ---------------------------------------------------------------------------------
    @property
    def version(self) -> int:
        return self._version

    def has_name(self, name: str) -> bool:
        with self._lock:
            return name in self._by_name or name in self._reserved

    def find_by_name(self, name: str) -> dict | None:
        with self._lock:
            ids = self._by_name.get(name)
            if not ids:
                return None
            # Prefer live entries over finished ones, like the original list scan order.
            for where in (_PENDING, _ACTIVE, _RETRY, _DONE):
                for did in ids:
                    if self._where.get(did) == where:
                        return dict(self._store(where)[did])
            return None

    def in_progress_names(self) -> set[str]:
        with self._lock:
            return self._in_progress_names_locked()

    def _in_progress_names_locked(self) -> set[str]:
        names = {item.get("name", "") for item in self._pending.values()}
        names |= {item.get("name", "") for item in self._active.values()}
        names |= {item.get("name", "") for item in self._retry.values()}
        names.discard("")
        return names

    def queued_keys(self) -> frozenset[str]:
        """Match keys of everything pending/active/retrying; cached until the queue changes."""
        with self._lock:
            cached = self._queued_keys_cache
            if cached is not None and cached[0] == self._version:
                return cached[1]
            names = self._in_progress_names_locked()
            version = self._version
        keys: set[str] = set()
        for name in names:
            if self._key_fn is not None:
                keys |= set(self._key_fn(name))
            else:
                keys.add(name.lower())
        result = frozenset(keys)
        with self._lock:
            if version == self._version:
                self._queued_keys_cache = (version, result)
        return result

    def in_progress_items(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in (*self._pending.values(), *self._active.values(), *self._retry.values())]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "pending": list(self._pending.values()),
                "active": list(self._active.keys()),
                "active_items": list(self._active.values()),
                "done": list(self._done.values()),
                "retry": list(self._retry.values()),
                "version": self._version,
            }

    def export_for_persistence(self) -> list[dict]:
        with self._lock:
            items: list[dict] = []
            for item in self._active.values():
                items.append(self._persisted(item, True))
            for item in self._retry.values():
                items.append(self._persisted(item, True))
            for item in self._pending.values():
                items.append(self._persisted(item, bool(item.get("start_requested", False))))
            return items

    @staticmethod
    def _persisted(item: dict, start_requested: bool) -> dict:
        return {
            "id": item["id"],
            "name": item["name"],
            "source": item["source"],
            "so_id": item["so_id"],
            "save_path": item["save_path"],
            "start_requested": start_requested,
        }

    # -- ordering --------------------------------------------------------------------------------
    def move(self, download_ids: Iterable[str], where: str) -> None:
        """Reorder pending items: ``where`` is ``top``, ``bottom``, ``up`` or ``down``."""
        if where not in ("top", "bottom", "up", "down"):
            raise ValueError(f"unknown move target: {where!r}")
        wanted = set(download_ids)
        with self._lock:
            keys = list(self._pending.keys())
            chosen = [k for k in keys if k in wanted]
            if not chosen:
                return
            if where == "top":
                keys = chosen + [k for k in keys if k not in wanted]
            elif where == "bottom":
                keys = [k for k in keys if k not in wanted] + chosen
            elif where == "up":
                for k in chosen:
                    i = keys.index(k)
                    if i > 0 and keys[i - 1] not in wanted:
                        keys[i - 1], keys[i] = keys[i], keys[i - 1]
            else:
                for k in reversed(chosen):
                    i = keys.index(k)
                    if i < len(keys) - 1 and keys[i + 1] not in wanted:
                        keys[i + 1], keys[i] = keys[i], keys[i + 1]
            if keys == list(self._pending.keys()):
                return
            self._pending = {k: self._pending[k] for k in keys}
            self._startable_dirty = True
            self._bump()

    def move_up(self, download_id: str):
        self.move([download_id], "up")

    def move_down(self, download_id: str):
        self.move([download_id], "down")
