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

from src.Backend.hmi_vector_field import (
    HmiVectorError,
    HmiVectorFrame,
    VectorOverlayOptions,
    apply_disambiguation,
    axis_transform_from_meta,
    block_reduce_mean,
    build_overlay_geometry,
    compute_field_components,
    load_vector_frames,
    nearest_vector_frame,
    segment_kind_from_filename,
)


def _uniform_frame(
    *,
    bx: float = 0.0,
    by: float = 0.0,
    bz: float = 0.0,
    shape: tuple[int, int] = (64, 64),
    scale: float = 1.0,
) -> HmiVectorFrame:
    ny, nx = shape
    transform = {
        "x_ref_pix": (nx - 1) / 2.0,
        "y_ref_pix": (ny - 1) / 2.0,
        "x_scale_arcsec_per_pix": scale,
        "y_scale_arcsec_per_pix": scale,
        "x_ref_arcsec": 0.0,
        "y_ref_arcsec": 0.0,
    }
    return HmiVectorFrame(
        time=datetime(2024, 5, 14, 16, 0, 0),
        bx=np.full(shape, bx, dtype=np.float32),
        by=np.full(shape, by, dtype=np.float32),
        bz=np.full(shape, bz, dtype=np.float32),
        axis_transform=transform,
        meta={},
    )


# -- segment identification / disambiguation --------------------------------


def test_segment_kind_from_filename_recognises_jsoc_names():
    assert segment_kind_from_filename("field.fits") == "field"
    assert segment_kind_from_filename("hmi.b_720s.20240514_160000_TAI.inclination.fits") == "inclination"
    assert segment_kind_from_filename("hmi.B_720s_2024.05.14_16_00_00_TAI.azimuth.fits") == "azimuth"
    assert segment_kind_from_filename("record.disambig.fits") == "disambig"
    assert segment_kind_from_filename("hmi.m_720s.magnetogram.fits") is None
    # 'field' must be a standalone token, not part of another word.
    assert segment_kind_from_filename("subfield.fits") is None


def test_apply_disambiguation_uses_selected_bit():
    azimuth = np.zeros((2, 2), dtype=float)
    disambig = np.asarray([[0, 4], [7, 3]], dtype=float)
    # method=2 checks bit 2 (value 4): set for 4 and 7, not for 0 and 3.
    out = apply_disambiguation(azimuth, disambig, method=2)
    assert out.tolist() == [[0.0, 180.0], [180.0, 0.0]]
    # method=0 checks bit 0: set for 7 and 3.
    out0 = apply_disambiguation(azimuth, disambig, method=0)
    assert out0.tolist() == [[0.0, 0.0], [180.0, 180.0]]


def test_apply_disambiguation_shape_mismatch_raises():
    with pytest.raises(HmiVectorError):
        apply_disambiguation(np.zeros((2, 2)), np.zeros((3, 3)))


def test_compute_field_components_known_angles():
    field = np.full((1, 1), 1000.0)
    # Inclination 0: purely line-of-sight, toward the observer.
    bx, by, bz = compute_field_components(field, np.zeros((1, 1)), np.zeros((1, 1)))
    assert bz[0, 0] == pytest.approx(1000.0)
    assert bx[0, 0] == pytest.approx(0.0, abs=1e-3)
    assert by[0, 0] == pytest.approx(0.0, abs=1e-3)

    # Inclination 90, azimuth 0: transverse, along CCD +y.
    bx, by, bz = compute_field_components(field, np.full((1, 1), 90.0), np.zeros((1, 1)))
    assert bz[0, 0] == pytest.approx(0.0, abs=1e-2)
    assert bx[0, 0] == pytest.approx(0.0, abs=1e-2)
    assert by[0, 0] == pytest.approx(1000.0, rel=1e-4)

    # Azimuth 90 (CCW from +y): transverse along -x.
    bx, by, bz = compute_field_components(field, np.full((1, 1), 90.0), np.full((1, 1), 90.0))
    assert bx[0, 0] == pytest.approx(-1000.0, rel=1e-4)
    assert by[0, 0] == pytest.approx(0.0, abs=1e-2)

    # A set disambig bit flips the transverse direction by 180 degrees.
    bx_d, by_d, _ = compute_field_components(
        field, np.full((1, 1), 90.0), np.full((1, 1), 90.0), np.full((1, 1), 4.0)
    )
    assert bx_d[0, 0] == pytest.approx(1000.0, rel=1e-4)


# -- transforms / downsampling ----------------------------------------------


def test_axis_transform_from_meta_uses_fits_keywords():
    meta = {"CDELT1": 0.5, "CDELT2": 0.5, "CRPIX1": 2048.5, "CRPIX2": 2048.5, "CRVAL1": 0.0, "CRVAL2": 0.0}
    tx = axis_transform_from_meta(meta, (4096, 4096))
    assert tx["x_scale_arcsec_per_pix"] == pytest.approx(0.5)
    assert tx["x_ref_pix"] == pytest.approx(2047.5)  # FITS CRPIX is 1-based
    assert tx["x_ref_arcsec"] == pytest.approx(0.0)


def test_axis_transform_from_meta_defaults_to_centre():
    tx = axis_transform_from_meta({}, (11, 21))
    assert tx["x_ref_pix"] == pytest.approx(10.0)
    assert tx["y_ref_pix"] == pytest.approx(5.0)
    assert tx["x_scale_arcsec_per_pix"] == 1.0


def test_block_reduce_mean_is_nan_aware():
    arr = np.asarray([[1.0, 3.0], [np.nan, 5.0]])
    out = block_reduce_mean(arr, 2)
    assert out.shape == (1, 1)
    assert out[0, 0] == pytest.approx(3.0)


# -- loading / grouping ------------------------------------------------------


def _fake_loader_factory(records: dict[str, tuple[np.ndarray, dict]]):
    def _loader(path: str):
        return records[path]

    return _loader


def test_load_vector_frames_groups_segments_by_time():
    shape = (8, 8)
    meta_a = {"T_REC": "2024.05.14_16:00:00_TAI", "CDELT1": 0.5, "CDELT2": 0.5}
    meta_b = {"T_REC": "2024.05.14_16:12:00_TAI", "CDELT1": 0.5, "CDELT2": 0.5}
    records = {
        "a.field.fits": (np.full(shape, 800.0), meta_a),
        "a.inclination.fits": (np.full(shape, 90.0), meta_a),
        "a.azimuth.fits": (np.zeros(shape), meta_a),
        "a.disambig.fits": (np.zeros(shape), meta_a),
        "b.field.fits": (np.full(shape, 500.0), meta_b),
        "b.inclination.fits": (np.zeros(shape), meta_b),
        "b.azimuth.fits": (np.zeros(shape), meta_b),
    }
    frames = load_vector_frames(records.keys(), segment_loader=_fake_loader_factory(records))
    assert len(frames) == 2
    assert frames[0].time == datetime(2024, 5, 14, 16, 0, 0)
    assert frames[1].time == datetime(2024, 5, 14, 16, 12, 0)
    # First step is fully transverse along +y, second fully vertical.
    assert float(np.nanmax(frames[0].by)) == pytest.approx(800.0, rel=1e-4)
    assert float(np.nanmax(frames[1].bz)) == pytest.approx(500.0, rel=1e-4)
    assert frames[0].downsample_factor == 1


def test_load_vector_frames_requires_a_complete_time_step():
    shape = (4, 4)
    meta = {"T_REC": "2024.05.14_16:00:00_TAI"}
    records = {
        "only.field.fits": (np.full(shape, 100.0), meta),
        "only.azimuth.fits": (np.zeros(shape), meta),
    }
    with pytest.raises(HmiVectorError):
        load_vector_frames(records.keys(), segment_loader=_fake_loader_factory(records))


def test_load_vector_frames_downsamples_large_grids():
    shape = (128, 128)
    meta = {
        "T_REC": "2024.05.14_16:00:00_TAI",
        "CDELT1": 0.5,
        "CDELT2": 0.5,
        "CRPIX1": 64.5,
        "CRPIX2": 64.5,
        "CRVAL1": 0.0,
        "CRVAL2": 0.0,
    }
    records = {
        "x.field.fits": (np.full(shape, 600.0), meta),
        "x.inclination.fits": (np.full(shape, 90.0), meta),
        "x.azimuth.fits": (np.zeros(shape), meta),
    }
    frames = load_vector_frames(
        records.keys(), segment_loader=_fake_loader_factory(records), max_dimension=32
    )
    frame = frames[0]
    assert frame.downsample_factor == 4
    assert frame.bx.shape == (32, 32)
    # The downsampled transform must preserve the frame extent: the centre of
    # the grid still maps to the same arcsec position (disk centre here).
    tx = frame.axis_transform
    assert tx["x_scale_arcsec_per_pix"] == pytest.approx(2.0)
    centre_arc = tx["x_ref_arcsec"] + ((32 - 1) / 2.0 - tx["x_ref_pix"]) * tx["x_scale_arcsec_per_pix"]
    assert centre_arc == pytest.approx(0.0, abs=1e-6)


def test_nearest_vector_frame_matches_by_time():
    early = _uniform_frame(bz=100.0)
    late = HmiVectorFrame(
        time=datetime(2024, 5, 14, 17, 0, 0),
        bx=early.bx,
        by=early.by,
        bz=early.bz,
        axis_transform=early.axis_transform,
        meta={},
    )
    frames = [early, late]
    assert nearest_vector_frame(frames, datetime(2024, 5, 14, 16, 10, 0)) is early
    assert nearest_vector_frame(frames, datetime(2024, 5, 14, 16, 50, 0)) is late
    assert nearest_vector_frame(frames, None) is early
    assert nearest_vector_frame([], datetime(2024, 5, 14)) is None
    assert (
        nearest_vector_frame(frames, datetime(2024, 5, 15, 12, 0, 0), max_delta_seconds=60)
        is None
    )


# -- overlay geometry --------------------------------------------------------


def test_build_overlay_geometry_arrows_split_by_polarity():
    ny, nx = 64, 64
    frame = _uniform_frame(bx=0.0, by=500.0, bz=300.0, shape=(ny, nx))
    # Left half negative polarity.
    bz = np.full((ny, nx), 300.0, dtype=np.float32)
    bz[:, : nx // 2] = -300.0
    frame = HmiVectorFrame(
        time=frame.time,
        bx=frame.bx,
        by=frame.by,
        bz=bz,
        axis_transform=frame.axis_transform,
        meta={},
    )
    options = VectorOverlayOptions(grid_step_px=16, min_transverse_gauss=100.0)
    geometry = build_overlay_geometry(frame, options)
    assert geometry.arrow_count == 16  # 4x4 grid of cells
    assert geometry.arrows_pos_x.size > 0
    assert geometry.arrows_neg_x.size > 0
    # NaN separators are present so canvases can draw with connect='finite'.
    assert np.isnan(geometry.arrows_pos_x).any()
    # Uniform +y transverse field: arrows point along +y (Solar Y increases).
    ys = geometry.arrows_pos_y
    finite = ys[np.isfinite(ys)]
    tail_y = finite[0]
    tip_y = finite[1]
    assert tip_y > tail_y


def test_build_overlay_geometry_threshold_filters_everything():
    frame = _uniform_frame(by=50.0, bz=100.0)
    geometry = build_overlay_geometry(
        frame, VectorOverlayOptions(grid_step_px=16, min_transverse_gauss=200.0)
    )
    assert geometry.arrow_count == 0
    assert geometry.arrows_pos_x.size == 0
    assert geometry.is_empty()


def test_build_overlay_geometry_respects_max_arrows():
    frame = _uniform_frame(by=800.0, bz=100.0, shape=(64, 64))
    geometry = build_overlay_geometry(
        frame,
        VectorOverlayOptions(grid_step_px=8, min_transverse_gauss=100.0, max_arrows=5),
    )
    assert geometry.arrow_count == 5


def test_build_overlay_geometry_streamlines_follow_uniform_field():
    frame = _uniform_frame(bx=600.0, by=0.0, bz=100.0, shape=(64, 64))
    options = VectorOverlayOptions(
        show_arrows=False,
        show_streamlines=True,
        grid_step_px=8,
        min_transverse_gauss=100.0,
        max_streamlines=4,
    )
    geometry = build_overlay_geometry(frame, options)
    assert geometry.streamline_count > 0
    assert geometry.stream_x.size > 0
    # A uniform +x field yields horizontal streamlines: y stays constant
    # within each NaN-separated segment.
    ys = geometry.stream_y
    xs = geometry.stream_x
    finite = np.isfinite(ys)
    assert np.nanstd(ys[finite]) < np.nanstd(xs[np.isfinite(xs)]) + 1e-9
    segment_y = ys[: int(np.argmax(~finite))] if (~finite).any() else ys[finite]
    assert np.nanmax(segment_y) - np.nanmin(segment_y) < 1e-6


def test_build_overlay_geometry_magnitude_layer():
    frame = _uniform_frame(by=900.0, bz=800.0, shape=(32, 32), scale=0.5)
    options = VectorOverlayOptions(
        show_arrows=False, show_magnitude=True, min_transverse_gauss=100.0
    )
    geometry = build_overlay_geometry(frame, options)
    assert geometry.magnitude_rgba is not None
    assert geometry.magnitude_rgba.shape == (32, 32, 4)
    assert geometry.magnitude_rgba[..., 3].max() > 0
    x0, y0, width, height = geometry.magnitude_rect
    # 32 px at 0.5 arcsec/px centred on disk centre.
    assert width == pytest.approx(16.0)
    assert x0 == pytest.approx(-8.0)


def test_build_overlay_geometry_all_disabled_is_empty():
    frame = _uniform_frame(by=900.0, bz=800.0)
    options = VectorOverlayOptions(show_arrows=False, show_streamlines=False, show_magnitude=False)
    geometry = build_overlay_geometry(frame, options)
    assert geometry.is_empty()


def test_vector_display_frame_is_a_plottable_hmi_magnetogram():
    from src.Backend.hmi_vector_field import vector_display_frame

    frame = _uniform_frame(by=400.0, bz=-750.0, shape=(16, 16), scale=2.0)
    display = vector_display_frame(frame)
    # Displays the vertical component and identifies as an HMI magnetogram
    # so the analyzer picks the hmimag colormap and allows the overlay.
    assert np.allclose(np.asarray(display.data), -750.0)
    assert display.instrument == "HMI"
    assert display.observatory == "SDO"
    assert str(display.meta["content"]).lower() == "magnetogram"
    assert display.date == "2024-05-14T16:00:00"
    # WCS keywords reproduce the stored grid's transform (CRPIX is 1-based).
    assert display.meta["cdelt1"] == pytest.approx(2.0)
    assert display.meta["crpix1"] == pytest.approx((16 - 1) / 2.0 + 1.0)
    assert display.meta["crval1"] == pytest.approx(0.0)


def test_overlay_options_are_hashable_for_caching():
    a = VectorOverlayOptions()
    b = VectorOverlayOptions()
    assert hash(a) == hash(b)
    assert a == b
    assert hash(VectorOverlayOptions(grid_step_px=32)) != hash(a) or VectorOverlayOptions(grid_step_px=32) != a
