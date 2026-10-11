"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The event-context strip's data (src/ui/app/event_context_strip.py).

:class:`EventContextController` keeps which catalogs are shown (View → Event
Context Strip, one checkbox per source, remembered between sessions), fetches
them for the loaded data's time range plus six hours each side, and turns them
into :class:`~src.backend.radio.radio_figures.EventMarkers` on the spectrum's
own time axis. The main window draws those markers on whichever canvas is
showing, and hands the same markers to Export Figure and the project report.

It is also the project's ``event_context`` part: the enabled sources and the
fetched events are saved, so a project reopens with its strip offline.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Signal, Slot

from src.backend.radio.radio_figures import EventLane, EventMark, EventMarkers
from src.backend.space_weather.event_catalogs import (
    DEFAULT_MARGIN,
    KIND_BURST,
    KIND_CME,
    KIND_FLARE,
    KIND_GST,
    KIND_IPS,
    KIND_SEP,
    LANE_TITLES,
    SOURCE_LABELS,
    SOURCES,
    CatalogCancelled,
    CatalogEvent,
    EventContextResult,
    events_in_window,
    flare_class_color,
    load_event_context,
    nearby_counts,
)
from src.version import APP_NAME, APP_ORG

ENABLED_KEY = "event_context/enabled"
SOURCES_KEY = "event_context/sources"
DEBOUNCE_MS = 600
AUTOFETCH_ENV = "E_CALLISTO_DISABLE_EVENT_CONTEXT_AUTOFETCH"

KIND_COLORS = {KIND_CME: "#9467bd", KIND_IPS: "#1f77b4", KIND_GST: "#8c564b", KIND_SEP: "#2ca02c"}
BURST_COLORS = {"II": "#d62728", "III": "#1f77b4", "IV": "#2ca02c", "V": "#ff7f0e", "I": "#7f7f7f"}

_LIVE: set[tuple[Any, Any]] = set()


def _make_settings() -> QSettings:
    """Persistent settings (a module-level factory so tests can redirect it)."""
    return QSettings(APP_ORG, APP_NAME)


def _cache_dir() -> Path:
    from src.ui.space_weather.geomagnetic_controller import space_weather_cache_dir

    return space_weather_cache_dir()


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# --------------------------------------------------------------------- markers


def event_color(event: CatalogEvent) -> str:
    if event.kind == KIND_FLARE:
        return flare_class_color(str(event.details.get("class") or event.label))
    if event.kind == KIND_BURST:
        family = str(event.details.get("type") or "").split("/")[0].strip("?+ ").upper()
        family = "III" if family.startswith("III") else family
        return BURST_COLORS.get(family, "#7f7f7f")
    return KIND_COLORS.get(event.kind, "#444444")


def short_label(event: CatalogEvent) -> str:
    if event.kind == KIND_FLARE:
        return str(event.details.get("class") or event.label)
    if event.kind == KIND_CME:
        speed = event.details.get("speed_kms")
        if speed:
            width = event.details.get("width_deg")
            halo = event.details.get("halo")
            if halo:
                return f"{float(speed):.0f} km/s, halo"
            if width:
                return f"{float(speed):.0f} km/s, {float(width):.0f}°"
            half = event.details.get("half_angle_deg")
            return f"{float(speed):.0f} km/s" + (f", {2 * float(half):.0f}°" if half else "")
        return "CME"
    if event.kind == KIND_BURST:
        return str(event.details.get("type") or event.label).replace("Type ", "")
    if event.kind == KIND_IPS:
        return "shock"
    if event.kind == KIND_GST:
        return event.label.replace("Storm ", "")
    return event.label


def tooltip(event: CatalogEvent) -> str:
    lines = [event.label or event.kind]
    times = f"{event.start:%Y-%m-%d %H:%M} UTC"
    if event.peak is not None:
        times += f", peak {event.peak:%H:%M}"
    if event.end is not None and event.end > event.start:
        times += f", end {event.end:%H:%M}"
    lines.append(times)
    lines.append(SOURCE_LABELS.get(event.source, event.source))
    if event.url:
        lines.append(event.url)
    return "\n".join(lines)


def build_event_markers(
    result: EventContextResult | None,
    sources: Iterable[str],
    base_utc: datetime,
    x_min: float,
    x_max: float,
    *,
    margin: timedelta = DEFAULT_MARGIN,
) -> EventMarkers:
    """Lanes for the plot range ``[x_min, x_max]`` seconds after ``base_utc``.

    Events overlapping the range are drawn; the ones within ``margin`` outside
    it are only counted, for the lane's "n more nearby" edge labels.
    """
    if result is None:
        return EventMarkers()
    base = _naive_utc(base_utc)
    start = base + timedelta(seconds=float(min(x_min, x_max)))
    end = base + timedelta(seconds=float(max(x_min, x_max)))
    wanted = set(sources)
    lanes = []
    for source in SOURCES:
        if source not in wanted:
            continue
        events = result.events.get(source, [])
        marks = []
        for event in events_in_window(events, start, end):
            x0 = (event.start - base).total_seconds()
            x1 = None
            if event.end is not None and event.end > event.start and event.kind in (KIND_FLARE, KIND_BURST, KIND_GST):
                x1 = (event.end - base).total_seconds()
            marks.append(EventMark(x0, x1, short_label(event), event_color(event), tooltip(event)))
        before, after = nearby_counts(events, start, end, margin=margin)
        title = LANE_TITLES[source]
        if source in result.errors:
            title += " (!)"
        lanes.append(EventLane(title, tuple(marks), before, after))
    return EventMarkers(tuple(lanes))


# ------------------------------------------------------------------ controller


class EventContextWorker(QObject):
    finished = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str)
    progress = Signal(str, object, object)

    def __init__(self, key: str, start: datetime, end: datetime, sources: tuple[str, ...],
                 cache_dir: Path, loader: Callable[..., EventContextResult]):
        super().__init__()
        self.key, self.start, self.end, self.sources = key, start, end, sources
        self.cache_dir = cache_dir
        self.loader = loader
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self.loader(
                self.start, self.end, sources=self.sources, cache_dir=self.cache_dir,
                progress_cb=lambda v, t: self.progress.emit(self.key, v, t), cancel_cb=self._cancel.is_set,
            )
        except CatalogCancelled:
            self.cancelled.emit(self.key)
            return
        except Exception as exc:  # reported, never raised into Qt's event loop
            self.failed.emit(self.key, str(exc) or type(exc).__name__)
            return
        self.finished.emit(self.key, result)


class EventContextController(QObject):
    """Which catalogs the strip shows, for which range, and what they returned."""

    resultChanged = Signal(object)
    enabledChanged = Signal(bool)
    statusChanged = Signal(str)
    modified = Signal()

    def __init__(self, parent: QObject | None = None, *, loader: Callable[..., EventContextResult] | None = None,
                 cache_dir: Path | None = None):
        super().__init__(parent)
        self._loader = loader or load_event_context
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        settings = self._settings()
        self.enabled = str(settings.value(ENABLED_KEY, "false")).lower() in ("1", "true", "yes")
        try:
            stored = json.loads(str(settings.value(SOURCES_KEY, "") or "[]"))
        except (TypeError, ValueError):
            stored = []
        self.sources: tuple[str, ...] = tuple(s for s in SOURCES if s in stored) or SOURCES
        self.result: EventContextResult | None = None
        self.range: tuple[datetime, datetime] | None = None
        self._result_key: str | None = None
        self._requested_key: str | None = None
        self._thread: QThread | None = None
        self._worker: EventContextWorker | None = None
        self._queued: tuple | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._fire)

    @staticmethod
    def _settings() -> QSettings:
        return _make_settings()

    def _save_settings(self) -> None:
        try:
            settings = self._settings()
            settings.setValue(ENABLED_KEY, "true" if self.enabled else "false")
            settings.setValue(SOURCES_KEY, json.dumps(list(self.sources)))
            settings.sync()
        except Exception:
            pass

    @staticmethod
    def autofetch_enabled() -> bool:
        return os.environ.get(AUTOFETCH_ENV, "").strip() != "1"

    # ------------------------------------------------------------ state
    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        self.enabled = enabled
        self._save_settings()
        self.enabledChanged.emit(enabled)
        self.modified.emit()
        if enabled and self.range is not None and self.request_key() != self._result_key:
            self._debounce.start()

    def set_sources(self, sources: Iterable[str]) -> None:
        chosen = tuple(s for s in SOURCES if s in set(sources))
        if chosen == self.sources:
            return
        self.sources = chosen
        self._save_settings()
        self.modified.emit()
        self.resultChanged.emit(self.result)
        if self.enabled and self.range is not None and self.request_key() != self._result_key:
            self._debounce.start()

    def set_range(self, start: datetime | None, end: datetime | None) -> None:
        """The loaded data's UTC range; the strip is fetched for it (plus margins)."""
        if start is None or end is None:
            self.range = None
            return
        self.range = (_naive_utc(start), _naive_utc(end))
        if self.enabled and self.autofetch_enabled() and self.request_key() != self._result_key:
            self._debounce.start()

    def request_key(self) -> str:
        if self.range is None:
            return ""
        start, end = self.range
        return f"{start:%Y%m%dT%H%M}|{end:%Y%m%dT%H%M}|{','.join(self.sources)}"

    # ---------------------------------------------------------- fetching
    def refresh(self) -> None:
        self._result_key = None
        self._fire(force=True)

    @Slot()
    def _fire(self, force: bool = False) -> None:
        if self.range is None:
            return
        key = self.request_key()
        if not force and key == self._result_key:
            return
        if not self.sources:
            return
        self._requested_key = key
        start, end = self.range
        if self._thread is not None:
            if self._worker is not None:
                self._worker.request_cancel()
            self._queued = (key, start, end, tuple(self.sources))
            return
        self._start(key, start, end, tuple(self.sources))

    def _start(self, key: str, start: datetime, end: datetime, sources: tuple[str, ...]) -> None:
        thread = QThread()  # never parented to a widget (thread-teardown rule)
        worker = EventContextWorker(key, start, end, sources, self._cache_dir or _cache_dir(), self._loader)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.progress.connect(self._on_progress)
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        for pair in list(_LIVE):
            try:
                if pair[0].isFinished():
                    _LIVE.discard(pair)
            except RuntimeError:
                _LIVE.discard(pair)
        _LIVE.add((thread, worker))
        self._thread, self._worker = thread, worker
        self.statusChanged.emit("Loading flares, CMEs, DONKI events and bursts…")
        thread.start()

    @Slot(str, object, object)
    def _on_progress(self, key: str, _value: object, text: object) -> None:
        if key == self._requested_key and text:
            self.statusChanged.emit(str(text))

    @Slot(str, object)
    def _on_finished(self, key: str, result: object) -> None:
        if key != self._requested_key:
            return
        self.result = result
        self._result_key = key
        self.resultChanged.emit(result)
        self.modified.emit()
        errors = getattr(result, "errors", {}) or {}
        count = sum(len(v) for v in getattr(result, "events", {}).values())
        message = f"Event context: {count} event(s)."
        if errors:
            message += " Unavailable: " + ", ".join(SOURCE_LABELS.get(s, s) for s in errors) + "."
        self.statusChanged.emit(message)

    @Slot(str, str)
    def _on_failed(self, key: str, message: str) -> None:
        if key == self._requested_key:
            self.statusChanged.emit(f"Event context failed: {message.splitlines()[0] if message else 'error'}")

    @Slot()
    def _on_thread_finished(self) -> None:
        thread = self._thread
        if thread is not None:
            thread.wait()
            _LIVE.discard((thread, self._worker))
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()
        queued, self._queued = self._queued, None
        if queued is not None:
            self._start(*queued)

    def is_busy(self) -> bool:
        return self._thread is not None

    def shutdown(self, wait_ms: int = 3000) -> None:
        self._debounce.stop()
        self._queued = None
        if self._worker is not None:
            self._worker.request_cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(int(wait_ms))

    # ----------------------------------------------------------- markers
    def markers(self, base_utc: datetime | None, x_min: float, x_max: float) -> EventMarkers | None:
        if not self.enabled or base_utc is None or self.result is None:
            return None
        markers = build_event_markers(self.result, self.sources, base_utc, x_min, x_max)
        return markers if markers.lanes else None

    # ----------------------------------------------------------- project
    def project_has_content(self) -> bool:
        return self.result is not None

    def project_capture(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return {
            "enabled": bool(self.enabled),
            "sources": list(self.sources),
            "result": self.result.to_json() if self.result is not None else None,
            "range": None if self.range is None else [t.isoformat() for t in self.range],
        }, {}

    def project_restore(self, meta: dict[str, Any], arrays: dict[str, Any]) -> None:
        self._debounce.stop()
        self.sources = tuple(s for s in SOURCES if s in set(meta.get("sources") or SOURCES)) or SOURCES
        self.result = EventContextResult.from_json(meta["result"]) if meta.get("result") else None
        try:
            rng = meta.get("range")
            self.range = (datetime.fromisoformat(rng[0]), datetime.fromisoformat(rng[1])) if rng else None
        except (TypeError, ValueError, IndexError):
            self.range = None
        self._result_key = self.request_key() if self.result is not None else None
        self._requested_key = self._result_key
        wanted = bool(meta.get("enabled", True))
        if wanted != self.enabled:
            self.enabled = wanted
            self._save_settings()
            self.enabledChanged.emit(wanted)
        self.resultChanged.emit(self.result)

    def project_clear(self) -> None:
        self._debounce.stop()
        self.result = None
        self._result_key = None
        self._requested_key = None
        self.range = None
        self.resultChanged.emit(None)
