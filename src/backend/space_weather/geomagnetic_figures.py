"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Stacked geomagnetic-response panels (src/backend/space_weather/geomagnetic_figures.py).

One drawer for every place the response is shown: the Geomagnetic Response
window draws onto its canvas with it, the project report and the event-figure
composer build OriginPro-style figures from it. Qt-free.

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
"""

from __future__ import annotations

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

#: Line colours, chosen to stay apart in print and for colour-blind readers.
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

EVENT_LINE = {"color": "#d62728", "linestyle": "--", "linewidth": 1.1}
RADIO_SPAN = {"facecolor": "#9467bd", "alpha": 0.18, "linewidth": 0.0}


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


def _plot_line(ax: Any, context: Any, name: str, *, label: str | None = None, step: bool = False, **kw: Any) -> bool:
    series = _series(context, name)
    if series is None or not series.finite():
        return False
    x = _dates(series.times)
    y = np.asarray(series.values, dtype=float)
    kw.setdefault("color", COLORS.get(name, "#000000"))
    kw.setdefault("linewidth", 1.0)
    text = label if label is not None else series.label
    if step:
        ax.step(x, y, where="post", label=text, **kw)
    else:
        ax.plot(x, y, label=text, **kw)
    return True


def _label(ax: Any, text: str, units: str) -> None:
    ax.set_ylabel(f"{text} [{units}]" if units else text)


def _no_data(ax: Any, text: str) -> None:
    ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center", va="center", fontsize=8, color="#666666")


def _legend(ax: Any, *, origin: bool) -> None:
    handles, labels = ax.get_legend_handles_labels()
    if len(handles) < 2:
        return
    if origin:
        from src.backend.common.figure_export import origin_legend

        origin_legend(ax, loc="upper right", fontsize=7, ncol=len(handles))
    else:
        ax.legend(loc="upper right", fontsize=7, ncol=len(handles), framealpha=0.6)


def _twin(ax: Any, *, origin: bool) -> Any:
    twin = ax.twinx()
    if origin:
        twin.tick_params(which="both", direction="in")
    return twin


def draw_dst(ax: Any, context: Any, *, origin: bool = False) -> None:
    drawn = _plot_line(ax, context, "dst", step=True, linewidth=1.3)
    drawn |= _plot_line(ax, context, "symh", linewidth=0.8, alpha=0.9)
    ax.axhline(0.0, color="#888888", linewidth=0.6)
    for level in (-50.0, -100.0, -250.0):
        ax.axhline(level, color="#bbbbbb", linewidth=0.5, linestyle=":")
    _label(ax, "Dst, SYM-H", "nT")
    if not drawn:
        _no_data(ax, "Dst / SYM-H unavailable")
    _legend(ax, origin=origin)


def draw_kp(ax: Any, context: Any, *, origin: bool = False) -> None:
    from src.backend.space_weather.geomagnetic_context import g_level

    series = _series(context, "kp")
    if series is None or not series.finite():
        _no_data(ax, "Kp unavailable")
    else:
        x = _dates(series.times)
        y = np.asarray(series.values, dtype=float)
        colors = [KP_COLORS[g_level(value)] if np.isfinite(value) else "#cccccc" for value in y]
        # Three-hour bars, drawn from the interval start.
        width = np.timedelta64(3, "h") / np.timedelta64(1, "D") * 0.92
        ax.bar(x, np.nan_to_num(y), width=width, align="edge", color=colors, edgecolor="none")
    ax.set_ylim(0, 9)
    ax.set_yticks([0, 3, 5, 7, 9])
    for level in (5, 7):
        ax.axhline(level, color="#bbbbbb", linewidth=0.5, linestyle=":")
    _label(ax, "Kp", "")


def draw_imf(ax: Any, context: Any, *, origin: bool = False) -> None:
    drawn = False
    for name, label in (("bt", "Bt"), ("bz", "Bz"), ("by", "By")):
        drawn |= _plot_line(ax, context, name, label=label)
    ax.axhline(0.0, color="#888888", linewidth=0.6)
    _label(ax, "IMF (GSM)", "nT")
    if not drawn:
        _no_data(ax, "IMF unavailable")
    _legend(ax, origin=origin)


def draw_plasma(axes: Sequence[Any], context: Any, *, origin: bool = False) -> None:
    top, bottom = axes
    if not _plot_line(top, context, "v", label="V"):
        _no_data(top, "Solar wind speed unavailable")
    _label(top, "V", "km/s")
    twin = _twin(top, origin=origin)
    _plot_line(twin, context, "np", label="Np", linewidth=0.8)
    twin.set_ylabel("Np [cm⁻³]", color=COLORS["np"])
    twin.tick_params(axis="y", colors=COLORS["np"])

    if _plot_line(bottom, context, "t", label="T"):
        bottom.set_yscale("log")
    else:
        _no_data(bottom, "Temperature unavailable")
    _label(bottom, "T", "K")
    twin = _twin(bottom, origin=origin)
    _plot_line(twin, context, "pdyn", label="Pdyn", linewidth=0.8)
    twin.set_ylabel("Pdyn [nPa]", color=COLORS["pdyn"])
    twin.tick_params(axis="y", colors=COLORS["pdyn"])


def draw_derived(axes: Sequence[Any], context: Any, *, origin: bool = False) -> None:
    top, bottom = axes
    if not _plot_line(top, context, "ey", label="Ey"):
        _no_data(top, "Ey unavailable")
    top.axhline(0.0, color="#888888", linewidth=0.6)
    _label(top, "Ey", "mV/m")

    if _plot_line(bottom, context, "beta", label="β"):
        bottom.set_yscale("log")
    else:
        _no_data(bottom, "β unavailable")
    _label(bottom, "β", "")
    twin = _twin(bottom, origin=origin)
    _plot_line(twin, context, "mach", label="Mach", linewidth=0.8)
    twin.set_ylabel("Alfvén Mach", color=COLORS["mach"])
    twin.tick_params(axis="y", colors=COLORS["mach"])


def draw_indices(ax: Any, context: Any, *, origin: bool = False) -> None:
    drawn = False
    for name in ("ae", "al", "au"):
        drawn |= _plot_line(ax, context, name, label=name.upper(), linewidth=0.8)
    ax.axhline(0.0, color="#888888", linewidth=0.6)
    _label(ax, "AE / AL / AU", "nT")
    if not drawn:
        _no_data(ax, "AE not yet published")
    _legend(ax, origin=origin)


def draw_geomagnetic_panels(
    axes: Sequence[Any],
    context: Any,
    panels: Iterable[str],
    *,
    radio_interval: tuple[datetime, datetime] | None = None,
    markers: Sequence[Mapping[str, Any]] = (),
    origin: bool = False,
    show_event_line: bool = True,
) -> list[Any]:
    """Draw ``panels`` onto ``axes`` (``total_rows(panels)`` of them, sharing x).

    ``markers`` are ``{"time": datetime, "label": str, "color": str}`` vertical
    lines — DONKI shock arrivals and storms — drawn on every panel and named
    once on the top one. Returns the axes, top to bottom.
    """
    keys = normalize_panels(panels)
    axes = list(axes)
    used: list[Any] = []
    cursor = 0
    for key in keys:
        rows = panel_row_count(key)
        group = axes[cursor : cursor + rows]
        cursor += rows
        if len(group) < rows:
            break
        if key == "dst":
            draw_dst(group[0], context, origin=origin)
        elif key == "kp":
            draw_kp(group[0], context, origin=origin)
        elif key == "imf":
            draw_imf(group[0], context, origin=origin)
        elif key == "plasma":
            draw_plasma(group, context, origin=origin)
        elif key == "derived":
            draw_derived(group, context, origin=origin)
        elif key == "indices":
            draw_indices(group[0], context, origin=origin)
        used.extend(group)

    if not used:
        return used
    start = getattr(context, "start", None)
    end = getattr(context, "end", None)
    event = getattr(context, "event_start", None)
    for ax in used:
        if radio_interval is not None and radio_interval[0] is not None and radio_interval[1] is not None:
            ax.axvspan(radio_interval[0], radio_interval[1], **RADIO_SPAN)
        if show_event_line and event is not None:
            ax.axvline(event, **EVENT_LINE)
        for marker in markers:
            when = marker.get("time")
            if when is None:
                continue
            ax.axvline(when, color=marker.get("color", "#1f77b4"), linestyle=marker.get("linestyle", "-."), linewidth=0.9)
        if start is not None and end is not None:
            ax.set_xlim(start, end)
        if origin:
            from src.backend.common.figure_export import style_origin_axes

            style_origin_axes(ax)
    top = used[0]
    for marker in markers:
        when = marker.get("time")
        if when is None or not marker.get("label"):
            continue
        top.annotate(
            str(marker["label"]), xy=(when, 1.0), xycoords=("data", "axes fraction"),
            xytext=(2, 2), textcoords="offset points", fontsize=6, rotation=90,
            va="bottom", ha="left", color=marker.get("color", "#1f77b4"), annotation_clip=False,
        )
    date_axis(used[-1])
    for ax in used[:-1]:
        ax.tick_params(axis="x", which="both", labelbottom=False)
    return used


def date_axis(ax: Any) -> None:
    """Day ticks with ``MM-DD`` labels, 6-hour minors, ``UTC`` in the label."""
    import matplotlib.dates as mdates

    locator = mdates.AutoDateLocator(minticks=4, maxticks=10)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.set_xlabel("Time [UTC]")


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
        fig = Figure(figsize=(width_in, 0.8 + row_height_in * rows), dpi=100, layout="constrained")
        axes = fig.subplots(rows, 1, sharex=True, squeeze=False)[:, 0]
        draw_geomagnetic_panels(axes, context, keys, radio_interval=radio_interval, markers=markers, origin=True)
        if title:
            axes[0].set_title(title)
    return fig
