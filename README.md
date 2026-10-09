# MiNERVA Archive Browser

A portable desktop GUI for browsing and downloading from [minerva-archive.org](https://minerva-archive.org/browse/), built with Python + Tkinter + libtorrent.

![Windows](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue) ![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![libtorrent 2.0](https://img.shields.io/badge/libtorrent-2.0-green)

---

## Features

### Browsing & Modern UI
- 📁 **Two-panel layout** — category tree on the left, file listing on the right
- 🔍 **Integrated search** — instant client-side filtering with live results and `Escape` shortcut
- 🧾 **Filter summary & reset** — always-visible active filters plus a one-click reset
- ⬇ / ✓ **Library status icons** — search rows show if a title is already in the download queue or already on disk (archive or extracted ISO/CHD)
- 💊 **Interactive region pills** — click-to-filter region chips (USA, Europe, Japan, World, etc.) with active glow
- 🏷️ **Dynamic tag filter dropdown** — compact popover menu to hide Demos, Betas, Prototypes, Unlicensed, or Hacks without consuming screen space
- 🧭 **Clickable breadcrumb navigation**
- ⚡ **Async loading** — GUI stays responsive while fetching
- 🌑 **Refined dark theme** — Catppuccin Mocha-inspired palette with comfortable typography, clear metrics, and styled scrollbars

### Downloading
- ✅ **Inline checkboxes & multi-select** — select multiple games then click **Queue Downloads**
- ⚡ **Double-click** any game to queue it instantly
- 🔄 **Download queue** with configurable concurrency (1–10 simultaneous downloads), tested with 10,000 queued items
- 📋 **Downloads list** — one scalable table (name, size, progress, speed, ETA, peers, status) with **All / Active / Queued / Done / Errors** filters, multi-select, right-click menu, and keyboard control (`Space` pause/resume, `Del` cancel/remove, `R` retry, `Alt+↑/↓` reorder, `Ctrl+A`, `Enter` open folder)
- 🔁 **Automatic retry** — stalled or failed downloads wait out a backoff (30 s, 2 min, 10 min) and retry before being reported as errors; failed items show the real reason and a **Retry** action
- 🔌 **Resumes after a crash or restart** — libtorrent resume data and DHT state are saved periodically and on exit, so partial downloads continue without a full re-check
- 🌱 **Optional seeding** — "Seed finished files" (off by default) keeps finished torrents alive until ratio 1.0 or 24 hours
- ⚙️ **Advanced downloads panel** — save folder, extract/CHD/Xbox toggles, and ROM tools (collapsed by default)
- 📂 **Custom save folder** via the Browse button (defaults to `downloads/` next to the app)
- 💾 **State persistence** — preferences, filters, and active/queued downloads persist across app launches
- 🗂️ **Torrent caching** — each *collection* `.torrent` is fetched once (not once per file), validated, and cached in `torrentfiles/`
- 🚫 **Deduplication** — automatically skips items already pending, active, completed, or being looked up
- 🛡️ **Hardened I/O** — HTTP requests retry with backoff and honour `Retry-After`; settings are written atomically with a `.bak` and a corrupt file is quarantined, never overwritten
- 💽 **Disk-space check** — a download that cannot fit is refused up front; a full disk mid-download pauses it with a clear "Disk full" state
- 📦 **DLC / update matching** — after queueing a game, offers matching DLC and updates from the same folder or related digital/PSN/CDN collections (select, download all, or skip)
- 📊 **Real-time metrics** — speed, ETA, peers, progress, and state for every download

### Extraction, CHD, and Xbox dumps
- 📦 **Auto-extract** — extract archives automatically once download finishes
- 🗜️ **Extractor detection** — auto-detects external extractors (**7-Zip**, **PeaZip**, **WinRAR**) with fallback to Python `zipfile`
- 🎮 **PS1/PS2 BIN/CUE/ISO → CHD** — convert supported disc images to CHD (`chdman`); skips PSP/PS3/GameCube/Wii/Xbox
- 🎮 **Xbox / Xbox 360 ISO unpack** — dump Redump and trimmed XISO/XGD images with **xdvdfs** (falls back to **extract-xiso**) into a folder with `default.xex` / `default.xbe` for a modded 360; skips `$SystemUpdate`
- 📡 **Redump XGD detection** — recognizes original Xbox (XGD1), Xbox 360 XGD2/XGD3, and trimmed XISO by XDVDFS magic at the game-partition start plus sector 32 (including `0xFDA0000` for typical 360 Redump ISOs)
- 📊 **ISO dump progress** — Downloads panel and per-item extract bars track dumped bytes while xdvdfs runs (xdvdfs itself does not print a percent)
- ↩️ **Fix incorrect CHD conversions** — restore PSP/PS3/GC discs that were turned into CHD, or redownload if needed
- 🔍 **Verify downloaded archives** — CRC-test zip/7z/rar files in the save folder and offer redownload on failure
- 🔑 **PS3 disc keys** — auto-queue matching Redump `.dkey` zips into `downloads/dkeys/`, plus a tools action to repair missing keys
- 🛠️ **Unified ROM Tools menu** — grouped utilities for CHD conversion, Xbox ISO unpack, BIN/CUE cleaning, verification, and name standardization
- 🧹 **Automatic name cleaning** — cleans region tags and disc descriptors while preserving disc numbering
- 🗑️ **Optional source deletion** — automatically deletes source archives and BIN/CUE/ISO files post-conversion
- 🚀 **Startup cleanup** — scans the extracted folder on launch to clean names and remove leftover BIN/CUE files next to valid CHDs only

---

## Requirements

- **Windows 10/11** or **Linux** (x86_64)
- **Standalone binary:** No installation required — download from [Releases](../../releases) and run
- **From source:** Python 3.10+ and `libtorrent` (optional, for inline downloads)
- **Linux + Python 3.14:** there is often no PyPI wheel. `build.sh` can compile bindings if you have `g++`, `pkg-config`, **libtorrent-rasterbar**, and **Boost.Python for 3.14** (`libboost_python314`)

Runtime tools are downloaded next to the app when needed (not stored in git):

| Tool | Used for | Location |
| --- | --- | --- |
| **chdman** | PS1/PS2 (and other CHD-eligible) disc compression | `tools/chdman/` |
| **xdvdfs** | Xbox / Xbox 360 ISO dump | `tools/xdvdfs/` |
| **extract-xiso** | Fallback Xbox unpacker if xdvdfs is missing | PATH or `tools/` |

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
.\build.ps1 -SkipTests        # Skip the unit test suite
.\build.ps1 -SkipDeploy       # Do not copy the exe after build
.\build.ps1 -DeployDir "$env:USERPROFILE\Desktop\MiNERVA Browser"  # Copy somewhere else
```

A successful build copies `MiNERVA-Browser.exe` to `OneDrive\Desktop\MiNERVA Browser` by default. Keep `downloads/`, `tools/`, and `torrentfiles/` in that folder if you already use a portable desktop copy.

### Linux (Bash)
```bash
./build.sh
```

Options:
```bash
./build.sh --clean            # Wipe build/, dist/, and .venv/ first
./build.sh --skip-tests       # Skip running the test suite
```

If pip has no `libtorrent` wheel (typical on CPython 3.14), the script runs `scripts/build_libtorrent_py314.sh`. That fetches libtorrent-rasterbar 2.1.1 into `vendor/` (gitignored), builds the Python extension into `.venv`, and `minerva_browser.spec` bundles the `.so` plus system `libtorrent-rasterbar` / `libboost_python314`. Browsing still works if the build fails.

Smoke-test a frozen binary:

```bash
python scripts/smoke_libtorrent_bundle.py dist/MiNERVA-Browser
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

Local settings (`minerva_settings.json`), logs, torrents, downloads, extracted dumps, and auto-installed tools stay next to the working copy (or the portable exe) and are gitignored.

---

## Project Structure

```
├── minerva_browser.py         # Application entry point
├── minerva_browser.spec       # PyInstaller standalone build configuration
├── build.ps1                  # Windows build automation script
├── build.sh                   # Linux build automation script
├── scripts/
│   ├── build_libtorrent_py314.sh    # Source-build python-libtorrent for 3.14
│   └── smoke_libtorrent_bundle.py   # Check a PyInstaller binary loads libtorrent
├── minerva/
│   ├── constants.py           # Paths, theme tokens, trackers, logging, atomic settings I/O
│   ├── core/
│   │   ├── torrent_engine.py  # libtorrent engine: one torrent per collection, many downloads per torrent
│   │   ├── lt_settings.py     # Validated libtorrent session settings + alert mask
│   │   ├── download_queue.py  # Pending/active/retry/done queue (O(1) lookups, backoff, reorder)
│   │   ├── resume_store.py    # Resume data + DHT state persistence
│   │   ├── http.py            # Retrying HTTP client, rate gate, atomic file writes
│   │   ├── torrent_cache.py   # Single-flight, validated collection .torrent cache
│   │   ├── lookup.py          # ROM page → verified torrent source, aggregated errors
│   │   ├── library_index.py   # Background "already downloaded" index
│   │   ├── entries.py         # Per-listing precompute (regions, tags, sizes, match keys)
│   │   ├── download_view.py   # Row formatting for the downloads list (Tk-free)
│   │   ├── settings_writer.py # Background, latest-wins settings writer
│   │   ├── pathsafe.py        # Safe file names for network-supplied names
│   │   ├── sqlite_http.py     # Directory-listing and ROM-page parsers (HTTP)
│   │   ├── extractors.py      # Archives, CHD, Xbox ISO classify/unpack
│   │   ├── companions.py      # DLC / update matching
│   │   └── ps3_dkeys.py       # Redump PS3 disc-key catalog matching
│   └── ui/
│       ├── theme.py           # Catppuccin palette & modern TTK style configurations
│       ├── app.py             # Main Tkinter desktop application window
│       ├── downloads_panel.py # Treeview-based downloads list
│       └── components/
│           ├── filter_bar.py  # Search, region pills, tags, summary, reset
│           ├── companion_dialog.py
│           └── tools_dialog.py# ROM tools menu & utilities modal dialog
└── tests/                     # stdlib unittest; engine tests run a real localhost libtorrent seeder
    ├── test_engine_transfer.py        # shared torrents, cancel/pause, magnets, resume, seeding, stalls
    ├── test_app_engine_integration.py # real window + real engine + real transfer
    ├── test_app_smoke.py              # headless window: scale, debounce, bulk queue, polling
    ├── test_downloads_panel.py        # 5k-row panel budgets, filters, key handling
    ├── test_download_queue*.py        # queue behaviour incl. 10k-item scale tests
    ├── test_http.py / test_torrent_cache.py / test_lookup.py
    ├── test_extractors.py / test_companions.py / test_ps3_dkeys.py / test_parsers.py
    └── …                              # settings, library index, entries, view model, assets
```

---

## How Downloads Work

MiNERVA distributes all files via BitTorrent:

1. Looks up the selected file on its minerva-archive.org `/rom?id=…` page to find its collection torrent and file index (a bounded pool of workers, rate-limited and retried)
2. Fetches the collection `.torrent` once into `torrentfiles/` and verifies that the file index really is the requested file
3. Adds the collection to libtorrent **once** and downloads only the selected files within it; several queued files from one collection share one torrent, and cancelling one never disturbs the others. Files land directly in your save folder (no collection sub-folders)
4. Optionally extracts the file using detected extractors (7-Zip / PeaZip / WinRAR / zipfile)
5. Optionally converts supported disc images to CHD and cleans up input files
6. Optionally unpacks Xbox / Xbox 360 ISOs with xdvdfs (or extract-xiso) into a folder with `default.xex` for a modded console, with a live dump progress bar
7. For Redump PS3 ISOs, queues the matching disc-key zip into `dkeys/` when one exists

Xbox dumps are identified by XDVDFS magic (`MICROSOFT*XBOX*MEDIA`) at known XGD/XISO offsets. Folder names such as `Microsoft - Xbox 360` are used as a hint when magic is missing. Use **ROM Tools → Unpack Xbox ISOs** to dump ISOs that are already in `downloads/extracted/`.
