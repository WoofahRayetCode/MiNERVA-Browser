import sys
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import urllib.request
import urllib.parse
import webbrowser
import queue
import uuid
import pathlib
import shutil
import subprocess
import re
import time
import zipfile
import hashlib
import json
import os

from minerva.constants import (
    APP_VERSION,
    GITHUB_REPO,
    BASE_URL,
    BROWSE_ROOT,
    TRACKERS,
    BG,
    PANEL,
    ACCENT,
    FG,
    FG_DIM,
    SEL_BG,
    ENTRY_BG,
    get_default_download_dir,
    get_runtime_base_dir,
    get_torrent_dir,
    get_assets_dir,
    get_icon_png_path,
    get_icon_ico_path,
    load_app_settings,
    save_app_settings,
    log_error,
    log_activity,
    winreg,
)
from minerva.ui.theme import (
    setup_modern_styles,
    PANEL_ALT,
    ACCENT_HOVER,
    ACCENT_PURPLE,
    SUCCESS,
    WARNING,
    DANGER,
    BORDER,
)
from minerva.ui.components.filter_bar import FilterBar
from minerva.ui.components.tools_dialog import ToolsMenu, ToolsDialog
from minerva.core.sqlite_http import fetch_entries, fetch_rom_info, extract_rom_id
from minerva.core.ps3_dkeys import (
    PS3_DISC_KEYS_TXT_PATH,
    find_dkey_entry,
    is_ps3_iso_browse_path,
)
from minerva.core.torrent_engine import (
    TorrentEngine,
    DownloadQueue,
    _LT_AVAILABLE,
)
from minerva.core.extractors import (
    IS_WINDOWS,
    _windows_startupinfo,
    find_archive_extractors,
    format_extractor_status,
    find_chdman_executable,
    normalize_chd_stem,
    clean_chd_names_in_base,
    is_likely_rom_file,
    verify_extracted_output,
    chd_source_mode,
    collect_chd_sources,
    compress_ps1_to_chd,
    extract_archive,
)


def _parse_size_bytes(size_str: str) -> int:
    """Convert human readable size string (e.g. '1.5 GB', '250 MB') to bytes."""
    if not isinstance(size_str, str) or not size_str.strip():
        return 0
    s = size_str.strip().upper()
    try:
        parts = s.split()
        if len(parts) == 2:
            num = float(parts[0])
            unit = parts[1]
            multipliers = {
                "B": 1,
                "KB": 1024,
                "MB": 1024**2,
                "GB": 1024**3,
                "TB": 1024**4,
                "KIB": 1024,
                "MIB": 1024**2,
                "GIB": 1024**3,
            }
            return int(num * multipliers.get(unit, 1))
        return int(float(s))
    except Exception:
        return 0


def _format_speed(bps: float) -> str:
    """Format bytes per second into human readable transfer rate."""
    if bps >= 1024**2:
        return f"{bps / (1024**2):.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{int(bps)} B/s"


class HoverTooltip:
    """Display a floating tooltip when hovering over a widget."""
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 15
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            tw, text=self.text, justify="left",
            background=PANEL, foreground=FG, relief="solid", borderwidth=1,
            font=("TkDefaultFont", 8), padx=6, pady=3,
        )
        lbl.pack(ipadx=1)

    def hide_tip(self, event=None):
        tw = self.tip_window
        self.tip_window = None
        if tw:
            try:
                tw.destroy()
            except Exception:
                pass


class MinervaApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"MiNERVA Archive Browser v{APP_VERSION}")
        self.geometry("1100x650")
        self.minsize(640, 480)
        self.configure(bg=BG)
        self._current_path = BROWSE_ROOT
        self._all_entries = []
        self._torrent_engine: TorrentEngine | None = None
        self._download_queue: DownloadQueue | None = None
        self._settings = load_app_settings()
        saved_download_dir = self._settings.get("download_dir")
        if not isinstance(saved_download_dir, str) or not saved_download_dir.strip():
            saved_download_dir = get_default_download_dir()
        self._download_dir = tk.StringVar(value=saved_download_dir)
        self._auto_extract_default_var = tk.BooleanVar(
            value=bool(self._settings.get("auto_extract_default", False))
        )
        self._delete_archive_default_var = tk.BooleanVar(
            value=bool(self._settings.get("delete_archive_default", True))
        )
        self._compress_ps1_chd_var = tk.BooleanVar(
            value=bool(self._settings.get("compress_ps1_chd", True))
        )
        self._autostart_var = tk.BooleanVar(
            value=bool(self._settings.get("autostart_with_windows", False))
        )
        self._start_minimized_var = tk.BooleanVar(
            value=bool(self._settings.get("start_minimized", False))
        )
        self._show_tag_specs = [
            ("demo", "Demo"),
            ("beta", "Beta"),
            ("revision", "Revision"),
            ("proto", "Proto"),
            ("unlicensed", "Unlicensed"),
            ("hack", "Hack"),
            ("translation", "Translation"),
        ]
        self._show_region_specs = [
            ("usa", "USA"),
            ("europe", "Europe"),
            ("japan", "Japan"),
            ("world", "World"),
            ("asia", "Asia"),
            ("korea", "Korea"),
            ("china", "China"),
            ("australia", "Australia"),
            ("canada", "Canada"),
            ("brazil", "Brazil"),
            ("france", "France"),
            ("germany", "Germany"),
            ("italy", "Italy"),
            ("spain", "Spain"),
            ("netherlands", "Netherlands"),
            ("sweden", "Sweden"),
            ("russia", "Russia"),
            ("taiwan", "Taiwan"),
            ("hong_kong", "Hong Kong"),
            ("other", "Other"),
        ]
        saved_hidden_tags = self._settings.get("hidden_tags", [])
        if not isinstance(saved_hidden_tags, list):
            saved_hidden_tags = []
        saved_hidden_tags = set(t for t in saved_hidden_tags if isinstance(t, str))
        saved_regions = self._settings.get("show_regions", [])
        if not isinstance(saved_regions, list):
            saved_regions = []
        saved_regions = set(r for r in saved_regions if isinstance(r, str))
        self._show_tag_vars = {
            key: tk.BooleanVar(value=key in saved_hidden_tags)
            for key, _ in self._show_tag_specs
        }
        self._show_region_vars = {
            key: tk.BooleanVar(value=key in saved_regions)
            for key, _ in self._show_region_specs
        }
        self._extractors = find_archive_extractors()
        self._chdman_path = find_chdman_executable()
        self._extract_tool_var = tk.StringVar(value=format_extractor_status(self._extractors))
        self._extract_status_var = tk.StringVar(value="")
        self._chd_progress_var = tk.DoubleVar(value=0.0)
        self._extract_progress: dict[str, dict] = {}
        self._extract_request_queue: queue.Queue[str | None] = queue.Queue()
        self._extract_pending_ids: set[str] = set()
        self._extract_pending_lock = threading.Lock()
        self._queued_selected_ids: set[str] = set()
        self._left_loaded_nodes: set[str] = set()
        self._left_loading_nodes: set[str] = set()
        self._dl_speed_samples: dict[str, tuple[int, float]] = {}
        self._chd_download_in_progress = False
        self._chd_compress_in_progress = False
        self._dl_active_widgets: dict[str, dict] = {}
        self._dl_queued_widgets: dict[str, dict] = {}
        self._dl_done_widgets: dict[str, dict] = {}
        self._checked_hrefs: set[str] = set()
        self._sort_column = "name"
        self._sort_reverse = False
        self._setup_styles()
        self._setup_window_icon()
        self._build_ui()
        self._setup_global_shortcuts()
        self._setup_system_tray()
        self._download_dir.trace_add("write", self._on_download_dir_change)
        if self._compress_ps1_chd_var.get() and not self._chdman_path:
            self._ensure_chdman_available_async()
        self._restore_persisted_queue()
        self._extract_worker_thread = threading.Thread(target=self._extract_worker_loop, daemon=True)
        self._extract_worker_thread.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        saved_last_path = self._settings.get("last_path")
        if not isinstance(saved_last_path, str) or not saved_last_path.strip():
            saved_last_path = BROWSE_ROOT
        saved_last_query = self._settings.get("last_search_query")
        if not isinstance(saved_last_query, str):
            saved_last_query = ""
        self._load_left_tree(
            on_done=lambda: self._restore_left_tree_selection(saved_last_path)
        )
        self._navigate(saved_last_path, preserve_search=True, restore_query=saved_last_query)
        if self._start_minimized_var.get() or "--minimized" in sys.argv:
            self.after(100, self.iconify)
        self.after(100, self._run_startup_cleanup)
        self.after(2500, self._check_for_updates_async)

    def _setup_styles(self):
        setup_modern_styles(self)

    def _build_ui(self):
        toolbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(12, 8))
        toolbar.pack(fill="x", side="top")
        ttk.Label(toolbar, text="🗂  MiNERVA Archive Browser",
                  style="Accent.TLabel",
                  font=("TkDefaultFont", 12, "bold")).pack(side="left", padx=(2, 16))
        self._open_btn = ttk.Button(toolbar, text="🌐 Open in Browser",
                                    style="Toolbar.TButton", command=self._open_in_browser)
        self._open_btn.pack(side="left", padx=3)
        HoverTooltip(self._open_btn, "Open current folder in default web browser")

        self._open_dl_folder_btn = ttk.Button(toolbar, text="📁 Download Folder",
                                              style="Toolbar.TButton", command=self._open_current_downloads_folder)
        self._open_dl_folder_btn.pack(side="left", padx=3)
        HoverTooltip(self._open_dl_folder_btn, "Open target download folder on disk (Ctrl+O)")

        self._update_btn = ttk.Button(toolbar, text="🔄 Check Updates",
                                      style="Toolbar.TButton", command=self._check_for_update_button_click)
        self._update_btn.pack(side="left", padx=3)
        HoverTooltip(self._update_btn, "Check GitHub for latest releases")

        self._loading_label = ttk.Label(toolbar, text="", style="Loading.TLabel")
        self._loading_label.pack(side="right", padx=8)

        paned = ttk.PanedWindow(self, orient="horizontal")
        self._main_paned = paned
        paned.pack(fill="both", expand=True, padx=0, pady=(2, 0))

        left_frame = ttk.Frame(paned, style="Panel.TFrame", width=250)
        left_frame.pack_propagate(False)
        paned.add(left_frame, weight=0)
        ttk.Label(left_frame, text="Categories", background=PANEL, foreground=ACCENT,
                  font=("TkDefaultFont", 11, "bold"), padding=(10, 8)).pack(fill="x")
        left_scroll = ttk.Scrollbar(left_frame, orient="vertical")
        self._left_tree = ttk.Treeview(left_frame, style="Left.Treeview",
                                       yscrollcommand=left_scroll.set,
                                       show="tree", selectmode="browse")
        left_scroll.config(command=self._left_tree.yview)
        left_scroll.pack(side="right", fill="y")
        self._left_tree.pack(fill="both", expand=True)
        self._left_tree.bind("<<TreeviewSelect>>", self._on_left_select)
        self._left_tree.bind("<<TreeviewOpen>>", self._on_left_open)

        right_frame = ttk.Frame(paned, style="TFrame")
        paned.add(right_frame, weight=1)

        self._breadcrumb_frame = ttk.Frame(right_frame, padding=(10, 6))
        self._breadcrumb_frame.pack(fill="x")
        self._update_breadcrumb()

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)

        self._filter_bar = FilterBar(
            right_frame,
            search_var=self._search_var,
            on_search_change=self._on_search_change,
            on_clear_search=self._clear_search_and_focus,
            region_specs=self._show_region_specs,
            region_vars=self._show_region_vars,
            tag_specs=self._show_tag_specs,
            tag_vars=self._show_tag_vars,
            on_filter_change=self._on_filter_change,
        )
        self._filter_bar.pack(fill="x")
        self._search_entry = self._filter_bar.search_entry

        right_frame.bind("<Configure>", self._on_right_frame_configure)

        cols = ("check", "name", "size")
        right_scroll_y = ttk.Scrollbar(
            right_frame, orient="vertical", style="Visible.Vertical.TScrollbar"
        )
        right_scroll_x = ttk.Scrollbar(
            right_frame, orient="horizontal", style="Visible.Horizontal.TScrollbar"
        )
        self._right_tree = ttk.Treeview(right_frame, style="Right.Treeview",
                                        columns=cols, show="headings",
                                        yscrollcommand=right_scroll_y.set,
                                        xscrollcommand=right_scroll_x.set,
                                        selectmode="extended")
        right_scroll_y.config(command=self._right_tree.yview)
        right_scroll_x.config(command=self._right_tree.xview)
        self._right_tree.heading("check", text="☐", command=self._toggle_check_all_visible)
        self._right_tree.column("check", width=28, stretch=False, anchor="center", minwidth=28)
        self._right_tree.heading("name", text="Name ▲", command=lambda: self._sort_by_column("name"))
        self._right_tree.column("name", stretch=True, minwidth=200)
        self._right_tree.heading("size", text="Size", command=lambda: self._sort_by_column("size"))
        self._right_tree.column("size", width=100, stretch=False, anchor="e")
        right_scroll_y.pack(side="right", fill="y", padx=(0, 8))
        right_scroll_x.pack(side="bottom", fill="x", padx=(10, 8))
        self._right_tree.pack(fill="both", expand=True, padx=(10, 0), pady=(4, 0))
        self._right_tree.bind("<Double-1>", self._on_right_double_click)
        self._right_tree.bind("<Button-1>", self._on_right_click)
        self._right_tree.bind("<Button-3>", self._show_tree_context_menu)
        if sys.platform == "darwin":
            self._right_tree.bind("<Button-2>", self._show_tree_context_menu)

        self._sel_bar = tk.Frame(right_frame, bg=PANEL_ALT, pady=6, highlightbackground=BORDER, highlightthickness=1)

        self._sel_count_lbl = tk.Label(
            self._sel_bar, text="", bg=PANEL_ALT, fg=FG,
            font=("TkDefaultFont", 10, "bold")
        )
        self._sel_count_lbl.pack(side="left", padx=(12, 8))

        self._sel_queue_btn = ttk.Button(
            self._sel_bar, text="⬇ Queue Downloads",
            style="Primary.TButton",
            command=self._queue_checked_downloads
        )
        self._sel_queue_btn.pack(side="left", padx=4)

        ttk.Button(
            self._sel_bar, text="Select All Visible",
            style="Toolbar.TButton",
            command=self._select_all_visible
        ).pack(side="left", padx=4)

        ttk.Button(
            self._sel_bar, text="Invert Selection",
            style="Toolbar.TButton",
            command=self._invert_selection
        ).pack(side="left", padx=4)

        ttk.Button(
            self._sel_bar, text="✕ Clear Selection",
            style="Toolbar.TButton",
            command=self._clear_checked
        ).pack(side="left", padx=4)

        self._downloads_visible = bool(self._settings.get("downloads_panel_open", False))
        self._downloads_frame = tk.Frame(self, bg=PANEL)

        # Row 1: Save folder path & browse
        dir_row = tk.Frame(self._downloads_frame, bg=PANEL)
        dir_row.pack(fill="x", padx=10, pady=(6, 2))

        tk.Label(dir_row, text="Save to:", bg=PANEL, fg=FG_DIM,
                 font=("TkDefaultFont", 9)).pack(side="left", padx=(0, 4))
        dir_entry = tk.Entry(dir_row, textvariable=self._download_dir, width=1,
                             bg=ENTRY_BG, fg=FG, insertbackground=FG,
                             relief="flat", font=("TkDefaultFont", 9))
        dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        btn_browse = ttk.Button(dir_row, text="Browse…", style="Header.TButton",
                                command=self._browse_download_dir)
        btn_browse.pack(side="left", padx=(0, 4))
        HoverTooltip(btn_browse, "Choose download target directory")

        btn_open_hdr = ttk.Button(dir_row, text="Open Folder", style="Header.TButton",
                                  command=self._open_current_downloads_folder)
        btn_open_hdr.pack(side="left", padx=(0, 0))
        HoverTooltip(btn_open_hdr, "Open download folder on disk (Ctrl+O)")

        # Row 2: Concurrency & options toggles (responsive wrapping)
        opts_row = tk.Frame(self._downloads_frame, bg=PANEL)
        opts_row.pack(fill="x", padx=10, pady=(2, 2))

        spin_frame = tk.Frame(opts_row, bg=PANEL)
        spin_frame.pack(side="left", padx=(0, 8), anchor="w")
        tk.Label(spin_frame, text="Max concurrent:", bg=PANEL, fg=FG_DIM,
                 font=("TkDefaultFont", 9)).pack(side="left", padx=(0, 4))
        self._max_concurrent_var = tk.IntVar(value=self._get_saved_max_concurrent())
        max_spin = tk.Spinbox(spin_frame, from_=1, to=10, width=3,
                              textvariable=self._max_concurrent_var,
                              command=self._on_max_concurrent_change,
                              bg=ENTRY_BG, fg=FG, buttonbackground=PANEL,
                              relief="flat", font=("TkDefaultFont", 9))
        max_spin.pack(side="left")

        self._dl_opts_container = tk.Frame(opts_row, bg=PANEL)
        self._dl_opts_container.pack(side="left", fill="x", expand=True)
        self._dl_opt_widgets = []

        for text, var, cmd in [
            ("Compress to CHD", self._compress_ps1_chd_var, self._on_extract_defaults_change),
            ("Decompress archives", self._auto_extract_default_var, self._on_extract_defaults_change),
            ("Delete archive", self._delete_archive_default_var, self._on_extract_defaults_change),
            ("Autostart", self._autostart_var, self._on_startup_settings_change),
            ("Start minimized", self._start_minimized_var, self._on_startup_settings_change),
        ]:
            cb = tk.Checkbutton(
                self._dl_opts_container,
                text=text,
                variable=var,
                bg=PANEL, fg=FG, selectcolor=PANEL,
                activebackground=PANEL, activeforeground=FG,
                relief="flat", highlightthickness=1,
                highlightbackground=ACCENT, highlightcolor=ACCENT,
                command=cmd,
            )
            self._dl_opt_widgets.append(cb)

        # Row 3: Streamlined Primary Action Buttons & ROM Tools Dropdown
        self._dl_actions_frame = tk.Frame(self._downloads_frame, bg=PANEL)
        self._dl_actions_frame.pack(fill="x", padx=10, pady=(2, 4))
        self._dl_action_buttons = []

        for text, cmd, tip in [
            ("⏸ Pause / Resume All", self._toggle_pause_all_active, "Toggle pause/resume on all active downloads"),
            ("▶ Start All Queued", self._start_all_queued, "Start downloading all queued items"),
            ("Start Selected", self._start_selected_queued, "Start downloading checked items in queue"),
            ("✕ Clear Finished", self._clear_completed, "Clear finished and errored items from panel"),
            ("🛠 ROM Tools ▾", self._show_rom_tools_menu, "ROM compression, verification, and disc utilities"),
            ("Open Downloads", self._open_current_downloads_folder, "Open target download folder on disk (Ctrl+O)"),
            ("Open Extracted", self._open_current_extracted_folder, "Open folder containing extracted ROMs"),
        ]:
            b = ttk.Button(self._dl_actions_frame, text=text, style="Header.TButton", command=cmd)
            HoverTooltip(b, tip)
            self._dl_action_buttons.append(b)

        self._rom_tools_btn = self._dl_action_buttons[4]

        self._downloads_frame.bind("<Configure>", self._on_downloads_frame_configure)

        info_row = tk.Frame(self._downloads_frame, bg=PANEL)
        info_row.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(
            info_row,
            textvariable=self._extract_tool_var,
            bg=PANEL,
            fg=FG_DIM,
            font=("TkDefaultFont", 8),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            info_row,
            textvariable=self._extract_status_var,
            bg=PANEL,
            fg=ACCENT,
            font=("TkDefaultFont", 8),
            anchor="e",
        ).pack(side="right")
        ttk.Progressbar(
            info_row,
            variable=self._chd_progress_var,
            maximum=100,
            mode="determinate",
            length=180,
        ).pack(side="right", padx=(0, 8))

        tk.Frame(self._downloads_frame, bg=SEL_BG, height=1).pack(fill="x", padx=8)

        dl_canvas_frame = tk.Frame(self._downloads_frame, bg=PANEL)
        dl_canvas_frame.pack(fill="both", expand=True)
        dl_canvas = tk.Canvas(dl_canvas_frame, bg=PANEL, bd=0,
                              highlightthickness=0, height=180)
        dl_scrollbar = ttk.Scrollbar(
            dl_canvas_frame,
            orient="vertical",
            style="Visible.Vertical.TScrollbar",
            command=dl_canvas.yview
        )
        dl_scrollbar.pack(side="right", fill="y")
        dl_canvas.pack(side="left", fill="both", expand=True)
        dl_canvas.configure(yscrollcommand=dl_scrollbar.set)

        self._dl_inner = tk.Frame(dl_canvas, bg=PANEL)
        self._dl_canvas_window = dl_canvas.create_window(
            (0, 0), window=self._dl_inner, anchor="nw"
        )
        self._dl_inner.bind("<Configure>",
            lambda e: dl_canvas.configure(scrollregion=dl_canvas.bbox("all")))
        dl_canvas.bind("<Configure>",
            lambda e: dl_canvas.itemconfig(self._dl_canvas_window, width=e.width))

        self._downloads_toggle_btn = ttk.Button(
            self,
            text="📥 Downloads",
            style="Toolbar.TButton",
            command=self._toggle_downloads,
        )
        self._downloads_toggle_btn.pack(side="top", fill="x", padx=8, pady=(6, 0), before=self._main_paned)
        if self._downloads_visible:
            self._downloads_frame.pack(side="top", fill="x", pady=(6, 0), before=self._main_paned)

        self._poll_downloads()

        self._status_var = tk.StringVar(value="")
        status_bar = ttk.Label(
            self,
            textvariable=self._status_var,
            style="Status.TLabel",
            relief="flat",
            padding=(10, 5),
        )
        status_bar.pack(fill="x", side="bottom", pady=(0, 4))

    def _update_breadcrumb(self):
        for w in self._breadcrumb_frame.winfo_children():
            w.destroy()
        parts = [p for p in self._current_path.split("/") if p]
        paths = []
        cumulative = "/"
        for p in parts:
            cumulative = cumulative.rstrip("/") + "/" + p + "/"
            paths.append((urllib.parse.unquote(p), cumulative))

        for i, (label, path) in enumerate(paths):
            display = label if label != "browse" else "\U0001f3e0 Home"
            lbl = ttk.Label(self._breadcrumb_frame, text=display, style="BreadcrumbLink.TLabel")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, p=path: self._navigate(p))
            if i < len(paths) - 1:
                ttk.Label(self._breadcrumb_frame, text=" \u203a ", style="Breadcrumb.TLabel").pack(side="left")

    def _set_loading(self, loading: bool):
        self._loading_label.config(text="\u23f3 Loading\u2026" if loading else "")

    def _load_left_tree(self, on_done=None):
        self._set_loading(True)

        def worker():
            try:
                entries = fetch_entries(BROWSE_ROOT)
            except Exception as e:
                entries = []
                log_error("MinervaApp._load_left_tree failed", e)
                self.after(0, lambda err=e: self._show_error(str(err)))
            self.after(0, lambda: self._populate_left_tree(entries, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_left_tree(self, entries, on_done=None):
        self._set_loading(False)
        self._left_tree.delete(*self._left_tree.get_children())
        self._left_loaded_nodes.clear()
        self._left_loading_nodes.clear()
        self._left_loaded_nodes.add(BROWSE_ROOT)
        for e in entries:
            if e["is_folder"]:
                self._insert_left_folder("", e)
        if on_done:
            on_done()

    def _on_left_select(self, event):
        sel = self._left_tree.selection()
        if sel:
            path = sel[0]
            self._expand_left_path(path)
            if path == self._current_path:
                return
            self._navigate(path)

    def _on_left_open(self, event):
        path = self._left_tree.focus()
        if path:
            self._expand_left_path(path)

    def _insert_left_folder(self, parent_iid: str, entry: dict):
        display = "\U0001f4c1 " + entry["name"]
        iid = entry["href"]
        if self._left_tree.exists(iid):
            return
        self._left_tree.insert(parent_iid, "end", iid=iid, text=display, tags=("folder",))
        self._left_tree.insert(iid, "end", text="")

    def _expand_left_path(self, path: str, on_done=None):
        if path in self._left_loaded_nodes:
            if on_done:
                self.after(0, on_done)
            return
        if path in self._left_loading_nodes:
            return
        if not self._left_tree.exists(path):
            return
        self._left_loading_nodes.add(path)

        def worker():
            try:
                entries = fetch_entries(path)
                self.after(0, lambda: self._populate_left_children(path, entries, on_done))
            except Exception as e:
                log_error(f"MinervaApp._expand_left_path failed for path={path}", e)
                self.after(0, lambda: self._left_loading_nodes.discard(path))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_left_children(self, parent_path: str, entries: list[dict], on_done=None):
        self._left_loading_nodes.discard(parent_path)
        if not self._left_tree.exists(parent_path):
            return
        self._left_tree.delete(*self._left_tree.get_children(parent_path))
        for e in entries:
            if e.get("is_folder"):
                self._insert_left_folder(parent_path, e)
        self._left_loaded_nodes.add(parent_path)
        if on_done:
            on_done()

    def _left_tree_ancestor_chain(self, path: str) -> list[str]:
        prefix = "/browse/./"
        if not path.startswith(prefix):
            return []
        remainder = path[len(prefix):].strip("/")
        if not remainder:
            return []
        cumulative = prefix
        chain = []
        for part in remainder.split("/"):
            cumulative = cumulative + part + "/"
            chain.append(cumulative)
        return chain

    def _restore_left_tree_selection(self, path: str):
        chain = self._left_tree_ancestor_chain(path)
        if not chain:
            return

        def step(idx: int):
            if idx >= len(chain):
                return
            node = chain[idx]
            if not self._left_tree.exists(node):
                return
            if idx == len(chain) - 1:
                self._left_tree.selection_set(node)
                self._left_tree.see(node)
                return
            self._left_tree.item(node, open=True)
            self._expand_left_path(node, on_done=lambda: step(idx + 1))

        step(0)

    def _navigate(self, path, preserve_search=False, restore_query=""):
        self._current_path = path
        if preserve_search:
            self._search_var.set(restore_query)
        else:
            self._search_var.set("")
        self._update_breadcrumb()
        self._set_loading(True)
        self._right_tree.delete(*self._right_tree.get_children())
        self._checked_hrefs.clear()
        self._status_var.set("Loading\u2026")

        def worker():
            try:
                entries = fetch_entries(path)
                self.after(0, lambda: self._populate_right(entries))
            except Exception as e:
                log_error(f"MinervaApp._navigate failed for path={path}", e)
                self.after(0, lambda err=e: self._show_error(str(err)))

        threading.Thread(target=worker, daemon=True).start()
        self._save_settings()

    def _populate_right(self, entries):
        self._set_loading(False)
        self._all_entries = entries
        self._render_right_list()

    def _update_status(self, entries):
        folders = sum(1 for e in entries if e["is_folder"])
        files = sum(1 for e in entries if not e["is_folder"])
        parts = []
        if folders:
            parts.append(f"{folders} folder{'s' if folders != 1 else ''}")
        if files:
            parts.append(f"{files} file{'s' if files != 1 else ''}")
        total = len(entries)
        self._status_var.set(f"{', '.join(parts)} ({total} items total)  |  {self._current_path}")

    def _on_search_change(self, *_):
        self._render_right_list()
        if getattr(self, "_search_save_after_id", None):
            self.after_cancel(self._search_save_after_id)
        self._search_save_after_id = self.after(500, self._save_settings)

    def _on_filter_change(self):
        self._render_right_list()
        self._save_settings()

    def _render_right_list(self):
        query = self._search_var.get().lower()
        filtered = [e for e in self._all_entries if self._entry_matches_filters(e, query)]
        visible_files = [e for e in filtered if not e.get("is_folder", False)]

        # Apply column sorting
        if self._sort_column == "size":
            visible_files.sort(
                key=lambda e: _parse_size_bytes(e.get("size", "")),
                reverse=self._sort_reverse
            )
        else:
            visible_files.sort(
                key=lambda e: e.get("name", "").lower(),
                reverse=self._sort_reverse
            )

        self._right_tree.delete(*self._right_tree.get_children())
        visible_hrefs = {e["href"] for e in visible_files}
        self._checked_hrefs.intersection_update(visible_hrefs)
        seen_hrefs = set()
        for e in visible_files:
            if e["href"] in seen_hrefs:
                continue
            seen_hrefs.add(e["href"])
            icon = "📄 "
            self._right_tree.insert("", "end", iid=e["href"],
                                    values=("", icon + e["name"], e["size"]),
                                    tags=("file",))
            if e["href"] in self._checked_hrefs:
                self._right_tree.set(e["href"], "check", "✓")

        if not visible_files:
            has_filter = (
                bool(query)
                or any(v.get() for v in self._show_tag_vars.values())
                or any(v.get() for v in self._show_region_vars.values())
            )
            if has_filter and self._all_entries:
                self._right_tree.insert(
                    "", "end", iid="__empty_state__",
                    values=("", "🔍 No matching items. Click here to reset search and filters.", ""),
                    tags=("empty_state",)
                )

        self._update_sel_bar()
        self._update_status(visible_files)

    def _entry_matches_filters(self, entry: dict, query: str) -> bool:
        name = entry.get("name", "")
        low = name.lower()
        if query and query not in low:
            return False

        if not entry.get("is_folder", False):
            selected_tags = {key for key, var in self._show_tag_vars.items() if var.get()}
            selected_regions = {key for key, var in self._show_region_vars.items() if var.get()}

            if selected_tags:
                tags = self._detect_release_tags(low)
                if tags.intersection(selected_tags):
                    return False
            if selected_regions:
                regions = self._detect_regions(low)
                if not regions.intersection(selected_regions):
                    return False

        return True

    @staticmethod
    def _detect_release_tags(name_lower: str) -> set[str]:
        tags = set()
        if "(demo" in name_lower or " demo" in name_lower:
            tags.add("demo")
        if "(beta" in name_lower or " beta" in name_lower:
            tags.add("beta")
        if "(rev" in name_lower or "(revision" in name_lower:
            tags.add("revision")
        if "(proto" in name_lower or "prototype" in name_lower:
            tags.add("proto")
        if "(unl" in name_lower or "unlicensed" in name_lower:
            tags.add("unlicensed")
        if "(hack" in name_lower or "hack)" in name_lower:
            tags.add("hack")
        if "(translation" in name_lower or "(t+" in name_lower:
            tags.add("translation")
        return tags

    @staticmethod
    def _detect_regions(name_lower: str) -> set[str]:
        regions = set()

        def has_any(*needles: str) -> bool:
            return any(n in name_lower for n in needles)

        if has_any("(usa", "(us", "(u)", "usa/", "/usa", "usa,"):
            regions.add("usa")
        if has_any("(europe", "(eu", "(e)", "europe/", "/europe", "europe,"):
            regions.add("europe")
        if has_any("(japan", "(jp", "(j)", "japan/", "/japan", "japan,"):
            regions.add("japan")
        if has_any("(world", "(w)", "(global"):
            regions.add("world")
        if has_any("(asia", "(a)"):
            regions.add("asia")
        if has_any("(korea", "(kr", "(k)"):
            regions.add("korea")
        if has_any("(china", "(cn", "(c)"):
            regions.add("china")
        if has_any("(australia", "(au"):
            regions.add("australia")
        if has_any("(canada", "(ca"):
            regions.add("canada")
        if has_any("(brazil", "(br"):
            regions.add("brazil")
        if has_any("(france", "(fr", "(f)"):
            regions.add("france")
        if has_any("(germany", "(de", "(g)"):
            regions.add("germany")
        if has_any("(italy", "(it", "(i)"):
            regions.add("italy")
        if has_any("(spain", "(es", "(s)"):
            regions.add("spain")
        if has_any("(netherlands", "(nl"):
            regions.add("netherlands")
        if has_any("(sweden", "(se", "(sw)"):
            regions.add("sweden")
        if has_any("(russia", "(ru"):
            regions.add("russia")
        if has_any("(taiwan", "(tw"):
            regions.add("taiwan")
        if has_any("(hong kong", "(hk"):
            regions.add("hong_kong")

        for grp in re.findall(r"\(([^)]*)\)", name_lower):
            compact = re.sub(r"[^a-z]", "", grp)
            if compact in {"u", "e", "j", "w", "ue", "uj", "uw", "ej", "ew", "jw", "uej", "uew", "ujw", "ejw", "uejw"}:
                if "u" in compact:
                    regions.add("usa")
                if "e" in compact:
                    regions.add("europe")
                if "j" in compact:
                    regions.add("japan")
                if "w" in compact:
                    regions.add("world")

        if not regions:
            regions.add("other")
        return regions

    def _download_single_entry(self, entry: dict):
        if not _LT_AVAILABLE:
            messagebox.showinfo(
                "libtorrent required",
                "Install libtorrent to enable downloads:\n  pip install libtorrent",
            )
            return
        rom_id = extract_rom_id(entry["href"])
        if not rom_id:
            messagebox.showerror(
                "Download Failed", f"Could not determine rom id for {entry['name']}."
            )
            return
        file_name = entry["name"]
        save_path = self.get_download_dir()
        browse_path = self._current_path
        download_id = str(uuid.uuid4())
        threading.Thread(
            target=self._lookup_and_enqueue,
            args=(download_id, rom_id, file_name, save_path, browse_path),
            daemon=True,
        ).start()
        if not self._downloads_visible:
            self._toggle_downloads()

    def _on_right_double_click(self, event):
        sel = self._right_tree.selection()
        if not sel:
            return
        href = sel[0]
        if href == "__empty_state__":
            self._reset_all_filters()
            return
        entry = next((e for e in self._all_entries if e["href"] == href), None)
        if entry is None:
            return
        if entry["is_folder"]:
            self._navigate(href)
        else:
            self._download_single_entry(entry)

    def _show_tree_context_menu(self, event):
        iid = self._right_tree.identify_row(event.y)
        if iid == "__empty_state__":
            self._reset_all_filters()
            return
        if iid:
            current_sel = self._right_tree.selection()
            if iid not in current_sel:
                self._right_tree.selection_set(iid)

        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT, activeforeground=FG)
        sel = self._right_tree.selection()
        if sel:
            if len(self._checked_hrefs) > 1:
                menu.add_command(
                    label=f"⬇ Queue Selected ({len(self._checked_hrefs)} items)",
                    command=self._queue_checked_downloads,
                )
            else:
                entry = next((e for e in self._all_entries if e["href"] == sel[0]), None)
                if entry and not entry.get("is_folder", False):
                    menu.add_command(
                        label=f"⬇ Download '{entry['name'][:28]}…'" if len(entry['name']) > 28 else f"⬇ Download '{entry['name']}'",
                        command=lambda ent=entry: self._download_single_entry(ent),
                    )
            menu.add_command(label="🌐 Open in Web Browser", command=self._open_in_browser)
            menu.add_separator()
        menu.add_command(label="📁 Open Download Folder", command=self._open_current_downloads_folder)
        menu.add_command(label="📂 Open Extracted Folder", command=self._open_current_extracted_folder)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_in_browser(self):
        sel = self._right_tree.selection()
        if sel:
            href = sel[0]
            webbrowser.open(BASE_URL + href)
        else:
            webbrowser.open(BASE_URL + self._current_path)

    def get_torrent_engine(self, show_errors: bool = True) -> "TorrentEngine | None":
        if not _LT_AVAILABLE:
            if show_errors:
                messagebox.showinfo(
                    "libtorrent required",
                    "Install libtorrent to enable downloads:\n  pip install libtorrent",
                )
            return None
        if self._torrent_engine is None:
            try:
                self._torrent_engine = TorrentEngine()
                self._download_queue = DownloadQueue(
                    self._torrent_engine,
                    max_active=self._get_current_max_concurrent()
                )
            except Exception as e:
                log_error("MinervaApp.get_torrent_engine failed to start engine", e)
                if show_errors:
                    messagebox.showerror("Engine Error", f"Could not start torrent engine:\n{e}")
                return None
        return self._torrent_engine

    def get_download_dir(self) -> str:
        d = self._download_dir.get() or get_default_download_dir()
        return str(pathlib.Path(d).resolve())

    def enqueue_download(self, download_id: str, name: str, source: str, so_id: int, save_path: str):
        engine = self.get_torrent_engine()
        if engine is None:
            return
        if self._download_queue is not None:
            self._download_queue.enqueue(download_id, name, source, so_id, save_path)
            self._save_settings()
        if not self._downloads_visible:
            self._toggle_downloads()

    def _toggle_downloads(self):
        self._downloads_visible = not self._downloads_visible
        if self._downloads_visible:
            self._downloads_frame.pack(side="top", fill="x", pady=(6, 0), before=self._main_paned)
        else:
            self._downloads_frame.pack_forget()
        self._save_settings()

    def _refresh_toggle_label(self):
        if self._download_queue is None:
            self._downloads_toggle_btn.config(text="📥 Downloads")
            self.title(f"MiNERVA Archive Browser v{APP_VERSION}")
            return
        snap = self._download_queue.snapshot()
        n_active = len(snap["active"])
        n_pending = len(snap["pending"])
        n_done = len(snap["done"])

        total_speed = 0.0
        avg_progress = 0.0
        if self._torrent_engine:
            statuses = self._torrent_engine.get_all_statuses()
            active_speeds = [statuses[did]["download_rate"] for did in snap["active"] if did in statuses]
            active_progs = [statuses[did]["progress"] for did in snap["active"] if did in statuses]
            total_speed = sum(active_speeds)
            if active_progs:
                avg_progress = sum(active_progs) / len(active_progs)

        parts = []
        if n_active:
            if total_speed > 0:
                parts.append(f"{n_active} active • {_format_speed(total_speed)}")
            else:
                parts.append(f"{n_active} active")
        if n_pending:
            parts.append(f"{n_pending} queued")
        if n_done:
            parts.append(f"{n_done} done")
        label = "📥 Downloads"
        if parts:
            label += "  (" + "  •  ".join(parts) + ")"
        self._downloads_toggle_btn.config(text=label)

        if n_active > 0:
            pct = int(avg_progress * 100)
            self.title(f"[{pct}% @ {_format_speed(total_speed)}] MiNERVA Archive Browser v{APP_VERSION}")
        else:
            self.title(f"MiNERVA Archive Browser v{APP_VERSION}")

    def _poll_downloads(self):
        if self._torrent_engine is not None and self._download_queue is not None:
            finished_ids = []
            queue_changed = False
            while True:
                try:
                    event = self._torrent_engine.events.get_nowait()
                except queue.Empty:
                    break
                etype = event.get("type")
                did = event.get("id", "")
                if etype == "finished":
                    self._normalize_downloaded_file_location(did)
                    self._torrent_engine.stop_seeding(did)
                    self._download_queue.on_finished(did)
                    finished_ids.append(did)
                    queue_changed = True
                elif etype == "error":
                    self._download_queue.on_finished(did, error=event.get("msg", "Unknown error"))
                    queue_changed = True
            if finished_ids:
                self._prompt_post_download_actions_batch(finished_ids)
            if queue_changed:
                self._save_settings()

            snap = self._download_queue.snapshot()
            self._rebuild_dl_panel(snap)
            self._refresh_toggle_label()

        self.after(500, self._poll_downloads)

    def _normalize_downloaded_file_location(self, download_id: str):
        if not self._torrent_engine:
            return
        meta = self._torrent_engine._meta.get(download_id)
        if not meta:
            return
        file_name = meta.get("name", "")
        if not file_name:
            return
        save_path = pathlib.Path(meta.get("save_path", "")).resolve()
        if not str(save_path):
            return
        target = save_path / file_name
        if target.exists():
            return
        try:
            src = None
            for _ in range(10):
                src = self._find_downloaded_file(save_path, file_name)
                if src is not None:
                    break
                time.sleep(1)
            if src is None or src == target:
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            parent_dir = src.parent
            shutil.move(str(src), str(target))
            log_activity(f"download.flatten id={download_id} src='{src}' dst='{target}'")
            try:
                if parent_dir != save_path and not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
            except Exception:
                pass
        except Exception as e:
            log_error(
                f"MinervaApp._normalize_downloaded_file_location failed for {file_name}",
                e
            )

    def _rebuild_dl_panel(self, snap: dict):
        active_ids = set(snap["active"])
        pending_ids = {item["id"] for item in snap["pending"]}
        self._queued_selected_ids.intersection_update(pending_ids)
        statuses = self._torrent_engine.get_all_statuses() if self._torrent_engine else {}

        gone = [did for did in list(self._dl_active_widgets) if did not in active_ids]
        for did in gone:
            w = self._dl_active_widgets.pop(did)
            w["frame"].destroy()
            self._dl_speed_samples.pop(did, None)

        for did in snap["active"]:
            st = statuses.get(did, {})
            if did not in self._dl_active_widgets:
                self._make_active_row(did, st.get("name", did))
            self._update_active_row(did, st)

        if not hasattr(self, "_dl_queued_frame"):
            self._dl_queued_frame = tk.Frame(self._dl_inner, bg=PANEL)
            self._dl_queued_frame.pack(fill="x")
        pending_map = {item["id"]: item for item in snap["pending"]}
        gone_pending = [did for did in list(self._dl_queued_widgets) if did not in pending_map]
        for did in gone_pending:
            w = self._dl_queued_widgets.pop(did)
            w["frame"].destroy()

        if pending_map:
            if not hasattr(self, "_dl_queued_header") or not self._dl_queued_header.winfo_exists():
                self._dl_queued_header = tk.Label(
                    self._dl_queued_frame, text="  QUEUED",
                    bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 8, "bold")
                )
                self._dl_queued_header.pack(anchor="w", padx=6)
            for item in snap["pending"]:
                did = item["id"]
                if did not in self._dl_queued_widgets:
                    self._make_queued_row(self._dl_queued_frame, item)
                self._update_queued_row(did, item)
        else:
            if hasattr(self, "_dl_queued_header") and self._dl_queued_header.winfo_exists():
                self._dl_queued_header.destroy()
                del self._dl_queued_header

        if not hasattr(self, "_dl_done_frame"):
            self._dl_done_frame = tk.Frame(self._dl_inner, bg=PANEL)
            self._dl_done_frame.pack(fill="x")

        done_ids = {item["id"] for item in snap["done"]}
        gone_done = [did for did in list(self._dl_done_widgets) if did not in done_ids]
        for did in gone_done:
            w = self._dl_done_widgets.pop(did)
            w["frame"].destroy()

        if snap["done"]:
            if not hasattr(self, "_dl_done_header") or not self._dl_done_header.winfo_exists():
                self._dl_done_header = tk.Label(
                    self._dl_done_frame, text="  COMPLETED",
                    bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 8, "bold"))
                self._dl_done_header.pack(anchor="w", padx=6)
            for item in snap["done"]:
                did = item["id"]
                if did not in self._dl_done_widgets:
                    self._make_done_row(self._dl_done_frame, item)
        else:
            if hasattr(self, "_dl_done_header") and self._dl_done_header.winfo_exists():
                self._dl_done_header.destroy()
                del self._dl_done_header

    def _show_rom_tools_menu(self):
        callbacks = {
            "compress_chd": self._compress_ps1_button_click,
            "clean_bin_cue": self._clean_bin_cue_button_click,
            "clean_names": self._clean_chd_names_button_click,
            "verify_extracted": self._verify_extracted_button_click,
            "open_extracted": self._open_current_extracted_folder,
            "force_delete_bins": self._force_delete_bins_button_click,
        }
        ToolsMenu.show_menu(self, self._rom_tools_btn, callbacks)

    def _on_right_frame_configure(self, event):
        pass

    def _on_downloads_frame_configure(self, event):
        w = event.width
        if w < 50:
            return
        if hasattr(self, "_dl_opt_widgets") and self._dl_opt_widgets:
            opt_cols = max(2, min(len(self._dl_opt_widgets), max(1, (w - 180) // 150)))
            for i, cb in enumerate(self._dl_opt_widgets):
                cb.grid(row=i // opt_cols, column=i % opt_cols, sticky="w", padx=(4, 6), pady=1)

        if hasattr(self, "_dl_action_buttons") and self._dl_action_buttons:
            act_cols = max(2, min(len(self._dl_action_buttons), max(1, (w - 20) // 135)))
            for col in range(12):
                self._dl_actions_frame.columnconfigure(col, weight=0)
            for col in range(act_cols):
                self._dl_actions_frame.columnconfigure(col, weight=1)
            for i, btn in enumerate(self._dl_action_buttons):
                btn.grid(row=i // act_cols, column=i % act_cols, sticky="ew", padx=2, pady=2)

    def _make_active_row(self, did: str, name: str):
        row = tk.Frame(self._dl_inner, bg=PANEL)
        row.pack(fill="x", before=self._dl_queued_frame if hasattr(self, "_dl_queued_frame") else None)

        cancel_btn = ttk.Button(row, text="✕", width=2,
                                command=lambda d=did: self._cancel_download(d))
        cancel_btn.pack(side="right", padx=(0, 6))
        HoverTooltip(cancel_btn, "Cancel download")

        pause_btn = ttk.Button(row, text="⏸", width=3,
                               command=lambda d=did: self._toggle_pause(d))
        pause_btn.pack(side="right", padx=(2, 4))
        HoverTooltip(pause_btn, "Pause/Resume download")

        state_lbl = tk.Label(row, text="—", bg=PANEL, fg=FG,
                             font=("TkDefaultFont", 9), width=16, anchor="w")
        state_lbl.pack(side="right", padx=(4, 8))

        speed_lbl = tk.Label(row, text="↓ —", bg=PANEL, fg=FG_DIM,
                             font=("TkDefaultFont", 9), width=14, anchor="e")
        speed_lbl.pack(side="right", padx=(4, 6))

        pct_lbl = tk.Label(row, text="0%", bg=PANEL, fg=FG,
                           font=("TkDefaultFont", 9), width=5, anchor="e")
        pct_lbl.pack(side="right", padx=(0, 6))

        pv = tk.DoubleVar(value=0)
        pb = ttk.Progressbar(row, variable=pv, maximum=100,
                             mode="determinate", length=110)
        pb.pack(side="right", padx=(0, 8))

        name_lbl = tk.Label(row, text="📄 " + name,
                            bg=PANEL, fg=FG, font=("TkDefaultFont", 9), anchor="w")
        name_lbl.pack(side="left", fill="x", expand=True, padx=(8, 6))

        self._dl_active_widgets[did] = {
            "frame": row,
            "pv": pv,
            "pct_lbl": pct_lbl,
            "speed_lbl": speed_lbl,
            "state_lbl": state_lbl,
            "pause_btn": pause_btn,
        }

    def _update_active_row(self, did: str, st: dict):
        w = self._dl_active_widgets.get(did)
        if not w:
            return
        pct = st.get("progress", 0) * 100
        w["pv"].set(pct)
        w["pct_lbl"].config(text=f"{pct:.0f}%")
        state = st.get("state", "—")
        paused = st.get("paused", False)
        total_done = int(st.get("total_done", 0) or 0)
        now = time.monotonic()
        sample = self._dl_speed_samples.get(did)
        calc_rate = 0.0
        if sample is not None:
            prev_done, prev_ts = sample
            delta_bytes = max(0, total_done - prev_done)
            delta_t = max(1e-6, now - prev_ts)
            calc_rate = delta_bytes / delta_t
        self._dl_speed_samples[did] = (total_done, now)
        raw_rate = float(st.get("download_rate", 0) or 0.0)
        rate = calc_rate if (state == "Downloading" and not paused and calc_rate > 0) else raw_rate
        w["speed_lbl"].config(text=self._fmt_rate(rate))
        w["state_lbl"].config(text=state)
        w["pause_btn"].config(text="▶" if paused else "⏸")

    def _make_queued_row(self, parent: tk.Frame, item: dict):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x")
        did = item["id"]
        is_selected = did in self._queued_selected_ids
        sel_var = tk.BooleanVar(value=is_selected)

        cb = tk.Checkbutton(
            row,
            variable=sel_var,
            bg=PANEL,
            fg=FG,
            selectcolor=PANEL,
            activebackground=PANEL,
            activeforeground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=ACCENT,
            highlightcolor=ACCENT,
            command=lambda d=did, v=sel_var: self._set_queued_selected(d, v.get())
        )
        cb.pack(side="left", padx=(4, 2))

        btn_cancel = ttk.Button(row, text="✕", width=2,
                                command=lambda d=did: self._cancel_download(d))
        btn_cancel.pack(side="right", padx=(0, 6))
        HoverTooltip(btn_cancel, "Cancel download")

        btn_start = ttk.Button(row, text="Start", width=5,
                               command=lambda d=did: self._start_specific_queued(d))
        btn_start.pack(side="right", padx=(0, 4))
        HoverTooltip(btn_start, "Start download now")

        btn_down = ttk.Button(row, text="▼", width=2,
                              command=lambda d=did: self._move_queued_down(d))
        btn_down.pack(side="right", padx=1)
        HoverTooltip(btn_down, "Move down in queue")

        btn_up = ttk.Button(row, text="▲", width=2,
                            command=lambda d=did: self._move_queued_up(d))
        btn_up.pack(side="right", padx=1)
        HoverTooltip(btn_up, "Move up in queue")

        state_lbl = tk.Label(row, text="Queued", bg=PANEL, fg=FG_DIM,
                             font=("TkDefaultFont", 9), anchor="w", width=12)
        state_lbl.pack(side="right", padx=(4, 8))

        name = item["name"]
        name_lbl = tk.Label(row, text="🕐 " + name,
                            bg=PANEL, fg=FG_DIM, font=("TkDefaultFont", 9), anchor="w")
        name_lbl.pack(side="left", fill="x", expand=True, padx=(4, 4))

        self._dl_queued_widgets[did] = {
            "frame": row,
            "sel_var": sel_var,
            "state_lbl": state_lbl,
        }

    def _update_queued_row(self, download_id: str, item: dict):
        w = self._dl_queued_widgets.get(download_id)
        if not w:
            return
        try:
            w["sel_var"].set(download_id in self._queued_selected_ids)
            status = "Ready" if item.get("start_requested") else "Queued"
            w["state_lbl"].config(text=status)
        except tk.TclError:
            pass

    def _make_done_row(self, parent: tk.Frame, item: dict):
        did = item["id"]
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x")

        open_extracted_btn = ttk.Button(
            row,
            text="Extracted",
            width=9,
            command=lambda p=item.get("save_path", ""): self._open_folder(pathlib.Path(p) / "extracted")
        )
        open_extracted_btn.pack(side="right", padx=(0, 6))
        HoverTooltip(open_extracted_btn, "Open folder containing extracted ROMs")

        open_btn = ttk.Button(
            row,
            text="Open",
            width=6,
            command=lambda p=item.get("save_path", ""): self._open_folder(pathlib.Path(p))
        )
        open_btn.pack(side="right", padx=(0, 4))
        HoverTooltip(open_btn, "Open download folder on disk (Ctrl+O)")

        ext_lbl = tk.Label(row, text="", bg=PANEL, fg=ACCENT,
                           font=("TkDefaultFont", 8), width=18, anchor="e")
        ext_lbl.pack(side="right", padx=(0, 6))

        ext_pv = tk.DoubleVar(value=0)
        ext_pb = ttk.Progressbar(row, variable=ext_pv, maximum=100,
                                 mode="determinate", length=90)
        ext_pb.pack(side="right", padx=(0, 6))

        icon = "✅" if item["status"] == "done" else "❌"
        name = item["name"]
        clr = FG if item["status"] == "done" else "#f38ba8"
        lbl_text = f"{icon} " + name
        if item.get("error"):
            lbl_text += f"  ({item['error']})"

        name_lbl = tk.Label(row, text=lbl_text, bg=PANEL, fg=clr,
                            font=("TkDefaultFont", 9), anchor="w")
        name_lbl.pack(side="left", fill="x", expand=True, padx=(6, 4))

        self._dl_done_widgets[did] = {
            "frame": row,
            "ext_pv": ext_pv,
            "ext_pb": ext_pb,
            "ext_lbl": ext_lbl,
        }

        if did in self._extract_progress:
            self._apply_extract_progress(did)

    def _refresh_extract_rows(self):
        for did in list(self._extract_progress.keys()):
            self._apply_extract_progress(did)

    def _apply_extract_progress(self, did: str):
        w = self._dl_done_widgets.get(did)
        info = self._extract_progress.get(did)
        if not w or not info:
            return
        pct = info.get("pct", 0)
        status = info.get("status", "")
        try:
            w["ext_pv"].set(pct)
            w["ext_lbl"].config(text=status)
        except tk.TclError:
            pass

    @staticmethod
    def _fmt_rate(r: float) -> str:
        if r <= 0:
            return "↓ —"
        if r < 1024:
            return f"↓ {r:.0f} B/s"
        if r < 1024 * 1024:
            return f"↓ {r / 1024:.1f} KB/s"
        return f"↓ {r / 1024 / 1024:.1f} MB/s"

    def _on_max_concurrent_change(self):
        if self._download_queue is not None:
            try:
                n = int(self._max_concurrent_var.get())
                self._download_queue.set_max_active(n)
            except (ValueError, tk.TclError):
                pass
        self._save_settings()

    @staticmethod
    def _sanitize_max_concurrent(value, fallback: int = 3) -> int:
        try:
            return max(1, min(10, int(value)))
        except (TypeError, ValueError):
            return fallback

    def _get_saved_max_concurrent(self) -> int:
        return self._sanitize_max_concurrent(self._settings.get("max_concurrent"), fallback=3)

    def _get_current_max_concurrent(self) -> int:
        try:
            return self._sanitize_max_concurrent(self._max_concurrent_var.get(), fallback=3)
        except tk.TclError:
            return self._get_saved_max_concurrent()

    def _collect_settings(self) -> dict:
        hidden_tags = [key for key, var in self._show_tag_vars.items() if var.get()]
        show_regions = [key for key, var in self._show_region_vars.items() if var.get()]
        return {
            "download_dir": self.get_download_dir(),
            "max_concurrent": self._get_current_max_concurrent(),
            "hidden_tags": hidden_tags,
            "show_regions": show_regions,
            "downloads_panel_open": bool(self._downloads_visible),
            "auto_extract_default": bool(self._auto_extract_default_var.get()),
            "delete_archive_default": bool(self._delete_archive_default_var.get()),
            "compress_ps1_chd": bool(self._compress_ps1_chd_var.get()),
            "autostart_with_windows": bool(self._autostart_var.get()),
            "start_minimized": bool(self._start_minimized_var.get()),
            "download_queue": self._get_persisted_queue_for_settings(),
            "last_path": self._current_path,
            "last_search_query": self._search_var.get(),
        }

    def _save_settings(self):
        settings = self._collect_settings()
        save_app_settings(settings)
        self._settings = settings

    @staticmethod
    def _normalize_queue_item(raw: dict) -> dict | None:
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        source = raw.get("source")
        save_path = raw.get("save_path")
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(source, str) or not source.strip():
            return None
        if not isinstance(save_path, str) or not save_path.strip():
            return None
        try:
            so_id = int(raw.get("so_id"))
        except (TypeError, ValueError):
            return None
        if so_id < 0:
            return None
        download_id = raw.get("id")
        if not isinstance(download_id, str) or not download_id.strip():
            download_id = str(uuid.uuid4())
        return {
            "id": download_id,
            "name": name,
            "source": source,
            "so_id": so_id,
            "save_path": save_path,
            "start_requested": bool(raw.get("start_requested", False)),
        }

    def _get_persisted_queue_for_settings(self) -> list[dict]:
        if self._download_queue is not None:
            return self._download_queue.export_for_persistence()
        existing = self._settings.get("download_queue", [])
        if not isinstance(existing, list):
            return []
        cleaned: list[dict] = []
        for raw in existing:
            item = self._normalize_queue_item(raw)
            if item is not None:
                cleaned.append(item)
        return cleaned

    def _restore_persisted_queue(self):
        saved = self._settings.get("download_queue", [])
        if not isinstance(saved, list) or not saved:
            return
        if not _LT_AVAILABLE:
            return
        engine = self.get_torrent_engine(show_errors=False)
        if engine is None or self._download_queue is None:
            return

        requested_ids: list[str] = []
        seen_ids: set[str] = set()
        restored_any = False
        for raw in saved:
            item = self._normalize_queue_item(raw)
            if item is None:
                continue
            did = item["id"]
            while did in seen_ids:
                did = str(uuid.uuid4())
            seen_ids.add(did)
            item["id"] = did
            self._download_queue.enqueue(
                did,
                item["name"],
                item["source"],
                item["so_id"],
                item["save_path"],
            )
            restored_any = True
            if item["start_requested"]:
                requested_ids.append(did)

        if requested_ids:
            self._download_queue.start_selected(requested_ids)
        if restored_any:
            self._refresh_toggle_label()
            self._save_settings()

    def _on_download_dir_change(self, *_):
        self._save_settings()

    def _on_extract_defaults_change(self):
        if self._compress_ps1_chd_var.get() and not self._chdman_path:
            self._extract_status_var.set("PS1/PS2→CHD enabled but chdman.exe not found")
            self._ensure_chdman_available_async()
        elif self._chdman_path:
            self._extract_status_var.set(f"CHD tool: {self._chdman_path}")
        else:
            self._extract_status_var.set("")
        self._save_settings()

    def _on_startup_settings_change(self):
        self._apply_autostart(self._autostart_var.get())
        self._save_settings()

    def _apply_autostart(self, enabled: bool):
        if winreg is None:
            return
        _AUTOSTART_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
        _APP_NAME = "MiNERVA Browser"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if enabled and getattr(sys, "frozen", False):
                    winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --minimized')
                else:
                    try:
                        winreg.DeleteValue(key, _APP_NAME)
                    except FileNotFoundError:
                        pass
        except Exception as e:
            log_error("MinervaApp._apply_autostart failed", e)

    def _ensure_chdman_available_async(self):
        if self._chd_download_in_progress or self._chdman_path:
            return
        self._chd_download_in_progress = True
        self._extract_status_var.set("Installing CHD tool (chdman)…")

        def worker():
            path = self._auto_install_chdman()
            self.after(0, lambda p=path: self._finish_chdman_install(p))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_install_chdman(self) -> str | None:
        found = find_chdman_executable()
        if found:
            return found

        if not IS_WINDOWS:
            log_activity("chd.install.skip reason=non_windows_platform")
            return None

        base = get_runtime_base_dir()
        tmp_root = base / "_chdman_install_tmp"
        pkg_path = tmp_root / "mame_release_windows_x64.exe"
        extract_dir = tmp_root / "extracted"
        out_dir = base / "tools" / "chdman"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "chdman.exe"

        try:
            if tmp_root.exists():
                shutil.rmtree(tmp_root, ignore_errors=True)
            tmp_root.mkdir(parents=True, exist_ok=True)
            extract_dir.mkdir(parents=True, exist_ok=True)

            release_url = "https://www.mamedev.org/release.html"
            req = urllib.request.Request(release_url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            links = re.findall(r'href="([^"]*mame\d+b_(?:x64|64bit)\.exe[^"]*)"', html, flags=re.IGNORECASE)
            if not links:
                raise RuntimeError("Could not find Windows x64 MAME binary link on release page")
            mame_url = urllib.parse.urljoin(release_url, links[0].split('"')[0])
            log_activity(f"chd.install.download url='{mame_url}'")

            dl_req = urllib.request.Request(mame_url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
            with urllib.request.urlopen(dl_req, timeout=120) as resp, pkg_path.open("wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)

            if not pkg_path.exists() or pkg_path.stat().st_size <= 0:
                raise RuntimeError("Downloaded MAME package is empty")

            startupinfo = _windows_startupinfo()

            extracted_ok = False
            last_err = ""
            for tool in self._extractors:
                if tool["kind"] in ("7zip", "peazip"):
                    cmd = [tool["exe"], "x", "-y", "-aoa", f"-o{extract_dir}", str(pkg_path)]
                elif tool["kind"] == "winrar":
                    cmd = [tool["exe"], "x", "-y", "-o+", str(pkg_path), str(extract_dir) + "\\"]
                else:
                    continue
                log_activity(f"chd.install.extract tool={tool['label']} cmd={' '.join(cmd)}")
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    startupinfo=startupinfo,
                )
                if proc.returncode == 0:
                    extracted_ok = True
                    break
                tail = " | ".join((proc.stdout or "").splitlines()[-3:])
                last_err = f"{tool['label']} rc={proc.returncode}" + (f" ({tail})" if tail else "")

            if not extracted_ok:
                # MAME Windows package is a 7z self-extracting archive (SFX) that can extract directly
                try:
                    log_activity("chd.install.extract trying direct SFX run")
                    proc = subprocess.run(
                        [str(pkg_path), "-y", f"-o{extract_dir}"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        startupinfo=startupinfo,
                    )
                    if proc.returncode == 0:
                        extracted_ok = True
                except Exception as e:
                    last_err = str(e)

            if not extracted_ok:
                raise RuntimeError(f"Failed to extract MAME package ({last_err or 'no extractor available'})")

            found_chd = next((p for p in extract_dir.rglob("chdman.exe") if p.is_file()), None)
            if found_chd is None:
                raise RuntimeError("chdman.exe was not found in extracted MAME package")

            shutil.copy2(found_chd, out_path)
            if not out_path.exists() or out_path.stat().st_size <= 0:
                raise RuntimeError("Failed to place chdman.exe in tools folder")
            log_activity(f"chd.install.ok copied='{out_path}'")
            return str(out_path)
        except Exception as e:
            log_error("MinervaApp._auto_install_chdman failed", e)
            return find_chdman_executable()
        finally:
            try:
                if tmp_root.exists():
                    shutil.rmtree(tmp_root, ignore_errors=True)
            except Exception:
                pass

    def _finish_chdman_install(self, path: str | None):
        self._chd_download_in_progress = False
        self._chdman_path = path
        if path:
            self._extract_status_var.set(f"CHD tool ready: {path}")
            log_activity(f"chd.install.ok path='{path}'")
        elif not IS_WINDOWS:
            self._extract_status_var.set(
                "chdman not found. Install MAME tools (e.g. 'sudo pacman -S mame-tools', "
                "'sudo apt install mame-tools', or 'brew install mame') and retry."
            )
            log_activity("chd.install.fail non_windows_no_chdman")
        else:
            self._extract_status_var.set("Could not auto-install chdman. Install MAME and retry.")
            log_activity("chd.install.fail no_path")

    def _start_selected_queued(self):
        if not self._download_queue or not self._queued_selected_ids:
            return
        selected_ids = list(self._queued_selected_ids)
        self._download_queue.start_selected(selected_ids)
        for did in selected_ids:
            self._queued_selected_ids.discard(did)
        self._save_settings()

    def _start_specific_queued(self, download_id: str):
        if not self._download_queue:
            return
        self._download_queue.start_selected([download_id])
        self._queued_selected_ids.discard(download_id)
        self._save_settings()

    def _start_all_queued(self):
        if not self._download_queue:
            return
        self._download_queue.start_all_pending()
        self._save_settings()

    def _set_queued_selected(self, download_id: str, selected: bool):
        if selected:
            self._queued_selected_ids.add(download_id)
        else:
            self._queued_selected_ids.discard(download_id)

    def _toggle_pause_all_active(self):
        if not self._download_queue or not self._torrent_engine:
            return
        snap = self._download_queue.snapshot()
        active_ids = list(snap["active"])
        if not active_ids:
            return
        statuses = self._torrent_engine.get_all_statuses()
        should_pause = any(not statuses.get(did, {}).get("paused", False) for did in active_ids)
        for did in active_ids:
            if should_pause:
                self._torrent_engine.pause(did)
            else:
                self._torrent_engine.resume(did)

    def _browse_download_dir(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self, initialdir=self.get_download_dir())
        if path:
            self._download_dir.set(path)
            self._save_settings()

    def _clear_completed(self):
        if self._download_queue:
            self._download_queue.clear_done()
        for did, w in list(self._dl_done_widgets.items()):
            try:
                w["frame"].destroy()
            except tk.TclError:
                pass
        self._dl_done_widgets.clear()
        self._extract_progress.clear()
        if hasattr(self, "_dl_done_header") and self._dl_done_header.winfo_exists():
            self._dl_done_header.destroy()
            del self._dl_done_header

    def _prompt_post_download_actions_batch(self, download_ids: list[str]):
        if not self._torrent_engine:
            return
        valid_items: list[tuple[str, dict]] = []
        for did in download_ids:
            meta = self._torrent_engine._meta.get(did)
            if meta:
                valid_items.append((did, meta))

        if not valid_items:
            return

        compress_chd = bool(self._compress_ps1_chd_var.get())
        decompress = bool(self._auto_extract_default_var.get())

        if not compress_chd and not decompress:
            return

        delete_archive = bool(self._delete_archive_default_var.get())
        for did, meta in valid_items:
            meta["auto_extract"] = bool(decompress)
            meta["compress_chd"] = bool(compress_chd)
            meta["delete_archive"] = bool(delete_archive)
            self._extract_download(did)

    def _open_current_downloads_folder(self):
        self._open_folder(pathlib.Path(self.get_download_dir()))

    def _open_current_extracted_folder(self):
        self._open_folder(pathlib.Path(self.get_download_dir()) / "extracted")

    def _verify_extracted_button_click(self):
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo("Verify Extracted", "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror("Verify Extracted", "Extracted path exists but is not a folder.")
            return

        targets = [d for d in base.iterdir() if d.is_dir()]
        if not targets:
            messagebox.showinfo("Verify Extracted", "No extracted game folders found.")
            return

        ok = 0
        failed: list[str] = []
        for d in targets:
            try:
                verify_extracted_output(d, d.name)
                ok += 1
            except Exception as e:
                failed.append(f"{d.name}: {e}")

        total = len(targets)
        if not failed:
            msg = f"Verified {ok}/{total} extracted folders successfully."
            self._extract_status_var.set(msg)
            messagebox.showinfo("Verify Extracted", msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = f"Verified {ok}/{total}. Failed: {len(failed)}.\n\n{preview}{more}"
        self._extract_status_var.set(f"Verify failed: {len(failed)} folder(s)")
        messagebox.showwarning("Verify Extracted", msg)

    def _compress_ps1_button_click(self):
        if self._chd_compress_in_progress:
            messagebox.showinfo("Compress PS1/PS2 to CHD", "CHD compression is already running.")
            return
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists() or not base.is_dir():
            messagebox.showinfo("Compress PS1/PS2 to CHD", "No extracted folder found yet.")
            return
        if not self._chdman_path:
            self._ensure_chdman_available_async()
            messagebox.showinfo(
                "Compress PS1/PS2 to CHD",
                "chdman is not installed yet. Installation has started in the background."
            )
            return

        targets = [d for d in base.iterdir() if d.is_dir()]
        if not targets:
            messagebox.showinfo("Compress PS1/PS2 to CHD", "No extracted game folders found.")
            return
        self._chd_compress_in_progress = True
        self._chd_progress_var.set(0.0)
        self._extract_status_var.set("CHD compression running…")

        def worker():
            converted = 0
            failed: list[str] = []
            total_done = 0
            total_planned = 0
            for d in targets:
                total_planned += len(collect_chd_sources(d))

            for d in targets:
                try:
                    def _manual_progress(done: int, total: int, cue_name: str):
                        display_done = total_done + done
                        display_total = max(total_planned, display_done)
                        self.after(
                            0,
                            lambda dd=display_done, dt=display_total, cn=cue_name:
                                self._update_chd_progress(dd, dt, cn)
                        )

                    made = compress_ps1_to_chd(d, self._chdman_path, progress_cb=_manual_progress)
                    converted += made
                    total_done += made
                except Exception as e:
                    failed.append(f"{d.name}: {e}")
            renamed, unchanged, cleanup_failed = clean_chd_names_in_base(base)
            if cleanup_failed:
                failed.append(
                    f"name cleanup: {len(cleanup_failed)} issue(s) after renaming {renamed} item(s)"
                )
            log_activity(
                f"chd.clean_names.manual renamed={renamed} unchanged={unchanged} failed={len(cleanup_failed)}"
            )
            self.after(0, lambda c=converted, f=failed, t=len(targets): self._finish_manual_chd_batch(c, f, t))

        threading.Thread(target=worker, daemon=True).start()

    def _clean_bin_cue_button_click(self):
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo("Clean BIN/CUE", "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror("Clean BIN/CUE", "Extracted path exists but is not a folder.")
            return

        chd_files = [p for p in base.rglob("*.chd") if p.is_file()]
        if not chd_files:
            messagebox.showinfo("Clean BIN/CUE", "No CHD files found under extracted folder.")
            return

        removed_bins = 0
        removed_cues = 0
        failed: list[str] = []
        for chd in chd_files:
            cue = chd.with_suffix(".cue")
            bin_file = chd.with_suffix(".bin")
            if cue.exists():
                try:
                    cue.unlink()
                    removed_cues += 1
                except Exception as e:
                    failed.append(f"{cue.name}: {e}")
            if bin_file.exists():
                try:
                    bin_file.unlink()
                    removed_bins += 1
                except Exception as e:
                    failed.append(f"{bin_file.name}: {e}")

        if not failed:
            msg = f"Cleanup complete. Removed {removed_bins} BIN and {removed_cues} CUE files."
            self._extract_status_var.set(msg)
            messagebox.showinfo("Clean BIN/CUE", msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = (
            f"Cleanup completed with issues.\n"
            f"Removed {removed_bins} BIN and {removed_cues} CUE files.\n"
            f"Failed: {len(failed)}\n\n{preview}{more}"
        )
        self._extract_status_var.set(f"BIN/CUE cleanup issues: {len(failed)} file(s)")
        messagebox.showwarning("Clean BIN/CUE", msg)

    def _clean_chd_names_button_click(self):
        TITLE = "Clean Names"
        FILE_EXTS = {".chd", ".bin", ".cue", ".iso", ".img", ".mdf", ".mds"}
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo(TITLE, "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror(TITLE, "Extracted path exists but is not a folder.")
            return

        renamed, unchanged, failed = clean_chd_names_in_base(base, file_exts=FILE_EXTS)
        if not failed:
            msg = f"Name cleanup complete. Renamed {renamed}, unchanged {unchanged}."
            self._extract_status_var.set(msg)
            messagebox.showinfo(TITLE, msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = (
            f"Name cleanup completed with issues.\n"
            f"Renamed: {renamed}, Unchanged: {unchanged}, Failed: {len(failed)}\n\n"
            f"{preview}{more}"
        )
        self._extract_status_var.set(f"Name cleanup issues: {len(failed)} file(s)")
        messagebox.showwarning(TITLE, msg)

    def _run_startup_cleanup(self):
        try:
            download_dir = self.get_download_dir()
            base = pathlib.Path(download_dir) / "extracted"
            if not base.exists() or not base.is_dir():
                return

            chd_files = list(base.rglob("*.chd"))
            if not chd_files:
                return

            log_activity(f"startup.cleanup detected {len(chd_files)} CHD files")

            bin_files = list(base.rglob("*.bin"))
            cue_files = list(base.rglob("*.cue"))
            iso_files = list(base.rglob("*.iso"))
            source_count = len(bin_files) + len(cue_files) + len(iso_files)

            if source_count > 0:
                renamed, unchanged, cleanup_failed = clean_chd_names_in_base(base)
                log_activity(
                    f"startup.cleanup names renamed={renamed} unchanged={unchanged} failed={len(cleanup_failed)}"
                )

            removed_bins = 0
            removed_cues = 0
            removed_isos = 0
            delete_failed: list[str] = []

            for chd in chd_files:
                cue = chd.with_suffix(".cue")
                bin_file = chd.with_suffix(".bin")
                iso_file = chd.with_suffix(".iso")

                if cue.exists():
                    try:
                        cue.unlink()
                        removed_cues += 1
                        log_activity(f"startup.cleanup.delete cue='{cue}'")
                    except Exception as e:
                        delete_failed.append(f"{cue.name}: {e}")
                        log_activity(f"startup.cleanup.delete_failed cue='{cue}' err={e}")

                if bin_file.exists():
                    try:
                        bin_file.unlink()
                        removed_bins += 1
                        log_activity(f"startup.cleanup.delete bin='{bin_file}'")
                    except Exception as e:
                        delete_failed.append(f"{bin_file.name}: {e}")
                        log_activity(f"startup.cleanup.delete_failed bin='{bin_file}' err={e}")

                if iso_file.exists():
                    try:
                        iso_file.unlink()
                        removed_isos += 1
                        log_activity(f"startup.cleanup.delete iso='{iso_file}'")
                    except Exception as e:
                        delete_failed.append(f"{iso_file.name}: {e}")
                        log_activity(f"startup.cleanup.delete_failed iso='{iso_file}' err={e}")

            if removed_bins > 0 or removed_cues > 0 or removed_isos > 0:
                msg = f"Cleanup: Removed {removed_bins} BIN, {removed_cues} CUE, {removed_isos} ISO files"
                log_activity(f"startup.cleanup.done {msg}")

        except Exception as e:
            log_error("MinervaApp._run_startup_cleanup failed", e)

    def _force_delete_bins_button_click(self):
        TITLE = "Delete BINs"
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo(TITLE, "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror(TITLE, "Extracted path exists but is not a folder.")
            return

        bin_files = [p for p in base.rglob("*.bin") if p.is_file()]
        if not bin_files:
            messagebox.showinfo(TITLE, "No BIN files found under extracted folder.")
            return

        if not messagebox.askyesno(
            TITLE,
            f"Permanently delete {len(bin_files)} BIN file(s) under:\n{base}\n\nThis cannot be undone.",
        ):
            return

        deleted = 0
        failed: list[str] = []
        for f in bin_files:
            try:
                f.unlink()
                deleted += 1
                log_activity(f"force_delete_bin removed='{f}'")
            except Exception as e:
                failed.append(f"{f.name}: {e}")

        if not failed:
            msg = f"Deleted {deleted} BIN file(s)."
            self._extract_status_var.set(msg)
            messagebox.showinfo(TITLE, msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = f"Deleted {deleted}, failed {len(failed)}:\n\n{preview}{more}"
        self._extract_status_var.set(f"Delete BINs: {len(failed)} failed")
        messagebox.showwarning(TITLE, msg)

    def _update_chd_progress(self, done: int, total: int, cue_name: str):
        self._extract_status_var.set(f"CHD converting {done}/{total}: {cue_name}")
        self._chd_progress_var.set(0.0 if total <= 0 else (done * 100.0 / total))

    def _finish_manual_chd_batch(self, converted: int, failed: list[str], total_folders: int):
        self._chd_compress_in_progress = False
        self._chd_progress_var.set(100.0)
        if not failed:
            msg = f"CHD compression finished: {converted} file(s) converted across {total_folders} folder(s)."
            self._extract_status_var.set(msg)
            messagebox.showinfo("Compress PS1/PS2 to CHD", msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = (
            f"CHD conversion completed with issues.\n"
            f"Converted: {converted} file(s), Failed folders: {len(failed)}.\n\n"
            f"{preview}{more}"
        )
        self._extract_status_var.set(f"CHD conversion issues: {len(failed)} folder(s)")
        messagebox.showwarning("Compress PS1/PS2 to CHD", msg)

    def _open_folder(self, path: pathlib.Path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            if IS_WINDOWS:
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            log_error(f"MinervaApp._open_folder failed for {path}", e)
            messagebox.showerror("Open Folder Failed", f"Could not open folder:\n{e}")

    def _toggle_pause(self, download_id: str):
        engine = self._torrent_engine
        if engine is None:
            return
        statuses = engine.get_all_statuses()
        s = statuses.get(download_id)
        if s and s.get("paused"):
            engine.resume(download_id)
        else:
            engine.pause(download_id)

    def _cancel_download(self, download_id: str):
        self._queued_selected_ids.discard(download_id)
        if self._download_queue:
            self._download_queue.cancel(download_id)
        w = self._dl_active_widgets.pop(download_id, None)
        if w:
            w["frame"].destroy()
        self._refresh_toggle_label()
        self._save_settings()

    def _on_right_click(self, event):
        col = self._right_tree.identify_column(event.x)
        iid = self._right_tree.identify_row(event.y)
        if iid == "__empty_state__":
            self._reset_all_filters()
            return "break"
        if col == "#1":
            entry = next((e for e in self._all_entries if e["href"] == iid), None)
            if entry is not None and not entry.get("is_folder", False):
                if iid in self._checked_hrefs:
                    self._checked_hrefs.discard(iid)
                    self._right_tree.set(iid, "check", "")
                else:
                    self._checked_hrefs.add(iid)
                    self._right_tree.set(iid, "check", "✓")
                self._update_sel_bar()
                return "break"

    def _update_sel_bar(self):
        n = len(self._checked_hrefs)
        if n == 0:
            self._sel_bar.pack_forget()
        else:
            if not self._sel_bar.winfo_ismapped():
                self._sel_bar.pack(fill="x")
            self._sel_count_lbl.config(text=f"✓ {n} file{'s' if n != 1 else ''} selected")
            self._sel_queue_btn.config(text=f"⬇ Queue {n} Download{'s' if n != 1 else ''}")

    def _clear_checked(self):
        for href in list(self._checked_hrefs):
            try:
                self._right_tree.set(href, "check", "")
            except tk.TclError:
                pass
        self._checked_hrefs.clear()
        self._update_sel_bar()

    def _select_all_visible(self):
        query = self._search_var.get().lower()
        visible = [
            e["href"]
            for e in self._all_entries
            if not e.get("is_folder", False) and self._entry_matches_filters(e, query)
        ]
        for href in visible:
            self._checked_hrefs.add(href)
            if self._right_tree.exists(href):
                self._right_tree.set(href, "check", "✓")
        self._update_sel_bar()

    def _invert_selection(self):
        query = self._search_var.get().lower()
        visible = [
            e["href"]
            for e in self._all_entries
            if not e.get("is_folder", False) and self._entry_matches_filters(e, query)
        ]
        for href in visible:
            if href in self._checked_hrefs:
                self._checked_hrefs.discard(href)
                if self._right_tree.exists(href):
                    self._right_tree.set(href, "check", "")
            else:
                self._checked_hrefs.add(href)
                if self._right_tree.exists(href):
                    self._right_tree.set(href, "check", "✓")
        self._update_sel_bar()

    def _toggle_check_all_visible(self):
        query = self._search_var.get().lower()
        visible = [
            e["href"]
            for e in self._all_entries
            if not e.get("is_folder", False) and self._entry_matches_filters(e, query)
        ]
        if not visible:
            return
        all_checked = all(href in self._checked_hrefs for href in visible)
        if all_checked:
            for href in visible:
                self._checked_hrefs.discard(href)
                if self._right_tree.exists(href):
                    self._right_tree.set(href, "check", "")
        else:
            for href in visible:
                self._checked_hrefs.add(href)
                if self._right_tree.exists(href):
                    self._right_tree.set(href, "check", "✓")
        self._update_sel_bar()

    def _sort_by_column(self, col: str):
        if self._sort_column == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col
            self._sort_reverse = False
        self._update_tree_heading_labels()
        self._render_right_list()

    def _update_tree_heading_labels(self):
        name_arrow = " ▲" if self._sort_column == "name" and not self._sort_reverse else (" ▼" if self._sort_column == "name" else "")
        size_arrow = " ▲" if self._sort_column == "size" and not self._sort_reverse else (" ▼" if self._sort_column == "size" else "")
        self._right_tree.heading("name", text=f"Name{name_arrow}")
        self._right_tree.heading("size", text=f"Size{size_arrow}")

    def _reset_all_filters(self):
        self._search_var.set("")
        for v in self._show_tag_vars.values():
            v.set(False)
        for v in self._show_region_vars.values():
            v.set(False)
        if hasattr(self, "_filter_bar"):
            self._filter_bar.refresh_pills()
            self._filter_bar._update_tag_btn_label()
        self._on_filter_change()

    def _focus_search(self):
        if hasattr(self, "_search_entry"):
            self._search_entry.focus_set()
            self._search_entry.select_range(0, tk.END)

    def _clear_search_and_focus(self):
        self._search_var.set("")
        if hasattr(self, "_right_tree"):
            self._right_tree.focus_set()

    def _on_escape_pressed(self, event=None):
        try:
            if self.focus_get() == getattr(self, "_search_entry", None):
                self._clear_search_and_focus()
            elif self._checked_hrefs:
                self._clear_checked()
        except Exception:
            pass

    def _on_select_all_shortcut(self, event=None):
        focus = self.focus_get()
        if isinstance(focus, (tk.Entry, ttk.Entry, tk.Spinbox)):
            return
        self._select_all_visible()

    def _setup_global_shortcuts(self):
        self.bind_all("<Control-f>", lambda e: self._focus_search())
        self.bind_all("<Control-F>", lambda e: self._focus_search())
        self.bind_all("<Control-d>", lambda e: self._toggle_downloads())
        self.bind_all("<Control-D>", lambda e: self._toggle_downloads())
        self.bind_all("<Control-o>", lambda e: self._open_current_downloads_folder())
        self.bind_all("<Control-O>", lambda e: self._open_current_downloads_folder())
        self.bind_all("<Control-a>", self._on_select_all_shortcut)
        self.bind_all("<Control-A>", self._on_select_all_shortcut)
        self.bind_all("<F5>", lambda e: self._navigate(self._current_path))
        self.bind_all("<Control-r>", lambda e: self._navigate(self._current_path))
        self.bind_all("<Control-R>", lambda e: self._navigate(self._current_path))
        self.bind_all("<Escape>", self._on_escape_pressed)

    def _move_queued_up(self, download_id: str):
        if self._download_queue:
            self._download_queue.move_up(download_id)
            snap = self._download_queue.snapshot()
            self._rebuild_dl_panel(snap)

    def _move_queued_down(self, download_id: str):
        if self._download_queue:
            self._download_queue.move_down(download_id)
            snap = self._download_queue.snapshot()
            self._rebuild_dl_panel(snap)

    def _queue_checked_downloads(self):
        if not _LT_AVAILABLE:
            messagebox.showinfo(
                "libtorrent required",
                "Install libtorrent to enable downloads:\n  pip install libtorrent",
            )
            return
        hrefs = list(self._checked_hrefs)
        if not hrefs:
            return
        save_path = self.get_download_dir()
        for href in hrefs:
            rom_id = extract_rom_id(href)
            if not rom_id:
                continue
            entry = next((e for e in self._all_entries if e["href"] == href), None)
            file_name = entry["name"] if entry else href
            download_id = str(uuid.uuid4())
            threading.Thread(
                target=self._lookup_and_enqueue,
                args=(download_id, rom_id, file_name, save_path, self._current_path),
                daemon=True
            ).start()
        self._clear_checked()
        if not self._downloads_visible:
            self._toggle_downloads()

    def _lookup_and_enqueue(
        self,
        download_id: str,
        rom_id: str,
        file_name: str,
        save_path: str,
        browse_path: str = "",
        *,
        skip_name_dedupe: bool = False,
        fetch_ps3_dkey: bool = True,
    ):
        if not skip_name_dedupe and self._download_queue and self._download_queue.has_name(file_name):
            if fetch_ps3_dkey and is_ps3_iso_browse_path(browse_path):
                self._enqueue_matching_ps3_dkey(file_name, save_path)
            return

        self.after(0, lambda: self._status_var.set(f"Looking up: {file_name}…"))

        try:
            row = fetch_rom_info(rom_id)
        except Exception as e:
            log_error(f"MinervaApp._lookup_and_enqueue rom lookup failed for {file_name}", e)
            self.after(0, lambda err=e: messagebox.showerror(
                "Lookup Failed", f"Could not look up {file_name}:\n{err}"
            ))
            return

        if row is None:
            self.after(0, lambda: messagebox.showwarning(
                "Not Found", f"{file_name} was not found on the server."
            ))
            return

        so_id = row.get("so_id") or 0
        full_path = row.get("full_path") or file_name

        torrent_url = None
        if row.get("torrents"):
            encoded_path = urllib.parse.quote(row["torrents"], safe="/")
            torrent_url = "https://minerva-archive.org/assets/" + encoded_path

        if torrent_url:
            try:
                torrent_dir = get_torrent_dir()
                source_key = hashlib.sha1(full_path.encode("utf-8", errors="ignore")).hexdigest()[:10]
                torrent_filename = (
                    row["torrents"].replace("/", "_").replace("\\", "_") + f"__{source_key}.torrent"
                )
                torrent_local = torrent_dir / torrent_filename
                if not torrent_local.exists():
                    req = urllib.request.Request(
                        torrent_url, headers={"User-Agent": "MiNERVA-Browser/1.0"}
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        torrent_data = resp.read()
                    torrent_local.write_bytes(torrent_data)
                torrent_source = str(torrent_local)
            except Exception as e:
                log_error(f"MinervaApp._lookup_and_enqueue torrent fetch failed for {file_name}", e)
                torrent_source = None
                if row.get("magnet"):
                    torrent_source = row["magnet"] + TRACKERS
                if torrent_source is None:
                    self.after(0, lambda err=e: messagebox.showerror(
                        "Torrent Download Failed", f"Could not download torrent for {file_name}:\n{err}"
                    ))
                    return
        elif row.get("magnet"):
            torrent_source = row["magnet"] + TRACKERS
        else:
            self.after(0, lambda: messagebox.showwarning(
                "No Torrent", f"No torrent info found for {file_name}."
            ))
            return

        self.after(0, lambda: self.enqueue_download(
            download_id=download_id,
            name=file_name,
            source=torrent_source,
            so_id=so_id,
            save_path=save_path,
        ))
        if fetch_ps3_dkey and is_ps3_iso_browse_path(browse_path):
            self._enqueue_matching_ps3_dkey(file_name, save_path)

    def _enqueue_matching_ps3_dkey(self, iso_file_name: str, save_path: str):
        try:
            self.after(0, lambda: self._status_var.set(f"Looking up dkey for: {iso_file_name}…"))
            entry = find_dkey_entry(iso_file_name)
            if entry is None:
                log_activity(f"ps3_dkeys.miss file='{iso_file_name}'")
                self.after(0, lambda: self._status_var.set(f"No dkey found for {iso_file_name}"))
                return
            rom_id = extract_rom_id(entry.get("href") or "")
            if not rom_id:
                return
            dkey_name = entry.get("name") or iso_file_name
            log_activity(f"ps3_dkeys.match iso='{iso_file_name}' dkey='{dkey_name}' id={rom_id}")
            self._lookup_and_enqueue(
                str(uuid.uuid4()),
                rom_id,
                dkey_name,
                save_path,
                PS3_DISC_KEYS_TXT_PATH,
                skip_name_dedupe=True,
                fetch_ps3_dkey=False,
            )
        except Exception as e:
            log_error(f"MinervaApp._enqueue_matching_ps3_dkey failed for {iso_file_name}", e)
            log_activity(f"ps3_dkeys.error file='{iso_file_name}' err={repr(e)}")

    def _find_downloaded_file(self, save_path: pathlib.Path, file_name: str) -> pathlib.Path | None:
        direct = save_path / file_name
        if direct.exists():
            return direct
        for depth in range(1, 4):
            pattern = "/".join(["*"] * depth) + f"/{file_name}"
            matches = list(save_path.glob(pattern))
            if matches:
                return matches[0]
        archive_exts = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso", ".chd"}
        candidates: list[tuple[int, pathlib.Path]] = []
        for depth in range(0, 4):
            pattern = "*"
            if depth > 0:
                pattern = "/".join(["*"] * depth) + "/*"
            for p in save_path.glob(pattern):
                if not p.is_file() or p.suffix.lower() not in archive_exts:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if size > 0:
                    candidates.append((size, p))
        if candidates:
            candidates.sort(key=lambda t: t[0], reverse=True)
            chosen = candidates[0][1]
            log_activity(f"extract.lookup fallback picked '{chosen}' for requested '{file_name}'")
            return chosen
        return None

    def _extract_worker_loop(self):
        while True:
            download_id = self._extract_request_queue.get()
            if download_id is None:
                self._extract_request_queue.task_done()
                break
            try:
                self._extract_download_sync(download_id)
            finally:
                with self._extract_pending_lock:
                    self._extract_pending_ids.discard(download_id)
                self._extract_request_queue.task_done()

    def _extract_download(self, download_id: str):
        with self._extract_pending_lock:
            if download_id in self._extract_pending_ids:
                return
            self._extract_pending_ids.add(download_id)
        self._extract_progress[download_id] = {"pct": 0, "status": "Queued for extraction…"}
        self.after(0, self._refresh_extract_rows)
        self._extract_request_queue.put(download_id)

    def _extract_download_sync(self, download_id: str):
        if not self._torrent_engine:
            return
        meta = self._torrent_engine._meta.get(download_id)
        if not meta:
            return

        save_path = pathlib.Path(meta["save_path"])
        torrent_dir = save_path / "extracted"
        torrent_dir.mkdir(parents=True, exist_ok=True)
        delete_archive = bool(meta.get("delete_archive"))
        extractors = list(self._extractors)
        file_name = meta["name"]
        log_activity(f"extract.start id={download_id} file='{file_name}' save_path='{save_path}'")

        def _set_progress(pct: int, status: str):
            self._extract_progress[download_id] = {"pct": pct, "status": status}
            self.after(0, self._refresh_extract_rows)

        self.after(0, lambda: self._chd_progress_var.set(0.0))

        try:
            src = None
            for _ in range(20):
                src = self._find_downloaded_file(save_path, file_name)
                if src is not None:
                    break

                _set_progress(0, "Waiting for downloaded file…")
                time.sleep(1)

            if src is None:
                log_activity(f"extract.missing id={download_id} file='{file_name}'")
                _set_progress(0, f"Missing downloaded file: {file_name}")
                return
            log_activity(f"extract.source id={download_id} src='{src}' size={src.stat().st_size if src.exists() else -1}")

            stable_count = 0
            last_size = -1
            for _ in range(10):
                try:
                    current_size = src.stat().st_size
                except OSError:
                    current_size = -1
                if current_size > 0 and current_size == last_size:
                    stable_count += 1
                    if stable_count >= 2:
                        break
                else:
                    stable_count = 0
                last_size = current_size
                time.sleep(1)

            out_dir = torrent_dir / src.stem
            out_dir.mkdir(parents=True, exist_ok=True)
            extracted_ok = extract_archive(
                src,
                out_dir,
                extractors=extractors,
                progress_cb=lambda pct, status: _set_progress(pct, status)
            )
            extracted_dir = out_dir if extracted_ok else None

            if extracted_ok and extracted_dir is not None:
                verify_extracted_output(extracted_dir, src.name)
                def _chd_progress(done: int, total: int, cue_name: str):
                    if total <= 0:
                        return
                    pct = 90 + int((done / total) * 9)
                    pct = max(90, min(99, pct))
                    self.after(0, lambda d=done, t=total: self._chd_progress_var.set(d * 100.0 / t))
                    _set_progress(pct, f"Converting to CHD ({done}/{total}): {cue_name}")

                compress_ps1_to_chd(
                    extracted_dir,
                    self._chdman_path,
                    progress_cb=_chd_progress
                )
                renamed, unchanged, failed = clean_chd_names_in_base(extracted_dir)
                if failed:
                    log_activity(
                        f"extract.clean_names.partial id={download_id} renamed={renamed} "
                        f"unchanged={unchanged} failed={len(failed)}"
                    )
                else:
                    log_activity(
                        f"extract.clean_names.ok id={download_id} renamed={renamed} unchanged={unchanged}"
                    )
                log_activity(f"extract.verify.ok id={download_id} dir='{extracted_dir}'")

            if extracted_ok and delete_archive and src.exists():
                src.unlink()
                log_activity(f"extract.delete_archive id={download_id} src='{src}'")

            status_text = "Extracted ✓"
            if extracted_ok and extracted_dir is not None:
                if any(extracted_dir.rglob("*.chd")):
                    status_text = "Compressed to CHD ✓"
            elif not extracted_ok:
                status_text = "Failed"

            _set_progress(100, status_text)
            self.after(0, lambda: self._chd_progress_var.set(100.0 if extracted_ok else 0.0))
            log_activity(f"extract.done id={download_id} ok={extracted_ok}")

        except Exception as e:
            log_error(f"MinervaApp._extract_download_sync failed for {file_name}", e)
            log_activity(f"extract.error id={download_id} file='{file_name}' err={repr(e)}")
            _set_progress(0, f"Error: {str(e)[:40]}")
            self.after(0, lambda: self._chd_progress_var.set(0.0))

    @staticmethod
    def _parse_version(tag: str) -> tuple[int, ...]:
        parts = [int(x) for x in re.findall(r"\d+", tag)]
        return tuple(parts) if parts else (0,)

    @staticmethod
    def _fetch_latest_release() -> tuple[str, str]:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": f"MiNERVA-Browser/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        tag = data.get("tag_name", "")
        assets = data.get("assets", [])
        exe_asset = next(
            (a for a in assets if a.get("name", "").lower().endswith(".exe")),
            None,
        )
        if not exe_asset:
            raise RuntimeError("No .exe asset found in latest release")
        return tag, exe_asset["browser_download_url"]

    def _check_for_updates_async(self, *, silent: bool = True):
        def worker():
            try:
                tag, url = self._fetch_latest_release()
                if self._parse_version(tag) > self._parse_version(APP_VERSION):
                    self.after(0, lambda t=tag, u=url: self._on_update_available(t, u))
                elif not silent:
                    self.after(0, lambda t=tag: self._on_already_up_to_date(t))
            except Exception as e:
                if not silent:
                    self.after(0, lambda err=e: messagebox.showerror(
                        "Update Check Failed", f"Could not check for updates:\n{err}"))
        threading.Thread(target=worker, daemon=True).start()

    def _check_for_update_button_click(self):
        self._update_btn.config(state="disabled", text="Checking…")
        def worker():
            try:
                tag, url = self._fetch_latest_release()
                if self._parse_version(tag) > self._parse_version(APP_VERSION):
                    self.after(0, lambda t=tag, u=url: self._on_update_available(t, u))
                else:
                    self.after(0, lambda t=tag: self._on_already_up_to_date(t))
            except Exception as e:
                self.after(0, lambda err=e: (
                    self._update_btn.config(state="normal", text="🔄 Check for Updates"),
                    messagebox.showerror("Update Check Failed", f"Could not check for updates:\n{err}"),
                ))
        threading.Thread(target=worker, daemon=True).start()

    def _on_already_up_to_date(self, latest_tag: str):
        self._update_btn.config(state="normal", text="🔄 Check for Updates")
        messagebox.showinfo("Up to Date", f"You are running the latest version (v{APP_VERSION}).")

    def _on_update_available(self, tag: str, download_url: str):
        self._update_btn.config(text=f"⬆ Update {tag}", state="normal",
                                command=lambda t=tag, u=download_url: self._show_update_dialog(t, u))

    def _show_update_dialog(self, tag: str, download_url: str):
        dlg = tk.Toplevel(self)
        dlg.title("Update Available")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text=f"A new version is available: {tag}",
                 bg=BG, fg=FG, font=("TkDefaultFont", 11, "bold")).pack(padx=20, pady=(16, 4))
        tk.Label(dlg, text=f"Current version: v{APP_VERSION}",
                 bg=BG, fg=FG_DIM, font=("TkDefaultFont", 9)).pack(padx=20)
        tk.Label(dlg, text=f"New version:     {tag}",
                 bg=BG, fg=FG_DIM, font=("TkDefaultFont", 9)).pack(padx=20, pady=(0, 12))

        status_var = tk.StringVar(value="Ready to download.")
        tk.Label(dlg, textvariable=status_var, bg=BG, fg=ACCENT,
                 font=("TkDefaultFont", 9)).pack(padx=20)

        progress_var = tk.DoubleVar(value=0.0)
        progress_bar = ttk.Progressbar(dlg, variable=progress_var, maximum=100, length=320)
        progress_bar.pack(padx=20, pady=(4, 12))

        btn_frame = tk.Frame(dlg, bg=BG)
        btn_frame.pack(pady=(0, 16))
        download_btn = ttk.Button(btn_frame, text="Download & Install",
                                  command=lambda: self._download_and_install_update(
                                      tag, download_url, dlg, status_var, progress_var, download_btn))
        download_btn.pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Later", command=dlg.destroy).pack(side="left", padx=8)

    def _download_and_install_update(self, tag: str, download_url: str,
                                     dlg: tk.Toplevel, status_var: tk.StringVar,
                                     progress_var: tk.DoubleVar, download_btn: ttk.Button):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Not Supported",
                                "Auto-update only works for the portable .exe build.\n"
                                f"Please download {tag} manually from GitHub.")
            dlg.destroy()
            return

        download_btn.config(state="disabled")
        dest = pathlib.Path(sys.executable).parent / "MiNERVA-Browser-update.exe"

        def worker():
            try:
                req = urllib.request.Request(
                    download_url, headers={"User-Agent": f"MiNERVA-Browser/{APP_VERSION}"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    chunk = 65536
                    with open(dest, "wb") as f:
                        while True:
                            buf = resp.read(chunk)
                            if not buf:
                                break
                            f.write(buf)
                            downloaded += len(buf)
                            if total > 0:
                                pct = downloaded * 100.0 / total
                                self.after(0, lambda p=pct: progress_var.set(p))
                            mb = downloaded / 1_048_576
                            self.after(0, lambda m=mb: status_var.set(f"Downloaded {m:.1f} MB…"))
                self.after(0, lambda: progress_var.set(100.0))
                self.after(0, lambda: status_var.set("Download complete. Restarting…"))
                self.after(500, lambda: self._launch_updater_and_exit(dest))
            except Exception as e:
                self.after(0, lambda err=e: status_var.set(f"Error: {err}"))
                self.after(0, lambda: download_btn.config(state="normal"))
                log_error("MinervaApp._download_and_install_update failed", e)

        threading.Thread(target=worker, daemon=True).start()

    def _launch_updater_and_exit(self, new_exe: pathlib.Path):
        current_exe = pathlib.Path(sys.executable)
        pid = os.getpid()
        new_exe_str = str(new_exe).replace("'", "''")
        current_exe_str = str(current_exe).replace("'", "''")
        script = (
            f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue\n"
            f"if ($p) {{ $p | Wait-Process -Timeout 15 }}\n"
            f"Start-Sleep -Milliseconds 500\n"
            f"Move-Item -Path '{new_exe_str}' -Destination '{current_exe_str}' -Force\n"
            f"Start-Process '{current_exe_str}'\n"
            f"Remove-Item -Path $PSCommandPath -Force -ErrorAction SilentlyContinue\n"
        )
        script_path = new_exe.parent / "_minerva_update.ps1"
        script_path.write_text(script, encoding="utf-8")
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
             "-File", str(script_path)],
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
        )
        self._on_close()

    def _setup_window_icon(self):
        """Set up the window titlebar and taskbar icon."""
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("minerva.archive.browser")
            except Exception:
                pass

        icon_png = get_icon_png_path()
        if icon_png.exists():
            try:
                self._app_icon_photo = tk.PhotoImage(file=str(icon_png))
                self.iconphoto(True, self._app_icon_photo)
            except Exception as e:
                log_error("Failed setting iconphoto", e)

        if sys.platform == "win32":
            icon_ico = get_icon_ico_path()
            if icon_ico.exists():
                try:
                    self.iconbitmap(default=str(icon_ico))
                except Exception as e:
                    log_error("Failed setting iconbitmap", e)

    def _setup_system_tray(self):
        """Set up the system tray icon using pystray."""
        self._tray_icon = None
        try:
            import pystray
            from PIL import Image

            icon_path = get_assets_dir() / "icon_32.png"
            if not icon_path.exists():
                icon_path = get_icon_png_path()
            if not icon_path.exists():
                return

            tray_image = Image.open(icon_path)

            menu = pystray.Menu(
                pystray.MenuItem("Show MiNERVA", lambda icon, item: self.after(0, self._restore_from_tray), default=True),
                pystray.MenuItem("Minimize to Tray", lambda icon, item: self.after(0, self.withdraw)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open Download Folder", lambda icon, item: self.after(0, self._open_current_downloads_folder)),
                pystray.MenuItem("Pause / Resume All", lambda icon, item: self.after(0, self._toggle_pause_all_active)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", lambda icon, item: self.after(0, self._quit_from_tray)),
            )

            self._tray_icon = pystray.Icon(
                "minerva_browser",
                tray_image,
                "MiNERVA Archive Browser",
                menu=menu
            )
            self._tray_icon.run_detached()
        except Exception as e:
            log_error("System tray initialization skipped or failed", e)

    def _restore_from_tray(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self):
        self._on_close()

    def _shutdown_tray(self):
        if hasattr(self, "_tray_icon") and self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    def _on_close(self):
        self._shutdown_tray()
        self._save_settings()
        if self._torrent_engine is not None:
            try:
                self._torrent_engine.shutdown()
            except Exception:
                log_error("MinervaApp._on_close engine shutdown failed")
        try:
            self._extract_request_queue.put_nowait(None)
        except Exception as e:
            log_error("MinervaApp._on_close extraction queue shutdown failed", e)
        self.destroy()

    def _show_error(self, msg):
        log_error(f"MinervaApp._show_error: {msg}")
        self._set_loading(False)
        self._right_tree.delete(*self._right_tree.get_children())
        self._status_var.set(f"Error: {msg}")
        messagebox.showerror("Error", f"Failed to load page:\n{msg}")
