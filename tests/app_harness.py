"""Shared setup for tests that run the real MinervaApp window offline and fully isolated.

Several modules import ``get_runtime_base_dir`` *by name*, so patching it in
``minerva.constants`` alone leaves them writing into the repository (an early version of these
tests downloaded a tool into ``./tools``).  Every such reference is patched here, and the app's
optional tool downloads and update check are disabled.
"""
import os
import pathlib
from unittest import mock

import minerva.constants as constants
import minerva.core.extractors as extractors


def isolated_app_patches(app_module, base: pathlib.Path, fetch_entries) -> list:
    app_cls = app_module.MinervaApp
    return [
        mock.patch.dict(os.environ, {"MINERVA_ERROR_LOG": str(base / "error.log")}),
        mock.patch.object(constants, "get_runtime_base_dir", lambda: base),
        mock.patch.object(extractors, "get_runtime_base_dir", lambda: base),
        mock.patch.object(app_module, "get_runtime_base_dir", lambda: base),
        mock.patch.object(app_module, "fetch_entries", fetch_entries),
        mock.patch.object(app_cls, "_check_for_updates_async", lambda self: None),
        mock.patch.object(app_cls, "_run_startup_cleanup", lambda self: None),
        mock.patch.object(app_cls, "_ensure_chdman_available_async", lambda self: None),
        mock.patch.object(app_cls, "_ensure_xbox_unpack_tool_async", lambda self: None),
        mock.patch.object(
            app_cls, "_setup_system_tray",
            lambda self: (setattr(self, "_tray_icon", None), setattr(self, "_quitting", False)),
        ),
    ]
