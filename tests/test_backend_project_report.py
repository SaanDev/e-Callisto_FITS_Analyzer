"""
e-CALLISTO FITS Analyzer
Version 2.4.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np

from src.Backend.project_report import (
    ProjectReportInput,
    build_analyzer_fit_figure,
    build_dynamic_spectrum_figure,
    build_light_curve_figure,
    build_max_intensity_figure,
    build_type_ii_figure,
    generate_project_report_pdf,
)


def _sample_session() -> dict:
    return {
        "analysis_run_id": "run-1",
        "max_intensity": {
            "time_channels": np.array([0.0, 1.0, 2.0, 3.0]),
            "time_seconds": np.array([0.0, 2.0, 4.0, 6.0]),
            "freqs": np.array([90.0, 78.0, 65.0, 55.0]),
            "fundamental": True,
            "harmonic": False,
        },
        "analyzer": {
            "fit_params": {"a": 95.0, "b": 0.3, "r2": 0.91, "rmse": 1.2},
            "fold": 2,
            "shock_summary": {
                "avg_freq_mhz": 72.0,
                "avg_drift_mhz_s": -0.12,
                "avg_shock_speed_km_s": 900.0,
                "avg_shock_height_rs": 1.8,
            },
        },
        "type_ii": {
            "upper": {
                "time_seconds": np.array([1.0, 2.0, 3.0]),
                "freqs": np.array([95.0, 88.0, 80.0]),
            },
            "lower": {
                "time_seconds": np.array([1.0, 2.0, 3.0]),
                "freqs": np.array([75.0, 68.0, 60.0]),
            },
            "upper_fit": {"a": 98.0, "b": 0.2},
            "lower_fit": {"a": 76.0, "b": 0.22},
            "results": {"compression_ratio": 1.3, "alfven_speed_km_s": 450.0, "magnetic_field_g": 0.8},
        },
    }


def test_report_figure_builders_return_png_bytes_with_nan_data():
    data = np.array(
        [
            [1.0, 2.0, np.nan, 4.0],
            [2.0, 3.0, 4.0, 5.0],
            [np.nan, np.nan, np.nan, np.nan],
        ]
    )
    freqs = np.array([90.0, 80.0, 70.0])
    time = np.array([0.0, 1.0, 2.0, 3.0])
    session = _sample_session()

    figures = [
        build_dynamic_spectrum_figure(data=data, freqs=freqs, time=time, title="Raw", unit_label="Digits"),
        build_light_curve_figure(
            data=data,
            freqs=freqs,
            time=time,
            records=[{"frequency_mhz": 80.0, "requested_mhz": 79.9}],
            unit_label="Digits",
        ),
        build_max_intensity_figure(session),
        build_analyzer_fit_figure(session),
        build_type_ii_figure(session),
    ]

    assert all(fig is not None for fig in figures)
    assert all(fig.image_png.startswith(b"\x89PNG") for fig in figures if fig is not None)


def test_generate_project_report_pdf_minimal(tmp_path):
    out = tmp_path / "minimal_report.pdf"
    report = ProjectReportInput(
        title="Minimal Project Report",
        app={"name": "e-CALLISTO FITS Analyzer", "version": "2.4.1"},
        data_source={"filename": "demo.fit", "shape": [3, 4]},
        processing={"plot_type": "Raw", "use_db": False, "use_utc": False, "cmap": "Custom"},
        selected_header={"DATE-OBS": "2026-01-01"},
    )

    result = generate_project_report_pdf(str(out), report)

    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF")
    assert result.file_size == out.stat().st_size
    assert result.figures_written == 0


def test_generate_project_report_pdf_with_optional_analysis_and_figures(tmp_path):
    data = np.arange(12, dtype=float).reshape(3, 4)
    freqs = np.array([90.0, 80.0, 70.0])
    time = np.array([0.0, 1.0, 2.0, 3.0])
    session = _sample_session()
    figure = build_dynamic_spectrum_figure(
        data=data,
        freqs=freqs,
        time=time,
        title="Raw Spectrum",
        unit_label="Digits",
    )
    assert figure is not None

    out = tmp_path / "full_report.pdf"
    report = ProjectReportInput(
        title="Full Project Report",
        app={"name": "e-CALLISTO FITS Analyzer", "version": "2.4.1"},
        data_source={"filename": "demo.fit", "shape": [3, 4], "freq_range_mhz": [70.0, 90.0]},
        processing={"plot_type": "Background Subtracted", "use_db": False, "use_utc": True, "cmap": "viridis"},
        rfi={"enabled": True, "applied": False},
        annotations=[{"kind": "text", "text": "burst", "visible": True}],
        light_curve={"records": [{"frequency_mhz": 80.0}], "settings": {"mode": "single"}},
        operation_log=[{"ts": "2026-01-01T00:00:00+00:00", "msg": "loaded"}],
        analysis_session=session,
        selected_header={"DATE-OBS": "2026-01-01"},
        full_header="DATE-OBS= '2026-01-01'",
        figures=[figure, build_max_intensity_figure(session), build_type_ii_figure(session)],
    )

    result = generate_project_report_pdf(str(out), report)

    assert out.read_bytes().startswith(b"%PDF")
    assert result.figures_written >= 2
