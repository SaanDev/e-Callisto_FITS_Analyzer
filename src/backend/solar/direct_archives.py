"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Direct-URL solar archives for the Solar Image Analysis window (src/backend/solar/direct_archives.py).

Two of the window's products come from archives whose files are plain URLs, so
they are searched and downloaded here rather than through SunPy's Fido:

* **Helioviewer JPEG2000 quicklooks.** ``getJPX`` lists every frame time of a
  source in a window in one call (see ``helioviewer_jp2``); ``getJP2Image``
  returns the file for an exact time. Ranges longer than a day are listed a day
  at a time so each call stays fast.
* **JSOC's AIA synoptic FITS.** Level 1.5 frames reduced to 1024² (2.4″ pixels),
  in ``YYYY/MM/DD/HhhMM/`` hour folders. The definitive set has a 2-minute
  cadence and appears about 7 days after observation; a near-real-time twin under
  ``nrt/`` has a 3-minute cadence and a 15-20 minute latency. Each hour folder is
  an Apache listing that carries every file's byte size, so the download's total
  is known exactly before it starts. ``jsoc.stanford.edu/data`` redirects to
  ``jsoc1``, which is asked directly to save the redirect.

Both produce the same ``SunPySearchResult`` / ``SunPyFetchResult`` /
``SunPyLoadResult`` the Fido path does, so the worker and the window treat every
archive alike. Downloads go through ``DownloadManager``, which reports real bytes
for every file. The search result's ``raw_response`` is the list of
:class:`DirectFile` the rows stand for.

No Qt, so everything here is unit-testable without a display.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from src.backend.solar import data_products as dp
from src.backend.solar import helioviewer_jp2 as hv
from src.backend.solar.sunpy_archive import (
    DATA_KIND_MAP,
    SunPyFetchResult,
    SunPyLoadResult,
    SunPyQuerySpec,
    SunPySearchResult,
    SunPySearchRow,
    configure_fetch_logging,
    normalize_query_spec,
)
from src.version import APP_VERSION

_logger = logging.getLogger("ecallisto.sunpy")

_USER_AGENT = f"e-Callisto-FITS-Analyzer/{APP_VERSION}"

SYNOPTIC_BASE = "https://jsoc1.stanford.edu/data/aia/synoptic/"
SYNOPTIC_NRT_BASE = SYNOPTIC_BASE + "nrt/"
_SYNOPTIC_TIMEOUT = (8, 30)

#: Longest window a synoptic search lists: one request per hour folder.
MAX_SYNOPTIC_DAYS = 7
#: Longest window a JP2 search lists: one ``getJPX`` call per day.
MAX_JP2_DAYS = 31

#: Helioviewer drops connections under bursts of parallel requests.
JP2_DOWNLOAD_CONNECTIONS = 3
SYNOPTIC_DOWNLOAD_CONNECTIONS = 4

# One file line of an Apache listing:
#   <a href="AIA20261005_1200_0193.fits">AIA20261005_1200_0193.fits</a>   12-Oct-2026 09:13   1166400
# Definitive files are named to the minute, NRT files to the second.
_LISTING_RE = re.compile(
    r'href="(AIA(\d{8})_(\d{4}(?:\d{2})?)_(\d{4})\.fits)"[^\n]*?\s(\d+)\s*$',
    re.MULTILINE,
)


@dataclass(frozen=True)
class DirectFile:
    """One downloadable file behind a search row."""

    url: str
    filename: str
    when: datetime
    size: int | None = None


class DirectArchiveCancelled(RuntimeError):
    """The caller cancelled a search."""


def _cancelled(cancel_cb: Callable[[], bool] | None) -> bool:
    try:
        return bool(cancel_cb and cancel_cb())
    except Exception:
        return False


def _iso_z(when: datetime) -> str:
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any) -> str:
    try:
        return "" if value is None else str(value)
    except Exception:
        return ""


# --- Shared ------------------------------------------------------------------------


def observable_from_spec(spec: SunPyQuerySpec) -> tuple[str, Any]:
    """The analysis window's (instrument, value) observable a spec was built from."""
    instrument = str(spec.instrument or "").strip().upper()
    if instrument in ("AIA", "EIT", "SUVI"):
        return instrument, spec.wavelength_angstrom
    if instrument == "HMI":
        return instrument, spec.product or "magnetogram"
    if instrument == "LASCO":
        return instrument, spec.detector
    if instrument == "SECCHI":
        return instrument, (spec.spacecraft, spec.detector, spec.wavelength_angstrom)
    return instrument, None


def thin_times(
    times: Sequence[datetime],
    *,
    cadence_seconds: float | None = None,
    max_records: int | None = None,
    latest: bool = False,
) -> list[datetime]:
    """Frame times kept for a search: at most one per cadence, then capped.

    Frames are kept first-come, so each kept frame is at least ``cadence``
    after the previous one (with a little slack for timestamp jitter). A cap
    then spreads the survivors evenly over the window, or keeps the newest when
    ``latest`` is set (Find Latest).
    """
    ordered = sorted(set(times))
    if cadence_seconds and cadence_seconds > 0:
        slack = max(1.0, 0.02 * float(cadence_seconds))
        kept: list[datetime] = []
        for when in ordered:
            if not kept or (when - kept[-1]).total_seconds() >= float(cadence_seconds) - slack:
                kept.append(when)
        ordered = kept
    cap = int(max_records or 0)
    if cap > 0 and len(ordered) > cap:
        if latest:
            return ordered[-cap:]
        if cap == 1:
            return [ordered[0]]
        ordered = hv.pick_frames_evenly(ordered, cap)
    return ordered


def _result(
    spec: SunPyQuerySpec,
    files: list[DirectFile],
    rows: list[SunPySearchRow],
) -> SunPySearchResult:
    return SunPySearchResult(
        spec=spec,
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=files,
        row_index_map=[(0, index) for index in range(len(rows))],
    )


# --- Helioviewer JP2 ---------------------------------------------------------------


def jp2_url(source: hv.JP2Source, when: datetime, *, api_base: str = hv.HELIOVIEWER_API_BASE) -> str:
    return f"{api_base}getJP2Image/?date={_iso_z(when)}&sourceId={int(source.source_id)}"


def _day_chunks(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        stop = min(end, cursor + timedelta(days=1))
        chunks.append((cursor, stop))
        cursor = stop
    return chunks or [(start, end)]


def _check_jp2_coverage(source: hv.JP2Source, start: datetime, end: datetime) -> None:
    if end.date() < source.first_day:
        raise ValueError(f"Helioviewer's {source.label} archive starts on {source.first_day:%Y-%m-%d}.")
    if source.last_day is not None and start.date() > source.last_day:
        raise ValueError(f"Helioviewer's {source.label} archive ends on {source.last_day:%Y-%m-%d}.")


def search_jp2(
    spec: SunPyQuerySpec,
    *,
    progress_cb: Callable[[Any, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    session: Any = None,
    api_base: str = hv.HELIOVIEWER_API_BASE,
    latest: bool = False,
) -> SunPySearchResult:
    """List Helioviewer JP2 frames for the spec's observable and window."""
    spec = normalize_query_spec(spec)
    instrument, value = observable_from_spec(spec)
    source = dp.jp2_source_for(instrument, value)
    if source is None:
        raise ValueError(
            dp.unavailable_reason(dp.PRODUCT_JP2, instrument, value)
            or "This observable has no JP2 quicklook archive."
        )
    _check_jp2_coverage(source, spec.start_dt, spec.end_dt)
    chunks = _day_chunks(spec.start_dt, spec.end_dt)
    if len(chunks) > MAX_JP2_DAYS:
        raise ValueError(f"JP2 searches cover at most {MAX_JP2_DAYS} days — narrow the window.")

    times: list[datetime] = []
    for index, (chunk_start, chunk_end) in enumerate(chunks):
        if _cancelled(cancel_cb):
            break
        if progress_cb is not None:
            if len(chunks) == 1:
                progress_cb(None, f"Listing Helioviewer {source.label} frames…")
            else:
                progress_cb(
                    int(index * 100 / len(chunks)),
                    f"Listing Helioviewer {source.label} frames: day {index + 1} of {len(chunks)}…",
                )
        try:
            times.extend(
                hv.list_frame_times(
                    source, chunk_start, chunk_end, session=session, api_base=api_base, cancel_cb=cancel_cb
                )
            )
        except hv.HelioviewerJP2Cancelled:
            break

    times = [when for when in times if spec.start_dt <= when <= spec.end_dt]
    chosen = thin_times(
        times, cadence_seconds=spec.sample_seconds, max_records=spec.max_records, latest=latest
    )
    size_hint = dp.format_size_hint(dp.jp2_frame_bytes(source))
    files: list[DirectFile] = []
    rows: list[SunPySearchRow] = []
    for when in chosen:
        name = hv.cache_path(Path("."), source, when).name
        files.append(DirectFile(url=jp2_url(source, when, api_base=api_base), filename=name, when=when))
        rows.append(
            SunPySearchRow(
                start=when,
                end=when,
                source=source.spacecraft,
                instrument=f"{source.label} JP2",
                provider="Helioviewer",
                fileid=name,
                size=size_hint,
            )
        )
    _logger.info(
        "JP2 search: source=%s window=%s..%s listed=%d kept=%d",
        source.key, spec.start_dt, spec.end_dt, len(times), len(rows),
    )
    return _result(spec, files, rows)


# --- AIA synoptic ------------------------------------------------------------------


def synoptic_hour_url(base: str, hour: datetime) -> str:
    return f"{base}{hour:%Y/%m/%d}/H{hour:%H}00/"


def parse_synoptic_listing(html: str, folder_url: str, wavelength: int) -> list[DirectFile]:
    """Files of one passband in an hour folder's Apache listing."""
    wanted = f"{int(wavelength):04d}"
    files: list[DirectFile] = []
    for match in _LISTING_RE.finditer(str(html or "")):
        name, day, clock, passband, size = match.groups()
        if passband != wanted:
            continue
        pattern = "%Y%m%d%H%M%S" if len(clock) == 6 else "%Y%m%d%H%M"
        try:
            when = datetime.strptime(day + clock, pattern)
        except ValueError:
            continue
        files.append(DirectFile(url=folder_url + name, filename=name, when=when, size=int(size)))
    return files


def _list_synoptic_hour(
    hour: datetime,
    wavelength: int,
    *,
    getter: Callable[..., Any],
) -> tuple[list[DirectFile], Exception | None]:
    """The hour's files: the definitive set when it exists, else the NRT one."""
    last_error: Exception | None = None
    for base in (SYNOPTIC_BASE, SYNOPTIC_NRT_BASE):
        url = synoptic_hour_url(base, hour)
        try:
            response = getter(url, timeout=_SYNOPTIC_TIMEOUT, headers={"User-Agent": _USER_AGENT})
        except Exception as exc:  # noqa: BLE001 - one unreachable folder is not fatal
            last_error = exc
            continue
        status = int(getattr(response, "status_code", 200) or 200)
        if status == 404:
            continue
        if status >= 400:
            last_error = RuntimeError(f"JSOC returned HTTP {status} for {url}")
            continue
        files = parse_synoptic_listing(getattr(response, "text", ""), url, wavelength)
        if files:
            return files, None
    return [], last_error


def search_aia_synoptic(
    spec: SunPyQuerySpec,
    *,
    progress_cb: Callable[[Any, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    getter: Callable[..., Any] | None = None,
    max_workers: int = 4,
    latest: bool = False,
) -> SunPySearchResult:
    """List AIA synoptic 1024² FITS for the spec's passband and window."""
    spec = normalize_query_spec(spec)
    wavelength = int(round(float(spec.wavelength_angstrom or 193.0)))
    if wavelength not in dp.AIA_SYNOPTIC_WAVELENGTHS:
        raise ValueError(f"The AIA synoptic archive has no {wavelength} Å frames.")
    if spec.end_dt - spec.start_dt > timedelta(days=MAX_SYNOPTIC_DAYS):
        raise ValueError(f"Synoptic searches cover at most {MAX_SYNOPTIC_DAYS} days — narrow the window.")
    if getter is None:
        import requests

        getter = requests.get

    first_hour = spec.start_dt.replace(minute=0, second=0, microsecond=0)
    hours: list[datetime] = []
    cursor = first_hour
    while cursor <= spec.end_dt:
        hours.append(cursor)
        cursor += timedelta(hours=1)

    found: list[DirectFile] = []
    errors: list[Exception] = []
    workers = max(1, min(int(max_workers), len(hours)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aia-synoptic-list") as pool:
        futures = [pool.submit(_list_synoptic_hour, hour, wavelength, getter=getter) for hour in hours]
        for done, future in enumerate(as_completed(futures), start=1):
            if _cancelled(cancel_cb):
                for pending in futures:
                    pending.cancel()
                break
            files, error = future.result()
            found.extend(files)
            if error is not None:
                errors.append(error)
            if progress_cb is not None:
                progress_cb(
                    int(done * 100 / len(hours)),
                    f"Listing AIA synoptic {wavelength} Å files: hour {done} of {len(hours)}…",
                )
    if not found and errors and not _cancelled(cancel_cb):
        raise RuntimeError(f"The JSOC synoptic archive could not be reached: {errors[0]}")

    by_time = {item.when: item for item in found if spec.start_dt <= item.when <= spec.end_dt}
    chosen = thin_times(
        list(by_time), cadence_seconds=spec.sample_seconds, max_records=spec.max_records, latest=latest
    )
    files = [by_time[when] for when in chosen]
    rows = [
        SunPySearchRow(
            start=item.when,
            end=item.when,
            source="SDO",
            instrument=f"AIA {wavelength} synoptic",
            provider="JSOC NRT" if item.url.startswith(SYNOPTIC_NRT_BASE) else "JSOC",
            fileid=item.filename,
            size=_format_size(item.size),
        )
        for item in files
    ]
    _logger.info(
        "AIA synoptic search: wavelength=%d window=%s..%s listed=%d kept=%d",
        wavelength, spec.start_dt, spec.end_dt, len(by_time), len(rows),
    )
    return _result(spec, files, rows)


def _format_size(size: int | None) -> str:
    if not size:
        return ""
    from src.backend.services.download_manager import format_bytes

    return format_bytes(size)


# --- Search dispatch ---------------------------------------------------------------


def search_direct(
    spec: SunPyQuerySpec,
    *,
    progress_cb: Callable[[Any, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    latest: bool = False,
) -> SunPySearchResult:
    archive = str(getattr(spec, "archive", "") or "")
    if archive == dp.ARCHIVE_HELIOVIEWER_JP2:
        return search_jp2(spec, progress_cb=progress_cb, cancel_cb=cancel_cb, latest=latest)
    if archive == dp.ARCHIVE_AIA_SYNOPTIC:
        return search_aia_synoptic(spec, progress_cb=progress_cb, cancel_cb=cancel_cb, latest=latest)
    raise ValueError(f"'{archive}' is not a direct archive.")


def find_latest_direct(
    spec: SunPyQuerySpec,
    *,
    progress_cb: Callable[[Any, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    now: datetime | None = None,
    search_fn: Callable[..., SunPySearchResult] | None = None,
) -> SunPySearchResult | None:
    """The newest frames of a direct archive, looking back further until found.

    The window keeps the user's length (at least six hours) and ends now; when
    it is empty the look-back widens. The result's spec spans the frames found,
    so the window's time fields can follow it.
    """
    search_fn = search_fn or search_direct
    now = now or datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    span = max(spec.end_dt - spec.start_dt, timedelta(hours=6))
    longest = timedelta(days=MAX_SYNOPTIC_DAYS if spec.archive == dp.ARCHIVE_AIA_SYNOPTIC else 14)
    lookbacks: list[timedelta] = []
    for candidate in (span, timedelta(days=3), longest):
        candidate = min(candidate, longest)
        if not lookbacks or candidate > lookbacks[-1]:
            lookbacks.append(candidate)
    for lookback in lookbacks:
        if _cancelled(cancel_cb):
            return None
        if progress_cb is not None:
            progress_cb(None, f"Looking for the newest frames in the last {_describe(lookback)}…")
        window = replace(spec, start_dt=now - lookback, end_dt=now)
        result = search_fn(window, progress_cb=None, cancel_cb=cancel_cb, latest=True)
        if result.rows:
            first = result.rows[0].start
            last = result.rows[-1].start
            if last <= first:
                last = first + timedelta(minutes=1)
            return replace(result, spec=replace(result.spec, start_dt=first, end_dt=last))
    return None


def _describe(span: timedelta) -> str:
    hours = span.total_seconds() / 3600.0
    if hours < 48:
        return f"{int(round(hours))} hours"
    return f"{int(round(hours / 24))} days"


# --- Download ----------------------------------------------------------------------


def _requested_rows(selected_rows: Sequence[int] | None, n_rows: int) -> list[int]:
    if selected_rows is None:
        return list(range(n_rows))
    return sorted({int(index) for index in selected_rows if 0 <= int(index) < n_rows})


def fetch_direct(
    search_result: SunPySearchResult,
    cache_dir: str | Path,
    selected_rows: Sequence[int] | None = None,
    *,
    progress_cb: Callable[[Any, str], None] | None = None,
    byte_progress_cb: Callable[[Any], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    download_manager: Any | None = None,
) -> SunPyFetchResult:
    """Download the selected rows of a JP2 or synoptic search."""
    from src.backend.services.download_manager import DownloadItem, DownloadManager

    archive = str(getattr(search_result.spec, "archive", "") or "")
    if not dp.is_direct_archive(archive):
        raise ValueError(f"'{archive}' is not a direct archive.")
    files: list[DirectFile] = list(search_result.raw_response or [])
    requested = [index for index in _requested_rows(selected_rows, len(search_result.rows)) if index < len(files)]
    if not requested:
        return SunPyFetchResult(paths=[], requested_count=0, failed_count=0, errors=[])

    # Straight into the folder, like the VSO and JSOC files: Save to Disk puts
    # them where the user pointed, and the names (hv<id>_<time>.jp2,
    # AIA<time>_<band>.fits) cannot collide with the other archives'.
    target = Path(cache_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    configure_fetch_logging(target.parent)

    is_jp2 = archive == dp.ARCHIVE_HELIOVIEWER_JP2
    items = [
        DownloadItem(
            url=files[index].url,
            dest=target / files[index].filename,
            expected_size=files[index].size,
            record_id=files[index].filename,
            label=files[index].filename,
        )
        for index in requested
    ]
    manager = download_manager or DownloadManager(
        max_concurrent=JP2_DOWNLOAD_CONNECTIONS if is_jp2 else SYNOPTIC_DOWNLOAD_CONNECTIONS,
        progress_interval=0.2,
        user_agent=_USER_AGENT,
    )
    noun = "JP2 frame" if is_jp2 else "synoptic frame"
    provider = "Helioviewer" if is_jp2 else "JSOC"

    def _on_aggregate(agg: Any) -> None:
        if byte_progress_cb is not None:
            byte_progress_cb(agg)
        if progress_cb is not None:
            done = int(getattr(agg, "files_done", 0) or 0)
            try:
                percent = int(round(float(agg.progress_fraction()) * 100))
            except Exception:
                percent = 0
            progress_cb(percent, f"Downloaded {done}/{len(items)} {noun}(s) from {provider}")

    _logger.info("direct fetch: archive=%s files=%d cache=%s", archive, len(items), target)
    download = manager.download(items, progress_cb=_on_aggregate, cancel_cb=cancel_cb)

    paths: list[str] = []
    errors = list(download.errors)
    for path in download.paths:
        if is_jp2 and not _looks_like_jp2(path):
            errors.append(f"{Path(path).name}: Helioviewer did not return a JPEG2000 image.")
            try:
                Path(path).unlink()
            except OSError:
                pass
            continue
        paths.append(str(path))
    got = {Path(path).name for path in paths}
    failed_rows = [] if download.cancelled else [
        index for index in requested if files[index].filename not in got
    ]
    return SunPyFetchResult(
        paths=paths,
        requested_count=len(items),
        failed_count=len(errors),
        errors=errors,
        failed_rows=failed_rows,
        cancelled=bool(download.cancelled),
    )


def _looks_like_jp2(path: str | Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(hv.JP2_SIGNATURE)) == hv.JP2_SIGNATURE
    except OSError:
        return False


# --- Load --------------------------------------------------------------------------


def load_jp2_frames(
    paths: Sequence[str | Path],
    *,
    max_px: int = dp.DEFAULT_JP2_MAX_PX,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> SunPyLoadResult:
    """Decode JP2 files into quicklook maps, one at a time, skipping bad files."""
    normalized = [str(Path(path).expanduser().resolve()) for path in paths if str(path).strip()]
    if not normalized:
        raise ValueError("No JP2 files were provided.")
    maps: list[Any] = []
    loaded: list[str] = []
    errors: list[str] = []
    for index, path in enumerate(normalized):
        if _cancelled(cancel_cb):
            break
        try:
            maps.append(hv.load_jp2_file(path, max_px=int(max_px or 0)))
            loaded.append(path)
        except Exception as exc:  # noqa: BLE001 - one bad file must not sink the set
            errors.append(f"{Path(path).name}: {exc}")
        if progress_cb is not None:
            progress_cb(index + 1, len(normalized))
    if not maps:
        detail = ("\n" + "\n".join(errors[:5])) if errors else ""
        raise RuntimeError("No JP2 frames could be decoded." + detail)
    first = maps[0]
    metadata = {
        "n_frames": len(maps),
        "observatory": _text(getattr(first, "observatory", "")),
        "instrument": _text(getattr(first, "instrument", "")),
        "detector": _text(getattr(first, "detector", "")),
        "wavelength": _text(getattr(first, "wavelength", "")),
        "date": _text(getattr(first, "date", "")),
        "level": "",
        "product": dp.PRODUCT_JP2,
        "load_errors": errors,
    }
    return SunPyLoadResult(data_kind=DATA_KIND_MAP, paths=loaded, maps_or_timeseries=maps, metadata=metadata)
