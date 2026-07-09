"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget

import src.UI.font_utils as font_utils
from src.UI.font_utils import preferred_monospace_font_family
from src.UI.gui_shared import (
    clamp_minimum_size_to_screen,
    fit_window_to_screen,
    screen_available_geometry,
)


def _app():
    return QApplication.instance() or QApplication([])


class TestFitWindowToScreen:
    def test_oversized_request_is_clamped_to_screen(self):
        _app()
        widget = QWidget()
        try:
            fit_window_to_screen(widget, 50000, 50000)
            avail = screen_available_geometry(widget)
            assert avail is not None
            assert widget.width() <= int(avail.width() * 0.94)
            assert widget.height() <= int(avail.height() * 0.90)
        finally:
            widget.deleteLater()

    def test_small_request_is_kept(self):
        _app()
        widget = QWidget()
        try:
            fit_window_to_screen(widget, 320, 240)
            assert widget.width() == 320
            assert widget.height() == 240
        finally:
            widget.deleteLater()

    def test_minimum_never_exceeds_final_size(self):
        _app()
        widget = QWidget()
        try:
            fit_window_to_screen(widget, 50000, 50000, min_width=50000, min_height=50000)
            assert widget.minimumWidth() <= widget.width()
            assert widget.minimumHeight() <= widget.height()
        finally:
            widget.deleteLater()


class TestClampMinimumSizeToScreen:
    def test_oversized_minimum_is_clamped(self):
        _app()
        widget = QWidget()
        try:
            clamp_minimum_size_to_screen(widget, 50000, 50000)
            avail = screen_available_geometry(widget)
            assert avail is not None
            assert widget.minimumWidth() <= int(avail.width() * 0.90)
            assert widget.minimumHeight() <= int(avail.height() * 0.90)
        finally:
            widget.deleteLater()

    def test_reasonable_minimum_is_kept(self):
        _app()
        widget = QWidget()
        try:
            clamp_minimum_size_to_screen(widget, 320, 240)
            assert widget.minimumWidth() == 320
            assert widget.minimumHeight() == 240
        finally:
            widget.deleteLater()


class TestPreferredMonospaceFontFamily:
    def test_never_returns_a_missing_family(self):
        """The point of the helper: a stylesheet must never name a font the
        platform does not have (that triggers the qt.qpa.fonts alias warning)."""
        families = {"Arial", "Helvetica"}
        result = preferred_monospace_font_family(available_families=families)
        assert result == "monospace" or result in families

    def test_macos_resolves_menlo(self, monkeypatch):
        monkeypatch.setattr(font_utils.sys, "platform", "darwin")
        result = preferred_monospace_font_family(available_families={"Menlo", "Arial"})
        assert result == "Menlo"

    def test_windows_resolves_consolas(self, monkeypatch):
        monkeypatch.setattr(font_utils.sys, "platform", "win32")
        result = preferred_monospace_font_family(available_families={"Consolas", "Arial"})
        assert result == "Consolas"

    def test_linux_resolves_dejavu(self, monkeypatch):
        monkeypatch.setattr(font_utils.sys, "platform", "linux")
        result = preferred_monospace_font_family(available_families={"DejaVu Sans Mono"})
        assert result == "DejaVu Sans Mono"

    def test_real_platform_returns_nonempty(self):
        _app()
        assert preferred_monospace_font_family()
