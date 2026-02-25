"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import warnings


DATA_KIND_MAP = "map"
DATA_KIND_TIMESERIES = "timeseries"
SUNPY_INSTALL_HINT = (
    'python3 -m pip install --upgrade "sunpy[map,net,timeseries]==7.1.0" lxml drms zeep reproject mpl-animators'
)


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
    max_records: int = 200


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
)


def list_instrument_registry() -> list[InstrumentRegistryEntry]:
    return list(INSTRUMENT_REGISTRY)


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

    if spec.sample_seconds and float(spec.sample_seconds) > 0 and hasattr(attrs_module, "Sample"):
        out.append(attrs_module.Sample(float(spec.sample_seconds) * units_module.second))

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
    fido_client: Any | None = None,
) -> SunPyFetchResult:
    if search_result.raw_response is None:
        raise ValueError("Search result does not contain a raw response object for fetching.")

    if fido_client is None:
        fido_client = _import_fido()

    cache_root = Path(cache_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)

    requested = _normalize_selected_rows(selected_rows, len(search_result.rows))
    if not requested:
        return SunPyFetchResult(paths=[], requested_count=0, failed_count=0, errors=[])

    downloaded_paths: list[str] = []
    errors: list[str] = []
    total = len(requested)
    processed_rows = 0
    row_template = str(cache_root / "{file}")
    max_conn = _resolve_fetch_max_conn()
    max_batch_size = _resolve_fetch_batch_size()

    def _mark_processed(delta: int):
        nonlocal processed_rows
        processed_rows += int(max(0, delta))
        if progress_cb is not None:
            progress_cb(
                int(processed_rows * 100 / max(total, 1)),
                f"Fetched {processed_rows}/{total} selections",
            )

    batches = _build_row_batches(search_result, requested, max_batch_size=max_batch_size)
    for batch_idx, batch in enumerate(batches, start=1):
        if progress_cb is not None:
            progress_cb(
                int(processed_rows * 100 / max(total, 1)),
                f"Downloading batch {batch_idx}/{len(batches)}...",
            )
        _fetch_rows_batch_adaptive(
            search_result,
            batch,
            fido_client=fido_client,
            row_template=row_template,
            downloaded_paths=downloaded_paths,
            errors=errors,
            max_conn=max_conn,
            processed_cb=_mark_processed,
        )

    return SunPyFetchResult(
        paths=_dedupe_preserve_order(downloaded_paths),
        requested_count=total,
        failed_count=len(errors),
        errors=errors,
    )


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
        max_records=max(1, int(spec.max_records or 1)),
    )


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
):
    query_slice = _query_slice_for_row(search_result, row_index)
    try:
        fetched = _fetch_row_with_runtime_guards(
            fido_client,
            query_slice,
            path_template=row_template,
            max_conn=max_conn,
            retry_count=_resolve_fetch_retry_count(),
            conn_candidates=[min(4, max_conn), 2, 1],
        )
        row_paths = _extract_fetch_paths(fetched)
        if not row_paths:
            manual_paths = _download_from_fetch_errors(
                fetched,
                row_template=row_template,
            )
            if manual_paths:
                downloaded_paths.extend(manual_paths)
                return
            fetch_errors = _extract_fetch_errors(fetched)
            if fetch_errors:
                errors.append(f"Row {row_index + 1}: {fetch_errors[0]}")
            else:
                errors.append(f"Row {row_index + 1}: fetch returned no files.")
        else:
            downloaded_paths.extend(row_paths)
    except Exception as exc:
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
    processed_cb: Callable[[int], None],
):
    if not batch:
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
        )
        processed_cb(1)
        return

    query_slice = _query_slice_for_rows(search_result, batch)
    if query_slice is None:
        for row_index in batch:
            _fetch_single_row(
                search_result,
                int(row_index),
                fido_client=fido_client,
                row_template=row_template,
                downloaded_paths=downloaded_paths,
                errors=errors,
                max_conn=max_conn,
            )
            processed_cb(1)
        return

    try:
        fetched = _fetch_row_with_runtime_guards(
            fido_client,
            query_slice,
            path_template=row_template,
            max_conn=1,
            retry_count=1,
            conn_candidates=[1],
        )
        row_paths = _extract_fetch_paths(fetched)
        if row_paths:
            downloaded_paths.extend(row_paths)
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
            processed_cb(len(batch))
            return
    except Exception:
        pass

    # Adaptive fallback: split failed batch into halves before trying per-row.
    midpoint = len(batch) // 2
    if midpoint <= 0 or midpoint >= len(batch):
        for row_index in batch:
            _fetch_single_row(
                search_result,
                int(row_index),
                fido_client=fido_client,
                row_template=row_template,
                downloaded_paths=downloaded_paths,
                errors=errors,
                max_conn=max_conn,
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
        processed_cb=processed_cb,
    )
    _fetch_rows_batch_adaptive(
        search_result,
        right,
        fido_client=fido_client,
        row_template=row_template,
        downloaded_paths=downloaded_paths,
        errors=errors,
        max_conn=max_conn,
        processed_cb=processed_cb,
    )


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
        raise RuntimeError(_format_sunpy_dependency_error(exc)) from exc
    return sunpy_attrs, astro_units


def _import_fido() -> Any:
    try:
        from sunpy.net import Fido
    except Exception as exc:
        raise RuntimeError(_format_sunpy_dependency_error(exc)) from exc
    return Fido


def _import_map_loader() -> Callable[..., Any]:
    try:
        from sunpy.map import Map
    except Exception as exc:
        raise RuntimeError(_format_sunpy_dependency_error(exc)) from exc
    return Map


def _import_timeseries_loader() -> Callable[..., Any]:
    try:
        from sunpy.timeseries import TimeSeries
    except Exception as exc:
        raise RuntimeError(_format_sunpy_dependency_error(exc)) from exc
    return TimeSeries


def _fetch_row_with_runtime_guards(
    fido_client: Any,
    query_slice: Any,
    path_template: str,
    *,
    max_conn: int,
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

        for attempt_idx in range(retries):
            try:
                result = _fetch_once(
                    fido_client,
                    query_slice,
                    path_template=path_template,
                    max_conn=None,
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
):
    downloader = _build_parfive_downloader(max_conn=max_conn)
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


def _build_parfive_downloader(*, max_conn: int | None) -> Any | None:
    try:
        from aiohttp import ClientTimeout
        from parfive import Downloader
        from parfive.config import SessionConfig
    except Exception:
        return None

    timeout_seconds = _resolve_fetch_timeout_seconds()
    read_timeout_seconds = _resolve_fetch_read_timeout_seconds()

    try:
        timeout = ClientTimeout(total=timeout_seconds, sock_read=read_timeout_seconds)
        config = SessionConfig(timeouts=timeout, file_progress=False)
        return Downloader(
            max_conn=int(max_conn) if max_conn is not None else _resolve_fetch_max_conn(),
            progress=False,
            config=config,
        )
    except Exception:
        return None


def _resolve_fetch_max_conn() -> int:
    raw = str(os.environ.get("ECALLISTO_SUNPY_MAX_CONN", "")).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 64)
        except Exception:
            pass
    return 6


def _resolve_fetch_batch_size() -> int:
    raw = str(os.environ.get("ECALLISTO_SUNPY_BATCH_SIZE", "")).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 100)
        except Exception:
            pass
    return 12


def _resolve_fetch_retry_count() -> int:
    raw = str(os.environ.get("ECALLISTO_SUNPY_FETCH_RETRIES", "")).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 6)
        except Exception:
            pass
    return 2


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


def _resolve_fetch_timeout_seconds() -> float:
    raw = str(os.environ.get("ECALLISTO_SUNPY_FETCH_TIMEOUT", "")).strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return min(value, 7200.0)
        except Exception:
            pass
    return 180.0


def _resolve_fetch_read_timeout_seconds() -> float:
    raw = str(os.environ.get("ECALLISTO_SUNPY_FETCH_READ_TIMEOUT", "")).strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return min(value, 3600.0)
        except Exception:
            pass
    return 40.0


def _resolve_manual_fetch_retries() -> int:
    raw = str(os.environ.get("ECALLISTO_SUNPY_MANUAL_FETCH_RETRIES", "")).strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return min(value, 6)
        except Exception:
            pass
    return 2


def _resolve_manual_fetch_timeout_seconds() -> float:
    raw = str(os.environ.get("ECALLISTO_SUNPY_MANUAL_FETCH_TIMEOUT", "")).strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return min(value, 7200.0)
        except Exception:
            pass
    return 90.0


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


def _download_from_fetch_errors(fetch_result: Any, *, row_template: str) -> list[str]:
    urls = _extract_fetch_error_urls(fetch_result)
    if not urls:
        return []

    target_dir = _resolve_cache_dir_from_row_template(row_template)
    target_dir.mkdir(parents=True, exist_ok=True)
    retries = _resolve_manual_fetch_retries()
    timeout_seconds = _resolve_manual_fetch_timeout_seconds()
    backoff_seconds = _resolve_manual_fetch_backoff_seconds()

    downloaded: list[str] = []
    for url in urls:
        path = _download_url_with_retries(
            url,
            target_dir=target_dir,
            retries=retries,
            timeout_seconds=timeout_seconds,
            backoff_seconds=backoff_seconds,
        )
        if path:
            downloaded.append(path)
            break
    return downloaded


def _download_url_with_retries(
    url: str,
    *,
    target_dir: Path,
    retries: int,
    timeout_seconds: float,
    backoff_seconds: float,
) -> str | None:
    last_error: Exception | None = None
    attempts = max(1, int(retries))
    for attempt_idx in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "eCallisto-SunPy/1.0"})
            with urlopen(request, timeout=float(timeout_seconds)) as response:
                filename = _guess_filename_from_response(url, response)
                out_path = _allocate_unique_path(target_dir / filename)
                with open(out_path, "wb") as handle:
                    while True:
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
        if attempt_idx + 1 < attempts:
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
