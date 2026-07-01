"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Near-real-time SOHO/LASCO quicklook previews via the Helioviewer API.

The calibrated LASCO FITS product on VSO/SDAC lags real time by many months (see
``find_latest_search`` in :mod:`src.Backend.sunpy_archive`). Helioviewer, by
contrast, ingests LASCO C2/C3 quicklook JP2 imagery within ~an hour, so it is
the right source for a "what does the corona look like right now" preview. These
are rendered browse images (PNG, with the standard LASCO colour table) — not
analysis-grade FITS — so they are shown in a dedicated preview dialog rather than
the FITS analysis canvas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from src.version import APP_VERSION

HELIOVIEWER_API_BASE = "https://api.helioviewer.org/v2/"

# Helioviewer sourceId values for the SOHO/LASCO coronagraph detectors.
LASCO_SOURCE_IDS: dict[str, int] = {"C2": 4, "C3": 5}

_HTTP_TIMEOUT = (6, 60)
_USER_AGENT = f"e-Callisto-FITS-Analyzer/{APP_VERSION}"


@dataclass(frozen=True)
class HelioviewerImageInfo:
    detector: str
    source_id: int
    date: datetime          # observation time of the closest image (UTC, naive)
    name: str
    scale: float            # native arcsec/pixel
    width: int
    height: int


@dataclass(frozen=True)
class HelioviewerPreview:
    info: HelioviewerImageInfo
    png_bytes: bytes
    image_scale: float      # arcsec/pixel used to render the preview
    size_px: int
    image_url: str          # direct takeScreenshot URL (opens the PNG in a browser)


def _normalize_detector(detector: str) -> str:
    det = str(detector or "").strip().upper()
    if det not in LASCO_SOURCE_IDS:
        raise ValueError(f"Unsupported LASCO detector '{detector}'. Expected one of {sorted(LASCO_SOURCE_IDS)}.")
    return det


def _parse_hv_date(text: str) -> datetime:
    """Parse the assorted date formats the Helioviewer API returns into a
    naive-UTC datetime."""
    raw = str(text or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    # Last resort: ISO 8601 with timezone.
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception as exc:
        raise ValueError(f"Unrecognised Helioviewer date '{raw}'.") from exc


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _session(session: requests.Session | None) -> requests.Session:
    if session is not None:
        return session
    sess = requests.Session()
    sess.headers.update({"User-Agent": _USER_AGENT})
    return sess


def latest_image_info(
    detector: str,
    *,
    date: datetime | None = None,
    api_base: str = HELIOVIEWER_API_BASE,
    timeout: tuple[int, int] | float = _HTTP_TIMEOUT,
    session: requests.Session | None = None,
) -> HelioviewerImageInfo:
    """Return metadata for the Helioviewer image nearest ``date`` (default: now).

    Uses the ``getClosestImage`` endpoint, which returns the actual observation
    time of the newest available frame — i.e. the near-real-time frontier.
    """
    det = _normalize_detector(detector)
    source_id = LASCO_SOURCE_IDS[det]
    when = date or datetime.now(timezone.utc)
    sess = _session(session)
    response = sess.get(
        f"{api_base}getClosestImage/",
        params={"date": _iso_z(when), "sourceId": source_id},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or "date" not in data:
        raise RuntimeError(f"Helioviewer returned no image for LASCO {det}.")
    return HelioviewerImageInfo(
        detector=det,
        source_id=source_id,
        date=_parse_hv_date(data["date"]),
        name=str(data.get("name") or f"LASCO {det}"),
        scale=float(data.get("scale") or 0.0),
        width=int(data.get("width") or 1024),
        height=int(data.get("height") or 1024),
    )


def build_screenshot_url(
    info: HelioviewerImageInfo,
    *,
    size_px: int = 512,
    api_base: str = HELIOVIEWER_API_BASE,
) -> tuple[str, float]:
    """Build a ``takeScreenshot`` URL that renders the full native FOV at
    ``size_px``. Returns ``(url, image_scale)``."""
    size = max(64, int(size_px))
    native_scale = float(info.scale) if info.scale and info.scale > 0 else 11.9
    native_width = int(info.width) if info.width else 1024
    # Scale so the whole native frame fits the requested pixel size.
    image_scale = native_scale * native_width / size
    params = {
        "date": _iso_z(info.date),
        "imageScale": f"{image_scale:.6f}",
        "layers": f"[{info.source_id},1,100]",
        "x0": 0,
        "y0": 0,
        "width": size,
        "height": size,
        "display": "true",
    }
    return f"{api_base}takeScreenshot/?{urlencode(params)}", image_scale


def fetch_preview(
    detector: str,
    *,
    size_px: int = 512,
    date: datetime | None = None,
    info: HelioviewerImageInfo | None = None,
    api_base: str = HELIOVIEWER_API_BASE,
    timeout: tuple[int, int] | float = _HTTP_TIMEOUT,
    session: requests.Session | None = None,
) -> HelioviewerPreview:
    """Fetch a near-real-time LASCO preview PNG for ``detector`` (C2/C3).

    Resolves the newest available frame (unless ``info`` is supplied), then
    renders the full coronagraph FOV as a PNG through ``takeScreenshot``.
    """
    det = _normalize_detector(detector)
    sess = _session(session)
    image_info = info or latest_image_info(
        det, date=date, api_base=api_base, timeout=timeout, session=sess
    )
    url, image_scale = build_screenshot_url(image_info, size_px=size_px, api_base=api_base)
    response = sess.get(url, timeout=timeout)
    response.raise_for_status()
    content = response.content or b""
    content_type = str(response.headers.get("Content-Type", "")).lower()
    if "image" not in content_type or not content.startswith(b"\x89PNG"):
        detail = response.text[:200] if "json" in content_type else content_type
        raise RuntimeError(f"Helioviewer did not return a preview image (got {detail or 'no data'}).")
    return HelioviewerPreview(
        info=image_info,
        png_bytes=content,
        image_scale=image_scale,
        size_px=int(size_px),
        image_url=url,
    )
