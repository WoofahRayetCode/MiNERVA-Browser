"""The downloads list: one ``ttk.Treeview`` instead of a widget group per download.

The previous panel built ~8 Tk widgets per queued/finished item (2000 items froze the UI for
~5 s on first layout and cost ~300 ms of repaint per tick).  A Treeview keeps cost
proportional to what is *visible*, gives wheel/keyboard/multi-select for free, and lets a
row move between sections with a single ``move`` call.

Updates are split in two so a tick stays cheap with thousands of rows:

* **structural** - rows added/removed/reordered; only runs when ``snapshot["version"]`` or the
  filter changed;
* **dynamic** - text of rows that change on their own (active transfers, retry countdowns,
  extraction progress); only those rows are touched.
"""
from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Callable

from minerva.core import download_view as dv
from minerva.ui.theme import (
    ACCENT,
    BORDER,
    DANGER,
    FG,
    FG_DIM,
    PANEL,
    PANEL_ALT,
    SEL_BG,
    SUCCESS,
    WARNING,
)

_noop = lambda *a, **k: None  # noqa: E731

_HEADINGS = {
    "name": "Name", "size": "Size", "progress": "Progress", "speed": "Speed",
    "eta": "ETA", "peers": "Peers", "state": "Status",
}
_FILTER_LABELS = {"all": "All", "active": "Active", "queued": "Queued", "done": "Done", "errors": "Errors"}


@dataclass
class PanelActions:
    """Callbacks the panel invokes; each receives the affected download ids."""

    start_now: Callable[[list[str]], None] = _noop
    toggle_pause: Callable[[list[str]], None] = _noop
    cancel: Callable[[list[str]], None] = _noop
    remove: Callable[[list[str]], None] = _noop
    retry: Callable[[list[str]], None] = _noop
    redownload: Callable[[list[str]], None] = _noop
    move: Callable[[list[str], str], None] = _noop
    open_folder: Callable[[list[str], bool], None] = _noop
    show_error: Callable[[str], None] = _noop
    copy_names: Callable[[list[str]], None] = _noop
    on_filter_change: Callable[[], None] = _noop


class DownloadsPanel(tk.Frame):
    TREE_STYLE = "Downloads.Treeview"

    def __init__(self, parent, actions: PanelActions | None = None, *, height: int = 8):
        super().__init__(parent, bg=PANEL)
        self.actions = actions or PanelActions()
        self._version: int | None = None
        self._structure_dirty = True
        self._values: dict[str, tuple] = {}
        self._kinds: dict[str, str] = {}
        self._done_items: dict[str, dict] = {}
        self._filter_var = tk.StringVar(value="all")
        self._chips: dict[str, ttk.Radiobutton] = {}
        self._build(height)

    # -- construction -------------------------------------------------------------------------
    def _build(self, height: int) -> None:
        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        for name, label in _FILTER_LABELS.items():
            chip = ttk.Radiobutton(
                bar, text=label, value=name, variable=self._filter_var,
                style="Chip.Toolbutton", command=self._on_filter_selected,
            )
            chip.pack(side="left", padx=(0, 3))
            self._chips[name] = chip

        body = tk.Frame(self, bg=PANEL)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            body, style=self.TREE_STYLE, columns=dv.COLUMNS, show="headings",
            selectmode="extended", height=height,
        )
        scroll = ttk.Scrollbar(body, orient="vertical", style="Visible.Vertical.TScrollbar",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        widths = {"name": 320, "size": 80, "progress": 150, "speed": 90, "eta": 75, "peers": 50, "state": 240}
        anchors = {"size": "e", "speed": "e", "eta": "e", "peers": "center"}
        for col in dv.COLUMNS:
            self.tree.heading(col, text=_HEADINGS[col], anchor=anchors.get(col, "w"))
            self.tree.column(col, width=widths[col], minwidth=40, anchor=anchors.get(col, "w"),
                             stretch=col in ("name", "state"))
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0))

        for tag, color in (
            (dv.KIND_ACTIVE, FG), (dv.KIND_PENDING, FG_DIM), (dv.KIND_RETRY, WARNING),
            (dv.KIND_DONE, SUCCESS), (dv.KIND_ERROR, DANGER), ("paused", WARNING), ("problem", DANGER),
        ):
            self.tree.tag_configure(tag, foreground=color)

        self._menu = tk.Menu(self, tearoff=0, bg=PANEL_ALT, fg=FG, activebackground=SEL_BG,
                             activeforeground=ACCENT, bd=0, relief="flat")
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Button-2>", self._on_right_click)  # macOS
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Delete>", lambda e: self.key_delete())
        self.tree.bind("<BackSpace>", lambda e: self.key_delete())
        self.tree.bind("<space>", lambda e: self.key_space())
        self.tree.bind("<Return>", lambda e: self.key_open())
        self.tree.bind("<Control-a>", lambda e: self.select_all())
        self.tree.bind("<Control-A>", lambda e: self.select_all())
        self.tree.bind("<Alt-Up>", lambda e: self.key_move("up"))
        self.tree.bind("<Alt-Down>", lambda e: self.key_move("down"))
        self.tree.bind("<r>", lambda e: self.key_retry())

    # -- selection helpers ------------------------------------------------------------------------
    def selected_ids(self) -> list[str]:
        return [i for i in self.tree.selection() if i in self._kinds]

    def kind_of(self, iid: str) -> str | None:
        return self._kinds.get(iid)

    def _ids_of(self, *kinds: str) -> list[str]:
        return [i for i in self.selected_ids() if self._kinds.get(i) in kinds]

    def selected_pending_ids(self) -> list[str]:
        return self._ids_of(dv.KIND_PENDING)

    # -- keyboard / mouse actions (public so they can be driven without synthesising key events) ----
    def select_all(self):
        self.tree.selection_set(self.tree.get_children(""))
        return "break"

    def key_delete(self):
        """Cancel selected live downloads; remove selected finished ones from the list."""
        live = self._ids_of(dv.KIND_PENDING, dv.KIND_ACTIVE, dv.KIND_RETRY)
        finished = self._ids_of(dv.KIND_DONE, dv.KIND_ERROR)
        if live:
            self.actions.cancel(live)
        if finished:
            self.actions.remove(finished)
        return "break"

    def key_space(self):
        ids = self._ids_of(dv.KIND_ACTIVE)
        if ids:
            self.actions.toggle_pause(ids)
        return "break"

    def key_retry(self):
        ids = self._ids_of(dv.KIND_ERROR)
        if ids:
            self.actions.retry(ids)
        return "break"

    def key_move(self, where: str):
        ids = self._ids_of(dv.KIND_PENDING)
        if ids:
            self.actions.move(ids, where)
        return "break"

    def key_open(self):
        ids = self.selected_ids()
        if ids:
            self.actions.open_folder(ids[:1], False)
        return "break"

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        kind = self._kinds.get(iid)
        if kind == dv.KIND_PENDING:
            self.actions.start_now([iid])
        elif kind == dv.KIND_ACTIVE:
            self.actions.toggle_pause([iid])
        elif kind in (dv.KIND_DONE, dv.KIND_ERROR):
            self.actions.open_folder([iid], False)
        return "break"

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        ids = self.selected_ids()
        if not ids:
            return
        kinds = {self._kinds[i] for i in ids}
        menu = self._menu
        menu.delete(0, "end")

        def add(label, command, enabled=True):
            menu.add_command(label=label, command=command, state="normal" if enabled else "disabled")

        pending = self._ids_of(dv.KIND_PENDING)
        active = self._ids_of(dv.KIND_ACTIVE)
        errors = self._ids_of(dv.KIND_ERROR)
        finished = self._ids_of(dv.KIND_DONE, dv.KIND_ERROR)
        live = self._ids_of(dv.KIND_PENDING, dv.KIND_ACTIVE, dv.KIND_RETRY)
        if pending:
            add("Start now", lambda: self.actions.start_now(pending))
        if active:
            add("Pause / Resume\tSpace", lambda: self.actions.toggle_pause(active))
        if errors:
            add("Retry\tR", lambda: self.actions.retry(errors))
        if pending:
            menu.add_separator()
            add("Move to top", lambda: self.actions.move(pending, "top"))
            add("Move up\tAlt+↑", lambda: self.actions.move(pending, "up"))
            add("Move down\tAlt+↓", lambda: self.actions.move(pending, "down"))
            add("Move to bottom", lambda: self.actions.move(pending, "bottom"))
        if kinds & {dv.KIND_DONE, dv.KIND_ERROR}:
            menu.add_separator()
            add("Open download folder", lambda: self.actions.open_folder(ids, False))
            add("Open extracted folder", lambda: self.actions.open_folder(ids, True))
            add("Redownload…", lambda: self.actions.redownload(finished))
        if len(ids) == 1 and kinds == {dv.KIND_ERROR}:
            add("Show error details…", lambda: self.actions.show_error(ids[0]))
        menu.add_separator()
        add("Copy name" + ("s" if len(ids) > 1 else ""), lambda: self.actions.copy_names(ids))
        if live:
            add("Cancel\tDel", lambda: self.actions.cancel(live))
        if finished:
            add("Remove from list\tDel", lambda: self.actions.remove(finished))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # -- filter -----------------------------------------------------------------------------------
    @property
    def filter_name(self) -> str:
        return self._filter_var.get()

    def set_filter(self, name: str) -> None:
        if name in dv.FILTERS:
            self._filter_var.set(name)
            self._on_filter_selected()

    def _on_filter_selected(self) -> None:
        self._structure_dirty = True
        self.actions.on_filter_change()

    # -- syncing ------------------------------------------------------------------------------------
    def sync(self, snapshot: dict, statuses: dict, extract_progress: dict, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        version = snapshot.get("version")
        if version is None or version != self._version or self._structure_dirty:
            self._sync_structure(snapshot, statuses, extract_progress, now)
            self._version = version
            self._structure_dirty = False
        else:
            self._sync_dynamic(snapshot, statuses, extract_progress, now)

    def _sync_structure(self, snapshot, statuses, extract_progress, now) -> None:
        all_rows = dv.build_rows(snapshot, statuses, extract_progress, now)
        counts = dv.count_by_filter(all_rows)
        for name, chip in self._chips.items():
            chip.configure(text=f"{_FILTER_LABELS[name]} ({counts[name]})")
        rows = dv.filter_rows(all_rows, self._filter_var.get())
        self._done_items = {it["id"]: it for it in snapshot.get("done", ())}

        tree = self.tree
        current = list(tree.get_children(""))
        inserted: set[str] = set()
        if not current:
            for r in rows:
                tree.insert("", "end", iid=r.iid, values=r.values, tags=r.tags)
                inserted.add(r.iid)
        else:
            wanted = {r.iid for r in rows}
            gone = [i for i in current if i not in wanted]
            if gone:
                tree.delete(*gone)
            mirror = [i for i in current if i in wanted]
            present = set(mirror)
            for k, r in enumerate(rows):
                if k < len(mirror) and mirror[k] == r.iid:
                    continue
                if r.iid in present:
                    tree.move(r.iid, "", k)
                    mirror.remove(r.iid)
                    mirror.insert(k, r.iid)
                else:
                    tree.insert("", k, iid=r.iid, values=r.values, tags=r.tags)
                    mirror.insert(k, r.iid)
                    present.add(r.iid)
                    inserted.add(r.iid)
        new_values: dict[str, tuple] = {}
        for r in rows:
            new_values[r.iid] = (r.values, r.tags)
            if r.iid not in inserted and self._values.get(r.iid) != new_values[r.iid]:
                tree.item(r.iid, values=r.values, tags=r.tags)
        self._values = new_values
        self._kinds = {r.iid: r.kind for r in rows}

    def _set_row(self, row: dv.Row) -> bool:
        if row.iid not in self._kinds:
            return False  # filtered out or not built yet
        packed = (row.values, row.tags)
        if self._values.get(row.iid) == packed:
            return False
        self._values[row.iid] = packed
        self._kinds[row.iid] = row.kind
        self.tree.item(row.iid, values=row.values, tags=row.tags)
        return True

    def _sync_dynamic(self, snapshot, statuses, extract_progress, now) -> None:
        for item in snapshot.get("active_items", ()):
            self._set_row(dv.build_row(dv.KIND_ACTIVE, item, statuses.get(item["id"]), now=now))
        for item in snapshot.get("retry", ()):
            self._set_row(dv.build_row(dv.KIND_RETRY, item, now=now))
        for did, info in extract_progress.items():
            item = self._done_items.get(did)
            if item is not None and self._kinds.get(did) == dv.KIND_DONE:
                self._set_row(dv.build_row(dv.KIND_DONE, item, extract=info, now=now))

    def invalidate(self) -> None:
        """Force the next ``sync`` to rebuild rows (e.g. after an external change)."""
        self._structure_dirty = True
