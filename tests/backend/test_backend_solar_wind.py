"""
e-CALLISTO FITS Analyzer
Tests for OMNI / L1 solar wind loading (src/backend/space_weather/solar_wind.py)
and the geomagnetic context built on it (geomagnetic_context.py). The network
is mocked through ``session=``; the OMNI file is a small CDF written here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pytest

cdflib = pytest.importorskip("cdflib")

from src.backend.space_weather import solar_wind as sw
from src.backend.space_weather.geomagnetic_context import (
    GeomagneticContext,
    GeoSeries,
    context_csv_rows,
    context_from_project,
    context_to_project,
    g_scale,
    load_geomagnetic_context,
    storm_class,
    summarise,
)


def _write_omni_cdf(path, times, columns):
    """A minimal OMNI HRO CDF: Epoch plus the given variables, with OMNI's fills."""
    from cdflib.cdfwrite import CDF
    from cdflib.epochs import CDFepoch

    fills = {"SYM_H": 99999, "AE_INDEX": 99999, "AL_INDEX": 99999, "AU_INDEX": 99999,
             "flow_speed": 99999.9, "T": 9999999.0, "proton_density": 999.99}
    cdf = CDF(str(path), cdf_spec={"Compressed": False})
    epochs = np.array([CDFepoch.compute_epoch([t.year, t.month, t.day, t.hour, t.minute, 0, 0]) for t in times])
    cdf.write_var(
        {"Variable": "Epoch", "Data_Type": CDF.CDF_EPOCH, "Num_Elements": 1, "Rec_Vary": True, "Dim_Sizes": []},
        var_attrs={"FILLVAL": [-1e31, "CDF_EPOCH"]},
        var_data=epochs,
    )
    for name, values in columns.items():
        integer = name in ("SYM_H", "AE_INDEX", "AL_INDEX", "AU_INDEX")
        fill = fills.get(name, 9999.99)
        cdf.write_var(
            {"Variable": name, "Data_Type": CDF.CDF_INT4 if integer else CDF.CDF_FLOAT,
             "Num_Elements": 1, "Rec_Vary": True, "Dim_Sizes": []},
            var_attrs={"FILLVAL": [fill, "CDF_INT4" if integer else "CDF_FLOAT"]},
            var_data=np.asarray(values, dtype=np.int32 if integer else np.float32),
        )
    cdf.close()


def test_omni_cdf_fill_values_become_nan(tmp_path):
    times = [datetime(2024, 5, 10, 17, 0) + timedelta(minutes=5 * i) for i in range(3)]
    path = tmp_path / "omni.cdf"
    _write_omni_cdf(path, times, {
        "BZ_GSM": [-5.0, 9999.99, -20.0],
        "flow_speed": [700.0, 99999.9, 750.0],
        "SYM_H": [-10, -50, 99999],
    })
    got_times, series = sw.read_omni_cdf(path)
    assert got_times[0] == np.datetime64("2024-05-10T17:00:00")
    assert np.isnan(series["bz"][1]) and series["bz"][2] == pytest.approx(-20.0)
    assert np.isnan(series["v"][1])
    assert series["symh"][1] == -50 and np.isnan(series["symh"][2])
    # Variables missing from the file are all-NaN rather than an error.
    assert np.isnan(series["ae"]).all()


def test_omni_month_is_downloaded_once_and_cached(tmp_path):
    times = [datetime(2024, 5, 1) + timedelta(minutes=5 * i) for i in range(4)]
    source = tmp_path / "src.cdf"
    _write_omni_cdf(source, times, {"BZ_GSM": [1.0, 2.0, 3.0, 4.0]})
    payload = source.read_bytes()
    calls = []

    class Response:
        status_code = 200

        def iter_content(self, chunk_size):
            yield payload

        def close(self):
            pass

    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            return Response()

    now = datetime(2026, 10, 1)
    path = sw.omni_month_path(2024, 5, tmp_path / "cache", session=Session(), now=now)
    assert path is not None and path.read_bytes() == payload
    assert calls == [sw.omni_url(2024, 5)]
    assert calls[0].endswith("hro_5min/2024/omni_hro_5min_20240501_v01.cdf")
    again = sw.omni_month_path(2024, 5, tmp_path / "cache", session=Session(), now=now)
    assert again == path and len(calls) == 1  # a settled month is never fetched twice


def test_an_unpublished_month_is_none(tmp_path):
    class Response:
        status_code = 404

        def close(self):
            pass

    class Session:
        def get(self, url, **kwargs):
            return Response()

    assert sw.omni_month_path(2026, 11, tmp_path, session=Session(), now=datetime(2026, 10, 10)) is None


def test_derived_parameters_use_the_omni_formulas():
    series = {"v": np.array([400.0]), "np": np.array([5.0]), "t": np.array([1.0e5]),
              "bt": np.array([5.0]), "bz": np.array([-10.0])}
    derived = sw.derive_parameters(series)
    assert derived["pdyn"][0] == pytest.approx(2e-6 * 5.0 * 400.0 ** 2)
    assert derived["ey"][0] == pytest.approx(4.0)  # -V·Bz·1e-3 = 400·10·1e-3
    assert derived["beta"][0] == pytest.approx(((1e5 * 4.16e-5) + 5.34) * 5.0 / 25.0)
    assert derived["mach"][0] == pytest.approx(400.0 * np.sqrt(5.0) / (20.0 * 5.0))


def test_l1_fills_only_after_omnis_last_record_and_is_labelled():
    axis = np.datetime64("2024-05-10T00:00:00") + np.arange(6) * np.timedelta64(300, "s")
    omni = {name: np.full(6, np.nan) for name in sw.SERIES_UNITS}
    omni["bz"][:4] = [-1.0, np.nan, -3.0, -4.0]  # a real gap at index 1, published to index 3
    omni["v"][:4] = 500.0
    omni["np"][:4] = 5.0
    omni["t"][:4] = 1e5
    omni["bt"][:4] = 6.0
    l1_times = axis[0] + np.arange(0, 30) * np.timedelta64(60, "s")
    l1 = {"bz": np.full(30, -9.0), "v": np.full(30, 800.0), "np": np.full(30, 10.0),
          "t": np.full(30, 2e5), "bt": np.full(30, 12.0), "by": np.full(30, 1.0)}

    merged, segments = sw.stitch_l1_fallback(axis, omni, [(sw.SOURCE_DSCOVR, l1_times, l1)])
    assert np.isnan(merged["bz"][1])  # never patched: OMNI published this, it is a gap
    assert merged["bz"][4] == pytest.approx(-9.0) and merged["bz"][5] == pytest.approx(-9.0)
    assert merged["v"][3] == pytest.approx(500.0) and merged["v"][4] == pytest.approx(800.0)
    assert [seg.source for seg in segments["bz"]] == [sw.SOURCE_OMNI, sw.SOURCE_DSCOVR]
    assert segments["bz"][1].note == sw.L1_NOTE
    assert segments["bz"][1].start == datetime(2024, 5, 10, 0, 20)
    # Ey/β/Mach are recomputed for the L1 stretch with OMNI's formulas.
    assert merged["ey"][4] == pytest.approx(800.0 * 9.0 * 1e-3)
    assert "OMNI formulas" in segments["ey"][-1].note
    # By had no OMNI data at all, so L1 provides all of it.
    assert segments["by"][0].source == sw.SOURCE_DSCOVR


def test_the_real_time_feed_keeps_only_the_active_spacecraft():
    mag = [
        {"time_tag": "2026-10-10T12:00:00", "active": True, "source": "DSCOVR", "bt": 5.0, "bz_gsm": -2.0, "by_gsm": 1.0},
        {"time_tag": "2026-10-10T12:00:01", "active": False, "source": "ACE", "bt": 99.0, "bz_gsm": 99.0, "by_gsm": 99.0},
    ]
    wind = [{"time_tag": "2026-10-10T12:00:00", "active": True, "proton_speed": 410.0,
             "proton_density": 4.0, "proton_temperature": 90000}]
    times, series = sw.parse_rtsw(mag, wind)
    assert list(times) == [np.datetime64("2026-10-10T12:00:00")]
    assert series["bt"][0] == 5.0 and series["v"][0] == 410.0


def test_dscovr_listing_picks_the_newest_processing_run():
    html = (
        '<a href="oe_m1m_dscovr_s20250930000000_e20250930235959_p20251001000000_pub.nc">x</a>'
        '<a href="oe_m1m_dscovr_s20250930000000_e20250930235959_p20251003010351_pub.nc">x</a>'
        '<a href="oe_f1m_dscovr_s20250930000000_e20250930235959_p20251003010516_pub.nc.gz">x</a>'
    )
    listing = sw.parse_dscovr_listing(html)
    assert listing[("m1m", "20250930")].endswith("p20251003010351_pub.nc")
    assert listing[("f1m", "20250930")].endswith(".nc.gz")


def test_dscovr_netcdf_is_read_with_fill_as_nan(tmp_path):
    netCDF4 = pytest.importorskip("netCDF4")
    path = tmp_path / "oe_m1m_dscovr_s20250930000000_e20250930235959_p20251003010351_pub.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("time", 3)
        t = ds.createVariable("time", "f8", ("time",))
        t[:] = [1759190400000.0, 1759190460000.0, 1759190520000.0]
        for name, values in (("bt", [5.0, -99999.0, 6.0]), ("bz_gsm", [-1.0, -2.0, -3.0]), ("by_gsm", [0.0, 0.0, 0.0])):
            var = ds.createVariable(name, "f4", ("time",), fill_value=-99999.0)
            var[:] = values
    times, series = sw.read_dscovr_netcdf(path, "m1m")
    assert times[0] == np.datetime64("2025-09-30T00:00:00")
    assert np.isnan(series["bt"][1]) and series["bz"][2] == pytest.approx(-3.0)


# ---------------------------------------------------------------------- context


def test_storm_class_and_g_scale_thresholds():
    assert storm_class(-412) == "super"
    assert storm_class(-150) == "intense"
    assert storm_class(-60) == "moderate"
    assert storm_class(-35) == "weak"
    assert storm_class(-10) == "quiet"
    assert g_scale(9.0) == "G5"
    assert g_scale(8.667) == "G4"  # Kp 9- is still G4
    assert g_scale(5.0) == "G1"
    assert g_scale(4.667) == "below G1"


def test_summary_metrics_are_taken_after_the_event():
    event = datetime(2024, 5, 10, 17, 0)
    times = np.array(["2024-05-10T12:00", "2024-05-11T02:00", "2024-05-11T14:00"], dtype="datetime64[s]")
    context = GeomagneticContext(event_start=event, start=event - timedelta(days=1), end=event + timedelta(days=7))
    context.series["dst"] = GeoSeries("dst", times, np.array([-500.0, -412.0, -200.0]))  # -500 is before the event
    context.series["kp"] = GeoSeries("kp", times, np.array([3.0, 9.0, 8.0]))
    context.series["bz"] = GeoSeries("bz", times, np.array([-60.0, -40.0, 5.0]))
    context.series["v"] = GeoSeries("v", times, np.array([400.0, 750.0, np.nan]))
    summary = summarise(context)
    assert summary.dst_min_nt == -412.0
    assert summary.dst_min_time == datetime(2024, 5, 11, 2, 0)
    assert summary.dst_min_delay_h == pytest.approx(9.0)
    assert summary.storm_class == "super"
    assert summary.kp_max == 9.0 and summary.g_scale == "G5"
    assert summary.bz_min_nt == -40.0
    assert summary.v_max_kms == 750.0


def test_load_context_survives_a_failing_source_and_round_trips(tmp_path):
    from src.backend.space_weather.kp_index import KpRangeData

    event = datetime(2024, 5, 10, 17, 0)

    def dst_loader(start, end, **kwargs):
        times = [start + timedelta(hours=h) for h in range(48)]
        values = np.linspace(0.0, -400.0, 48)
        values[5] = np.nan
        return np.array(times, dtype=object), values, tuple(["Provisional"] * 30 + ["Real-time"] * 18)

    def kp_loader(start, end, **kwargs):
        raise RuntimeError("GFZ is down")

    def wind_loader(start, end, **kwargs):
        axis = np.datetime64(start, "s") + np.arange(4) * np.timedelta64(300, "s")
        series = {name: np.full(4, np.nan) for name in sw.SERIES_UNITS}
        series["bz"][:] = [-1.0, -30.0, -2.0, -3.0]
        return sw.SolarWindData(times=axis, series=series, segments={"bz": []}, notes=["SYM-H not yet published."])

    import pathlib

    from src.backend.space_weather.event_catalogs import parse_donki

    fixtures = pathlib.Path(__file__).parent / "fixtures" / "event_catalogs"

    def donki_loader(start, end, **kwargs):
        events = []
        for kind in ("CME", "IPS", "GST"):
            events += parse_donki(kind, json.loads((fixtures / f"donki_{kind.lower()}.json").read_text()))
        return events

    context = load_geomagnetic_context(
        event, cache_dir=tmp_path, now=datetime(2026, 10, 10), event_label="Burst list: II",
        loaders={"dst": dst_loader, "kp": kp_loader, "solar_wind": wind_loader, "donki": donki_loader},
    )
    kinds = {(m["kind"], m["time"]) for m in context.markers}
    assert ("ips", datetime(2024, 5, 10, 16, 36)) in kinds  # the shock at Earth, not the STEREO-A one
    assert ("ips", datetime(2024, 5, 10, 14, 0)) not in kinds
    assert ("gst", datetime(2024, 5, 10, 15, 0)) in kinds
    assert "Kp" in context.errors and "kp" not in context.series
    assert [seg.source for seg in context.series["dst"].segments] == ["Kyoto Dst (Provisional)", "Kyoto Dst (Real-time)"]
    assert any("Kp unavailable" in note for note in context.notes)
    assert context.summary.dst_min_nt == pytest.approx(-400.0)

    meta, arrays = context_to_project(context)
    assert all(key.startswith("geo_") for key in arrays)
    json.dumps(meta)  # plain JSON
    back = context_from_project(json.loads(json.dumps(meta)), arrays)
    assert back.event_label == "Burst list: II"
    assert {(m["kind"], m["time"]) for m in back.markers} == kinds
    assert back.summary.dst_min_time == context.summary.dst_min_time
    assert np.allclose(back.series["dst"].values, context.series["dst"].values, equal_nan=True)
    assert back.series["dst"].times[0] == context.series["dst"].times[0]
    rows = context_csv_rows(back)
    assert rows[0] == ["time_utc", "series", "value", "units", "provenance"]
    assert any(row[4] == "Kyoto Dst (Real-time)" for row in rows[1:])


def test_a_window_in_the_future_loads_nothing():
    event = datetime(2030, 1, 1)
    context = load_geomagnetic_context(event, cache_dir=".", now=datetime(2026, 10, 10), loaders={})
    assert not context.series
    assert context.notes


def test_stop_during_the_kp_request_returns_at_once():
    import threading
    import time as time_module

    from src.backend.space_weather.geomagnetic_context import GeomagneticCancelled

    release = threading.Event()
    stop = threading.Event()

    def dst_loader(start, end, **kwargs):
        return np.array([start], dtype=object), np.array([-10.0]), ("Provisional",)

    def kp_loader(start, end, **kwargs):
        stop.set()  # the user presses Stop while GFZ has not answered yet
        release.wait(10.0)
        raise RuntimeError("never reached in time")

    began = time_module.monotonic()
    with pytest.raises(GeomagneticCancelled):
        load_geomagnetic_context(
            datetime(2024, 5, 10, 17), cache_dir=".", now=datetime(2026, 10, 10),
            loaders={"dst": dst_loader, "kp": kp_loader}, cancel_cb=stop.is_set,
        )
    assert time_module.monotonic() - began < 3.0
    release.set()
