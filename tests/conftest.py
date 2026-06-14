"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _isolate_qsettings(tmp_path, monkeypatch):
    """Redirect QSettings to a per-test temp location.

    Prevents tests from reading or writing the developer's real application
    settings (registry on Windows / plist on macOS / INI on Linux), which would
    make tests order-dependent and pollute the machine. The two-arg
    QSettings(org, app) constructor always uses the native backend, so modules
    that persist settings expose a `_make_settings()` factory we redirect to an
    isolated INI file here.
    """
    try:
        from PySide6.QtCore import QSettings
    except Exception:
        yield
        return

    settings_dir = tmp_path / "_qsettings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(settings_dir))
    QSettings.setPath(QSettings.IniFormat, QSettings.SystemScope, str(settings_dir))

    ini_path = str(settings_dir / "settings.ini")

    def _temp_settings():
        return QSettings(ini_path, QSettings.IniFormat)

    # Redirect modules that persist settings, but only if already imported, so
    # that pure backend test runs are not forced to import the Qt UI layer.
    for module_name in ("src.UI.sunpy_solar_viewer",):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "_make_settings"):
            monkeypatch.setattr(module, "_make_settings", _temp_settings, raising=False)

    yield
