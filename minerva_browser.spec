# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MiNERVA Archive Browser
# Produces a single portable .exe with no console window.

block_cipher = None

# libtorrent is optional — only bundle its binaries/shared libs if installed.
import os
import pathlib

_lt_binaries = []
try:
    from PyInstaller.utils.hooks import collect_dynamic_libs
    _lt_binaries = collect_dynamic_libs('libtorrent')
except Exception:
    pass

try:
    import libtorrent
    if hasattr(libtorrent, '__file__') and libtorrent.__file__:
        _lt_path = str(pathlib.Path(libtorrent.__file__).resolve())
        if not any(src == _lt_path for src, _ in _lt_binaries):
            _lt_binaries.append((_lt_path, '.'))
except Exception:
    libtorrent = None

# Locally built cp314 extension links against system shared libs; collect them.
# Always resolve symlinks so we package the real ELF, then also expose the SONAME
# filename the extension DT_NEEDED entry expects.
_lt_shared_candidates = [
    '/usr/lib/libtorrent-rasterbar.so.2.1',
    '/usr/lib/libtorrent-rasterbar.so.2.1.1',
    '/usr/lib/libtorrent-rasterbar.so',
    '/usr/lib/libboost_python314.so.1.92.0',
    '/usr/lib/libboost_python314.so',
]
_seen_dst = {dst for _, dst in _lt_binaries}
_seen_pairs = {(src, dst) for src, dst in _lt_binaries}
for _cand in _lt_shared_candidates:
    _cand_path = pathlib.Path(_cand)
    if not _cand_path.exists():
        continue
    _src = str(_cand_path.resolve())
    if not pathlib.Path(_src).is_file() or pathlib.Path(_src).stat().st_size < 1024:
        continue
    for _dst_name in {_cand_path.name, pathlib.Path(_src).name}:
        _pair = (_src, '.')
        # PyInstaller binaries are (src, dest_dir). Duplicate basenames collide,
        # so copy under each needed SONAME via dest '.' and unique temp names is
        # awkward; instead add one entry per destination basename using hardlink
        # copies next to the spec when names differ.
        if _dst_name in _seen_dst:
            continue
        if _dst_name == pathlib.Path(_src).name:
            _lt_binaries.append((_src, '.'))
            _seen_dst.add(_dst_name)
            continue
        _alias_dir = pathlib.Path('build') / '_lt_sonames'
        _alias_dir.mkdir(parents=True, exist_ok=True)
        _alias = _alias_dir / _dst_name
        if not _alias.exists() or _alias.stat().st_size != pathlib.Path(_src).stat().st_size:
            _alias.write_bytes(pathlib.Path(_src).read_bytes())
        _lt_binaries.append((str(_alias.resolve()), '.'))
        _seen_dst.add(_dst_name)

_hidden = [
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    '_tkinter',
    'minerva',
    'minerva.constants',
    'minerva.core',
    'minerva.core.sqlite_http',
    'minerva.core.torrent_engine',
    'minerva.core.extractors',
    'minerva.core.companions',
    'minerva.ui',
    'minerva.ui.app',
    'pystray',
    'PIL',
    'PIL.Image',
    'PIL.PngImagePlugin',
    'PIL.IcoImagePlugin',
]
# Do not import pystray backends here: _xorg/_gtk open a display at import time
# and break headless/CI builds. Listing them as hiddenimports is enough for bundling.
for _mod in (
    'pystray._win32',
    'pystray._util',
    'pystray._appindicator',
    'pystray._gtk',
    'pystray._xorg',
    'pystray._darwin',
):
    _hidden.append(_mod)
try:
    import libtorrent  # noqa: F401
    _hidden.append('libtorrent')
except ImportError:
    pass



a = Analysis(
    ['minerva_browser.py'],
    pathex=[],
    binaries=_lt_binaries,
    datas=[
        ('minerva/assets', 'minerva/assets'),
    ],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['_meipass_fix.py'],
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MiNERVA-Browser',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='minerva/assets/icon.ico',
)
