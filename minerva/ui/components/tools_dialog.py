"""ROM & Disc Tools dialog and popup menu component."""

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
    SUCCESS,
    WARNING,
)


class ToolsMenu:
    """Helper to create and pop up a structured ROM & Disc tools menu."""

    @staticmethod
    def show_menu(
        parent: tk.Widget,
        anchor_widget: tk.Widget,
        callbacks: dict[str, Callable[[], None]],
    ):
        menu = tk.Menu(parent, tearoff=0, bg=PANEL_ALT, fg=FG, activebackground=SEL_BG, activeforeground=FG)

        menu.add_command(label="-- ROM & Disc Utilities --", state="disabled")
        menu.add_separator()

        menu.add_command(
            label="💿 Compress PS1/PS2 to CHD",
            command=callbacks.get("compress_chd"),
        )
        menu.add_command(
            label="🧹 Clean BIN / CUE Source Files",
            command=callbacks.get("clean_bin_cue"),
        )
        menu.add_command(
            label="🏷 Standardize ROM / CHD Names",
            command=callbacks.get("clean_names"),
        )
        menu.add_command(
            label="🔍 Verify Extracted ROM Integrity",
            command=callbacks.get("verify_extracted"),
        )
        menu.add_separator()
        menu.add_command(
            label="📁 Open Extracted ROMs Folder",
            command=callbacks.get("open_extracted"),
        )
        menu.add_command(
            label="🗑 Force Delete Uncompressed BINs",
            command=callbacks.get("force_delete_bins"),
        )

        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
        menu.tk_popup(x, y)


class ToolsDialog(tk.Toplevel):
    """Dedicated modal dialog for managing disc tools and batch operations."""

    def __init__(
        self,
        parent: tk.Widget,
        callbacks: dict[str, Callable[[], None]],
        extractor_status: str,
        chdman_status: str,
    ):
        super().__init__(parent)
        self.title("MiNERVA ROM & Disc Utilities")
        self.configure(bg=BG)
        self.geometry("540x440")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.callbacks = callbacks
        self.extractor_status = extractor_status
        self.chdman_status = chdman_status

        self._build_ui()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=PANEL, padx=16, pady=12)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="🛠  ROM & Archive Processing Tools",
            bg=PANEL,
            fg=ACCENT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            hdr,
            text="Manage automated extraction, CHD compression, and folder integrity.",
            bg=PANEL,
            fg=FG_DIM,
            font=("TkDefaultFont", 9),
        ).pack(anchor="w", pady=(2, 0))

        # Status Cards
        status_frame = tk.Frame(self, bg=BG, padx=16, pady=12)
        status_frame.pack(fill="x")

        s1 = tk.Frame(status_frame, bg=PANEL_ALT, padx=10, pady=8, highlightbackground=BORDER, highlightthickness=1)
        s1.pack(fill="x", pady=(0, 6))
        tk.Label(s1, text="Archive Extractor:", bg=PANEL_ALT, fg=FG_DIM, font=("TkDefaultFont", 9)).pack(side="left")
        tk.Label(s1, text=self.extractor_status, bg=PANEL_ALT, fg=SUCCESS, font=("TkDefaultFont", 9, "bold")).pack(side="right")

        s2 = tk.Frame(status_frame, bg=PANEL_ALT, padx=10, pady=8, highlightbackground=BORDER, highlightthickness=1)
        s2.pack(fill="x")
        tk.Label(s2, text="CHDMAN Converter:", bg=PANEL_ALT, fg=FG_DIM, font=("TkDefaultFont", 9)).pack(side="left")
        tk.Label(s2, text=self.chdman_status, bg=PANEL_ALT, fg=SUCCESS, font=("TkDefaultFont", 9, "bold")).pack(side="right")

        # Actions Section
        actions_frame = tk.Frame(self, bg=BG, padx=16)
        actions_frame.pack(fill="both", expand=True)

        tk.Label(
            actions_frame,
            text="Batch Actions",
            bg=BG,
            fg=FG,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w", pady=(6, 6))

        tools_list = [
            (
                "💿 Compress PS1/PS2 to CHD",
                "Convert disc cue/bin or iso files to compressed CHD",
                self.callbacks.get("compress_chd"),
            ),
            (
                "🧹 Clean BIN/CUE Sources",
                "Safely remove orphaned .bin/.cue files after successful CHD conversion",
                self.callbacks.get("clean_bin_cue"),
            ),
            (
                "🔍 Verify Extracted Integrity",
                "Scan downloaded files and verify extracted ROM folder consistency",
                self.callbacks.get("verify_extracted"),
            ),
            (
                "🏷 Standardize ROM Names",
                "Clean redundant tags and suffixes across converted CHD names",
                self.callbacks.get("clean_names"),
            ),
        ]

        for title, desc, cmd in tools_list:
            card = tk.Frame(actions_frame, bg=PANEL, padx=10, pady=6, highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x", pady=3)

            lbl_box = tk.Frame(card, bg=PANEL)
            lbl_box.pack(side="left", fill="x", expand=True)
            tk.Label(lbl_box, text=title, bg=PANEL, fg=FG, font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
            tk.Label(lbl_box, text=desc, bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 8)).pack(anchor="w")

            btn = ttk.Button(card, text="Run", style="Header.TButton", command=cmd)
            btn.pack(side="right", padx=4)

        # Footer
        footer = tk.Frame(self, bg=PANEL, padx=16, pady=10)
        footer.pack(fill="x", side="bottom")
        ttk.Button(footer, text="Close", style="Toolbar.TButton", command=self.destroy).pack(side="right")
