#!/usr/bin/env python3
"""Extract bundled libtorrent bits from the onefile binary and import them."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

from PyInstaller.archive.readers import CArchiveReader

ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY = ROOT / "dist" / "MiNERVA-Browser"
OUT = ROOT / "vendor" / "lt-smoke"


def main() -> int:
    if not BINARY.exists():
        print(f"missing binary: {BINARY}", file=sys.stderr)
        return 1

    if OUT.exists():
        for child in OUT.iterdir():
            child.unlink()
    OUT.mkdir(parents=True, exist_ok=True)

    reader = CArchiveReader(str(BINARY))
    extracted = []
    for name in list(reader.toc):
        base = name.split("/")[-1]
        if "libtorrent" in base or "boost_python314" in base:
            data = reader.extract(name)
            (OUT / base).write_bytes(data)
            extracted.append((base, len(data)))
            print(f"extracted {base} ({len(data)} bytes)")

    if not any(name.startswith("libtorrent.cpython-") for name, _ in extracted):
        print("libtorrent extension missing from bundle", file=sys.stderr)
        return 1

    short = [name for name, size in extracted if size < 1024]
    if short:
        print(f"suspiciously small bundled libs: {short}", file=sys.stderr)
        return 1

    os.environ["LD_LIBRARY_PATH"] = str(OUT) + (
        os.pathsep + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else ""
    )
    ext = next(OUT.glob("libtorrent.cpython-*.so"))
    spec = importlib.util.spec_from_file_location("libtorrent", ext)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    print(f"runtime import OK {module.__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
