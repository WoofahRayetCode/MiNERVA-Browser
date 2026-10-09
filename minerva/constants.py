import sys
import os
import pathlib
import threading
import json
import shutil
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime

try:
    import winreg
except ImportError:  # pragma: no cover - Linux/macOS build support
    winreg = None

APP_VERSION = "0.0.0"  # replaced at build time by build.ps1
GITHUB_REPO = "WoofahRayetCode/MiNERVA-Browser"

BASE_URL = "https://minerva-archive.org"
BROWSE_ROOT = "/browse/"
TRACKERS = (
    "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2F9.rarbg.com%3A2810%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.openbittorrent.com%3A6969%2Fannounce"
    "&tr=http%3A%2F%2Ftracker.openbittorrent.com%3A80%2Fannounce"
    "&tr=http%3A%2F%2F95.107.48.115%3A80%2Fannounce"
    "&tr=http%3A%2F%2Fopen.acgnxtracker.com%3A80%2Fannounce"
    "&tr=http%3A%2F%2Ft.acg.rip%3A6699%2Fannounce"
    "&tr=http%3A%2F%2Ft.nyaatracker.com%3A80%2Fannounce"
    "&tr=http%3A%2F%2Ftracker.bt4g.com%3A2095%2Fannounce"
    "&tr=http%3A%2F%2Ftracker.files.fm%3A6969%2Fannounce"
    "&tr=http%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
    "&tr=http%3A%2F%2Fvps02.net.orel.ru%3A80%2Fannounce"
    "&tr=https%3A%2F%2F1337.abcvg.info%3A443%2Fannounce"
    "&tr=https%3A%2F%2Fopentracker.i2p.rocks%3A443%2Fannounce"
    "&tr=https%3A%2F%2Ftracker.nanoha.org%3A443%2Fannounce"
    "&tr=https%3A%2F%2Ftracker.sloppyta.co%3A443%2Fannounce"
    "&tr=udp%3A%2F%2F208.83.20.20%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2F37.235.174.46%3A2710%2Fannounce"
    "&tr=udp%3A%2F%2F75.127.14.224%3A2710%2Fannounce"
    "&tr=udp%3A%2F%2Fexodus.desync.com%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fexplodie.org%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Ffe.dealclub.de%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fipv4.tracker.harry.lu%3A80%2Fannounce"
    "&tr=udp%3A%2F%2Fmovies.zsw.ca%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fopen.demonii.com%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce"
    "&tr=udp%3A%2F%2Fopentracker.i2p.rocks%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fp4p.arenabg.com%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Fpublic.tracker.vraphim.com%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fretracker.lanta-net.ru%3A2710%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.0x.tf%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.dler.org%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.filemail.com%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.moeking.me%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.pomf.se%3A80%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.swateam.org.uk%3A2710%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.tiny-vps.com%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce"
    "&tr=https%3A%2F%2Ftracker1.ctix.cn%3A443%2Fannounce"
    "&tr=https%3A%2F%2Ftracker.loligirl.cn%3A443%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker-udp.gbitt.info%3A80%2Fannounce"
    "&tr=https%3A%2F%2Ftracker.gbitt.info%3A443%2Fannounce"
    "&tr=http%3A%2F%2Ftracker.gbitt.info%3A80%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.therarbg.to%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.therarbg.com%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fopentracker.io%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fnew-line.net%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fmoonburrow.club%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fepider.me%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fbt1.archive.org%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fbt.ktrackers.com%3A6666%2Fannounce"
)


def get_default_trackers() -> list[str]:
    """Return a list of unquoted tracker URLs parsed from the TRACKERS constant."""
    urls = []
    for piece in TRACKERS.split("&tr="):
        piece = piece.strip()
        if piece:
            urls.append(urllib.parse.unquote(piece))
    return urls


BG = "#181825"
PANEL = "#1e1e2e"
PANEL_ALT = "#242438"
ACCENT = "#89b4fa"
ACCENT_HOVER = "#b4befe"
ACCENT_PURPLE = "#cba6f7"
FG = "#cdd6f4"
FG_DIM = "#a6adc8"
SEL_BG = "#313244"
ENTRY_BG = "#1e1e2e"
BORDER = "#313244"
SUCCESS = "#a6e3a1"
WARNING = "#f9e2af"
DANGER = "#f38ba8"

_LOG_LOCK = threading.Lock()


def get_default_download_dir() -> str:
    """Return (and create) the downloads/ folder next to the exe or project root."""
    d = get_runtime_base_dir() / "downloads"
    d.mkdir(exist_ok=True)
    return str(d.resolve())


def get_runtime_base_dir() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).parent.resolve()
    return pathlib.Path(__file__).resolve().parent.parent


def get_torrent_dir() -> pathlib.Path:
    """Return (and create) the torrentfiles/ folder next to the exe / script."""
    d = get_runtime_base_dir() / "torrentfiles"
    d.mkdir(exist_ok=True)
    return d


def get_assets_dir() -> pathlib.Path:
    """Return the assets directory whether running frozen (PyInstaller) or source."""
    if getattr(sys, "_MEIPASS", None):
        p = pathlib.Path(sys._MEIPASS) / "minerva" / "assets"
        if p.exists():
            return p
        p = pathlib.Path(sys._MEIPASS) / "assets"
        if p.exists():
            return p
    return pathlib.Path(__file__).resolve().parent / "assets"


def get_icon_png_path() -> pathlib.Path:
    return get_assets_dir() / "icon.png"


def get_icon_ico_path() -> pathlib.Path:
    return get_assets_dir() / "icon.ico"


def get_error_log_path() -> pathlib.Path:
    override = os.environ.get("MINERVA_ERROR_LOG")
    if override:
        return pathlib.Path(override)
    return get_runtime_base_dir() / "minerva_error.log"


def get_settings_path() -> pathlib.Path:
    return get_runtime_base_dir() / "minerva_settings.json"


MAX_LOG_BYTES = 5 * 1024 * 1024


def _append_log(line: str) -> None:
    """Append to the log, rotating to ``<log>.1`` once it passes ``MAX_LOG_BYTES``."""
    log_path = get_error_log_path()
    with _LOG_LOCK:
        try:
            if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
                os.replace(log_path, log_path.with_name(log_path.name + ".1"))
        except OSError:
            pass  # rotation is best effort; keep logging
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)


def log_error(context: str, exc: Exception | None = None):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"[{ts}] {context}"]
        if exc is not None:
            lines.append(f"Exception: {repr(exc)}")
            lines.append(traceback.format_exc().rstrip())
        _append_log("\n".join(lines) + "\n\n")
    except Exception:
        pass


def log_activity(message: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _append_log(f"[{ts}] {message}\n")
    except Exception:
        pass


_SAVE_LOCK = threading.Lock()
_BAK_REFRESH_SECONDS = 60.0
_last_bak_refresh = float("-inf")  # monotonic() can be tiny right after boot


@dataclass
class SettingsLoadResult:
    """Outcome of loading the settings file.

    ``source`` is ``"primary"``, ``"backup"`` (primary was corrupt, ``.bak`` was used) or
    ``"defaults"`` (nothing usable).  A corrupt primary is never deleted: it is moved to
    ``quarantined`` so a later save cannot destroy the only copy of the user's data.
    """

    data: dict = field(default_factory=dict)
    source: str = "defaults"
    error: str = ""
    quarantined: pathlib.Path | None = None


def _backup_path(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(path.name + ".bak")


def _read_json_object(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Settings file must contain a JSON object")
    return data


def replace_with_retry(src: pathlib.Path, dst: pathlib.Path, attempts: int = 4):
    """``os.replace`` with short retries: on Windows AV/indexers briefly lock the target."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def load_app_settings_ex(path: pathlib.Path | None = None) -> SettingsLoadResult:
    path = path or get_settings_path()
    if not path.exists():
        return SettingsLoadResult()
    try:
        return SettingsLoadResult(data=_read_json_object(path), source="primary")
    except Exception as e:
        log_error(f"load_app_settings failed for {path}", e)
        result = SettingsLoadResult(error=repr(e))
    quarantine = path.with_name(f"{path.stem}.corrupt-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
    try:
        replace_with_retry(path, quarantine)
        result.quarantined = quarantine
        log_activity(f"settings: corrupt file moved to {quarantine}")
    except Exception as e:
        log_error(f"load_app_settings could not quarantine {path}", e)
    bak = _backup_path(path)
    if bak.exists():
        try:
            result.data = _read_json_object(bak)
            result.source = "backup"
            log_activity(f"settings: restored from {bak}")
        except Exception as e:
            log_error(f"load_app_settings backup unreadable: {bak}", e)
    return result


def load_app_settings() -> dict:
    return load_app_settings_ex().data


def save_app_settings(settings: dict, path: pathlib.Path | None = None) -> bool:
    """Atomically write ``settings``; returns False (and logs) on failure.

    Unique temp file + fsync + ``os.replace`` so a crash or power loss leaves either the
    old or the new file intact.  The previous valid file is copied to ``.bak`` at most once
    a minute (and on the first save of a session) so a corrupted primary can be recovered.
    """
    global _last_bak_refresh
    path = path or get_settings_path()
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _SAVE_LOCK:
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            now = time.monotonic()
            if path.exists() and (now - _last_bak_refresh >= _BAK_REFRESH_SECONDS):
                try:
                    _read_json_object(path)  # only back up a file that actually parses
                    shutil.copy2(path, _backup_path(path))
                    _last_bak_refresh = now
                except Exception as e:
                    log_error(f"save_app_settings backup skipped for {path}", e)
            replace_with_retry(tmp_path, path)
            return True
        except Exception as e:
            log_error(f"save_app_settings failed for {path}", e)
            try:
                tmp_path.unlink()
            except OSError:
                pass
            return False
