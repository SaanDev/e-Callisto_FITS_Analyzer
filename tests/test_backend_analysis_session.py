"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np

from src.Backend.analysis_session import (
    SESSION_SCHEMA_VERSION,
    from_legacy_max_intensity,
    normalize_session,
    to_project_payload,
    validate_session_for_source,
)


def test_normalize_session_valid_payload():
    session = normalize_session(
        {
            "source": {"filename": "A.fit", "shape": [8, 5]},
            "max_intensity": {
                "time_channels": [0, 1, 2, 3, 4],
                "time_seconds": [0, 3, 6, 9, 12],
                "freqs": [80, 79, 78, 77, 76],
                "fundamental": True,
                "harmonic": False,
            },
            "analyzer": {
                "fit_params": {"a": 20.0, "b": -0.4, "std_errs": [0.1, 0.01], "r2": 0.9, "rmse": 0.5},
                "fold": 2,
                "shock_summary": {"avg_freq_mhz": 75.0},
            },
            "ui": {"restore_max_window": True, "restore_analyzer_window": True},
        }
    )

    assert session is not None
    assert session["version"] == SESSION_SCHEMA_VERSION
    assert session["analysis_run_id"]
    assert session["source"]["filename"] == "A.fit"
    assert np.array_equal(session["max_intensity"]["time_channels"], np.array([0, 1, 2, 3, 4], dtype=float))
    assert np.array_equal(session["max_intensity"]["time_seconds"], np.array([0, 3, 6, 9, 12], dtype=float))
    assert session["analyzer"]["fit_params"]["a"] == 20.0
    assert session["analyzer"]["fit_params"]["b"] == 0.4
    assert session["analyzer"]["fold"] == 2


def test_from_legacy_max_intensity_migrates_payload():
    meta = {
        "filename": "legacy.fit",
        "is_combined": False,
        "max_intensity": {
            "present": True,
            "fundamental": True,
            "harmonic": False,
            "analyzer": {
                "fit_params": {"a": 12.0, "b": -0.3, "std_errs": [0.2, 0.03], "r2": 0.85, "rmse": 1.2},
                "fold": 3,
            },
        },
    }
    arrays = {
        "max_time_channels": np.arange(6, dtype=float),
        "max_freqs": np.linspace(90, 70, 6, dtype=float),
    }

    session = from_legacy_max_intensity(meta, arrays)
    assert session is not None
    assert session["source"]["filename"] == "legacy.fit"
    assert session["analyzer"]["fold"] == 3
    assert session["analyzer"]["fit_params"]["b"] == 0.3
    assert len(session["max_intensity"]["time_channels"]) == 6


def test_to_project_payload_moves_vectors_to_arrays():
    session = normalize_session(
        {
            "source": {"filename": "x.fit"},
            "max_intensity": {
                "time_channels": [1, 2, 3],
                "time_seconds": [3, 6, 9],
                "freqs": [90, 80, 70],
            },
            "analyzer": {"fold": 1},
        }
    )

    meta_session, arrays = to_project_payload(session)
    assert meta_session is not None
    assert "time_channels" not in meta_session["max_intensity"]
    assert "time_seconds" not in meta_session["max_intensity"]
    assert "freqs" not in meta_session["max_intensity"]
    assert np.array_equal(arrays["analysis_time_channels"], np.array([1, 2, 3], dtype=float))
    assert np.array_equal(arrays["analysis_time_seconds"], np.array([3, 6, 9], dtype=float))
    assert np.array_equal(arrays["analysis_freqs"], np.array([90, 80, 70], dtype=float))


def test_type_ii_payload_round_trip_moves_points_to_arrays():
    session = normalize_session(
        {
            "source": {"filename": "x.fit", "shape": [8, 5]},
            "type_ii": {
                "upper": {"time_seconds": [1.0, 2.0, 3.0], "freqs": [90.0, 84.0, 79.0]},
                "lower": {"time_seconds": [1.0, 2.0, 3.0], "freqs": [78.0, 73.0, 69.0]},
                "upper_fit": {"a": 90.0, "b": 0.4, "std_errs": [0.1, 0.01], "r2": 0.98, "rmse": 0.5},
                "lower_fit": {"a": 78.0, "b": 0.38, "std_errs": [0.1, 0.01], "r2": 0.97, "rmse": 0.6},
                "analysis_inputs": {
                    "initial_shock_speed_km_s": 980.0,
                    "avg_shock_speed_km_s": 820.0,
                    "initial_shock_height_rs": 1.32,
                    "avg_shock_height_rs": 1.48,
                    "start_freq_mhz": 63.0,
                    "avg_drift_mhz_s": -0.124,
                    "avg_drift_err_mhz_s": 0.011,
                    "fold": 2,
                    "speed_mode": "average",
                },
                "results": {
                    "end_time_s": 3.0,
                    "avg_upper_freq_mhz": 84.0,
                    "avg_lower_freq_mhz": 73.0,
                    "bandwidth_mhz": 11.0,
                    "upper_avg_drift_mhz_s": -4.1,
                    "compression_ratio": 1.44,
                    "alfven_speed_km_s": 520.0,
                    "magnetic_field_g": 0.52,
                },
            },
            "ui": {"restore_type_ii_window": True},
        }
    )

    meta_session, arrays = to_project_payload(session)
    assert meta_session is not None
    assert "time_seconds" not in meta_session["type_ii"]["upper"]
    assert "freqs" not in meta_session["type_ii"]["upper"]
    assert np.array_equal(arrays["type_ii_upper_time_seconds"], np.array([1.0, 2.0, 3.0], dtype=float))
    assert np.array_equal(arrays["type_ii_upper_freqs"], np.array([90.0, 84.0, 79.0], dtype=float))
    assert np.array_equal(arrays["type_ii_lower_time_seconds"], np.array([1.0, 2.0, 3.0], dtype=float))
    assert np.array_equal(arrays["type_ii_lower_freqs"], np.array([78.0, 73.0, 69.0], dtype=float))
    assert meta_session["type_ii"]["analysis_inputs"]["speed_mode"] == "average"
    assert meta_session["type_ii"]["analysis_inputs"]["fold"] == 2


def test_normalize_session_accepts_legacy_type_ii_shock_result_fields():
    session = normalize_session(
        {
            "source": {"filename": "x.fit", "shape": [100, 5]},
            "type_ii": {
                "upper": {"time_seconds": [1.0, 2.0], "freqs": [90.0, 85.0]},
                "lower": {"time_seconds": [1.0, 2.0], "freqs": [80.0, 76.0]},
                "fold": 2,
                "results": {"shock_speed_km_s": 980.0, "shock_height_rs": 1.45, "magnetic_field_g": 0.52},
            },
        }
    )

    assert session is not None
    assert session["type_ii"]["fold"] == 2
    assert session["type_ii"]["results"]["magnetic_field_g"] == 0.52
    assert "shock_speed_km_s" not in session["type_ii"]["results"]


def test_validate_session_for_source_mismatch_reports_error():
    session = normalize_session(
        {
            "source": {"filename": "x.fit", "shape": [100, 5]},
            "max_intensity": {
                "time_channels": [0, 1, 2, 3, 4],
                "freqs": [90, 89, 88, 87, 86],
            },
        }
    )

    ok, reason = validate_session_for_source(session, current_shape=(100, 6))
    assert ok is False
    assert "time-axis length" in reason or "shape" in reason


def test_validate_session_accepts_type_ii_only_payload():
    session = normalize_session(
        {
            "source": {"filename": "x.fit", "shape": [100, 5]},
            "type_ii": {
                "upper": {"time_seconds": [1.0, 2.0], "freqs": [90.0, 85.0]},
                "lower": {"time_seconds": [1.0, 2.0], "freqs": [80.0, 76.0]},
                "fold": 1,
            },
            "ui": {"restore_type_ii_window": True},
        }
    )

    ok, reason = validate_session_for_source(session, current_shape=(100, 5))
    assert ok is True
    assert reason == ""
