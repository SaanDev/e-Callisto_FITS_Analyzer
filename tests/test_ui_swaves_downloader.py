"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("cdflib")
pytest.importorskip("requests")

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import QApplication

from src.Backend.swaves import (
    FIRST_ARCHIVE_DAY,
    SPACECRAFT_AHEAD,
    SPACECRAFT_BEHIND,
    STEREO_B_LAST_CONTACT,
)
from src.UI.swaves_downloader import SwavesDownloaderApp


def _app():
    return QApplication.instance() or QApplication([])


def _behind_index(dialog):
    return dialog.spacecraft_combo.findData(SPACECRAFT_BEHIND)


def _behind_enabled(dialog) -> bool:
    return bool(dialog.spacecraft_combo.model().item(_behind_index(dialog)).isEnabled())


def test_dialog_starts_with_sane_defaults():
    _app()
    dlg = SwavesDownloaderApp()

    assert dlg.plot_button.isEnabled() is True
    assert dlg.cancel_button.isEnabled() is False
    # Nothing to copy until the analyzer hands over a window.
    assert dlg.use_callisto_btn.isEnabled() is False
    assert dlg.selected_spacecraft() == SPACECRAFT_AHEAD
    assert dlg.pad_seconds() == 1800
    assert dlg.is_operation_running() is False

    dlg.close()


def test_dialog_refuses_dates_before_the_archive_starts():
    _app()
    dlg = SwavesDownloaderApp()

    minimum = dlg.date_edit.minimumDate().toPython()
    assert minimum == FIRST_ARCHIVE_DAY

    dlg.close()


def test_end_time_follows_the_duration_spin():
    _app()
    dlg = SwavesDownloaderApp()

    dlg.date_edit.setDate(QDate(2012, 3, 7))
    dlg.time_edit.setTime(QTime(1, 30, 0))
    dlg.duration_spin.setValue(2.5)

    assert dlg.selected_start_utc() == datetime(2012, 3, 7, 1, 30, tzinfo=timezone.utc)
    assert dlg.selected_end_utc() == datetime(2012, 3, 7, 4, 0, tzinfo=timezone.utc)

    dlg.close()


def test_behind_is_available_while_stereo_b_was_alive():
    _app()
    dlg = SwavesDownloaderApp()

    dlg.date_edit.setDate(QDate(2012, 3, 7))
    assert _behind_enabled(dlg) is True

    dlg.spacecraft_combo.setCurrentIndex(_behind_index(dlg))
    assert dlg.selected_spacecraft() == SPACECRAFT_BEHIND

    dlg.close()


def test_behind_is_disabled_and_deselected_after_contact_was_lost():
    _app()
    dlg = SwavesDownloaderApp()

    dlg.date_edit.setDate(QDate(2012, 3, 7))
    dlg.spacecraft_combo.setCurrentIndex(_behind_index(dlg))
    assert dlg.selected_spacecraft() == SPACECRAFT_BEHIND

    dlg.date_edit.setDate(QDate(2024, 1, 1))

    assert _behind_enabled(dlg) is False
    assert dlg.selected_spacecraft() == SPACECRAFT_AHEAD, "must fall back rather than request fill"
    tip = dlg.spacecraft_combo.itemData(_behind_index(dlg), 3)  # Qt.ToolTipRole
    assert STEREO_B_LAST_CONTACT.isoformat() in str(tip)

    dlg.close()


def test_use_callisto_window_pads_both_sides():
    _app()
    start = datetime(2012, 3, 7, 1, 0, tzinfo=timezone.utc)
    end = datetime(2012, 3, 7, 1, 15, tzinfo=timezone.utc)
    dlg = SwavesDownloaderApp(callisto_window=(start, end))

    assert dlg.use_callisto_btn.isEnabled() is True
    assert dlg.selected_start_utc() == start - timedelta(minutes=30)
    assert dlg.selected_end_utc() == end + timedelta(minutes=30)

    dlg.close()


def test_padding_spin_changes_the_copied_window():
    _app()
    start = datetime(2012, 3, 7, 1, 0, tzinfo=timezone.utc)
    end = datetime(2012, 3, 7, 1, 15, tzinfo=timezone.utc)
    dlg = SwavesDownloaderApp(callisto_window=(start, end))

    dlg.pad_spin.setValue(90)
    dlg.use_callisto_window()

    assert dlg.selected_start_utc() == start - timedelta(minutes=90)
    assert dlg.selected_end_utc() == end + timedelta(minutes=90)

    dlg.close()


def test_set_callisto_context_enables_the_copy_button():
    _app()
    dlg = SwavesDownloaderApp()
    assert dlg.use_callisto_btn.isEnabled() is False

    start = datetime(2012, 3, 7, 1, 0, tzinfo=timezone.utc)
    base = datetime(2012, 3, 7, 0, 45, tzinfo=timezone.utc)
    dlg.set_callisto_context((start, start + timedelta(minutes=15)), base)

    assert dlg.use_callisto_btn.isEnabled() is True
    assert dlg._callisto_base_utc == base

    dlg.close()


def test_summary_flags_a_window_that_spans_two_archive_days():
    _app()
    dlg = SwavesDownloaderApp()

    dlg.date_edit.setDate(QDate(2012, 3, 7))
    dlg.time_edit.setTime(QTime(23, 0, 0))
    dlg.duration_spin.setValue(3.0)

    assert "2 archive day files" in dlg.summary_label.text()

    dlg.close()
