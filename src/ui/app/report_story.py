"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The project report's event story, gathered from the main window (src/ui/app/report_story.py).

Three steps, so the GUI never waits on the network and matplotlib only ever
draws on the GUI thread:

1. :func:`story_request` (GUI thread) reads what the window already holds —
   the radio interval and its analysis, the event-context catalogs, the
   geomagnetic response, a GCS reconstruction — and notes what is missing.
2. :class:`EventStoryFetchWorker` (its own thread) fetches the missing catalogs
   and geomagnetic response through the usual caches, unless fetching is off.
3. :func:`story_section` (GUI thread) tells the story and renders its figures:
   the event chain, the event overview and the geomagnetic response.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from src.backend.session.event_story import EventStory, GCSKinematics, RadioSummary, build_event_story, event_chain_figure
from src.backend.session.project_report import ProjectReportFigure
from src.backend.space_weather.event_catalogs import SOURCES, CatalogCancelled, EventContextResult, load_event_context
from src.backend.space_weather.geomagnetic_context import GeomagneticCancelled, load_geomagnetic_context

#: A geomagnetic response loaded for an event this far from the radio start is another event's.
SAME_EVENT = timedelta(hours=6)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


@dataclass
class StoryRequest:
    """What the main window holds for the story, and what still has to be fetched."""

    radio: RadioSummary | None
    anchor: datetime | None
    catalog: EventContextResult | None = None
    geomagnetic: Any = None
    markers: list[dict[str, Any]] = field(default_factory=list)
    gcs: GCSKinematics | None = None
    radio_interval: tuple[datetime, datetime] | None = None
    #: The interval the catalogs are looked up for: the radio data's, or for a
    #: project without radio data the Solar Image Analysis frames' or GCS event's.
    catalog_interval: tuple[datetime, datetime] | None = None
    event_label: str = ""
    #: Catalog sources to fetch (none when fetching is off or nothing is missing).
    missing_sources: tuple[str, ...] = ()
    fetch_geomagnetic: bool = False
    cache_dir: Path | None = None
    #: Why parts of the story will be missing, for the report.
    notes: list[str] = field(default_factory=list)

    def needs_fetch(self) -> bool:
        return bool(self.missing_sources) or self.fetch_geomagnetic


@dataclass
class StoryFetchResult:
    catalog: EventContextResult | None = None
    geomagnetic: Any = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 1. request


def _radio_summary(main: Any, session: dict | None) -> RadioSummary | None:
    try:
        ctx = main._goes_overlay_request_context()
    except Exception:
        ctx = None
    if not ctx:
        return None
    analyzer = dict((session or {}).get("analyzer") or {})
    shock = dict(analyzer.get("shock_summary") or {})
    type_ii = dict(dict((session or {}).get("type_ii") or {}).get("results") or {})

    def number(mapping: dict, key: str) -> float | None:
        try:
            value = float(mapping.get(key))
        except (TypeError, ValueError):
            return None
        return value if value == value and abs(value) != float("inf") else None

    try:
        station = str(main._analysis_station_name() or "")
    except Exception:
        station = ""
    return RadioSummary(
        start=_naive_utc(ctx["start_utc"]),
        end=_naive_utc(ctx["end_utc"]),
        station=station,
        label=str(getattr(main, "filename", "") or ""),
        shock_speed_km_s=number(shock, "avg_shock_speed_km_s"),
        initial_shock_speed_km_s=number(shock, "initial_shock_speed_km_s"),
        shock_height_rs=number(shock, "avg_shock_height_rs"),
        drift_mhz_s=number(shock, "avg_drift_mhz_s"),
        alfven_mach=number(type_ii, "alfven_mach_number"),
        magnetic_field_g=number(type_ii, "magnetic_field_g"),
    )


def gcs_kinematics(main: Any) -> GCSKinematics | None:
    """The GCS window's recorded fits as one reconstruction (direction of the last, speed of all)."""
    window = getattr(main, "_gcs_window", None)
    try:
        fits = dict(getattr(window, "_fits", {}) or {}) if window is not None else {}
    except RuntimeError:
        return None
    if not fits:
        return None
    times = sorted(fits)
    last = fits[times[-1]]
    heights = [float(fits[when].apex_height_rsun) for when in times]
    speed = None
    if len(times) >= 2:
        try:
            from src.backend.gcs.gcs_figures import HeightTimeSeries, fit_series

            fit = fit_series(HeightTimeSeries.build("GCS", times, heights, order=1))
            speed = float(fit.speed_km_s) if fit is not None and fit.speed_km_s == fit.speed_km_s else None
        except Exception:
            speed = None
    return GCSKinematics(
        times=tuple(_naive_utc(t) for t in times),
        heights_rsun=tuple(heights),
        lon_deg=float(last.lon_deg),
        lat_deg=float(last.lat_deg),
        tilt_deg=float(getattr(last, "tilt_deg", float("nan"))),
        speed_km_s=speed,
    )


def window_interval(main: Any) -> tuple[tuple[datetime, datetime], str] | None:
    """``((start, end), label)`` of the Solar Image Analysis frames, else the GCS event; None without either."""
    from src.backend.solar.solar_data_analysis import frame_observation_time

    solar = getattr(main, "_solar_data_analysis_window", None)
    try:
        frames = list(getattr(solar, "_map_frames", []) or []) if solar is not None else []
    except RuntimeError:
        frames = []
    times = []
    for frame in frames:
        try:
            when = frame_observation_time(frame)
        except Exception:
            when = None
        if isinstance(when, datetime):
            times.append(_naive_utc(when))
    if times:
        return (min(times), max(times)), "Solar Image Analysis"
    window = getattr(main, "_gcs_window", None)
    try:
        if window is not None and window.project_has_content():
            fits = sorted(getattr(window, "_fits", {}) or {})
            start, end = (fits[0], fits[-1]) if fits else window.event_range()
            return (_naive_utc(start), _naive_utc(end)), "GCS CME fitting"
    except RuntimeError:
        return None
    return None


def story_request(main: Any, session: dict | None) -> StoryRequest | None:
    """Everything the main window holds for the story (GUI thread); None without an event.

    The story is anchored on the radio data's start; a project without radio
    data is anchored on its Solar Image Analysis frames or its GCS event.
    """
    from src.ui.app.event_context_strip import EventContextController
    from src.ui.space_weather.geomagnetic_controller import GeomagneticController, space_weather_cache_dir

    radio = _radio_summary(main, session)
    controller = getattr(main, "_geomagnetic", None)
    context = getattr(controller, "context", None)
    interval, label = ((radio.start, radio.end), f"FITS: {radio.label}" if radio.label else "Project report") \
        if radio is not None else (window_interval(main) or (None, "Project report"))
    anchor = interval[0] if interval is not None else _naive_utc(getattr(context, "event_start", None))
    if anchor is None:
        return None
    request = StoryRequest(radio=radio, anchor=anchor, gcs=gcs_kinematics(main), cache_dir=space_weather_cache_dir())
    request.event_label = label
    request.radio_interval = (radio.start, radio.end) if radio is not None else None
    request.catalog_interval = interval

    # Catalogs: the strip's result, if it covers the interval; the rest is fetched.
    strip = getattr(main, "_event_context", None)
    result = getattr(strip, "result", None)
    if result is not None and interval is not None and not (result.start <= interval[0] and result.end >= interval[1]):
        result = None
    request.catalog = result
    wanted = tuple(s for s in SOURCES if result is None or s not in result.events)
    if wanted and interval is not None:
        if EventContextController.fetching_allowed():
            request.missing_sources = wanted
        else:
            request.notes.append("Event catalogs were not loaded and automatic fetching is off.")

    # Geomagnetic response: the card's, if it is for this event.
    if context is not None and abs((_naive_utc(context.event_start) - anchor).total_seconds()) <= SAME_EVENT.total_seconds():
        request.geomagnetic = context
        request.markers = list(controller.all_markers())
    elif GeomagneticController.fetching_allowed():
        request.fetch_geomagnetic = True
    else:
        request.notes.append("The geomagnetic response was not loaded and automatic fetching is off.")
    return request


# ----------------------------------------------------------------- 2. fetching


def _merge(old: EventContextResult | None, new: EventContextResult | None) -> EventContextResult | None:
    if old is None or new is None:
        return new or old
    return EventContextResult(
        min(old.start, new.start), max(old.end, new.end),
        events={**old.events, **new.events}, errors={**old.errors, **new.errors},
    )


class EventStoryFetchWorker(QObject):
    """Fetches the story's missing catalogs and geomagnetic response off the GUI thread."""

    progress = Signal(int, str)
    finished = Signal(object)
    cancelled = Signal()

    def __init__(self, request: StoryRequest):
        super().__init__()
        self.request = request
        self._cancel = threading.Event()

    @Slot()
    def request_cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        request = self.request
        out = StoryFetchResult()
        try:
            if request.missing_sources and request.catalog_interval is not None:
                self.progress.emit(2, "Fetching event catalogs (flares, CMEs, DONKI, radio bursts)...")
                try:
                    fetched = load_event_context(
                        *request.catalog_interval, sources=request.missing_sources,
                        cache_dir=request.cache_dir, cancel_cb=self._cancel.is_set,
                    )
                    out.catalog = _merge(request.catalog, fetched)
                    for source, message in fetched.errors.items():
                        out.notes.append(f"{source}: {message}")
                except CatalogCancelled:
                    raise
                except Exception as exc:
                    out.notes.append(f"Event catalogs could not be fetched: {str(exc).splitlines()[0] if str(exc) else exc}")
            if request.fetch_geomagnetic and request.anchor is not None:
                self.progress.emit(4, "Loading the geomagnetic response (Dst, Kp, solar wind)...")
                try:
                    out.geomagnetic = load_geomagnetic_context(
                        request.anchor, cache_dir=request.cache_dir, cancel_cb=self._cancel.is_set,
                        event_label=request.event_label,
                    )
                except GeomagneticCancelled:
                    raise
                except Exception as exc:
                    out.notes.append(f"The geomagnetic response could not be loaded: {str(exc).splitlines()[0] if str(exc) else exc}")
        except (CatalogCancelled, GeomagneticCancelled):
            self.cancelled.emit()
            return
        if self._cancel.is_set():
            self.cancelled.emit()
            return
        self.finished.emit(out)


# ------------------------------------------------------------------ 3. telling


def _png(fig: Any, dpi: int) -> bytes:
    from src.backend.common.figure_export import figure_png_bytes

    return figure_png_bytes(fig, dpi=dpi, tight=True)


def _overview_figure(main: Any, catalog: EventContextResult | None, geomagnetic: Any, markers: list,
                     radio_interval: tuple[datetime, datetime] | None, dpi: int) -> ProjectReportFigure:
    """The composer's one-axis event figure: spectrum, GOES, catalog lanes, geomagnetic block."""
    title = "Event Overview"
    try:
        from src.backend.common.event_figure import EventFigureSpec, build_event_figure
        from src.ui.app.event_context_strip import build_event_markers
        from src.ui.app.event_figure_composer import collect_event_figure_data

        state = main._composer_state
        spec = state.spec if state.touched else EventFigureSpec()
        data = collect_event_figure_data(main, spec, state)
        if catalog is not None and data.base_utc is not None and data.x_range is not None:
            lanes = build_event_markers(catalog, SOURCES, data.base_utc, data.x_range[0], data.x_range[1])
            data.events = lanes if lanes.lanes else None
        if geomagnetic is not None:
            data.geomagnetic, data.geo_markers, data.radio_interval = geomagnetic, list(markers), radio_interval
        if not state.touched:
            # Only the panels there is something to draw in.
            has = {"spectrum": data.spectrum is not None, "goes": bool(data.goes), "events": data.events is not None}
            spec = replace(
                spec,
                panels=[replace(panel, enabled=panel.enabled and has.get(panel.key, panel.enabled)) for panel in spec.panels],
                geomagnetic=replace(spec.geomagnetic, enabled=data.geomagnetic is not None),
            )
        png = _png(build_event_figure(spec, data), dpi)
    except Exception as exc:
        return ProjectReportFigure(title, availability_note=f"The event overview could not be drawn: {exc}")
    return ProjectReportFigure(
        title, png_bytes=png,
        caption="The radio spectrum, GOES X-rays and the event catalogs on one time axis"
                + (", with the geomagnetic response on its own axis below." if geomagnetic is not None else "."),
    )


def geomagnetic_report_figure(geomagnetic: Any, markers: list, radio_interval, *, dpi: int = 220) -> ProjectReportFigure:
    """The geomagnetic response as a report figure (OriginPro style), or a note saying why not."""
    title = "Geomagnetic Response"
    if geomagnetic is None or not geomagnetic.has_data():
        return ProjectReportFigure(title, availability_note="The geomagnetic response is not available.")
    try:
        from src.backend.space_weather.geomagnetic_figures import geomagnetic_figure
        from src.ui.space_weather.geomagnetic_response_window import load_panel_choice

        png = _png(geomagnetic_figure(geomagnetic, load_panel_choice(), radio_interval=radio_interval, markers=markers), dpi)
    except Exception as exc:
        return ProjectReportFigure(title, availability_note=f"The geomagnetic figure could not be drawn: {exc}")
    caption = (
        f"{geomagnetic.event_label} · {geomagnetic.start:%Y-%m-%d %H:%M} to {geomagnetic.end:%Y-%m-%d %H:%M} UTC. "
        "The dashed line is the event start; the shaded band is the radio data."
    )
    if geomagnetic.notes:
        caption += " " + " ".join(geomagnetic.notes)
    return ProjectReportFigure(title, png_bytes=png, caption=caption)


def story_section(main: Any, request: StoryRequest | None, fetched: StoryFetchResult | None = None, *,
                  dpi: int = 220) -> tuple[EventStory | None, list[ProjectReportFigure], list[str]]:
    """The story, its figures and its notes (GUI thread)."""
    if request is None:
        return None, [], []
    fetched = fetched or StoryFetchResult()
    catalog = fetched.catalog or request.catalog
    geomagnetic = fetched.geomagnetic if fetched.geomagnetic is not None else request.geomagnetic
    markers = request.markers if request.geomagnetic is not None else list(getattr(geomagnetic, "markers", []) or [])
    notes = list(request.notes) + list(fetched.notes)
    story = build_event_story(radio=request.radio, catalog=catalog, geomagnetic=geomagnetic, markers=markers,
                              gcs=request.gcs)
    if story is None:
        return None, [], notes
    try:
        chain = ProjectReportFigure("Event Chain", png_bytes=_png(event_chain_figure(story), dpi),
                                    caption="From the Sun (top) to the Earth (bottom). Bold steps are joined by the "
                                            "time between them; the time axis is compressed so minutes and days both fit.")
    except Exception as exc:
        chain = ProjectReportFigure("Event Chain", availability_note=f"The event chain could not be drawn: {exc}")
    figures = [
        chain,
        _overview_figure(main, catalog, geomagnetic, markers, request.radio_interval, dpi),
        geomagnetic_report_figure(geomagnetic, markers, request.radio_interval, dpi=dpi),
    ]
    return story, figures, notes
