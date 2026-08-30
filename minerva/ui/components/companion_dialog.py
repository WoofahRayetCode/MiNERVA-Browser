"""Modal to pick matching DLC and updates after queueing a base game."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from minerva.ui.theme import BG, PANEL, PANEL_ALT, FG, FG_DIM, ACCENT, BORDER, SUCCESS
from minerva.core.companions import KIND_DLC, KIND_UPDATE, Companion


def prompt_companions(parent, game_name: str, items: list[Companion], *, precheck_dlc: bool = False) -> list[Companion] | None:
    """Return selected companions, empty list if skip, or None if the window was closed."""
    dlg = tk.Toplevel(parent)
    dlg.title("DLC and updates")
    dlg.configure(bg=BG)
    dlg.transient(parent)
    dlg.grab_set()
    dlg.geometry("640x480")
    dlg.minsize(480, 360)

    result: dict[str, list[Companion] | None] = {"picked": None}

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
    dlg.wait_window()
    return result["picked"]


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
