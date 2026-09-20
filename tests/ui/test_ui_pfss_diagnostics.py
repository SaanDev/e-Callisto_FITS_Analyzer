"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Tests for the PFSS diagnostics window and PFSS session persistence.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication

from src.backend.session.solar_session import (
    SOLAR_SCHEMA_VERSION,
    deserialize_pfss_state,
    serialize_pfss_state,
)
from src.ui.solar.pfss_diagnostics_window import PfssDiagnosticsWindow


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 4):
    for _ in range(times):
        QApplication.processEvents()


@pytest.fixture(scope="module")
def solution():
    """A real (small) PFSS solve, so the panels have genuine data to draw."""
    pytest.importorskip("sunkit_magex")
    import sunpy.map
    from sunkit_magex.pfss import utils

    from src.backend.solar.pfss_model import PfssParameters, solve_pfss

    nlon, nlat = 72, 36
    header = utils.carr_cea_wcs_header("2020-09-01T13:00:00", (nlon, nlat))
    sin_lat = np.linspace(-1, 1, nlat)[:, None]
    lon = np.linspace(0, 360, nlon, endpoint=False)[None, :]
    data = 10.0 * sin_lat + 6.0 * np.sin(np.radians(2 * lon)) * np.sqrt(1 - sin_lat**2)
    magnetogram = sunpy.map.Map(np.broadcast_to(data, (nlat, nlon)).copy(), header)
    return solve_pfss(
        magnetogram,
        PfssParameters(nrho=16, seed_density=8),
        provenance={
            "source_label": "GONG synoptic",
            "carrington_rotation": 2234,
            "obstime": "2020-09-01T13:00:00",
            "frame_offset_hours": 48.0,
            "non_finite_pixels": 120,
            "non_finite_fraction": 0.046,
            "net_flux_ratio": 0.0012,
            "bunit": "Gauss",
        },
    )


@pytest.fixture
def window(solution):
    _app()
    win = PfssDiagnosticsWindow(None, solution=solution, provenance=solution.provenance)
    _flush()
    yield win
    win.close()
    _flush()


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #

def test_all_four_panels_are_drawn(window):
    titles = [axes.get_title() for axes in window.figure.get_axes() if axes.get_title()]
    joined = " | ".join(titles)
    assert "Input magnetogram" in joined
    assert "Source surface" in joined
    assert "Open" in joined and "closed" in joined
    assert "Solution" in joined


def test_the_caption_leads_with_the_magnetogram_and_warns_about_staleness(window):
    text = window.caption_label.text()
    assert "GONG synoptic" in text
    assert "CR 2234" in text
    assert "Carrington rotation" in text, "the synoptic-map caveat must be stated"


def test_the_statistics_panel_reports_the_model_and_its_provenance(window):
    blocks = [
        text.get_text()
        for axes in window.figure.get_axes()
        for text in axes.texts
        if len(text.get_text()) > 40
    ]
    assert blocks, "the statistics panel drew no text"
    stats = max(blocks, key=len)
    for expected in ("Magnetogram", "Carrington rotation", "Source surface", "Radial cells"):
        assert expected in stats


def test_the_residual_net_flux_is_reported_not_corrected(window):
    """pfss() excludes the monopole itself, so this is a quality indicator."""
    blocks = [
        text.get_text()
        for axes in window.figure.get_axes()
        for text in axes.texts
        if "Residual net flux" in text.get_text()
    ]
    assert blocks


def test_zero_filled_gaps_are_disclosed(window):
    """Unmeasured polar pixels are a real limit of the boundary condition."""
    labels = " ".join(axes.get_xlabel() for axes in window.figure.get_axes())
    assert "zero-filled" in labels


def test_a_window_without_a_solution_does_not_crash():
    _app()
    win = PfssDiagnosticsWindow(None, solution=None)
    _flush()
    assert len(win.figure.get_axes()) == 1
    win.close()
    _flush()


def test_set_solution_redraws_in_place(solution):
    _app()
    win = PfssDiagnosticsWindow(None, solution=None)
    _flush()
    assert len(win.figure.get_axes()) == 1

    win.set_solution(solution, None, solution.provenance)
    _flush()
    assert len(win.figure.get_axes()) > 1
    win.close()
    _flush()


def test_the_open_closed_panel_is_skipped_without_a_polarity_grid(window):
    """A trace run with with_boundaries=False has no grid to draw."""
    titles = [axes.get_title() for axes in window.figure.get_axes()]
    assert any("not computed" in title for title in titles)


def test_a_polarity_grid_is_drawn_when_present(solution):
    from src.backend.solar.pfss_model import TracedField

    _app()
    grid = np.sign(np.random.default_rng(0).normal(size=(12, 24)))
    grid[4:8, :] = 0.0
    traced = TracedField(
        lon_deg=np.zeros(0),
        lat_deg=np.zeros(0),
        radius_rsun=np.zeros(0),
        offsets=np.zeros(1, dtype=np.int64),
        polarity=np.zeros(0),
        open_mask=np.zeros(0, dtype=bool),
        polarity_grid=grid,
        grid_lon_deg=np.linspace(0, 360, 24, endpoint=False),
        grid_lat_deg=np.linspace(-80, 80, 12),
    )
    win = PfssDiagnosticsWindow(None, solution=solution, traced=traced)
    _flush()
    titles = " ".join(axes.get_title() for axes in win.figure.get_axes())
    assert "not computed" not in titles
    win.close()
    _flush()


def test_a_neutral_line_crossing_the_seam_is_broken_not_bridged():
    """Without a NaN break a wrapping PIL draws a stroke across the whole map."""
    lon, lat = PfssDiagnosticsWindow._pil_lonlat(
        type("Pil", (), {
            "lon": type("Q", (), {"to_value": staticmethod(lambda _u: np.array([350.0, 358.0, 3.0, 8.0]))})(),
            "lat": type("Q", (), {"to_value": staticmethod(lambda _u: np.array([1.0, 2.0, 3.0, 4.0]))})(),
        })()
    )
    assert lon is not None
    assert np.isnan(lon).sum() == 1
    assert np.isnan(lat).sum() == 1


# --------------------------------------------------------------------------- #
# Session persistence
# --------------------------------------------------------------------------- #

def test_the_schema_version_is_not_bumped():
    """Both readers compare with strict equality, so a bump would make every
    existing session file be rejected rather than read. A new key is ignored by
    an older reader, which is why the PFSS state is stored as one."""
    assert SOLAR_SCHEMA_VERSION == 1


def test_serialising_accepts_nothing_and_returns_nothing():
    assert serialize_pfss_state(None) == {}
    assert deserialize_pfss_state(None) == {}
    assert deserialize_pfss_state({}) == {}


def test_the_state_survives_a_json_round_trip():
    state = {
        "source": "adapt",
        "nrho": 48,
        "rss": 3.25,
        "seed_mode": "open_field",
        "seed_density": 30,
        "realization": 7,
        "show_open": False,
        "show_closed": True,
        "show_boundaries": True,
        "near_side_only": False,
        "magnetogram_path": "/tmp/mag.fits",
        "carrington_rotation": 2272,
        "clicked_arcsec": [(120.5, -80.25), (-300.0, 10.0)],
    }
    payload = serialize_pfss_state(state)
    restored = deserialize_pfss_state(json.loads(json.dumps(payload)))

    assert restored["nrho"] == 48
    assert restored["rss"] == pytest.approx(3.25)
    assert restored["seed_mode"] == "open_field"
    assert restored["show_open"] is False
    assert restored["near_side_only"] is False
    assert restored["clicked_arcsec"] == [(120.5, -80.25), (-300.0, 10.0)]


def test_numpy_scalars_are_cast_before_json():
    """The session writer calls json.dumps with no default= handler, so an
    un-cast numpy scalar would abort the entire save."""
    payload = serialize_pfss_state(
        {
            "source": "gong",
            "nrho": np.int64(41),
            "rss": np.float64(2.75),
            "seed_density": np.int32(18),
            "realization": np.int64(3),
        }
    )
    json.dumps(payload)   # must not raise
    assert isinstance(payload["nrho"], int)
    assert isinstance(payload["rss"], float)


def test_the_solution_itself_is_never_stored():
    """Settings only: a solve is seconds, and the magnetogram is cached."""
    payload = serialize_pfss_state({"source": "gong", "nrho": 35, "solution": object()})
    assert "solution" not in payload
    assert "traced" not in payload
    for value in payload.values():
        assert isinstance(value, (str, int, float, bool, list, type(None)))
