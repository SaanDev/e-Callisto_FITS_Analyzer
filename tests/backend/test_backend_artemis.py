"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest
from astropy.io import fits

from src.backend.radio import artemis
from src.backend.radio.fits_io import extract_ut_start_sec, load_callisto_fits, preview_callisto_fits


SAMPLE_COUNT = 601
CHANNEL_COUNT = 128


def artemis_header(**overrides) -> fits.Header:
    """A header shaped like the ones ARTLOOK writes for the ASG receiver."""
    hdr = fits.Header()
    hdr["SIMPLE"] = (True, "STANDARD FITS CREATED BY ARTLOOK")
    hdr["BITPIX"] = 16
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = SAMPLE_COUNT
    hdr["NAXIS2"] = CHANNEL_COUNT
    hdr["CTYPE1"] = "         TIME (UT)"
    hdr["CRPIX1"] = 1
    hdr["CRVAL1"] = 8.9999722222          # hours of UT, not seconds
    hdr["CDELT1"] = 0.0002777778          # one second, expressed in hours
    hdr["CTYPE2"] = "   FREQUENCY (MHZ)"
    hdr["CRPIX2"] = 1
    hdr["CRVAL2"] = 686.6300048828
    hdr["CDELT2"] = -4.5403938293
    hdr["BUNIT"] = "            COUNTS"
    hdr["BLANK"] = -32767
    hdr["OBJECT"] = "               SUN"
    hdr["ORIGIN"] = "ARTEMIS-IV, THERMOPYLAE, GREECE"
    hdr["TELESCOP"] = " RADIOSPECTROGRAPH"
    hdr["INSTRUME"] = "               ASG"
    hdr["DATE"] = "2015-22-06T14:56:03.092"   # ARTLOOK writes YYYY-DD-MM
    hdr["DATE-OBS"] = "2000-07-14T08:59:59.900"
    hdr["TIME-OBS"] = "      08:59:59.900"
    for key, value in overrides.items():
        if value is None:
            hdr.remove(key, ignore_missing=True)
        else:
            hdr[key] = value
    return hdr


def callisto_header() -> fits.Header:
    hdr = fits.Header()
    hdr["SIMPLE"] = True
    hdr["INSTRUME"] = "CALLISTO"
    hdr["ORIGIN"] = "e-CALLISTO"
    hdr["TIME-OBS"] = "09:45:05.000"
    hdr["CRVAL1"] = 35105.0
    hdr["CDELT1"] = 0.25
    return hdr


@pytest.fixture
def artemis_file(tmp_path):
    """A small on-disk ARTEMIS file with a burst in it."""
    rng = np.random.default_rng(7)
    # Per-channel gain profile spanning an order of magnitude, as ARTEMIS has.
    gains = np.linspace(20.0, 160.0, CHANNEL_COUNT).reshape(-1, 1)
    data = gains + rng.normal(0.0, 3.0, size=(CHANNEL_COUNT, SAMPLE_COUNT))
    data[40:80, 200:260] += 900.0  # a burst
    path = tmp_path / "00714.FITS"
    fits.PrimaryHDU(
        data=data.astype(np.int16), header=artemis_header()
    ).writeto(path, overwrite=True, output_verify="ignore")
    return str(path)


# --- Identification -------------------------------------------------------


def test_identifies_artemis_from_origin():
    assert artemis.is_artemis_header(artemis_header()) is True


def test_identifies_artemis_from_artlook_comment_when_origin_is_missing():
    hdr = artemis_header(ORIGIN=None)
    assert artemis.is_artemis_header(hdr) is True


def test_callisto_header_is_not_artemis():
    assert artemis.is_artemis_header(callisto_header()) is False
    assert artemis.describe_artemis(callisto_header()) is None


def test_receiver_is_read_from_instrume():
    assert artemis.artemis_receiver(artemis_header()) == artemis.RECEIVER_ASG
    assert artemis.artemis_receiver(artemis_header(INSTRUME="SAO")) == artemis.RECEIVER_SAO
    assert artemis.artemis_receiver(artemis_header(INSTRUME="something else")) == ""


# --- Time axis ------------------------------------------------------------


def test_time_axis_converts_hours_to_relative_seconds():
    axis = artemis.artemis_time_axis(artemis_header(), SAMPLE_COUNT)

    assert axis.unit == artemis.TIME_UNIT_HOURS
    assert axis.unit_source == "ut-cross-check"
    assert axis.seconds[0] == pytest.approx(0.0)
    assert axis.seconds[-1] == pytest.approx(SAMPLE_COUNT - 1, abs=1e-3)
    assert axis.cadence_s == pytest.approx(1.0, abs=1e-6)


def test_ut_start_comes_from_the_axis_and_matches_time_obs():
    axis = artemis.artemis_time_axis(artemis_header(), SAMPLE_COUNT)
    assert axis.ut_start_sec == pytest.approx(8 * 3600 + 59 * 60 + 59.9, abs=1e-3)


def test_ut_start_survives_a_missing_time_obs():
    axis = artemis.artemis_time_axis(artemis_header(**{"TIME-OBS": None}), SAMPLE_COUNT)
    assert axis.unit_source == "ut-cross-check"  # DATE-OBS still confirms it
    assert axis.ut_start_sec == pytest.approx(8 * 3600 + 59 * 60 + 59.9, abs=1e-3)


def test_crpix_offsets_the_ut_start():
    axis = artemis.artemis_time_axis(artemis_header(CRPIX1=11, **{"TIME-OBS": None, "DATE-OBS": None}), SAMPLE_COUNT)
    # Ten samples before the reference pixel, at one second each.
    assert axis.ut_start_sec == pytest.approx(8 * 3600 + 59 * 60 + 49.9, abs=1e-2)


def test_cunit_wins_over_the_artlook_convention():
    hdr = artemis_header(CUNIT1="s", CRVAL1=32399.9, CDELT1=1.0)
    axis = artemis.artemis_time_axis(hdr, SAMPLE_COUNT)
    assert (axis.unit, axis.unit_source) == (artemis.TIME_UNIT_SECONDS, "cunit")
    assert axis.cadence_s == pytest.approx(1.0)
    assert axis.seconds[-1] == pytest.approx(SAMPLE_COUNT - 1)


def test_seconds_axis_is_detected_by_cross_check_without_cunit():
    hdr = artemis_header(CRVAL1=32399.9, CDELT1=1.0)
    axis = artemis.artemis_time_axis(hdr, SAMPLE_COUNT)
    assert (axis.unit, axis.unit_source) == (artemis.TIME_UNIT_SECONDS, "ut-cross-check")


def test_unit_falls_back_to_the_convention_when_nothing_can_confirm_it():
    hdr = artemis_header(**{"TIME-OBS": None, "DATE-OBS": None})
    axis = artemis.artemis_time_axis(hdr, SAMPLE_COUNT)
    assert (axis.unit, axis.unit_source) == (artemis.TIME_UNIT_HOURS, "convention")


def test_time_axis_of_zero_length_is_empty():
    axis = artemis.artemis_time_axis(artemis_header(), 0)
    assert axis.seconds.size == 0


# --- Calibration ----------------------------------------------------------


def test_asg_scale_is_the_adc_span_over_the_published_dynamic_range():
    cal = artemis.artemis_calibration(artemis.RECEIVER_ASG)

    assert cal.counts_per_db == pytest.approx(4096.0 / 70.0)
    assert cal.db_per_count == pytest.approx(70.0 / 4096.0)
    assert cal.dynamic_range_is_assumed is False


def test_sao_borrows_the_asg_dynamic_range_and_says_so():
    cal = artemis.artemis_calibration(artemis.RECEIVER_SAO)
    assert cal.dynamic_range_is_assumed is True
    assert "unpublished" in cal.reference


def test_calibration_constants_are_overridable():
    cal = artemis.artemis_calibration(
        artemis.RECEIVER_ASG, adc_full_scale_counts=1024.0, dynamic_range_db=50.0
    )
    assert cal.counts_per_db == pytest.approx(1024.0 / 50.0)
    assert cal.dynamic_range_is_assumed is False


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan")])
def test_nonsense_overrides_fall_back_to_the_published_figures(bad):
    cal = artemis.artemis_calibration(
        artemis.RECEIVER_ASG, adc_full_scale_counts=bad, dynamic_range_db=bad
    )
    assert cal.counts_per_db == pytest.approx(4096.0 / 70.0)


def test_counts_and_db_round_trip():
    cal = artemis.artemis_calibration(artemis.RECEIVER_ASG)
    counts = np.array([0.0, 58.514, 1387.0])
    assert artemis.counts_to_db(counts, cal)[1] == pytest.approx(1.0, abs=1e-4)
    assert artemis.db_to_counts(artemis.counts_to_db(counts, cal), cal) == pytest.approx(counts)


def test_db_conversion_keeps_float32_in_float32():
    cal = artemis.artemis_calibration(artemis.RECEIVER_ASG)
    assert artemis.counts_to_db(np.zeros(4, dtype=np.float32), cal).dtype == np.float32


def test_artemis_scale_differs_from_the_callisto_constant():
    # The CALLISTO digit-to-dB constant would overstate an ARTEMIS burst by ~22x.
    callisto = 2500.0 / 256.0 / 25.4
    artemis_scale = artemis.artemis_calibration(artemis.RECEIVER_ASG).db_per_count
    assert callisto / artemis_scale > 20.0


# --- Header quirks --------------------------------------------------------


def test_creation_date_with_day_and_month_swapped_is_recovered():
    parsed, swapped = artemis.parse_fits_datetime("2015-22-06T14:56:03.092")
    assert swapped is True
    assert (parsed.year, parsed.month, parsed.day) == (2015, 6, 22)


def test_well_formed_date_is_not_reported_as_swapped():
    parsed, swapped = artemis.parse_fits_datetime("2000-07-14T08:59:59.900")
    assert swapped is False
    assert (parsed.month, parsed.day) == (7, 14)


def test_unparseable_date_yields_none():
    assert artemis.parse_fits_datetime("not a date") == (None, False)
    assert artemis.parse_fits_datetime("") == (None, False)


def test_notes_flag_the_hours_axis_the_swapped_date_and_the_missing_calibration():
    notes = " ".join(artemis.artemis_header_notes(artemis_header()))
    assert "hours" in notes
    assert "YYYY-DD-MM" in notes
    assert "not flux calibrated" in notes or "no absolute flux calibration" in notes


def test_a_convention_only_unit_guess_is_flagged_as_unconfirmed():
    notes = " ".join(
        artemis.artemis_header_notes(artemis_header(**{"TIME-OBS": None, "DATE-OBS": None}))
    )
    assert "assumed" in notes


# --- Profile --------------------------------------------------------------


def test_profile_describes_the_1996_era_asg_configuration():
    profile = artemis.describe_artemis(artemis_header())

    assert profile.receiver == artemis.RECEIVER_ASG
    assert profile.channel_count == CHANNEL_COUNT
    assert profile.freq_low_mhz == pytest.approx(110.0, abs=0.1)
    assert profile.freq_high_mhz == pytest.approx(686.63, abs=0.1)
    assert profile.channel_width_mhz == pytest.approx(4.5404, abs=1e-3)
    assert profile.cadence_s == pytest.approx(1.0, abs=1e-6)
    assert profile.observed_at.isoformat().startswith("2000-07-14T08:59:59")
    assert "110" in profile.summary() and "687" in profile.summary()


def test_report_is_empty_for_a_callisto_header():
    assert artemis.artemis_header_report(callisto_header()) == ""


def test_report_carries_the_scale_and_the_notes():
    report = artemis.artemis_header_report(artemis_header())
    assert "58.5" in report
    assert "counts/dB" in report
    assert "YYYY-DD-MM" in report


def test_suggested_display_range_spans_the_data():
    rng = np.random.default_rng(1)
    data = rng.normal(0.0, 5.0, size=(32, 500))
    data[10:14, 100:140] += 800.0
    low, high = artemis.suggested_display_range(data)
    assert low < 0.0 < high
    assert high > 5.0


def test_suggested_display_range_rejects_an_all_nan_array():
    assert artemis.suggested_display_range(np.full((4, 4), np.nan)) == (None, None)


# --- Reader integration ---------------------------------------------------


def test_loader_returns_relative_seconds_for_an_artemis_file(artemis_file):
    result = load_callisto_fits(artemis_file)

    assert result.data.shape == (CHANNEL_COUNT, SAMPLE_COUNT)
    assert result.time[0] == pytest.approx(0.0)
    # Three hours would come out as 3 "seconds" without the conversion.
    assert result.time[-1] == pytest.approx(SAMPLE_COUNT - 1, abs=1e-2)
    assert result.freqs[0] > result.freqs[-1]
    assert result.freqs[-1] == pytest.approx(110.0, abs=0.1)


def test_loader_leaves_ut_start_at_the_declared_observation_time(artemis_file):
    result = load_callisto_fits(artemis_file)
    assert extract_ut_start_sec(result.header0) == pytest.approx(32399.9, abs=1e-2)


def test_preview_matches_the_loaded_axes(artemis_file):
    preview = preview_callisto_fits(artemis_file)
    result = load_callisto_fits(artemis_file)

    assert preview.data_shape == result.data.shape
    assert preview.time_source == "artemis"
    np.testing.assert_allclose(preview.time, result.time)
    np.testing.assert_allclose(preview.freqs, result.freqs)


def test_callisto_time_axis_is_untouched_by_the_artemis_path(tmp_path):
    hdr = callisto_header()
    hdr["CRPIX1"] = 1
    hdr["CRVAL2"] = 80.0
    hdr["CDELT2"] = -0.5
    hdr["CRPIX2"] = 1
    path = tmp_path / "AUSTRIA_20141105_094505_59.fit"
    fits.PrimaryHDU(data=np.zeros((8, 10), dtype=np.int16), header=hdr).writeto(path)

    result = load_callisto_fits(str(path))

    # Header WCS seconds pass straight through, absolute values and all.
    assert result.time[0] == pytest.approx(35105.0)
    assert extract_ut_start_sec(result.header0) == pytest.approx(35105.0)
