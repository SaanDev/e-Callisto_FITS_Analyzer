from __future__ import annotations

import csv

from src.Backend.analysis_log import CSV_COLUMNS, append_csv_log, append_txt_summary, build_log_row


def _sample_session() -> dict:
    return {
        "analysis_run_id": "run-123",
        "max_intensity": {"fundamental": True, "harmonic": False},
        "analyzer": {
            "fold": 2,
            "fit_params": {"a": 11.5, "b": -0.45, "r2": 0.93, "rmse": 0.7},
            "shock_summary": {
                "avg_freq_mhz": 63.0,
                "avg_freq_err_mhz": 1.1,
                "avg_drift_mhz_s": -0.12,
                "avg_drift_err_mhz_s": 0.01,
                "start_freq_mhz": 90.0,
                "start_freq_err_mhz": 0.8,
                "initial_shock_speed_km_s": 980.0,
                "initial_shock_speed_err_km_s": 40.0,
                "initial_shock_height_rs": 1.5,
                "initial_shock_height_err_rs": 0.08,
                "avg_shock_speed_km_s": 920.0,
                "avg_shock_speed_err_km_s": 35.0,
                "avg_shock_height_rs": 1.9,
                "avg_shock_height_err_rs": 0.09,
            },
        },
    }


def test_csv_append_writes_header_once(tmp_path):
    row = build_log_row(
        project_path="/tmp/demo.efaproj",
        fits_primary="A.fit",
        fits_sources=["A.fit", "B.fit"],
        combined_mode="time",
        station="A",
        date_obs="2026-02-14",
        session=_sample_session(),
    )

    csv_path = tmp_path / "analysis_log.csv"
    append_csv_log(str(csv_path), row)
    append_csv_log(str(csv_path), row)

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))

    assert rows[0] == CSV_COLUMNS
    assert len(rows) == 3


def test_txt_append_creates_segmented_summary(tmp_path):
    row = build_log_row(
        project_path="/tmp/demo.efaproj",
        fits_primary="A.fit",
        fits_sources=["A.fit"],
        combined_mode="",
        station="A",
        date_obs="2026-02-14",
        session=_sample_session(),
    )

    txt_path = tmp_path / "analysis_log.txt"
    append_txt_summary(str(txt_path), row)
    append_txt_summary(str(txt_path), row)

    text = txt_path.read_text(encoding="utf-8")
    assert text.count("Analysis Log Entry UTC") == 2
    assert "Analysis Run ID: run-123" in text
    assert "Shock parameters:" in text


def test_missing_analyzer_state_builds_safe_blanks():
    row = build_log_row(
        project_path="",
        fits_primary="A.fit",
        fits_sources=[],
        combined_mode="",
        station="",
        date_obs="",
        session=None,
    )

    assert row["fit_a"] == ""
    assert row["avg_freq_mhz"] == ""
    assert row["harmonic"] == ""
