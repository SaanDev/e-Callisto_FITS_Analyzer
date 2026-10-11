"""
e-CALLISTO FITS Analyzer
Tests for the project report's event story (src/backend/session/event_story.py)
and its section of the PDF: which flare, CME, shock and storm belong to the
event, how the story is told when they do and do not fit together, and the
event chain graphic.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta

import pytest

from src.backend.session import event_story as es
from src.backend.space_weather import event_catalogs as ec
from src.backend.space_weather.geomagnetic_context import GeomagneticContext, GeomagneticSummary

START = datetime(2024, 5, 10, 6, 30)
END = datetime(2024, 5, 10, 7, 0)


def _catalog(*, ii=True) -> ec.EventContextResult:
    bursts = [
        # An all-day continuum entry is background, not the event.
        ec.CatalogEvent(ec.KIND_BURST, ec.SOURCE_BURSTS, datetime(2024, 5, 10, 0, 0), end=datetime(2024, 5, 10, 23, 59),
                        label="Type CTM", details={"type": "CTM"}),
        ec.CatalogEvent(ec.KIND_BURST, ec.SOURCE_BURSTS, datetime(2024, 5, 10, 6, 31), end=datetime(2024, 5, 10, 6, 43),
                        label="Type III", details={"type": "III"}),
    ]
    if ii:
        bursts.append(ec.CatalogEvent(ec.KIND_BURST, ec.SOURCE_BURSTS, datetime(2024, 5, 10, 6, 45),
                                      end=datetime(2024, 5, 10, 6, 56), label="Type II", details={"type": "II"}))
    return ec.EventContextResult(
        START - timedelta(hours=6), END + timedelta(hours=6),
        events={
            ec.SOURCE_GOES: [
                ec.CatalogEvent(ec.KIND_FLARE, ec.SOURCE_GOES, datetime(2024, 5, 10, 6, 27), datetime(2024, 5, 10, 6, 54),
                                datetime(2024, 5, 10, 7, 6), "X3.9", {"class": "X3.9", "noaa_ar": 13664}),
                ec.CatalogEvent(ec.KIND_FLARE, ec.SOURCE_GOES, datetime(2024, 5, 10, 4, 0), datetime(2024, 5, 10, 4, 10),
                                datetime(2024, 5, 10, 4, 20), "M1.3", {"class": "M1.3"}),
            ],
            ec.SOURCE_CDAW: [
                ec.CatalogEvent(ec.KIND_CME, ec.SOURCE_CDAW, datetime(2024, 5, 10, 7, 12), label="CME",
                                details={"speed_kms": 953.0, "halo": True, "width": "Halo"}, url="https://cdaw.example/cme"),
                ec.CatalogEvent(ec.KIND_CME, ec.SOURCE_CDAW, datetime(2024, 5, 10, 12, 0), label="CME",
                                details={"speed_kms": 2000.0, "width": "40"}),  # too late to be this event's
            ],
            ec.SOURCE_DONKI: [
                ec.CatalogEvent(ec.KIND_CME, ec.SOURCE_DONKI, datetime(2024, 5, 10, 7, 12), label="CME 1018 km/s",
                                details={"speed_kms": 1018.0, "half_angle_deg": 41.0, "source_location": "S18W37"}),
            ],
            ec.SOURCE_BURSTS: bursts,
        },
    )


def _geomagnetic(*, dst_min: float, dst_at: datetime, kp: float, kp_at: datetime, g: str, klass: str) -> GeomagneticContext:
    context = GeomagneticContext(event_start=START, start=START - timedelta(days=1), end=START + timedelta(days=7))
    context.summary = GeomagneticSummary(
        dst_min_nt=dst_min, dst_min_time=dst_at, storm_class=klass, kp_max=kp, kp_max_time=kp_at, g_scale=g,
        bz_min_nt=-22.5, bz_min_time=dst_at - timedelta(hours=3), v_max_kms=780.0, v_max_time=dst_at - timedelta(hours=1),
    )
    return context


def _markers(shock_at: datetime, *extra) -> list[dict]:
    return [
        {"time": datetime(2024, 5, 10, 7, 12), "label": "Linked CME 1018 km/s, 82°", "kind": "cme"},
        {"time": shock_at, "label": "Shock at Earth (DONKI)", "kind": "ips"},
        *extra,
    ]


def _radio(**kw) -> es.RadioSummary:
    return es.RadioSummary(START, END, station="BIR", label="BIR_20240510_063000_01.fit", **kw)


def test_goes_class_flux():
    assert es.goes_class_flux("X3.9") == pytest.approx(3.9e-4)
    assert es.goes_class_flux("M1") == pytest.approx(1e-5)
    assert es.goes_class_flux("c2.5") == pytest.approx(2.5e-6)
    assert es.goes_class_flux("") is None and es.goes_class_flux("flare") is None


def test_the_full_chain_from_flare_to_storm():
    shock = START + timedelta(hours=34)
    geo = _geomagnetic(dst_min=-180.0, dst_at=shock + timedelta(hours=10), kp=7.7, kp_at=shock + timedelta(hours=6),
                       g="G3", klass="intense")
    story = es.build_event_story(radio=_radio(shock_speed_km_s=1180.0), catalog=_catalog(), geomagnetic=geo,
                                 markers=_markers(shock))
    assert "GOES X3.9 flare from NOAA AR 13664" in story.headline
    assert "953 km/s halo CME" in story.headline
    assert "G3 geomagnetic storm followed (Kp 7.7, Dst −180 nT)" in story.headline
    assert story.chain.split(" → ") == [
        "X3.9 flare", "type II burst", "953 km/s halo CME", "shock at Earth after 33.3 h", "G3 storm, Dst −180 nT",
    ]
    keys = [m.title for m in story.milestones if m.key]
    assert keys == ["X3.9 flare", "Type II burst", "CME in LASCO C2, 953 km/s", "Shock at Earth", "Dst min −180 nT"]
    assert all("CTM" not in m.title for m in story.milestones)  # the all-day continuum is background
    assert [title for _phase, title, _text in story.chapters] == [
        "At the Sun", "In the corona", "Through the heliosphere", "At Earth"]
    corona = dict((p, t) for p, _title, t in story.chapters)["corona"]
    assert "types III and II" in corona and "DONKI links this CME" in corona
    # The 3D speed (DONKI) is the one compared, not CDAW's projected one.
    assert "1,018 km/s DONKI" in dict((p, t) for p, _title, t in story.chapters)["heliosphere"]
    numbers = dict(story.key_numbers)
    assert numbers["Sun–Earth transit"].startswith("33.3 h (mean 1,24")
    assert "linked to the Earth impact" in numbers["CME (DONKI)"]
    assert any("DONKI's own links" in caveat for caveat in story.caveats)
    assert ("CME (CDAW)", "https://cdaw.example/cme") in story.sources


def test_a_storm_that_peaked_before_the_shock_is_not_this_events():
    shock = START + timedelta(hours=26)
    geo = _geomagnetic(dst_min=-406.0, dst_at=shock - timedelta(hours=7.5), kp=9.0, kp_at=shock - timedelta(hours=9.5),
                       g="G5", klass="super")
    earlier = {"time": START + timedelta(hours=10), "label": "Shock at Earth (DONKI)", "kind": "ips"}
    storm = {"time": START + timedelta(hours=8.5), "label": "Storm Kp 9.0", "kind": "gst"}
    story = es.build_event_story(radio=_radio(), catalog=_catalog(), geomagnetic=geo, markers=_markers(shock, earlier, storm))
    assert "while Earth was already in a G5 storm from earlier eruptions" in story.headline
    earth = dict((p, t) for p, _title, t in story.chapters)["earth"]
    assert "did not cause the storm's peak" in earth and "(a DONKI storm from 15:00 UT)" in earth
    helio = dict((p, t) for p, _title, t in story.chapters)["heliosphere"]
    assert "came too soon to be this event's" in helio  # the +10 h shock is an earlier eruption's
    assert story.chain.endswith("during a G5 storm already under way")
    dst = next(m for m in story.milestones if m.kind == "dst")
    assert dst.key is False  # not joined into this event's chain
    shock_milestone = next(m for m in story.milestones if m.title == "Shock at Earth")
    assert shock_milestone.time == shock


def test_a_quiet_earth_and_a_radio_only_story():
    shock = START + timedelta(hours=40)
    geo = _geomagnetic(dst_min=-21.0, dst_at=shock + timedelta(hours=5), kp=3.3, kp_at=shock, g="below G1", klass="quiet")
    story = es.build_event_story(radio=_radio(), catalog=_catalog(), geomagnetic=geo, markers=_markers(shock))
    assert "and Earth stayed largely quiet (Dst −21 nT)" in story.headline
    assert story.chain.endswith("no significant storm")

    alone = es.build_event_story(radio=_radio())
    assert alone.headline == "On 10 May 2024, BIR recorded the radio event from 06:30 UT to 07:00 UT."
    assert not alone.has_space_weather()
    assert [phase for phase, _title, _text in alone.chapters] == ["corona", "earth"]
    assert "not loaded" in dict((p, t) for p, _title, t in alone.chapters)["earth"]
    assert es.build_event_story() is None


def test_a_gcs_reconstruction_gives_direction_and_the_best_speed():
    gcs = es.GCSKinematics((datetime(2024, 5, 10, 7, 24), datetime(2024, 5, 10, 8, 0)), (5.1, 11.4), -8.0, 12.0,
                           speed_km_s=1650.0)
    assert gcs.earth_angle_deg == pytest.approx(14.4, abs=0.2)
    assert es.GCSKinematics((START,), (3.0,), 90.0, 0.0).earth_angle_deg == pytest.approx(90.0)
    shock = START + timedelta(hours=26)
    story = es.build_event_story(radio=_radio(shock_speed_km_s=1180.0), catalog=_catalog(), markers=_markers(shock), gcs=gcs)
    corona = dict((p, t) for p, _title, t in story.chapters)["corona"]
    assert "14° from the Sun–Earth line, Earth-directed" in corona
    assert "0.72 times the GCS 3D CME speed" in corona
    assert "close to the 1,650 km/s GCS 3D launch speed" in dict((p, t) for p, _title, t in story.chapters)["heliosphere"]


def test_the_event_chain_figure_draws_every_story():
    shock = START + timedelta(hours=34)
    geo = _geomagnetic(dst_min=-180.0, dst_at=shock + timedelta(hours=10), kp=7.7, kp_at=shock + timedelta(hours=6),
                       g="G3", klass="intense")
    for story in (
        es.build_event_story(radio=_radio(), catalog=_catalog(), geomagnetic=geo, markers=_markers(shock)),
        es.build_event_story(radio=_radio()),
    ):
        fig = es.event_chain_figure(story)
        ax = fig.axes[0]
        assert [label.get_text() for label in ax.get_yticklabels()] == ["Sun", "Corona", "Heliosphere", "Earth"]
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png")
        assert buffer.getvalue()[:4] == b"\x89PNG"
    texts = [t.get_text() for t in fig.axes[0].texts]
    assert any(text.startswith("BIR observation") for text in texts)


def test_the_report_tells_the_story_in_its_own_section(tmp_path):
    pytest.importorskip("reportlab")
    from src.backend.session.project_report import (
        ProjectReportFigure,
        ProjectReportInput,
        _add_event_story,
        _add_story_styles,
        _import_reportlab,
        generate_project_report_pdf,
        report_styles,
    )

    shock = START + timedelta(hours=34)
    geo = _geomagnetic(dst_min=-180.0, dst_at=shock + timedelta(hours=10), kp=7.7, kp_at=shock + timedelta(hours=6),
                       g="G3", klass="intense")
    story = es.build_event_story(radio=_radio(), catalog=_catalog(), geomagnetic=geo, markers=_markers(shock))
    buffer = io.BytesIO()
    es.event_chain_figure(story).savefig(buffer, format="png", dpi=60)
    report = ProjectReportInput(
        title="Story Report", event_story=story,
        story_figures=[ProjectReportFigure("Event Chain", png_bytes=buffer.getvalue()),
                       ProjectReportFigure("Geomagnetic Response", availability_note="Not loaded.")],
        story_notes=["Event catalogs could not be fetched: offline"],
    )

    rl = _import_reportlab()
    styles = report_styles(rl)
    _add_story_styles(rl, styles)
    flow: list = []
    assert _add_event_story(flow, rl, styles, report, 500.0) == 1

    def texts(items):
        for item in items:
            if hasattr(item, "_content"):  # KeepTogether
                yield from texts(item._content)
            elif hasattr(item, "text"):
                yield item.text
            elif hasattr(item, "_cellvalues"):
                for row in item._cellvalues:
                    yield from texts(cell for cell in row if cell is not None)

    joined = "\n".join(texts(flow))
    for needle in ("Event Story", "a G3 geomagnetic storm followed", "Key Numbers", "What Happened", "At Earth",
                   "Timeline", "Shock at Earth", "Event Chain", "Not loaded.", "Sources And Caveats",
                   "could not be fetched: offline", "T+34.0 h"):
        assert needle in joined, needle

    result = generate_project_report_pdf(str(tmp_path / "story.pdf"), report)
    assert result.figures_written == 1 and result.file_size > 2000
