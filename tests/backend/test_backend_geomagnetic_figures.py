"""
e-CALLISTO FITS Analyzer
Tests for the geomagnetic-response panels: markers outside the window, the
Dst scale, crowded marker labels, screen colours and legends.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("matplotlib")

import matplotlib.dates as mdates
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from src.backend.space_weather import geomagnetic_figures as gf
from src.backend.space_weather.geomagnetic_context import GeomagneticContext, GeoSeries

EVENT = datetime(2026, 4, 23, 4, 45)


def _context(dst_floor: float = -39.0) -> GeomagneticContext:
    context = GeomagneticContext(event_start=EVENT, start=EVENT - timedelta(days=1), end=EVENT + timedelta(days=7))
    hours = np.arange(np.datetime64(context.start, "h"), np.datetime64(context.end, "h"), np.timedelta64(1, "h"))
    dip = np.exp(-(((np.arange(hours.size) - 48) / 10.0) ** 2))
    dst = -15.0 + (dst_floor + 15.0) * dip
    context.series["dst"] = GeoSeries("dst", hours.astype("datetime64[s]"), dst)
    context.series["symh"] = GeoSeries("symh", hours.astype("datetime64[s]"), dst - 4.0)
    context.series["kp"] = GeoSeries("kp", hours[::3].astype("datetime64[s]"), np.full(hours[::3].size, 2.0))
    for name, value in (("bt", 5.0), ("bz", -2.0), ("by", 1.0), ("ae", 120.0), ("au", 60.0), ("al", -60.0)):
        context.series[name] = GeoSeries(name, hours.astype("datetime64[s]"), np.full(hours.size, value))
    return context


def _draw(context, panels=("dst", "kp", "imf"), markers=(), **kw):
    fig = Figure(figsize=(9, 6), layout="constrained")
    gf.tighten_layout(fig)
    axes = fig.subplots(gf.total_rows(panels), 1, sharex=True, squeeze=False)[:, 0]
    used = gf.draw_geomagnetic_panels(axes, context, panels, markers=list(markers), **kw)
    return fig, used


def _annotations(ax) -> list[str]:
    return [child.get_text() for child in ax.texts]


def test_markers_outside_the_window_are_neither_drawn_nor_named():
    # A DONKI storm four days before the window used to stretch the layout.
    early = {"time": EVENT - timedelta(days=5), "label": "Storm Kp 5.7", "color": "#8c564b", "kind": "gst"}
    inside = {"time": EVENT + timedelta(days=2), "label": "Shock at Earth (DONKI)", "color": "#1f77b4", "kind": "ips"}
    fig, used = _draw(_context(), markers=[early, inside])
    texts = _annotations(used[0])
    assert "Shock at Earth (DONKI)" in texts
    assert "Storm Kp 5.7" not in texts
    early_x = mdates.date2num(early["time"])
    for line in used[1].get_lines():
        xs = np.atleast_1d(line.get_xdata())
        if xs.size and isinstance(xs[0], datetime):
            xs = mdates.date2num(xs)
        assert not np.any(np.isclose(np.asarray(xs, dtype=float), early_x))
    # Every label sits within the panels' width.
    fig.canvas.draw()
    left, right = used[0].get_window_extent().x0, used[0].get_window_extent().x1
    for text in used[0].texts:
        box = text.get_window_extent()
        assert box.x0 >= left - 2 and box.x1 <= right + 2


def test_the_dst_scale_follows_the_data_instead_of_the_severe_storm_level():
    _fig, used = _draw(_context(dst_floor=-39.0), panels=("dst",))
    bottom, top = used[0].get_ylim()
    assert -80.0 < bottom < -39.0 and top > 0.0
    assert any("min Dst −39 nT" == text for text in _annotations(used[0]))

    _fig, used = _draw(_context(dst_floor=-406.0), panels=("dst",))
    bottom, _top = used[0].get_ylim()
    assert bottom < -406.0
    names = _annotations(used[0])
    # -50 and -100 are too close at this scale to both be named.
    assert "severe" in names and not ("moderate" in names and "intense" in names)


def test_a_crowded_window_falls_back_to_short_labels():
    markers = [
        {"time": EVENT + timedelta(hours=6 * i), "label": f"Linked CME {900 + i} km/s, 80°",
         "color": "#9467bd", "kind": "cme"}
        for i in range(12)
    ]
    _fig, used = _draw(_context(), markers=markers)
    texts = _annotations(used[0])
    assert "Event" in texts  # the event is named first
    assert any(text.startswith("CME 9") for text in texts)
    assert not any(text.startswith("Linked CME") for text in texts)


def test_short_marker_labels():
    assert gf.short_marker_label("cme", "Linked CME 1330 km/s, 90°") == "CME 1330"
    assert gf.short_marker_label("ips", "Shock at Earth (DONKI)") == "Shock"
    assert gf.short_marker_label("gst", "Storm Kp 9.0") == "Kp 9.0"
    assert gf.short_marker_label("event", "Event May 10 17:00") == "Event"


def test_the_dark_palette_draws_neutral_series_in_the_text_colour():
    palette = gf.dark_palette(foreground="#f0f0f0", face="#1f1f1f", figure="#2b2b2b")
    _fig, used = _draw(_context(), panels=("imf",), palette=palette)
    bt = next(line for line in used[0].get_lines() if line.get_label() == "Bt")
    assert to_hex(bt.get_color()) == "#f0f0f0"
    assert to_hex(used[0].get_facecolor()) == "#1f1f1f"
    # The exported (OriginPro) figure stays black on white.
    _fig, used = _draw(_context(), panels=("imf",), origin=True)
    bt = next(line for line in used[0].get_lines() if line.get_label() == "Bt")
    assert to_hex(bt.get_color()) == "#000000"


def test_two_quantity_panels_colour_their_axes_instead_of_a_legend():
    context = _context()
    hours = context.series["dst"].times
    context.series["beta"] = GeoSeries("beta", hours, np.full(hours.size, 1.2))
    context.series["mach"] = GeoSeries("mach", hours, np.full(hours.size, 9.0))
    context.series["ey"] = GeoSeries("ey", hours, np.full(hours.size, 0.5))
    _fig, used = _draw(context, panels=("derived",))
    beta_ax = used[1]
    assert beta_ax.get_legend() is None
    assert to_hex(beta_ax.yaxis.label.get_color()) == to_hex(gf.series_color("beta"))
    twin = beta_ax._geo_twin
    assert to_hex(twin.yaxis.label.get_color()) == to_hex(gf.series_color("mach"))


def test_the_export_figure_uses_day_ticks_and_names_the_year():
    fig = gf.geomagnetic_figure(_context(), ["dst", "kp"])
    bottom = fig.axes[1]
    assert bottom.get_xlabel() == "Time [UTC], 2026"
    fig.canvas.draw()
    labels = [tick.get_text() for tick in bottom.get_xticklabels() if tick.get_text()]
    assert "Apr 23" in labels
