"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Publication exports for GCS fitting: the OriginPro-style figure export
(src/backend/common/figure_export.py), the viewpoints and height–time figures
(src/backend/gcs/gcs_figures.py) and the PDF report (src/backend/gcs/gcs_report.py).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("matplotlib")

import matplotlib

from src.backend.common.figure_export import (
    FIGURE_EXPORT_FILTERS,
    ORIGIN_FIT_COLOR,
    export_format,
    figure_rgb,
    origin_style,
    save_figure,
)
from src.backend.gcs.gcs_figures import (
    HeightTimeSeries,
    ViewpointRender,
    WireframeStyle,
    fit_parameter_rows,
    fit_series,
    fits_by_order,
    height_time_figure,
    height_time_title,
    kinematics_rows,
    viewpoints_figure,
)

BASE = datetime(2012, 7, 12, 16, 0, 0)


def _series(order: int = 1, n: int = 6, errors: bool = True) -> HeightTimeSeries:
    times = [BASE + timedelta(minutes=12 * index) for index in range(n)]
    heights = [3.0 + 1.1 * index + 0.05 * (-1) ** index for index in range(n)]
    sigma = [0.1 + 0.02 * index for index in range(n)] if errors else None
    return HeightTimeSeries.build("GCS apex (3-D)", times, heights, sigma, order=order)


def _view(label: str, caption: str = "", data: bool = True) -> ViewpointRender:
    if not data:
        return ViewpointRender(label=label, caption=f"{label} · not loaded")
    yy, xx = np.mgrid[0:64, 0:64]
    image = np.exp(-((np.hypot(xx - 31.5, yy - 31.5) - 20.0) ** 2) / 8.0)
    theta = np.linspace(0.0, 2.0 * np.pi, 50)
    return ViewpointRender(
        label=label,
        caption=caption or f"{label} · SOHO C2\n2012-07-12 16:00:00 UTC",
        data=image,
        extent=(-3000.0, 3000.0, -3000.0, 3000.0),
        vmin=0.0,
        vmax=1.0,
        limb_radius_arcsec=960.0,
        gcs_xy=(1500 * np.cos(theta), 1500 * np.sin(theta)),
        points=((1200.0, 300.0),),
    )


# --- OriginPro style and formats ---------------------------------------------


def test_origin_style_applies_only_inside_its_block():
    before = dict(matplotlib.rcParams)
    with origin_style():
        params = matplotlib.rcParams
        assert params["xtick.direction"] == "in" and params["ytick.direction"] == "in"
        assert params["xtick.top"] and params["ytick.right"]
        assert params["xtick.minor.visible"] and params["ytick.minor.visible"]
        assert params["axes.linewidth"] == pytest.approx(1.5)
        assert params["legend.edgecolor"] == "#000000" and not params["legend.fancybox"]
        assert params["font.family"][-1] == "DejaVu Sans"
    assert {key: matplotlib.rcParams[key] for key in before} == before


def test_exported_graphs_are_light_even_under_a_dark_theme():
    dark = {"figure.facecolor": "black", "axes.facecolor": "black", "text.color": "white",
            "axes.labelcolor": "white", "axes.edgecolor": "white"}
    with matplotlib.rc_context(dark):
        figure = height_time_figure([_series()])
    pixels = figure_rgb(figure)
    assert tuple(pixels[2, 2]) == (255, 255, 255)
    ax = figure.axes[0]
    assert matplotlib.colors.to_hex(ax.get_facecolor()) == "#ffffff"
    assert matplotlib.colors.to_hex(ax.xaxis.label.get_color()) == "#000000"


_MAGIC = {
    "png": b"\x89PNG",
    "pdf": b"%PDF",
    "eps": b"%!PS",
    "tiff": (b"II*\x00", b"MM\x00*"),
    "tif": (b"II*\x00", b"MM\x00*"),
    "jpg": b"\xff\xd8",
    "jpeg": b"\xff\xd8",
}


@pytest.mark.parametrize("suffix", ["png", "pdf", "eps", "svg", "tiff", "tif", "jpg", "jpeg"])
def test_save_figure_writes_each_format_the_filters_offer(tmp_path, suffix):
    path = save_figure(height_time_figure([_series()]), tmp_path / f"graph.{suffix}", dpi=80)
    content = path.read_bytes()
    assert content
    if suffix == "svg":
        assert b"<svg" in content[:2000]
    else:
        magic = _MAGIC[suffix]
        assert content.startswith(magic if isinstance(magic, tuple) else (magic,))


def test_every_filter_suffix_is_supported_and_others_are_refused():
    suffixes = re.findall(r"\*\.(\w+)", FIGURE_EXPORT_FILTERS)
    assert set(suffixes) >= {"png", "pdf", "eps", "svg", "tiff", "jpg"}
    for suffix in suffixes:
        export_format(f"figure.{suffix}")
    with pytest.raises(ValueError):
        export_format("figure.gif")


# --- Viewpoints -----------------------------------------------------------------


def test_viewpoints_figure_keeps_one_size_whatever_the_captions():
    """Movie frames must share a size: captions and missing panels cannot move the layout."""
    short = viewpoints_figure([_view("A"), _view("B"), _view("C")], WireframeStyle(), title="t", dpi=50)
    long_caption = "\n".join(f"line {index} " + "word " * 30 for index in range(9))
    tall = viewpoints_figure(
        [_view("A", long_caption), _view("B", data=False), _view("C")], WireframeStyle(), title="t", dpi=50
    )
    assert figure_rgb(short).shape == figure_rgb(tall).shape


def test_viewpoint_panels_draw_the_image_in_arcsec_with_the_models():
    style = WireframeStyle(gcs_rgb=(255, 0, 0), width_px=4.0, opacity=0.5)
    figure = viewpoints_figure([_view("A")], style)
    ax = figure.axes[0]
    image = ax.get_images()[0]
    assert tuple(image.get_extent()) == (-3000.0, 3000.0, -3000.0, 3000.0)
    colours = [matplotlib.colors.to_rgba(line.get_color()) for line in ax.get_lines()]
    assert (1.0, 0.0, 0.0, 0.5) in colours  # the shell, in the window's colour and opacity
    assert ax.get_xlabel() == "Solar X (arcsec)"


def test_panel_colour_table_is_used_for_the_image():
    lut = np.zeros((256, 3), dtype=np.uint8)
    lut[:, 1] = np.arange(256)  # black to green
    view = ViewpointRender(label="A", data=np.ones((8, 8)), extent=(-1, 1, -1, 1), lut=lut, vmin=0.0, vmax=1.0)
    ax = viewpoints_figure([view], WireframeStyle()).axes[0]
    red, green, blue, _alpha = ax.get_images()[0].cmap(1.0)
    assert green == pytest.approx(1.0) and red == pytest.approx(0.0) and blue == pytest.approx(0.0)


# --- Height–time -------------------------------------------------------------------


def test_height_time_graph_draws_points_errors_and_the_red_fit():
    figure = height_time_figure([_series()], y_label="Apex height (R☉)")
    ax = figure.axes[0]
    markers = [line for line in ax.get_lines() if line.get_marker() == "s"]
    fits = [line for line in ax.get_lines() if matplotlib.colors.to_hex(line.get_color()) == ORIGIN_FIT_COLOR.lower()]
    assert len(markers) == 1 and len(fits) == 1
    assert len(ax.containers) == 1  # the error bars
    labels = [text.get_text() for text in ax.get_legend().get_texts()]
    assert labels[0] == "GCS apex (3-D)" and labels[1].startswith("Linear fit\nv = ")
    assert ax.get_ylabel() == "Apex height (R☉)"


def test_fit_matches_the_kinematics_card_fit():
    from src.backend.solar.coronagraph import RSUN_KM, fit_height_time

    series = _series(order=2)
    seconds = [(when - series.times[0]).total_seconds() for when in series.times]
    direct = fit_height_time(seconds, [height * RSUN_KM for height in series.heights_rsun], order=2)
    assert fit_series(series).speed_km_s == pytest.approx(direct.speed_km_s)
    assert fit_series(_series(order=3, n=3)) is None  # a cubic needs four times


def test_kinematics_rows_give_values_with_errors():
    rows = dict(kinematics_rows(fit_series(_series())))
    assert rows["Fit"] == "Linear (degree 1)"
    assert re.fullmatch(r"[\d,]+ ± [\d,]+ km/s", rows["Speed"])
    assert rows["Recorded times"] == "6"


# --- Report ------------------------------------------------------------------------


def _gcs_entry(when, height, refined=True):
    return SimpleNamespace(
        when=when, apex_height_rsun=height, lon_deg=30.0, lat_deg=-10.0, tilt_deg=15.0, alpha_deg=35.0,
        kappa=0.35, rms_arcsec=12.5, n_points=14, n_viewpoints=3, separation_deg=62.0,
        lon_err_deg=1.5, lat_err_deg=2.0, height_err_rsun=0.2, refined=refined,
    )


def _shock_entry(when, height):
    return SimpleNamespace(
        when=when, apex_height_rsun=height, lon_deg=31.0, lat_deg=-9.0, tilt_deg=0.0, kappa=0.8, epsilon=0.1,
        alpha=1.0, model="spheroid", rms_arcsec=15.0, n_points=9, n_viewpoints=2, separation_deg=62.0,
        lon_err_deg=float("nan"), lat_err_deg=float("nan"), height_err_rsun=float("nan"), refined=False,
    )


def _report_input(**overrides):
    from src.backend.gcs.gcs_model import GCSParameters, ObserverGeometry
    from src.backend.gcs.gcs_report import GCSReportInput, ViewpointInfo
    from src.backend.gcs.shock_model import ShockParameters

    times = [BASE + timedelta(minutes=12 * index) for index in range(3)]
    observer = ObserverGeometry(lon_deg=0.0, lat_deg=0.0, dsun_rsun=213.0, rsun_arcsec=968.0)
    data = dict(
        generated_at=datetime(2026, 9, 18, 12, 0, 0),
        app_name="e-CALLISTO FITS Analyzer",
        app_version="3.0.0",
        event_range=(BASE - timedelta(hours=1), BASE + timedelta(hours=1)),
        shared_time=times[1],
        max_time_offset_s=300.0,
        view_mode="Running difference",
        frame_cap=60,
        fit_order=1,
        viewpoints=[ViewpointInfo("A", "STEREO-B COR2"), ViewpointInfo("B", "SOHO LASCO C2", 5, times[0], times[2], observer)],
        separations=[("B", "C", 62.0)],
        gcs=GCSParameters(30.0, -10.0, 15.0, 6.0, 35.0, 0.35),
        shock=ShockParameters(lon_deg=31.0, lat_deg=-9.0, height_rsun=7.0, kappa=0.8, epsilon=0.1),
        gcs_fits={when: _gcs_entry(when, 4.0 + index) for index, when in enumerate(times)},
        shock_fits={times[2]: _shock_entry(times[2], 7.5)},
        fit_figures={times[0]: None},
        fit_figure_notes={times[0]: "The images this fit was recorded from are no longer loaded."},
    )
    data.update(overrides)
    return GCSReportInput(**data)


def test_report_pdf_holds_every_section_and_its_symbols(tmp_path):
    pytest.importorskip("reportlab")
    pdfium = pytest.importorskip("pypdfium2")
    from src.backend.gcs.gcs_report import generate_gcs_report_pdf

    result = generate_gcs_report_pdf(tmp_path / "report.pdf", _report_input())
    document = pdfium.PdfDocument(result.path)
    text = "\n".join(document[index].get_textpage().get_text_range() for index in range(len(document)))
    for needle in (
        "GCS CME Fitting Report",
        "Viewpoints",
        "Model at the time shown",
        "Recorded fits",
        "Fit at 2012-07-12 16:00:00 UTC",
        "no longer loaded",
        "Height–time kinematics",
        "Shock apex: linear fit",
        "Not fitted: a linear fit needs at least 2 distinct recorded times; 1 recorded.",
        "Method and limitations",
    ):
        assert needle in text, needle
    # ReportLab's Helvetica has no ☉, α or κ; the report's own font must draw them.
    assert "R☉" in text and "α" in text and "κ" in text
    # All three fits of the GCS series (three times), each with its parameters.
    for name in ("linear", "quadratic", "cubic"):
        assert f"GCS apex: {name} fit" in text  # (graph titles are inside the images)
    assert "Not fitted: a cubic fit needs at least 4 distinct recorded times; 3 recorded." in text
    assert "GCS apex: the three fits compared" in text and "Degrees of freedom" in text
    # Every page carries the application's name and the author's.
    for index in range(len(document)):
        page = document[index].get_textpage().get_text_range()
        assert "e-CALLISTO FITS Analyzer" in page and "©Sahan S Liyanage" in page and f"Page {index + 1}" in page
    assert result.figures_written == 2  # the linear and quadratic graphs; no images in this input


def test_report_without_fits_says_how_to_get_kinematics():
    from src.backend.gcs.gcs_report import Text, build_report_blocks

    blocks = build_report_blocks(_report_input(gcs_fits={}, shock_fits={}, fit_figures={}, fit_figure_notes={}))
    texts = [block.text for block in blocks if isinstance(block, Text)]
    assert any(text.startswith("No fits were recorded.") for text in texts)


def test_formal_errors_are_shown_only_for_the_refined_parameters():
    from src.backend.gcs.gcs_model import GCSParameters
    from src.backend.gcs.gcs_report import gcs_parameter_pairs

    params = GCSParameters(30.0, -10.0, 15.0, 6.0, 35.0, 0.35)
    refinement = SimpleNamespace(
        converged=True, parameters=params, sigma={"lon_deg": 1.25, "height_rsun": 0.1},
        rms_arcsec=9.0, n_points=12, n_viewpoints=3, weakly_constrained=("alpha_deg",),
    )
    pairs = dict(gcs_parameter_pairs(params, refinement))
    assert pairs["Longitude (Stonyhurst)"] == "+30.00 ± 1.25°"
    assert "Weakly constrained: alpha_deg" in pairs["Errors"]
    moved = GCSParameters(31.0, -10.0, 15.0, 6.0, 35.0, 0.35)
    stale = dict(gcs_parameter_pairs(moved, refinement))
    assert "±" not in stale["Longitude (Stonyhurst)"]
    assert stale["Errors"].startswith("No valid refinement")


def test_graphs_carry_a_title_naming_the_fit():
    series = _series(order=2)
    title = height_time_title("GCS flux-rope apex height–time", series)
    assert title == "GCS flux-rope apex height–time: quadratic fit"
    figure = height_time_figure([series], title=title)
    assert figure.axes[0].get_title() == title
    # A series too short for its degree draws no fit, and the title does not claim one.
    assert height_time_title("Shock apex height–time", _series(order=3, n=3)) == "Shock apex height–time"


def test_each_fit_states_its_model_coefficients_and_freedom():
    fits = fits_by_order(_series(n=4))
    assert all(fits[order] is not None for order in (1, 2, 3))
    rows = dict(fit_parameter_rows(fits[3]))
    assert rows["Model"].startswith("h(t) = h₀ + v₀·t + ½·a₀·t² + ⅙·j·t³")
    assert rows["Degrees of freedom"] == "0"
    assert re.fullmatch(r"[\d.]+ R☉", rows["Height at first time h₀"])  # no error bar without freedom
    assert rows["Errors"].startswith("not estimable")
    linear = dict(fit_parameter_rows(fits[1]))
    assert linear["Degrees of freedom"] == "2" and "±" in linear["Height at first time h₀"]
    assert fits_by_order(_series(n=3))[3] is None
