"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Solar event catalogs for the event-context strip (src/backend/space_weather/event_catalogs.py).

Four catalogs, one record type (:class:`CatalogEvent`):

* **GOES flares** — the HEK ``FL`` events with ``FRM_Name == "SWPC"`` (the NOAA
  SWPC flare list), searched through sunpy;
* **CDAW CMEs** — the SOHO/LASCO CME catalog, one monthly HTML table per month
  (the parser the CME viewer uses lives here);
* **NASA DONKI** — CMEs, flares, interplanetary shocks (IPS), geomagnetic
  storms (GST) and SEP events from the CCMC web service, which needs no key;
* **Monstein burst list** — the e-CALLISTO radio-burst tables.

Results are cached on disk as JSON, one file per source and UTC day. A day
within the last 30 days is refetched after a day (catalogs are still being
filled in); older days never expire. Qt-free.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Sequence
from urllib.parse import urljoin

import requests

from src.version import APP_VERSION

# --------------------------------------------------------------------- record

KIND_FLARE = "flare"
KIND_CME = "cme"
KIND_IPS = "ips"
KIND_GST = "gst"
KIND_SEP = "sep"
KIND_BURST = "burst"

SOURCE_GOES = "flares"
SOURCE_CDAW = "cmes"
SOURCE_DONKI = "donki"
SOURCE_BURSTS = "bursts"
SOURCES = (SOURCE_GOES, SOURCE_CDAW, SOURCE_DONKI, SOURCE_BURSTS)
SOURCE_LABELS = {
    SOURCE_GOES: "Flares (GOES/SWPC)",
    SOURCE_CDAW: "CMEs (CDAW)",
    SOURCE_DONKI: "DONKI",
    SOURCE_BURSTS: "Bursts (e-CALLISTO)",
}
#: Lane titles on the strip.
LANE_TITLES = {SOURCE_GOES: "Flares", SOURCE_CDAW: "CMEs", SOURCE_DONKI: "DONKI", SOURCE_BURSTS: "Bursts"}

#: How far beyond the plot range events are looked for.
DEFAULT_MARGIN = timedelta(hours=6)

#: Days this recent are refetched after a day; older cached days never expire.
RECENT_DAYS = 30
RECENT_MAX_AGE_S = 24 * 3600.0


@dataclass(frozen=True)
class CatalogEvent:
    """One catalog entry; times are naive UTC."""

    kind: str
    source: str
    start: datetime
    peak: datetime | None = None
    end: datetime | None = None
    label: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    url: str = ""

    def span(self) -> tuple[datetime, datetime]:
        return self.start, self.end if self.end is not None and self.end > self.start else self.start

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("start", "peak", "end"):
            value = out.get(key)
            out[key] = value.isoformat() if isinstance(value, datetime) else None
        out["details"] = _jsonable(out.get("details") or {})
        return out

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "CatalogEvent | None":
        start = _parse_time(raw.get("start"))
        if start is None:
            return None
        return cls(
            kind=str(raw.get("kind") or ""),
            source=str(raw.get("source") or ""),
            start=start,
            peak=_parse_time(raw.get("peak")),
            end=_parse_time(raw.get("end")),
            label=str(raw.get("label") or ""),
            details=dict(raw.get("details") or {}),
            url=str(raw.get("url") or ""),
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif hasattr(value, "datetime"):  # astropy Time
        try:
            dt = value.datetime
        except Exception:
            return None
    else:
        text = str(value).strip()
        if not text or text.lower() in {"none", "nan", "null"}:
            return None
        text = text.replace("Z", "").replace("/", "-")
        if "T" not in text and " " in text:
            text = text.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# ----------------------------------------------------------------- filtering


def overlaps(event: CatalogEvent, start: datetime, end: datetime) -> bool:
    first, last = event.span()
    return first <= end and last >= start


def events_in_window(events: Iterable[CatalogEvent], start: datetime, end: datetime) -> list[CatalogEvent]:
    """Events overlapping ``[start, end]``, in time order."""
    return sorted((event for event in events if overlaps(event, start, end)), key=lambda event: event.start)


def nearby_counts(
    events: Iterable[CatalogEvent], start: datetime, end: datetime, *, margin: timedelta = DEFAULT_MARGIN
) -> tuple[int, int]:
    """How many events lie just outside ``[start, end]``: (before, after), within ``margin``."""
    before = after = 0
    for event in events:
        first, last = event.span()
        if last < start and last >= start - margin:
            before += 1
        elif first > end and first <= end + margin:
            after += 1
    return before, after


# ------------------------------------------------------------------- flares


def flare_class_color(goes_class: str) -> str:
    letter = str(goes_class or "").strip()[:1].upper()
    return {"X": "#d62728", "M": "#ff7f0e", "C": "#e6c229", "B": "#7f7f7f", "A": "#bbbbbb"}.get(letter, "#9467bd")


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError, ValueError):
        return getattr(row, key, default)


def parse_hek_flare_rows(rows: Iterable[Any]) -> list[CatalogEvent]:
    """HEK ``FL`` rows (sunpy table rows or plain dicts) as flare events."""
    out = []
    for row in rows:
        start = _parse_time(_row_get(row, "event_starttime"))
        if start is None:
            continue
        goes_class = str(_row_get(row, "fl_goescls", "") or "").strip()
        region = _row_get(row, "ar_noaanum", None)
        try:
            region = int(region) if region not in (None, "") and int(region) > 0 else None
        except (TypeError, ValueError):
            region = None
        label = goes_class or "flare"
        if region:
            label += f" (AR {region})"
        out.append(
            CatalogEvent(
                kind=KIND_FLARE,
                source=SOURCE_GOES,
                start=start,
                peak=_parse_time(_row_get(row, "event_peaktime")),
                end=_parse_time(_row_get(row, "event_endtime")),
                label=label,
                details={"class": goes_class, "noaa_ar": region, "frm": str(_row_get(row, "frm_name", "") or "")},
            )
        )
    return sorted(out, key=lambda event: event.start)


def fetch_goes_flares(
    start: datetime, end: datetime, *, hek_client: Any = None, attrs_module: Any = None, **_kwargs: Any
) -> list[CatalogEvent]:
    """SWPC flares from the HEK, through sunpy (imported here, in the worker)."""
    if hek_client is None or attrs_module is None:
        from sunpy.net import attrs as sunpy_attrs
        from sunpy.net.hek import HEKClient

        attrs_module = attrs_module or sunpy_attrs
        hek_client = hek_client or HEKClient()
    a = attrs_module
    rows = hek_client.search(
        a.Time(start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")),
        a.hek.EventType("FL"),
        a.hek.FRM.Name == "SWPC",
    )
    return parse_hek_flare_rows(rows)


# ------------------------------------------------------------------ CDAW CMEs

CDAW_ROOT = "https://cdaw.gsfc.nasa.gov"
BASE_URL = f"{CDAW_ROOT}/CME_list/UNIVERSAL_ver2/"
FETCH_TIMEOUT = (6, 25)
FETCH_MAX_ATTEMPTS = 3

#: The catalog's own logger stays silent; the CME viewer passes its file logger.
_CDAW_LOGGER = logging.getLogger("callisto.cme_catalog")
_CDAW_LOGGER.addHandler(logging.NullHandler())
_CDAW_LOGGER.propagate = False

STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_MONTH_UNPUBLISHED = "month_unpublished"
STATUS_FETCH_ERROR = "fetch_error"


@dataclass(frozen=True)
class CMECatalogRow:
    timestamp: datetime
    values: List[str]
    catalog_movie_url: str
    fallback_movie_url: str
    source_href: str = ""

    @property
    def preferred_movie_url(self) -> str:
        return self.catalog_movie_url or self.fallback_movie_url

    @property
    def interactive_movie_url(self) -> str:
        # make_javamovie provides running-difference + GOES X-ray synchronized view.
        return self.fallback_movie_url or self.catalog_movie_url


@dataclass(frozen=True)
class FetchOutcome:
    status: str
    rows: List[CMECatalogRow]
    message: str = ""
    month_url: str = ""
    http_status: int = 0


def build_month_catalog_url(year: str, month: str) -> str:
    y = str(year or "").strip()
    m = str(month or "").strip().zfill(2)
    return f"{BASE_URL}{y}_{m}/univ{y}_{m}.html"


def build_fallback_movie_url(start_dt: datetime) -> str:
    end_dt = start_dt + timedelta(hours=2)
    stime = start_dt.strftime("%Y%m%d_%H%M")
    etime = end_dt.strftime("%Y%m%d_%H%M")
    return (
        "https://cdaw.gsfc.nasa.gov/movie/make_javamovie.php"
        f"?stime={stime}&etime={etime}&img1=lasc2rdf"
    )


def resolve_catalog_movie_url(raw_href: str) -> str:
    href = str(raw_href or "").strip()
    if not href:
        return ""
    return urljoin(CDAW_ROOT, href)


def parse_catalog_rows(html: str, year: str, month: str, day: str | None = None) -> List[CMECatalogRow]:
    """Rows of a CDAW monthly table; only one day's when ``day`` is given."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(str(html or ""), "html.parser")
    all_rows = soup.find_all("tr")
    if not all_rows:
        return []

    start_index = 0
    for idx, row in enumerate(all_rows):
        if "First C2 Appearance" in row.get_text(" ", strip=True):
            start_index = idx + 1
            break

    target_date = None
    if day is not None and str(day).strip():
        target_date = f"{str(year).strip()}/{str(month).strip().zfill(2)}/{str(day).strip().zfill(2)}"
    parsed_rows: List[CMECatalogRow] = []

    for row in all_rows[start_index:]:
        cols = row.find_all("td")
        if len(cols) < 13:
            continue

        row_date = cols[0].get_text(strip=True)
        row_time = cols[1].get_text(strip=True)
        if target_date is not None and row_date != target_date:
            continue

        datetime_str = f"{row_date} {row_time}"
        try:
            timestamp = datetime.strptime(datetime_str, "%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue

        values = [
            datetime_str,
            cols[2].get_text(strip=True),
            cols[3].get_text(strip=True),
            cols[4].get_text(strip=True),
            cols[7].get_text(strip=True),
            cols[8].get_text(strip=True),
            cols[9].get_text(strip=True),
            cols[10].get_text(strip=True),
            cols[12].get_text(strip=True),
        ]

        href = ""
        anchor = cols[11].find("a") if cols[11] else None
        if anchor is not None and anchor.has_attr("href"):
            href = str(anchor["href"]).strip()

        parsed_rows.append(
            CMECatalogRow(
                timestamp=timestamp,
                values=values,
                catalog_movie_url=resolve_catalog_movie_url(href),
                fallback_movie_url=build_fallback_movie_url(timestamp),
                source_href=href,
            )
        )

    return parsed_rows


def fetch_catalog_outcome(
    year: str,
    month: str,
    day: str | None,
    timeout: tuple[int, int] = FETCH_TIMEOUT,
    max_attempts: int = FETCH_MAX_ATTEMPTS,
    *,
    logger: logging.Logger | None = None,
) -> FetchOutcome:
    """One month's CDAW table (one day of it when ``day`` is given), with its status."""
    logger = logger or _CDAW_LOGGER
    month_url = build_month_catalog_url(year, month)
    headers = {"User-Agent": f"e-Callisto-FITS-Analyzer/{APP_VERSION}"}
    period = f"{year}-{str(month).zfill(2)}" + (f"-{str(day).zfill(2)}" if day else "")

    logger.info("event=fetch_start year=%s month=%s day=%s month_url=%s", year, month, day, month_url)

    last_error = ""
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            response = requests.get(month_url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = str(exc)
            logger.warning(
                "event=fetch_retry reason=network_error attempt=%s max_attempts=%s error=%s",
                attempt, max_attempts, last_error,
            )
            if attempt < max_attempts:
                _time.sleep(0.35 * attempt)
                continue
            return FetchOutcome(
                status=STATUS_FETCH_ERROR,
                rows=[],
                message=f"Network error while loading CME catalog: {last_error}",
                month_url=month_url,
            )

        status_code = int(response.status_code)
        if status_code == 404:
            logger.info("event=fetch_month_unpublished year=%s month=%s month_url=%s", year, month, month_url)
            return FetchOutcome(
                status=STATUS_MONTH_UNPUBLISHED,
                rows=[],
                message=f"CME catalog for {year}-{str(month).zfill(2)} is not published yet.",
                month_url=month_url,
                http_status=status_code,
            )

        if status_code != 200:
            last_error = f"HTTP {status_code}"
            logger.warning(
                "event=fetch_non_200 status=%s attempt=%s max_attempts=%s", status_code, attempt, max_attempts
            )
            if status_code >= 500 and attempt < max_attempts:
                _time.sleep(0.35 * attempt)
                continue
            return FetchOutcome(
                status=STATUS_FETCH_ERROR,
                rows=[],
                message=f"CME catalog request failed with HTTP {status_code}.",
                month_url=month_url,
                http_status=status_code,
            )

        rows = parse_catalog_rows(response.text, year, month, day)
        if rows:
            logger.info("event=fetch_success rows=%s year=%s month=%s day=%s", len(rows), year, month, day)
            return FetchOutcome(status=STATUS_OK, rows=rows, month_url=month_url, http_status=status_code)

        logger.info("event=fetch_no_data year=%s month=%s day=%s month_url=%s", year, month, day, month_url)
        return FetchOutcome(
            status=STATUS_NO_DATA,
            rows=[],
            message=f"No CME data available for {period}.",
            month_url=month_url,
            http_status=status_code,
        )

    return FetchOutcome(
        status=STATUS_FETCH_ERROR,
        rows=[],
        message=f"Unable to load CME catalog ({last_error or 'unknown error'}).",
        month_url=month_url,
    )


def _number(text: Any) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(text or ""))
    return float(match.group(0)) if match else None


def cdaw_row_to_event(row: CMECatalogRow) -> CatalogEvent:
    """A CDAW row as a CME event at its first C2 appearance."""
    values = list(row.values) + [""] * 9
    central_pa, width, speed = values[1], values[2], values[3]
    width_text = str(width).strip()
    halo = width_text.lower().startswith("halo") or _number(width_text) == 360
    speed_value = _number(speed)
    width_value = 360.0 if halo else _number(width_text)
    label = "CME"
    if speed_value is not None:
        label += f" {speed_value:.0f} km/s"
    if width_value is not None:
        label += ", halo" if halo else f", {width_value:.0f}°"
    return CatalogEvent(
        kind=KIND_CME,
        source=SOURCE_CDAW,
        start=row.timestamp,
        label=label,
        details={
            "central_pa": central_pa,
            "width": width_text,
            "width_deg": width_value,
            "speed_kms": speed_value,
            "halo": bool(halo),
            "remarks": values[8],
        },
        url=row.preferred_movie_url,
    )


def _months(start: datetime, end: datetime) -> list[tuple[int, int]]:
    out = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def fetch_cdaw_cmes(start: datetime, end: datetime, *, cancel_cb: Callable[[], bool] | None = None, **_kwargs: Any) -> list[CatalogEvent]:
    """CDAW CMEs whose first C2 appearance is in ``[start, end]``."""
    events: list[CatalogEvent] = []
    errors = []
    for year, month in _months(start, end):
        if cancel_cb is not None and cancel_cb():
            raise CatalogCancelled("Catalog load cancelled.")
        outcome = fetch_catalog_outcome(str(year), f"{month:02d}", None, max_attempts=2)
        if outcome.status == STATUS_FETCH_ERROR:
            errors.append(outcome.message)
            continue
        events.extend(cdaw_row_to_event(row) for row in outcome.rows if start <= row.timestamp <= end)
    if errors and not events:
        raise CatalogError("; ".join(errors))
    return sorted(events, key=lambda event: event.start)


# ---------------------------------------------------------------------- DONKI

#: The CCMC web service. kauai.ccmc.gsfc.nasa.gov now redirects to a news page;
#: CCMC moved the same paths under ccmc.gsfc.nasa.gov (verified Oct 2026).
DONKI_BASE_URL = "https://ccmc.gsfc.nasa.gov/DONKI/WS/get/"
DONKI_KINDS = ("CME", "FLR", "IPS", "GST", "SEP")


def _linked(raw: Mapping[str, Any]) -> list[str]:
    return [str(item.get("activityID")) for item in raw.get("linkedEvents") or [] if isinstance(item, Mapping)]


def parse_donki(kind: str, payload: Any) -> list[CatalogEvent]:
    """DONKI JSON for one event type as events."""
    kind = str(kind).upper()
    out: list[CatalogEvent] = []
    for raw in payload if isinstance(payload, list) else []:
        if not isinstance(raw, Mapping):
            continue
        link = str(raw.get("link") or "")
        if kind == "CME":
            start = _parse_time(raw.get("startTime"))
            if start is None:
                continue
            analyses = [a for a in raw.get("cmeAnalyses") or [] if isinstance(a, Mapping)]
            best = next((a for a in analyses if a.get("isMostAccurate")), analyses[0] if analyses else {})
            speed = best.get("speed")
            half = best.get("halfAngle")
            label = "CME"
            if speed:
                label += f" {float(speed):.0f} km/s"
            if half:
                label += f", {2 * float(half):.0f}°"
            out.append(CatalogEvent(KIND_CME, SOURCE_DONKI, start, label=label, url=link, details={
                "id": raw.get("activityID"), "speed_kms": speed, "half_angle_deg": half,
                "source_location": raw.get("sourceLocation"), "linked": _linked(raw), "note": raw.get("note") or "",
            }))
        elif kind == "FLR":
            start = _parse_time(raw.get("beginTime"))
            if start is None:
                continue
            out.append(CatalogEvent(KIND_FLARE, SOURCE_DONKI, start, peak=_parse_time(raw.get("peakTime")),
                                    end=_parse_time(raw.get("endTime")), label=str(raw.get("classType") or "flare"),
                                    url=link, details={"id": raw.get("flrID"), "class": raw.get("classType"),
                                                       "source_location": raw.get("sourceLocation"),
                                                       "linked": _linked(raw)}))
        elif kind == "IPS":
            start = _parse_time(raw.get("eventTime"))
            if start is None:
                continue
            location = str(raw.get("location") or "")
            out.append(CatalogEvent(KIND_IPS, SOURCE_DONKI, start, label=f"Shock at {location or 'L1'}", url=link,
                                    details={"id": raw.get("activityID"), "location": location, "linked": _linked(raw)}))
        elif kind == "GST":
            start = _parse_time(raw.get("startTime"))
            if start is None:
                continue
            kps = [k for k in raw.get("allKpIndex") or [] if isinstance(k, Mapping)]
            kp_max = max((float(k.get("kpIndex") or 0) for k in kps), default=None)
            last = max((_parse_time(k.get("observedTime")) for k in kps if _parse_time(k.get("observedTime"))), default=None)
            label = "Storm" + (f" Kp {kp_max:.1f}" if kp_max is not None else "")
            out.append(CatalogEvent(KIND_GST, SOURCE_DONKI, start, end=last, label=label, url=link,
                                    details={"id": raw.get("gstID"), "kp_max": kp_max, "linked": _linked(raw)}))
        elif kind == "SEP":
            start = _parse_time(raw.get("eventTime"))
            if start is None:
                continue
            out.append(CatalogEvent(KIND_SEP, SOURCE_DONKI, start, label="SEP", url=link,
                                    details={"id": raw.get("sepID"), "linked": _linked(raw)}))
    return sorted(out, key=lambda event: event.start)


def fetch_donki(
    start: datetime,
    end: datetime,
    *,
    kinds: Sequence[str] = DONKI_KINDS,
    session: Any = None,
    cancel_cb: Callable[[], bool] | None = None,
    **_kwargs: Any,
) -> list[CatalogEvent]:
    client = session or requests
    headers = {"User-Agent": f"e-Callisto-FITS-Analyzer/{APP_VERSION}"}
    params = {"startDate": f"{start:%Y-%m-%d}", "endDate": f"{end:%Y-%m-%d}"}
    events: list[CatalogEvent] = []
    errors = []
    for kind in kinds:
        if cancel_cb is not None and cancel_cb():
            raise CatalogCancelled("Catalog load cancelled.")
        try:
            response = client.get(f"{DONKI_BASE_URL}{kind}", params=params, headers=headers, timeout=(8, 40))
            status = int(getattr(response, "status_code", 200) or 200)
            if status >= 400:
                errors.append(f"DONKI {kind}: HTTP {status}")
                continue
            text = getattr(response, "text", "")
            payload = response.json() if str(text).strip() else []
        except Exception as exc:
            errors.append(f"DONKI {kind}: {exc}")
            continue
        events.extend(parse_donki(kind, payload))
    if errors and not events:
        raise CatalogError("; ".join(errors))
    return sorted(events, key=lambda event: event.start)


def link_donki_events(events: Sequence[CatalogEvent]) -> dict[str, CatalogEvent]:
    """DONKI events by activity id, for following ``linked`` references."""
    out = {}
    for event in events:
        ident = event.details.get("id")
        if ident:
            out[str(ident)] = event
    return out


# --------------------------------------------------------------- burst list


def burst_events_to_catalog(events: Iterable[Any]) -> list[CatalogEvent]:
    out = []
    for event in events:
        start = getattr(event, "start_utc", None)
        if start is None:
            continue
        burst_type = str(getattr(event, "burst_type", "") or "---")
        stations = tuple(getattr(event, "stations", ()) or ())
        out.append(CatalogEvent(
            KIND_BURST, SOURCE_BURSTS, start, end=getattr(event, "end_utc", None),
            label=f"Type {burst_type}",
            details={"type": burst_type, "stations": list(stations),
                     "f_min_mhz": getattr(event, "frequency_min_mhz", None),
                     "f_max_mhz": getattr(event, "frequency_max_mhz", None)},
            url=str(getattr(event, "source_url", "") or ""),
        ))
    return sorted(out, key=lambda event: event.start)


def fetch_burst_list(start: datetime, end: datetime, *, cancel_cb: Callable[[], bool] | None = None, **_kwargs: Any) -> list[CatalogEvent]:
    from src.backend.radio.burst_list import BurstListCancelled, fetch_burst_events

    try:
        result = fetch_burst_events(start.date(), end.date(), cancelled=cancel_cb)
    except BurstListCancelled as exc:
        raise CatalogCancelled(str(exc)) from exc
    events = [event for event in result.events if event.start_utc <= end and (event.end_utc or event.start_utc) >= start]
    return burst_events_to_catalog(events)


# ----------------------------------------------------------------- caching


class CatalogError(RuntimeError):
    """A catalog could not be read."""


class CatalogCancelled(RuntimeError):
    """The caller cancelled."""


FETCHERS: dict[str, Callable[..., list[CatalogEvent]]] = {
    SOURCE_GOES: fetch_goes_flares,
    SOURCE_CDAW: fetch_cdaw_cmes,
    SOURCE_DONKI: fetch_donki,
    SOURCE_BURSTS: fetch_burst_list,
}


def _day_path(cache_dir: Path, source: str, day: date) -> Path:
    return Path(cache_dir) / "event_catalogs" / source / f"{day:%Y-%m-%d}.json"


def _day_is_fresh(path: Path, day: date, *, now: datetime) -> bool:
    if not path.is_file():
        return False
    if (now.date() - day).days > RECENT_DAYS:
        return True
    try:
        age = _time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age < RECENT_MAX_AGE_S


def _days(start: datetime, end: datetime) -> list[date]:
    out = []
    day = start.date()
    while day <= end.date():
        out.append(day)
        day += timedelta(days=1)
    return out


def load_catalog(
    source: str,
    start: datetime,
    end: datetime,
    *,
    cache_dir: Any,
    fetcher: Callable[..., list[CatalogEvent]] | None = None,
    now: datetime | None = None,
    session: Any = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> list[CatalogEvent]:
    """Events of one source overlapping ``[start, end]``, through the per-day cache.

    Missing or stale days are fetched as contiguous ranges, and each fetched
    day is written back, empty days included (an empty day is an answer too).
    """
    start, end = _naive_utc(start), _naive_utc(end)
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    fetcher = fetcher or FETCHERS[source]
    days = [day for day in _days(start, end) if day <= now.date()]
    events: list[CatalogEvent] = []
    missing: list[date] = []
    for day in days:
        path = _day_path(Path(cache_dir), source, day)
        if _day_is_fresh(path, day, now=now):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
                events.extend(e for e in (CatalogEvent.from_json(r) for r in rows) if e is not None)
                continue
            except (OSError, ValueError):
                pass
        missing.append(day)

    ranges: list[tuple[date, date]] = []
    for day in missing:
        if ranges and (day - ranges[-1][1]).days == 1:
            ranges[-1] = (ranges[-1][0], day)
        else:
            ranges.append((day, day))
    for first, last in ranges:
        if cancel_cb is not None and cancel_cb():
            raise CatalogCancelled("Catalog load cancelled.")
        range_start = datetime(first.year, first.month, first.day)
        range_end = datetime(last.year, last.month, last.day, 23, 59, 59)
        fetched = fetcher(range_start, range_end, session=session, cancel_cb=cancel_cb)
        by_day: dict[date, list[CatalogEvent]] = {}
        for event in fetched:
            by_day.setdefault(event.start.date(), []).append(event)
        day = first
        while day <= last:
            path = _day_path(Path(cache_dir), source, day)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps([e.to_json() for e in by_day.get(day, [])]), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                pass
            day += timedelta(days=1)
        events.extend(fetched)

    unique = {(e.kind, e.source, e.start, e.label): e for e in events}
    return events_in_window(unique.values(), start, end)


@dataclass
class EventContextResult:
    """Events per source for one plot range, with what failed."""

    start: datetime
    end: datetime
    events: dict[str, list[CatalogEvent]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "events": {source: [e.to_json() for e in events] for source, events in self.events.items()},
            "errors": dict(self.errors),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "EventContextResult | None":
        start, end = _parse_time(raw.get("start")), _parse_time(raw.get("end"))
        if start is None or end is None:
            return None
        events = {}
        for source, rows in dict(raw.get("events") or {}).items():
            events[str(source)] = [e for e in (CatalogEvent.from_json(r) for r in rows or []) if e is not None]
        return cls(start, end, events, {str(k): str(v) for k, v in dict(raw.get("errors") or {}).items()})


def load_event_context(
    start: datetime,
    end: datetime,
    *,
    sources: Iterable[str] = SOURCES,
    cache_dir: Any,
    margin: timedelta = DEFAULT_MARGIN,
    session: Any = None,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    fetchers: Mapping[str, Callable[..., list[CatalogEvent]]] | None = None,
    now: datetime | None = None,
) -> EventContextResult:
    """Every enabled catalog for ``[start − margin, end + margin]``; one failing doesn't stop the rest."""
    start, end = _naive_utc(start), _naive_utc(end)
    query_start, query_end = start - margin, end + margin
    result = EventContextResult(query_start, query_end)
    wanted = [source for source in SOURCES if source in set(sources)]
    for index, source in enumerate(wanted):
        if cancel_cb is not None and cancel_cb():
            raise CatalogCancelled("Catalog load cancelled.")
        if progress_cb is not None:
            try:
                progress_cb(int(100 * index / max(1, len(wanted))), f"Loading {SOURCE_LABELS[source]}…")
            except Exception:
                pass
        fetcher = (fetchers or {}).get(source)
        try:
            result.events[source] = load_catalog(
                source, query_start, query_end, cache_dir=cache_dir, fetcher=fetcher,
                now=now, session=session, cancel_cb=cancel_cb,
            )
        except CatalogCancelled:
            raise
        except Exception as exc:
            result.errors[source] = str(exc) or type(exc).__name__
    return result
