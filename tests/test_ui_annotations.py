"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")
pytest.importorskip("openpyxl")
pytest.importorskip("requests")

from PySide6.QtWidgets import QApplication

from src.Backend.goes_overlay import GoesOverlayPayload
from src.UI.gui_main import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _make_goes_payload():
    return GoesOverlayPayload(
        start_utc=datetime(2026, 2, 10, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 10, 0, 1, tzinfo=timezone.utc),
        base_utc=datetime(2026, 2, 10, 0, 0, tzinfo=timezone.utc),
        satellite_number=17,
        satellite_numbers=(17,),
        series={
            "xrsa": {
                "channel_key": "xrsa",
                "display_label": "Short(XRS-A)",
                "channel_label": "xrsa_short",
                "satellite_number": 17,
                "x_seconds": np.array([0.0, 60.0], dtype=float),
                "flux_wm2": np.array([8e-9, 1e-8], dtype=float),
            },
            "xrsb": {
                "channel_key": "xrsb",
                "display_label": "Long(XRS-B)",
                "channel_label": "xrsb_long",
                "satellite_number": 17,
                "x_seconds": np.array([0.0, 60.0], dtype=float),
                "flux_wm2": np.array([1e-8, 2e-8], dtype=float),
            },
        },
        x_seconds=np.array([0.0, 60.0], dtype=float),
        flux_wm2=np.array([1e-8, 2e-8], dtype=float),
        channel_label="xrsb_long",
    )


def test_main_window_hw_annotation_actions_keep_accel_canvas(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    if not window.accel_canvas.is_available:
        pytest.skip("pyqtgraph not available in test environment")

    window.raw_data = np.zeros((2, 2), dtype=np.float32)
    window.use_hw_live_preview = True

    window._show_plot_canvas()
    window.start_annotation_line()
    assert window.plot_stack.currentWidget() is window.accel_canvas
    assert window._annotation_mode == "line"

    monkeypatch.setattr(
        window,
        "_open_text_annotation_dialog",
        lambda **_k: {
            "text": "Label",
            "color": "#ffaa00",
            "font_family": "Helvetica",
            "font_size": 15,
            "font_bold": True,
            "font_italic": False,
        },
    )
    window._show_plot_canvas()
    window.start_annotation_text()
    assert window.plot_stack.currentWidget() is window.accel_canvas
    assert window._annotation_mode == "text"
    assert window._annotation_pending_text == "Label"
    assert window._annotation_pending_text_style["font_family"] == "Helvetica"

    window.close()


def test_main_window_hw_annotation_finish_adds_annotation():
    _app()
    window = MainWindow(theme=None)
    if not window.accel_canvas.is_available:
        pytest.skip("pyqtgraph not available in test environment")

    window.raw_data = np.zeros((2, 2), dtype=np.float32)
    window.use_hw_live_preview = True
    window._annotation_mode = "line"

    window._on_accel_annotation_capture_finished("line", [(1.0, 2.0), (3.0, 4.0)])

    assert len(window._annotations) == 1
    assert window._annotations[0]["kind"] == "line"
    assert window._annotations[0]["points"] == [[1.0, 2.0], [3.0, 4.0]]
    assert window._annotation_mode is None

    window.close()


def test_edit_text_label_updates_style(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    window._annotations = [
        {
            "id": "ann1",
            "kind": "text",
            "points": [[4.0, 5.0]],
            "text": "Old",
            "color": "#00d4ff",
            "font_family": "",
            "font_size": 12,
            "font_bold": False,
            "font_italic": False,
            "line_width": 1.5,
            "visible": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    monkeypatch.setattr(window, "_choose_text_annotation_index", lambda **_k: 0)
    monkeypatch.setattr(
        window,
        "_open_text_annotation_dialog",
        lambda **_k: {
            "text": "New",
            "color": "#ff5500",
            "font_family": "Arial",
            "font_size": 18,
            "font_bold": True,
            "font_italic": True,
        },
    )

    window.edit_text_label()

    ann = window._annotations[0]
    assert ann["text"] == "New"
    assert ann["color"] == "#ff5500"
    assert ann["font_family"] == "Arial"
    assert ann["font_size"] == 18
    assert ann["font_bold"] is True
    assert ann["font_italic"] is True
    window.close()


def test_move_text_label_repositions_selected_label():
    _app()
    window = MainWindow(theme=None)
    window._annotations = [
        {
            "id": "ann1",
            "kind": "text",
            "points": [[1.0, 2.0]],
            "text": "Label",
            "color": "#00d4ff",
            "font_family": "",
            "font_size": 12,
            "font_bold": False,
            "font_italic": False,
            "line_width": 1.5,
            "visible": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    window._annotation_target_index = 0

    window._move_selected_text_annotation_to((8.0, 9.0))

    assert window._annotations[0]["points"] == [[8.0, 9.0]]
    assert window._annotation_mode is None
    window.close()


def test_goes_overlay_submenu_is_present_in_solar_events_menu():
    _app()
    window = MainWindow(theme=None)

    solar_menu = None
    for action in window.menuBar().actions():
        if action.text() == "Solar Events":
            solar_menu = action.menu()
            break

    assert solar_menu is not None
    actions = [action for action in solar_menu.actions() if not action.isSeparator()]
    assert actions[-2].text() == "Sync Current Time Window"
    assert actions[-1].text() == "GOES Overlay"
    goes_menu = actions[-1].menu()
    assert goes_menu is not None
    goes_actions = [action for action in goes_menu.actions() if not action.isSeparator()]
    assert [action.text() for action in goes_actions] == ["Short(XRS-A)", "Long(XRS-B)"]
    assert all(action.isCheckable() for action in goes_actions)
    window.close()


def test_goes_overlay_toggle_reverts_without_utc_context():
    _app()
    window = MainWindow(theme=None)
    window.raw_data = np.zeros((2, 2), dtype=np.float32)
    window.goes_overlay_long_action.setChecked(True)

    assert window.goes_overlay_long_action.isChecked() is False
    assert window.goes_overlay_short_action.isChecked() is False
    assert window._goes_overlay_enabled is False
    window.close()


def test_goes_overlay_toggle_with_mocked_success_keeps_spectrogram_data(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    window.raw_data = np.arange(4, dtype=np.float32).reshape(2, 2)
    window.freqs = np.array([40.0, 41.0], dtype=float)
    window.time = np.array([0.0, 60.0], dtype=float)
    window.ut_start_sec = 0.0
    window._fits_header0 = {"DATE-OBS": "2026-02-10T00:00:00Z"}
    window.filename = "test.fit"

    before = window.raw_data.copy()
    monkeypatch.setattr(window, "_render_goes_overlay", lambda: None)

    def fake_start(ctx):
        payload = GoesOverlayPayload(
            start_utc=ctx["start_utc"],
            end_utc=ctx["end_utc"],
            base_utc=ctx["base_utc"],
            satellite_number=16,
            satellite_numbers=(16,),
            series={
                "xrsa": {
                    "channel_key": "xrsa",
                    "display_label": "Short(XRS-A)",
                    "channel_label": "xrsa_short",
                    "satellite_number": 16,
                    "x_seconds": np.array([0.0, 30.0, 60.0], dtype=float),
                    "flux_wm2": np.array([7e-9, 1e-8, 2e-8], dtype=float),
                },
                "xrsb": {
                    "channel_key": "xrsb",
                    "display_label": "Long(XRS-B)",
                    "channel_label": "xrsb_long",
                    "satellite_number": 16,
                    "x_seconds": np.array([0.0, 30.0, 60.0], dtype=float),
                    "flux_wm2": np.array([1e-8, 2e-8, 4e-8], dtype=float),
                },
            },
            x_seconds=np.array([0.0, 30.0, 60.0], dtype=float),
            flux_wm2=np.array([1e-8, 2e-8, 4e-8], dtype=float),
            channel_label="xrsb_long",
        )
        window._on_goes_overlay_finished(ctx["request_key"], payload)

    monkeypatch.setattr(window, "_start_goes_overlay_request", fake_start)
    window.goes_overlay_long_action.setChecked(True)

    assert window.goes_overlay_long_action.isChecked() is True
    assert window._goes_overlay_enabled is True
    assert window._goes_overlay_payload is not None
    assert np.array_equal(window.raw_data, before)
    assert window.noise_reduced_data is None
    window.close()


def test_goes_overlay_request_context_uses_legacy_satellites_for_historic_data():
    _app()
    window = MainWindow(theme=None)
    window.time = np.array([0.0, 60.0], dtype=float)
    window.ut_start_sec = 0.0
    window._fits_header0 = {"DATE-OBS": "2015-03-11T00:00:00Z"}

    ctx = window._goes_overlay_request_context()

    assert ctx is not None
    assert ctx["satellite_numbers"] == (15, 14, 13, 12, 11, 10)
    window.close()


def test_goes_overlay_status_bar_includes_peak_time_for_overlay_axis_hover():
    _app()
    window = MainWindow(theme=None)
    window.use_hw_live_preview = False
    window.filename = "demo.fit"
    window.freqs = np.array([100.0, 95.0, 90.0], dtype=float)
    window.time = np.array([0.0, 60.0, 120.0], dtype=float)
    window.raw_data = np.array(
        [
            [1.0, 2.0, 3.0],
            [4.0, 5.0, 6.0],
            [7.0, 8.0, 9.0],
        ],
        dtype=np.float32,
    )

    window.plot_data(window.raw_data, title="Raw")
    QApplication.processEvents()

    window._goes_overlay_payload = _make_goes_payload()
    window._goes_overlay_enabled = True
    window._set_goes_overlay_checked(("xrsb",))
    window._render_goes_overlay()
    window.canvas.draw()

    overlay_ax = window._goes_overlay_mpl_ax
    assert overlay_ax is not None

    hover_px = window.canvas.ax.transData.transform((60.0, 95.0))
    event = SimpleNamespace(
        inaxes=overlay_ax,
        x=float(hover_px[0]),
        y=float(hover_px[1]),
        xdata=60.0,
        ydata=2.0e-8,
    )

    window.on_mouse_motion_status(event)

    text = window.cursor_label.text()
    assert "t = 60.00 s" in text
    assert "Long(XRS-B) peak = 00:01:00 UT (60.00 s)" in text
    window.close()


def test_apply_loaded_dataset_invalidates_goes_overlay_and_requests_refresh(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    window._set_goes_overlay_checked(("xrsa",))
    window._goes_overlay_enabled = True
    window._goes_overlay_payload = _make_goes_payload()
    window._goes_overlay_payload_key = "old"

    calls = []
    cancelled = []
    monkeypatch.setattr(window, "plot_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_render_goes_overlay", lambda: None)
    monkeypatch.setattr(window, "_cancel_goes_overlay_request", lambda: cancelled.append(True))
    monkeypatch.setattr(
        window,
        "_ensure_goes_overlay_for_current_data",
        lambda force=False: calls.append(bool(force)) or True,
    )

    window._apply_loaded_dataset(
        data=np.zeros((2, 2), dtype=np.float32),
        freqs=np.array([40.0, 41.0], dtype=float),
        time=np.array([0.0, 120.0], dtype=float),
        filename="next.fit",
        header0={"DATE-OBS": "2026-02-11T00:00:00Z"},
        ut_start_sec=0.0,
    )
    QApplication.processEvents()

    assert window._goes_overlay_payload is None
    assert window._goes_overlay_payload_key is None
    assert cancelled == [True]
    assert calls == [True]
    window.close()


def test_goes_overlay_toggle_off_clears_renderers(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    window._goes_overlay_enabled = True
    window._goes_overlay_payload = _make_goes_payload()
    window._set_goes_overlay_checked(("xrsa", "xrsb"))

    cleared = {"mpl": 0, "hw": 0}
    monkeypatch.setattr(window, "_remove_goes_overlay_mpl_axis", lambda: cleared.__setitem__("mpl", cleared["mpl"] + 1))
    monkeypatch.setattr(window.accel_canvas, "clear_goes_overlay", lambda: cleared.__setitem__("hw", cleared["hw"] + 1))

    window.goes_overlay_long_action.setChecked(False)
    window.goes_overlay_short_action.setChecked(False)

    assert window._goes_overlay_enabled is False
    assert cleared["mpl"] >= 1
    assert cleared["hw"] >= 1
    window.close()


def test_rectangular_zoom_accepts_goes_overlay_axis_events():
    _app()
    window = MainWindow(theme=None)
    window.use_hw_live_preview = False
    window.filename = "demo.fit"
    window.freqs = np.array([100.0, 95.0, 90.0], dtype=float)
    window.time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    window.raw_data = np.array(
        [
            [1.0, 2.0, 3.0, 4.0],
            [2.0, 3.0, 4.0, 5.0],
            [3.0, 4.0, 5.0, 6.0],
        ],
        dtype=np.float32,
    )

    window.plot_data(window.raw_data, title="Raw")
    QApplication.processEvents()

    window._goes_overlay_payload = _make_goes_payload()
    window._goes_overlay_enabled = True
    window._set_goes_overlay_checked(("xrsa", "xrsb"))
    window._render_goes_overlay()
    window.canvas.draw()

    overlay_ax = window._goes_overlay_mpl_ax
    assert overlay_ax is not None

    start_px = window.canvas.ax.transData.transform((0.5, 99.0))
    end_px = window.canvas.ax.transData.transform((2.5, 91.0))
    eclick = SimpleNamespace(inaxes=overlay_ax, x=float(start_px[0]), y=float(start_px[1]), xdata=None, ydata=None)
    erelease = SimpleNamespace(inaxes=overlay_ax, x=float(end_px[0]), y=float(end_px[1]), xdata=None, ydata=None)

    window.rect_zoom_active = True
    window._on_rect_zoom_select(eclick, erelease)

    assert window.canvas.ax.get_xlim() == pytest.approx((0.5, 2.5))
    assert window.canvas.ax.get_ylim() == pytest.approx((91.0, 99.0))
    assert window.rect_zoom_active is False
    window.close()


def test_rectangular_zoom_temporarily_suspends_mpl_goes_overlay():
    _app()
    window = MainWindow(theme=None)
    window.use_hw_live_preview = False
    window.filename = "demo.fit"
    window.freqs = np.array([100.0, 95.0, 90.0], dtype=float)
    window.time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    window.raw_data = np.array(
        [
            [1.0, 2.0, 3.0, 4.0],
            [2.0, 3.0, 4.0, 5.0],
            [3.0, 4.0, 5.0, 6.0],
        ],
        dtype=np.float32,
    )

    window.plot_data(window.raw_data, title="Raw")
    QApplication.processEvents()

    window._goes_overlay_payload = _make_goes_payload()
    window._goes_overlay_enabled = True
    window._set_goes_overlay_checked(("xrsa", "xrsb"))
    window._render_goes_overlay()
    window.canvas.draw()

    assert window._goes_overlay_mpl_ax is not None

    window.nav_locked = True
    window.rectangular_zoom()

    assert window._rect_selector is not None
    assert window._goes_overlay_mpl_ax is None
    assert window._rect_zoom_restore_goes_overlay is True

    start_px = window.canvas.ax.transData.transform((0.5, 99.0))
    end_px = window.canvas.ax.transData.transform((2.5, 91.0))
    eclick = SimpleNamespace(inaxes=window.canvas.ax, x=float(start_px[0]), y=float(start_px[1]), xdata=None, ydata=None)
    erelease = SimpleNamespace(inaxes=window.canvas.ax, x=float(end_px[0]), y=float(end_px[1]), xdata=None, ydata=None)
    window._on_rect_zoom_select(eclick, erelease)

    assert window._goes_overlay_mpl_ax is not None
    assert window._rect_zoom_restore_goes_overlay is False
    assert window.canvas.ax.get_xlim() == pytest.approx((0.5, 2.5))
    assert window.canvas.ax.get_ylim() == pytest.approx((91.0, 99.0))
    window.close()
