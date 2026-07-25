"""
Assisted Type II radio-burst detection.

The detector intentionally keeps candidate generation deterministic.  A locally
trained model may calibrate candidate confidence and refine a mask, but the
application always has a usable cold-start path and never requires network
access.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import math
from typing import Any, Callable, Literal, Mapping, Protocol

import numpy as np
from scipy.ndimage import (
    binary_closing,
    binary_dilation,
    gaussian_filter,
    label as connected_components,
)

from src.Backend.frequency_axis import frequency_step_mhz, invalid_row_mask
from src.Backend.noise_reduction import rowwise_baseline, rowwise_noise_scale


TypeIIBand = Literal["fundamental", "harmonic"]
CancelCallback = Callable[[], bool]

ALGORITHM_VERSION = "1"
CANDIDATE_FEATURE_SCHEMA_VERSION = 1


class TypeIIDetectionCancelled(RuntimeError):
    """Raised when a caller cancels a running detection."""


class TypeIIModelProtocol(Protocol):
    model_id: str

    def predict_candidate(self, features: np.ndarray, *, station: str = "") -> float:
        ...

    def refine_candidate_mask(self, candidate: "TypeIICandidate", shape: tuple[int, int]) -> np.ndarray | None:
        ...


@dataclass(frozen=True)
class TypeIIDetectionSettings:
    sensitivity: str = "conservative"
    snr_floor: float = 3.0
    min_drift_mhz_s: float = 0.02
    max_drift_mhz_s: float = 3.0
    min_duration_s: float = 10.0
    min_time_samples: int = 8
    min_frequency_span_mhz: float = 2.0
    min_frequency_channels: int = 3
    min_continuity: float = 0.35
    confidence_threshold: float = 0.75
    max_candidates: int = 10
    deterministic_weight: float = 0.40
    learned_weight: float = 0.60

    @classmethod
    def for_sensitivity(cls, name: str) -> "TypeIIDetectionSettings":
        mode = str(name or "conservative").strip().lower()
        if mode == "balanced":
            return cls(
                sensitivity="balanced",
                snr_floor=2.6,
                min_drift_mhz_s=0.015,
                max_drift_mhz_s=4.0,
                min_duration_s=8.0,
                min_continuity=0.28,
                confidence_threshold=0.62,
            )
        if mode == "sensitive":
            return cls(
                sensitivity="sensitive",
                snr_floor=2.2,
                min_drift_mhz_s=0.01,
                max_drift_mhz_s=5.0,
                min_duration_s=6.0,
                min_time_samples=6,
                min_frequency_span_mhz=1.0,
                min_continuity=0.20,
                confidence_threshold=0.50,
            )
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class TypeIICandidate:
    candidate_id: str
    mask: np.ndarray = field(repr=False, compare=False)
    ridge_time_indices: np.ndarray = field(repr=False, compare=False)
    ridge_frequency_indices: np.ndarray = field(repr=False, compare=False)
    ridge_times_s: np.ndarray = field(repr=False, compare=False)
    ridge_freqs_mhz: np.ndarray = field(repr=False, compare=False)
    start_time_s: float
    end_time_s: float
    high_frequency_mhz: float
    low_frequency_mhz: float
    drift_mhz_s: float
    duration_s: float
    frequency_span_mhz: float
    continuity: float
    median_snr: float
    fit_quality: float
    inlier_fraction: float
    deterministic_score: float
    learned_probability: float | None
    confidence: float
    feature_vector: np.ndarray = field(repr=False, compare=False)
    snr_data: np.ndarray | None = field(default=None, repr=False, compare=False)
    suggested_band: TypeIIBand | None = None
    paired_candidate_id: str | None = None
    station_calibrated: bool = False
    rejection_reason: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "start_time_s": float(self.start_time_s),
            "end_time_s": float(self.end_time_s),
            "high_frequency_mhz": float(self.high_frequency_mhz),
            "low_frequency_mhz": float(self.low_frequency_mhz),
            "drift_mhz_s": float(self.drift_mhz_s),
            "duration_s": float(self.duration_s),
            "frequency_span_mhz": float(self.frequency_span_mhz),
            "continuity": float(self.continuity),
            "median_snr": float(self.median_snr),
            "fit_quality": float(self.fit_quality),
            "inlier_fraction": float(self.inlier_fraction),
            "deterministic_score": float(self.deterministic_score),
            "learned_probability": (
                None if self.learned_probability is None else float(self.learned_probability)
            ),
            "confidence": float(self.confidence),
            "suggested_band": self.suggested_band,
            "paired_candidate_id": self.paired_candidate_id,
            "station_calibrated": bool(self.station_calibrated),
            "rejection_reason": str(self.rejection_reason),
        }


@dataclass(frozen=True)
class TypeIIDetectionResult:
    candidates: tuple[TypeIICandidate, ...]
    generated_candidates: tuple[TypeIICandidate, ...]
    settings: TypeIIDetectionSettings
    algorithm_version: str
    model_id: str | None
    source_kind: str
    station: str
    roi: dict[str, float | int]
    snr_data: np.ndarray = field(repr=False, compare=False)

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


def _check_cancel(cancel_cb: CancelCallback | None) -> None:
    if cancel_cb is not None and bool(cancel_cb()):
        raise TypeIIDetectionCancelled("Type II detection was cancelled.")


def robust_snr_spectrum(
    data: np.ndarray,
    *,
    gap_row_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Return a row-normalized SNR image without changing the source array."""

    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Type II detection expects 2D (frequency, time) data; got {arr.shape}.")
    baseline = rowwise_baseline(arr, method="robust", gap_row_mask=gap_row_mask)
    centered = arr - baseline
    scale = rowwise_noise_scale(centered, gap_row_mask=gap_row_mask)
    with np.errstate(divide="ignore", invalid="ignore"):
        snr = centered / scale
    snr = np.clip(snr, -8.0, 30.0).astype(np.float32, copy=False)
    bad_rows = invalid_row_mask(arr, gap_row_mask)
    snr[bad_rows, :] = np.nan
    snr[~np.isfinite(arr)] = np.nan
    return snr


def _axis_indices(values: np.ndarray, low: float | None, high: float | None) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    finite = np.isfinite(arr)
    if low is None or high is None:
        return np.where(finite)[0]
    lo, hi = sorted((float(low), float(high)))
    return np.where(finite & (arr >= lo) & (arr <= hi))[0]


def _resolve_roi(
    shape: tuple[int, int],
    freqs: np.ndarray,
    times: np.ndarray,
    roi: Mapping[str, Any] | tuple[float, float, float, float] | None,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | int]]:
    if roi is None:
        time_low = time_high = freq_low = freq_high = None
    elif isinstance(roi, Mapping):
        time_low = roi.get("time_start_s")
        time_high = roi.get("time_end_s")
        freq_low = roi.get("frequency_min_mhz")
        freq_high = roi.get("frequency_max_mhz")
    else:
        if len(roi) != 4:
            raise ValueError("ROI tuple must be (time_start_s, time_end_s, frequency_min_mhz, frequency_max_mhz).")
        time_low, time_high, freq_low, freq_high = roi

    rows = _axis_indices(freqs, freq_low, freq_high)
    cols = _axis_indices(times, time_low, time_high)
    if rows.size == 0 or cols.size == 0:
        raise ValueError("The selected Type II detection range does not overlap the spectrum.")
    if rows.size != shape[0] and (np.min(rows) < 0 or np.max(rows) >= shape[0]):
        raise ValueError("Frequency ROI does not match the data shape.")
    if cols.size != shape[1] and (np.min(cols) < 0 or np.max(cols) >= shape[1]):
        raise ValueError("Time ROI does not match the data shape.")

    info: dict[str, float | int] = {
        "row_start": int(rows.min()),
        "row_end": int(rows.max()),
        "column_start": int(cols.min()),
        "column_end": int(cols.max()),
        "time_start_s": float(np.nanmin(np.asarray(times, dtype=float)[cols])),
        "time_end_s": float(np.nanmax(np.asarray(times, dtype=float)[cols])),
        "frequency_min_mhz": float(np.nanmin(np.asarray(freqs, dtype=float)[rows])),
        "frequency_max_mhz": float(np.nanmax(np.asarray(freqs, dtype=float)[rows])),
    }
    return rows, cols, info


def _robust_polyfit(x: np.ndarray, y: np.ndarray, degree: int) -> tuple[np.ndarray, np.ndarray, float]:
    keep = np.isfinite(x) & np.isfinite(y)
    min_points = max(degree + 2, 4)
    if np.count_nonzero(keep) < min_points:
        raise ValueError("Not enough ridge points.")
    for _ in range(4):
        coef = np.polyfit(x[keep], y[keep], degree)
        fitted = np.polyval(coef, x)
        resid = y - fitted
        center = float(np.nanmedian(resid[keep]))
        mad = float(np.nanmedian(np.abs(resid[keep] - center)))
        scale = max(1.4826 * mad, 1e-6)
        updated = np.isfinite(resid) & (np.abs(resid - center) <= 3.0 * scale)
        if np.count_nonzero(updated) < min_points or np.array_equal(updated, keep):
            break
        keep = updated
    coef = np.polyfit(x[keep], y[keep], degree)
    fitted = np.polyval(coef, x)
    rmse = float(np.sqrt(np.nanmean(np.square((y - fitted)[keep]))))
    return coef, keep, rmse


def _fit_ridge(times: np.ndarray, freqs: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float, float]:
    x = np.asarray(times, dtype=float)
    y = np.asarray(freqs, dtype=float)
    x0 = float(np.nanmin(x))
    centered_x = x - x0
    linear, lin_keep, lin_rmse = _robust_polyfit(centered_x, y, 1)
    selected = linear
    keep = lin_keep
    rmse = lin_rmse
    if x.size >= 10:
        try:
            quadratic, quad_keep, quad_rmse = _robust_polyfit(centered_x, y, 2)
            if quad_rmse <= 0.85 * max(lin_rmse, 1e-6):
                selected, keep, rmse = quadratic, quad_keep, quad_rmse
        except Exception:
            pass

    predicted = np.polyval(selected, centered_x)
    derivative = np.polyder(selected)
    drift = float(np.nanmedian(np.polyval(derivative, centered_x)))
    y_keep = y[keep]
    residual_sum = float(np.nansum(np.square(y_keep - predicted[keep])))
    total_sum = float(np.nansum(np.square(y_keep - np.nanmean(y_keep))))
    r2 = 1.0 - residual_sum / total_sum if total_sum > 1e-12 else 0.0
    quality = float(np.clip(r2, 0.0, 1.0))
    return predicted, keep, drift, quality, rmse


def _score_candidate(
    *,
    drift: float,
    continuity: float,
    fit_quality: float,
    inlier_fraction: float,
    duration: float,
    frequency_span: float,
    median_snr: float,
    occupancy: float,
    settings: TypeIIDetectionSettings,
) -> tuple[float, np.ndarray]:
    magnitude = abs(float(drift))
    center = math.sqrt(settings.min_drift_mhz_s * settings.max_drift_mhz_s)
    drift_score = math.exp(-0.35 * abs(math.log(max(magnitude, 1e-9) / center)))
    continuity_score = float(np.clip(continuity, 0.0, 1.0))
    fit_score = float(np.clip(0.65 * fit_quality + 0.35 * inlier_fraction, 0.0, 1.0))
    duration_score = float(np.clip(duration / max(settings.min_duration_s * 3.0, 1.0), 0.0, 1.0))
    span_score = float(np.clip(frequency_span / max(settings.min_frequency_span_mhz * 3.0, 1.0), 0.0, 1.0))
    duration_band_score = 0.55 * duration_score + 0.45 * span_score
    snr_score = float(np.clip((median_snr - settings.snr_floor + 2.0) / 6.0, 0.0, 1.0))
    morphology_score = float(np.clip(1.0 - max(0.0, occupancy - 0.45) / 0.55, 0.0, 1.0))
    score = (
        0.25 * drift_score
        + 0.20 * continuity_score
        + 0.15 * fit_score
        + 0.15 * duration_band_score
        + 0.15 * snr_score
        + 0.10 * morphology_score
    )
    features = np.asarray(
        [
            drift,
            abs(drift),
            continuity,
            fit_quality,
            inlier_fraction,
            duration,
            frequency_span,
            median_snr,
            occupancy,
            drift_score,
            duration_score,
            span_score,
            morphology_score,
            score,
        ],
        dtype=np.float64,
    )
    return float(np.clip(score, 0.0, 1.0)), features


def _candidate_id(
    start_col: int,
    end_col: int,
    ridge_freqs: np.ndarray,
    drift: float,
) -> str:
    payload = (
        f"{start_col}:{end_col}:{float(np.nanmedian(ridge_freqs)):.6f}:{drift:.8f}"
    ).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


def _build_corridor_mask(
    component: np.ndarray,
    row_indices: np.ndarray,
    col_indices: np.ndarray,
    ridge_cols_local: np.ndarray,
    ridge_rows_local: np.ndarray,
    shape: tuple[int, int],
) -> np.ndarray:
    full_mask = np.zeros(shape, dtype=bool)
    component_rows, component_cols = np.where(component)
    if component_rows.size == 0:
        return full_mask

    deviations = []
    ridge_by_col = {int(c): int(r) for c, r in zip(ridge_cols_local, ridge_rows_local)}
    for r, c in zip(component_rows, component_cols):
        if int(c) in ridge_by_col:
            deviations.append(abs(int(r) - ridge_by_col[int(c)]))
    robust_width = float(np.nanpercentile(deviations, 85.0)) if deviations else 1.0
    half_width = max(2, int(math.ceil(robust_width)) + 1)

    start_col = int(np.min(ridge_cols_local))
    end_col = int(np.max(ridge_cols_local))
    target_cols = np.arange(start_col, end_col + 1, dtype=int)
    interp_rows = np.interp(target_cols, ridge_cols_local, ridge_rows_local)
    for local_col, local_row in zip(target_cols, interp_rows):
        lo = max(0, int(round(local_row)) - half_width)
        hi = min(row_indices.size, int(round(local_row)) + half_width + 1)
        global_rows = row_indices[lo:hi]
        global_col = int(col_indices[local_col])
        full_mask[global_rows, global_col] = True

    component_full = np.zeros(shape, dtype=bool)
    component_full[
        row_indices[component_rows],
        col_indices[component_cols],
    ] = True
    full_mask |= component_full
    return binary_closing(full_mask, structure=np.ones((3, 3), dtype=bool))


def _component_candidate(
    component: np.ndarray,
    smoothed: np.ndarray,
    snr_roi: np.ndarray,
    row_indices: np.ndarray,
    col_indices: np.ndarray,
    freqs: np.ndarray,
    times: np.ndarray,
    full_shape: tuple[int, int],
    settings: TypeIIDetectionSettings,
) -> TypeIICandidate | None:
    rows, cols = np.where(component)
    if rows.size == 0:
        return None
    unique_cols = np.unique(cols)
    if unique_cols.size < settings.min_time_samples:
        return None

    ridge_rows: list[int] = []
    ridge_cols: list[int] = []
    ridge_snr: list[float] = []
    for col in unique_cols:
        component_at_col = rows[cols == col]
        values = smoothed[component_at_col, col]
        if values.size == 0 or not np.any(np.isfinite(values)):
            continue
        pos = int(np.nanargmax(values))
        ridge_rows.append(int(component_at_col[pos]))
        ridge_cols.append(int(col))
        ridge_snr.append(float(values[pos]))
    if len(ridge_cols) < settings.min_time_samples:
        return None

    ridge_cols_arr = np.asarray(ridge_cols, dtype=int)
    ridge_rows_arr = np.asarray(ridge_rows, dtype=int)
    global_cols = col_indices[ridge_cols_arr]
    global_rows = row_indices[ridge_rows_arr]
    ridge_times = np.asarray(times, dtype=float)[global_cols]
    ridge_freqs = np.asarray(freqs, dtype=float)[global_rows]
    order = np.argsort(ridge_times)
    ridge_cols_arr = ridge_cols_arr[order]
    ridge_rows_arr = ridge_rows_arr[order]
    global_cols = global_cols[order]
    global_rows = global_rows[order]
    ridge_times = ridge_times[order]
    ridge_freqs = ridge_freqs[order]
    ridge_snr_arr = np.asarray(ridge_snr, dtype=float)[order]

    duration = float(np.nanmax(ridge_times) - np.nanmin(ridge_times))
    frequency_span = float(np.nanmax(ridge_freqs) - np.nanmin(ridge_freqs))
    min_span = max(
        settings.min_frequency_span_mhz,
        settings.min_frequency_channels * frequency_step_mhz(freqs),
    )
    if duration < settings.min_duration_s or frequency_span < min_span:
        return None

    predicted, inliers, drift, fit_quality, _rmse = _fit_ridge(ridge_times, ridge_freqs)
    if drift >= -settings.min_drift_mhz_s or abs(drift) > settings.max_drift_mhz_s:
        return None

    time_range_samples = int(np.max(ridge_cols_arr) - np.min(ridge_cols_arr) + 1)
    continuity = float(len(ridge_cols_arr) / max(time_range_samples, 1))
    if continuity < settings.min_continuity:
        return None

    bbox_area = (
        int(np.max(rows) - np.min(rows) + 1)
        * int(np.max(cols) - np.min(cols) + 1)
    )
    occupancy = float(rows.size / max(bbox_area, 1))
    median_snr = float(np.nanmedian(ridge_snr_arr))
    inlier_fraction = float(np.count_nonzero(inliers) / max(inliers.size, 1))
    score, features = _score_candidate(
        drift=drift,
        continuity=continuity,
        fit_quality=fit_quality,
        inlier_fraction=inlier_fraction,
        duration=duration,
        frequency_span=frequency_span,
        median_snr=median_snr,
        occupancy=occupancy,
        settings=settings,
    )

    fitted_rows = np.asarray(
        [int(np.nanargmin(np.abs(np.asarray(freqs, dtype=float)[row_indices] - value))) for value in predicted],
        dtype=int,
    )
    mask = _build_corridor_mask(
        component,
        row_indices,
        col_indices,
        ridge_cols_arr,
        fitted_rows,
        full_shape,
    )
    cid = _candidate_id(int(global_cols.min()), int(global_cols.max()), ridge_freqs, drift)
    return TypeIICandidate(
        candidate_id=cid,
        mask=np.asarray(mask, dtype=bool),
        ridge_time_indices=np.asarray(global_cols, dtype=int),
        ridge_frequency_indices=np.asarray(global_rows, dtype=int),
        ridge_times_s=np.asarray(ridge_times, dtype=float),
        ridge_freqs_mhz=np.asarray(ridge_freqs, dtype=float),
        start_time_s=float(np.nanmin(ridge_times)),
        end_time_s=float(np.nanmax(ridge_times)),
        high_frequency_mhz=float(np.nanmax(ridge_freqs)),
        low_frequency_mhz=float(np.nanmin(ridge_freqs)),
        drift_mhz_s=float(drift),
        duration_s=duration,
        frequency_span_mhz=frequency_span,
        continuity=continuity,
        median_snr=median_snr,
        fit_quality=fit_quality,
        inlier_fraction=inlier_fraction,
        deterministic_score=score,
        learned_probability=None,
        confidence=score,
        feature_vector=features,
        snr_data=None,
    )


def _associate_bands(candidates: list[TypeIICandidate]) -> list[TypeIICandidate]:
    paired: dict[str, tuple[str, TypeIIBand]] = {}
    best_distance: dict[str, float] = {}
    for i, lower in enumerate(candidates):
        for higher in candidates[i + 1 :]:
            low, high = sorted(
                (lower, higher),
                key=lambda c: 0.5 * (c.high_frequency_mhz + c.low_frequency_mhz),
            )
            overlap = max(
                0.0,
                min(low.end_time_s, high.end_time_s) - max(low.start_time_s, high.start_time_s),
            )
            min_duration = max(min(low.duration_s, high.duration_s), 1e-6)
            if overlap / min_duration < 0.60:
                continue
            low_mid = 0.5 * (low.high_frequency_mhz + low.low_frequency_mhz)
            high_mid = 0.5 * (high.high_frequency_mhz + high.low_frequency_mhz)
            ratio = high_mid / max(low_mid, 1e-6)
            drift_ratio = abs(high.drift_mhz_s) / max(abs(low.drift_mhz_s), 1e-6)
            if not (1.7 <= ratio <= 2.3 and 1.4 <= drift_ratio <= 2.6):
                continue
            distance = abs(ratio - 2.0) + 0.25 * abs(drift_ratio - 2.0)
            for candidate, partner, band in (
                (low, high, "fundamental"),
                (high, low, "harmonic"),
            ):
                if distance < best_distance.get(candidate.candidate_id, float("inf")):
                    best_distance[candidate.candidate_id] = distance
                    paired[candidate.candidate_id] = (partner.candidate_id, band)

    return [
        replace(
            candidate,
            paired_candidate_id=paired.get(candidate.candidate_id, (None, None))[0],
            suggested_band=paired.get(candidate.candidate_id, (None, None))[1],
        )
        for candidate in candidates
    ]


def _merge_fragmented_candidates(
    candidates: list[TypeIICandidate],
    data: np.ndarray,
    freqs: np.ndarray,
    times: np.ndarray,
    *,
    gap_row_mask: np.ndarray | None,
    settings: TypeIIDetectionSettings,
) -> list[TypeIICandidate]:
    """Merge temporally separated ridge pieces with compatible extrapolation."""

    output = list(candidates)
    max_frequency_gap = max(5.0, 6.0 * frequency_step_mhz(freqs))
    changed = True
    while changed:
        changed = False
        output.sort(key=lambda item: (item.start_time_s, -item.high_frequency_mhz))
        for left_index, left in enumerate(output):
            for right_index in range(left_index + 1, len(output)):
                right = output[right_index]
                time_gap = float(right.start_time_s - left.end_time_s)
                if time_gap < 0.0 or time_gap > 15.0:
                    continue
                relative_drift_difference = abs(left.drift_mhz_s - right.drift_mhz_s) / max(
                    abs(left.drift_mhz_s),
                    abs(right.drift_mhz_s),
                    1e-6,
                )
                if relative_drift_difference > 0.50:
                    continue
                left_last = float(left.ridge_freqs_mhz[np.argmax(left.ridge_times_s)])
                right_first = float(right.ridge_freqs_mhz[np.argmin(right.ridge_times_s)])
                expected = left_last + left.drift_mhz_s * time_gap
                if abs(expected - right_first) > max_frequency_gap:
                    continue
                union = np.asarray(left.mask, dtype=bool) | np.asarray(right.mask, dtype=bool)
                try:
                    merged = type_ii_candidate_from_mask(
                        data,
                        freqs,
                        times,
                        union,
                        gap_row_mask=gap_row_mask,
                        settings=settings,
                    )
                except Exception:
                    continue
                output = [
                    item
                    for index, item in enumerate(output)
                    if index not in (left_index, right_index)
                ]
                output.append(merged)
                changed = True
                break
            if changed:
                break
    return output


def _merge_split_band_candidates(
    candidates: list[TypeIICandidate],
    data: np.ndarray,
    freqs: np.ndarray,
    times: np.ndarray,
    *,
    gap_row_mask: np.ndarray | None,
    settings: TypeIIDetectionSettings,
) -> list[TypeIICandidate]:
    """Keep close, parallel Type II split lanes in one emission-band mask."""

    output = list(candidates)
    changed = True
    while changed:
        changed = False
        for left_index, first in enumerate(output):
            for right_index in range(left_index + 1, len(output)):
                second = output[right_index]
                overlap = max(
                    0.0,
                    min(first.end_time_s, second.end_time_s)
                    - max(first.start_time_s, second.start_time_s),
                )
                if overlap / max(min(first.duration_s, second.duration_s), 1e-6) < 0.70:
                    continue
                first_mid = 0.5 * (first.high_frequency_mhz + first.low_frequency_mhz)
                second_mid = 0.5 * (second.high_frequency_mhz + second.low_frequency_mhz)
                low_mid, high_mid = sorted((first_mid, second_mid))
                ratio = high_mid / max(low_mid, 1e-6)
                drift_ratio = abs(first.drift_mhz_s) / max(abs(second.drift_mhz_s), 1e-6)
                if not (1.02 <= ratio <= 1.35 and 0.65 <= drift_ratio <= 1.55):
                    continue
                union = np.asarray(first.mask, dtype=bool) | np.asarray(second.mask, dtype=bool)
                try:
                    merged = type_ii_candidate_from_mask(
                        data,
                        freqs,
                        times,
                        union,
                        gap_row_mask=gap_row_mask,
                        settings=settings,
                    )
                except Exception:
                    continue
                output = [
                    item
                    for index, item in enumerate(output)
                    if index not in (left_index, right_index)
                ]
                output.append(merged)
                changed = True
                break
            if changed:
                break
    return output


def detect_type_ii_bursts(
    data: np.ndarray,
    freqs_mhz: np.ndarray,
    times_s: np.ndarray,
    *,
    station: str = "",
    gap_row_mask: np.ndarray | None = None,
    roi: Mapping[str, Any] | tuple[float, float, float, float] | None = None,
    settings: TypeIIDetectionSettings | None = None,
    model: TypeIIModelProtocol | None = None,
    cancel_cb: CancelCallback | None = None,
    source_kind: str = "noise_reduced",
) -> TypeIIDetectionResult:
    """Detect and rank Type II candidates in a dynamic spectrum."""

    config = settings or TypeIIDetectionSettings()
    arr = np.asarray(data, dtype=np.float32)
    freqs = np.asarray(freqs_mhz, dtype=float).ravel()
    times = np.asarray(times_s, dtype=float).ravel()
    if arr.ndim != 2:
        raise ValueError("Type II detection expects a 2D dynamic spectrum.")
    if freqs.size != arr.shape[0] or times.size != arr.shape[1]:
        raise ValueError("Frequency/time axes do not match the spectrum shape.")
    if arr.shape[0] < 3 or arr.shape[1] < config.min_time_samples:
        raise ValueError("The spectrum is too small for Type II detection.")

    _check_cancel(cancel_cb)
    snr = robust_snr_spectrum(arr, gap_row_mask=gap_row_mask)
    rows, cols, roi_info = _resolve_roi(arr.shape, freqs, times, roi)
    snr_roi = snr[np.ix_(rows, cols)]
    finite = np.isfinite(snr_roi)
    work = np.where(finite, snr_roi, -8.0).astype(np.float32)
    _check_cancel(cancel_cb)

    smoothed = gaussian_filter(work, sigma=(0.9, 1.1), mode="nearest")
    binary = finite & (smoothed >= float(config.snr_floor))
    if cols.size > 1:
        dt = float(np.nanmedian(np.abs(np.diff(times[cols]))))
    else:
        dt = 1.0
    close_time = max(3, min(11, int(round(2.0 / max(dt, 1e-3))) | 1))
    binary = binary_closing(binary, structure=np.ones((3, close_time), dtype=bool))
    binary = binary_dilation(binary, structure=np.ones((1, 3), dtype=bool))
    labels, count = connected_components(binary, structure=np.ones((3, 3), dtype=int))

    generated: list[TypeIICandidate] = []
    for component_id in range(1, int(count) + 1):
        _check_cancel(cancel_cb)
        component = labels == component_id
        if np.count_nonzero(component) < max(12, config.min_time_samples * 2):
            continue
        try:
            candidate = _component_candidate(
                component,
                smoothed,
                snr_roi,
                rows,
                cols,
                freqs,
                times,
                arr.shape,
                config,
            )
        except (ValueError, FloatingPointError, np.linalg.LinAlgError):
            candidate = None
        if candidate is None:
            continue
        generated.append(candidate)

    generated = _merge_fragmented_candidates(
        generated,
        arr,
        freqs,
        times,
        gap_row_mask=gap_row_mask,
        settings=config,
    )
    generated = _merge_split_band_candidates(
        generated,
        arr,
        freqs,
        times,
        gap_row_mask=gap_row_mask,
        settings=config,
    )
    generated = _associate_bands(generated)
    generated = [replace(candidate, snr_data=snr) for candidate in generated]
    calibrated: list[TypeIICandidate] = []
    for candidate in generated:
        learned_probability: float | None = None
        station_calibrated = False
        if model is not None:
            try:
                learned_probability = float(
                    np.clip(model.predict_candidate(candidate.feature_vector, station=station), 0.0, 1.0)
                )
                station_calibrated = bool(
                    getattr(model, "has_station_calibration", lambda _station: False)(station)
                )
            except Exception:
                learned_probability = None
        confidence = candidate.deterministic_score
        if learned_probability is not None:
            confidence = (
                config.deterministic_weight * candidate.deterministic_score
                + config.learned_weight * learned_probability
            )
        calibrated.append(
            replace(
                candidate,
                learned_probability=learned_probability,
                confidence=float(np.clip(confidence, 0.0, 1.0)),
                station_calibrated=station_calibrated,
            )
        )

    calibrated.sort(key=lambda item: (-item.confidence, item.start_time_s, -item.high_frequency_mhz))
    accepted = tuple(
        candidate
        for candidate in calibrated
        if candidate.confidence >= config.confidence_threshold
    )[: config.max_candidates]
    return TypeIIDetectionResult(
        candidates=accepted,
        generated_candidates=tuple(calibrated),
        settings=config,
        algorithm_version=ALGORITHM_VERSION,
        model_id=str(getattr(model, "model_id", "") or "") or None,
        source_kind=str(source_kind or "noise_reduced"),
        station=str(station or ""),
        roi=roi_info,
        snr_data=snr,
    )


def build_type_ii_isolation_mask(
    candidate: TypeIICandidate,
    *,
    model: TypeIIModelProtocol | None = None,
    shape: tuple[int, int] | None = None,
) -> np.ndarray:
    """Return a full-resolution Boolean mask for exactly one candidate band."""

    expected_shape = tuple(shape or candidate.mask.shape)
    mask = np.asarray(candidate.mask, dtype=bool)
    if mask.shape != expected_shape:
        raise ValueError(f"Candidate mask shape {mask.shape} does not match {expected_shape}.")
    if model is not None:
        try:
            refined = model.refine_candidate_mask(candidate, expected_shape)
            if refined is not None:
                refined_arr = np.asarray(refined, dtype=bool)
                if refined_arr.shape == expected_shape and np.any(refined_arr):
                    mask = refined_arr
        except Exception:
            pass
    return np.asarray(mask, dtype=bool).copy()


def type_ii_candidate_from_mask(
    data: np.ndarray,
    freqs_mhz: np.ndarray,
    times_s: np.ndarray,
    mask: np.ndarray,
    *,
    gap_row_mask: np.ndarray | None = None,
    settings: TypeIIDetectionSettings | None = None,
) -> TypeIICandidate:
    """Describe a user-reviewed lasso mask with the detector feature schema."""

    config = settings or TypeIIDetectionSettings()
    arr = np.asarray(data, dtype=np.float32)
    freqs = np.asarray(freqs_mhz, dtype=float).ravel()
    times = np.asarray(times_s, dtype=float).ravel()
    selected = np.asarray(mask, dtype=bool)
    if arr.ndim != 2 or selected.shape != arr.shape:
        raise ValueError("The teaching mask must match the 2D spectrum.")
    if freqs.size != arr.shape[0] or times.size != arr.shape[1]:
        raise ValueError("Frequency/time axes do not match the teaching spectrum.")
    if not np.any(selected):
        raise ValueError("The teaching mask is empty.")

    snr = robust_snr_spectrum(arr, gap_row_mask=gap_row_mask)
    ridge_rows: list[int] = []
    ridge_cols: list[int] = []
    ridge_snr: list[float] = []
    for col in np.where(np.any(selected, axis=0))[0]:
        rows = np.where(selected[:, col] & np.isfinite(snr[:, col]))[0]
        if rows.size == 0:
            continue
        pos = int(np.nanargmax(snr[rows, col]))
        ridge_rows.append(int(rows[pos]))
        ridge_cols.append(int(col))
        ridge_snr.append(float(snr[rows[pos], col]))
    if len(ridge_cols) < 4:
        raise ValueError("The teaching mask needs signal in at least four time columns.")

    ridge_rows_arr = np.asarray(ridge_rows, dtype=int)
    ridge_cols_arr = np.asarray(ridge_cols, dtype=int)
    ridge_times = times[ridge_cols_arr]
    ridge_freqs = freqs[ridge_rows_arr]
    order = np.argsort(ridge_times)
    ridge_rows_arr = ridge_rows_arr[order]
    ridge_cols_arr = ridge_cols_arr[order]
    ridge_times = ridge_times[order]
    ridge_freqs = ridge_freqs[order]
    ridge_snr_arr = np.asarray(ridge_snr, dtype=float)[order]
    predicted, inliers, drift, fit_quality, _ = _fit_ridge(ridge_times, ridge_freqs)
    fitted_rows = np.asarray(
        [int(np.nanargmin(np.abs(freqs - value))) for value in predicted],
        dtype=int,
    )
    duration = float(np.nanmax(ridge_times) - np.nanmin(ridge_times))
    frequency_span = float(np.nanmax(ridge_freqs) - np.nanmin(ridge_freqs))
    continuity = float(
        ridge_cols_arr.size
        / max(int(ridge_cols_arr.max() - ridge_cols_arr.min() + 1), 1)
    )
    rows, cols = np.where(selected)
    bbox_area = int(rows.max() - rows.min() + 1) * int(cols.max() - cols.min() + 1)
    occupancy = float(rows.size / max(bbox_area, 1))
    median_snr = float(np.nanmedian(ridge_snr_arr))
    inlier_fraction = float(np.count_nonzero(inliers) / max(inliers.size, 1))
    score, features = _score_candidate(
        drift=drift,
        continuity=continuity,
        fit_quality=fit_quality,
        inlier_fraction=inlier_fraction,
        duration=duration,
        frequency_span=frequency_span,
        median_snr=median_snr,
        occupancy=occupancy,
        settings=config,
    )
    cid = _candidate_id(int(ridge_cols_arr.min()), int(ridge_cols_arr.max()), ridge_freqs, drift)
    return TypeIICandidate(
        candidate_id=cid,
        mask=selected.copy(),
        ridge_time_indices=ridge_cols_arr,
        ridge_frequency_indices=fitted_rows,
        ridge_times_s=ridge_times,
        ridge_freqs_mhz=ridge_freqs,
        start_time_s=float(np.nanmin(ridge_times)),
        end_time_s=float(np.nanmax(ridge_times)),
        high_frequency_mhz=float(np.nanmax(ridge_freqs)),
        low_frequency_mhz=float(np.nanmin(ridge_freqs)),
        drift_mhz_s=float(drift),
        duration_s=duration,
        frequency_span_mhz=frequency_span,
        continuity=continuity,
        median_snr=median_snr,
        fit_quality=fit_quality,
        inlier_fraction=inlier_fraction,
        deterministic_score=score,
        learned_probability=None,
        confidence=score,
        feature_vector=features,
        snr_data=snr,
    )
