"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from src.Backend import solar_data_analysis as solar_mod
from src.Backend.solar_data_analysis import (
    AiaArrayMap,
    AiaCompositeSpec,
    AiaLightcurve,
    AiaMetadataRegion,
    AiaMovieExportSpec,
    apply_display_scale,
    clip_crop_bounds,
    crop_array,
    crop_maps,
    detect_active_regions,
    difference_sequence,
    export_movie,
    extract_region_lightcurve,
    frame_exposure_time,
    frame_observation_time,
    label_regions_with_metadata,
    make_composite,
    nearest_frame_index,
    normalize_exposure,
    radio_euv_lag,
    register_aia_maps,
    render_movie_frames,
)


class FakeMap:
    observatory = "SDO"
    instrument = "AIA"
    detector = ""
    wavelength = "193 Angstrom"
    date = "2026-02-10T01:00:00"
    nickname = ""
    source = ""

    def __init__(self, data):
        self.data = np.asarray(data, dtype=float)
        self.meta = {"instrume": "AIA"}


def test_clip_crop_bounds_clamps_and_sorts():
    assert clip_crop_bounds((10, 20), (18, -4, 12, 2)) == (0, 18, 2, 10)


def test_crop_array_returns_expected_slice():
    arr = np.arange(100, dtype=float).reshape(10, 10)
    cropped = crop_array(arr, (2, 6, 1, 5))
    assert cropped.shape == (4, 4)
    assert cropped[0, 0] == 12.0
    assert cropped[-1, -1] == 45.0


def test_difference_sequence_running_and_base_modes():
    frames = [FakeMap(np.full((3, 3), value, dtype=float)) for value in (1.0, 4.0, 9.0)]
    running = difference_sequence(frames, mode="running")
    base = difference_sequence(frames, mode="base")

    np.testing.assert_allclose(running[0], np.full((3, 3), 3.0))
    np.testing.assert_allclose(running[1], np.full((3, 3), 3.0))
    np.testing.assert_allclose(running[2], np.full((3, 3), 5.0))
    np.testing.assert_allclose(base[0], np.zeros((3, 3)))
    np.testing.assert_allclose(base[2], np.full((3, 3), 8.0))


def test_crop_maps_returns_map_like_wrappers():
    frames = [FakeMap(np.arange(25).reshape(5, 5))]
    cropped = crop_maps(frames, (1, 4, 2, 5))
    assert isinstance(cropped[0], AiaArrayMap)
    assert cropped[0].data.shape == (3, 3)
    assert cropped[0].instrument == "AIA"


def test_crop_maps_adjusts_reference_pixel_metadata():
    frame = FakeMap(np.arange(100).reshape(10, 10))
    frame.meta.update({"crpix1": 5.5, "crpix2": 5.5})
    cropped = crop_maps([frame], (2, 7, 3, 8))[0]

    assert cropped.meta["crpix1"] == 3.5
    assert cropped.meta["crpix2"] == 2.5
    assert cropped._crop_origin_px == (2, 3)


def test_make_composite_normalizes_three_channels():
    frames = [
        FakeMap(np.arange(16).reshape(4, 4)),
        FakeMap(np.arange(16, 32).reshape(4, 4)),
        FakeMap(np.arange(32, 48).reshape(4, 4)),
    ]
    composite = make_composite(frames, AiaCompositeSpec(frame_indexes=(0, 1, 2)))
    assert composite.data.shape == (4, 4, 3)
    assert np.nanmin(composite.data) >= 0.0
    assert np.nanmax(composite.data) <= 1.0


def test_detect_active_regions_reports_bright_components():
    arr = np.zeros((30, 30), dtype=float)
    arr[4:9, 5:10] = 100.0
    arr[18:24, 20:26] = 200.0
    regions = detect_active_regions(arr, threshold_percentile=95.0, min_area_px=8, max_regions=5)

    assert len(regions) == 2
    assert regions[0].peak == 200.0
    assert regions[0].bbox == (20, 26, 18, 24)
    assert regions[1].bbox == (5, 10, 4, 9)


def test_label_regions_with_metadata_matches_nearest_label():
    arr = np.zeros((20, 20), dtype=float)
    arr[8:12, 8:12] = 50.0
    regions = detect_active_regions(arr, threshold_percentile=90.0, min_area_px=4)
    metadata = [
        AiaMetadataRegion(
            label="NOAA 12345",
            noaa_number="12345",
            center_x_arcsec=regions[0].centroid_x_arcsec + 2.0,
            center_y_arcsec=regions[0].centroid_y_arcsec,
            start_time=datetime(2026, 2, 10),
            source="HEK",
        )
    ]

    labeled = label_regions_with_metadata(regions, metadata, max_distance_arcsec=10.0)
    assert labeled[0].label == "NOAA 12345"
    assert labeled[0].noaa_number == "12345"
    assert labeled[0].metadata_source == "HEK"


def test_render_movie_frames_returns_rgb_uint8():
    frames = [FakeMap(np.full((5, 5), value, dtype=float)) for value in (1.0, 2.0)]
    rendered = render_movie_frames(frames, mode="raw")
    assert len(rendered) == 2
    assert rendered[0].shape == (5, 5, 3)
    assert rendered[0].dtype == np.uint8


def test_render_movie_frames_accepts_aia_colormap_name():
    frames = [FakeMap(np.arange(25, dtype=float).reshape(5, 5))]
    rendered = render_movie_frames(frames, mode="raw", colormap_name="sdoaia304")
    assert rendered[0].shape == (5, 5, 3)
    assert rendered[0].dtype == np.uint8


def test_export_movie_gif_uses_duration_not_fps(monkeypatch, tmp_path):
    captured = {}

    class FakeImageIO:
        @staticmethod
        def mimsave(path, frames, **kwargs):
            captured["path"] = path
            captured["frames"] = frames
            captured["kwargs"] = kwargs

    monkeypatch.setattr(solar_mod, "_import_imageio_v2", lambda: FakeImageIO)

    out_path = tmp_path / "aia.gif"
    export_movie([FakeMap(np.arange(16).reshape(4, 4))], AiaMovieExportSpec(path=str(out_path), fps=5.0))

    assert captured["path"] == str(out_path)
    assert len(captured["frames"]) == 1
    assert captured["kwargs"] == {"duration": 0.2}


def test_export_movie_mp4_forces_ffmpeg_writer(monkeypatch, tmp_path):
    captured = {}

    class FakeWriter:
        def __init__(self):
            self.frames = []
            self.closed = False

        def append_data(self, frame):
            self.frames.append(np.asarray(frame))

        def close(self):
            self.closed = True

    writer = FakeWriter()

    class FakeImageIO:
        @staticmethod
        def get_writer(path, **kwargs):
            captured["path"] = path
            captured["kwargs"] = kwargs
            return writer

    monkeypatch.setattr(solar_mod, "_ensure_imageio_ffmpeg_available", lambda: None)
    monkeypatch.setattr(solar_mod, "_import_imageio_v2", lambda: FakeImageIO)

    out_path = tmp_path / "aia.mp4"
    frames = [FakeMap(np.ones((4, 4))), FakeMap(np.full((4, 4), 2.0))]
    export_movie(frames, AiaMovieExportSpec(path=str(out_path), fps=8.0))

    assert captured["path"] == str(out_path)
    assert captured["kwargs"]["format"] == "FFMPEG"
    assert captured["kwargs"]["mode"] == "I"
    assert captured["kwargs"]["fps"] == 8.0
    assert captured["kwargs"]["codec"] == "libx264"
    assert writer.closed is True
    assert len(writer.frames) == 2
    assert writer.frames[0].dtype == np.uint8


def test_export_movie_mp4_reports_missing_ffmpeg(monkeypatch, tmp_path):
    def fail_ffmpeg_import():
        raise RuntimeError("MP4 export requires imageio-ffmpeg.")

    monkeypatch.setattr(solar_mod, "_ensure_imageio_ffmpeg_available", fail_ffmpeg_import)

    with pytest.raises(RuntimeError, match="imageio-ffmpeg"):
        export_movie([FakeMap(np.ones((4, 4)))], AiaMovieExportSpec(path=str(tmp_path / "aia.mp4")))


def _aia_frame(data, *, exptime=2.0, date="2026-02-10T01:00:00"):
    frame = FakeMap(np.asarray(data, dtype=float))
    frame.meta = {"instrume": "AIA", "exptime": exptime}
    frame.date = date
    return frame


def test_frame_exposure_time_reads_header():
    assert frame_exposure_time(_aia_frame(np.ones((2, 2)), exptime=2.9)) == 2.9
    no_exp = FakeMap(np.ones((2, 2)))
    no_exp.meta = {"instrume": "AIA"}
    assert frame_exposure_time(no_exp) is None


def test_normalize_exposure_yields_dn_per_second():
    frame = _aia_frame(np.full((4, 4), 10.0), exptime=2.0)
    out = normalize_exposure([frame])
    assert isinstance(out[0], AiaArrayMap)
    np.testing.assert_allclose(out[0].data, np.full((4, 4), 5.0))
    assert out[0].meta["bunit"] == "DN/s"
    # Missing exposure -> passthrough (no divide-by-zero).
    bare = FakeMap(np.full((2, 2), 7.0))
    bare.meta = {"instrume": "AIA"}
    np.testing.assert_allclose(normalize_exposure([bare])[0].data, np.full((2, 2), 7.0))


def test_frame_observation_time_parses_iso():
    when = frame_observation_time(_aia_frame(np.ones((2, 2)), date="2026-02-10T01:23:45"))
    assert when == datetime(2026, 2, 10, 1, 23, 45)


def test_extract_region_lightcurve_mean_dn_per_second():
    frames = [
        _aia_frame(np.full((6, 6), 10.0), exptime=2.0, date="2026-02-10T01:00:00"),
        _aia_frame(np.full((6, 6), 40.0), exptime=2.0, date="2026-02-10T01:02:00"),
        _aia_frame(np.full((6, 6), 20.0), exptime=2.0, date="2026-02-10T01:04:00"),
    ]
    lc = extract_region_lightcurve(frames, bounds=(1, 5, 1, 5), normalize=True)
    assert isinstance(lc, AiaLightcurve)
    # DN/s = DN / exptime; mean over the ROI.
    np.testing.assert_allclose(lc.values, [5.0, 20.0, 10.0])
    assert lc.unit == "DN/s"
    assert lc.peak_index() == 1
    assert lc.peak_time() == datetime(2026, 2, 10, 1, 2, 0)


def test_extract_region_lightcurve_without_normalization():
    frames = [_aia_frame(np.full((4, 4), 8.0), exptime=4.0)]
    lc = extract_region_lightcurve(frames, normalize=False, statistic="sum")
    assert lc.unit == "DN"
    assert lc.statistic == "sum"
    assert lc.values[0] == pytest.approx(8.0 * 16)


def test_nearest_frame_index_picks_closest_time():
    times = [
        datetime(2026, 2, 10, 1, 0, 0),
        datetime(2026, 2, 10, 1, 5, 0),
        datetime(2026, 2, 10, 1, 10, 0),
    ]
    assert nearest_frame_index(times, datetime(2026, 2, 10, 1, 4, 0)) == 1
    assert nearest_frame_index([None, None], datetime(2026, 2, 10, 1, 4, 0)) == -1


def test_radio_euv_lag_seconds():
    onset = datetime(2026, 2, 10, 1, 0, 0)
    peak = datetime(2026, 2, 10, 1, 1, 30)
    assert radio_euv_lag(onset, peak) == 90.0
    assert radio_euv_lag(None, peak) is None


def test_register_aia_maps_uses_injected_register():
    frames = [_aia_frame(np.ones((4, 4))), _aia_frame(np.ones((4, 4)))]
    calls = []

    def fake_register(frame):
        calls.append(frame)
        return AiaArrayMap(np.asarray(frame.data) * 2.0, frame)

    out = register_aia_maps(frames, aiapy_register=fake_register)
    assert len(out) == 2 and len(calls) == 2
    np.testing.assert_allclose(out[0].data, np.full((4, 4), 2.0))


def test_register_aia_maps_falls_back_on_error():
    frames = [_aia_frame(np.ones((4, 4)))]

    def boom(_frame):
        raise RuntimeError("registration failed")

    # On a per-frame failure the original frame is preserved, never dropped.
    assert register_aia_maps(frames, aiapy_register=boom) == frames


def test_apply_display_scale_linear_and_log():
    arr = np.array([[1.0, 10.0, 100.0, 1000.0]])
    np.testing.assert_array_equal(apply_display_scale(arr, "linear"), arr)
    log = apply_display_scale(arr, "log")
    assert log[0, -1] == pytest.approx(3.0)        # log10(1000)
    assert log[0, 1] == pytest.approx(1.0)         # log10(10)
    # RGB composites (3-D) are returned unchanged.
    rgb = np.ones((2, 2, 3))
    np.testing.assert_array_equal(apply_display_scale(rgb, "log"), rgb)


def test_render_movie_log_brighter_than_linear_for_high_dynamic_range():
    # A high-dynamic-range frame: faint structure is crushed to black in linear
    # but lifts into mid-tones in log (matching the on-screen preview).
    vals = np.array([1, 2, 5, 10, 30, 80, 200, 600, 2000, 9000], dtype=float)
    frame = FakeMap(np.tile(vals, (10, 1)))
    lin = render_movie_frames([frame], scale="linear", percentile_low=0, percentile_high=100,
                              colormap_name="gray")[0]
    log = render_movie_frames([frame], scale="log", percentile_low=0, percentile_high=100,
                              colormap_name="gray")[0]
    assert float(log.mean()) > float(lin.mean()) * 1.5


def test_pad_frame_to_block_preserves_pixels():
    frame = np.full((1741, 1624, 3), 200, dtype=np.uint8)
    padded = solar_mod._pad_frame_to_block(frame, 16)
    assert padded.shape == (1744, 1632, 3)
    assert padded.shape[0] % 16 == 0 and padded.shape[1] % 16 == 0
    assert (padded[:1741, :1624] == 200).all()     # original pixels intact
    assert (padded[1741:, :] == 0).all()           # black padding
    # Already-divisible frames are untouched.
    ok = np.ones((32, 16, 3), dtype=np.uint8)
    assert solar_mod._pad_frame_to_block(ok, 16).shape == (32, 16, 3)
