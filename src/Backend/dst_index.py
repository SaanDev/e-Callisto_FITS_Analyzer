"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import unescape
import re
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
    if use_cache and cache_key in _MONTH_CACHE:
        return _MONTH_CACHE[cache_key]

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
            return month_data
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
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


def load_dst_range(
    start_dt: datetime,
    end_dt: datetime,
    *,
    session=None,
    timeout: int = 30,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]]:
    if end_dt < start_dt:
        raise ValueError("End time must be after start time.")

    months = list(iter_year_months(start_dt, end_dt))
    if progress_cb:
        progress_cb(0, "Preparing Kyoto Dst requests...")

    selected_times: list[datetime] = []
    selected_values: list[float] = []
    selected_sources: list[str] = []

    for idx, (year, month) in enumerate(months, start=1):
        if progress_cb:
            progress_cb(
                int(100 * (idx - 1) / max(1, len(months))),
                f"Fetching Kyoto Dst for {year:04d}-{month:02d}...",
            )

        month_data = fetch_month_data(year, month, session=session, timeout=timeout)
        for timestamp, value in zip(month_data.timestamps, month_data.values_nt):
            if start_dt <= timestamp <= end_dt:
                selected_times.append(timestamp)
                selected_values.append(float(value))
                selected_sources.append(month_data.source_label)

    if progress_cb:
        progress_cb(100, "Preparing Dst plot...")

    source_order = {
        ARCHIVE_FINAL: 0,
        ARCHIVE_PROVISIONAL: 1,
        ARCHIVE_REALTIME: 2,
    }
    unique_sources = tuple(sorted(set(selected_sources), key=lambda item: source_order.get(item, 99)))

    return (
        np.array(selected_times, dtype=object),
        np.array(selected_values, dtype=float),
        unique_sources,
    )

