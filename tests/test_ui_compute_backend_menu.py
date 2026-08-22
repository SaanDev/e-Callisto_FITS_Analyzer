"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication

from src.Backend import compute
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window():
    _app()
    win = MainWindow()
    try:
        yield win
    finally:
        win.close()
        compute.select_backend(compute.BACKEND_AUTO)


def test_menu_offers_every_backend(window):
    assert set(window.compute_backend_actions) == set(compute.BACKEND_CHOICES)
    for backend, action in window.compute_backend_actions.items():
        assert action.data() == backend
        assert action.text() == compute.BACKEND_LABELS[backend]


def test_auto_and_numpy_are_always_selectable(window):
    assert window.compute_backend_actions[compute.BACKEND_AUTO].isEnabled()
    assert window.compute_backend_actions[compute.BACKEND_NUMPY].isEnabled()


def test_construction_does_not_probe_for_devices():
    """Device discovery imports JAX, which is far too slow for the startup path."""
    compute.reset_for_tests()
    _app()
    win = MainWindow()
    try:
        assert compute.is_probed() is False
    finally:
        win.close()


def test_unavailable_backends_are_disabled_once_the_menu_opens(window):
    # Availability is resolved lazily, when the menu is about to be shown.
    window.compute_backend_menu.aboutToShow.emit()

    available = {device.backend for device in compute.available_devices()}
    for backend in (compute.BACKEND_CUDA, compute.BACKEND_METAL):
        action = window.compute_backend_actions[backend]
        assert action.isVisible() or not action.isEnabled()
        assert action.isEnabled() == (backend in available)


def test_backend_selection_is_exclusive(window):
    assert window.compute_backend_group.isExclusive()
    checked = [a for a in window.compute_backend_actions.values() if a.isChecked()]
    assert len(checked) == 1


def test_selecting_numpy_persists_and_applies(window):
    action = window.compute_backend_actions[compute.BACKEND_NUMPY]
    action.trigger()

    assert compute.active_device() == compute.NUMPY_DEVICE
    assert compute.is_accelerated() is False
    stored = window._ui_settings.value(window.COMPUTE_BACKEND_SETTINGS_KEY, "", type=str)
    assert stored == compute.BACKEND_NUMPY


def test_stored_backend_is_restored_on_startup(window):
    window._ui_settings.setValue(window.COMPUTE_BACKEND_SETTINGS_KEY, compute.BACKEND_NUMPY)
    window._restore_compute_backend()

    assert compute.requested_backend() == compute.BACKEND_NUMPY
    assert window.compute_backend_actions[compute.BACKEND_NUMPY].isChecked()


def test_unknown_stored_backend_falls_back_to_auto(window):
    window._ui_settings.setValue(window.COMPUTE_BACKEND_SETTINGS_KEY, "opencl")
    window._restore_compute_backend()

    assert compute.requested_backend() == compute.BACKEND_AUTO
    assert window.compute_backend_actions[compute.BACKEND_AUTO].isChecked()


def test_device_info_reports_the_active_backend(window, monkeypatch):
    captured = {}

    def _fake_information(parent, title, text):
        captured["title"] = title
        captured["text"] = text

    monkeypatch.setattr("src.UI.main_window.QMessageBox.information", _fake_information)
    window.show_compute_device_info()

    assert captured["title"] == "Compute Device Info"
    assert "Active:" in captured["text"]
    assert "Available backends:" in captured["text"]
    assert compute.BACKEND_LABELS[compute.BACKEND_NUMPY] in captured["text"]


def test_selecting_an_unavailable_backend_reports_the_fallback(window, monkeypatch):
    messages = []
    monkeypatch.setattr(
        window.statusBar(), "showMessage", lambda text, *a, **k: messages.append(text)
    )

    available = {device.backend for device in compute.available_devices()}
    if compute.BACKEND_CUDA in available:
        pytest.skip("this machine really has a CUDA backend")

    window._on_compute_backend_selected(window.compute_backend_actions[compute.BACKEND_CUDA])
    assert messages and "not available here" in messages[-1]
