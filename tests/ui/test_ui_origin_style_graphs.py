"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

OriginPro-style graphs in the radio dialogs: the Analyzer draws its best fit in
the Origin style on screen (the other Analyzer plots keep their look), and every
figure the Analyzer, Maximum Intensities and Type II windows save, like the Type
II figure in the project report, is an Origin graph on a white page.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from matplotlib.colors import to_hex
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import src.ui.radio.dialogs.analyze_dialog as analyze_module
import src.ui.radio.dialogs.max_intensity_dialog as max_module
import src.ui.radio.dialogs.type_ii_band_splitting_dialog as type_ii_module
from src.backend.common.figure_export import FIGURE_EXPORT_FILTERS, ORIGIN_FIT_COLOR, origin_font_families
from src.ui.radio.dialogs.analyze_dialog import AnalyzeDialog
from src.ui.radio.dialogs.max_intensity_dialog import MaxIntensityPlotDialog
from src.ui.radio.dialogs.type_ii_band_splitting_dialog import TypeIIBandSplittingDialog


def _app():
    return QApplication.instance() or QApplication([])


class _SaveSpy:
    """Stands in for a dialog's file picker, message boxes and save_figure."""

    def __init__(self, module, monkeypatch, path: Path):
        self.path = path
        self.filters = None
        self.figures = []
        self.messages = []
        real_save = module.save_figure

        def pick(_parent, _caption, _name, filters, default_filter=None):
            self.filters = filters
            return str(path), path.suffix.lstrip(".")

        def save(fig, target, **kwargs):
            self.figures.append(fig)
            return real_save(fig, target, **kwargs)

        monkeypatch.setattr(module, "pick_export_path", pick)
        monkeypatch.setattr(module, "save_figure", save)
        for kind in ("information", "critical", "warning"):
            monkeypatch.setattr(module.QMessageBox, kind, lambda *args, _kind=kind, **kwargs: self.messages.append((_kind, args[2])))

    def assert_saved_white_page(self):
        assert [kind for kind, _text in self.messages] == ["information"]
        assert self.filters == FIGURE_EXPORT_FILTERS
        image = QImage(str(self.path))
        assert not image.isNull()
        assert image.pixelColor(0, 0) == QColor("#ffffff")
        return self.figures[-1].axes[0]


def _assert_origin_points(ax, *, legend: bool) -> None:
    data_line = ax.get_lines()[0]
    assert data_line.get_marker() == "s" and to_hex(data_line.get_color()) == "#000000"
    for spine in ax.spines.values():
        assert spine.get_visible() and to_hex(spine.get_edgecolor()) == "#000000"
    assert ax.xaxis.get_tick_params()["top"] is True
    assert (ax.get_legend() is not None) is legend


def _analyzer() -> AnalyzeDialog:
    time_s = np.arange(1.0, 30.0)
    return AnalyzeDialog(time_s / 0.25, 90.0 * time_s ** -0.45, "demo.fit", time_seconds=time_s)


def test_analyzer_best_fit_is_drawn_in_the_origin_style():
    _app()
    dlg = _analyzer()

    dlg.plot_fit()

    ax = dlg.canvas.ax
    data_line, fit_line = ax.get_lines()
    assert data_line.get_marker() == "s" and data_line.get_linestyle() == "None"
    assert to_hex(data_line.get_color()) == "#000000"
    assert to_hex(fit_line.get_color()) == ORIGIN_FIT_COLOR.lower()
    for spine in ax.spines.values():
        assert spine.get_visible() and to_hex(spine.get_edgecolor()) == "#000000"
    assert ax.xaxis.get_tick_params()["direction"] == "in"
    assert ax.xaxis.get_tick_params()["top"] is True
    assert not any(line.get_visible() for line in ax.get_xgridlines())
    legend = ax.get_legend()
    assert to_hex(legend.get_frame().get_edgecolor()) == "#000000"
    assert [text.get_text() for text in legend.get_texts()][0] == "Original Data"
    assert ax.get_title() == "demo_Best_Fit"
    assert ax.title.get_fontfamily()[0] == origin_font_families()[0]
    assert to_hex(dlg.canvas.figure.get_facecolor()) == "#ffffff"
    dlg.close()


def test_analyzer_restored_fit_is_drawn_in_the_origin_style():
    _app()
    dlg = _analyzer()
    dlg.plot_fit()
    state = dlg.session_state()
    dlg.close()

    restored = _analyzer()
    restored.restore_session(state, emit_change=False)

    assert restored.canvas.ax.get_lines()[0].get_marker() == "s"
    restored.close()


def test_analyzer_maximum_intensity_view_keeps_its_look():
    _app()
    dlg = _analyzer()
    dlg.plot_fit()

    dlg.plot_max()

    ax = dlg.canvas.ax
    assert not ax.get_lines()
    assert ax.get_legend() is None
    assert to_hex(ax.collections[0].get_facecolor()[0]) == "#0000ff"
    assert any(line.get_visible() for line in ax.get_xgridlines())
    dlg.close()


def _type_ii_dialog(**kwargs) -> TypeIIBandSplittingDialog:
    data = np.arange(20, dtype=float).reshape(4, 5)
    freqs = np.array([120.0, 110.0, 100.0, 90.0], dtype=float)
    times = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float)
    dlg = TypeIIBandSplittingDialog(data, freqs, times, "demo.fit", **kwargs)
    dlg._upper_points = [(1.0, 118.0), (2.0, 110.0), (3.0, 104.0)]
    dlg._lower_points = [(1.0, 108.0), (2.0, 100.0), (3.0, 95.0)]
    dlg._fit_both_bands()
    return dlg


def test_type_ii_origin_figure_draws_both_lanes_in_their_colours():
    _app()
    dlg = _type_ii_dialog()

    fig = dlg.origin_figure()

    ax = fig.axes[0]
    labels = [text.get_text() for text in ax.get_legend().get_texts()]
    assert labels == ["Upper band", "Upper band fit", "Lower band", "Lower band fit"]
    upper, upper_fit, _lower, _lower_fit = ax.get_lines()
    style = dlg._current_plot_style
    assert to_hex(upper.get_markerfacecolor()) == style["upper_marker_color"]
    assert to_hex(upper_fit.get_color()) == style["upper_line_color"]
    assert ax.get_title() == "demo_Type_II_Band_Splitting"
    assert ax.images and ax.images[0].get_extent()[2] < ax.images[0].get_extent()[3]
    assert all(spine.get_visible() for spine in ax.spines.values())
    assert any(getattr(axes, "_colorbar", None) is not None for axes in fig.axes)
    dlg.close()


# --- Saved figures ---------------------------------------------------------------


def test_analyzer_save_graph_writes_the_best_fit_as_an_origin_figure(tmp_path, monkeypatch):
    _app()
    dlg = _analyzer()
    dlg.plot_fit()
    spy = _SaveSpy(analyze_module, monkeypatch, tmp_path / "fit.png")

    dlg.save_graph()

    ax = spy.assert_saved_white_page()
    _assert_origin_points(ax, legend=True)
    assert to_hex(ax.get_lines()[1].get_color()) == ORIGIN_FIT_COLOR.lower()
    assert ax.get_title() == "demo_Best_Fit"
    dlg.close()


def test_analyzer_save_graph_turns_the_other_plots_origin_style_only_in_the_file(tmp_path, monkeypatch):
    _app()
    dlg = _analyzer()
    dlg.plot_fit()
    dlg.extra_plot_combo.setCurrentText("Shock Speed vs Frequency")
    dlg.plot_extra()
    spy = _SaveSpy(analyze_module, monkeypatch, tmp_path / "extra.pdf")

    dlg.save_graph()

    assert [kind for kind, _text in spy.messages] == ["information"]
    assert (tmp_path / "extra.pdf").read_bytes().startswith(b"%PDF")
    ax = spy.figures[-1].axes[0]
    _assert_origin_points(ax, legend=False)
    assert ax.get_title() == "demo_Shock_Speed_vs_Frequency"
    assert ax.get_xlabel() == "Frequency (MHz)" and ax.get_ylabel() == "Shock Speed (km/s)"
    screen = dlg.canvas.ax
    assert to_hex(screen.collections[0].get_facecolor()[0]) == "#800080", "the window keeps its purple points"

    dlg.plot_max()
    png = _SaveSpy(analyze_module, monkeypatch, tmp_path / "max.png")
    dlg.save_graph()
    ax = png.assert_saved_white_page()
    _assert_origin_points(ax, legend=False)
    assert ax.get_title() == "demo_Maximum_Intensity"
    dlg.close()


def test_analyzer_save_graph_needs_a_plot(tmp_path, monkeypatch):
    _app()
    dlg = _analyzer()
    spy = _SaveSpy(analyze_module, monkeypatch, tmp_path / "nothing.png")

    dlg.save_graph()

    assert spy.messages == [("information", "Plot the maximum intensities or the best fit first.")]
    assert not (tmp_path / "nothing.png").exists()
    dlg.close()


def test_maximum_intensity_export_is_an_origin_figure_of_the_points_on_show(tmp_path, monkeypatch):
    _app()
    time_s = np.arange(1.0, 30.0)
    dlg = MaxIntensityPlotDialog(time_s / 0.25, 90.0 * time_s ** -0.45, "demo.fit", time_seconds=time_s)
    dlg.selected_mask[:3] = True
    dlg.remove_selected_outliers()
    dlg.activate_lasso()  # the on-screen prompt is not a title for the file
    spy = _SaveSpy(max_module, monkeypatch, tmp_path / "max.png")

    dlg.export_figure()

    ax = spy.assert_saved_white_page()
    _assert_origin_points(ax, legend=False)
    assert ax.get_title() == "Filtered Max Intensities"
    assert ax.get_xlabel() == "Time (s)"
    assert len(ax.get_lines()[0].get_xdata()) == time_s.size - 3
    dlg.close()


def test_type_ii_save_plot_writes_the_origin_figure(tmp_path, monkeypatch):
    _app()
    dlg = _type_ii_dialog()
    spy = _SaveSpy(type_ii_module, monkeypatch, tmp_path / "type_ii.eps")

    dlg._save_plot()

    assert [kind for kind, _text in spy.messages] == ["information"]
    assert spy.filters == FIGURE_EXPORT_FILTERS
    assert (tmp_path / "type_ii.eps").read_bytes().startswith(b"%!PS")
    ax = spy.figures[-1].axes[0]
    assert [text.get_text() for text in ax.get_legend().get_texts()][0] == "Upper band"
    assert all(spine.get_visible() for spine in ax.spines.values())
    dlg.close()
