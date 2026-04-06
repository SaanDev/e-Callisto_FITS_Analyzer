"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import numpy as np
import os
import re
from datetime import datetime

from src.Backend.frequency_axis import frequency_step_mhz, invalid_row_mask, orient_frequency_axis, orient_frequency_rows
from src.Backend.fits_io import (
    build_combined_header,
    extract_ut_start_sec,
    load_callisto_fits,
    preview_callisto_fits,
)
from src.Backend.noise_reduction import subtract_background_rows

FREQUENCY_ALIGN_ATOL_MHZ = 1e-3
HEADER_RANGE_TOL_FRACTION = 0.5
GRID_ALIGN_TOL_FRACTION = 0.25
HEADER_FOCUS_KEYS = ("FOCUS", "FOCUSID", "RECEIVER", "RECEIVERID", "RCVR", "RCVRID")
GAP_FILL_EDGE_ROWS = 4
GAP_FILL_BACKGROUND_PERCENTILE = 25.0

def load_fits(filepath):
    res = load_callisto_fits(filepath, memmap=False)
    return res.data, res.freqs, res.time

def reduce_noise(data, clip_low=-5, clip_high=20):
    low = float(min(clip_low, clip_high))
    high = float(max(clip_low, clip_high))
    arr = np.asarray(data, dtype=float)
    data = subtract_background_rows(
        arr,
        method="robust",
        gap_row_mask=invalid_row_mask(arr),
        equalize_noise=bool(np.any(invalid_row_mask(arr))),
    ).astype(float, copy=False)
    gap_rows = invalid_row_mask(data)
    try:
        print("Before clip:", np.nanmin(data), np.nanmax(data))
    except Exception:
        print("Before clip: nan nan")
    data = np.clip(data, low, high)
    if np.any(gap_rows):
        data[gap_rows, :] = np.nan
    # Y-factor style conversion where Icold=low threshold and Ihot is signal.
    data = (data - low) * 2500.0 / 256.0 / 25.4
    return data

def parse_filename(filepath):
    base = os.path.basename(filepath)
    # Support common CALLISTO variants, e.g.:
    #   STATION_YYYYMMDD_HHMMSS_ID.fit(.gz)
    #   STATION_YYYYMMDD_HHMMSS_HHMMSS_ID.fit(.gz)
    stem = base
    for ext in (".fit.gz", ".fits.gz", ".fit", ".fits"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break

    parts = stem.split("_")
    if len(parts) < 4:
        raise ValueError(f"Invalid CALLISTO filename format: {base}")

    station = parts[0]
    date = parts[1]
    time = parts[2]
    receiver_id = parts[-1]

    return station, date, time, receiver_id


def _parse_observation_datetime(filepath):
    station, obs_date, obs_time, receiver_id = parse_filename(filepath)
    observed_at = datetime.strptime(f"{obs_date}{obs_time}", "%Y%m%d%H%M%S")
    return station, observed_at, receiver_id


def are_frequency_combinable(file_paths):
    if len(file_paths) < 2:
        return False

    try:
        _prepare_frequency_blocks(file_paths)
    except Exception:
        return False

    return True


def combine_frequency(file_paths):
    if len(file_paths) < 2:
        raise ValueError("Need at least 2 files to combine frequencies.")

    prepared = _prepare_frequency_blocks(file_paths)
    blocks = prepared["blocks"]
    combined_data = prepared["data"]
    combined_freqs = prepared["freqs"]
    time_ref = prepared["time"]
    gap_row_mask = prepared["gap_row_mask"]
    gap_row_count = int(prepared.get("gap_row_count", 0))
    header0 = prepared["header0"]
    step_mhz = float(prepared["frequency_step_mhz"])

    station, date, tstamp, _ = parse_filename(blocks[0]["path"])
    combined_name = f"{station}_{date}_{tstamp}_freq_combined"

    ut_start_sec = extract_ut_start_sec(header0)

    combined_header = build_combined_header(
        header0,
        mode="frequency",
        sources=[str(block["path"]) for block in blocks],
        data_shape=combined_data.shape,
        freqs=combined_freqs,
        time=time_ref,
    )
    combined_header["CRVAL2"] = (float(combined_freqs[0]), "Value on axis 2 [MHz]")
    combined_header["CRPIX2"] = (1.0, "Reference pixel for axis 2")
    combined_header["CDELT2"] = (-float(step_mhz), "Frequency step [MHz]")
    combined_header["FREQMIN"] = (float(np.nanmin(combined_freqs)), "Min frequency (MHz)")
    combined_header["FREQMAX"] = (float(np.nanmax(combined_freqs)), "Max frequency (MHz)")
    combined_header["HISTORY"] = f"Regularized frequency grid step: {step_mhz:.6f} MHz"
    combined_header["HISTORY"] = f"Inserted background-interpolated gap rows: {gap_row_count}"

    return {
        "data": combined_data,
        "freqs": combined_freqs,
        "time": time_ref,
        "filename": combined_name,
        "ut_start_sec": ut_start_sec,
        "header0": combined_header,
        "sources": [str(block["path"]) for block in blocks],
        "combine_type": "frequency",
        "gap_row_mask": gap_row_mask,
        "frequency_step_mhz": float(step_mhz),
    }


def _prepare_frequency_blocks(file_paths):
    if len(file_paths) < 2:
        raise ValueError("Need at least 2 files to combine frequencies.")

    try:
        s_ref, d_ref, t_ref, _ = parse_filename(file_paths[0])
    except Exception as exc:
        raise ValueError("Invalid CALLISTO filename format.") from exc

    receiver_ids = set()
    previews = []
    time_ref = None
    source_steps = []

    for fp in file_paths:
        try:
            s, d, t, rec = parse_filename(fp)
        except Exception as exc:
            raise ValueError(f"Invalid CALLISTO filename format: {os.path.basename(fp)}") from exc

        if s != s_ref or d != d_ref or t != t_ref:
            raise ValueError("Frequency combine requires the same station, date, and timestamp.")

        rec_norm = _normalize_focus_code(rec)
        if rec_norm in receiver_ids:
            raise ValueError("Frequency combine requires distinct focus codes.")
        receiver_ids.add(rec_norm)

        preview = preview_callisto_fits(fp, memmap=False)
        header_focus = _header_focus_code(preview.header0)
        if header_focus and header_focus != rec_norm:
            raise ValueError(
                f"Focus code mismatch for {os.path.basename(fp)}: filename='{rec_norm}', header='{header_focus}'."
            )

        if preview.freq_source == "index":
            raise ValueError(
                f"Frequency axis metadata are missing in {os.path.basename(fp)}; cannot preflight frequency combine."
            )

        freq_arr = orient_frequency_axis(preview.freqs, direction=1)
        if freq_arr.size == 0:
            raise ValueError("Frequency axis cannot be empty.")
        time_arr = np.asarray(preview.time, dtype=float).ravel()
        if time_arr.size == 0:
            raise ValueError("Time axis cannot be empty.")

        if time_ref is None:
            time_ref = time_arr
        elif not _axes_match(time_arr, time_ref, atol=0.01):
            raise ValueError("Time arrays do not match; cannot frequency-combine.")

        step_mhz = _preview_frequency_step(preview.header0, freq_arr)
        if not np.isfinite(step_mhz) or step_mhz <= 0.0:
            raise ValueError(f"Could not determine channel spacing for {os.path.basename(fp)}.")
        source_steps.append(float(step_mhz))

        _validate_header_frequency_range(preview.header0, freq_arr, step_mhz, os.path.basename(fp))
        range_min, range_max = _resolved_frequency_range(preview.header0, freq_arr)

        previews.append(
            {
                "path": fp,
                "freqs": freq_arr,
                "time": time_arr,
                "header0": preview.header0,
                "focus_code": rec_norm,
                "freq_min": float(range_min),
                "freq_max": float(range_max),
                "frequency_step_mhz": float(step_mhz),
            }
        )

    previews.sort(key=lambda block: (block["freq_min"], block["freq_max"]))

    prev_high = None
    grid_step_ref = float(min(source_steps)) if source_steps else 0.0
    if not np.isfinite(grid_step_ref) or grid_step_ref <= 0.0:
        raise ValueError("Could not determine a shared frequency spacing.")
    overlap_tol = _range_tol(grid_step_ref, fraction=GRID_ALIGN_TOL_FRACTION)
    for block in previews:
        if prev_high is not None and float(block["freq_min"]) <= float(prev_high) + overlap_tol:
            raise ValueError("Frequency bands overlap or interleave; only non-overlapping bands can be combined.")
        prev_high = float(block["freq_max"])

    overall_min = float(previews[0]["freq_min"])
    overall_max = float(previews[-1]["freq_max"])
    total_span = float(overall_max - overall_min)
    if total_span <= 0.0:
        raise ValueError("Combined frequency range must span more than one channel.")

    # Real CALLISTO frequency tables are often irregular, so the full span does
    # not necessarily divide cleanly by a representative channel step. Build
    # the regularized grid from the true span instead of rejecting the combine.
    grid_count = max(1, int(round(total_span / grid_step_ref)))
    freq_grid_asc = np.linspace(overall_min, overall_max, grid_count + 1, dtype=float)
    grid_step = float(freq_grid_asc[1] - freq_grid_asc[0]) if freq_grid_asc.size > 1 else float(grid_step_ref)
    ncols = int(np.asarray(time_ref).size)
    combined_asc = np.zeros((freq_grid_asc.size, ncols), dtype=float)
    filled_row_mask_asc = np.zeros(freq_grid_asc.size, dtype=bool)
    blocks = []

    for preview in previews:
        res = load_callisto_fits(preview["path"], memmap=False)
        data, freqs = orient_frequency_rows(res.data, res.freqs, direction=1)
        freq_arr = np.asarray(freqs, dtype=float).ravel()
        time_arr = np.asarray(res.time, dtype=float).ravel()
        data_arr = np.asarray(data, dtype=float)

        if not _axes_match(time_arr, time_ref, atol=0.01):
            raise ValueError("Time arrays do not match; cannot frequency-combine.")
        if not _axes_match(freq_arr, preview["freqs"], atol=_grid_align_tol(grid_step)):
            raise ValueError(f"Frequency axis changed while loading {os.path.basename(preview['path'])}.")

        band_min = float(preview["freq_min"])
        band_max = float(preview["freq_max"])
        tol = _grid_align_tol(grid_step)
        covered = (freq_grid_asc >= band_min - tol) & (freq_grid_asc <= band_max + tol)
        if not np.any(covered):
            raise ValueError(f"Frequency channels in {os.path.basename(preview['path'])} are outside the combined grid.")
        if np.any(filled_row_mask_asc[covered]):
            raise ValueError("Frequency bands overlap or interleave; only non-overlapping bands can be combined.")

        if freq_arr.size == 1:
            positions = np.zeros(int(np.count_nonzero(covered)), dtype=int)
        else:
            midpoints = 0.5 * (freq_arr[:-1] + freq_arr[1:])
            positions = np.searchsorted(midpoints, freq_grid_asc[covered], side="right")

        combined_asc[covered, :] = data_arr[positions, :]
        filled_row_mask_asc[covered] = True
        loaded_block = dict(preview)
        loaded_block["data"] = data_arr
        blocks.append(loaded_block)

    _fill_frequency_gap_background(
        combined_asc,
        filled_row_mask_asc,
        freq_grid_asc,
        edge_rows=GAP_FILL_EDGE_ROWS,
        percentile=GAP_FILL_BACKGROUND_PERCENTILE,
    )

    combined_data = combined_asc[::-1, :]
    combined_freqs = freq_grid_asc[::-1]
    gap_row_count = int(np.count_nonzero(~filled_row_mask_asc))

    return {
        "blocks": blocks,
        "data": combined_data,
        "freqs": combined_freqs,
        "time": np.asarray(time_ref, dtype=float),
        "gap_row_mask": None,
        "gap_row_count": gap_row_count,
        "header0": previews[0]["header0"],
        "frequency_step_mhz": grid_step,
    }


def _header_focus_code(header0) -> str:
    if header0 is None:
        return ""
    for key in HEADER_FOCUS_KEYS:
        value = header0.get(key, None)
        if value is None:
            continue
        text = _normalize_focus_code(value)
        if text:
            return text
    return ""


def _fill_frequency_gap_background(
    data_asc: np.ndarray,
    filled_row_mask_asc: np.ndarray,
    freqs_asc: np.ndarray,
    *,
    edge_rows: int,
    percentile: float,
) -> None:
    mask = np.asarray(filled_row_mask_asc, dtype=bool).ravel()
    if mask.size == 0 or np.all(mask):
        return

    freqs = np.asarray(freqs_asc, dtype=float).ravel()
    nrows = mask.size
    idx = 0
    while idx < nrows:
        if mask[idx]:
            idx += 1
            continue

        start = idx
        while idx < nrows and not mask[idx]:
            idx += 1
        end = idx

        left_rows = _neighbor_rows(data_asc, mask, start, direction=-1, max_rows=edge_rows)
        right_rows = _neighbor_rows(data_asc, mask, end, direction=1, max_rows=edge_rows)
        left_bg = _edge_background_trace(left_rows, percentile=percentile)
        right_bg = _edge_background_trace(right_rows, percentile=percentile)

        if left_bg is None and right_bg is None:
            continue
        if left_bg is None:
            left_bg = np.asarray(right_bg, dtype=float).copy()
        if right_bg is None:
            right_bg = np.asarray(left_bg, dtype=float).copy()

        if start > 0 and end < nrows:
            left_freq = float(freqs[start - 1])
            right_freq = float(freqs[end])
            span = float(right_freq - left_freq)
            if abs(span) > 1e-12:
                alphas = ((freqs[start:end] - left_freq) / span).astype(float)
            else:
                alphas = np.full(end - start, 0.5, dtype=float)
        else:
            count = end - start
            alphas = np.linspace(1.0 / (count + 1), count / (count + 1), count, dtype=float)

        data_asc[start:end, :] = (
            (1.0 - alphas)[:, None] * left_bg[None, :]
            + alphas[:, None] * right_bg[None, :]
        )


def _neighbor_rows(
    data_asc: np.ndarray,
    filled_row_mask_asc: np.ndarray,
    anchor: int,
    *,
    direction: int,
    max_rows: int,
) -> np.ndarray | None:
    rows = []
    step = -1 if int(direction) < 0 else 1
    idx = int(anchor) - 1 if step < 0 else int(anchor)
    nrows = int(np.asarray(filled_row_mask_asc).size)

    while 0 <= idx < nrows and len(rows) < int(max_rows):
        if not bool(filled_row_mask_asc[idx]):
            break
        rows.append(np.asarray(data_asc[idx, :], dtype=float))
        idx += step

    if not rows:
        return None
    if step < 0:
        rows.reverse()
    return np.vstack(rows)


def _edge_background_trace(rows: np.ndarray | None, *, percentile: float) -> np.ndarray | None:
    if rows is None:
        return None
    arr = np.asarray(rows, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return None
    if arr.shape[0] == 1:
        return arr[0].astype(float, copy=True)
    return np.nanpercentile(arr, float(percentile), axis=0).astype(float, copy=False)


def _normalize_focus_code(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    if not tokens:
        return text.upper()
    ignore = {"FOCUS", "FOCUSCODE", "FOCUSID", "RECEIVER", "RECEIVERID", "RCVR", "RCVRID"}
    filtered = [tok for tok in tokens if tok.upper() not in ignore]
    chosen = filtered[-1] if filtered else tokens[-1]
    return str(chosen).strip().upper()


def _header_frequency_range(header0):
    if header0 is None:
        return None
    try:
        lo = header0.get("FREQMIN", None)
        hi = header0.get("FREQMAX", None)
        if lo is None or hi is None:
            return None
        lo = float(lo)
        hi = float(hi)
    except Exception:
        return None
    if not np.isfinite(lo) or not np.isfinite(hi):
        return None
    return (min(lo, hi), max(lo, hi))


def _preview_frequency_step(header0, freqs: np.ndarray) -> float:
    step = float(frequency_step_mhz(freqs, default=0.0))
    if np.isfinite(step) and step > 0.0:
        return step
    try:
        cdelt = abs(float(header0.get("CDELT2", 0.0)))
    except Exception:
        cdelt = 0.0
    return float(cdelt)


def _validate_header_frequency_range(header0, freqs: np.ndarray, step_mhz: float, filename: str) -> None:
    hdr_range = _header_frequency_range(header0)
    if hdr_range is None:
        return
    axis_min = float(np.nanmin(freqs))
    axis_max = float(np.nanmax(freqs))
    tol = _range_tol(step_mhz, fraction=HEADER_RANGE_TOL_FRACTION)
    if abs(axis_min - hdr_range[0]) > tol or abs(axis_max - hdr_range[1]) > tol:
        raise ValueError(
            f"Header frequency range does not match axis values for {filename}: "
            f"header={hdr_range[0]:.6f}-{hdr_range[1]:.6f} MHz, "
            f"axis={axis_min:.6f}-{axis_max:.6f} MHz."
        )


def _resolved_frequency_range(header0, freqs: np.ndarray) -> tuple[float, float]:
    hdr_range = _header_frequency_range(header0)
    if hdr_range is not None:
        return float(hdr_range[0]), float(hdr_range[1])
    return float(np.nanmin(freqs)), float(np.nanmax(freqs))


def _axes_match(a, b, atol: float) -> bool:
    arr_a = np.asarray(a, dtype=float).ravel()
    arr_b = np.asarray(b, dtype=float).ravel()
    if arr_a.shape != arr_b.shape:
        return False
    return bool(np.allclose(arr_a, arr_b, atol=float(atol), rtol=0.0))


def _range_tol(step_mhz: float, *, fraction: float) -> float:
    return max(FREQUENCY_ALIGN_ATOL_MHZ, abs(float(step_mhz)) * float(fraction))


def _grid_align_tol(step_mhz: float) -> float:
    return _range_tol(step_mhz, fraction=GRID_ALIGN_TOL_FRACTION)



def are_time_combinable(file_paths):
    if len(file_paths) < 2:
        return False

    try:
        sorted_paths = sorted(
            file_paths,
            key=lambda p: _parse_observation_datetime(p)[1]
        )
    except Exception:
        return False

    s_ref, dt_ref, foc_ref = _parse_observation_datetime(sorted_paths[0])
    _, freqs_ref, _ = load_fits(sorted_paths[0])
    t_prev = dt_ref

    for fp in sorted_paths[1:]:
        s, t_now, foc = _parse_observation_datetime(fp)

        if s != s_ref:
            return False
        if foc != foc_ref:
            return False

        _, freqs, _ = load_fits(fp)
        if not np.allclose(freqs, freqs_ref, atol=0.01):
            return False

        diff = abs((t_now - t_prev).total_seconds())

        if not (750 <= diff <= 1050):
            return False

        t_prev = t_now

    return True


def combine_time(file_paths):
    sorted_paths = sorted(
        file_paths,
        key=lambda p: _parse_observation_datetime(p)[1]
    )

    combined_data = None
    combined_time = None
    reference_freqs = None
    header0 = None

    for idx, f in enumerate(sorted_paths):
        res = load_callisto_fits(f, memmap=False)
        data, freqs, time = res.data, res.freqs, res.time

        if reference_freqs is None:
            reference_freqs = freqs
            combined_data = data
            combined_time = time
            dt = time[1] - time[0]
            header0 = res.header0
        else:

            dt = time[1] - time[0]
            shift = combined_time[-1] + dt
            adjusted_time = time + shift

            combined_data = np.concatenate((combined_data, data), axis=1)
            combined_time = np.concatenate((combined_time, adjusted_time))

    s, d, t, foc = parse_filename(sorted_paths[0])
    combined_name = f"{s}_{d}_combined_time"

    ut_start_sec = extract_ut_start_sec(header0)

    combined_header = build_combined_header(
        header0,
        mode="time",
        sources=sorted_paths,
        data_shape=combined_data.shape,
        freqs=reference_freqs,
        time=combined_time,
    )

    return {
        "data": combined_data,
        "freqs": reference_freqs,
        "time": combined_time,
        "filename": combined_name,
        "ut_start_sec": ut_start_sec,
        "header0": combined_header,
        "sources": list(sorted_paths),
        "combine_type": "time",
    }
