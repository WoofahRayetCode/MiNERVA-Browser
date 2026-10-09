"""Small dependency-free HTTP helpers: retries with backoff, a shared rate gate, atomic writes.

Every network call in the app used to be a single ``urllib`` attempt, so one dropped
packet, a 503 or a 429 turned into a failed download and a modal dialog.  ``get_bytes``
retries transient failures with full-jitter exponential backoff, honours ``Retry-After``
and never retries errors that cannot succeed (401/403/404).
"""
from __future__ import annotations

import email.utils
import http.client
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Mapping

from minerva.constants import replace_with_retry

USER_AGENT = "MiNERVA-Browser/1.0"
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 120.0
_CHUNK = 1024 * 1024


class HttpError(Exception):
    """A request failed for good (or exhausted its retries)."""

    def __init__(self, message: str, *, status: int | None = None, url: str = "", retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.url = url
        self.retryable = retryable


class RateGate:
    """Spaces requests out across threads and lets a 429 slow everyone down at once."""

    def __init__(self, rate_per_sec: float = 5.0, *, clock=time.monotonic, sleep=time.sleep):
        self._interval = 1.0 / rate_per_sec if rate_per_sec and rate_per_sec > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._penalty_until = 0.0

    def wait(self, cancel: threading.Event | None = None) -> None:
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot, self._penalty_until)
            self._next_slot = slot + self._interval
        delay = slot - now
        if delay > 0:
            if cancel is not None:
                cancel.wait(delay)
            else:
                self._sleep(delay)

    def penalize(self, seconds: float) -> None:
        """Hold back *all* callers for ``seconds`` (capped); used on 429/503 + Retry-After."""
        seconds = max(0.0, min(float(seconds), MAX_RETRY_AFTER))
        with self._lock:
            self._penalty_until = max(self._penalty_until, self._clock() + seconds)


#: Shared by every request to minerva-archive.org (listings, ROM pages, .torrent files) so a
#: bulk queue cannot hammer the server and a 429 slows all of them down together.
SITE_GATE = RateGate(rate_per_sec=10.0)


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(float(value), MAX_RETRY_AFTER)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    current = time.time() if now is None else now
    return max(0.0, min(when.timestamp() - current, MAX_RETRY_AFTER))


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 30.0, rng: Callable[[], float] = random.random) -> float:
    """Full-jitter exponential backoff: uniform in [0, min(cap, base * 2**attempt)]."""
    return rng() * min(cap, base * (2 ** attempt))


def _read_limited(resp, max_bytes: int | None, url: str) -> bytes:
    if max_bytes is None:
        return resp.read()
    length = resp.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise HttpError(f"Response too large ({length} bytes)", url=url)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HttpError(f"Response exceeded {max_bytes} bytes", url=url)
        chunks.append(chunk)
    return b"".join(chunks)


def get_bytes(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 4,
    max_bytes: int | None = None,
    gate: RateGate | None = None,
    cancel: threading.Event | None = None,
    opener=urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> bytes:
    """GET ``url`` and return the body, retrying transient failures.

    Raises :class:`HttpError`; ``retryable`` tells the caller whether trying again later
    could help (the retries here were already exhausted).
    """
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    attempt = 0
    while True:
        if cancel is not None and cancel.is_set():
            raise HttpError("Cancelled", url=url)
        if gate is not None:
            gate.wait(cancel)
        retry_after: float | None = None
        try:
            req = urllib.request.Request(url, headers=request_headers)
            with opener(req, timeout=timeout) as resp:
                data = _read_limited(resp, max_bytes, url)
                expected = resp.headers.get("Content-Length")
                if expected and expected.isdigit() and len(data) != int(expected):
                    raise http.client.IncompleteRead(data, int(expected) - len(data))
                return data
        except urllib.error.HTTPError as e:
            status = e.code
            retry_after = parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
            try:
                e.close()
            except Exception:
                pass
            retryable = status in RETRYABLE_STATUS
            if not retryable or attempt >= retries:
                raise HttpError(f"HTTP {status} for {url}", status=status, url=url, retryable=retryable) from e
        except HttpError:
            raise
        except (OSError, http.client.HTTPException) as e:
            # URLError, timeouts, connection resets, SSL errors, truncated bodies.
            if attempt >= retries:
                reason = getattr(e, "reason", None) or e
                raise HttpError(f"{reason} ({url})", url=url, retryable=True) from e
        delay = backoff_delay(attempt, rng=rng)
        if retry_after:
            delay = max(delay, retry_after)
            if gate is not None:
                gate.penalize(retry_after)
        if cancel is not None:
            cancel.wait(delay)
        else:
            sleep(delay)
        attempt += 1


def atomic_write_bytes(dest: Path, data: bytes, validate: Callable[[bytes], None] | None = None) -> None:
    """Write ``data`` to ``dest`` so readers never see a partial file.

    ``validate`` (raising ``ValueError``) runs before anything touches the destination, so a
    bad download can never replace a good cached copy.
    """
    if validate is not None:
        validate(data)
    dest = Path(dest)
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def fetch_to_file(url: str, dest: Path, validate: Callable[[bytes], None] | None = None, **get_kwargs) -> Path:
    data = get_bytes(url, **get_kwargs)
    atomic_write_bytes(dest, data, validate)
    return Path(dest)
