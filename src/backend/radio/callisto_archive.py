"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Locations and HTTP session policy for the servers publishing e-CALLISTO FITS files.

Kept in the backend so the downloader UI, the on-disk cache and the timeline
service all share one set of addresses, one retry/timeout configuration and one
way of listing a UTC day, whichever server the user chose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
import re
from html import unescape
from urllib.parse import quote, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.backend.radio.callisto_naming import _FITS_SUFFIXES, parse_callisto_archive_filename
from src.version import APP_NAME, APP_VERSION

BASE_URL = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/"
REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 30
_REQUEST_RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
_HREF_RE = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*(?P<quote>['"]?)(?P<href>[^"' >]+)(?P=quote)""",
    re.IGNORECASE,
)
# What a filename from a JSON listing may contain before it becomes part of a
# URL and a cache path: no separators, drive colons or other path syntax.
_SAFE_BASENAME_RE = re.compile(r"[^\W_][\w .+-]*")

DEFAULT_SOURCE_ID = "ecallisto"
ASTRODONCEL_SOURCE_ID = "astrodoncel"


@dataclass(frozen=True)
class ArchiveSource:
    """A server publishing FITS files under ``<files_url>YYYY/MM/DD/``."""

    id: str
    label: str
    files_url: str
    #: JSON endpoint listing a day. Without one, the day directories are Apache
    #: indexes; with one, those indexes may be refused (Astrodoncel answers 403).
    listing_url: str = ""


ARCHIVE_SOURCES = (
    ArchiveSource(DEFAULT_SOURCE_ID, "e-CALLISTO", BASE_URL),
    ArchiveSource(
        ASTRODONCEL_SOURCE_ID,
        "Astrodoncel",
        "https://astrodoncel.uah.es/ecallistodata/",
        "https://astrodoncel.uah.es/dashboard/datos/getitems.php",
    ),
)
_SOURCES_BY_ID = {source.id: source for source in ARCHIVE_SOURCES}


@dataclass(frozen=True)
class ArchiveFile:
    """One FITS file a server lists for a UTC day.

    ``station``, ``observed_at_utc`` and ``focus_code`` are empty for a filename
    the archive naming rules cannot read (see ``list_day(include_unparsed=True)``).
    """

    source_id: str
    filename: str
    station: str
    observed_at_utc: datetime | None
    focus_code: str
    download_url: str
    size_bytes: int | None = None


class ArchiveListingError(RuntimeError):
    """A server answered a day listing with an error or an unreadable response."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def archive_source(source_id: str) -> ArchiveSource:
    """Return a source definition; an unknown id is a programming error."""
    try:
        return _SOURCES_BY_ID[source_id]
    except KeyError:
        raise ValueError(f"Unknown archive source: {source_id!r}") from None


def normalize_source_id(value) -> str:
    """Return a stored source id when it is still valid, else the default source."""
    return value if isinstance(value, str) and value in _SOURCES_BY_ID else DEFAULT_SOURCE_ID


def source_label(source_id: str) -> str:
    return archive_source(source_id).label


def source_id_for_url(url: str) -> str:
    """Return the source a file URL belongs to; any other URL keeps the default source."""
    text = str(url or "")
    for source in ARCHIVE_SOURCES:
        if source.id != DEFAULT_SOURCE_ID and text.startswith(source.files_url):
            return source.id
    return DEFAULT_SOURCE_ID


def day_url(observation_date, source_id: str = DEFAULT_SOURCE_ID) -> str:
    """Return the URL prefix of the files of one UTC day on a server."""
    base = archive_source(source_id).files_url
    return f"{base}{observation_date.year}/{observation_date.month:02}/{observation_date.day:02}/"


def extract_fits_links(html: str) -> list[str]:
    """Extract FITS file links from a simple directory listing page."""
    links: list[str] = []
    seen: set[str] = set()

    for match in _HREF_RE.finditer(str(html or "")):
        href = unescape(match.group("href")).strip()
        href = href.split("#", 1)[0].split("?", 1)[0]
        href_low = href.lower()
        if not href_low.endswith(_FITS_SUFFIXES):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)

    return links


def stations_in_listing(hrefs) -> list[str]:
    """Return the distinct stations named by archive FITS links, sorted case-insensitively.

    Spellings that differ only in case are one station; the first one listed wins.
    """
    stations: dict[str, str] = {}
    for href in hrefs:
        try:
            station, _observed_at, _focus_code = parse_callisto_archive_filename(href)
        except ValueError:
            continue
        stations.setdefault(station.casefold(), station)
    return sorted(stations.values(), key=str.casefold)


def _archive_file(source_id: str, filename: str, url: str, size_bytes=None) -> ArchiveFile:
    try:
        station, observed_at, focus_code = parse_callisto_archive_filename(filename)
    except ValueError:
        station, observed_at, focus_code = "", None, ""
    return ArchiveFile(source_id, filename, station, observed_at, focus_code, url, size_bytes)


def _list_directory_day(source: ArchiveSource, observation_date, session) -> list[ArchiveFile]:
    """Read an Apache day directory.

    The archive has no directory for a day nobody has published yet (including
    future days), which is an empty day rather than an error.
    """
    url = day_url(observation_date, source.id)
    with session.get(url, timeout=REQUEST_TIMEOUT) as page:
        status = int(getattr(page, "status_code", 200))
        if status == 404:
            return []
        if status >= 400:
            raise ArchiveListingError(f"HTTP {status} for {url}", status=status)
        hrefs = extract_fits_links(page.text)
    return [_archive_file(source.id, os.path.basename(href), urljoin(url, href)) for href in hrefs]


def _is_safe_basename(name: str) -> bool:
    return bool(_SAFE_BASENAME_RE.fullmatch(name)) and ".." not in name


def _list_json_day(source: ArchiveSource, observation_date, session) -> list[ArchiveFile]:
    """Read a JSON day listing, which answers ``[]`` for a day without files."""
    day = date(observation_date.year, observation_date.month, observation_date.day)
    where = f"{source.label} listing for {day:%Y-%m-%d}"
    params = {"path": day.strftime("%Y/%m/%d")}
    with session.get(source.listing_url, params=params, timeout=REQUEST_TIMEOUT) as response:
        status = int(getattr(response, "status_code", 200))
        if status >= 400:
            raise ArchiveListingError(f"HTTP {status} for the {where}", status=status)
        text = response.text

    # Served as text/html despite being JSON, so only the body can decide.
    try:
        records = json.loads(text)
    except ValueError as exc:
        raise ArchiveListingError(f"The {where} is not valid JSON.") from exc
    if not isinstance(records, list):
        raise ArchiveListingError(f"The {where} is not a list of files.")

    prefix = day_url(day, source.id)
    files: list[ArchiveFile] = []
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("name"), str)
            or not isinstance(record.get("isDirectory"), bool)
        ):
            raise ArchiveListingError(f"The {where} has an unexpected record.")
        name = record["name"]
        if record["isDirectory"] or not name.lower().endswith(_FITS_SUFFIXES):
            continue
        if not _is_safe_basename(name):
            continue
        size = record.get("size")
        size_bytes = size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None
        entry = _archive_file(source.id, name, prefix + quote(name, safe=""), size_bytes)
        if entry.observed_at_utc is None or entry.observed_at_utc.date() != day:
            continue
        files.append(entry)
    return files


def list_day(
    observation_date,
    source_id: str = DEFAULT_SOURCE_ID,
    session=None,
    *,
    include_unparsed: bool = False,
) -> list[ArchiveFile]:
    """List the FITS files a server publishes for one UTC day.

    Raises :class:`ArchiveListingError` when the server answers with an error or
    something other than a listing, so that is never mistaken for an empty day;
    connection failures propagate as ``requests`` exceptions. A directory listing
    can name files the naming rules cannot read; those are left out unless
    ``include_unparsed`` is set.
    """
    source = archive_source(source_id)
    owns_session = session is None
    if owns_session:
        session = build_archive_session()
    try:
        if source.listing_url:
            return _list_json_day(source, observation_date, session)
        files = _list_directory_day(source, observation_date, session)
    finally:
        if owns_session:
            session.close()
    if include_unparsed:
        return files
    return [entry for entry in files if entry.observed_at_utc is not None]


def fetch_day_stations(observation_date, session, source_id: str = DEFAULT_SOURCE_ID) -> list[str]:
    """Return the stations that published FITS files for one UTC day."""
    return stations_in_listing(entry.filename for entry in list_day(observation_date, source_id, session))


def build_archive_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=_REQUEST_RETRY_STATUS_CODES,
        allowed_methods=frozenset({"HEAD", "GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update({"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
