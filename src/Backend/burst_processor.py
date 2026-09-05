"""
e-CALLISTO FITS Analyzer
Version 3.0.0
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
GAP_FILL_BACKGROUND = "background"
GAP_FILL_HATCHED = "hatched"
GAP_FILL_ZERO = "zero"
GAP_FILL_AVERAGE = "average"
OVERLAP_SPLIT = "split"
OVERLAP_LOW = "low"
OVERLAP_HIGH = "high"
OVERLAP_REJECT = "reject"
COMBINE_TIME = "time"
COMBINE_FREQUENCY = "frequency"
COMBINE_TIME_FREQUENCY = "time_frequency"
MIN_CONSECUTIVE_SECONDS = 750.0
MAX_CONSECUTIVE_SECONDS = 1050.0

def load_fits(filepath):
    res = load_callisto_fits(filepath, memmap=False)
    return res.data, res.freqs, res.time

def reduce_noise(data, clip_low=-5, clip_high=20):
    low = float(min(clip_low, clip_high))
    high = float(max(clip_low, clip_high))
    arr = np.asarray(data, dtype=float)
    source_gap_rows = invalid_row_mask(arr)
    data = subtract_background_rows(
        arr,
        method="robust",
        gap_row_mask=source_gap_rows,
        equalize_noise=bool(np.any(source_gap_rows)),
    ).astype(float, copy=False)
    gap_rows = invalid_row_mask(data)
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


def _invalid_inspection(error: str, **details) -> dict:
    result = {
        "valid": False,
        "combine_type": details.pop("combine_type", None),
        "station": details.pop("station", ""),
        "timestamp_groups": details.pop("timestamp_groups", []),
        "focus_codes": details.pop("focus_codes", []),
        "frequency_relation": details.pop("frequency_relation", None),
        "error": str(error),
    }
    result.update(details)
    return result


def _classify_combination_structure(file_paths) -> dict:
    paths = [str(path) for path in list(file_paths or []) if str(path or "").strip()]
    if len(paths) < 2:
        return _invalid_inspection("Select at least two FITS files to combine.")

    records = []
    for path in paths:
        try:
            station, observed_at, focus_code = _parse_observation_datetime(path)
        except Exception as exc:
            return _invalid_inspection(
                f"Invalid CALLISTO filename format: {os.path.basename(path)}"
            )
        records.append(
            {
                "path": path,
                "station": station,
                "observed_at": observed_at,
                "focus_code": _normalize_focus_code(focus_code),
            }
        )

    stations = sorted({record["station"] for record in records})
    if len(stations) != 1:
        return _invalid_inspection(
            "All files in one combined dataset must come from the same station; "
            f"found: {', '.join(stations)}."
        )

    station = stations[0]
    grouped: dict[datetime, dict[str, str]] = {}
    for record in records:
        observed_at = record["observed_at"]
        focus_code = record["focus_code"]
        cells = grouped.setdefault(observed_at, {})
        if focus_code in cells:
            stamp = observed_at.strftime("%Y-%m-%d %H:%M:%S")
            return _invalid_inspection(
                f"Duplicate focus code '{focus_code}' at {stamp}: "
                f"{os.path.basename(cells[focus_code])} and {os.path.basename(record['path'])}.",
                station=station,
            )
        cells[focus_code] = record["path"]

    ordered_times = sorted(grouped)
    all_focus_codes = sorted({focus for cells in grouped.values() for focus in cells})
    timestamp_groups = [
        [grouped[observed_at][focus] for focus in sorted(grouped[observed_at])]
        for observed_at in ordered_times
    ]

    common = {
        "station": station,
        "timestamp_groups": timestamp_groups,
        "focus_codes": all_focus_codes,
        "observed_at": ordered_times,
    }

    if len(ordered_times) == 1:
        if len(all_focus_codes) < 2:
            return _invalid_inspection(
                "Frequency combine requires at least two distinct focus codes at one timestamp.",
                **common,
            )
        return {"valid": True, "combine_type": COMBINE_FREQUENCY, "error": None, **common}

    expected_focus = set(all_focus_codes)
    if len(expected_focus) > 1:
        for observed_at in ordered_times:
            actual_focus = set(grouped[observed_at])
            if actual_focus != expected_focus:
                missing = sorted(expected_focus - actual_focus)
                extra = sorted(actual_focus - expected_focus)
                parts = []
                if missing:
                    parts.append(f"missing focus code(s): {', '.join(missing)}")
                if extra:
                    parts.append(f"unexpected focus code(s): {', '.join(extra)}")
                stamp = observed_at.strftime("%Y-%m-%d %H:%M:%S")
                return _invalid_inspection(
                    f"Incomplete time/frequency grid at {stamp} ({'; '.join(parts)}).",
                    combine_type=COMBINE_TIME_FREQUENCY,
                    **common,
                )
        combine_type = COMBINE_TIME_FREQUENCY
    else:
        combine_type = COMBINE_TIME

    for previous, current in zip(ordered_times, ordered_times[1:]):
        delta = float((current - previous).total_seconds())
        if not (MIN_CONSECUTIVE_SECONDS <= delta <= MAX_CONSECUTIVE_SECONDS):
            return _invalid_inspection(
                "Non-consecutive observation timestamps: "
                f"{previous.strftime('%Y-%m-%d %H:%M:%S')} to "
                f"{current.strftime('%Y-%m-%d %H:%M:%S')} is {delta:.0f} seconds; "
                f"expected {MIN_CONSECUTIVE_SECONDS:.0f}-{MAX_CONSECUTIVE_SECONDS:.0f} seconds.",
                combine_type=combine_type,
                **common,
            )

    return {"valid": True, "combine_type": combine_type, "error": None, **common}


def inspect_combination(file_paths) -> dict:
    """Inspect and fully validate a multi-file FITS combination selection."""
    structural = _classify_combination_structure(file_paths)
    if not structural.get("valid", False):
        return structural

    combine_type = structural["combine_type"]
    relation = None
    try:
        if combine_type == COMBINE_TIME:
            combine_time(file_paths)
        elif combine_type == COMBINE_FREQUENCY:
            group = structural["timestamp_groups"][0]
            relation = describe_frequency_combination(group)
            combine_frequency(group)
        elif combine_type == COMBINE_TIME_FREQUENCY:
            first_group = structural["timestamp_groups"][0]
            relation = describe_frequency_combination(first_group)
            combine_time_frequency(file_paths)
        else:
            raise ValueError(f"Unsupported combine type: {combine_type}")
    except Exception as exc:
        return _invalid_inspection(
            str(exc),
            combine_type=combine_type,
            station=structural.get("station", ""),
            timestamp_groups=structural.get("timestamp_groups", []),
            focus_codes=structural.get("focus_codes", []),
            frequency_relation=relation,
            observed_at=structural.get("observed_at", []),
        )

    result = dict(structural)
    result["frequency_relation"] = relation
    result["valid"] = True
    result["error"] = None
    return result


def are_frequency_combinable(
    file_paths,
    *,
    gap_fill: str = GAP_FILL_BACKGROUND,
    overlap_policy: str = OVERLAP_SPLIT,
    overlap_connection_mhz: float | None = None,
):
    if len(file_paths) < 2:
        return False

    try:
        _prepare_frequency_blocks(
            file_paths,
            gap_fill=gap_fill,
            overlap_policy=overlap_policy,
            overlap_connection_mhz=overlap_connection_mhz,
        )
    except Exception:
        return False

    return True


def combine_frequency(
    file_paths,
    *,
    gap_fill: str = GAP_FILL_BACKGROUND,
    overlap_policy: str = OVERLAP_SPLIT,
    overlap_connection_mhz: float | None = None,
):
    if len(file_paths) < 2:
        raise ValueError("Need at least 2 files to combine frequencies.")

    prepared = _prepare_frequency_blocks(
        file_paths,
        gap_fill=gap_fill,
        overlap_policy=overlap_policy,
        overlap_connection_mhz=overlap_connection_mhz,
    )
    blocks = prepared["blocks"]
    combined_data = prepared["data"]
    combined_freqs = prepared["freqs"]
    time_ref = prepared["time"]
    gap_row_mask = prepared["gap_row_mask"]
    gap_row_count = int(prepared.get("gap_row_count", 0))
    gap_fill_mode = prepared.get("gap_fill", GAP_FILL_BACKGROUND)
    overlap_mode = prepared.get("overlap_policy", OVERLAP_SPLIT)
    overlap_connection = prepared.get("overlap_connection_mhz", None)
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
    combined_header["HISTORY"] = f"Frequency gap fill mode: {gap_fill_mode}; rows: {gap_row_count}"
    combined_header["HISTORY"] = f"Frequency overlap policy: {overlap_mode}"
    if overlap_connection is not None:
        combined_header["HISTORY"] = f"Overlap connection frequency: {float(overlap_connection):.6f} MHz"

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
        "gap_fill": gap_fill_mode,
        "overlap_policy": overlap_mode,
        "overlap_connection_mhz": overlap_connection,
    }


def describe_frequency_combination(file_paths) -> dict:
    if len(file_paths) < 2:
        return {
            "has_gap": False,
            "has_overlap": False,
            "gaps": [],
            "overlaps": [],
            "blocks": [],
        }

    blocks = []
    steps = []
    for fp in file_paths:
        try:
            station, obs_date, obs_time, focus_code = parse_filename(fp)
        except Exception:
            station, obs_date, obs_time, focus_code = "", "", "", ""

        preview = preview_callisto_fits(fp, memmap=False)
        freq_arr = orient_frequency_axis(preview.freqs, direction=1)
        if freq_arr.size == 0:
            continue

        step_mhz = _preview_frequency_step(preview.header0, freq_arr)
        if np.isfinite(step_mhz) and step_mhz > 0.0:
            steps.append(float(step_mhz))

        range_min, range_max = _resolved_frequency_range(preview.header0, freq_arr)
        blocks.append(
            {
                "path": fp,
                "station": station,
                "date": obs_date,
                "time": obs_time,
                "focus_code": _normalize_focus_code(focus_code),
                "freq_min": float(range_min),
                "freq_max": float(range_max),
                "frequency_step_mhz": float(step_mhz) if np.isfinite(step_mhz) else None,
            }
        )

    blocks.sort(key=lambda item: (item["freq_min"], item["freq_max"], str(item["path"])))
    step_ref = float(min(steps)) if steps else 1.0
    tol = _range_tol(step_ref, fraction=GRID_ALIGN_TOL_FRACTION)
    gaps = []
    overlaps = []

    if blocks:
        active = blocks[0]
        active_high = float(active["freq_max"])
        for block in blocks[1:]:
            block_low = float(block["freq_min"])
            block_high = float(block["freq_max"])

            if block_low > active_high + tol:
                gaps.append(
                    {
                        "low": float(active_high),
                        "high": float(block_low),
                        "lower_file": active["path"],
                        "higher_file": block["path"],
                    }
                )
            else:
                overlap_low = max(float(active["freq_min"]), block_low)
                overlap_high = min(active_high, block_high)
                if overlap_high >= overlap_low - tol:
                    overlaps.append(
                        {
                            "low": float(overlap_low),
                            "high": float(overlap_high),
                            "lower_file": active["path"],
                            "higher_file": block["path"],
                        }
                    )

            if block_high > active_high:
                active = block
                active_high = block_high

    return {
        "has_gap": bool(gaps),
        "has_overlap": bool(overlaps),
        "gaps": gaps,
        "overlaps": overlaps,
        "blocks": blocks,
    }


def _prepare_frequency_blocks(
    file_paths,
    *,
    gap_fill: str = GAP_FILL_BACKGROUND,
    overlap_policy: str = OVERLAP_SPLIT,
    overlap_connection_mhz: float | None = None,
):
    if len(file_paths) < 2:
        raise ValueError("Need at least 2 files to combine frequencies.")

    gap_fill_mode = _normalize_gap_fill(gap_fill)
    overlap_mode = _normalize_overlap_policy(overlap_policy)
    connection_mhz = _optional_float(overlap_connection_mhz)

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
            if overlap_mode == OVERLAP_REJECT:
                raise ValueError("Frequency bands overlap or interleave; selected overlap policy rejects overlap.")
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
        if freq_arr.size == 1:
            positions = np.zeros(int(np.count_nonzero(covered)), dtype=int)
        else:
            midpoints = 0.5 * (freq_arr[:-1] + freq_arr[1:])
            positions = np.searchsorted(midpoints, freq_grid_asc[covered], side="right")

        target_rows = np.flatnonzero(covered)
        write_rows = np.ones(target_rows.size, dtype=bool)
        overlapped = filled_row_mask_asc[target_rows]
        if np.any(overlapped):
            if overlap_mode == OVERLAP_REJECT:
                raise ValueError("Frequency bands overlap or interleave; selected overlap policy rejects overlap.")
            overlap_freqs = freq_grid_asc[target_rows[overlapped]]
            replace = _overlap_replace_mask(
                overlap_freqs,
                overlap_policy=overlap_mode,
                overlap_min=max(float(preview["freq_min"]), float(np.nanmin(overlap_freqs))),
                overlap_max=min(float(preview["freq_max"]), float(np.nanmax(overlap_freqs))),
                connection_mhz=connection_mhz,
            )
            write_rows[overlapped] = replace

        if np.any(write_rows):
            write_targets = target_rows[write_rows]
            combined_asc[write_targets, :] = data_arr[positions[write_rows], :]
            filled_row_mask_asc[write_targets] = True
        loaded_block = dict(preview)
        loaded_block["data"] = data_arr
        blocks.append(loaded_block)

    gap_row_mask_asc = ~filled_row_mask_asc
    gap_row_count = int(np.count_nonzero(gap_row_mask_asc))
    if gap_row_count:
        if gap_fill_mode == GAP_FILL_BACKGROUND:
            _fill_frequency_gap_background(
                combined_asc,
                filled_row_mask_asc,
                freq_grid_asc,
                edge_rows=GAP_FILL_EDGE_ROWS,
                percentile=GAP_FILL_BACKGROUND_PERCENTILE,
                interpolate=True,
            )
        elif gap_fill_mode == GAP_FILL_AVERAGE:
            _fill_frequency_gap_background(
                combined_asc,
                filled_row_mask_asc,
                freq_grid_asc,
                edge_rows=GAP_FILL_EDGE_ROWS,
                percentile=GAP_FILL_BACKGROUND_PERCENTILE,
                interpolate=False,
            )
        elif gap_fill_mode == GAP_FILL_HATCHED:
            combined_asc[gap_row_mask_asc, :] = np.nan
        elif gap_fill_mode == GAP_FILL_ZERO:
            combined_asc[gap_row_mask_asc, :] = 0.0

    combined_data = combined_asc[::-1, :]
    combined_freqs = freq_grid_asc[::-1]
    gap_row_mask = gap_row_mask_asc[::-1] if gap_fill_mode == GAP_FILL_HATCHED and gap_row_count else None

    return {
        "blocks": blocks,
        "data": combined_data,
        "freqs": combined_freqs,
        "time": np.asarray(time_ref, dtype=float),
        "gap_row_mask": gap_row_mask,
        "gap_row_count": gap_row_count,
        "header0": previews[0]["header0"],
        "frequency_step_mhz": grid_step,
        "gap_fill": gap_fill_mode,
        "overlap_policy": overlap_mode,
        "overlap_connection_mhz": connection_mhz,
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
    interpolate: bool,
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

        if not bool(interpolate):
            alphas = np.full(end - start, 0.5, dtype=float)
        elif start > 0 and end < nrows:
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


def _normalize_gap_fill(value: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "background": GAP_FILL_BACKGROUND,
        "interpolate": GAP_FILL_BACKGROUND,
        "interpolated": GAP_FILL_BACKGROUND,
        "synthetic": GAP_FILL_BACKGROUND,
        "synthetic_background": GAP_FILL_BACKGROUND,
        "hatched": GAP_FILL_HATCHED,
        "hatch": GAP_FILL_HATCHED,
        "blank": GAP_FILL_HATCHED,
        "nan": GAP_FILL_HATCHED,
        "grey_hatched": GAP_FILL_HATCHED,
        "gray_hatched": GAP_FILL_HATCHED,
        "zero": GAP_FILL_ZERO,
        "zeros": GAP_FILL_ZERO,
        "average": GAP_FILL_AVERAGE,
        "mean": GAP_FILL_AVERAGE,
        "edge_average": GAP_FILL_AVERAGE,
    }
    if text in aliases:
        return aliases[text]
    raise ValueError(f"Unsupported frequency gap fill mode: {value}")


def _normalize_overlap_policy(value: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "split": OVERLAP_SPLIT,
        "both": OVERLAP_SPLIT,
        "connection": OVERLAP_SPLIT,
        "connect": OVERLAP_SPLIT,
        "midpoint": OVERLAP_SPLIT,
        "low": OVERLAP_LOW,
        "low_band": OVERLAP_LOW,
        "prefer_low": OVERLAP_LOW,
        "keep_low": OVERLAP_LOW,
        "lower": OVERLAP_LOW,
        "high": OVERLAP_HIGH,
        "high_band": OVERLAP_HIGH,
        "prefer_high": OVERLAP_HIGH,
        "keep_high": OVERLAP_HIGH,
        "upper": OVERLAP_HIGH,
        "reject": OVERLAP_REJECT,
        "none": OVERLAP_REJECT,
    }
    if text in aliases:
        return aliases[text]
    raise ValueError(f"Unsupported frequency overlap policy: {value}")


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if not np.isfinite(out):
        return None
    return out


def _overlap_replace_mask(
    freqs: np.ndarray,
    *,
    overlap_policy: str,
    overlap_min: float,
    overlap_max: float,
    connection_mhz: float | None,
) -> np.ndarray:
    arr = np.asarray(freqs, dtype=float).ravel()
    if overlap_policy == OVERLAP_LOW:
        return np.zeros(arr.size, dtype=bool)
    if overlap_policy == OVERLAP_HIGH:
        return np.ones(arr.size, dtype=bool)
    if overlap_policy == OVERLAP_REJECT:
        raise ValueError("Frequency bands overlap or interleave; selected overlap policy rejects overlap.")

    lo = min(float(overlap_min), float(overlap_max))
    hi = max(float(overlap_min), float(overlap_max))
    connection = float(connection_mhz) if connection_mhz is not None else 0.5 * (lo + hi)
    connection = min(max(connection, lo), hi)
    return arr > connection


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



def _validated_time_paths(file_paths) -> list[str]:
    structural = _classify_combination_structure(file_paths)
    if not structural.get("valid", False) or structural.get("combine_type") != COMBINE_TIME:
        raise ValueError(structural.get("error") or "The selected files are not time-combinable.")
    return [group[0] for group in structural["timestamp_groups"]]


def _time_sample_step(time_axis, *, source_label: str) -> float:
    arr = np.asarray(time_axis, dtype=float).ravel()
    if arr.size < 2:
        raise ValueError(f"{source_label} must contain at least two time samples.")
    step = float(arr[1] - arr[0])
    if not np.isfinite(step) or step <= 0.0:
        raise ValueError(f"{source_label} has an invalid time sample interval.")
    return step


def _normalized_gap_mask(value, row_count: int):
    if value is None:
        return None
    mask = np.asarray(value, dtype=bool).ravel()
    if mask.size != int(row_count):
        raise ValueError("Frequency gap mask does not match the combined frequency axis.")
    return mask


def _stitch_time_segments(segments, *, combine_type: str, filename: str, sources) -> dict:
    if len(segments) < 2:
        raise ValueError("Need at least two time segments to combine.")

    combined_data = None
    combined_time = None
    reference_freqs = None
    reference_gap_mask = None
    reference_step = None
    header0 = None

    for index, segment in enumerate(segments):
        data = np.asarray(segment["data"])
        freqs = np.asarray(segment["freqs"], dtype=float).ravel()
        time_axis = np.asarray(segment["time"], dtype=float).ravel()
        label = str(segment.get("filename") or f"segment {index + 1}")

        if data.ndim != 2 or data.shape != (freqs.size, time_axis.size):
            raise ValueError(
                f"{label} data shape {data.shape} does not match its frequency/time axes "
                f"({freqs.size}, {time_axis.size})."
            )
        step = _time_sample_step(time_axis, source_label=label)
        gap_mask = _normalized_gap_mask(segment.get("gap_row_mask"), freqs.size)

        if index == 0:
            reference_freqs = freqs
            reference_gap_mask = gap_mask
            reference_step = segment.get("frequency_step_mhz", None)
            combined_data = data
            combined_time = time_axis
            header0 = segment.get("header0", None)
            continue

        if not _axes_match(freqs, reference_freqs, atol=0.01):
            raise ValueError(f"Combined frequency axis mismatch in {label}.")
        if (reference_gap_mask is None) != (gap_mask is None):
            raise ValueError(f"Frequency gap layout mismatch in {label}.")
        if reference_gap_mask is not None and not np.array_equal(gap_mask, reference_gap_mask):
            raise ValueError(f"Frequency gap layout mismatch in {label}.")

        step_mhz = segment.get("frequency_step_mhz", None)
        if reference_step is not None or step_mhz is not None:
            try:
                if reference_step is None or step_mhz is None or not np.isclose(
                    float(step_mhz), float(reference_step), atol=FREQUENCY_ALIGN_ATOL_MHZ, rtol=0.0
                ):
                    raise ValueError(f"Frequency channel spacing mismatch in {label}.")
            except (TypeError, ValueError):
                raise ValueError(f"Frequency channel spacing mismatch in {label}.")

        shift = float(combined_time[-1]) + step
        adjusted_time = time_axis + shift
        combined_data = np.concatenate((combined_data, data), axis=1)
        combined_time = np.concatenate((combined_time, adjusted_time))

    source_list = [str(path) for path in list(sources or [])]
    combined_header = build_combined_header(
        header0,
        mode=combine_type,
        sources=source_list,
        data_shape=combined_data.shape,
        freqs=reference_freqs,
        time=combined_time,
    )

    return {
        "data": combined_data,
        "freqs": reference_freqs,
        "time": combined_time,
        "filename": filename,
        "ut_start_sec": extract_ut_start_sec(header0),
        "header0": combined_header,
        "sources": source_list,
        "combine_type": combine_type,
        "gap_row_mask": reference_gap_mask,
        "frequency_step_mhz": reference_step,
    }


def are_time_combinable(file_paths):
    try:
        sorted_paths = _validated_time_paths(file_paths)
        reference_freqs = None
        for path in sorted_paths:
            _data, freqs, _time = load_fits(path)
            if reference_freqs is None:
                reference_freqs = np.asarray(freqs, dtype=float).ravel()
            elif not _axes_match(freqs, reference_freqs, atol=0.01):
                return False
    except Exception:
        return False
    return True


def combine_time(file_paths):
    sorted_paths = _validated_time_paths(file_paths)
    segments = []
    for path in sorted_paths:
        result = load_callisto_fits(path, memmap=False)
        segments.append(
            {
                "data": result.data,
                "freqs": result.freqs,
                "time": result.time,
                "filename": os.path.basename(path),
                "header0": result.header0,
            }
        )

    station, obs_date, _obs_time, _focus = parse_filename(sorted_paths[0])
    return _stitch_time_segments(
        segments,
        combine_type=COMBINE_TIME,
        filename=f"{station}_{obs_date}_combined_time",
        sources=sorted_paths,
    )


def _frequency_relations_match(first: dict, second: dict) -> bool:
    for key in ("gaps", "overlaps"):
        first_ranges = np.asarray(
            [(float(item["low"]), float(item["high"])) for item in list(first.get(key) or [])],
            dtype=float,
        ).reshape(-1, 2)
        second_ranges = np.asarray(
            [(float(item["low"]), float(item["high"])) for item in list(second.get(key) or [])],
            dtype=float,
        ).reshape(-1, 2)
        if first_ranges.shape != second_ranges.shape:
            return False
        if first_ranges.size and not np.allclose(
            first_ranges,
            second_ranges,
            atol=FREQUENCY_ALIGN_ATOL_MHZ,
            rtol=0.0,
        ):
            return False
    return True


def combine_time_frequency(
    file_paths,
    *,
    gap_fill: str = GAP_FILL_BACKGROUND,
    overlap_policy: str = OVERLAP_SPLIT,
    overlap_connection_mhz: float | None = None,
):
    """Frequency-combine each timestamp in a complete grid, then stitch in time."""
    structural = _classify_combination_structure(file_paths)
    if not structural.get("valid", False) or structural.get("combine_type") != COMBINE_TIME_FREQUENCY:
        raise ValueError(structural.get("error") or "The selected files are not time-and-frequency combinable.")

    segments = []
    sources = []
    reference_relation = None
    for group in structural["timestamp_groups"]:
        relation = describe_frequency_combination(group)
        if reference_relation is None:
            reference_relation = relation
        elif not _frequency_relations_match(reference_relation, relation):
            stamp = parse_filename(group[0])[2]
            raise ValueError(
                f"Frequency gap/overlap layout at timestamp {stamp} does not match the first timestamp."
            )

        combined = combine_frequency(
            group,
            gap_fill=gap_fill,
            overlap_policy=overlap_policy,
            overlap_connection_mhz=overlap_connection_mhz,
        )
        segments.append(combined)
        sources.extend(combined.get("sources", group))

    station = structural["station"]
    first_date = structural["observed_at"][0].strftime("%Y%m%d")
    result = _stitch_time_segments(
        segments,
        combine_type=COMBINE_TIME_FREQUENCY,
        filename=f"{station}_{first_date}_time_frequency_combined",
        sources=sources,
    )
    result.update(
        {
            "gap_fill": _normalize_gap_fill(gap_fill),
            "overlap_policy": _normalize_overlap_policy(overlap_policy),
            "overlap_connection_mhz": _optional_float(overlap_connection_mhz),
            "timestamp_count": len(structural["timestamp_groups"]),
            "focus_codes": list(structural["focus_codes"]),
        }
    )
    try:
        result["header0"]["NTIMES"] = (len(structural["timestamp_groups"]), "Number of observation timestamps")
        result["header0"]["NFOCUS"] = (len(structural["focus_codes"]), "Number of receiver focus codes")
    except Exception:
        pass
    return result


def are_time_frequency_combinable(file_paths):
    try:
        structural = _classify_combination_structure(file_paths)
        if not structural.get("valid", False) or structural.get("combine_type") != COMBINE_TIME_FREQUENCY:
            return False
        combine_time_frequency(file_paths)
    except Exception:
        return False
    return True


def combine_compatible(
    file_paths,
    *,
    gap_fill: str = GAP_FILL_BACKGROUND,
    overlap_policy: str = OVERLAP_SPLIT,
    overlap_connection_mhz: float | None = None,
):
    """Detect and combine a valid time, frequency, or time-frequency selection."""
    structural = _classify_combination_structure(file_paths)
    if not structural.get("valid", False):
        raise ValueError(structural.get("error") or "The selected FITS files cannot be combined.")

    combine_type = structural["combine_type"]
    if combine_type == COMBINE_TIME:
        return combine_time(file_paths)
    if combine_type == COMBINE_FREQUENCY:
        return combine_frequency(
            file_paths,
            gap_fill=gap_fill,
            overlap_policy=overlap_policy,
            overlap_connection_mhz=overlap_connection_mhz,
        )
    if combine_type == COMBINE_TIME_FREQUENCY:
        return combine_time_frequency(
            file_paths,
            gap_fill=gap_fill,
            overlap_policy=overlap_policy,
            overlap_connection_mhz=overlap_connection_mhz,
        )
    raise ValueError(f"Unsupported combine type: {combine_type}")


def _time_frequency_candidate_runs(paths) -> list[list[str]]:
    """Maximal runs of consecutive timestamps that share one complete focus-code set.

    Selecting a whole day and one stray file must still find the grid inside it,
    so the search is per run rather than per station: requiring the entire
    station group to be valid would never fire for a single-station selection.
    """
    by_station: dict[str, dict] = {}
    rejected: set[str] = set()
    for path in paths:
        try:
            station, obs_date, obs_time, focus_code = parse_filename(path)
            observed_at = datetime.strptime(f"{obs_date}{obs_time}", "%Y%m%d%H%M%S")
        except Exception:
            continue
        cells = by_station.setdefault(station, {}).setdefault(observed_at, {})
        focus_key = _normalize_focus_code(focus_code)
        if focus_key in cells:
            # Duplicate focus code at one timestamp; leave this station to the
            # time-only and frequency-only passes.
            rejected.add(station)
            continue
        cells[focus_key] = path

    runs: list[list[str]] = []
    for station in sorted(by_station):
        if station in rejected:
            continue
        grouped = by_station[station]

        def _flush(stamps):
            if len(stamps) < 2:
                return
            runs.append(
                [grouped[stamp][focus] for stamp in stamps for focus in sorted(grouped[stamp])]
            )

        current: list[datetime] = []
        for stamp in sorted(grouped):
            focus_set = set(grouped[stamp])
            if len(focus_set) < 2:
                _flush(current)
                current = []
                continue
            if current:
                delta = float((stamp - current[-1]).total_seconds())
                consecutive = MIN_CONSECUTIVE_SECONDS <= delta <= MAX_CONSECUTIVE_SECONDS
                if not consecutive or set(grouped[current[-1]]) != focus_set:
                    _flush(current)
                    current = []
            current.append(stamp)
        _flush(current)

    return runs


def group_combinable_paths(
    file_paths,
    *,
    combine: bool = True,
    gap_fill: str = GAP_FILL_BACKGROUND,
    overlap_policy: str = OVERLAP_SPLIT,
    overlap_connection_mhz: float | None = None,
) -> list[dict]:
    """Split a selection into the largest valid combinations, leftovers as singles.

    Returns one entry per resulting dataset::

        {"paths": [...], "combine_type": "time_frequency"|"time"|"frequency"|None,
         "combined": dict|None, "error": str}

    ``combine=True`` carries the merged payload so callers never redo the work.
    Groups keep the order of their earliest member in ``file_paths``.
    """
    options = {
        "gap_fill": gap_fill,
        "overlap_policy": overlap_policy,
        "overlap_connection_mhz": overlap_connection_mhz,
    }

    paths: list[str] = []
    seen: set[str] = set()
    for path in file_paths or []:
        text = str(path or "").strip()
        if not text or text in seen:
            continue
        paths.append(text)
        seen.add(text)
    if not paths:
        return []

    if len(paths) > 1:
        try:
            inspection = inspect_combination(paths)
            if inspection.get("valid", False):
                combine_type = str(inspection.get("combine_type") or "")
                return [
                    {
                        "paths": list(paths),
                        "combine_type": combine_type,
                        "combined": combine_compatible(paths, **options) if combine else None,
                        "error": "",
                    }
                ]
        except Exception:
            pass

    path_index = {path: idx for idx, path in enumerate(paths)}
    consumed: set[str] = set()
    output: list[tuple[int, dict]] = []

    def _group_by(key_func):
        groups: dict[tuple, list[str]] = {}
        for path in paths:
            if path in consumed:
                continue
            try:
                key = key_func(path)
            except Exception:
                continue
            groups.setdefault(tuple(key), []).append(path)
        return sorted(groups.values(), key=lambda group: min(path_index[path] for path in group))

    def _append(group: list[str], combine_type: str, payload) -> None:
        for path in group:
            consumed.add(path)
        output.append(
            (
                min(path_index[path] for path in group),
                {
                    "paths": list(group),
                    "combine_type": combine_type,
                    "combined": payload,
                    "error": "",
                },
            )
        )

    # A complete time x focus-code grid must be handled before the legacy
    # time-only grouping consumes each focus code as a separate panel.
    for group in _time_frequency_candidate_runs(paths):
        if len(group) < 4 or any(path in consumed for path in group):
            continue
        try:
            inspection = inspect_combination(group)
            if inspection.get("valid", False) and inspection.get("combine_type") == COMBINE_TIME_FREQUENCY:
                _append(
                    group,
                    COMBINE_TIME_FREQUENCY,
                    combine_compatible(group, **options) if combine else None,
                )
        except Exception:
            continue

    for group in _group_by(lambda path: (parse_filename(path)[0], parse_filename(path)[3])):
        if len(group) < 2:
            continue
        try:
            if are_time_combinable(group):
                _append(group, COMBINE_TIME, combine_time(group) if combine else None)
        except Exception:
            continue

    for group in _group_by(lambda path: (parse_filename(path)[0], parse_filename(path)[1], parse_filename(path)[2])):
        if len(group) < 2:
            continue
        try:
            if are_frequency_combinable(group, **options):
                _append(
                    group,
                    COMBINE_FREQUENCY,
                    combine_frequency(group, **options) if combine else None,
                )
        except Exception:
            continue

    for path in paths:
        if path in consumed:
            continue
        output.append(
            (
                path_index[path],
                {"paths": [path], "combine_type": None, "combined": None, "error": ""},
            )
        )

    output.sort(key=lambda item: item[0])
    return [group for _idx, group in output]


def combined_combine_options(combined) -> dict:
    """Extract the frequency-combine settings a combined payload was built with.

    Re-combining an extended selection has to reuse them, otherwise a gap-filled
    dataset silently re-renders with the defaults.
    """
    payload = combined if isinstance(combined, dict) else {}
    options = {}
    for key in ("gap_fill", "overlap_policy", "overlap_connection_mhz"):
        if payload.get(key, None) is not None:
            options[key] = payload[key]
    return options
