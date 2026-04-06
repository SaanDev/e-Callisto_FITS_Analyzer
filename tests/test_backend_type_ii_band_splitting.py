from __future__ import annotations

import math

import numpy as np

from src.Backend.type_ii_band_splitting import (
    calculate_type_ii_parameters,
    electron_density_cm3_from_frequency_mhz,
    fit_power_law,
    magnetic_field_gauss_from_alfven_speed,
)


def test_fit_power_law_returns_compact_metrics():
    times = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
    freqs = 96.0 * np.power(times, -0.42)

    fit = fit_power_law(times, freqs)

    assert fit["point_count"] == 4
    assert fit["a"] > 0.0
    assert fit["b"] > 0.0
    assert fit["r2"] > 0.999
    assert fit["rmse"] < 1e-6


def test_calculate_type_ii_parameters_matches_expected_formulae():
    upper_fit = {"a": 100.0, "b": 0.4}
    lower_fit = {"a": 82.0, "b": 0.35}
    upper_t = np.array([1.0, 2.0, 3.0], dtype=float)
    lower_t = np.array([1.0, 2.0, 3.0], dtype=float)
    upper_f = 100.0 * np.power(upper_t, -0.4)
    lower_f = 82.0 * np.power(lower_t, -0.35)

    result = calculate_type_ii_parameters(
        upper_time_seconds=upper_t,
        upper_freqs_mhz=upper_f,
        lower_time_seconds=lower_t,
        lower_freqs_mhz=lower_f,
        upper_fit=upper_fit,
        lower_fit=lower_fit,
        fold=1,
    )

    start_time = 1.0
    upper_start = 100.0
    lower_start = 82.0
    bandwidth = upper_start - lower_start
    compression = (upper_start / lower_start) ** 2
    mach = math.sqrt((compression * (compression + 5.0)) / (2.0 * (4.0 - compression)))
    drift = -100.0 * 0.4 * math.pow(start_time, -1.4)
    denom = 3.385
    log_term = math.log((upper_start ** 2) / denom)
    shock_speed = (13853221.38 * abs(drift)) / (upper_start * (log_term ** 2))
    shock_height = (4.32 * math.log(10.0)) / log_term
    alfven_speed = shock_speed / mach
    density_cm3 = electron_density_cm3_from_frequency_mhz(upper_start)
    magnetic_field = magnetic_field_gauss_from_alfven_speed(alfven_speed, density_cm3)

    assert math.isclose(result["start_time_s"], start_time, rel_tol=1e-9)
    assert math.isclose(result["upper_start_freq_mhz"], upper_start, rel_tol=1e-9)
    assert math.isclose(result["lower_start_freq_mhz"], lower_start, rel_tol=1e-9)
    assert math.isclose(result["bandwidth_mhz"], bandwidth, rel_tol=1e-9)
    assert math.isclose(result["compression_ratio"], compression, rel_tol=1e-9)
    assert math.isclose(result["upper_drift_mhz_s"], drift, rel_tol=1e-9)
    assert math.isclose(result["shock_speed_km_s"], shock_speed, rel_tol=1e-9)
    assert math.isclose(result["shock_height_rs"], shock_height, rel_tol=1e-9)
    assert math.isclose(result["alfven_mach_number"], mach, rel_tol=1e-9)
    assert math.isclose(result["alfven_speed_km_s"], alfven_speed, rel_tol=1e-9)
    assert math.isclose(result["magnetic_field_g"], magnetic_field, rel_tol=1e-9)
    assert result["warning"] == ""


def test_calculate_type_ii_parameters_marks_lower_extrapolation():
    upper_fit = {"a": 90.0, "b": 0.4}
    lower_fit = {"a": 75.0, "b": 0.35}

    result = calculate_type_ii_parameters(
        upper_time_seconds=np.array([1.0, 2.0, 3.0], dtype=float),
        upper_freqs_mhz=90.0 * np.power(np.array([1.0, 2.0, 3.0]), -0.4),
        lower_time_seconds=np.array([2.0, 3.0, 4.0], dtype=float),
        lower_freqs_mhz=75.0 * np.power(np.array([2.0, 3.0, 4.0]), -0.35),
        upper_fit=upper_fit,
        lower_fit=lower_fit,
        fold=1,
    )

    assert result["lower_extrapolated"] is True
    assert "extrapolated" in result["warning"].lower()
