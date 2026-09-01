"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

pytest.importorskip("astropy")
pytest.importorskip("sunpy.map")

import astropy.units as u
import numpy as np
import sunpy.map
from astropy.coordinates import SkyCoord
from sunpy.coordinates import get_earth

from src.Backend.derotation import (
    MODE_REPROJECT,
    MODE_TRACK,
    DerotationSpec,
    derotate_region,
    supports_derotation,
    track_region_bounds,
)


def make_frame(hours: float, *, size: int = 256, scale: float = 4.8):
    """A synthetic AIA-like map with one bright blob, observed from Earth.

    ``sunpy``'s AIAMap requires a wavelength, so it is always supplied — without
    it map construction fails inside the colormap lookup.
    """
    obstime = f"2024-05-10T{int(hours):02d}:00:00"
    yy, xx = np.mgrid[0:size, 0:size]
    data = np.exp(
        -(((xx - size * 0.6) ** 2 + (yy - size * 0.55) ** 2) / (2 * (size * 0.04) ** 2))
    ).astype(np.float32)
    header = sunpy.map.make_fitswcs_header(
        data,
        SkyCoord(
            0 * u.arcsec,
            0 * u.arcsec,
            obstime=obstime,
            observer=get_earth(obstime),
            frame="helioprojective",
        ),
        scale=[scale, scale] * u.arcsec / u.pix,
        instrument="AIA",
        wavelength=193 * u.angstrom,
        exposure=2.0 * u.s,
    )
    header["lvl_num"] = 1.0
    return sunpy.map.Map(data, header)


@pytest.fixture(scope="module")
def frames():
    return [make_frame(h) for h in (0, 2, 4, 6)]


REGION = (200.0, 400.0, 100.0, 300.0)


class ArrayLikeMap:
    """Stand-in for AiaArrayMap: data and meta only, no world coordinates."""

    def __init__(self):
        self.data = np.zeros((8, 8))
        self.meta = {}


def test_supports_derotation_accepts_real_maps(frames):
    assert all(supports_derotation(frame) for frame in frames)


def test_supports_derotation_rejects_derived_arrays():
    assert supports_derotation(ArrayLikeMap()) is False


def test_tracked_bounds_follow_solar_rotation_westward(frames):
    spec = DerotationSpec(bounds_arcsec=REGION, reference_index=0)
    bounds = track_region_bounds(frames, spec)

    assert all(b is not None for b in bounds)
    centres = [(b[0] + b[1]) / 2 for b in bounds]
    # A feature east of centre drifts west (increasing Tx) as the Sun rotates.
    assert centres == sorted(centres)
    assert centres[0] == pytest.approx(300.0, abs=1.0)
    assert centres[-1] > centres[0] + 40.0


def test_tracked_bounds_keep_the_requested_region_size(frames):
    spec = DerotationSpec(bounds_arcsec=REGION, reference_index=0)
    for bound in track_region_bounds(frames, spec):
        assert bound[1] - bound[0] == pytest.approx(200.0)
        assert bound[3] - bound[2] == pytest.approx(200.0)


@pytest.mark.parametrize("mode", [MODE_TRACK, MODE_REPROJECT])
def test_derotated_frames_share_one_array_shape(frames, mode):
    """Playback, differencing and movie export all need a uniform shape.

    An arcsec-sized cut rounds to a different pixel count from frame to frame,
    which is why the window is sized in pixels off the reference submap.
    """
    result = derotate_region(frames, DerotationSpec(bounds_arcsec=REGION, mode=mode))
    shapes = {frame.data.shape for frame in result.frames}
    assert len(shapes) == 1
    assert len(result.frames) == len(frames)


@pytest.mark.parametrize("mode", [MODE_TRACK, MODE_REPROJECT])
def test_derotated_frames_keep_their_own_observation_times(frames, mode):
    """``reproject_to`` adopts the target WCS wholesale, including its date.

    Left alone, every output frame would claim the reference frame's time and
    the light-curve axis and movie timestamps would collapse to one instant.
    """
    result = derotate_region(frames, DerotationSpec(bounds_arcsec=REGION, mode=mode))
    times = [frame.date.isot for frame in result.frames]
    assert times == [frame.date.isot for frame in frames]
    assert len(set(times)) == len(frames)


@pytest.mark.parametrize("mode", [MODE_TRACK, MODE_REPROJECT])
def test_derotated_frames_keep_their_exposure_time(frames, mode):
    """Difference imaging normalises by EXPTIME, so losing it changes numbers."""
    result = derotate_region(frames, DerotationSpec(bounds_arcsec=REGION, mode=mode))
    assert all(float(frame.meta["exptime"]) == pytest.approx(2.0) for frame in result.frames)


@pytest.mark.parametrize("mode", [MODE_TRACK, MODE_REPROJECT])
def test_derotated_frames_keep_full_world_coordinates(frames, mode):
    """The graticule, hover read-out and limb overlay all need a live WCS."""
    result = derotate_region(frames, DerotationSpec(bounds_arcsec=REGION, mode=mode))
    assert all(supports_derotation(frame) for frame in result.frames)


def make_rotating_sequence(hours=(0, 2, 4, 6), *, blob_arcsec=(300.0, 200.0), size=256, blob_width=0.04):
    """A sequence where the bright blob genuinely rides the solar surface.

    The blob is painted at the differentially rotated position of one fixed
    point, so a derotated window should hold it steady while a fixed window
    watches it drift away.
    """
    from sunpy.physics.differential_rotation import solar_rotate_coordinate

    reference = make_frame(hours[0], size=size)
    origin = SkyCoord(
        blob_arcsec[0] * u.arcsec, blob_arcsec[1] * u.arcsec, frame=reference.coordinate_frame
    )

    out = []
    for hour in hours:
        blank = make_frame(hour, size=size)
        rotated = solar_rotate_coordinate(origin, observer=blank.observer_coordinate)
        px, py = blank.wcs.world_to_pixel(
            SkyCoord(rotated.Tx, rotated.Ty, frame=blank.coordinate_frame)
        )
        yy, xx = np.mgrid[0:size, 0:size]
        data = np.exp(
            -(((xx - float(px)) ** 2 + (yy - float(py)) ** 2) / (2 * (size * blob_width) ** 2))
        ).astype(np.float32)
        out.append(sunpy.map.Map(data, blank.meta))
    return out


@pytest.mark.parametrize("mode", [MODE_TRACK, MODE_REPROJECT])
def test_derotation_holds_the_feature_steady(mode):
    """The point of the feature: the blob stays put in the cut-out."""
    sequence = make_rotating_sequence()
    result = derotate_region(sequence, DerotationSpec(bounds_arcsec=REGION, mode=mode))

    peaks = [float(np.nanmax(frame.data)) for frame in result.frames]
    assert all(peak > 0.9 for peak in peaks)

    # The brightest pixel stays at essentially the same place in every cut-out.
    positions = [np.unravel_index(int(np.nanargmax(f.data)), f.data.shape) for f in result.frames]
    rows = [p[0] for p in positions]
    cols = [p[1] for p in positions]
    assert max(rows) - min(rows) <= 2
    assert max(cols) - min(cols) <= 2


def test_a_fixed_window_loses_the_feature_that_derotation_keeps():
    """The comparison that justifies the feature.

    Cropping the same rectangle out of every frame without derotation lets the
    region drift off the feature, which is exactly how a light curve silently
    starts measuring neighbouring solar surface.
    """
    # A compact feature in a tight box: over 12 hours solar rotation carries it
    # about 100 arcsec, which is well outside an 80 arcsec window.
    tight = (260.0, 340.0, 160.0, 240.0)
    sequence = make_rotating_sequence(hours=(0, 6, 12), blob_width=0.015)

    bottom_left = SkyCoord(
        tight[0] * u.arcsec, tight[2] * u.arcsec, frame=sequence[0].coordinate_frame
    )
    top_right = SkyCoord(
        tight[1] * u.arcsec, tight[3] * u.arcsec, frame=sequence[0].coordinate_frame
    )
    fixed_peaks = [
        float(np.nanmax(frame.submap(bottom_left, top_right=top_right).data))
        for frame in sequence
    ]

    derotated = derotate_region(sequence, DerotationSpec(bounds_arcsec=tight, mode=MODE_TRACK))
    derotated_peaks = [float(np.nanmax(frame.data)) for frame in derotated.frames]

    assert fixed_peaks[0] > 0.9 and derotated_peaks[0] > 0.9
    assert fixed_peaks[-1] < 0.5
    assert derotated_peaks[-1] > 0.9


def test_reprojection_lands_on_the_reference_grid(frames):
    """Every reprojected frame shares the reference frame's WCS."""
    result = derotate_region(frames, DerotationSpec(bounds_arcsec=REGION, mode=MODE_REPROJECT))
    reference_wcs = result.frames[0].wcs
    for frame in result.frames[1:]:
        assert frame.wcs.wcs.crval == pytest.approx(reference_wcs.wcs.crval)
        assert frame.wcs.wcs.cdelt == pytest.approx(reference_wcs.wcs.cdelt)


def test_reprojection_fills_the_region_without_empty_edges(frames):
    """The padded cut exists so interpolation reaches the output edge."""
    result = derotate_region(
        frames, DerotationSpec(bounds_arcsec=REGION, mode=MODE_REPROJECT, pad_arcsec=60.0)
    )
    for frame in result.frames:
        assert np.mean(np.isfinite(frame.data)) > 0.99


def test_result_reports_reference_epoch_and_kept_frames(frames):
    result = derotate_region(frames, DerotationSpec(bounds_arcsec=REGION, reference_index=1))
    assert result.reference_time.startswith("2024-05-10T02:00")
    assert result.kept_indexes == [0, 1, 2, 3]
    assert len(result.centres_arcsec) == len(frames)


def test_progress_is_reported_once_per_frame(frames):
    seen = []
    derotate_region(
        frames,
        DerotationSpec(bounds_arcsec=REGION),
        progress_cb=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_cancelling_stops_immediately(frames):
    result = derotate_region(
        frames, DerotationSpec(bounds_arcsec=REGION), cancel_cb=lambda: True
    )
    assert result.cancelled is True
    assert result.frames == []


def test_frames_that_rotate_out_of_view_are_dropped_with_a_reason():
    """A region near the west limb leaves a small frame as the Sun turns."""
    long_run = [make_frame(h, size=192) for h in (0, 6, 12, 18)]
    result = derotate_region(
        long_run, DerotationSpec(bounds_arcsec=(300.0, 400.0, 0.0, 100.0), mode=MODE_TRACK)
    )
    assert result.dropped
    assert len(result.frames) < len(long_run)
    assert result.kept_indexes == sorted(result.kept_indexes)
    assert any("rotated outside" in reason for _, reason in result.dropped)
    assert any("dropped" in warning for warning in result.warnings)


def test_derived_arrays_are_refused_with_an_actionable_message(frames):
    with pytest.raises(ValueError, match="derived arrays"):
        derotate_region(
            [ArrayLikeMap(), ArrayLikeMap()], DerotationSpec(bounds_arcsec=REGION)
        )


def test_region_outside_the_frame_says_so(frames):
    with pytest.raises(ValueError, match="outside the reference frame"):
        derotate_region(frames, DerotationSpec(bounds_arcsec=(5000.0, 5200.0, 0.0, 200.0)))


def test_a_single_frame_cannot_be_derotated(frames):
    with pytest.raises(ValueError, match="at least two frames"):
        derotate_region(frames[:1], DerotationSpec(bounds_arcsec=REGION))


def test_unknown_mode_is_rejected(frames):
    with pytest.raises(ValueError, match="Unknown derotation mode"):
        derotate_region(frames, DerotationSpec(bounds_arcsec=REGION, mode="wobble"))


def test_rotation_callable_is_injectable(frames):
    """The geometry can be driven without sunpy's rotation model."""
    calls = []

    def fake_rotate(coordinate, observer=None, **kwargs):
        calls.append(observer)
        return coordinate

    bounds = track_region_bounds(
        frames, DerotationSpec(bounds_arcsec=REGION), rotate_fn=fake_rotate
    )
    assert len(calls) == len(frames)
    # With rotation stubbed out the region never moves.
    assert all(b == pytest.approx(bounds[0]) for b in bounds)
