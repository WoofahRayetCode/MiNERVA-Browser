# MiNERVA Archive Browser

A portable desktop GUI for browsing and downloading from [minerva-archive.org](https://minerva-archive.org/browse/), built with Python + tkinter + libtorrent.

![Dark theme, two-panel layout](https://img.shields.io/badge/platform-Windows-blue) ![Python 3.13](https://img.shields.io/badge/python-3.13-blue) ![libtorrent 2.0](https://img.shields.io/badge/libtorrent-2.0-green)

## Features

### Browsing
- 📁 Two-panel layout — category tree on the left, file listing on the right
- 🔍 Live search/filter with no extra network requests
- 🧭 Clickable breadcrumb navigation
- ⚡ Async loading — GUI stays responsive while fetching
- 🌑 Dark theme

### Downloading
- ✅ Inline checkboxes — tick multiple games then click **Download Checked**
- ⚡ Double-click a game to queue it instantly
- 🔄 Download queue with configurable concurrency (1–10 simultaneous downloads)
- 📂 Choose your save folder via Browse button
- 💾 Download/filter preferences and queued downloads persist between app launches
- 🗂️ Torrent files cached locally in `torrentfiles/` — no redundant fetches
- 🚫 Deduplication — skips games already pending, active, or completed
- 📊 Per-download progress bars with speed, peers, and state

### Extraction
- 📦 **Auto-extract** — automatically extract archives after download completes
- 🗜️ Uses external extractors when detected (**7-Zip**, **PeaZip**, **WinRAR**); falls back to Python `zipfile` for `.zip`
- 🔍 Extractor detection shown in the UI (tool paths displayed when found)
- 📊 Per-download extraction progress bar
- 🗑️ Optional **delete archive** after successful extraction
- ⚙️ **Auto extract**, **delete archive**, and **PS1/PS2 BIN/CUE/ISO → CHD** defaults are configurable in the Downloads panel and persist across launches
- 🧹 CHD conversion now auto-cleans names after conversion and removes the old BIN/CUE/ISO inputs
- ⬇️ If CHD compression is enabled and `chdman` is missing, the app parses the latest MAME release page, downloads the Windows package, extracts only `chdman.exe`, and cleans up the rest
- ✅ Extraction now verifies that ROM content was actually produced in the game output folder
- 🚀 **Startup cleanup** — on every app launch, scans the extracted folder for processed CHD files and automatically cleans file names (removes region tags) and deletes source files (BIN/CUE/ISO)

## Requirements

- Windows 10/11 (64-bit)
- For the portable exe: nothing — just run `MiNERVA-Browser.exe`
- For building from source: Python 3.13 + libtorrent

## Portable exe (recommended)

Download `MiNERVA-Browser.exe` from [Releases](../../releases) and run it — no installation required.

## Building from source

```powershell
.\build.ps1
```

The script will:
1. Detect Python 3.13 (installs via [Scoop](https://scoop.sh) if not found)
2. Create an isolated `.venv`
3. Install PyInstaller + libtorrent
4. Build `dist\MiNERVA-Browser.exe` (~15 MB, fully self-contained)

### Build options

| Flag | Description |
|---|---|
| `-Clean` | Wipe `build/`, `dist/`, `.venv` before building |
| `-SkipPythonCheck` | Skip the Scoop auto-install check |

```powershell
.\build.ps1 -Clean        # recommended for a reproducible build
```

## Running from source

```powershell
pip install libtorrent
python minerva_browser.py
```

> libtorrent is required for downloading. Without it the browser still works but downloads are disabled.

### Generated local files

The app writes a few files next to the executable/script while running, including `torrentfiles/`, `downloads/`, `extracted/`, `minerva_settings.json`, and `minerva_error.log`. These are local runtime artifacts and are intentionally excluded from source control by the repository `.gitignore`.

## How downloads work

MiNERVA distributes all files via BitTorrent. The app:

1. Looks up the selected file in `hashes.db` (fetched via HTTP range requests — no full DB download)
2. Downloads the collection `.torrent` file to `torrentfiles/`
3. Tells libtorrent to download only the selected file within that torrent (`so_id` file priority)
4. Optionally extracts the result with a detected external extractor (7-Zip / PeaZip / WinRAR) after completion
