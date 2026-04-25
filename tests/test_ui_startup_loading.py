"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.UI.startup_loading import StartupLoadingScreen


def _app():
    return QApplication.instance() or QApplication([])


def test_startup_loading_screen_tracks_progress_and_status():
    _app()

    splash = StartupLoadingScreen("Test App", "9.9.9")
    splash.present()
    splash.set_progress(140, "Preparing interface...")

    assert splash.isVisible()
    assert splash.progress_value() == 100
    assert splash.status_text() == "Preparing interface..."
    assert bool(splash.windowFlags() & Qt.WindowType.SplashScreen)

    splash.close()
