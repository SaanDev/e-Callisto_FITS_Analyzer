"""
e-CALLISTO FITS Analyzer
Version 2.4.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
import io
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np


ProgressCallback = Callable[[int, str], None]


class ReportGenerationCancelled(RuntimeError):
    pass


@dataclass(init=False)
class ProjectReportFigure:
    title: str
    source_filename: str = ""
    caption: str = ""
    png_bytes: bytes | None = None
    image_path: str = ""
    availability_note: str = ""

    def __init__(
        self,
        title: str,
        *,
        source_filename: str = "",
        caption: str = "",
        png_bytes: bytes | None = None,
        image_png: bytes | None = None,
        image_path: str = "",
        availability_note: str = "",
    ):
        self.title = str(title or "Figure")
        self.source_filename = str(source_filename or "")
        self.caption = str(caption or "")
        self.png_bytes = png_bytes if png_bytes is not None else image_png
        self.image_path = str(image_path or "")
        self.availability_note = str(availability_note or "")

    @property
    def image_png(self) -> bytes | None:
        return self.png_bytes

    @image_png.setter
    def image_png(self, value: bytes | None) -> None:
        self.png_bytes = value


@dataclass
class ProjectReportInput:
    title: str
    app: Mapping[str, Any] = field(default_factory=dict)
    data_source: Mapping[str, Any] = field(default_factory=dict)
    processing: Mapping[str, Any] = field(default_factory=dict)
    rfi: Mapping[str, Any] = field(default_factory=dict)
    annotations: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    light_curve: Mapping[str, Any] = field(default_factory=dict)
    analysis_session: Mapping[str, Any] | None = None
    analysis_row: Mapping[str, Any] = field(default_factory=dict)
    project_path: str = ""
    fits_primary: str = ""
    station: str = ""
    date_obs: str = ""
    generated_at: str = ""
    selected_header: Mapping[str, Any] = field(default_factory=dict)
    full_header: str = ""
    figures: Sequence[ProjectReportFigure] = field(default_factory=tuple)


@dataclass
class ProjectReportResult:
    path: str
    file_size: int
    figures_written: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _emit(progress_cb: ProgressCallback | None, value: int, text: str) -> None:
    if progress_cb is not None:
        progress_cb(int(value), str(text))


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _fmt_number(value: Any, digits: int = 5) -> str:
    if value is None:
        return "Not available"
    if isinstance(value, str) and value == "":
        return "Not available"
    try:
        f = float(value)
    except Exception:
        return str(value)
    if not math.isfinite(f):
        return "Not available"
    return f"{f:.{digits}g}"


def _fmt_value(value: Any) -> str:
    if value is None:
        return "Not available"
    if isinstance(value, str) and value == "":
        return "Not available"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (np.bool_,)):
        return "Yes" if bool(value) else "No"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return _fmt_number(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        if not value:
            return "Not available"
        parts = [f"{k}: {_fmt_value(v)}" for k, v in value.items()]
        return "; ".join(parts)
    if isinstance(value, (list, tuple, set, np.ndarray)):
        try:
            values = list(value.tolist()) if isinstance(value, np.ndarray) else list(value)
        except Exception:
            return str(value)
        if not values:
            return "Not available"
        preview = ", ".join(_fmt_value(item) for item in values[:8])
        if len(values) > 8:
            preview += f", ... ({len(values)} total)"
        return preview
    return str(value)


def _safe_para_text(value: Any) -> str:
    return escape(_fmt_value(value)).replace("\n", "<br/>")


def _import_reportlab():
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Image,
            KeepTogether,
            PageBreak,
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception as exc:
        raise RuntimeError(
            "ReportLab is required to generate PDF project reports. "
            "Install it with: python3 -m pip install reportlab"
        ) from exc
    return {
        "colors": colors,
        "TA_CENTER": TA_CENTER,
        "A4": A4,
        "ParagraphStyle": ParagraphStyle,
        "getSampleStyleSheet": getSampleStyleSheet,
        "inch": inch,
        "Image": Image,
        "KeepTogether": KeepTogether,
        "PageBreak": PageBreak,
        "Paragraph": Paragraph,
        "Preformatted": Preformatted,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
        "Table": Table,
        "TableStyle": TableStyle,
    }


def _make_styles(rl):
    styles = rl["getSampleStyleSheet"]()
    ParagraphStyle = rl["ParagraphStyle"]
    TA_CENTER = rl["TA_CENTER"]
    colors = rl["colors"]
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=27,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#111827"),
            spaceAfter=16,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSubtitle",
            parent=styles["Normal"],
            alignment=TA_CENTER,
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#4b5563"),
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SmallText",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#374151"),
            wordWrap="CJK",
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableKey",
            parent=styles["SmallText"],
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#111827"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Caption",
            parent=styles["SmallText"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#6b7280"),
            spaceAfter=8,
        )
    )
    styles["Heading1"].textColor = colors.HexColor("#111827")
    styles["Heading2"].textColor = colors.HexColor("#1f2937")
    styles["Heading2"].spaceBefore = 10
    styles["Heading2"].spaceAfter = 7
    styles["Normal"].wordWrap = "CJK"
    return styles


def _pair_table(rl, styles, pairs: Sequence[tuple[str, Any]], width: float):
    Paragraph = rl["Paragraph"]
    Table = rl["Table"]
    TableStyle = rl["TableStyle"]
    colors = rl["colors"]
    rows = []
    for key, value in pairs:
        rows.append(
            [
                Paragraph(_safe_para_text(key), styles["TableKey"]),
                Paragraph(_safe_para_text(value), styles["SmallText"]),
            ]
        )
    if not rows:
        rows = [[Paragraph("Status", styles["TableKey"]), Paragraph("Not available", styles["SmallText"])]]
    table = Table(rows, colWidths=[width * 0.32, width * 0.68], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7dce3")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f6fa")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _section(story: list[Any], rl, styles, title: str) -> None:
    story.append(rl["Paragraph"](escape(title), styles["Heading2"]))


def _available_pairs(mapping: Mapping[str, Any], keys: Sequence[tuple[str, str]]) -> list[tuple[str, Any]]:
    src = _as_mapping(mapping)
    return [(label, src.get(key)) for label, key in keys]


def _fit_equation_text(fit: Mapping[str, Any]) -> str | None:
    try:
        a = float(fit.get("a"))
        b = abs(float(fit.get("b")))
    except Exception:
        return None
    return f"f(x) = {_fmt_number(a)} * x^-{_fmt_number(b)}"


def _analysis_pairs(report: ProjectReportInput) -> list[tuple[str, Any]]:
    row = _as_mapping(report.analysis_row)
    sess = _as_mapping(report.analysis_session)
    analyzer = _as_mapping(sess.get("analyzer"))
    fit = _as_mapping(analyzer.get("fit_params"))
    shock = _as_mapping(analyzer.get("shock_summary"))
    pairs = [
        ("Analysis run ID", sess.get("analysis_run_id")),
        ("Fit equation", _fit_equation_text(fit)),
        ("R2", fit.get("r2", row.get("fit_r2"))),
        ("RMSE", fit.get("rmse", row.get("fit_rmse"))),
        ("Fold", analyzer.get("fold", row.get("fold"))),
        ("Fundamental", _as_mapping(sess.get("max_intensity")).get("fundamental", row.get("fundamental"))),
        ("Harmonic", _as_mapping(sess.get("max_intensity")).get("harmonic", row.get("harmonic"))),
        ("Average frequency (MHz)", shock.get("avg_freq_mhz", row.get("avg_freq_mhz"))),
        ("Average drift (MHz/s)", shock.get("avg_drift_mhz_s", row.get("avg_drift_mhz_s"))),
        ("Initial shock speed (km/s)", shock.get("initial_shock_speed_km_s", row.get("initial_shock_speed_km_s"))),
        ("Average shock speed (km/s)", shock.get("avg_shock_speed_km_s", row.get("avg_shock_speed_km_s"))),
        ("Average shock height (Rs)", shock.get("avg_shock_height_rs", row.get("avg_shock_height_rs"))),
    ]
    return pairs


def _type_ii_pairs(session: Mapping[str, Any] | None) -> list[tuple[str, Any]]:
    type_ii = _as_mapping(_as_mapping(session).get("type_ii"))
    results = _as_mapping(type_ii.get("results"))
    return [
        ("Start time (s)", results.get("start_time_s")),
        ("End time (s)", results.get("end_time_s")),
        ("Bandwidth (MHz)", results.get("bandwidth_mhz")),
        ("Compression ratio", results.get("compression_ratio")),
        ("Alfven Mach number", results.get("alfven_mach_number")),
        ("Alfven speed (km/s)", results.get("alfven_speed_km_s")),
        ("Magnetic field (G)", results.get("magnetic_field_g")),
        ("Warning", results.get("warning")),
    ]


def _fit_image(rl, image_bytes: bytes, max_width: float, max_height: float):
    Image = rl["Image"]
    img = Image(io.BytesIO(image_bytes))
    width = float(getattr(img, "imageWidth", 0) or 1)
    height = float(getattr(img, "imageHeight", 0) or 1)
    scale = min(max_width / width, max_height / height, 1.0)
    img.drawWidth = width * scale
    img.drawHeight = height * scale
    return img


def _draw_header_footer(report_title: str, rl):
    colors = rl["colors"]
    A4 = rl["A4"]
    inch = rl["inch"]

    def _inner(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d7dce3"))
        canvas.setLineWidth(0.4)
        canvas.line(doc.leftMargin, 0.55 * inch, A4[0] - doc.rightMargin, 0.55 * inch)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.setFont("Helvetica", 8)
        if canvas.getPageNumber() > 1:
            canvas.drawString(doc.leftMargin, A4[1] - 0.45 * inch, report_title[:90])
        canvas.drawRightString(A4[0] - doc.rightMargin, 0.35 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    return _inner


def generate_project_report_pdf(
    output_path: str,
    report: ProjectReportInput,
    *,
    progress_cb: ProgressCallback | None = None,
) -> ProjectReportResult:
    if not output_path:
        raise ValueError("Output path is required for the project report.")
    path = Path(output_path)
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)

    rl = _import_reportlab()
    styles = _make_styles(rl)
    SimpleDocTemplate = rl["SimpleDocTemplate"]
    Paragraph = rl["Paragraph"]
    Spacer = rl["Spacer"]
    KeepTogether = rl["KeepTogether"]
    PageBreak = rl["PageBreak"]
    Preformatted = rl["Preformatted"]
    A4 = rl["A4"]
    inch = rl["inch"]

    title = str(report.title or "e-CALLISTO Project Report")
    generated_at = str(report.generated_at or _now_iso())

    _emit(progress_cb, 5, "Preparing report layout...")
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.72 * inch,
        title=title,
        author=str(_as_mapping(report.app).get("name") or "e-CALLISTO FITS Analyzer"),
    )
    story: list[Any] = []

    story.append(Spacer(1, 0.6 * inch))
    story.append(Paragraph(escape(title), styles["ReportTitle"]))
    subtitle = [
        f"Generated at: {escape(generated_at)}",
        f"Application: {escape(_fmt_value(_as_mapping(report.app).get('name')))} {escape(_fmt_value(_as_mapping(report.app).get('version')))}",
    ]
    if report.project_path:
        subtitle.append(f"Project: {escape(report.project_path)}")
    if report.fits_primary:
        subtitle.append(f"FITS primary: {escape(report.fits_primary)}")
    story.append(Paragraph("<br/>".join(subtitle), styles["ReportSubtitle"]))
    story.append(Spacer(1, 0.25 * inch))
    story.append(
        _pair_table(
            rl,
            styles,
            [
                ("Station", report.station),
                ("Observation date", report.date_obs),
                ("Current plot", _as_mapping(report.processing).get("plot_type")),
                ("Source file", _as_mapping(report.data_source).get("filename")),
            ],
            doc.width,
        )
    )
    story.append(PageBreak())

    _emit(progress_cb, 18, "Adding data and processing sections...")
    _section(story, rl, styles, "Data Source")
    story.append(
        _pair_table(
            rl,
            styles,
            _available_pairs(
                report.data_source,
                [
                    ("Filename", "filename"),
                    ("Combined", "is_combined"),
                    ("Combined mode", "combined_mode"),
                    ("Array shape", "shape"),
                    ("Frequency range (MHz)", "freq_range_mhz"),
                    ("Time range (s)", "time_range_s"),
                    ("Sources", "sources"),
                ],
            )
            + [
                ("Project path", report.project_path),
                ("FITS primary", report.fits_primary),
            ],
            doc.width,
        )
    )
    story.append(Spacer(1, 0.12 * inch))

    _section(story, rl, styles, "Selected FITS Header")
    story.append(_pair_table(rl, styles, list(_as_mapping(report.selected_header).items()), doc.width))
    story.append(Spacer(1, 0.12 * inch))

    _section(story, rl, styles, "Processing")
    processing_pairs = _available_pairs(
        report.processing,
        [
            ("Plot type", "plot_type"),
            ("Units in dB", "use_db"),
            ("Use UT axis", "use_utc"),
            ("Colormap", "cmap"),
            ("Noise clip low", "noise_clip_low"),
            ("Noise clip high", "noise_clip_high"),
            ("Noise clip scale", "noise_clip_scale"),
            ("Active preset", "active_preset"),
        ],
    )
    story.append(_pair_table(rl, styles, processing_pairs, doc.width))
    story.append(Spacer(1, 0.12 * inch))

    _section(story, rl, styles, "RFI, Annotations, And Light Curves")
    story.append(
        _pair_table(
            rl,
            styles,
            [
                ("RFI settings", report.rfi),
                ("Annotation count", len(list(report.annotations or []))),
                ("Annotations", report.annotations),
                ("Light curve settings", _as_mapping(report.light_curve).get("settings")),
                ("Light curve records", _as_mapping(report.light_curve).get("records")),
            ],
            doc.width,
        )
    )
    story.append(PageBreak())

    _emit(progress_cb, 34, "Adding analysis sections...")
    _section(story, rl, styles, "Analysis Summary")
    story.append(_pair_table(rl, styles, _analysis_pairs(report), doc.width))
    story.append(Spacer(1, 0.12 * inch))
    _section(story, rl, styles, "Type II Results")
    story.append(_pair_table(rl, styles, _type_ii_pairs(report.analysis_session), doc.width))
    story.append(Spacer(1, 0.12 * inch))

    figures_written = 0
    figure_items = [fig for fig in list(report.figures or []) if fig]
    if not figure_items:
        _section(story, rl, styles, "Figures")
        story.append(Paragraph("Not available", styles["Normal"]))
    for idx, figure in enumerate(figure_items, start=1):
        _emit(progress_cb, 40 + int(35 * idx / max(1, len(figure_items))), f"Adding figure: {figure.title}")
        figure_block = []
        if idx == 1:
            figure_block.append(Paragraph("Figures", styles["Heading2"]))
        figure_block.append(Paragraph(escape(str(figure.title or f"Figure {idx}")), styles["Heading3"]))
        image_bytes = figure.png_bytes
        if image_bytes is None and figure.image_path:
            try:
                image_bytes = Path(figure.image_path).read_bytes()
            except Exception:
                image_bytes = None
        if image_bytes:
            try:
                figure_block.append(_fit_image(rl, image_bytes, doc.width, 4.65 * inch))
                figures_written += 1
            except Exception as exc:
                figure_block.append(Paragraph(f"Figure could not be embedded: {escape(str(exc))}", styles["SmallText"]))
        else:
            note = str(figure.availability_note or "Not available")
            figure_block.append(Paragraph(escape(note), styles["SmallText"]))
        if figure.source_filename:
            figure_block.append(Paragraph(f"Source: {escape(str(figure.source_filename))}", styles["Caption"]))
        if figure.caption:
            figure_block.append(Paragraph(escape(str(figure.caption)), styles["Caption"]))
        figure_block.append(Spacer(1, 0.1 * inch))
        story.append(KeepTogether(figure_block))

    story.append(PageBreak())
    _emit(progress_cb, 80, "Adding appendix...")
    _section(story, rl, styles, "Full FITS Header Appendix")
    header_text = str(report.full_header or "").strip()
    if header_text:
        story.append(
            Preformatted(
                header_text[:18000],
                styles["SmallText"],
                maxLineLength=110,
                newLineChars="\n",
            )
        )
        if len(header_text) > 18000:
            story.append(Paragraph("Header appendix was truncated for PDF readability.", styles["Caption"]))
    else:
        story.append(Paragraph("Not available", styles["Normal"]))

    _emit(progress_cb, 92, "Writing PDF...")
    on_page = _draw_header_footer(title, rl)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    _emit(progress_cb, 100, "Project report complete.")

    return ProjectReportResult(
        path=str(path),
        file_size=int(path.stat().st_size) if path.exists() else 0,
        figures_written=int(figures_written),
    )
