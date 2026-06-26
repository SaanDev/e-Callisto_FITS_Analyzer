"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from src.Backend.solar_data_analysis import (
    AiaArrayMap,
    AiaCompositeSpec,
    AiaMetadataRegion,
    clip_crop_bounds,
    crop_array,
    crop_maps,
    detect_active_regions,
    difference_sequence,
    label_regions_with_metadata,
    make_composite,
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
