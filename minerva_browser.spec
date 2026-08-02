# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MiNERVA Archive Browser
# Produces a single portable .exe with no console window.

block_cipher = None

# libtorrent is optional — only bundle its DLLs if it is actually installed.
try:
    from PyInstaller.utils.hooks import collect_dynamic_libs
    _lt_binaries = collect_dynamic_libs('libtorrent')
except Exception:
    _lt_binaries = []

_hidden = [
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    '_tkinter',
]
try:
    import libtorrent  # noqa: F401
    _hidden.append('libtorrent')
except ImportError:
    pass

a = Analysis(
    ['minerva_browser.py'],
    pathex=[],
    binaries=_lt_binaries,
    datas=[],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['_meipass_fix.py'],
    excludes=['numpy', 'pandas', 'matplotlib', 'PIL', 'scipy'],
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
)
