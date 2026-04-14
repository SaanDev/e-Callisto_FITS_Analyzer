"""
Helpers for Type II band-splitting fitting and derived plasma parameters.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.optimize import curve_fit


MU_0 = 4.0 * math.pi * 1e-7
PROTON_MASS_KG = 1.67262192369e-27
PLASMA_FREQ_COEFF_MHZ = 0.00898


def power_law(x: np.ndarray | float, a: float, b: float) -> np.ndarray | float:
    return a * np.power(x, -b)


def power_law_drift_rate(x: np.ndarray | float, a: float, b: float) -> np.ndarray | float:
    return -a * b * np.power(x, -b - 1.0)


def power_law_fit_mask(time_values: Any, freq_values: Any) -> np.ndarray:
    x = np.asarray(time_values, dtype=float).reshape(-1)
    y = np.asarray(freq_values, dtype=float).reshape(-1)
    return np.isfinite(x) & np.isfinite(y) & (x > 0.0) & (y > 0.0)


def initial_power_law_guess(time_values: Any, freq_values: Any) -> tuple[float, float]:
    x = np.asarray(time_values, dtype=float).reshape(-1)
    y = np.asarray(freq_values, dtype=float).reshape(-1)
    mask = power_law_fit_mask(x, y)
    if np.count_nonzero(mask) >= 2:
        lx = np.log(x[mask])
        ly = np.log(y[mask])
        try:
            slope, intercept = np.polyfit(lx, ly, 1)
            a0 = float(np.exp(intercept))
            b0 = float(max(1e-9, -slope))
            if np.isfinite(a0) and a0 > 0.0 and np.isfinite(b0):
                return a0, b0
        except Exception:
            pass

    positive_freqs = y[np.isfinite(y) & (y > 0.0)]
    if positive_freqs.size:
        return float(np.nanmax(positive_freqs)), 0.5
    return 1.0, 0.5


def fit_power_law(time_values: Any, freq_values: Any) -> dict[str, Any]:
    x = np.asarray(time_values, dtype=float).reshape(-1)
    y = np.asarray(freq_values, dtype=float).reshape(-1)
    mask = power_law_fit_mask(x, y)
    if np.count_nonzero(mask) < 2:
        raise ValueError("Power-law fitting requires at least two points with time > 0 s and frequency > 0 MHz.")

    fit_time = x[mask]
    fit_freq = y[mask]
    params, cov = curve_fit(
        power_law,
        fit_time,
        fit_freq,
        p0=initial_power_law_guess(fit_time, fit_freq),
        bounds=([1e-12, 1e-9], [np.inf, np.inf]),
        maxfev=10000,
    )
    a = float(params[0])
    b = abs(float(params[1]))

    std_errs = [math.nan, math.nan]
    try:
        diag = np.sqrt(np.diag(cov))
        if diag.size >= 2:
            std_errs = [float(diag[0]), float(diag[1])]
    except Exception:
        pass

    predicted = np.asarray(power_law(fit_time, a, b), dtype=float)
    residuals = fit_freq - predicted
    rmse = float(np.sqrt(np.mean(np.square(residuals)))) if residuals.size else math.nan
    ss_res = float(np.sum(np.square(residuals)))
    ss_tot = float(np.sum(np.square(fit_freq - np.mean(fit_freq))))
    if ss_tot > 0.0:
        r2 = float(1.0 - (ss_res / ss_tot))
    else:
        r2 = 1.0 if ss_res <= 1e-12 else 0.0

    return {
        "a": a,
        "b": b,
        "std_errs": std_errs,
        "r2": r2,
        "rmse": rmse,
        "point_count": int(fit_time.size),
        "fit_time": fit_time,
        "fit_freq": fit_freq,
    }


def electron_density_cm3_from_frequency_mhz(freq_mhz: float) -> float:
    freq = float(freq_mhz)
    if not math.isfinite(freq) or freq <= 0.0:
        raise ValueError("Frequency must be positive to derive density.")
    return float((freq / PLASMA_FREQ_COEFF_MHZ) ** 2)


def magnetic_field_gauss_from_alfven_speed(alfven_speed_km_s: float, electron_density_cm3: float) -> float:
    va_km_s = float(alfven_speed_km_s)
    ne_cm3 = float(electron_density_cm3)
    if not math.isfinite(va_km_s) or va_km_s <= 0.0:
        raise ValueError("Alfven speed must be positive.")
    if not math.isfinite(ne_cm3) or ne_cm3 <= 0.0:
        raise ValueError("Electron density must be positive.")

    va_m_s = va_km_s * 1000.0
    ne_m3 = ne_cm3 * 1e6
    b_t = va_m_s * math.sqrt(MU_0 * PROTON_MASS_KG * ne_m3)
    return float(b_t * 1e4)


def newkirk_height_rs_from_frequency_mhz(freq_mhz: Any, fold: int) -> np.ndarray:
    freq = np.asarray(freq_mhz, dtype=float)
    denom = max(1, int(fold)) * 3.385
    ratio = np.square(freq) / float(denom)
    if np.any(~np.isfinite(ratio)) or np.any(ratio <= 1.0):
        raise ValueError("Frequencies must stay above the Newkirk cutoff to derive shock height.")
    return 4.32 * np.log(10.0) / np.log(ratio)


def _sampling_times_for_interval(
    *,
    t_start: float,
    t_end: float,
    upper_fit_times: np.ndarray,
    available_time_seconds: Any = None,
) -> np.ndarray:
    if available_time_seconds is not None:
        samples = np.asarray(available_time_seconds, dtype=float).reshape(-1)
        mask = np.isfinite(samples) & (samples >= t_start) & (samples <= t_end)
        samples = np.asarray(samples[mask], dtype=float)
        if samples.size >= 2:
            return np.unique(samples)

    samples = np.unique(np.asarray(upper_fit_times, dtype=float).reshape(-1))
    samples = samples[np.isfinite(samples) & (samples >= t_start) & (samples <= t_end)]
    if samples.size >= 2:
        return samples

    if math.isclose(t_start, t_end, rel_tol=0.0, abs_tol=1e-9):
        return np.array([t_start], dtype=float)

    return np.linspace(t_start, t_end, 512, dtype=float)


def _sample_type_ii_fits(
    *,
    upper_time_seconds: Any,
    upper_freqs_mhz: Any,
    lower_time_seconds: Any,
    lower_freqs_mhz: Any,
    upper_fit: dict[str, Any],
    lower_fit: dict[str, Any],
    available_time_seconds: Any = None,
) -> dict[str, Any]:
    upper_times = np.asarray(upper_time_seconds, dtype=float).reshape(-1)
    lower_times = np.asarray(lower_time_seconds, dtype=float).reshape(-1)
    upper_mask = power_law_fit_mask(upper_times, upper_freqs_mhz)
    lower_mask = power_law_fit_mask(lower_time_seconds, lower_freqs_mhz)
    if np.count_nonzero(upper_mask) < 2 or np.count_nonzero(lower_mask) < 2:
        raise ValueError("Both bands need at least two positive-time points before calculations.")

    fit_upper_times = np.asarray(upper_times[upper_mask], dtype=float)
    fit_lower_times = np.asarray(lower_times[lower_mask], dtype=float)

    t_start = float(np.min(fit_upper_times))
    t_end = float(np.max(fit_upper_times))
    upper_a = float(upper_fit["a"])
    upper_b = float(upper_fit["b"])
    lower_a = float(lower_fit["a"])
    lower_b = float(lower_fit["b"])

    sample_times = _sampling_times_for_interval(
        t_start=t_start,
        t_end=t_end,
        upper_fit_times=fit_upper_times,
        available_time_seconds=available_time_seconds,
    )
    upper_curve = np.asarray(power_law(sample_times, upper_a, upper_b), dtype=float)
    lower_curve = np.asarray(power_law(sample_times, lower_a, lower_b), dtype=float)
    upper_drift = np.asarray(power_law_drift_rate(sample_times, upper_a, upper_b), dtype=float)
    compression = np.square(upper_curve / lower_curve)
    if np.any(~np.isfinite(compression)) or np.any(compression <= 0.0) or np.any(compression >= 4.0):
        raise ValueError("Compression ratio X must stay between 0 and 4 over the selected interval.")

    lower_extrapolated = bool(
        t_start < float(np.min(fit_lower_times))
        or t_end > float(np.max(fit_lower_times))
    )
    warning = ""
    if lower_extrapolated:
        warning = "Lower-band fit was extrapolated over part of the averaging interval."

    return {
        "t_start": t_start,
        "t_end": t_end,
        "sample_times_s": sample_times,
        "upper_curve_mhz": upper_curve,
        "lower_curve_mhz": lower_curve,
        "upper_drift_mhz_s": upper_drift,
        "compression_ratio": compression,
        "lower_extrapolated": lower_extrapolated,
        "warning": warning,
    }


def calculate_b_vs_r_profile(
    *,
    upper_time_seconds: Any,
    upper_freqs_mhz: Any,
    lower_time_seconds: Any,
    lower_freqs_mhz: Any,
    upper_fit: dict[str, Any],
    lower_fit: dict[str, Any],
    analysis_shock_speed_km_s: float,
    fold: int,
    available_time_seconds: Any = None,
) -> dict[str, Any]:
    sampled = _sample_type_ii_fits(
        upper_time_seconds=upper_time_seconds,
        upper_freqs_mhz=upper_freqs_mhz,
        lower_time_seconds=lower_time_seconds,
        lower_freqs_mhz=lower_freqs_mhz,
        upper_fit=upper_fit,
        lower_fit=lower_fit,
        available_time_seconds=available_time_seconds,
    )

    analysis_shock_speed = float(analysis_shock_speed_km_s)
    if not math.isfinite(analysis_shock_speed) or analysis_shock_speed <= 0.0:
        raise ValueError("Analyzer shock speed must be positive.")

    compression = np.asarray(sampled["compression_ratio"], dtype=float)
    mach = np.sqrt((compression * (compression + 5.0)) / (2.0 * (4.0 - compression)))
    if np.any(~np.isfinite(mach)) or np.any(mach <= 0.0):
        raise ValueError("Failed to derive a valid Alfven Mach number over the selected interval.")

    alfven_speed = analysis_shock_speed / mach
    upper_curve = np.asarray(sampled["upper_curve_mhz"], dtype=float)
    densities_cm3 = np.square(upper_curve / PLASMA_FREQ_COEFF_MHZ)
    magnetic_field_g = np.asarray(
        [magnetic_field_gauss_from_alfven_speed(float(va), float(ne)) for va, ne in zip(alfven_speed, densities_cm3)],
        dtype=float,
    )
    heights_rs = np.asarray(newkirk_height_rs_from_frequency_mhz(upper_curve, fold), dtype=float)
    order = np.argsort(heights_rs)
    heights_sorted = heights_rs[order]
    magnetic_sorted = magnetic_field_g[order]

    fit = fit_power_law(heights_sorted, magnetic_sorted)

    return {
        "sample_times_s": np.asarray(sampled["sample_times_s"], dtype=float),
        "heights_rs": heights_sorted,
        "magnetic_field_g": magnetic_sorted,
        "alfven_mach_number": np.asarray(mach, dtype=float)[order],
        "alfven_speed_km_s": np.asarray(alfven_speed, dtype=float)[order],
        "compression_ratio": compression[order],
        "fit": fit,
        "lower_extrapolated": bool(sampled["lower_extrapolated"]),
        "warning": str(sampled["warning"] or ""),
        "start_time_s": float(sampled["t_start"]),
        "end_time_s": float(sampled["t_end"]),
    }


def calculate_type_ii_parameters(
    *,
    upper_time_seconds: Any,
    upper_freqs_mhz: Any,
    lower_time_seconds: Any,
    lower_freqs_mhz: Any,
    upper_fit: dict[str, Any],
    lower_fit: dict[str, Any],
    analysis_start_freq_mhz: float,
    analysis_shock_speed_km_s: float,
    available_time_seconds: Any = None,
) -> dict[str, Any]:
    sampled = _sample_type_ii_fits(
        upper_time_seconds=upper_time_seconds,
        upper_freqs_mhz=upper_freqs_mhz,
        lower_time_seconds=lower_time_seconds,
        lower_freqs_mhz=lower_freqs_mhz,
        upper_fit=upper_fit,
        lower_fit=lower_fit,
        available_time_seconds=available_time_seconds,
    )
    t_start = float(sampled["t_start"])
    t_end = float(sampled["t_end"])
    upper_curve = np.asarray(sampled["upper_curve_mhz"], dtype=float)
    lower_curve = np.asarray(sampled["lower_curve_mhz"], dtype=float)
    upper_drift = np.asarray(sampled["upper_drift_mhz_s"], dtype=float)
    compression_series = np.asarray(sampled["compression_ratio"], dtype=float)

    f_upper = float(upper_curve[0])
    f_lower = float(lower_curve[0])
    avg_upper_freq = float(np.mean(upper_curve))
    avg_lower_freq = float(np.mean(lower_curve))
    bandwidth = float(np.mean(upper_curve - lower_curve))
    avg_upper_drift = float(np.mean(upper_drift))
    compression_ratio = float((avg_upper_freq / avg_lower_freq) ** 2)

    mach = float(math.sqrt((compression_ratio * (compression_ratio + 5.0)) / (2.0 * (4.0 - compression_ratio))))
    if mach <= 0.0 or not math.isfinite(mach):
        raise ValueError("Failed to derive a valid Alfven Mach number.")

    analysis_freq = float(analysis_start_freq_mhz)
    if not math.isfinite(analysis_freq) or analysis_freq <= 0.0:
        raise ValueError("Analyzer starting frequency must be positive.")

    analysis_shock_speed = float(analysis_shock_speed_km_s)
    if not math.isfinite(analysis_shock_speed) or analysis_shock_speed <= 0.0:
        raise ValueError("Analyzer shock speed must be positive.")

    alfven_speed = float(analysis_shock_speed / mach)
    electron_density_cm3 = electron_density_cm3_from_frequency_mhz(analysis_freq)
    magnetic_field_g = magnetic_field_gauss_from_alfven_speed(alfven_speed, electron_density_cm3)

    return {
        "start_time_s": t_start,
        "end_time_s": t_end,
        "upper_start_freq_mhz": f_upper,
        "lower_start_freq_mhz": f_lower,
        "avg_upper_freq_mhz": avg_upper_freq,
        "avg_lower_freq_mhz": avg_lower_freq,
        "bandwidth_mhz": bandwidth,
        "upper_avg_drift_mhz_s": avg_upper_drift,
        "compression_ratio": compression_ratio,
        "alfven_mach_number": mach,
        "alfven_speed_km_s": alfven_speed,
        "magnetic_field_g": magnetic_field_g,
        "lower_extrapolated": bool(sampled["lower_extrapolated"]),
        "warning": str(sampled["warning"] or ""),
    }
