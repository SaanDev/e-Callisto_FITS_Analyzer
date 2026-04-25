"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import requests

GFZ_KP_JSON_URL = "https://kp.gfz.de/app/json/"
GFZ_SOURCE_LABEL = "GFZ Potsdam"
KP_INTERVAL = timedelta(hours=3)
_API_TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"


class KpDataError(RuntimeError):
    """Raised when GFZ Kp data cannot be downloaded or parsed."""


@dataclass(frozen=True)
class KpRangeData:
    interval_starts: tuple[datetime, ...]
    interval_ends: tuple[datetime, ...]
    kp_decimal: tuple[float, ...]
    kp_code: tuple[str, ...]
    status: tuple[str, ...]
    source_label: str = GFZ_SOURCE_LABEL

    @classmethod
    def empty(cls) -> "KpRangeData":
        return cls(
            interval_starts=(),
            interval_ends=(),
            kp_decimal=(),
            kp_code=(),
            status=(),
        )


def floor_to_kp_interval_start(dt: datetime) -> datetime:
    return dt.replace(hour=(int(dt.hour) // 3) * 3, minute=0, second=0, microsecond=0)


def overlapping_kp_interval_bounds(start_dt: datetime, end_dt: datetime) -> tuple[datetime, datetime]:
    if end_dt < start_dt:
        raise ValueError("End time must be after start time.")

    first = floor_to_kp_interval_start(start_dt)
    if end_dt == start_dt:
        last_ref = end_dt
    else:
        last_ref = end_dt - timedelta(microseconds=1)
    last = floor_to_kp_interval_start(last_ref)
    return first, last


def kp_decimal_to_code(value: float) -> str:
    thirds = int(round(float(value) * 3.0))
    if thirds < 0 or thirds > 27:
        raise ValueError(f"Kp value out of supported range 0..9: {value}")

    base, remainder = divmod(thirds, 3)
    if remainder == 0:
        return f"{base}o"
    if remainder == 1:
        return f"{base}+"
    return f"{base + 1}-"


def _parse_datetime(text: str) -> datetime:
    try:
        return datetime.strptime(str(text), _API_TIME_FMT)
    except Exception as exc:
        raise KpDataError(f"Invalid GFZ datetime value: {text!r}") from exc


def parse_kp_api_payload(payload: dict) -> KpRangeData:
    if not isinstance(payload, dict):
        raise KpDataError("GFZ Kp response is not a JSON object.")

    meta = payload.get("meta") or {}
    source_label = str(meta.get("source") or GFZ_SOURCE_LABEL).strip() or GFZ_SOURCE_LABEL
    datetimes = list(payload.get("datetime") or [])
    kp_values = list(payload.get("Kp") or [])
    statuses = list(payload.get("status") or [])
    if not datetimes and not kp_values and not statuses:
        return KpRangeData(
            interval_starts=(),
            interval_ends=(),
            kp_decimal=(),
            kp_code=(),
            status=(),
            source_label=source_label,
        )

    if len(datetimes) != len(kp_values):
        raise KpDataError("GFZ Kp response contains mismatched datetime and Kp arrays.")
    if statuses and len(statuses) != len(datetimes):
        raise KpDataError("GFZ Kp response contains mismatched status and datetime arrays.")
    if not statuses:
        statuses = [""] * len(datetimes)

    interval_starts: list[datetime] = []
    interval_ends: list[datetime] = []
    kp_decimal: list[float] = []
    kp_code: list[str] = []
    normalized_status: list[str] = []

    for start_text, kp_value, status in zip(datetimes, kp_values, statuses):
        start_dt = _parse_datetime(start_text)
        value = float(kp_value)
        interval_starts.append(start_dt)
        interval_ends.append(start_dt + KP_INTERVAL)
        kp_decimal.append(value)
        kp_code.append(kp_decimal_to_code(value))
        normalized_status.append(str(status or "").strip())

    return KpRangeData(
        interval_starts=tuple(interval_starts),
        interval_ends=tuple(interval_ends),
        kp_decimal=tuple(kp_decimal),
        kp_code=tuple(kp_code),
        status=tuple(normalized_status),
        source_label=source_label,
    )


def load_kp_range(
    start_dt: datetime,
    end_dt: datetime,
    *,
    session=None,
    timeout: int = 30,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
) -> KpRangeData:
    query_start, query_end = overlapping_kp_interval_bounds(start_dt, end_dt)
    if progress_cb:
        progress_cb(0, "Requesting GFZ Kp data...")

    params = {
        "start": query_start.strftime(_API_TIME_FMT),
        "end": query_end.strftime(_API_TIME_FMT),
        "index": "Kp",
    }

    client = session or requests
    try:
        response = client.get(GFZ_KP_JSON_URL, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        raise KpDataError(f"Failed to download GFZ Kp data: {exc}") from exc
    except ValueError as exc:
        raise KpDataError("GFZ Kp response was not valid JSON.") from exc
    except Exception as exc:
        raise KpDataError(f"Failed to request GFZ Kp data: {exc}") from exc

    if progress_cb:
        progress_cb(70, "Parsing GFZ Kp data...")

    result = parse_kp_api_payload(payload)
    if not result.interval_starts:
        if progress_cb:
            progress_cb(100, "No Kp data in selected range.")
        return result

    starts: list[datetime] = []
    ends: list[datetime] = []
    values: list[float] = []
    codes: list[str] = []
    statuses: list[str] = []
    for interval_start, interval_end, kp_value, kp_code, status in zip(
        result.interval_starts,
        result.interval_ends,
        result.kp_decimal,
        result.kp_code,
        result.status,
    ):
        if query_start <= interval_start <= query_end:
            starts.append(interval_start)
            ends.append(interval_end)
            values.append(float(kp_value))
            codes.append(str(kp_code))
            statuses.append(str(status))

    if progress_cb:
        progress_cb(100, "Preparing Kp plot...")

    return KpRangeData(
        interval_starts=tuple(starts),
        interval_ends=tuple(ends),
        kp_decimal=tuple(values),
        kp_code=tuple(codes),
        status=tuple(statuses),
    )
