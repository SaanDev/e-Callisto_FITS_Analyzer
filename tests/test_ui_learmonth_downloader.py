"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("astropy")

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import QApplication

from src.Backend.learmonth import LearmonthChunk
from src.UI.learmonth_downloader import LearmonthDownloaderApp
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _find_menu_action(menu, text):
    for action in menu.actions():
        if action.text() == text:
            return action
    return None


def test_learmonth_menu_action_is_present_in_radio_bursts_menu():
    _app()
    window = MainWindow(theme=None)

    solar_menu = _find_menu_action(window.menuBar(), "Solar Events").menu()
    radio_menu = _find_menu_action(solar_menu, "Radio Bursts").menu()
    radio_actions = [action.text() for action in radio_menu.actions() if not action.isSeparator()]

    assert radio_actions == ["e-CALLISTO", "Learmonth"]
    window.close()


def test_learmonth_dialog_starts_with_actions_disabled():
    _app()
    dlg = LearmonthDownloaderApp()

    assert dlg.show_button.isEnabled() is True
    assert dlg.download_button.isEnabled() is False
    assert dlg.select_all_button.isEnabled() is False
    assert dlg.deselect_all_button.isEnabled() is False
    assert dlg.convert_button.isEnabled() is False
    assert dlg.convert_import_button.isEnabled() is False

    dlg.close()


def test_learmonth_dialog_load_finished_populates_chunks_and_preselects_matching_time(tmp_path):
    _app()
    dlg = LearmonthDownloaderApp()
    day_path = tmp_path / "LM240401.srs"
    day_path.write_bytes(b"demo")

    dlg.date_edit.setDate(QDate(2024, 4, 1))
    dlg.time_edit.setTime(QTime(0, 6, 0))

    first = LearmonthChunk(
        index=0,
        start_dt=datetime(2024, 3, 31, 23, 50, 0),
        end_dt=datetime(2024, 4, 1, 0, 5, 0),
        scan_count=300,
        offset_start=24,
        offset_end=240624,
        is_partial=False,
    )
    second = LearmonthChunk(
        index=1,
        start_dt=datetime(2024, 4, 1, 0, 5, 0),
        end_dt=datetime(2024, 4, 1, 0, 20, 0),
        scan_count=300,
        offset_start=240648,
        offset_end=481248,
        is_partial=False,
    )

    dlg._on_load_finished(
        {
            "local_path": str(day_path),
            "chunks": [first, second],
            "url": "https://example.test/LM240401.srs",
            "filename": "LM240401.srs",
            "size_bytes": 1024,
        }
    )

    assert dlg.chunk_list.count() == 2
    assert dlg.chunk_list.currentRow() == 1
    assert dlg.download_button.isEnabled() is True
    assert dlg.convert_button.isEnabled() is False
    assert dlg.convert_import_button.isEnabled() is False

    dlg.select_all_chunks()
    assert all(dlg.chunk_list.item(row).checkState() for row in range(dlg.chunk_list.count()))
    assert dlg.convert_button.isEnabled() is True
    assert dlg.convert_import_button.isEnabled() is True

    dlg.deselect_all_chunks()
    assert all(dlg.chunk_list.item(row).checkState() == 0 for row in range(dlg.chunk_list.count()))
    assert dlg.convert_button.isEnabled() is False
    assert dlg.convert_import_button.isEnabled() is False

    dlg.close()
