"""Undoing the last front point and hiding the image banners in the GCS window."""

from datetime import datetime

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("sunpy.map")

from PySide6.QtCore import Qt

from test_ui_gcs_window import _app, _flush, _sequence
from src.UI.gcs_fitting_window import GCS_MODEL, SHOCK_MODEL, GCSFittingWindow

BASE = datetime(2012, 7, 12, 16)


@pytest.fixture
def window():
    _app()
    widget = GCSFittingWindow(target_time=BASE)
    yield widget
    widget.close()


def _load(window, n=3, cadence_min=12):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon, n=n, cadence_min=cadence_min))
    _flush()


# --- Undo the last front point ------------------------------------------------------------


def test_undo_removes_the_last_point_clicked_in_any_panel(window):
    _load(window)
    assert not window.undo_point_btn.isEnabled()
    window._on_canvas_click("A", 1000.0, 10.0, "left")
    window._on_canvas_click("B", 2000.0, 20.0, "left")
    window._on_canvas_click("A", 3000.0, 30.0, "left")
    window._on_canvas_click("C", 4000.0, 40.0, "left")
    assert window.undo_point_btn.isEnabled()

    window.undo_point_btn.click()
    assert window._clicks["C"] == []
    assert window.status_label.text() == "Removed the last front point, from panel C."
    window._undo_last_point()
    assert window._clicks["A"] == [(1000.0, 10.0)]
    window._undo_last_point()
    assert window._clicks["B"] == []
    window._undo_last_point()
    assert window._clicks["A"] == []
    assert not window.undo_point_btn.isEnabled()
    window._undo_last_point()
    assert window.status_label.text() == "No front point to undo on the displayed frames."


def test_undo_skips_points_already_removed_by_right_click_or_clear(window):
    _load(window)
    window._on_canvas_click("A", 1000.0, 10.0, "left")
    window._on_canvas_click("B", 2000.0, 20.0, "left")
    window._on_canvas_click("B", 0.0, 0.0, "right")  # removes B's point
    window._undo_last_point()
    assert window._clicks["A"] == [] and window._clicks["B"] == []
    window._on_canvas_click("C", 3000.0, 30.0, "left")
    window._clear_points()
    assert not window._tracks[GCS_MODEL].click_order
    assert not window.undo_point_btn.isEnabled()


def test_undo_leaves_points_on_frames_not_shown_until_they_return(window):
    _load(window, n=3, cadence_min=10)
    window.time_slider.setValue(0)
    window._on_canvas_click("A", 1000.0, 10.0, "left")
    window.time_slider.setValue(1)
    window._on_canvas_click("A", 2000.0, 20.0, "left")
    window.time_slider.setValue(2)
    assert not window.undo_point_btn.isEnabled()  # nothing clicked on these frames
    window._undo_last_point()
    window.time_slider.setValue(0)
    assert window._clicks["A"] == [(1000.0, 10.0)]
    window._undo_last_point()
    assert window._clicks["A"] == []
    window.time_slider.setValue(1)
    assert window._clicks["A"] == [(2000.0, 20.0)]


def test_undo_acts_on_the_edited_models_points_only(window):
    _load(window)
    window._on_canvas_click("A", 1000.0, 10.0, "left")
    window.set_editing_model(SHOCK_MODEL)
    window._on_canvas_click("B", 2000.0, 20.0, "left")
    window.set_editing_model(GCS_MODEL)
    window._undo_last_point()
    assert window._clicks["A"] == []
    assert window._tracks[SHOCK_MODEL].clicks["B"] == [(2000.0, 20.0)]
    window.set_editing_model(SHOCK_MODEL)
    window._undo_last_point()
    assert window._tracks[SHOCK_MODEL].clicks["B"] == []
    assert window.status_label.text() == "Removed the last shock front point, from panel B."


def test_reloading_a_channel_forgets_its_undo_history(window):
    _load(window)
    window._on_canvas_click("A", 1000.0, 10.0, "left")
    window._on_canvas_click("B", 2000.0, 20.0, "left")
    window.panels[1].set_frames(_sequence(62.0, n=3))
    assert [entry[0] for entry in window._tracks[GCS_MODEL].click_order] == ["A"]
    window._undo_last_point()
    assert window._clicks["A"] == []


def test_undo_is_in_the_fit_menu_with_ctrl_z(window):
    _load(window)
    action = window.menu_actions["undo_point"]
    assert action.shortcut().toString() == "Ctrl+Z"
    window._sync_menu_actions()
    assert not action.isEnabled()
    window._on_canvas_click("A", 1000.0, 10.0, "left")
    window._sync_menu_actions()
    assert action.isEnabled()
    action.trigger()
    assert window._clicks["A"] == []


# --- Image banners ------------------------------------------------------------------------------


def test_the_banner_toggle_hides_and_shows_every_image_banner(window):
    _load(window)
    assert window.banner_check.isChecked()
    assert all(panel.caption_visible() for panel in window.panels)
    window.banner_check.setChecked(False)
    assert not any(panel.caption_visible() for panel in window.panels)
    # Hidden banners keep up with the images, so showing them again is current.
    window.next_frame()
    assert "16:12:00" in window.panels[0].title_label.text()
    window.panels[0].set_frames(_sequence(0.0, n=3, cadence_min=12))
    assert not window.panels[0].caption_visible()  # a load does not bring it back
    window.banner_check.setChecked(True)
    assert all(panel.caption_visible() for panel in window.panels)


def test_the_banner_is_in_the_view_menu_and_on_the_b_key(window):
    _load(window)
    action = window.menu_actions["banner"]
    window._sync_menu_actions()
    assert action.isChecked()
    action.trigger()
    assert not window.banner_check.isChecked() and not window.panels[1].caption_visible()
    window._sync_menu_actions()
    assert not action.isChecked()
    keys = {shortcut.key().toString() for shortcut in window._shortcuts}
    assert "B" in keys
    shortcut = next(item for item in window._shortcuts if item.key().toString() == "B")
    assert shortcut.context() == Qt.WidgetWithChildrenShortcut
    shortcut.activated.emit()
    assert window.banner_check.isChecked() and window.panels[1].caption_visible()


def test_a_snapshot_without_banners_leaves_them_out(window, tmp_path, monkeypatch):
    _load(window)
    window.resize(1200, 800)
    window.show()
    _flush(20)
    try:
        paths = {}
        for state in (True, False):
            window.banner_check.setChecked(state)
            _flush(5)
            path = tmp_path / f"banner_{state}.png"
            monkeypatch.setattr(
                "src.UI.gcs_window_exports.pick_export_path", lambda *a, p=path, **k: (str(p), "png")
            )
            window._save_snapshot()
            paths[state] = path
        from PySide6.QtGui import QImage

        shown, hidden = (QImage(str(paths[state])) for state in (True, False))
        assert not shown.isNull() and shown.size() == hidden.size()
        assert shown != hidden
    finally:
        window.hide()
