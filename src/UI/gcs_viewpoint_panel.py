"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

One viewpoint of the GCS fitting window (src/UI/gcs_viewpoint_panel.py).

A panel is its image and nothing else on screen: the plot's axes and title row
are hidden so every pixel of the square goes to the Sun, and the caption is drawn
on the image itself. The panel still *owns* its controls — channel, date range,
fetch, colormap and contrast — but does not place them; the window mounts them in
its control cards, which is what lets the images take all the space the window
can give them.

Only Helioviewer JPEG2000 frames are loaded. The loading, orientation,
co-registration and differencing live in ``src.Backend.helioviewer_jp2``; this
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

from src.Backend import helioviewer_jp2 as hvjp2
from src.Backend.gcs_model import ObserverGeometry
from src.Backend.solar_data_analysis import frame_observation_time
from src.UI.sunpy_plot_window import SunPyPlotCanvas

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
    return value.toPython().replace(tzinfo=None) if hasattr(value, "toPython") else value


def jp2_cache_dir(base: Any = None) -> Path:
    """Where downloaded JP2s are kept, under the application's archive cache."""
    if base:
        root = Path(base)
    else:
        from src.UI.sunpy_solar_viewer import _default_cache_dir

        root = _default_cache_dir()
    path = Path(root) / "helioviewer_jp2"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


class JP2FetchWorker(QObject):
    """Runs :func:`src.Backend.helioviewer_jp2.fetch_range` off the GUI thread."""

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
        self._item.setParentItem(canvas.map_plot.getViewBox())
        self._item.setPos(6, 6)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._text = str(text)
        self._item.setText(self._text)


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
        self.observer: ObserverGeometry | None = None
        self._display_cache: dict[tuple[str, int], np.ndarray] = {}
        #: Display ranges, which only change with the frame, mode or sliders. The
        #: percentile behind each costs ~15 ms on a 1024-pixel frame.
        self._levels_cache: dict[tuple, tuple[float | None, float | None]] = {}
        self._difference_mode = DEFAULT_DIFFERENCE_MODE
        self._index = 0
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._target_time: datetime | None = None
        self._difference_failed = ""
        self._limb_visible = True
        #: Set by the window: the cap on frames per channel for a range fetch.
        self.max_frames_provider: Callable[[], int] | None = None

        self._build_image()
        self._build_controls()
        self.select_source(source_key)

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
        from src.UI.solar_measure_tools import GCSParameterSlider

        self.source_combo = QComboBox()
        for jp2_source in hvjp2.GCS_JP2_SOURCES:
            self.source_combo.addItem(jp2_source.label, userData=jp2_source.key)
        self.source_combo.setToolTip(
            "Coronagraph to load, as Helioviewer JPEG2000 frames. Entries a spacecraft\n"
            "could not have observed in this channel's date range are disabled."
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
        self.start_edit = QDateTimeEdit(QDateTime(now - timedelta(hours=1)))
        self.end_edit = QDateTimeEdit(QDateTime(now + timedelta(hours=1)))
        for edit, which in ((self.start_edit, "first"), (self.end_edit, "last")):
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
        self.start_edit.setDateTime(QDateTime(start.replace(tzinfo=None, microsecond=0)))
        self.end_edit.setDateTime(QDateTime(end.replace(tzinfo=None, microsecond=0)))

    # -------------------------------------------------------------- frames
    def set_frames(self, frames: list[Any], *, harmonised: bool = False) -> None:
        """Adopt a sequence: time-ordered, on one pixel grid, ready to difference.

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
        if dropped:
            self.statusChanged.emit(
                f"{self.label}: dropped {dropped} frame(s) that could not share the sequence's "
                "pixel grid."
            )
        self.frames = ordered
        self.observer = (
            ObserverGeometry.from_frame(ordered[0], label=self.label) if ordered else None
        )
        self._index = 0
        self._display_cache.clear()
        self._levels_cache.clear()
        self._difference_failed = ""
        if ordered:
            self._difference_mode = DEFAULT_DIFFERENCE_MODE
            was = self.colormap_combo.blockSignals(True)
            self.colormap_combo.setCurrentText(DEFAULT_COLORMAP)
            self.colormap_combo.blockSignals(was)
        self.frames_label.setText(f"{len(ordered)} frame{'s' if len(ordered) != 1 else ''}" if ordered else "—")
        self._refresh_limb()
        self.framesChanged.emit(self)
        self.render()

    def set_difference_mode(self, mode: str) -> None:
        self._difference_mode = str(mode or "raw")
        self._difference_failed = ""
        self.render()

    def can_difference(self) -> bool:
        """True when this panel has enough frames to form a difference image."""
        return len(self.frames) >= 2

    def difference_mode(self) -> str:
        """The mode actually in effect, which is ``raw`` when it cannot difference."""
        if self._difference_mode != "raw" and not self.can_difference():
            return "raw"
        return self._difference_mode

    def frame_count(self) -> int:
        return len(self.frames)

    def display_array(self, index: int) -> np.ndarray:
        """The image drawn for ``index`` in the current mode, computed on demand.

        Difference frames cost ~25 ms each to build, so they are made the first
        time they are shown and then cached; switching modes is therefore instant
        and a playback pass pays the cost once per frame. A failure falls back to
        the raw frame *and says so*.
        """
        index = max(0, min(int(index), len(self.frames) - 1))
        mode = self.difference_mode()
        key = (mode, index)
        cached = self._display_cache.get(key)
        if cached is not None:
            return cached

        frame = self.frames[index]
        if mode == "raw":
            image = np.asarray(frame.data, dtype=np.float32)
        else:
            try:
                if mode == "running":
                    if index == 0:
                        current, reference = self.frames[1], self.frames[0]
                    else:
                        current, reference = frame, self.frames[index - 1]
                else:
                    current, reference = frame, self.frames[0]
                image = hvjp2.difference_image(current, reference)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                if message != self._difference_failed:
                    self.statusChanged.emit(f"{self.label}: differencing failed ({message}); showing raw.")
                self._difference_failed = message
                image = np.asarray(frame.data, dtype=np.float32)

        if len(self._display_cache) > 4 * max(1, len(self.frames)):
            self._display_cache.clear()
            self._levels_cache.clear()
        self._display_cache[key] = image
        return image

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
        self._target_time = when
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
        self.render()

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

    # ------------------------------------------------------------ rendering
    def render(self) -> None:
        """Redraw the image for the current index, contrast and colormap."""
        if not self.frames:
            self.canvas.clear_gcs_overlay()
            if not self.is_fetching():
                self.title_label.setText(f"{self.label} · not loaded")
            return
        index = max(0, min(self._index, len(self.frames) - 1))
        data = self.display_array(index)
        mode = self.difference_mode()
        levels_key = (
            mode,
            index,
            round(float(self.low_slider.value()), 3),
            round(float(self.high_slider.value()), 3),
            bool(self._difference_failed),
        )
        levels = self._levels_cache.get(levels_key)
        if levels is None:
            levels = self._levels(data, mode)
            self._levels_cache[levels_key] = levels
        vmin, vmax = levels

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
        stamp = f"{when:%m-%d %H:%M:%S}" if when else "—"
        caption = f"{self.label} · {self._frame_name(frame)} · {stamp} · {index + 1}/{len(self.frames)}"
        if self._difference_mode != "raw" and not self.can_difference():
            caption += f" · {dict(DIFFERENCE_MODES)[self._difference_mode].lower()} unavailable — 1 frame"
        elif self._difference_failed and mode != "raw":
            caption += f" · {dict(DIFFERENCE_MODES)[mode].lower()} failed — showing raw"
        self.title_label.setText(caption)

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
        Helioviewer (see ``src.Backend.helioviewer_jp2``).
        """
        import astropy.units as u

        try:
            scale = frame.scale
            reference_pixel = frame.reference_pixel
            reference_coord = frame.reference_coordinate
            return {
                "x_ref_pix": float(reference_pixel.x.to_value(u.pix)),
                "y_ref_pix": float(reference_pixel.y.to_value(u.pix)),
                "x_scale_arcsec_per_pix": float(scale.axis1.to_value(u.arcsec / u.pix)),
                "y_scale_arcsec_per_pix": float(scale.axis2.to_value(u.arcsec / u.pix)),
                "x_ref_arcsec": float(reference_coord.Tx.to_value(u.arcsec)),
                "y_ref_arcsec": float(reference_coord.Ty.to_value(u.arcsec)),
            }
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
            self.statusChanged.emit(
                f"{self.label}: {jp2_source.label} has no data for {start:%Y-%m-%d} — pick another channel."
            )
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
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_fetch_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        self.fetch_btn.setEnabled(False)
        self.frames_label.setText("Loading…")
        self._on_progress(f"Searching {jp2_source.label} {start:%m-%d %H:%M} → {end:%m-%d %H:%M}…")
        self._launch(worker)
        self.busyChanged.emit(True)
        return True

    def _on_progress(self, text: str) -> None:
        if not self.frames:
            self.title_label.setText(f"{self.label} · {text}")
        self.statusChanged.emit(f"{self.label}: {text}")

    def _finish_fetch(self) -> None:
        self._teardown()
        self.fetch_btn.setEnabled(True)
        self.busyChanged.emit(False)

    def _on_fetch_finished(self, sequence: Any) -> None:
        self._finish_fetch()
        frames = list(getattr(sequence, "frames", ()) or ())
        if not frames:
            self.frames_label.setText("none")
            self.statusChanged.emit(f"{self.label}: nothing could be loaded.")
            return
        self.set_frames(frames, harmonised=True)
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
        self._finish_fetch()
        last = [line for line in str(message).strip().splitlines() if line.strip()]
        text = last[-1] if last else "load failed"
        self.frames_label.setText(f"{len(self.frames)} frames" if self.frames else "failed")
        if not self.frames:
            self.title_label.setText(f"{self.label} · {text}")
        self.statusChanged.emit(f"{self.label}: {text}")

    def _on_cancelled(self) -> None:
        self._finish_fetch()
        self.frames_label.setText(f"{len(self.frames)} frames" if self.frames else "—")
        self.statusChanged.emit(f"{self.label}: load cancelled.")
        self.render()

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
