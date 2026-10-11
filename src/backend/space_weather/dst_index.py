"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import re
import time
from typing import Callable, Iterable

import numpy as np
import requests

BASE_URL = "https://wdc.kugi.kyoto-u.ac.jp"

ARCHIVE_FINAL = "Final"
ARCHIVE_PROVISIONAL = "Provisional"
ARCHIVE_REALTIME = "Real-time"

# Kyoto's archive split verified from the official index pages on 2026-03-31:
# final through 2020-12, provisional through 2025-06, real-time from 2025-07.
FINAL_LAST_MONTH = 202012
PROVISIONAL_LAST_MONTH = 202506

#: Kyoto's fill for an hour with no value (future hours of the current month).
FILL_VALUE_NT = 9999

#: How long a provisional or real-time month stays cached. Final months never
#: change; the others are revised as Kyoto updates them through the day.
LIVE_MONTH_TTL_S = 3 * 3600.0

_PRE_RE = re.compile(r"<pre[^>]*class=[\"']data[\"'][^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)
_DAY_ROW_RE = re.compile(r"^\s*(\d{1,2})\b")
_INT_RE = re.compile(r"[+-]?\d+")


class DstDataError(RuntimeError):
    """Raised when Kyoto Dst data cannot be downloaded or parsed."""


@dataclass(frozen=True)
class DstMonthData:
    year: int
    month: int
    source_label: str
    updated_at_utc: str | None
    timestamps: tuple[datetime, ...]
    values_nt: tuple[int, ...]


@dataclass(frozen=True)
class _ArchiveSpec:
    label: str
    segment: str


ARCHIVE_SPECS = {
    ARCHIVE_FINAL: _ArchiveSpec(label=ARCHIVE_FINAL, segment="dst_final"),
    ARCHIVE_PROVISIONAL: _ArchiveSpec(label=ARCHIVE_PROVISIONAL, segment="dst_provisional"),
    ARCHIVE_REALTIME: _ArchiveSpec(label=ARCHIVE_REALTIME, segment="dst_realtime"),
}

_MONTH_CACHE: dict[tuple[int, int], DstMonthData] = {}
#: When each cached month was fetched (time.monotonic()).
_MONTH_FETCHED_AT: dict[tuple[int, int], float] = {}


class DstCancelled(RuntimeError):
    """The caller cancelled a load."""


def _naive_utc(value: datetime) -> datetime:
    """Kyoto hours are naive UTC; accept aware datetimes by converting them."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def is_fill(value: float) -> bool:
    return abs(float(value)) >= FILL_VALUE_NT


def _cached_month(key: tuple[int, int]) -> DstMonthData | None:
    month = _MONTH_CACHE.get(key)
    if month is None:
        return None
    if month.source_label == ARCHIVE_FINAL:
        return month
    fetched = _MONTH_FETCHED_AT.get(key)
    if fetched is None or time.monotonic() - fetched > LIVE_MONTH_TTL_S:
        _MONTH_CACHE.pop(key, None)
        _MONTH_FETCHED_AT.pop(key, None)
        return None
    return month


def clear_month_cache() -> None:
    _MONTH_CACHE.clear()
    _MONTH_FETCHED_AT.clear()


def iter_year_months(start_dt: datetime, end_dt: datetime) -> Iterable[tuple[int, int]]:
    year = int(start_dt.year)
    month = int(start_dt.month)
    last = (int(end_dt.year), int(end_dt.month))
    while (year, month) <= last:
        yield year, month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def preferred_archives_for_month(year: int, month: int) -> tuple[_ArchiveSpec, ...]:
    yyyymm = int(year) * 100 + int(month)
    if yyyymm <= FINAL_LAST_MONTH:
        labels = (ARCHIVE_FINAL,)
    elif yyyymm <= PROVISIONAL_LAST_MONTH:
        labels = (ARCHIVE_PROVISIONAL, ARCHIVE_REALTIME)
    else:
        labels = (ARCHIVE_REALTIME, ARCHIVE_PROVISIONAL)
    return tuple(ARCHIVE_SPECS[label] for label in labels)


def build_month_page_url(year: int, month: int, archive: _ArchiveSpec) -> str:
    return f"{BASE_URL}/{archive.segment}/{year:04d}{month:02d}/index.html"


def _extract_pre_block(html: str) -> str:
    match = _PRE_RE.search(html or "")
    if not match:
        raise DstDataError("Could not locate the Dst data table on the Kyoto page.")
    return unescape(match.group(1))


def _extract_updated_at_utc(html: str) -> str | None:
    match = re.search(r"\[Updated at ([^\]]+UT)\]", html or "", flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def parse_dst_html_page(year: int, month: int, html: str, source_label: str) -> DstMonthData:
    pre_block = _extract_pre_block(html)

    timestamps: list[datetime] = []
    values: list[int] = []

    for raw_line in pre_block.splitlines():
        if not _DAY_ROW_RE.match(raw_line):
            continue
        numbers = [int(item) for item in _INT_RE.findall(raw_line)]
        if len(numbers) < 25:
            continue

        day = numbers[0]
        hourly_values = numbers[1:25]
        for hour, value in enumerate(hourly_values):
            timestamps.append(datetime(year, month, day, hour, 0, 0))
            values.append(value)

    if not timestamps:
        raise DstDataError(f"Kyoto page for {year:04d}-{month:02d} did not contain hourly Dst rows.")

    return DstMonthData(
        year=year,
        month=month,
        source_label=source_label,
        updated_at_utc=_extract_updated_at_utc(html),
        timestamps=tuple(timestamps),
        values_nt=tuple(values),
    )


def _http_get_text(url: str, session=None, timeout: int = 30) -> str:
    client = session or requests
    response = client.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def fetch_month_data(
    year: int,
    month: int,
    *,
    session=None,
    timeout: int = 30,
    use_cache: bool = True,
) -> DstMonthData:
    cache_key = (int(year), int(month))
    if use_cache:
        cached = _cached_month(cache_key)
        if cached is not None:
            return cached

    attempts: list[str] = []
    last_error: Exception | None = None
    for archive in preferred_archives_for_month(year, month):
        url = build_month_page_url(year, month, archive)
        attempts.append(url)
        try:
            html = _http_get_text(url, session=session, timeout=timeout)
            month_data = parse_dst_html_page(year, month, html, archive.label)
            if use_cache:
                _MONTH_CACHE[cache_key] = month_data
                _MONTH_FETCHED_AT[cache_key] = time.monotonic()
            return month_data
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Kyoto answers 403, not 404, for a real-time month that has since
            # moved to the provisional archive (seen for 2026-04 in Oct 2026).
            if status in (403, 404):
                last_error = exc
                continue
            raise DstDataError(f"Failed to download Kyoto Dst data from {url}: {exc}") from exc
        except Exception as exc:
            last_error = exc
            raise DstDataError(f"Failed to read Kyoto Dst data from {url}: {exc}") from exc

    attempted = "\n".join(attempts)
    raise DstDataError(
        f"Dst data for {year:04d}-{month:02d} are not available from the expected Kyoto archives.\n{attempted}"
    ) from last_error


def load_dst_samples(
    start_dt: datetime,
    end_dt: datetime,
    *,
    session=None,
    timeout: int = 30,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]]:
    """Hourly Dst in ``[start_dt, end_dt]`` with each sample's archive label.

    Returns ``(times, values_nt, sources)`` where ``times`` are naive UTC
    datetimes, fill hours are NaN, and ``sources[i]`` is the archive
    (Final / Provisional / Real-time) sample ``i`` came from. Aware datetimes
    are accepted and converted to UTC.
    """
    start_dt = _naive_utc(start_dt)
    end_dt = _naive_utc(end_dt)
    if end_dt < start_dt:
        raise ValueError("End time must be after start time.")

    months = list(iter_year_months(start_dt, end_dt))
    if progress_cb:
        progress_cb(0, "Preparing Kyoto Dst requests...")

    selected_times: list[datetime] = []
    selected_values: list[float] = []
    selected_sources: list[str] = []

    for idx, (year, month) in enumerate(months, start=1):
        if cancel_cb is not None and cancel_cb():
            raise DstCancelled("Dst load cancelled.")
        if progress_cb:
            progress_cb(
                int(100 * (idx - 1) / max(1, len(months))),
                f"Fetching Kyoto Dst for {year:04d}-{month:02d}...",
            )

        month_data = fetch_month_data(year, month, session=session, timeout=timeout)
        for timestamp, value in zip(month_data.timestamps, month_data.values_nt):
            if start_dt <= timestamp <= end_dt:
                selected_times.append(timestamp)
                selected_values.append(float("nan") if is_fill(value) else float(value))
                selected_sources.append(month_data.source_label)

    return (
        np.array(selected_times, dtype=object),
        np.array(selected_values, dtype=float),
        tuple(selected_sources),
    )


def load_dst_range(
    start_dt: datetime,
    end_dt: datetime,
    *,
    session=None,
    timeout: int = 30,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]]:
    """Hourly Dst in ``[start_dt, end_dt]``; fill hours are NaN.

    Returns ``(times, values_nt, archives)`` where ``archives`` lists the
    archives used, Final first.
    """
    times, values, sources = load_dst_samples(
        start_dt,
        end_dt,
        session=session,
        timeout=timeout,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
    if progress_cb:
        progress_cb(100, "Preparing Dst plot...")

    source_order = {
        ARCHIVE_FINAL: 0,
        ARCHIVE_PROVISIONAL: 1,
        ARCHIVE_REALTIME: 2,
    }
    unique_sources = tuple(sorted(set(sources), key=lambda item: source_order.get(item, 99)))
    return times, values, unique_sources
