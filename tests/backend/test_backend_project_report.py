"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import io
import pytest
from PIL import Image

from src.backend.session.project_report import (
    ProjectReportFigure,
    ProjectReportInput,
    generate_project_report_pdf,
)


def _tiny_png() -> bytes:
    buf = io.BytesIO()
    image = Image.new("RGB", (12, 12), color=(31, 96, 196))
    image.save(buf, format="PNG")
    return buf.getvalue()


def _sample_session() -> dict:
    return {
        "analysis_run_id": "run-1",
        "max_intensity": {
            "time_channels": [0.0, 1.0, 2.0, 3.0],
            "time_seconds": [0.0, 2.0, 4.0, 6.0],
            "freqs": [90.0, 78.0, 65.0, 55.0],
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
                "time_seconds": [1.0, 2.0, 3.0],
                "freqs": [95.0, 88.0, 80.0],
            },
            "lower": {
                "time_seconds": [1.0, 2.0, 3.0],
                "freqs": [75.0, 68.0, 60.0],
            },
            "upper_fit": {"a": 98.0, "b": 0.2},
            "lower_fit": {"a": 76.0, "b": 0.22},
            "results": {"compression_ratio": 1.3, "alfven_speed_km_s": 450.0, "magnetic_field_g": 0.8},
        },
    }


def test_report_figure_accepts_pre_rendered_png_and_unavailable_note():
    figure = ProjectReportFigure(
        title="Raw Dynamic Spectrum",
        source_filename="demo.fit",
        png_bytes=_tiny_png(),
        caption="Captured from the UI.",
    )
    unavailable = ProjectReportFigure(
        title="Kp Index",
        source_filename="demo.fit",
        availability_note="Kp index data is not available.",
    )

    assert figure.png_bytes.startswith(b"\x89PNG")
    assert figure.image_png == figure.png_bytes
    assert unavailable.png_bytes is None
    assert unavailable.availability_note == "Kp index data is not available."


def test_generate_project_report_pdf_minimal(tmp_path):
    out = tmp_path / "minimal_report.pdf"
    report = ProjectReportInput(
        title="Minimal Project Report",
        app={"name": "e-CALLISTO FITS Analyzer", "version": "2.6.0"},
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
    session = _sample_session()
    figure = ProjectReportFigure(
        title="Raw Dynamic Spectrum",
        source_filename="demo.fit",
        png_bytes=_tiny_png(),
        caption="Captured from the UI.",
    )

    out = tmp_path / "full_report.pdf"
    report = ProjectReportInput(
        title="Full Project Report",
        app={"name": "e-CALLISTO FITS Analyzer", "version": "2.6.0"},
        data_source={"filename": "demo.fit", "shape": [3, 4], "freq_range_mhz": [70.0, 90.0]},
        processing={"plot_type": "Background Subtracted", "use_db": False, "use_utc": True, "cmap": "viridis"},
        rfi={"enabled": True, "applied": False},
        annotations=[{"kind": "text", "text": "burst", "visible": True}],
        light_curve={"records": [{"frequency_mhz": 80.0}], "settings": {"mode": "single"}},
        analysis_session=session,
        selected_header={"DATE-OBS": "2026-01-01"},
        full_header="DATE-OBS= '2026-01-01'",
        figures=[
            figure,
            ProjectReportFigure(title="Dst Index", availability_note="Not available"),
        ],
    )

    result = generate_project_report_pdf(str(out), report)

    assert out.read_bytes().startswith(b"%PDF")
    assert result.figures_written == 1


def test_project_report_input_does_not_accept_operation_log():
    with pytest.raises(TypeError):
        ProjectReportInput(
            title="No Operation Log",
            operation_log=[{"ts": "2026-01-01T00:00:00+00:00", "msg": "loaded"}],
        )


def _section_titles(monkeypatch):
    """Record the radio sections' headings (they go through ``_section``)."""
    from src.backend.session import project_report

    titles = []
    original = project_report._section

    def record(story, rl, styles, title):
        titles.append(title)
        original(story, rl, styles, title)

    monkeypatch.setattr(project_report, "_section", record)
    return titles


def test_a_report_without_radio_data_has_only_its_window_sections(tmp_path, monkeypatch):
    from src.backend.session.project_report import ProjectReportSection

    titles = _section_titles(monkeypatch)
    solar = ProjectReportSection(
        "Solar Image Analysis",
        pairs=[("Instrument", "SOHO/LASCO C2"), ("Height–time picks", 3)],
        figures=[ProjectReportFigure("Current Frame", png_bytes=_tiny_png(), caption="SOHO/LASCO | C2")],
    )
    gcs = ProjectReportSection(
        "GCS CME Fitting",
        pairs=[("Recorded GCS fits", 1)],
        figures=[ProjectReportFigure("GCS Viewpoints", png_bytes=_tiny_png())],
        table=(["UTC", "Height (R☉)"], [["2012-07-12 16:24:00", "4.10"]]),
        table_title="Recorded GCS Fits",
        notes=["The window's own report has every fit."],
    )
    report = ProjectReportInput(title="Solar Only", app={"name": "e-CALLISTO FITS Analyzer", "version": "3.2.0"},
                                has_radio=False, sections=[solar, gcs])
    result = generate_project_report_pdf(str(tmp_path / "solar_only.pdf"), report)

    assert (tmp_path / "solar_only.pdf").read_bytes().startswith(b"%PDF")
    assert result.figures_written == 2
    # No Data Source, FITS header, processing, analysis or appendix sections.
    assert titles == []


def test_window_sections_follow_the_radio_sections(tmp_path, monkeypatch):
    from src.backend.session.project_report import ProjectReportSection

    titles = _section_titles(monkeypatch)
    report = ProjectReportInput(
        title="Radio And GCS", data_source={"filename": "demo.fit"},
        sections=[ProjectReportSection("GCS CME Fitting", table=(["UTC"], []), table_title="Recorded GCS Fits")],
    )
    result = generate_project_report_pdf(str(tmp_path / "both.pdf"), report)
    assert result.figures_written == 0
    assert titles[0] == "Data Source" and titles[-1] == "Full FITS Header Appendix"
