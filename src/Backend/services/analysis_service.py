from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
from scipy.optimize import curve_fit
from sklearn.metrics import mean_squared_error, r2_score


def model_func(t: np.ndarray, a: float, b: float) -> np.ndarray:
    return a * t ** b


def drift_rate(t: np.ndarray, a: float, b: float) -> np.ndarray:
    return a * b * t ** (b - 1)


@dataclass(frozen=True)
class FitResult:
    params: Tuple[float, float]
    equation: str
    r2: float
    rmse: float
    avg_freq: float
    avg_freq_err: float
    avg_drift: float
    avg_drift_err: float
    start_freq: float
    start_freq_err: float
    initial_shock_speed: float
    initial_shock_speed_err: float
    initial_shock_height: float
    initial_shock_height_err: float
    avg_shock_speed: float
    avg_shock_speed_err: float
    avg_shock_height: float
    avg_shock_height_err: float

    def to_serializable(self) -> Dict[str, object]:
        return {
            "params": {"a": self.params[0], "b": self.params[1]},
            "equation": self.equation,
            "r2": self.r2,
            "rmse": self.rmse,
            "avg_freq": self.avg_freq,
            "avg_freq_err": self.avg_freq_err,
            "avg_drift": self.avg_drift,
            "avg_drift_err": self.avg_drift_err,
            "start_freq": self.start_freq,
            "start_freq_err": self.start_freq_err,
            "initial_shock_speed": self.initial_shock_speed,
            "initial_shock_speed_err": self.initial_shock_speed_err,
            "initial_shock_height": self.initial_shock_height,
            "initial_shock_height_err": self.initial_shock_height_err,
            "avg_shock_speed": self.avg_shock_speed,
            "avg_shock_speed_err": self.avg_shock_speed_err,
            "avg_shock_height": self.avg_shock_height,
            "avg_shock_height_err": self.avg_shock_height_err,
        }


def fit_analysis(time: np.ndarray, freq: np.ndarray, harmonic: bool = False) -> FitResult:
    params, cov = curve_fit(model_func, time, freq, maxfev=10000)
    a, b = params
    std_errs = np.sqrt(np.diag(cov))

    predicted = model_func(time, a, b)
    r2 = float(r2_score(freq, predicted))
    rmse = float(np.sqrt(mean_squared_error(freq, predicted)))

    drift_vals = drift_rate(time, a, b)
    residuals = freq - predicted
    freq_err = np.std(residuals)
    drift_errs = np.abs(drift_vals) * np.sqrt((std_errs[0] / a) ** 2 + (std_errs[1] / b) ** 2)

    shock_speed = (13853221.38 * np.abs(drift_vals)) / (freq * (np.log(freq**2 / 3.385)) ** 2)
    r_p = 4.32 * np.log(10) / np.log(freq**2 / 3.385)

    percentile = 90
    start_freq = float(np.percentile(freq, percentile))
    if harmonic:
        start_freq = start_freq / 2

    idx = int(np.abs(freq - start_freq).argmin())
    f0 = freq[idx]
    start_shock_speed = shock_speed[idx]
    start_height = r_p[idx]
    drift0 = drift_vals[idx]
    drift_err0 = drift_errs[idx]

    shock_speed_err = (13853221.38 * drift_err0) / (f0 * (np.log(f0**2 / 3.385)) ** 2)
    d_rp_df = (8.64 / f0) / np.log(10) / np.log(f0**2 / 3.385)
    rp_err = np.abs(d_rp_df * freq_err)

    avg_freq = float(np.mean(freq))
    avg_freq_err = float(np.std(freq) / np.sqrt(len(freq)))
    avg_drift = float(np.mean(drift_vals))
    avg_drift_err = float(np.std(drift_vals) / np.sqrt(len(drift_vals)))
    avg_speed = float(np.mean(shock_speed))
    avg_speed_err = float(np.std(shock_speed) / np.sqrt(len(shock_speed)))
    avg_height = float(np.mean(r_p))
    avg_height_err = float(np.std(r_p) / np.sqrt(len(r_p)))

    equation = f"f(t) = {a:.2f} · t^{b:.2f}"

    return FitResult(
        params=(float(a), float(b)),
        equation=equation,
        r2=r2,
        rmse=rmse,
        avg_freq=avg_freq,
        avg_freq_err=avg_freq_err,
        avg_drift=avg_drift,
        avg_drift_err=avg_drift_err,
        start_freq=float(start_freq),
        start_freq_err=float(freq_err),
        initial_shock_speed=float(start_shock_speed),
        initial_shock_speed_err=float(shock_speed_err),
        initial_shock_height=float(start_height),
        initial_shock_height_err=float(rp_err),
        avg_shock_speed=float(avg_speed),
        avg_shock_speed_err=float(avg_speed_err),
        avg_shock_height=float(avg_height),
        avg_shock_height_err=float(avg_height_err),
    )
