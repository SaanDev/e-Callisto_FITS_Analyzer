from __future__ import annotations

from math import log
from typing import Any

import numpy as np
from scipy.optimize import curve_fit


def _model_func(t: np.ndarray, a: float, b: float) -> np.ndarray:
    return a * np.power(t, b)


def _drift_rate(t: np.ndarray, a: float, b: float) -> np.ndarray:
    return a * b * np.power(t, b - 1)


def _safe_fit_time(time_seconds: np.ndarray) -> np.ndarray:
    return np.where(time_seconds <= 0, 1.0e-6, time_seconds)


def _r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum(np.square(y_true - y_pred)))
    ss_tot = float(np.sum(np.square(y_true - np.mean(y_true))))
    if ss_tot <= 0:
        return 1.0
    return 1.0 - (ss_res / ss_tot)


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(y_true - y_pred))))


def fit_analyzer(
    points: list[dict[str, float]],
    *,
    mode: str,
    fold: int,
) -> dict[str, Any]:
    if len(points) < 2:
        raise ValueError("At least two points are required for fitting.")

    ordered = sorted(points, key=lambda item: float(item["timeChannel"]))
    time_channels = np.asarray([float(item["timeChannel"]) for item in ordered], dtype=float)
    time_seconds = time_channels * 0.25
    freq = np.asarray([float(item["freqMHz"]) for item in ordered], dtype=float)

    safe_time = _safe_fit_time(time_seconds)
    params, cov = curve_fit(_model_func, safe_time, freq, maxfev=10000)
    a, b = float(params[0]), float(params[1])
    std_errs = np.sqrt(np.diag(cov)).astype(float) if cov.size else np.array([np.nan, np.nan], dtype=float)

    predicted = _model_func(safe_time, a, b)
    r2 = _r2_score(freq, predicted)
    rmse = _rmse(freq, predicted)

    drift_vals = _drift_rate(safe_time, a, b)
    residuals = freq - predicted
    freq_err = float(np.std(residuals))

    err_a = 0.0 if a == 0 else float(std_errs[0] / a) ** 2
    err_b = 0.0 if b == 0 else float(std_errs[1] / b) ** 2
    drift_errs = np.abs(drift_vals) * np.sqrt(err_a + err_b)

    n = max(1, min(4, int(fold)))
    denom = n * 3.385
    log_term = np.log(np.square(freq) / denom)
    shock_speed = (13853221.38 * np.abs(drift_vals)) / (freq * np.square(log_term))
    shock_height = 4.32 * log(10) / log_term

    start_freq = float(np.percentile(freq, 90))
    harmonic = str(mode).strip().lower() == "harmonic"
    fundamental = not harmonic
    if harmonic:
        start_freq = start_freq / 2.0
    idx = int(np.abs(freq - start_freq).argmin())
    f0 = float(freq[idx])
    drift_err0 = float(drift_errs[idx])
    g0 = float(np.log((f0**2) / denom))
    start_shock_speed = float(shock_speed[idx])
    start_height = float(shock_height[idx])
    shock_speed_err = float((13853221.38 * drift_err0) / (f0 * (g0**2)))
    d_rp_df = float(8.64 * log(10) / (f0 * (g0**2)))
    rp_err = abs(d_rp_df * freq_err)

    avg_freq = float(np.mean(freq))
    avg_freq_err = float(np.std(freq) / np.sqrt(len(freq)))
    avg_drift = float(np.mean(drift_vals))
    avg_drift_err = float(np.std(drift_vals) / np.sqrt(len(drift_vals)))
    avg_speed = float(np.mean(shock_speed))
    avg_speed_err = float(np.std(shock_speed) / np.sqrt(len(shock_speed)))
    avg_height = float(np.mean(shock_height))
    avg_height_err = float(np.std(shock_height) / np.sqrt(len(shock_height)))

    time_fit_seconds = np.linspace(float(np.min(time_seconds)), float(np.max(time_seconds)), 400)
    fit_line = _model_func(_safe_fit_time(time_fit_seconds), a, b)

    return {
        "mode": "harmonic" if harmonic else "fundamental",
        "fold": n,
        "equation": f"f(t) = {a:.2f} * t^{b:.2f}",
        "fit": {
            "a": a,
            "b": b,
            "stdErrs": [float(std_errs[0]), float(std_errs[1])],
            "r2": r2,
            "rmse": rmse,
        },
        "shockSummary": {
            "avgFreqMHz": avg_freq,
            "avgFreqErrMHz": avg_freq_err,
            "avgDriftMHzPerSec": avg_drift,
            "avgDriftErrMHzPerSec": avg_drift_err,
            "startFreqMHz": float(start_freq),
            "startFreqErrMHz": freq_err,
            "initialShockSpeedKmPerSec": start_shock_speed,
            "initialShockSpeedErrKmPerSec": shock_speed_err,
            "initialShockHeightRs": start_height,
            "initialShockHeightErrRs": float(rp_err),
            "avgShockSpeedKmPerSec": avg_speed,
            "avgShockSpeedErrKmPerSec": avg_speed_err,
            "avgShockHeightRs": avg_height,
            "avgShockHeightErrRs": avg_height_err,
            "fundamental": fundamental,
            "harmonic": harmonic,
            "fold": n,
        },
        "points": [
            {
                "timeChannel": float(tc),
                "timeSeconds": float(ts),
                "freqMHz": float(fm),
            }
            for tc, ts, fm in zip(time_channels, time_seconds, freq)
        ],
        "plots": {
            "bestFit": {
                "points": [
                    {"x": float(ts), "y": float(fm)}
                    for ts, fm in zip(time_seconds, freq)
                ],
                "fitLine": [
                    {"x": float(ts), "y": float(fm)}
                    for ts, fm in zip(time_fit_seconds, fit_line)
                ],
            },
            "shockSpeedVsHeight": {
                "points": [
                    {"x": float(height), "y": float(speed)}
                    for height, speed in zip(shock_height, shock_speed)
                ]
            },
            "shockSpeedVsFrequency": {
                "points": [
                    {"x": float(freq_value), "y": float(speed)}
                    for freq_value, speed in zip(freq, shock_speed)
                ]
            },
            "shockHeightVsFrequency": {
                "points": [
                    {"x": float(height), "y": float(freq_value)}
                    for height, freq_value in zip(shock_height, freq)
                ]
            },
        },
    }

