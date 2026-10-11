"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Publication event figures (src/backend/common/event_figure.py).

One figure for a whole event, in the OriginPro style of the other exports:

* a row of **thumbnails** (EUV or coronagraph images at chosen times), with a
  dashed line from each down through the panels at its time;
* the **event block**: stacked panels on one UT axis, in the order and with the
  height ratios the user chose — the CALLISTO dynamic spectrum, STEREO/SWAVES,
  GOES X-ray flux, GOES proton (SEP) flux and the event-catalog lanes;
* an optional **geomagnetic block** with its own, days-long UT axis — Dst,
  Kp, the IMF, the solar wind — with the radio interval shaded and DONKI shock
  arrivals and storms marked.

The page is a journal column width: 8.8 cm (one column), 12 cm, 18 cm (two
columns) or a custom size; the font scales with it. :class:`EventFigureSpec`
is the composer's state (and the project's ``composer`` part);
:class:`EventFigureData` is what the main window hands over to draw it.
Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from src.backend.radio.radio_figures import (
    EventMarkers,
    GraphText,
    SpectrumGraph,
    SwavesPanel,
    draw_callisto_panel,
    draw_event_strip,
    draw_goes_flux,
    draw_swaves_panel,
    origin_graph_style,
    ut_time_axis,
)

CM_PER_INCH = 2.54

#: Journal widths in centimetres.
SIZE_PRESETS: dict[str, float] = {"8.8 cm (one column)": 8.8, "12 cm": 12.0, "18 cm (two columns)": 18.0}
CUSTOM_SIZE = "Custom"

PANEL_LABELS: dict[str, str] = {
    "spectrum": "CALLISTO dynamic spectrum",
    "swaves": "STEREO/SWAVES",
    "goes": "GOES X-ray flux",
    "sep": "GOES proton flux (SEP)",
    "events": "Event catalogs",
}
PANEL_KEYS = tuple(PANEL_LABELS)
DEFAULT_RATIOS = {"spectrum": 3.0, "swaves": 2.0, "goes": 1.3, "sep": 1.3, "events": 0.7}
DEFAULT_ENABLED = ("spectrum", "goes", "events")

#: Inches of height per unit of panel height ratio, per inch of page width.
_UNIT_PER_WIDTH = 0.12


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


# --------------------------------------------------------------------- spec


@dataclass
class PanelSpec:
    key: str
    enabled: bool = True
    height_ratio: float = 1.0


@dataclass
class ThumbnailSpec:
    time: datetime
    #: A ``helioviewer.THUMBNAIL_SOURCES`` label ("AIA 193", "LASCO C2", "COR2-A").
    source: str


@dataclass
class GeoBlockSpec:
    enabled: bool = False
    panels: tuple[str, ...] = ("dst", "kp", "imf")
    height_ratio: float = 1.0


def default_panels() -> list[PanelSpec]:
    return [PanelSpec(key, key in DEFAULT_ENABLED, DEFAULT_RATIOS[key]) for key in PANEL_KEYS]


@dataclass
class EventFigureSpec:
    panels: list[PanelSpec] = field(default_factory=default_panels)
    #: UT range of the event block; None means the radio data's range.
    start: datetime | None = None
    end: datetime | None = None
    thumbnails: list[ThumbnailSpec] = field(default_factory=list)
    geomagnetic: GeoBlockSpec = field(default_factory=GeoBlockSpec)
    size_preset: str = "18 cm (two columns)"
    custom_width_cm: float = 18.0
    #: None: the height follows from the panels.
    custom_height_cm: float | None = None
    font_scale: float = 1.0
    title: str = ""

    def width_cm(self) -> float:
        if self.size_preset in SIZE_PRESETS:
            return SIZE_PRESETS[self.size_preset]
        return max(4.0, float(self.custom_width_cm))

    def width_in(self) -> float:
        return self.width_cm() / CM_PER_INCH

    def enabled_panels(self) -> list[PanelSpec]:
        return [panel for panel in self.panels if panel.enabled and panel.key in PANEL_LABELS]

    def to_json(self) -> dict[str, Any]:
        return {
            "panels": [{"key": p.key, "enabled": bool(p.enabled), "height_ratio": float(p.height_ratio)} for p in self.panels],
            "start": _iso(self.start),
            "end": _iso(self.end),
            "thumbnails": [{"time": _iso(t.time), "source": t.source} for t in self.thumbnails],
            "geomagnetic": {
                "enabled": bool(self.geomagnetic.enabled),
                "panels": list(self.geomagnetic.panels),
                "height_ratio": float(self.geomagnetic.height_ratio),
            },
            "size_preset": self.size_preset,
            "custom_width_cm": float(self.custom_width_cm),
            "custom_height_cm": None if self.custom_height_cm is None else float(self.custom_height_cm),
            "font_scale": float(self.font_scale),
            "title": self.title,
        }

    @classmethod
    def from_json(cls, raw: Any) -> "EventFigureSpec":
        spec = cls()
        if not isinstance(raw, Mapping):
            return spec
        panels = []
        seen = set()
        for row in raw.get("panels") or []:
            if not isinstance(row, Mapping) or row.get("key") not in PANEL_LABELS or row.get("key") in seen:
                continue
            seen.add(row["key"])
            try:
                ratio = max(0.1, float(row.get("height_ratio") or DEFAULT_RATIOS[row["key"]]))
            except (TypeError, ValueError):
                ratio = DEFAULT_RATIOS[row["key"]]
            panels.append(PanelSpec(str(row["key"]), bool(row.get("enabled", True)), ratio))
        panels += [PanelSpec(key, False, DEFAULT_RATIOS[key]) for key in PANEL_KEYS if key not in seen]
        spec.panels = panels
        spec.start, spec.end = _parse(raw.get("start")), _parse(raw.get("end"))
        spec.thumbnails = [
            ThumbnailSpec(when, str(row.get("source") or ""))
            for row in raw.get("thumbnails") or []
            if isinstance(row, Mapping) and (when := _parse(row.get("time"))) is not None
        ]
        geo = raw.get("geomagnetic") or {}
        spec.geomagnetic = GeoBlockSpec(
            bool(geo.get("enabled", False)), tuple(str(k) for k in geo.get("panels") or ("dst", "kp", "imf")),
            float(geo.get("height_ratio") or 1.0),
        )
        spec.size_preset = str(raw.get("size_preset") or spec.size_preset)
        for key in ("custom_width_cm", "font_scale"):
            try:
                setattr(spec, key, float(raw.get(key) or getattr(spec, key)))
            except (TypeError, ValueError):
                pass
        try:
            height = raw.get("custom_height_cm")
            spec.custom_height_cm = float(height) if height not in (None, "") else None
        except (TypeError, ValueError):
            spec.custom_height_cm = None
        spec.title = str(raw.get("title") or "")
        return spec


# --------------------------------------------------------------------- data


@dataclass
class ThumbnailImage:
    """One image for the thumbnail row: 2-D data (with levels/cmap) or RGB(A)."""

    time: datetime
    label: str
    image: Any
    #: "loaded" (a processed frame from the Solar or GCS window) or "helioviewer".
    source: str = "loaded"
    cmap: Any = "gray"
    levels: tuple[float, float] | None = None


@dataclass
class SepSeries:
    times: Sequence[datetime]
    low: Sequence[float]
    high: Sequence[float]
    low_label: str = "P(low)"
    high_label: str = "P(high)"
    units: str = "pfu"
    spacecraft: str = ""


@dataclass
class EventFigureData:
    """What the main window has, for :func:`build_event_figure` to draw."""

    #: UTC of x = 0 on the event block's axis (the spectrum's time origin).
    base_utc: datetime | None = None
    spectrum: SpectrumGraph | None = None
    swaves: SwavesPanel | None = None
    #: GOES channels as ``(label, x_seconds, flux_wm2)``.
    goes: Sequence[tuple[str, Any, Any]] = ()
    sep: SepSeries | None = None
    events: EventMarkers | None = None
    geomagnetic: Any = None
    geo_markers: Sequence[Mapping[str, Any]] = ()
    radio_interval: tuple[datetime, datetime] | None = None
    thumbnails: Sequence[ThumbnailImage] = ()
    #: The radio data's x range, used when the spec gives no time range.
    x_range: tuple[float, float] | None = None


# ------------------------------------------------------------------- drawing


def _no_data(ax: Any, text: str) -> None:
    ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center", va="center", fontsize=7, color="#666666")


def draw_sep_flux(ax: Any, sep: SepSeries, base_utc: datetime, text: GraphText) -> bool:
    """GOES proton flux on a log axis, with the S1 storm threshold (10 pfu, ≥10 MeV)."""
    from src.backend.common.figure_export import ORIGIN_COLORS, origin_legend, style_origin_axes

    base = base_utc
    x = np.array([(t - base).total_seconds() for t in sep.times], dtype=float)
    drawn = False
    for index, (values, label) in enumerate(((sep.low, sep.low_label), (sep.high, sep.high_label))):
        y = np.asarray(values, dtype=float)
        keep = np.isfinite(x) & np.isfinite(y) & (y > 0)
        if keep.any():
            ax.plot(x[keep], y[keep], color=ORIGIN_COLORS[index % len(ORIGIN_COLORS)], linewidth=1.2, label=label)
            drawn = True
    if drawn:
        ax.set_yscale("log")
        ax.axhline(10.0, color="#999999", linewidth=0.7, linestyle=":")
        origin_legend(ax, loc="upper left", fontsize=max(5.0, text.tick_size - 2))
    style_origin_axes(ax)
    ax.set_ylabel(f"Protons [{sep.units or 'pfu'}]", **text.label_kw())
    return drawn


def _x_range(spec: EventFigureSpec, data: EventFigureData) -> tuple[float, float] | None:
    if spec.start is not None and spec.end is not None and data.base_utc is not None and spec.end > spec.start:
        return ((spec.start - data.base_utc).total_seconds(), (spec.end - data.base_utc).total_seconds())
    if data.x_range is not None:
        return data.x_range
    if data.spectrum is not None and data.spectrum.image is not None:
        x0, x1 = float(data.spectrum.image.extent[0]), float(data.spectrum.image.extent[1])
        return (min(x0, x1), max(x0, x1))
    return None


def _ut_start(base_utc: datetime) -> float:
    return base_utc.hour * 3600.0 + base_utc.minute * 60.0 + base_utc.second + base_utc.microsecond / 1e6


def figure_size_in(spec: EventFigureSpec, data: EventFigureData | None = None) -> tuple[float, float]:
    """The page size in inches for ``spec`` (and how many thumbnails ``data`` has)."""
    width = spec.width_in()
    if spec.custom_height_cm:
        return width, float(spec.custom_height_cm) / CM_PER_INCH
    unit = width * _UNIT_PER_WIDTH
    panels = spec.enabled_panels()
    height = 0.35 + sum(p.height_ratio for p in panels) * unit
    thumbs = len(data.thumbnails) if data is not None else len(spec.thumbnails)
    if thumbs:
        height += width / max(4, thumbs) + 0.25
    if spec.geomagnetic.enabled:
        from src.backend.space_weather.geomagnetic_figures import total_rows

        height += 0.45 + total_rows(spec.geomagnetic.panels) * unit * spec.geomagnetic.height_ratio
    if spec.title:
        height += 0.2
    return width, max(height, 1.5)


def build_event_figure(spec: EventFigureSpec, data: EventFigureData) -> Any:
    """The whole event as one OriginPro-style figure (see the module docstring)."""
    from matplotlib.figure import Figure
    from matplotlib.patches import ConnectionPatch

    from src.backend.space_weather.geomagnetic_figures import draw_geomagnetic_panels, normalize_panels, total_rows

    scale = max(0.4, float(spec.font_scale)) * min(1.0, 0.55 + spec.width_cm() / 36.0)
    text = GraphText(tick_size=8.0 * scale, label_size=8.5 * scale, title_size=9.5 * scale)
    panels = spec.enabled_panels()
    thumbs = sorted(data.thumbnails, key=lambda item: item.time)
    geo_keys = normalize_panels(spec.geomagnetic.panels) if spec.geomagnetic.enabled else []
    geo_rows = total_rows(geo_keys) if geo_keys else 0
    width, height = figure_size_in(spec, data)

    with origin_graph_style(text):
        fig = Figure(figsize=(width, height), dpi=100, layout="constrained")
        blocks: list[tuple[str, float]] = []
        unit = width * _UNIT_PER_WIDTH
        if thumbs:
            blocks.append(("thumbs", width / max(4, len(thumbs))))
        if panels:
            blocks.append(("event", sum(p.height_ratio for p in panels) * unit))
        if geo_rows:
            blocks.append(("geo", geo_rows * unit * spec.geomagnetic.height_ratio + 0.2))
        if not blocks:
            ax = fig.add_subplot(1, 1, 1)
            ax.set_axis_off()
            _no_data(ax, "Choose at least one panel.")
            return fig
        outer = fig.add_gridspec(len(blocks), 1, height_ratios=[h for _name, h in blocks])

        thumb_axes: list[Any] = []
        event_axes: list[Any] = []
        event_keys: list[str] = []
        for index, (name, _h) in enumerate(blocks):
            cell = outer[index, 0]
            if name == "thumbs":
                grid = cell.subgridspec(1, len(thumbs), wspace=0.02)
                for column, thumb in enumerate(thumbs):
                    ax = fig.add_subplot(grid[0, column])
                    _draw_thumbnail(ax, thumb, text)
                    thumb_axes.append(ax)
            elif name == "event":
                grid = cell.subgridspec(len(panels), 1, height_ratios=[p.height_ratio for p in panels], hspace=0.04)
                first = None
                for row, panel in enumerate(panels):
                    ax = fig.add_subplot(grid[row, 0], sharex=first)
                    first = first or ax
                    if panel.key != "events":
                        _draw_event_panel(fig, ax, panel.key, data, text)
                    event_axes.append(ax)
                    event_keys.append(panel.key)
            elif name == "geo":
                grid = cell.subgridspec(geo_rows, 1, hspace=0.05)
                first = None
                axes = []
                for row in range(geo_rows):
                    ax = fig.add_subplot(grid[row, 0], sharex=first)
                    first = first or ax
                    axes.append(ax)
                draw_geomagnetic_panels(
                    axes, data.geomagnetic, geo_keys, radio_interval=data.radio_interval,
                    markers=list(data.geo_markers), origin=True,
                )

        x_range = _x_range(spec, data)
        if event_axes:
            if x_range is not None:
                event_axes[0].set_xlim(*x_range)
            # The catalog lanes last: their labels are placed against the final range.
            for ax, key in zip(event_axes, event_keys):
                if key == "events":
                    _draw_event_panel(fig, ax, key, data, text)
            for ax in event_axes[:-1]:
                ax.tick_params(axis="x", which="both", labelbottom=False)
                ax.set_xlabel("")
            bottom = event_axes[-1]
            # Whichever panel ends up at the bottom carries the time labels,
            # including the catalog lanes, which hide them when drawn alone.
            bottom.tick_params(axis="x", which="both", labelbottom=True)
            if data.base_utc is not None:
                for ax in event_axes:
                    ut_time_axis(ax, _ut_start(data.base_utc))
                bottom.set_xlabel(f"Time [UT] on {data.base_utc:%Y-%m-%d}", **text.label_kw())
            else:
                bottom.set_xlabel("Time [s]", **text.label_kw())

        # Each thumbnail's time, as a dashed line through the event panels and a
        # connector from the image down to the top panel.
        if thumbs and event_axes and data.base_utc is not None:
            top = event_axes[0]
            for ax_thumb, thumb in zip(thumb_axes, thumbs):
                x = (thumb.time - data.base_utc).total_seconds()
                if x_range is not None and not (x_range[0] <= x <= x_range[1]):
                    continue
                for ax in event_axes:
                    ax.axvline(x, color="#333333", linestyle="--", linewidth=0.7, zorder=8)
                connector = ConnectionPatch(
                    xyA=(0.5, 0.0), coordsA=ax_thumb.transAxes,
                    xyB=(x, 1.0), coordsB=top.get_xaxis_transform(),
                    color="#333333", linestyle="--", linewidth=0.7,
                )
                connector.set_in_layout(False)
                fig.add_artist(connector)

        if spec.title:
            fig.suptitle(spec.title, fontsize=text.title_size)
    return fig


def _draw_thumbnail(ax: Any, thumb: ThumbnailImage, text: GraphText) -> None:
    image = np.asarray(thumb.image)
    kwargs: dict[str, Any] = {"origin": "upper" if thumb.source == "helioviewer" else "lower"}
    if image.ndim == 2:
        kwargs["cmap"] = thumb.cmap
        if thumb.levels is not None:
            kwargs["vmin"], kwargs["vmax"] = thumb.levels
    ax.imshow(image, **kwargs)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor("#000000")
        spine.set_linewidth(0.8)
    ax.set_title(f"{thumb.label}\n{thumb.time:%H:%M:%S} UT", fontsize=max(5.0, text.tick_size - 1.5))


def _draw_event_panel(fig: Any, ax: Any, key: str, data: EventFigureData, text: GraphText) -> None:
    from src.backend.common.figure_export import style_origin_axes

    if key == "spectrum":
        if data.spectrum is not None and data.spectrum.image is not None:
            graph = data.spectrum
            draw_callisto_panel(fig, ax, replace(graph, text=text))
            return
        _no_data(ax, "No CALLISTO spectrum loaded")
    elif key == "swaves":
        if data.swaves is not None:
            draw_swaves_panel(fig, ax, data.swaves, text, standalone=False)
            return
        _no_data(ax, "No STEREO/SWAVES data loaded")
    elif key == "goes":
        if data.goes and draw_goes_flux(ax, data.goes, text):
            ax.set_ylabel("Flux [W m⁻²]", **text.label_kw())
            return
        _no_data(ax, "No GOES X-ray data")
    elif key == "sep":
        if data.sep is not None and data.base_utc is not None and draw_sep_flux(ax, data.sep, data.base_utc, text):
            return
        _no_data(ax, "No GOES proton data")
    elif key == "events":
        if data.events is not None and data.events.lanes:
            draw_event_strip(ax, data.events, text)
            return
        _no_data(ax, "No event catalogs loaded")
    style_origin_axes(ax)
    ax.set_yticks([])


def auto_thumbnail_times(start: datetime, end: datetime, count: int = 3) -> list[datetime]:
    """``count`` times spread across ``[start, end]``, on whole minutes."""
    if end <= start or count <= 0:
        return []
    step = (end - start) / (count + 1)
    out = []
    for index in range(1, count + 1):
        when = start + step * index
        out.append(when.replace(second=0, microsecond=0))
    return out
