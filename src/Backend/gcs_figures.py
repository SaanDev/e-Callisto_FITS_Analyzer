"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Publication renders for GCS fitting (src/Backend/gcs_figures.py).

Two kinds of figure, both drawn with matplotlib from the data rather than
grabbed from the screen:

* The **viewpoints figure**: the fitting window's three images side by side in
  arcsec, with the GCS and shock wireframes, the solar limb and the clicked
  front points. The snapshot, every movie frame and the report's images are
  this figure. A screen grab cannot serve: with hardware acceleration on, the
  window's images are OpenGL surfaces that a widget grab returns blank.
* The **height–time graph**: recorded apex heights with their formal errors and
  the polynomial fit the kinematics card shows, in the OriginPro style of
  ``src.Backend.figure_export``.

Both are light mode whatever the application theme. Nothing here imports Qt.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

import numpy as np

from src.Backend.figure_export import (
    ORIGIN_COLORS,
    ORIGIN_FIT_COLOR,
    ORIGIN_MARKERS,
    origin_legend,
    origin_style,
    style_origin_axes,
)

#: What each polynomial degree is called, as in the kinematics card.
FIT_ORDER_NAMES = {1: "Linear", 2: "Quadratic", 3: "Cubic"}

#: Limb and front-point colours on the images: light enough for dark coronagraph
#: frames, and distinct from the default orange shell and sky-blue shock.
LIMB_COLOR = "#8fe3a0"
POINT_COLOR = "#ffe14d"

#: Caption lines kept above each panel. A fixed budget, not the caption's own
#: length, sets the page layout, so every movie frame has the same size.
CAPTION_LINES = 4
CAPTION_FONT_PT = 7.5


# --- Viewpoints ---------------------------------------------------------------


@dataclass(frozen=True)
class WireframeStyle:
    """How the models are drawn: the fitting window's own colour, width and opacity."""

    gcs_rgb: tuple[int, int, int] = (255, 140, 40)
    shock_rgb: tuple[int, int, int] = (86, 180, 233)
    #: The on-screen pen width in pixels (the Display card's Width slider).
    width_px: float = 2.6
    opacity: float = 1.0

    @property
    def linewidth_pt(self) -> float:
        # A 2.6 px screen pen on a ~500 px panel reads like ~1.2 pt on a
        # 4-inch printed panel: heavy enough to see, fine enough to publish.
        return max(0.4, 0.45 * float(self.width_px))

    def colour(self, rgb: tuple[int, int, int]) -> tuple[float, float, float, float]:
        red, green, blue = (max(0, min(255, int(value))) / 255.0 for value in rgb)
        return red, green, blue, max(0.05, min(1.0, float(self.opacity)))


@dataclass(frozen=True)
class ViewpointRender:
    """One panel of the viewpoints figure, as plain data.

    ``extent`` is ``(x0, x1, y0, y1)`` in arcsec for ``origin="lower"``; without
    one the image is drawn in pixels and carries no overlays, exactly as on screen
    for a frame with no usable solar WCS.
    """

    label: str
    caption: str = ""
    data: np.ndarray | None = None
    extent: tuple[float, float, float, float] | None = None
    #: ``(N, 3)`` or ``(N, 4)`` uint8 colour table the panel draws with.
    lut: np.ndarray | None = None
    vmin: float | None = None
    vmax: float | None = None
    limb_radius_arcsec: float | None = None
    gcs_xy: tuple[np.ndarray, np.ndarray] | None = None
    shock_xy: tuple[np.ndarray, np.ndarray] | None = None
    points: tuple[tuple[float, float], ...] = ()
    #: Whether the view takes part in the fit (inside the time tolerance).
    in_fit: bool = True


def _colormap(lut: np.ndarray | None) -> Any:
    from matplotlib.colors import ListedColormap

    if lut is None or np.asarray(lut).ndim != 2 or len(lut) == 0:
        cmap = ListedColormap(np.linspace(0.0, 1.0, 256)[:, None].repeat(3, axis=1), name="gray")
    else:
        table = np.asarray(lut, dtype=float)[:, :3] / 255.0
        cmap = ListedColormap(table, name="panel")
    cmap = cmap.copy()
    cmap.set_bad("black")
    return cmap


def wrap_caption(text: str, width_in: float) -> str:
    """The panel caption wrapped to the panel's width and cut to :data:`CAPTION_LINES`."""
    # An average Arial glyph is about 0.55 em wide.
    columns = max(20, int(width_in * 72.0 / (0.55 * CAPTION_FONT_PT)))
    lines: list[str] = []
    for line in str(text or "").splitlines():
        lines.extend(textwrap.wrap(line, width=columns) or [""])
    if len(lines) > CAPTION_LINES:
        lines = lines[: CAPTION_LINES - 1] + [lines[CAPTION_LINES - 1].rstrip() + " …"]
    return "\n".join(lines)


def draw_viewpoint(
    ax: Any,
    view: ViewpointRender,
    style: WireframeStyle,
    *,
    label_axes: bool = True,
    caption_width_in: float = 3.4,
) -> None:
    """Draw one panel: image, limb, wireframes and points, with arcsec axes."""
    ax.set_title(
        wrap_caption(view.caption or view.label, caption_width_in),
        loc="left",
        fontsize=CAPTION_FONT_PT,
        linespacing=1.25,
        pad=5,
    )
    if view.data is None:
        ax.set_facecolor("#f2f2f2")
        ax.text(0.5, 0.5, "Not loaded", ha="center", va="center", transform=ax.transAxes, color="#555555")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_aspect("equal", adjustable="box")
        return

    data = np.asarray(view.data, dtype=float)
    vmin = view.vmin if view.vmin is not None and np.isfinite(view.vmin) else None
    vmax = view.vmax if view.vmax is not None and np.isfinite(view.vmax) else None
    ax.imshow(
        data,
        origin="lower",
        extent=view.extent,
        cmap=_colormap(view.lut),
        vmin=vmin,
        vmax=vmax,
        # Smooths when a movie frame shrinks a 1024-pixel image, where nearest
        # neighbour would sparkle; the panels on screen downsample the same way.
        interpolation="antialiased",
    )
    if view.extent is not None:
        x0, x1, y0, y1 = view.extent
        if view.limb_radius_arcsec:
            theta = np.linspace(0.0, 2.0 * math.pi, 361)
            radius = float(view.limb_radius_arcsec)
            # Opaque: PostScript (EPS) cannot draw transparency.
            ax.plot(radius * np.cos(theta), radius * np.sin(theta), color=LIMB_COLOR, lw=0.8)
        linewidth = style.linewidth_pt
        if view.shock_xy is not None:
            ax.plot(*view.shock_xy, color=style.colour(style.shock_rgb), lw=linewidth, solid_capstyle="round")
        if view.gcs_xy is not None:
            ax.plot(*view.gcs_xy, color=style.colour(style.gcs_rgb), lw=linewidth, solid_capstyle="round")
        if view.points:
            px, py = zip(*view.points)
            ax.plot(px, py, linestyle="none", marker="+", ms=7, mew=1.4, color=POINT_COLOR)
        ax.set_xlim(min(x0, x1), max(x0, x1))
        ax.set_ylim(min(y0, y1), max(y0, y1))
        if label_axes:
            ax.set_xlabel("Solar X (arcsec)")
            ax.set_ylabel("Solar Y (arcsec)")
    elif label_axes:
        ax.set_xlabel("x (pixel)")
        ax.set_ylabel("y (pixel)")
    ax.set_aspect("equal", adjustable="box")
    style_origin_axes(ax)
    # Black inward ticks vanish into a dark coronagraph frame; draw the marks
    # white over the image and keep the numbers black on the page.
    ax.tick_params(which="both", color="white", labelcolor="black", labelsize=8)
    ax.xaxis.label.set_size(9)
    ax.yaxis.label.set_size(9)


def viewpoints_figure(
    views: Sequence[ViewpointRender],
    style: WireframeStyle,
    *,
    title: str = "",
    footer: str = "",
    width_in: float = 13.0,
    dpi: float = 100.0,
) -> Any:
    """The three viewpoints side by side on a white page, at a fixed layout.

    The layout never depends on the content, so every frame of a movie has
    exactly the same size and the panels never jump between frames.
    """
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    count = max(1, len(views))
    left_in, right_in, gap = 0.72, 0.15, 0.30
    # Square panels across the page; the margins are fixed in inches.
    panel_in = (width_in - left_in - right_in) / (count + (count - 1) * gap)
    title_in = 0.42 if title else 0.12
    caption_in = CAPTION_LINES * CAPTION_FONT_PT * 1.25 / 72.0 + 0.12
    bottom_in = 1.0
    height_in = panel_in + title_in + caption_in + bottom_in
    left, right = left_in / width_in, 1.0 - right_in / width_in
    with origin_style():
        fig = Figure(figsize=(width_in, height_in), dpi=dpi)
        grid = fig.add_gridspec(
            1,
            count,
            left=left,
            right=right,
            bottom=bottom_in / height_in,
            top=1.0 - (title_in + caption_in) / height_in,
            wspace=gap,
        )
        for index, view in enumerate(views):
            ax = fig.add_subplot(grid[0, index])
            draw_viewpoint(ax, view, style, label_axes=True, caption_width_in=panel_in)
        if title:
            fig.text(0.5, 1.0 - 0.1 / height_in, title, ha="center", va="top", fontsize=11, fontweight="bold")

        handles = []
        if any(view.gcs_xy is not None for view in views):
            handles.append(Line2D([], [], color=style.colour(style.gcs_rgb), lw=2.0, label="GCS flux rope"))
        if any(view.shock_xy is not None for view in views):
            handles.append(Line2D([], [], color=style.colour(style.shock_rgb), lw=2.0, label="Shock"))
        if any(view.limb_radius_arcsec for view in views if view.extent is not None):
            handles.append(Line2D([], [], color=LIMB_COLOR, lw=1.2, label="Solar limb"))
        if any(view.points for view in views):
            handles.append(
                Line2D([], [], color=POINT_COLOR, marker="+", linestyle="none", ms=8, mew=1.5, label="Front points")
            )
        if handles:
            legend = fig.legend(
                handles=handles,
                loc="lower right",
                bbox_to_anchor=(right, 0.06 / height_in),
                ncol=len(handles),
                fontsize=8.5,
                frameon=True,
                fancybox=False,
                edgecolor="#000000",
                framealpha=1.0,
            )
            legend.get_frame().set_linewidth(0.8)
        if footer:
            fig.text(left, 0.1 / height_in, footer, ha="left", va="bottom", fontsize=8, color="#333333", wrap=True)
    return fig


# --- Height–time ----------------------------------------------------------------


@dataclass(frozen=True)
class HeightTimeSeries:
    """One recorded height series and the polynomial degree to fit it with."""

    label: str
    times: tuple[datetime, ...]
    heights_rsun: tuple[float, ...]
    #: 1-sigma errors in R☉; NaN (or ``None`` for all) where none was estimated.
    errors_rsun: tuple[float, ...] | None = None
    order: int = 1

    @classmethod
    def build(
        cls,
        label: str,
        times: Sequence[datetime],
        heights_rsun: Sequence[float],
        errors_rsun: Sequence[float] | None = None,
        *,
        order: int = 1,
    ) -> "HeightTimeSeries":
        rows = sorted(
            zip(times, heights_rsun, errors_rsun if errors_rsun is not None else [math.nan] * len(times)),
            key=lambda row: row[0],
        )
        return cls(
            label=str(label),
            times=tuple(row[0] for row in rows),
            heights_rsun=tuple(float(row[1]) for row in rows),
            errors_rsun=None if errors_rsun is None else tuple(float(row[2]) for row in rows),
            order=int(order),
        )


def fit_series(series: HeightTimeSeries) -> Any | None:
    """The kinematics card's height–time fit of ``series``, or ``None`` when it has none.

    The same call as the card's Fit button (``fit_height_time`` on heights in
    km), so the exported curve and numbers are the ones shown on screen.
    """
    from src.Backend.coronagraph import RSUN_KM, fit_height_time

    times = list(series.times)
    if len(times) < max(2, series.order + 1):
        return None
    seconds = [(when - times[0]).total_seconds() for when in times]
    if max(seconds) <= min(seconds):
        return None
    try:
        return fit_height_time(seconds, [height * RSUN_KM for height in series.heights_rsun], order=series.order)
    except ValueError:
        return None


def _with_error(value: float, error: float, spec: str, unit: str) -> str:
    if not np.isfinite(value):
        return "—"
    text = format(value, spec)
    if np.isfinite(error):
        text += f" ± {format(abs(error), spec.replace('+', ''))}"
    return f"{text} {unit}".rstrip()


def kinematics_rows(fit: Any) -> list[tuple[str, str]]:
    """The fit's kinematics as (quantity, value ± 1σ) rows, in the card's units."""
    from src.Backend.coronagraph import RSUN_KM

    rows = [
        ("Fit", f"{FIT_ORDER_NAMES.get(int(fit.order), f'Order {fit.order}')} (degree {int(fit.order)})"),
        ("Recorded times", str(int(fit.times_s.size))),
        ("Time span", f"{(float(fit.times_s.max()) - float(fit.times_s.min())) / 60.0:.1f} min"),
    ]
    if int(fit.order) == 1:
        rows.append(("Speed", _with_error(fit.speed_km_s, fit.speed_err_km_s, ",.0f", "km/s")))
    else:
        rows.append(("Speed at first time", _with_error(fit.speed_km_s, fit.speed_err_km_s, ",.0f", "km/s")))
        rows.append(
            ("Speed at last time", _with_error(fit.speed_final_km_s, fit.speed_final_err_km_s, ",.0f", "km/s"))
        )
    if np.isfinite(fit.acceleration_km_s2):
        name = "Acceleration (quadratic companion fit)" if int(fit.order) == 1 else "Acceleration"
        if int(fit.order) >= 3:
            name = "Acceleration at first time"
        rows.append(
            (name, _with_error(fit.acceleration_km_s2 * 1000.0, fit.acceleration_err_km_s2 * 1000.0, "+,.1f", "m/s²"))
        )
        if int(fit.order) >= 3 and np.isfinite(fit.acceleration_final_km_s2):
            rows.append(
                (
                    "Acceleration at last time",
                    _with_error(
                        fit.acceleration_final_km_s2 * 1000.0, fit.acceleration_final_err_km_s2 * 1000.0, "+,.1f", "m/s²"
                    ),
                )
            )
    else:
        rows.append(("Acceleration", "needs three or more recorded times"))
    if int(fit.order) >= 3 and np.isfinite(fit.jerk_km_s3):
        rows.append(("Jerk", _with_error(fit.jerk_km_s3 * 1000.0, fit.jerk_err_km_s3 * 1000.0, "+,.3f", "m/s³")))
    if np.isfinite(fit.rms_residual_km):
        rows.append(("RMS residual", f"{fit.rms_residual_km / RSUN_KM:.3f} R☉"))
    return rows


def _fit_legend_label(fit: Any) -> str:
    name = FIT_ORDER_NAMES.get(int(fit.order), f"Order-{fit.order}")
    lines = [f"{name} fit"]
    if int(fit.order) == 1:
        lines.append("v = " + _with_error(fit.speed_km_s, fit.speed_err_km_s, ",.0f", "km/s"))
    else:
        lines.append("v₀ = " + _with_error(fit.speed_km_s, fit.speed_err_km_s, ",.0f", "km/s"))
        lines.append("v_end = " + _with_error(fit.speed_final_km_s, fit.speed_final_err_km_s, ",.0f", "km/s"))
    if np.isfinite(fit.acceleration_km_s2):
        lines.append(
            "a = " + _with_error(fit.acceleration_km_s2 * 1000.0, fit.acceleration_err_km_s2 * 1000.0, "+,.1f", "m/s²")
        )
    return "\n".join(lines)


def height_time_figure(
    series_list: Sequence[HeightTimeSeries],
    *,
    y_label: str = "Height (R☉)",
    width_in: float = 6.4,
    height_in: float = 4.8,
    dpi: float = 100.0,
) -> Any:
    """Recorded heights against UT with their error bars and fitted curves, Origin style."""
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure
    from matplotlib.ticker import AutoMinorLocator

    with origin_style():
        fig = Figure(figsize=(width_in, height_in), dpi=dpi)
        ax = fig.add_subplot(1, 1, 1)
        fig.subplots_adjust(left=0.14, right=0.96, bottom=0.14, top=0.95)
        all_times: list[datetime] = []
        for index, series in enumerate(series_list):
            if not series.times:
                continue
            colour = ORIGIN_COLORS[(index * 2) % len(ORIGIN_COLORS)]
            fit_colour = ORIGIN_FIT_COLOR if len(series_list) == 1 else ORIGIN_COLORS[(index * 2 + 1) % len(ORIGIN_COLORS)]
            marker = ORIGIN_MARKERS[index % len(ORIGIN_MARKERS)]
            times = list(series.times)
            all_times.extend(times)
            heights = np.asarray(series.heights_rsun, dtype=float)
            ax.plot(
                times,
                heights,
                linestyle="none",
                marker=marker,
                ms=7,
                color=colour,
                markeredgecolor=colour,
                label=series.label,
                zorder=3,
            )
            if series.errors_rsun is not None:
                errors = np.asarray(series.errors_rsun, dtype=float)
                usable = np.isfinite(errors) & (errors > 0)
                if usable.any():
                    ax.errorbar(
                        [time for time, ok in zip(times, usable) if ok],
                        heights[usable],
                        yerr=errors[usable],
                        fmt="none",
                        ecolor=colour,
                        elinewidth=1.0,
                        capsize=3,
                        capthick=1.0,
                        zorder=2,
                    )
            fit = fit_series(series)
            if fit is not None and fit.coeffs_km is not None:
                from src.Backend.coronagraph import RSUN_KM

                t_line = np.linspace(float(fit.times_s.min()), float(fit.times_s.max()), 160)
                curve_times = [times[0] + timedelta(seconds=float(value)) for value in t_line]
                ax.plot(
                    curve_times,
                    np.polyval(fit.coeffs_km, t_line) / RSUN_KM,
                    color=fit_colour,
                    lw=1.8,
                    label=_fit_legend_label(fit),
                    zorder=4,
                )

        if all_times:
            span_days = {when.date() for when in all_times}
            if len(span_days) == 1:
                ax.set_xlabel(f"Time (UT), {min(all_times):%Y-%m-%d}")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
            else:
                ax.set_xlabel("Time (UT)")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
            start, end = min(all_times), max(all_times)
            pad = max((end - start) * 0.06, timedelta(minutes=2))
            ax.set_xlim(start - pad, end + pad)
        else:
            ax.set_xlabel("Time (UT)")
            ax.text(0.5, 0.5, "No recorded fits", ha="center", va="center", transform=ax.transAxes)
        ax.set_ylabel(y_label)
        style_origin_axes(ax)
        # Dates are plain numbers to matplotlib, so the numeric minor locator
        # puts one minor tick between each pair of time ticks.
        ax.xaxis.set_minor_locator(AutoMinorLocator(2))
        if all_times:
            origin_legend(ax, loc="best", fontsize=9)
    return fig
