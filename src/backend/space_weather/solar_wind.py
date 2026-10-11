"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Solar wind, IMF and auroral indices at Earth (src/backend/space_weather/solar_wind.py).

Primary source: OMNI HRO 5-minute data (CDAWeb ``OMNI_HRO_5MIN``), one CDF per
month from SPDF, disk cached. OMNI is time-shifted to the bow-shock nose and
carries the derived parameters (Ey, plasma beta, Alfvén Mach number) and the
SYM-H / AE indices as well.

OMNI is published with a lag of weeks: the newest monthly file exists but its
IMF and plasma columns end some days before its last record. Past that point
the L1 measurements are used instead, from the newest source that has them:

* NOAA NCEI DSCOVR daily 1-minute files (``oe_m1m`` magnetometer, ``oe_f1m``
  Faraday cup, netCDF), and
* SWPC's real-time solar-wind feed (``json/rtsw``), which covers the last day
  or so and mixes spacecraft — only rows flagged ``active`` are used.

Those segments are labelled "L1, not time-shifted to the bow shock", and their
Ey, beta and Mach number are computed with OMNI's own formulas. SYM-H and AE
have no such fallback; :func:`load_solar_wind` reports them as not yet
published.

Variable names and fill values were checked against
``omni_hro_5min_20240501_v01.cdf`` and ``oe_{m1m,f1m}_dscovr_s20250930…_pub.nc``.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

OMNI_BASE_URL = "https://spdf.gsfc.nasa.gov/pub/data/omni/omni_cdaweb/hro_5min/"
DSCOVR_LISTING_URLS = (
    "https://www.ngdc.noaa.gov/dscovr/data/{year:04d}/{month:02d}/",
    "https://lasco6.nascom.nasa.gov/pub/sdb/dscovr/{year:04d}/{month:02d}/",
)
SWPC_RTSW_MAG_URL = "https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json"
SWPC_RTSW_WIND_URL = "https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json"

SOURCE_OMNI = "OMNI"
SOURCE_DSCOVR = "DSCOVR (NCEI)"
SOURCE_RTSW = "SWPC real-time"
L1_NOTE = "L1, not time-shifted to the bow shock"

_HTTP_TIMEOUT = (8, 90)
_USER_AGENT = "e-CALLISTO FITS Analyzer"
_DSCOVR_NAME_RE = re.compile(
    r"oe_(m1m|f1m)_dscovr_s(\d{8})\d{6}_e\d{14}_p\d{14}_pub\.nc(?:\.gz)?", re.IGNORECASE
)

#: OMNI variables read, and the series names they become.
OMNI_VARIABLES: dict[str, str] = {
    "BY_GSM": "by",
    "BZ_GSM": "bz",
    "F": "bt",
    "flow_speed": "v",
    "proton_density": "np",
    "T": "t",
    "Pressure": "pdyn",
    "E": "ey",
    "Beta": "beta",
    "Mach_num": "mach",
    "SYM_H": "symh",
    "AE_INDEX": "ae",
    "AL_INDEX": "al",
    "AU_INDEX": "au",
}

#: Series in a :class:`SolarWindData`, with their units.
SERIES_UNITS: dict[str, str] = {
    "bt": "nT",
    "bz": "nT",
    "by": "nT",
    "v": "km/s",
    "np": "cm⁻³",
    "t": "K",
    "pdyn": "nPa",
    "ey": "mV/m",
    "beta": "",
    "mach": "",
    "symh": "nT",
    "ae": "nT",
    "al": "nT",
    "au": "nT",
}

IMF_SERIES = ("bt", "bz", "by")
PLASMA_SERIES = ("v", "np", "t")
DERIVED_SERIES = ("pdyn", "ey", "beta", "mach")
INDEX_SERIES = ("symh", "ae", "al", "au")


class SolarWindError(RuntimeError):
    """Data could not be downloaded or decoded."""


class SolarWindCancelled(RuntimeError):
    """The caller cancelled the load."""


@dataclass(frozen=True)
class Segment:
    """Where one stretch of a series came from."""

    start: datetime
    end: datetime
    source: str
    note: str = ""


@dataclass
class SolarWindData:
    """Every series on one shared time axis (naive UTC), NaN where missing."""

    times: np.ndarray  # datetime64[s]
    series: dict[str, np.ndarray] = field(default_factory=dict)
    #: Per series, the source segments in time order.
    segments: dict[str, list[Segment]] = field(default_factory=dict)
    #: Human notes, e.g. "SYM-H/AE not yet published after 2026-09-30".
    notes: list[str] = field(default_factory=list)

    def provenance_at(self, name: str, when: np.datetime64) -> str:
        for segment in self.segments.get(name, []):
            if np.datetime64(segment.start) <= when <= np.datetime64(segment.end):
                return segment.source + (f" ({segment.note})" if segment.note else "")
        return ""


# --------------------------------------------------------------------- helpers


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _check_cancel(cancel_cb: Callable[[], bool] | None) -> None:
    try:
        cancelled = bool(cancel_cb and cancel_cb())
    except Exception:
        cancelled = False
    if cancelled:
        raise SolarWindCancelled("Solar wind load cancelled.")


def _report(progress_cb: Callable[[int | None, str | None], None] | None, value: int | None, text: str) -> None:
    if progress_cb is not None:
        try:
            progress_cb(value, text)
        except Exception:
            pass


def _client(session: Any) -> Any:
    if session is not None:
        return session
    import requests

    return requests


def _months(start: datetime, end: datetime) -> list[tuple[int, int]]:
    out = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _days(start: datetime, end: datetime) -> list[datetime]:
    day = datetime(start.year, start.month, start.day)
    out = []
    while day <= end:
        out.append(day)
        day += timedelta(days=1)
    return out


# ------------------------------------------------------------- derived physics


def flow_pressure_npa(density_cc: np.ndarray, speed_kms: np.ndarray) -> np.ndarray:
    """OMNI: P = 2e-6 · Np · V² nPa (the 2 allows ~4 % alphas)."""
    return 2.0e-6 * np.asarray(density_cc, dtype=float) * np.asarray(speed_kms, dtype=float) ** 2


def electric_field_mvm(speed_kms: np.ndarray, bz_nt: np.ndarray) -> np.ndarray:
    """OMNI: Ey = −V · Bz × 10⁻³ mV/m (V in km/s, Bz GSM in nT)."""
    return -np.asarray(speed_kms, dtype=float) * np.asarray(bz_nt, dtype=float) * 1.0e-3


def plasma_beta(temperature_k: np.ndarray, density_cc: np.ndarray, bt_nt: np.ndarray) -> np.ndarray:
    """OMNI: β = [(T · 4.16 × 10⁻⁵) + 5.34] · Np / B²."""
    t = np.asarray(temperature_k, dtype=float)
    n = np.asarray(density_cc, dtype=float)
    b = np.asarray(bt_nt, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = ((t * 4.16e-5) + 5.34) * n / (b * b)
    return np.where(np.isfinite(out), out, np.nan)


def alfven_mach(speed_kms: np.ndarray, density_cc: np.ndarray, bt_nt: np.ndarray) -> np.ndarray:
    """OMNI: Ma = V · Np^½ / (20 · B)."""
    v = np.asarray(speed_kms, dtype=float)
    n = np.asarray(density_cc, dtype=float)
    b = np.asarray(bt_nt, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = v * np.sqrt(n) / (20.0 * b)
    return np.where(np.isfinite(out), out, np.nan)


def derive_parameters(series: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Pdyn, Ey, β and Mach from Bt/Bz/V/Np/T, as OMNI computes them."""
    v, n, t = series["v"], series["np"], series["t"]
    return {
        "pdyn": flow_pressure_npa(n, v),
        "ey": electric_field_mvm(v, series["bz"]),
        "beta": plasma_beta(t, n, series["bt"]),
        "mach": alfven_mach(v, n, series["bt"]),
    }


# ------------------------------------------------------------------------ OMNI


def omni_filename(year: int, month: int) -> str:
    return f"omni_hro_5min_{year:04d}{month:02d}01_v01.cdf"


def omni_url(year: int, month: int) -> str:
    return f"{OMNI_BASE_URL}{year:04d}/{omni_filename(year, month)}"


def _download(url: str, destination: Path, *, session: Any, cancel_cb: Callable[[], bool] | None) -> bool:
    """Stream ``url`` into ``destination``; False on 404, raises otherwise."""
    client = _client(session)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        response = client.get(url, timeout=_HTTP_TIMEOUT, headers={"User-Agent": _USER_AGENT}, stream=True)
    except Exception as exc:
        raise SolarWindError(f"Could not reach {url}: {exc}") from exc
    try:
        status = int(getattr(response, "status_code", 200) or 200)
        if status == 404:
            return False
        if status >= 400:
            raise SolarWindError(f"{url} returned HTTP {status}.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(partial, "wb") as handle:
            chunks = response.iter_content(chunk_size=256 * 1024) if hasattr(response, "iter_content") else [response.content]
            for chunk in chunks:
                _check_cancel(cancel_cb)
                if chunk:
                    handle.write(chunk)
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    os.replace(partial, destination)
    return True


def _month_is_settled(year: int, month: int, *, now: datetime) -> bool:
    """A month whose OMNI file can no longer change (well past its end)."""
    after = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return now - after > timedelta(days=120)


def omni_month_path(
    year: int,
    month: int,
    cache_dir: str | os.PathLike[str],
    *,
    session: Any = None,
    cancel_cb: Callable[[], bool] | None = None,
    now: datetime | None = None,
    max_age_s: float = 12 * 3600.0,
) -> Path | None:
    """The cached OMNI CDF for a month, downloading it if needed; None if unpublished.

    Recent months are still being filled in, so their cached copy is refreshed
    after ``max_age_s``; months long finished are kept for good.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    path = Path(cache_dir) / "omni_hro_5min" / f"{year:04d}" / omni_filename(year, month)
    if path.is_file() and path.stat().st_size > 0:
        fresh = _month_is_settled(year, month, now=now) or (time.time() - path.stat().st_mtime) < max_age_s
        if fresh:
            return path
    try:
        found = _download(omni_url(year, month), path, session=session, cancel_cb=cancel_cb)
    except SolarWindError:
        if path.is_file():
            return path  # stale beats nothing when the network is down
        raise
    return path if found else None


def _cdf_fill_mask(values: np.ndarray, attrs: Mapping[str, Any]) -> np.ndarray:
    data = np.asarray(values, dtype=float)
    bad = ~np.isfinite(data)
    fill = attrs.get("FILLVAL")
    try:
        fill_value = float(np.asarray(fill).ravel()[0]) if fill is not None else None
    except (TypeError, ValueError, IndexError):
        fill_value = None
    if fill_value is not None:
        bad |= np.isclose(data, fill_value, rtol=0.0, atol=abs(fill_value) * 1e-6 + 1e-6)
        if fill_value > 0:
            bad |= data >= fill_value * 0.999
    for key, compare in (("VALIDMIN", np.less), ("VALIDMAX", np.greater)):
        limit = attrs.get(key)
        try:
            limit_value = float(np.asarray(limit).ravel()[0]) if limit is not None else None
        except (TypeError, ValueError, IndexError):
            limit_value = None
        if limit_value is not None:
            bad |= compare(data, limit_value)
    return bad


def read_omni_cdf(path: str | os.PathLike[str]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """``(times, series)`` from one OMNI HRO CDF; fill values become NaN."""
    try:
        import cdflib
        from cdflib.epochs import CDFepoch
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise SolarWindError("Reading OMNI data needs the 'cdflib' package.") from exc
    try:
        cdf = cdflib.CDF(str(path))
        epoch = np.asarray(cdf.varget("Epoch"))
        times = np.asarray(CDFepoch.to_datetime(epoch), dtype="datetime64[s]")
    except Exception as exc:
        raise SolarWindError(f"Could not read the OMNI file {Path(path).name}: {exc}") from exc
    series: dict[str, np.ndarray] = {}
    for variable, name in OMNI_VARIABLES.items():
        try:
            raw = np.asarray(cdf.varget(variable))
            attrs = dict(cdf.varattsget(variable) or {})
        except Exception:
            series[name] = np.full(times.shape, np.nan)
            continue
        values = raw.astype(float)
        values[_cdf_fill_mask(raw, attrs)] = np.nan
        series[name] = values
    return times, series


# ------------------------------------------------------------------- DSCOVR L1


def parse_dscovr_listing(html: str) -> dict[tuple[str, str], str]:
    """``{(product, YYYYMMDD): filename}`` from a DSCOVR directory listing.

    Several processing runs of one day can be listed; the newest (latest
    ``p`` stamp, which sorts last) wins.
    """
    found: dict[tuple[str, str], str] = {}
    for name in sorted({m.group(0) for m in _DSCOVR_NAME_RE.finditer(html or "")}):
        m = _DSCOVR_NAME_RE.fullmatch(name)
        if m:
            found[(m.group(1).lower(), m.group(2))] = name
    return found


def read_dscovr_netcdf(path: str | os.PathLike[str], product: str) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """``(times, series)`` from one DSCOVR ``m1m`` or ``f1m`` file (``.nc`` or ``.nc.gz``)."""
    try:
        import netCDF4
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise SolarWindError("Reading DSCOVR data needs the 'netCDF4' package.") from exc
    path = str(path)
    try:
        if path.lower().endswith(".gz"):
            with gzip.open(path, "rb") as handle:
                dataset = netCDF4.Dataset("inmemory.nc", memory=handle.read())
        else:
            dataset = netCDF4.Dataset(path)
    except Exception as exc:
        raise SolarWindError(f"Could not read the DSCOVR file {Path(path).name}: {exc}") from exc
    with dataset:
        ms = np.asarray(dataset.variables["time"][:], dtype="float64")
        times = (np.datetime64("1970-01-01T00:00:00") + ms.astype("int64").astype("timedelta64[ms]")).astype(
            "datetime64[s]"
        )
        wanted = (
            {"bt": "bt", "bz_gsm": "bz", "by_gsm": "by"}
            if product == "m1m"
            else {"proton_speed": "v", "proton_density": "np", "proton_temperature": "t"}
        )
        series: dict[str, np.ndarray] = {}
        for variable, name in wanted.items():
            var = dataset.variables.get(variable)
            if var is None:
                series[name] = np.full(times.shape, np.nan)
                continue
            raw = var[:]
            values = np.ma.filled(np.ma.asarray(raw, dtype=float), np.nan)
            values = np.where(values <= -99990.0, np.nan, values)
            series[name] = values
    return times, series


def load_dscovr_range(
    start: datetime,
    end: datetime,
    cache_dir: str | os.PathLike[str],
    *,
    session: Any = None,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    """DSCOVR 1-minute IMF and plasma for the days in ``[start, end]``; None if none found."""
    client = _client(session)
    root = Path(cache_dir) / "dscovr"
    listings: dict[tuple[int, int], dict[tuple[str, str], tuple[str, str]]] = {}
    for year, month in _months(start, end):
        merged: dict[tuple[str, str], tuple[str, str]] = {}
        for template in DSCOVR_LISTING_URLS:
            base = template.format(year=year, month=month)
            _check_cancel(cancel_cb)
            try:
                response = client.get(base, timeout=_HTTP_TIMEOUT, headers={"User-Agent": _USER_AGENT})
                if int(getattr(response, "status_code", 200) or 200) >= 400:
                    continue
                for key, name in parse_dscovr_listing(response.text).items():
                    merged.setdefault(key, (base, name))
            except Exception:
                continue
        listings[(year, month)] = merged

    times_parts: list[np.ndarray] = []
    series_parts: dict[str, list[np.ndarray]] = {name: [] for name in IMF_SERIES + PLASMA_SERIES}
    found_any = False
    for day in _days(start, end):
        key_day = f"{day:%Y%m%d}"
        files = listings.get((day.year, day.month), {})
        day_series: dict[str, tuple[np.ndarray, dict[str, np.ndarray]]] = {}
        for product in ("m1m", "f1m"):
            entry = files.get((product, key_day))
            if entry is None:
                continue
            base, name = entry
            path = root / f"{day:%Y}" / name
            _check_cancel(cancel_cb)
            if not (path.is_file() and path.stat().st_size > 0):
                _report(progress_cb, None, f"Downloading DSCOVR {product} for {day:%Y-%m-%d}…")
                try:
                    if not _download(base + name, path, session=session, cancel_cb=cancel_cb):
                        continue
                except SolarWindError:
                    continue
            try:
                day_series[product] = read_dscovr_netcdf(path, product)
            except SolarWindError:
                continue
        if not day_series:
            continue
        found_any = True
        axis = np.unique(np.concatenate([value[0] for value in day_series.values()]))
        times_parts.append(axis)
        for product, (times, values) in day_series.items():
            for name, column in values.items():
                aligned = np.full(axis.shape, np.nan)
                index = np.searchsorted(axis, times)
                aligned[index] = column
                series_parts[name].append(aligned)
        for name in series_parts:
            if len(series_parts[name]) < len(times_parts):
                series_parts[name].append(np.full(axis.shape, np.nan))
    if not found_any:
        return None
    times = np.concatenate(times_parts)
    series = {name: np.concatenate(parts) for name, parts in series_parts.items()}
    return times, series


# ---------------------------------------------------------------- SWPC real time


def parse_rtsw(mag_rows: Sequence[Mapping[str, Any]], wind_rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """SWPC real-time solar wind as ``(times, series)``.

    The feed lists every spacecraft (ACE, DSCOVR, IMAP, SWFO-L1 …) side by side;
    only rows SWPC flags ``active`` — the one it is using operationally — are
    kept, so the series never jumps between instruments minute to minute.
    """
    def collect(rows, fields):
        out: dict[datetime, dict[str, float]] = {}
        for row in rows or ():
            if not isinstance(row, Mapping) or not row.get("active"):
                continue
            try:
                when = datetime.fromisoformat(str(row.get("time_tag")).replace("Z", "")).replace(second=0, microsecond=0)
            except ValueError:
                continue
            values = {}
            for source_key, name in fields.items():
                value = row.get(source_key)
                try:
                    values[name] = float(value) if value is not None else np.nan
                except (TypeError, ValueError):
                    values[name] = np.nan
            out[when] = values
        return out

    mag = collect(mag_rows, {"bt": "bt", "bz_gsm": "bz", "by_gsm": "by"})
    wind = collect(wind_rows, {"proton_speed": "v", "proton_density": "np", "proton_temperature": "t"})
    stamps = sorted(set(mag) | set(wind))
    times = np.array(stamps, dtype="datetime64[s]")
    series = {name: np.full(len(stamps), np.nan) for name in IMF_SERIES + PLASMA_SERIES}
    for index, when in enumerate(stamps):
        for source in (mag.get(when, {}), wind.get(when, {})):
            for name, value in source.items():
                series[name][index] = value
    return times, series


def load_rtsw(
    *,
    session: Any = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    client = _client(session)
    payloads = []
    for url in (SWPC_RTSW_MAG_URL, SWPC_RTSW_WIND_URL):
        _check_cancel(cancel_cb)
        try:
            response = client.get(url, timeout=_HTTP_TIMEOUT, headers={"User-Agent": _USER_AGENT})
            if int(getattr(response, "status_code", 200) or 200) >= 400:
                return None
            payloads.append(response.json())
        except Exception:
            return None
    times, series = parse_rtsw(payloads[0], payloads[1])
    return (times, series) if times.size else None


# ---------------------------------------------------------------------- merge


def _resample(times: np.ndarray, values: np.ndarray, axis: np.ndarray, *, step_s: int) -> np.ndarray:
    """Mean of ``values`` in each ``step_s`` bin starting at ``axis`` (NaN-aware)."""
    out = np.full(axis.shape, np.nan)
    if times.size == 0:
        return out
    start = axis[0]
    bins = ((times - start) / np.timedelta64(step_s, "s")).astype(np.int64)
    good = np.isfinite(values) & (bins >= 0) & (bins < axis.size)
    if not good.any():
        return out
    sums = np.bincount(bins[good], weights=values[good], minlength=axis.size)
    counts = np.bincount(bins[good], minlength=axis.size)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = sums / counts
    out[counts > 0] = mean[counts > 0]
    return out


def _last_valid_time(times: np.ndarray, values: np.ndarray) -> np.datetime64 | None:
    good = np.flatnonzero(np.isfinite(values))
    return times[good[-1]] if good.size else None


def stitch_l1_fallback(
    axis: np.ndarray,
    omni: Mapping[str, np.ndarray],
    l1_sources: Sequence[tuple[str, np.ndarray, Mapping[str, np.ndarray]]],
    *,
    step_s: int = 300,
) -> tuple[dict[str, np.ndarray], dict[str, list[Segment]]]:
    """Fill the unpublished end of OMNI's IMF and plasma with L1 data.

    Only time *after* OMNI's last valid sample of a series is filled: a gap
    inside the published record is a real data gap and stays one. Each L1
    source fills what the previous ones left empty, in the order given
    (archived DSCOVR before the real-time feed). Ey, β, Mach and Pdyn are then
    recomputed from the merged series where OMNI had none.
    """
    merged = {name: np.array(values, dtype=float, copy=True) for name, values in omni.items()}
    segments: dict[str, list[Segment]] = {}

    def to_dt(value: np.datetime64) -> datetime:
        return value.astype("datetime64[s]").astype(datetime)

    for name in IMF_SERIES + PLASMA_SERIES + DERIVED_SERIES + INDEX_SERIES:
        values = merged.setdefault(name, np.full(axis.shape, np.nan))
        last = _last_valid_time(axis, values)
        segments[name] = []
        if last is not None:
            first = axis[np.flatnonzero(np.isfinite(values))[0]]
            segments[name].append(Segment(to_dt(first), to_dt(last), SOURCE_OMNI))

    filled_any = np.zeros(axis.shape, dtype=bool)
    for name in IMF_SERIES + PLASMA_SERIES:
        values = merged[name]
        last = _last_valid_time(axis, values)
        open_from = 0 if last is None else int(np.searchsorted(axis, last, side="right"))
        for source, times, series in l1_sources:
            if open_from >= axis.size or name not in series:
                continue
            resampled = _resample(np.asarray(times), np.asarray(series[name], dtype=float), axis, step_s=step_s)
            window = np.zeros(axis.shape, dtype=bool)
            window[open_from:] = True
            take = window & ~np.isfinite(values) & np.isfinite(resampled)
            if not take.any():
                continue
            values[take] = resampled[take]
            filled_any |= take
            idx = np.flatnonzero(take)
            segments[name].append(Segment(to_dt(axis[idx[0]]), to_dt(axis[idx[-1]]), source, L1_NOTE))
        merged[name] = values

    if filled_any.any():
        derived = derive_parameters(merged)
        for name, values in derived.items():
            target = merged[name]
            take = filled_any & ~np.isfinite(target) & np.isfinite(values)
            if take.any():
                target[take] = values[take]
                idx = np.flatnonzero(take)
                source = next(
                    (seg.source for seg in reversed(segments.get("v", [])) if seg.note), SOURCE_DSCOVR
                )
                segments[name].append(
                    Segment(to_dt(axis[idx[0]]), to_dt(axis[idx[-1]]), source, L1_NOTE + "; computed with OMNI formulas")
                )
    return merged, segments


def load_solar_wind(
    start: datetime,
    end: datetime,
    *,
    cache_dir: str | os.PathLike[str],
    session: Any = None,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    step_s: int = 300,
    use_l1_fallback: bool = True,
    now: datetime | None = None,
) -> SolarWindData:
    """5-minute solar wind, IMF and SYM-H/AE for ``[start, end]`` (see the module docstring)."""
    start = _naive_utc(start)
    end = _naive_utc(end)
    if end <= start:
        raise ValueError("End time must be after start time.")
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    # Bins start on whole multiples of the step, as OMNI's own records do.
    epoch_s = int((start - datetime(1970, 1, 1)).total_seconds())
    axis_start = np.datetime64(epoch_s - epoch_s % int(step_s), "s")
    count = int((np.datetime64(end, "s") - axis_start) / np.timedelta64(step_s, "s")) + 1
    axis = axis_start + np.arange(count) * np.timedelta64(step_s, "s")

    omni = {name: np.full(axis.shape, np.nan) for name in SERIES_UNITS}
    months = _months(start, end)
    for index, (year, month) in enumerate(months):
        _check_cancel(cancel_cb)
        _report(progress_cb, int(70 * index / max(1, len(months))), f"OMNI {year:04d}-{month:02d}…")
        path = omni_month_path(year, month, cache_dir, session=session, cancel_cb=cancel_cb, now=now)
        if path is None:
            continue
        times, series = read_omni_cdf(path)
        for name, values in series.items():
            resampled = _resample(times, values, axis, step_s=step_s)
            take = np.isfinite(resampled)
            omni[name][take] = resampled[take]

    l1_sources: list[tuple[str, np.ndarray, Mapping[str, np.ndarray]]] = []
    last_omni = max(
        (t for t in (_last_valid_time(axis, omni[name]) for name in ("bz", "v")) if t is not None),
        default=None,
    )
    needs_l1 = last_omni is None or last_omni < axis[-1] - np.timedelta64(step_s, "s")
    if use_l1_fallback and needs_l1:
        from_time = start if last_omni is None else last_omni.astype("datetime64[s]").astype(datetime)
        _report(progress_cb, 75, "OMNI not yet published for the end of the range; reading L1 data…")
        try:
            dscovr = load_dscovr_range(from_time, end, cache_dir, session=session, progress_cb=progress_cb, cancel_cb=cancel_cb)
        except SolarWindCancelled:
            raise
        except Exception:
            dscovr = None
        if dscovr is not None:
            l1_sources.append((SOURCE_DSCOVR, dscovr[0], dscovr[1]))
        if end > now - timedelta(days=3):
            rtsw = load_rtsw(session=session, cancel_cb=cancel_cb)
            if rtsw is not None:
                l1_sources.append((SOURCE_RTSW, rtsw[0], rtsw[1]))

    merged, segments = stitch_l1_fallback(axis, omni, l1_sources, step_s=step_s)
    notes: list[str] = []
    for name, label in (("symh", "SYM-H"), ("ae", "AE")):
        last = _last_valid_time(axis, merged[name])
        if last is None:
            notes.append(f"{label} not yet published for this range.")
        elif last < axis[-1] - np.timedelta64(3600, "s"):
            notes.append(f"{label} not yet published after {last.astype(datetime):%Y-%m-%d %H:%M} UTC.")
    if any(seg.note for segs in segments.values() for seg in segs):
        notes.append(f"Solar wind after OMNI's last record is {L1_NOTE}.")
    _report(progress_cb, 100, "Solar wind ready.")
    return SolarWindData(times=axis, series=merged, segments=segments, notes=notes)
