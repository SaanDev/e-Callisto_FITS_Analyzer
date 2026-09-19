"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

ARTEMIS-IV (Thermopylae, Greece) dynamic-spectrum support.

ARTEMIS-IV is the solar radiospectrograph of the University of Athens, running
at the Thermopylae Satellite Telecommunication Station since 1996.  Its FITS
files are written by ``ARTLOOK`` and are *not* CALLISTO files: they carry the
spectrogram as a plain image in the primary HDU with a WCS description, no
binary-table axes, and — the part that silently ruins a plot — a **time axis in
hours of UT** rather than the seconds-from-start a CALLISTO ``TIME`` column
holds.  Loaded naively, a three-hour observation renders as three seconds wide
and every UT label is wrong.

Two receivers feed the archive, and ``INSTRUME`` names which one wrote a file:

* ``ASG`` — the sweep-frequency analyser ("Analyseur de Spectre Global"), the
  full-band receiver.  Its detector is logarithmic with a 70 dB dynamic range,
  and its analogue output (0–5 V dc) is digitised by a 12-bit ADC, so a sample
  is an integer in 0–4095.
* ``SAO`` — the acousto-optical analyser covering 270–450 MHz in 128 channels,
  digitised by an ADC of the same depth.

Calibration
-----------
ARTEMIS-IV publishes no absolute flux calibration: there is no per-file hot/cold
load record, and the dynamic spectra in the instrument's literature are shown in
relative units.  What *is* published is the receiver chain, and it is what makes
a physical scale possible at all — a logarithmic detector digitised linearly
means the stored counts are proportional to decibels:

    dB = (counts - background) / counts_per_dB
    counts_per_dB = adc_full_scale_counts / dynamic_range_dB
                  = 4096 / 70 = 58.51 counts/dB   (ASG)

So this module converts to **dB above the per-channel background**, never to
sfu, and says so.  The CALLISTO constant the rest of the analyzer uses
(``2500/256/25.4`` — a 2500 mV span over 256 digits against a 25.4 mV/dB log
detector) describes a different receiver entirely and is wrong here by a factor
of ~6.

Both instrument constants are overridable, because the two figures come from the
published instrument descriptions rather than from the file, and a user
reducing a particular campaign may know better.

References
----------
* Caroubalos et al. (2001), "Solar radio observations with the ARTEMIS-IV
  radiospectrograph", and Kontogeorgos et al., "The improved ARTEMIS IV
  multichannel solar radio spectrograph of the University of Athens"
  (arXiv:1009.3628) — receiver layout, 70 dB ASG dynamic range, 12-bit
  Keithley KPCI-3100 ADC over the 0–5 V ASG output.
* Caroubalos et al., "Ten Years of the Solar Radiospectrograph ARTEMIS-IV"
  (arXiv:1009.4150) — coverage and cadence across the instrument's eras.

Pure functions over headers and arrays (no Qt, no I/O), so the reader, the UI
gating and the tests can all share one description of the instrument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

import numpy as np


# --- Identification -------------------------------------------------------

ORIGIN_TOKEN = "ARTEMIS"
STATION = "Thermopylae, Greece"
OBSERVATORY = "ARTEMIS-IV"

RECEIVER_ASG = "ASG"
RECEIVER_SAO = "SAO"
RECEIVER_UNKNOWN = ""

#: Full-scale span of the acquisition ADC, in counts.  Both receiver PCs use a
#: 12-bit card, so a sample is an integer in 0-4095 and the span is 4096.
ADC_FULL_SCALE_COUNTS = 4096.0

#: Published dynamic range of the ASG log detector.
ASG_DYNAMIC_RANGE_DB = 70.0

_RECEIVER_ALIASES = {
    "ASG": RECEIVER_ASG,
    "SWEEP": RECEIVER_ASG,
    "SAO": RECEIVER_SAO,
    "AOS": RECEIVER_SAO,
}


# --- Time axis ------------------------------------------------------------

TIME_UNIT_HOURS = "hours"
TIME_UNIT_MINUTES = "minutes"
TIME_UNIT_SECONDS = "seconds"

_TIME_UNIT_SCALES = {
    TIME_UNIT_HOURS: 3600.0,
    TIME_UNIT_MINUTES: 60.0,
    TIME_UNIT_SECONDS: 1.0,
}

_CUNIT_ALIASES = {
    "h": TIME_UNIT_HOURS,
    "hr": TIME_UNIT_HOURS,
    "hrs": TIME_UNIT_HOURS,
    "hour": TIME_UNIT_HOURS,
    "hours": TIME_UNIT_HOURS,
    "min": TIME_UNIT_MINUTES,
    "mins": TIME_UNIT_MINUTES,
    "minute": TIME_UNIT_MINUTES,
    "minutes": TIME_UNIT_MINUTES,
    "s": TIME_UNIT_SECONDS,
    "sec": TIME_UNIT_SECONDS,
    "secs": TIME_UNIT_SECONDS,
    "second": TIME_UNIT_SECONDS,
    "seconds": TIME_UNIT_SECONDS,
}

#: How far ``CRVAL1`` may sit from ``TIME-OBS`` before a unit guess is rejected.
_UT_CROSS_CHECK_TOLERANCE_S = 60.0

_SECONDS_PER_DAY = 86400.0


# --- Calibration modes ----------------------------------------------------

CAL_COUNTS = "counts"
CAL_COUNTS_ABOVE_BACKGROUND = "counts_above_background"
CAL_DB_ABOVE_BACKGROUND = "db_above_background"

CALIBRATION_MODES = (CAL_COUNTS, CAL_COUNTS_ABOVE_BACKGROUND, CAL_DB_ABOVE_BACKGROUND)

_MODE_LABELS = {
    CAL_COUNTS: "Counts",
    CAL_COUNTS_ABOVE_BACKGROUND: "Counts above background",
    CAL_DB_ABOVE_BACKGROUND: "dB above background",
}


@dataclass(frozen=True)
class ArtemisCalibration:
    """The counts-to-decibels scale for one ARTEMIS-IV receiver."""

    receiver: str
    adc_full_scale_counts: float
    dynamic_range_db: float
    counts_per_db: float
    #: True when ``dynamic_range_db`` was carried over from the ASG because the
    #: receiver's own figure is not published.  The UI says so rather than
    #: presenting a borrowed number as measured.
    dynamic_range_is_assumed: bool
    reference: str

    @property
    def db_per_count(self) -> float:
        return 1.0 / self.counts_per_db if self.counts_per_db else 0.0


@dataclass(frozen=True)
class ArtemisTimeAxis:
    """A normalised ARTEMIS time axis, in the units the analyzer expects."""

    #: Seconds since the first sample — what ``FitsLoadResult.time`` must hold.
    seconds: np.ndarray
    #: Seconds of UT day at the first sample, for the UT axis mode.
    ut_start_sec: float | None
    #: Sample spacing in seconds.
    cadence_s: float | None
    #: Which unit the header's ``CDELT1``/``CRVAL1`` were found to be in.
    unit: str
    #: How the unit was decided: "cunit", "ut-cross-check", "convention".
    unit_source: str


@dataclass(frozen=True)
class ArtemisProfile:
    """Everything the analyzer needs to know about one ARTEMIS-IV file."""

    receiver: str
    origin: str
    telescope: str
    station: str
    observed_at: datetime | None
    created_at: datetime | None
    channel_count: int
    sample_count: int
    freq_low_mhz: float | None
    freq_high_mhz: float | None
    channel_width_mhz: float | None
    cadence_s: float | None
    duration_s: float | None
    bunit: str
    calibration: ArtemisCalibration
    time_axis_unit: str
    #: Header oddities worth telling the user about, already phrased for display.
    notes: tuple[str, ...]

    @property
    def receiver_label(self) -> str:
        if self.receiver == RECEIVER_ASG:
            return "ASG (sweep-frequency analyser)"
        if self.receiver == RECEIVER_SAO:
            return "SAO (acousto-optical analyser)"
        return self.receiver or "unknown receiver"

    @property
    def instrument_label(self) -> str:
        return f"{OBSERVATORY}/{self.receiver}" if self.receiver else OBSERVATORY

    def summary(self) -> str:
        """One line naming the instrument and what it recorded."""
        bits = [f"{OBSERVATORY} · {self.receiver_label}", self.station]
        if self.freq_low_mhz is not None and self.freq_high_mhz is not None:
            bits.append(f"{self.freq_low_mhz:.0f}–{self.freq_high_mhz:.0f} MHz")
        if self.channel_count:
            bits.append(f"{self.channel_count} channels")
        if self.cadence_s:
            bits.append(_format_cadence(self.cadence_s))
        return " · ".join(b for b in bits if b)


# --- Header access helpers ------------------------------------------------


def _header_get(header: Any, key: str, default: Any = None) -> Any:
    if header is None:
        return default
    try:
        getter = getattr(header, "get", None)
        if callable(getter):
            value = getter(key, default)
            return default if value is None else value
    except Exception:
        return default
    if isinstance(header, Mapping):
        return header.get(key, default)
    return default


def _header_text(header: Any, key: str) -> str:
    value = _header_get(header, key, "")
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _header_float(header: Any, key: str) -> float | None:
    value = _header_get(header, key, None)
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def _header_int(header: Any, key: str, default: int = 0) -> int:
    value = _header_get(header, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


# --- Identification -------------------------------------------------------


def is_artemis_header(header: Any) -> bool:
    """True when this primary header came from ARTEMIS-IV.

    ``ORIGIN`` carries the observatory name; the ``SIMPLE`` comment carries the
    writer (``STANDARD FITS CREATED BY ARTLOOK``).  Either is enough, because
    older files in the archive vary in which one they fill in.
    """
    if ORIGIN_TOKEN in _header_text(header, "ORIGIN").upper():
        return True
    if ORIGIN_TOKEN in _header_text(header, "OBSERVAT").upper():
        return True
    return "ARTLOOK" in _simple_comment(header).upper()


def _simple_comment(header: Any) -> str:
    comments = getattr(header, "comments", None)
    if comments is None:
        return ""
    try:
        return str(comments["SIMPLE"])
    except Exception:
        return ""


def artemis_receiver(header: Any) -> str:
    """The receiver that wrote the file: ``ASG``, ``SAO`` or ``""``."""
    token = _header_text(header, "INSTRUME").upper()
    if not token:
        return RECEIVER_UNKNOWN
    for alias, receiver in _RECEIVER_ALIASES.items():
        if alias in token:
            return receiver
    return RECEIVER_UNKNOWN


# --- Calibration ----------------------------------------------------------


def artemis_calibration(
    receiver: str,
    *,
    adc_full_scale_counts: float | None = None,
    dynamic_range_db: float | None = None,
) -> ArtemisCalibration:
    """The counts-to-dB scale for ``receiver``.

    Both constants may be overridden; passing neither gives the published
    figures (12-bit ADC, 70 dB ASG dynamic range).
    """
    name = _RECEIVER_ALIASES.get(str(receiver or "").strip().upper(), RECEIVER_UNKNOWN)

    full_scale = ADC_FULL_SCALE_COUNTS if adc_full_scale_counts is None else float(adc_full_scale_counts)
    if not np.isfinite(full_scale) or full_scale <= 0.0:
        full_scale = ADC_FULL_SCALE_COUNTS

    assumed = False
    if dynamic_range_db is None:
        span_db = ASG_DYNAMIC_RANGE_DB
        # The 70 dB figure is published for the ASG. The SAO's own dynamic range
        # is not, so it borrows the ASG's and is flagged as doing so.
        assumed = name != RECEIVER_ASG
    else:
        span_db = float(dynamic_range_db)
    if not np.isfinite(span_db) or span_db <= 0.0:
        span_db = ASG_DYNAMIC_RANGE_DB
        assumed = True

    reference = (
        "12-bit ADC full scale over the receiver's log-detector dynamic range"
        if not assumed
        else "12-bit ADC full scale over the ASG dynamic range (this receiver's own figure is unpublished)"
    )

    return ArtemisCalibration(
        receiver=name,
        adc_full_scale_counts=full_scale,
        dynamic_range_db=span_db,
        counts_per_db=full_scale / span_db,
        dynamic_range_is_assumed=assumed,
        reference=reference,
    )


def counts_to_db(values: Any, calibration: ArtemisCalibration) -> np.ndarray:
    """Convert a count *difference* (signal minus background) to decibels."""
    arr = np.asarray(values)
    dtype = np.float32 if arr.dtype == np.float32 else np.float64
    arr = np.asarray(arr, dtype=dtype)
    scale = dtype(calibration.db_per_count)
    return arr * scale


def db_to_counts(values: Any, calibration: ArtemisCalibration) -> np.ndarray:
    """Inverse of :func:`counts_to_db`, for round-tripping UI thresholds."""
    arr = np.asarray(values)
    dtype = np.float32 if arr.dtype == np.float32 else np.float64
    arr = np.asarray(arr, dtype=dtype)
    return arr * dtype(calibration.counts_per_db)


def calibration_mode_label(mode: str) -> str:
    return _MODE_LABELS.get(str(mode or "").strip().lower(), _MODE_LABELS[CAL_COUNTS])


# --- Time axis ------------------------------------------------------------


def _cunit_time_unit(header: Any) -> str | None:
    token = _header_text(header, "CUNIT1").lower().strip()
    if not token:
        return None
    return _CUNIT_ALIASES.get(token)


def ut_seconds_from_header(header: Any) -> float | None:
    """Seconds of UT day at the start of the observation, from ``TIME-OBS``.

    ``DATE-OBS`` is used as a fallback because a few ARTLOOK files carry the
    start time only there.
    """
    text = _header_text(header, "TIME-OBS")
    seconds = _parse_ut_time(text)
    if seconds is not None:
        return seconds

    stamp = _header_text(header, "DATE-OBS")
    if "T" in stamp:
        return _parse_ut_time(stamp.split("T", 1)[1])
    return None


def _parse_ut_time(text: str) -> float | None:
    parts = str(text or "").strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except (TypeError, ValueError):
        return None
    total = hours * 3600.0 + minutes * 60.0 + seconds
    return total if 0.0 <= total < _SECONDS_PER_DAY * 1.5 else None


def infer_time_unit(header: Any) -> tuple[str, str]:
    """Decide what unit ``CRVAL1``/``CDELT1`` are in, and say how.

    ``CUNIT1`` wins when present.  Otherwise ARTLOOK's own convention — UT in
    hours — is cross-checked against ``TIME-OBS``: if ``CRVAL1`` scaled by the
    candidate unit lands on the declared start time, the unit is confirmed.
    That check is what keeps a hard-coded convention from mangling a file
    written with a different one.
    """
    declared = _cunit_time_unit(header)
    if declared is not None:
        return declared, "cunit"

    crval = _header_float(header, "CRVAL1")
    reference = ut_seconds_from_header(header)
    if crval is not None and reference is not None:
        for unit, scale in _TIME_UNIT_SCALES.items():
            if abs((crval * scale) - reference) <= _UT_CROSS_CHECK_TOLERANCE_S:
                return unit, "ut-cross-check"

    return TIME_UNIT_HOURS, "convention"


def artemis_time_axis(header: Any, length: int) -> ArtemisTimeAxis:
    """Build the relative-seconds time axis for an ARTEMIS image HDU.

    The analyzer's ``time`` array is seconds since the first sample, and
    ``ut_start_sec`` carries the absolute UT — the same split a CALLISTO
    ``TIME`` column plus ``TIME-OBS`` gives.  ARTEMIS stores absolute UT on the
    axis itself, so it is rebased here and the offset kept.
    """
    count = max(int(length or 0), 0)
    unit, unit_source = infer_time_unit(header)
    scale = _TIME_UNIT_SCALES.get(unit, 3600.0)

    cdelt = _header_float(header, "CDELT1")
    crval = _header_float(header, "CRVAL1")
    crpix = _header_float(header, "CRPIX1")
    if crpix is None:
        crpix = 1.0

    cadence = None if cdelt is None else abs(cdelt) * scale
    if cadence is not None and not np.isfinite(cadence):
        cadence = None

    if count == 0:
        seconds = np.empty(0, dtype=float)
    elif cdelt is None:
        seconds = np.arange(count, dtype=float)
        cadence = 1.0
    else:
        seconds = np.arange(count, dtype=float) * (float(cdelt) * scale)

    # Absolute UT of the first sample: prefer the axis (it is precise to the
    # sample), fall back to the declared start time.
    ut_start = None
    if crval is not None:
        # FITS pixels are 1-based, so the first sample sits at CRVAL1 offset by
        # (1 - CRPIX1) steps.
        ut_start = (float(crval) + ((1.0 - float(crpix)) * float(cdelt or 0.0))) * scale
    if ut_start is None or not np.isfinite(ut_start) or not (0.0 <= ut_start < _SECONDS_PER_DAY * 1.5):
        ut_start = ut_seconds_from_header(header)

    return ArtemisTimeAxis(
        seconds=seconds,
        ut_start_sec=None if ut_start is None else float(ut_start),
        cadence_s=cadence,
        unit=unit,
        unit_source=unit_source,
    )


# --- Header repair / notes ------------------------------------------------


def parse_fits_datetime(text: str) -> tuple[datetime | None, bool]:
    """Parse a FITS timestamp, tolerating a day/month swap.

    ARTLOOK writes ``DATE`` as ``YYYY-DD-MM``, so a file created on 22 June 2015
    reads ``2015-22-06``.  ``DATE-OBS`` is written correctly, which is why the
    observation time is trusted and the creation time only reported.  Returns
    the parsed value and whether the swap was needed.
    """
    stamp = str(text or "").strip()
    if not stamp:
        return None, False

    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(stamp, fmt), False
        except ValueError:
            continue

    # Same formats, with the second and third fields exchanged.
    parts = stamp.split("-", 2)
    if len(parts) == 3:
        swapped = f"{parts[0]}-{parts[2][:2]}-{parts[1]}{parts[2][2:]}"
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(swapped, fmt), True
            except ValueError:
                continue

    return None, False


def artemis_header_notes(header: Any, *, time_axis: ArtemisTimeAxis | None = None) -> tuple[str, ...]:
    """Header oddities worth surfacing, phrased for the header viewer."""
    notes: list[str] = []

    axis = time_axis if time_axis is not None else artemis_time_axis(header, _header_int(header, "NAXIS1"))
    if axis.unit != TIME_UNIT_SECONDS:
        detail = {
            "cunit": "declared by CUNIT1",
            "ut-cross-check": "confirmed against TIME-OBS",
            "convention": "assumed from the ARTLOOK convention; TIME-OBS was unavailable to confirm it",
        }.get(axis.unit_source, axis.unit_source)
        notes.append(
            f"Time axis stored as UT in {axis.unit} ({detail}); converted to seconds from the first sample."
        )

    _, swapped = parse_fits_datetime(_header_text(header, "DATE"))
    if swapped:
        notes.append(
            f"DATE (file creation) is written as YYYY-DD-MM: {_header_text(header, 'DATE')}. "
            "DATE-OBS is well formed and is what the analyzer uses."
        )

    cdelt2 = _header_float(header, "CDELT2")
    if cdelt2 is not None and cdelt2 < 0.0:
        notes.append("Frequency axis descends from CRVAL2; rows are stored high frequency first.")

    blank = _header_get(header, "BLANK", None)
    if blank is not None:
        notes.append(f"BLANK={blank} marks deleted samples; those become NaN and are excluded from statistics.")

    bunit = _header_text(header, "BUNIT")
    if bunit:
        notes.append(
            f"BUNIT is {bunit!r}: raw ADC counts. ARTEMIS-IV has no absolute flux calibration, "
            "so intensities are relative — dB above the per-channel background."
        )

    return tuple(notes)


# --- Profile --------------------------------------------------------------


def describe_artemis(
    header: Any,
    *,
    freqs: Any = None,
    adc_full_scale_counts: float | None = None,
    dynamic_range_db: float | None = None,
) -> ArtemisProfile | None:
    """Describe an ARTEMIS-IV file, or ``None`` if the header is not one.

    ``freqs`` may be passed when the axis has already been built (the reader
    has it); otherwise it is derived from the header WCS.
    """
    if not is_artemis_header(header):
        return None

    receiver = artemis_receiver(header)
    channel_count = _header_int(header, "NAXIS2")
    sample_count = _header_int(header, "NAXIS1")

    freq_arr = _frequency_axis(header, channel_count) if freqs is None else np.asarray(freqs, dtype=float).ravel()
    freq_low = freq_high = width = None
    if freq_arr.size:
        finite = freq_arr[np.isfinite(freq_arr)]
        if finite.size:
            freq_low = float(np.min(finite))
            freq_high = float(np.max(finite))
        if finite.size > 1:
            steps = np.abs(np.diff(finite))
            steps = steps[steps > 0.0]
            if steps.size:
                width = float(np.median(steps))
    if channel_count <= 0 and freq_arr.size:
        channel_count = int(freq_arr.size)

    axis = artemis_time_axis(header, sample_count)
    duration = None
    if axis.cadence_s and sample_count > 0:
        duration = float(axis.cadence_s) * float(sample_count)

    observed_at, _ = parse_fits_datetime(_header_text(header, "DATE-OBS"))
    created_at, _ = parse_fits_datetime(_header_text(header, "DATE"))

    return ArtemisProfile(
        receiver=receiver,
        origin=_header_text(header, "ORIGIN") or f"{OBSERVATORY}, {STATION}",
        telescope=_header_text(header, "TELESCOP"),
        station=STATION,
        observed_at=observed_at,
        created_at=created_at,
        channel_count=int(max(channel_count, 0)),
        sample_count=int(max(sample_count, 0)),
        freq_low_mhz=freq_low,
        freq_high_mhz=freq_high,
        channel_width_mhz=width,
        cadence_s=axis.cadence_s,
        duration_s=duration,
        bunit=_header_text(header, "BUNIT"),
        calibration=artemis_calibration(
            receiver,
            adc_full_scale_counts=adc_full_scale_counts,
            dynamic_range_db=dynamic_range_db,
        ),
        time_axis_unit=axis.unit,
        notes=artemis_header_notes(header, time_axis=axis),
    )


def _frequency_axis(header: Any, length: int) -> np.ndarray:
    count = max(int(length or 0), 0)
    if count <= 0:
        return np.empty(0, dtype=float)
    crval = _header_float(header, "CRVAL2")
    cdelt = _header_float(header, "CDELT2")
    if crval is None or cdelt is None:
        return np.empty(0, dtype=float)
    crpix = _header_float(header, "CRPIX2")
    if crpix is None:
        crpix = 1.0
    index = np.arange(count, dtype=float) + 1.0
    return crval + (index - crpix) * cdelt


def _format_cadence(seconds: float) -> str:
    value = float(seconds)
    if value >= 1.0:
        return f"{value:g} s cadence"
    rate = 1.0 / value if value > 0 else 0.0
    return f"{rate:g} samples/s"


# --- Reader integration ---------------------------------------------------


def normalize_artemis_axes(
    header: Any,
    freqs: Any,
    time: Any,
    *,
    sample_count: int | None = None,
) -> tuple[np.ndarray, np.ndarray, float | None]:
    """Put an ARTEMIS file's axes into the units the rest of the analyzer uses.

    Frequency is already in MHz and is passed through unchanged.  Time is
    rebuilt as seconds since the first sample, and the UT offset is returned
    separately.  Callers that are not looking at an ARTEMIS header get their
    inputs back untouched.
    """
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    time_arr = np.asarray(time, dtype=float).ravel()

    if not is_artemis_header(header):
        return freq_arr, time_arr, None

    count = int(time_arr.size if sample_count is None else sample_count)
    axis = artemis_time_axis(header, count)
    seconds = axis.seconds
    if seconds.size != count:
        seconds = np.arange(count, dtype=float) * float(axis.cadence_s or 1.0)
    return freq_arr, seconds, axis.ut_start_sec


def artemis_ut_start_sec(header: Any) -> float | None:
    """UT-of-day seconds at the first sample, or ``None`` for a non-ARTEMIS header."""
    if not is_artemis_header(header):
        return None
    return artemis_time_axis(header, _header_int(header, "NAXIS1")).ut_start_sec


def artemis_display_units(profile: ArtemisProfile | None) -> tuple[str, str]:
    """The (linear, logarithmic) intensity unit names for the Units sidebar."""
    if profile is None:
        return "Digits", "dB"
    return "Counts", "dB"


def suggested_display_range(
    data: Any,
    *,
    low_percentile: float = 5.0,
    high_percentile: float = 98.0,
) -> tuple[float, float] | tuple[None, None]:
    """A first-look clipping range for background-subtracted ARTEMIS counts.

    ARTEMIS channels differ in gain by more than an order of magnitude, so the
    fixed +/-100 range that suits CALLISTO digits leaves a burst either clipped
    flat or invisible.  Percentiles over the whole array give a range that
    actually spans the data.
    """
    arr = np.asarray(data, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None, None
    low = float(np.percentile(finite, float(low_percentile)))
    high = float(np.percentile(finite, float(high_percentile)))
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return None, None
    return low, high


def describe_calibration(profile: ArtemisProfile | None) -> tuple[str, ...]:
    """Lines describing the active calibration, for the header viewer / report."""
    if profile is None:
        return ()
    cal = profile.calibration
    lines = [
        f"Receiver: {profile.receiver_label}",
        f"ADC full scale: {cal.adc_full_scale_counts:g} counts (12-bit)",
        f"Dynamic range: {cal.dynamic_range_db:g} dB"
        + (" (assumed — see notes)" if cal.dynamic_range_is_assumed else ""),
        f"Scale: {cal.counts_per_db:.3f} counts/dB ({cal.db_per_count:.5f} dB/count)",
        "Reference level: per-channel background — values are dB above background, not sfu.",
    ]
    return tuple(lines)


def db_scale_for_header(header: Any, default: float) -> float:
    """dB per raw intensity unit for whatever instrument wrote ``header``.

    Non-ARTEMIS headers get ``default`` back, so a caller can stay written
    around the CALLISTO constant it already has and still be right when an
    ARTEMIS file comes through.
    """
    profile = describe_artemis(header)
    if profile is None:
        return float(default)
    return float(profile.calibration.db_per_count)


def linear_unit_for_header(header: Any, default: str = "Digits") -> str:
    """The name of the raw intensity unit for whatever wrote ``header``."""
    return "Counts" if is_artemis_header(header) else str(default)


def artemis_header_report(header: Any, *, freqs: Any = None) -> str:
    """A readable preamble for the FITS header viewer, or "" for other files.

    ARTLOOK headers are terse and, in two places, misleading — the time axis is
    UT in hours where the keyword only says "TIME (UT)", and ``DATE`` has its
    day and month exchanged. Saying so next to the raw cards is cheaper than
    having every user rediscover it.
    """
    profile = describe_artemis(header, freqs=freqs)
    if profile is None:
        return ""

    lines: list[str] = [profile.summary(), ""]

    if profile.observed_at is not None:
        lines.append(f"Observation start : {profile.observed_at:%Y-%m-%d %H:%M:%S} UT")
    if profile.duration_s:
        lines.append(f"Duration          : {profile.duration_s / 3600.0:.2f} h ({profile.duration_s:.0f} s)")
    if profile.channel_width_mhz:
        lines.append(f"Channel spacing   : {profile.channel_width_mhz:.4f} MHz")
    if profile.telescope:
        lines.append(f"Telescope         : {profile.telescope}")

    lines.append("")
    lines.append("Calibration")
    for line in describe_calibration(profile):
        lines.append(f"  {line}")

    if profile.notes:
        lines.append("")
        lines.append("Header notes")
        for note in profile.notes:
            lines.append(f"  - {note}")

    lines.append("")
    lines.append("-" * 72)
    lines.append("")
    return "\n".join(lines)


def summarize_sources(paths: Sequence[str] | None) -> str:
    """Short provenance string for combined ARTEMIS datasets."""
    items = [p for p in (paths or []) if p]
    if not items:
        return ""
    if len(items) == 1:
        return str(items[0])
    return f"{items[0]} … {items[-1]} ({len(items)} files)"
