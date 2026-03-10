from __future__ import annotations

import csv
from io import BytesIO, StringIO
import os
import re
from typing import Any

from astropy.io import fits
import matplotlib
from matplotlib.figure import Figure
import numpy as np
from openpyxl import Workbook

from app.domain.types import DatasetBundle

matplotlib.use("Agg")


_FIGURE_MEDIA_TYPES = {
    "png": "image/png",
    "pdf": "application/pdf",
    "eps": "application/postscript",
    "svg": "image/svg+xml",
    "tiff": "image/tiff",
}

_FIT_SUFFIXES = (".fit.gz", ".fits.gz", ".fit", ".fits")


def _normalize_figure_format(fmt: str) -> str:
    value = str(fmt or "png").strip().lower()
    if value not in _FIGURE_MEDIA_TYPES:
        raise ValueError(f"Unsupported export format: {fmt}")
    return value


def _default_title_from_source(source: str) -> str:
    return "Background Subtracted" if str(source).strip().lower() == "processed" else "Raw Spectrum"


def _download_name(stem: str, ext: str) -> str:
    safe_stem = re.sub(r"[^\w.-]+", "_", str(stem or "export")).strip("_") or "export"
    return f"{safe_stem}.{ext}"


def _figure_bytes(fig: Figure, fmt: str) -> bytes:
    buf = BytesIO()
    fig.savefig(buf, dpi=300, bbox_inches="tight", format=fmt)
    buf.seek(0)
    return buf.read()


def export_spectrum_figure(
    dataset: DatasetBundle,
    *,
    source: str,
    title: str | None,
    fmt: str,
) -> tuple[bytes, str, str]:
    fmt = _normalize_figure_format(fmt)
    use_processed = str(source).strip().lower() == "processed"
    data = dataset.processed_data if use_processed else dataset.raw_data
    if data is None:
        raise ValueError("Processed data are not available for export.")

    fig = Figure(figsize=(12, 6))
    ax = fig.add_subplot(111)
    arr = np.asarray(data, dtype=float)
    freqs = np.asarray(dataset.freqs, dtype=float)
    time = np.asarray(dataset.time, dtype=float)
    extent = [float(time[0]), float(time[-1]), float(freqs[-1]), float(freqs[0])]
    im = ax.imshow(arr, aspect="auto", extent=extent, cmap="viridis")
    fig.colorbar(im, ax=ax).set_label("Intensity")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (MHz)")
    plot_title = str(title or "").strip() or _default_title_from_source(source)
    ax.set_title(plot_title)
    payload = _figure_bytes(fig, fmt)
    return payload, _download_name(plot_title.replace(" ", "_"), fmt), _FIGURE_MEDIA_TYPES[fmt]


def export_maxima_figure(
    points: list[dict[str, float]],
    *,
    title: str | None,
    fmt: str,
) -> tuple[bytes, str, str]:
    fmt = _normalize_figure_format(fmt)
    fig = Figure(figsize=(10, 6))
    ax = fig.add_subplot(111)
    xs = [float(point["timeSeconds"]) for point in points]
    ys = [float(point["freqMHz"]) for point in points]
    ax.scatter(xs, ys, s=10, color="red")
    plot_title = str(title or "").strip() or "Maximum_Intensity_for_Each_Time_Channel"
    ax.set_title(plot_title)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (MHz)")
    ax.grid(True)
    payload = _figure_bytes(fig, fmt)
    return payload, _download_name(plot_title.replace(" ", "_"), fmt), _FIGURE_MEDIA_TYPES[fmt]


def export_analysis_figure(
    analysis_result: dict[str, Any],
    *,
    plot_kind: str,
    title: str | None,
    fmt: str,
) -> tuple[bytes, str, str]:
    fmt = _normalize_figure_format(fmt)
    plot_name = str(plot_kind or "").strip()
    fig = Figure(figsize=(10, 6))
    ax = fig.add_subplot(111)

    if plot_name == "best_fit":
        points = analysis_result["plots"]["bestFit"]["points"]
        fit_line = analysis_result["plots"]["bestFit"]["fitLine"]
        ax.scatter([p["x"] for p in points], [p["y"] for p in points], s=10, color="blue", label="Original Data")
        ax.plot([p["x"] for p in fit_line], [p["y"] for p in fit_line], color="red", label=analysis_result["equation"])
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Frequency (MHz)")
        default_title = "Best_Fit"
        ax.legend()
    elif plot_name == "shock_speed_vs_height":
        points = analysis_result["plots"]["shockSpeedVsHeight"]["points"]
        ax.scatter([p["x"] for p in points], [p["y"] for p in points], s=12, color="green")
        ax.set_xlabel("Shock Height (Rs)")
        ax.set_ylabel("Shock Speed (km/s)")
        default_title = "Shock_Speed_vs_Shock_Height"
    elif plot_name == "shock_speed_vs_frequency":
        points = analysis_result["plots"]["shockSpeedVsFrequency"]["points"]
        ax.scatter([p["x"] for p in points], [p["y"] for p in points], s=12, color="purple")
        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel("Shock Speed (km/s)")
        default_title = "Shock_Speed_vs_Frequency"
    elif plot_name == "shock_height_vs_frequency":
        points = analysis_result["plots"]["shockHeightVsFrequency"]["points"]
        ax.scatter([p["x"] for p in points], [p["y"] for p in points], s=12, color="red")
        ax.set_xlabel("Shock Height (Rs)")
        ax.set_ylabel("Frequency (MHz)")
        default_title = "Rs_vs_Freq"
    else:
        raise ValueError(f"Unsupported analyzer plot kind: {plot_kind}")

    plot_title = str(title or "").strip() or default_title
    ax.set_title(plot_title)
    ax.grid(True)
    payload = _figure_bytes(fig, fmt)
    return payload, _download_name(plot_title, fmt), _FIGURE_MEDIA_TYPES[fmt]


def recommend_bitpix(data: np.ndarray) -> int:
    try:
        arr = np.asarray(data)
        if arr.size == 0:
            return 16
        mn = float(np.nanmin(arr))
        mx = float(np.nanmax(arr))
    except Exception:
        return 16
    if 0.0 <= mn and mx <= 255.0:
        return 8
    if -32768.0 <= mn and mx <= 32767.0:
        return 16
    return 32


def cast_data_for_bitpix(data: np.ndarray, bitpix: int) -> np.ndarray:
    arr = np.nan_to_num(np.asarray(data), nan=0.0, posinf=0.0, neginf=0.0)
    if int(bitpix) == 8:
        return np.clip(np.rint(arr), 0, 255).astype(np.uint8, copy=False)
    if int(bitpix) == 16:
        info = np.iinfo(np.int16)
        return np.clip(np.rint(arr), info.min, info.max).astype(np.int16, copy=False)
    if int(bitpix) == 32:
        info = np.iinfo(np.int32)
        return np.clip(np.rint(arr), info.min, info.max).astype(np.int32, copy=False)
    raise ValueError(f"Unsupported BITPIX: {bitpix}")


def _sanitize_primary_header_for_export(hdr: fits.Header) -> fits.Header:
    for key in ("SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "EXTEND", "BSCALE", "BZERO", "BLANK"):
        try:
            hdr.remove(key, ignore_missing=True, remove_all=True)
        except Exception:
            pass
    return hdr


def _axis_kind_from_name(name: str) -> str | None:
    normalized = str(name or "").strip().lower()
    if normalized in ("freq", "frequency", "freqs", "frequency_mhz", "freq_mhz"):
        return "freq"
    if normalized in ("time", "times", "time_s", "time_sec", "seconds", "sec"):
        return "time"
    if "freq" in normalized:
        return "freq"
    if normalized.startswith("time"):
        return "time"
    return None


def _update_axis_table_hdu(hdu, freqs: np.ndarray, times: np.ndarray) -> bool:
    data = getattr(hdu, "data", None)
    if data is None:
        return False
    dtype = getattr(data, "dtype", None)
    names = list(getattr(dtype, "names", []) or [])
    if not names:
        return False

    axis_map: dict[str, np.ndarray] = {}
    for name in names:
        kind = _axis_kind_from_name(name)
        if kind == "freq":
            axis_map[name] = freqs
        elif kind == "time":
            axis_map[name] = times

    if not axis_map:
        extname = str(hdu.header.get("EXTNAME", "")).lower()
        if "freq" in extname and len(names) == 1:
            axis_map[names[0]] = freqs
        elif "time" in extname and len(names) == 1:
            axis_map[names[0]] = times

    if not axis_map:
        return False

    old_rows = int(data.shape[0]) if hasattr(data, "shape") and len(data.shape) > 0 else 0
    row_lengths: list[int] = []
    for name, axis in axis_map.items():
        field_dtype = dtype.fields[name][0]
        if field_dtype.shape == ():
            row_lengths.append(len(axis))

    nrows = row_lengths[0] if row_lengths else max(old_rows, 1)
    for length in row_lengths[1:]:
        if length != nrows:
            nrows = max(row_lengths)
            break

    new_descr = []
    for name in names:
        field_dtype = dtype.fields[name][0]
        base = field_dtype.base
        if name in axis_map:
            if field_dtype.shape == ():
                new_descr.append((name, base))
            else:
                new_descr.append((name, base, (len(axis_map[name]),)))
        else:
            if field_dtype.shape == ():
                new_descr.append((name, field_dtype))
            else:
                new_descr.append((name, base, field_dtype.shape))

    new_dtype = np.dtype(new_descr)
    new_data = np.zeros(nrows, dtype=new_dtype)
    for name, axis in axis_map.items():
        axis_arr = np.asarray(axis)
        target = new_data[name]
        if target.ndim == 1:
            axis_cast = axis_arr.astype(target.dtype, copy=False)
            if axis_cast.shape[0] < nrows:
                pad = np.zeros(nrows, dtype=target.dtype)
                pad[: axis_cast.shape[0]] = axis_cast
                axis_cast = pad
            elif axis_cast.shape[0] > nrows:
                axis_cast = axis_cast[:nrows]
            new_data[name] = axis_cast
        else:
            vec_len = target.shape[1]
            axis_cast = axis_arr.astype(target.dtype, copy=False)
            if axis_cast.shape[0] < vec_len:
                pad = np.zeros(vec_len, dtype=target.dtype)
                pad[: axis_cast.shape[0]] = axis_cast
                axis_cast = pad
            elif axis_cast.shape[0] > vec_len:
                axis_cast = axis_cast[:vec_len]
            new_data[name][:] = axis_cast

    for name in names:
        if name in axis_map:
            continue
        try:
            old_col = data[name]
            if old_col.shape == new_data[name].shape:
                new_data[name] = old_col
            elif old_col.size > 0:
                new_data[name][0] = old_col[0]
                if new_data[name].shape[0] > 1:
                    new_data[name][1:] = new_data[name][0]
        except Exception:
            pass

    hdu.data = new_data
    try:
        hdu.update_header()
    except Exception:
        pass
    return True


def _build_export_hdul_from_template(
    template_hdul: fits.HDUList,
    primary: fits.PrimaryHDU,
    freqs: np.ndarray,
    times: np.ndarray,
) -> tuple[fits.HDUList, bool]:
    new_hdus = [primary]
    updated_any = False
    for hdu in template_hdul[1:]:
        new_hdu = hdu.copy()
        if isinstance(new_hdu, (fits.BinTableHDU, fits.TableHDU)):
            if _update_axis_table_hdu(new_hdu, freqs, times):
                updated_any = True
        new_hdus.append(new_hdu)
    return fits.HDUList(new_hdus), updated_any


def export_fits_file(
    dataset: DatasetBundle,
    *,
    source: str,
    bitpix: str | int,
) -> tuple[bytes, str, str]:
    use_processed = str(source).strip().lower() == "processed"
    data_to_save = dataset.processed_data if use_processed else dataset.raw_data
    if data_to_save is None:
        raise ValueError("Processed data are not available for FITS export.")

    if isinstance(bitpix, str) and str(bitpix).strip().lower() == "auto":
        target_bitpix = recommend_bitpix(data_to_save)
    else:
        target_bitpix = int(bitpix)
    export_data = cast_data_for_bitpix(data_to_save, target_bitpix)

    hdr0 = _sanitize_primary_header_for_export(dataset.header0.copy())
    hdr0["HISTORY"] = "Exported by e-CALLISTO FITS Analyzer Web"
    hdr0["HISTORY"] = f"Export plot type: {'Background Subtracted' if use_processed else 'Raw'}"
    hdr0["BUNIT"] = "Digits"

    primary = fits.PrimaryHDU(data=export_data, header=hdr0)
    try:
        primary.header["BSCALE"] = 1
        primary.header["BZERO"] = 0
    except Exception:
        pass
    try:
        primary.header["DATAMIN"] = float(np.nanmin(export_data))
        primary.header["DATAMAX"] = float(np.nanmax(export_data))
    except Exception:
        pass

    freqs = np.asarray(dataset.freqs, dtype=np.float32)
    times = np.asarray(dataset.time, dtype=np.float32)
    hdul = None
    updated_any = False
    template_path = dataset.source_path
    if template_path and template_path.exists():
        try:
            with fits.open(str(template_path), memmap=False) as template_hdul:
                hdul, updated_any = _build_export_hdul_from_template(template_hdul, primary, freqs, times)
        except Exception:
            hdul = None
            updated_any = False

    if hdul is None:
        cols = fits.ColDefs(
            [
                fits.Column(name="FREQUENCY", format=f"{freqs.size}E", array=[freqs]),
                fits.Column(name="TIME", format=f"{times.size}E", array=[times]),
            ]
        )
        axis_hdu = fits.BinTableHDU.from_columns(cols)
        axis_hdu.header["EXTNAME"] = "AXIS"
        hdul = fits.HDUList([primary, axis_hdu])
    try:
        hdul[0].header["EXTEND"] = True if len(hdul) > 1 else False
    except Exception:
        pass

    buf = BytesIO()
    hdul.writeto(buf, overwrite=True, output_verify="silentfix")
    stem = PathLikeStr(dataset.filename).stem or "export"
    if use_processed:
        stem = f"{stem}_background_subtracted"
    return buf.getvalue(), _download_name(stem, "fit"), "application/fits"


def export_maxima_csv(points: list[dict[str, float]]) -> tuple[bytes, str, str]:
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Time Channel", "Frequency (MHz)"])
    for point in points:
        writer.writerow([float(point["timeSeconds"]), float(point["freqMHz"])])
    return buf.getvalue().encode("utf-8"), "maxima.csv", "text/csv"


_STATION_LIST = [
    "ALASKA-ANCHORAGE", "ALASKA-COHOE", "ALASKA-HAARP", "ALGERIA-CRAAG", "ALMATY",
    "Arecibo-observatory", "AUSTRIA-Krumbach", "AUSTRIA-MICHELBACH", "AUSTRIA-OE3FLB",
    "AUSTRIA-UNIGRAZ", "Australia-ASSA", "BRAZIL", "BIR", "Croatia-Visnjan", "DENMARK",
    "EGYPT-Alexandria", "EGYPT-SpaceAgency", "ETHIOPIA", "FINLAND-Siuntio", "FINLAND-Kempele",
    "GERMANY-ESSEN", "GERMANY-DLR", "GLASGOW", "GREENLAND", "HUMAIN", "HURBANOVO",
    "INDIA-GAURI", "INDIA-Nashik", "INDIA-OOTY", "INDIA-UDAIPUR", "INDONESIA",
    "ITALY-Strassolt", "JAPAN-IBARAKI", "KASI", "KRIM", "MEXART",
    "MEXICO-ENSENADA-UNAM", "MEXICO-FCFM-UANL", "MEXICO-FCFM-UNACH", "MEXICO-LANCE-A",
    "MEXICO-LANCE-B", "MEXICO-UANL-INFIERNILLO", "MONGOLIA-UB", "MRO", "MRT1", "MRT3",
    "Malaysia_Banting", "NASA-GSFC", "NORWAY-EGERSUND", "NORWAY-NY-AALESUND", "NORWAY-RANDABERG",
    "PARAGUAY", "POLAND-BALDY", "POLAND-Grotniki", "ROMANIA", "ROSWELL-NM", "RWANDA",
    "SOUTHAFRICA-SANSA", "SPAIN-ALCALA", "SPAIN-PERALEJOS", "SPAIN-SIGUENZA", "SRI-Lanka",
    "SSRT", "SWISS-CalU", "SWISS-FM", "SWISS-HB9SCT", "SWISS-HEITERSWIL", "SWISS-IRSOL",
    "SWISS-Landschlacht", "SWISS-MUHEN", "TAIWAN-NCU", "THAILAND-Pathumthani", "TRIEST",
    "TURKEY", "UNAM", "URUGUAY", "USA-ARIZONA-ERAU", "USA-BOSTON", "UZBEKISTAN",
]


def _station_from_filename(source_filename: str) -> str:
    filename_lower = str(source_filename).lower()
    for station in _STATION_LIST:
        if filename_lower.startswith(station.lower()):
            return station
    return "UNKNOWN"


def _date_from_filename(source_filename: str) -> str:
    match = re.search(r"_(\d{4})(\d{2})(\d{2})_", str(source_filename))
    if not match:
        return "UNKNOWN"
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def export_analyzer_xlsx(
    analysis_result: dict[str, Any],
    *,
    source_filename: str,
) -> tuple[bytes, str, str]:
    fit = dict(analysis_result.get("fit") or {})
    shock = dict(analysis_result.get("shockSummary") or {})

    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "Date", "Station", "Best_fit", "R_sq", "RMSE",
            "avg_freq", "avg_freq_err", "Avg_drift", "avg_drift_err",
            "start_freq", "start_freq_err", "initial_shock_speed", "initial_shock_speed_err",
            "initial_shock_height", "initial_shock_height_err", "avg_shock_speed", "avg_shock_speed_err",
            "avg_shock_height", "avg_shock_height_err", "avg_drift_abs",
        ]
    )

    avg_drift = shock.get("avgDriftMHzPerSec", "")
    try:
        avg_drift_abs = abs(float(avg_drift))
    except Exception:
        avg_drift_abs = ""

    ws.append(
        [
            _date_from_filename(source_filename),
            _station_from_filename(source_filename),
            str(analysis_result.get("equation") or ""),
            fit.get("r2", ""),
            fit.get("rmse", ""),
            shock.get("avgFreqMHz", ""),
            shock.get("avgFreqErrMHz", ""),
            shock.get("avgDriftMHzPerSec", ""),
            shock.get("avgDriftErrMHzPerSec", ""),
            shock.get("startFreqMHz", ""),
            shock.get("startFreqErrMHz", ""),
            shock.get("initialShockSpeedKmPerSec", ""),
            shock.get("initialShockSpeedErrKmPerSec", ""),
            shock.get("initialShockHeightRs", ""),
            shock.get("initialShockHeightErrRs", ""),
            shock.get("avgShockSpeedKmPerSec", ""),
            shock.get("avgShockSpeedErrKmPerSec", ""),
            shock.get("avgShockHeightRs", ""),
            shock.get("avgShockHeightErrRs", ""),
            avg_drift_abs,
        ]
    )

    buf = BytesIO()
    wb.save(buf)
    stem = os.path.splitext(PathLikeStr(source_filename).stem)[0]
    return buf.getvalue(), _download_name(f"{stem}_analysis", "xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class PathLikeStr(str):
    @property
    def stem(self) -> str:
        text = str(self)
        lower = text.lower()
        for suffix in _FIT_SUFFIXES:
            if lower.endswith(suffix):
                return text[: -len(suffix)]
        return os.path.splitext(text)[0]
