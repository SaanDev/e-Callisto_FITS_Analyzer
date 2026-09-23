"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("astropy")

from PySide6.QtCore import QMimeData, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QGraphicsView, QMessageBox

from src.ui.app import recent_files
from src.ui.app.main_window import MainWindow
from tests.helpers.learmonth import write_test_callisto_fit


def _app():
    return QApplication.instance() or QApplication([])


def _flush_events():
    app = _app()
    for _ in range(5):
        app.processEvents()


def _fits(path, *, time_obs="12:00:00"):
    return write_test_callisto_fit(
        path,
        data=np.arange(12, dtype=np.uint8).reshape(3, 4),
        freqs=np.array([60.0, 50.0, 40.0]),
        time=np.array([0.0, 225.0, 450.0, 675.0]),
        date_obs="2024/01/01",
        time_obs=time_obs,
    )


def _mime(*paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def _drop(window, *paths):
    mime = _mime(*paths)
    event = QDropEvent(QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.dropEvent(event)
    _flush_events()
    return event


# --- the stored lists ---------------------------------------------------------


def test_recent_lists_keep_newest_first_without_duplicates(tmp_path):
    settings = QSettings(str(tmp_path / "recent.ini"), QSettings.IniFormat)
    a, b = str(tmp_path / "A.fit"), str(tmp_path / "B.fit")

    recent_files.add_recent_fits(settings, [a])
    recent_files.add_recent_fits(settings, [b, a])
    recent_files.add_recent_fits(settings, [a])

    entries = recent_files.recent_fits_entries(settings)
    assert [len(entry) for entry in entries] == [1, 2]
    assert recent_files.fits_entry_label(entries[1]).endswith("(+1 more)")

    for index in range(15):
        recent_files.add_recent_project(settings, str(tmp_path / f"p{index}.efaproj"))
    projects = recent_files.recent_projects(settings)
    assert len(projects) == recent_files.MAX_RECENT_ENTRIES
    assert projects[0].endswith("p14.efaproj")


def test_dropped_paths_are_classified():
    assert recent_files.classify_dropped_paths(["a.fit", "b.fit.gz"]) == ("fits", ["a.fit", "b.fit.gz"])
    assert recent_files.classify_dropped_paths(["x.efaproj"]) == ("project", "x.efaproj")
    assert recent_files.classify_dropped_paths(["x.efaproj", "a.fit"])[0] is None
    assert recent_files.classify_dropped_paths(["notes.txt"])[0] is None


# --- the main window ----------------------------------------------------------


def test_dropping_a_fits_file_opens_it_and_remembers_it(tmp_path):
    _app()
    path = _fits(tmp_path / "STAT_20240101_120000_01.fit")
    window = MainWindow(theme=None)

    event = _drop(window, path)

    assert event.isAccepted()
    assert window.raw_data is not None
    assert window.filename == path.name
    assert recent_files.recent_fits_entries(window._ui_settings)[0] == [str(path)]
    labels = [action.text() for action in window.recent_files_menu.actions()]
    assert path.name in labels
    window.close()


def test_dropping_consecutive_files_combines_them(tmp_path):
    _app()
    first = _fits(tmp_path / "STAT_20240101_120000_01.fit")
    second = _fits(tmp_path / "STAT_20240101_121500_01.fit", time_obs="12:15:00")
    window = MainWindow(theme=None)

    _drop(window, first, second)

    assert window._is_combined is True
    assert window.raw_data.shape[1] == 8
    window.close()


def test_unsupported_files_are_refused_at_drag_enter(tmp_path):
    _app()
    note = tmp_path / "notes.txt"
    note.write_text("x")
    window = MainWindow(theme=None)

    # The event keeps only a pointer to the mime data, so hold a reference.
    mime = _mime(note)
    event = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.dragEnterEvent(event)

    assert not event.isAccepted()
    window.close()


def test_graphics_views_hand_drops_to_the_window():
    _app()
    window = MainWindow(theme=None)

    assert window.acceptDrops()
    for view in window.findChildren(QGraphicsView):
        assert not view.acceptDrops()
        assert not view.viewport().acceptDrops()
    window.close()


def test_recent_project_reopens_and_a_missing_one_is_forgotten(tmp_path, monkeypatch):
    _app()
    fits_path = _fits(tmp_path / "STAT_20240101_120000_01.fit")
    project = tmp_path / "saved.efaproj"
    window = MainWindow(theme=None)
    window.open_fits_paths([str(fits_path)])
    _flush_events()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    assert window.save_project_as()
    assert recent_files.recent_projects(window._ui_settings)[0] == str(project)
    assert project.name in [action.text() for action in window.recent_projects_menu.actions()]

    other = MainWindow(theme=None)
    assert other.open_recent_project(str(project))
    _flush_events()
    assert other.raw_data is not None

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    missing = str(tmp_path / "gone.efaproj")
    recent_files.add_recent_project(other._ui_settings, missing)
    assert not other.open_recent_project(missing)
    assert missing not in recent_files.recent_projects(other._ui_settings)
    assert warnings
    window.close()
    other.close()


def test_each_recent_menu_clears_only_its_own_list(tmp_path):
    _app()
    path = _fits(tmp_path / "STAT_20240101_120000_01.fit")
    window = MainWindow(theme=None)
    window.open_fits_paths([str(path)])
    recent_files.add_recent_project(window._ui_settings, str(tmp_path / "kept.efaproj"))
    window._rebuild_recent_menu()

    clear = [action for action in window.recent_files_menu.actions() if action.text() == "Clear Recent Files"]
    assert clear
    clear[0].trigger()

    assert recent_files.recent_fits_entries(window._ui_settings) == []
    assert [action.text() for action in window.recent_files_menu.actions()] == ["No Recent Files"]
    assert [action.text() for action in window.recent_projects_menu.actions()][0] == "kept.efaproj"

    clear = [action for action in window.recent_projects_menu.actions() if action.text() == "Clear Recent Projects"]
    clear[0].trigger()
    assert [action.text() for action in window.recent_projects_menu.actions()] == ["No Recent Projects"]
    window.close()
