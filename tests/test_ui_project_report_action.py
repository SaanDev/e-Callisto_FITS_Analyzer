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

    def fake_dynamic_renderer(data, *, title, overlays=None):
        render_calls.append((title, overlays))
        return _solid_png()

    monkeypatch.setattr(win, "_render_dynamic_spectrum_report_png", fake_dynamic_renderer)
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
    assert [call[0] for call in render_calls] == titles[:4]
    assert render_calls[-1][1]
    win.close()


def test_dynamic_spectrum_report_renderer_produces_nonblack_png():
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

    png = win._render_dynamic_spectrum_report_png(data, title="Raw Dynamic Spectrum")
    black_png = _solid_png("#000000")

    assert png.startswith(b"\x89PNG")
    assert win._png_is_blank_or_black(png) is False
    assert win._png_is_blank_or_black(black_png) is True
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
