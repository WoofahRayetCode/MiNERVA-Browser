import bz2
import gzip
import lzma
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import zipfile
from minerva.constants import get_runtime_base_dir, log_activity, log_error

IS_WINDOWS = sys.platform.startswith("win")


def _windows_startupinfo():
    if not IS_WINDOWS:
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return startupinfo


def _windows_creationflags() -> int:
    if not IS_WINDOWS:
        return 0
    return subprocess.CREATE_NO_WINDOW


def _hidden_subprocess_kwargs() -> dict:
    """Hide console windows for child processes on Windows."""
    if not IS_WINDOWS:
        return {}
    return {
        "startupinfo": _windows_startupinfo(),
        "creationflags": _windows_creationflags(),
    }


def find_archive_extractors() -> list[dict]:
    tools: list[dict] = []

    def _add_tool(kind: str, label: str, exe: str | None):
        if not exe:
            return
        p = pathlib.Path(exe)
        if not p.exists():
            return
        if any(t["kind"] == kind and pathlib.Path(t["exe"]).resolve() == p.resolve() for t in tools):
            return
        tools.append({"kind": kind, "label": label, "exe": str(p)})

    for candidate in [
        shutil.which("7z"),
        shutil.which("7z.exe"),
        shutil.which("7za"),
        shutil.which("7za.exe"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]:
        _add_tool("7zip", "7-Zip", candidate)

    for candidate in [
        r"C:\Program Files\PeaZip\res\bin\7z\7z.exe",
        r"C:\Program Files (x86)\PeaZip\res\bin\7z\7z.exe",
        r"C:\Program Files\PeaZip\res\7z\7z.exe",
        r"C:\Program Files (x86)\PeaZip\res\7z\7z.exe",
    ]:
        _add_tool("peazip", "PeaZip", candidate)

    for candidate in [
        shutil.which("winrar"),
        shutil.which("winrar.exe"),
        shutil.which("rar"),
        shutil.which("rar.exe"),
        r"C:\Program Files\WinRAR\WinRAR.exe",
        r"C:\Program Files (x86)\WinRAR\WinRAR.exe",
    ]:
        _add_tool("winrar", "WinRAR", candidate)

    return tools


def format_extractor_status(extractors: list[dict]) -> str:
    if not extractors:
        return "No external extractor found; using built-in Python extraction (ZIP, TAR, GZ, BZ2, XZ)"
    labels = [f"{tool['label']}: {tool['exe']}" for tool in extractors]
    return "Extractors detected: " + " | ".join(labels)


def find_chdman_executable() -> str | None:
    managed_dir = get_runtime_base_dir() / "tools" / "chdman"
    candidates = [
        str(managed_dir / "chdman.exe"),
        str(managed_dir / "chdman"),
        shutil.which("chdman"),
        shutil.which("chdman.exe"),
        str(pathlib.Path.home() / "scoop" / "apps" / "mame" / "current" / "chdman.exe"),
        r"C:\Program Files\MAME\chdman.exe",
        str(pathlib.Path.home() / ".local" / "bin" / "chdman"),
        "/usr/bin/chdman",
        "/usr/local/bin/chdman",
        "/opt/homebrew/bin/chdman",
    ]
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).exists():
            return str(pathlib.Path(candidate))
    return None


XDVDFS_GITHUB_REPO = "antangelo/xdvdfs"
XDVDFS_MAGIC = b"MICROSOFT*XBOX*MEDIA"
# Sector 32 of the game partition, plus known Redump video-partition prefixes.
_XBOX_VOLUME_OFFSETS = (
    (0x10000, "xbox"),       # trimmed XISO (OG or 360)
    (0x2080000, "xbox360"),  # XGD3
    (0xFD90000, "xbox360"),  # XGD2
    (0x18300000, "xbox"),    # XGD1 (original Xbox Redump)
    (0x0, "xbox"),
)
_XBOX_ISO_SUFFIXES = {".iso", ".xiso"}
_XBOX_UNPACK_BLOCKED_HINTS = (
    "xbox one",
    "xbox series",
)
_XBOX_EXEC_NAMES = frozenset({"default.xex", "default.xbe"})


def _first_existing_path(*candidates) -> pathlib.Path | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = pathlib.Path(candidate)
        if path.is_file():
            return path
    return None


def find_xbox_unpack_tool() -> dict | None:
    """Prefer xdvdfs, then extract-xiso. Managed tools/ copies win over PATH."""
    base = get_runtime_base_dir()
    xdvdfs = _first_existing_path(
        base / "tools" / "xdvdfs" / ("xdvdfs.exe" if IS_WINDOWS else "xdvdfs"),
        base / "tools" / "xdvdfs" / "xdvdfs.exe",
        base / "tools" / "xdvdfs" / "xdvdfs",
        shutil.which("xdvdfs"),
        shutil.which("xdvdfs.exe"),
    )
    if xdvdfs is not None:
        return {"kind": "xdvdfs", "label": "xdvdfs", "exe": str(xdvdfs)}
    extract_xiso = _first_existing_path(
        base / "tools" / "extract-xiso" / ("extract-xiso.exe" if IS_WINDOWS else "extract-xiso"),
        base / "tools" / "extract-xiso" / "extract-xiso.exe",
        base / "tools" / "extract-xiso" / "extract-xiso",
        shutil.which("extract-xiso"),
        shutil.which("extract-xiso.exe"),
    )
    if extract_xiso is not None:
        return {"kind": "extract-xiso", "label": "extract-xiso", "exe": str(extract_xiso)}
    return None


def format_xbox_tool_status(tool: dict | None) -> str:
    if not tool:
        return "Xbox unpacker not found (will install xdvdfs when needed)"
    return f"{tool['label']}: {tool['exe']}"


def pick_xdvdfs_release_asset(assets: list[dict], *, windows: bool) -> dict | None:
    """Choose the CLI zip from a GitHub releases/latest payload."""
    needle = "xdvdfs-windows" if windows else "xdvdfs-linux"
    for asset in assets or []:
        name = str(asset.get("name") or "").lower()
        if needle in name and name.endswith(".zip") and "web" not in name:
            return asset
    return None


def xbox_unpack_command(tool: dict, iso: pathlib.Path, out_dir: pathlib.Path) -> list[str]:
    kind = (tool or {}).get("kind")
    exe = (tool or {}).get("exe")
    if not exe or not kind:
        raise ValueError("Xbox unpack tool is missing")
    if kind == "xdvdfs":
        return [str(exe), "unpack", str(iso), str(out_dir)]
    if kind == "extract-xiso":
        return [str(exe), "-x", "-s", str(iso), "-d", str(out_dir)]
    raise ValueError(f"Unsupported Xbox unpack tool: {kind}")


_REGION_TOKENS = {
    "usa", "us", "u", "europe", "euro", "eur", "e", "japan", "jpn", "jp", "j",
    "world", "w", "korea", "kor", "kr", "k", "asia", "australia", "aus", "oceania",
    "germany", "ger", "france", "fr", "italy", "it", "spain", "es", "sweden",
    "netherlands", "nl", "brazil", "bra", "canada", "uk", "gb", "uae", "russia",
    "rus", "poland", "pl", "china", "chn", "taiwan", "tw", "hong kong", "hk",
    "pal", "ntsc", "ntsc-j", "ntsc-u", "ntsc-c",
}
_LANGUAGE_TOKENS = {
    "en", "fr", "de", "es", "it", "pt", "nl", "sv", "no", "da", "fi", "pl", "ru",
    "jp", "ja", "zh", "ko", "cs", "hu", "tr", "el", "ar", "hr", "en-gb", "en-us",
}
_DUMP_STATUS_TOKENS = {
    "!", "b", "f", "h", "t", "a", "p", "o", "u", "unl", "proto", "prototype",
    "beta", "demo", "sample", "alt", "pirate", "overdump", "underdump",
}
_IMPORTANT_EDITION = re.compile(
    r"\b("
    r"special(\s+edition)?"
    r"|limited(\s+edition)?"
    r"|collector'?s?(\s+edition)?"
    r"|deluxe(\s+edition)?"
    r"|complete(\s+edition)?"
    r"|ultimate(\s+edition)?"
    r"|game of the year|goty"
    r"|greatest hits|platinum(\s+hits)?"
    r"|playstation\s+\d*\s*the best|the best"
    r"|anniversary"
    r"|director'?s cut"
    r"|uncut|uncensored"
    r"|remaster(ed)?"
    r"|remix"
    r"|bonus disc"
    r"|bundle"
    r")\b",
    re.IGNORECASE,
)
_DISC_KEEP = re.compile(
    r"^(disc|disk|cd|dvd|bd)\s*\d+[a-z]?(?:\s*(of|/)\s*\d+)?$"
    r"|^side\s*[a-z0-9]+$"
    r"|^track\s*\d+$"
    r"|^part\s*\d+[a-z]?$",
    re.IGNORECASE,
)
_REV_TOKEN = re.compile(r"^(rev|revision|ver|version)\s*[a-z0-9.]+$", re.IGNORECASE)
_VERSION_TOKEN = re.compile(r"^v\d+([._]\d+)*$", re.IGNORECASE)
_SERIAL_TOKEN = re.compile(r"^(slus|sles|scus|sces|slps|slpm|blus|bles|bcus|bces|bljm|bljs|npub|npeb)\s*-?\s*[0-9]+$", re.IGNORECASE)
_DUMP_FLAG = re.compile(r"^[bfhtapo]\d*$", re.IGNORECASE)


def normalize_chd_stem(stem: str) -> str:
    """Strip region/rev/language tags; keep disc numbers and edition descriptors."""
    s = stem.strip()
    removable_parenthetical = re.compile(r"\s*[\(\[]([^\(\)\[\]]+)[\)\]]")

    def _is_important_descriptor(text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        if _DISC_KEEP.match(t) or _SERIAL_TOKEN.match(t):
            return True
        if _IMPORTANT_EDITION.search(t):
            return True
        return False

    def _is_removable_token(text: str) -> bool:
        t = text.strip().lower().replace("_", " ")
        t = re.sub(r"\s+", " ", t)
        if not t:
            return False
        if _is_important_descriptor(t):
            return False
        if t in _REGION_TOKENS:
            return True
        if t in _LANGUAGE_TOKENS:
            return True
        if t in _DUMP_STATUS_TOKENS or _DUMP_FLAG.match(t):
            return True
        if _REV_TOKEN.match(t) or _VERSION_TOKEN.match(t):
            return True
        if re.match(r"^fw\d+(\.\d+)*$", t):
            return True
        return False

    def _is_removable_descriptor(text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        if _is_important_descriptor(t):
            return False
        if _is_removable_token(t):
            return True
        tokens = [tok.strip(" .,_-/") for tok in re.split(r"[,+/&]", t) if tok.strip(" .,_-/")]
        if len(tokens) > 1 and all(_is_removable_token(tok) for tok in tokens):
            return True
        return False

    def _replace(match: re.Match) -> str:
        inside = match.group(1)
        if _is_removable_descriptor(inside):
            return " "
        tokens = [tok.strip(" .,_-/") for tok in re.split(r"[,+/&]", inside) if tok.strip(" .,_-/")]
        if len(tokens) > 1:
            kept = [tok for tok in tokens if not _is_removable_token(tok)]
            if not kept:
                return " "
            if len(kept) < len(tokens):
                return f" ({', '.join(kept)}) "
        return match.group(0)

    last = None
    while last != s:
        last = s
        s = removable_parenthetical.sub(_replace, s)

    s = re.sub(r"\s*-\s*(rev|revision|ver|version)\s*[a-z0-9.]+\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([)\]])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\[\s*\]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -_")
    return s


def clean_chd_names_in_base(
    base: pathlib.Path,
    file_exts: set[str] | None = None,
) -> tuple[int, int, list[str]]:
    file_exts = file_exts or {
        ".chd", ".bin", ".cue", ".iso", ".img", ".mdf", ".mds",
        ".gdi", ".cdi", ".nrg", ".ccd", ".sub", ".toc", ".cso",
        ".zso", ".rvz", ".wbfs", ".wia", ".gcm", ".z64", ".n64",
        ".v64", ".smc", ".sfc", ".nes", ".fds", ".gb", ".gbc",
        ".gba", ".nds", ".3ds", ".cia", ".md", ".gen", ".smd",
        ".pce", ".sgx", ".pbp", ".dkey", ".key", ".zip", ".7z", ".rar",
    }
    renamed = 0
    unchanged = 0
    failed: list[str] = []

    def _try_rename(path: pathlib.Path, new_name: str) -> bool:
        nonlocal renamed, unchanged
        if new_name == path.name:
            unchanged += 1
            return True
        target = path.parent / new_name
        if target.exists():
            failed.append(f"{path.name}: target exists ({new_name})")
            return False
        try:
            path.rename(target)
            renamed += 1
            return True
        except Exception as e:
            failed.append(f"{path.name}: {e}")
            return False

    all_files = sorted(
        (p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in file_exts),
        key=lambda p: (-len(p.parts), p.name),
    )
    for f in all_files:
        new_stem = normalize_chd_stem(f.stem)
        if not new_stem:
            unchanged += 1
            continue
        _try_rename(f, new_stem + f.suffix)

    game_dirs = sorted(
        (p for p in base.rglob("*") if p.is_dir() and p != base),
        key=lambda p: -len(p.parts),
    )
    for d in game_dirs:
        new_name = normalize_chd_stem(d.name)
        if not new_name:
            unchanged += 1
            continue
        _try_rename(d, new_name)
    return renamed, unchanged, failed


def is_likely_rom_file(path: pathlib.Path) -> bool:
    rom_exts = {
        ".cue", ".bin", ".iso", ".chd", ".cso", ".zso", ".pbp", ".img", ".ccd", ".sub",
        ".mdf", ".mds", ".gdi", ".cdi", ".nrg", ".toc", ".zip", ".7z", ".rar", ".tar",
        ".gz", ".bz2", ".xz", ".z64", ".n64", ".v64", ".smc", ".sfc", ".nes", ".fds",
        ".gb", ".gbc", ".gba", ".nds", ".3ds", ".cia", ".xci", ".nsp", ".md", ".gen",
        ".smd", ".32x", ".gg", ".sms", ".sg", ".pce", ".sgx", ".ws", ".wsc", ".ngp",
        ".ngc", ".a26", ".a78", ".a52", ".lnx", ".jag", ".m3u", ".rvz", ".wbfs", ".wia",
        ".gcm", ".dkey", ".key",
        ".xex", ".xbe", ".xiso",
    }
    return path.suffix.lower() in rom_exts


def migrate_app_root_roms(app_root: pathlib.Path, dest: pathlib.Path) -> tuple[int, list[str]]:
    """Move leftover ROM/archive files from the app root into dest (usually downloads/)."""
    moved = 0
    failed: list[str] = []
    try:
        app_root = app_root.resolve()
        dest = dest.resolve()
        dest.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return 0, [str(e)]
    if app_root == dest:
        return 0, []

    extracted_src = app_root / "extracted"
    extracted_dest = dest / "extracted"
    if extracted_src.is_dir() and extracted_src.resolve() != extracted_dest.resolve():
        try:
            if not extracted_dest.exists():
                shutil.move(str(extracted_src), str(extracted_dest))
                moved += 1
                log_activity(f"migrate.extracted '{extracted_src}' -> '{extracted_dest}'")
            else:
                for child in list(extracted_src.iterdir()):
                    target = extracted_dest / child.name
                    if target.exists():
                        continue
                    shutil.move(str(child), str(target))
                    moved += 1
                try:
                    next(extracted_src.iterdir())
                except StopIteration:
                    extracted_src.rmdir()
        except Exception as e:
            failed.append(f"extracted/: {e}")
            log_error("migrate_app_root_roms extracted folder", e)

    for path in list(app_root.iterdir()):
        if path.is_dir():
            continue
        if path.suffix.lower() in {".exe", ".json", ".log", ".py", ".pyc", ".md", ".txt", ".ico", ".png", ".spec"}:
            continue
        if not is_likely_rom_file(path) and not is_archive_path(path):
            continue
        target = dest / path.name
        if target.exists():
            continue
        try:
            shutil.move(str(path), str(target))
            moved += 1
            log_activity(f"migrate.file '{path.name}' -> '{dest}'")
        except Exception as e:
            failed.append(f"{path.name}: {e}")
            log_error(f"migrate_app_root_roms file {path}", e)
    return moved, failed


def verify_extracted_output(out_dir: pathlib.Path, source_name: str):
    if not out_dir.exists() or not out_dir.is_dir():
        raise RuntimeError("Extraction output folder was not created")
    files = [p for p in out_dir.rglob("*") if p.is_file()]
    if not files:
        raise RuntimeError("No files were extracted")
    meaningful: list[pathlib.Path] = []
    metadata_exts = {".txt", ".nfo", ".sfv", ".md5", ".sha1", ".sha256", ".json"}
    for p in files:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        if p.suffix.lower() in metadata_exts:
            continue
        meaningful.append(p)
    if not meaningful:
        raise RuntimeError("Extracted output contains no usable ROM files")
    if not any(is_likely_rom_file(p) for p in meaningful):
        sample = ", ".join(sorted({p.suffix.lower() or "<no-ext>" for p in meaningful[:6]}))
        raise RuntimeError(
            f"Extracted files from {source_name} do not look like ROM content ({sample})"
        )


# Disc systems whose emulators commonly play CHD (DuckStation, PCSX2, Flycast, Beetle, etc.).
_CHD_SYSTEM_HINTS = (
    "sony - playstation 2",
    "playstation 2",
    "ps2",
    "sony - playstation",
    "playstation 1",
    "ps1",
    "psx",
    "sega - saturn",
    "sega saturn",
    "dreamcast",
    "sega cd",
    "sega-cd",
    "mega-cd",
    "mega cd",
    "pc engine cd",
    "pc-engine cd",
    "turbografx-cd",
    "turbografx cd",
    "neo-geo cd",
    "neo geo cd",
    "panasonic 3do",
    "3do interactive",
    "philips cd-i",
    "cd-i",
    "amiga cd32",
    "fm towns",
    "pc-fx",
    "jaguar cd",
)
_CHD_BLOCKED_HINTS = (
    "playstation 3",
    "playstation 4",
    "playstation 5",
    "playstation portable",
    "playstation vita",
    "psp",
    "ps3",
    "ps4",
    "ps5",
    "ps vita",
    "nintendo gamecube",
    "gamecube",
    "nintendo wii u",
    "nintendo wii",
    "wii u",
    "xbox 360",
    "xbox one",
    "xbox series",
    "microsoft - xbox",
    "nintendo switch",
    "nintendo 3ds",
    "nintendo ds",
)
_PS1_SERIAL = re.compile(
    r"\b(SLUS|SLES|SCES|SCUS|SLPS|SLPM|SCPS|SIPS|PAPX|PBPX)\s*-?\s*\d{4,5}\b",
    re.IGNORECASE,
)
_PS2_SERIAL = re.compile(
    r"\b(SLUS|SLES|SCES|SCUS|SLPS|SLPM|SCPS|SCAJ|SLKA|SLAJ|SCKA)\s*-?\s*\d{5}\b",
    re.IGNORECASE,
)
_PSP_SERIAL = re.compile(
    r"\b(ULUS|ULES|ULJS|ULAS|UCUS|UCES|UCJS|NPUH|NPEH|NPJH|NPHH)\s*-?\s*\d{5}\b",
    re.IGNORECASE,
)
_PS3_SERIAL = re.compile(
    r"\b(BLES|BLUS|BLJS|BLJM|BCUS|BCES|BCJS|NPEB|NPUB|NPJB)\s*-?\s*\d{5}\b",
    re.IGNORECASE,
)
# CD dump formats that chdman createcd is meant for; the matching cores play CHD.
_CHD_CD_SUFFIXES = {".cue", ".gdi", ".toc", ".ccd"}
_CHD_DVD_SUFFIXES = {".iso", ".mds", ".mdf"}


def chd_source_mode(path: pathlib.Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in _CHD_CD_SUFFIXES or suffix == ".nrg":
        return "createcd"
    if suffix in _CHD_DVD_SUFFIXES:
        return "createdvd"
    return None


def _norm_hint_text(*parts: object) -> str:
    chunks: list[str] = []
    for part in parts:
        if not part:
            continue
        text = str(part).replace("\\", "/").replace("%20", " ")
        chunks.append(urllib.parse.unquote(text).lower())
    return " ".join(chunks)


def _hint_has_token(text: str, token: str) -> bool:
    if not token:
        return False
    if re.search(rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)", text):
        return True
    return token in text if " " in token or "-" in token else False


def chd_system_from_hints(*parts: object) -> str | None:
    """Return 'blocked', 'supported', or None from folder/file names."""
    text = _norm_hint_text(*parts)
    if not text:
        return None
    # Longer / more specific blocked names first so "playstation 3" wins over "playstation".
    blocked = sorted(_CHD_BLOCKED_HINTS, key=len, reverse=True)
    for token in blocked:
        if _hint_has_token(text, token):
            return "blocked"
    # Avoid treating bare "playstation" as PS1 when a later generation is also present
    # (already handled by blocked). "sony - playstation/" without a number is PS1.
    supported = sorted(_CHD_SYSTEM_HINTS, key=len, reverse=True)
    for token in supported:
        if token in {"ps1", "psx", "ps2"}:
            if _hint_has_token(text, token):
                return "supported"
            continue
        if token == "sony - playstation":
            if "sony - playstation" in text and "sony - playstation 2" not in text:
                # "sony - playstation 3" already returned blocked.
                if re.search(r"sony - playstation(?:\s+[345]| portable| vita)", text):
                    continue
                return "supported"
            continue
        if _hint_has_token(text, token) or token in text:
            return "supported"
    if _PSP_SERIAL.search(text) or _PS3_SERIAL.search(text):
        return "blocked"
    if _PS2_SERIAL.search(text) or _PS1_SERIAL.search(text):
        return "supported"
    return None


def classify_disc_bytes(sample: bytes, suffix: str = "") -> str | None:
    """Identify a disc image from header/volume bytes. None if unknown."""
    if not sample:
        return None
    header = sample[:64]
    if header[0x1C:0x20] == b"\xc2\x33\x9f\x3d":
        return "gamecube"
    if header.startswith(b"WBFS") or b"RVL-" in header[:0x40]:
        return "wii"
    if XDVDFS_MAGIC in sample or header[:4] == b"XBOX":
        return "xbox"
    upper = sample.upper()
    if b"PS3_GAME" in sample or b"PS3_DISC.SFB" in sample or b"PS3_UPDATE" in sample:
        return "ps3"
    if b"PSP_GAME" in sample or b"UMD_DATA.BIN" in sample:
        return "psp"
    if b"SYSTEM.CNF" in upper:
        if b"BOOT2" in upper:
            return "ps2"
        if b"BOOT" in upper:
            return "ps1"
    if suffix.lower() == ".gdi":
        return "dreamcast"
    return None


def classify_disc_image(path: pathlib.Path) -> str | None:
    """Identify a disc image from header/volume data. None if unknown."""
    suffix = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    try:
        with path.open("rb") as handle:
            sample = handle.read(min(size, 2 * 1024 * 1024))
    except OSError:
        return None
    kind = classify_disc_bytes(sample, suffix)
    if kind:
        return kind
    if suffix in _XBOX_ISO_SUFFIXES:
        return classify_xbox_iso(path)
    return None


_CHD_IMAGE_KINDS = {"ps1", "ps2", "dreamcast", "saturn"}
_NOT_CHD_IMAGE_KINDS = {"psp", "ps3", "gamecube", "wii", "xbox"}


def should_convert_to_chd(path: pathlib.Path, context: str = "") -> bool:
    """True only for disc images whose typical emulators play CHD."""
    suffix = path.suffix.lower()
    if chd_source_mode(path) is None:
        return False
    hint = chd_system_from_hints(context, path, path.parent)
    if hint == "blocked":
        log_activity(f"chd.skip reason=blocked_system file='{path.name}'")
        return False
    if suffix in _CHD_CD_SUFFIXES:
        # CUE/GDI/CCD/TOC are the CHD source formats for PS1/Saturn/DC/PCE-CD cores.
        if hint == "blocked":
            return False
        return True
    kind = classify_disc_image(path)
    if kind in _NOT_CHD_IMAGE_KINDS:
        log_activity(f"chd.skip reason=emulator_no_chd kind={kind} file='{path.name}'")
        return False
    if kind in _CHD_IMAGE_KINDS or hint == "supported":
        return True
    log_activity(f"chd.skip reason=unknown_disc_type file='{path.name}'")
    return False


def collect_chd_sources(extracted_dir: pathlib.Path, context: str = "") -> list[pathlib.Path]:
    sources: list[pathlib.Path] = []
    hint = context or str(extracted_dir)
    for ext in ("*.cue", "*.gdi", "*.toc", "*.ccd", "*.nrg", "*.iso", "*.mds"):
        for path in sorted(p for p in extracted_dir.rglob(ext) if p.is_file()):
            if should_convert_to_chd(path, hint):
                sources.append(path)
    return sources


def classify_xbox_iso_from_reads(size: int, read_at) -> str | None:
    """Identify an Xbox/360 image from size + offset reads of XDVDFS magic."""
    magic_len = len(XDVDFS_MAGIC)
    if size <= 0:
        return None
    for offset, kind in _XBOX_VOLUME_OFFSETS:
        if size < offset + magic_len:
            continue
        try:
            chunk = read_at(offset, magic_len)
        except OSError:
            continue
        if chunk == XDVDFS_MAGIC:
            return kind
    return None


def classify_xbox_iso(path: pathlib.Path) -> str | None:
    """Return 'xbox360', 'xbox', or None by seeking known XGD/XISO offsets."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    try:
        with path.open("rb") as handle:
            def read_at(offset: int, n: int) -> bytes:
                handle.seek(offset)
                return handle.read(n)

            return classify_xbox_iso_from_reads(size, read_at)
    except OSError:
        return None


def xbox_system_from_hints(*parts: object) -> str | None:
    """Return 'xbox360' / 'xbox' from folder names, or None if not Xbox (or Xbox One/Series)."""
    text = _norm_hint_text(*parts)
    if not text:
        return None
    for token in _XBOX_UNPACK_BLOCKED_HINTS:
        if _hint_has_token(text, token) or token in text:
            return None
    compact = text.replace(" ", "").replace("-", "")
    if "xbox360" in compact or _hint_has_token(text, "xbox 360"):
        return "xbox360"
    if _hint_has_token(text, "original xbox") or "microsoft - xbox" in text:
        return "xbox360" if "360" in text else "xbox"
    if _hint_has_token(text, "xbox"):
        return "xbox360" if "360" in text else "xbox"
    return None


def should_unpack_xbox_iso(path: pathlib.Path, context: str = "") -> bool:
    suffix = path.suffix.lower()
    if suffix not in _XBOX_ISO_SUFFIXES:
        return False
    hint = xbox_system_from_hints(context, path, path.parent)
    if hint:
        return True
    return classify_xbox_iso(path) is not None


def collect_xbox_iso_sources(extracted_dir: pathlib.Path, context: str = "") -> list[pathlib.Path]:
    if not extracted_dir.exists():
        return []
    hint = context or str(extracted_dir)
    sources: list[pathlib.Path] = []
    for ext in ("*.iso", "*.xiso"):
        for path in sorted(p for p in extracted_dir.rglob(ext) if p.is_file()):
            if should_unpack_xbox_iso(path, hint):
                sources.append(path)
    return sources


def iter_xbox_executables(root: pathlib.Path):
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and path.name.lower() in _XBOX_EXEC_NAMES:
            yield path


def xbox_unpack_succeeded(out_dir: pathlib.Path) -> bool:
    return next(iter_xbox_executables(out_dir), None) is not None


def remove_xbox_system_update(out_dir: pathlib.Path) -> int:
    """Delete $SystemUpdate trees (dashboard updates are unused on a modded 360)."""
    if not out_dir.exists():
        return 0
    removed = 0
    matches = [
        p for p in out_dir.rglob("*")
        if p.is_dir() and p.name.lower() in {"$systemupdate", "systemupdate"}
    ]
    # Deepest first so nested dirs don't trip rmtree of a parent.
    for folder in sorted(matches, key=lambda p: len(p.parts), reverse=True):
        if not folder.exists():
            continue
        try:
            shutil.rmtree(folder)
            removed += 1
            log_activity(f"xbox.cleanup.systemupdate removed='{folder}'")
        except OSError as e:
            log_activity(f"xbox.cleanup.systemupdate failed='{folder}' err={e}")
    return removed


def _hoist_unpacked_xbox_tree(out_dir: pathlib.Path) -> None:
    """If the tool nested default.xex in a single subfolder, lift that tree up."""
    if not out_dir.exists():
        return
    root_has_exec = any(
        p.is_file() and p.name.lower() in _XBOX_EXEC_NAMES for p in out_dir.iterdir()
    )
    if root_has_exec:
        return
    found = list(iter_xbox_executables(out_dir))
    if not found:
        return
    game_dir = found[0].parent
    try:
        if game_dir.resolve() == out_dir.resolve():
            return
    except OSError:
        return
    for child in list(game_dir.iterdir()):
        dest = out_dir / child.name
        if dest.exists():
            continue
        try:
            child.rename(dest)
        except OSError as e:
            log_activity(f"xbox.hoist.fail src='{child}' err={e}")
    try:
        if game_dir.exists() and not any(game_dir.iterdir()):
            game_dir.rmdir()
    except OSError:
        pass


def unpack_xbox_iso(
    iso: pathlib.Path,
    out_dir: pathlib.Path,
    tool: dict | None,
    progress_cb=None,
    delete_iso: bool = True,
) -> bool:
    """Unpack one Xbox/360 ISO into out_dir. Returns True when default.xex/xbe is present."""
    if tool is None:
        raise RuntimeError("No Xbox unpack tool (xdvdfs or extract-xiso) is available")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = xbox_unpack_command(tool, iso, out_dir)
    log_activity(f"xbox.unpack.run cmd={' '.join(cmd)}")
    _report_progress(progress_cb, 0, f"Unpacking {display_filename(iso.name)}…")
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_hidden_subprocess_kwargs(),
    )
    tail = " | ".join((proc.stdout or "").splitlines()[-3:])
    if proc.returncode != 0:
        raise RuntimeError(
            f"Xbox unpack failed for {iso.name} (rc={proc.returncode})"
            + (f" ({tail})" if tail else "")
        )
    _hoist_unpacked_xbox_tree(out_dir)
    remove_xbox_system_update(out_dir)
    if not xbox_unpack_succeeded(out_dir):
        raise RuntimeError(
            f"Xbox unpack produced no default.xex/default.xbe for {iso.name}"
            + (f" ({tail})" if tail else "")
        )
    if delete_iso and iso.exists():
        try:
            iso.unlink()
            log_activity(f"xbox.cleanup.iso removed='{iso}'")
        except OSError as e:
            log_activity(f"xbox.cleanup.iso failed='{iso}' err={e}")
    log_activity(f"xbox.unpack.ok iso='{iso}' dir='{out_dir}' tool={tool.get('kind')}")
    _report_progress(progress_cb, 100, f"Unpacked {display_filename(iso.name)} ✓")
    return True


def unpack_xbox_isos_in_dir(
    extracted_dir: pathlib.Path,
    tool: dict | None,
    progress_cb=None,
    context: str = "",
    delete_iso: bool = True,
) -> int:
    sources = collect_xbox_iso_sources(extracted_dir, context=context)
    if not sources:
        log_activity(f"xbox.skip reason=no_xbox_iso dir='{extracted_dir}'")
        return 0
    if tool is None:
        log_activity("xbox.skip reason=no_tool")
        raise RuntimeError("No Xbox unpack tool (xdvdfs or extract-xiso) is available")
    unpacked = 0
    total = len(sources)
    for idx, iso in enumerate(sources, start=1):
        if progress_cb is not None:
            try:
                progress_cb(idx - 1, total, iso.name)
            except Exception:
                pass
        unpack_xbox_iso(
            iso,
            iso.parent,
            tool,
            progress_cb=None,
            delete_iso=delete_iso,
        )
        unpacked += 1
        if progress_cb is not None:
            try:
                progress_cb(idx, total, iso.name)
            except Exception:
                pass
    return unpacked


def compress_ps1_to_chd(
    extracted_dir: pathlib.Path,
    chdman_path: str | None,
    progress_cb=None,
    context: str = "",
) -> int:
    if not chdman_path:
        log_activity("chd.skip reason=no_chdman")
        return 0
    chd_sources = collect_chd_sources(extracted_dir, context=context)
    if not chd_sources:
        log_activity(f"chd.skip reason=no_chd_eligible_sources dir='{extracted_dir}'")
        return 0

    converted = 0
    total = len(chd_sources)
    cpu_threads = max(1, (os.cpu_count() or 1))
    for idx, source in enumerate(chd_sources, start=1):
        mode = chd_source_mode(source)
        if mode is None:
            continue
        out_chd = source.with_suffix(".chd")
        if progress_cb is not None:
            try:
                progress_cb(idx - 1, total, source.name)
            except Exception:
                pass
        if out_chd.exists():
            if progress_cb is not None:
                try:
                    progress_cb(idx, total, source.name)
                except Exception:
                    pass
            continue
        cmd = [chdman_path, mode, "-np", str(cpu_threads), "-i", str(source), "-o", str(out_chd)]
        log_activity(f"chd.run cmd={' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_hidden_subprocess_kwargs(),
        )
        if proc.returncode != 0:
            tail = " | ".join((proc.stdout or "").splitlines()[-3:])
            raise RuntimeError(
                f"CHD conversion failed for {source.name} (rc={proc.returncode})"
                + (f" ({tail})" if tail else "")
            )
        if not out_chd.exists() or out_chd.stat().st_size <= 0:
            raise RuntimeError(f"CHD output missing for {source.name}")
        log_activity(f"chd.ok source='{source}' chd='{out_chd}'")

        suffix = source.suffix.lower()
        if suffix == ".cue":
            try:
                cue_text = source.read_text(encoding="utf-8", errors="replace")
                referenced_bins = [
                    source.parent / m.group(1)
                    for m in re.finditer(r'^\s*FILE\s+"?([^"]+\.bin)"?\s+BINARY', cue_text, re.IGNORECASE | re.MULTILINE)
                ]
            except Exception:
                referenced_bins = []
            if not referenced_bins:
                folder_name = source.parent.name
                referenced_bins = [
                    p for p in source.parent.iterdir()
                    if p.suffix.lower() == ".bin" and p.stem.lower().startswith(folder_name.lower())
                ]
            if not referenced_bins:
                referenced_bins = [source.with_suffix(".bin")]
            for bin_path in referenced_bins:
                if bin_path.exists():
                    try:
                        bin_path.unlink()
                        log_activity(f"chd.cleanup.bin removed='{bin_path}'")
                    except Exception as e:
                        log_activity(f"chd.cleanup.bin failed='{bin_path}' err='{e}'")
            source.unlink()
            log_activity(f"chd.cleanup.cue removed='{source}'")

        elif suffix == ".gdi":
            referenced_tracks = []
            try:
                gdi_text = source.read_text(encoding="utf-8", errors="replace")
                for line in gdi_text.splitlines():
                    m = re.search(r'["\']([^"\']+)["\']', line)
                    if m:
                        referenced_tracks.append(source.parent / m.group(1))
                    else:
                        parts = line.strip().split()
                        if len(parts) >= 5 and (parts[-1].endswith(".bin") or parts[-1].endswith(".raw") or parts[-1].endswith(".iso")):
                            referenced_tracks.append(source.parent / parts[-1])
            except Exception:
                referenced_tracks = []
            if not referenced_tracks:
                referenced_tracks = [
                    p for p in source.parent.iterdir()
                    if p.suffix.lower() in (".bin", ".raw", ".iso") and p != source
                ]
            for track_path in referenced_tracks:
                if track_path.exists():
                    try:
                        track_path.unlink()
                        log_activity(f"chd.cleanup.gdi_track removed='{track_path}'")
                    except Exception as e:
                        log_activity(f"chd.cleanup.gdi_track failed='{track_path}' err='{e}'")
            source.unlink()
            log_activity(f"chd.cleanup.gdi removed='{source}'")

        elif suffix == ".ccd":
            for companion_ext in (".img", ".sub"):
                companion = source.with_suffix(companion_ext)
                if companion.exists():
                    try:
                        companion.unlink()
                        log_activity(f"chd.cleanup.ccd_companion removed='{companion}'")
                    except Exception as e:
                        log_activity(f"chd.cleanup.ccd_companion failed='{companion}' err='{e}'")
            source.unlink()
            log_activity(f"chd.cleanup.ccd removed='{source}'")

        elif suffix == ".mds":
            mdf = source.with_suffix(".mdf")
            if mdf.exists():
                try:
                    mdf.unlink()
                    log_activity(f"chd.cleanup.mdf removed='{mdf}'")
                except Exception as e:
                    log_activity(f"chd.cleanup.mdf failed='{mdf}' err='{e}'")
            source.unlink()
            log_activity(f"chd.cleanup.mds removed='{source}'")

        else:
            source.unlink()
            log_activity(f"chd.cleanup.source removed='{source}'")

        converted += 1
        if progress_cb is not None:
            try:
                progress_cb(idx, total, source.name)
            except Exception:
                pass
    return converted


_BLOCKED_COMPANION_NAMES = {
    "param.sfo", "eboot.pbp", "umd_data.bin", "ps3_disc.sfb", "ps3_game",
}
_DISC_RESTORE_SUFFIXES = {".iso", ".cue", ".gdi", ".toc", ".ccd", ".mds", ".mdf", ".nrg", ".img"}


def _match_keys_for_name(name: str) -> set[str]:
    stem = pathlib.Path(name).stem
    cleaned = normalize_chd_stem(stem) or stem
    keys = {stem.lower().strip(), cleaned.lower().strip()}
    keys.discard("")
    return keys


def names_refer_to_same_rom(left: str, right: str) -> bool:
    """True when cleaned/original stems match. Does not treat series prefixes as equal."""
    return bool(_match_keys_for_name(left) & _match_keys_for_name(right))


def parse_chdman_info_media(text: str) -> str | None:
    blob = text or ""
    upper = blob.upper()
    if "TAG='CHTR'" in upper or 'TAG="CHTR"' in upper or "TAG='CHT2'" in upper:
        return "cd"
    if "TAG='DVD " in upper or "TAG='DVD'" in upper:
        return "dvd"
    if "TAG='GDDD'" in upper:
        return "hd"
    lower = blob.lower()
    if "createcd" in lower or "cd-rom" in lower:
        return "cd"
    if "createdvd" in lower or "dvd-rom" in lower:
        return "dvd"
    return None


def chd_media_kind(chd_path: pathlib.Path, chdman_path: str | None) -> str | None:
    if not chdman_path:
        return None
    try:
        proc = subprocess.run(
            [chdman_path, "info", "-i", str(chd_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_hidden_subprocess_kwargs(),
        )
    except Exception as e:
        log_error(f"chd.info failed for {chd_path}", e)
        return None
    return parse_chdman_info_media(proc.stdout or "")


def _folder_has_blocked_companion(folder: pathlib.Path) -> bool:
    try:
        children = list(folder.iterdir())
    except OSError:
        return False
    for child in children:
        name = child.name.lower()
        if child.suffix.lower() in {".dkey", ".key"} and child.is_file():
            return True
        if name in _BLOCKED_COMPANION_NAMES:
            return True
        if child.is_dir() and name in {"psp_game", "ps3_game"}:
            return True
    return False


def is_likely_incorrect_chd(path: pathlib.Path, context: str = "") -> bool:
    """True when a .chd almost certainly belongs to a system that does not play CHD."""
    if path.suffix.lower() != ".chd":
        return False
    hint = chd_system_from_hints(context, path, path.parent)
    if hint == "blocked":
        return True
    if hint == "supported":
        return False
    if _folder_has_blocked_companion(path.parent):
        return True
    for ext in (".iso", ".img", ".mdf"):
        companion = path.with_suffix(ext)
        if companion.is_file():
            kind = classify_disc_image(companion)
            if kind in _NOT_CHD_IMAGE_KINDS:
                return True
    return False


def chd_companions_safe_to_delete(chd_path: pathlib.Path, context: str = "") -> bool:
    """True when sibling .cue/.bin/.iso are leftovers from a successful CHD conversion."""
    if chd_path.suffix.lower() != ".chd" or not chd_path.is_file():
        return False
    if is_likely_incorrect_chd(chd_path, context):
        return False
    iso = chd_path.with_suffix(".iso")
    if iso.is_file():
        kind = classify_disc_image(iso)
        if kind in _NOT_CHD_IMAGE_KINDS:
            return False
    return True


def classify_zip_disc(archive_path: pathlib.Path) -> str | None:
    if not zipfile.is_zipfile(archive_path):
        return None
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()
            joined = " ".join(names).lower()
            if "psp_game" in joined or "umd_data.bin" in joined:
                return "psp"
            if "ps3_game" in joined or "ps3_disc.sfb" in joined:
                return "ps3"
            for info in zf.infolist():
                lower = info.filename.replace("\\", "/").lower()
                if not lower.endswith((".iso", ".bin", ".img", ".mdf")):
                    continue
                if info.file_size <= 0:
                    continue
                with zf.open(info) as handle:
                    sample = handle.read(2 * 1024 * 1024)
                kind = classify_disc_bytes(sample, pathlib.Path(lower).suffix)
                if kind:
                    return kind
    except Exception as e:
        log_error(f"classify_zip_disc failed for {archive_path}", e)
    return None


def find_matching_download_source(
    chd_path: pathlib.Path,
    download_dir: pathlib.Path | None,
) -> pathlib.Path | None:
    if not download_dir or not download_dir.is_dir():
        return None
    keys_folder = _match_keys_for_name(chd_path.parent.name)
    keys_file = _match_keys_for_name(chd_path.name)
    skip = {"extracted", "torrentfiles", "tools"}
    try:
        candidates = [
            p for p in download_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (_ARCHIVE_SUFFIXES | _DISC_RESTORE_SUFFIXES)
        ]
    except OSError:
        candidates = []
    try:
        candidates.extend(collect_downloaded_archives(download_dir))
    except Exception:
        pass
    seen: set[pathlib.Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if any(part.lower() in skip for part in candidate.parts):
            continue
        if names_refer_to_same_rom(candidate.name, chd_path.name) or names_refer_to_same_rom(
            candidate.name, chd_path.parent.name
        ):
            return candidate
        ckeys = _match_keys_for_name(candidate.name)
        if ckeys & keys_folder or ckeys & keys_file:
            return candidate
    return None


def _unlink_quietly(path: pathlib.Path) -> None:
    try:
        if path.exists():
            path.unlink()
            log_activity(f"chd.repair.removed '{path}'")
    except OSError as e:
        log_activity(f"chd.repair.remove_failed '{path}' err={e}")


def _run_chdman_extract(chdman_path: str, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [chdman_path, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_hidden_subprocess_kwargs(),
    )
    return proc.returncode, proc.stdout or ""


def extract_chd_to_original(
    chd_path: pathlib.Path,
    chdman_path: str | None,
) -> pathlib.Path | None:
    """Decompress a CHD back to CUE/BIN or ISO. Returns the primary restored file."""
    if not chdman_path:
        return None
    media = chd_media_kind(chd_path, chdman_path)
    attempts: list[tuple[str, pathlib.Path, list[str]]] = []
    iso_out = chd_path.with_suffix(".iso")
    cue_out = chd_path.with_suffix(".cue")
    if media == "cd":
        attempts.append(("extractcd", cue_out, ["extractcd", "-f", "-i", str(chd_path), "-o", str(cue_out)]))
        attempts.append(("extractdvd", iso_out, ["extractdvd", "-f", "-i", str(chd_path), "-o", str(iso_out)]))
    else:
        attempts.append(("extractdvd", iso_out, ["extractdvd", "-f", "-i", str(chd_path), "-o", str(iso_out)]))
        attempts.append(("extractcd", cue_out, ["extractcd", "-f", "-i", str(chd_path), "-o", str(cue_out)]))
    for label, dest, args in attempts:
        try:
            rc, out = _run_chdman_extract(chdman_path, args)
            if rc != 0 and "-f" in args:
                rc, out = _run_chdman_extract(chdman_path, [a for a in args if a != "-f"])
        except Exception as e:
            log_error(f"chd.repair.{label} error file='{chd_path}'", e)
            continue
        if rc == 0 and dest.exists() and dest.stat().st_size > 0:
            log_activity(f"chd.repair.{label}.ok src='{chd_path}' dest='{dest}'")
            return dest
        log_activity(f"chd.repair.{label}.fail rc={rc} file='{chd_path.name}' tail='{out[-240:]}'")
    return None


def collect_incorrect_chds(
    root: pathlib.Path,
    context: str = "",
    download_dir: pathlib.Path | None = None,
) -> list[pathlib.Path]:
    if not root.is_dir():
        return []
    search_dir = download_dir
    if search_dir is None:
        search_dir = root.parent if root.name.lower() == "extracted" else root
    found: list[pathlib.Path] = []
    for path in sorted(root.rglob("*.chd")):
        if not path.is_file():
            continue
        extra = context
        source = find_matching_download_source(path, search_dir)
        archive_kind = None
        if source is not None and is_archive_path(source):
            archive_kind = classify_zip_disc(source)
            extra = f"{context} {source} {archive_kind or ''}"
        if archive_kind in _NOT_CHD_IMAGE_KINDS or is_likely_incorrect_chd(path, extra):
            found.append(path)
    return found


def repair_incorrect_chd(
    chd_path: pathlib.Path,
    *,
    chdman_path: str | None = None,
    download_dir: pathlib.Path | None = None,
    extractors: list[dict] | None = None,
    progress_cb=None,
) -> dict:
    """Restore a CHD that should not exist. Returns a result dict."""
    result = {
        "path": chd_path,
        "action": "error",
        "restored": None,
        "reason": "",
        "redownload_name": None,
    }

    def _report(pct: int, msg: str):
        _report_progress(progress_cb, pct, msg)

    _report(0, f"Checking {display_filename(chd_path.name)}…")
    source = find_matching_download_source(chd_path, download_dir)
    if source is not None:
        result["redownload_name"] = source.name
        if is_archive_path(source):
            kind = classify_zip_disc(source)
            if kind in _CHD_IMAGE_KINDS:
                result["action"] = "kept"
                result["reason"] = f"matching archive is {kind}; CHD is correct"
                return result
            _report(20, f"Re-extracting {display_filename(source.name)}…")
            out_dir = chd_path.parent
            try:
                extract_archive(source, out_dir, extractors=extractors or [], progress_cb=progress_cb)
            except Exception as e:
                log_error(f"chd.repair.extract_archive failed src='{source}'", e)
            else:
                restored = None
                for ext in (".iso", ".cue", ".gdi", ".img"):
                    candidate = out_dir / (chd_path.stem + ext)
                    if candidate.is_file() and candidate.stat().st_size > 0:
                        restored = candidate
                        break
                if restored is None:
                    discs = [
                        p for p in out_dir.rglob("*")
                        if p.is_file() and p.suffix.lower() in _DISC_RESTORE_SUFFIXES
                    ]
                    if discs:
                        restored = max(discs, key=lambda p: p.stat().st_size)
                if restored is not None:
                    kind = classify_disc_image(restored) or kind
                    if kind in _CHD_IMAGE_KINDS:
                        result["action"] = "kept"
                        result["reason"] = f"archive restored {kind}; keeping CHD"
                        return result
                    _unlink_quietly(chd_path)
                    result["action"] = "reversed"
                    result["restored"] = restored
                    result["reason"] = f"restored from archive as {restored.suffix}"
                    _report(100, f"Restored {display_filename(restored.name)}")
                    return result
        elif source.suffix.lower() in _DISC_RESTORE_SUFFIXES:
            kind = classify_disc_image(source)
            if kind in _NOT_CHD_IMAGE_KINDS or kind is None:
                dest = chd_path.parent / source.name
                try:
                    if dest.resolve() != source.resolve():
                        shutil.copy2(source, dest)
                    _unlink_quietly(chd_path)
                    result["action"] = "reversed"
                    result["restored"] = dest
                    result["reason"] = f"copied original {source.suffix}"
                    return result
                except Exception as e:
                    log_error(f"chd.repair.copy failed src='{source}'", e)

    companion_iso = chd_path.with_suffix(".iso")
    if companion_iso.is_file():
        kind = classify_disc_image(companion_iso)
        if kind in _NOT_CHD_IMAGE_KINDS:
            _unlink_quietly(chd_path)
            result["action"] = "reversed"
            result["restored"] = companion_iso
            result["reason"] = f"removed CHD beside original {kind} ISO"
            return result

    _report(40, f"Decompressing {display_filename(chd_path.name)} with chdman…")
    restored = extract_chd_to_original(chd_path, chdman_path)
    if restored is not None:
        kind = classify_disc_image(restored)
        if kind in _CHD_IMAGE_KINDS:
            _unlink_quietly(restored)
            bin_side = restored.with_suffix(".bin")
            if bin_side.exists():
                _unlink_quietly(bin_side)
            result["action"] = "kept"
            result["reason"] = f"chdman extract looks like {kind}; CHD is correct"
            return result
        _unlink_quietly(chd_path)
        result["action"] = "reversed"
        result["restored"] = restored
        result["reason"] = f"reversed CHD to {restored.suffix}" + (f" ({kind})" if kind else "")
        _report(100, f"Reversed to {display_filename(restored.name)}")
        return result

    result["action"] = "needs_redownload"
    result["reason"] = "could not reverse CHD; redownload required"
    if result["redownload_name"] is None:
        result["redownload_name"] = chd_path.name
    _report(100, f"Need redownload: {display_filename(chd_path.name)}")
    return result


def repair_incorrect_chds(
    root: pathlib.Path,
    *,
    chdman_path: str | None = None,
    download_dir: pathlib.Path | None = None,
    extractors: list[dict] | None = None,
    progress_cb=None,
) -> list[dict]:
    chds = collect_incorrect_chds(root, context=str(root), download_dir=download_dir)
    results: list[dict] = []
    total = max(1, len(chds))
    for i, chd in enumerate(chds, start=1):
        _report_progress(
            progress_cb,
            int((i - 1) * 100 / total),
            f"Repair {i}/{len(chds)}: {display_filename(chd.name)}",
        )
        results.append(
            repair_incorrect_chd(
                chd,
                chdman_path=chdman_path,
                download_dir=download_dir,
                extractors=extractors,
                progress_cb=progress_cb,
            )
        )
    return results


class ArchiveVerificationError(RuntimeError):
    """Raised when a downloaded archive is missing, truncated, or fails CRC tests."""


_ARCHIVE_SUFFIXES = {
    ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".lzma",
    ".tgz", ".tbz2", ".txz",
}
_SEVEN_Z_MAGIC = b"7z\xbc\xaf'\x1c"
_RAR_MAGIC = b"Rar!\x1a"


def is_archive_path(path: pathlib.Path) -> bool:
    name = path.name.lower()
    if name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return True
    return path.suffix.lower() in _ARCHIVE_SUFFIXES


_SKIP_ARCHIVE_SCAN_DIRS = {"extracted", "torrentfiles", "tools", "build", "dist", "dkeys"}


def collect_downloaded_archives(
    root: pathlib.Path,
    exclude_names: set[str] | None = None,
) -> list[pathlib.Path]:
    """Find archives in the download folder, skipping extract/cache subfolders."""
    if not root.exists() or not root.is_dir():
        return []
    skip = {n.lower() for n in (exclude_names or set()) if n}
    found: list[pathlib.Path] = []
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_file() or not is_archive_path(path):
            continue
        if path.name.lower() in skip:
            continue
        try:
            rel_parts = path.relative_to(root).parts[:-1]
        except ValueError:
            continue
        if any(part.lower() in _SKIP_ARCHIVE_SCAN_DIRS for part in rel_parts):
            continue
        found.append(path)
    return sorted(found, key=lambda p: p.name.lower())


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def display_filename(name: str, max_len: int = 40) -> str:
    leaf = pathlib.Path(str(name).replace("\\", "/")).name or str(name)
    if len(leaf) <= max_len:
        return leaf
    keep = max_len - 3
    head = keep // 2
    tail = keep - head
    return f"{leaf[:head]}...{leaf[-tail:]}"


def _report_progress(progress_cb, pct: int, msg: str):
    if progress_cb:
        try:
            progress_cb(pct, msg)
        except Exception:
            pass


def _archive_member_is_safe(out_dir: pathlib.Path, member_name: str) -> bool:
    """Reject absolute paths and zip/tar slip (`../`) members."""
    rel = (member_name or "").replace("\\", "/")
    if not rel or rel.startswith("/") or (len(rel) >= 2 and rel[1] == ":"):
        return False
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return False
    dest = out_dir.joinpath(*parts)
    try:
        dest.resolve().relative_to(out_dir.resolve())
    except (ValueError, OSError):
        return False
    return True


def _parse_extractor_progress_line(line: str) -> tuple[int | None, str | None]:
    """Best-effort parse of 7-Zip / WinRAR stdout for percent and current file."""
    text = line.strip()
    if not text:
        return None, None
    pct = None
    m = re.search(r"(\d{1,3})\s*%", text)
    if m:
        pct = min(100, int(m.group(1)))
    name = None
    extract_m = re.search(
        r"(?:Extracting|Testing|Compressing|Skipping)\s+(?:archive\s+)?(.+)$",
        text,
        re.IGNORECASE,
    )
    if extract_m:
        candidate = extract_m.group(1).strip().strip("\"'")
        candidate = re.sub(r"\s+\d{1,3}%\s*$", "", candidate).strip()
        if candidate and candidate not in {"-", "..."}:
            name = display_filename(candidate)
    elif pct is not None:
        remainder = re.sub(r"^\d{1,3}\s*%\s*", "", text).strip(" -")
        remainder = re.sub(r"^\d+\s+", "", remainder).strip()
        if remainder and not remainder.isdigit() and "%" not in remainder:
            name = display_filename(remainder)
    return pct, name


def _decompress_stream_ok(opener, src_path: pathlib.Path) -> None:
    with opener(src_path, "rb") as f_in:
        while True:
            chunk = f_in.read(1024 * 1024)
            if not chunk:
                break


def _test_with_external_extractor(src_path: pathlib.Path, extractors: list[dict], progress_cb) -> bool:
    archive = display_filename(src_path.name)
    for tool in extractors:
        kind = tool.get("kind")
        exe = tool.get("exe")
        if not exe:
            continue
        label = tool.get("label") or kind or "extractor"
        if kind in ("7zip", "peazip"):
            cmd = [exe, "t", "-y", "-bd", "-bso1", "-bsp1", str(src_path)]
        elif kind == "winrar":
            cmd = [exe, "t", "-y", str(src_path)]
        else:
            continue
        log_activity(f"archive.test.tool.run tool={label} file='{src_path.name}'")
        _report_progress(progress_cb, 0, f"Testing with {label}: {archive}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_hidden_subprocess_kwargs(),
            )
            last_pct = -1
            current_file = ""
            if proc.stdout is not None:
                for line in proc.stdout:
                    pct, name = _parse_extractor_progress_line(line)
                    if name:
                        current_file = name
                    if pct is not None and pct != last_pct:
                        last_pct = pct
                        detail = f" — {current_file}" if current_file else ""
                        _report_progress(
                            progress_cb,
                            min(99, pct),
                            f"Testing with {label}: {archive}{detail} ({pct}%)",
                        )
            rc = proc.wait()
            if rc == 0:
                log_activity(f"archive.test.tool.ok tool={label} file='{src_path.name}'")
                return True
            log_activity(f"archive.test.tool.fail tool={label} rc={rc} file='{src_path.name}'")
            _report_progress(progress_cb, 0, f"{label} could not verify {archive}")
        except Exception as e:
            log_error(f"archive.test.tool.error tool={label} file='{src_path}'", e)
    return False


def verify_archive(
    src_path: pathlib.Path,
    extractors: list[dict] | None = None,
    progress_cb=None,
) -> str:
    """CRC/integrity-test an archive. Returns the detected kind. Raises ArchiveVerificationError."""
    extractors = extractors or []
    if not src_path.exists() or not src_path.is_file():
        raise ArchiveVerificationError(f"Downloaded file is missing: {src_path.name}")
    try:
        size = src_path.stat().st_size
    except OSError as e:
        raise ArchiveVerificationError(f"Cannot read {src_path.name}: {e}") from e
    if size <= 0:
        raise ArchiveVerificationError(f"{src_path.name} is empty (0 bytes)")

    suffix = src_path.suffix.lower()
    full_name = src_path.name.lower()
    archive = display_filename(src_path.name)
    _report_progress(
        progress_cb, 0, f"Verifying {archive} ({format_bytes(size)})…"
    )

    if suffix in (".7z", ".rar"):
        _report_progress(progress_cb, 1, f"Checking {suffix.lstrip('.')} header: {archive}")
        header = src_path.read_bytes()[:8] if size >= 8 else src_path.read_bytes()
        if suffix == ".7z" and not header.startswith(_SEVEN_Z_MAGIC):
            raise ArchiveVerificationError(f"{src_path.name} is not a valid 7z archive (bad header)")
        if suffix == ".rar" and not (header.startswith(_RAR_MAGIC) or header.startswith(b"Rar!")):
            raise ArchiveVerificationError(f"{src_path.name} is not a valid RAR archive (bad header)")
        if not _test_with_external_extractor(src_path, extractors, progress_cb):
            if not extractors:
                raise ArchiveVerificationError(
                    f"Cannot test or extract {src_path.name} without 7-Zip, PeaZip, or WinRAR"
                )
            raise ArchiveVerificationError(f"{src_path.name} failed integrity test (truncated or corrupt)")
        _report_progress(progress_cb, 100, f"Verified {archive} ✓")
        return suffix.lstrip(".")

    if suffix == ".zip" or zipfile.is_zipfile(src_path):
        try:
            with zipfile.ZipFile(src_path, "r") as zf:
                members = [info for info in zf.infolist() if not info.is_dir()]
                if not members:
                    raise ArchiveVerificationError(f"{src_path.name} is an empty ZIP")
                sample = ", ".join(display_filename(info.filename, 24) for info in members[:3])
                extra = f" +{len(members) - 3} more" if len(members) > 3 else ""
                _report_progress(
                    progress_cb,
                    5,
                    f"ZIP CRC check: {len(members)} file(s) — {sample}{extra}",
                )
                bad = zf.testzip()
                if bad is not None:
                    raise ArchiveVerificationError(f"{src_path.name} CRC check failed ({bad})")
        except ArchiveVerificationError:
            raise
        except zipfile.BadZipFile as e:
            raise ArchiveVerificationError(f"{src_path.name} is not a valid ZIP: {e}") from e
        except Exception as e:
            raise ArchiveVerificationError(f"{src_path.name} failed ZIP verification: {e}") from e
        _report_progress(progress_cb, 100, f"Verified {archive} ✓")
        return "zip"

    if (
        suffix in (".tar", ".tgz", ".tbz2", ".txz")
        or full_name.endswith((".tar.gz", ".tar.bz2", ".tar.xz"))
        or tarfile.is_tarfile(src_path)
    ):
        try:
            with tarfile.open(src_path, "r:*") as tf:
                members = [m for m in tf.getmembers() if m.isfile()]
                if not members:
                    raise ArchiveVerificationError(f"{src_path.name} is an empty TAR archive")
                total = max(1, len(members))
                _report_progress(
                    progress_cb, 2, f"TAR integrity: {len(members)} file(s) in {archive}"
                )
                for i, member in enumerate(members, start=1):
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    with extracted:
                        while extracted.read(1024 * 1024):
                            pass
                    _report_progress(
                        progress_cb,
                        int(i * 99 / total),
                        f"Verifying TAR {i}/{total}: {display_filename(member.name)}",
                    )
        except ArchiveVerificationError:
            raise
        except Exception as e:
            raise ArchiveVerificationError(f"{src_path.name} failed TAR verification: {e}") from e
        _report_progress(progress_cb, 100, f"Verified {archive} ✓")
        return "tar"

    if suffix == ".gz" and not full_name.endswith(".tar.gz"):
        _report_progress(progress_cb, 5, f"Decompress-testing GZIP: {archive}")
        try:
            _decompress_stream_ok(gzip.open, src_path)
        except Exception as e:
            raise ArchiveVerificationError(f"{src_path.name} failed GZIP verification: {e}") from e
        _report_progress(progress_cb, 100, f"Verified {archive} ✓")
        return "gz"

    if suffix == ".bz2" and not full_name.endswith(".tar.bz2"):
        _report_progress(progress_cb, 5, f"Decompress-testing BZIP2: {archive}")
        try:
            _decompress_stream_ok(bz2.open, src_path)
        except Exception as e:
            raise ArchiveVerificationError(f"{src_path.name} failed BZIP2 verification: {e}") from e
        _report_progress(progress_cb, 100, f"Verified {archive} ✓")
        return "bz2"

    if suffix in (".xz", ".lzma") and not full_name.endswith(".tar.xz"):
        _report_progress(progress_cb, 5, f"Decompress-testing XZ: {archive}")
        try:
            _decompress_stream_ok(lzma.open, src_path)
        except Exception as e:
            raise ArchiveVerificationError(f"{src_path.name} failed XZ/LZMA verification: {e}") from e
        _report_progress(progress_cb, 100, f"Verified {archive} ✓")
        return suffix.lstrip(".")

    if is_archive_path(src_path):
        if not _test_with_external_extractor(src_path, extractors, progress_cb):
            raise ArchiveVerificationError(f"{src_path.name} could not be verified as a readable archive")
        _report_progress(progress_cb, 100, f"Verified {archive} ✓")
        return "archive"

    raise ArchiveVerificationError(f"{src_path.name} is not a recognized archive")


def extract_archive(
    src_path: pathlib.Path,
    out_dir: pathlib.Path,
    extractors: list[dict] | None = None,
    progress_cb=None,
) -> bool:
    """Extract an archive file to out_dir with support for 7z/RAR/ZIP/TAR/GZ/BZ2/XZ and fallbacks."""
    extractors = extractors or []
    suffix = src_path.suffix.lower()
    full_name = src_path.name.lower()
    archive = display_filename(src_path.name)
    try:
        src_size = src_path.stat().st_size
    except OSError:
        src_size = 0

    def _report(pct: int, msg: str):
        _report_progress(progress_cb, pct, msg)

    if suffix in (".7z", ".rar"):
        try:
            header = src_path.read_bytes()[:8] if src_size >= 8 else src_path.read_bytes()
        except OSError as e:
            raise ArchiveVerificationError(f"Cannot read {src_path.name}: {e}") from e
        if suffix == ".7z" and not header.startswith(_SEVEN_Z_MAGIC):
            raise ArchiveVerificationError(f"{src_path.name} is not a valid 7z archive (bad header)")
        if suffix == ".rar" and not (header.startswith(_RAR_MAGIC) or header.startswith(b"Rar!")):
            raise ArchiveVerificationError(f"{src_path.name} is not a valid RAR archive (bad header)")

    out_dir.mkdir(parents=True, exist_ok=True)
    _report(0, f"Extracting {archive} ({format_bytes(src_size)})…")

    # 1. Try external tools (7-Zip, PeaZip, WinRAR) first if available
    for tool in extractors:
        label = tool.get("label") or tool.get("kind") or "extractor"
        if tool["kind"] in ("7zip", "peazip"):
            cmd = [tool["exe"], "x", "-y", "-aoa", "-bd", "-bso1", "-bsp1", f"-o{out_dir}", str(src_path)]
        elif tool["kind"] == "winrar":
            cmd = [tool["exe"], "x", "-y", "-o+", str(src_path), str(out_dir) + os.sep]
        else:
            continue

        log_activity(f"extract.tool.run tool={label} cmd={' '.join(cmd)}")
        _report(0, f"Extracting with {label}: {archive}")
        for attempt in range(1, 3):
            if attempt > 1:
                _report(0, f"{label} retry {attempt}/2: {archive}")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    **_hidden_subprocess_kwargs(),
                )
                last_pct = -1
                current_file = ""
                if proc.stdout is not None:
                    for line in proc.stdout:
                        pct, name = _parse_extractor_progress_line(line)
                        if name:
                            current_file = name
                        if pct is not None and pct != last_pct:
                            last_pct = pct
                            detail = f" → {current_file}" if current_file else ""
                            _report(pct, f"{label}: {archive}{detail} ({pct}%)")
                rc = proc.wait()
                if rc == 0:
                    log_activity(f"extract.tool.ok tool={label}")
                    _report(100, f"Extracted with {label}: {archive} ✓")
                    return True
                _report(0, f"{label} failed (exit {rc}), trying next tool…")
            except Exception as e:
                log_error(f"extract.tool.error tool={label}", e)
                _report(0, f"{label} error: {str(e)[:48]}")
            if attempt < 2:
                time.sleep(1)

    # 2. Python Built-in ZIP Extraction
    if suffix == ".zip" or zipfile.is_zipfile(src_path):
        _report(0, f"Extracting ZIP: {archive}")
        try:
            with zipfile.ZipFile(src_path, "r") as zf:
                members = zf.infolist()
                total = max(1, len(members))
                _report(1, f"ZIP contains {len(members)} item(s)")
                for i, member in enumerate(members, start=1):
                    if not _archive_member_is_safe(out_dir, member.filename):
                        log_activity(f"extract.zip.skip_unsafe member='{member.filename}'")
                        continue
                    zf.extract(member, out_dir)
                    pct = int(i * 100 / total)
                    kind = "folder" if member.is_dir() else format_bytes(member.file_size)
                    _report(
                        pct,
                        f"ZIP {i}/{total}: {display_filename(member.filename)} ({kind})",
                    )
            log_activity(f"extract.zip.ok src='{src_path}'")
            _report(100, f"Extracted ZIP: {archive} ✓")
            return True
        except Exception as e:
            log_error(f"extract.zip.fail src='{src_path}'", e)
            if suffix == ".zip":
                raise ArchiveVerificationError(f"{src_path.name} failed ZIP extraction: {e}") from e

    # 3. Python Built-in TAR Extraction (.tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz)
    if (
        suffix in (".tar", ".tgz", ".tbz2", ".txz")
        or full_name.endswith(".tar.gz")
        or full_name.endswith(".tar.bz2")
        or full_name.endswith(".tar.xz")
        or tarfile.is_tarfile(src_path)
    ):
        _report(0, f"Extracting TAR: {archive}")
        try:
            with tarfile.open(src_path, "r:*") as tf:
                members = tf.getmembers()
                total = max(1, len(members))
                _report(1, f"TAR contains {len(members)} item(s)")
                for i, member in enumerate(members, start=1):
                    if not _archive_member_is_safe(out_dir, member.name):
                        continue
                    tf.extract(member, out_dir)
                    pct = int(i * 100 / total)
                    kind = "folder" if member.isdir() else format_bytes(member.size)
                    _report(
                        pct,
                        f"TAR {i}/{total}: {display_filename(member.name)} ({kind})",
                    )
            log_activity(f"extract.tar.ok src='{src_path}'")
            _report(100, f"Extracted TAR: {archive} ✓")
            return True
        except Exception as e:
            log_error(f"extract.tar.fail src='{src_path}'", e)

    # 4. Python Built-in GZIP Extraction (single file .gz, e.g. game.nes.gz -> game.nes)
    if suffix == ".gz" and not full_name.endswith(".tar.gz"):
        dest_filename = src_path.stem if src_path.suffix.lower() == ".gz" else f"{src_path.stem}.bin"
        dest_file = out_dir / dest_filename
        _report(0, f"Decompressing GZIP: {archive} → {display_filename(dest_filename)}")
        try:
            with gzip.open(src_path, "rb") as f_in, open(dest_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            log_activity(f"extract.gzip.ok src='{src_path}' dest='{dest_file}'")
            _report(100, f"Decompressed GZIP: {display_filename(dest_filename)} ✓")
            return True
        except Exception as e:
            log_error(f"extract.gzip.fail src='{src_path}'", e)

    # 5. Python Built-in BZIP2 Extraction (single file .bz2, e.g. game.sfc.bz2 -> game.sfc)
    if suffix == ".bz2" and not full_name.endswith(".tar.bz2"):
        dest_filename = src_path.stem if src_path.suffix.lower() == ".bz2" else f"{src_path.stem}.bin"
        dest_file = out_dir / dest_filename
        _report(0, f"Decompressing BZIP2: {archive} → {display_filename(dest_filename)}")
        try:
            with bz2.open(src_path, "rb") as f_in, open(dest_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            log_activity(f"extract.bz2.ok src='{src_path}' dest='{dest_file}'")
            _report(100, f"Decompressed BZIP2: {display_filename(dest_filename)} ✓")
            return True
        except Exception as e:
            log_error(f"extract.bz2.fail src='{src_path}'", e)

    # 6. Python Built-in LZMA/XZ Extraction (single file .xz or .lzma, e.g. game.z64.xz -> game.z64)
    if suffix in (".xz", ".lzma") and not full_name.endswith(".tar.xz"):
        dest_filename = src_path.stem if suffix in (".xz", ".lzma") else f"{src_path.stem}.bin"
        dest_file = out_dir / dest_filename
        _report(0, f"Decompressing XZ: {archive} → {display_filename(dest_filename)}")
        try:
            with lzma.open(src_path, "rb") as f_in, open(dest_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            log_activity(f"extract.lzma.ok src='{src_path}' dest='{dest_file}'")
            _report(100, f"Decompressed XZ: {display_filename(dest_filename)} ✓")
            return True
        except Exception as e:
            log_error(f"extract.lzma.fail src='{src_path}'", e)

    if is_archive_path(src_path):
        raise ArchiveVerificationError(f"Could not extract {src_path.name}")

    # 7. Passthrough (raw uncompressed ROM or uncompressed disc image)
    dest_file = out_dir / src_path.name
    _report(0, f"Copying ROM {archive} ({format_bytes(src_size)})…")
    try:
        shutil.copy2(src_path, dest_file)
        log_activity(f"extract.passthrough.ok src='{src_path}' dest='{dest_file}'")
        _report(100, f"Copied ROM: {archive} ✓")
        return True
    except Exception as e:
        log_error(f"extract.passthrough.fail src='{src_path}'", e)
        raise RuntimeError(f"Could not extract or process downloaded file: {e}")
