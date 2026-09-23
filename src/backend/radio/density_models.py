"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Coronal electron-density models for turning a radio burst's frequency drift
into a shock (or exciter) height and speed.

A burst emits near the local plasma frequency, so a density model n(r) maps
each frequency to a height, and the drift rate df/dt to a speed:

    v = |df/dt| * |dr/df|,   dr/df = 2 n / (f |dn/dr|)

Every model is scaled by the usual "fold" multiplier (n -> fold * n). The
constants are the ones the Analyzer has always used for Newkirk, so the
Newkirk results are unchanged; the other models share them.

Models (r in solar radii, n in cm^-3):

* Newkirk (1961):             n = 4.2e4 * 10**(4.32 / r)
* Saito, Poland & Munro (1977): n = 1.36e6 r**-2.14 + 1.68e8 r**-6.13
* Leblanc, Dulk & Bougeret (1998): n = 3.3e5 r**-2 + 4.1e6 r**-4 + 8.0e7 r**-6
* Baumbach-Allen (Allen 1947): n = 1e8 (2.99 r**-16 + 1.55 r**-6 + 0.036 r**-1.5)
* Mann et al. (1999):         n = 5.14e9 * exp(13.83 * (1 / r - 1))

Newkirk keeps its closed-form height for every frequency, exactly as before.
The other models only report heights between the photosphere (1 R_sun) and
1 AU; a frequency outside that range has no height or speed (NaN).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

import numpy as np

#: Solar radius in km, as implied by the Analyzer's historical Newkirk speed
#: constant (13 853 221.38 = R_sun * 8.64 * ln 10).
RSUN_KM = 696_340.0

#: f_p**2 / n in MHz**2 cm**3, from the Analyzer's Newkirk constant 3.385
#: (= 4.2e4 * this). Equivalent to f_p = 8.977e-3 * sqrt(n) MHz.
PLASMA_FREQ_SQ_PER_DENSITY = 3.385 / 4.2e4

#: Heights the tabulated models are solved over, in solar radii (1 AU ~ 215).
MIN_HEIGHT_RS = 1.0
MAX_HEIGHT_RS = 215.0

_NEWKIRK_N0 = 4.2e4
_NEWKIRK_SCALE = 4.32
_MANN_NS = 5.14e9
_MANN_A = 13.83
_BISECTION_STEPS = 80


@dataclass(frozen=True)
class DensityModel:
    key: str
    label: str
    reference: str
    density: Callable[[np.ndarray], np.ndarray]
    derivative: Callable[[np.ndarray], np.ndarray]


def _newkirk(r):
    return _NEWKIRK_N0 * np.power(10.0, _NEWKIRK_SCALE / r)


def _newkirk_dr(r):
    return -_newkirk(r) * _NEWKIRK_SCALE * math.log(10.0) / np.square(r)


def _saito(r):
    return 1.36e6 * np.power(r, -2.14) + 1.68e8 * np.power(r, -6.13)


def _saito_dr(r):
    return -2.14 * 1.36e6 * np.power(r, -3.14) - 6.13 * 1.68e8 * np.power(r, -7.13)


def _leblanc(r):
    return 3.3e5 * np.power(r, -2.0) + 4.1e6 * np.power(r, -4.0) + 8.0e7 * np.power(r, -6.0)


def _leblanc_dr(r):
    return -2.0 * 3.3e5 * np.power(r, -3.0) - 4.0 * 4.1e6 * np.power(r, -5.0) - 6.0 * 8.0e7 * np.power(r, -7.0)


def _baumbach_allen(r):
    return 1e8 * (2.99 * np.power(r, -16.0) + 1.55 * np.power(r, -6.0) + 0.036 * np.power(r, -1.5))


def _baumbach_allen_dr(r):
    return 1e8 * (-16.0 * 2.99 * np.power(r, -17.0) - 6.0 * 1.55 * np.power(r, -7.0) - 1.5 * 0.036 * np.power(r, -2.5))


def _mann(r):
    return _MANN_NS * np.exp(_MANN_A * (1.0 / r - 1.0))


def _mann_dr(r):
    return -_MANN_A * _mann(r) / np.square(r)


DENSITY_MODELS: dict[str, DensityModel] = {
    "newkirk": DensityModel("newkirk", "Newkirk", "Newkirk (1961)", _newkirk, _newkirk_dr),
    "saito": DensityModel("saito", "Saito", "Saito, Poland & Munro (1977)", _saito, _saito_dr),
    "leblanc": DensityModel("leblanc", "Leblanc", "Leblanc, Dulk & Bougeret (1998)", _leblanc, _leblanc_dr),
    "baumbach_allen": DensityModel(
        "baumbach_allen", "Baumbach-Allen", "Baumbach (1937); Allen (1947)", _baumbach_allen, _baumbach_allen_dr
    ),
    "mann": DensityModel("mann", "Mann", "Mann et al. (1999)", _mann, _mann_dr),
}
DENSITY_MODEL_ORDER = tuple(DENSITY_MODELS)
DEFAULT_DENSITY_MODEL = "newkirk"


def normalize_density_model(value) -> str:
    """A known model key; anything unrecognised falls back to Newkirk."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in DENSITY_MODELS:
        return text
    for key, model in DENSITY_MODELS.items():
        if text == model.label.lower().replace("-", "_").replace(" ", "_"):
            return key
    return DEFAULT_DENSITY_MODEL


def density_model_label(value) -> str:
    return DENSITY_MODELS[normalize_density_model(value)].label


def _fold(fold) -> float:
    try:
        value = float(fold)
    except (TypeError, ValueError):
        return 1.0
    return value if math.isfinite(value) and value > 0.0 else 1.0


def model_density_for_frequency(freq_mhz, fold=1) -> np.ndarray:
    """The unscaled model density a plasma frequency calls for (n / fold)."""
    freq = np.asarray(freq_mhz, dtype=float)
    return np.square(freq) / (PLASMA_FREQ_SQ_PER_DENSITY * _fold(fold))


def _solve_height(model: DensityModel, target: np.ndarray) -> np.ndarray:
    """Height where the (monotonically falling) model reaches ``target``."""
    lo = np.full(target.shape, MIN_HEIGHT_RS)
    hi = np.full(target.shape, MAX_HEIGHT_RS)
    inside = (target <= model.density(lo)) & (target >= model.density(hi)) & np.isfinite(target)
    log_lo, log_hi = np.log(lo), np.log(hi)
    for _ in range(_BISECTION_STEPS):
        mid = 0.5 * (log_lo + log_hi)
        above = model.density(np.exp(mid)) > target
        log_lo = np.where(above, mid, log_lo)
        log_hi = np.where(above, log_hi, mid)
    return np.where(inside, np.exp(0.5 * (log_lo + log_hi)), np.nan)


def height_rs_from_frequency(freq_mhz, model="newkirk", fold=1) -> np.ndarray:
    """Emission height (R_sun) of a fundamental plasma frequency (MHz)."""
    key = normalize_density_model(model)
    freq = np.asarray(freq_mhz, dtype=float)
    target = model_density_for_frequency(freq, fold)
    if key == "newkirk":
        with np.errstate(divide="ignore", invalid="ignore"):
            return _NEWKIRK_SCALE * math.log(10.0) / np.log(target / _NEWKIRK_N0)
    if key == "mann":
        with np.errstate(divide="ignore", invalid="ignore"):
            height = 1.0 / (1.0 + np.log(target / _MANN_NS) / _MANN_A)
        valid = np.isfinite(height) & (height >= MIN_HEIGHT_RS) & (height <= MAX_HEIGHT_RS)
        return np.where(valid, height, np.nan)
    return _solve_height(DENSITY_MODELS[key], np.atleast_1d(target)).reshape(target.shape)


def height_gradient_rs_per_mhz(freq_mhz, model="newkirk", fold=1) -> np.ndarray:
    """|dr/df| in R_sun per MHz at each frequency (NaN where out of range)."""
    key = normalize_density_model(model)
    freq = np.asarray(freq_mhz, dtype=float)
    height = height_rs_from_frequency(freq, key, fold)
    if key == "newkirk":
        # The closed form the Analyzer has always used: 8.64 ln10 / (f g^2).
        with np.errstate(divide="ignore", invalid="ignore"):
            g = np.log(model_density_for_frequency(freq, fold) / _NEWKIRK_N0)
            return 2.0 * _NEWKIRK_SCALE * math.log(10.0) / (freq * np.square(g))
    density = DENSITY_MODELS[key].density(height)
    slope = np.abs(DENSITY_MODELS[key].derivative(height))
    with np.errstate(divide="ignore", invalid="ignore"):
        return 2.0 * density / (freq * slope)


def shock_speed_km_s(freq_mhz, drift_mhz_s, model="newkirk", fold=1) -> np.ndarray:
    """Radial speed (km/s) of a source at ``freq_mhz`` drifting at ``drift_mhz_s``."""
    gradient = height_gradient_rs_per_mhz(freq_mhz, model, fold)
    return RSUN_KM * np.abs(np.asarray(drift_mhz_s, dtype=float)) * gradient


def _nan_mean(values) -> float:
    arr = np.asarray(values, dtype=float)
    finite = arr[np.isfinite(arr)]
    return float(np.mean(finite)) if finite.size else float("nan")


def _nan_sem(values) -> float:
    arr = np.asarray(values, dtype=float)
    finite = arr[np.isfinite(arr)]
    return float(np.std(finite) / np.sqrt(finite.size)) if finite.size else float("nan")


def shock_parameters(
    freq_mhz,
    drift_mhz_s,
    drift_err_mhz_s,
    *,
    freq_err_mhz: float,
    model="newkirk",
    fold=1,
    start_percentile: float = 90.0,
) -> dict:
    """Shock speed and height along a fitted burst backbone, for one model.

    ``freq_mhz`` / ``drift_mhz_s`` are the fundamental plasma frequency and its
    drift sampled along the fit. The "initial" values are read where the
    frequency is at its ``start_percentile`` (the start of the burst); the
    averages run over the whole backbone. Uncertainties propagate the drift
    and frequency errors linearly through |dr/df|.
    """
    key = normalize_density_model(model)
    freqs = np.asarray(freq_mhz, dtype=float).reshape(-1)
    drifts = np.asarray(drift_mhz_s, dtype=float).reshape(-1)
    drift_errs = np.asarray(drift_err_mhz_s, dtype=float).reshape(-1)

    gradient = height_gradient_rs_per_mhz(freqs, key, fold)
    speed = RSUN_KM * np.abs(drifts) * gradient
    height = height_rs_from_frequency(freqs, key, fold)

    start_freq = float(np.percentile(freqs, start_percentile))
    idx = int(np.abs(freqs - start_freq).argmin())

    return {
        "density_model": key,
        "fold": int(round(_fold(fold))),
        "shock_speed_km_s": speed,
        "shock_height_rs": height,
        "start_freq_mhz": start_freq,
        "start_index": idx,
        "initial_shock_speed_km_s": float(speed[idx]),
        "initial_shock_speed_err_km_s": float(RSUN_KM * drift_errs[idx] * gradient[idx]),
        "initial_shock_height_rs": float(height[idx]),
        "initial_shock_height_err_rs": float(abs(gradient[idx] * float(freq_err_mhz))),
        "avg_shock_speed_km_s": _nan_mean(speed),
        "avg_shock_speed_err_km_s": _nan_sem(speed),
        "avg_shock_height_rs": _nan_mean(height),
        "avg_shock_height_err_rs": _nan_sem(height),
    }
