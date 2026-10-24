"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Which event the geomagnetic response is shown for, and fetching it
(src/ui/space_weather/geomagnetic_controller.py).

Three things choose the event:

* loading, combining or extending FITS data — its observation start;
* selecting a row in the burst list;
* selecting a day or a file in the e-CALLISTO downloader.

The last two *override* the loaded dataset's event (the sidebar card then
offers "Use loaded data" to switch back). Requests are debounced by 600 ms and
de-duplicated on the event hour, and each carries a key so a slow reply for an
event the user has since moved away from is dropped. Nothing ever pops up: the
sidebar card and the full window both just watch this controller.

The controller is also the project's ``geomagnetic`` part: a project stores
the settings, the event, the summary and the (small) series, so it reopens
offline without fetching anything.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QStandardPaths, QThread, QTimer, Signal, Slot

from src.backend.space_weather.geomagnetic_context import (
    DEFAULT_AFTER,
    DEFAULT_BEFORE,
    GeomagneticCancelled,
    GeomagneticContext,
    context_from_project,
    context_to_project,
    load_geomagnetic_context,
)

DEBOUNCE_MS = 600

#: Set to "1" to stop data loads and selections from fetching automatically
#: (the test suite does; Refresh still fetches).
AUTOFETCH_ENV = "E_CALLISTO_DISABLE_GEOMAGNETIC_AUTOFETCH"

#: Fetch threads and their workers, kept alive independently of the controller:
#: a QThread destroyed while running aborts the process, and a controller can be
#: garbage-collected while its last fetch is still unwinding.
_LIVE: set[tuple[Any, Any]] = set()


def _purge_finished() -> None:
    for pair in list(_LIVE):
        try:
            done = pair[0].isFinished()
        except RuntimeError:
            done = True
        if done:
            _LIVE.discard(pair)


SOURCE_LOADED = "loaded"
SOURCE_BURST_LIST = "burst_list"
SOURCE_DOWNLOADER = "downloader"


def space_weather_cache_dir() -> Path:
    override = os.environ.get("ECALLISTO_SPACE_WEATHER_CACHE", "").strip()
    if override:
        return Path(override)
    raw = ""
    try:
        raw = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
    except Exception:
        raw = ""
    base = Path(raw) if raw else Path.home() / ".local" / "share" / "e-callisto-fits-analyzer"
    return base / "space_weather_cache"


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


@dataclass(frozen=True)
class EventChoice:
    when: datetime
    label: str
    source: str


class GeomagneticWorker(QObject):
    """Loads one :class:`GeomagneticContext` off the GUI thread."""

    progress = Signal(str, object, object)
    finished = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str)

    def __init__(self, key: str, choice: EventChoice, before: timedelta, after: timedelta,
                 cache_dir: Path, loader: Callable[..., GeomagneticContext]):
        super().__init__()
        self.key = key
        self.choice = choice
        self.before = before
        self.after = after
        self.cache_dir = cache_dir
        self.loader = loader
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            context = self.loader(
                self.choice.when,
                before=self.before,
                after=self.after,
                cache_dir=self.cache_dir,
                progress_cb=lambda value, text: self.progress.emit(self.key, value, text),
                cancel_cb=self._cancel.is_set,
                event_label=self.choice.label,
            )
        except GeomagneticCancelled:
            self.cancelled.emit(self.key)
            return
        except Exception as exc:  # reported, never raised into Qt's event loop
            self.failed.emit(self.key, str(exc) or type(exc).__name__)
            return
        if self._cancel.is_set():
            self.cancelled.emit(self.key)
            return
        self.finished.emit(self.key, context)


class GeomagneticController(QObject):
    """The event, the span, the current context and the fetch that fills it."""

    contextChanged = Signal(object)  # GeomagneticContext | None
    eventChanged = Signal()  # event, label or override state changed
    statusChanged = Signal(str)
    busyChanged = Signal(bool)
    #: Something worth saving changed (project dirty).
    modified = Signal()

    def __init__(self, parent: QObject | None = None, *, loader: Callable[..., GeomagneticContext] | None = None,
                 cache_dir: Path | None = None):
        super().__init__(parent)
        self._loader = loader or load_geomagnetic_context
        self._cache_dir = Path(cache_dir) if cache_dir is not None else space_weather_cache_dir()
        self.before = DEFAULT_BEFORE
        self.after = DEFAULT_AFTER
        self.context: GeomagneticContext | None = None
        self._loaded: EventChoice | None = None
        self._override: EventChoice | None = None
        self._requested_key: str | None = None
        self._context_key: str | None = None
        self._thread: QThread | None = None
        self._worker: GeomagneticWorker | None = None
        self._queued: tuple[str, EventChoice] | None = None
        self.radio_interval: tuple[datetime, datetime] | None = None
        self.markers: list[dict[str, Any]] = []
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._fire)

    # ------------------------------------------------------------- the event
    @staticmethod
    def autofetch_enabled() -> bool:
        return os.environ.get(AUTOFETCH_ENV, "").strip() != "1"

    def current_choice(self) -> EventChoice | None:
        return self._override or self._loaded

    def is_overridden(self) -> bool:
        return self._override is not None and self._loaded is not None

    def all_markers(self) -> list[dict[str, Any]]:
        """DONKI markers loaded with the response, plus any added from outside."""
        markers = list(getattr(self.context, "markers", []) or []) if self.context is not None else []
        return markers + [m for m in self.markers if m not in markers]

    def event_label(self) -> str:
        choice = self.current_choice()
        if choice is None:
            return ""
        return f"{choice.label} · {choice.when:%Y-%m-%d %H:%M} UTC"

    def set_loaded_event(self, when: datetime | None, label: str,
                         radio_interval: tuple[datetime, datetime] | None = None) -> None:
        """The loaded FITS data's event; it wins unless a selection overrode it."""
        if when is None:
            self._loaded = None
            self.radio_interval = None
            self.eventChanged.emit()
            return
        self._loaded = EventChoice(_naive_utc(when), str(label), SOURCE_LOADED)
        if radio_interval is not None:
            self.radio_interval = (_naive_utc(radio_interval[0]), _naive_utc(radio_interval[1]))
        self._override = None
        self._drop_stale_context()
        self.eventChanged.emit()
        self._schedule()

    def select_event(self, when: datetime, label: str, source: str) -> None:
        """A burst-list or downloader selection: overrides the loaded event."""
        if when is None:
            return
        self._override = EventChoice(_naive_utc(when), str(label), str(source))
        self._drop_stale_context()
        self.eventChanged.emit()
        self._schedule()

    def use_loaded_data(self) -> None:
        if self._override is None:
            return
        self._override = None
        self._drop_stale_context()
        self.eventChanged.emit()
        self._schedule(force=True)

    def set_span(self, before: timedelta, after: timedelta) -> None:
        before = max(timedelta(hours=1), before)
        after = max(timedelta(hours=6), after)
        if (before, after) == (self.before, self.after):
            return
        self.before, self.after = before, after
        self._drop_stale_context()
        self.modified.emit()
        self._schedule(force=True)

    def _drop_stale_context(self) -> None:
        """Forget a response loaded for another event hour or span than the current one.

        Otherwise its numbers stay under the new event's label until the new
        fetch arrives, and for good if that fails or automatic fetching is off;
        a project saved meanwhile would store them as the new event's.
        """
        if self.context is None or self.request_key() == self._context_key:
            return
        self.context = None
        self._context_key = None
        self.contextChanged.emit(None)
        self.modified.emit()

    def request_key(self, choice: EventChoice | None = None) -> str:
        """De-duplication key: the event hour and the span."""
        choice = choice or self.current_choice()
        if choice is None:
            return ""
        hour = choice.when.replace(minute=0, second=0, microsecond=0)
        return f"{hour:%Y%m%dT%H}|{int(self.before.total_seconds())}|{int(self.after.total_seconds())}"

    # -------------------------------------------------------------- fetching
    def _schedule(self, *, force: bool = False) -> None:
        if not (force or self.autofetch_enabled()):
            if self.current_choice() is not None and self.request_key() != self._context_key:
                self.statusChanged.emit("Press Refresh to load the geomagnetic response.")
            return
        self._debounce.start()

    def refresh(self) -> None:
        """Fetch now for the current event, even if it is already shown."""
        self._context_key = None
        self._debounce.stop()
        self._fire(force=True)

    @Slot()
    def _fire(self, force: bool = False) -> None:
        choice = self.current_choice()
        if choice is None:
            self.statusChanged.emit("Load data or select an event to see the geomagnetic response.")
            return
        key = self.request_key(choice)
        if not force and key == self._context_key:
            # Same event hour already shown: only the label may differ.
            if self.context is not None and self.context.event_label != choice.label:
                self.context.event_label = choice.label
                self.contextChanged.emit(self.context)
            return
        if not force and key == self._requested_key and self._thread is not None:
            return
        self._requested_key = key
        if self._thread is not None:
            # One fetch at a time: cancel the running one, start this when it stops.
            if self._worker is not None:
                self._worker.request_cancel()
            self._queued = (key, choice)
            return
        self._start(key, choice)

    def _start(self, key: str, choice: EventChoice) -> None:
        thread = QThread()  # never parented to a widget (thread-teardown rule)
        worker = GeomagneticWorker(key, choice, self.before, self.after, self._cache_dir, self._loader)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        _purge_finished()
        _LIVE.add((thread, worker))
        self._thread = thread
        self._worker = worker
        self.busyChanged.emit(True)
        self.statusChanged.emit(f"Loading the geomagnetic response for {choice.when:%Y-%m-%d %H:%M} UTC…")
        thread.start()

    @Slot(str, object, object)
    def _on_progress(self, key: str, _value: object, text: object) -> None:
        if key == self._requested_key and text:
            self.statusChanged.emit(str(text))

    @Slot(str, object)
    def _on_finished(self, key: str, context: object) -> None:
        if key != self._requested_key:
            return  # a stale reply for an event the user has moved away from
        self.context = context
        self._context_key = key
        self.contextChanged.emit(context)
        self.modified.emit()
        problems = len(getattr(context, "errors", {}) or {})
        self.statusChanged.emit(
            "Geomagnetic response loaded." if not problems else f"Loaded with {problems} source(s) unavailable."
        )

    @Slot(str, str)
    def _on_failed(self, key: str, message: str) -> None:
        if key != self._requested_key:
            return
        self.statusChanged.emit(f"Geomagnetic response failed: {message.splitlines()[0] if message else 'error'}")

    @Slot(str)
    def _on_cancelled(self, key: str) -> None:
        if key == self._requested_key and self._queued is None:
            self.statusChanged.emit("Geomagnetic response cancelled.")

    @Slot()
    def _on_thread_finished(self) -> None:
        thread = self._thread
        if thread is not None:
            thread.wait()
            _LIVE.discard((thread, self._worker))
        self._thread = None
        self._worker = None  # freed here, on the GUI thread
        if thread is not None:
            thread.deleteLater()
        queued, self._queued = self._queued, None
        if queued is not None:
            self._start(*queued)
            return
        self.busyChanged.emit(False)

    def is_busy(self) -> bool:
        return self._thread is not None

    def shutdown(self, wait_ms: int = 3000) -> None:
        self._debounce.stop()
        self._queued = None
        worker, thread = self._worker, self._thread
        if worker is not None:
            worker.request_cancel()
        if thread is not None:
            thread.quit()
            thread.wait(int(wait_ms))

    # ------------------------------------------------------------ project
    def project_has_content(self) -> bool:
        return self.context is not None

    def project_capture(self) -> tuple[dict[str, Any], dict[str, Any]]:
        meta, arrays = context_to_project(self.context) if self.context is not None else ({}, {})
        choice = self.current_choice()
        meta = {
            "context": meta,
            "before_hours": self.before.total_seconds() / 3600.0,
            "after_hours": self.after.total_seconds() / 3600.0,
            "event": None if choice is None else {
                "time": choice.when.isoformat(), "label": choice.label, "source": choice.source,
            },
            "overridden": self.is_overridden(),
            "radio_interval": None if self.radio_interval is None else [t.isoformat() for t in self.radio_interval],
        }
        return meta, arrays

    def project_restore(self, meta: dict[str, Any], arrays: dict[str, Any]) -> None:
        """Show the stored response; nothing is fetched."""
        self._debounce.stop()
        try:
            self.before = timedelta(hours=float(meta.get("before_hours") or DEFAULT_BEFORE.total_seconds() / 3600))
            self.after = timedelta(hours=float(meta.get("after_hours") or DEFAULT_AFTER.total_seconds() / 3600))
        except (TypeError, ValueError):
            self.before, self.after = DEFAULT_BEFORE, DEFAULT_AFTER
        event = meta.get("event") or {}
        choice = None
        try:
            if event.get("time"):
                choice = EventChoice(datetime.fromisoformat(str(event["time"])), str(event.get("label") or ""),
                                     str(event.get("source") or SOURCE_LOADED))
        except ValueError:
            choice = None
        if choice is not None:
            if choice.source == SOURCE_LOADED or not meta.get("overridden"):
                self._loaded, self._override = EventChoice(choice.when, choice.label, SOURCE_LOADED), None
            else:
                self._override = choice
        interval = meta.get("radio_interval")
        try:
            self.radio_interval = (
                (datetime.fromisoformat(interval[0]), datetime.fromisoformat(interval[1])) if interval else None
            )
        except (TypeError, ValueError, IndexError):
            self.radio_interval = None
        self.context = context_from_project(dict(meta.get("context") or {}), arrays)
        self._context_key = self.request_key() if self.context is not None else None
        self._requested_key = self._context_key
        self.eventChanged.emit()
        self.contextChanged.emit(self.context)
        self.statusChanged.emit("Geomagnetic response restored from the project.")

    def project_clear(self) -> None:
        self._debounce.stop()
        self.context = None
        self._context_key = None
        self._requested_key = None
        self._override = None
        self._loaded = None
        self.radio_interval = None
        self.eventChanged.emit()
        self.contextChanged.emit(None)

    def reset(self) -> None:
        """Back to a fresh start (Reset All): no event, no response, the default span.

        A fetch still running is cancelled; its reply no longer matches the
        requested key, so it is dropped when it arrives.
        """
        self._queued = None
        if self._worker is not None:
            self._worker.request_cancel()
        self.before = DEFAULT_BEFORE
        self.after = DEFAULT_AFTER
        self.markers = []
        self.project_clear()
        self.statusChanged.emit("")
        if self._thread is not None:
            # The cancelled fetch may take a moment to stop; to the user it is over.
            self.busyChanged.emit(False)
