"""
Backend tests for walking a loaded dataset through the archive.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from src.Backend import callisto_timeline as timeline
from src.Backend.callisto_timeline import (
    TimelineUnavailable,
    describe_timeline,
    extended_paths,
    find_archive_segment,
    find_local_segment,
    list_archive_day,
    probe_segment,
    resolve_segment,
    target_timestamp,
    trimmed_paths,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    timeline.clear_day_cache()
    yield
    timeline.clear_day_cache()


def _name(stamp: str, focus: str, station: str = "BIR") -> str:
    return f"{station}_20240102_{stamp}_{focus}.fit.gz"


def _stamp_of(path: str) -> str:
    from src.Backend.callisto_naming import parse_callisto_archive_filename

    return parse_callisto_archive_filename(path)[1].strftime("%H:%M")


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeSession:
    """Serves a directory listing per day; counts requests."""

    def __init__(self, days: dict[date, list[str]]):
        self.days = days
        self.calls: list[str] = []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        for day, names in self.days.items():
            if url.endswith(f"{day.year}/{day.month:02}/{day.day:02}/"):
                body = "".join(f'<a href="{n}">{n}</a>' for n in names)
                return FakeResponse(body)
        return FakeResponse("", status_code=404)

    def close(self):
        return None


# -----------------------------
# describe_timeline
# -----------------------------
def test_describe_single_file_timeline():
    state = describe_timeline([_name("101500", "01")])

    assert state.station == "BIR"
    assert state.focus_codes == ("01",)
    assert len(state.segments) == 1
    assert state.step_seconds == timeline.NOMINAL_STEP_SECONDS
    assert state.start == state.stop == datetime(2024, 1, 2, 10, 15)


def test_describe_measures_the_step_from_the_data():
    state = describe_timeline([_name("101500", "01"), _name("103000", "01")])

    assert state.step_seconds == 900.0
    assert state.focus_codes == ("01",)


def test_describe_orders_segments_and_groups_focus_codes():
    state = describe_timeline([
        _name("103000", "02"),
        _name("101500", "01"),
        _name("103000", "01"),
        _name("101500", "02"),
    ])

    assert state.focus_codes == ("01", "02")
    assert [segment.observed_at.strftime("%H:%M") for segment in state.segments] == ["10:15", "10:30"]
    assert all(segment.focus_codes == ("01", "02") for segment in state.segments)
    assert state.paths[0].endswith("_101500_01.fit.gz")


def test_describe_rejects_mixed_stations():
    with pytest.raises(ValueError, match="one station"):
        describe_timeline([_name("101500", "01"), _name("101500", "01", station="GREENLAND")])


def test_describe_rejects_a_ragged_focus_grid():
    with pytest.raises(ValueError, match="same focus codes"):
        describe_timeline([
            _name("101500", "01"),
            _name("101500", "02"),
            _name("103000", "01"),
        ])


def test_describe_rejects_unparsable_names():
    with pytest.raises(ValueError):
        describe_timeline(["BIR_20240102_101500.fit.gz"])


def test_target_timestamp_steps_both_ways():
    state = describe_timeline([_name("101500", "01"), _name("103000", "01")])

    assert target_timestamp(state, "next") == datetime(2024, 1, 2, 10, 45)
    assert target_timestamp(state, "previous") == datetime(2024, 1, 2, 10, 0)
    with pytest.raises(ValueError):
        target_timestamp(state, "sideways")


# -----------------------------
# Local sibling lookup
# -----------------------------
def _write(tmp_path, names):
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def test_local_lookup_finds_the_adjacent_files(tmp_path):
    _write(tmp_path, [_name("101500", "01"), _name("103000", "01")])
    state = describe_timeline([str(tmp_path / _name("101500", "01"))])

    found = find_local_segment(state, "next")

    assert found == [str(tmp_path / _name("103000", "01"))]
    assert find_local_segment(state, "previous") is None


def test_local_lookup_requires_every_focus_code(tmp_path):
    _write(tmp_path, [
        _name("101500", "01"), _name("101500", "02"),
        _name("103000", "01"),  # focus 02 missing at the next step
    ])
    state = describe_timeline([
        str(tmp_path / _name("101500", "01")),
        str(tmp_path / _name("101500", "02")),
    ])

    assert find_local_segment(state, "next") is None


def test_local_lookup_ignores_a_non_consecutive_gap(tmp_path):
    # 10:15 -> 11:00 is 2700 s, well outside the 750-1050 s combine window.
    _write(tmp_path, [_name("101500", "01"), _name("110000", "01")])
    state = describe_timeline([str(tmp_path / _name("101500", "01"))])

    assert find_local_segment(state, "next") is None


def test_local_lookup_ignores_other_stations(tmp_path):
    _write(tmp_path, [_name("101500", "01"), _name("103000", "01", station="GREENLAND")])
    state = describe_timeline([str(tmp_path / _name("101500", "01"))])

    assert find_local_segment(state, "next") is None


# -----------------------------
# Archive lookup
# -----------------------------
def test_archive_lookup_matches_station_time_and_focus():
    session = FakeSession({
        date(2024, 1, 2): [_name("101500", "01"), _name("103000", "01"), _name("103000", "02")]
    })
    state = describe_timeline([_name("101500", "01")])

    entries = find_archive_segment(state, "next", session=session)

    assert [entry.filename for entry in entries] == [_name("103000", "01")]
    assert entries[0].url.endswith("/2024/01/02/" + _name("103000", "01"))


def test_archive_lookup_tolerates_a_drifting_timestamp():
    # 10:15:00 -> 10:29:57 is 897 s: inside the combine window, so acceptable.
    session = FakeSession({date(2024, 1, 2): ["BIR_20240102_102957_01.fit.gz"]})
    state = describe_timeline([_name("101500", "01")])

    entries = find_archive_segment(state, "next", session=session)

    assert entries[0].filename == "BIR_20240102_102957_01.fit.gz"


def test_archive_lookup_reports_partial_focus_coverage():
    session = FakeSession({date(2024, 1, 2): [_name("103000", "01")]})
    state = describe_timeline([_name("101500", "01"), _name("101500", "02")])

    with pytest.raises(TimelineUnavailable, match="missing focus code"):
        find_archive_segment(state, "next", session=session)


def test_archive_lookup_reports_an_empty_day():
    session = FakeSession({date(2024, 1, 2): [_name("101500", "01")]})
    state = describe_timeline([_name("101500", "01")])

    with pytest.raises(TimelineUnavailable, match="No BIR data after 10:15"):
        find_archive_segment(state, "next", session=session)


def test_archive_lookup_crosses_midnight_into_the_next_day():
    session = FakeSession({
        date(2024, 1, 2): ["BIR_20240102_234500_01.fit.gz"],
        date(2024, 1, 3): ["BIR_20240103_000000_01.fit.gz"],
    })
    state = describe_timeline(["BIR_20240102_234500_01.fit.gz"])

    entries = find_archive_segment(state, "next", session=session)

    assert entries[0].filename == "BIR_20240103_000000_01.fit.gz"
    assert entries[0].url.endswith("/2024/01/03/BIR_20240103_000000_01.fit.gz")


def test_archive_lookup_crosses_midnight_backwards():
    session = FakeSession({
        date(2024, 1, 1): ["BIR_20240101_234500_01.fit.gz"],
        date(2024, 1, 2): ["BIR_20240102_000000_01.fit.gz"],
    })
    state = describe_timeline(["BIR_20240102_000000_01.fit.gz"])

    entries = find_archive_segment(state, "previous", session=session)

    assert entries[0].filename == "BIR_20240101_234500_01.fit.gz"


def test_day_listing_is_cached_across_calls():
    session = FakeSession({date(2024, 1, 2): [_name("101500", "01")]})

    first = list_archive_day(date(2024, 1, 2), session=session)
    second = list_archive_day(date(2024, 1, 2), session=session)

    assert first == second
    assert len(session.calls) == 1, "a cached day must not be re-fetched"

    timeline.clear_day_cache()
    list_archive_day(date(2024, 1, 2), session=session)
    assert len(session.calls) == 2


def test_missing_day_returns_no_entries():
    session = FakeSession({})

    assert list_archive_day(date(2024, 1, 2), session=session) == []


def test_unreachable_archive_is_reported_as_unavailable():
    class Broken:
        def get(self, *_args, **_kwargs):
            raise OSError("connection reset")

        def close(self):
            return None

    with pytest.raises(TimelineUnavailable, match="Could not reach the archive"):
        list_archive_day(date(2024, 1, 2), session=Broken())


# -----------------------------
# resolve / probe
# -----------------------------
def test_resolve_prefers_local_files_and_never_touches_the_network(tmp_path):
    _write(tmp_path, [_name("101500", "01"), _name("103000", "01")])
    state = describe_timeline([str(tmp_path / _name("101500", "01"))])
    session = FakeSession({date(2024, 1, 2): [_name("103000", "01")]})

    resolved = resolve_segment(state, "next", session=session)

    assert resolved == [str(tmp_path / _name("103000", "01"))]
    assert session.calls == []


def test_resolve_falls_back_to_the_archive_through_the_cache(tmp_path, monkeypatch):
    state = describe_timeline([_name("101500", "01")])
    session = FakeSession({date(2024, 1, 2): [_name("103000", "01")]})

    fetched = []

    def fake_fetch(url, filename="", **_kwargs):
        fetched.append((url, filename))
        return tmp_path / filename, False

    monkeypatch.setattr("src.Backend.callisto_cache.fetch_cached", fake_fetch)

    resolved = resolve_segment(state, "next", session=session)

    assert resolved == [str(tmp_path / _name("103000", "01"))]
    assert len(fetched) == 1
    assert fetched[0][1] == _name("103000", "01")


def test_probe_reports_availability_and_the_reason():
    state = describe_timeline([_name("101500", "01")])

    available, reason = probe_segment(
        state, "next", session=FakeSession({date(2024, 1, 2): [_name("103000", "01")]})
    )
    assert (available, reason) == (True, "")

    timeline.clear_day_cache()
    available, reason = probe_segment(
        state, "next", session=FakeSession({date(2024, 1, 2): [_name("101500", "01")]})
    )
    assert available is False
    assert "No BIR data after 10:15" in reason


# -----------------------------
# path assembly
# -----------------------------
def test_extended_paths_keeps_time_order():
    state = describe_timeline([_name("101500", "01")])

    assert extended_paths(state, [_name("103000", "01")], "next") == [
        _name("101500", "01"), _name("103000", "01")
    ]
    assert extended_paths(state, [_name("100000", "01")], "previous") == [
        _name("100000", "01"), _name("101500", "01")
    ]


def test_trim_reports_the_shift_it_causes():
    state = describe_timeline([
        _name("101500", "01"), _name("103000", "01"), _name("104500", "01")
    ])

    remaining, shift = trimmed_paths(state, "start")
    assert [_stamp_of(p) for p in remaining] == ["10:30", "10:45"]
    assert shift == -900.0

    remaining, shift = trimmed_paths(state, "end")
    assert [_stamp_of(p) for p in remaining] == ["10:15", "10:30"]
    assert shift == 0.0


def test_trim_refuses_the_last_segment():
    state = describe_timeline([_name("101500", "01")])

    with pytest.raises(ValueError, match="cannot be trimmed"):
        trimmed_paths(state, "start")
    with pytest.raises(ValueError, match="Trim edge"):
        trimmed_paths(describe_timeline([_name("101500", "01"), _name("103000", "01")]), "middle")


def test_trim_keeps_every_focus_code_of_the_remaining_segments():
    state = describe_timeline([
        _name("101500", "01"), _name("101500", "02"),
        _name("103000", "01"), _name("103000", "02"),
    ])

    remaining, shift = trimmed_paths(state, "end")

    assert sorted(remaining) == sorted([_name("101500", "01"), _name("101500", "02")])
    assert shift == 0.0
