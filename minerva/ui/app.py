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
import json
import os

from minerva.constants import (
    APP_VERSION,
    GITHUB_REPO,
    BASE_URL,
    BROWSE_ROOT,
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
from minerva.ui.downloads_panel import DownloadsPanel, PanelActions
from minerva.ui.components.tools_dialog import ToolsMenu, ToolsDialog
from minerva.ui.components.companion_dialog import prompt_companions
from minerva.core.sqlite_http import fetch_entries, fetch_rom_info, extract_rom_id
from minerva.core.companions import find_companions, classify_release, KIND_BASE
from minerva.core.ps3_dkeys import (
    DKEY_ZIP_MAX_BYTES,
    PS3_DISC_KEYS_TXT_PATH,
    collect_local_ps3_rom_names,
    find_dkey_entry,
    find_dkey_entry_for_path,
    find_local_dkey,
    find_local_dkey_zip,
    get_dkey_save_dir,
    is_dkey_save_path,
    is_ps3_iso_browse_path,
)
from minerva.core.entries import detect_regions, detect_release_tags, enrich_entries, parse_size_bytes
from minerva.core.library_index import LibraryIndex
from minerva.core.pathsafe import is_safe_leaf_name
from minerva.core.settings_writer import AsyncSettingsWriter
from minerva.core.workers import DaemonPool
from minerva.core.lookup import (
    LookupErrors,
    LookupFailure,
    QueuedDownload,
    RomResolver,
    strip_default_trackers,
)
from minerva.core.torrent_cache import TorrentCache
from minerva.core.torrent_engine import (
    TorrentEngine,
    DownloadQueue,
    _LT_AVAILABLE,
)
from minerva.core.extractors import (
    IS_WINDOWS,
    _hidden_subprocess_kwargs,
    find_archive_extractors,
    format_extractor_status,
    find_chdman_executable,
    find_xbox_unpack_tool,
    pick_xdvdfs_release_asset,
    collect_xbox_iso_sources,
    unpack_xbox_iso,
    unpack_xbox_isos_in_dir,
    should_unpack_xbox_iso,
    XDVDFS_GITHUB_REPO,
    normalize_chd_stem,
    clean_chd_names_in_base,
    verify_extracted_output,
    is_archive_path,
    collect_downloaded_archives,
    library_keys_for_name,
    status_from_keys,
    verify_archive,
    ArchiveVerificationError,
    chd_source_mode,
    collect_chd_sources,
    compress_ps1_to_chd,
    extract_archive,
    migrate_app_root_roms,
    format_bytes,
    display_filename,
    collect_incorrect_chds,
    repair_incorrect_chds,
    names_refer_to_same_rom,
    chd_companions_safe_to_delete,
)



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
        else:
            try:
                saved_resolved = pathlib.Path(saved_download_dir).resolve()
                if saved_resolved == get_runtime_base_dir().resolve():
                    saved_download_dir = get_default_download_dir()
            except Exception:
                saved_download_dir = get_default_download_dir()
        try:
            pathlib.Path(saved_download_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_error("MinervaApp could not create download directory", e)
            saved_download_dir = get_default_download_dir()
        self._download_dir = tk.StringVar(value=str(pathlib.Path(saved_download_dir).resolve()))
        self._auto_extract_default_var = tk.BooleanVar(
            value=bool(self._settings.get("auto_extract_default", False))
        )
        self._delete_archive_default_var = tk.BooleanVar(
            value=bool(self._settings.get("delete_archive_default", True))
        )
        self._compress_ps1_chd_var = tk.BooleanVar(
            value=bool(self._settings.get("compress_ps1_chd", True))
        )
        self._unpack_xbox_iso_var = tk.BooleanVar(
            value=bool(self._settings.get("unpack_xbox_iso", True))
        )
        self._autostart_var = tk.BooleanVar(
            value=bool(self._settings.get("autostart_with_windows", False))
        )
        self._start_minimized_var = tk.BooleanVar(
            value=bool(self._settings.get("start_minimized", False))
        )
        self._offer_companions_var = tk.BooleanVar(
            value=bool(self._settings.get("offer_companions", True))
        )
        self._seed_var = tk.BooleanVar(value=bool(self._settings.get("seed_after_download", False)))
        self._companion_dialog_open = False
        self._companion_prompt_queue: queue.Queue = queue.Queue()
        self._launch_in_tray = bool(self._start_minimized_var.get()) or "--minimized" in sys.argv
        if self._launch_in_tray:
            # Hide before the window maps so it never appears on the taskbar.
            try:
                self.withdraw()
            except tk.TclError:
                pass
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
        self._xbox_unpack_tool = find_xbox_unpack_tool()
        self._extract_tool_var = tk.StringVar(value=format_extractor_status(self._extractors))
        self._extract_status_var = tk.StringVar(value="")
        self._chd_progress_var = tk.DoubleVar(value=0.0)
        self._extract_progress: dict[str, dict] = {}
        self._extract_request_queue: queue.Queue[str | None] = queue.Queue()
        self._extract_pending_ids: set[str] = set()
        self._extract_pending_lock = threading.Lock()
        self._verify_archives_in_progress = False
        self._verify_extracted_in_progress = False
        self._ensure_dkeys_in_progress = False
        self._download_history: dict[str, dict] = self._load_download_history()
        self._left_loaded_nodes: set[str] = set()
        self._left_loading_nodes: set[str] = set()
        self._chd_download_in_progress = False
        self._chd_compress_in_progress = False
        self._chd_repair_in_progress = False
        self._xbox_tool_download_in_progress = False
        self._xbox_unpack_in_progress = False
        self._quitting = False
        self._extract_refresh_pending = False
        self._nav_generation = 0
        self._search_silenced = False
        self._render_after_id = None
        self._settings_writer = AsyncSettingsWriter()
        self._library_index = LibraryIndex()
        self._row_keys: dict[str, frozenset] = {}
        self._row_status: dict[str, str] = {}
        self._icon_state: tuple | None = None
        self._icon_refresh_after_id = None
        self._library_rescan_after_id = None
        self._poll_error_logged_at = 0.0
        self._poll_after_id = None
        self._lookup_pool = DaemonPool(5, "lookup")
        self._lookup_lock = threading.Lock()
        self._lookup_pending = 0
        self._lookup_results: queue.SimpleQueue = queue.SimpleQueue()
        self._lookup_errors = LookupErrors()
        self._lookup_pump_after_id = None
        self._torrent_cache = TorrentCache(get_torrent_dir())
        self._rom_resolver = RomResolver(self._torrent_cache, fetch_rom_info)
        self._dlstat_tip_window = None
        self._dlstat_tip_text = ""
        self._checked_hrefs: set[str] = set()
        self._sort_column = "name"
        self._sort_reverse = False
        self._setup_styles()
        self._setup_window_icon()
        self._build_ui()
        self._setup_global_shortcuts()
        self._setup_system_tray()
        self._migrate_root_roms_on_launch()
        self._download_dir.trace_add("write", self._on_download_dir_change)
        if self._compress_ps1_chd_var.get() and not self._chdman_path:
            self._ensure_chdman_available_async()
        if self._unpack_xbox_iso_var.get() and not self._xbox_unpack_tool:
            self._ensure_xbox_unpack_tool_async()
        self._restore_persisted_queue()
        self._extract_worker_thread = threading.Thread(target=self._extract_worker_loop, daemon=True)
        self._extract_worker_thread.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        self.bind("<Unmap>", self._on_window_unmap)
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
        if getattr(self, "_launch_in_tray", False):
            self._minimize_to_tray()
        self.after(100, self._start_startup_cleanup)
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

        self._verify_archives_btn = ttk.Button(
            toolbar,
            text="🔍 Verify Archives",
            style="Toolbar.TButton",
            command=self._verify_downloaded_archives_button_click,
        )
        self._verify_archives_btn.pack(side="left", padx=3)
        HoverTooltip(self._verify_archives_btn, "CRC-test already downloaded zip/7z/rar archives")

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
            on_reset_filters=self._reset_all_filters,
        )
        self._filter_bar.pack(fill="x")
        self._search_entry = self._filter_bar.search_entry

        right_frame.bind("<Configure>", self._on_right_frame_configure)

        cols = ("check", "dlstat", "name", "size")
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
        self._right_tree.heading("dlstat", text="")
        self._right_tree.column("dlstat", width=28, stretch=False, anchor="center", minwidth=28)
        self._right_tree.heading("name", text="Name ▲", command=lambda: self._sort_by_column("name"))
        self._right_tree.column("name", stretch=True, minwidth=200)
        self._right_tree.heading("size", text="Size", command=lambda: self._sort_by_column("size"))
        self._right_tree.column("size", width=100, stretch=False, anchor="e")
        right_scroll_y.pack(side="right", fill="y", padx=(0, 8))
        right_scroll_x.pack(side="bottom", fill="x", padx=(10, 8))
        self._right_tree.pack(fill="both", expand=True, padx=(10, 0), pady=(4, 0))
        self._right_tree.tag_configure("queued", foreground=WARNING)
        self._right_tree.tag_configure("downloaded", foreground=SUCCESS)
        self._right_tree.bind("<Double-1>", self._on_right_double_click)
        self._right_tree.bind("<Button-1>", self._on_right_click)
        self._right_tree.bind("<Button-3>", self._show_tree_context_menu)
        self._right_tree.bind("<Motion>", self._on_right_motion)
        self._right_tree.bind("<Leave>", lambda e: self._hide_dlstat_tip())
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
        self._downloads_advanced = bool(self._settings.get("downloads_advanced_open", False))
        self._max_concurrent_var = tk.IntVar(value=self._get_saved_max_concurrent())

        # Bottom downloads drawer (Focus layout): browse stays primary.
        self._downloads_drawer = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        self._downloads_handle = tk.Frame(self._downloads_drawer, bg=PANEL_ALT)
        self._downloads_handle.pack(fill="x")

        self._downloads_toggle_btn = ttk.Button(
            self._downloads_handle,
            text="📥 Downloads",
            style="Toolbar.TButton",
            command=self._toggle_downloads,
        )
        self._downloads_toggle_btn.pack(side="left", padx=(8, 4), pady=4)
        HoverTooltip(self._downloads_toggle_btn, "Show or hide the downloads drawer (Ctrl+D)")

        self._dl_summary_lbl = tk.Label(
            self._downloads_handle,
            text="Idle",
            bg=PANEL_ALT,
            fg=FG_DIM,
            font=("TkDefaultFont", 9),
            anchor="w",
        )
        self._dl_summary_lbl.pack(side="left", fill="x", expand=True, padx=(4, 8))

        self._dl_advanced_btn = ttk.Button(
            self._downloads_handle,
            text="Advanced ▾",
            style="Header.TButton",
            command=self._toggle_downloads_advanced,
        )
        self._dl_advanced_btn.pack(side="right", padx=(0, 8), pady=4)
        HoverTooltip(self._dl_advanced_btn, "Show CHD/Xbox/extract options and ROM tools")

        self._downloads_frame = tk.Frame(self._downloads_drawer, bg=PANEL)

        # Compact primary actions (always visible when drawer body is open)
        compact_row = tk.Frame(self._downloads_frame, bg=PANEL)
        compact_row.pack(fill="x", padx=10, pady=(6, 2))
        for text, cmd, tip in [
            ("▶ Start All", self._start_all_queued, "Start downloading all queued items"),
            ("⏸ Pause / Resume", self._toggle_pause_all_active, "Toggle pause/resume on all active downloads"),
            ("✕ Clear Finished", self._clear_completed, "Clear finished and errored items from panel"),
            ("Open Folder", self._open_current_downloads_folder, "Open target download folder on disk (Ctrl+O)"),
        ]:
            b = ttk.Button(compact_row, text=text, style="Header.TButton", command=cmd)
            b.pack(side="left", padx=(0, 4))
            HoverTooltip(b, tip)

        # Advanced section: path, options, secondary tools (collapsed by default)
        self._dl_advanced_frame = tk.Frame(self._downloads_frame, bg=PANEL)

        dir_row = tk.Frame(self._dl_advanced_frame, bg=PANEL)
        dir_row.pack(fill="x", padx=10, pady=(4, 2))

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
        btn_open_hdr.pack(side="left", padx=(0, 4))
        HoverTooltip(btn_open_hdr, "Open download folder on disk (Ctrl+O)")

        btn_verify_hdr = ttk.Button(
            dir_row,
            text="Verify Archives",
            style="Header.TButton",
            command=self._verify_downloaded_archives_button_click,
        )
        btn_verify_hdr.pack(side="left", padx=(0, 0))
        HoverTooltip(btn_verify_hdr, "CRC-test archives already in the download folder")

        opts_row = tk.Frame(self._dl_advanced_frame, bg=PANEL)
        opts_row.pack(fill="x", padx=10, pady=(2, 2))

        spin_frame = tk.Frame(opts_row, bg=PANEL)
        spin_frame.pack(side="left", padx=(0, 8), anchor="w")
        tk.Label(spin_frame, text="Max concurrent:", bg=PANEL, fg=FG_DIM,
                 font=("TkDefaultFont", 9)).pack(side="left", padx=(0, 4))
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
            ("Unpack Xbox ISOs", self._unpack_xbox_iso_var, self._on_extract_defaults_change),
            ("Decompress archives", self._auto_extract_default_var, self._on_extract_defaults_change),
            ("Delete archive", self._delete_archive_default_var, self._on_extract_defaults_change),
            ("Autostart", self._autostart_var, self._on_startup_settings_change),
            ("Start minimized to tray", self._start_minimized_var, self._on_startup_settings_change),
            ("Offer DLC / updates", self._offer_companions_var, self._on_extract_defaults_change),
            ("Seed finished files", self._seed_var, self._on_seed_setting_change),
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

        self._dl_actions_frame = tk.Frame(self._dl_advanced_frame, bg=PANEL)
        self._dl_actions_frame.pack(fill="x", padx=10, pady=(2, 4))
        self._dl_action_buttons = []

        for text, cmd, tip in [
            ("Start Selected", self._start_selected_queued, "Start downloading checked items in queue"),
            ("🔍 Verify Archives", self._verify_downloaded_archives_button_click, "CRC-test already downloaded archives"),
            ("🔑 PS3 Dkeys", self._ensure_ps3_dkeys_button_click, "Find, verify, and redownload missing PS3 disc keys"),
            ("🛠 ROM Tools ▾", self._show_rom_tools_menu, "ROM compression, verification, and disc utilities"),
            ("Open Extracted", self._open_current_extracted_folder, "Open folder containing extracted ROMs"),
        ]:
            b = ttk.Button(self._dl_actions_frame, text=text, style="Header.TButton", command=cmd)
            HoverTooltip(b, tip)
            self._dl_action_buttons.append(b)
            if text.startswith("🛠"):
                self._rom_tools_btn = b

        info_row = tk.Frame(self._dl_advanced_frame, bg=PANEL)
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

        self._downloads_frame.bind("<Configure>", self._on_downloads_frame_configure)

        tk.Frame(self._downloads_frame, bg=SEL_BG, height=1).pack(fill="x", padx=8)

        self._downloads_panel = DownloadsPanel(self._downloads_frame, self._build_panel_actions())
        self._downloads_panel.pack(fill="both", expand=True)

        self._status_var = tk.StringVar(value="")
        status_bar = ttk.Label(
            self,
            textvariable=self._status_var,
            style="Status.TLabel",
            relief="flat",
            padding=(10, 5),
        )
        status_bar.pack(fill="x", side="bottom", pady=(0, 4))

        # Drawer sits above the status bar so browse keeps the center of the window.
        self._downloads_drawer.pack(fill="x", side="bottom", before=status_bar)
        self._apply_downloads_drawer_visibility()
        self._apply_downloads_advanced_visibility()

        self._poll_downloads()

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
        self._nav_generation += 1
        generation = self._nav_generation
        self._cancel_pending_render()
        # Clearing the box would otherwise render the *old* folder's entries once more.
        self._search_silenced = True
        try:
            self._search_var.set(restore_query if preserve_search else "")
        finally:
            self._search_silenced = False
        if hasattr(self, "_filter_bar"):
            self._filter_bar.refresh_summary()
        self._update_breadcrumb()
        self._set_loading(True)
        self._right_tree.delete(*self._right_tree.get_children())
        self._reset_row_status()
        self._checked_hrefs.clear()
        self._status_var.set("Loading\u2026")

        def worker():
            try:
                entries = enrich_entries(fetch_entries(path))
                self.after(0, lambda: self._populate_right(entries, generation))
            except Exception as e:
                log_error(f"MinervaApp._navigate failed for path={path}", e)
                self.after(0, lambda err=e: self._show_error(str(err), generation))

        threading.Thread(target=worker, daemon=True).start()
        self._save_settings()

    def _populate_right(self, entries, generation: int | None = None):
        if generation is not None and generation != self._nav_generation:
            return  # a newer navigation superseded this response
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

    SEARCH_DEBOUNCE_MS = 150

    def _cancel_pending_render(self):
        if self._render_after_id is not None:
            self.after_cancel(self._render_after_id)
            self._render_after_id = None

    def _on_search_change(self, *_):
        if self._search_silenced:
            return
        if hasattr(self, "_filter_bar"):
            self._filter_bar.refresh_summary()
        # Re-filtering thousands of rows per keystroke made typing laggy; wait for a pause.
        self._cancel_pending_render()
        self._render_after_id = self.after(self.SEARCH_DEBOUNCE_MS, self._render_right_list)
        if getattr(self, "_search_save_after_id", None):
            self.after_cancel(self._search_save_after_id)
        self._search_save_after_id = self.after(500, self._save_settings)

    def _on_filter_change(self):
        if hasattr(self, "_filter_bar"):
            self._filter_bar.refresh_summary()
        self._render_right_list()
        self._save_settings()

    def _render_right_list(self):
        self._cancel_pending_render()
        query = self._search_var.get().lower()
        selected_tags, selected_regions = self._selected_filter_keys()
        filtered = [
            e for e in self._all_entries
            if self._entry_matches_filters(e, query, selected_tags, selected_regions)
        ]
        visible_files = [e for e in filtered if not e.get("is_folder", False)]

        # Apply column sorting
        if self._sort_column == "size":
            visible_files.sort(
                key=lambda e: e["size_bytes"] if "size_bytes" in e else parse_size_bytes(e.get("size", "")),
                reverse=self._sort_reverse
            )
        else:
            visible_files.sort(
                key=lambda e: e.get("lname") or e.get("name", "").lower(),
                reverse=self._sort_reverse
            )

        self._right_tree.delete(*self._right_tree.get_children())
        self._reset_row_status()
        # Queue/disk keys are computed once per render, not once per row.
        queued_keys = self._queued_keys()
        disk_keys = self._on_disk_library_keys()
        self._icon_state = (queued_keys, disk_keys)
        visible_hrefs = {e["href"] for e in visible_files}
        self._checked_hrefs.intersection_update(visible_hrefs)
        seen_hrefs = set()
        for e in visible_files:
            if e["href"] in seen_hrefs:
                continue
            seen_hrefs.add(e["href"])
            icon = "📄 "
            row_keys = e.get("keys") or library_keys_for_name(e["name"])
            status = status_from_keys(row_keys, queued_keys, disk_keys)
            self._row_keys[e["href"]] = row_keys
            self._row_status[e["href"]] = status
            status_icon = {"downloaded": "✓", "queued": "⬇"}.get(status, "")
            tags = ("file", status) if status else ("file",)
            self._right_tree.insert("", "end", iid=e["href"],
                                    values=("", status_icon, icon + e["name"], e["size"]),
                                    tags=tags)
            if e["href"] in self._checked_hrefs:
                self._right_tree.set(e["href"], "check", "✓")

        if not visible_files:
            has_filter = bool(query) or bool(selected_tags) or bool(selected_regions)
            if has_filter and self._all_entries:
                self._right_tree.insert(
                    "", "end", iid="__empty_state__",
                    values=("", "", "🔍 No matching items. Click here to reset search and filters.", ""),
                    tags=("empty_state",)
                )

        self._update_sel_bar()
        self._update_status(visible_files)

    def _selected_filter_keys(self) -> tuple[set[str], set[str]]:
        """Checked tag/region filters; read the Tk variables once per render, not once per row."""
        tags = {key for key, var in self._show_tag_vars.items() if var.get()}
        regions = {key for key, var in self._show_region_vars.items() if var.get()}
        return tags, regions

    def _entry_matches_filters(self, entry: dict, query: str,
                               selected_tags: set[str] | None = None,
                               selected_regions: set[str] | None = None) -> bool:
        if selected_tags is None or selected_regions is None:
            selected_tags, selected_regions = self._selected_filter_keys()
        low = entry.get("lname")
        if low is None:
            low = entry.get("name", "").lower()
        if query and query not in low:
            return False

        if not entry.get("is_folder", False):
            if selected_tags:
                tags = entry.get("tags")
                if tags is None:
                    tags = self._detect_release_tags(low)
                if not tags.isdisjoint(selected_tags):
                    return False
            if selected_regions:
                regions = entry.get("regions")
                if regions is None:
                    regions = self._detect_regions(low)
                if regions.isdisjoint(selected_regions):
                    return False

        return True

    _detect_release_tags = staticmethod(detect_release_tags)
    _detect_regions = staticmethod(detect_regions)

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
        self._submit_lookup(str(uuid.uuid4()), rom_id, file_name, save_path, browse_path)
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
                self._torrent_engine = TorrentEngine(state_dir=get_runtime_base_dir() / "resume")
                self._torrent_engine.set_seeding(bool(self._seed_var.get()))
                self._download_queue = DownloadQueue(
                    self._torrent_engine,
                    max_active=self._get_current_max_concurrent(),
                    key_fn=library_keys_for_name,
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
            self._remember_download(name, source, so_id, save_path)
            self._save_settings()
            self._request_icon_refresh()
        if not self._downloads_visible:
            self._toggle_downloads()

    def _load_download_history(self) -> dict[str, dict]:
        raw = self._settings.get("download_history", [])
        history: dict[str, dict] = {}
        if not isinstance(raw, list):
            return history
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            source = item.get("source")
            save_path = item.get("save_path")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(source, str) or not source.strip():
                continue
            if not isinstance(save_path, str) or not save_path.strip():
                continue
            try:
                so_id = int(item.get("so_id"))
            except (TypeError, ValueError):
                continue
            history[name] = {
                "name": name,
                "source": strip_default_trackers(source),
                "so_id": so_id,
                "save_path": save_path,
            }
        return history

    def _remember_download(self, name: str, source: str, so_id: int, save_path: str):
        self._download_history[name] = {
            "name": name,
            "source": source,
            "so_id": int(so_id),
            "save_path": str(pathlib.Path(save_path).resolve()),
        }

    def _lookup_download_meta(self, name: str) -> dict | None:
        if self._download_queue is not None:
            found = self._download_queue.find_by_name(name)
            if found and found.get("source"):
                return found
        return self._download_history.get(name)

    def _delete_bad_archive(self, save_path: str, file_name: str) -> bool:
        path = pathlib.Path(save_path) / file_name
        removed = False
        for candidate in (path, *list(path.parent.glob(file_name + ".parts")), *list(path.parent.glob(file_name + ".*!ut"))):
            if candidate.is_file():
                try:
                    candidate.unlink()
                    removed = True
                    log_activity(f"redownload.delete file='{candidate}'")
                except OSError as e:
                    log_activity(f"redownload.delete_failed file='{candidate}' err={e}")
        found = self._find_downloaded_file(pathlib.Path(save_path), file_name)
        if found is not None and found.is_file() and found != path:
            try:
                found.unlink()
                removed = True
                log_activity(f"redownload.delete nested='{found}'")
            except OSError as e:
                log_activity(f"redownload.delete_nested_failed file='{found}' err={e}")
        return removed

    def _redownload_item(self, *, download_id: str | None = None, name: str | None = None, confirm: bool = True) -> bool:
        meta = None
        if download_id and self._download_queue is not None:
            snap = self._download_queue.snapshot()
            meta = next((item for item in snap["done"] if item.get("id") == download_id), None)
        if meta is None and name:
            meta = self._lookup_download_meta(name)
        if meta is None:
            messagebox.showwarning(
                "Redownload",
                f"No torrent info is saved for {name or 'this file'}. Queue it again from the browser.",
            )
            return False
        file_name = meta.get("name") or name or ""
        source = meta.get("source") or ""
        save_path = meta.get("save_path") or self.get_download_dir()
        try:
            so_id = int(meta.get("so_id") or 0)
        except (TypeError, ValueError):
            so_id = 0
        if not file_name or not source:
            messagebox.showwarning("Redownload", f"Missing torrent source for {file_name}.")
            return False
        if confirm:
            if not messagebox.askyesno(
                "Redownload",
                f"{file_name} looks corrupt or incomplete.\n\nDelete it and download again?",
            ):
                return False
        engine = self.get_torrent_engine()
        if engine is None or self._download_queue is None:
            return False
        if download_id:
            self._download_queue.pop_done(download_id)
        else:
            existing = self._download_queue.find_by_name(file_name)
            if existing and existing.get("id"):
                if existing["id"] in self._download_queue.snapshot()["active"]:
                    self._download_queue.cancel(existing["id"])
                else:
                    self._download_queue.pop_done(existing["id"])
                    self._download_queue.cancel(existing["id"])
        self._delete_bad_archive(save_path, file_name)
        new_id = str(uuid.uuid4())
        self._download_queue.enqueue(new_id, file_name, source, so_id, save_path)
        self._remember_download(file_name, source, so_id, save_path)
        self._download_queue.start_selected([new_id])
        self._save_settings()
        if not self._downloads_visible:
            self._toggle_downloads()
        self._extract_status_var.set(f"Redownloading {file_name}…")
        log_activity(f"redownload.start file='{file_name}' id={new_id}")
        snap = self._download_queue.snapshot()
        self._rebuild_dl_panel(snap)
        self._refresh_toggle_label()
        return True

    def _apply_downloads_drawer_visibility(self):
        if self._downloads_visible:
            if not self._downloads_frame.winfo_ismapped():
                self._downloads_frame.pack(fill="both", expand=False, after=self._downloads_handle)
        else:
            self._downloads_frame.pack_forget()

    def _apply_downloads_advanced_visibility(self):
        if not hasattr(self, "_dl_advanced_frame"):
            return
        if self._downloads_advanced:
            if not self._dl_advanced_frame.winfo_ismapped():
                children = list(self._downloads_frame.pack_slaves())
                after_widget = children[0] if children else None
                if after_widget is not None:
                    self._dl_advanced_frame.pack(fill="x", after=after_widget)
                else:
                    self._dl_advanced_frame.pack(fill="x")
            self._dl_advanced_btn.config(text="Advanced ▴")
        else:
            self._dl_advanced_frame.pack_forget()
            self._dl_advanced_btn.config(text="Advanced ▾")

    def _toggle_downloads(self):
        self._downloads_visible = not self._downloads_visible
        self._apply_downloads_drawer_visibility()
        if self._downloads_visible and self._download_queue is not None:
            self._rebuild_dl_panel(self._download_queue.snapshot())  # skipped while collapsed
        self._refresh_toggle_label()
        self._save_settings()

    def _toggle_downloads_advanced(self):
        self._downloads_advanced = not self._downloads_advanced
        self._apply_downloads_advanced_visibility()
        self._save_settings()

    def _refresh_toggle_label(self, snap: dict | None = None, statuses: dict | None = None):
        if not hasattr(self, "_downloads_toggle_btn"):
            return
        chevron = "▴" if self._downloads_visible else "▾"
        if self._download_queue is None:
            self._downloads_toggle_btn.config(text=f"📥 Downloads {chevron}")
            if hasattr(self, "_dl_summary_lbl"):
                self._dl_summary_lbl.config(text="Idle", fg=FG_DIM)
            self.title(f"MiNERVA Archive Browser v{APP_VERSION}")
            return
        if snap is None:
            snap = self._download_queue.snapshot()
        n_active = len(snap["active"])
        n_pending = len(snap["pending"]) + len(snap.get("retry", []))
        n_done = len(snap["done"])

        total_speed = 0.0
        avg_progress = 0.0
        if self._torrent_engine:
            if statuses is None:
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

        label = f"📥 Downloads {chevron}"
        if parts:
            label += "  (" + "  •  ".join(parts) + ")"
        self._downloads_toggle_btn.config(text=label)

        if hasattr(self, "_dl_summary_lbl"):
            if parts:
                self._dl_summary_lbl.config(text=" · ".join(parts), fg=ACCENT if n_active else FG_DIM)
            else:
                self._dl_summary_lbl.config(text="Idle", fg=FG_DIM)

        if n_active > 0:
            pct = int(avg_progress * 100)
            self.title(f"[{pct}% @ {_format_speed(total_speed)}] MiNERVA Archive Browser v{APP_VERSION}")
        else:
            self.title(f"MiNERVA Archive Browser v{APP_VERSION}")

    def _poll_downloads(self):
        try:
            self._poll_downloads_once()
        except Exception as e:
            # One bad tick (e.g. a TclError on a destroyed widget) must not stop download
            # updates for the rest of the session; log at most every 30 s.
            now = time.monotonic()
            if now - self._poll_error_logged_at > 30:
                self._poll_error_logged_at = now
                log_error("MinervaApp._poll_downloads tick failed", e)
        finally:
            if not self._quitting:
                try:
                    self._poll_after_id = self.after(500, self._poll_downloads)
                except tk.TclError:
                    pass  # window destroyed

    def _downloads_panel_visible(self) -> bool:
        """True when rebuilding the downloads panel would actually be seen."""
        if not self._downloads_visible:
            return False
        try:
            return self.state() in ("normal", "zoomed")
        except tk.TclError:
            return False

    def _poll_downloads_once(self):
        engine = self._torrent_engine
        dl_queue = self._download_queue
        if engine is None or dl_queue is None:
            return
        finished_ids: list[str] = []
        finished_names: list[str] = []
        queue_changed = False
        while True:
            try:
                event = engine.events.get_nowait()
            except queue.Empty:
                break
            etype = event.get("type")
            did = event.get("id", "")
            if etype == "finished":
                meta = engine.get_meta(did) or {}
                if meta.get("name"):
                    finished_names.append(meta["name"])
                if not self._seed_var.get():
                    engine.stop_seeding(did)
                dl_queue.on_finished(did)
                finished_ids.append(did)
                queue_changed = True
            elif etype == "error":
                # Transient failures (stall, busy torrent, network) wait out a backoff and retry.
                dl_queue.on_failed(did, event.get("msg", "Unknown error"), retryable=bool(event.get("retryable")))
                queue_changed = True
        if dl_queue.tick():  # retry timers that elapsed
            queue_changed = True
        if finished_ids:
            # Add the new files to the library index instead of rescanning the disk.
            if self._library_index.add_names(finished_names):
                self._request_icon_refresh()
            self._prompt_post_download_actions_batch(finished_ids)
        if queue_changed:
            self._save_settings()

        snap = dl_queue.snapshot()
        statuses = engine.get_all_statuses()
        # Rebuilding the row widgets is wasted work while the drawer is collapsed or the
        # window is minimised/in the tray; _toggle_downloads rebuilds when it is reopened.
        if self._downloads_panel_visible():
            self._rebuild_dl_panel(snap, statuses)
        self._refresh_toggle_label(snap, statuses)

    def _normalize_downloaded_file_location(self, download_id: str):
        if not self._torrent_engine:
            return
        meta = self._torrent_engine.get_meta(download_id)
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
            for attempt in range(5):
                try:
                    shutil.move(str(src), str(target))
                    break
                except PermissionError:
                    # Windows: libtorrent/AV may still hold the file for a moment.
                    if attempt == 4:
                        raise
                    time.sleep(1)
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

    def _queued_keys(self) -> frozenset[str]:
        q = self._download_queue
        return q.queued_keys() if q is not None else frozenset()

    def _reset_row_status(self):
        self._row_keys.clear()
        self._row_status.clear()
        self._icon_state = None

    def _invalidate_library_keys_cache(self):
        """Rebuild the on-disk key index in the background (never on the Tk thread)."""
        self._library_index.rescan(self.get_download_dir(), on_change=self._on_library_index_changed)

    def _on_disk_library_keys(self) -> frozenset[str]:
        return self._library_index.ensure(
            self.get_download_dir(), on_change=self._on_library_index_changed
        )

    def _on_library_index_changed(self):
        # Called from the index's worker thread.
        try:
            self.after(0, self._request_icon_refresh)
        except (RuntimeError, tk.TclError):
            pass  # window already closed

    def _request_icon_refresh(self):
        """Coalesce bursts (e.g. queueing 200 files) into a single icon pass."""
        if self._icon_refresh_after_id is not None:
            return
        self._icon_refresh_after_id = self.after(40, self._run_icon_refresh)

    def _run_icon_refresh(self):
        self._icon_refresh_after_id = None
        self._refresh_library_status_icons()

    def _refresh_library_status_icons(self):
        """Update ⬇/✓ icons, touching only rows whose status actually changed."""
        if not hasattr(self, "_right_tree") or not self._row_keys:
            return
        queued_keys = self._queued_keys()
        disk_keys = self._on_disk_library_keys()
        last = self._icon_state
        if last is not None and last[0] == queued_keys and last[1] is disk_keys:
            return
        self._icon_state = (queued_keys, disk_keys)
        tree = self._right_tree
        icons = {"downloaded": "✓", "queued": "⬇"}
        for iid, keys in self._row_keys.items():
            status = status_from_keys(keys, queued_keys, disk_keys)
            if status == self._row_status.get(iid, ""):
                continue
            self._row_status[iid] = status
            try:
                tags = set(tree.item(iid, "tags") or ())
                tags.discard("queued")
                tags.discard("downloaded")
                if status:
                    tags.add(status)
                tree.set(iid, "dlstat", icons.get(status, ""))
                tree.item(iid, tags=tuple(tags))
            except tk.TclError:
                continue  # row was removed between render and refresh

    def _hide_dlstat_tip(self):
        tw = getattr(self, "_dlstat_tip_window", None)
        self._dlstat_tip_window = None
        self._dlstat_tip_text = ""
        if tw:
            try:
                tw.destroy()
            except Exception:
                pass

    def _on_right_motion(self, event):
        row = self._right_tree.identify_row(event.y)
        col = self._right_tree.identify_column(event.x)
        if not row or row == "__empty_state__" or col != "#2":
            self._hide_dlstat_tip()
            return
        icon = self._right_tree.set(row, "dlstat")
        text = ""
        if icon == "✓":
            text = "Already downloaded"
        elif icon == "⬇":
            text = "Already in the download queue"
        if not text:
            self._hide_dlstat_tip()
            return
        if self._dlstat_tip_window and self._dlstat_tip_text == text:
            return
        self._hide_dlstat_tip()
        tw = tk.Toplevel(self._right_tree)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{event.x_root + 12}+{event.y_root + 16}")
        tk.Label(
            tw, text=text, justify="left",
            background=PANEL, foreground=FG, relief="solid", borderwidth=1,
            font=("TkDefaultFont", 8), padx=6, pady=3,
        ).pack()
        self._dlstat_tip_window = tw
        self._dlstat_tip_text = text

    def _rebuild_dl_panel(self, snap: dict, statuses: dict | None = None):
        """Bring the downloads list up to date with the queue/engine (cheap when nothing changed)."""
        if statuses is None:
            statuses = self._torrent_engine.get_all_statuses() if self._torrent_engine else {}
        self._downloads_panel.sync(snap, statuses, self._extract_progress)
        self._request_icon_refresh()

    def _show_rom_tools_menu(self):
        callbacks = {
            "compress_chd": self._compress_ps1_button_click,
            "unpack_xbox": self._unpack_xbox_button_click,
            "repair_chd": self._repair_incorrect_chd_button_click,
            "clean_bin_cue": self._clean_bin_cue_button_click,
            "clean_names": self._clean_chd_names_button_click,
            "verify_extracted": self._verify_extracted_button_click,
            "verify_archives": self._verify_downloaded_archives_button_click,
            "ensure_dkeys": self._ensure_ps3_dkeys_button_click,
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

    def _refresh_extract_rows(self):
        """Extraction progress changed (called via after(0) from worker threads; coalesced)."""
        if self._extract_refresh_pending:
            return
        self._extract_refresh_pending = True
        self.after(100, self._flush_extract_rows)

    def _flush_extract_rows(self):
        self._extract_refresh_pending = False
        if self._download_queue is not None and self._downloads_panel_visible():
            self._rebuild_dl_panel(self._download_queue.snapshot())

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
            "downloads_advanced_open": bool(getattr(self, "_downloads_advanced", False)),
            "auto_extract_default": bool(self._auto_extract_default_var.get()),
            "delete_archive_default": bool(self._delete_archive_default_var.get()),
            "compress_ps1_chd": bool(self._compress_ps1_chd_var.get()),
            "unpack_xbox_iso": bool(self._unpack_xbox_iso_var.get()),
            "autostart_with_windows": bool(self._autostart_var.get()),
            "start_minimized": bool(self._start_minimized_var.get()),
            "offer_companions": bool(self._offer_companions_var.get()),
            "seed_after_download": bool(self._seed_var.get()),
            "download_queue": self._get_persisted_queue_for_settings(),
            "download_history": list(self._download_history.values())[-400:],
            "last_path": self._current_path,
            "last_search_query": self._search_var.get(),
        }

    def _save_settings(self):
        """Snapshot the settings now; the write happens on a background thread (latest wins)."""
        settings = self._collect_settings()
        self._settings = settings
        self._settings_writer.submit(settings)

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
            "source": strip_default_trackers(source),
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
            source = item["source"]
            local_source = pathlib.Path(source)
            source_missing = (
                not source.startswith(("magnet:", "http://", "https://"))
                and not local_source.exists()
            )
            if item["start_requested"] and not source_missing:
                requested_ids.append(did)
            elif source_missing:
                log_activity(
                    f"queue.restore.skip_start name='{item['name']}' "
                    f"missing_source='{source}'"
                )

        if requested_ids:
            self._download_queue.start_selected(requested_ids)
        if restored_any:
            self._refresh_toggle_label()
            self._save_settings()

    def _on_download_dir_change(self, *_):
        self._save_settings()
        # Debounced: the folder entry fires this on every keystroke.
        if self._library_rescan_after_id is not None:
            self.after_cancel(self._library_rescan_after_id)
        self._library_rescan_after_id = self.after(400, self._run_library_rescan)

    def _run_library_rescan(self):
        self._library_rescan_after_id = None
        self._invalidate_library_keys_cache()

    def _on_extract_defaults_change(self):
        if self._compress_ps1_chd_var.get() and not self._chdman_path:
            self._extract_status_var.set("PS1/PS2→CHD enabled but chdman.exe not found")
            self._ensure_chdman_available_async()
        elif self._unpack_xbox_iso_var.get() and not self._xbox_unpack_tool:
            self._extract_status_var.set("Xbox unpack enabled but xdvdfs/extract-xiso not found")
            self._ensure_xbox_unpack_tool_async()
        elif self._xbox_unpack_tool:
            self._extract_status_var.set(f"Xbox tool: {self._xbox_unpack_tool['exe']}")
        elif self._chdman_path:
            self._extract_status_var.set(f"CHD tool: {self._chdman_path}")
        else:
            self._extract_status_var.set("")
        self._save_settings()

    def _on_seed_setting_change(self):
        if self._torrent_engine is not None:
            self._torrent_engine.set_seeding(bool(self._seed_var.get()))
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

            extracted_ok = False
            last_err = ""
            hidden_kwargs = _hidden_subprocess_kwargs()
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
                    **hidden_kwargs,
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
                        **hidden_kwargs,
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

    def _ensure_xbox_unpack_tool_async(self):
        if self._xbox_tool_download_in_progress or self._xbox_unpack_tool:
            return
        self._xbox_tool_download_in_progress = True
        self._extract_status_var.set("Installing Xbox unpack tool (xdvdfs)…")

        def worker():
            tool = self._auto_install_xdvdfs()
            self.after(0, lambda t=tool: self._finish_xbox_unpack_install(t))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_install_xdvdfs(self) -> dict | None:
        found = find_xbox_unpack_tool()
        if found:
            return found

        base = get_runtime_base_dir()
        tmp_root = base / "_xdvdfs_install_tmp"
        zip_path = tmp_root / "xdvdfs.zip"
        extract_dir = tmp_root / "extracted"
        out_dir = base / "tools" / "xdvdfs"
        out_name = "xdvdfs.exe" if IS_WINDOWS else "xdvdfs"
        out_path = out_dir / out_name

        try:
            if tmp_root.exists():
                shutil.rmtree(tmp_root, ignore_errors=True)
            tmp_root.mkdir(parents=True, exist_ok=True)
            extract_dir.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)

            api_url = f"https://api.github.com/repos/{XDVDFS_GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(api_url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            asset = pick_xdvdfs_release_asset(data.get("assets") or [], windows=IS_WINDOWS)
            dl_url = (asset or {}).get("browser_download_url")
            if not dl_url:
                raise RuntimeError("Could not find an xdvdfs CLI zip for this platform")
            log_activity(f"xbox.install.download url='{dl_url}'")

            dl_req = urllib.request.Request(dl_url, headers={"User-Agent": "MiNERVA-Browser/1.0"})
            with urllib.request.urlopen(dl_req, timeout=120) as resp, zip_path.open("wb") as handle:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            if not zip_path.exists() or zip_path.stat().st_size <= 0:
                raise RuntimeError("Downloaded xdvdfs package is empty")

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            found_bin = next(
                (
                    p for p in extract_dir.rglob("*")
                    if p.is_file() and p.name.lower() in {"xdvdfs.exe", "xdvdfs"}
                ),
                None,
            )
            if found_bin is None:
                raise RuntimeError("xdvdfs binary was not found in the release zip")
            shutil.copy2(found_bin, out_path)
            if not IS_WINDOWS:
                try:
                    os.chmod(out_path, 0o755)
                except OSError:
                    pass
            if not out_path.exists() or out_path.stat().st_size <= 0:
                raise RuntimeError("Failed to place xdvdfs in tools folder")
            log_activity(f"xbox.install.ok copied='{out_path}'")
            return {"kind": "xdvdfs", "label": "xdvdfs", "exe": str(out_path)}
        except Exception as e:
            log_error("MinervaApp._auto_install_xdvdfs failed", e)
            return find_xbox_unpack_tool()
        finally:
            try:
                if tmp_root.exists():
                    shutil.rmtree(tmp_root, ignore_errors=True)
            except Exception:
                pass

    def _finish_xbox_unpack_install(self, tool: dict | None):
        self._xbox_tool_download_in_progress = False
        self._xbox_unpack_tool = tool
        if tool:
            self._extract_status_var.set(f"Xbox tool ready: {tool['exe']}")
            log_activity(f"xbox.install.ready exe='{tool['exe']}' kind={tool.get('kind')}")
        else:
            self._extract_status_var.set(
                "Could not install xdvdfs. Place xdvdfs or extract-xiso in tools/ and retry."
            )
            log_activity("xbox.install.fail")

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
        ids = self._downloads_panel.selected_pending_ids()
        if not self._download_queue or not ids:
            return
        self._download_queue.start_selected(ids)
        self._save_settings()

    def _start_specific_queued(self, download_id: str):
        if not self._download_queue:
            return
        self._download_queue.start_selected([download_id])
        self._save_settings()

    def _start_all_queued(self):
        if not self._download_queue:
            return
        self._download_queue.start_all_pending()
        self._save_settings()

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
        self._extract_progress.clear()
        self._sync_downloads_panel()

    def _prompt_post_download_actions_batch(self, download_ids: list[str]):
        if not self._torrent_engine:
            return
        valid_items: list[tuple[str, dict]] = []
        for did in download_ids:
            meta = self._torrent_engine.get_meta(did)
            if meta:
                valid_items.append((did, meta))

        if not valid_items:
            return

        compress_chd = bool(self._compress_ps1_chd_var.get())
        unpack_xbox = bool(self._unpack_xbox_iso_var.get())
        decompress = bool(self._auto_extract_default_var.get())
        delete_archive = bool(self._delete_archive_default_var.get())
        for did, meta in valid_items:
            meta["auto_extract"] = bool(decompress)
            meta["compress_chd"] = bool(compress_chd)
            meta["unpack_xbox_iso"] = bool(unpack_xbox)
            meta["delete_archive"] = bool(delete_archive)
            self._extract_download(did)

    def _open_current_downloads_folder(self):
        self._open_folder(pathlib.Path(self.get_download_dir()))

    def _open_current_extracted_folder(self):
        self._open_folder(pathlib.Path(self.get_download_dir()) / "extracted")

    def _in_progress_download_names(self) -> set[str]:
        if self._download_queue is None:
            return set()
        return self._download_queue.in_progress_names()

    def _verify_downloaded_archives_button_click(self):
        TITLE = "Verify Archives"
        if self._verify_archives_in_progress:
            messagebox.showinfo(TITLE, "Archive verification is already running.")
            return
        download_dir = pathlib.Path(self.get_download_dir())
        in_progress = self._in_progress_download_names()
        archives = collect_downloaded_archives(download_dir, exclude_names=in_progress)
        skipped = 0
        if in_progress:
            all_found = collect_downloaded_archives(download_dir)
            skipped = sum(1 for p in all_found if p.name in in_progress or p.name.lower() in {n.lower() for n in in_progress})
        if not archives:
            if skipped:
                messagebox.showinfo(
                    TITLE,
                    f"No completed archives to verify.\n{skipped} archive(s) are still downloading and were skipped.",
                )
            else:
                messagebox.showinfo(TITLE, "No downloaded archives found in the save folder.")
            return
        extractors = list(self._extractors)
        self._verify_archives_in_progress = True
        self._extract_status_var.set(f"Verifying 0/{len(archives)} archives…")
        if not self._downloads_visible:
            self._toggle_downloads()

        def worker():
            ok = 0
            failed: list[str] = []
            skipped_live = skipped
            total = len(archives)
            for i, archive in enumerate(archives, start=1):
                live_names = {n.lower() for n in self._in_progress_download_names()}
                if archive.name.lower() in live_names:
                    skipped_live += 1
                    log_activity(f"archive.verify.skip_in_progress file='{archive.name}'")
                    continue
                self.after(
                    0,
                    lambda n=i, t=total, name=archive.name:
                        self._extract_status_var.set(f"Verifying {n}/{t}: {name}"),
                )
                try:
                    verify_archive(archive, extractors=extractors)
                    ok += 1
                    log_activity(f"archive.verify.manual.ok file='{archive}'")
                except Exception as e:
                    failed.append(f"{archive.name}: {e}")
                    log_activity(f"archive.verify.manual.fail file='{archive}' err={e}")
            self.after(
                0,
                lambda o=ok, f=failed, t=total, s=skipped_live: self._finish_archive_verify(o, f, t, s),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_archive_verify(self, ok: int, failed: list[str], total: int, skipped: int = 0):
        self._verify_archives_in_progress = False
        TITLE = "Verify Archives"
        skip_note = f" Skipped {skipped} still downloading." if skipped else ""
        if not failed:
            msg = f"All {ok}/{total} completed archive(s) passed integrity checks.{skip_note}"
            self._extract_status_var.set(msg)
            messagebox.showinfo(TITLE, msg)
            return
        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = (
            f"Passed: {ok}/{total}. Failed: {len(failed)}.{skip_note}\n\n{preview}{more}"
        )
        self._extract_status_var.set(f"Archive verify failed: {len(failed)}/{total}")
        messagebox.showwarning(TITLE, msg)
        failed_names = [line.split(":", 1)[0].strip() for line in failed if ":" in line]
        redownloadable = [n for n in failed_names if self._lookup_download_meta(n)]
        if not redownloadable:
            return
        listed = "\n".join(redownloadable[:8])
        extra = f"\n...and {len(redownloadable) - 8} more" if len(redownloadable) > 8 else ""
        if messagebox.askyesno(
            "Redownload failed archives?",
            f"Redownload {len(redownloadable)} archive(s) that failed verification?\n\n{listed}{extra}",
        ):
            for name in redownloadable:
                self._redownload_item(name=name, confirm=False)

    def _verify_extracted_button_click(self):
        TITLE = "Verify Extracted"
        if getattr(self, "_verify_extracted_in_progress", False):
            messagebox.showinfo(TITLE, "Extracted-folder verification is already running.")
            return
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists():
            messagebox.showinfo(TITLE, "No extracted folder found yet.")
            return
        if not base.is_dir():
            messagebox.showerror(TITLE, "Extracted path exists but is not a folder.")
            return

        targets = [d for d in base.iterdir() if d.is_dir()]
        if not targets:
            messagebox.showinfo(TITLE, "No extracted game folders found.")
            return

        self._verify_extracted_in_progress = True
        self._extract_status_var.set(f"Verifying 0/{len(targets)} extracted folders…")
        download_dir = pathlib.Path(self.get_download_dir())

        def worker():
            ok = 0
            failed: list[str] = []
            total = len(targets)
            for i, d in enumerate(targets, start=1):
                self.after(
                    0,
                    lambda n=i, t=total, name=d.name:
                        self._extract_status_var.set(f"Verifying extracted {n}/{t}: {name}"),
                )
                try:
                    verify_extracted_output(d, d.name)
                    ok += 1
                except Exception as e:
                    failed.append(f"{d.name}: {e}")
            try:
                incorrect = collect_incorrect_chds(base, context=str(base), download_dir=download_dir)
            except Exception as e:
                log_error("verify extracted collect_incorrect_chds failed", e)
                incorrect = []
            self.after(
                0,
                lambda o=ok, f=failed, t=total, inc=incorrect:
                    self._finish_verify_extracted(o, f, t, inc),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_verify_extracted(self, ok: int, failed: list[str], total: int, incorrect: list):
        TITLE = "Verify Extracted"
        self._verify_extracted_in_progress = False
        extra = ""
        if incorrect:
            extra = (
                f"\n\n{len(incorrect)} CHD file(s) look like they were converted "
                "for a system that does not use CHD."
            )
        if not failed:
            msg = f"Verified {ok}/{total} extracted folders successfully.{extra}"
            self._extract_status_var.set(msg if not incorrect else f"Found {len(incorrect)} incorrect CHD(s)")
            if incorrect:
                if messagebox.askyesno(
                    TITLE,
                    msg + "\n\nRestore those discs to ISO/CUE (or redownload if needed)?",
                ):
                    self._repair_incorrect_chd_button_click(already_confirmed=True)
                return
            messagebox.showinfo(TITLE, msg)
            return

        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = f"Verified {ok}/{total}. Failed: {len(failed)}.{extra}\n\n{preview}{more}"
        self._extract_status_var.set(f"Verify failed: {len(failed)} folder(s)")
        messagebox.showwarning(TITLE, msg)
        if incorrect and messagebox.askyesno(TITLE, "Also restore incorrect CHD conversions now?"):
            self._repair_incorrect_chd_button_click(already_confirmed=True)

    def _history_name_for_rom(self, chd_path: pathlib.Path, suggested: str | None = None) -> str | None:
        guesses: list[str] = []
        if suggested:
            guesses.append(suggested)
        guesses.extend(
            [
                chd_path.name,
                chd_path.stem + ".zip",
                chd_path.stem + ".7z",
                chd_path.parent.name + ".zip",
            ]
        )
        for name in guesses:
            if self._lookup_download_meta(name):
                return name
        for hist_name in self._download_history:
            if names_refer_to_same_rom(hist_name, chd_path.stem) or names_refer_to_same_rom(
                hist_name, chd_path.parent.name
            ):
                return hist_name
        return None

    def _repair_incorrect_chd_button_click(self, *, already_confirmed: bool = False):
        TITLE = "Fix incorrect CHD"
        if self._chd_repair_in_progress:
            messagebox.showinfo(TITLE, "CHD repair is already running.")
            return
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        download_dir = pathlib.Path(self.get_download_dir())
        if not base.exists() or not base.is_dir():
            messagebox.showinfo(TITLE, "No extracted folder found yet.")
            return
        found = collect_incorrect_chds(base, context=str(base), download_dir=download_dir)
        if not found:
            messagebox.showinfo(
                TITLE,
                "No CHD files look incorrectly converted (PSP, PS3, GameCube, Wii, Xbox, etc.).",
            )
            return
        preview = "\n".join(p.name for p in found[:10])
        more = f"\n...and {len(found) - 10} more" if len(found) > 10 else ""
        if not already_confirmed:
            if not messagebox.askyesno(
                TITLE,
                f"Found {len(found)} CHD file(s) that probably should not be CHD.\n\n"
                f"{preview}{more}\n\n"
                "Restore the original ISO/CUE when possible, otherwise redownload?",
            ):
                return
        self._chd_repair_in_progress = True
        self._chd_progress_var.set(0.0)
        self._extract_status_var.set(f"Repairing {len(found)} incorrect CHD file(s)…")

        def worker():
            def _progress(pct: int, status: str):
                self.after(0, lambda p=pct, s=status: (
                    self._chd_progress_var.set(p),
                    self._extract_status_var.set(s),
                ))

            try:
                results = repair_incorrect_chds(
                    base,
                    chdman_path=self._chdman_path,
                    download_dir=download_dir,
                    extractors=list(self._extractors),
                    progress_cb=_progress,
                )
            except Exception as e:
                log_error("repair_incorrect_chds failed", e)
                results = []
                self.after(0, lambda err=e: self._finish_chd_repair([], str(err)))
                return
            self.after(0, lambda r=results: self._finish_chd_repair(r, None))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_chd_repair(self, results: list, error: str | None):
        self._chd_repair_in_progress = False
        self._chd_progress_var.set(100.0)
        TITLE = "Fix incorrect CHD"
        if error:
            self._extract_status_var.set(f"CHD repair failed: {error[:80]}")
            messagebox.showerror(TITLE, f"CHD repair failed:\n{error}")
            return
        reversed_n = [r for r in results if r.get("action") == "reversed"]
        kept = [r for r in results if r.get("action") == "kept"]
        need = [r for r in results if r.get("action") == "needs_redownload"]
        errors = [r for r in results if r.get("action") == "error"]
        lines = [
            f"Restored {len(reversed_n)} disc(s) from CHD.",
            f"Left {len(kept)} CHD(s) that are valid for their system.",
        ]
        if errors:
            lines.append(f"{len(errors)} error(s).")
        redownload_names: list[str] = []
        for item in need:
            path = item.get("path")
            suggested = item.get("redownload_name")
            hist = None
            if isinstance(path, pathlib.Path):
                hist = self._history_name_for_rom(path, suggested)
            elif suggested:
                hist = self._lookup_download_meta(suggested) and suggested
            if hist:
                redownload_names.append(hist)
        if redownload_names:
            listed = "\n".join(redownload_names[:8])
            extra = f"\n...and {len(redownload_names) - 8} more" if len(redownload_names) > 8 else ""
            lines.append(f"{len(redownload_names)} need a redownload.")
            msg = "\n".join(lines) + f"\n\nRedownload now?\n\n{listed}{extra}"
            self._extract_status_var.set(f"Restored {len(reversed_n)}; {len(redownload_names)} need redownload")
            if messagebox.askyesno(TITLE, msg):
                for name in redownload_names:
                    self._redownload_item(name=name, confirm=False)
            return
        msg = "\n".join(lines)
        if not results:
            msg = "No incorrect CHD files were repaired."
        self._extract_status_var.set(
            f"CHD repair: restored {len(reversed_n)}, kept {len(kept)}"
        )
        messagebox.showinfo(TITLE, msg)

    def _prompt_repair_redownloads(self, items: list):
        names: list[str] = []
        for item in items:
            path = item.get("path")
            suggested = item.get("redownload_name")
            hist = None
            if isinstance(path, pathlib.Path):
                hist = self._history_name_for_rom(path, suggested)
            elif suggested and self._lookup_download_meta(suggested):
                hist = suggested
            if hist:
                names.append(hist)
        if not names:
            return
        listed = "\n".join(names[:8])
        extra = f"\n...and {len(names) - 8} more" if len(names) > 8 else ""
        if messagebox.askyesno(
            "Redownload original ROM?",
            f"{len(names)} CHD file(s) could not be reversed. Redownload the original ROM(s)?\n\n{listed}{extra}",
        ):
            for name in names:
                self._redownload_item(name=name, confirm=False)

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

                    made = compress_ps1_to_chd(
                        d,
                        self._chdman_path,
                        progress_cb=_manual_progress,
                        context=str(d),
                    )
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

    def _unpack_xbox_button_click(self):
        if self._xbox_unpack_in_progress:
            messagebox.showinfo("Unpack Xbox ISOs", "Xbox ISO unpack is already running.")
            return
        base = pathlib.Path(self.get_download_dir()) / "extracted"
        if not base.exists() or not base.is_dir():
            messagebox.showinfo("Unpack Xbox ISOs", "No extracted folder found yet.")
            return
        tool = self._xbox_unpack_tool or find_xbox_unpack_tool()
        if tool is None:
            self._ensure_xbox_unpack_tool_async()
            messagebox.showinfo(
                "Unpack Xbox ISOs",
                "xdvdfs is not installed yet. Installation has started in the background.",
            )
            return
        targets = [d for d in base.iterdir() if d.is_dir()]
        if not targets:
            messagebox.showinfo("Unpack Xbox ISOs", "No extracted game folders found.")
            return
        self._xbox_unpack_in_progress = True
        self._extract_status_var.set("Xbox ISO unpack running…")
        self._chd_progress_var.set(0.0)

        def worker():
            unpacked = 0
            failed: list[str] = []
            planned = 0
            done_total = 0
            for d in targets:
                planned += len(collect_xbox_iso_sources(d, context=str(d)))
            if planned <= 0:
                self.after(0, lambda: self._chd_progress_var.set(0.0))
            for d in targets:
                try:
                    def _manual_progress(done, total, iso_name: str):
                        display_total = max(planned, 1)
                        frac = (done_total + max(0.0, float(done))) / display_total
                        pct = max(0, min(99, int(frac * 100)))
                        self.after(0, lambda p=pct: self._chd_progress_var.set(float(p)))
                        self.after(
                            0,
                            lambda p=pct, n=iso_name, t=display_total:
                                self._extract_status_var.set(
                                    f"Dumping Xbox ISO {display_filename(n)} ({p}%)"
                                    + (f"  [{t} disc(s)]" if t > 1 else "")
                                ),
                        )

                    made = unpack_xbox_isos_in_dir(
                        d,
                        tool,
                        progress_cb=_manual_progress,
                        context=str(d),
                        delete_iso=True,
                    )
                    unpacked += made
                    done_total += made
                except Exception as e:
                    failed.append(f"{d.name}: {e}")
            self.after(
                0,
                lambda u=unpacked, f=failed, t=len(targets): self._finish_manual_xbox_unpack(u, f, t),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_manual_xbox_unpack(self, unpacked: int, failed: list[str], total_folders: int):
        self._xbox_unpack_in_progress = False
        self._chd_progress_var.set(0.0 if failed and unpacked <= 0 else 100.0)
        if not failed:
            msg = (
                f"Xbox unpack finished: {unpacked} ISO(s) dumped across {total_folders} folder(s). "
                "Copy the game folder (with default.xex) to your modded Xbox 360."
            )
            self._extract_status_var.set(msg)
            messagebox.showinfo("Unpack Xbox ISOs", msg)
            return
        preview = "\n".join(failed[:8])
        more = f"\n...and {len(failed) - 8} more" if len(failed) > 8 else ""
        msg = (
            f"Xbox unpack completed with issues.\n"
            f"Unpacked: {unpacked} ISO(s), Failed folders: {len(failed)}.\n\n"
            f"{preview}{more}"
        )
        self._extract_status_var.set(f"Xbox unpack issues: {len(failed)} folder(s)")
        messagebox.showwarning("Unpack Xbox ISOs", msg)

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
        FILE_EXTS = {
            ".chd", ".bin", ".cue", ".iso", ".img", ".mdf", ".mds",
            ".gdi", ".dkey", ".key", ".zip", ".7z", ".rar",
        }
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

    def _migrate_root_roms_on_launch(self):
        app_root = get_runtime_base_dir()
        dest = pathlib.Path(self.get_download_dir())
        old_root = str(app_root.resolve())
        new_dest = str(dest.resolve())
        moved, failed = migrate_app_root_roms(app_root, dest)
        if moved:
            log_activity(f"migrate.startup moved={moved} dest='{dest}'")
            self._extract_status_var.set(f"Moved {moved} leftover ROM(s) into downloads/")
        if failed:
            log_activity(f"migrate.startup.failed {len(failed)}: {failed[:3]}")
        rewritten = 0
        for item in self._download_history.values():
            sp = item.get("save_path")
            if isinstance(sp, str) and str(pathlib.Path(sp).resolve()) == old_root:
                item["save_path"] = new_dest
                rewritten += 1
        saved_queue = self._settings.get("download_queue", [])
        if isinstance(saved_queue, list):
            for raw in saved_queue:
                if isinstance(raw, dict) and isinstance(raw.get("save_path"), str):
                    try:
                        if str(pathlib.Path(raw["save_path"]).resolve()) == old_root:
                            raw["save_path"] = new_dest
                            rewritten += 1
                    except Exception:
                        pass
        if rewritten:
            self._save_settings()

    def _start_startup_cleanup(self):
        """The cleanup walks the whole extracted/ tree; keep that off the UI thread."""
        threading.Thread(target=self._run_startup_cleanup, name="startup-cleanup", daemon=True).start()

    def _run_startup_cleanup(self):
        try:
            download_dir = self.get_download_dir()
            base = pathlib.Path(download_dir) / "extracted"
            if not base.exists() or not base.is_dir():
                return

            chd_files = list(base.rglob("*.chd"))
            log_activity(f"startup.cleanup detected {len(chd_files)} CHD files")

            renamed, unchanged, cleanup_failed = clean_chd_names_in_base(base)
            log_activity(
                f"startup.cleanup names renamed={renamed} unchanged={unchanged} failed={len(cleanup_failed)}"
            )
            chd_files = list(base.rglob("*.chd"))
            if not chd_files:
                return

            bin_files = list(base.rglob("*.bin"))
            cue_files = list(base.rglob("*.cue"))
            iso_files = list(base.rglob("*.iso"))
            source_count = len(bin_files) + len(cue_files) + len(iso_files)
            if source_count <= 0:
                return

            removed_bins = 0
            removed_cues = 0
            removed_isos = 0
            delete_failed: list[str] = []

            for chd in chd_files:
                if not chd_companions_safe_to_delete(chd, context=str(base)):
                    log_activity(f"startup.cleanup.skip_incorrect_chd file='{chd}'")
                    continue
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
        if self._download_queue:
            self._download_queue.cancel(download_id)
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
            self._filter_bar.refresh_summary()
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
        self._move_queued([download_id], "up")

    def _move_queued_down(self, download_id: str):
        self._move_queued([download_id], "down")

    def _move_queued(self, ids: list[str], where: str):
        if self._download_queue:
            self._download_queue.move(ids, where)
            self._sync_downloads_panel()
            self._save_settings()

    def _sync_downloads_panel(self):
        if self._download_queue is not None:
            self._rebuild_dl_panel(self._download_queue.snapshot())

    # -- downloads list actions (wired to DownloadsPanel) ----------------------------------------
    def _build_panel_actions(self) -> PanelActions:
        return PanelActions(
            start_now=self._panel_start_now,
            toggle_pause=self._panel_toggle_pause,
            cancel=self._panel_cancel,
            remove=self._panel_remove,
            retry=self._panel_retry,
            redownload=self._panel_redownload,
            move=self._move_queued,
            open_folder=self._panel_open_folder,
            show_error=self._panel_show_error,
            copy_names=self._panel_copy_names,
            on_filter_change=self._sync_downloads_panel,
        )

    def _done_item(self, download_id: str) -> dict | None:
        if self._download_queue is None:
            return None
        return next((d for d in self._download_queue.snapshot()["done"] if d["id"] == download_id), None)

    def _panel_start_now(self, ids: list[str]):
        if self._download_queue:
            self._download_queue.start_selected(ids)
            self._save_settings()
            self._sync_downloads_panel()

    def _panel_toggle_pause(self, ids: list[str]):
        for did in ids:
            self._toggle_pause(did)

    def _panel_cancel(self, ids: list[str]):
        snap = self._download_queue.snapshot() if self._download_queue else {"active": []}
        active = set(snap["active"])
        needs_confirm = len(ids) > 1 or any(i in active for i in ids)
        if needs_confirm and not messagebox.askyesno(
            "Cancel downloads",
            f"Cancel {len(ids)} download(s)?\n\nPartly downloaded data for them is deleted.",
        ):
            return
        for did in ids:
            self._cancel_download(did)
        self._sync_downloads_panel()

    def _panel_remove(self, ids: list[str]):
        if not self._download_queue:
            return
        for did in ids:
            self._download_queue.pop_done(did)
            self._extract_progress.pop(did, None)
        self._save_settings()
        self._sync_downloads_panel()

    def _panel_retry(self, ids: list[str]):
        if not self._download_queue:
            return
        retried = 0
        for did in ids:
            if self._download_queue.requeue_done(did, str(uuid.uuid4()), start=True):
                self._extract_progress.pop(did, None)
                retried += 1
        if retried:
            self._status_var.set(f"Retrying {retried} download(s)")
            self._save_settings()
            self._sync_downloads_panel()

    def _panel_redownload(self, ids: list[str]):
        if not ids:
            return
        if len(ids) > 1 and not messagebox.askyesno(
            "Redownload", f"Delete {len(ids)} files and download them again?"
        ):
            return
        for did in ids:
            item = self._done_item(did)
            self._redownload_item(download_id=did, name=(item or {}).get("name"), confirm=len(ids) == 1)

    def _panel_open_folder(self, ids: list[str], extracted: bool):
        for did in ids:
            item = self._done_item(did)
            base = pathlib.Path((item or {}).get("save_path") or self.get_download_dir())
            self._open_folder(base / "extracted" if extracted else base)

    def _panel_show_error(self, download_id: str):
        item = self._done_item(download_id)
        if item:
            messagebox.showerror(item.get("name", "Download failed"), item.get("error") or "Unknown error")

    def _panel_copy_names(self, ids: list[str]):
        if not self._download_queue:
            return
        snap = self._download_queue.snapshot()
        names = {it["id"]: it["name"] for it in (*snap["pending"], *snap.get("retry", ()), *snap["done"], *snap["active_items"])}
        text = "\n".join(names[i] for i in ids if i in names)
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

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
        browse_path = self._current_path
        by_href = {e["href"]: e for e in self._all_entries}
        for href in hrefs:
            rom_id = extract_rom_id(href)
            if not rom_id:
                continue
            entry = by_href.get(href)
            file_name = entry["name"] if entry else href
            self._submit_lookup(str(uuid.uuid4()), rom_id, file_name, save_path, browse_path)
        self._clear_checked()
        if not self._downloads_visible:
            self._toggle_downloads()

    # -- lookups: bounded worker pool -> result queue -> one batched enqueue on the Tk thread --
    def _submit_lookup(self, download_id: str, rom_id: str, file_name: str, save_path: str,
                       browse_path: str = "", **flags) -> bool:
        """Resolve and enqueue one file on the lookup pool (max 5 concurrent requests)."""
        if not is_safe_leaf_name(file_name):
            self._record_lookup_error("unexpected", file_name, "the file name contains path characters and was refused")
            return False
        if self.get_torrent_engine() is None:
            return False
        with self._lookup_lock:
            self._lookup_pending += 1

        def run():
            try:
                self._lookup_and_enqueue(download_id, rom_id, file_name, save_path, browse_path, **flags)
            except Exception as e:
                log_error(f"MinervaApp lookup worker failed for {file_name}", e)
                self._record_lookup_error("unexpected", file_name, str(e))
            finally:
                with self._lookup_lock:
                    self._lookup_pending -= 1

        try:
            self._lookup_pool.submit(run)
        except RuntimeError:  # pool already shut down (app closing)
            with self._lookup_lock:
                self._lookup_pending -= 1
            return False
        self._schedule_lookup_pump()
        return True

    def _schedule_lookup_pump(self):
        if self._lookup_pump_after_id is None and not self._quitting:
            self._lookup_pump_after_id = self.after(100, self._lookup_pump)

    def _wake_lookup_pump(self):
        """Thread-safe: ask the Tk thread to drain results/errors."""
        try:
            self.after(0, self._schedule_lookup_pump)
        except (RuntimeError, tk.TclError):
            pass  # window closed

    def _record_lookup_error(self, kind: str, name: str, message: str):
        self._lookup_errors.add(kind, name, message)
        self._wake_lookup_pump()

    def _lookup_pump(self):
        self._lookup_pump_after_id = None
        batch: list[QueuedDownload] = []
        while len(batch) < 200:
            try:
                batch.append(self._lookup_results.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._enqueue_resolved(batch)
        with self._lookup_lock:
            pending = self._lookup_pending
        if pending > 0 or not self._lookup_results.empty():
            if pending > 0:
                self._status_var.set(f"Looking up {pending} file(s)…")
            self._schedule_lookup_pump()
        else:
            self._flush_lookup_errors()

    def _enqueue_resolved(self, results: list[QueuedDownload]):
        dl_queue = self._download_queue
        if self.get_torrent_engine() is None or dl_queue is None:
            for r in results:
                dl_queue and dl_queue.release(r.name)
            return
        dl_queue.enqueue_many(
            {"id": r.download_id, "name": r.name, "source": r.source, "so_id": r.so_id, "save_path": r.save_path}
            for r in results
        )
        for r in results:
            self._remember_download(r.name, r.source, r.so_id, r.save_path)
        self._save_settings()  # once per batch, not once per file
        self._request_icon_refresh()
        self._status_var.set(f"Queued {len(results)} download(s)")
        if not self._downloads_visible:
            self._toggle_downloads()

    def _flush_lookup_errors(self):
        errors = self._lookup_errors.take()
        if not errors:
            return
        title, body = LookupErrors.format(errors)
        self._status_var.set(f"{len(errors)} download(s) could not be queued")
        if len(errors) == 1 and errors[0].kind in ("not_found", "no_torrent"):
            messagebox.showwarning(title, body)
        elif len(errors) == 1:
            messagebox.showerror(title, body)
        else:
            messagebox.showwarning(title, body)

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
        skip_companions: bool = False,
    ):
        dl_queue = self._download_queue
        reserved = False
        if not skip_name_dedupe and dl_queue is not None:
            # Atomic claim: concurrent lookups of the same title can no longer both pass.
            if not dl_queue.reserve(file_name):
                if fetch_ps3_dkey and is_ps3_iso_browse_path(browse_path):
                    self._enqueue_matching_ps3_dkey(file_name, save_path)
                return
            reserved = True

        try:
            resolved = self._rom_resolver.resolve(rom_id, file_name)
        except LookupFailure as e:
            if reserved:
                dl_queue.release(file_name)
            log_error(f"MinervaApp._lookup_and_enqueue {e.kind} for {file_name}: {e.message}")
            self._record_lookup_error(e.kind, file_name, e.message)
            return
        except Exception as e:
            if reserved:
                dl_queue.release(file_name)
            log_error(f"MinervaApp._lookup_and_enqueue failed for {file_name}", e)
            self._record_lookup_error("unexpected", file_name, str(e))
            return

        self._lookup_results.put(
            QueuedDownload(download_id, file_name, resolved.source, resolved.so_id, save_path)
        )
        self._wake_lookup_pump()
        if fetch_ps3_dkey and is_ps3_iso_browse_path(browse_path):
            self._enqueue_matching_ps3_dkey(file_name, save_path)
        if not skip_companions:
            self._maybe_prompt_companions(file_name, browse_path, save_path)

    def _maybe_prompt_companions(self, file_name: str, browse_path: str, save_path: str):
        if not bool(self._offer_companions_var.get()):
            return
        if classify_release(file_name, browse_path) != KIND_BASE:
            return
        try:
            self.after(0, lambda: self._status_var.set(f"Looking for DLC/updates: {file_name}…"))
            current = list(self._all_entries) if browse_path == self._current_path else None
            items = find_companions(
                file_name,
                browse_path,
                current_entries=current,
                fetch_fn=fetch_entries,
                latest_update_only=True,
            )
        except Exception as e:
            log_error(f"companion search failed for {file_name}", e)
            return
        if not items:
            return
        self.after(0, lambda: self._show_companion_prompt(file_name, items, browse_path, save_path))

    def _show_companion_prompt(self, file_name: str, items, browse_path: str, save_path: str):
        if self._companion_dialog_open:
            self._companion_prompt_queue.put((file_name, items, browse_path, save_path))
            return
        self._companion_dialog_open = True
        picked = prompt_companions(self, file_name, items, precheck_dlc=False)
        self._companion_dialog_open = False
        if picked:
            self._enqueue_companion_items(picked, browse_path, save_path)
        try:
            nxt = self._companion_prompt_queue.get_nowait()
        except queue.Empty:
            nxt = None
        if nxt:
            self.after(0, lambda n=nxt: self._show_companion_prompt(*n))

    def _enqueue_companion_items(self, items, browse_path: str, save_path: str):
        for item in items:
            rom_id = item.rom_id
            if not rom_id:
                continue
            if self._download_queue and self._download_queue.has_name(item.name):
                continue
            self._submit_lookup(
                str(uuid.uuid4()), rom_id, item.name, save_path, browse_path,
                skip_companions=True, fetch_ps3_dkey=False,
            )
        if items and not self._downloads_visible:
            self._toggle_downloads()
        n = len(items)
        self._status_var.set(f"Queued {n} DLC/update file(s)")
        log_activity(f"companions.queued count={n} base_folder='{browse_path}'")

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
            rom_save = pathlib.Path(save_path)
            if is_dkey_save_path(rom_save):
                dkey_dir = rom_save
            else:
                dkey_dir = get_dkey_save_dir(rom_save)
            if find_local_dkey(rom_save if not is_dkey_save_path(rom_save) else rom_save.parent, iso_file_name):
                log_activity(f"ps3_dkeys.already_present file='{iso_file_name}'")
                return
            if self._dkey_download_in_progress(dkey_name):
                return
            log_activity(f"ps3_dkeys.match iso='{iso_file_name}' dkey='{dkey_name}' id={rom_id}")
            self._lookup_and_enqueue(
                str(uuid.uuid4()),
                rom_id,
                dkey_name,
                str(dkey_dir),
                PS3_DISC_KEYS_TXT_PATH,
                skip_name_dedupe=True,
                fetch_ps3_dkey=False,
            )
        except Exception as e:
            log_error(f"MinervaApp._enqueue_matching_ps3_dkey failed for {iso_file_name}", e)
            log_activity(f"ps3_dkeys.error file='{iso_file_name}' err={repr(e)}")

    def _resolve_dkey_catalog_entry(self, name: str, save_path: str = "") -> dict | None:
        entry = find_dkey_entry(name)
        if entry:
            return entry
        candidates: list[pathlib.Path] = []
        if save_path:
            candidates.append(pathlib.Path(save_path) / name)
        candidates.append(pathlib.Path(self.get_download_dir()) / name)
        for path in candidates:
            if path.is_file():
                found = find_dkey_entry_for_path(path)
                if found:
                    return found
        return None

    def _dkey_download_in_progress(self, dkey_name: str) -> bool:
        if self._download_queue is None:
            return False
        target = dkey_name.lower()
        for item in self._download_queue.in_progress_items():
            if (item.get("name") or "").lower() != target:
                continue
            if is_dkey_save_path(item.get("save_path")):
                return True
        return False

    def _ensure_ps3_dkeys_button_click(self):
        TITLE = "PS3 Disc Keys"
        if self._ensure_dkeys_in_progress:
            messagebox.showinfo(TITLE, "PS3 dkey check is already running.")
            return
        if not _LT_AVAILABLE:
            messagebox.showinfo(TITLE, "Install libtorrent to download missing dkeys.")
            return
        self.get_torrent_engine()
        download_dir = pathlib.Path(self.get_download_dir())
        self._ensure_dkeys_in_progress = True
        self._extract_status_var.set("Checking PS3 dkeys…")
        if not self._downloads_visible:
            self._toggle_downloads()
        extractors = list(self._extractors)

        def worker():
            queued = 0
            ok = 0
            repaired = 0
            skipped = 0
            missing_catalog = 0
            errors: list[str] = []
            try:
                rom_names = set(collect_local_ps3_rom_names(download_dir))
                if self._download_queue is not None:
                    snap = self._download_queue.snapshot()
                    for item in (*snap["pending"], *snap["done"]):
                        name = item.get("name") or ""
                        if is_dkey_save_path(item.get("save_path")):
                            continue
                        entry = self._resolve_dkey_catalog_entry(name, item.get("save_path") or "")
                        if entry:
                            rom_names.add(entry.get("name") or name)
                    for item in self._download_queue.in_progress_items():
                        name = item.get("name") or ""
                        if is_dkey_save_path(item.get("save_path")):
                            continue
                        entry = self._resolve_dkey_catalog_entry(name, item.get("save_path") or "")
                        if entry:
                            rom_names.add(entry.get("name") or name)
                for hist in self._download_history.values():
                    if is_dkey_save_path(hist.get("save_path")):
                        continue
                    name = hist.get("name") or ""
                    entry = self._resolve_dkey_catalog_entry(name, hist.get("save_path") or "")
                    if entry:
                        rom_names.add(entry.get("name") or name)

                total = len(rom_names)
                for i, rom_name in enumerate(sorted(rom_names), start=1):
                    self.after(
                        0,
                        lambda n=i, t=total, name=rom_name:
                            self._extract_status_var.set(f"PS3 dkeys {n}/{t}: {name}"),
                    )
                    entry = find_dkey_entry(rom_name)
                    if entry is None:
                        missing_catalog += 1
                        continue
                    dkey_name = entry.get("name") or rom_name
                    if find_local_dkey(download_dir, rom_name):
                        ok += 1
                        continue
                    if self._dkey_download_in_progress(dkey_name):
                        skipped += 1
                        continue
                    zip_path = find_local_dkey_zip(download_dir, rom_name)
                    if zip_path is not None and zip_path.is_file():
                        try:
                            zip_size = zip_path.stat().st_size
                            if zip_size <= 0 or zip_size > DKEY_ZIP_MAX_BYTES:
                                raise ArchiveVerificationError("dkey zip size looks wrong")
                            verify_archive(zip_path, extractors=extractors)
                            if find_local_dkey(download_dir, rom_name):
                                ok += 1
                                continue
                            out = zip_path.parent / zip_path.stem
                            extract_archive(zip_path, out, extractors=extractors)
                            if find_local_dkey(download_dir, rom_name):
                                ok += 1
                                continue
                            raise ArchiveVerificationError("dkey zip did not contain a valid .dkey")
                        except Exception as e:
                            log_activity(f"ps3_dkeys.bad_zip file='{zip_path}' err={e}")
                            try:
                                zip_path.unlink()
                            except OSError:
                                pass
                            repaired += 1
                    self._enqueue_matching_ps3_dkey(rom_name, str(download_dir))
                    queued += 1
            except Exception as e:
                log_error("MinervaApp._ensure_ps3_dkeys_button_click failed", e)
                errors.append(str(e))
            self.after(
                0,
                lambda q=queued, o=ok, r=repaired, s=skipped, e=errors:
                    self._finish_ensure_ps3_dkeys(q, o, r, s, e),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_ensure_ps3_dkeys(self, queued: int, ok: int, repaired: int, skipped: int, errors: list[str]):
        self._ensure_dkeys_in_progress = False
        TITLE = "PS3 Disc Keys"
        if self._download_queue is not None:
            self._rebuild_dl_panel(self._download_queue.snapshot())
        parts = [
            f"{ok} already present",
            f"{queued} queued",
            f"{repaired} bad zips replaced",
            f"{skipped} still downloading",
        ]
        msg = "PS3 dkey check complete. " + "; ".join(parts) + "."
        if errors:
            msg += "\n\n" + errors[0]
            self._extract_status_var.set("PS3 dkey check failed")
            messagebox.showwarning(TITLE, msg)
            return
        self._extract_status_var.set(msg)
        messagebox.showinfo(TITLE, msg)

    def _find_downloaded_file(self, save_path: pathlib.Path, file_name: str) -> pathlib.Path | None:
        if not file_name:
            return None
        direct = save_path / file_name
        if direct.is_file():
            return direct
        for depth in range(1, 4):
            pattern = "/".join(["*"] * depth) + f"/{file_name}"
            matches = [p for p in save_path.glob(pattern) if p.is_file()]
            if matches:
                return matches[0]
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
        meta = self._torrent_engine.get_meta(download_id)
        if not meta:
            return

        # Moves the file next to the other downloads.  Runs here (worker thread) because it
        # polls with sleep() while the torrent releases the file; it used to freeze the UI.
        self._normalize_downloaded_file_location(download_id)

        save_path = pathlib.Path(meta["save_path"])
        torrent_dir = save_path / "extracted"
        torrent_dir.mkdir(parents=True, exist_ok=True)
        delete_archive = bool(meta.get("delete_archive"))
        extractors = list(self._extractors)
        file_name = meta["name"]
        log_activity(f"extract.start id={download_id} file='{file_name}' save_path='{save_path}'")

        def _set_progress(pct: int, status: str):
            self._extract_progress[download_id] = {"pct": pct, "status": status}
            shown = f"{display_filename(file_name, 28)} — {status}"
            self.after(0, lambda s=shown: self._extract_status_var.set(s))
            self.after(0, self._refresh_extract_rows)

        self.after(0, lambda: self._chd_progress_var.set(0.0))

        try:
            src = None
            for _ in range(20):
                src = self._find_downloaded_file(save_path, file_name)
                if src is not None:
                    break

                _set_progress(0, f"Waiting for {display_filename(file_name)}…")
                time.sleep(1)

            if src is None:
                log_activity(f"extract.missing id={download_id} file='{file_name}'")
                _set_progress(0, f"Missing downloaded file: {display_filename(file_name)}")
                return
            try:
                src_size = src.stat().st_size
            except OSError:
                src_size = -1
            log_activity(f"extract.source id={download_id} src='{src}' size={src_size}")
            size_label = format_bytes(src_size) if src_size >= 0 else "unknown size"
            _set_progress(0, f"Found {display_filename(src.name)} ({size_label})")

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
                    if current_size > 0:
                        _set_progress(
                            0,
                            f"Waiting for write to finish: {display_filename(src.name)} "
                            f"({format_bytes(current_size)})",
                        )
                last_size = current_size
                time.sleep(1)

            auto_extract = bool(meta.get("auto_extract"))
            compress_chd = bool(meta.get("compress_chd"))
            unpack_xbox = bool(meta.get("unpack_xbox_iso"))
            if is_dkey_save_path(save_path) and is_archive_path(src):
                auto_extract = True
            if not auto_extract and not compress_chd and not unpack_xbox:
                if is_archive_path(src):
                    try:
                        verify_archive(
                            src,
                            extractors=extractors,
                            progress_cb=lambda pct, status: _set_progress(pct, status),
                        )
                        log_activity(f"archive.verify.ok id={download_id} file='{src.name}'")
                        _set_progress(100, "Archive verified ✓")
                    except ArchiveVerificationError as e:
                        log_activity(f"archive.verify.fail id={download_id} file='{src.name}' err={e}")
                        _set_progress(0, f"Bad archive: {str(e)[:48]}")
                        if self._download_queue is not None:
                            self._download_queue.mark_error(download_id, str(e))
                        self.after(
                            0,
                            lambda did=download_id, name=file_name: self._redownload_item(
                                download_id=did, name=name, confirm=True
                            ),
                        )
                    return
                _set_progress(100, "Download complete")
                return

            cleaned_stem = normalize_chd_stem(src.stem) or src.stem
            out_dir = torrent_dir / cleaned_stem
            out_dir.mkdir(parents=True, exist_ok=True)
            process_context = " ".join(
                str(p)
                for p in (
                    out_dir,
                    src,
                    file_name,
                    save_path,
                    meta.get("browse_path") or "",
                )
                if p
            )
            extracted_ok = False
            extracted_dir = None
            raw_xbox_iso = (
                unpack_xbox
                and not is_archive_path(src)
                and should_unpack_xbox_iso(src, process_context)
            )
            if raw_xbox_iso:
                tool = self._xbox_unpack_tool or find_xbox_unpack_tool()
                if tool is None:
                    tool = self._auto_install_xdvdfs()
                    self._xbox_unpack_tool = tool
                if tool is None:
                    raise RuntimeError("Xbox unpack is enabled but xdvdfs/extract-xiso is not available")
                _set_progress(1, f"Dumping Xbox ISO {display_filename(src.name)}…")

                def _raw_dump_progress(pct: int, status: str):
                    shown = max(1, min(99, int(pct)))
                    self.after(0, lambda p=shown: self._chd_progress_var.set(float(p)))
                    _set_progress(shown, status)

                unpack_xbox_iso(
                    src,
                    out_dir,
                    tool,
                    progress_cb=_raw_dump_progress,
                    delete_iso=False,
                )
                extracted_ok = True
                extracted_dir = out_dir
            elif auto_extract or compress_chd or unpack_xbox:
                extracted_ok = extract_archive(
                    src,
                    out_dir,
                    extractors=extractors,
                    progress_cb=lambda pct, status: _set_progress(pct, status)
                )
                extracted_dir = out_dir if extracted_ok else None

            if extracted_ok and extracted_dir is not None:
                _set_progress(90, "Checking extracted files for ROM content…")
                verify_extracted_output(extracted_dir, src.name)
                extracted_files = [p for p in extracted_dir.rglob("*") if p.is_file()]
                _set_progress(
                    92,
                    f"Extracted {len(extracted_files)} file(s) into {display_filename(out_dir.name)}",
                )
                if compress_chd:
                    def _chd_progress(done: int, total: int, cue_name: str):
                        if total <= 0:
                            return
                        pct = 90 + int((done / total) * 9)
                        pct = max(90, min(99, pct))
                        self.after(0, lambda d=done, t=total: self._chd_progress_var.set(d * 100.0 / t))
                        _set_progress(
                            pct,
                            f"CHD {done}/{total}: {display_filename(cue_name)}",
                        )

                    chd_context = " ".join(
                        str(p)
                        for p in (
                            extracted_dir,
                            src,
                            file_name,
                            save_path,
                            meta.get("browse_path") or "",
                        )
                        if p
                    )
                    converted = compress_ps1_to_chd(
                        extracted_dir,
                        self._chdman_path,
                        progress_cb=_chd_progress,
                        context=chd_context,
                    )
                    if converted:
                        _set_progress(
                            99,
                            f"Converted {converted} disc image(s) to CHD",
                        )
                    else:
                        disc_exts = {".cue", ".gdi", ".toc", ".ccd", ".iso", ".mds", ".mdf", ".nrg"}
                        leftover = [
                            p.name
                            for p in extracted_dir.rglob("*")
                            if p.is_file() and p.suffix.lower() in disc_exts
                        ]
                        if leftover:
                            _set_progress(
                                95,
                                "Kept original disc image (this system does not use CHD)",
                            )
                if unpack_xbox and not raw_xbox_iso:
                    xbox_sources = collect_xbox_iso_sources(extracted_dir, context=process_context)
                    if xbox_sources:
                        tool = self._xbox_unpack_tool or find_xbox_unpack_tool()
                        if tool is None:
                            tool = self._auto_install_xdvdfs()
                            self._xbox_unpack_tool = tool
                        if tool is None:
                            raise RuntimeError(
                                "Xbox unpack is enabled but xdvdfs/extract-xiso is not available"
                            )

                        def _xbox_progress(done, total: int, iso_name: str):
                            if total <= 0:
                                return
                            frac = max(0.0, min(1.0, float(done) / float(total)))
                            pct = max(1, min(99, int(frac * 100)))
                            self.after(0, lambda p=pct: self._chd_progress_var.set(float(p)))
                            _set_progress(
                                pct,
                                f"Dumping Xbox ISO {display_filename(iso_name)} ({pct}%)",
                            )

                        unpacked = unpack_xbox_isos_in_dir(
                            extracted_dir,
                            tool,
                            progress_cb=_xbox_progress,
                            context=process_context,
                            delete_iso=True,
                        )
                        if unpacked:
                            _set_progress(
                                99,
                                f"Unpacked {unpacked} Xbox ISO(s) for a modded console",
                            )
                repair_context = " ".join(
                    str(p)
                    for p in (
                        extracted_dir,
                        src,
                        file_name,
                        save_path,
                        meta.get("browse_path") or "",
                    )
                    if p
                )
                incorrect = collect_incorrect_chds(
                    extracted_dir,
                    context=repair_context,
                    download_dir=save_path,
                )
                if incorrect:
                    _set_progress(
                        96,
                        f"Fixing {len(incorrect)} incorrect CHD file(s)…",
                    )
                    repair_results = repair_incorrect_chds(
                        extracted_dir,
                        chdman_path=self._chdman_path,
                        download_dir=save_path,
                        extractors=extractors,
                        progress_cb=lambda pct, status: _set_progress(pct, status),
                    )
                    need = [r for r in repair_results if r.get("action") == "needs_redownload"]
                    restored_n = sum(1 for r in repair_results if r.get("action") == "reversed")
                    if restored_n:
                        _set_progress(98, f"Restored {restored_n} disc(s) that should not be CHD")
                    if need:
                        self.after(
                            0,
                            lambda items=need: self._prompt_repair_redownloads(items),
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
                self._torrent_engine.stop_seeding(download_id)  # release the file if it is being seeded
                src.unlink()
                log_activity(f"extract.delete_archive id={download_id} src='{src}'")

            status_text = "Extracted ✓"
            if extracted_ok and extracted_dir is not None:
                file_count = len([p for p in extracted_dir.rglob("*") if p.is_file()])
                chd_count = len(list(extracted_dir.rglob("*.chd")))
                xbox_exec = [
                    p for p in extracted_dir.rglob("*")
                    if p.is_file() and p.name.lower() in {"default.xex", "default.xbe"}
                ]
                if xbox_exec:
                    status_text = f"Xbox dump {len(xbox_exec)} xex/xbe ✓ ({file_count} files)"
                elif chd_count:
                    status_text = f"Compressed {chd_count} CHD ✓ ({file_count} files)"
                else:
                    status_text = f"Extracted {file_count} file(s) ✓"
            elif not extracted_ok:
                status_text = "Extract failed (archive may be corrupt)"
                if self._download_queue is not None:
                    self._download_queue.mark_error(download_id, status_text)
                self.after(
                    0,
                    lambda did=download_id, name=file_name: self._redownload_item(
                        download_id=did, name=name, confirm=True
                    ),
                )

            _set_progress(100 if extracted_ok else 0, status_text)
            self.after(0, lambda: self._chd_progress_var.set(100.0 if extracted_ok else 0.0))
            log_activity(f"extract.done id={download_id} ok={extracted_ok}")

        except Exception as e:
            log_error(f"MinervaApp._extract_download_sync failed for {file_name}", e)
            log_activity(f"extract.error id={download_id} file='{file_name}' err={repr(e)}")
            _set_progress(0, f"Error: {str(e)[:40]}")
            if self._download_queue is not None:
                self._download_queue.mark_error(download_id, str(e))
            if isinstance(e, ArchiveVerificationError) or is_archive_path(pathlib.Path(file_name)):
                self.after(
                    0,
                    lambda did=download_id, name=file_name: self._redownload_item(
                        download_id=did, name=name, confirm=True
                    ),
                )
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
            **_hidden_subprocess_kwargs(),
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
        self._quitting = False
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
                pystray.MenuItem("Minimize to Tray", lambda icon, item: self.after(0, self._minimize_to_tray)),
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

    def _tray_available(self) -> bool:
        return bool(getattr(self, "_tray_icon", None)) and not getattr(self, "_quitting", False)

    def _minimize_to_tray(self):
        if not self._tray_available():
            self.iconify()
            return
        try:
            self.withdraw()
        except tk.TclError:
            pass

    def _on_window_unmap(self, event):
        """Send the taskbar minimize button to the tray instead of the taskbar."""
        if event.widget is not self or not self._tray_available():
            return
        try:
            if self.state() == "iconic":
                self.after(0, self._minimize_to_tray)
        except tk.TclError:
            pass

    def _on_close_request(self):
        """Titlebar close hides to the tray; use Exit on the tray menu to quit."""
        if self._tray_available():
            self._minimize_to_tray()
            return
        self._on_close()

    def _restore_from_tray(self):
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

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
        self._quitting = True
        self._lookup_pool.shutdown()
        self._shutdown_tray()
        self._save_settings()
        self._settings_writer.close()
        try:
            self.withdraw()  # engine shutdown writes resume data (<= a few seconds); don't look frozen
        except tk.TclError:
            pass
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

    _TIMER_ATTRS = (
        "_poll_after_id", "_render_after_id", "_icon_refresh_after_id", "_lookup_pump_after_id",
        "_library_rescan_after_id", "_search_save_after_id",
    )

    def destroy(self):
        """Cancel our own timers first so none fires into a half-destroyed window."""
        self._quitting = True
        for attr in self._TIMER_ATTRS:
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        super().destroy()

    def _show_error(self, msg, generation: int | None = None):
        if generation is not None and generation != self._nav_generation:
            return
        log_error(f"MinervaApp._show_error: {msg}")
        self._set_loading(False)
        self._right_tree.delete(*self._right_tree.get_children())
        self._reset_row_status()
        self._status_var.set(f"Error: {msg}")
        messagebox.showerror("Error", f"Failed to load page:\n{msg}")
