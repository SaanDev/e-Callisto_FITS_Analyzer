"""
e-CALLISTO FITS Analyzer
Tests for the publication event figure (src/backend/common/event_figure.py).
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from src.backend.common.event_figure import (
    CM_PER_INCH,
    PANEL_KEYS,
    SIZE_PRESETS,
    EventFigureData,
    EventFigureSpec,
    GeoBlockSpec,
    PanelSpec,
    SepSeries,
    ThumbnailImage,
    ThumbnailSpec,
    auto_thumbnail_times,
    build_event_figure,
    figure_size_in,
)
from src.backend.common.figure_export import save_figure
from src.backend.radio.frequency_axis import matplotlib_extent
from src.backend.radio.radio_figures import EventLane, EventMark, EventMarkers, SpectrumGraph, SpectrumImage, SwavesPanel
from src.backend.space_weather.geomagnetic_context import GeomagneticContext, GeoSeries

BASE = datetime(2024, 5, 10, 6, 30)


def _data(with_everything: bool = True) -> EventFigureData:
    if not with_everything:
        return EventFigureData(base_utc=BASE)
    freqs = np.linspace(80.0, 20.0, 30)
    time = np.linspace(0.0, 1800.0, 60)
    spectrum = SpectrumGraph(SpectrumImage(np.random.default_rng(1).random((30, 60)), matplotlib_extent(freqs, time),
                                           "viridis", (0.0, 1.0), "Digits"))
    rows = np.linspace(np.log10(16000.0), 1.0, 20)
    swaves = SwavesPanel(SpectrumImage(np.random.default_rng(2).random((20, 40)),
                                       [-600.0, 2400.0, float(rows[-1]), float(rows[0])], "viridis", None, "dB"))
    goes = [("1-8 Å", time, 1e-5 * (1 + np.sin(time / 300.0) ** 2)), ("0.5-4 Å", time, 1e-6 * np.ones_like(time))]
    sep_times = [BASE + timedelta(minutes=5 * i) for i in range(10)]
    sep = SepSeries(sep_times, np.linspace(1, 30, 10), np.linspace(0.1, 3, 10), "P≥10 MeV", "P≥100 MeV")
    events = EventMarkers((EventLane("Flares", (EventMark(-180.0, 2160.0, "X3.9", "#d62728"),)),
                           EventLane("CMEs", (EventMark(1080.0, None, "1353 km/s", "#9467bd"),))))
    context = GeomagneticContext(event_start=BASE, start=BASE - timedelta(days=1), end=BASE + timedelta(days=7))
    hours = np.datetime64(BASE - timedelta(days=1), "s") + np.arange(192) * np.timedelta64(3600, "s")
    context.series["dst"] = GeoSeries("dst", hours, -100 * np.sin(np.arange(192) / 30.0) ** 2)
    context.series["kp"] = GeoSeries("kp", hours[::3], np.linspace(1, 8, 64))
    context.series["bz"] = GeoSeries("bz", hours, np.cos(np.arange(192) / 10.0) * 10)
    thumbs = [
        ThumbnailImage(BASE + timedelta(minutes=10), "AIA 193", np.random.default_rng(3).random((32, 32)), "loaded"),
        ThumbnailImage(BASE + timedelta(minutes=20), "LASCO C2", np.zeros((16, 16, 3), dtype=np.uint8), "helioviewer"),
    ]
    return EventFigureData(
        base_utc=BASE, spectrum=spectrum, swaves=swaves, goes=goes, sep=sep, events=events,
        geomagnetic=context, geo_markers=[{"time": BASE + timedelta(hours=34), "label": "Shock", "color": "#1f77b4"}],
        radio_interval=(BASE, BASE + timedelta(minutes=30)), thumbnails=thumbs, x_range=(0.0, 1800.0),
    )


def _spec(keys, *, geo=False):
    return EventFigureSpec(
        panels=[PanelSpec(key, key in keys, 1.0) for key in PANEL_KEYS],
        geomagnetic=GeoBlockSpec(enabled=geo, panels=("dst", "kp", "imf")),
    )


@pytest.mark.parametrize("count", range(1, len(PANEL_KEYS) + 1))
def test_every_panel_combination_builds(count):
    data = _data()
    for keys in itertools.combinations(PANEL_KEYS, count):
        fig = build_event_figure(_spec(keys), data)
        assert len([ax for ax in fig.axes if ax.get_label() != "<colorbar>"]) >= count + len(data.thumbnails)
        fig.canvas.draw() if fig.canvas is not None else None


def test_every_panel_builds_with_no_data():
    for key in PANEL_KEYS:
        fig = build_event_figure(_spec((key,), geo=True), EventFigureData(base_utc=BASE))
        texts = [t.get_text() for ax in fig.axes for t in ax.texts]
        assert any(text.startswith("No ") for text in texts)
    empty = build_event_figure(EventFigureSpec(panels=[PanelSpec(k, False) for k in PANEL_KEYS]), EventFigureData())
    assert any("Choose at least one panel" in t.get_text() for ax in empty.axes for t in ax.texts)


@pytest.mark.parametrize("preset, cm", list(SIZE_PRESETS.items()))
def test_size_presets_give_the_journal_width(preset, cm):
    spec = _spec(("spectrum",))
    spec.size_preset = preset
    fig = build_event_figure(spec, _data())
    assert fig.get_size_inches()[0] == pytest.approx(cm / CM_PER_INCH)


def test_a_custom_size_is_used_as_given():
    spec = _spec(("spectrum", "goes"))
    spec.size_preset = "Custom"
    spec.custom_width_cm = 15.0
    spec.custom_height_cm = 20.0
    assert figure_size_in(spec) == pytest.approx((15.0 / CM_PER_INCH, 20.0 / CM_PER_INCH))
    fig = build_event_figure(spec, _data())
    assert tuple(fig.get_size_inches()) == pytest.approx((15.0 / CM_PER_INCH, 20.0 / CM_PER_INCH))


def test_thumbnails_get_dashed_time_lines_through_the_panels():
    fig = build_event_figure(_spec(("spectrum", "goes")), _data())
    dashed = [line for ax in fig.axes for line in ax.get_lines() if line.get_linestyle() == "--"]
    assert len(dashed) >= 4  # two thumbnails × two panels
    assert len([a for a in fig.artists if a.__class__.__name__ == "ConnectionPatch"]) == 2
    titles = [ax.get_title() for ax in fig.axes]
    assert any(title.startswith("AIA 193") for title in titles)


def test_the_geomagnetic_block_has_its_own_time_axis():
    fig = build_event_figure(_spec(("spectrum",), geo=True), _data())
    date_axes = [ax for ax in fig.axes if ax.get_xlabel() == "Time [UTC]"]
    assert date_axes
    spectrum_axes = [ax for ax in fig.axes if ax.get_xlabel().startswith("Time [UT] on 2024-05-10")]
    assert spectrum_axes
    assert date_axes[0].get_xlim() != spectrum_axes[0].get_xlim()


def test_the_figure_saves_as_pdf_and_eps(tmp_path):
    fig = build_event_figure(_spec(PANEL_KEYS, geo=True), _data())
    for suffix in ("pdf", "eps", "png"):
        path = tmp_path / f"event.{suffix}"
        save_figure(fig, path, tight=True)
        assert path.stat().st_size > 2000


def test_the_spec_round_trips_through_json():
    spec = EventFigureSpec(
        panels=[PanelSpec("goes", True, 2.0), PanelSpec("spectrum", True, 3.5)],
        start=BASE, end=BASE + timedelta(minutes=30),
        thumbnails=[ThumbnailSpec(BASE + timedelta(minutes=5), "AIA 193")],
        geomagnetic=GeoBlockSpec(True, ("dst", "imf"), 1.5),
        size_preset="8.8 cm (one column)", font_scale=1.2, title="2024 May 10",
    )
    back = EventFigureSpec.from_json(json.loads(json.dumps(spec.to_json())))
    assert [p.key for p in back.panels][:2] == ["goes", "spectrum"]  # the order survives
    assert {p.key for p in back.panels} == set(PANEL_KEYS)  # missing panels come back disabled
    assert back.panels[1].height_ratio == 3.5
    assert back.start == BASE and back.thumbnails[0].source == "AIA 193"
    assert back.geomagnetic.panels == ("dst", "imf") and back.geomagnetic.enabled
    assert back.size_preset == "8.8 cm (one column)" and back.font_scale == 1.2 and back.title == "2024 May 10"


def test_auto_thumbnail_times_spread_over_the_range():
    times = auto_thumbnail_times(BASE, BASE + timedelta(minutes=40), 3)
    assert times == [BASE + timedelta(minutes=10), BASE + timedelta(minutes=20), BASE + timedelta(minutes=30)]


def test_the_bottom_panel_carries_the_ut_tick_labels_even_when_it_is_the_strip():
    fig = build_event_figure(_spec(("spectrum", "events")), _data())
    fig.canvas.draw()
    bottom = [ax for ax in fig.axes if ax.get_xlabel().startswith("Time [UT]")][0]
    assert bottom.xaxis.get_tick_params()["labelbottom"] is True
    labels = [label.get_text() for label in bottom.get_xticklabels() if label.get_text()]
    assert labels and all(":" in label for label in labels)
