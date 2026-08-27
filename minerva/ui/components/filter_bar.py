"""Modern Search and Filter Bar component with Region Pills and Tag Dropdown."""

import tkinter as tk
from tkinter import ttk
from typing import Callable

from minerva.ui.theme import (
    BG,
    PANEL,
    PANEL_ALT,
    ACCENT,
    ACCENT_HOVER,
    FG,
    FG_DIM,
    SEL_BG,
    BORDER,
    PILL_ACTIVE_BG,
    PILL_ACTIVE_FG,
    PILL_INACTIVE_BG,
    PILL_INACTIVE_FG,
    PILL_HOVER_BG,
)


class RegionPill(tk.Label):
    """An interactive pill/chip toggle button for filtering regions."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        var: tk.BooleanVar,
        command: Callable[[], None] | None = None,
    ):
        super().__init__(
            parent,
            text=text,
            font=("TkDefaultFont", 9, "bold" if var.get() else "normal"),
            padx=10,
            pady=3,
            cursor="hand2",
            relief="flat",
            borderwidth=1,
        )
        self.var = var
        self.command = command
        self._hovered = False

        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

        self.update_style()

    def _on_click(self, event=None):
        self.var.set(not self.var.get())
        self.update_style()
        if self.command:
            self.command()

    def _on_enter(self, event=None):
        self._hovered = True
        if not self.var.get():
            self.configure(bg=PILL_HOVER_BG, fg="#ffffff")

    def _on_leave(self, event=None):
        self._hovered = False
        self.update_style()

    def update_style(self):
        is_active = self.var.get()
        if is_active:
            self.configure(
                bg=PILL_ACTIVE_BG,
                fg=PILL_ACTIVE_FG,
                font=("TkDefaultFont", 9, "bold"),
            )
        else:
            self.configure(
                bg=PILL_HOVER_BG if self._hovered else PILL_INACTIVE_BG,
                fg=PILL_INACTIVE_FG,
                font=("TkDefaultFont", 9, "normal"),
            )


class FilterBar(tk.Frame):
    """Unified Modern Search, Region Filter Pills, and Tag Popover Bar."""

    def __init__(
        self,
        parent: tk.Widget,
        search_var: tk.StringVar,
        on_search_change: Callable[..., None],
        on_clear_search: Callable[[], None],
        region_specs: list[tuple[str, str]],
        region_vars: dict[str, tk.BooleanVar],
        tag_specs: list[tuple[str, str]],
        tag_vars: dict[str, tk.BooleanVar],
        on_filter_change: Callable[[], None],
    ):
        super().__init__(parent, bg=BG)
        self.search_var = search_var
        self.on_search_change = on_search_change
        self.on_clear_search = on_clear_search
        self.region_specs = region_specs
        self.region_vars = region_vars
        self.tag_specs = tag_specs
        self.tag_vars = tag_vars
        self.on_filter_change = on_filter_change

        self._pills: list[RegionPill] = []
        self._tag_menu: tk.Menu | None = None

        self._build_ui()

    def _build_ui(self):
        # Top Row: Integrated Search + Tag Menu Button
        top_row = tk.Frame(self, bg=BG)
        top_row.pack(fill="x", padx=10, pady=(6, 4))

        # Search Bar with embedded icon & clear button
        search_container = tk.Frame(top_row, bg=PANEL_ALT, bd=1, relief="solid")
        search_container.configure(highlightbackground=BORDER, highlightcolor=ACCENT, highlightthickness=1)
        search_container.pack(side="left", fill="x", expand=True, padx=(0, 8))

        search_icon = tk.Label(search_container, text="🔍", bg=PANEL_ALT, fg=FG_DIM, font=("TkDefaultFont", 9))
        search_icon.pack(side="left", padx=(8, 2))

        self.search_entry = ttk.Entry(search_container, textvariable=self.search_var, style="TEntry")
        self.search_entry.pack(side="left", fill="x", expand=True, padx=2, pady=2)
        self.search_entry.bind("<Escape>", lambda e: self.on_clear_search())

        self.clear_btn = tk.Label(
            search_container,
            text="✕",
            bg=PANEL_ALT,
            fg=FG_DIM,
            font=("TkDefaultFont", 9, "bold"),
            cursor="hand2",
            padx=8,
        )
        self.clear_btn.pack(side="right")
        self.clear_btn.bind("<Button-1>", lambda e: self.on_clear_search())
        self.clear_btn.bind("<Enter>", lambda e: self.clear_btn.configure(fg=ACCENT))
        self.clear_btn.bind("<Leave>", lambda e: self.clear_btn.configure(fg=FG_DIM))

        # Tags Dropdown Button
        self.tags_btn = ttk.Button(
            top_row,
            text="🏷 Hide Tags ▾",
            style="Toolbar.TButton",
            command=self._show_tag_menu,
        )
        self.tags_btn.pack(side="right")
        self._update_tag_btn_label()

        # Bottom row: region pills wrap onto extra lines so none are clipped
        pill_row = tk.Frame(self, bg=BG)
        pill_row.pack(fill="x", padx=10, pady=(0, 4))

        region_lbl = tk.Label(
            pill_row,
            text="Region:",
            bg=BG,
            fg=FG_DIM,
            font=("TkDefaultFont", 9),
        )
        region_lbl.pack(side="left", padx=(0, 6), anchor="n", pady=3)

        self._pill_wrap = tk.Frame(pill_row, bg=BG)
        self._pill_wrap.pack(side="left", fill="x", expand=True)
        self._pill_wrap.pack_propagate(False)
        self._wrap_pills: list[tk.Widget] = []

        self.all_regions_var = tk.BooleanVar(value=all(v.get() for v in self.region_vars.values()))
        self.all_pill = tk.Label(
            self._pill_wrap,
            text="All",
            bg=PILL_ACTIVE_BG if self.all_regions_var.get() else PILL_INACTIVE_BG,
            fg=PILL_ACTIVE_FG if self.all_regions_var.get() else PILL_INACTIVE_FG,
            font=("TkDefaultFont", 9, "bold" if self.all_regions_var.get() else "normal"),
            padx=10,
            pady=3,
            cursor="hand2",
            relief="flat",
        )
        self._wrap_pills.append(self.all_pill)
        self.all_pill.bind("<Button-1>", self._on_all_regions_click)
        self.all_pill.bind("<Enter>", lambda e: self._on_all_pill_hover(True))
        self.all_pill.bind("<Leave>", lambda e: self._on_all_pill_hover(False))

        for key, label in self.region_specs:
            var = self.region_vars[key]
            pill = RegionPill(self._pill_wrap, text=label, var=var, command=self._on_region_pill_toggled)
            self._wrap_pills.append(pill)
            self._pills.append(pill)

        self._pill_wrap.bind("<Configure>", self._reflow_region_pills)
        self.after_idle(self._reflow_region_pills)

    def _reflow_region_pills(self, event=None):
        wrap = getattr(self, "_pill_wrap", None)
        if wrap is None:
            return
        width = max(wrap.winfo_width(), 1)
        x = 0
        y = 0
        row_h = 0
        gap = 4
        for child in self._wrap_pills:
            w = child.winfo_reqwidth()
            h = child.winfo_reqheight()
            if x > 0 and x + w > width:
                x = 0
                y += row_h + gap
                row_h = 0
            child.place(x=x, y=y)
            x += w + gap
            row_h = max(row_h, h)
        new_h = max(y + row_h, 1)
        if int(wrap.cget("height") or 0) != new_h:
            wrap.configure(height=new_h)

    def _on_all_pill_hover(self, entering: bool):
        if not self.all_regions_var.get():
            self.all_pill.configure(bg=PILL_HOVER_BG if entering else PILL_INACTIVE_BG)

    def _on_all_regions_click(self, event=None):
        new_val = not self.all_regions_var.get()
        self.all_regions_var.set(new_val)
        for var in self.region_vars.values():
            var.set(new_val)
        self.refresh_pills()
        if self.on_filter_change:
            self.on_filter_change()

    def _on_region_pill_toggled(self):
        all_active = all(v.get() for v in self.region_vars.values())
        self.all_regions_var.set(all_active)
        self.all_pill.configure(
            bg=PILL_ACTIVE_BG if all_active else PILL_INACTIVE_BG,
            fg=PILL_ACTIVE_FG if all_active else PILL_INACTIVE_FG,
            font=("TkDefaultFont", 9, "bold" if all_active else "normal"),
        )
        if self.on_filter_change:
            self.on_filter_change()

    def refresh_pills(self):
        all_active = all(v.get() for v in self.region_vars.values())
        self.all_regions_var.set(all_active)
        self.all_pill.configure(
            bg=PILL_ACTIVE_BG if all_active else PILL_INACTIVE_BG,
            fg=PILL_ACTIVE_FG if all_active else PILL_INACTIVE_FG,
            font=("TkDefaultFont", 9, "bold" if all_active else "normal"),
        )
        for pill in self._pills:
            pill.update_style()

    def _update_tag_btn_label(self):
        hidden_count = sum(1 for v in self.tag_vars.values() if v.get())
        if hidden_count > 0:
            self.tags_btn.configure(text=f"🏷 Hide Tags ({hidden_count}) ▾")
        else:
            self.tags_btn.configure(text="🏷 Hide Tags ▾")

    def _show_tag_menu(self):
        menu = tk.Menu(self, tearoff=0, bg=PANEL_ALT, fg=FG, activebackground=SEL_BG, activeforeground=FG)

        menu.add_command(label="-- Hide ROMs by Tag --", state="disabled")
        menu.add_separator()

        for key, label in self.tag_specs:
            var = self.tag_vars[key]
            menu.add_checkbutton(
                label=label,
                variable=var,
                command=self._on_tag_toggle,
            )

        menu.add_separator()
        menu.add_command(label="Show All (Clear Tag Filters)", command=self._clear_tag_filters)
        menu.add_command(label="Hide All Optional Tags", command=self._hide_all_tags)

        # Position menu directly below the button
        x = self.tags_btn.winfo_rootx()
        y = self.tags_btn.winfo_rooty() + self.tags_btn.winfo_height()
        menu.tk_popup(x, y)

    def _on_tag_toggle(self):
        self._update_tag_btn_label()
        if self.on_filter_change:
            self.on_filter_change()

    def _clear_tag_filters(self):
        for var in self.tag_vars.values():
            var.set(False)
        self._update_tag_btn_label()
        if self.on_filter_change:
            self.on_filter_change()

    def _hide_all_tags(self):
        for var in self.tag_vars.values():
            var.set(True)
        self._update_tag_btn_label()
        if self.on_filter_change:
            self.on_filter_change()
