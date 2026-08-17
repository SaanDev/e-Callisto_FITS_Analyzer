"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import requests


BASE_URL = "https://spdf.gsfc.nasa.gov/pub/data/stereo/combined/swaves/level2_cdf/"
FILE_VERSION = "v02"
FIRST_ARCHIVE_DAY = date(2006, 10, 27)

SPACECRAFT_AHEAD = "ahead"
SPACECRAFT_BEHIND = "behind"
SPACECRAFT_ORDER: tuple[str, ...] = (SPACECRAFT_AHEAD, SPACECRAFT_BEHIND)
SPACECRAFT_LABELS: dict[str, str] = {
    SPACECRAFT_AHEAD: "STEREO-A (Ahead)",
    SPACECRAFT_BEHIND: "STEREO-B (Behind)",
}
# Contact with STEREO-B was lost on 2014-10-01; later files carry fill only.
STEREO_B_LAST_CONTACT = date(2014, 10, 1)

# CDF FILLVAL is -1e31 for every data variable in this product.
FILL_THRESHOLD = -1.0e30

DEFAULT_PAD_SECONDS = 1800
UNITS_LABEL = "Intensity [dB above background]"

MIN_LOG_ROWS = 512
MAX_LOG_ROWS = 2048

_REQUEST_TIMEOUT = 45
_CHUNK_BYTES = 1024 * 256
_USER_AGENT = "e-CALLISTO FITS Analyzer"

_FILENAME_RE = re.compile(r"stereo_level2_swaves_(\d{8})_v(\d+)\.cdf", re.IGNORECASE)

# Year index listings are small and immutable for past years; cache per process.
_YEAR_INDEX_CACHE: dict[int, dict[str, str]] = {}


class SwavesArchiveError(RuntimeError):
    """Raised when the SWAVES archive cannot be accessed or parsed."""


class SwavesNotFoundError(SwavesArchiveError):
    """Raised when the requested SWAVES day file does not exist."""


class SwavesCancelled(RuntimeError):
    """Raised when a caller-supplied cancel callback aborts a load."""


@dataclass(frozen=True)
class SwavesDay:
    """One decoded daily CDF for a single spacecraft."""

    day: date
    spacecraft: str
    times: np.ndarray  # datetime64[ns], ascending
    freqs_khz: np.ndarray  # native channel centres, piecewise ascending
    intensity_db: np.ndarray  # (n_freq, n_time), NaN where filled
    source_path: str


@dataclass(frozen=True)
class SwavesPayload:
    """A resampled SWAVES window ready to draw beside a CALLISTO spectrum.

    ``x_seconds`` is expressed relative to ``base_utc`` so it shares the
    x coordinate frame of the CALLISTO panel, whose x data is always
    seconds-from-file-start with UT applied as a tick formatter.
    """

    spacecraft: str
    start_utc: datetime
    end_utc: datetime
    base_utc: datetime
    x_seconds: np.ndarray
    log_freq_rows: np.ndarray  # log10(kHz), descending (row 0 = highest freq)
    intensity_db: np.ndarray  # (n_rows, n_time), row 0 = highest freq
    units_label: str = UNITS_LABEL
    source_files: tuple[str, ...] = ()

    @property
    def spacecraft_label(self) -> str:
        return SPACECRAFT_LABELS.get(self.spacecraft, self.spacecraft)

    @property
    def freqs_khz(self) -> np.ndarray:
        return np.power(10.0, np.asarray(self.log_freq_rows, dtype=float))

    def matplotlib_extent(self) -> list[float]:
        """[x0, x1, y_bottom, y_top] for imshow(origin='upper')."""
        x0, x1 = self.time_bounds()
        rows = np.asarray(self.log_freq_rows, dtype=float)
        edges = _row_edges(rows)
        return [x0, x1, float(edges[-1]), float(edges[0])]

    def pyqtgraph_extent(self) -> list[float]:
        """[x0, x1, y_min, y_max] for a Cartesian (upward) y axis."""
        x0, x1 = self.time_bounds()
        rows = np.asarray(self.log_freq_rows, dtype=float)
        edges = _row_edges(rows)
        lo, hi = float(min(edges[0], edges[-1])), float(max(edges[0], edges[-1]))
        return [x0, x1, lo, hi]

    def time_bounds(self) -> tuple[float, float]:
        arr = np.asarray(self.x_seconds, dtype=float).ravel()
        if arr.size == 0:
            raise SwavesArchiveError("SWAVES payload has an empty time axis.")
        x0 = float(arr[0])
        x1 = float(arr[-1])
        if abs(x1 - x0) < 1e-9:
            x1 = x0 + 60.0
        return x0, x1

    def rebase(self, base_utc: datetime) -> "SwavesPayload":
        """Re-express ``x_seconds`` against a different time origin.

        The CALLISTO panel's x=0 is its file start, so a payload fetched
        before a FITS file was loaded has to be shifted once the real
        origin is known, or the two panels drift apart.
        """
        target = _as_utc(base_utc)
        shift = (self.base_utc - target).total_seconds()
        if abs(shift) < 1e-9:
            return self
        return SwavesPayload(
            spacecraft=self.spacecraft,
            start_utc=self.start_utc,
            end_utc=self.end_utc,
            base_utc=target,
            x_seconds=np.asarray(self.x_seconds, dtype=float) + shift,
            log_freq_rows=self.log_freq_rows,
            intensity_db=self.intensity_db,
            units_label=self.units_label,
            source_files=self.source_files,
        )

    def to_meta(self) -> dict[str, Any]:
        return {
            "spacecraft": str(self.spacecraft),
            "start_utc": self.start_utc.isoformat(),
            "end_utc": self.end_utc.isoformat(),
            "base_utc": self.base_utc.isoformat(),
            "units_label": str(self.units_label),
            "source_files": [str(p) for p in self.source_files],
        }

    def to_arrays(self) -> dict[str, np.ndarray]:
        return {
            "swaves_x_seconds": np.asarray(self.x_seconds, dtype=np.float64),
            "swaves_log_freq": np.asarray(self.log_freq_rows, dtype=np.float64),
            "swaves_intensity": np.asarray(self.intensity_db, dtype=np.float32),
        }

    @classmethod
    def from_project(
        cls,
        meta: Mapping[str, Any],
        arrays: Mapping[str, np.ndarray],
    ) -> "SwavesPayload | None":
        try:
            x_seconds = np.asarray(arrays["swaves_x_seconds"], dtype=float)
            log_freq = np.asarray(arrays["swaves_log_freq"], dtype=float)
            intensity = np.asarray(arrays["swaves_intensity"], dtype=float)
        except (KeyError, TypeError, ValueError):
            return None
        if x_seconds.size == 0 or log_freq.size == 0 or intensity.size == 0:
            return None
        if intensity.shape != (log_freq.size, x_seconds.size):
            return None
        try:
            return cls(
                spacecraft=normalize_spacecraft(meta.get("spacecraft")),
                start_utc=_parse_iso_utc(meta.get("start_utc")),
                end_utc=_parse_iso_utc(meta.get("end_utc")),
                base_utc=_parse_iso_utc(meta.get("base_utc")),
                x_seconds=x_seconds,
                log_freq_rows=log_freq,
                intensity_db=intensity,
                units_label=str(meta.get("units_label") or UNITS_LABEL),
                source_files=tuple(str(p) for p in (meta.get("source_files") or ())),
            )
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Archive location
# ---------------------------------------------------------------------------


def normalize_spacecraft(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"a", "ahead", "sta", "stereo-a", "stereo_a"}:
        return SPACECRAFT_AHEAD
    if text in {"b", "behind", "stb", "stereo-b", "stereo_b"}:
        return SPACECRAFT_BEHIND
    raise ValueError(f"Unknown STEREO spacecraft: {value!r}")


def build_swaves_filename(day: date, *, version: str = FILE_VERSION) -> str:
    return f"stereo_level2_swaves_{day:%Y%m%d}_{version}.cdf"


def _year_index_url(year: int) -> str:
    return f"{BASE_URL}{year:04d}/"


def _http_client(session: Any | None):
    return session or requests


def _fetch_year_index(year: int, session: Any | None = None) -> dict[str, str]:
    """Map YYYYMMDD -> filename by scraping the year directory listing."""
    cached = _YEAR_INDEX_CACHE.get(int(year))
    if cached is not None:
        return cached

    client = _http_client(session)
    try:
        response = client.get(
            _year_index_url(year),
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
    except Exception as exc:
        raise SwavesArchiveError(f"Could not list the SWAVES archive for {year}: {exc}") from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 404:
        raise SwavesNotFoundError(f"The SWAVES archive has no data for {year}.")
    if status >= 400:
        raise SwavesArchiveError(f"SWAVES archive listing failed with HTTP {status}.")

    index: dict[str, str] = {}
    for match in _FILENAME_RE.finditer(str(getattr(response, "text", "") or "")):
        index[match.group(1)] = match.group(0)

    _YEAR_INDEX_CACHE[int(year)] = index
    return index


def resolve_swaves_filename(day: date, *, session: Any | None = None) -> str:
    """Return the archive filename for ``day``.

    The whole archive currently uses ``_v02``, so that is tried without a
    network round trip. Only when a download 404s do we fall back to the
    directory listing, which keeps a future version bump from breaking this.
    """
    index = _YEAR_INDEX_CACHE.get(day.year)
    if index is not None:
        name = index.get(f"{day:%Y%m%d}")
        if name:
            return name
    return build_swaves_filename(day)


def resolve_swaves_url(day: date, *, filename: str | None = None) -> str:
    return f"{BASE_URL}{day:%Y}/{filename or build_swaves_filename(day)}"


def resolve_swaves_cache_path(
    day: date,
    cache_dir: str | os.PathLike[str],
    *,
    filename: str | None = None,
) -> Path:
    cache_root = Path(cache_dir).expanduser()
    return cache_root / f"{day:%Y}" / (filename or build_swaves_filename(day))


def _download_to(
    url: str,
    destination: Path,
    *,
    session: Any | None,
    progress_cb: Callable[[str, int], None] | None,
    label: str,
) -> None:
    client = _http_client(session)
    temp_path = destination.with_suffix(f"{destination.suffix}.part")

    try:
        with client.get(
            url,
            stream=True,
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        ) as response:
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 404:
                raise SwavesNotFoundError(f"No SWAVES archive data were found for {label}.")
            if status >= 400:
                raise SwavesArchiveError(f"SWAVES archive request failed with HTTP {status}.")

            total = 0
            try:
                total = int(response.headers.get("content-length") or 0)
            except Exception:
                total = 0

            written = 0
            with open(temp_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if progress_cb is not None and total > 0:
                        percent = int(min(100, round(100.0 * written / total)))
                        progress_cb(f"Downloading {label}", percent)
    except SwavesArchiveError:
        _unlink_quietly(temp_path)
        raise
    except Exception as exc:
        _unlink_quietly(temp_path)
        raise SwavesArchiveError(f"Could not download SWAVES archive data: {exc}") from exc

    if not temp_path.exists() or temp_path.stat().st_size <= 0:
        _unlink_quietly(temp_path)
        raise SwavesArchiveError("SWAVES archive download completed without writing data.")

    os.replace(temp_path, destination)


def _unlink_quietly(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def download_swaves_day(
    day: date,
    cache_dir: str | os.PathLike[str],
    *,
    session: Any | None = None,
    progress_cb: Callable[[str, int], None] | None = None,
) -> str:
    """Download (or reuse a cached copy of) the daily SWAVES CDF."""
    if day < FIRST_ARCHIVE_DAY:
        raise SwavesNotFoundError(
            f"The SWAVES archive starts on {FIRST_ARCHIVE_DAY.isoformat()}; {day.isoformat()} is earlier."
        )

    filename = resolve_swaves_filename(day, session=session)
    destination = resolve_swaves_cache_path(day, cache_dir, filename=filename)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and destination.stat().st_size > 0:
        if progress_cb is not None:
            progress_cb(f"Using cached {filename}", 100)
        return str(destination)

    label = day.isoformat()
    try:
        _download_to(
            resolve_swaves_url(day, filename=filename),
            destination,
            session=session,
            progress_cb=progress_cb,
            label=label,
        )
        return str(destination)
    except SwavesNotFoundError:
        pass

    # Version fallback: consult the directory listing for the real filename.
    index = _fetch_year_index(day.year, session=session)
    listed = index.get(f"{day:%Y%m%d}")
    if not listed:
        raise SwavesNotFoundError(f"No SWAVES archive data were found for {label}.")
    if listed == filename:
        raise SwavesNotFoundError(f"No SWAVES archive data were found for {label}.")

    destination = resolve_swaves_cache_path(day, cache_dir, filename=listed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return str(destination)

    _download_to(
        resolve_swaves_url(day, filename=listed),
        destination,
        session=session,
        progress_cb=progress_cb,
        label=label,
    )
    return str(destination)


# ---------------------------------------------------------------------------
# CDF decoding
# ---------------------------------------------------------------------------


def _open_cdf(path: str | os.PathLike[str]):
    try:
        import cdflib
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise SwavesArchiveError(
            "Reading SWAVES data needs the 'cdflib' package, which is not installed."
        ) from exc

    try:
        return cdflib.CDF(str(path))
    except Exception as exc:
        raise SwavesArchiveError(f"Could not open SWAVES CDF file: {exc}") from exc


def _variable(cdf, name: str) -> np.ndarray:
    try:
        return np.asarray(cdf.varget(name))
    except Exception as exc:
        raise SwavesArchiveError(f"SWAVES CDF is missing the '{name}' variable: {exc}") from exc


def _blank_fill(arr: np.ndarray) -> np.ndarray:
    out = np.asarray(arr, dtype=float)
    out = np.where(np.isfinite(out) & (out > FILL_THRESHOLD), out, np.nan)
    return out


# A band-boundary step must stand this far above the profile's typical
# step-to-step scatter, and this far above the mirrored position, to count.
_BAND_STEP_MIN_RATIO = 3.0
_BAND_STEP_MIN_MARGIN = 2.0


def band_boundary_orientation(profile: np.ndarray, split: int) -> int:
    """Decide whether a frequency profile is index-aligned with its axis.

    The LFR and HFR receivers have very different noise floors, so a profile
    that is index-aligned with ``frequency`` carries a sharp step at the band
    boundary. If the array were stored back-to-front the step would instead
    land at the mirrored index.

    Returns ``+1`` (aligned), ``-1`` (mirrored) or ``0`` (no verdict).
    """
    prof = np.asarray(profile, dtype=float).ravel()
    n = prof.size
    if split <= 1 or split >= n - 1:
        return 0

    steps = np.abs(np.diff(prof))
    finite = steps[np.isfinite(steps)]
    if finite.size < 8:
        return 0

    scale = float(np.median(finite))
    if scale <= 0.0:
        peak = float(np.max(finite))
        if peak <= 0.0:
            return 0
        scale = peak / 100.0

    at_split = float(steps[split - 1]) / scale
    at_mirror = float(steps[n - split - 1]) / scale
    if not (np.isfinite(at_split) and np.isfinite(at_mirror)):
        return 0

    if at_split >= _BAND_STEP_MIN_RATIO and at_split > _BAND_STEP_MIN_MARGIN * at_mirror:
        return 1
    if at_mirror >= _BAND_STEP_MIN_RATIO and at_mirror > _BAND_STEP_MIN_MARGIN * at_split:
        return -1
    return 0


def orient_to_frequency_axis(
    intensity: np.ndarray,
    background: np.ndarray,
    freqs_khz: np.ndarray,
) -> np.ndarray:
    """Align ``intensity`` rows with the file's ``frequency`` support variable.

    The ``avg_intens_*`` CATDESC claims an inverted frequency order, but every
    file inspected stores the data index-aligned with ``frequency``. Rather
    than trust either label, key on the LFR/HFR receiver step, which is a
    strong and unambiguous marker in the background profile (the two arrays
    for a spacecraft always share a row order). No verdict means no flip, so
    an unfamiliar file is passed through rather than silently mirrored.
    """
    data = np.asarray(intensity, dtype=float)
    ref = np.asarray(background, dtype=float)
    if data.ndim != 2 or ref.shape != data.shape:
        return data

    bands = frequency_bands(freqs_khz)
    if len(bands) != 2:
        return data

    with warnings.catch_warnings():
        # An all-fill spacecraft (STEREO-B after 2014) is an empty slice here.
        warnings.simplefilter("ignore", RuntimeWarning)
        ref_profile = np.nanmean(ref, axis=1)

    if band_boundary_orientation(ref_profile, bands[0][1]) < 0:
        return data[::-1, :]
    return data


def read_swaves_cdf(path: str | os.PathLike[str], spacecraft: str) -> SwavesDay:
    """Decode one daily CDF into (n_freq, n_time) with NaN for fill."""
    craft = normalize_spacecraft(spacecraft)
    cdf = _open_cdf(path)

    from cdflib.epochs import CDFepoch

    epochs = _variable(cdf, "Epoch")
    try:
        times = np.asarray(CDFepoch.to_datetime(epochs), dtype="datetime64[ns]")
    except Exception as exc:
        raise SwavesArchiveError(f"Could not decode the SWAVES time axis: {exc}") from exc

    freqs = np.asarray(_variable(cdf, "frequency"), dtype=float).ravel()

    # CDF stores (n_time, n_freq); the app works in (n_freq, n_time).
    intensity = _blank_fill(_variable(cdf, f"avg_intens_{craft}")).T
    background = _blank_fill(_variable(cdf, f"background_{craft}")).T

    if intensity.shape[0] != freqs.size:
        raise SwavesArchiveError(
            f"SWAVES frequency axis ({freqs.size}) does not match the data ({intensity.shape[0]})."
        )
    if times.size != intensity.shape[1]:
        raise SwavesArchiveError(
            f"SWAVES time axis ({times.size}) does not match the data ({intensity.shape[1]})."
        )

    intensity = orient_to_frequency_axis(intensity, background, freqs)

    day_value = times[0].astype("datetime64[D]").astype(object) if times.size else FIRST_ARCHIVE_DAY
    return SwavesDay(
        day=day_value,
        spacecraft=craft,
        times=times,
        freqs_khz=freqs,
        intensity_db=intensity,
        source_path=str(path),
    )


def spacecraft_has_data(path: str | os.PathLike[str], spacecraft: str) -> bool:
    """True when the file holds at least one finite sample for ``spacecraft``."""
    craft = normalize_spacecraft(spacecraft)
    try:
        cdf = _open_cdf(path)
        arr = _blank_fill(_variable(cdf, f"avg_intens_{craft}"))
    except SwavesArchiveError:
        return False
    return bool(np.any(np.isfinite(arr)))


# ---------------------------------------------------------------------------
# Frequency resampling
# ---------------------------------------------------------------------------


def frequency_bands(freqs_khz: Sequence[float] | np.ndarray) -> list[tuple[int, int]]:
    """Split the channel list wherever the frequency axis stops increasing.

    SWAVES concatenates a log-spaced LFR band (2.61-153.36 kHz) with a
    linearly spaced HFR band that restarts at 125 kHz, so the axis is only
    piecewise monotonic. The split points are detected rather than hard-coded.
    """
    arr = np.asarray(freqs_khz, dtype=float).ravel()
    if arr.size == 0:
        return []
    breaks = np.where(np.diff(arr) <= 0.0)[0]
    bounds = [0, *(int(b) + 1 for b in breaks), arr.size]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] > bounds[i]]


def suggested_log_rows(freqs_khz: Sequence[float] | np.ndarray) -> int:
    """Rows needed to preserve the finest native step, clipped to a sane range."""
    arr = np.asarray(freqs_khz, dtype=float).ravel()
    arr = arr[np.isfinite(arr) & (arr > 0.0)]
    if arr.size < 2:
        return MIN_LOG_ROWS

    ratios: list[float] = []
    for lo, hi in frequency_bands(arr):
        band = arr[lo:hi]
        if band.size < 2:
            continue
        step = band[1:] / band[:-1]
        step = step[np.isfinite(step) & (step > 1.0)]
        if step.size:
            ratios.append(float(np.min(step)))
    if not ratios:
        return MIN_LOG_ROWS

    span = float(np.log10(arr.max() / arr.min()))
    finest = float(np.log10(min(ratios)))
    if finest <= 0.0 or span <= 0.0:
        return MIN_LOG_ROWS
    return int(np.clip(int(np.ceil(span / finest)), MIN_LOG_ROWS, MAX_LOG_ROWS))


def _interp_weights(src_log: np.ndarray, dst_log: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    idx = np.clip(np.searchsorted(src_log, dst_log, side="left"), 1, src_log.size - 1)
    lo = idx - 1
    hi = idx
    span = src_log[hi] - src_log[lo]
    span = np.where(np.abs(span) < 1e-15, 1.0, span)
    weight = (dst_log - src_log[lo]) / span
    return lo, hi, np.clip(weight, 0.0, 1.0)


def _row_edges(rows: np.ndarray) -> np.ndarray:
    arr = np.asarray(rows, dtype=float).ravel()
    if arr.size == 1:
        return np.array([arr[0] - 0.5, arr[0] + 0.5], dtype=float)
    mid = 0.5 * (arr[1:] + arr[:-1])
    first = arr[0] - (mid[0] - arr[0])
    last = arr[-1] + (arr[-1] - mid[-1])
    return np.concatenate(([first], mid, [last]))


def resample_log_frequency(
    intensity: np.ndarray,
    freqs_khz: np.ndarray,
    *,
    nrows: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate a (n_freq, n_time) spectrogram onto a uniform log grid.

    Each monotonic band is interpolated in log10(frequency) over the grid rows
    it covers; overlapping bands are averaged. Rows outside every band stay
    NaN so ``masked_display_data`` renders them transparent.

    Returns ``(grid_khz_ascending, resampled)`` with ``resampled`` shaped
    ``(nrows, n_time)``.
    """
    data = np.asarray(intensity, dtype=float)
    freqs = np.asarray(freqs_khz, dtype=float).ravel()
    if data.ndim != 2:
        raise ValueError(f"Expected a 2D spectrogram, got ndim={data.ndim}.")
    if data.shape[0] != freqs.size:
        raise ValueError(f"Frequency axis ({freqs.size}) does not match data rows ({data.shape[0]}).")

    valid = np.isfinite(freqs) & (freqs > 0.0)
    if int(np.count_nonzero(valid)) < 2:
        raise ValueError("SWAVES frequency axis needs at least two positive channels.")

    rows = int(nrows) if nrows else suggested_log_rows(freqs[valid])
    rows = int(np.clip(rows, 2, MAX_LOG_ROWS))

    f_lo = float(np.min(freqs[valid]))
    f_hi = float(np.max(freqs[valid]))
    grid = np.logspace(np.log10(f_lo), np.log10(f_hi), rows)
    grid_log = np.log10(grid)

    total = np.zeros((rows, data.shape[1]), dtype=float)
    count = np.zeros((rows, data.shape[1]), dtype=float)

    for lo, hi in frequency_bands(freqs):
        band_f = freqs[lo:hi]
        band_d = data[lo:hi, :]
        keep = np.isfinite(band_f) & (band_f > 0.0)
        if int(np.count_nonzero(keep)) < 2:
            continue
        band_f = band_f[keep]
        band_d = band_d[keep, :]

        order = np.argsort(band_f)
        band_f = band_f[order]
        band_d = band_d[order, :]
        band_log = np.log10(band_f)

        covered = (grid_log >= band_log[0]) & (grid_log <= band_log[-1])
        if not np.any(covered):
            continue

        idx_lo, idx_hi, weight = _interp_weights(band_log, grid_log[covered])
        values = band_d[idx_lo, :] * (1.0 - weight)[:, None] + band_d[idx_hi, :] * weight[:, None]

        # Count per element so NaN samples never drag the overlap average down.
        finite = np.isfinite(values)
        total[covered] += np.where(finite, values, 0.0)
        count[covered] += finite.astype(float)

    out = np.full((rows, data.shape[1]), np.nan, dtype=float)
    filled = count > 0.0
    out[filled] = total[filled] / count[filled]
    return grid, out


# ---------------------------------------------------------------------------
# Window assembly
# ---------------------------------------------------------------------------


def _parse_iso_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_datetime64(value: datetime) -> np.datetime64:
    return np.datetime64(_as_utc(value).replace(tzinfo=None), "ns")


def days_in_window(start_utc: datetime, end_utc: datetime) -> list[date]:
    first = _as_utc(start_utc).date()
    last = _as_utc(end_utc).date()
    if last < first:
        first, last = last, first
    span = (last - first).days
    return [first + timedelta(days=offset) for offset in range(span + 1)]


def _check_cancelled(cancel_cb: Callable[[], bool] | None) -> None:
    if cancel_cb is not None and bool(cancel_cb()):
        raise SwavesCancelled("SWAVES load cancelled.")


def load_swaves_window(
    start_utc: datetime,
    end_utc: datetime,
    spacecraft: str,
    cache_dir: str | os.PathLike[str],
    *,
    base_utc: datetime | None = None,
    nrows: int | None = None,
    session: Any | None = None,
    progress_cb: Callable[[str, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> SwavesPayload:
    """Fetch, stitch, slice and resample a SWAVES window.

    Every UTC day the window touches is downloaded and concatenated, so a
    request straddling midnight is never silently truncated.
    """
    craft = normalize_spacecraft(spacecraft)
    start = _as_utc(start_utc)
    end = _as_utc(end_utc)
    if end <= start:
        raise ValueError("The SWAVES window end must be after its start.")
    base = _as_utc(base_utc) if base_utc is not None else start

    day_list = days_in_window(start, end)
    times_parts: list[np.ndarray] = []
    data_parts: list[np.ndarray] = []
    freqs: np.ndarray | None = None
    sources: list[str] = []
    missing: list[date] = []

    for position, day in enumerate(day_list, start=1):
        _check_cancelled(cancel_cb)
        if progress_cb is not None:
            progress_cb(f"Fetching SWAVES {day.isoformat()} ({position}/{len(day_list)})", 0)
        try:
            local_path = download_swaves_day(day, cache_dir, session=session, progress_cb=progress_cb)
        except SwavesNotFoundError:
            missing.append(day)
            continue

        _check_cancelled(cancel_cb)
        decoded = read_swaves_cdf(local_path, craft)
        if freqs is None:
            freqs = decoded.freqs_khz
        elif decoded.freqs_khz.shape != freqs.shape or not np.allclose(
            decoded.freqs_khz, freqs, equal_nan=True
        ):
            raise SwavesArchiveError(
                f"SWAVES frequency axis changed on {day.isoformat()}; cannot stitch these days."
            )
        times_parts.append(decoded.times)
        data_parts.append(decoded.intensity_db)
        sources.append(os.path.basename(local_path))

    if freqs is None or not data_parts:
        span = ", ".join(day.isoformat() for day in (missing or day_list))
        raise SwavesNotFoundError(f"No SWAVES data are available for {span}.")

    times = np.concatenate(times_parts)
    data = np.concatenate(data_parts, axis=1)

    order = np.argsort(times, kind="stable")
    times = times[order]
    data = data[:, order]
    unique = np.concatenate(([True], np.diff(times) != np.timedelta64(0, "ns")))
    times = times[unique]
    data = data[:, unique]

    keep = (times >= _to_datetime64(start)) & (times <= _to_datetime64(end))
    if not np.any(keep):
        raise SwavesNotFoundError(
            f"No SWAVES samples fall between {start.isoformat()} and {end.isoformat()}."
        )
    times = times[keep]
    data = data[:, keep]

    _check_cancelled(cancel_cb)
    if progress_cb is not None:
        progress_cb("Resampling SWAVES frequency axis", 0)

    grid_khz, resampled = resample_log_frequency(data, freqs, nrows=nrows)

    # Row 0 must be the highest frequency so the panel stacks continuously
    # under the CALLISTO spectrum, which is drawn with origin="upper".
    log_rows = np.log10(grid_khz)[::-1]
    resampled = resampled[::-1, :]

    x_seconds = (times - _to_datetime64(base)) / np.timedelta64(1, "s")

    return SwavesPayload(
        spacecraft=craft,
        start_utc=start,
        end_utc=end,
        base_utc=base,
        x_seconds=np.asarray(x_seconds, dtype=float),
        log_freq_rows=np.asarray(log_rows, dtype=float),
        intensity_db=np.asarray(resampled, dtype=float),
        units_label=UNITS_LABEL,
        source_files=tuple(sources),
    )


def format_frequency_khz(value_khz: float) -> str:
    """Human tick label for a frequency given in kHz."""
    try:
        khz = float(value_khz)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(khz) or khz <= 0.0:
        return ""
    if khz >= 1000.0:
        mhz = khz / 1000.0
        text = f"{mhz:.1f}".rstrip("0").rstrip(".")
        return f"{text} MHz"
    if khz >= 10.0:
        return f"{khz:.0f} kHz"
    text = f"{khz:.1f}".rstrip("0").rstrip(".")
    return f"{text} kHz"


def format_log_frequency(log_khz: float) -> str:
    """Human tick label for a log10(kHz) axis coordinate."""
    try:
        return format_frequency_khz(10.0 ** float(log_khz))
    except (TypeError, ValueError, OverflowError):
        return ""


def log_frequency_ticks(log_lo: float, log_hi: float, max_ticks: int = 8) -> list[float]:
    """Decade (and half-decade when sparse) tick positions on a log10(kHz) axis."""
    lo = float(min(log_lo, log_hi))
    hi = float(max(log_lo, log_hi))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return []

    for step in (1.0, 0.5, np.log10(2.0)):
        first = np.ceil(lo / step) * step
        ticks = np.arange(first, hi + 1e-9, step)
        if 2 <= ticks.size <= max_ticks:
            return [float(t) for t in ticks]

    return [float(t) for t in np.linspace(lo, hi, min(max_ticks, 5))]
