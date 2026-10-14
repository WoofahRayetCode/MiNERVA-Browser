"""Modal to pick matching DLC and updates after queueing a base game."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from minerva.ui.theme import BG, PANEL, PANEL_ALT, FG, FG_DIM, ACCENT, BORDER, SUCCESS
from minerva.core.companions import KIND_DLC, KIND_UPDATE, Companion, KNOWN_SYSTEM_BROWSE_PATHS
from minerva.ui.components.modal import grab_when_mapped


def prompt_companions(parent, game_name: str, items: list[Companion], *, precheck_dlc: bool = False) -> list[Companion] | None:
    """Return selected companions, empty list if skip, or None if the window was closed.

    The window is built while it is withdrawn and only shown once it is complete, and it is destroyed if
    building fails, so a half-built (blank) window can never be left on screen.
    """
    result: dict[str, list[Companion] | None] = {"picked": None}
    dlg = tk.Toplevel(parent)
    dlg.withdraw()
    try:
        _build(dlg, parent, game_name, items, precheck_dlc, result)
    except BaseException:
        dlg.destroy()
        raise
    dlg.deiconify()
    grab_when_mapped(dlg)
    dlg.focus_set()
    dlg.wait_window()
    return result["picked"]


def _build(dlg, parent, game_name, items, precheck_dlc, result) -> None:
    dlg.title("DLC and updates")
    dlg.configure(bg=BG)
    dlg.transient(parent)
    dlg.geometry("640x480")
    dlg.minsize(480, 360)

    tk.Label(
        dlg,
        text=f"Found extra content for:\n{game_name}",
        bg=BG,
        fg=FG,
        font=("TkDefaultFont", 10, "bold"),
        justify="left",
        wraplength=600,
    ).pack(anchor="w", padx=16, pady=(14, 8))

    canvas_host = tk.Frame(dlg, bg=BG)
    canvas_host.pack(fill="both", expand=True, padx=12, pady=(0, 8))
    canvas = tk.Canvas(canvas_host, bg=PANEL, highlightthickness=0)
    scroll = ttk.Scrollbar(canvas_host, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=PANEL)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    vars_by_item: list[tuple[Companion, tk.BooleanVar]] = []

    def _section(title: str, color: str):
        tk.Label(inner, text=title, bg=PANEL, fg=color, font=("TkDefaultFont", 9, "bold")).pack(
            anchor="w", padx=10, pady=(10, 2)
        )

    updates = [c for c in items if c.kind == KIND_UPDATE]
    dlcs = [c for c in items if c.kind == KIND_DLC]

    if updates:
        _section("Updates", SUCCESS)
        for item in updates:
            var = tk.BooleanVar(value=True)
            vars_by_item.append((item, var))
            _row(inner, item, var)
    if dlcs:
        _section("DLC", ACCENT)
        for item in dlcs:
            var = tk.BooleanVar(value=bool(precheck_dlc))
            vars_by_item.append((item, var))
            _row(inner, item, var)

    btns = tk.Frame(dlg, bg=BG)
    btns.pack(fill="x", padx=12, pady=(0, 14))

    def _finish(selected: list[Companion] | None):
        result["picked"] = selected
        dlg.destroy()

    def _selected():
        return [item for item, var in vars_by_item if var.get()]

    ttk.Button(btns, text="Skip", command=lambda: _finish([])).pack(side="right", padx=4)
    ttk.Button(btns, text="Download all", style="Primary.TButton",
               command=lambda: _finish(list(items))).pack(side="right", padx=4)
    ttk.Button(btns, text="Download selected", style="Primary.TButton",
               command=lambda: _finish(_selected())).pack(side="right", padx=4)

    dlg.protocol("WM_DELETE_WINDOW", lambda: _finish(None))


def _row(parent, item: Companion, var: tk.BooleanVar):
    row = tk.Frame(parent, bg=PANEL_ALT, highlightbackground=BORDER, highlightthickness=1)
    row.pack(fill="x", padx=10, pady=3)
    cb = tk.Checkbutton(
        row,
        variable=var,
        bg=PANEL_ALT,
        fg=FG,
        selectcolor=PANEL,
        activebackground=PANEL_ALT,
        activeforeground=FG,
        relief="flat",
    )
    cb.pack(side="left", padx=(6, 4), pady=6)
    text = item.name
    if item.size:
        text += f"  ·  {item.size}"
    tk.Label(row, text=text, bg=PANEL_ALT, fg=FG, anchor="w", wraplength=520, justify="left").pack(
        side="left", fill="x", expand=True, pady=6
    )
    if item.folder:
        tk.Label(row, text=item.folder, bg=PANEL_ALT, fg=FG_DIM, font=("TkDefaultFont", 8)).pack(
            side="right", padx=8
        )


def prompt_system_choice(parent, game_name: str, default_system: str | None = None) -> str | None:
    """Prompt the user to choose which console system a ROM belongs to.

    Returns the collection browse path (e.g. '/browse/./Redump/Sony - PlayStation 3/'),
    or None if cancelled.
    """
    systems = list(KNOWN_SYSTEM_BROWSE_PATHS.keys())
    if not systems:
        return None
    default = default_system if default_system in systems else systems[0]

    chosen: dict[str, str | None] = {"path": None}
    dlg = tk.Toplevel(parent)
    dlg.withdraw()
    try:
        dlg.title("Select System")
        dlg.configure(bg=BG)
        dlg.transient(parent)
        dlg.geometry("480x200")
        dlg.minsize(420, 180)

        tk.Label(
            dlg,
            text=f"Select console system for:\n{game_name}",
            bg=BG,
            fg=FG,
            font=("TkDefaultFont", 10, "bold"),
            justify="left",
            wraplength=440,
        ).pack(anchor="w", padx=16, pady=(16, 12))

        combo_frame = tk.Frame(dlg, bg=BG)
        combo_frame.pack(fill="x", padx=16, pady=(0, 16))

        tk.Label(combo_frame, text="System:", bg=BG, fg=FG_DIM, font=("TkDefaultFont", 9)).pack(side="left", padx=(0, 8))

        var = tk.StringVar(value=default)
        combo = ttk.Combobox(combo_frame, textvariable=var, values=systems, state="readonly", width=35)
        combo.pack(side="left", fill="x", expand=True)

        btns = tk.Frame(dlg, bg=BG)
        btns.pack(fill="x", padx=16, pady=(0, 14))

        def _cancel():
            dlg.destroy()

        def _confirm():
            sys_name = var.get()
            chosen["path"] = KNOWN_SYSTEM_BROWSE_PATHS.get(sys_name)
            dlg.destroy()

        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="right", padx=4)
        ttk.Button(btns, text="Check Updates", style="Primary.TButton", command=_confirm).pack(side="right", padx=4)

        dlg.protocol("WM_DELETE_WINDOW", _cancel)
    except BaseException:
        dlg.destroy()
        raise

    dlg.deiconify()
    grab_when_mapped(dlg)
    dlg.focus_set()
    dlg.wait_window()
    return chosen["path"]

