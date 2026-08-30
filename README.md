# MiNERVA Archive Browser

A portable desktop GUI for browsing and downloading from [minerva-archive.org](https://minerva-archive.org/browse/), built with Python + Tkinter + libtorrent.

![Windows](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue) ![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![libtorrent 2.0](https://img.shields.io/badge/libtorrent-2.0-green)

---

## Features

### Browsing & Modern UI
- 📁 **Two-panel layout** — category tree on the left, file listing on the right
- 🔍 **Integrated search** — instant client-side filtering with live results and `Escape` shortcut
- 💊 **Interactive region pills** — click-to-filter region chips (USA, Europe, Japan, World, etc.) with active glow
- 🏷️ **Dynamic tag filter dropdown** — compact popover menu to hide Demos, Betas, Prototypes, Unlicensed, or Hacks without consuming screen space
- 🧭 **Clickable breadcrumb navigation**
- ⚡ **Async loading** — GUI stays responsive while fetching
- 🌑 **Refined dark theme** — Catppuccin Mocha-inspired palette with comfortable typography, clear metrics, and styled scrollbars

### Downloading
- ✅ **Inline checkboxes & multi-select** — select multiple games then click **Queue Downloads**
- ⚡ **Double-click** any game to queue it instantly
- 🔄 **Download queue** with configurable concurrency (1–10 simultaneous downloads)
- 📂 **Custom save folder** via the Browse button (defaults to `downloads/` next to the app)
- 💾 **State persistence** — preferences, filters, and active/queued downloads persist across app launches
- 🗂️ **Torrent caching** — `.torrent` files cached in `torrentfiles/` to eliminate redundant fetches
- 🚫 **Deduplication** — automatically skips items already pending, active, or completed
- 📦 **DLC / update matching** — after queueing a game, offers matching DLC and updates from the same folder or related digital/PSN/CDN collections (select, download all, or skip)
- 📊 **Real-time metrics** — speed, ETA, progress bars, and state tracking without text clipping

### Extraction & CHD Compression
- 📦 **Auto-extract** — extract archives automatically once download finishes
- 🗜️ **Extractor detection** — auto-detects external extractors (**7-Zip**, **PeaZip**, **WinRAR**) with fallback to Python `zipfile`
- 🎮 **PS1/PS2 BIN/CUE/ISO → CHD** — convert supported disc images to CHD (`chdman`); skips PSP/PS3/GameCube/Wii/Xbox
- 🎮 **Xbox / Xbox 360 ISO unpack** — dump XISO/XGD contents with **xdvdfs** (falls back to **extract-xiso**) into a folder with `default.xex` / `default.xbe` for a modded 360; skips `$SystemUpdate`
- ↩️ **Fix incorrect CHD conversions** — restore PSP/PS3/GC discs that were turned into CHD, or redownload if needed
- 🔍 **Verify downloaded archives** — CRC-test zip/7z/rar files in the save folder and offer redownload on failure
- 🔑 **PS3 disc keys** — auto-queue matching Redump `.dkey` zips into `downloads/dkeys/`, plus a tools action to repair missing keys
- 🛠️ **Unified ROM Tools menu** — grouped utilities for CHD conversion, BIN/CUE cleaning, verification, and name standardization
- 🧹 **Automatic name cleaning** — cleans region tags and disc descriptors while preserving disc numbering
- 🗑️ **Optional source deletion** — automatically deletes source archives and BIN/CUE/ISO files post-conversion
- 🚀 **Startup cleanup** — scans the extracted folder on launch to clean names and remove leftover BIN/CUE files next to valid CHDs only

---

## Requirements

- **Windows 10/11** or **Linux** (x86_64)
- **Standalone binary:** No installation required — download from [Releases](../../releases) and run
- **From source:** Python 3.10+ and `libtorrent` (optional, for inline downloads)

---

## Building from Source

### Windows (PowerShell)
```powershell
.\build.ps1
```

Options:
```powershell
.\build.ps1 -Clean            # Wipe build/, dist/, and .venv/ first
.\build.ps1 -SkipPythonCheck  # Skip Scoop Python auto-install check
```

### Linux (Bash)
```bash
./build.sh
```

Options:
```bash
./build.sh --clean            # Wipe build/, dist/, and .venv/ first
./build.sh --skip-tests       # Skip running the test suite
```

---

## Running from Source

```bash
# Install dependencies
pip install -r requirements-build.txt

# Run the test suite
python -m unittest discover -s tests -v

# Run the application
python minerva_browser.py
```

> **Note:** `libtorrent` is required for downloading. Without it, the browser still works for navigating and searching, but downloads will be disabled. `pillow` and `pystray` enable the system tray icon.

---

## Project Structure

```
├── minerva_browser.py         # Application entry point
├── minerva_browser.spec       # PyInstaller standalone build configuration
├── build.ps1                  # Windows build automation script
├── build.sh                   # Linux build automation script
├── minerva/
│   ├── constants.py           # Paths, theme tokens, trackers, and logging
│   ├── core/
│   │   ├── sqlite_http.py     # HTTP range SQLite reader & web parser
│   │   ├── torrent_engine.py  # libtorrent session engine & DownloadQueue
│   │   ├── extractors.py      # Archive extraction & CHD compression tools
│   │   └── ps3_dkeys.py       # Redump PS3 disc-key catalog matching
│   └── ui/
│       ├── theme.py           # Catppuccin palette & modern TTK style configurations
│       ├── app.py             # Main Tkinter desktop application window
│       └── components/
│           ├── filter_bar.py  # Search entry, Region pills, and Tag popover
│           └── tools_dialog.py# ROM tools menu & utilities modal dialog
└── tests/
    ├── test_sqlite_http.py    # Varint & SQLite B-Tree record unit tests
    ├── test_parsers.py        # HTML & ROM ID parsing tests
    ├── test_extractors.py     # ROM detection & name cleaning tests
    ├── test_download_queue.py # Download queue state & concurrency tests
    ├── test_ui_components.py  # UI theme, filter pills & tag popover tests
    └── test_assets.py         # Asset and icon integrity tests
```

---

## How Downloads Work

MiNERVA distributes all files via BitTorrent:

1. Looks up the selected file in `hashes.db` (fetched via HTTP range requests — no full DB download needed)
2. Downloads the collection `.torrent` file into `torrentfiles/`
3. Instructs libtorrent to download only the selected file within that torrent (`so_id` file priority)
4. Optionally extracts the file using detected extractors (7-Zip / PeaZip / WinRAR / zipfile)
5. Optionally converts supported disc images to CHD and cleans up input files
6. Optionally unpacks Xbox / Xbox 360 ISOs with xdvdfs (or extract-xiso) into a folder with `default.xex` for a modded console
7. For Redump PS3 ISOs, queues the matching disc-key zip into `dkeys/` when one exists
