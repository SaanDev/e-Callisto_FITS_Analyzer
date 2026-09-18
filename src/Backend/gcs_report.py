"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

PDF report of a GCS fitting session (src/Backend/gcs_report.py).

The report is the fitting window's state written down for a reader who was not
there: the viewpoints and their geometry, the model on screen, every recorded
fit with the images it was made from, the height–time kinematics of each model,
and what the numbers can and cannot claim. Graphs are drawn in light mode in
the OriginPro style of ``src.Backend.figure_export``; the viewpoint images come
in as PNG bytes, because only the window can step its panels to a recorded time.

Layout goes through ReportLab, as the analyzer's project report does
(``src.Backend.project_report``), and reuses its page styles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.Backend.project_report import (
    ProjectReportResult,
    _draw_header_footer,
    _fit_image,
    _import_reportlab,
    _make_styles,
    _pair_table,
)

ProgressCallback = Callable[[int, str], None]

REPORT_TITLE = "GCS CME Fitting Report"


# --- Blocks -------------------------------------------------------------------


@dataclass(frozen=True)
class Heading:
    text: str
    level: int = 2


@dataclass(frozen=True)
class Text:
    text: str
    small: bool = False


@dataclass(frozen=True)
class KeyValues:
    pairs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    #: Relative column widths; equal when omitted.
    widths: tuple[float, ...] | None = None


@dataclass(frozen=True)
class Figure:
    title: str
    png: bytes | None
    caption: str = ""
    #: Shown instead of the image when there is none.
    note: str = ""
    max_height_in: float = 4.4


@dataclass(frozen=True)
class PageBreak:
    pass


Block = Heading | Text | KeyValues | Table | Figure | PageBreak


def write_report_pdf(
    output_path: str | Path,
    *,
    title: str,
    subtitle_lines: Sequence[str],
    blocks: Sequence[Block],
    progress_cb: ProgressCallback | None = None,
    author: str = "e-CALLISTO FITS Analyzer",
) -> ProjectReportResult:
    """Lay ``blocks`` out on A4 pages behind a title block and write the PDF."""
    path = Path(output_path).expanduser()
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)

    rl = _import_reportlab()
    styles = _make_styles(rl)
    colors = rl["colors"]
    inch = rl["inch"]
    Paragraph = rl["Paragraph"]
    Spacer = rl["Spacer"]
    doc = rl["SimpleDocTemplate"](
        str(path),
        pagesize=rl["A4"],
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.72 * inch,
        title=title,
        author=author,
    )

    story: list[Any] = [
        Spacer(1, 0.35 * inch),
        Paragraph(escape(title), styles["ReportTitle"]),
        Paragraph("<br/>".join(escape(line) for line in subtitle_lines), styles["ReportSubtitle"]),
        Spacer(1, 0.15 * inch),
    ]
    figures = 0
    total = max(1, len(blocks))
    for index, block in enumerate(blocks):
        if progress_cb is not None and index % 5 == 0:
            progress_cb(int(90 * index / total), "Laying out the report…")
        if isinstance(block, Heading):
            style = styles["Heading2"] if block.level <= 2 else styles["Heading3"]
            story.append(Paragraph(escape(block.text), style))
        elif isinstance(block, Text):
            story.append(Paragraph(escape(block.text).replace("\n", "<br/>"), styles["SmallText" if block.small else "Normal"]))
            story.append(Spacer(1, 0.06 * inch))
        elif isinstance(block, KeyValues):
            story.append(_pair_table(rl, styles, list(block.pairs), doc.width))
            story.append(Spacer(1, 0.12 * inch))
        elif isinstance(block, Table):
            story.append(_data_table(rl, styles, block, doc.width, colors))
            story.append(Spacer(1, 0.12 * inch))
        elif isinstance(block, Figure):
            parts: list[Any] = [Paragraph(escape(block.title), styles["Heading3"])]
            if block.png:
                parts.append(_fit_image(rl, block.png, doc.width, block.max_height_in * inch))
                figures += 1
            else:
                parts.append(Paragraph(escape(block.note or "Not available."), styles["SmallText"]))
            if block.caption:
                parts.append(Paragraph(escape(block.caption).replace("\n", "<br/>"), styles["Caption"]))
            parts.append(Spacer(1, 0.08 * inch))
            story.append(rl["KeepTogether"](parts))
        elif isinstance(block, PageBreak):
            story.append(rl["PageBreak"]())
    if progress_cb is not None:
        progress_cb(92, "Writing the PDF…")
    on_page = _draw_header_footer(title, rl)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    if progress_cb is not None:
        progress_cb(100, "Report written.")
    return ProjectReportResult(
        path=str(path),
        file_size=int(path.stat().st_size) if path.exists() else 0,
        figures_written=figures,
    )


def _data_table(rl, styles, block: Table, width: float, colors) -> Any:
    Paragraph = rl["Paragraph"]
    header = [Paragraph(f"<b>{escape(text)}</b>", styles["SmallText"]) for text in block.header]
    rows = [[Paragraph(escape(str(cell)), styles["SmallText"]) for cell in row] for row in block.rows]
    if not rows:
        rows = [[Paragraph("None recorded.", styles["SmallText"])] + [""] * (len(header) - 1)]
    weights = list(block.widths or [1.0] * len(header))
    scale = width / float(sum(weights))
    table = rl["Table"]([header] + rows, colWidths=[weight * scale for weight in weights], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        rl["TableStyle"](
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7dce3")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


# --- Content ------------------------------------------------------------------


@dataclass
class ViewpointInfo:
    """One panel of the fitting window, as the report describes it."""

    label: str
    channel: str
    frames: int = 0
    first_utc: datetime | None = None
    last_utc: datetime | None = None
    #: The observer of the image on screen (``gcs_model.ObserverGeometry``), if any.
    observer: Any | None = None


@dataclass
class GCSReportInput:
    """Everything the report states, gathered from the fitting window."""

    generated_at: datetime
    app_name: str
    app_version: str
    event_range: tuple[datetime, datetime]
    shared_time: datetime | None
    max_time_offset_s: float
    view_mode: str
    frame_cap: int
    fit_order: int
    viewpoints: list[ViewpointInfo]
    #: ``(first label, second label, degrees)`` for each pair of loaded views.
    separations: list[tuple[str, str, float]]
    gcs: Any
    shock: Any | None = None
    gcs_refinement: Any | None = None
    shock_refinement: Any | None = None
    #: ``_current_provenance`` of the edited model: the images on screen.
    current_observations: Mapping[str, Any] = field(default_factory=dict)
    current_figure: bytes | None = None
    current_note: str = ""
    gcs_fits: Mapping[datetime, Any] = field(default_factory=dict)
    gcs_provenance: Mapping[datetime, Any] = field(default_factory=dict)
    shock_fits: Mapping[datetime, Any] = field(default_factory=dict)
    shock_provenance: Mapping[datetime, Any] = field(default_factory=dict)
    archived_gcs: Mapping[datetime, Any] = field(default_factory=dict)
    archived_shock: Mapping[datetime, Any] = field(default_factory=dict)
    #: Viewpoints image for each recorded time; ``None`` where it could not be drawn.
    fit_figures: Mapping[datetime, bytes | None] = field(default_factory=dict)
    fit_figure_notes: Mapping[datetime, str] = field(default_factory=dict)


def _num(value: Any, spec: str = ".2f", unit: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return "—"
    return f"{format(number, spec)}{unit}"


def _num_err(value: Any, error: Any, spec: str = ".2f", unit: str = "") -> str:
    text = _num(value, spec)
    if text == "—":
        return text
    try:
        sigma = float(error)
    except (TypeError, ValueError):
        sigma = math.nan
    if math.isfinite(sigma):
        text += f" ± {format(abs(sigma), spec.replace('+', ''))}"
    return text + unit


def _utc(value: Any, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if isinstance(value, datetime):
        return value.strftime(fmt)
    if isinstance(value, str) and value:
        return value.replace("T", " ").rstrip("Z")
    return "—"


def _matching_refinement(refinement: Any | None, params: Any) -> Any | None:
    """The refinement, only while it still describes ``params`` (errors go stale otherwise)."""
    if refinement is None or not getattr(refinement, "converged", False):
        return None
    return refinement if getattr(refinement, "parameters", None) == params else None


def _sigma(refinement: Any | None, name: str) -> float:
    if refinement is None:
        return math.nan
    return float(dict(getattr(refinement, "sigma", {}) or {}).get(name, math.nan))


def gcs_parameter_pairs(params: Any, refinement: Any | None = None) -> list[tuple[str, str]]:
    """The six GCS parameters, their formal errors when valid, and the derived geometry."""
    from src.Backend.gcs_model import (
        angular_widths_deg,
        apex_cross_section_radius_rsun,
        leg_height_rsun,
        shell_centre_distance_rsun,
    )

    refined = _matching_refinement(refinement, params)
    pairs = [
        ("Longitude (Stonyhurst)", _num_err(params.lon_deg, _sigma(refined, "lon_deg"), "+.2f", "°")),
        ("Latitude (Stonyhurst)", _num_err(params.lat_deg, _sigma(refined, "lat_deg"), "+.2f", "°")),
        ("Tilt", _num_err(params.tilt_deg, _sigma(refined, "tilt_deg"), "+.2f", "°")),
        ("Apex height (from Sun centre)", _num_err(params.height_rsun, _sigma(refined, "height_rsun"), ".3f", " R☉")),
        ("Half angle α", _num_err(params.alpha_deg, _sigma(refined, "alpha_deg"), ".2f", "°")),
        ("Aspect ratio κ", _num_err(params.kappa, _sigma(refined, "kappa"), ".3f")),
    ]
    try:
        face_on, edge_on = angular_widths_deg(params)
        pairs += [
            ("Leg height h", _num(leg_height_rsun(params), ".3f", " R☉")),
            ("Apex cross-section radius", _num(apex_cross_section_radius_rsun(params), ".3f", " R☉")),
            ("Shell centre distance", _num(shell_centre_distance_rsun(params), ".3f", " R☉")),
            ("Face-on / edge-on width", f"{_num(face_on, '.1f', '°')} / {_num(edge_on, '.1f', '°')}"),
        ]
    except Exception:
        pass
    pairs.append(("Errors", _refinement_note(refined)))
    return pairs


def shock_parameter_pairs(params: Any, refinement: Any | None = None) -> list[tuple[str, str]]:
    """The shock in PyThea's parameters, with the centre and semi-axes they imply."""
    from src.Backend.shock_model import ELLIPSOID, SHOCK_MODEL_LABELS, shock_semi_axes

    refined = _matching_refinement(refinement, params)
    pairs = [
        ("Shape", SHOCK_MODEL_LABELS.get(params.model, str(params.model))),
        ("Longitude (Stonyhurst)", _num_err(params.lon_deg, _sigma(refined, "lon_deg"), "+.2f", "°")),
        ("Latitude (Stonyhurst)", _num_err(params.lat_deg, _sigma(refined, "lat_deg"), "+.2f", "°")),
        ("Apex height (from Sun centre)", _num_err(params.height_rsun, _sigma(refined, "height_rsun"), ".3f", " R☉")),
        ("κ = b / (height − 1 R☉)", _num_err(params.kappa, _sigma(refined, "kappa"), ".3f")),
        ("Eccentricity ε", _num_err(params.epsilon, _sigma(refined, "epsilon"), "+.3f")),
    ]
    if params.model == ELLIPSOID:
        pairs += [
            ("α = b / c", _num_err(params.alpha, _sigma(refined, "alpha"), ".3f")),
            ("Tilt about the radial axis", _num_err(params.tilt_deg, _sigma(refined, "tilt_deg"), "+.2f", "°")),
        ]
    try:
        axes = shock_semi_axes(params)
        lateral = f"b {axes.orthoaxis1_rsun:.3f}, c {axes.orthoaxis2_rsun:.3f}"
        pairs += [
            ("Centre distance (rcenter)", _num(axes.rcenter_rsun, ".3f", " R☉")),
            ("Semi-axes (radaxis a; orthoaxes b, c)", f"a {axes.radaxis_rsun:.3f}, {lateral} R☉"),
        ]
    except Exception:
        pass
    pairs.append(("Errors", _refinement_note(refined)))
    return pairs


def _refinement_note(refined: Any | None) -> str:
    if refined is None:
        return "No valid refinement for these values; no formal errors."
    note = (
        f"Formal 1σ from the last refinement: rms {_num(refined.rms_arcsec, '.1f', '″')} over "
        f"{int(refined.n_points)} point(s) in {int(refined.n_viewpoints)} view(s)."
    )
    weak = tuple(getattr(refined, "weakly_constrained", ()) or ())
    if weak:
        note += " Weakly constrained: " + ", ".join(weak) + "."
    return note


def _observation_table(provenance: Mapping[str, Any] | None) -> Table:
    rows = []
    for frame in (provenance or {}).get("frames", ()) or ():
        offset = frame.get("offset_seconds")
        rows.append(
            (
                str(frame.get("panel", "")),
                str(frame.get("instrument") or frame.get("source_key") or "—"),
                _utc(frame.get("observation_time_utc")),
                _num(offset, "+.0f") if offset is not None else "—",
                "yes" if frame.get("included_in_fit") else "no",
                {"raw": "Raw", "running": "Running diff.", "base": "Base diff."}.get(frame.get("display_mode"), "—"),
                _utc(frame.get("difference_reference_utc")),
                str(len(frame.get("front_points_arcsec") or ())),
            )
        )
    return Table(
        ("Panel", "Instrument", "Observation (UTC)", "Δt (s)", "In fit", "View", "Reference (UTC)", "Points"),
        tuple(rows),
        (0.6, 1.5, 1.9, 0.7, 0.6, 1.1, 1.9, 0.7),
    )


def _fit_row(model: str, entry: Any) -> tuple[str, ...]:
    from src.Backend.shock_model import SHOCK_MODEL_LABELS

    if model == "gcs":
        shape = f"α {float(entry.alpha_deg):.1f}°, κ {float(entry.kappa):.3f}"
        name = "GCS"
    else:
        shape = f"{SHOCK_MODEL_LABELS.get(entry.model, entry.model)}, κ {float(entry.kappa):.3f}, ε {float(entry.epsilon):+.3f}"
        if entry.model == "ellipsoid":
            shape += f", b/c {float(entry.alpha):.3f}"
        name = "Shock"
    return (
        name,
        _num_err(entry.apex_height_rsun, entry.height_err_rsun, ".3f"),
        _num_err(entry.lon_deg, entry.lon_err_deg, "+.1f"),
        _num_err(entry.lat_deg, entry.lat_err_deg, "+.1f"),
        _num(entry.tilt_deg, "+.1f"),
        shape,
        _num(entry.rms_arcsec, ".1f"),
        str(int(entry.n_points)),
        str(int(entry.n_viewpoints)),
        _num(entry.separation_deg, ".0f"),
        "yes" if bool(entry.refined) else "no",
    )


_FIT_HEADER = ("Model", "Apex (R☉)", "Lon (°)", "Lat (°)", "Tilt (°)", "Shape", "RMS (″)", "Pts", "Views", "Sep. (°)", "Refined")
_FIT_WIDTHS = (0.7, 1.2, 1.0, 1.0, 0.7, 2.3, 0.7, 0.5, 0.55, 0.65, 0.7)


def _series_table(fits: Mapping[datetime, Any]) -> Table:
    times = sorted(fits)
    rows = []
    for when in times:
        entry = fits[when]
        rows.append(
            (
                _utc(when),
                _num((when - times[0]).total_seconds() / 60.0, ".1f"),
                _num(entry.apex_height_rsun, ".3f"),
                _num(entry.height_err_rsun, ".3f"),
                _num(entry.lon_deg, "+.1f"),
                _num(entry.lat_deg, "+.1f"),
                "yes" if bool(entry.refined) else "no",
            )
        )
    return Table(
        ("Time (UTC)", "t (min)", "Apex height (R☉)", "σ (R☉)", "Lon (°)", "Lat (°)", "Refined"),
        tuple(rows),
        (2.0, 0.8, 1.3, 0.9, 0.9, 0.9, 0.8),
    )


def model_series(label: str, fits: Mapping[datetime, Any], order: int) -> Any:
    """A model's recorded apex heights as a height–time series for the graphs."""
    from src.Backend.gcs_figures import HeightTimeSeries

    times = sorted(fits)
    return HeightTimeSeries.build(
        label,
        times,
        [float(fits[when].apex_height_rsun) for when in times],
        [float(fits[when].height_err_rsun) for when in times],
        order=order,
    )


def _kinematics_blocks(title: str, label: str, fits: Mapping[datetime, Any], order: int) -> list[Block]:
    from src.Backend.figure_export import figure_png_bytes
    from src.Backend.gcs_figures import FIT_ORDER_NAMES, fit_series, height_time_figure, kinematics_rows

    blocks: list[Block] = [Heading(title, 3), _series_table(fits)]
    series = model_series(label, fits, order)
    fit = fit_series(series)
    graph = figure_png_bytes(height_time_figure([series], y_label="Apex height (R☉)"), dpi=200)
    caption = (
        f"Recorded apex heights (de-projected under the model) with formal 1σ errors, and the "
        f"{FIT_ORDER_NAMES.get(order, str(order)).lower()} height–time fit."
    )
    if fit is None:
        caption += f" A {FIT_ORDER_NAMES.get(order, str(order)).lower()} fit needs {max(2, order + 1)} distinct recorded times."
    blocks.append(Figure(f"{label}: height–time", graph, caption))
    if fit is not None:
        blocks.append(KeyValues(tuple(kinematics_rows(fit))))
    return blocks


def build_report_blocks(data: GCSReportInput) -> list[Block]:
    """The report's content, in reading order."""
    from src.Backend.figure_export import figure_png_bytes
    from src.Backend.gcs_figures import FIT_ORDER_NAMES, height_time_figure

    start, end = data.event_range
    blocks: list[Block] = []
    channels = " · ".join(f"{view.label}: {view.channel}" for view in data.viewpoints)
    blocks.append(
        KeyValues(
            (
                ("Event range (UTC)", f"{_utc(start, '%Y-%m-%d %H:%M')} → {_utc(end, '%Y-%m-%d %H:%M')}"),
                ("Time shown at export (UTC)", _utc(data.shared_time)),
                ("Viewpoints", channels or "—"),
                ("Max time offset for fitting", _num(data.max_time_offset_s / 60.0, ".1f", " min")),
                ("Image view", data.view_mode),
                ("Frames per channel (cap)", str(int(data.frame_cap))),
                ("Recorded GCS fits", str(len(data.gcs_fits))),
                ("Recorded shock fits", str(len(data.shock_fits))),
                ("Height–time fit", f"{FIT_ORDER_NAMES.get(data.fit_order, str(data.fit_order))} (degree {data.fit_order})"),
            )
        )
    )
    blocks.append(
        Text(
            "Images are Helioviewer JPEG2000 display products: suitable for fitting the shape of a CME front, "
            "not for calibrated intensity. Heights are measured from Sun centre and de-projected under the model "
            "assumptions; errors are formal. See the last section for what they leave out.",
            small=True,
        )
    )

    # Viewpoints.
    blocks.append(Heading("Viewpoints"))
    rows = []
    for view in data.viewpoints:
        observer = view.observer
        loaded = (
            f"{_utc(view.first_utc, '%m-%d %H:%M')} → {_utc(view.last_utc, '%m-%d %H:%M')}"
            if view.first_utc is not None
            else "—"
        )
        rows.append(
            (
                view.label,
                view.channel,
                str(view.frames),
                loaded,
                f"{_num(observer.lon_deg, '+.1f')}, {_num(observer.lat_deg, '+.1f')}" if observer else "—",
                _num(observer.dsun_rsun / 215.032, ".3f") if observer else "—",
                _num(observer.rsun_arcsec, ".0f") if observer else "—",
            )
        )
    blocks.append(
        Table(
            ("Panel", "Channel", "Frames", "Loaded (UTC)", "Observer lon, lat (°)", "Distance (AU)", "R☉ (″)"),
            tuple(rows),
            (0.6, 1.6, 0.7, 1.9, 1.5, 1.0, 0.8),
        )
    )
    if data.separations:
        text = "Separations between the observers of the images shown: " + ", ".join(
            f"{a}–{b} {angle:.1f}°" for a, b, angle in data.separations
        ) + "."
        if not any(20.0 <= angle <= 160.0 for _a, _b, angle in data.separations):
            text += " No pair lies between 20° and 160°, so the views cannot constrain the propagation direction."
        blocks.append(Text(text, small=True))

    # The model on screen.
    blocks.append(PageBreak())
    blocks.append(Heading("Model at the time shown"))
    blocks.append(
        Figure(
            f"Viewpoints at {_utc(data.shared_time)} UTC",
            data.current_figure,
            data.current_note,
            note="No images were loaded.",
        )
    )
    blocks.append(Heading("GCS flux rope", 3))
    blocks.append(KeyValues(tuple(gcs_parameter_pairs(data.gcs, data.gcs_refinement))))
    if data.shock is not None:
        blocks.append(Heading("Shock", 3))
        blocks.append(KeyValues(tuple(shock_parameter_pairs(data.shock, data.shock_refinement))))
    blocks.append(Heading("Images shown", 3))
    blocks.append(_observation_table(data.current_observations))

    # Recorded fits, one block per distinct time.
    times = sorted(set(data.gcs_fits) | set(data.shock_fits))
    if times:
        blocks.append(PageBreak())
        blocks.append(Heading("Recorded fits"))
        blocks.append(
            Text(
                "Each recorded fit, drawn on the images it was made from, with the observation details "
                "stored when it was recorded.",
                small=True,
            )
        )
        for when in times:
            blocks.append(Heading(f"Fit at {_utc(when)} UTC", 3))
            blocks.append(
                Figure(
                    f"Viewpoints at {_utc(when)} UTC",
                    data.fit_figures.get(when),
                    note=data.fit_figure_notes.get(when, "The images of this fit are no longer loaded."),
                )
            )
            rows = []
            if when in data.gcs_fits:
                rows.append(_fit_row("gcs", data.gcs_fits[when]))
            if when in data.shock_fits:
                rows.append(_fit_row("shock", data.shock_fits[when]))
            blocks.append(Table(_FIT_HEADER, tuple(rows), _FIT_WIDTHS))
            provenance = data.gcs_provenance.get(when) or data.shock_provenance.get(when)
            if provenance:
                blocks.append(_observation_table(provenance))

    # Kinematics.
    blocks.append(PageBreak())
    blocks.append(Heading("Height–time kinematics"))
    if not data.gcs_fits and not data.shock_fits:
        blocks.append(Text("No fits were recorded. Commit fits at two or more times for a height–time fit."))
    if data.gcs_fits:
        blocks += _kinematics_blocks("GCS flux rope", "GCS apex", data.gcs_fits, data.fit_order)
    if data.shock_fits:
        blocks += _kinematics_blocks("Shock", "Shock apex", data.shock_fits, data.fit_order)
    if len(data.gcs_fits) >= 2 and len(data.shock_fits) >= 2:
        combined = height_time_figure(
            [
                model_series("GCS apex", data.gcs_fits, data.fit_order),
                model_series("Shock apex", data.shock_fits, data.fit_order),
            ],
            y_label="Apex height (R☉)",
        )
        blocks.append(
            Figure(
                "GCS and shock apex heights",
                figure_png_bytes(combined, dpi=200),
                "The shock apex runs ahead of the flux-rope apex; their separation is the shock stand-off distance "
                "under both models' assumptions.",
            )
        )

    # Fits kept from other events.
    if data.archived_gcs or data.archived_shock:
        blocks.append(Heading("Fits from other events"))
        blocks.append(Text("Recorded while a different event range was loaded; their images are not shown.", small=True))
        rows = [_fit_row("gcs", data.archived_gcs[when]) for when in sorted(data.archived_gcs)]
        rows += [_fit_row("shock", data.archived_shock[when]) for when in sorted(data.archived_shock)]
        times = sorted(data.archived_gcs) + sorted(data.archived_shock)
        blocks.append(
            Table(("Time (UTC)",) + _FIT_HEADER, tuple((_utc(when),) + row for when, row in zip(times, rows)), (1.6,) + _FIT_WIDTHS)
        )

    blocks += method_blocks()
    return blocks


def method_blocks() -> list[Block]:
    """What the model is, and what its numbers do not include."""
    return [
        PageBreak(),
        Heading("Method and limitations"),
        Text(
            "Flux rope. The Graduated Cylindrical Shell (Thernisien et al. 2006; Thernisien 2011) is a hollow "
            "croissant set by six parameters: the Stonyhurst longitude and latitude of its direction of travel, the "
            "tilt of the shell about that direction, the apex height from Sun centre, the half angle α between the "
            "legs and the aspect ratio κ. It describes the ejecta, not the shock."
        ),
        Text(
            "Shock. A spheroid or ellipsoid in the conventions of PyThea (Kouloumvakos et al. 2022): the apex height "
            "h, κ = b / (h − 1 R☉) for the lateral size, the signed eccentricity ε (positive stretches the surface "
            "radially), and for an ellipsoid α = b / c and a tilt about the radial axis. With fewer than three views "
            "ε and the tilt are weakly constrained."
        ),
        Text(
            "Fitting. One model is matched to the same feature in every view inside the time tolerance, then "
            "refined by least squares against the clicked front points. Views closer than about 20°, or nearly "
            "opposite, do not constrain the direction."
        ),
        Text(
            "Uncertainty. The quoted errors are formal: they propagate the scatter of the clicked points and assume "
            "the model is exact. They leave out the choice of front, non-simultaneous images, image processing and "
            "the assumed geometry, and a small residual does not make a fit unique or accurate (Verbeke et al. 2023)."
        ),
        Text(
            "Kinematics. Speeds and accelerations come from a polynomial fit to the recorded apex heights, so they "
            "are model-dependent radial values. Repeated image combinations are never recorded as independent "
            "samples. Between recorded times, movies interpolate the shells for display only."
        ),
        Heading("References", 3),
        Text(
            "Kouloumvakos, A. et al. 2019, ApJ 876, 80 (spheroid shock model), doi:10.3847/1538-4357/ab15d7\n"
            "Kouloumvakos, A. et al. 2022, Front. Astron. Space Sci. 9, 974137 (PyThea), doi:10.3389/fspas.2022.974137\n"
            "Kwon, R.-Y., Zhang, J. & Olmedo, O. 2014, ApJ 794, 148 (ellipsoid model), doi:10.1088/0004-637X/794/2/148\n"
            "Thernisien, A. F. R., Howard, R. A. & Vourlidas, A. 2006, ApJ 652, 763\n"
            "Thernisien, A. 2011, ApJS 194, 33\n"
            "Verbeke, C. et al. 2023, reconstruction uncertainty of 3-D CME parameters, arXiv:2302.00531",
            small=True,
        ),
    ]


def generate_gcs_report_pdf(
    output_path: str | Path,
    data: GCSReportInput,
    *,
    progress_cb: ProgressCallback | None = None,
) -> ProjectReportResult:
    """Write the GCS fitting report for ``data`` to ``output_path``."""
    start, end = data.event_range
    subtitle = [
        f"Event {_utc(start, '%Y-%m-%d %H:%M')} → {_utc(end, '%Y-%m-%d %H:%M')} UTC",
        f"Generated {_utc(data.generated_at)} UTC",
        f"{data.app_name} {data.app_version}",
    ]
    return write_report_pdf(
        output_path,
        title=REPORT_TITLE,
        subtitle_lines=subtitle,
        blocks=build_report_blocks(data),
        progress_cb=progress_cb,
        author=data.app_name,
    )
