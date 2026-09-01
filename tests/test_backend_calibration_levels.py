"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

from src.Backend import calibration_levels as levels_mod
from src.Backend.calibration_levels import (
    LEVEL_1,
    LEVEL_1_5,
    LEVEL_1B,
    LEVEL_2,
    ORIGIN_ARCHIVE,
    ORIGIN_LOCAL,
    LevelOption,
    apply_level,
    clear_pointing_cache,
    detect_frame_level,
    find_level_option,
    instrument_token,
    levels_for_frame,
    requires_download,
)


class FakeMap:
    """Minimal duck-typed map: the module only reads instrument name and meta."""

    def __init__(self, instrument="AIA", meta=None, date="2024-05-10T00:00:00"):
        self.instrument = instrument
        self.meta = dict(meta or {})
        self.date = date


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_pointing_cache()
    yield
    clear_pointing_cache()


def test_instrument_token_recognises_supported_imagers():
    assert instrument_token(FakeMap(instrument="AIA")) == "AIA"
    assert instrument_token(FakeMap(instrument="SUVI")) == "SUVI"
    assert instrument_token(FakeMap(instrument="Solar Ultraviolet Imager")) == "SUVI"


def test_instrument_token_falls_back_to_meta():
    frame = FakeMap(instrument="", meta={"INSTRUME": "AIA_3"})
    assert instrument_token(frame) == "AIA"


def test_aia_offers_level_1_and_locally_derived_level_1p5():
    options = levels_for_frame(FakeMap(instrument="AIA"))
    assert [o.key for o in options] == [LEVEL_1, LEVEL_1_5]
    assert options[0].origin == ORIGIN_ARCHIVE
    assert options[1].origin == ORIGIN_LOCAL


def test_aia_offers_no_level_2_because_none_exists():
    keys = [o.key for o in levels_for_frame(FakeMap(instrument="AIA"))]
    assert LEVEL_2 not in keys


def test_suvi_levels_are_both_archive_products():
    options = levels_for_frame(FakeMap(instrument="SUVI"))
    assert [o.key for o in options] == [LEVEL_1B, LEVEL_2]
    assert all(o.origin == ORIGIN_ARCHIVE for o in options)


def test_instruments_without_alternative_levels_expose_none():
    assert levels_for_frame(FakeMap(instrument="HMI")) == ()
    assert levels_for_frame(FakeMap(instrument="SECCHI")) == ()
    # LASCO is deliberately absent: the VSO returns the same records whatever
    # a.Level is set to, so a selector would be a control that does nothing.
    assert levels_for_frame(FakeMap(instrument="LASCO")) == ()


def test_find_level_option_accepts_loose_spellings():
    frame = FakeMap(instrument="AIA")
    assert find_level_option(frame, "1.5").key == LEVEL_1_5
    assert find_level_option(frame, "Level 1.5").key == LEVEL_1_5
    assert find_level_option(frame, "9") is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("L1b", "1b"),
        ("1b", "1b"),
        ("1.0", "1"),
        (1.0, "1"),
        (1, "1"),
        ("Level 2", "2"),
        ("lev1.5", "1.5"),
        (1.5, "1.5"),
        ("0.5", "0.5"),
        ("", ""),
        (None, ""),
    ],
)
def test_level_keys_normalize_to_one_canonical_form(raw, expected):
    assert levels_mod._normalize_level_key(raw) == expected


def test_detect_frame_level_prefers_lvl_num():
    assert detect_frame_level(FakeMap(meta={"LVL_NUM": 1.0})) == LEVEL_1
    assert detect_frame_level(FakeMap(meta={"LVL_NUM": 1.5})) == LEVEL_1_5


def test_detect_frame_level_falls_back_to_level_keyword():
    assert detect_frame_level(FakeMap(meta={"LEVEL": "L1b"})) == LEVEL_1B


def test_detect_frame_level_returns_none_when_header_is_silent():
    assert detect_frame_level(FakeMap(meta={"INSTRUME": "AIA"})) is None


def test_requires_download_only_for_a_different_archive_level():
    aia = FakeMap(instrument="AIA")
    local = find_level_option(aia, LEVEL_1_5)
    archive = find_level_option(aia, LEVEL_1)
    # Locally derivable levels never need the archive.
    assert requires_download(local, LEVEL_1) is False
    # The level already in hand is restored, not re-fetched.
    assert requires_download(archive, LEVEL_1) is False
    # A different archive level is a different set of files.
    suvi_l2 = find_level_option(FakeMap(instrument="SUVI"), LEVEL_2)
    assert requires_download(suvi_l2, LEVEL_1B) is True


def test_apply_level_returns_frames_unchanged_when_already_at_target():
    frames = [FakeMap(meta={"LVL_NUM": 1.0})]
    result = apply_level(frames, LEVEL_1, base_level=LEVEL_1)
    assert result.frames == frames
    assert result.level == LEVEL_1


def test_apply_level_registers_every_frame_and_reports_progress():
    frames = [FakeMap(meta={"LVL_NUM": 1.0}) for _ in range(3)]
    seen = []
    progress = []

    def fake_register(frame):
        seen.append(frame)
        return FakeMap(meta={"LVL_NUM": 1.5})

    result = apply_level(
        frames,
        LEVEL_1_5,
        base_level=LEVEL_1,
        register_fn=fake_register,
        pointing_table_fetcher=lambda *a, **k: None,
        progress_cb=lambda done, total: progress.append((done, total)),
    )

    assert len(seen) == 3
    assert len(result.frames) == 3
    assert result.level == LEVEL_1_5
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_apply_level_applies_pointing_update_when_a_table_is_available():
    frames = [FakeMap(meta={"LVL_NUM": 1.0})]
    calls = []

    def fake_update_pointing(frame, *, pointing_table):
        calls.append(pointing_table)
        return frame

    result = apply_level(
        frames,
        LEVEL_1_5,
        base_level=LEVEL_1,
        register_fn=lambda f: f,
        update_pointing_fn=fake_update_pointing,
        pointing_table="TABLE",
    )

    assert calls == ["TABLE"]
    assert result.pointing_applied is True
    assert result.warnings == []


def test_apply_level_still_registers_when_the_pointing_table_is_unreachable():
    """Offline must degrade, not fail — but it must say so.

    A level 1.5 produced without the 3-hourly master pointing is a different
    product from one produced with it, so the caller has to be told.
    """
    frames = [FakeMap(meta={"LVL_NUM": 1.0})]

    def offline_fetcher(*args, **kwargs):
        raise RuntimeError("no network")

    result = apply_level(
        frames,
        LEVEL_1_5,
        base_level=LEVEL_1,
        register_fn=lambda f: f,
        pointing_table_fetcher=offline_fetcher,
    )

    assert len(result.frames) == 1
    assert result.pointing_applied is False
    assert any("pointing" in w.lower() for w in result.warnings)


def test_apply_level_keeps_the_input_frame_when_one_registration_fails():
    good = FakeMap(meta={"LVL_NUM": 1.0})
    bad = FakeMap(meta={"LVL_NUM": 1.0})

    def flaky_register(frame):
        if frame is bad:
            raise RuntimeError("bad frame")
        return frame

    result = apply_level(
        [good, bad],
        LEVEL_1_5,
        base_level=LEVEL_1,
        register_fn=flaky_register,
        pointing_table_fetcher=lambda *a, **k: None,
    )

    assert result.frames == [good, bad]
    assert any("fell back" in w for w in result.warnings)


def test_apply_level_stops_when_cancelled():
    frames = [FakeMap(meta={"LVL_NUM": 1.0}) for _ in range(3)]
    result = apply_level(
        frames,
        LEVEL_1_5,
        base_level=LEVEL_1,
        register_fn=lambda f: f,
        pointing_table_fetcher=lambda *a, **k: None,
        cancel_cb=lambda: True,
    )
    assert result.cancelled is True


def test_apply_level_refuses_to_fabricate_an_archive_level():
    frames = [FakeMap(instrument="SUVI", meta={"LEVEL": "L1b"})]
    with pytest.raises(ValueError, match="archive product"):
        apply_level(frames, LEVEL_2, base_level=LEVEL_1B, register_fn=lambda f: f)


def test_apply_level_requires_frames():
    with pytest.raises(ValueError, match="Load solar frames"):
        apply_level([], LEVEL_1_5)


def test_level_option_is_local_reflects_origin():
    assert LevelOption("1.5", "x", ORIGIN_LOCAL).is_local is True
    assert LevelOption("1", "x", ORIGIN_ARCHIVE).is_local is False
