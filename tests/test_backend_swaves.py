"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

pytest.importorskip("cdflib")
pytest.importorskip("requests")

from src.Backend.swaves import (
    BASE_URL,
    FIRST_ARCHIVE_DAY,
    SPACECRAFT_AHEAD,
    SPACECRAFT_BEHIND,
    SwavesArchiveError,
    SwavesCancelled,
    SwavesNotFoundError,
    SwavesPayload,
    _YEAR_INDEX_CACHE,
    band_boundary_orientation,
    build_swaves_filename,
    days_in_window,
    download_swaves_day,
    format_frequency_khz,
    format_log_frequency,
    frequency_bands,
    load_swaves_window,
    log_frequency_ticks,
    normalize_spacecraft,
    orient_to_frequency_axis,
    read_swaves_cdf,
    resample_log_frequency,
    resolve_swaves_cache_path,
    resolve_swaves_url,
    spacecraft_has_data,
    suggested_log_rows,
)

from tests.helpers_swaves import (
    CHANNEL_COUNT,
    FILL,
    FakeResponse,
    FakeSession,
    LFR_COUNT,
    build_fake_cdf,
    make_background,
    make_frequency_axis,
    make_intensity,
    patch_reader,
)


@pytest.fixture(autouse=True)
def _clear_year_cache():
    _YEAR_INDEX_CACHE.clear()
    yield
    _YEAR_INDEX_CACHE.clear()


# ---------------------------------------------------------------------------
# Archive location
# ---------------------------------------------------------------------------


def test_normalize_spacecraft_accepts_common_spellings():
    for value in ("a", "A", "ahead", "STEREO-A", "sta"):
        assert normalize_spacecraft(value) == SPACECRAFT_AHEAD
    for value in ("b", "behind", "STEREO-B", "stb"):
        assert normalize_spacecraft(value) == SPACECRAFT_BEHIND
    with pytest.raises(ValueError):
        normalize_spacecraft("psp")


def test_resolve_url_and_cache_path_use_the_archive_layout(tmp_path):
    day = date(2012, 3, 7)
    assert build_swaves_filename(day) == "stereo_level2_swaves_20120307_v02.cdf"
    assert resolve_swaves_url(day) == f"{BASE_URL}2012/stereo_level2_swaves_20120307_v02.cdf"

    cache_path = resolve_swaves_cache_path(day, tmp_path)
    assert cache_path.parent.name == "2012"
    assert cache_path.name == "stereo_level2_swaves_20120307_v02.cdf"


def test_download_writes_atomically_then_reuses_the_cache(tmp_path):
    day = date(2012, 3, 7)
    url = resolve_swaves_url(day)
    session = FakeSession({url: FakeResponse(body=b"CDF-PAYLOAD" * 40)})

    first = download_swaves_day(day, tmp_path, session=session)
    assert open(first, "rb").read() == b"CDF-PAYLOAD" * 40
    # The temporary sibling must not survive a successful download.
    assert not list(tmp_path.rglob("*.part"))
    assert len(session.requested) == 1

    second = download_swaves_day(day, tmp_path, session=session)
    assert second == first
    assert len(session.requested) == 1, "a cached day must not be re-requested"


def test_download_reports_byte_progress(tmp_path):
    day = date(2012, 3, 7)
    session = FakeSession({resolve_swaves_url(day): FakeResponse(body=b"x" * 1024)})

    seen: list[tuple[str, int]] = []
    download_swaves_day(day, tmp_path, session=session, progress_cb=lambda m, v: seen.append((m, v)))

    assert seen, "expected at least one progress callback"
    assert seen[-1][1] == 100


def test_download_falls_back_to_the_directory_listing_when_the_version_moves(tmp_path):
    day = date(2012, 3, 7)
    listed = "stereo_level2_swaves_20120307_v03.cdf"
    session = FakeSession(
        {
            resolve_swaves_url(day): FakeResponse(status_code=404),
            f"{BASE_URL}2012/": FakeResponse(text=f'<a href="{listed}">{listed}</a>'),
            f"{BASE_URL}2012/{listed}": FakeResponse(body=b"V3"),
        }
    )

    path = download_swaves_day(day, tmp_path, session=session)
    assert path.endswith(listed)
    assert open(path, "rb").read() == b"V3"


def test_download_raises_not_found_when_the_listing_has_no_such_day(tmp_path):
    day = date(2012, 3, 7)
    session = FakeSession(
        {
            resolve_swaves_url(day): FakeResponse(status_code=404),
            f"{BASE_URL}2012/": FakeResponse(text="<a href='00readme.txt'>readme</a>"),
        }
    )
    with pytest.raises(SwavesNotFoundError):
        download_swaves_day(day, tmp_path, session=session)


def test_download_rejects_days_before_the_archive_starts(tmp_path):
    session = FakeSession({})
    with pytest.raises(SwavesNotFoundError):
        download_swaves_day(FIRST_ARCHIVE_DAY - timedelta(days=1), tmp_path, session=session)
    assert session.requested == [], "no request should be made for an out-of-range day"


def test_download_surfaces_server_errors(tmp_path):
    day = date(2012, 3, 7)
    session = FakeSession({resolve_swaves_url(day): FakeResponse(status_code=500)})
    with pytest.raises(SwavesArchiveError):
        download_swaves_day(day, tmp_path, session=session)
    assert not list(tmp_path.rglob("*.part"))


# ---------------------------------------------------------------------------
# Frequency handling
# ---------------------------------------------------------------------------


def test_frequency_bands_detects_the_lfr_hfr_split():
    freqs = make_frequency_axis()
    assert frequency_bands(freqs) == [(0, LFR_COUNT), (LFR_COUNT, CHANNEL_COUNT)]
    # The axis really is non-monotonic at the join.
    assert float(freqs[LFR_COUNT]) < float(freqs[LFR_COUNT - 1])


def test_frequency_bands_handles_a_single_monotonic_run():
    assert frequency_bands(np.array([1.0, 2.0, 3.0])) == [(0, 3)]
    assert frequency_bands(np.array([])) == []


def test_suggested_rows_resolve_the_finest_native_step():
    rows = suggested_log_rows(make_frequency_axis())
    assert 512 <= rows <= 2048
    # The HFR band's 50 kHz step at 16 MHz is the tightest, needing ~2800 rows,
    # so the cap should be in force.
    assert rows == 2048


def test_resample_round_trips_within_a_fraction_of_a_dB():
    freqs = make_frequency_axis()
    data = make_intensity(freqs, 40, seed=3).T  # (n_freq, n_time)

    grid, resampled = resample_log_frequency(data, freqs, nrows=2048)

    assert grid.shape == (2048,)
    assert resampled.shape == (2048, 40)
    assert np.all(np.diff(grid) > 0)

    back = np.vstack(
        [np.interp(np.log10(freqs), np.log10(grid), resampled[:, t]) for t in range(data.shape[1])]
    ).T
    error = np.abs(back - data)
    assert float(np.nanmedian(error)) < 0.05
    assert float(np.nanpercentile(error, 99)) < 1.0


def test_resample_covers_every_row_of_the_real_axis():
    # Bands created by a descending step always overlap, so the real product
    # leaves no masked stripe anywhere in the grid.
    freqs = make_frequency_axis()
    data = make_intensity(freqs, 12, seed=5).T

    _grid, resampled = resample_log_frequency(data, freqs, nrows=512)
    assert np.all(np.isfinite(resampled))


def test_resample_masks_rows_a_skipped_band_would_have_covered():
    # A trailing band of a single channel cannot be interpolated, so the grid
    # rows only it reached stay NaN rather than being invented.
    freqs = np.array([10.0, 20.0, 30.0, 5.0])
    data = np.ones((4, 3), dtype=float)

    grid, resampled = resample_log_frequency(data, freqs, nrows=128)

    below = grid < 10.0
    assert np.any(below)
    assert np.all(np.isnan(resampled[below]))
    assert np.all(np.isfinite(resampled[grid >= 10.0]))


def test_resample_averages_the_overlap_between_bands():
    # Band A covers 10-100 at value 0; band B covers 50-500 at value 10.
    freqs = np.concatenate([np.linspace(10.0, 100.0, 10), np.linspace(50.0, 500.0, 10)])
    data = np.vstack([np.zeros((10, 3)), np.full((10, 3), 10.0)])

    grid, resampled = resample_log_frequency(data, freqs, nrows=256)

    overlap = (grid > 55.0) & (grid < 95.0)
    assert np.allclose(resampled[overlap], 5.0)


def test_resample_rejects_a_mismatched_frequency_axis():
    with pytest.raises(ValueError):
        resample_log_frequency(np.zeros((4, 3)), np.array([1.0, 2.0]))


# ---------------------------------------------------------------------------
# Orientation guard
# ---------------------------------------------------------------------------


def test_orientation_guard_leaves_aligned_data_alone():
    freqs = make_frequency_axis()
    intensity = make_intensity(freqs, 30, seed=1).T
    background = make_background(freqs, 30).T.astype(float)

    result = orient_to_frequency_axis(intensity, background, freqs)
    assert np.allclose(result, intensity)


def test_orientation_guard_flips_mirrored_data():
    freqs = make_frequency_axis()
    intensity = make_intensity(freqs, 30, seed=1).T
    background = make_background(freqs, 30).T.astype(float)

    result = orient_to_frequency_axis(intensity[::-1, :], background[::-1, :], freqs)
    assert np.allclose(result, intensity)


def test_orientation_guard_does_not_flip_without_a_band_step():
    # No receiver step to key on: pass the data through rather than guess.
    freqs = make_frequency_axis()
    intensity = make_intensity(freqs, 30, seed=1).T
    smooth = np.tile(80.0 - 12.0 * np.log10(np.maximum(freqs, 1.0)), (30, 1)).T.astype(float)

    assert np.allclose(orient_to_frequency_axis(intensity, smooth, freqs), intensity)


def test_orientation_guard_is_inert_on_degenerate_input():
    intensity = np.arange(12, dtype=float).reshape(4, 3)
    flat = np.zeros((4, 3), dtype=float)
    assert np.allclose(orient_to_frequency_axis(intensity, flat, np.arange(4.0)), intensity)


def test_band_boundary_orientation_reports_no_verdict_on_a_smooth_profile():
    profile = np.linspace(80.0, 10.0, 367)
    assert band_boundary_orientation(profile, 48) == 0


def test_band_boundary_orientation_detects_each_direction():
    profile = np.linspace(80.0, 10.0, 367)
    stepped = profile.copy()
    stepped[48:] += 56.0
    assert band_boundary_orientation(stepped, 48) == 1
    assert band_boundary_orientation(stepped[::-1], 48) == -1


# ---------------------------------------------------------------------------
# CDF decoding
# ---------------------------------------------------------------------------


def test_read_swaves_cdf_returns_freq_major_data(monkeypatch):
    day = date(2012, 3, 7)
    patch_reader(monkeypatch, lambda _path: build_fake_cdf(day, n_time=60))

    decoded = read_swaves_cdf("ignored.cdf", "ahead")

    assert decoded.spacecraft == SPACECRAFT_AHEAD
    assert decoded.day == day
    assert decoded.intensity_db.shape == (CHANNEL_COUNT, 60)
    assert decoded.freqs_khz.shape == (CHANNEL_COUNT,)
    assert decoded.times.shape == (60,)
    assert np.all(np.isfinite(decoded.intensity_db))


def test_read_swaves_cdf_converts_fill_to_nan(monkeypatch):
    day = date(2024, 1, 1)
    patch_reader(monkeypatch, lambda _path: build_fake_cdf(day, n_time=20, behind=False))

    decoded = read_swaves_cdf("ignored.cdf", "behind")
    assert np.all(np.isnan(decoded.intensity_db))


def test_read_swaves_cdf_undoes_a_reversed_row_order(monkeypatch):
    day = date(2012, 3, 7)
    patch_reader(monkeypatch, lambda _path: build_fake_cdf(day, n_time=40, reverse_ahead_rows=True))
    flipped = read_swaves_cdf("ignored.cdf", "ahead")

    patch_reader(monkeypatch, lambda _path: build_fake_cdf(day, n_time=40, reverse_ahead_rows=False))
    plain = read_swaves_cdf("ignored.cdf", "ahead")

    assert np.allclose(flipped.intensity_db, plain.intensity_db)


def test_spacecraft_has_data_tracks_the_stereo_b_loss(monkeypatch):
    patch_reader(monkeypatch, lambda _path: build_fake_cdf(date(2012, 3, 7), n_time=10, behind=True))
    assert spacecraft_has_data("x.cdf", "ahead") is True
    assert spacecraft_has_data("x.cdf", "behind") is True

    patch_reader(monkeypatch, lambda _path: build_fake_cdf(date(2024, 1, 1), n_time=10, behind=False))
    assert spacecraft_has_data("x.cdf", "ahead") is True
    assert spacecraft_has_data("x.cdf", "behind") is False


# ---------------------------------------------------------------------------
# Window assembly
# ---------------------------------------------------------------------------


def test_days_in_window_covers_every_touched_utc_day():
    start = datetime(2012, 3, 7, 23, 30, tzinfo=timezone.utc)
    end = datetime(2012, 3, 9, 0, 30, tzinfo=timezone.utc)
    assert days_in_window(start, end) == [date(2012, 3, 7), date(2012, 3, 8), date(2012, 3, 9)]


def _window_session(days, tmp_path):
    responses = {}
    for day in days:
        responses[resolve_swaves_url(day)] = FakeResponse(body=b"stub")
    return FakeSession(responses)


def test_load_window_stitches_across_midnight(tmp_path, monkeypatch):
    days = [date(2012, 3, 7), date(2012, 3, 8)]
    session = _window_session(days, tmp_path)

    def factory(path: str):
        day = date(2012, 3, 8) if "20120308" in path else date(2012, 3, 7)
        return build_fake_cdf(day, n_time=1440)

    patch_reader(monkeypatch, factory)

    start = datetime(2012, 3, 7, 23, 30, tzinfo=timezone.utc)
    end = datetime(2012, 3, 8, 0, 30, tzinfo=timezone.utc)
    payload = load_swaves_window(start, end, "ahead", tmp_path, base_utc=start, nrows=512, session=session)

    assert len(payload.source_files) == 2
    assert payload.intensity_db.shape[1] == 60  # 23:30:30 .. 00:29:30, one per minute
    assert np.all(np.diff(payload.x_seconds) > 0)
    assert payload.x_seconds[0] == pytest.approx(30.0)


def test_load_window_orders_rows_highest_frequency_first(tmp_path, monkeypatch):
    session = _window_session([date(2012, 3, 7)], tmp_path)
    patch_reader(monkeypatch, lambda _path: build_fake_cdf(date(2012, 3, 7), n_time=1440))

    start = datetime(2012, 3, 7, 0, 0, tzinfo=timezone.utc)
    payload = load_swaves_window(
        start, start + timedelta(hours=1), "ahead", tmp_path, base_utc=start, nrows=512, session=session
    )

    rows = payload.log_freq_rows
    assert rows[0] > rows[-1], "row 0 must be the highest frequency, matching origin='upper'"
    assert payload.intensity_db.shape == (512, rows.size and payload.intensity_db.shape[1])

    extent = payload.matplotlib_extent()
    assert extent[3] > extent[2], "matplotlib extent must place high frequency at the top"
    pg_extent = payload.pyqtgraph_extent()
    assert pg_extent[3] > pg_extent[2]


def test_load_window_skips_a_missing_day_but_keeps_the_rest(tmp_path, monkeypatch):
    session = FakeSession(
        {
            resolve_swaves_url(date(2012, 3, 7)): FakeResponse(body=b"stub"),
            resolve_swaves_url(date(2012, 3, 8)): FakeResponse(status_code=404),
            f"{BASE_URL}2012/": FakeResponse(text=""),
        }
    )
    patch_reader(monkeypatch, lambda _path: build_fake_cdf(date(2012, 3, 7), n_time=1440))

    start = datetime(2012, 3, 7, 23, 0, tzinfo=timezone.utc)
    end = datetime(2012, 3, 8, 1, 0, tzinfo=timezone.utc)
    payload = load_swaves_window(start, end, "ahead", tmp_path, base_utc=start, nrows=512, session=session)

    assert len(payload.source_files) == 1
    assert payload.intensity_db.shape[1] > 0


def test_load_window_raises_when_no_sample_falls_inside(tmp_path, monkeypatch):
    session = _window_session([date(2012, 3, 7)], tmp_path)
    patch_reader(monkeypatch, lambda _path: build_fake_cdf(date(2012, 3, 7), n_time=10))

    start = datetime(2012, 3, 7, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(SwavesNotFoundError):
        load_swaves_window(
            start, start + timedelta(minutes=30), "ahead", tmp_path, base_utc=start, session=session
        )


def test_load_window_rejects_a_backwards_window(tmp_path):
    start = datetime(2012, 3, 7, 1, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        load_swaves_window(start, start, "ahead", tmp_path)


def test_load_window_honours_the_cancel_callback(tmp_path, monkeypatch):
    session = _window_session([date(2012, 3, 7)], tmp_path)
    patch_reader(monkeypatch, lambda _path: build_fake_cdf(date(2012, 3, 7), n_time=60))

    start = datetime(2012, 3, 7, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(SwavesCancelled):
        load_swaves_window(
            start,
            start + timedelta(hours=1),
            "ahead",
            tmp_path,
            base_utc=start,
            session=session,
            cancel_cb=lambda: True,
        )


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def _payload(base_utc: datetime | None = None) -> SwavesPayload:
    base = base_utc or datetime(2012, 3, 7, tzinfo=timezone.utc)
    return SwavesPayload(
        spacecraft=SPACECRAFT_AHEAD,
        start_utc=base,
        end_utc=base + timedelta(hours=1),
        base_utc=base,
        x_seconds=np.arange(4, dtype=float) * 60.0,
        log_freq_rows=np.array([4.2, 3.0, 2.0, 0.4]),
        intensity_db=np.arange(16, dtype=float).reshape(4, 4),
        source_files=("stereo_level2_swaves_20120307_v02.cdf",),
    )


def test_payload_rebase_shifts_x_seconds_without_touching_the_data():
    payload = _payload()
    moved = payload.rebase(payload.base_utc + timedelta(minutes=45))

    assert moved.base_utc == payload.base_utc + timedelta(minutes=45)
    assert np.allclose(moved.x_seconds, payload.x_seconds - 2700.0)
    assert np.allclose(moved.intensity_db, payload.intensity_db)
    assert payload.rebase(payload.base_utc) is payload


def test_payload_survives_a_project_round_trip():
    payload = _payload()
    restored = SwavesPayload.from_project(payload.to_meta(), payload.to_arrays())

    assert restored is not None
    assert restored.spacecraft == payload.spacecraft
    assert restored.base_utc == payload.base_utc
    assert np.allclose(restored.x_seconds, payload.x_seconds)
    assert np.allclose(restored.log_freq_rows, payload.log_freq_rows)
    assert np.allclose(restored.intensity_db, payload.intensity_db)


def test_payload_from_project_rejects_inconsistent_arrays():
    payload = _payload()
    arrays = payload.to_arrays()
    arrays["swaves_intensity"] = np.zeros((3, 3), dtype=np.float32)
    assert SwavesPayload.from_project(payload.to_meta(), arrays) is None
    assert SwavesPayload.from_project({}, {}) is None


def test_payload_spacecraft_label_is_human_readable():
    assert _payload().spacecraft_label == "STEREO-A (Ahead)"


# ---------------------------------------------------------------------------
# Tick formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("khz", "expected"),
    [
        (2.61, "2.6 kHz"),
        (10.0, "10 kHz"),
        (100.0, "100 kHz"),
        (1000.0, "1 MHz"),
        (16025.0, "16 MHz"),
    ],
)
def test_format_frequency_khz(khz, expected):
    assert format_frequency_khz(khz) == expected


def test_format_frequency_rejects_nonsense():
    assert format_frequency_khz(0.0) == ""
    assert format_frequency_khz(float("nan")) == ""
    assert format_log_frequency(None) == ""


def test_log_frequency_ticks_stay_inside_the_range_and_stay_few():
    lo, hi = np.log10(2.61), np.log10(16025.0)
    ticks = log_frequency_ticks(lo, hi)

    assert 2 <= len(ticks) <= 8
    assert all(lo <= t <= hi for t in ticks)
    assert [format_log_frequency(t) for t in ticks] == ["10 kHz", "100 kHz", "1 MHz", "10 MHz"]


def test_log_frequency_ticks_handle_a_degenerate_range():
    assert log_frequency_ticks(3.0, 3.0) == []
    assert log_frequency_ticks(float("nan"), 2.0) == []
