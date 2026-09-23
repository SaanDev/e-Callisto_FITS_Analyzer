"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Recent FITS selections and projects, kept in the application settings, and
the rules for which dropped files the main window can open.

A FITS entry is the whole selection that was opened together, so reopening a
combined dataset combines the same files again. Lists are stored as JSON text:
QSettings hands a one-item list back as a bare string on some platforms.
"""

from __future__ import annotations

import json
import os

RECENT_FITS_KEY = "recent/fits"
RECENT_PROJECTS_KEY = "recent/projects"
MAX_RECENT_ENTRIES = 10

FITS_SUFFIXES = (".fit", ".fits", ".fit.gz", ".fits.gz")
PROJECT_SUFFIX = ".efaproj"


def is_fits_path(path) -> bool:
    return str(path or "").lower().endswith(FITS_SUFFIXES)


def is_project_path(path) -> bool:
    return str(path or "").lower().endswith(PROJECT_SUFFIX)


def _normalized(path) -> str:
    return os.path.normpath(os.path.abspath(str(path)))


def _key(path) -> str:
    return os.path.normcase(_normalized(path))


def _read(settings, key) -> list:
    try:
        raw = settings.value(key, "")
    except Exception:
        return []
    if not raw:
        return []
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _write(settings, key, entries) -> None:
    settings.setValue(key, json.dumps(entries))
    try:
        settings.sync()
    except Exception:
        pass


def recent_fits_entries(settings) -> list[list[str]]:
    """Recently opened FITS selections, newest first; each is a list of paths."""
    entries = []
    for entry in _read(settings, RECENT_FITS_KEY):
        paths = [str(path) for path in (entry if isinstance(entry, list) else [entry]) if str(path or "").strip()]
        if paths:
            entries.append(paths)
    return entries[:MAX_RECENT_ENTRIES]


def recent_projects(settings) -> list[str]:
    return [str(path) for path in _read(settings, RECENT_PROJECTS_KEY) if str(path or "").strip()][:MAX_RECENT_ENTRIES]


def _selection_key(paths) -> tuple[str, ...]:
    return tuple(sorted(_key(path) for path in paths))


def add_recent_fits(settings, paths) -> None:
    selection = [_normalized(path) for path in list(paths or []) if str(path or "").strip()]
    if not selection:
        return
    wanted = _selection_key(selection)
    entries = [entry for entry in recent_fits_entries(settings) if _selection_key(entry) != wanted]
    _write(settings, RECENT_FITS_KEY, [selection, *entries][:MAX_RECENT_ENTRIES])


def remove_recent_fits(settings, paths) -> None:
    unwanted = _selection_key(paths)
    entries = [entry for entry in recent_fits_entries(settings) if _selection_key(entry) != unwanted]
    _write(settings, RECENT_FITS_KEY, entries)


def add_recent_project(settings, path) -> None:
    if not str(path or "").strip():
        return
    project = _normalized(path)
    entries = [entry for entry in recent_projects(settings) if _key(entry) != _key(project)]
    _write(settings, RECENT_PROJECTS_KEY, [project, *entries][:MAX_RECENT_ENTRIES])


def remove_recent_project(settings, path) -> None:
    entries = [entry for entry in recent_projects(settings) if _key(entry) != _key(path)]
    _write(settings, RECENT_PROJECTS_KEY, entries)


def clear_recent_fits(settings) -> None:
    _write(settings, RECENT_FITS_KEY, [])


def clear_recent_projects(settings) -> None:
    _write(settings, RECENT_PROJECTS_KEY, [])


def fits_entry_label(paths) -> str:
    """Menu text for a FITS selection: the first file, plus how many more."""
    names = [os.path.basename(str(path)) for path in paths]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{names[0]} (+{len(names) - 1} more)"


def classify_dropped_paths(paths) -> tuple[str | None, object]:
    """What a drop of ``paths`` should open.

    Returns ``("project", path)`` for a single project file, ``("fits", [paths])``
    for FITS files only (one file, or several to combine), or ``(None, reason)``
    when the drop cannot be opened as a whole.
    """
    files = [str(path) for path in list(paths or []) if str(path or "").strip()]
    if not files:
        return None, "Nothing to open."
    projects = [path for path in files if is_project_path(path)]
    fits_files = [path for path in files if is_fits_path(path)]
    others = [os.path.basename(path) for path in files if not is_project_path(path) and not is_fits_path(path)]
    if others:
        return None, "Only FITS files (.fit, .fits, .fit.gz, .fits.gz) and projects (.efaproj) can be opened:\n" + "\n".join(others)
    if projects:
        if len(files) == 1:
            return "project", projects[0]
        return None, "Drop one project file on its own, or FITS files only."
    return "fits", fits_files
