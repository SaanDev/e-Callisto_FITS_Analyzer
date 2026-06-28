"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import time
from threading import Lock
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import warnings


DATA_KIND_MAP = "map"
DATA_KIND_TIMESERIES = "timeseries"
SUNPY_INSTALL_HINT = (
    'python3 -m pip install --upgrade "sunpy[map,net,timeseries]==7.1.0" lxml drms zeep reproject mpl-animators'
)

# ---------------------------------------------------------------------------
# Diagnostics logging
#
# The download path is async (parfive/aiohttp) and several failure modes are
# normally swallowed, which makes packaged builds (notably frozen Windows)
# impossible to diagnose. A dedicated file logger captures the real exceptions
# and the path actually taken (parfive vs urllib fallback). The UI configures
# the log directory at startup via configure_fetch_logging(); until then the
# logger is a no-op so importing this module stays side-effect free.
# ---------------------------------------------------------------------------

_LOGGER_NAME = "ecallisto.sunpy"
_FETCH_LOG_FILENAME = "sunpy_fetch.log"
_logger = logging.getLogger(_LOGGER_NAME)
_logger.setLevel(logging.INFO)
_logger.propagate = False
_logger.addHandler(logging.NullHandler())
_configured_log_path: Path | None = None


def get_sunpy_logger() -> logging.Logger:
    return _logger


_sunpy_logging_configured = False


def _configure_sunpy_logging() -> None:
    """Quiet SunPy's own logger so routine INFO messages (e.g. the very common
    'Missing metadata for solar radius...') do not flood the console when many
    maps are loaded. Runs once; the threshold can be overridden via
    ECALLISTO_SUNPY_LOG_LEVEL (e.g. INFO/DEBUG)."""
    global _sunpy_logging_configured
    if _sunpy_logging_configured:
        return
    level_name = str(os.environ.get("ECALLISTO_SUNPY_LOG_LEVEL", "WARNING")).strip().upper()
    level = getattr(logging, level_name, logging.WARNING)
    try:
        logging.getLogger("sunpy").setLevel(level)
        _sunpy_logging_configured = True
    except Exception:
        pass


def get_fetch_log_path() -> Path | None:
    return _configured_log_path


def configure_fetch_logging(log_dir: str | Path | None) -> Path | None:
    """Attach a rotating file handler for fetch diagnostics. Idempotent."""
    global _configured_log_path
    if not log_dir:
        return _configured_log_path
    try:
        directory = Path(log_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _FETCH_LOG_FILENAME
    except Exception:
        return _configured_log_path

    if _configured_log_path is not None and _configured_log_path == path:
        return _configured_log_path

    for handler in list(_logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            _logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    try:
        handler = RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
        _logger.addHandler(handler)
        _configured_log_path = path
        _logger.info("SunPy fetch logging initialised at %s", path)
        _logger.info(
            "TLS config: frozen=%s SSL_CERT_FILE=%s REQUESTS_CA_BUNDLE=%s",
            bool(getattr(sys, "frozen", False)),
            os.environ.get("SSL_CERT_FILE", "<unset>"),
            os.environ.get("REQUESTS_CA_BUNDLE", "<unset>"),
        )
    except Exception:
        pass
    return _configured_log_path


@dataclass(frozen=True)
class SunPyQuerySpec:
    start_dt: datetime
    end_dt: datetime
    spacecraft: str
    instrument: str
    wavelength_angstrom: float | None = None
    detector: str | None = None
    satellite_number: int | None = None
    sample_seconds: float | None = None
    resolution: float | str | None = None
    max_records: int = 200
    product: str | None = None  # HMI observable: magnetogram/continuum/dopplergram


@dataclass(frozen=True)
class SunPySearchRow:
    start: datetime
    end: datetime
    source: str
    instrument: str
    provider: str
    fileid: str
    size: str
    selected: bool = True


@dataclass(frozen=True)
class SunPyLoadResult:
    data_kind: str
    paths: list[str]
    maps_or_timeseries: Any
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SunPySearchResult:
    spec: SunPyQuerySpec
    data_kind: str
    rows: list[SunPySearchRow]
    raw_response: Any
    row_index_map: list[tuple[int, int]]


@dataclass(frozen=True)
class SunPyFetchResult:
    paths: list[str]
    requested_count: int
    failed_count: int
    errors: list[str]
    failed_rows: list[int] = field(default_factory=list)
    cancelled: bool = False


@dataclass(frozen=True)
class InstrumentRegistryEntry:
    key: str
    label: str
    spacecraft: str
    instrument: str
    detector: str | None
    data_kind: str
    supports_wavelength: bool
    supports_detector: bool
    supports_satellite: bool
    default_wavelength: float | None
    default_detector: str | None
    default_satellite: int | None
    wavelengths: tuple[float, ...]
    supports_product: bool = False
    products: tuple[str, ...] = ()
    default_product: str | None = None


@dataclass(frozen=True)
class _DirectDownloadItem:
    row_index: int
    url: str
    fileid: str
    path_hint: str
    expected_bytes: int | None = None
    urls: tuple[str, ...] = ()


INSTRUMENT_REGISTRY: tuple[InstrumentRegistryEntry, ...] = (
    InstrumentRegistryEntry(
        key="sdo_aia",
        label="SDO/AIA",
        spacecraft="SDO",
        instrument="AIA",
        detector=None,
        data_kind=DATA_KIND_MAP,
        supports_wavelength=True,
        supports_detector=False,
        supports_satellite=False,
        default_wavelength=193.0,
        default_detector=None,
        default_satellite=None,
        wavelengths=(94.0, 131.0, 171.0, 193.0, 211.0, 304.0, 335.0, 1600.0, 1700.0),
    ),
    InstrumentRegistryEntry(
        key="sdo_hmi",
        label="SDO/HMI",
        spacecraft="SDO",
        instrument="HMI",
        detector=None,
        data_kind=DATA_KIND_MAP,
        supports_wavelength=False,
        supports_detector=False,
        supports_satellite=False,
        default_wavelength=None,
        default_detector=None,
        default_satellite=None,
        wavelengths=(),
        supports_product=True,
        products=("magnetogram", "continuum", "dopplergram"),
        default_product="magnetogram",
    ),
    InstrumentRegistryEntry(
        key="soho_lasco_c2",
        label="SOHO/LASCO C2",
        spacecraft="SOHO",
        instrument="LASCO",
        detector="C2",
        data_kind=DATA_KIND_MAP,
        supports_wavelength=False,
        supports_detector=True,
        supports_satellite=False,
        default_wavelength=None,
        default_detector="C2",
        default_satellite=None,
        wavelengths=(),
    ),
    InstrumentRegistryEntry(
        key="soho_lasco_c3",
        label="SOHO/LASCO C3",
        spacecraft="SOHO",
        instrument="LASCO",
        detector="C3",
        data_kind=DATA_KIND_MAP,
        supports_wavelength=False,
        supports_detector=True,
        supports_satellite=False,
        default_wavelength=None,
        default_detector="C3",
        default_satellite=None,
        wavelengths=(),
    ),
    InstrumentRegistryEntry(
        key="stereo_a_euvi",
        label="STEREO-A/EUVI",
        spacecraft="STEREO_A",
        instrument="EUVI",
        detector=None,
        data_kind=DATA_KIND_MAP,
        supports_wavelength=True,
        supports_detector=False,
        supports_satellite=False,
        default_wavelength=195.0,
        default_detector=None,
        default_satellite=None,
        wavelengths=(171.0, 195.0, 284.0, 304.0),
    ),
    InstrumentRegistryEntry(
        key="goes_xrs",
        label="GOES/XRS",
        spacecraft="GOES",
        instrument="XRS",
        detector=None,
        data_kind=DATA_KIND_TIMESERIES,
        supports_wavelength=False,
        supports_detector=False,
        supports_satellite=True,
        default_wavelength=None,
        default_detector=None,
        default_satellite=16,
        wavelengths=(),
    ),
    InstrumentRegistryEntry(
        key="proba2_swap",
        label="PROBA2/SWAP",
        spacecraft="PROBA2",
        instrument="SWAP",
        detector=None,
        data_kind=DATA_KIND_MAP,
        supports_wavelength=False,
        supports_detector=False,
        supports_satellite=False,
        default_wavelength=None,
        default_detector=None,
        default_satellite=None,
        wavelengths=(),
    ),
)


def list_instrument_registry() -> list[InstrumentRegistryEntry]:
    return list(INSTRUMENT_REGISTRY)


def registry_spacecraft_list() -> list[str]:
    """Ordered, de-duplicated spacecraft names from the registry."""
    out: list[str] = []
    for entry in INSTRUMENT_REGISTRY:
        if entry.spacecraft not in out:
            out.append(entry.spacecraft)
    return out


def registry_instruments_for(spacecraft: str) -> list[str]:
    """Ordered, de-duplicated instruments available for a spacecraft."""
    sc = str(spacecraft or "").strip().upper()
    out: list[str] = []
    for entry in INSTRUMENT_REGISTRY:
        if entry.spacecraft == sc and entry.instrument not in out:
            out.append(entry.instrument)
    return out


def registry_detectors_for(spacecraft: str, instrument: str) -> list[str]:
    """Ordered detector names for a spacecraft/instrument (empty if none)."""
    sc = str(spacecraft or "").strip().upper()
    inst = str(instrument or "").strip().upper()
    out: list[str] = []
    for entry in INSTRUMENT_REGISTRY:
        if entry.spacecraft == sc and entry.instrument == inst and entry.detector:
            if entry.detector not in out:
                out.append(entry.detector)
    return out


def registry_lookup(
    spacecraft: str,
    instrument: str,
    detector: str | None = None,
) -> InstrumentRegistryEntry | None:
    """Find the registry entry for a target, or None if unsupported."""
    sc = str(spacecraft or "").strip().upper()
    inst = str(instrument or "").strip().upper()
    det = str(detector or "").strip().upper()
    for entry in INSTRUMENT_REGISTRY:
        if entry.spacecraft != sc or entry.instrument != inst:
            continue
        if entry.detector and det and entry.detector != det:
            continue
        return entry
    for entry in INSTRUMENT_REGISTRY:
        if entry.spacecraft == sc and entry.instrument == inst:
            return entry
    return None


def build_attrs(
    spec: SunPyQuerySpec,
    attrs_module: Any | None = None,
    units_module: Any | None = None,
) -> list[Any]:
    spec = normalize_query_spec(spec)
    entry = resolve_registry_entry(spec)
    attrs_module, units_module = _resolve_attr_and_units(attrs_module, units_module)

    out = [
        attrs_module.Time(spec.start_dt, spec.end_dt),
        attrs_module.Source(spec.spacecraft),
        attrs_module.Instrument(spec.instrument),
    ]

    detector = (spec.detector or entry.default_detector or "").strip()
    if detector and entry.supports_detector:
        out.append(attrs_module.Detector(detector))

    wavelength = spec.wavelength_angstrom or entry.default_wavelength
    if wavelength is not None and entry.supports_wavelength:
        out.append(attrs_module.Wavelength(float(wavelength) * units_module.angstrom))

    # HMI observables (magnetogram / continuum / dopplergram) are selected via
    # VSO Physobs rather than a wavelength.
    if entry.supports_product:
        product = (spec.product or entry.default_product or "").strip().lower()
        if product and hasattr(attrs_module, "Physobs"):
            try:
                from src.Backend.jsoc_client import hmi_physobs

                out.append(attrs_module.Physobs(hmi_physobs(product)))
            except Exception:
                pass

    if spec.sample_seconds and float(spec.sample_seconds) > 0 and hasattr(attrs_module, "Sample"):
        out.append(attrs_module.Sample(float(spec.sample_seconds) * units_module.second))

    if spec.resolution is not None and hasattr(attrs_module, "Resolution"):
        out.append(attrs_module.Resolution(spec.resolution))

    if entry.supports_satellite:
        sat_number = int(spec.satellite_number or entry.default_satellite or 0)
        if sat_number > 0:
            goes_attrs = getattr(attrs_module, "goes", None)
            sat_cls = getattr(goes_attrs, "SatelliteNumber", None) if goes_attrs is not None else None
            if sat_cls is not None:
                out.append(sat_cls(int(sat_number)))

    return out


def search(
    spec: SunPyQuerySpec,
    *,
    fido_client: Any | None = None,
    attrs_module: Any | None = None,
    units_module: Any | None = None,
) -> SunPySearchResult:
    spec = normalize_query_spec(spec)
    entry = resolve_registry_entry(spec)
    attrs = build_attrs(spec, attrs_module=attrs_module, units_module=units_module)

    if fido_client is None:
        fido_client = _import_fido()

    raw_response = fido_client.search(*attrs)
    rows, row_index_map = _normalize_search_rows(raw_response, spec, max_records=int(spec.max_records or 0))

    return SunPySearchResult(
        spec=spec,
        data_kind=entry.data_kind,
        rows=rows,
        raw_response=raw_response,
        row_index_map=row_index_map,
    )


def fetch(
    search_result: SunPySearchResult,
    cache_dir: str | Path,
    selected_rows: Sequence[int] | None = None,
    *,
    progress_cb: Callable[[int, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    fido_client: Any | None = None,
) -> SunPyFetchResult:
    if search_result.raw_response is None:
        raise ValueError("Search result does not contain a raw response object for fetching.")

    cache_root = Path(cache_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    # Safety net: ensure diagnostics are captured even if the UI did not
    # configure logging (cache lives at <app_data>/sunpy_cache, log alongside).
    configure_fetch_logging(cache_root.parent)
    _ensure_event_loop()

    requested = _normalize_selected_rows(selected_rows, len(search_result.rows))
    if not requested:
        return SunPyFetchResult(paths=[], requested_count=0, failed_count=0, errors=[])

    downloaded_paths: list[str] = []
    errors: list[str] = []
    total = len(requested)
    processed_rows = 0
    row_template = str(cache_root / "{file}")
    priority = _is_priority_fetch(search_result)
    max_conn = _resolve_fetch_max_conn(priority=priority)
    max_batch_size = _resolve_fetch_batch_size(priority=priority)
    _logger.info(
        "fetch start: requested=%d cache=%s max_conn=%d batch_size=%d priority=%s",
        total,
        cache_root,
        max_conn,
        max_batch_size,
        priority,
    )

    def _mark_processed(delta: int):
        nonlocal processed_rows
        processed_rows += int(max(0, delta))
        if progress_cb is not None:
            label = "high-resolution frame" if priority else "selection"
            progress_cb(
                int(processed_rows * 100 / max(total, 1)),
                f"Downloaded {processed_rows}/{total} {label}(s)",
            )

    cancelled = False
    remaining_requested = list(requested)

    if _direct_vso_download_enabled(search_result):
        if progress_cb is not None:
            progress_cb(0, "Resolving direct VSO download URLs...")
        direct_items, unresolved_rows = _resolve_vso_direct_downloads(
            search_result,
            requested,
            row_template=row_template,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        if _is_cancelled(cancel_cb):
            cancelled = True
            _logger.info("fetch cancelled while resolving direct URLs")
        elif direct_items:
            direct_row_indexes = {item.row_index for item in direct_items}
            _logger.info(
                "direct VSO download path: resolved=%d unresolved=%d workers=%d",
                len(direct_items),
                len(unresolved_rows),
                _resolve_direct_download_workers(priority=priority),
            )
            direct_paths, direct_errors, direct_cancelled = _download_vso_direct_items(
                direct_items,
                cache_root,
                total_requested=total,
                base_completed=processed_rows,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
                priority=priority,
            )
            downloaded_paths.extend(direct_paths)
            errors.extend(direct_errors)
            processed_rows += len(direct_items)
            remaining_requested = [row_index for row_index in requested if row_index not in direct_row_indexes]
            cancelled = bool(direct_cancelled or _is_cancelled(cancel_cb))
            if progress_cb is not None:
                progress_cb(
                    int(processed_rows * 100 / max(total, 1)),
                    f"Direct download finished for {len(direct_items)}/{total} record(s).",
                )
        else:
            _logger.info("direct VSO download path unavailable; falling back to SunPy fetch")

    if cancelled:
        remaining_requested = []

    if remaining_requested and fido_client is None:
        fido_client = _import_fido()

    batches = _build_row_batches(search_result, remaining_requested, max_batch_size=max_batch_size)
    for batch_idx, batch in enumerate(batches, start=1):
        if _is_cancelled(cancel_cb):
            cancelled = True
            _logger.info("fetch cancelled before batch %d/%d", batch_idx, len(batches))
            break
        if progress_cb is not None:
            label = "high-resolution batch" if priority else "batch"
            progress_cb(
                int(processed_rows * 100 / max(total, 1)),
                (
                    f"Downloading {label} {batch_idx}/{len(batches)} "
                    f"({processed_rows}/{total} complete, up to {max_conn} connections)..."
                ),
            )
        _fetch_rows_batch_adaptive(
            search_result,
            batch,
            fido_client=fido_client,
            row_template=row_template,
            downloaded_paths=downloaded_paths,
            errors=errors,
            max_conn=max_conn,
            batch_max_conn=(max_conn if priority else 1),
            priority=priority,
            processed_cb=_mark_processed,
            cancel_cb=cancel_cb,
        )
        if _is_cancelled(cancel_cb):
            cancelled = True
            _logger.info("fetch cancelled after batch %d/%d", batch_idx, len(batches))
            break

    deduped_paths = _dedupe_preserve_order(downloaded_paths)
    failed_rows = _failed_rows_from_errors(errors)
    if cancelled:
        _logger.info("fetch cancelled: downloaded=%d/%d", len(deduped_paths), total)
    elif errors:
        _logger.warning(
            "fetch done: downloaded=%d/%d failed=%d first_error=%s",
            len(deduped_paths),
            total,
            len(errors),
            errors[0],
        )
    else:
        _logger.info("fetch done: downloaded=%d/%d failed=0", len(deduped_paths), total)

    return SunPyFetchResult(
        paths=deduped_paths,
        requested_count=total,
        failed_count=len(errors),
        errors=errors,
        failed_rows=failed_rows,
        cancelled=cancelled,
    )


def fetch_via_jsoc(
    search_result: SunPySearchResult,
    cache_dir: str | Path,
    selected_rows: Sequence[int] | None = None,
    *,
    email: str,
    cadence_seconds: float | int | None = None,
    process: dict[str, Any] | None = None,
    max_conn: int | None = None,
    progress_cb: Callable[[int, str], None] | None = None,
    byte_progress_cb: Callable[[Any], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    jsoc_client: Any | None = None,
    download_manager: Any | None = None,
) -> SunPyFetchResult:
    """Download SDO/AIA frames through the JSOC fast path.

    Resolves direct, compressed segment URLs from JSOC (built from the selected
    rows' time window + the query's wavelength/cadence) and transfers them with
    the shared byte-accurate :class:`DownloadManager`. This is the fast,
    fallback-capable alternative to the VSO :func:`fetch`; on any JSOC failure
    the caller should fall back to ``fetch`` so the user is never stranded.

    The download engine skips files already complete in ``cache_dir`` (JSOC
    filenames are deterministic per record), which is the persistent cross-
    session cache.
    """
    from src.Backend.download_manager import DownloadItem, DownloadManager
    from src.Backend.jsoc_client import export_urls

    spec = search_result.spec
    rows = search_result.rows
    requested = _normalize_selected_rows(selected_rows, len(rows))
    if requested:
        start = min(rows[i].start for i in requested)
        end = max(rows[i].end for i in requested)
    else:
        start, end = spec.start_dt, spec.end_dt
    if end <= start:
        end = start + _min_window_delta()

    cadence = cadence_seconds if cadence_seconds is not None else spec.sample_seconds
    is_hmi = str(getattr(spec, "instrument", "") or "").upper() == "HMI"

    if is_hmi:
        export = export_urls(
            start=start,
            end=end,
            product=str(spec.product or "magnetogram"),
            email=str(email),
            cadence_seconds=cadence,
            process=process,
            client=jsoc_client,
        )
    else:
        export = export_urls(
            start=start,
            end=end,
            wavelength_angstrom=(spec.wavelength_angstrom or 193.0),
            email=str(email),
            cadence_seconds=cadence,
            process=process,
            client=jsoc_client,
        )

    cache_root = Path(cache_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    configure_fetch_logging(cache_root.parent)

    # JSOC reports the SAME segment filename ("image_lev1.fits") for every
    # record, so naming local files after it makes all downloads collide on a
    # single path (only one file survives, the rest fail the rename). Derive a
    # unique, deterministic name from each record's timestamp instead.
    items: list[Any] = []
    used_names: set[str] = set()
    for index, entry in enumerate(export.urls):
        name = _jsoc_local_filename(entry, index, used_names)
        items.append(
            DownloadItem(
                url=entry.url,
                dest=cache_root / name,
                expected_size=entry.size,
                record_id=entry.record,
                label=entry.filename,
            )
        )
    _logger.info("JSOC fetch: %d url(s) resolved from %s", len(items), export.recordset)

    manager = download_manager or DownloadManager(
        max_concurrent=int(max_conn) if max_conn else _resolve_fetch_max_conn(priority=True),
        progress_interval=0.2,
    )

    def _on_aggregate(agg: Any) -> None:
        if byte_progress_cb is not None:
            byte_progress_cb(agg)
        if progress_cb is not None:
            done = int(getattr(agg, "files_done", 0) or 0)
            total = int(getattr(agg, "files_total", 0) or 0) or len(items)
            try:
                percent = int(agg.percent())
            except Exception:
                percent = 0
            progress_cb(percent, f"Downloaded {done}/{total} frame(s) via JSOC (fast path)")

    download = manager.download(items, progress_cb=_on_aggregate, cancel_cb=cancel_cb)
    return SunPyFetchResult(
        paths=list(download.paths),
        requested_count=len(items),
        failed_count=len(download.errors),
        errors=list(download.errors),
        failed_rows=[],
        cancelled=bool(download.cancelled),
    )


def _min_window_delta():
    from datetime import timedelta

    return timedelta(seconds=12)


def _jsoc_local_filename(entry: Any, index: int, used_names: set[str]) -> str:
    """Build a unique, deterministic local filename for a JSOC record.

    The export's ``filename`` is the generic segment name shared by every
    record, so we name files after the record id (which carries the unique
    T_REC + wavelength). Re-running the same query yields the same names, so the
    persistent cache still skips already-downloaded frames.
    """
    record = str(getattr(entry, "record", "") or "").strip()
    base = _sanitize_filename(record)
    if not base:
        base = _sanitize_filename(str(getattr(entry, "filename", "") or ""))
    if not base:
        base = f"jsoc_{index:04d}"
    if not base.lower().endswith((".fits", ".fts", ".fit")):
        base = f"{base}.fits"

    # Guard against two records sanitising to the same name.
    candidate = base
    suffix = 1
    while candidate in used_names:
        stem = base[:-5] if base.lower().endswith(".fits") else base
        candidate = f"{stem}_{suffix}.fits"
        suffix += 1
    used_names.add(candidate)
    return candidate


def load_downloaded(
    paths: Iterable[str | Path],
    data_kind: str,
    *,
    map_loader: Callable[..., Any] | None = None,
    timeseries_loader: Callable[..., Any] | None = None,
) -> SunPyLoadResult:
    normalized = [str(Path(p).expanduser().resolve()) for p in paths if str(p).strip()]
    if not normalized:
        raise ValueError("No files were provided for SunPy loading.")

    if data_kind == DATA_KIND_MAP:
        if map_loader is None:
            map_loader = _import_map_loader()
        loaded = _load_maps(map_loader, normalized)
        maps = _extract_maps(loaded)
        meta = {
            "n_frames": len(maps),
            "observatory": _safe_str(getattr(maps[0], "observatory", "")) if maps else "",
            "instrument": _safe_str(getattr(maps[0], "instrument", "")) if maps else "",
            "detector": _safe_str(getattr(maps[0], "detector", "")) if maps else "",
            "wavelength": _safe_str(getattr(maps[0], "wavelength", "")) if maps else "",
            "date": _safe_str(getattr(maps[0], "date", "")) if maps else "",
        }
        return SunPyLoadResult(
            data_kind=DATA_KIND_MAP,
            paths=normalized,
            maps_or_timeseries=loaded,
            metadata=meta,
        )

    if data_kind == DATA_KIND_TIMESERIES:
        if timeseries_loader is None:
            timeseries_loader = _import_timeseries_loader()
        loaded = _load_timeseries(timeseries_loader, normalized)
        meta = {"n_files": len(normalized)}
        to_dataframe = getattr(loaded, "to_dataframe", None)
        if callable(to_dataframe):
            try:
                frame = to_dataframe()
                meta["columns"] = [str(c) for c in getattr(frame, "columns", [])]
                meta["n_samples"] = int(len(frame))
            except Exception:
                pass
        return SunPyLoadResult(
            data_kind=DATA_KIND_TIMESERIES,
            paths=normalized,
            maps_or_timeseries=loaded,
            metadata=meta,
        )

    raise ValueError(f"Unsupported data_kind '{data_kind}'. Expected '{DATA_KIND_MAP}' or '{DATA_KIND_TIMESERIES}'.")


def normalize_query_spec(spec: SunPyQuerySpec) -> SunPyQuerySpec:
    start_dt = _naive_utc(spec.start_dt)
    end_dt = _naive_utc(spec.end_dt)
    if end_dt <= start_dt:
        raise ValueError("End time must be after start time.")

    return SunPyQuerySpec(
        start_dt=start_dt,
        end_dt=end_dt,
        spacecraft=str(spec.spacecraft or "").strip().upper(),
        instrument=str(spec.instrument or "").strip().upper(),
        wavelength_angstrom=spec.wavelength_angstrom,
        detector=(str(spec.detector).strip().upper() if spec.detector else None),
        satellite_number=(int(spec.satellite_number) if spec.satellite_number is not None else None),
        sample_seconds=(float(spec.sample_seconds) if spec.sample_seconds is not None else None),
        resolution=_normalize_resolution_value(spec.resolution),
        max_records=max(1, int(spec.max_records or 1)),
        product=(str(spec.product).strip().lower() if spec.product else None),
    )


def _normalize_resolution_value(value: float | str | None) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return text
    try:
        return float(value)
    except Exception:
        return value


def resolve_registry_entry(spec: SunPyQuerySpec) -> InstrumentRegistryEntry:
    spacecraft = str(spec.spacecraft or "").strip().upper()
    instrument = str(spec.instrument or "").strip().upper()
    detector = str(spec.detector or "").strip().upper()

    for entry in INSTRUMENT_REGISTRY:
        if entry.spacecraft != spacecraft:
            continue
        if entry.instrument != instrument:
            continue
        if entry.detector and entry.detector != detector:
            continue
        return entry

    if spacecraft == "SOHO" and instrument == "LASCO" and not detector:
        raise ValueError("SOHO/LASCO queries must include detector 'C2' or 'C3'.")
    raise ValueError(f"Unsupported SunPy query target: spacecraft={spacecraft}, instrument={instrument}, detector={detector or '-'}")


def _normalize_selected_rows(selected_rows: Sequence[int] | None, n_rows: int) -> list[int]:
    if selected_rows is None:
        return list(range(n_rows))

    out: list[int] = []
    seen = set()
    for idx in selected_rows:
        i = int(idx)
        if i < 0 or i >= n_rows or i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def _query_slice_for_row(search_result: SunPySearchResult, row_index: int) -> Any:
    block_idx, local_idx = search_result.row_index_map[row_index]
    block = search_result.raw_response[block_idx]
    try:
        return block[local_idx : local_idx + 1]
    except Exception:
        return block[local_idx]


def _query_slice_for_rows(search_result: SunPySearchResult, row_indexes: Sequence[int]) -> Any | None:
    if not row_indexes:
        return None
    if len(row_indexes) == 1:
        return _query_slice_for_row(search_result, row_indexes[0])

    block_idx, first_local = search_result.row_index_map[row_indexes[0]]
    locals_sorted = [first_local]

    for row_index in row_indexes[1:]:
        bidx, lidx = search_result.row_index_map[row_index]
        if bidx != block_idx:
            return None
        locals_sorted.append(lidx)

    locals_sorted = sorted(locals_sorted)
    if not _is_contiguous(locals_sorted):
        return None

    block = search_result.raw_response[block_idx]
    start = locals_sorted[0]
    end = locals_sorted[-1]
    try:
        selection = block[start : end + 1]
    except Exception:
        return None

    return selection if _safe_len(selection) > 0 else None


def _build_row_batches(
    search_result: SunPySearchResult,
    requested: Sequence[int],
    *,
    max_batch_size: int,
) -> list[list[int]]:
    ordered = sorted(
        ((row_index, *search_result.row_index_map[row_index]) for row_index in requested),
        key=lambda x: (x[1], x[2]),
    )

    batches: list[list[int]] = []
    current: list[int] = []
    current_block = None
    current_local = None
    size_limit = max(1, int(max_batch_size))

    for row_index, block_idx, local_idx in ordered:
        if not current:
            current = [row_index]
            current_block = block_idx
            current_local = local_idx
            continue

        can_extend = (
            block_idx == current_block
            and local_idx == int(current_local) + 1
            and len(current) < size_limit
        )
        if can_extend:
            current.append(row_index)
            current_local = local_idx
            continue

        batches.append(current)
        current = [row_index]
        current_block = block_idx
        current_local = local_idx

    if current:
        batches.append(current)

    return batches


def _fetch_single_row(
    search_result: SunPySearchResult,
    row_index: int,
    *,
    fido_client: Any,
    row_template: str,
    downloaded_paths: list[str],
    errors: list[str],
    max_conn: int,
    priority: bool = False,
    cancel_cb: Callable[[], bool] | None = None,
):
    if _is_cancelled(cancel_cb):
        return
    query_slice = _query_slice_for_row(search_result, row_index)
    row_label = _row_log_label(search_result, row_index)
    _logger.info("fetch row start: row=%d %s priority=%s", row_index + 1, row_label, priority)
    try:
        fetched = _fetch_row_with_runtime_guards(
            fido_client,
            query_slice,
            path_template=row_template,
            max_conn=max_conn,
            priority=priority,
            retry_count=_resolve_fetch_retry_count(priority=priority),
            conn_candidates=_row_fetch_connection_candidates(max_conn, priority=priority),
        )
        row_paths = _extract_fetch_paths(fetched)
        if not row_paths:
            manual_paths = _download_from_fetch_errors(
                fetched,
                row_template=row_template,
                priority=priority,
                cancel_cb=cancel_cb,
            )
            if not manual_paths:
                # Final fallback: synchronous urllib download from the record URL.
                manual_paths = _download_from_row_record(
                    search_result,
                    row_index,
                    row_template=row_template,
                    priority=priority,
                    cancel_cb=cancel_cb,
                )
            if manual_paths:
                downloaded_paths.extend(manual_paths)
                _logger.info("fetch row recovered: row=%d paths=%d", row_index + 1, len(manual_paths))
                return
            fetch_errors = _extract_fetch_errors(fetched)
            if fetch_errors:
                errors.append(f"Row {row_index + 1}: {fetch_errors[0]}")
            else:
                errors.append(f"Row {row_index + 1}: fetch returned no files.")
        else:
            downloaded_paths.extend(row_paths)
            _logger.info("fetch row done: row=%d paths=%d", row_index + 1, len(row_paths))
    except Exception as exc:
        _logger.warning("fetch row failed: row=%d %s error=%s", row_index + 1, row_label, exc)
        errors.append(f"Row {row_index + 1}: {exc}")


def _fetch_rows_batch_adaptive(
    search_result: SunPySearchResult,
    batch: Sequence[int],
    *,
    fido_client: Any,
    row_template: str,
    downloaded_paths: list[str],
    errors: list[str],
    max_conn: int,
    batch_max_conn: int,
    priority: bool,
    processed_cb: Callable[[int], None],
    cancel_cb: Callable[[], bool] | None = None,
):
    if not batch:
        return
    if _is_cancelled(cancel_cb):
        return

    if len(batch) <= 1:
        row_index = int(batch[0])
        _fetch_single_row(
            search_result,
            row_index,
            fido_client=fido_client,
            row_template=row_template,
            downloaded_paths=downloaded_paths,
            errors=errors,
            max_conn=max_conn,
            priority=priority,
            cancel_cb=cancel_cb,
        )
        processed_cb(1)
        return

    query_slice = _query_slice_for_rows(search_result, batch)
    if query_slice is None:
        for row_index in batch:
            if _is_cancelled(cancel_cb):
                return
            _fetch_single_row(
                search_result,
                int(row_index),
                fido_client=fido_client,
                row_template=row_template,
                downloaded_paths=downloaded_paths,
                errors=errors,
                max_conn=max_conn,
                priority=priority,
                cancel_cb=cancel_cb,
            )
            processed_cb(1)
        return

    _logger.info(
        "fetch batch start: rows=%s size=%d max_conn=%d priority=%s",
        ",".join(str(int(row_index) + 1) for row_index in batch),
        len(batch),
        max(1, int(batch_max_conn)),
        priority,
    )
    try:
        fetched = _fetch_row_with_runtime_guards(
            fido_client,
            query_slice,
            path_template=row_template,
            max_conn=max(1, int(batch_max_conn)),
            priority=priority,
            retry_count=1,
            conn_candidates=[max(1, int(batch_max_conn))],
        )
        row_paths = _extract_fetch_paths(fetched)
        if row_paths:
            downloaded_paths.extend(row_paths)
            _logger.info("fetch batch done: size=%d paths=%d", len(batch), len(row_paths))
            processed_cb(len(batch))
            return
        fetch_errors = _extract_fetch_errors(fetched)
        if _should_fast_fail_timeout_batch(
            fetched,
            fetch_errors=fetch_errors,
            batch_size=len(batch),
        ):
            reason = _format_fast_fail_timeout_reason(fetch_errors)
            for row_index in batch:
                errors.append(f"Row {int(row_index) + 1}: {reason}")
            _logger.warning("fetch batch fast-failed: size=%d reason=%s", len(batch), reason)
            processed_cb(len(batch))
            return
    except Exception as exc:
        _logger.warning("fetch batch failed, splitting: size=%d error=%s", len(batch), exc)

    # Adaptive fallback: split failed batch into halves before trying per-row.
    midpoint = len(batch) // 2
    if midpoint <= 0 or midpoint >= len(batch):
        for row_index in batch:
            if _is_cancelled(cancel_cb):
                return
            _fetch_single_row(
                search_result,
                int(row_index),
                fido_client=fido_client,
                row_template=row_template,
                downloaded_paths=downloaded_paths,
                errors=errors,
                max_conn=max_conn,
                priority=priority,
                cancel_cb=cancel_cb,
            )
            processed_cb(1)
        return

    left = list(batch[:midpoint])
    right = list(batch[midpoint:])
    _fetch_rows_batch_adaptive(
        search_result,
        left,
        fido_client=fido_client,
        row_template=row_template,
        downloaded_paths=downloaded_paths,
        errors=errors,
        max_conn=max_conn,
        batch_max_conn=max(1, int(batch_max_conn) // 2),
        priority=priority,
        processed_cb=processed_cb,
        cancel_cb=cancel_cb,
    )
    _fetch_rows_batch_adaptive(
        search_result,
        right,
        fido_client=fido_client,
        row_template=row_template,
        downloaded_paths=downloaded_paths,
        errors=errors,
        max_conn=max_conn,
        batch_max_conn=max(1, int(batch_max_conn) // 2),
        priority=priority,
        processed_cb=processed_cb,
        cancel_cb=cancel_cb,
    )


def _is_cancelled(cancel_cb: Callable[[], bool] | None) -> bool:
    if cancel_cb is None:
        return False
    try:
        return bool(cancel_cb())
    except Exception:
        return False


def _is_priority_fetch(search_result: SunPySearchResult) -> bool:
    spec = getattr(search_result, "spec", None)
    if spec is None:
        return False
    return bool(
        str(getattr(spec, "spacecraft", "") or "").upper() == "SDO"
        and str(getattr(spec, "instrument", "") or "").upper() == "AIA"
        and getattr(spec, "resolution", None) is not None
    )


def _row_log_label(search_result: SunPySearchResult, row_index: int) -> str:
    try:
        row = search_result.rows[row_index]
    except Exception:
        return ""
    return (
        f"start={getattr(row, 'start', '')} "
        f"source={getattr(row, 'source', '')} "
        f"instrument={getattr(row, 'instrument', '')} "
        f"fileid={getattr(row, 'fileid', '')}"
    )


def _row_fetch_connection_candidates(max_conn: int, *, priority: bool = False) -> list[int]:
    max_conn = max(1, int(max_conn))
    if priority:
        return _dedupe_ints([max_conn, 1])
    return _dedupe_ints([min(4, max_conn), 2, 1])


def _direct_vso_download_enabled(search_result: SunPySearchResult) -> bool:
    raw = str(os.environ.get("ECALLISTO_SUNPY_DIRECT_DOWNLOAD", "")).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if search_result.data_kind != DATA_KIND_MAP:
        return False
    try:
        for block in search_result.raw_response:
            if getattr(block, "client", None) is not None:
                return True
    except Exception:
        return False
    return False


def _resolve_vso_direct_downloads(
    search_result: SunPySearchResult,
    row_indexes: Sequence[int],
    *,
    row_template: str,
    progress_cb: Callable[[int, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> tuple[list[_DirectDownloadItem], list[int]]:
    requested = _normalize_selected_rows(row_indexes, len(search_result.rows))
    if not requested:
        return [], []

    fileid_to_rows: dict[str, list[int]] = {}
    for row_index in requested:
        try:
            fileid = str(search_result.rows[row_index].fileid)
        except Exception:
            fileid = ""
        if fileid:
            fileid_to_rows.setdefault(fileid, []).append(row_index)

    resolved: list[_DirectDownloadItem] = []
    resolved_rows: set[int] = set()
    priority = _is_priority_fetch(search_result)
    site_order = _resolve_direct_vso_sites(priority=priority)
    batches = _build_row_batches(
        search_result,
        requested,
        max_batch_size=_resolve_direct_url_batch_size(),
    )
    for batch_idx, batch in enumerate(batches, start=1):
        if _is_cancelled(cancel_cb):
            break
        if progress_cb is not None:
            progress_cb(
                int((len(resolved_rows) * 100) / max(len(requested), 1)),
                f"Resolving direct download URLs {batch_idx}/{len(batches)}...",
            )
        query_slice = _query_slice_for_rows(search_result, batch)
        if query_slice is None:
            continue
        records_by_fileid: dict[str, dict[str, Any]] = {}
        try:
            for site in site_order:
                try:
                    records = _resolve_vso_url_records(query_slice, row_template=row_template, site=site)
                except Exception as exc:
                    _logger.warning(
                        "direct VSO URL resolution failed for site=%s batch size=%d: %s",
                        site or "<default>",
                        len(batch),
                        exc,
                    )
                    continue
                for fileid, url, path_hint in records:
                    if not fileid:
                        fileid = url
                    entry = records_by_fileid.setdefault(
                        str(fileid),
                        {"fileid": str(fileid), "path_hint": str(path_hint or ""), "urls": []},
                    )
                    if path_hint and not entry.get("path_hint"):
                        entry["path_hint"] = str(path_hint)
                    if url and url not in entry["urls"]:
                        entry["urls"].append(str(url))
        except Exception as exc:
            _logger.warning("direct VSO URL resolution failed for batch size=%d: %s", len(batch), exc)
            continue

        batch_unassigned = [int(row_index) for row_index in batch if int(row_index) not in resolved_rows]
        for entry in records_by_fileid.values():
            if _is_cancelled(cancel_cb):
                break
            fileid = str(entry.get("fileid", ""))
            urls = tuple(str(url) for url in entry.get("urls", []) if str(url).strip())
            if not urls:
                continue
            row_index: int | None = None
            candidates = fileid_to_rows.get(str(fileid), [])
            while candidates:
                candidate = int(candidates.pop(0))
                if candidate not in resolved_rows:
                    row_index = candidate
                    break
            if row_index is None and batch_unassigned:
                row_index = int(batch_unassigned.pop(0))
            if row_index is None:
                continue
            try:
                expected_bytes = _parse_size_bytes(search_result.rows[row_index].size)
            except Exception:
                expected_bytes = None
            resolved.append(
                _DirectDownloadItem(
                    row_index=row_index,
                    url=str(urls[0]),
                    fileid=str(fileid or search_result.rows[row_index].fileid),
                    path_hint=str(entry.get("path_hint", "") or ""),
                    expected_bytes=expected_bytes,
                    urls=urls,
                )
            )
            resolved_rows.add(row_index)

    unresolved = [row_index for row_index in requested if row_index not in resolved_rows]
    return resolved, unresolved


def _resolve_vso_url_records(query_slice: Any, *, row_template: str, site: str | None = None) -> list[tuple[str, str, str]]:
    client = getattr(query_slice, "client", None)
    if client is None:
        return []
    info: dict[str, Any] = {}
    if site:
        info["site"] = str(site)
    data_request = client.make_getdatarequest(query_slice, None, info)
    response_type = client.api.get_type("VSO:VSOGetDataResponse")
    response = response_type(client.api.service.GetData(data_request))
    by_fileid = client.by_fileid(query_slice)

    out: list[tuple[str, str, str]] = []
    for data_response in getattr(response, "getdataresponseitem", []) or []:
        status_code = _vso_response_status_code(data_response)
        if status_code != "200":
            raise RuntimeError(f"VSO returned status {status_code or '?'} while resolving download URLs.")
        method_types = list(getattr(getattr(data_response, "method", None), "methodtype", []) or [])
        method = str(method_types[0] if method_types else "")
        if method and not method.upper().startswith("URL"):
            raise RuntimeError(f"VSO returned unsupported download method '{method}'.")
        data_items = getattr(getattr(data_response, "getdataitem", None), "dataitem", []) or []
        for data_item in data_items:
            url = str(getattr(data_item, "url", "") or "").strip()
            if not url:
                continue
            fileids = list(getattr(getattr(data_item, "fileiditem", None), "fileid", []) or [])
            fileid = str(fileids[0] if fileids else "").strip()
            path_hint = ""
            query_row = by_fileid.get(fileid) if fileid else None
            if query_row is not None:
                try:
                    path_hint = str(client.mk_filename(str(row_template), query_row, None, url))
                except Exception:
                    path_hint = ""
            out.append((fileid, url, path_hint))
    return out


def _vso_response_status_code(data_response: Any) -> str:
    version_ranges = (
        ("0.8", (5, 8)),
        ("0.7", (1, 4)),
        ("0.6", (0, 3)),
    )
    status = str(getattr(data_response, "status", "") or "")
    if not status:
        return "200"
    for version, (start, stop) in version_ranges:
        try:
            if str(getattr(data_response, version, "0.6")) >= version:
                return status[start:stop] or "200"
        except Exception:
            continue
    return status


def _download_vso_direct_items(
    items: Sequence[_DirectDownloadItem],
    cache_root: Path,
    *,
    total_requested: int,
    base_completed: int,
    progress_cb: Callable[[int, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    priority: bool = False,
) -> tuple[list[str], list[str], bool]:
    if not items:
        return [], [], False

    workers = _resolve_direct_download_workers(priority=priority)
    paths: list[str] = []
    errors: list[str] = []
    fractions: dict[int, float] = {int(item.row_index): 0.0 for item in items}
    lock = Lock()

    def report(item: _DirectDownloadItem, fraction: float, message: str) -> None:
        if progress_cb is None:
            return
        with lock:
            fractions[int(item.row_index)] = max(0.0, min(1.0, float(fraction)))
            done_equivalent = base_completed + sum(fractions.values())
            value = int(done_equivalent * 100 / max(total_requested, 1))
        progress_cb(max(0, min(100, value)), message)

    def worker(item: _DirectDownloadItem) -> tuple[_DirectDownloadItem, str | None, str | None]:
        if _is_cancelled(cancel_cb):
            return item, None, "cancelled"
        try:
            path = _download_direct_item(
                item,
                cache_root,
                priority=priority,
                cancel_cb=cancel_cb,
                progress_cb=lambda fraction, text: report(item, fraction, text),
            )
            return item, path, None
        except Exception as exc:
            return item, None, str(exc)

    cancelled = False
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aia-direct-download") as executor:
        futures = [executor.submit(worker, item) for item in items]
        for future in as_completed(futures):
            item, path, error = future.result()
            if path:
                paths.append(path)
                report(item, 1.0, f"Downloaded {len(paths)}/{len(items)} direct AIA file(s).")
                _logger.info("direct VSO download done: row=%d path=%s", item.row_index + 1, path)
            elif error == "cancelled" or _is_cancelled(cancel_cb):
                cancelled = True
                report(item, 1.0, "Direct download cancelled.")
                _logger.info("direct VSO download cancelled: row=%d", item.row_index + 1)
            else:
                errors.append(f"Row {item.row_index + 1}: direct download failed: {error}")
                report(item, 1.0, f"Direct download failed for row {item.row_index + 1}; continuing...")
                _logger.warning("direct VSO download failed: row=%d error=%s", item.row_index + 1, error)
            if _is_cancelled(cancel_cb):
                cancelled = True
    return _dedupe_preserve_order(paths), errors, cancelled


def _download_direct_item(
    item: _DirectDownloadItem,
    cache_root: Path,
    *,
    priority: bool = False,
    cancel_cb: Callable[[], bool] | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> str:
    target = _direct_download_target_path(item, cache_root)
    if target.exists() and target.stat().st_size > 0:
        if progress_cb is not None:
            progress_cb(1.0, f"Using cached AIA file {target.name}")
        return str(target.resolve())

    candidate_urls = tuple(_dedupe_preserve_order([*(item.urls or ()), item.url]))
    if not candidate_urls:
        raise RuntimeError("No direct download URLs were resolved for this record.")
    last_error: Exception | None = None
    for url_index, url in enumerate(candidate_urls, start=1):
        if _is_cancelled(cancel_cb):
            raise RuntimeError("Download cancelled.")
        try:
            if progress_cb is not None and len(candidate_urls) > 1:
                progress_cb(0.01, f"Trying mirror {url_index}/{len(candidate_urls)} for {target.name}")
            return _download_direct_url_to_path(
                str(url),
                target,
                item,
                priority=priority,
                cancel_cb=cancel_cb,
                progress_cb=progress_cb,
            )
        except Exception as exc:
            last_error = exc
            _logger.warning(
                "direct VSO mirror failed: row=%d mirror=%d/%d url=%s error=%s",
                item.row_index + 1,
                url_index,
                len(candidate_urls),
                url,
                exc,
            )
            if progress_cb is not None and url_index < len(candidate_urls):
                progress_cb(0.01, f"Mirror {url_index}/{len(candidate_urls)} failed for {target.name}; retrying...")
    raise RuntimeError(str(last_error or "direct download failed"))


def _download_direct_url_to_path(
    url: str,
    target: Path,
    item: _DirectDownloadItem,
    *,
    priority: bool = False,
    cancel_cb: Callable[[], bool] | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f"{target.name}.part")
    timeout_seconds = _resolve_manual_fetch_timeout_seconds(priority=priority)
    expected_bytes = int(item.expected_bytes or 0)
    last_report = 0.0
    downloaded = 0

    if progress_cb is not None:
        progress_cb(0.01, f"Starting {target.name}")
    try:
        request = Request(url, headers={"User-Agent": "eCallisto-SunPy/1.0"})
        with urlopen(request, timeout=float(timeout_seconds)) as response:
            content_length = _response_content_length(response)
            if content_length:
                expected_bytes = content_length
            with open(tmp_path, "wb") as handle:
                while True:
                    if _is_cancelled(cancel_cb):
                        raise RuntimeError("Download cancelled.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if progress_cb is not None and (now - last_report >= 0.35 or downloaded >= expected_bytes > 0):
                        last_report = now
                        if expected_bytes > 0:
                            fraction = min(0.98, max(0.01, downloaded / expected_bytes))
                            progress_cb(fraction, f"Downloading {target.name}: {_format_bytes(downloaded)} / {_format_bytes(expected_bytes)}")
                        else:
                            fraction = min(0.95, 0.01 + downloaded / max(64 * 1024 * 1024, downloaded + 1))
                            progress_cb(fraction, f"Downloading {target.name}: {_format_bytes(downloaded)}")
        if not tmp_path.exists() or tmp_path.stat().st_size <= 0:
            raise RuntimeError("download produced an empty file")
        tmp_path.replace(target)
        if progress_cb is not None:
            progress_cb(1.0, f"Finished {target.name}")
        return str(target.resolve())
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _direct_download_target_path(item: _DirectDownloadItem, cache_root: Path) -> Path:
    hint = str(item.path_hint or "").strip()
    if hint:
        try:
            path = Path(hint).expanduser()
            if not path.is_absolute():
                path = cache_root / path
        except Exception:
            path = cache_root / _guess_filename_from_url(item.url)
    else:
        path = cache_root / _guess_filename_from_url(item.url)
    if not path.suffix:
        path = path.with_suffix(".fits")
    return path.resolve()


def _response_content_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("Content-Length") or headers.get("content-length")
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        return None


def _parse_size_bytes(value: Any) -> int | None:
    text = _safe_str(value).strip()
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b(?:yte)?|bytes?)?", text, flags=re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    scale = 1
    if unit.startswith("k"):
        scale = 1024
    elif unit.startswith("m"):
        scale = 1024**2
    elif unit.startswith("g"):
        scale = 1024**3
    elif unit.startswith("t"):
        scale = 1024**4
    return int(number * scale)


def _format_bytes(value: int | float) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} GB"


def _guess_filename_from_url(url: str) -> str:
    parsed_url = urlparse(str(url or ""))
    query = parse_qs(parsed_url.query)
    for key in ("record", "Record", "file", "filename"):
        values = query.get(key) or []
        if values:
            name = _sanitize_filename(values[0].replace(";", "_"))
            if name:
                if not name.lower().endswith((".fits", ".fit", ".fts", ".fits.gz", ".fit.gz", ".fts.gz")):
                    name = f"{name}.fits"
                return name
    basename = _sanitize_filename(Path(parsed_url.path).name)
    if not basename:
        basename = "sunpy_download.fits"
    if "." not in basename:
        basename = f"{basename}.fits"
    return basename


def _failed_rows_from_errors(errors: Sequence[str]) -> list[int]:
    """Recover the 0-based indices of failed rows from the error messages.

    Every failure is recorded with a consistent ``"Row N: ..."`` (1-based)
    prefix, so the failed rows can be derived without threading extra state
    through the recursive fetch helpers.
    """
    out: list[int] = []
    seen: set[int] = set()
    for message in errors:
        match = re.match(r"\s*Row\s+(\d+)\s*:", str(message))
        if not match:
            continue
        idx = int(match.group(1)) - 1
        if idx >= 0 and idx not in seen:
            seen.add(idx)
            out.append(idx)
    return sorted(out)


def _is_contiguous(values: Sequence[int]) -> bool:
    if not values:
        return False
    return all(values[idx] == values[0] + idx for idx in range(len(values)))


def _normalize_search_rows(
    raw_response: Any,
    spec: SunPyQuerySpec,
    *,
    max_records: int,
) -> tuple[list[SunPySearchRow], list[tuple[int, int]]]:
    rows: list[SunPySearchRow] = []
    row_index_map: list[tuple[int, int]] = []

    for block_index, block in enumerate(raw_response):
        n_rows = _safe_len(block)
        for local_index in range(n_rows):
            row = block[local_index]
            start = _extract_row_datetime(row, keys=("Start Time", "start_time", "time_start", "Time"))
            end = _extract_row_datetime(row, keys=("End Time", "end_time", "time_end", "Time"))
            if start is None:
                start = spec.start_dt
            if end is None:
                end = spec.end_dt

            source = _extract_row_text(row, keys=("Source", "source", "Observatory", "obs_observatory"))
            instrument = _extract_row_text(row, keys=("Instrument", "instrument", "obs_instrument"))
            provider = _extract_row_text(row, keys=("Provider", "provider", "Physobs", "physobs"))
            fileid = _extract_row_text(
                row,
                keys=(
                    "fileid",
                    "FileID",
                    "file_id",
                    "URL",
                    "url",
                    "Filename",
                    "filename",
                    "Record",
                    "record",
                ),
            )
            size = _extract_row_text(row, keys=("Size", "size", "File Size", "filesize"))

            rows.append(
                SunPySearchRow(
                    start=start,
                    end=end,
                    source=source or spec.spacecraft,
                    instrument=instrument or spec.instrument,
                    provider=provider or "",
                    fileid=fileid or f"{spec.spacecraft}/{spec.instrument}/{start.isoformat()}",
                    size=size,
                    selected=True,
                )
            )
            row_index_map.append((block_index, local_index))

            if max_records > 0 and len(rows) >= max_records:
                return rows, row_index_map

    return rows, row_index_map


def _extract_row_text(row: Any, keys: Sequence[str]) -> str:
    for key in keys:
        value = _row_get(row, key)
        if value is None:
            continue
        text = _safe_str(value).strip()
        if text:
            return text
    return ""


def _extract_row_datetime(row: Any, keys: Sequence[str]) -> datetime | None:
    for key in keys:
        value = _row_get(row, key)
        dt = _as_datetime(value)
        if dt is not None:
            return dt
    return None


def _row_get(row: Any, key: str) -> Any | None:
    if row is None:
        return None

    candidates = {key, key.lower(), key.upper(), key.replace(" ", "_"), key.replace(" ", "").lower()}
    for candidate in candidates:
        try:
            value = row[candidate]
            return value
        except Exception:
            continue

    keys_attr = getattr(row, "keys", None)
    if callable(keys_attr):
        try:
            for existing in keys_attr():
                if str(existing).strip().lower() == key.strip().lower():
                    return row[existing]
        except Exception:
            pass
    return None


def _resolve_attr_and_units(attrs_module: Any | None, units_module: Any | None) -> tuple[Any, Any]:
    if attrs_module is not None and units_module is not None:
        return attrs_module, units_module

    try:
        from sunpy.net import attrs as sunpy_attrs
        import astropy.units as astro_units
    except Exception as exc:
        _logger.error("Failed to import sunpy.net.attrs / astropy.units", exc_info=True)
        raise RuntimeError(_format_sunpy_dependency_error(exc)) from exc
    _configure_sunpy_logging()
    return sunpy_attrs, astro_units


def _import_fido() -> Any:
    try:
        from sunpy.net import Fido
    except Exception as exc:
        _logger.error("Failed to import sunpy.net.Fido", exc_info=True)
        raise RuntimeError(_format_sunpy_dependency_error(exc)) from exc
    _configure_sunpy_logging()
    return Fido


def _import_map_loader() -> Callable[..., Any]:
    try:
        from sunpy.map import Map
    except Exception as exc:
        _logger.error("Failed to import sunpy.map.Map", exc_info=True)
        raise RuntimeError(_format_sunpy_dependency_error(exc)) from exc
    _configure_sunpy_logging()
    return Map


def _import_timeseries_loader() -> Callable[..., Any]:
    try:
        from sunpy.timeseries import TimeSeries
    except Exception as exc:
        _logger.error("Failed to import sunpy.timeseries.TimeSeries", exc_info=True)
        raise RuntimeError(_format_sunpy_dependency_error(exc)) from exc
    _configure_sunpy_logging()
    return TimeSeries


def _fetch_row_with_runtime_guards(
    fido_client: Any,
    query_slice: Any,
    path_template: str,
    *,
    max_conn: int,
    priority: bool = False,
    retry_count: int = 1,
    conn_candidates: Sequence[int] | None = None,
):
    if conn_candidates is None:
        attempts = [int(max_conn), 6, 4, 2, 1]
    else:
        attempts = list(conn_candidates) + [int(max_conn), 1]
    dedup_attempts = _dedupe_ints([value for value in attempts if value > 0])
    retries = max(1, int(retry_count))
    backoff_seconds = _resolve_fetch_retry_backoff_seconds()
    last_result: Any = None
    last_exception: Exception | None = None

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"This download has been started in a thread which is not the main thread.*",
            category=UserWarning,
        )
        for conn in dedup_attempts:
            for attempt_idx in range(retries):
                try:
                    result = _fetch_once(
                        fido_client,
                        query_slice,
                        path_template=path_template,
                        max_conn=conn,
                        priority=priority,
                    )
                    last_result = result
                    if _extract_fetch_paths(result):
                        return result
                    fetch_errors = _extract_fetch_errors(result)
                    if not fetch_errors:
                        return result
                    if attempt_idx + 1 < retries and _is_retryable_fetch_errors(fetch_errors):
                        time.sleep(backoff_seconds * (attempt_idx + 1))
                        continue
                    break
                except Exception as exc:
                    last_exception = exc
                    if attempt_idx + 1 < retries:
                        time.sleep(backoff_seconds * (attempt_idx + 1))
                        continue
                    break

        if not priority:
            for attempt_idx in range(retries):
                try:
                    result = _fetch_once(
                        fido_client,
                        query_slice,
                        path_template=path_template,
                        max_conn=None,
                        priority=priority,
                    )
                    last_result = result
                    if _extract_fetch_paths(result):
                        return result
                    fetch_errors = _extract_fetch_errors(result)
                    if not fetch_errors:
                        return result
                    if attempt_idx + 1 < retries and _is_retryable_fetch_errors(fetch_errors):
                        time.sleep(backoff_seconds * (attempt_idx + 1))
                        continue
                    break
                except Exception as exc:
                    last_exception = exc
                    if attempt_idx + 1 < retries:
                        time.sleep(backoff_seconds * (attempt_idx + 1))
                        continue
                    break

    if last_result is not None:
        return last_result
    if last_exception is not None:
        raise last_exception
    raise RuntimeError("SunPy fetch failed without a response.")


def _fetch_once(
    fido_client: Any,
    query_slice: Any,
    *,
    path_template: str,
    max_conn: int | None,
    priority: bool = False,
):
    downloader = _build_parfive_downloader(max_conn=max_conn, priority=priority)
    kwargs_candidates: list[dict[str, Any]] = []

    if max_conn is not None:
        if downloader is not None:
            kwargs_candidates.append(
                {
                    "path": path_template,
                    "progress": False,
                    "max_conn": int(max_conn),
                    "downloader": downloader,
                }
            )
        kwargs_candidates.append({"path": path_template, "progress": False, "max_conn": int(max_conn)})
    if max_conn is None and downloader is not None:
        kwargs_candidates.append({"path": path_template, "progress": False, "downloader": downloader})
    kwargs_candidates.append({"path": path_template, "progress": False})
    kwargs_candidates.append({"path": path_template})

    last_type_error: TypeError | None = None
    for kwargs in kwargs_candidates:
        try:
            return fido_client.fetch(query_slice, **kwargs)
        except TypeError as exc:
            last_type_error = exc
            continue

    if last_type_error is not None:
        try:
            return fido_client.fetch(query_slice, path_template)
        except TypeError:
            return fido_client.fetch(query_slice, path=path_template)
    return fido_client.fetch(query_slice, path=path_template)


def _ensure_event_loop() -> None:
    """Guarantee the current thread has a usable asyncio event loop.

    parfive runs downloads via asyncio. When ``fetch`` executes inside a
    ``QThread`` (a non-main thread) there may be no current event loop, which
    is fragile and has been observed to break downloads in frozen Windows
    builds. Creating one here is harmless when parfive manages its own loop.
    """
    # A loop already running in this thread is fine.
    try:
        asyncio.get_running_loop()
        return
    except RuntimeError:
        pass

    # Inspect any existing (non-running) loop without surfacing the Py3.12
    # get_event_loop() DeprecationWarning.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop is not None and not loop.is_closed():
            return
    except Exception:
        pass

    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
        _logger.info("Created asyncio event loop for thread %s", _current_thread_name())
    except Exception:
        _logger.warning("Could not create asyncio event loop for this thread", exc_info=True)


def _current_thread_name() -> str:
    try:
        import threading

        return threading.current_thread().name
    except Exception:
        return "?"


def _build_parfive_downloader(*, max_conn: int | None, priority: bool = False) -> Any | None:
    try:
        from aiohttp import ClientTimeout
        from parfive import Downloader
        from parfive.config import SessionConfig
    except Exception:
        _logger.warning("parfive/aiohttp import failed; using Fido default downloader", exc_info=True)
        return None

    timeout_seconds = _resolve_fetch_timeout_seconds(priority=priority)
    read_timeout_seconds = _resolve_fetch_read_timeout_seconds(priority=priority)

    try:
        timeout = ClientTimeout(total=timeout_seconds, sock_read=read_timeout_seconds)
        config = SessionConfig(timeouts=timeout, file_progress=False)
        return Downloader(
            max_conn=int(max_conn) if max_conn is not None else _resolve_fetch_max_conn(),
            progress=False,
            config=config,
        )
    except Exception:
        _logger.warning("Failed to build custom parfive Downloader; using Fido default", exc_info=True)
        return None


def _resolve_fetch_max_conn(*, priority: bool = False) -> int:
    raw = str(
        os.environ.get("ECALLISTO_SUNPY_HIGH_RES_MAX_CONN" if priority else "ECALLISTO_SUNPY_MAX_CONN", "")
    ).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 64)
        except Exception:
            pass
    return 8 if priority else 6


def _resolve_direct_download_workers(*, priority: bool = False) -> int:
    raw = str(
        os.environ.get(
            "ECALLISTO_SUNPY_DIRECT_DOWNLOAD_WORKERS"
            if priority
            else "ECALLISTO_SUNPY_DIRECT_DOWNLOAD_WORKERS_NORMAL",
            "",
        )
    ).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 12)
        except Exception:
            pass
    return 4 if priority else 3


def _resolve_direct_url_batch_size() -> int:
    raw = str(os.environ.get("ECALLISTO_SUNPY_DIRECT_URL_BATCH_SIZE", "")).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 200)
        except Exception:
            pass
    return 40


def _resolve_direct_vso_sites(*, priority: bool = False) -> tuple[str | None, ...]:
    raw = str(os.environ.get("ECALLISTO_SUNPY_DIRECT_VSO_SITES", "")).strip()
    if raw:
        lowered = raw.lower()
        if lowered in {"0", "false", "no", "off", "none"}:
            return (None,)
        sites: list[str | None] = []
        for chunk in re.split(r"[,;]\s*", raw):
            item = chunk.strip()
            if not item:
                continue
            if item.lower() in {"default", "primary", "vso"}:
                sites.append(None)
            else:
                sites.append(item)
        return tuple(_dedupe_site_order(sites)) or (None,)
    if priority:
        return ("NSO", None, "ROB", "MPS")
    return (None,)


def _dedupe_site_order(values: Sequence[str | None]) -> list[str | None]:
    out: list[str | None] = []
    seen: set[str] = set()
    for value in values:
        key = "<default>" if value is None else str(value).strip().upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _resolve_fetch_batch_size(*, priority: bool = False) -> int:
    raw = str(
        os.environ.get("ECALLISTO_SUNPY_HIGH_RES_BATCH_SIZE" if priority else "ECALLISTO_SUNPY_BATCH_SIZE", "")
    ).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 100)
        except Exception:
            pass
    return 1 if priority else 12


def _resolve_fetch_retry_count(*, priority: bool = False) -> int:
    raw = str(
        os.environ.get("ECALLISTO_SUNPY_HIGH_RES_FETCH_RETRIES" if priority else "ECALLISTO_SUNPY_FETCH_RETRIES", "")
    ).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 6)
        except Exception:
            pass
    return 1 if priority else 2


def _resolve_fetch_retry_backoff_seconds() -> float:
    raw = str(os.environ.get("ECALLISTO_SUNPY_FETCH_RETRY_BACKOFF", "")).strip()
    if raw:
        try:
            value = float(raw)
            if value >= 0:
                return min(value, 5.0)
        except Exception:
            pass
    return 0.4


def _resolve_fast_fail_enabled() -> bool:
    raw = str(os.environ.get("ECALLISTO_SUNPY_FAST_FAIL_TIMEOUT_BATCH", "")).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return True


def _resolve_fast_fail_min_batch_size() -> int:
    raw = str(os.environ.get("ECALLISTO_SUNPY_FAST_FAIL_MIN_BATCH", "")).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 128)
        except Exception:
            pass
    return 8


def _resolve_fetch_timeout_seconds(*, priority: bool = False) -> float:
    raw = str(
        os.environ.get("ECALLISTO_SUNPY_HIGH_RES_FETCH_TIMEOUT" if priority else "ECALLISTO_SUNPY_FETCH_TIMEOUT", "")
    ).strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return min(value, 7200.0)
        except Exception:
            pass
    return 45.0 if priority else 180.0


def _resolve_fetch_read_timeout_seconds(*, priority: bool = False) -> float:
    raw = str(
        os.environ.get(
            "ECALLISTO_SUNPY_HIGH_RES_FETCH_READ_TIMEOUT" if priority else "ECALLISTO_SUNPY_FETCH_READ_TIMEOUT",
            "",
        )
    ).strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return min(value, 3600.0)
        except Exception:
            pass
    return 12.0 if priority else 40.0


def _resolve_manual_fetch_retries(*, priority: bool = False) -> int:
    raw = str(
        os.environ.get(
            "ECALLISTO_SUNPY_HIGH_RES_MANUAL_FETCH_RETRIES" if priority else "ECALLISTO_SUNPY_MANUAL_FETCH_RETRIES",
            "",
        )
    ).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 6)
        except Exception:
            pass
    return 1 if priority else 2


def _resolve_manual_fetch_timeout_seconds(*, priority: bool = False) -> float:
    raw = str(
        os.environ.get(
            "ECALLISTO_SUNPY_HIGH_RES_MANUAL_FETCH_TIMEOUT" if priority else "ECALLISTO_SUNPY_MANUAL_FETCH_TIMEOUT",
            "",
        )
    ).strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return min(value, 7200.0)
        except Exception:
            pass
    return 30.0 if priority else 90.0


def _resolve_manual_fetch_backoff_seconds() -> float:
    raw = str(os.environ.get("ECALLISTO_SUNPY_MANUAL_FETCH_BACKOFF", "")).strip()
    if raw:
        try:
            value = float(raw)
            if value >= 0:
                return min(value, 10.0)
        except Exception:
            pass
    return 0.4


def _dedupe_ints(values: Sequence[int]) -> list[int]:
    out: list[int] = []
    seen = set()
    for value in values:
        intval = int(value)
        if intval in seen:
            continue
        seen.add(intval)
        out.append(intval)
    return out


def _extract_fetch_paths(fetch_result: Any) -> list[str]:
    out: list[str] = []
    if fetch_result is None:
        return out
    try:
        items = list(fetch_result)
    except Exception:
        items = [fetch_result]

    for item in items:
        if item is None:
            continue
        try:
            text = str(Path(item).expanduser().resolve())
        except Exception:
            text = str(item).strip()
        if text:
            out.append(text)
    return out


def _download_from_fetch_errors(
    fetch_result: Any,
    *,
    row_template: str,
    priority: bool = False,
    cancel_cb: Callable[[], bool] | None = None,
) -> list[str]:
    urls = _extract_fetch_error_urls(fetch_result)
    if not urls:
        return []

    target_dir = _resolve_cache_dir_from_row_template(row_template)
    target_dir.mkdir(parents=True, exist_ok=True)
    retries = _resolve_manual_fetch_retries(priority=priority)
    timeout_seconds = _resolve_manual_fetch_timeout_seconds(priority=priority)
    backoff_seconds = _resolve_manual_fetch_backoff_seconds()

    downloaded: list[str] = []
    for url in urls:
        if _is_cancelled(cancel_cb):
            break
        path = _download_url_with_retries(
            url,
            target_dir=target_dir,
            retries=retries,
            timeout_seconds=timeout_seconds,
            backoff_seconds=backoff_seconds,
            cancel_cb=cancel_cb,
        )
        if path:
            downloaded.append(path)
            break
    return downloaded


def _download_from_row_record(
    search_result: SunPySearchResult,
    row_index: int,
    *,
    row_template: str,
    priority: bool = False,
    cancel_cb: Callable[[], bool] | None = None,
) -> list[str]:
    """Last-resort fallback: download a row directly from any URL present in
    its raw search record, using synchronous urllib (no aiohttp/asyncio).

    This guarantees a working path on frozen Windows even when the parfive
    async downloader is fundamentally broken, provided the record exposes a
    direct http(s) URL (true for several providers).
    """
    record = _raw_record_for_row(search_result, row_index)
    if record is None:
        return []
    urls = _extract_record_urls(record)
    if not urls:
        return []

    target_dir = _resolve_cache_dir_from_row_template(row_template)
    target_dir.mkdir(parents=True, exist_ok=True)
    retries = _resolve_manual_fetch_retries(priority=priority)
    timeout_seconds = _resolve_manual_fetch_timeout_seconds(priority=priority)
    backoff_seconds = _resolve_manual_fetch_backoff_seconds()

    for url in urls:
        if _is_cancelled(cancel_cb):
            break
        path = _download_url_with_retries(
            url,
            target_dir=target_dir,
            retries=retries,
            timeout_seconds=timeout_seconds,
            backoff_seconds=backoff_seconds,
            cancel_cb=cancel_cb,
        )
        if path:
            _logger.info("Row %d recovered via urllib record URL: %s", row_index + 1, url)
            return [path]
    return []


def _raw_record_for_row(search_result: SunPySearchResult, row_index: int) -> Any | None:
    try:
        block_idx, local_idx = search_result.row_index_map[row_index]
        return search_result.raw_response[block_idx][local_idx]
    except Exception:
        return None


def _extract_record_urls(record: Any) -> list[str]:
    urls: list[str] = []

    for key in ("url", "URL", "fileid", "FileID", "file_id", "Record", "record"):
        text = _safe_str(_row_get(record, key)).strip()
        if text.startswith(("http://", "https://")):
            urls.append(text)

    keys_attr = getattr(record, "keys", None)
    if callable(keys_attr):
        try:
            for key in keys_attr():
                text = _safe_str(record[key]).strip()
                if text.startswith(("http://", "https://")):
                    urls.append(text)
        except Exception:
            pass

    return _dedupe_preserve_order(urls)


def _download_url_with_retries(
    url: str,
    *,
    target_dir: Path,
    retries: int,
    timeout_seconds: float,
    backoff_seconds: float,
    cancel_cb: Callable[[], bool] | None = None,
) -> str | None:
    last_error: Exception | None = None
    attempts = max(1, int(retries))
    for attempt_idx in range(attempts):
        if _is_cancelled(cancel_cb):
            return None
        try:
            request = Request(url, headers={"User-Agent": "eCallisto-SunPy/1.0"})
            with urlopen(request, timeout=float(timeout_seconds)) as response:
                filename = _guess_filename_from_response(url, response)
                out_path = _allocate_unique_path(target_dir / filename)
                with open(out_path, "wb") as handle:
                    while True:
                        if _is_cancelled(cancel_cb):
                            raise RuntimeError("Download cancelled.")
                        chunk = response.read(1024 * 512)
                        if not chunk:
                            break
                        handle.write(chunk)
            if out_path.exists() and out_path.stat().st_size > 0:
                return str(out_path.resolve())
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as exc:
            last_error = exc
        if attempt_idx + 1 < attempts and not _is_cancelled(cancel_cb):
            time.sleep(backoff_seconds * (attempt_idx + 1))
    if last_error is not None:
        return None
    return None


def _resolve_cache_dir_from_row_template(row_template: str) -> Path:
    marker = "{file}"
    text = str(row_template or "").strip()
    if marker in text:
        base = text.split(marker, 1)[0]
        if base:
            return Path(base).expanduser().resolve()
    return Path.cwd().resolve()


def _guess_filename_from_response(url: str, response: Any) -> str:
    headers = getattr(response, "headers", None)
    if headers is not None:
        disposition = headers.get("Content-Disposition") or headers.get("content-disposition")
        parsed = _filename_from_content_disposition(disposition)
        if parsed:
            return parsed
        header_name = headers.get("X-Filename") or headers.get("x-filename")
        if header_name:
            cleaned = _sanitize_filename(header_name)
            if cleaned:
                return cleaned

    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)
    record_values = query.get("record") or query.get("Record") or []
    if record_values:
        record = _sanitize_filename(record_values[0].replace(";", "_"))
        if record:
            if not record.lower().endswith((".fits", ".fit", ".fts")):
                record = f"{record}.fits"
            return record

    basename = Path(parsed_url.path).name
    cleaned_base = _sanitize_filename(basename) or "sunpy_download.fits"
    if "." not in cleaned_base:
        cleaned_base = f"{cleaned_base}.fits"
    return cleaned_base


def _filename_from_content_disposition(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(r'filename\\*?=([^;]+)', value, flags=re.IGNORECASE)
    if not match:
        return ""
    raw = match.group(1).strip().strip('"').strip("'")
    if raw.lower().startswith("utf-8''"):
        raw = raw[7:]
    return _sanitize_filename(raw)


def _sanitize_filename(name: str) -> str:
    text = str(name or "").strip().replace("/", "_").replace("\\", "_")
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = text.strip("._")
    return text[:180]


def _allocate_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix or ".fits"
    parent = path.parent
    for idx in range(1, 1000):
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem}_{int(time.time())}{suffix}"


def _extract_fetch_error_urls(fetch_result: Any) -> list[str]:
    out: list[str] = []
    raw_errors = getattr(fetch_result, "errors", None)
    if not raw_errors:
        return out

    for item in list(raw_errors):
        url_text = ""
        if isinstance(item, tuple) and len(item) >= 1:
            url_text = str(item[0]).strip()
        else:
            url_text = str(getattr(item, "url", "") or "").strip()
        if url_text.startswith("http://") or url_text.startswith("https://"):
            out.append(url_text)
    return _dedupe_preserve_order(out)


def _is_retryable_fetch_errors(errors: Sequence[str]) -> bool:
    if not errors:
        return False
    merged = " | ".join(str(item).lower() for item in errors)
    retry_tokens = (
        "timed out",
        "timeout",
        "socket",
        "connection reset",
        "server disconnected",
        "temporarily unavailable",
        "temporary failure",
        "too many requests",
        "503",
        "504",
        "502",
    )
    return any(token in merged for token in retry_tokens)


def _should_fast_fail_timeout_batch(
    fetch_result: Any,
    *,
    fetch_errors: Sequence[str],
    batch_size: int,
) -> bool:
    if not _resolve_fast_fail_enabled():
        return False
    if int(batch_size) < _resolve_fast_fail_min_batch_size():
        return False
    if not fetch_errors or not _is_retryable_fetch_errors(fetch_errors):
        return False

    raw_error_count = _safe_len(getattr(fetch_result, "errors", None))
    if raw_error_count < max(2, int(batch_size) // 2):
        return False

    urls = _extract_fetch_error_urls(fetch_result)
    if not urls:
        return False

    hosts = {urlparse(url).netloc.strip().lower() for url in urls if str(url).strip()}
    if len(hosts) != 1:
        return False

    host = next(iter(hosts))
    if not host:
        return False
    return host.endswith(".nascom.nasa.gov")


def _format_fast_fail_timeout_reason(fetch_errors: Sequence[str]) -> str:
    base = "Archive server timed out for all records in this batch."
    detail = str(fetch_errors[0]).strip() if fetch_errors else ""
    if detail:
        return f"{base} {detail}"
    return base


def _extract_fetch_errors(fetch_result: Any) -> list[str]:
    out: list[str] = []
    raw_errors = getattr(fetch_result, "errors", None)
    if not raw_errors:
        return out

    for item in list(raw_errors)[:8]:
        if isinstance(item, tuple) and len(item) >= 2:
            out.append(f"{item[0]}: {item[1]}")
            continue

        url = getattr(item, "url", None)
        exc = getattr(item, "exception", None)
        if url is not None or exc is not None:
            parts = []
            if url is not None:
                parts.append(str(url))
            if exc is not None:
                parts.append(str(exc))
            out.append(": ".join(parts))
            continue

        out.append(str(item))
    return out


def _format_sunpy_dependency_error(exc: Exception) -> str:
    missing = ""
    if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", ""):
        missing = str(exc.name).strip()

    if missing:
        return (
            f"SunPy dependency missing: '{missing}'.\n"
            f"Install required packages with:\n{SUNPY_INSTALL_HINT}"
        )
    return (
        "SunPy dependencies are not available.\n"
        f"Install required packages with:\n{SUNPY_INSTALL_HINT}"
    )


def _load_maps(map_loader: Callable[..., Any], paths: list[str]) -> Any:
    if len(paths) <= 1:
        return map_loader(paths[0])

    try:
        return map_loader(paths, sequence=True)
    except TypeError:
        return map_loader(paths)


def _load_timeseries(timeseries_loader: Callable[..., Any], paths: list[str]) -> Any:
    try:
        return timeseries_loader(paths, concatenate=True)
    except TypeError:
        return timeseries_loader(paths)


def _extract_maps(loaded: Any) -> list[Any]:
    if loaded is None:
        return []

    maps_attr = getattr(loaded, "maps", None)
    if maps_attr is not None:
        try:
            return list(maps_attr)
        except Exception:
            pass

    if isinstance(loaded, (list, tuple)):
        return list(loaded)

    return [loaded]


def _safe_len(value: Any) -> int:
    try:
        return int(len(value))
    except Exception:
        return 0


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return _naive_utc(value)

    to_datetime = getattr(value, "to_datetime", None)
    if callable(to_datetime):
        try:
            return _naive_utc(to_datetime())
        except Exception:
            pass

    isot = getattr(value, "isot", None)
    if isinstance(isot, str) and isot.strip():
        parsed = _parse_datetime_str(isot.strip())
        if parsed is not None:
            return parsed

    if isinstance(value, str):
        parsed = _parse_datetime_str(value.strip())
        if parsed is not None:
            return parsed

    return None


def _parse_datetime_str(text: str) -> datetime | None:
    if not text:
        return None

    candidate = text.strip().replace("Z", "+00:00")
    try:
        return _naive_utc(datetime.fromisoformat(candidate))
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _naive_utc(datetime.strptime(text, fmt))
        except Exception:
            continue
    return None


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=None)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _dedupe_preserve_order(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
