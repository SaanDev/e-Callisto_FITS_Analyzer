"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

OriginPro-style radio graphs (src/backend/radio/radio_figures.py): the dynamic
spectrum of Export Figure and the project report, the fit, Type II and GOES
graphs, and the restyled copy the report makes of a Solar Events window's plot
(src/backend/common/figure_export.py).
"""

from __future__ import annotations

import pickle

import numpy as np
import pytest

pytest.importorskip("matplotlib")

import matplotlib
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from src.backend.common.figure_export import (
    ORIGIN_FIT_COLOR,
    figure_rgb,
    origin_font_families,
    origin_restyled_copy,
    save_figure,
)
from src.backend.radio.frequency_axis import matplotlib_extent
from src.backend.radio.radio_figures import (
    BandTrace,
    GoesOverlay,
    GraphText,
    Measurement,
    SpectrumGraph,
    SpectrumImage,
    SwavesPanel,
    dynamic_spectrum_figure,
    fit_graph_figure,
    goes_flux_figure,
    power_law_label,
    type_ii_spectrum_figure,
)

BLACK = "#000000"


def _image(levels=(0.0, 10.0), label: str = "Intensity [Digits]") -> SpectrumImage:
    freqs = np.linspace(90.0, 20.0, 40)
    time = np.linspace(0.0, 900.0, 60)
    data = np.zeros((40, 60), dtype=float)
    data[10:30, 20:40] = 10.0
    return SpectrumImage(data, matplotlib_extent(freqs, time), "viridis", levels, label)


def _swaves(title: str = "STEREO/SWAVES — STEREO-A") -> SwavesPanel:
    rows = np.linspace(np.log10(16000.0), 1.0, 30)
    data = np.random.default_rng(3).normal(5.0, 2.0, (30, 80))
    return SwavesPanel(
        image=SpectrumImage(data, [-600.0, 1500.0, float(rows[-1]), float(rows[0])], "viridis", None, "dB"),
        title=title,
        time_bounds=(-600.0, 1500.0),
        callisto_span=(0.0, 900.0),
    )


def _plot_axes(fig):
    """The axes that carry data: not the colour bars, not the GOES twin."""
    return [ax for ax in fig.axes if getattr(ax, "_colorbar", None) is None and ax.patch.get_visible()]


def _assert_origin_frame(ax) -> None:
    for spine in ax.spines.values():
        assert spine.get_visible()
        assert to_hex(spine.get_edgecolor()) == BLACK
    x_params = ax.xaxis.get_tick_params()
    y_params = ax.yaxis.get_tick_params()
    assert x_params["direction"] == "in" and y_params["direction"] == "in"
    assert x_params["top"] is True and x_params["bottom"] is True
    assert ax.xaxis.get_tick_params(which="minor")["direction"] == "in"
    assert ax.xaxis.get_minor_locator().__class__.__name__ != "NullLocator"


# --- Dynamic spectrum ---------------------------------------------------------


def test_dynamic_spectrum_is_an_origin_graph_on_a_white_page():
    fig = dynamic_spectrum_figure(SpectrumGraph(_image(), title="demo.fit-Raw"))

    (ax,) = _plot_axes(fig)
    _assert_origin_frame(ax)
    assert ax.yaxis.get_tick_params()["right"] is True
    assert to_hex(fig.get_facecolor()) == "#ffffff"
    assert ax.get_title() == "demo.fit-Raw"
    assert ax.title.get_fontfamily()[0] == origin_font_families()[0]
    assert ax.images[0].get_clim() == (0.0, 10.0)

    colorbars = [axes._colorbar for axes in fig.axes if getattr(axes, "_colorbar", None) is not None]
    assert len(colorbars) == 1
    assert colorbars[0].ax.get_ylabel() == "Intensity [Digits]"
    assert to_hex(colorbars[0].outline.get_edgecolor()) == BLACK

    rgb = figure_rgb(fig)
    assert tuple(rgb[0, 0]) == (255, 255, 255)
    assert tuple(rgb[-1, -1]) == (255, 255, 255)


def test_building_a_figure_leaves_the_global_style_alone():
    before = dict(matplotlib.rcParams)
    dynamic_spectrum_figure(SpectrumGraph(_image(), text=GraphText(font_family="DejaVu Serif")))
    fit_graph_figure([1.0, 2.0], [3.0, 4.0])
    assert dict(matplotlib.rcParams) == before


def test_graph_settings_set_the_font_sizes_and_emphasis():
    text = GraphText(font_family="DejaVu Serif", tick_size=9, label_size=15, title_size=17, title_bold=True, axis_italic=True)
    fig = dynamic_spectrum_figure(SpectrumGraph(_image(), title="Title", text=text))

    (ax,) = _plot_axes(fig)
    assert ax.title.get_fontsize() == 17
    assert ax.title.get_fontweight() == "bold"
    assert ax.title.get_fontfamily()[0] == "DejaVu Serif"
    assert ax.yaxis.label.get_fontsize() == 15
    assert ax.yaxis.label.get_fontstyle() == "italic"
    assert ax.xaxis.get_major_ticks()[0].label1.get_fontsize() == 9


def test_ut_ticks_fall_on_whole_minutes_even_from_an_odd_start():
    start = 12 * 3600 + 37.0
    fig = dynamic_spectrum_figure(SpectrumGraph(_image(), ut_start_sec=start, x_label="Time [UT]"))
    (ax,) = _plot_axes(fig)
    fig.canvas.draw()

    locs = [loc for loc in ax.xaxis.get_majorticklocs() if 0.0 <= loc <= 900.0]
    assert locs
    assert all(abs((loc + start) % 60.0) < 1e-6 for loc in locs)
    labels = [label.get_text() for label in ax.get_xticklabels() if label.get_text()]
    assert labels and all(len(label) == 5 and label.startswith("12:") for label in labels)
    assert ax.get_xlabel() == "Time [UT]"


def test_the_on_screen_zoom_is_reproduced():
    fig = dynamic_spectrum_figure(SpectrumGraph(_image(), view={"xlim": (100.0, 400.0), "ylim": (30.0, 60.0)}))
    (ax,) = _plot_axes(fig)
    assert ax.get_xlim() == pytest.approx((100.0, 400.0))
    assert ax.get_ylim() == pytest.approx((30.0, 60.0))


def test_a_log_frequency_axis_uses_labelled_frequencies():
    fig = dynamic_spectrum_figure(SpectrumGraph(_image(), log_frequency_bounds=(20.0, 90.0)))
    (ax,) = _plot_axes(fig)
    fig.canvas.draw()

    assert ax.get_yscale() == "log"
    assert ax.get_ylim() == pytest.approx((20.0, 90.0))
    labels = [label.get_text() for label in ax.get_yticklabels() if label.get_text()]
    assert "20" in labels and "50" in labels


def test_overlays_are_drawn_and_goes_takes_the_right_edge():
    time = np.linspace(0.0, 900.0, 60)
    curve = {
        "time": time,
        "y": 50.0 + np.sin(time / 90.0),
        "color": "#00e5ff",
        "line_width": 2.0,
        "opacity": 0.9,
        "line_style": "dashed",
        "show_label": True,
        "label": "50.000 MHz",
        "label_x": 0.0,
        "label_y": 51.0,
    }
    annotations = [
        {"kind": "polygon", "points": [[100, 30], [200, 40], [150, 60]], "color": "#ff0000"},
        {"kind": "text", "points": [[300, 70]], "text": "Type II", "color": "#ffffff", "font_size": 13, "font_bold": True},
        {"kind": "line", "points": [[0, 20], [10, 30]], "visible": False},
    ]
    goes = GoesOverlay([(time, 1e-6 * (1.0 + time / 900.0), "#ffffff", 3.2)])
    graph = SpectrumGraph(
        _image(),
        gap_spans=[(40.0, 45.0)],
        light_curves=[curve],
        annotations=annotations,
        goes=goes,
        measurement=Measurement([(100.0, 60.0), (400.0, 40.0)], "Δt = 300 s"),
    )
    fig = dynamic_spectrum_figure(graph)

    (ax,) = _plot_axes(fig)
    texts = {text.get_text() for text in ax.texts}
    assert {"50.000 MHz", "Type II", "Δt = 300 s"} <= texts
    assert any(line.get_linestyle() == "--" for line in ax.get_lines())
    assert len([line for line in ax.get_lines() if to_hex(line.get_color()) == "#ff0000"]) == 1
    assert any(patch.get_hatch() == "///" for patch in ax.patches)

    twins = [axes for axes in fig.axes if axes is not ax and getattr(axes, "_colorbar", None) is None]
    assert len(twins) == 1
    assert twins[0].get_yscale() == "log"
    assert twins[0].get_ylabel() == "GOES X-Ray Class"
    assert ax.yaxis.get_tick_params()["right"] is False


def test_the_swaves_panel_sits_under_the_spectrum_on_a_shared_time_axis():
    fig = dynamic_spectrum_figure(SpectrumGraph(_image(), title="Split", swaves=_swaves(), x_label="Time [s]"))

    top, bottom = _plot_axes(fig)
    assert len(fig.axes) == 4
    assert top.get_title() == "Split"
    assert bottom.get_title(loc="left") == "STEREO/SWAVES — STEREO-A"
    assert top.get_xlim() == pytest.approx((-600.0, 1500.0))
    assert bottom.get_xlabel() == "Time [s]" and top.get_xlabel() == ""
    assert top.xaxis.get_tick_params()["labelbottom"] is False
    assert bottom.patches, "the CALLISTO interval is outlined on the SWAVES panel"
    _assert_origin_frame(bottom)


def test_swaves_alone_fills_the_page_with_its_own_title():
    fig = dynamic_spectrum_figure(SpectrumGraph(None, swaves=_swaves("STEREO/SWAVES — STEREO-B")))
    (ax,) = _plot_axes(fig)
    assert ax.get_title() == "STEREO/SWAVES — STEREO-B"
    assert not ax.patches


def test_a_figure_needs_something_to_draw():
    with pytest.raises(ValueError):
        dynamic_spectrum_figure(SpectrumGraph(None))


# --- Fit, Type II and GOES graphs ---------------------------------------------


def test_fit_graph_draws_black_squares_a_red_curve_and_a_boxed_legend():
    x = np.linspace(1.0, 100.0, 30)
    fig = fit_graph_figure(
        x,
        300.0 * x ** -0.4,
        fit_x=x,
        fit_y=300.0 * x ** -0.4,
        data_label="Maximum intensity",
        fit_label=power_law_label(300.0, 0.4),
        title="Maximum Intensity Fit",
        x_label="Time [s]",
        y_label="Frequency [MHz]",
    )

    (ax,) = fig.axes
    data_line, fit_line = ax.get_lines()
    assert data_line.get_marker() == "s" and data_line.get_linestyle() == "None"
    assert to_hex(data_line.get_color()) == BLACK
    assert to_hex(fit_line.get_color()) == ORIGIN_FIT_COLOR.lower()
    _assert_origin_frame(ax)
    legend = ax.get_legend()
    assert to_hex(legend.get_frame().get_edgecolor()) == BLACK
    assert [text.get_text() for text in legend.get_texts()][0] == "Maximum intensity"
    fig.canvas.draw()  # the exponent's mathtext must parse


def test_power_law_label_sets_the_exponent_as_a_superscript():
    assert power_law_label(123.456, 0.5) == r"Best fit: f = 123.46 x$\mathregular{^{-0.50}}$"
    assert power_law_label(2.0, 1.25, prefix="Fit", symbol="B", variable="R", digits=1) == r"Fit: B = 2.0 R$\mathregular{^{-1.2}}$"


def test_type_ii_figure_draws_both_lanes_over_the_spectrum():
    fit_x = np.linspace(100.0, 300.0, 20)
    bands = [
        BandTrace("Upper band", [(100.0, 80.0), (200.0, 70.0)], (fit_x, 800.0 * fit_x ** -0.5), "#ff8c42", "#ff5a1f"),
        BandTrace("Lower band", [(100.0, 65.0), (200.0, 57.0)], None, "#38bdf8", "#0ea5e9"),
    ]
    fig = type_ii_spectrum_figure(_image(), bands, title="demo_Type_II_Band_Splitting")

    (ax,) = _plot_axes(fig)
    labels = [text.get_text() for text in ax.get_legend().get_texts()]
    assert labels == ["Upper band", "Upper band fit", "Lower band"]
    upper, upper_fit, lower = ax.get_lines()
    assert to_hex(upper.get_markerfacecolor()) == "#ff8c42"
    assert to_hex(upper_fit.get_color()) == "#ff5a1f"
    assert lower.get_marker() == "o"
    _assert_origin_frame(ax)


def test_goes_flux_graph_is_logarithmic_in_origin_colours():
    time = np.linspace(0.0, 600.0, 20)
    fig = goes_flux_figure([("XRS-A", time, 1e-7 + time * 1e-10), ("XRS-B", time, 1e-6 + time * 1e-9)])
    (ax,) = fig.axes
    assert ax.get_yscale() == "log"
    assert [to_hex(line.get_color()) for line in ax.get_lines()] == [BLACK, "#ff0000"]

    with pytest.raises(ValueError):
        goes_flux_figure([("XRS-A", time, np.zeros_like(time))])


# --- Restyling a window's plot --------------------------------------------------


def _dark_figure() -> Figure:
    fig = Figure(figsize=(4, 3))
    fig.patch.set_facecolor("#202020")
    ax = fig.add_subplot(1, 1, 1)
    ax.set_facecolor("#111111")
    ax.plot([0, 1, 2], [1, 3, 2], color="#1f77b4", label="flux")
    ax.plot([0, 2], [2, 2], color="#f5f5f5")
    ax.text(0.5, 2.1, "C1", color="#f0f0f0")
    ax.set_title("Kp", color="#f0f0f0")
    ax.set_xlabel("Time (UTC)", color="#f0f0f0")
    for spine in ax.spines.values():
        spine.set_color("#f0f0f0")
    ax.spines["top"].set_visible(False)
    ax.grid(True, color="#555555")
    ax.legend()
    return fig


def test_restyled_copy_is_an_origin_graph_and_the_window_keeps_its_look():
    fig = _dark_figure()
    clone = origin_restyled_copy(fig)

    ax = clone.axes[0]
    assert to_hex(clone.get_facecolor()) == "#ffffff"
    assert to_hex(ax.get_facecolor()) == "#ffffff"
    _assert_origin_frame(ax)
    assert to_hex(ax.title.get_color()) == BLACK
    assert to_hex(ax.xaxis.label.get_color()) == BLACK
    assert to_hex(ax.texts[0].get_color()) == BLACK
    assert not any(line.get_visible() for line in ax.get_xgridlines())
    data_line, guide = ax.get_lines()
    assert to_hex(data_line.get_color()) == "#1f77b4"
    assert to_hex(guide.get_color()) == "#404040"
    assert to_hex(ax.get_legend().get_frame().get_edgecolor()) == BLACK
    assert ax.title.get_fontfamily()[0] == origin_font_families()[0]

    original = fig.axes[0]
    assert to_hex(fig.get_facecolor()) == "#202020"
    assert to_hex(original.title.get_color()) == "#f0f0f0"
    assert not original.spines["top"].get_visible()


def test_a_figure_that_cannot_be_copied_gives_none():
    fig = _dark_figure()
    fig.axes[0].xaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:.0f}"))
    with pytest.raises(Exception):
        pickle.dumps(fig)
    assert origin_restyled_copy(fig) is None


def test_tight_save_trims_the_page_to_the_drawing(tmp_path):
    fig = fit_graph_figure([1.0, 2.0, 3.0], [3.0, 2.0, 1.0], title="Trim")
    loose = save_figure(fig, tmp_path / "loose.png", dpi=50)
    tight = save_figure(fig, tmp_path / "tight.png", dpi=50, tight=True)
    assert loose.stat().st_size > 0 and tight.stat().st_size > 0
    assert loose.read_bytes() != tight.read_bytes()
