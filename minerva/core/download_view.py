"""Tk-free view model for the downloads list: formatting and row building.

The panel shows one Treeview row per download.  Everything that decides *what text a row
shows* lives here so it can be unit-tested (and benchmarked with thousands of rows) without
a display.  A row's ``values`` tuple matches :data:`COLUMNS`.
"""
from __future__ import annotations

from dataclasses import dataclass

COLUMNS = ("name", "size", "progress", "speed", "eta", "peers", "state")

KIND_ACTIVE = "active"
KIND_PENDING = "pending"
KIND_RETRY = "retry"
KIND_DONE = "done"
KIND_ERROR = "error"

FILTERS = ("all", "active", "queued", "done", "errors")
_FILTER_KINDS = {
    "all": None,
    "active": {KIND_ACTIVE},
    "queued": {KIND_PENDING, KIND_RETRY},
    "done": {KIND_DONE},
    "errors": {KIND_ERROR},
}

_FULL, _EMPTY = "█", "░"
DASH = "—"


@dataclass(frozen=True)
class Row:
    iid: str
    kind: str
    values: tuple
    tags: tuple = ()


def format_size(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return DASH
    if n <= 0:
        return DASH
    for unit, limit in (("B", 1024), ("KB", 1024 ** 2), ("MB", 1024 ** 3), ("GB", 1024 ** 4)):
        if n < limit:
            div = limit // 1024
            return f"{n} B" if unit == "B" else f"{n / div:.1f} {unit}"
    return f"{n / 1024 ** 4:.1f} TB"


def format_rate(bytes_per_second) -> str:
    try:
        r = float(bytes_per_second)
    except (TypeError, ValueError):
        return DASH
    if r <= 0:
        return DASH
    if r < 1024:
        return f"{r:.0f} B/s"
    if r < 1024 ** 2:
        return f"{r / 1024:.1f} KB/s"
    return f"{r / 1024 ** 2:.1f} MB/s"


def format_eta(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return DASH
    if s < 0:
        return DASH
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60:02d}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def progress_text(fraction, width: int = 10) -> str:
    """``████░░░░░░ 40%``"""
    try:
        f = max(0.0, min(1.0, float(fraction)))
    except (TypeError, ValueError):
        f = 0.0
    filled = int(f * width)
    return f"{_FULL * filled}{_EMPTY * (width - filled)} {int(f * 100):d}%"


def _active_row(item: dict, status: dict | None) -> Row:
    st = status or {}
    state = st.get("state") or "Starting"
    tag = KIND_ACTIVE
    if st.get("paused"):
        tag = "paused"
    if st.get("error"):
        tag = "problem"
        state = f"{state}: {st['error']}" if st["error"] not in state else state
    values = (
        "📄 " + (st.get("name") or item.get("name") or item["id"]),
        format_size(st.get("total")),
        progress_text(st.get("progress", 0.0)),
        format_rate(st.get("download_rate")),
        format_eta(st.get("eta", -1)),
        str(st.get("num_peers", 0)) if st else DASH,
        state,
    )
    return Row(item["id"], KIND_ACTIVE, values, (tag,))


def _pending_row(item: dict) -> Row:
    state = "Ready" if item.get("start_requested") else "Queued"
    return Row(item["id"], KIND_PENDING,
               ("🕐 " + item["name"], DASH, DASH, DASH, DASH, DASH, state), (KIND_PENDING,))


def _retry_row(item: dict, now: float) -> Row:
    wait = max(0, int(item.get("retry_at", now) - now))
    attempt = int(item.get("attempts", 1))
    reason = (item.get("last_error") or "").splitlines()[0][:60]
    state = f"Retry in {wait}s (attempt {attempt + 1})" + (f" – {reason}" if reason else "")
    return Row(item["id"], KIND_RETRY,
               ("🔁 " + item["name"], DASH, DASH, DASH, DASH, DASH, state), (KIND_RETRY,))


def _done_row(item: dict, extract: dict | None) -> Row:
    if item.get("status") == "done":
        info = extract or {}
        pct = info.get("pct")
        state = info.get("status") or "Done"
        progress = progress_text(pct / 100.0) if pct is not None and pct < 100 else progress_text(1.0)
        return Row(item["id"], KIND_DONE,
                   ("✅ " + item["name"], DASH, progress, DASH, DASH, DASH, state), (KIND_DONE,))
    error = (item.get("error") or "Failed").splitlines()[0]
    return Row(item["id"], KIND_ERROR,
               ("❌ " + item["name"], DASH, DASH, DASH, DASH, DASH, error), (KIND_ERROR,))


def build_row(kind: str, item: dict, status: dict | None = None, extract: dict | None = None,
              now: float = 0.0) -> Row:
    if kind == KIND_ACTIVE:
        return _active_row(item, status)
    if kind == KIND_PENDING:
        return _pending_row(item)
    if kind == KIND_RETRY:
        return _retry_row(item, now)
    return _done_row(item, extract)


def build_rows(snapshot: dict, statuses: dict, extract_progress: dict, now: float = 0.0) -> list[Row]:
    """All rows in display order: active, queued, retrying, then finished (newest first)."""
    rows: list[Row] = []
    active_items = snapshot.get("active_items")
    if active_items is None:  # older snapshot shape: only ids
        active_items = [{"id": did, "name": did} for did in snapshot.get("active", [])]
    for item in active_items:
        rows.append(_active_row(item, statuses.get(item["id"])))
    for item in snapshot.get("pending", []):
        rows.append(_pending_row(item))
    for item in snapshot.get("retry", []):
        rows.append(_retry_row(item, now))
    for item in reversed(snapshot.get("done", [])):
        rows.append(_done_row(item, extract_progress.get(item["id"])))
    return rows


def filter_rows(rows: list[Row], name: str) -> list[Row]:
    kinds = _FILTER_KINDS.get(name)
    if kinds is None:
        return rows
    return [r for r in rows if r.kind in kinds]


def count_by_filter(rows: list[Row]) -> dict[str, int]:
    counts = {name: 0 for name in FILTERS}
    counts["all"] = len(rows)
    for r in rows:
        for name, kinds in _FILTER_KINDS.items():
            if kinds is not None and r.kind in kinds:
                counts[name] += 1
    return counts
