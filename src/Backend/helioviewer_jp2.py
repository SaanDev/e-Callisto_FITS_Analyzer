"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Helioviewer JPEG2000 coronagraph frames for GCS fitting (src/Backend/helioviewer_jp2.py).

GCS fitting needs the *shape* of a CME front seen from two or three places at
once, plus exactly where each image was taken from. It does not need calibrated
photometry. Helioviewer's JPEG2000 archive fits that almost perfectly: each JP2
is a few hundred kilobytes rather than the several megabytes of a FITS file, the
archive answers in about a second, and every JP2 embeds the original FITS
header — observer position included — in an XML box.

Finding frames
--------------
The obvious approach, one ``getClosestImage`` lookup per target time, is slow
and wasteful: each lookup takes ~1.3 s, bursts of parallel lookups make the
server drop connections, and because the targets are guesses most of them
resolve to a frame already found. ``getJPX`` with ``verbose``, ``linked`` and
``jpip`` set instead returns *every* frame time in a window in one call without
building a movie — measured at 0.7-1.6 s for a two-hour window and a few seconds
for a whole day. Downloads then ask ``getJP2Image`` for those exact times, a few
at a time, with retries, into an on-disk cache.

Traps in the data, each verified against real files
---------------------------------------------------
* **Pixel order.** JP2 rows run top-down; FITS rows run bottom-up. Pixels are
  flipped on decode, as ``sunpy.io._jp2.read`` does.
* **LASCO roll.** Helioviewer has already rotated LASCO JP2s to solar north up,
  but leaves the original ``CROTA`` (about -174 deg since 2003) in the header.
  Honouring it turns LASCO upside down under the wireframe. sunpy's
  ``LASCOMap.rotation_matrix`` ignores CROTA for exactly this reason, keyed on
  the ``helioviewer`` XML section being present, so the header is parsed the way
  sunpy parses it and that key survives.
* **LASCO has no observer.** Its quicklook header carries no HGLN/HGLT/DSUN at
  all, and ``RSUN = 0``. SOHO sits at L1, so its observer is placed on the
  Sun-Earth line 1.5e6 km sunward of Earth. The halo orbit moves it by at most
  ~0.25 deg of longitude, which is invisible at GCS fitting precision.
* **Mixed resolution in one sequence.** Consecutive COR2 JP2s alternate between
  2048 and 1024 pixels. Dropping the odd ones out can leave too few frames to
  difference, so sequences are resampled onto one grid instead.
* **Pointing jitter.** The Sun centre moves by up to half a pixel between frames.
  A difference taken without co-registering them turns every streamer edge into
  a bright/dark pair, so frames are shifted onto the first frame's grid.
* **Byte scaling.** JP2 pixels are 8-bit display values, not counts, so dividing
  by exposure time — right for FITS — manufactures differences between frames
  whose exposures differ. Differences here are taken on the bytes directly, after
  removing the median offset that can separate a 1024-pixel frame from a
  2048-pixel one.

No Qt, so everything above is unit-testable without a display.
"""

from __future__ import annotations

import io
import re
import struct
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from src.version import APP_VERSION

HELIOVIEWER_API_BASE = "https://api.helioviewer.org/v2/"

#: The JPEG2000 signature box every valid JP2 file starts with.
JP2_SIGNATURE = bytes.fromhex("0000000c6a5020200d0a870a")

#: Frames larger than this are block-averaged down. A GCS front is tens of pixels
#: across, and three 2048-pixel sequences would hold over a gigabyte in memory.
MAX_GRID_PX = 1024

#: SOHO's distance sunward of Earth at L1, metres.
L1_OFFSET_M = 1.5e9

#: IAU nominal solar radius, metres. Setting it explicitly stops sunpy logging a
#: "missing solar radius" notice for every frame.
RSUN_REF_M = 695_700_000.0

_USER_AGENT = f"e-Callisto-FITS-Analyzer/{APP_VERSION}"
_HTTP_TIMEOUT = (8, 120)
_RETRY_DELAYS_S = (1.0, 2.0, 4.0, 8.0)

#: Helioviewer drops connections when hit with many requests at once, so every
#: request from every viewpoint panel shares this limit.
_REQUEST_SLOTS = threading.BoundedSemaphore(3)


class HelioviewerJP2Error(RuntimeError):
    """A JP2 search or download could not be completed."""


class HelioviewerJP2Cancelled(HelioviewerJP2Error):
    """The caller cancelled the operation."""


# --- Sources ----------------------------------------------------------------


@dataclass(frozen=True)
class JP2Source:
    """One Helioviewer coronagraph data source usable for GCS fitting."""

    #: Short key, also the combo-box label ("COR2-A").
    key: str
    #: Longer human label ("STEREO-A COR2").
    label: str
    #: Helioviewer ``sourceId``.
    source_id: int
    #: FITS INSTRUME / DETECTOR, matching ``INSTRUMENT_FOV_RSUN`` keys.
    instrument: str
    detector: str
    #: Vantage point, for the separation readout.
    spacecraft: str
    #: First and last day the archive holds data; ``None`` means still running.
    first_day: date
    last_day: date | None = None

    def available_on(self, when: datetime | date) -> bool:
        day = when.date() if isinstance(when, datetime) else when
        return day >= self.first_day and (self.last_day is None or day <= self.last_day)


#: Coronagraphs only: GCS is fitted to white-light CME fronts. GOES CCOR-1 is
#: deliberately absent — its JP2s use a zenithal-polynomial projection in degrees
#: and carry no observer position, neither of which the fast projector handles.
GCS_JP2_SOURCES: tuple[JP2Source, ...] = (
    JP2Source("COR2-A", "STEREO-A COR2", 29, "SECCHI", "COR2", "STEREO-A", date(2006, 12, 29)),
    JP2Source("COR1-A", "STEREO-A COR1", 28, "SECCHI", "COR1", "STEREO-A", date(2006, 12, 4)),
    JP2Source("LASCO C2", "SOHO LASCO C2", 4, "LASCO", "C2", "SOHO", date(1997, 2, 21)),
    JP2Source("LASCO C3", "SOHO LASCO C3", 5, "LASCO", "C3", "SOHO", date(1997, 2, 20)),
    JP2Source("COR2-B", "STEREO-B COR2", 31, "SECCHI", "COR2", "STEREO-B", date(2007, 1, 10), date(2014, 9, 27)),
    JP2Source("COR1-B", "STEREO-B COR1", 30, "SECCHI", "COR1", "STEREO-B", date(2006, 12, 12), date(2014, 9, 27)),
)

_SOURCES_BY_KEY = {source.key: source for source in GCS_JP2_SOURCES}


def source_by_key(key: str) -> JP2Source | None:
    return _SOURCES_BY_KEY.get(str(key or "").strip())


def default_viewpoints(when: datetime | date) -> tuple[JP2Source, JP2Source, JP2Source]:
    """The classic STEREO-A / SOHO / STEREO-B triad, adapted to what existed then.

    Before 2014-09-27 that is COR2-A, LASCO C2 and COR2-B. Afterwards STEREO-B is
    gone, so the third panel falls back to LASCO C3 — a different field of view
    from the same vantage, which still helps with the height but, honestly, adds
    no new direction.
    """
    first = _SOURCES_BY_KEY["COR2-A"]
    second = _SOURCES_BY_KEY["LASCO C2"]
    third = _SOURCES_BY_KEY["COR2-B"]
    if not third.available_on(when):
        third = _SOURCES_BY_KEY["LASCO C3"]
    return first, second, third


# --- HTTP ---------------------------------------------------------------------


def _session(session: Any = None) -> Any:
    if session is not None:
        return session
    import requests

    new = requests.Session()
    new.headers.update({"User-Agent": _USER_AGENT})
    return new


def _cancelled(cancel_cb: Callable[[], bool] | None) -> bool:
    try:
        return bool(cancel_cb and cancel_cb())
    except Exception:
        return False


def _get(
    session: Any,
    endpoint: str,
    params: dict[str, Any],
    *,
    api_base: str = HELIOVIEWER_API_BASE,
    timeout: Any = _HTTP_TIMEOUT,
    cancel_cb: Callable[[], bool] | None = None,
    retry_delays: Sequence[float] = _RETRY_DELAYS_S,
) -> Any:
    """GET with a shared concurrency limit and backoff on dropped connections.

    Retries only what is worth retrying — connection resets, timeouts, 429 and
    5xx. A 4xx is a bad request and fails at once.
    """
    import requests

    last_error: Exception | None = None
    for attempt in range(len(retry_delays) + 1):
        if _cancelled(cancel_cb):
            raise HelioviewerJP2Cancelled("Cancelled.")
        with _REQUEST_SLOTS:
            try:
                response = session.get(f"{api_base}{endpoint}", params=params, timeout=timeout)
                status = int(getattr(response, "status_code", 200) or 200)
                if status == 429 or status >= 500:
                    last_error = HelioviewerJP2Error(f"Helioviewer returned HTTP {status}.")
                else:
                    response.raise_for_status()
                    return response
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
            except requests.HTTPError as exc:
                raise HelioviewerJP2Error(f"Helioviewer rejected the request: {exc}") from exc
        if attempt < len(retry_delays):
            deadline = time.monotonic() + float(retry_delays[attempt])
            while time.monotonic() < deadline:
                if _cancelled(cancel_cb):
                    raise HelioviewerJP2Cancelled("Cancelled.")
                time.sleep(0.1)
    raise HelioviewerJP2Error(
        f"Helioviewer did not respond after {len(retry_delays) + 1} attempts ({last_error})."
    ) from last_error


def _iso_z(when: datetime) -> str:
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Search -------------------------------------------------------------------


def list_frame_times(
    source: JP2Source,
    start: datetime,
    end: datetime,
    *,
    session: Any = None,
    api_base: str = HELIOVIEWER_API_BASE,
    cancel_cb: Callable[[], bool] | None = None,
) -> list[datetime]:
    """Every frame time Helioviewer holds for ``source`` in ``[start, end]``.

    One ``getJPX`` call. ``linked`` and ``jpip`` make the server return a JPIP
    link to a movie of pointers instead of assembling the movie itself, and
    ``verbose`` adds the per-frame UNIX timestamps, which are all this needs.
    """
    if end < start:
        start, end = end, start
    response = _get(
        _session(session),
        "getJPX/",
        {
            "startTime": _iso_z(start),
            "endTime": _iso_z(end),
            "sourceId": source.source_id,
            "verbose": "true",
            "linked": "true",
            "jpip": "true",
        },
        api_base=api_base,
        cancel_cb=cancel_cb,
    )
    try:
        payload = response.json()
    except Exception as exc:
        raise HelioviewerJP2Error("Helioviewer returned an unreadable frame list.") from exc
    frames = payload.get("frames") if isinstance(payload, dict) else None
    times: list[datetime] = []
    for stamp in frames or []:
        try:
            times.append(datetime.fromtimestamp(int(stamp), timezone.utc).replace(tzinfo=None))
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return sorted(set(times))


#: Longest range one channel may request. getJPX lists a day in a few seconds;
#: much beyond this the listing gets slow and the frames far outnumber any
#: sensible cap, so the user is asked to narrow the range instead.
MAX_RANGE = timedelta(days=3)

#: Default cap on frames per channel for a range fetch.
DEFAULT_MAX_FRAMES = 60


def validate_range(start: datetime, end: datetime) -> str | None:
    """Why ``[start, end]`` cannot be fetched, or ``None`` when it can."""
    if end <= start:
        return "The end of the range must be after its start."
    if end - start > MAX_RANGE:
        days = MAX_RANGE.days
        return f"The range is longer than {days} days — narrow it to the event."
    return None


def pick_frames_evenly(times: Sequence[datetime], max_frames: int) -> list[datetime]:
    """At most ``max_frames`` of ``times``, spread evenly and keeping both ends.

    A range normally yields fewer frames than the cap and they are all kept, which
    is what a running difference wants: consecutive native frames. Only a range
    that outnumbers the cap is thinned, and then evenly, so the event is still
    covered end to end rather than truncated at the cap.
    """
    ordered = sorted(times)
    cap = max(2, int(max_frames))
    if len(ordered) <= cap:
        return ordered
    positions = np.unique(np.round(np.linspace(0, len(ordered) - 1, cap)).astype(int))
    return [ordered[int(index)] for index in positions]


def pick_frames_near(times: Sequence[datetime], target: datetime, count: int) -> list[datetime]:
    """The ``count`` frames closest to ``target``, returned in time order.

    Taking the nearest rather than spreading picks across the window keeps them
    *consecutive*, which is what a running difference needs, and centres them on
    the moment the user asked about.
    """
    wanted = max(1, int(count))
    nearest = sorted(times, key=lambda stamp: abs((stamp - target).total_seconds()))[:wanted]
    return sorted(nearest)


# --- Download -----------------------------------------------------------------


def cache_path(cache_dir: Path, source: JP2Source, when: datetime) -> Path:
    return Path(cache_dir) / f"hv{source.source_id}_{when:%Y%m%dT%H%M%S}.jp2"


def download_jp2(
    source: JP2Source,
    when: datetime,
    *,
    cache_dir: Path | None = None,
    session: Any = None,
    api_base: str = HELIOVIEWER_API_BASE,
    cancel_cb: Callable[[], bool] | None = None,
) -> bytes:
    """The JP2 for one exact frame time, from cache when it is already there."""
    path = cache_path(cache_dir, source, when) if cache_dir is not None else None
    if path is not None and path.is_file():
        cached = path.read_bytes()
        if cached.startswith(JP2_SIGNATURE):
            return cached
    response = _get(
        _session(session),
        "getJP2Image/",
        {"date": _iso_z(when), "sourceId": source.source_id},
        api_base=api_base,
        cancel_cb=cancel_cb,
    )
    content = bytes(getattr(response, "content", b"") or b"")
    if not content.startswith(JP2_SIGNATURE):
        raise HelioviewerJP2Error(
            f"Helioviewer did not return a JPEG2000 image for {source.label} at {when:%H:%M}."
        )
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".part")
            partial.write_bytes(content)
            partial.replace(path)
        except OSError:
            pass  # a cache that cannot be written is not a reason to fail the fetch
    return content


# --- Decoding -----------------------------------------------------------------


def jp2_xml_text(raw: bytes) -> str | None:
    """The embedded FITS/Helioviewer XML, found by walking the JP2 box structure."""
    position = 0
    size = len(raw)
    while position + 8 <= size:
        length, kind = struct.unpack(">I4s", raw[position : position + 8])
        header = 8
        if length == 1:
            if position + 16 > size:
                return None
            length = struct.unpack(">Q", raw[position + 8 : position + 16])[0]
            header = 16
        elif length == 0:
            length = size - position
        if length < header:
            return None
        if kind == b"xml ":
            return raw[position + header : position + length].decode("utf-8", "replace")
        position += length
    return None


def _typed(text: str) -> Any:
    value = str(text).strip()
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    return value


def parse_jp2_header(xml_text: str) -> dict[str, Any]:
    """FITS keywords from the XML box, lower-cased, typed, as sunpy would read them.

    Every element becomes a key — the ``meta``, ``fits`` and ``helioviewer``
    containers included — because that is what ``sunpy.io._jp2`` does, and
    ``LASCOMap`` decides whether to ignore CROTA by testing for the
    ``helioviewer`` key. Helioviewer terminates the XML with a NUL byte, which a
    strict parser rejects; when a stray character in a HISTORY card breaks
    parsing anyway, simple ``<KEY>value</KEY>`` pairs are recovered by pattern.
    """
    text = str(xml_text or "").rstrip("\x00 \t\r\n")
    header: dict[str, Any] = {}
    try:
        root = ET.fromstring(text)
        for node in root.iter():
            if node.tag.upper() in ("HISTORY", "COMMENT"):
                continue
            if node.text is not None:
                header[node.tag.lower()] = _typed(node.text)
    except ET.ParseError:
        for tag, body in re.findall(r"<([A-Za-z][\w\-]*)>([^<]*)</\1>", text):
            if tag.upper() not in ("HISTORY", "COMMENT"):
                header[tag.lower()] = _typed(body)
        for container in ("meta", "fits", "helioviewer"):
            if f"<{container}>" in text:
                header[container] = ""
    return header


def decode_jp2_pixels(raw: bytes) -> np.ndarray:
    """Pixels as float32 in FITS row order (row 0 at the bottom)."""
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        pixels = np.asarray(image)
    if pixels.ndim == 3:
        pixels = pixels[..., 0]
    return np.ascontiguousarray(np.flipud(pixels)).astype(np.float32)


def normalise_header(header: dict[str, Any]) -> dict[str, Any]:
    """Make a Helioviewer header complete enough for sunpy and the GCS projector."""
    fixed = dict(header)
    fixed.setdefault("rsun_ref", RSUN_REF_M)
    rsun = fixed.get("rsun")
    if not isinstance(rsun, (int, float)) or rsun <= 0:
        fixed.pop("rsun", None)

    if str(fixed.get("instrume", "")).strip().upper() == "LASCO":
        stamp = str(fixed.get("date_obs") or fixed.get("date-obs") or "").strip().replace("/", "-")
        clock = str(fixed.get("time_obs") or fixed.get("time-obs") or "").strip()
        if stamp and "T" not in stamp and clock:
            stamp = f"{stamp}T{clock}"
        if stamp and not all(key in fixed for key in ("hgln_obs", "hglt_obs", "dsun_obs")):
            try:
                import astropy.units as u
                from sunpy.coordinates import get_earth

                earth = get_earth(stamp)
                fixed["hgln_obs"] = 0.0
                fixed["hglt_obs"] = float(earth.lat.to_value(u.deg))
                fixed["dsun_obs"] = float(earth.radius.to_value(u.m)) - L1_OFFSET_M
            except Exception:
                pass  # leaves the frame without an observer; GCS then skips it
    return fixed


def jp2_to_map(raw: bytes) -> Any:
    """A ``sunpy.map.Map`` from a Helioviewer JP2, oriented and located correctly."""
    import sunpy.map

    xml_text = jp2_xml_text(raw)
    if xml_text is None:
        raise HelioviewerJP2Error("The JP2 file carries no metadata box.")
    header = normalise_header(parse_jp2_header(xml_text))
    return sunpy.map.Map((decode_jp2_pixels(raw), header))


# --- Sequences ----------------------------------------------------------------


def _sun_centre_pixel(frame: Any) -> tuple[float, float]:
    """0-based (x, y) pixel of helioprojective (0, 0) — the Sun centre."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    centre = SkyCoord(0 * u.arcsec, 0 * u.arcsec, frame=frame.coordinate_frame)
    x, y = frame.world_to_pixel(centre)
    return float(x.to_value(u.pix)), float(y.to_value(u.pix))


def _rebinned(frame: Any, factor: int) -> Any:
    """Block-average by an integer factor, keeping the WCS exactly consistent."""
    import sunpy.map

    data = np.asarray(frame.data, dtype=np.float32)
    ny, nx = data.shape
    ny_out, nx_out = ny // factor, nx // factor
    binned = data[: ny_out * factor, : nx_out * factor].reshape(ny_out, factor, nx_out, factor).mean(axis=(1, 3))
    meta = frame.meta.copy()
    for axis in (1, 2):
        # 1-based FITS pixel p maps to (p - 0.5) / k + 0.5 after k-fold binning.
        meta[f"crpix{axis}"] = (float(meta[f"crpix{axis}"]) - 0.5) / factor + 0.5
        meta[f"cdelt{axis}"] = float(meta[f"cdelt{axis}"]) * factor
    meta["naxis1"], meta["naxis2"] = nx_out, ny_out
    return sunpy.map.Map((binned.astype(np.float32), meta))


def _shifted(frame: Any, dx: float, dy: float) -> Any:
    """Translate the image by (dx, dy) pixels and move CRPIX with it."""
    import sunpy.map
    from scipy import ndimage

    data = ndimage.shift(
        np.asarray(frame.data, dtype=np.float32), (dy, dx), order=1, mode="constant", cval=0.0
    )
    meta = frame.meta.copy()
    meta["crpix1"] = float(meta["crpix1"]) + dx
    meta["crpix2"] = float(meta["crpix2"]) + dy
    return sunpy.map.Map((data, meta))


def harmonise_sequence(frames: Sequence[Any], *, max_px: int = MAX_GRID_PX) -> tuple[list[Any], int]:
    """One pixel grid for a whole sequence: common size, Sun centres aligned.

    The grid is the most common frame size (capped at ``max_px``, ties going to
    the smaller). Larger frames that are a whole multiple of it are
    block-averaged down, which discards resolution but never invents it; frames
    that cannot be brought onto the grid that way are dropped rather than
    upsampled. Targeting the modal size instead of the smallest matters: one
    stray 512-pixel frame must not quietly halve the resolution of an otherwise
    1024-pixel sequence. Frames are then translated so every Sun centre lands on
    the first frame's, after which a running difference is a plain subtraction.

    Returns ``(frames, dropped_count)``.
    """
    usable = [frame for frame in frames if frame is not None and np.ndim(getattr(frame, "data", None)) == 2]
    if not usable:
        return [], len(frames)

    def effective(side: int) -> int:
        return max_px if side > max_px and side % max_px == 0 else side

    tally: dict[int, int] = {}
    for frame in usable:
        side = effective(int(frame.data.shape[0]))
        tally[side] = tally.get(side, 0) + 1
    target = min(tally, key=lambda side: (-tally[side], side))

    sized: list[Any] = []
    grid_shape: tuple[int, ...] | None = None
    for frame in usable:
        side = int(frame.data.shape[0])
        if side > target and side % target == 0:
            frame = _rebinned(frame, side // target)
        if int(frame.data.shape[0]) != target:
            continue  # smaller than the grid, or not a whole multiple of it
        shape = tuple(frame.data.shape)
        if grid_shape is None:
            grid_shape = shape
        if shape != grid_shape:
            continue
        sized.append(frame)
    dropped = len(frames) - len(sized)
    if not sized:
        return [], dropped

    try:
        ref_x, ref_y = _sun_centre_pixel(sized[0])
    except Exception:
        return sized, dropped  # no usable WCS to align on; same size is still differenceable
    aligned = [sized[0]]
    for frame in sized[1:]:
        try:
            x, y = _sun_centre_pixel(frame)
        except Exception:
            aligned.append(frame)
            continue
        dx, dy = ref_x - x, ref_y - y
        aligned.append(_shifted(frame, dx, dy) if max(abs(dx), abs(dy)) > 0.05 else frame)
    return aligned, dropped


@dataclass(frozen=True)
class JP2Sequence:
    """What a fetch hands back to the viewpoint panel."""

    source: JP2Source
    #: sunpy maps on one shared grid, in time order.
    frames: tuple[Any, ...]
    #: Frame times listed in the requested range, before any thinning.
    listed: int
    #: Frames served from the local cache rather than downloaded.
    from_cache: int
    #: Frames that could not be downloaded or decoded, with the reason.
    skipped: tuple[str, ...] = ()
    #: Frames discarded because they could not share the sequence's pixel grid.
    dropped: int = 0


def fetch_range(
    source: JP2Source,
    start: datetime,
    end: datetime,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    cache_dir: Path | None = None,
    session: Any = None,
    api_base: str = HELIOVIEWER_API_BASE,
    progress_cb: Callable[[str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> JP2Sequence:
    """List, pick, download, decode and harmonise every frame in ``[start, end]``."""
    def report(text: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(text)
            except Exception:
                pass

    problem = validate_range(start, end)
    if problem:
        raise HelioviewerJP2Error(problem)

    sess = _session(session)
    report(f"Listing {source.label} frames…")
    times = _cached_listing(source, start, end, session=sess, api_base=api_base, cancel_cb=cancel_cb)
    if not times:
        raise HelioviewerJP2Error(
            f"Helioviewer has no {source.label} frames between {start:%Y-%m-%d %H:%M} and "
            f"{end:%Y-%m-%d %H:%M} UTC."
        )
    picked = pick_frames_evenly(times, max_frames)
    cached = {
        when for when in picked if cache_dir is not None and cache_path(cache_dir, source, when).is_file()
    }

    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    frames: list[Any] = []
    skipped: list[str] = []
    done_count = 0

    def load(when: datetime) -> Any:
        raw = download_jp2(source, when, cache_dir=cache_dir, session=sess, api_base=api_base, cancel_cb=cancel_cb)
        return jp2_to_map(raw)

    report(f"Downloading {len(picked) - len(cached)} of {len(picked)} {source.label} frame(s)…")
    # The pool is only allowed to run as fast as _REQUEST_SLOTS lets every panel
    # together, so three panels fetching at once still make at most three requests.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="hv-jp2") as pool:
        pending = {pool.submit(load, when): when for when in picked}
        try:
            while pending:
                finished, _ = wait(list(pending), timeout=0.25, return_when=FIRST_COMPLETED)
                if _cancelled(cancel_cb):
                    raise HelioviewerJP2Cancelled("Cancelled.")
                for future in finished:
                    when = pending.pop(future)
                    try:
                        frames.append(future.result())
                    except HelioviewerJP2Cancelled:
                        raise
                    except Exception as exc:
                        skipped.append(f"{when:%H:%M:%S}: {exc}")
                    done_count += 1
                    report(f"{source.label}: {done_count}/{len(picked)} frame(s) ready…")
        except HelioviewerJP2Cancelled:
            for future in pending:
                future.cancel()
            raise

    if not frames:
        raise HelioviewerJP2Error(
            f"None of the {len(picked)} {source.label} frames could be loaded "
            f"({skipped[0] if skipped else 'unknown error'})."
        )
    report(f"Aligning {len(frames)} {source.label} frame(s)…")
    frames.sort(key=lambda frame: frame.date.datetime)
    harmonised, dropped = harmonise_sequence(frames)
    return JP2Sequence(
        source=source,
        frames=tuple(harmonised),
        listed=len(times),
        from_cache=len(cached),
        skipped=tuple(skipped),
        dropped=dropped,
    )


#: Frame listings for windows that ended over a day ago, which the archive will
#: not add to. Keyed by (sourceId, start, end).
_LISTING_CACHE: dict[tuple[int, datetime, datetime], list[datetime]] = {}
_LISTING_CACHE_LOCK = threading.Lock()


def _cached_listing(
    source: JP2Source,
    start: datetime,
    end: datetime,
    *,
    session: Any,
    api_base: str,
    cancel_cb: Callable[[], bool] | None,
) -> list[datetime]:
    key = (source.source_id, start, end)
    settled = end < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    if settled:
        with _LISTING_CACHE_LOCK:
            if key in _LISTING_CACHE:
                return list(_LISTING_CACHE[key])
    times = list_frame_times(source, start, end, session=session, api_base=api_base, cancel_cb=cancel_cb)
    if settled and times:
        with _LISTING_CACHE_LOCK:
            if len(_LISTING_CACHE) > 256:
                _LISTING_CACHE.clear()
            _LISTING_CACHE[key] = list(times)
    return times


# --- Difference images --------------------------------------------------------


def valid_field_mask(frame: Any, *, erode_px: int = 3) -> np.ndarray:
    """Pixels inside the detector's usable field.

    Helioviewer zeroes both the occulter and everything outside the detector, so
    the non-zero pixels *are* the field; eroding them removes the ringing at both
    edges, which otherwise dominates a difference image's contrast.
    """
    from scipy import ndimage

    inside = np.asarray(frame.data) > 0
    if erode_px > 0:
        inside = ndimage.binary_erosion(inside, iterations=int(erode_px))
    return inside


def difference_image(
    current: Any,
    reference: Any,
    *,
    smooth_sigma_px: float = 1.0,
) -> np.ndarray:
    """``current - reference`` on a harmonised pair, ready to display.

    The median over the shared field is removed so "no change" sits at zero —
    a 1024-pixel and a 2048-pixel JP2 of the same corona can differ by several
    counts overall — and a light Gaussian takes out the JPEG2000 and photon noise
    that otherwise buries a faint front. Outside the field the result is exactly
    zero, which a display symmetric about zero shows as neutral.
    """
    from scipy import ndimage

    a = np.asarray(current.data, dtype=np.float32)
    b = np.asarray(reference.data, dtype=np.float32)
    if a.shape != b.shape:
        raise ValueError("Difference frames must share one grid; harmonise the sequence first.")
    field = valid_field_mask(current) & valid_field_mask(reference)
    diff = a - b
    if np.any(field):
        diff -= float(np.median(diff[field]))
    if smooth_sigma_px and smooth_sigma_px > 0:
        diff = ndimage.gaussian_filter(np.where(field, diff, 0.0), sigma=float(smooth_sigma_px))
    return np.where(field, diff, 0.0).astype(np.float32)


def symmetric_clip(image: np.ndarray, percentile: float = 99.0) -> float:
    """Half-range for a zero-centred display, from the non-zero (in-field) pixels."""
    values = np.abs(np.asarray(image, dtype=np.float32))
    values = values[values > 0]
    if values.size == 0:
        return 1.0
    extent = float(np.percentile(values, min(100.0, max(50.0, float(percentile)))))
    return extent if extent > 0 else 1.0
