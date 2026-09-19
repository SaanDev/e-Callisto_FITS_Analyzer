"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

One viewpoint of the GCS fitting window (src/ui/gcs/gcs_viewpoint_panel.py).

A panel is its image and nothing else on screen: the plot's axes and title row
are hidden so every pixel of the square goes to the Sun, and the caption is drawn
on the image itself. The panel still *owns* its controls — channel, date range,
fetch, colormap and contrast — but does not place them; the window mounts them in
its control cards, which is what lets the images take all the space the window
can give them.

Only Helioviewer JPEG2000 frames are loaded. The loading, orientation,
co-registration and differencing live in ``src.backend.solar.helioviewer_jp2``; this
panel drives it and draws the result.

Panels are deliberately independent in what they *show* and identical in what
they are *told*. Three spacecraft rarely share a cadence — COR2 total-brightness
frames come every 15 minutes, LASCO C2 every 12 — so each panel snaps to its own
frame nearest the shared time rather than being forced onto a common index,
which would drift the three views apart in time without saying so.
"""

from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QDateTime, QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.backend.solar import helioviewer_jp2 as hvjp2
from src.backend.gcs.gcs_model import ObserverGeometry
from src.backend.solar.solar_data_analysis import frame_observation_time
from src.ui.solar.sunpy_plot_window import SunPyPlotCanvas

#: Threads still running, kept alive independently of the widget that started
#: them. A QThread destroyed while running aborts the process, and Python is free
#: to collect a widget's attributes the moment the widget goes away.
_LIVE_THREADS: set = set()


def _track_thread(thread: Any) -> None:
    _LIVE_THREADS.add(thread)


def _forget_thread(thread: Any) -> None:
    _LIVE_THREADS.discard(thread)


#: Colormaps offered per panel. Grey first: it is the default for every load.
COLORMAP_CHOICES: tuple[str, ...] = (
    "gray",
    "soholasco2",
    "soholasco3",
    "stereocor1",
    "stereocor2",
    "inferno",
    "magma",
    "viridis",
)

#: The colormap every panel shows when frames are loaded.
DEFAULT_COLORMAP = "gray"

#: Difference modes, in the order they appear in the shared toolbar.
DIFFERENCE_MODES: tuple[tuple[str, str], ...] = (
    ("raw", "Raw"),
    ("running", "Running difference"),
    ("base", "Base difference"),
)

#: The view mode every panel starts in, and returns to whenever frames load.
DEFAULT_DIFFERENCE_MODE = "running"

#: Date format for the per-channel range editors.
RANGE_FORMAT = "yyyy-MM-dd HH:mm"

#: Points around the drawn solar limb.
_LIMB_SAMPLES = 361


def _utc_naive(value: Any) -> datetime:
    value = value.toPython() if hasattr(value, "toPython") else value
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


def _qdatetime_utc(value: datetime) -> QDateTime:
    value = _utc_naive(value)
    return QDateTime(value.year, value.month, value.day, value.hour, value.minute,
                     value.second, value.microsecond // 1000, Qt.UTC)


def jp2_cache_dir(base: Any = None) -> Path:
    """Where downloaded JP2s are kept, under the application's archive cache."""
    if base:
        root = Path(base)
    else:
        from src.ui.solar.sunpy_solar_viewer import _default_cache_dir

        root = _default_cache_dir()
    path = Path(root) / "helioviewer_jp2"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


class JP2FetchWorker(QObject):
    """Runs :func:`src.backend.solar.helioviewer_jp2.fetch_range` off the GUI thread."""

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        source: hvjp2.JP2Source,
        start: datetime,
        end: datetime,
        *,
        max_frames: int,
        cache_dir: Path | None,
    ):
        super().__init__()
        self.source = source
        self.start = start
        self.end = end
        self.max_frames = int(max_frames)
        self.cache_dir = cache_dir
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            sequence = hvjp2.fetch_range(
                self.source,
                self.start,
                self.end,
                max_frames=self.max_frames,
                cache_dir=self.cache_dir,
                progress_cb=self.progress.emit,
                cancel_cb=self._cancel.is_set,
            )
        except hvjp2.HelioviewerJP2Cancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # reported, never raised into Qt's event loop
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        if self._cancel.is_set():
            self.cancelled.emit()
            return
        self.finished.emit(sequence)


class _FetchDelivery(QObject):
    """Deliver a worker's signals only while that exact request is current.

    A queued Qt signal can still arrive after disconnect(). A receiver retained
    for the request carries its identity through queueing and also guarantees
    that all widget updates happen on the GUI thread.
    """

    def __init__(self, panel: GCSViewpointPanel, worker: JP2FetchWorker):
        super().__init__(panel)
        self.panel = panel
        self.worker = worker

    @Slot(str)
    def progress(self, message: str) -> None:
        if self.panel._worker is self.worker:
            self.panel._on_progress(message)

    @Slot(object)
    def finished(self, sequence: Any) -> None:
        if self.panel._worker is self.worker:
            self.panel._on_fetch_finished(sequence)
        self.deleteLater()

    @Slot(str)
    def failed(self, message: str) -> None:
        if self.panel._worker is self.worker:
            self.panel._on_failed(message)
        self.deleteLater()

    @Slot()
    def cancelled(self) -> None:
        if self.panel._worker is self.worker:
            self.panel._on_cancelled()
        self.deleteLater()


class ImageCaption:
    """A panel's caption, drawn on the image and pinned to its top-left corner.

    Parented to the ViewBox rather than added to the plot, so it is positioned
    in screen pixels and stays put when the view zooms or pans — and it costs the
    image no space, unlike the 30 px title row it replaces.
    """

    def __init__(self, canvas: SunPyPlotCanvas):
        import pyqtgraph as pg

        self._text = ""
        self._item = pg.TextItem("", color=(240, 240, 240), fill=pg.mkBrush(0, 0, 0, 160), anchor=(0, 0))
        self._item.setZValue(60)
        self._view = canvas.map_plot.getViewBox()
        self._item.setParentItem(self._view)
        self._item.setPos(6, 6)
        self._view.sigResized.connect(self._resize)
        self._resize()

    def _resize(self) -> None:
        self._item.setTextWidth(max(80.0, self._view.width() - 16.0))

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._text = str(text)
        self._item.setText(self._text)

    def setVisible(self, visible: bool) -> None:  # noqa: N802 (Qt naming)
        """Show or hide the banner; its text keeps updating either way."""
        self._item.setVisible(bool(visible))

    def isVisible(self) -> bool:  # noqa: N802 (Qt naming)
        return bool(self._item.isVisible())


class GCSViewpointPanel(QWidget):
    """One observer: its own JP2 fetch, rendering controls and image."""

    #: Emitted when this panel's frames change, so the window can re-derive the
    #: shared time span and the observer separations.
    framesChanged = Signal(object)
    #: Emitted with a status line for the window's shared status bar.
    statusChanged = Signal(str)
    #: Emitted when a fetch starts or ends, so the window can show busy state.
    busyChanged = Signal(bool)
    #: The cursor's helioprojective position over this image, or (None, None).
    hovered = Signal(object, object)

    def __init__(
        self,
        label: str,
        *,
        source_key: str = "LASCO C2",
        cache_dir: Any = None,
        theme: Any = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.label = str(label)
        self._cache_base = cache_dir
        self.theme = theme

        self.frames: list[Any] = []
        #: The archive's frame just before ``frames[0]``: the running-difference
        #: reference for the first frame, which is never displayed itself.
        self.previous_frame: Any | None = None
        self.observer: ObserverGeometry | None = None
        self._display_cache: dict[tuple[str, int], np.ndarray] = {}
        self._display_failures: dict[tuple[str, int], str] = {}
        #: Display ranges, which only change with the frame, mode or sliders. The
        #: percentile behind each costs ~15 ms on a 1024-pixel frame.
        self._levels_cache: dict[tuple, tuple[float | None, float | None]] = {}
        self._difference_mode = DEFAULT_DIFFERENCE_MODE
        self._index = 0
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._target_time: datetime | None = None
        self._sync_tolerance_seconds = 300.0
        self._difference_failed = ""
        self._limb_visible = True
        #: Set by the window: the cap on frames per channel for a range fetch.
        self.max_frames_provider: Callable[[], int] | None = None

        self._build_image()
        self._build_controls()
        self.select_source(source_key)
        self.source_combo.currentIndexChanged.connect(self.invalidate_frames)
        self.start_edit.dateTimeChanged.connect(self.invalidate_frames)
        self.end_edit.dateTimeChanged.connect(self.invalidate_frames)

    # ------------------------------------------------------------------- UI
    def _build_image(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.canvas = SunPyPlotCanvas(theme=self.theme)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Every pixel to the image: no axes, no title row. Square mode stays on,
        # which is what keeps the Sun round — the window sizes each panel square.
        self.canvas.set_map_axis_titles_visible(False)
        self.canvas.set_map_chrome_visible(False)
        layout.addWidget(self.canvas)
        self.title_label = ImageCaption(self.canvas)
        self.title_label.setText(f"{self.label} · not loaded")
        self.canvas.set_hover_callback(lambda x, y: self.hovered.emit(x, y))

    def _build_controls(self) -> None:
        """Create the panel's controls; the window decides where they go."""
        from src.ui.solar.solar_measure_tools import GCSParameterSlider

        self.source_combo = QComboBox()
        for jp2_source in hvjp2.GCS_JP2_SOURCES:
            self.source_combo.addItem(jp2_source.label, userData=jp2_source.key)
        self.source_combo.setToolTip(
            "Coronagraph to load, as Helioviewer JPEG2000 frames. Entries a spacecraft\n"
            "could not have observed in this channel's date range are disabled."
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
        self.start_edit = QDateTimeEdit(_qdatetime_utc(now - timedelta(hours=1)))
        self.end_edit = QDateTimeEdit(_qdatetime_utc(now + timedelta(hours=1)))
        for edit, which in ((self.start_edit, "first"), (self.end_edit, "last")):
            edit.setTimeSpec(Qt.UTC)
            edit.setCalendarPopup(True)
            edit.setDisplayFormat(RANGE_FORMAT)
            edit.setToolTip(f"UTC time of the {which} frame this channel should load.")

        self.fetch_btn = QPushButton("Load")
        self.fetch_btn.setToolTip("Load this channel's frames for its date range.")
        self.fetch_btn.clicked.connect(lambda: self.start_fetch())

        self.frames_label = QLabel("—")
        self.frames_label.setMinimumWidth(64)
        self.frames_label.setToolTip("Frames loaded for this channel.")

        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(COLORMAP_CHOICES)
        self.colormap_combo.setToolTip("Colormap for this viewpoint. Every load resets it to gray.")
        self.colormap_combo.currentTextChanged.connect(lambda _t: self.render())

        # Raw frames: a low/high percentile stretch over the in-field pixels.
        # Difference frames: "High" sets the symmetric clip about zero.
        self.low_slider = GCSParameterSlider("Low", 1.0, minimum=0.0, maximum=20.0, unit="%", decimals=1)
        self.high_slider = GCSParameterSlider("High", 99.0, minimum=80.0, maximum=100.0, unit="%", decimals=1)
        self.low_slider.setToolTip("Raw frames: percentile mapped to the bottom of the colour scale.")
        self.high_slider.setToolTip(
            "Raw frames: percentile mapped to the top of the colour scale.\n"
            "Difference frames: percentile of |change| that saturates — lower it to\n"
            "boost a faint front, raise it if the noise is overwhelming."
        )
        self.low_slider.valueChanged.connect(lambda _v: self.render())
        self.high_slider.valueChanged.connect(lambda _v: self.render())

    # -------------------------------------------------------------- source
    def source(self) -> hvjp2.JP2Source | None:
        return hvjp2.source_by_key(self.source_combo.currentData())

    def select_source(self, key: str) -> None:
        index = self.source_combo.findData(key)
        if index >= 0:
            self.source_combo.setCurrentIndex(index)

    def update_availability(self, when: datetime | None) -> None:
        """Disable sources that did not exist on ``when``.

        STEREO-B was lost in 2014; fetching it for a 2020 event would only ever
        come back empty, so the entry is greyed out rather than left as a trap.
        """
        model = self.source_combo.model()
        for row in range(self.source_combo.count()):
            jp2_source = hvjp2.source_by_key(self.source_combo.itemData(row))
            item = model.item(row) if hasattr(model, "item") else None
            if jp2_source is None or item is None:
                continue
            available = when is None or jp2_source.available_on(when)
            flags = item.flags()
            item.setFlags(flags | Qt.ItemIsEnabled if available else flags & ~Qt.ItemIsEnabled)

    def source_available(self, when: datetime | None) -> bool:
        jp2_source = self.source()
        return jp2_source is not None and (when is None or jp2_source.available_on(when))

    # --------------------------------------------------------------- range
    def date_range(self) -> tuple[datetime, datetime]:
        return _utc_naive(self.start_edit.dateTime()), _utc_naive(self.end_edit.dateTime())

    def set_date_range(self, start: datetime, end: datetime) -> None:
        start, end = (_utc_naive(value).replace(microsecond=0) for value in (start, end))
        if (start, end) == self.date_range():
            return
        for edit, value in ((self.start_edit, start), (self.end_edit, end)):
            blocked = edit.blockSignals(True)
            edit.setDateTime(_qdatetime_utc(value))
            edit.blockSignals(blocked)
        self.invalidate_frames()

    def invalidate_frames(self, *_args: Any) -> None:
        """Discard frames and pending downloads when their source/range changes."""
        self.cancel_fetch(discard_result=True)
        if self.frames:
            self.set_frames([])
        else:
            self.render()

    # -------------------------------------------------------------- frames
    def set_frames(self, frames: list[Any], *, harmonised: bool = False, previous: Any | None = None) -> None:
        """Adopt a sequence: time-ordered, on one pixel grid, ready to difference.

        ``previous`` is the frame just before the sequence, used only as the first
        frame's running-difference reference; it is dropped unless it really is
        earlier than every frame.

        Every load resets the panel to the house defaults — gray, running
        difference — so a fresh event always opens the way a CME front is easiest
        to see, whatever the previous event was left on.
        """
        ordered = [frame for frame in frames if frame is not None]
        ordered.sort(key=lambda f: frame_observation_time(f) or datetime.min)
        dropped = 0
        if not harmonised and ordered:
            try:
                ordered, dropped = hvjp2.harmonise_sequence(ordered)
            except Exception as exc:
                self.statusChanged.emit(f"{self.label}: frames could not be aligned ({exc}).")
            if previous is not None and ordered:
                try:
                    previous = hvjp2.align_to_grid(previous, ordered[0])
                except Exception:
                    previous = None
        if dropped:
            self.statusChanged.emit(
                f"{self.label}: dropped {dropped} frame(s) that could not share the sequence's "
                "pixel grid."
            )
        self.frames = ordered
        previous_time = frame_observation_time(previous) if previous is not None else None
        first_time = frame_observation_time(ordered[0]) if ordered else None
        self.previous_frame = (
            previous if previous_time is not None and first_time is not None and previous_time < first_time else None
        )
        self._index = 0
        self._display_cache.clear()
        self._display_failures.clear()
        self._levels_cache.clear()
        self._difference_failed = ""
        if ordered:
            self._difference_mode = DEFAULT_DIFFERENCE_MODE
            was = self.colormap_combo.blockSignals(True)
            self.colormap_combo.setCurrentText(DEFAULT_COLORMAP)
            self.colormap_combo.blockSignals(was)
        self.frames_label.setText(f"{len(ordered)} frame{'s' if len(ordered) != 1 else ''}" if ordered else "—")
        self._select_nearest_frame()
        self._refresh_observer()
        self.framesChanged.emit(self)
        self.render()

    def set_difference_mode(self, mode: str) -> None:
        mode = str(mode or "raw")
        if mode not in dict(DIFFERENCE_MODES):
            raise ValueError(f"Unknown difference mode: {mode}")
        self._difference_mode = mode
        self._difference_failed = ""
        self.render()

    def can_difference(self) -> bool:
        """True when this panel has enough frames to form the selected difference.

        Two frames always do. A single frame can still be running-differenced
        against the frame loaded from before the range; a base difference of it
        would only be a self-difference.
        """
        if len(self.frames) >= 2:
            return True
        return bool(self.frames) and self._difference_mode == "running" and self.previous_frame is not None

    def difference_mode(self) -> str:
        """Selected sequence mode, or ``raw`` when it cannot difference."""
        if self._difference_mode != "raw" and not self.can_difference():
            return "raw"
        return self._difference_mode

    def difference_reference(self, index: int | None = None) -> Any | None:
        """The frame subtracted from frame ``index`` in the current mode.

        ``None`` when that image is shown raw: in raw mode, and for the first
        frame of a base difference. The first frame of a running difference is
        referenced to the frame before the range, when one was loaded.
        """
        if not self.frames:
            return None
        index = max(0, min(self._index if index is None else int(index), len(self.frames) - 1))
        mode = self.difference_mode()
        if mode == "running":
            return self.frames[index - 1] if index > 0 else self.previous_frame
        if mode == "base" and index > 0:
            return self.frames[0]
        return None

    def rendered_mode(self) -> str:
        """Mode of the current image, including baseline and failed references."""
        mode = self.difference_mode()
        if self.difference_reference() is None or (mode, self._index) in self._display_failures:
            return "raw"
        return mode

    def frame_count(self) -> int:
        return len(self.frames)

    def display_array(self, index: int) -> np.ndarray:
        """The image drawn for ``index`` in the current mode, computed on demand.

        Difference frames cost ~25 ms each to build, so they are made the first
        time they are shown and then cached; switching modes is therefore instant
        and a playback pass pays the cost once per frame. A failure falls back to
        the raw frame *and says so*.
        """
        if not self.frames:
            raise ValueError("No frames are loaded.")
        index = max(0, min(int(index), len(self.frames) - 1))
        mode = self.difference_mode()
        key = (mode, index)
        self._difference_failed = self._display_failures.get(key, "")
        cached = self._display_cache.get(key)
        if cached is not None:
            return cached

        frame = self.frames[index]
        # Without an earlier observation the first frame is shown raw. Displaying
        # the next minus first image instead would label a future front with the
        # first frame's timestamp, observer position and saved-fit metadata.
        reference = self.difference_reference(index)
        if reference is None:
            image = np.asarray(frame.data, dtype=np.float32)
        else:
            try:
                self._validate_difference_grid(frame, reference)
                image = hvjp2.difference_image(frame, reference)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                if message != self._difference_failed:
                    self.statusChanged.emit(f"{self.label}: differencing failed ({message}); showing raw.")
                self._difference_failed = message
                self._display_failures[key] = message
                image = np.asarray(frame.data, dtype=np.float32)

        if len(self._display_cache) > 4 * max(1, len(self.frames)):
            self._display_cache.clear()
            self._levels_cache.clear()
        self._display_cache[key] = image
        return image

    def _validate_difference_grid(self, current: Any, reference: Any) -> None:
        """Reject equal-sized arrays whose pixels refer to different sky points."""
        current_grid = self._axis_transform(current, current.data.shape)
        reference_grid = self._axis_transform(reference, reference.data.shape)
        if current_grid is None or reference_grid is None:
            raise ValueError("a north-up solar WCS is required for differencing")
        for axis, size in (("x", current.data.shape[1]), ("y", current.data.shape[0])):
            positions = np.array([0.0, float(size - 1)])
            coordinates = []
            scales = []
            for grid in (current_grid, reference_grid):
                scale = grid[f"{axis}_scale_arcsec_per_pix"]
                coordinates.append(grid[f"{axis}_ref_arcsec"] +
                                   (positions - grid[f"{axis}_ref_pix"]) * scale)
                scales.append(abs(scale))
            # The loader ignores pointing corrections below 0.05 pixel. Allow
            # this numerical tolerance while rejecting scale/roll mismatches.
            if np.max(np.abs(coordinates[0] - coordinates[1])) > 0.1 * min(scales):
                raise ValueError("frames do not share the same solar coordinate grid")

    def times(self) -> list[datetime]:
        out: list[datetime] = []
        for frame in self.frames:
            when = frame_observation_time(frame)
            if when is not None:
                out.append(when)
        return out

    def set_shared_time(self, when: datetime | None) -> None:
        """Snap to the frame nearest ``when``.

        Matching on time rather than index is what keeps three instruments with
        different cadences actually simultaneous.
        """
        self._target_time = _utc_naive(when) if when is not None else None
        self._select_nearest_frame()
        self._refresh_observer()
        self.render()

    def _select_nearest_frame(self) -> None:
        when = self._target_time
        if when is None or not self.frames:
            return
        best = 0
        best_gap = None
        for index, frame in enumerate(self.frames):
            stamp = frame_observation_time(frame)
            if stamp is None:
                continue
            gap = abs((stamp - when).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap = index, gap
        self._index = best

    def set_sync_tolerance(self, seconds: float) -> None:
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Synchronization tolerance must be finite and nonnegative.")
        self._sync_tolerance_seconds = seconds
        self.render()

    def time_offset_seconds(self) -> float | None:
        when = self.current_time()
        if when is None or self._target_time is None:
            return None
        return (when - self._target_time).total_seconds()

    def is_synchronized(self) -> bool:
        if self.current_time() is None:
            return False
        offset = self.time_offset_seconds()
        return offset is None or abs(offset) <= self._sync_tolerance_seconds

    def _refresh_observer(self) -> None:
        frame = self.current_frame()
        self.observer = (
            ObserverGeometry.from_frame(frame, label=self.label)
            if frame is not None and self._axis_transform(frame, frame.data.shape) is not None
            else None
        )
        self._refresh_limb()

    def current_frame(self) -> Any | None:
        if not self.frames:
            return None
        return self.frames[max(0, min(self._index, len(self.frames) - 1))]

    def current_time(self) -> datetime | None:
        frame = self.current_frame()
        return frame_observation_time(frame) if frame is not None else None

    # ------------------------------------------------------------ overlays
    def set_limb_visible(self, visible: bool) -> None:
        self._limb_visible = bool(visible)
        self._refresh_limb()

    def limb_visible(self) -> bool:
        return self._limb_visible

    def _refresh_limb(self) -> None:
        """The photospheric limb, from this observer's apparent solar radius.

        Behind a coronagraph's occulter the disk itself is never seen, which is
        exactly why the outline helps: it shows where the Sun is and how large,
        the reference a CME's height is judged against.
        """
        observer = self.observer
        if not self._limb_visible or observer is None or not observer.rsun_arcsec:
            self.canvas.set_aia_limb_overlay(None, None, visible=False)
            return
        theta = np.linspace(0.0, 2.0 * math.pi, _LIMB_SAMPLES)
        radius = float(observer.rsun_arcsec)
        self.canvas.set_aia_limb_overlay(radius * np.cos(theta), radius * np.sin(theta), visible=True)

    def set_axes_visible(self, visible: bool) -> None:
        self.canvas.set_map_chrome_visible(bool(visible))

    def set_caption_visible(self, visible: bool) -> None:
        """Show or hide the information banner drawn across the top of the image."""
        self.title_label.setVisible(visible)

    def caption_visible(self) -> bool:
        return self.title_label.isVisible()

    # ------------------------------------------------------------ rendering
    def render(self) -> None:
        """Redraw the image for the current index, contrast and colormap."""
        if not self.frames:
            self.canvas.clear_plot()
            self.canvas.clear_model_overlays()
            if not self.is_fetching():
                self.title_label.setText(f"{self.label} · not loaded")
            return
        index = max(0, min(self._index, len(self.frames) - 1))
        data = self.display_array(index)
        mode = self.difference_mode()
        rendered_mode = self.rendered_mode()
        self.low_slider.setEnabled(rendered_mode == "raw")
        vmin, vmax = self._current_levels(index, data, mode, rendered_mode)

        self.canvas.set_colormap_name(self.colormap_combo.currentText())
        frame = self.frames[index]
        self.canvas.plot_map_data(
            data,
            title="",
            vmin=vmin,
            vmax=vmax,
            axis_transform=self._axis_transform(frame, data.shape),
        )
        when = frame_observation_time(frame)
        # Short enough for the smallest panel: the view mode and the full date are
        # already in the toolbar, so the caption carries only what differs per
        # panel — and says so when this panel cannot show what the toolbar asks.
        stamp = f"{when:%Y-%m-%d %H:%M:%S} UTC" if when else "time unknown"
        caption = f"{self.label} · {self._frame_name(frame)}\n{stamp} · {index + 1}/{len(self.frames)}"
        offset = self.time_offset_seconds()
        if offset is not None:
            caption += f" · Δt {offset / 60:+.1f} min"
            if not self.is_synchronized():
                caption += " · outside tolerance"
        if self._difference_mode != "raw" and not self.can_difference():
            caption += f" · {dict(DIFFERENCE_MODES)[self._difference_mode].lower()} unavailable — 1 frame"
        elif self._difference_failed and mode != "raw":
            caption += f" · {dict(DIFFERENCE_MODES)[mode].lower()} failed — showing raw"
        elif mode == "running" and self.difference_reference(index) is None:
            caption += "\nRunning difference unavailable — no earlier frame; showing raw"
        elif mode == "base" and index == 0:
            caption += "\nBase reference frame — showing raw (self-difference is zero)"
        elif mode != "raw":
            reference = self.difference_reference(index)
            reference_time = frame_observation_time(reference)
            reference_stamp = f"{reference_time:%Y-%m-%d %H:%M:%S} UTC" if reference_time else "unknown time"
            caption += f"\n{dict(DIFFERENCE_MODES)[mode]} · reference {reference_stamp}"
        if self.observer is None:
            caption += "\nProjection unavailable — no usable solar WCS/observer"
        self.title_label.setText(caption)

    def _current_levels(
        self, index: int, data: np.ndarray, mode: str, rendered_mode: str
    ) -> tuple[float | None, float | None]:
        """The display range of frame ``index`` at the current sliders, cached."""
        levels_key = (
            mode,
            index,
            round(float(self.low_slider.value()), 3),
            round(float(self.high_slider.value()), 3),
            bool(self._difference_failed),
        )
        levels = self._levels_cache.get(levels_key)
        if levels is None:
            levels = self._levels(data, rendered_mode)
            self._levels_cache[levels_key] = levels
        return levels

    def export_render(self) -> Any:
        """The image on screen as data, for the exports: never a grab of the screen.

        Same array, display range, colour table, placement and caption the canvas
        draws, so an export matches the panel exactly. With hardware acceleration
        the canvas is an OpenGL surface, which a widget grab returns blank.
        The window adds the model overlays.
        """
        from src.backend.gcs.gcs_figures import ViewpointRender

        if not self.frames:
            return ViewpointRender(label=self.label, caption=self.title_label.text() or f"{self.label} · not loaded")
        index = max(0, min(self._index, len(self.frames) - 1))
        data = self.display_array(index)
        vmin, vmax = self._current_levels(index, data, self.difference_mode(), self.rendered_mode())
        frame = self.frames[index]
        transform = self._axis_transform(frame, data.shape)
        extent = None
        if transform is not None:
            x_scale = transform["x_scale_arcsec_per_pix"]
            y_scale = transform["y_scale_arcsec_per_pix"]
            # The canvas's own placement (SunPyPlotCanvas._map_rect_from_transform):
            # pixel centres on the WCS, so the image spans half a pixel beyond them.
            x0 = transform["x_ref_arcsec"] - (transform["x_ref_pix"] + 0.5) * x_scale
            y0 = transform["y_ref_arcsec"] - (transform["y_ref_pix"] + 0.5) * y_scale
            extent = (x0, x0 + data.shape[1] * x_scale, y0, y0 + data.shape[0] * y_scale)
        observer = self.observer
        return ViewpointRender(
            label=self.label,
            caption=self.title_label.text(),
            data=data,
            extent=extent,
            lut=self.canvas.map_lut(),
            vmin=vmin,
            vmax=vmax,
            limb_radius_arcsec=(
                float(observer.rsun_arcsec)
                if self._limb_visible and observer is not None and observer.rsun_arcsec
                else None
            ),
        )

    def _levels(self, data: np.ndarray, mode: str) -> tuple[float | None, float | None]:
        """Display range: percentile stretch for raw frames, symmetric for differences."""
        low = float(self.low_slider.value())
        high = float(self.high_slider.value())
        if high <= low:
            high = min(100.0, low + 0.5)
        if mode == "raw" or self._difference_failed:
            # Percentiles over the field only: the zeroed occulter and corners
            # would otherwise pin the low end to 0 and waste half the scale.
            in_field = data[(data > 0) & np.isfinite(data)]
            if in_field.size == 0:
                in_field = data[np.isfinite(data)]
            if in_field.size == 0:
                return None, None
            vmin = float(np.percentile(in_field, low))
            vmax = float(np.percentile(in_field, high))
            return (vmin, vmax) if vmax > vmin else (None, None)
        # A difference image is signed and centred on zero, so the clip is
        # symmetric and mid-scale means "no change".
        extent = hvjp2.symmetric_clip(data, high)
        return -extent, extent

    @staticmethod
    def _frame_name(frame: Any) -> str:
        """Name the frame from its own header, not from the channel combo."""
        parts = [
            str(getattr(frame, "observatory", "") or "").strip(),
            str(getattr(frame, "detector", "") or "").strip(),
        ]
        if not parts[1]:
            parts[1] = str(getattr(frame, "instrument", "") or "").strip()
        name = " ".join(part for part in parts if part)
        return name or "unknown"

    @staticmethod
    def _axis_transform(frame: Any, _shape: tuple[int, ...]) -> dict | None:
        """Pixel-to-arcsec mapping for the canvas, from the map's own WCS.

        Valid as a plain linear mapping because every frame here is north up:
        STEREO JP2s carry no roll, and LASCO JP2s have already been rotated by
        Helioviewer (see ``src.backend.solar.helioviewer_jp2``).
        """
        import astropy.units as u

        try:
            scale = frame.scale
            reference_pixel = frame.reference_pixel
            reference_coord = frame.reference_coordinate
            rotation = np.asarray(frame.rotation_matrix, dtype=float)
            if rotation.shape != (2, 2) or not np.allclose(rotation, np.eye(2), atol=1e-8):
                return None
            transform = {
                "x_ref_pix": float(reference_pixel.x.to_value(u.pix)),
                "y_ref_pix": float(reference_pixel.y.to_value(u.pix)),
                "x_scale_arcsec_per_pix": float(scale.axis1.to_value(u.arcsec / u.pix)),
                "y_scale_arcsec_per_pix": float(scale.axis2.to_value(u.arcsec / u.pix)),
                "x_ref_arcsec": float(reference_coord.Tx.to_value(u.arcsec)),
                "y_ref_arcsec": float(reference_coord.Ty.to_value(u.arcsec)),
            }
            if not all(math.isfinite(value) for value in transform.values()):
                return None
            if not transform["x_scale_arcsec_per_pix"] or not transform["y_scale_arcsec_per_pix"]:
                return None
            return transform
        except Exception:
            # A frame without a usable WCS still renders, just in pixel space —
            # the GCS overlay hides itself for the same frames (ObserverGeometry
            # returns None), so the two can never disagree.
            return None

    # -------------------------------------------------------------- fetching
    def start_fetch(self) -> bool:
        """Load this channel's frames for its date range. False if not started."""
        if self._thread is not None:
            self.statusChanged.emit(f"{self.label}: a load is already running.")
            return False
        jp2_source = self.source()
        if jp2_source is None:
            return False
        start, end = self.date_range()
        problem = hvjp2.validate_range(start, end)
        if problem:
            self.statusChanged.emit(f"{self.label}: {problem}")
            return False
        if not (jp2_source.available_on(start) or jp2_source.available_on(end)):
            reason = f"{jp2_source.label} has no data for {start:%Y-%m-%d}"
            if not self.frames:
                # Said on the image too: a default can be a spacecraft that was
                # lost (STEREO-B), and a blank panel must not look like a failure.
                self.title_label.setText(f"{self.label} · {reason}")
            self.statusChanged.emit(f"{self.label}: {reason} — pick another channel.")
            return False
        max_frames = hvjp2.DEFAULT_MAX_FRAMES
        if self.max_frames_provider is not None:
            try:
                max_frames = int(self.max_frames_provider())
            except Exception:
                pass
        worker = JP2FetchWorker(
            jp2_source, start, end, max_frames=max_frames, cache_dir=jp2_cache_dir(self._cache_base)
        )
        delivery = _FetchDelivery(self, worker)
        worker.progress.connect(delivery.progress)
        worker.finished.connect(delivery.finished)
        worker.failed.connect(delivery.failed)
        worker.cancelled.connect(delivery.cancelled)
        self.fetch_btn.setEnabled(False)
        self.frames_label.setText("Loading…")
        self._on_progress(f"Searching {jp2_source.label} {start:%m-%d %H:%M} → {end:%m-%d %H:%M}…")
        self._launch(worker)
        self.busyChanged.emit(True)
        return True

    def _on_progress(self, text: str) -> None:
        if not self._accept_worker_signal():
            return
        if not self.frames:
            self.title_label.setText(f"{self.label} · {text}")
        self.statusChanged.emit(f"{self.label}: {text}")

    def _finish_fetch(self) -> None:
        self._teardown()
        self.fetch_btn.setEnabled(True)
        self.busyChanged.emit(False)

    def _on_fetch_finished(self, sequence: Any) -> None:
        if not self._accept_worker_signal():
            return
        self._finish_fetch()
        frames = list(getattr(sequence, "frames", ()) or ())
        if not frames:
            self.set_frames([])
            self.frames_label.setText("none")
            self.statusChanged.emit(f"{self.label}: nothing could be loaded.")
            return
        self.set_frames(frames, harmonised=True, previous=getattr(sequence, "previous", None))
        notes = [f"{len(frames)} frame(s)"]
        listed = int(getattr(sequence, "listed", 0) or 0)
        if listed > len(frames):
            notes.append(f"thinned from {listed} in range")
        if getattr(sequence, "from_cache", 0):
            notes.append(f"{sequence.from_cache} from cache")
        if getattr(sequence, "skipped", ()):
            notes.append(f"{len(sequence.skipped)} failed to download")
        if getattr(sequence, "dropped", 0):
            notes.append(f"{sequence.dropped} dropped (incompatible size)")
        source_label = getattr(getattr(sequence, "source", None), "label", "")
        self.statusChanged.emit(f"{self.label}: loaded {source_label} — {', '.join(notes)}.")

    def _on_failed(self, message: str) -> None:
        if not self._accept_worker_signal():
            return
        self._finish_fetch()
        last = [line for line in str(message).strip().splitlines() if line.strip()]
        text = last[-1] if last else "load failed"
        self.frames_label.setText(f"{len(self.frames)} frames" if self.frames else "failed")
        if not self.frames:
            self.title_label.setText(f"{self.label} · {text}")
        self.statusChanged.emit(f"{self.label}: {text}")

    def _on_cancelled(self) -> None:
        if not self._accept_worker_signal():
            return
        self._finish_fetch()
        self.frames_label.setText(f"{len(self.frames)} frames" if self.frames else "—")
        self.statusChanged.emit(f"{self.label}: load cancelled.")
        self.render()

    def _accept_worker_signal(self) -> bool:
        """Ignore a queued completion belonging to an invalidated request."""
        sender = self.sender()
        return not isinstance(sender, JP2FetchWorker) or sender is self._worker

    def cancel_fetch(self, *, discard_result: bool = False) -> None:
        """Cancel the active request; optionally detach it so a new one may start.

        Detached threads retain their own lifetime/cleanup connections and may
        finish a network request in the background. Their queued results must
        never replace images belonging to a different event or channel.
        """
        worker = self._worker
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
        if not discard_result:
            return
        was_fetching = self._thread is not None or worker is not None
        self._teardown()
        self.fetch_btn.setEnabled(True)
        self.frames_label.setText(f"{len(self.frames)} frames" if self.frames else "—")
        if was_fetching:
            self.busyChanged.emit(False)

    def _launch(self, worker: QObject) -> None:
        """Start ``worker`` on its own thread, wired to clean itself up.

        The thread is deliberately **not** parented to this widget, and nothing
        ever calls ``deleteLater`` on it directly. Qt aborts the process if a
        QThread is destroyed while still running, and a download blocks inside
        ``run()`` for far longer than any timeout worth waiting out. The thread's
        own ``finished`` signal drives the deletion, which by definition only
        fires once ``run()`` has returned.
        """
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        for name in ("finished", "failed", "cancelled", "search_finished", "load_finished"):
            signal = getattr(worker, name, None)
            if signal is not None and hasattr(signal, "connect"):
                signal.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda t=thread: _forget_thread(t))
        _track_thread(thread)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _teardown(self) -> None:
        """Detach from a worker that has already signalled completion."""
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()

    def is_fetching(self) -> bool:
        """True while a load is in flight for this panel."""
        thread = self._thread
        try:
            return thread is not None and thread.isRunning()
        except RuntimeError:
            return False

    def shutdown(self, *, wait_ms: int = 8000) -> None:
        """Cancel any in-flight load and join its thread. Call before closing.

        Cancelling matters more than waiting: the loader checks its cancel flag
        between requests and during retry back-off, so a cancelled load unwinds
        quickly, where an uncancelled one would run to completion and be destroyed
        underneath us.
        """
        worker = self._worker
        if worker is not None and hasattr(worker, "cancel"):
            try:
                worker.cancel()
            except Exception:
                pass
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is None:
            return
        try:
            thread.quit()
            thread.wait(int(wait_ms))
        except RuntimeError:
            pass  # already deleted by the finished chain
