"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Fast SDO/AIA data access through NASA JSOC (drms).

The default VSO path is convenient but slow: its servers are heavily shared and
every file is staged server-side. JSOC's ``url_quick`` export returns *direct*
segment URLs immediately (no export queue) and the stored lev1 records are
already Rice-compressed, so a single request gives us the fastest source, the
smallest bytes-on-the-wire, and a list of plain HTTP(S) URLs that drop straight
into :mod:`src.Backend.download_manager` for byte-accurate progress.

This module only *builds the request and resolves URLs*; the actual transfer is
done by the shared download engine so the UI experience is identical across
sources. JSOC export requires a one-time registered notify e-mail
(https://jsoc.stanford.edu/ajax/register_email.html); the window stores it.

Everything here accepts an injectable ``client`` so it can be unit tested
without a network round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Sequence

_logger = logging.getLogger("ecallisto.sunpy")

# AIA prime-key series by channel. EUV and UV live in different series with
# different native cadences; the continuum/visible channels are out of scope.
_AIA_EUV_WAVELENGTHS = (94, 131, 171, 193, 211, 304, 335)
_AIA_UV_WAVELENGTHS = (1600, 1700)
SERIES_AIA_EUV = "aia.lev1_euv_12s"
SERIES_AIA_UV = "aia.lev1_uv_24s"
AIA_SEGMENT = "image"

# Default cadence (s) when the caller does not pin one, so a recordset never
# accidentally expands to every native-cadence frame in the window.
DEFAULT_CADENCE_SECONDS = 60

# Frame-size modes. Smaller payloads download dramatically faster; binned and
# cutout modes are produced server-side by JSOC (im_patch / rebin) so only the
# reduced data crosses the wire.
SIZE_FULL = "full"
SIZE_BIN2 = "bin2"
SIZE_BIN4 = "bin4"
SIZE_CUTOUT = "cutout"

# Rough per-frame byte estimates (compressed) used only for the UI's size/time
# hint before a download starts. Full-disk lev1 EUV is ~8 MB Rice-compressed;
# binning halves/quarters linear resolution (¼ / 1/16 the pixels).
_PER_FRAME_BYTES = {
    SIZE_FULL: 8 * 1024**2,
    SIZE_BIN2: 2 * 1024**2,
    SIZE_BIN4: 512 * 1024,
    SIZE_CUTOUT: 400 * 1024,
}
# Conservative sustained throughput (bytes/s) assumed when estimating time.
_ESTIMATE_THROUGHPUT_BPS = 5 * 1024**2


def size_process(
    mode: str,
    *,
    cutout: tuple[float, float, float, float] | None = None,
    t_ref: str | None = None,
) -> dict[str, Any] | None:
    """Map a frame-size mode to a JSOC export ``process`` dict (or None).

    ``cutout`` is ``(x, y, width, height)`` in arcsec relative to disk centre.
    Binned/cutout modes force a staged export (see :func:`export_urls`).
    """
    mode = str(mode or SIZE_FULL)
    if mode == SIZE_FULL:
        return None
    if mode == SIZE_BIN2:
        return {"rebin": {"method": "boxcar", "scale": 0.5}}
    if mode == SIZE_BIN4:
        return {"rebin": {"method": "boxcar", "scale": 0.25}}
    if mode == SIZE_CUTOUT:
        if not cutout:
            raise JsocError("Cutout mode requires a centre and box size (x, y, width, height).")
        x, y, width, height = cutout
        if width <= 0 or height <= 0:
            raise JsocError("Cutout width and height must be positive.")
        patch: dict[str, Any] = {
            "t": 0,
            "r": 0,
            "c": 0,
            "locunits": "arcsec",
            "boxunits": "arcsec",
            "x": float(x),
            "y": float(y),
            "width": float(width),
            "height": float(height),
        }
        if t_ref:
            patch["t_ref"] = t_ref
        return {"im_patch": patch}
    raise JsocError(f"Unknown frame-size mode: {mode!r}")


def per_frame_bytes(mode: str) -> int:
    return _PER_FRAME_BYTES.get(str(mode or SIZE_FULL), _PER_FRAME_BYTES[SIZE_FULL])


def estimate_download(n_frames: int, mode: str) -> tuple[int, float]:
    """Return ``(estimated_bytes, estimated_seconds)`` for a UI hint."""
    n = max(0, int(n_frames))
    total = n * per_frame_bytes(mode)
    seconds = total / _ESTIMATE_THROUGHPUT_BPS if total else 0.0
    return total, seconds


@dataclass(frozen=True)
class JsocUrl:
    url: str
    filename: str
    record: str
    size: int | None = None


@dataclass(frozen=True)
class JsocExportResult:
    series: str
    recordset: str
    urls: list[JsocUrl]
    record_count: int


class JsocError(RuntimeError):
    """Raised when a JSOC request cannot be built or resolved."""


def series_for_wavelength(wavelength_angstrom: float | int) -> str:
    wl = int(round(float(wavelength_angstrom)))
    if wl in _AIA_UV_WAVELENGTHS:
        return SERIES_AIA_UV
    if wl in _AIA_EUV_WAVELENGTHS:
        return SERIES_AIA_EUV
    raise JsocError(f"No JSOC AIA series for wavelength {wl} A.")


def _format_jsoc_time(dt: datetime) -> str:
    # JSOC accepts ISO UTC with a trailing Z in recordset time ranges.
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_recordset(
    *,
    start: datetime,
    end: datetime,
    wavelength_angstrom: float | int,
    cadence_seconds: float | int | None = None,
    segment: str = AIA_SEGMENT,
) -> tuple[str, str]:
    """Return ``(series, recordset)`` for an AIA query.

    The recordset uses JSOC's ``[start/duration@cadence][wavelength]{segment}``
    grammar, e.g.
    ``aia.lev1_euv_12s[2014-11-05T09:45:00Z/3600s@120s][193]{image}``.
    """
    if end <= start:
        raise JsocError("End time must be after start time.")
    series = series_for_wavelength(wavelength_angstrom)
    wl = int(round(float(wavelength_angstrom)))

    duration = int(round((end - start).total_seconds()))
    cadence = int(round(float(cadence_seconds))) if cadence_seconds else DEFAULT_CADENCE_SECONDS
    cadence = max(1, cadence)

    recordset = (
        f"{series}[{_format_jsoc_time(start)}/{duration}s@{cadence}s][{wl}]"
        f"{{{segment}}}"
    )
    return series, recordset


def make_client(email: str | None = None, *, drms_module: Any | None = None) -> Any:
    """Construct a drms client, importing drms lazily."""
    if drms_module is None:
        try:
            import drms as drms_module  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised only without drms
            raise JsocError(
                "The 'drms' package is required for the JSOC fast path. "
                "Install it with: python3 -m pip install drms"
            ) from exc
    try:
        return drms_module.Client(email=email) if email else drms_module.Client()
    except Exception as exc:
        raise JsocError(f"Could not create JSOC client: {exc}") from exc


def check_email(email: str, *, client: Any | None = None, drms_module: Any | None = None) -> bool:
    """Return True if the e-mail is registered for JSOC exports."""
    email = str(email or "").strip()
    if not email or "@" not in email:
        return False
    if client is None:
        client = make_client(email, drms_module=drms_module)
    checker = getattr(client, "check_email", None)
    if not callable(checker):
        return True  # older clients cannot check; assume caller registered it
    try:
        return bool(checker(email))
    except Exception:
        return False


def export_urls(
    *,
    start: datetime,
    end: datetime,
    wavelength_angstrom: float | int,
    email: str,
    cadence_seconds: float | int | None = None,
    segment: str = AIA_SEGMENT,
    process: dict[str, Any] | None = None,
    method: str = "url_quick",
    protocol: str = "as-is",
    client: Any | None = None,
    drms_module: Any | None = None,
) -> JsocExportResult:
    """Resolve direct download URLs for an AIA query via JSOC export.

    ``method='url_quick'`` + ``protocol='as-is'`` skips the staging queue and
    returns the compressed lev1 segments directly — the fast path. A
    ``process`` dict (e.g. ``im_patch`` cutouts or ``rebin``) forces a staged
    ``method='url'`` export because the server must generate new files; callers
    that pass ``process`` should expect a short wait.
    """
    email = str(email or "").strip()
    if not email:
        raise JsocError("A registered JSOC notify e-mail is required for export.")

    series, recordset = build_recordset(
        start=start,
        end=end,
        wavelength_angstrom=wavelength_angstrom,
        cadence_seconds=cadence_seconds,
        segment=segment,
    )

    if client is None:
        client = make_client(email, drms_module=drms_module)

    # Cutout / rebin processing cannot use the quick path; it must be staged.
    effective_method = method
    effective_protocol = protocol
    if process:
        effective_method = "url"
        if protocol == "as-is":
            effective_protocol = "fits"

    _logger.info(
        "JSOC export: series=%s method=%s protocol=%s process=%s recordset=%s",
        series, effective_method, effective_protocol, bool(process), recordset,
    )

    try:
        request = client.export(
            recordset,
            method=effective_method,
            protocol=effective_protocol,
            email=email,
            process=process or None,
        )
    except TypeError:
        # Older drms signatures don't accept process/email kwargs uniformly.
        request = client.export(recordset, method=effective_method, protocol=effective_protocol)
    except Exception as exc:
        raise JsocError(f"JSOC export failed: {exc}") from exc

    _wait_for_request(request, staged=bool(process) or effective_method == "url")
    urls = _extract_urls(request)
    if not urls:
        raise JsocError("JSOC export returned no URLs for the requested records.")

    return JsocExportResult(series=series, recordset=recordset, urls=urls, record_count=len(urls))


def _wait_for_request(request: Any, *, staged: bool) -> None:
    waiter = getattr(request, "wait", None)
    if staged and callable(waiter):
        try:
            waiter()
        except Exception as exc:
            raise JsocError(f"JSOC export did not complete: {exc}") from exc


def _extract_urls(request: Any) -> list[JsocUrl]:
    """Pull (url, filename, record) rows out of an ExportRequest.

    drms exposes them as a pandas DataFrame on ``.urls`` with at least a 'url'
    column and usually 'record' and 'filename'. We stay duck-typed so a fake
    request (a plain object with a list/records) works in tests.
    """
    table = getattr(request, "urls", None)
    if table is None:
        return []

    # pandas DataFrame path.
    to_records = getattr(table, "to_dict", None)
    if callable(to_records):
        try:
            rows = table.to_dict("records")  # type: ignore[call-arg]
        except TypeError:
            rows = table.to_dict()
        return _rows_to_urls(rows if isinstance(rows, list) else [])

    if isinstance(table, Sequence):
        return _rows_to_urls(list(table))
    return []


def _rows_to_urls(rows: list[Any]) -> list[JsocUrl]:
    out: list[JsocUrl] = []
    for row in rows:
        url = _row_value(row, "url")
        if not url:
            continue
        record = _row_value(row, "record") or ""
        filename = _row_value(row, "filename") or _filename_from_record(record) or _filename_from_url(url)
        size = _row_int(row, "size")
        out.append(JsocUrl(url=str(url), filename=str(filename), record=str(record), size=size))
    return out


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key) or row.get(key.upper()) or row.get(key.capitalize())
    return getattr(row, key, None)


def _row_int(row: Any, key: str) -> int | None:
    value = _row_value(row, key)
    try:
        ivalue = int(value)
        return ivalue if ivalue > 0 else None
    except (TypeError, ValueError):
        return None


def _filename_from_record(record: str) -> str:
    text = str(record or "").strip()
    if not text:
        return ""
    safe = (
        text.replace("[", "_").replace("]", "").replace("{", "_").replace("}", "")
        .replace(":", "").replace("/", "_").replace(" ", "")
    )
    if not safe.lower().endswith((".fits", ".fts")):
        safe = f"{safe}.fits"
    return safe


def _filename_from_url(url: str) -> str:
    from pathlib import Path

    name = Path(str(url).split("?", 1)[0]).name
    return name or "jsoc_download.fits"
