"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Publication renders of the radio graphs (src/backend/radio/radio_figures.py).

The main window's Export Figure, the project report and the Analyzer's best-fit
graph are drawn here with matplotlib, from the data rather than grabbed from the
screen, in the OriginPro style of ``src.backend.common.figure_export``: a white
page, Arial, a closed black frame with inward major and minor ticks, a framed
colour scale, Origin's filled black squares and red fitted curve, and a boxed
legend. The interactive views keep the application's own look.

* :func:`dynamic_spectrum_figure`: a spectrogram with its colour scale, the
  overlays the main window shows (frequency gaps, light curves, annotations,
  the GOES X-ray overlay, a ruler measurement) and, when loaded, the
  STEREO/SWAVES panel beneath it.
* :func:`fit_graph_figure` / :func:`draw_fit_graph`: data points with their
  fitted curve, as in the Analyzer's best fit and the Type II B-versus-R fit.
* :func:`type_ii_spectrum_figure`: the band-splitting lanes over the spectrum.
* :func:`goes_flux_figure`: GOES X-ray flux against time.

Nothing here imports Qt.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

import numpy as np

from src.backend.common.figure_export import (
    ORIGIN_COLORS,
    ORIGIN_FIT_COLOR,
    ORIGIN_MARKERS,
    origin_font_families,
    origin_legend,
    origin_style,
    style_origin_axes,
)
from src.backend.radio.frequency_axis import format_frequency_mhz, log_frequency_ticks_mhz, masked_display_data
from src.backend.radio.swaves import format_log_frequency, log_frequency_ticks
from src.backend.space_weather.goes_overlay import goes_class_ticks_for_limits, goes_flux_axis_limits

#: Page sizes in inches: a spectrogram, one with the SWAVES panel under it, an x-y graph.
SPECTRUM_FIGSIZE = (8.0, 5.0)
SPLIT_FIGSIZE = (8.0, 8.0)
GRAPH_FIGSIZE = (6.4, 4.8)

#: The hatching the main window lays over missing frequency channels.
GAP_STYLE = {
    "facecolor": "#b8b8b8",
    "edgecolor": "#555555",
    "alpha": 0.35,
    "hatch": "///",
    "linewidth": 0.0,
    "zorder": 3,
}

#: The ruler measurement's colour, as on screen.
MEASUREMENT_COLOR = "#18b4ff"

_LINE_STYLES = {"solid": "-", "dashed": "--", "dotted": ":"}


# --- Text ---------------------------------------------------------------------


@dataclass(frozen=True)
class GraphText:
    """Font, sizes in points and emphasis of a graph's titles, labels and ticks.

    An empty ``font_family`` means Origin's Arial. A family the user picked in
    the graph settings leads, with Arial and its stand-ins behind it for the
    glyphs it lacks.
    """

    font_family: str = ""
    tick_size: float = 11.0
    label_size: float = 12.0
    title_size: float = 12.0
    title_bold: bool = False
    title_italic: bool = False
    axis_bold: bool = False
    axis_italic: bool = False
    ticks_bold: bool = False
    ticks_italic: bool = False

    def families(self) -> list[str]:
        origin = list(origin_font_families())
        chosen = str(self.font_family or "").strip()
        if not chosen:
            return origin
        return [chosen] + [name for name in origin if name != chosen]

    def title_kw(self) -> dict[str, Any]:
        return {
            "fontsize": self.title_size,
            "fontweight": _weight(self.title_bold),
            "fontstyle": _style(self.title_italic),
        }

    def label_kw(self) -> dict[str, Any]:
        return {
            "fontsize": self.label_size,
            "fontweight": _weight(self.axis_bold),
            "fontstyle": _style(self.axis_italic),
        }


def _weight(bold: bool) -> str:
    return "bold" if bold else "normal"


def _style(italic: bool) -> str:
    return "italic" if italic else "normal"


@contextmanager
def origin_graph_style(text: GraphText | None = None) -> Iterator[GraphText]:
    """:func:`origin_style` set in ``text``'s font and sizes, for the block only.

    Build the figure inside the block: matplotlib reads these settings when an
    artist is created, and ticks made later copy the first tick's font.
    """
    import matplotlib

    text = text or GraphText()
    with origin_style(), matplotlib.rc_context(
        {
            "font.family": text.families(),
            "xtick.labelsize": text.tick_size,
            "ytick.labelsize": text.tick_size,
            "axes.labelsize": text.label_size,
            "axes.titlesize": text.title_size,
            "legend.fontsize": max(6.0, text.tick_size - 1.0),
        }
    ):
        yield text


def _emphasise_ticks(ax: Any, text: GraphText) -> None:
    """Bold or italic tick labels, as the graph settings ask."""
    if not (text.ticks_bold or text.ticks_italic):
        return
    for axis in (ax.xaxis, ax.yaxis):
        for label in axis.get_ticklabels(which="both"):
            label.set_fontweight(_weight(text.ticks_bold))
            label.set_fontstyle(_style(text.ticks_italic))


def power_law_label(a: float, b: float, *, prefix: str = "Best fit", symbol: str = "f", variable: str = "x", digits: int = 2) -> str:
    """``prefix: f = a x⁻ᵇ`` for a legend, the exponent a true superscript in the text font."""
    exponent = f"{-float(b):.{digits}f}"
    head = f"{prefix}: " if prefix else ""
    return f"{head}{symbol} = {float(a):.{digits}f} {variable}" + rf"$\mathregular{{^{{{exponent}}}}}$"


# --- Dynamic spectra ----------------------------------------------------------


@dataclass(frozen=True)
class SpectrumImage:
    """A spectrogram in display units, laid out by a matplotlib ``extent``.

    ``extent`` is ``(x0, x1, y_bottom, y_top)`` for ``imshow``'s default
    ``origin="upper"``: row 0 sits at ``y_top``. NaN draws as blank page.
    """

    data: Any
    extent: Sequence[float]
    cmap: Any = "viridis"
    levels: tuple[float, float] | None = None
    colorbar_label: str = ""


@dataclass(frozen=True)
class GoesOverlay:
    """GOES X-ray flux over the spectrogram, on a log flux-class axis at the right.

    Each series is ``(x_seconds, flux_wm2, colour, line_width)``.
    """

    series: Sequence[tuple[Any, Any, str, float]]
    label: str = "GOES X-Ray Class"


@dataclass(frozen=True)
class Measurement:
    """A two-point ruler measurement: its points and the label at the last one."""

    points: Sequence[tuple[float, float]]
    label: str = ""


@dataclass(frozen=True)
class SwavesPanel:
    """The STEREO/SWAVES spectrogram, its rows in log10(kHz)."""

    image: SpectrumImage
    title: str = ""
    y_label: str = "Frequency"
    #: Time range of the SWAVES data, in seconds on the shared axis.
    time_bounds: tuple[float, float] | None = None
    #: The CALLISTO interval, outlined inside the wider SWAVES window.
    callisto_span: tuple[float, float] | None = None


@dataclass(frozen=True)
class SpectrumGraph:
    """Everything :func:`dynamic_spectrum_figure` draws, as plain data.

    Light curves and annotations are the main window's own records: a light
    curve carries ``time``, ``y``, ``color``, ``line_width``, ``opacity``,
    ``line_style`` and its optional ``label`` at ``label_x``/``label_y``; an
    annotation carries ``kind`` (``line``, ``polygon`` or ``text``), ``points``,
    ``color``, ``line_width`` and, for text, ``text`` and its font settings.
    """

    image: SpectrumImage | None
    title: str = ""
    title_loc: str = "center"
    x_label: str = "Time [s]"
    y_label: str = "Frequency [MHz]"
    #: UT of time zero in seconds of the day; None labels the axis in seconds.
    ut_start_sec: float | None = None
    #: The zoom to reproduce, ``{"xlim": (x0, x1), "ylim": (y0, y1)}``.
    view: Mapping[str, Any] | None = None
    #: A log frequency axis over these bounds (MHz); None keeps it linear.
    log_frequency_bounds: tuple[float, float] | None = None
    gap_spans: Sequence[tuple[float, float]] = ()
    light_curves: Sequence[Mapping[str, Any]] = ()
    annotations: Sequence[Mapping[str, Any]] = ()
    goes: GoesOverlay | None = None
    measurement: Measurement | None = None
    swaves: SwavesPanel | None = None
    text: GraphText = field(default_factory=GraphText)
    figsize: tuple[float, float] | None = None


def dynamic_spectrum_figure(graph: SpectrumGraph) -> Any:
    """The dynamic spectrum as an OriginPro graph, with the SWAVES panel when given."""
    from matplotlib.figure import Figure

    if graph.image is None and graph.swaves is None:
        raise ValueError("A dynamic-spectrum figure needs a spectrum or a SWAVES panel.")
    split = graph.image is not None and graph.swaves is not None
    text = graph.text

    with origin_graph_style(text):
        fig = Figure(figsize=graph.figsize or (SPLIT_FIGSIZE if split else SPECTRUM_FIGSIZE), dpi=100, layout="constrained")
        if split:
            main_ax, swaves_ax = fig.subplots(2, 1, sharex=True)
        elif graph.image is not None:
            main_ax, swaves_ax = fig.add_subplot(1, 1, 1), None
        else:
            main_ax, swaves_ax = None, fig.add_subplot(1, 1, 1)

        goes_ax = None
        if main_ax is not None:
            goes_ax = _draw_callisto_panel(fig, main_ax, graph)
        if swaves_ax is not None:
            _draw_swaves_panel(fig, swaves_ax, graph.swaves, text, standalone=main_ax is None)

        panels = [ax for ax in (main_ax, swaves_ax) if ax is not None]
        bottom = panels[-1]
        if graph.ut_start_sec is not None:
            for ax in panels:
                ut_time_axis(ax, graph.ut_start_sec)
        bottom.set_xlabel(graph.x_label, **text.label_kw())
        if split:
            main_ax.tick_params(axis="x", which="both", labelbottom=False)

        _set_time_range(panels, graph)
        _apply_view(main_ax, graph.view)

        top = panels[0]
        title = graph.title if main_ax is not None else (graph.title or graph.swaves.title)
        if title:
            top.set_title(title, loc=graph.title_loc, **text.title_kw())
        for ax in panels + ([goes_ax] if goes_ax is not None else []):
            _emphasise_ticks(ax, text)
    return fig


def _draw_callisto_panel(fig: Any, ax: Any, graph: SpectrumGraph) -> Any:
    """The spectrogram, its colour scale and overlays; returns the GOES axes, if any."""
    image = graph.image
    text = graph.text
    im = _show_image(ax, image)
    for lo, hi in graph.gap_spans:
        ax.axhspan(float(lo), float(hi), **GAP_STYLE)
    style_origin_axes(ax)
    x0, x1, y_bottom, y_top = (float(value) for value in image.extent)
    ax.set_xlim(min(x0, x1), max(x0, x1))
    ax.set_ylim(y_bottom, y_top)
    if graph.log_frequency_bounds is not None:
        _log_frequency_axis(ax, graph.log_frequency_bounds)
    ax.set_ylabel(graph.y_label, **text.label_kw())

    _draw_light_curves(ax, graph.light_curves, text)
    _draw_annotations(ax, graph.annotations, text)
    if graph.measurement is not None:
        _draw_measurement(ax, graph.measurement, text)
    goes_ax = _draw_goes_overlay(ax, graph.goes, text) if graph.goes is not None else None

    # Constrained layout already clears the GOES class labels at the right edge.
    colorbar = fig.colorbar(im, ax=ax, pad=0.02, fraction=0.05)
    _style_colorbar(colorbar, image.colorbar_label, text)
    return goes_ax


def _show_image(ax: Any, image: SpectrumImage) -> Any:
    im = ax.imshow(
        masked_display_data(image.data),
        aspect="auto",
        extent=[float(value) for value in image.extent],
        cmap=image.cmap,
    )
    if image.levels is not None:
        low, high = (float(value) for value in image.levels)
        if math.isfinite(low) and math.isfinite(high) and high > low:
            im.set_clim(low, high)
    return im


def _style_colorbar(colorbar: Any, label: str, text: GraphText) -> None:
    """Origin's colour scale: a thin black box, inward ticks at the right."""
    colorbar.outline.set_edgecolor("#000000")
    colorbar.outline.set_linewidth(1.0)
    colorbar.ax.tick_params(which="both", direction="in", colors="#000000", labelsize=text.tick_size)
    colorbar.ax.tick_params(which="major", length=5, width=1.0)
    colorbar.ax.tick_params(which="minor", length=2.5, width=0.8)
    if label:
        colorbar.set_label(label, **text.label_kw())
    _emphasise_ticks(colorbar.ax, text)


def _log_frequency_axis(ax: Any, bounds: tuple[float, float]) -> None:
    """A log frequency axis with the same labelled ticks as the main window's."""
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter

    try:
        lo, hi = sorted((float(bounds[0]), float(bounds[1])))
    except (TypeError, ValueError, IndexError):
        return
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo <= 0.0 or hi <= lo:
        return
    ax.set_yscale("log")
    ax.set_ylim(lo, hi)
    labelled, minor = log_frequency_ticks_mhz(lo, hi)
    if labelled:
        ax.yaxis.set_major_locator(FixedLocator(labelled))
        ax.yaxis.set_minor_locator(FixedLocator(minor))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: format_frequency_mhz(value)))
        ax.yaxis.set_minor_formatter(NullFormatter())


#: UT tick spacings in seconds, from one second to six hours.
_UT_STEPS = (1, 2, 5, 10, 15, 20, 30, 60, 120, 180, 300, 600, 900, 1200, 1800, 3600, 7200, 10800, 21600)


def ut_time_axis(ax: Any, ut_start_sec: float, *, max_ticks: int = 8) -> None:
    """Label ``ax``'s time axis in UT, with ticks on whole UT minutes, hours, ...

    The axis runs in seconds from ``ut_start_sec``, which is rarely a round
    time; ticks are placed on round UT values rather than round offsets.
    """
    from matplotlib.ticker import Locator

    start = float(ut_start_sec)

    class _UtLocator(Locator):
        def __call__(self):
            vmin, vmax = self.axis.get_view_interval()
            return self.tick_values(vmin, vmax)

        def tick_values(self, vmin, vmax):
            lo, hi = sorted((float(vmin), float(vmax)))
            span = hi - lo
            if not (math.isfinite(lo) and math.isfinite(hi)) or span <= 0.0:
                return []
            step = next((value for value in _UT_STEPS if span / value <= max_ticks), None)
            if step is None:
                step = _UT_STEPS[-1] * math.ceil(span / (_UT_STEPS[-1] * max_ticks))
            first = math.ceil((lo + start) / step) * step - start
            return list(np.arange(first, hi + step * 1e-9, step))

    ax.xaxis.set_major_locator(_UtLocator())
    ax.xaxis.set_major_formatter(ut_time_formatter(start, ax))


def ut_time_formatter(ut_start_sec: float, ax: Any) -> Any:
    """Seconds on the time axis as UT, ``HH:MM`` or ``HH:MM:SS`` over five minutes or less."""
    from matplotlib.ticker import FuncFormatter

    start = float(ut_start_sec)

    def _format(value: float, _pos: Any) -> str:
        try:
            x0, x1 = ax.get_xlim()
            show_seconds = abs(float(x1) - float(x0)) <= 5 * 60
            total = int(round(start + float(value)))
        except (TypeError, ValueError, OverflowError):
            return ""
        hours = (total // 3600) % 24
        minutes = (total % 3600) // 60
        seconds = total % 60
        if show_seconds:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{hours:02d}:{minutes:02d}"

    return FuncFormatter(_format)


def _draw_light_curves(ax: Any, curves: Sequence[Mapping[str, Any]], text: GraphText) -> None:
    for curve in curves or ():
        colour = str(curve.get("color") or "#00e5ff")
        ax.plot(
            np.asarray(curve.get("time", []), dtype=float),
            np.asarray(curve.get("y", []), dtype=float),
            color=colour,
            linewidth=float(curve.get("line_width", 2.0)),
            alpha=float(curve.get("opacity", 0.95)),
            linestyle=_LINE_STYLES.get(str(curve.get("line_style") or "solid").lower(), "-"),
            zorder=8,
        )
        label = str(curve.get("label") or "").strip()
        if curve.get("show_label") and label:
            ax.text(
                float(curve.get("label_x", 0.0)),
                float(curve.get("label_y", curve.get("frequency_mhz", 0.0))),
                label,
                color=colour,
                fontsize=max(6.0, text.tick_size - 2.0),
                ha="left",
                va="bottom",
                zorder=9,
            )


def _draw_annotations(ax: Any, annotations: Sequence[Mapping[str, Any]], text: GraphText) -> None:
    for annotation in annotations or ():
        if not annotation.get("visible", True):
            continue
        kind = annotation.get("kind")
        points = list(annotation.get("points") or [])
        colour = str(annotation.get("color") or "#00d4ff")
        if kind in {"polygon", "line"} and len(points) >= 2:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            if kind == "polygon":
                xs.append(xs[0])
                ys.append(ys[0])
            ax.plot(xs, ys, color=colour, linewidth=float(annotation.get("line_width", 1.5)), alpha=0.95, zorder=10)
        elif kind == "text" and points:
            x, y = points[0][0], points[0][1]
            family = str(annotation.get("font_family") or "").strip()
            extra = {"fontfamily": [family] + text.families()} if family else {}
            ax.text(
                float(x),
                float(y),
                str(annotation.get("text", "")),
                color=colour,
                fontsize=float(annotation.get("font_size", text.tick_size)),
                fontweight=_weight(bool(annotation.get("font_bold", False))),
                fontstyle=_style(bool(annotation.get("font_italic", False))),
                ha="left",
                va="bottom",
                zorder=11,
                **extra,
            )


def _draw_measurement(ax: Any, measurement: Measurement, text: GraphText) -> None:
    points = [(float(x), float(y)) for x, y in measurement.points]
    if not points:
        return
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    ax.scatter(xs, ys, marker="o", s=46, facecolors="white", edgecolors=MEASUREMENT_COLOR, linewidths=1.5, zorder=20)
    if len(points) < 2:
        return
    ax.plot(xs[:2], ys[:2], color=MEASUREMENT_COLOR, linewidth=1.8, zorder=19)
    if measurement.label:
        ax.text(
            xs[-1],
            ys[-1],
            measurement.label,
            color="white",
            fontsize=max(6.0, text.tick_size - 2.0),
            ha="left",
            va="bottom",
            bbox={"boxstyle": "square,pad=0.25", "facecolor": "#0f172a", "edgecolor": MEASUREMENT_COLOR, "alpha": 0.85},
            zorder=21,
        )


def _draw_goes_overlay(ax: Any, goes: GoesOverlay, text: GraphText) -> Any:
    """GOES flux on a twin log axis; the right edge becomes its X-ray class scale."""
    from matplotlib.ticker import LogLocator, NullFormatter

    series = []
    for xs, flux, colour, width in goes.series:
        x_arr = np.asarray(xs, dtype=float).reshape(-1)
        f_arr = np.asarray(flux, dtype=float).reshape(-1)
        n = min(x_arr.size, f_arr.size)
        mask = np.isfinite(x_arr[:n]) & np.isfinite(f_arr[:n]) & (f_arr[:n] > 0.0)
        if np.any(mask):
            series.append((x_arr[:n][mask], f_arr[:n][mask], colour, width))
    if not series:
        return None
    limits = goes_flux_axis_limits(np.concatenate([item[1] for item in series]))
    if limits is None:
        return None

    twin = ax.twinx()
    twin.set_yscale("log")
    twin.set_ylim(limits)
    for index, (xs, flux, colour, width) in enumerate(series, start=1):
        twin.plot(xs, flux, color=colour, linewidth=float(width), alpha=0.98, solid_capstyle="round", zorder=6 + index)
    class_ticks = goes_class_ticks_for_limits(limits[0], limits[1])
    twin.set_yticks([value for value, _label in class_ticks], labels=[label for _value, label in class_ticks])
    twin.yaxis.set_minor_locator(LogLocator(base=10.0, subs=np.arange(2.0, 10.0), numticks=120))
    twin.yaxis.set_minor_formatter(NullFormatter())
    twin.patch.set_visible(False)
    twin.grid(False)
    twin.tick_params(axis="y", which="both", direction="in", left=False, right=True, colors="#000000")
    twin.tick_params(axis="y", which="major", length=6, width=1.5)
    twin.tick_params(axis="y", which="minor", length=3, width=1.0)
    twin.set_ylabel(goes.label, **text.label_kw())
    return twin


def _draw_swaves_panel(fig: Any, ax: Any, panel: SwavesPanel, text: GraphText, *, standalone: bool) -> None:
    from matplotlib.ticker import FixedLocator, FuncFormatter

    im = _show_image(ax, panel.image)
    style_origin_axes(ax)
    _x0, _x1, y_bottom, y_top = (float(value) for value in panel.image.extent)
    ax.set_ylim(y_bottom, y_top)
    lo, hi = min(y_bottom, y_top), max(y_bottom, y_top)
    ticks = log_frequency_ticks(lo, hi)
    if ticks:
        ax.yaxis.set_major_locator(FixedLocator(ticks))
    ax.yaxis.set_minor_locator(FixedLocator(_log10_minor_ticks(lo, hi)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: format_log_frequency(value)))
    ax.set_ylabel(panel.y_label, **text.label_kw())
    if not standalone:
        if panel.title:
            ax.set_title(panel.title, loc="left", fontsize=text.label_size, fontweight=_weight(text.axis_bold), fontstyle=_style(text.axis_italic))
        if panel.callisto_span is not None:
            t0, t1 = (float(value) for value in panel.callisto_span)
            if math.isfinite(t0) and math.isfinite(t1) and t1 > t0:
                ax.axvspan(t0, t1, facecolor="none", edgecolor="#202020", linewidth=1.2, linestyle="--", zorder=5)
    colorbar = fig.colorbar(im, ax=ax, pad=0.02, fraction=0.05)
    _style_colorbar(colorbar, panel.image.colorbar_label, text)


def _log10_minor_ticks(lo: float, hi: float) -> list[float]:
    """Minor ticks at 2..9 of each decade, on an axis already in log10 units."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return []
    ticks = []
    for decade in range(int(math.floor(lo)), int(math.ceil(hi)) + 1):
        for mantissa in range(2, 10):
            value = decade + math.log10(mantissa)
            if lo <= value <= hi:
                ticks.append(value)
    return ticks


def _set_time_range(panels: Sequence[Any], graph: SpectrumGraph) -> None:
    """The time axis covers the spectrum, widened to the SWAVES window when split."""
    bounds = []
    if graph.image is not None:
        x0, x1 = float(graph.image.extent[0]), float(graph.image.extent[1])
        bounds.append((min(x0, x1), max(x0, x1)))
    if graph.swaves is not None:
        if graph.swaves.time_bounds is not None:
            bounds.append(tuple(sorted(float(value) for value in graph.swaves.time_bounds)))
        else:
            x0, x1 = float(graph.swaves.image.extent[0]), float(graph.swaves.image.extent[1])
            bounds.append((min(x0, x1), max(x0, x1)))
    finite = [(lo, hi) for lo, hi in bounds if math.isfinite(lo) and math.isfinite(hi) and hi > lo]
    if finite:
        panels[0].set_xlim(min(lo for lo, _hi in finite), max(hi for _lo, hi in finite))


def _apply_view(ax: Any, view: Mapping[str, Any] | None) -> None:
    """Reproduce the on-screen zoom; a view without finite limits is ignored."""
    if ax is None or not view:
        return
    try:
        xlim = tuple(float(value) for value in view.get("xlim"))
        ylim = tuple(float(value) for value in view.get("ylim"))
    except (TypeError, ValueError):
        return
    if len(xlim) == 2 and all(math.isfinite(value) for value in xlim) and xlim[0] != xlim[1]:
        ax.set_xlim(xlim)
    if len(ylim) == 2 and all(math.isfinite(value) for value in ylim) and ylim[0] != ylim[1]:
        if ax.get_yscale() == "log" and min(ylim) <= 0.0:
            return
        ax.set_ylim(ylim)


# --- Data and fitted curves ---------------------------------------------------


def draw_fit_graph(
    ax: Any,
    x: Any,
    y: Any,
    *,
    fit_x: Any = None,
    fit_y: Any = None,
    data_label: str = "Data",
    fit_label: str = "Fit",
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    text: GraphText | None = None,
    marker: str = ORIGIN_MARKERS[0],
    data_color: str = ORIGIN_COLORS[0],
    fit_color: str = ORIGIN_FIT_COLOR,
) -> None:
    """Origin's graph of data and its fit on ``ax``: filled black squares, a red curve.

    Draw inside :func:`origin_graph_style`, with ``ax`` created in the same block.
    """
    text = text or GraphText()
    xs, ys = _finite_pairs(x, y)
    if xs.size:
        ax.plot(
            xs,
            ys,
            linestyle="none",
            marker=marker,
            markersize=6,
            color=data_color,
            markeredgecolor=data_color,
            label=data_label,
            zorder=3,
        )
    fx, fy = _finite_pairs(fit_x, fit_y)
    if fx.size:
        ax.plot(fx, fy, color=fit_color, linewidth=1.8, label=fit_label, zorder=4)
    style_origin_axes(ax)
    if x_label:
        ax.set_xlabel(x_label, **text.label_kw())
    if y_label:
        ax.set_ylabel(y_label, **text.label_kw())
    if title:
        ax.set_title(title, **text.title_kw())
    if ax.get_legend_handles_labels()[0]:
        origin_legend(ax, loc="best")
    _emphasise_ticks(ax, text)


def fit_graph_figure(
    x: Any,
    y: Any,
    *,
    fit_x: Any = None,
    fit_y: Any = None,
    data_label: str = "Data",
    fit_label: str = "Fit",
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    text: GraphText | None = None,
    figsize: tuple[float, float] = GRAPH_FIGSIZE,
) -> Any:
    """:func:`draw_fit_graph` on its own white page."""
    from matplotlib.figure import Figure

    with origin_graph_style(text) as style:
        fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        ax = fig.add_subplot(1, 1, 1)
        draw_fit_graph(
            ax,
            x,
            y,
            fit_x=fit_x,
            fit_y=fit_y,
            data_label=data_label,
            fit_label=fit_label,
            title=title,
            x_label=x_label,
            y_label=y_label,
            text=style,
        )
    return fig


def _finite_pairs(x: Any, y: Any) -> tuple[np.ndarray, np.ndarray]:
    if x is None or y is None:
        return np.empty(0, dtype=float), np.empty(0, dtype=float)
    xs = np.asarray(x, dtype=float).reshape(-1)
    ys = np.asarray(y, dtype=float).reshape(-1)
    n = min(xs.size, ys.size)
    xs, ys = xs[:n], ys[:n]
    mask = np.isfinite(xs) & np.isfinite(ys)
    return xs[mask], ys[mask]


# --- Type II band splitting ---------------------------------------------------


@dataclass(frozen=True)
class BandTrace:
    """One band-splitting lane: its clicked points and sampled fitted curve."""

    label: str
    points: Sequence[tuple[float, float]]
    fit: tuple[Any, Any] | None = None
    marker_color: str = ORIGIN_COLORS[0]
    line_color: str = ORIGIN_FIT_COLOR
    marker_size: float = 6.0
    line_width: float = 1.8


def type_ii_spectrum_figure(
    image: SpectrumImage | None,
    bands: Sequence[BandTrace],
    *,
    title: str = "",
    x_label: str = "Time (s)",
    y_label: str = "Frequency (MHz)",
    text: GraphText | None = None,
    figsize: tuple[float, float] = SPECTRUM_FIGSIZE,
) -> Any:
    """The upper and lower Type II lanes and their fits over the spectrum."""
    from matplotlib.figure import Figure

    with origin_graph_style(text) as style:
        fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        ax = fig.add_subplot(1, 1, 1)
        im = _show_image(ax, image) if image is not None else None
        for index, band in enumerate(bands):
            points = np.asarray(list(band.points), dtype=float).reshape(-1, 2) if len(band.points) else np.empty((0, 2))
            xs, ys = _finite_pairs(points[:, 0], points[:, 1])
            if xs.size:
                ax.plot(
                    xs,
                    ys,
                    linestyle="none",
                    marker=ORIGIN_MARKERS[index % len(ORIGIN_MARKERS)],
                    markersize=band.marker_size,
                    color=band.marker_color,
                    markeredgecolor="#000000",
                    markeredgewidth=0.8,
                    label=band.label,
                    zorder=4,
                )
            if band.fit is not None:
                fx, fy = _finite_pairs(*band.fit)
                if fx.size:
                    ax.plot(fx, fy, color=band.line_color, linewidth=band.line_width, label=f"{band.label} fit", zorder=5)
        style_origin_axes(ax)
        if image is not None:
            x0, x1, y_bottom, y_top = (float(value) for value in image.extent)
            ax.set_xlim(min(x0, x1), max(x0, x1))
            ax.set_ylim(y_bottom, y_top)
            colorbar = fig.colorbar(im, ax=ax, pad=0.02, fraction=0.05)
            _style_colorbar(colorbar, image.colorbar_label, style)
        ax.set_xlabel(x_label, **style.label_kw())
        ax.set_ylabel(y_label, **style.label_kw())
        if title:
            ax.set_title(title, **style.title_kw())
        if ax.get_legend_handles_labels()[0]:
            origin_legend(ax, loc="best")
        _emphasise_ticks(ax, style)
    return fig


# --- GOES X-ray flux ----------------------------------------------------------


def goes_flux_figure(
    series: Sequence[tuple[str, Any, Any]],
    *,
    title: str = "GOES X-Ray Data",
    x_label: str = "Time [s]",
    y_label: str = "Flux (W/m²)",
    ut_start_sec: float | None = None,
    text: GraphText | None = None,
    figsize: tuple[float, float] = GRAPH_FIGSIZE,
) -> Any:
    """GOES X-ray flux on a log axis, one Origin colour per channel.

    ``series`` holds ``(label, x_seconds, flux_wm2)``; raises when none has a
    positive, finite sample to draw.
    """
    from matplotlib.figure import Figure

    with origin_graph_style(text) as style:
        fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        ax = fig.add_subplot(1, 1, 1)
        plotted = 0
        for label, xs, flux in series:
            x_arr, f_arr = _finite_pairs(xs, flux)
            keep = f_arr > 0.0
            if not np.any(keep):
                continue
            ax.plot(x_arr[keep], f_arr[keep], color=ORIGIN_COLORS[plotted % len(ORIGIN_COLORS)], linewidth=1.5, label=str(label))
            plotted += 1
        if not plotted:
            raise ValueError("No positive GOES flux to plot.")
        ax.set_yscale("log")
        style_origin_axes(ax)
        if ut_start_sec is not None:
            ut_time_axis(ax, ut_start_sec)
        ax.set_xlabel(x_label, **style.label_kw())
        ax.set_ylabel(y_label, **style.label_kw())
        if title:
            ax.set_title(title, **style.title_kw())
        origin_legend(ax, loc="best")
        _emphasise_ticks(ax, style)
    return fig
