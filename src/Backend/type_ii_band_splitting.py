"""
Helpers for Type II band-splitting fitting and derived plasma/shock parameters.
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


def calculate_type_ii_parameters(
    *,
    upper_time_seconds: Any,
    upper_freqs_mhz: Any,
    lower_time_seconds: Any,
    lower_freqs_mhz: Any,
    upper_fit: dict[str, Any],
    lower_fit: dict[str, Any],
    fold: int,
) -> dict[str, Any]:
    upper_times = np.asarray(upper_time_seconds, dtype=float).reshape(-1)
    lower_times = np.asarray(lower_time_seconds, dtype=float).reshape(-1)
    upper_mask = power_law_fit_mask(upper_times, upper_freqs_mhz)
    lower_mask = power_law_fit_mask(lower_time_seconds, lower_freqs_mhz)
    if np.count_nonzero(upper_mask) < 2 or np.count_nonzero(lower_mask) < 2:
        raise ValueError("Both bands need at least two positive-time points before calculations.")

    t_start = float(np.min(upper_times[upper_mask]))
    upper_a = float(upper_fit["a"])
    upper_b = float(upper_fit["b"])
    lower_a = float(lower_fit["a"])
    lower_b = float(lower_fit["b"])

    f_upper = float(power_law(t_start, upper_a, upper_b))
    f_lower = float(power_law(t_start, lower_a, lower_b))
    drift_upper = float(power_law_drift_rate(t_start, upper_a, upper_b))
    bandwidth = float(f_upper - f_lower)
    compression_ratio = float((f_upper / f_lower) ** 2)

    if compression_ratio <= 0.0 or compression_ratio >= 4.0:
        raise ValueError("Compression ratio X must stay between 0 and 4 for the selected Mach-number formula.")

    mach = float(math.sqrt((compression_ratio * (compression_ratio + 5.0)) / (2.0 * (4.0 - compression_ratio))))
    if mach <= 0.0 or not math.isfinite(mach):
        raise ValueError("Failed to derive a valid Alfven Mach number.")

    denom = max(1.0, int(fold)) * 3.385
    log_term = math.log((f_upper ** 2) / denom)
    if abs(log_term) <= 1e-12:
        raise ValueError("Shock calculations are undefined for the selected starting frequency.")

    shock_speed = float((13853221.38 * abs(drift_upper)) / (f_upper * (log_term ** 2)))
    shock_height = float((4.32 * math.log(10.0)) / log_term)
    alfven_speed = float(shock_speed / mach)
    electron_density_cm3 = electron_density_cm3_from_frequency_mhz(f_upper)
    magnetic_field_g = magnetic_field_gauss_from_alfven_speed(alfven_speed, electron_density_cm3)

    lower_fit_times = np.asarray(lower_times[lower_mask], dtype=float)
    lower_extrapolated = bool(t_start < float(np.min(lower_fit_times)) or t_start > float(np.max(lower_fit_times)))
    warning = ""
    if lower_extrapolated:
        warning = "Lower-band fit was extrapolated at the upper-band start time."

    return {
        "start_time_s": t_start,
        "upper_start_freq_mhz": f_upper,
        "lower_start_freq_mhz": f_lower,
        "bandwidth_mhz": bandwidth,
        "compression_ratio": compression_ratio,
        "upper_drift_mhz_s": drift_upper,
        "shock_speed_km_s": shock_speed,
        "shock_height_rs": shock_height,
        "alfven_mach_number": mach,
        "alfven_speed_km_s": alfven_speed,
        "magnetic_field_g": magnetic_field_g,
        "fold": int(max(1, int(fold))),
        "lower_extrapolated": lower_extrapolated,
        "warning": warning,
    }
