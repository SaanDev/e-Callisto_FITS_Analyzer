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
    assert session["analyzer"]["fit_params"]["a"] == 20.0
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
    assert session["analyzer"]["fit_params"]["b"] == -0.3
    assert len(session["max_intensity"]["time_channels"]) == 6


def test_to_project_payload_moves_vectors_to_arrays():
    session = normalize_session(
        {
            "source": {"filename": "x.fit"},
            "max_intensity": {
                "time_channels": [1, 2, 3],
                "freqs": [90, 80, 70],
            },
            "analyzer": {"fold": 1},
        }
    )

    meta_session, arrays = to_project_payload(session)
    assert meta_session is not None
    assert "time_channels" not in meta_session["max_intensity"]
    assert "freqs" not in meta_session["max_intensity"]
    assert np.array_equal(arrays["analysis_time_channels"], np.array([1, 2, 3], dtype=float))
    assert np.array_equal(arrays["analysis_freqs"], np.array([90, 80, 70], dtype=float))


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
