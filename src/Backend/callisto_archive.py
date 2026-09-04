"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

HTTP session policy for the e-CALLISTO archive at soleil.i4ds.ch.

Kept in the backend so both the downloader UI and the on-disk cache share one
retry/timeout configuration.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.version import APP_NAME, APP_VERSION

REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 30
_REQUEST_RETRY_STATUS_CODES = (429, 500, 502, 503, 504)


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
