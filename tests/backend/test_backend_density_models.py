"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.backend.radio import density_models as dm


@pytest.mark.parametrize("fold", [1, 2, 3, 4])
def test_newkirk_reproduces_the_analyzers_historical_formulas(fold):
    freqs = np.linspace(180.0, 25.0, 50)
    drifts = -0.05 * freqs
    denom = fold * 3.385
    old_speed = 13853221.38 * np.abs(drifts) / (freqs * np.log(freqs ** 2 / denom) ** 2)
    old_height = 4.32 * np.log(10.0) / np.log(freqs ** 2 / denom)

    assert np.allclose(dm.shock_speed_km_s(freqs, drifts, "newkirk", fold), old_speed, rtol=1e-8)
    assert np.allclose(dm.height_rs_from_frequency(freqs, "newkirk", fold), old_height, rtol=1e-12)


@pytest.mark.parametrize("model", ["saito", "leblanc", "baumbach_allen", "mann"])
def test_heights_invert_each_models_density(model):
    freqs = np.array([100.0, 60.0, 25.0, 8.0])
    heights = dm.height_rs_from_frequency(freqs, model, fold=2)

    density = 2.0 * dm.DENSITY_MODELS[model].density(heights)
    assert np.allclose(np.sqrt(density * dm.PLASMA_FREQ_SQ_PER_DENSITY), freqs, rtol=1e-6)
    assert np.all(np.diff(heights) > 0)  # lower frequency sits higher up


@pytest.mark.parametrize("model", dm.DENSITY_MODEL_ORDER)
def test_speed_matches_a_numerical_derivative(model):
    freq, drift, fold = 40.0, -0.2, 1
    step = 1e-4
    dr_df = (
        dm.height_rs_from_frequency(freq + step, model, fold)
        - dm.height_rs_from_frequency(freq - step, model, fold)
    ) / (2 * step)

    expected = dm.RSUN_KM * abs(drift) * abs(float(dr_df))
    assert float(dm.shock_speed_km_s(freq, drift, model, fold)) == pytest.approx(expected, rel=1e-5)


def test_frequencies_a_model_cannot_reach_have_no_height():
    # Saito's density at the photosphere corresponds to ~116 MHz at 1-fold.
    assert np.isnan(dm.height_rs_from_frequency(150.0, "saito", 1))
    assert np.isfinite(dm.height_rs_from_frequency(150.0, "saito", 2))


def test_unknown_model_names_fall_back_to_newkirk():
    assert dm.normalize_density_model("Baumbach-Allen") == "baumbach_allen"
    assert dm.normalize_density_model("nonsense") == "newkirk"
    assert dm.normalize_density_model(None) == "newkirk"


def test_shock_parameters_reads_initial_values_at_the_start_percentile():
    freqs = np.linspace(75.0, 20.0, 400)  # inside Leblanc's range at 1-fold (< ~82 MHz)
    drifts = np.full(freqs.shape, -0.5)
    result = dm.shock_parameters(freqs, drifts, np.full(freqs.shape, 0.05), freq_err_mhz=1.0, model="leblanc")

    start = float(np.percentile(freqs, 90))
    idx = int(np.abs(freqs - start).argmin())
    assert result["start_index"] == idx
    assert np.isfinite(result["initial_shock_height_rs"])
    assert result["initial_shock_height_rs"] == pytest.approx(float(result["shock_height_rs"][idx]))
    assert result["avg_shock_speed_km_s"] == pytest.approx(float(np.nanmean(result["shock_speed_km_s"])))
