"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Stacked geomagnetic-response panels (src/backend/space_weather/geomagnetic_figures.py).

One drawer for every place the response is shown: the Geomagnetic Response
window draws onto its canvas with it (in the app's light or dark colours, see
:class:`GeoPalette`), the project report and the event-figure composer build
OriginPro-style figures from it. Qt-free.

Panel groups, in order:

=========  ==============================================  =====
key        content                                         axes
=========  ==============================================  =====
dst        Dst (hourly steps) with SYM-H (5 min)            1
kp         Kp bars coloured by NOAA G level                 1
imf        Bt, Bz, By                                       1
plasma     V | Np (twin);  T | Pdyn (twin)                  2
derived    Ey;  β | Alfvén Mach (twin)                      2
indices    AE, AL, AU                                       1
=========  ==============================================  =====

Markers (the event start, DONKI shocks, storms and linked CMEs) are lines
through every panel, named once above the top panel. Only markers inside the
plotted interval are drawn: a label for a storm days before the window used to
drag the whole layout sideways.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

GEO_PANELS: tuple[tuple[str, str], ...] = (
    ("dst", "Dst / SYM-H"),
    ("kp", "Kp"),
    ("imf", "IMF Bt, Bz, By"),
    ("plasma", "V, Np, T, Pdyn"),
    ("derived", "Ey, β, Mach"),
    ("indices", "AE / AL / AU"),
)
DEFAULT_GEO_PANELS = ("dst", "kp", "imf", "plasma")
_ROWS = {"dst": 1, "kp": 1, "imf": 1, "plasma": 2, "derived": 2, "indices": 1}

#: Kp bar colours by G level (0 = below storm level … 5 = G5).
KP_COLORS = ("#2ca02c", "#f1c232", "#ff9900", "#e06666", "#cc0000", "#7f0000")

#: Line colours, chosen to stay apart in print and for colour-blind readers
#: (Okabe–Ito). Black means "the page's text colour": white on a dark screen.
COLORS = {
    "dst": "#000000",
    "symh": "#0072B2",
    "bt": "#000000",
    "bz": "#D55E00",
    "by": "#009E73",
    "v": "#0072B2",
    "np": "#D55E00",
    "t": "#CC79A7",
    "pdyn": "#009E73",
    "ey": "#D55E00",
    "beta": "#0072B2",
    "mach": "#E69F00",
    "ae": "#000000",
    "al": "#0072B2",
    "au": "#D55E00",
}
#: Brighter stand-ins where an Okabe–Ito colour is too dark on a dark canvas.
DARK_COLORS = {
    "symh": "#56B4E9",
    "v": "#56B4E9",
    "beta": "#56B4E9",
    "al": "#56B4E9",
    "bz": "#FF7F2A",
    "np": "#FF7F2A",
    "ey": "#FF7F2A",
    "au": "#FF7F2A",
    "mach": "#F0E442",
}

EVENT_LINE = {"color": "#d62728", "linestyle": "--", "linewidth": 1.1}
RADIO_SPAN = {"facecolor": "#9467bd", "alpha": 0.18, "linewidth": 0.0}

#: Dst storm thresholds (nT) and the class below each, as reference lines.
DST_LEVELS = ((-50.0, "moderate"), (-100.0, "intense"), (-250.0, "severe"))
#: Kp thresholds drawn as reference lines, with their NOAA G level.
KP_LEVELS = ((5.0, "G1"), (7.0, "G3"))

#: At most this many rows of marker labels above the top panel.
MARKER_LABEL_ROWS = 3


@dataclass(frozen=True)
class GeoPalette:
    """Screen colours. The default is a white page (exports use OriginPro style)."""

    foreground: str = "#000000"
    face: str = "#ffffff"
    figure: str = "#ffffff"
    #: Axis frame and tick marks: quieter than the text.
    frame: str = "#555555"
    #: Reference lines (zero, storm levels) and secondary text.
    muted: str = "#8a8a8a"
    grid: str = "#000000"
    grid_alpha: float = 0.08
    dark: bool = False


LIGHT_PALETTE = GeoPalette()


def dark_palette(foreground: str = "#f0f0f0", face: str = "#1f1f1f", figure: str = "#2b2b2b") -> GeoPalette:
    return GeoPalette(
        foreground=foreground, face=face, figure=figure,
        frame="#9a9a9a", muted="#8c8c8c", grid="#ffffff", grid_alpha=0.07, dark=True,
    )


def series_color(name: str, palette: GeoPalette | None = None) -> str:
    palette = palette or LIGHT_PALETTE
    base = COLORS.get(name, "#000000")
    if base == "#000000":
        return palette.foreground
    if palette.dark:
        return DARK_COLORS.get(name, base)
    return base


def _readable(color: str, palette: GeoPalette) -> str:
    """Marker colours are picked for white paper; lift them on a dark canvas."""
    if not palette.dark:
        return color
    from matplotlib.colors import to_hex, to_rgb

    try:
        r, g, b = to_rgb(color)
    except ValueError:
        return color
    mix = 0.45
    return to_hex((r + (1 - r) * mix, g + (1 - g) * mix, b + (1 - b) * mix))


def panel_row_count(key: str) -> int:
    return _ROWS.get(str(key), 0)


def normalize_panels(panels: Iterable[str] | None) -> list[str]:
    wanted = [str(key) for key in (panels or DEFAULT_GEO_PANELS)]
    return [key for key, _label in GEO_PANELS if key in wanted]


def total_rows(panels: Iterable[str]) -> int:
    return sum(panel_row_count(key) for key in normalize_panels(panels))


def _dates(times: np.ndarray) -> np.ndarray:
    return np.asarray(times, dtype="datetime64[s]").astype("datetime64[ms]").astype(datetime)


def _series(context: Any, name: str):
    series = getattr(context, "series", {}).get(name)
    if series is None or np.asarray(series.times).size == 0:
        return None
    return series


def _plot_line(
    ax: Any, context: Any, name: str, *, label: str | None = None, step: bool = False,
    palette: GeoPalette | None = None, **kw: Any,
) -> bool:
    series = _series(context, name)
    if series is None or not series.finite():
        return False
    x = _dates(series.times)
    y = np.asarray(series.values, dtype=float)
    kw.setdefault("color", series_color(name, palette))
    kw.setdefault("linewidth", 0.9)
    text = label if label is not None else series.label
    if step:
        ax.step(x, y, where="post", label=text, **kw)
    else:
        ax.plot(x, y, label=text, **kw)
    return True


def _finite_values(context: Any, *names: str) -> np.ndarray:
    chunks = []
    for name in names:
        series = _series(context, name)
        if series is not None:
            values = np.asarray(series.values, dtype=float)
            chunks.append(values[np.isfinite(values)])
    return np.concatenate(chunks) if chunks else np.array([], dtype=float)


def _label(ax: Any, text: str, units: str) -> None:
    ax.set_ylabel(f"{text} [{units}]" if units else text)


def _no_data(ax: Any, text: str, palette: GeoPalette | None = None) -> None:
    palette = palette or LIGHT_PALETTE
    ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center", va="center", fontsize=8, color=palette.muted)


def _hline(ax: Any, y: float, palette: GeoPalette, *, dotted: bool = False) -> None:
    ax.axhline(
        y, color=palette.muted, linewidth=0.5 if dotted else 0.6,
        linestyle=":" if dotted else "-", alpha=0.9, zorder=0.5,
    )


def _level_text(ax: Any, y: float, text: str, palette: GeoPalette, *, below: bool) -> None:
    """A small name for a reference line, at the panel's right edge."""
    import matplotlib.transforms as mtransforms

    # Inset from the frame (OriginPro ticks point inwards) and boxed in the
    # panel colour, so a marker line through the label cannot hide it.
    ax.text(
        0.985, y, text, transform=mtransforms.blended_transform_factory(ax.transAxes, ax.transData),
        ha="right", va="top" if below else "bottom", fontsize=6.5, color=palette.muted, clip_on=True,
        zorder=5, bbox={"boxstyle": "square,pad=0.15", "facecolor": palette.face, "edgecolor": "none", "alpha": 0.85},
    )


def _legend(ax: Any, *, origin: bool, palette: GeoPalette | None = None) -> None:
    """A one-row legend in the panel's emptiest corner.

    Two-quantity panels (a twin axis) have none: each axis is coloured like
    its line instead, which costs no plot area.
    """
    handles, labels = ax.get_legend_handles_labels()
    if len(handles) < 2:
        return
    if origin:
        from src.backend.common.figure_export import origin_legend

        origin_legend(ax, loc="best", fontsize=7, ncol=len(handles))
        return
    palette = palette or LIGHT_PALETTE
    ax.legend(
        handles, labels, loc="best", ncol=len(handles), fontsize=7.5,
        frameon=True, framealpha=0.7, facecolor=palette.face, edgecolor="none",
        handlelength=1.4, handletextpad=0.4, columnspacing=1.0, borderaxespad=0.3,
        borderpad=0.25, labelcolor=palette.foreground,
    )


def _twin(ax: Any, *, origin: bool, palette: GeoPalette | None = None) -> Any:
    twin = ax.twinx()
    if origin:
        twin.minorticks_on()
        twin.tick_params(which="both", direction="in", left=False, right=True)
        ax.tick_params(which="both", right=False)
    else:
        _style_screen_axes(twin, palette or LIGHT_PALETTE, grid=False)
    # Remembered so OriginPro styling keeps the host's right-hand ticks off:
    # the twin draws its own there.
    ax._geo_twin = twin
    return twin


def _color_y(ax: Any, color: str) -> None:
    """Tie an axis of a two-quantity panel to its line by colour."""
    ax.tick_params(axis="y", which="both", labelcolor=color)
    ax.yaxis.label.set_color(color)


def _style_screen_axes(ax: Any, palette: GeoPalette, *, grid: bool = True) -> None:
    ax.set_facecolor(palette.face)
    for spine in ax.spines.values():
        spine.set_color(palette.frame)
        spine.set_linewidth(0.8)
    ax.tick_params(which="both", colors=palette.frame, labelcolor=palette.foreground, labelsize=8.5)
    ax.xaxis.label.set_color(palette.foreground)
    ax.yaxis.label.set_color(palette.foreground)
    ax.xaxis.label.set_size(9)
    ax.yaxis.label.set_size(9)
    if grid:
        ax.grid(True, which="major", color=palette.grid, alpha=palette.grid_alpha, linewidth=0.6)
        ax.set_axisbelow(True)


def draw_dst(ax: Any, context: Any, *, origin: bool = False, palette: GeoPalette | None = None) -> None:
    palette = palette or LIGHT_PALETTE
    drawn = _plot_line(ax, context, "symh", linewidth=0.8, alpha=0.95, palette=palette)
    has_dst = _plot_line(ax, context, "dst", step=True, linewidth=1.4, palette=palette, zorder=3)
    drawn |= has_dst
    _hline(ax, 0.0, palette)
    values = _finite_values(context, "dst", "symh")
    low = float(values.min()) if values.size else -60.0
    high = float(values.max()) if values.size else 20.0
    # A quiet week still shows where "weak" ends; a storm sets its own depth.
    low, high = min(low, -30.0), max(high, 10.0)
    pad = 0.08 * (high - low)
    bottom, top = low - pad, high + pad
    shown = [(level, name) for level, name in DST_LEVELS if level >= bottom]
    if shown:
        # Room under the lowest line for its name.
        bottom = min(bottom, shown[-1][0] - 0.14 * (top - bottom))
    ax.set_ylim(bottom, top)
    named: list[float] = []
    for level, name in shown:
        _hline(ax, level, palette, dotted=True)
        # Deep storms squeeze -50 and -100 together: name only lines with room.
        if not named or named[-1] - level >= 0.12 * (top - bottom):
            _level_text(ax, level, name, palette, below=True)
            named.append(level)
    _mark_minimum(ax, context, "dst" if has_dst else "symh", palette)
    _label(ax, "Dst, SYM-H", "nT")
    if not drawn:
        _no_data(ax, "Dst / SYM-H unavailable", palette)
    _legend(ax, origin=origin, palette=palette)


def _mark_minimum(ax: Any, context: Any, name: str, palette: GeoPalette) -> None:
    series = _series(context, name)
    if series is None or not series.finite():
        return
    values = np.asarray(series.values, dtype=float)
    index = int(np.nanargmin(values))
    when = _dates(series.times[index : index + 1])[0]
    value = float(values[index])
    color = series_color(name, palette)
    ax.plot([when], [value], marker="^", markersize=5, color=color, linestyle="none", zorder=4)
    start, end = getattr(context, "start", None), getattr(context, "end", None)
    right_side = True
    if start is not None and end is not None and end > start:
        right_side = (when - start).total_seconds() / (end - start).total_seconds() < 0.8
    number = f"{value:.0f}".replace("-", "−")  # a true minus, as on the axis
    ax.annotate(
        f"min {series.label} {number} nT", xy=(when, value), xytext=(6 if right_side else -6, 0),
        textcoords="offset points", ha="left" if right_side else "right", va="center",
        fontsize=7, color=color, annotation_clip=True,
    )


def draw_kp(ax: Any, context: Any, *, origin: bool = False, palette: GeoPalette | None = None) -> None:
    from matplotlib.ticker import MultipleLocator

    from src.backend.space_weather.geomagnetic_context import g_level

    palette = palette or LIGHT_PALETTE
    series = _series(context, "kp")
    if series is None or not series.finite():
        _no_data(ax, "Kp unavailable", palette)
    else:
        x = _dates(series.times)
        y = np.asarray(series.values, dtype=float)
        colors = [KP_COLORS[g_level(value)] if np.isfinite(value) else "#cccccc" for value in y]
        # Three-hour bars, drawn from the interval start.
        width = np.timedelta64(3, "h") / np.timedelta64(1, "D") * 0.9
        ax.bar(x, np.nan_to_num(y), width=width, align="edge", color=colors, edgecolor="none", zorder=2)
    ax.set_ylim(0, 9)
    ax.yaxis.set_major_locator(MultipleLocator(3))
    ax.yaxis.set_minor_locator(MultipleLocator(1))
    for level, name in KP_LEVELS:
        _hline(ax, level, palette, dotted=True)
        _level_text(ax, level, name, palette, below=False)
    _label(ax, "Kp", "")


def draw_imf(ax: Any, context: Any, *, origin: bool = False, palette: GeoPalette | None = None) -> None:
    palette = palette or LIGHT_PALETTE
    drawn = False
    # Bz last, on top: it is the component that drives the storm.
    for name, label, width in (("bt", "Bt", 1.0), ("by", "By", 0.7), ("bz", "Bz", 0.9)):
        drawn |= _plot_line(ax, context, name, label=label, linewidth=width, palette=palette)
    _hline(ax, 0.0, palette)
    _label(ax, "IMF (GSM)", "nT")
    if not drawn:
        _no_data(ax, "IMF unavailable", palette)
    _legend(ax, origin=origin, palette=palette)


def draw_plasma(axes: Sequence[Any], context: Any, *, origin: bool = False, palette: GeoPalette | None = None) -> None:
    palette = palette or LIGHT_PALETTE
    top, bottom = axes
    if not _plot_line(top, context, "v", label="V", palette=palette):
        _no_data(top, "Solar wind speed unavailable", palette)
    _label(top, "V", "km/s")
    twin = _twin(top, origin=origin, palette=palette)
    _plot_line(twin, context, "np", label="Np", linewidth=0.7, palette=palette)
    twin.set_ylabel("Np [cm⁻³]")
    _color_y(top, series_color("v", palette))
    _color_y(twin, series_color("np", palette))

    if _plot_line(bottom, context, "t", label="T", palette=palette):
        bottom.set_yscale("log")
    else:
        _no_data(bottom, "Temperature unavailable", palette)
    _label(bottom, "T", "K")
    twin = _twin(bottom, origin=origin, palette=palette)
    _plot_line(twin, context, "pdyn", label="Pdyn", linewidth=0.7, palette=palette)
    twin.set_ylabel("Pdyn [nPa]")
    _color_y(bottom, series_color("t", palette))
    _color_y(twin, series_color("pdyn", palette))


def draw_derived(axes: Sequence[Any], context: Any, *, origin: bool = False, palette: GeoPalette | None = None) -> None:
    palette = palette or LIGHT_PALETTE
    top, bottom = axes
    if not _plot_line(top, context, "ey", label="Ey", palette=palette):
        _no_data(top, "Ey unavailable", palette)
    _hline(top, 0.0, palette)
    _label(top, "Ey", "mV/m")

    if _plot_line(bottom, context, "beta", label="β", palette=palette):
        bottom.set_yscale("log")
    else:
        _no_data(bottom, "β unavailable", palette)
    _label(bottom, "β", "")
    twin = _twin(bottom, origin=origin, palette=palette)
    _plot_line(twin, context, "mach", label="Mach", linewidth=0.7, palette=palette)
    twin.set_ylabel("Alfvén Mach")
    _color_y(bottom, series_color("beta", palette))
    _color_y(twin, series_color("mach", palette))


def draw_indices(ax: Any, context: Any, *, origin: bool = False, palette: GeoPalette | None = None) -> None:
    palette = palette or LIGHT_PALETTE
    drawn = False
    for name in ("ae", "au", "al"):
        drawn |= _plot_line(ax, context, name, label=name.upper(), linewidth=0.8, palette=palette)
    _hline(ax, 0.0, palette)
    _label(ax, "AE, AU, AL", "nT")
    if not drawn:
        _no_data(ax, "AE not yet published", palette)
    _legend(ax, origin=origin, palette=palette)


def _visible_markers(markers: Sequence[Mapping[str, Any]], start: Any, end: Any) -> list[Mapping[str, Any]]:
    out = []
    for marker in markers:
        when = marker.get("time")
        if when is None:
            continue
        if start is not None and end is not None and not (start <= when <= end):
            continue
        out.append(marker)
    return out


#: Which marker labels survive when the rows are full (lower number wins).
_LABEL_PRIORITY = {"event": 0, "gst": 1, "ips": 2, "cme": 3}


@dataclass(frozen=True)
class _MarkerLabel:
    time: datetime
    text: str
    short: str
    color: str
    kind: str = ""


def short_marker_label(kind: str, text: str) -> str:
    """A crowded window's name for a marker: "CME 1330", "Shock", "Kp 9.0"."""
    import re

    if kind == "event":
        return "Event"
    if kind == "ips":
        return "Shock"
    if kind == "gst":
        match = re.search(r"Kp\s*[\d.]+", text)
        return match.group(0) if match else "Storm"
    if kind == "cme":
        match = re.search(r"(\d+)\s*km/s", text)
        return f"CME {match.group(1)}" if match else "CME"
    return text if len(text) <= 14 else text[:13] + "…"


def _place_labels(
    spans: Sequence[tuple[float, float]], order: Sequence[int], *, rows: int, gap: float = 4.0
) -> dict[int, int]:
    """Give each span (in ``order``) the first row it fits in; spans that fit nowhere are left out."""
    taken: list[list[tuple[float, float]]] = []
    placed: dict[int, int] = {}
    for index in order:
        left, right = spans[index]
        for row, intervals in enumerate(taken):
            if all(right + gap < a or left > b + gap for a, b in intervals):
                intervals.append((left, right))
                placed[index] = row
                break
        else:
            if len(taken) < rows:
                taken.append([(left, right)])
                placed[index] = len(taken) - 1
    return placed


def _label_markers(
    top: Any, labels: Sequence[_MarkerLabel], start: datetime, end: datetime, *,
    fontsize: float, axes_width_pt: float,
) -> None:
    """Name each line just above the top panel, in up to three non-overlapping rows.

    Full names when they fit; otherwise short ones ("CME 1330", "Shock"); and
    if even those collide, the least important names are left off (their lines
    stay). Text widths are estimated (the renderer is not known yet) at about
    0.55 em per character, which errs wide for these labels.
    """
    span = (end - start).total_seconds()
    if span <= 0 or not labels:
        return
    xs = [(label.time - start).total_seconds() / span * axes_width_pt for label in labels]
    order = sorted(range(len(labels)), key=lambda i: (_LABEL_PRIORITY.get(labels[i].kind, 4), xs[i]))

    def spans_for(texts: Sequence[str]) -> list[tuple[float, float]]:
        out = []
        for x, text in zip(xs, texts):
            width = 0.55 * fontsize * len(text) + 4.0
            out.append((x - width, x) if x + width > axes_width_pt else (x, x + width))
        return out

    texts = [label.text for label in labels]
    spans = spans_for(texts)
    placed = _place_labels(spans, order, rows=MARKER_LABEL_ROWS)
    if len(placed) < len(labels):
        texts = [label.short for label in labels]
        spans = spans_for(texts)
        placed = _place_labels(spans, order, rows=MARKER_LABEL_ROWS)
    for index, row in placed.items():
        label = labels[index]
        flip = spans[index][0] < xs[index]  # placed to the left of its line
        top.annotate(
            texts[index], xy=(label.time, 1.0), xycoords=("data", "axes fraction"),
            xytext=(0, 3 + row * (fontsize + 3.5)), textcoords="offset points",
            ha="right" if flip else "left", va="bottom", fontsize=fontsize, color=label.color,
            annotation_clip=False,
            arrowprops={
                "arrowstyle": "-", "color": label.color, "linewidth": 0.6, "shrinkA": 0, "shrinkB": 0,
                "relpos": (1.0, 0.0) if flip else (0.0, 0.0),
            },
        )


def draw_geomagnetic_panels(
    axes: Sequence[Any],
    context: Any,
    panels: Iterable[str],
    *,
    radio_interval: tuple[datetime, datetime] | None = None,
    markers: Sequence[Mapping[str, Any]] = (),
    origin: bool = False,
    show_event_line: bool = True,
    palette: GeoPalette | None = None,
) -> list[Any]:
    """Draw ``panels`` onto ``axes`` (``total_rows(panels)`` of them, sharing x).

    ``markers`` are ``{"time": datetime, "label": str, "color": str}`` vertical
    lines — DONKI shock arrivals and storms — drawn on every panel and named
    once above the top one; markers outside the plotted interval are left out.
    ``palette`` colours a screen rendering (ignored when ``origin``). Returns
    the axes, top to bottom.
    """
    palette = LIGHT_PALETTE if (origin or palette is None) else palette
    keys = normalize_panels(panels)
    axes = list(axes)
    if not origin:
        for ax in axes:
            _style_screen_axes(ax, palette)
    used: list[Any] = []
    cursor = 0
    for key in keys:
        rows = panel_row_count(key)
        group = axes[cursor : cursor + rows]
        cursor += rows
        if len(group) < rows:
            break
        if key == "dst":
            draw_dst(group[0], context, origin=origin, palette=palette)
        elif key == "kp":
            draw_kp(group[0], context, origin=origin, palette=palette)
        elif key == "imf":
            draw_imf(group[0], context, origin=origin, palette=palette)
        elif key == "plasma":
            draw_plasma(group, context, origin=origin, palette=palette)
        elif key == "derived":
            draw_derived(group, context, origin=origin, palette=palette)
        elif key == "indices":
            draw_indices(group[0], context, origin=origin, palette=palette)
        used.extend(group)

    if not used:
        return used
    start = getattr(context, "start", None)
    end = getattr(context, "end", None)
    event = getattr(context, "event_start", None)
    if event is not None and start is not None and end is not None and not (start <= event <= end):
        event = None
    visible = _visible_markers(markers, start, end)
    for ax in used:
        if radio_interval is not None and radio_interval[0] is not None and radio_interval[1] is not None:
            ax.axvspan(radio_interval[0], radio_interval[1], zorder=0.4, **RADIO_SPAN)
        if show_event_line and event is not None:
            ax.axvline(event, zorder=2.5, **EVENT_LINE)
        for marker in visible:
            ax.axvline(
                marker["time"], color=_readable(marker.get("color", "#1f77b4"), palette),
                linestyle=marker.get("linestyle", "-."), linewidth=0.9, zorder=2.5,
            )
        if start is not None and end is not None:
            ax.set_xlim(start, end)
        if origin:
            from src.backend.common.figure_export import style_origin_axes

            style_origin_axes(ax)
            twin = getattr(ax, "_geo_twin", None)
            if twin is not None:
                ax.tick_params(which="both", right=False)
                twin.tick_params(which="both", direction="in", left=False, right=True)
                twin.tick_params(which="major", length=6, width=1.5)
                twin.tick_params(which="minor", length=3, width=1.0)
    if start is not None and end is not None:
        items = [
            _MarkerLabel(
                marker["time"], str(marker["label"]),
                short_marker_label(str(marker.get("kind") or ""), str(marker["label"])),
                _readable(marker.get("color", "#1f77b4"), palette), str(marker.get("kind") or ""),
            )
            for marker in visible if marker.get("label")
        ]
        if show_event_line and event is not None:
            items.append(_MarkerLabel(event, f"Event {event:%b %d %H:%M}", "Event", EVENT_LINE["color"], "event"))
        figure = used[0].figure
        width_in = float(figure.get_size_inches()[0]) if figure is not None else 7.5
        _label_markers(
            used[0], items, start, end, fontsize=7.0 if origin else 7.5,
            axes_width_pt=max(144.0, width_in * 72.0 * 0.78),
        )
    date_axis(used[-1], start, end)
    for ax in used[:-1]:
        ax.tick_params(axis="x", which="both", labelbottom=False)
    if used[0].figure is not None:
        used[0].figure.align_ylabels(used)
    return used


def date_axis(ax: Any, start: datetime | None = None, end: datetime | None = None) -> None:
    """Day ticks labelled ``Apr 23``, 6- or 12-hour minors, the year in the label."""
    import matplotlib.dates as mdates

    days = (end - start).total_seconds() / 86400.0 if start is not None and end is not None else None
    if days is None or days < 2.0:
        locator = mdates.AutoDateLocator(minticks=4, maxticks=10)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax.set_xlabel("Time [UTC]")
        return
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, math.ceil(days / 10.0))))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    if days <= 10.0:
        ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=range(0, 24, 6)))
    elif days <= 20.0:
        ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=(0, 12)))
    years = f"{start:%Y}" if start.year == end.year else f"{start:%Y}–{end:%Y}"
    ax.set_xlabel(f"Time [UTC], {years}")


def tighten_layout(fig: Any) -> None:
    """Stack the panels closely, as space-weather overview plots usually are."""
    engine = fig.get_layout_engine()
    if engine is not None and hasattr(engine, "set"):
        try:
            engine.set(h_pad=0.02, hspace=0.0)
        except TypeError:
            pass


def geomagnetic_figure(
    context: Any,
    panels: Iterable[str] | None = None,
    *,
    radio_interval: tuple[datetime, datetime] | None = None,
    markers: Sequence[Mapping[str, Any]] = (),
    width_in: float = 7.5,
    row_height_in: float = 1.35,
    title: str = "",
) -> Any:
    """An OriginPro-style figure of the response (for the report and exports)."""
    from matplotlib.figure import Figure

    from src.backend.radio.radio_figures import origin_graph_style

    keys = normalize_panels(panels)
    rows = max(1, total_rows(keys))
    with origin_graph_style():
        fig = Figure(figsize=(width_in, 0.9 + row_height_in * rows), dpi=100, layout="constrained")
        tighten_layout(fig)
        axes = fig.subplots(rows, 1, sharex=True, squeeze=False)[:, 0]
        draw_geomagnetic_panels(axes, context, keys, radio_interval=radio_interval, markers=markers, origin=True)
        if title:
            axes[0].set_title(title, pad=40)
    return fig
