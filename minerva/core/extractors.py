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
import zipfile
from minerva.constants import get_runtime_base_dir, log_activity, log_error

IS_WINDOWS = sys.platform.startswith("win")


def _windows_startupinfo():
    if not IS_WINDOWS:
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 6  # SW_MINIMIZE
    return startupinfo


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


def normalize_chd_stem(stem: str) -> str:
    s = stem.strip()
    removable_parenthetical = re.compile(r"\s*[\(\[]([^\(\)\[\]]+)[\)\]]")

    def _is_important_descriptor(text: str) -> bool:
        t = text.strip().lower()
        return bool(
            re.match(r"^disc\s*\d+[a-z]?$", t)
            or re.match(r"^disk\s*\d+[a-z]?$", t)
            or re.match(r"^side\s*[a-z0-9]+$", t)
            or re.match(r"^track\s*\d+$", t)
            or re.match(r"^part\s*\d+[a-z]?$", t)
        )

    def _is_removable_descriptor(text: str) -> bool:
        t = text.strip().lower()
        if not t:
            return False
        if _is_important_descriptor(t):
            return False
        if re.match(r"^(rev|revision)\s*[a-z0-9.]+$", t):
            return True
        if re.match(r"^v\d+([._]\d+)*$", t):
            return True
        if re.match(r"^(usa|europe|japan|world|korea|asia|australia|germany|france|italy|spain|sweden|netherlands|brazil|canada|uk|uae)$", t):
            return True
        tokens = [tok.strip(" .,_-/") for tok in re.split(r"[,+/&]", t) if tok.strip(" .,_-/")]
        if tokens and all(tok in {"en", "fr", "de", "es", "it", "pt", "nl", "sv", "no", "da", "fi", "pl", "ru", "jp", "ja", "zh", "ko"} for tok in tokens):
            return True
        if t in {"unl", "proto", "prototype", "beta", "demo", "sample", "alt"}:
            return True
        return False

    def _replace(match: re.Match) -> str:
        inside = match.group(1)
        if _is_removable_descriptor(inside):
            return " "
        tokens = [tok.strip(" .,_-/") for tok in re.split(r"[,+/&]", inside) if tok.strip(" .,_-/")]
        if len(tokens) > 1:
            kept = [tok for tok in tokens if not _is_removable_descriptor(tok)]
            if len(kept) < len(tokens):
                return f" ({', '.join(kept)}) " if kept else " "
        return match.group(0)

    last = None
    while last != s:
        last = s
        s = removable_parenthetical.sub(_replace, s)

    s = re.sub(r"\s*-\s*(rev|revision)\s*[a-z0-9.]+\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([)\]])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
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
        ".pce", ".sgx", ".pbp"
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
        ".gcm"
    }
    return path.suffix.lower() in rom_exts


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


def chd_source_mode(path: pathlib.Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in (".cue", ".gdi", ".toc", ".ccd", ".nrg"):
        return "createcd"
    if suffix in (".iso", ".mds", ".mdf"):
        return "createdvd"
    return None


def collect_chd_sources(extracted_dir: pathlib.Path) -> list[pathlib.Path]:
    sources: list[pathlib.Path] = []
    for ext in ("*.cue", "*.gdi", "*.toc", "*.ccd", "*.nrg", "*.iso", "*.mds"):
        sources.extend(sorted(p for p in extracted_dir.rglob(ext) if p.is_file()))
    return sources


def compress_ps1_to_chd(
    extracted_dir: pathlib.Path,
    chdman_path: str | None,
    progress_cb=None
) -> int:
    if not chdman_path:
        log_activity("chd.skip reason=no_chdman")
        return 0
    chd_sources = collect_chd_sources(extracted_dir)
    if not chd_sources:
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
        startupinfo = _windows_startupinfo()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
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


def extract_archive(
    src_path: pathlib.Path,
    out_dir: pathlib.Path,
    extractors: list[dict] | None = None,
    progress_cb=None,
) -> bool:
    """Extract an archive file to out_dir with support for 7z/RAR/ZIP/TAR/GZ/BZ2/XZ and fallbacks."""
    out_dir.mkdir(parents=True, exist_ok=True)
    extractors = extractors or []
    suffix = src_path.suffix.lower()
    full_name = src_path.name.lower()

    def _report(pct: int, msg: str):
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    _report(0, "Extracting…")

    # 1. Try external tools (7-Zip, PeaZip, WinRAR) first if available
    for tool in extractors:
        if tool["kind"] in ("7zip", "peazip"):
            cmd = [tool["exe"], "x", "-y", "-aoa", "-bd", "-bso1", "-bsp1", f"-o{out_dir}", str(src_path)]
        elif tool["kind"] == "winrar":
            cmd = [tool["exe"], "x", "-y", "-o+", str(src_path), str(out_dir) + "\\"]
        else:
            continue

        log_activity(f"extract.tool.run tool={tool['label']} cmd={' '.join(cmd)}")
        for attempt in range(1, 3):
            startupinfo = _windows_startupinfo()
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    startupinfo=startupinfo,
                )
                last_pct = -1
                if proc.stdout is not None:
                    for line in proc.stdout:
                        line = line.strip()
                        m = re.search(r"(\d{1,3})%", line)
                        if m:
                            pct = min(100, int(m.group(1)))
                            if pct != last_pct:
                                last_pct = pct
                                _report(pct, f"Extracting… {pct}%")
                rc = proc.wait()
                if rc == 0:
                    log_activity(f"extract.tool.ok tool={tool['label']}")
                    _report(100, "Extracted ✓")
                    return True
            except Exception as e:
                log_error(f"extract.tool.error tool={tool['label']}", e)
            if attempt < 2:
                time.sleep(1)

    # 2. Python Built-in ZIP Extraction
    if suffix == ".zip" or zipfile.is_zipfile(src_path):
        _report(0, "Extracting ZIP archive…")
        try:
            with zipfile.ZipFile(src_path, "r") as zf:
                members = zf.infolist()
                total = max(1, len(members))
                for i, member in enumerate(members, start=1):
                    zf.extract(member, out_dir)
                    pct = int(i * 100 / total)
                    _report(pct, f"Extracting… {pct}%")
            log_activity(f"extract.zip.ok src='{src_path}'")
            _report(100, "Extracted ✓")
            return True
        except Exception as e:
            log_error(f"extract.zip.fail src='{src_path}'", e)

    # 3. Python Built-in TAR Extraction (.tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz)
    if (
        suffix in (".tar", ".tgz", ".tbz2", ".txz")
        or full_name.endswith(".tar.gz")
        or full_name.endswith(".tar.bz2")
        or full_name.endswith(".tar.xz")
        or tarfile.is_tarfile(src_path)
    ):
        _report(0, "Extracting TAR archive…")
        try:
            with tarfile.open(src_path, "r:*") as tf:
                members = tf.getmembers()
                total = max(1, len(members))
                for i, member in enumerate(members, start=1):
                    target_path = out_dir / member.name
                    if not str(target_path.resolve()).startswith(str(out_dir.resolve())):
                        continue
                    tf.extract(member, out_dir)
                    pct = int(i * 100 / total)
                    _report(pct, f"Extracting… {pct}%")
            log_activity(f"extract.tar.ok src='{src_path}'")
            _report(100, "Extracted ✓")
            return True
        except Exception as e:
            log_error(f"extract.tar.fail src='{src_path}'", e)

    # 4. Python Built-in GZIP Extraction (single file .gz, e.g. game.nes.gz -> game.nes)
    if suffix == ".gz" and not full_name.endswith(".tar.gz"):
        _report(0, "Decompressing GZIP ROM…")
        dest_filename = src_path.stem if src_path.suffix.lower() == ".gz" else f"{src_path.stem}.bin"
        dest_file = out_dir / dest_filename
        try:
            with gzip.open(src_path, "rb") as f_in, open(dest_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            log_activity(f"extract.gzip.ok src='{src_path}' dest='{dest_file}'")
            _report(100, "Extracted ✓")
            return True
        except Exception as e:
            log_error(f"extract.gzip.fail src='{src_path}'", e)

    # 5. Python Built-in BZIP2 Extraction (single file .bz2, e.g. game.sfc.bz2 -> game.sfc)
    if suffix == ".bz2" and not full_name.endswith(".tar.bz2"):
        _report(0, "Decompressing BZIP2 ROM…")
        dest_filename = src_path.stem if src_path.suffix.lower() == ".bz2" else f"{src_path.stem}.bin"
        dest_file = out_dir / dest_filename
        try:
            with bz2.open(src_path, "rb") as f_in, open(dest_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            log_activity(f"extract.bz2.ok src='{src_path}' dest='{dest_file}'")
            _report(100, "Extracted ✓")
            return True
        except Exception as e:
            log_error(f"extract.bz2.fail src='{src_path}'", e)

    # 6. Python Built-in LZMA/XZ Extraction (single file .xz or .lzma, e.g. game.z64.xz -> game.z64)
    if suffix in (".xz", ".lzma") and not full_name.endswith(".tar.xz"):
        _report(0, "Decompressing XZ/LZMA ROM…")
        dest_filename = src_path.stem if suffix in (".xz", ".lzma") else f"{src_path.stem}.bin"
        dest_file = out_dir / dest_filename
        try:
            with lzma.open(src_path, "rb") as f_in, open(dest_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            log_activity(f"extract.lzma.ok src='{src_path}' dest='{dest_file}'")
            _report(100, "Extracted ✓")
            return True
        except Exception as e:
            log_error(f"extract.lzma.fail src='{src_path}'", e)

    # 7. Passthrough (raw uncompressed ROM or uncompressed disc image)
    _report(0, "Copying ROM file…")
    dest_file = out_dir / src_path.name
    try:
        shutil.copy2(src_path, dest_file)
        log_activity(f"extract.passthrough.ok src='{src_path}' dest='{dest_file}'")
        _report(100, "Extracted ✓")
        return True
    except Exception as e:
        log_error(f"extract.passthrough.fail src='{src_path}'", e)
        raise RuntimeError(f"Could not extract or process downloaded file: {e}")
