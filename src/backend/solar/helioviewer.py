"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Near-real-time SOHO quicklook previews (LASCO and EIT) via the Helioviewer API.

The calibrated LASCO FITS product on VSO/SDAC lags real time by many months (see
``find_latest_search`` in :mod:`src.backend.solar.sunpy_archive`). Helioviewer, by
contrast, ingests LASCO C2/C3 quicklook JP2 imagery within ~an hour, so it is
the right source for a "what does the corona look like right now" preview. These
are rendered browse images (PNG, with the standard LASCO colour table) — not
analysis-grade FITS — so they are shown in a dedicated preview dialog rather than
the FITS analysis canvas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlencode

import requests

from src.version import APP_VERSION

HELIOVIEWER_API_BASE = "https://api.helioviewer.org/v2/"

# Helioviewer sourceId values, keyed by (instrument, channel). LASCO's channel
# is a detector (C2/C3); EIT's is an EUV passband in angstrom, so the table is
# keyed on the pair rather than on a bare detector string.
SOURCE_IDS: dict[tuple[str, str], int] = {
    ("EIT", "171"): 0,
    ("EIT", "195"): 1,
    ("EIT", "284"): 2,
    ("EIT", "304"): 3,
    ("LASCO", "C2"): 4,
    ("LASCO", "C3"): 5,
}

# Retained for callers that only ever deal with LASCO detectors.
LASCO_SOURCE_IDS: dict[str, int] = {
    channel: source_id for (inst, channel), source_id in SOURCE_IDS.items() if inst == "LASCO"
}

DEFAULT_INSTRUMENT = "LASCO"

_HTTP_TIMEOUT = (6, 60)
_USER_AGENT = f"e-Callisto-FITS-Analyzer/{APP_VERSION}"


@dataclass(frozen=True)
class HelioviewerImageInfo:
    detector: str           # channel: LASCO detector (C2/C3) or EIT passband ("195")
    source_id: int
    date: datetime          # observation time of the closest image (UTC, naive)
    name: str
    scale: float            # native arcsec/pixel
    width: int
    height: int
    instrument: str = DEFAULT_INSTRUMENT


@dataclass(frozen=True)
class HelioviewerPreview:
    info: HelioviewerImageInfo
    png_bytes: bytes
    image_scale: float      # arcsec/pixel used to render the preview
    size_px: int
    image_url: str          # direct takeScreenshot URL (opens the PNG in a browser)


@dataclass(frozen=True)
class HelioviewerFrame:
    date: datetime          # actual observation time of the frame (UTC, naive)
    png_bytes: bytes
    image_scale: float
    image_url: str


def channels_for(instrument: str = DEFAULT_INSTRUMENT) -> tuple[str, ...]:
    """The channels this instrument offers, in table order (C2/C3, or 171..304)."""
    inst = str(instrument or "").strip().upper()
    return tuple(channel for (candidate, channel) in SOURCE_IDS if candidate == inst)


def display_name(instrument: str, channel: str) -> str:
    """Human label for a source, e.g. 'SOHO/LASCO C2' or 'SOHO/EIT 195'."""
    inst = str(instrument or "").strip().upper()
    return f"SOHO/{inst} {str(channel or '').strip()}".strip()


def _resolve_channel(instrument: str, channel: str) -> tuple[str, str, int]:
    """Normalise an (instrument, channel) pair and return it with its sourceId.

    EIT channels are passbands, so '195', 195 and 195.0 all resolve to '195'.
    """
    inst = str(instrument or DEFAULT_INSTRUMENT).strip().upper()
    chan = str(channel or "").strip().upper()
    if chan.endswith(".0"):  # a float wavelength arriving as "195.0"
        chan = chan[:-2]
    if (inst, chan) not in SOURCE_IDS:
        available = sorted(channels_for(inst))
        if not available:
            raise ValueError(
                f"Unsupported Helioviewer instrument '{instrument}'. "
                f"Expected one of {sorted({i for i, _ in SOURCE_IDS})}."
            )
        raise ValueError(f"Unsupported {inst} channel '{channel}'. Expected one of {available}.")
    return inst, chan, SOURCE_IDS[(inst, chan)]


def _normalize_detector(detector: str) -> str:
    """Backwards-compatible LASCO-only detector check."""
    return _resolve_channel(DEFAULT_INSTRUMENT, detector)[1]


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
    instrument: str = DEFAULT_INSTRUMENT,
    date: datetime | None = None,
    api_base: str = HELIOVIEWER_API_BASE,
    timeout: tuple[int, int] | float = _HTTP_TIMEOUT,
    session: requests.Session | None = None,
) -> HelioviewerImageInfo:
    """Return metadata for the Helioviewer image nearest ``date`` (default: now).

    Uses the ``getClosestImage`` endpoint, which returns the actual observation
    time of the newest available frame — i.e. the near-real-time frontier.
    """
    inst, det, source_id = _resolve_channel(instrument, detector)
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
        raise RuntimeError(f"Helioviewer returned no image for {display_name(inst, det)}.")
    return HelioviewerImageInfo(
        detector=det,
        source_id=source_id,
        date=_parse_hv_date(data["date"]),
        name=str(data.get("name") or f"{inst} {det}"),
        scale=float(data.get("scale") or 0.0),
        width=int(data.get("width") or 1024),
        height=int(data.get("height") or 1024),
        instrument=inst,
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
    instrument: str = DEFAULT_INSTRUMENT,
    size_px: int = 512,
    date: datetime | None = None,
    info: HelioviewerImageInfo | None = None,
    api_base: str = HELIOVIEWER_API_BASE,
    timeout: tuple[int, int] | float = _HTTP_TIMEOUT,
    session: requests.Session | None = None,
) -> HelioviewerPreview:
    """Fetch a near-real-time preview PNG for an ``(instrument, detector)`` source.

    ``detector`` is the channel: a LASCO detector (C2/C3) or an EIT passband
    ("171"/"195"/"284"/"304"). Resolves the newest available frame (unless
    ``info`` is supplied), then renders the full native FOV as a PNG through
    ``takeScreenshot``.
    """
    inst, det, _source_id = _resolve_channel(instrument, detector)
    sess = _session(session)
    image_info = info or latest_image_info(
        det, instrument=inst, date=date, api_base=api_base, timeout=timeout, session=sess
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


def build_frame_times(
    start: datetime,
    end: datetime,
    step_seconds: float,
    *,
    max_frames: int = 48,
) -> list[datetime]:
    """Target timestamps spanning ``[start, end]`` at ``step_seconds`` spacing.

    Honours the requested step while the frame count stays within
    ``max_frames``; if the range/step would exceed that cap, the frames are
    spread evenly across the full range instead (so the whole window is still
    covered). Timestamps are the *requested* times — the fetcher resolves each
    to the nearest actually-available frame and de-duplicates.
    """
    start = start.replace(tzinfo=None) if start.tzinfo else start
    end = end.replace(tzinfo=None) if end.tzinfo else end
    if end < start:
        start, end = end, start
    span = (end - start).total_seconds()
    if span <= 0:
        return [start]
    step = max(1.0, float(step_seconds))
    cap = max(1, int(max_frames))

    count = int(span // step) + 1
    if count <= cap:
        times = [start + timedelta(seconds=step * i) for i in range(count)]
        if (end - times[-1]).total_seconds() > step * 0.5:
            times.append(end)
        return times

    # Too many frames for the cap: spread `cap` points evenly across the range.
    return [start + timedelta(seconds=span * i / (cap - 1)) for i in range(cap)]


def fetch_frame_sequence(
    detector: str,
    start: datetime,
    end: datetime,
    *,
    instrument: str = DEFAULT_INSTRUMENT,
    step_seconds: float,
    size_px: int = 512,
    max_frames: int = 48,
    api_base: str = HELIOVIEWER_API_BASE,
    timeout: tuple[int, int] | float = _HTTP_TIMEOUT,
    session: requests.Session | None = None,
    progress_cb: Callable[[int, int, datetime], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> list[HelioviewerFrame]:
    """Fetch a de-duplicated frame sequence over ``[start, end]``.

    One Helioviewer frame is fetched per target timestamp (see
    :func:`build_frame_times`). Frames that resolve to the same actual
    observation time (when the step is finer than the native cadence) are
    dropped, and timestamps with no data are skipped, so the result is the set
    of distinct real frames covering the window, ordered in time.
    """
    inst, det, _source_id = _resolve_channel(instrument, detector)
    sess = _session(session)
    targets = build_frame_times(start, end, step_seconds, max_frames=max_frames)
    total = len(targets)

    frames: list[HelioviewerFrame] = []
    seen: set[datetime] = set()
    for index, target in enumerate(targets):
        if cancel_cb is not None and cancel_cb():
            break
        if progress_cb is not None:
            progress_cb(index, total, target)
        try:
            preview = fetch_preview(
                det, instrument=inst, date=target, size_px=size_px,
                api_base=api_base, timeout=timeout, session=sess,
            )
        except Exception:
            continue  # data gap or transient error: skip this timestamp
        actual = preview.info.date
        if actual in seen:
            continue
        seen.add(actual)
        frames.append(
            HelioviewerFrame(
                date=actual,
                png_bytes=preview.png_bytes,
                image_scale=preview.image_scale,
                image_url=preview.image_url,
            )
        )
    frames.sort(key=lambda frame: frame.date)
    if progress_cb is not None:
        progress_cb(total, total, end.replace(tzinfo=None) if end.tzinfo else end)
    return frames
