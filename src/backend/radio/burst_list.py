"""Read the published Monstein/e-CALLISTO burst lists without Qt dependencies.

The archive contains monthly e-CALLISTO tables, older BLEN/SGD tables with
times in tenths of a minute, and a November 2014 CELESTINA detection list.
Reported values and qualifications are retained; this catalog is a discovery
aid and does not establish which FITS files are available for a station.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from typing import Callable, Iterable
from urllib.parse import unquote, urljoin, urlparse

import requests

from src.backend.radio.callisto_archive import REQUEST_TIMEOUT, build_archive_session

BURST_LIST_BASE_URL = (
    "https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/"
)
_MODERN_ROW = re.compile(
    r"^(?P<date>\d{8})\s+(?P<start>\d{1,2}:\d{2}(?::\d{2})?)"
    r"\s*[-–]\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<rest>.+)$"
)
_MONTHLY_NAME = re.compile(
    r"^(?:e-CALLISTO|SGD_BLEN)_(\d{4})_(\d{2})\.txt$", re.IGNORECASE
)
_RANGE_NAME = re.compile(r"^(\d{8})-(\d{8})_nhit(\d+)\.txt$", re.IGNORECASE)
_LEGACY_CLOCK = re.compile(r"^(\d{2})(\d{2})(?:\.(\d))?$")
_NUMBER = re.compile(r"^[<>]?(\d+(?:\.\d+)?)[Xx?]?$")


class BurstListError(RuntimeError):
    """The catalog could not be retrieved or was not a recognized table."""


class BurstListCancelled(RuntimeError):
    """The user cancelled retrieval."""


@dataclass(frozen=True)
class BurstEvent:
    """One catalog event; timestamps are naive UTC, as in the FITS downloader."""

    start_utc: datetime
    end_utc: datetime
    burst_type: str
    frequency_min_mhz: float | None
    frequency_max_mhz: float | None
    stations: tuple[str, ...]
    remarks: str
    source_url: str
    raw_line: str


@dataclass
class BurstListResult:
    events: list[BurstEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Includes complete headers, legends and notices, keyed by original URL.
    source_notes: dict[str, str] = field(default_factory=dict)


@dataclass
class _ParsedList:
    events: list[BurstEvent] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)
    notices: list[tuple[date, str]] = field(default_factory=list)
    recognized: bool = False


def _clock(day: date, value: str, *, legacy: bool = False) -> datetime:
    if legacy:
        match = _LEGACY_CLOCK.fullmatch(value)
        if not match:
            raise ValueError("Invalid SGD time")
        hour, minute = int(match[1]), int(match[2])
        second = int(match[3] or 0) * 6
    else:
        parts = [int(part) for part in value.split(":")]
        hour, minute = parts[:2]
        second = parts[2] if len(parts) == 3 else 0
    if hour == 24 and minute == second == 0:
        return datetime.combine(day + timedelta(days=1), time())
    return datetime.combine(day, time(hour, minute, second))


def _stations(value: str) -> tuple[tuple[str, ...], list[str]]:
    stations, notes = [], []
    for raw_station in re.split(r",|;", value):
        raw_station = raw_station.strip()
        station = raw_station.strip("()[]? ")
        if not station or station in {"---", "-"}:
            continue
        if any(mark in raw_station for mark in "()[]?"):
            notes.append(f"Station qualification: {raw_station}")
        if station.casefold() not in {item.casefold() for item in stations}:
            stations.append(station)
    return tuple(stations), notes


def _parse_modern(match, source_url: str, raw_line: str, untyped: bool) -> BurstEvent | None:
    day = datetime.strptime(match["date"], "%Y%m%d").date()
    start = _clock(day, match["start"])
    end = _clock(day, match["end"])
    if end < start:
        end += timedelta(days=1)
    rest = match["rest"].strip()
    first, _, tail = rest.partition("\t")
    if "\t" not in rest:
        fields = rest.split(None, 1)
        first, tail = fields[0], fields[1] if len(fields) == 2 else ""
    first = first.strip()
    if first.upper() == "REM":
        return None
    if untyped:
        burst_type, station_text = "---", rest
    else:
        burst_type, station_text = first, tail
    station_text, _, comment = station_text.partition("\t")
    station_text, separator, inline_comment = station_text.partition("#")
    stations, notes = _stations(station_text)
    if burst_type == "---":
        notes.append("No burst classification supplied by the catalog")
    if comment.strip():
        notes.append(comment.strip())
    if separator and inline_comment.strip():
        notes.append(inline_comment.strip())
    return BurstEvent(start, end, burst_type, None, None, stations,
                      "; ".join(notes), source_url, raw_line)


def _parse_legacy(line: str, source_url: str) -> BurstEvent | None:
    fields = line.split()
    day = datetime.strptime(fields.pop(0), "%y%m%d").date()
    # The first event of a day includes the observer's coverage, not its time.
    if len(fields) >= 2 and re.fullmatch(r"\d{4}", fields[0]) and re.fullmatch(r"\d{4}", fields[1]):
        fields = fields[2:]
    if not fields:
        raise ValueError("Missing SGD station")
    station, *fields = fields
    if not fields:  # An observation day without a reported event.
        return None
    if len(fields) < 5:
        raise ValueError("Incomplete SGD event")
    start = _clock(day, fields[0], legacy=True)
    end = _clock(day, fields[1], legacy=True)
    if end < start:
        end += timedelta(days=1)
    burst_type, *details = fields[2:]
    # Last two fields are frequency bounds; X qualifiers remain in remarks.
    low, high = _NUMBER.fullmatch(details[-2]), _NUMBER.fullmatch(details[-1])
    if low is None or high is None:
        raise ValueError("Invalid SGD frequency bounds")
    frequencies = sorted((float(low[1]), float(high[1])))
    notes = ["SGD/BLEN record; times reported in tenths of a minute"]
    if len(details) > 2:
        notes.append("SGD subtype/intensity: " + " ".join(details[:-2]))
    notes.append("Reported frequency bounds (MHz): " + " ".join(details[-2:]))
    return BurstEvent(start, end, burst_type, *frequencies, (station,),
                      "; ".join(notes), source_url, line)


def _parse_content(text: str, *, source_url: str) -> _ParsedList:
    parsed = _ParsedList()
    untyped = bool(_RANGE_NAME.fullmatch(urlparse(source_url).path.rsplit("/", 1)[-1]))
    seen: set[BurstEvent] = set()
    for number, raw_line in enumerate(str(text or "").splitlines(), 1):
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue
        if line.startswith("#") or line.lower().startswith("product:"):
            parsed.notes.append(raw_line)
            if "date" in line.lower() and "time" in line.lower() and "stations" in line.lower():
                parsed.recognized = True
                untyped = "type" not in line.lower()
            continue
        if re.match(r"^\d{8}\s+[#:-]+(?:\s|$)", line):
            parsed.recognized = True
            parsed.notes.append(raw_line)
            continue
        modern = _MODERN_ROW.fullmatch(line)
        legacy = re.match(r"^\d{6}\s", line)
        if modern or legacy:
            try:
                event = (_parse_modern(modern, source_url, raw_line, untyped)
                         if modern else _parse_legacy(raw_line, source_url))
                parsed.recognized = True
                if event is None:
                    parsed.notes.append(raw_line)
                    if modern:
                        day = datetime.strptime(modern["date"], "%Y%m%d").date()
                        parsed.notices.append((day, modern["rest"].strip()))
                elif event not in seen:
                    parsed.events.append(event)
                    seen.add(event)
            except (ValueError, IndexError, OverflowError):
                parsed.invalid.append(number)
                parsed.notes.append(raw_line)
        elif re.match(r"^\d{6,8}\s", line):
            parsed.invalid.append(number)
            parsed.notes.append(raw_line)
        else:
            parsed.notes.append(raw_line)
            if "no bursts" in line.casefold():
                parsed.recognized = True
    parsed.events.sort(key=lambda event: (event.start_utc, event.end_utc, event.burst_type))
    return parsed


def parse_burst_list(text: str, *, source_url: str) -> list[BurstEvent]:
    """Parse supported catalog tables, omitting notices and invalid records.

    No times, dates, station aliases or classifications are guessed to repair
    source typos. Unclassified events use ``---``; absent frequencies are None.
    Station punctuation is retained in remarks and the unchanged source line.
    """
    return _parse_content(text, source_url=source_url).events


def _matches_type(value: str, requested: str) -> bool:
    value, requested = value.upper(), requested.upper()
    if requested in {"UNCLASSIFIED", "UNKNOWN", "---"}:
        return value in {"---", "?", "???", "UNKNOWN"}
    if "/" in requested:
        return value == requested
    parts = re.split(r"[+,]", value)
    families = [part.split("/", 1)[0].strip("? ") for part in parts]
    if requested == "III":
        return any(family in {"III", "IIIG", "IIIGG"} for family in families)
    return requested in families


def filter_burst_events(
    events: Iterable[BurstEvent], *, burst_type: str = "", station: str = "",
    text: str = "", frequency_min_mhz: float | None = None,
    frequency_max_mhz: float | None = None,
) -> list[BurstEvent]:
    """Apply type family, station substring, text and frequency overlap filters.

    Frequency filters exclude records with unspecified frequency coverage.
    Roman numeral types are matched as families, so II cannot match III or IV.
    """
    if (frequency_min_mhz is not None and frequency_max_mhz is not None
            and frequency_min_mhz > frequency_max_mhz):
        raise ValueError("Minimum frequency must not exceed maximum frequency.")
    burst_type, station, text = burst_type.strip(), station.strip().casefold(), text.strip().casefold()
    result = []
    for event in events:
        if burst_type and not _matches_type(event.burst_type, burst_type):
            continue
        if station and not any(station in item.casefold() for item in event.stations):
            continue
        if text and text not in " ".join((event.raw_line, event.remarks, event.burst_type, *event.stations)).casefold():
            continue
        if frequency_min_mhz is not None:
            if event.frequency_max_mhz is None or event.frequency_max_mhz < frequency_min_mhz:
                continue
        if frequency_max_mhz is not None:
            if event.frequency_min_mhz is None or event.frequency_min_mhz > frequency_max_mhz:
                continue
        result.append(event)
    return result


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.hrefs.extend(value for name, value in attrs if name.lower() == "href" and value)


def _listing_links(body: str, directory: str) -> list[str]:
    parser = _Links()
    parser.feed(body)
    # Restrict directory navigation to children of the requested public source.
    links = []
    for href in parser.hrefs:
        url = urljoin(directory, href)
        if url.startswith(directory) and not urlparse(url).query and not urlparse(url).fragment:
            if url not in links:
                links.append(url)
    return links


def _source_period(url: str) -> tuple[date, date, int | None] | None:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    monthly = _MONTHLY_NAME.fullmatch(name)
    try:
        if monthly:
            year, month = int(monthly[1]), int(monthly[2])
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]), None
        ranged = _RANGE_NAME.fullmatch(name)
        if ranged:
            start, end = (datetime.strptime(ranged[index], "%Y%m%d").date() for index in (1, 2))
            return start, end, int(ranged[3])
    except ValueError:
        pass
    return None


def fetch_burst_events(
    start_date: date, end_date: date, *,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> BurstListResult:
    """Fetch published tables intersecting an inclusive range of UTC start dates.

    Missing years/months and HTTP 404/410 are reported in ``warnings``. Network
    failures and other HTTP errors raise ``BurstListError`` (never masquerading
    as an empty result). Cancellation raises ``BurstListCancelled``. Progress
    receives (completed files, total files, message); discovery uses total=0.
    """
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    if start_date > end_date:
        raise ValueError("Start date must not be after end date.")
    result = BurstListResult()

    def check_cancel():
        if cancelled and cancelled():
            raise BurstListCancelled("Burst list retrieval cancelled.")

    def report(current, total, message):
        check_cancel()
        if progress:
            progress(current, total, message)

    check_cancel()
    session = build_archive_session()

    def get(url, *, missing_ok=True):
        check_cancel()
        try:
            with session.get(url, timeout=REQUEST_TIMEOUT) as response:
                check_cancel()
                if missing_ok and response.status_code in (404, 410):
                    return None
                if response.status_code != 200:
                    raise BurstListError(f"Could not retrieve burst catalog (HTTP {response.status_code}): {url}")
                # Historical files/readme use Latin-1; newer files may use UTF-8.
                content = getattr(response, "content", None)
                if isinstance(content, bytes):
                    try:
                        return content.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        return content.decode("latin-1")
                return response.text
        except (requests.RequestException, OSError) as exc:
            raise BurstListError(f"Could not reach the burst catalog: {exc}") from exc

    try:
        report(0, 0, "Reading published burst catalog years…")
        root = get(BURST_LIST_BASE_URL, missing_ok=False)
        years = {}
        for url in _listing_links(root, BURST_LIST_BASE_URL):
            match = re.fullmatch(r"(\d{4})/", url[len(BURST_LIST_BASE_URL):])
            if match:
                years[int(match[1])] = url
        if not years:
            raise BurstListError("The burst catalog index contains no recognized year directories.")
        sources = []
        missing_years = []
        for year in range(start_date.year, end_date.year + 1):
            check_cancel()
            if year not in years:
                missing_years.append(year)
                continue
            report(0, 0, f"Reading {year} burst catalog index…")
            listing = get(years[year])
            if listing is None:
                missing_years.append(year)
                continue
            available = []
            for url in _listing_links(listing, years[year]):
                period = _source_period(url)
                if period and period[0] <= end_date and period[1] >= start_date:
                    available.append((url, period))
            # CELESTINA nhit3 overlaps nhit2, sometimes splitting an event.
            # Use the lowest coincidence threshold for each published period.
            thresholds = {}
            for _url, (first, last, threshold) in available:
                if threshold is not None:
                    thresholds[first, last] = min(thresholds.get((first, last), threshold), threshold)
            for url, period in available:
                if period[2] is None or period[2] == thresholds[period[:2]]:
                    sources.append((url, period))
            first_month = start_date.month if year == start_date.year else 1
            last_month = end_date.month if year == end_date.year else 12
            missing_months = []
            for month in range(first_month, last_month + 1):
                month_first, month_last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
                if not any(first <= month_last and last >= month_first for _url, (first, last, _threshold) in available):
                    missing_months.append(f"{month:02}")
            if missing_months:
                result.warnings.append(f"No published burst catalog for {year} month(s): {', '.join(missing_months)}.")
        if missing_years:
            result.warnings.append("No published burst catalog for year(s): " + ", ".join(map(str, missing_years)) + ".")
        if start_date.year <= 2019 and end_date.year >= 2012:
            result.warnings.append("The source reports no regular burst lists for 2012–2019; November 2014 has a separate unclassified CELESTINA detection list.")
        sources.sort(key=lambda entry: (entry[1][0], entry[0]))
        for index, (url, (period_start, period_end, threshold)) in enumerate(sources):
            filename = url.rsplit("/", 1)[-1]
            report(index, len(sources), f"Loading {filename}…")
            body = get(url)
            if body is None:
                result.warnings.append(f"Published catalog is unavailable: {url}")
                continue
            parsed = _parse_content(body, source_url=url)
            check_cancel()
            if not parsed.recognized:
                raise BurstListError(f"Unrecognized burst catalog format: {url}")
            result.source_notes[url] = "\n".join(parsed.notes)
            if parsed.invalid:
                result.warnings.append(f"{filename}: skipped {len(parsed.invalid)} invalid record(s), at line(s) " + ", ".join(map(str, parsed.invalid[:10])) + ".")
            outside = sum(not period_start <= event.start_utc.date() <= period_end for event in parsed.events)
            if outside:
                result.warnings.append(f"{filename}: {outside} record(s) have dates outside the file's stated period; source dates were preserved.")
            for day, notice in parsed.notices:
                if start_date <= day <= end_date:
                    result.warnings.append(f"{day.isoformat()} — {notice}")
            if threshold is not None:
                result.warnings.append(f"{filename}: unclassified CELESTINA detections with at least {threshold} coincident stations; overlapping higher-threshold lists are omitted.")
            result.events.extend(event for event in parsed.events if start_date <= event.start_utc.date() <= end_date)
            report(index + 1, len(sources), f"Loaded {filename}")
        result.events.sort(key=lambda event: (event.start_utc, event.end_utc, event.burst_type))
        check_cancel()
        return result
    finally:
        session.close()
