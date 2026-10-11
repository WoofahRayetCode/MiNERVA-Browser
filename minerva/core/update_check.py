"""Find updates/DLC for ROMs that are already downloaded.

Updates used to be offered only at the moment a base game was queued.  This checks every game the
app knows the archive folder of, drops what is already installed, and keeps only updates that are
newer than the newest update already downloaded for that game.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from minerva.constants import log_error
from minerva.core.companions import (
    KIND_BASE,
    KIND_UPDATE,
    Companion,
    classify_release,
    find_companions,
    title_key,
    version_key,
)


@dataclass
class UpdateReport:
    base_name: str
    browse_path: str
    items: list[Companion]


@dataclass
class UpdateCheckResult:
    reports: list[UpdateReport] = field(default_factory=list)
    checked: int = 0
    #: Games without a recorded archive folder (downloaded before the app remembered it).
    unknown_folder: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def items(self) -> list[Companion]:
        """All offered files, one entry per archive file."""
        seen: set[str] = set()
        out: list[Companion] = []
        for report in self.reports:
            for item in report.items:
                key = item.href.lower() or item.name.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(item)
        return out


def _installed_update_versions(installed_names: Iterable[str]) -> dict[str, tuple]:
    """Newest already-downloaded update version per game title (computed once for the whole check)."""
    newest: dict[str, tuple] = {}
    for name in installed_names:
        if classify_release(name) != KIND_UPDATE:
            continue
        key = title_key(name)
        if key and version_key(name) > newest.get(key, ()):
            newest[key] = version_key(name)
    return newest


def check_library_updates(
    games: Iterable[dict],
    installed_names: Iterable[str],
    fetch_fn,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    finder=find_companions,
) -> UpdateCheckResult:
    """``games`` are dicts with ``name`` and ``browse_path`` (the archive folder it came from)."""
    installed = [n for n in installed_names if n]
    installed_lower = {n.lower() for n in installed}
    newest_installed = _installed_update_versions(installed)

    unique: dict[str, dict] = {}
    result = UpdateCheckResult()
    for game in games:
        name = (game.get("name") or "").strip()
        if not name or name.lower() in unique:
            continue
        if classify_release(name) != KIND_BASE:
            continue  # an update/DLC file is not a game to look updates up for
        if not game.get("browse_path"):
            result.unknown_folder += 1
            continue
        unique[name.lower()] = game
    total = len(unique)
    for i, game in enumerate(unique.values(), start=1):
        if cancel is not None and cancel():
            result.cancelled = True
            break
        name, browse_path = game["name"], game["browse_path"]
        if progress is not None:
            progress(i, total, name)
        result.checked += 1
        try:
            companions = finder(name, browse_path, fetch_fn=fetch_fn, latest_update_only=True)
        except Exception as e:
            log_error(f"update check failed for {name}", e)
            result.errors.append(f"{name}: {e}")
            continue
        have = newest_installed.get(title_key(name), ())
        wanted = []
        for item in companions:
            if item.name.lower() in installed_lower:
                continue
            if item.kind == KIND_UPDATE and have and item.version_key <= have:
                continue  # an equal or newer update is already downloaded
            wanted.append(item)
        if wanted:
            result.reports.append(UpdateReport(name, browse_path, wanted))
    return result
