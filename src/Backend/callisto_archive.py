"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Location and HTTP session policy for the e-CALLISTO archive at soleil.i4ds.ch.

Kept in the backend so the downloader UI, the on-disk cache and the timeline
service all share one address, one retry/timeout configuration and one way of
reading an Apache directory listing.
"""

from __future__ import annotations

import re
from html import unescape

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.Backend.callisto_naming import _FITS_SUFFIXES
from src.version import APP_NAME, APP_VERSION

BASE_URL = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/"
REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 30
_REQUEST_RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
_HREF_RE = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*(?P<quote>['"]?)(?P<href>[^"' >]+)(?P=quote)""",
    re.IGNORECASE,
)


def day_url(observation_date) -> str:
    """Return the archive directory URL holding one UTC day of observations."""
    return f"{BASE_URL}{observation_date.year}/{observation_date.month:02}/{observation_date.day:02}/"


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
