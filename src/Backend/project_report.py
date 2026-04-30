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
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure

from src.Backend.frequency_axis import finite_data_limits, masked_display_data, matplotlib_extent


ProgressCallback = Callable[[int, str], None]


class ReportGenerationCancelled(RuntimeError):
    pass


@dataclass
class ProjectReportFigure:
    title: str
    image_png: bytes | None = None
    caption: str = ""
    image_path: str = ""


@dataclass
class ProjectReportInput:
    title: str
    app: Mapping[str, Any] = field(default_factory=dict)
    data_source: Mapping[str, Any] = field(default_factory=dict)
    processing: Mapping[str, Any] = field(default_factory=dict)
    rfi: Mapping[str, Any] = field(default_factory=dict)
    annotations: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    light_curve: Mapping[str, Any] = field(default_factory=dict)
    operation_log: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
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


def _as_float_array(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    try:
        arr = np.asarray(value, dtype=float)
    except Exception:
        return None
    if arr.ndim == 0:
        return None
    return arr.reshape(-1)


def _finite_min_max(value: Any) -> tuple[float, float] | None:
    try:
        arr = np.asarray(value, dtype=float)
    except Exception:
        return None
    if arr.size == 0:
        return None
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None
    return float(np.nanmin(finite)), float(np.nanmax(finite))


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


def _style_axis(ax) -> None:
    ax.grid(True, color="#d8dde6", linewidth=0.6, alpha=0.8)
    ax.set_facecolor("#ffffff")
    for spine in ax.spines.values():
        spine.set_color("#2f3742")
        spine.set_linewidth(0.8)
    ax.tick_params(colors="#1f2933", labelsize=9)
    ax.title.set_color("#111827")
    ax.xaxis.label.set_color("#111827")
    ax.yaxis.label.set_color("#111827")


def figure_to_png_bytes(fig: Figure, *, dpi: int = 180) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    return buf.getvalue()


def _resolve_cmap(cmap: Any):
    if callable(cmap):
        return cmap
    name = str(cmap or "viridis").strip()
    if not name or name.lower() == "custom":
        return LinearSegmentedColormap.from_list(
            "callisto_report_custom",
            [(0.0, "blue"), (0.5, "red"), (1.0, "yellow")],
        )
    try:
        return colormaps[name]
    except Exception:
        return colormaps["viridis"]


def build_dynamic_spectrum_figure(
    *,
    data: Any,
    freqs: Any,
    time: Any,
    title: str,
    unit_label: str,
    cmap: Any = "viridis",
    frequency_step_mhz: float | None = None,
) -> ProjectReportFigure | None:
    try:
        arr = np.asarray(data, dtype=float)
    except Exception:
        return None
    freq_arr = _as_float_array(freqs)
    time_arr = _as_float_array(time)
    if arr.ndim != 2 or freq_arr is None or time_arr is None or len(freq_arr) == 0 or len(time_arr) == 0:
        return None

    fig = Figure(figsize=(7.2, 4.2), dpi=150)
    ax = fig.add_subplot(111)
    im = ax.imshow(
        masked_display_data(arr),
        aspect="auto",
        extent=matplotlib_extent(freq_arr, time_arr, default_step=frequency_step_mhz),
        cmap=_resolve_cmap(cmap),
    )
    vmin, vmax = finite_data_limits(arr)
    if vmin is not None and vmax is not None:
        im.set_clim(vmin, vmax)
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.035)
    cbar.set_label(f"Intensity [{unit_label}]")
    ax.set_title(str(title or "Dynamic Spectrum"))
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Frequency [MHz]")
    _style_axis(ax)
    fig.tight_layout()
    return ProjectReportFigure(title=str(title or "Dynamic Spectrum"), image_png=figure_to_png_bytes(fig))


def build_light_curve_figure(
    *,
    data: Any,
    freqs: Any,
    time: Any,
    records: Sequence[Mapping[str, Any]] | None,
    title: str = "Light Curves",
    unit_label: str = "Digits",
) -> ProjectReportFigure | None:
    try:
        arr = np.asarray(data, dtype=float)
    except Exception:
        return None
    freq_arr = _as_float_array(freqs)
    time_arr = _as_float_array(time)
    if arr.ndim != 2 or freq_arr is None or time_arr is None or not records:
        return None
    if arr.shape[0] != len(freq_arr) or arr.shape[1] == 0:
        return None

    fig = Figure(figsize=(7.2, 3.7), dpi=150)
    ax = fig.add_subplot(111)
    plotted = 0
    n = min(arr.shape[1], len(time_arr))
    for record in records:
        item = _as_mapping(record)
        requested = item.get("requested_mhz", item.get("frequency_mhz"))
        try:
            requested_f = float(requested)
        except Exception:
            continue
        finite = np.isfinite(freq_arr)
        if not np.any(finite):
            continue
        finite_indices = np.flatnonzero(finite)
        idx = int(finite_indices[int(np.argmin(np.abs(freq_arr[finite] - requested_f)))])
        series = np.asarray(arr[idx, :n], dtype=float)
        times = np.asarray(time_arr[:n], dtype=float)
        mask = np.isfinite(times) & np.isfinite(series)
        if not np.any(mask):
            continue
        ax.plot(times[mask], series[mask], linewidth=1.4, label=f"{freq_arr[idx]:.3f} MHz")
        plotted += 1

    if plotted == 0:
        return None
    ax.set_title(title)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel(f"Intensity [{unit_label}]")
    ax.legend(loc="best", fontsize=8)
    _style_axis(ax)
    fig.tight_layout()
    return ProjectReportFigure(title=title, image_png=figure_to_png_bytes(fig))


def build_max_intensity_figure(session: Mapping[str, Any] | None) -> ProjectReportFigure | None:
    sess = _as_mapping(session)
    max_block = _as_mapping(sess.get("max_intensity"))
    times = _as_float_array(max_block.get("time_seconds"))
    if times is None:
        times = _as_float_array(max_block.get("time_channels"))
    freqs = _as_float_array(max_block.get("freqs"))
    if times is None or freqs is None:
        return None
    n = min(len(times), len(freqs))
    if n == 0:
        return None
    times = times[:n]
    freqs = freqs[:n]
    mask = np.isfinite(times) & np.isfinite(freqs)
    if not np.any(mask):
        return None

    fig = Figure(figsize=(6.8, 3.7), dpi=150)
    ax = fig.add_subplot(111)
    ax.scatter(times[mask], freqs[mask], s=12, color="#1d4ed8", alpha=0.9)
    ax.set_title("Maximum Intensity Trace")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Frequency [MHz]")
    _style_axis(ax)
    fig.tight_layout()
    return ProjectReportFigure(
        title="Maximum Intensity Trace",
        image_png=figure_to_png_bytes(fig),
        caption=f"{int(np.count_nonzero(mask))} valid points.",
    )


def build_analyzer_fit_figure(session: Mapping[str, Any] | None) -> ProjectReportFigure | None:
    sess = _as_mapping(session)
    max_block = _as_mapping(sess.get("max_intensity"))
    analyzer = _as_mapping(sess.get("analyzer"))
    fit = _as_mapping(analyzer.get("fit_params"))
    if fit.get("a") is None or fit.get("b") is None:
        return None
    times = _as_float_array(max_block.get("time_seconds"))
    if times is None:
        times = _as_float_array(max_block.get("time_channels"))
    freqs = _as_float_array(max_block.get("freqs"))
    if times is None or freqs is None:
        return None
    n = min(len(times), len(freqs))
    if n == 0:
        return None
    times = times[:n]
    freqs = freqs[:n]
    try:
        a = float(fit.get("a"))
        b = abs(float(fit.get("b")))
    except Exception:
        return None
    mask = np.isfinite(times) & np.isfinite(freqs) & (times > 0.0) & (freqs > 0.0)
    if np.count_nonzero(mask) < 2:
        return None

    x_fit = np.linspace(float(np.nanmin(times[mask])), float(np.nanmax(times[mask])), 300)
    y_fit = a * np.power(x_fit, -b)
    fig = Figure(figsize=(6.8, 3.7), dpi=150)
    ax = fig.add_subplot(111)
    ax.scatter(times, freqs, s=12, color="#1d4ed8", label="Maximum intensity")
    ax.plot(x_fit, y_fit, color="#dc2626", linewidth=1.8, label=f"f = {a:.3g} * x^-{b:.3g}")
    ax.set_title("Analyzer Best Fit")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Frequency [MHz]")
    ax.legend(loc="best", fontsize=8)
    _style_axis(ax)
    fig.tight_layout()
    caption_parts = []
    if fit.get("r2") is not None:
        caption_parts.append(f"R2: {_fmt_number(fit.get('r2'), 4)}")
    if fit.get("rmse") is not None:
        caption_parts.append(f"RMSE: {_fmt_number(fit.get('rmse'), 4)}")
    return ProjectReportFigure(
        title="Analyzer Best Fit",
        image_png=figure_to_png_bytes(fig),
        caption=", ".join(caption_parts),
    )


def build_type_ii_figure(session: Mapping[str, Any] | None) -> ProjectReportFigure | None:
    sess = _as_mapping(session)
    type_ii = _as_mapping(sess.get("type_ii") if "type_ii" in sess else sess)
    upper = _as_mapping(type_ii.get("upper"))
    lower = _as_mapping(type_ii.get("lower"))
    upper_t = _as_float_array(upper.get("time_seconds"))
    upper_f = _as_float_array(upper.get("freqs"))
    lower_t = _as_float_array(lower.get("time_seconds"))
    lower_f = _as_float_array(lower.get("freqs"))
    has_upper = upper_t is not None and upper_f is not None and min(len(upper_t), len(upper_f)) > 0
    has_lower = lower_t is not None and lower_f is not None and min(len(lower_t), len(lower_f)) > 0
    if not has_upper and not has_lower:
        return None

    fig = Figure(figsize=(6.8, 3.9), dpi=150)
    ax = fig.add_subplot(111)

    def _plot_band(times, freqs, fit, color, label):
        n = min(len(times), len(freqs))
        t = np.asarray(times[:n], dtype=float)
        f = np.asarray(freqs[:n], dtype=float)
        mask = np.isfinite(t) & np.isfinite(f)
        if not np.any(mask):
            return 0
        ax.scatter(t[mask], f[mask], s=18, color=color, label=label)
        fit_map = _as_mapping(fit)
        if fit_map.get("a") is not None and fit_map.get("b") is not None and np.count_nonzero(mask) >= 2:
            try:
                a = float(fit_map["a"])
                b = abs(float(fit_map["b"]))
                x_min = max(float(np.nanmin(t[mask])), 1e-9)
                x_max = float(np.nanmax(t[mask]))
                if x_max > x_min:
                    xs = np.linspace(x_min, x_max, 180)
                    ax.plot(xs, a * np.power(xs, -b), color=color, linewidth=1.5, alpha=0.85)
            except Exception:
                pass
        return int(np.count_nonzero(mask))

    count = 0
    if has_upper:
        count += _plot_band(upper_t, upper_f, type_ii.get("upper_fit"), "#ea580c", "Upper band")
    if has_lower:
        count += _plot_band(lower_t, lower_f, type_ii.get("lower_fit"), "#0284c7", "Lower band")
    if count == 0:
        return None
    ax.set_title("Type II Band Splitting")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Frequency [MHz]")
    ax.legend(loc="best", fontsize=8)
    _style_axis(ax)
    fig.tight_layout()
    return ProjectReportFigure(
        title="Type II Band Splitting",
        image_png=figure_to_png_bytes(fig),
        caption=f"{count} band points.",
    )


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


def _operation_rows(log: Sequence[Mapping[str, Any]], limit: int = 30) -> list[tuple[str, Any]]:
    rows = []
    for item in list(log or [])[-limit:]:
        entry = _as_mapping(item)
        rows.append((str(entry.get("ts") or ""), str(entry.get("msg") or "")))
    return rows


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
    valid_figures = [fig for fig in list(report.figures or []) if fig and (fig.image_png or fig.image_path)]
    if not valid_figures:
        _section(story, rl, styles, "Figures")
        story.append(Paragraph("Not available", styles["Normal"]))
    for idx, figure in enumerate(valid_figures, start=1):
        _emit(progress_cb, 40 + int(35 * idx / max(1, len(valid_figures))), f"Adding figure: {figure.title}")
        figure_block = []
        if idx == 1:
            figure_block.append(Paragraph("Figures", styles["Heading2"]))
        figure_block.append(Paragraph(escape(str(figure.title or f"Figure {idx}")), styles["Heading3"]))
        image_bytes = figure.image_png
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
        if figure.caption:
            figure_block.append(Paragraph(escape(str(figure.caption)), styles["Caption"]))
        figure_block.append(Spacer(1, 0.1 * inch))
        story.append(KeepTogether(figure_block))

    story.append(PageBreak())
    _emit(progress_cb, 80, "Adding logs and appendix...")
    _section(story, rl, styles, "Operation Log")
    op_pairs = _operation_rows(report.operation_log)
    story.append(_pair_table(rl, styles, op_pairs, doc.width))
    if len(list(report.operation_log or [])) > len(op_pairs):
        story.append(Paragraph("Showing the most recent 30 operation log entries.", styles["Caption"]))
    story.append(Spacer(1, 0.12 * inch))

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
