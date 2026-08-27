"""Modern theme palette and TTK style configurations for MiNERVA-Browser."""

import sys
import tkinter as tk
from tkinter import ttk

# Catppuccin Mocha-inspired refined palette
BG = "#181825"
PANEL = "#1e1e2e"
PANEL_ALT = "#242438"
ACCENT = "#89b4fa"
ACCENT_HOVER = "#b4befe"
ACCENT_PURPLE = "#cba6f7"
FG = "#cdd6f4"
FG_DIM = "#a6adc8"
SEL_BG = "#313244"
SEL_FG = "#ffffff"
ENTRY_BG = "#1e1e2e"
ENTRY_BORDER = "#45475a"
BORDER = "#313244"

# Status colors
SUCCESS = "#a6e3a1"
WARNING = "#f9e2af"
DANGER = "#f38ba8"

# Pill / Tag colors
PILL_ACTIVE_BG = "#89b4fa"
PILL_ACTIVE_FG = "#11111b"
PILL_INACTIVE_BG = "#2a2b3d"
PILL_INACTIVE_FG = "#cdd6f4"
PILL_HOVER_BG = "#3b3d54"


def setup_modern_styles(root: tk.Tk) -> ttk.Style:
    """Configure modern ttk styles across the entire application."""
    style = ttk.Style(root)
    style.theme_use("clam")

    # Base default style
    style.configure(
        ".",
        background=BG,
        foreground=FG,
        fieldbackground=ENTRY_BG,
        troughcolor=PANEL,
        bordercolor=PANEL,
        darkcolor=PANEL,
        lightcolor=PANEL,
        selectbackground=SEL_BG,
        selectforeground=FG,
        font=("TkDefaultFont", 10),
    )

    # Frame styles
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Card.TFrame", background=PANEL_ALT)
    style.configure("Toolbar.TFrame", background=PANEL)

    # Label styles
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Panel.TLabel", background=PANEL, foreground=FG)
    style.configure("Dim.TLabel", background=BG, foreground=FG_DIM)
    style.configure("DimPanel.TLabel", background=PANEL, foreground=FG_DIM)
    style.configure("Accent.TLabel", background=PANEL, foreground=ACCENT, font=("TkDefaultFont", 10, "bold"))
    style.configure("Status.TLabel", background=PANEL, foreground=FG_DIM, font=("TkDefaultFont", 9))
    style.configure("Loading.TLabel", background=PANEL, foreground=ACCENT_PURPLE, font=("TkDefaultFont", 10, "italic"))
    style.configure("Breadcrumb.TLabel", background=BG, foreground=FG_DIM, font=("TkDefaultFont", 10))
    style.configure(
        "BreadcrumbLink.TLabel",
        background=BG,
        foreground=ACCENT,
        font=("TkDefaultFont", 10),
        cursor="hand2",
    )

    # Button styles
    style.configure(
        "Toolbar.TButton",
        background=PANEL_ALT,
        foreground=FG,
        bordercolor=BORDER,
        focuscolor=ACCENT,
        padding=(10, 5),
        relief="flat",
        font=("TkDefaultFont", 9),
    )
    style.map(
        "Toolbar.TButton",
        background=[("active", SEL_BG), ("pressed", ACCENT)],
        foreground=[("pressed", "#11111b"), ("active", "#ffffff")],
    )

    style.configure(
        "Primary.TButton",
        background=ACCENT,
        foreground="#11111b",
        bordercolor=ACCENT,
        focuscolor=ACCENT,
        padding=(12, 5),
        font=("TkDefaultFont", 9, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[("active", ACCENT_HOVER)],
        foreground=[("active", "#11111b")],
    )

    style.configure(
        "Header.TButton",
        background=PANEL_ALT,
        foreground=FG,
        bordercolor=BORDER,
        focuscolor=ACCENT,
        padding=(6, 3),
        font=("TkDefaultFont", 9),
    )
    style.map(
        "Header.TButton",
        background=[("active", SEL_BG), ("pressed", ACCENT)],
        foreground=[("pressed", "#11111b"), ("active", "#ffffff")],
    )

    style.configure(
        "Action.TButton",
        background=PANEL_ALT,
        foreground=FG,
        bordercolor=BORDER,
        focuscolor=ACCENT,
        padding=(8, 4),
        font=("TkDefaultFont", 9),
    )
    style.map(
        "Action.TButton",
        background=[("active", SEL_BG)],
        foreground=[("active", ACCENT)],
    )

    # Treeview styles
    style.configure(
        "Left.Treeview",
        background=PANEL,
        foreground=FG,
        fieldbackground=PANEL,
        borderwidth=0,
        rowheight=26,
        font=("TkDefaultFont", 10),
    )
    style.map(
        "Left.Treeview",
        background=[("selected", SEL_BG)],
        foreground=[("selected", ACCENT)],
    )
    style.configure(
        "Left.Treeview.Heading",
        background=PANEL,
        foreground=ACCENT,
        font=("TkDefaultFont", 10, "bold"),
        borderwidth=0,
    )

    style.configure(
        "Right.Treeview",
        background=PANEL,
        foreground=FG,
        fieldbackground=PANEL,
        borderwidth=0,
        rowheight=26,
        font=("TkDefaultFont", 10),
    )
    style.map(
        "Right.Treeview",
        background=[("selected", SEL_BG)],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Right.Treeview.Heading",
        background=PANEL_ALT,
        foreground=ACCENT,
        font=("TkDefaultFont", 9, "bold"),
        borderwidth=1,
        relief="flat",
    )

    # Entry styles
    style.configure(
        "TEntry",
        fieldbackground=ENTRY_BG,
        foreground=FG,
        insertcolor=FG,
        bordercolor=BORDER,
        relief="flat",
        padding=6,
    )

    # Scrollbar styles
    style.configure(
        "TScrollbar",
        background=SEL_BG,
        troughcolor=PANEL,
        arrowcolor=FG_DIM,
        bordercolor=PANEL,
        darkcolor=SEL_BG,
        lightcolor=SEL_BG,
        arrowsize=12,
    )
    style.map(
        "TScrollbar",
        background=[("active", ACCENT)],
        arrowcolor=[("active", FG)],
    )

    style.configure(
        "Visible.Vertical.TScrollbar",
        background=SEL_BG,
        troughcolor=PANEL,
        arrowcolor=FG_DIM,
        bordercolor=PANEL,
        darkcolor=SEL_BG,
        lightcolor=SEL_BG,
        arrowsize=12,
    )
    style.configure(
        "Visible.Horizontal.TScrollbar",
        background=SEL_BG,
        troughcolor=PANEL,
        arrowcolor=FG_DIM,
        bordercolor=PANEL,
        darkcolor=SEL_BG,
        lightcolor=SEL_BG,
        arrowsize=12,
    )
    style.map(
        "Visible.Vertical.TScrollbar",
        background=[("active", ACCENT)],
        arrowcolor=[("active", FG)],
    )
    style.map(
        "Visible.Horizontal.TScrollbar",
        background=[("active", ACCENT)],
        arrowcolor=[("active", FG)],
    )

    # Progressbar styles
    style.configure(
        "TProgressbar",
        background=ACCENT,
        troughcolor=PANEL_ALT,
        bordercolor=BORDER,
        darkcolor=ACCENT,
        lightcolor=ACCENT,
    )

    return style
