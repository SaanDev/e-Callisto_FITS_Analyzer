"""
e-CALLISTO FITS Analyzer
Version 2.4.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

import numpy as np
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import src.UI.main_window as main_window_module
from src.Backend.project_report import ProjectReportFigure
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _find_menu_action(menu_bar, text: str):
    for menu_action in menu_bar.actions():
        menu = menu_action.menu()
        if menu is None:
            continue
        for action in menu.actions():
            if action.text() == text:
                return action
    return None


def _solid_png(color: str = "#1f60c4") -> bytes:
    image = QImage(16, 16, QImage.Format_RGB32)
    image.fill(QColor(color))
    return MainWindow._qimage_to_png_bytes(image)


def test_file_menu_exposes_project_report_action_and_enablement():
    _app()
    win = MainWindow(theme=None)

    action = _find_menu_action(win.menuBar(), "Generate Project Report...")
    assert action is win.generate_project_report_action
    assert action.isEnabled() is False

    win.raw_data = np.ones((2, 3), dtype=float)
    win.freqs = np.array([90.0, 80.0])
    win.time = np.array([0.0, 1.0, 2.0])
    win.filename = "demo.fit"
    win._sync_project_actions()

    assert win.generate_project_report_action.isEnabled() is True
    win.close()


def test_generate_project_report_without_data_shows_message(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    messages = []
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: messages.append(args))

    win.generate_project_report()

    assert messages
    assert messages[0][1] == "Generate Project Report"
    assert "Load a FITS file first" in messages[0][2]
    win.close()


def test_pick_project_report_path_appends_pdf_extension(monkeypatch, tmp_path):
    _app()
    win = MainWindow(theme=None)
    target = tmp_path / "report_without_ext"
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "PDF (*.pdf)"),
    )

    assert win._pick_project_report_path() == f"{target}.pdf"
    win.close()


def test_project_report_default_filename_uses_original_sources(tmp_path):
    _app()
    win = MainWindow(theme=None)
    win.filename = "Graph Title That Should Not Be Used"
    win._project_path = str(tmp_path / "saved_project.efaproj")
    win._combined_sources = [
        str(tmp_path / "CALLISTO_A_20260101.fit"),
        str(tmp_path / "CALLISTO_B_20260101.fit"),
    ]

    default_path = win._project_report_default_path()

    assert default_path.startswith(str(tmp_path))
    assert default_path.endswith("CALLISTO_A_20260101_CALLISTO_B_20260101_project_report.pdf")
    win.close()


def test_project_report_figures_use_explicit_dynamic_spectrum_titles(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    base = np.linspace(1.0, 40.0, 24, dtype=float).reshape(4, 6)
    win.raw_data = base
    win.noise_reduced_original = base + 10.0
    win.noise_reduced_data = base + 25.0
    win.current_plot_type = "Isolated Burst"
    win.freqs = np.array([90.0, 84.0, 78.0, 72.0], dtype=float)
    win.time = np.arange(6, dtype=float)
    win.filename = "demo.fit"
    win._light_curve_records = [{"frequency_mhz": 84.0, "requested_mhz": 84.0}]

    render_calls = []

    def fake_dynamic_renderer(data, *, plot_type=None, overlays=None, view=None):
        render_calls.append((plot_type, data, overlays, view))
        return _solid_png()

    monkeypatch.setattr(win, "_render_original_dynamic_spectrum_export_png", fake_dynamic_renderer)
    monkeypatch.setattr(
        win,
        "_capture_max_intensity_fit_report_figure",
        lambda _session: ProjectReportFigure(title="Maximum Intensity Fit", availability_note="Not available"),
    )
    monkeypatch.setattr(
        win,
        "_capture_type_ii_report_figure",
        lambda _session: ProjectReportFigure(title="Type II Band Splitting", availability_note="Not available"),
    )

    figures = win._build_project_report_figures({})
    titles = [figure.title for figure in figures]

    assert titles[:4] == [
        "Raw Dynamic Spectrum",
        "Background Subtracted Dynamic Spectrum",
        "Burst Isolated Dynamic Spectrum",
        "Light Curves With Dynamic Spectrum",
    ]
    assert "Current Main View" not in titles
    assert "GOES X-Ray Overlay" not in titles
    assert [call[0] for call in render_calls] == ["Raw", "Background Subtracted", "Isolated Burst", "Isolated Burst"]
    assert np.array_equal(render_calls[1][1], win.noise_reduced_original)
    assert np.array_equal(render_calls[2][1], win.noise_reduced_data)
    assert render_calls[-1][2]
    win.close()


def test_project_report_reconstructs_lasso_isolated_burst_when_title_changed(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    base = np.arange(24, dtype=float).reshape(4, 6)
    mask = np.zeros_like(base, dtype=bool)
    mask[1:3, 2:5] = True
    win.raw_data = base + 100.0
    win.noise_reduced_original = base + 10.0
    win.noise_reduced_data = base + 25.0
    win.lasso_mask = mask
    win.current_plot_type = "Background Subtracted"
    win.freqs = np.array([90.0, 84.0, 78.0, 72.0], dtype=float)
    win.time = np.arange(6, dtype=float)
    win.filename = "demo.fit"

    render_calls = {}

    def fake_dynamic_renderer(data, *, plot_type=None, overlays=None, view=None):
        render_calls[plot_type] = np.asarray(data)
        return _solid_png()

    monkeypatch.setattr(win, "_render_original_dynamic_spectrum_export_png", fake_dynamic_renderer)
    monkeypatch.setattr(
        win,
        "_capture_max_intensity_fit_report_figure",
        lambda _session: ProjectReportFigure(title="Maximum Intensity Fit", availability_note="Not available"),
    )
    monkeypatch.setattr(
        win,
        "_capture_type_ii_report_figure",
        lambda _session: ProjectReportFigure(title="Type II Band Splitting", availability_note="Not available"),
    )

    figures = win._build_project_report_figures({})
    isolated = next(figure for figure in figures if figure.title == "Burst Isolated Dynamic Spectrum")
    isolated_data = render_calls["Isolated Burst"]

    assert isolated.png_bytes.startswith(b"\x89PNG")
    assert np.array_equal(render_calls["Background Subtracted"], win.noise_reduced_original)
    assert np.array_equal(isolated_data[mask], win.noise_reduced_original[mask])
    assert np.all(isolated_data[~mask] == 0.0)
    win.close()


def test_project_report_uses_stored_isolated_burst_workflow_state(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    base = np.arange(24, dtype=float).reshape(4, 6)
    mask = np.zeros_like(base, dtype=bool)
    mask[1:3, 2:5] = True
    stored_isolated = np.zeros_like(base, dtype=float)
    stored_isolated[mask] = base[mask] + 25.0

    win.raw_data = base + 100.0
    win.noise_reduced_original = base + 10.0
    win.noise_reduced_data = stored_isolated
    win.lasso_mask = mask
    win.current_plot_type = "Isolated Burst"
    win.freqs = np.array([90.0, 84.0, 78.0, 72.0], dtype=float)
    win.time = np.arange(6, dtype=float)
    win.filename = "demo.fit"

    render_calls = {}

    def fake_dynamic_renderer(data, *, plot_type=None, overlays=None, view=None):
        render_calls[plot_type] = np.asarray(data)
        return _solid_png()

    monkeypatch.setattr(win, "_render_original_dynamic_spectrum_export_png", fake_dynamic_renderer)
    monkeypatch.setattr(
        win,
        "_capture_max_intensity_fit_report_figure",
        lambda _session: ProjectReportFigure(title="Maximum Intensity Fit", availability_note="Not available"),
    )
    monkeypatch.setattr(
        win,
        "_capture_type_ii_report_figure",
        lambda _session: ProjectReportFigure(title="Type II Band Splitting", availability_note="Not available"),
    )

    win._build_project_report_figures({})

    assert np.array_equal(render_calls["Isolated Burst"], stored_isolated)
    assert not np.array_equal(render_calls["Isolated Burst"][mask], win.noise_reduced_original[mask])
    win.close()


def test_original_dynamic_spectrum_export_ignores_stale_default_view():
    _app()
    win = MainWindow(theme=None)
    extent = (0.0, 1800.0, 15.0, 87.0)

    stale = win._dynamic_spectrum_view_for_extent({"xlim": (0.0, 1.0), "ylim": (0.0, 1.0)}, extent)
    valid = win._dynamic_spectrum_view_for_extent({"xlim": (100.0, 200.0), "ylim": (20.0, 60.0)}, extent)

    assert stale is None
    assert valid == {"xlim": (100.0, 200.0), "ylim": (20.0, 60.0)}
    win.close()


def test_original_dynamic_spectrum_export_renderer_produces_nonblack_png():
    pytest.importorskip("pyqtgraph")
    _app()
    win = MainWindow(theme=None)
    win.freqs = np.array([90.0, 84.0, 78.0, 72.0], dtype=float)
    win.time = np.arange(6, dtype=float)
    win._gap_row_mask = np.array([False, True, False, False])
    data = np.array(
        [
            [1.0, 3.0, 6.0, 8.0, 10.0, 12.0],
            [np.nan, np.nan, np.nan, np.nan, np.nan, np.nan],
            [2.0, 4.0, 8.0, 16.0, 20.0, 28.0],
            [30.0, 26.0, 22.0, 18.0, 12.0, 8.0],
        ],
        dtype=float,
    )

    png = win._render_original_dynamic_spectrum_export_png(data, plot_type="Raw")
    black_png = _solid_png("#000000")

    assert png.startswith(b"\x89PNG")
    assert win._png_is_blank_or_black(png) is False
    assert win._png_is_blank_or_black(black_png) is True
    win.close()


def test_original_dynamic_spectrum_export_renderer_preserves_mid_spectrum_content():
    pytest.importorskip("pyqtgraph")
    _app()
    win = MainWindow(theme=None)
    win.freqs = np.linspace(90.0, 20.0, 64)
    win.time = np.linspace(0.0, 1800.0, 240)
    data = np.zeros((64, 240), dtype=float)
    data[18:46, 110:175] = np.linspace(5.0, 60.0, 65)[None, :]

    png = win._render_original_dynamic_spectrum_export_png(data, plot_type="Raw")

    image = QImage()
    assert image.loadFromData(png, "PNG")
    rgba = win._qimage_to_rgba_array(image.convertToFormat(QImage.Format_RGBA8888))
    h, w = rgba.shape[:2]
    center = rgba[int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75), :3]
    assert float(np.std(center)) > 10.0
    assert int(np.max(center)) - int(np.min(center)) > 50
    win.close()


def test_original_dynamic_spectrum_export_renderer_uses_app_export_widget(monkeypatch):
    pytest.importorskip("pyqtgraph")
    import pyqtgraph.exporters as pg_exporters

    _app()
    win = MainWindow(theme=None)
    win.filename = "demo.fit"
    win.freqs = np.linspace(82.0, 45.0, 24)
    win.time = np.linspace(0.0, 900.0, 36)
    data = np.zeros((24, 36), dtype=float)
    data[8:16, 12:24] = 10.0
    monkeypatch.setattr(win, "_is_dark_ui", lambda: True)
    monkeypatch.setattr(win, "_report_spectrum_export_geometry", lambda: (640, 360, 1800))

    class FakeWidget:
        is_available = True

        def __init__(self):
            self.dark = None
            self.updated = None
            self.overlays = None

        def setAttribute(self, *_args):
            pass

        def resize(self, *_args):
            pass

        def set_dark(self, value):
            self.dark = value

        def set_time_mode(self, *_args):
            pass

        def set_navigation_locked(self, *_args):
            pass

        def set_text_style(self, **_kwargs):
            pass

        def update_image(self, data, **kwargs):
            self.updated = {"data": np.asarray(data), **kwargs}

        def set_goes_overlay(self, *_args, **_kwargs):
            pass

        def set_light_curve_overlays(self, overlays):
            self.overlays = overlays

        def show(self):
            pass

        def export_plot_item(self):
            return object()

        def close(self):
            pass

        def deleteLater(self):
            pass

    fake_widget = FakeWidget()

    class FakeExporter:
        def __init__(self, plot_item):
            self.plot_item = plot_item
            self.params = {}

        def parameters(self):
            return self.params

        def export(self, *, toBytes=False):
            assert toBytes is True
            assert self.params["width"] == 1800
            image = QImage(16, 16, QImage.Format_RGB32)
            image.fill(QColor("#1f60c4"))
            return image

    monkeypatch.setattr(main_window_module, "AcceleratedPlotWidget", lambda **_kwargs: fake_widget)
    monkeypatch.setattr(pg_exporters, "ImageExporter", FakeExporter)

    png = win._render_original_dynamic_spectrum_export_png(data, plot_type="Raw")

    assert png.startswith(b"\x89PNG")
    assert fake_widget.dark is True
    assert fake_widget.updated["title"] == "demo.fit-Raw"
    assert fake_widget.updated["x_label"] == "Time [s]"
    assert fake_widget.updated["y_label"] == "Frequency [MHz]"
    assert fake_widget.updated["colorbar_label"] == "Intensity [Digits]"
    assert fake_widget.updated["levels"] == (0.0, 10.0)
    assert np.array_equal(fake_widget.updated["data"], data.astype(np.float32))
    win.close()


def test_report_dynamic_spectrum_levels_clip_display_outliers():
    _app()
    win = MainWindow(theme=None)
    data = np.full((20, 50), 100.0, dtype=float)
    data[6:14, 16:34] = 140.0
    data[0, 0] = 1000.0

    levels = win._report_levels_for_spectrum_data(data, data)

    assert levels is not None
    assert levels[0] <= 100.0
    assert 120.0 <= levels[1] < 1000.0
    win.close()


def test_report_isolated_burst_levels_use_stored_isolated_data_limits():
    _app()
    win = MainWindow(theme=None)
    win.noise_vmin = 0.0
    win.noise_vmax = 64.0
    data = np.zeros((20, 50), dtype=float)
    data[6:14, 16:34] = 50.0

    levels = win._report_levels_for_spectrum_data(data, data, plot_type="Isolated Burst")

    assert levels == (0.0, 50.0)
    win.close()


def test_type_ii_report_uses_pyqtgraph_export_path(monkeypatch):
    _app()
    win = MainWindow(theme=None)

    class FakeTypeIIDialog:
        def __init__(self):
            self.called = False

        def _render_export_image(self, min_width=2400):
            self.called = True
            image = QImage(32, 32, QImage.Format_RGB32)
            image.fill(QColor("#234abc"))
            return image

    fake_dialog = FakeTypeIIDialog()
    win._type_ii_dialog = fake_dialog
    monkeypatch.setattr(win, "_dialog_alive", lambda dialog: dialog is fake_dialog)

    figure = win._capture_type_ii_report_figure(
        {
            "type_ii": {
                "upper": {"time_seconds": [1.0, 2.0], "freqs": [95.0, 90.0]},
                "lower": {"time_seconds": [1.0, 2.0], "freqs": [75.0, 70.0]},
            }
        }
    )

    assert fake_dialog.called is True
    assert figure.title == "Type II Band Splitting"
    assert figure.png_bytes.startswith(b"\x89PNG")
    win.close()


def test_goes_payload_report_uses_matplotlib_renderer():
    _app()
    win = MainWindow(theme=None)
    win._set_goes_overlay_checked(("xrsa", "xrsb"))
    win._goes_overlay_payload = {
        "series": {
            "xrsa": {
                "x_seconds": [0.0, 10.0, 20.0],
                "flux_wm2": [1e-7, 2e-7, 1.5e-7],
                "display_label": "GOES 1-8 A",
            },
            "xrsb": {
                "x_seconds": [0.0, 10.0, 20.0],
                "flux_wm2": [1e-6, 2e-6, 1.5e-6],
                "display_label": "GOES 0.5-4 A",
            },
        }
    }

    figure = win._capture_goes_payload_report_figure()

    assert figure.png_bytes.startswith(b"\x89PNG")
    assert "Matplotlib" in figure.caption
    win.close()
