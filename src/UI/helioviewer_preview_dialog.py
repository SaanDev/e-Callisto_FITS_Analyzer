"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Near-real-time SOHO/LASCO quicklook preview and time-range movie (Helioviewer).

These are browse images (PNG, with the standard LASCO colour table) updated
within ~an hour — the fresh view the calibrated, months-behind VSO/SDAC FITS
archive cannot provide. The dialog opens on the newest still frame; from there
the user can build a flip-book "movie" over a chosen time range and step and
play/scrub through it. This is situational-awareness imagery, not analysis-grade
FITS, so it lives in its own dialog rather than the FITS analysis canvas.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from PySide6.QtCore import QDateTime, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.Backend.helioviewer import (
    HelioviewerFrame,
    HelioviewerPreview,
    fetch_frame_sequence,
    fetch_preview,
)

_PREVIEW_PX = 512


class _StillWorker(QObject):
    finished = Signal(object)   # HelioviewerPreview
    failed = Signal(str)

    def __init__(self, detector: str, size_px: int = _PREVIEW_PX):
        super().__init__()
        self._detector = str(detector)
        self._size_px = int(size_px)

    @Slot()
    def run(self):
        try:
            self.finished.emit(fetch_preview(self._detector, size_px=self._size_px))
        except Exception as exc:  # noqa: BLE001 - surface network/API errors to the UI
            self.failed.emit(str(exc))


class _MovieWorker(QObject):
    progress = Signal(int, int)     # done, total
    finished = Signal(object)       # list[HelioviewerFrame]
    failed = Signal(str)

    def __init__(self, detector, start, end, step_seconds, max_frames, size_px=_PREVIEW_PX):
        super().__init__()
        self._detector = str(detector)
        self._start = start
        self._end = end
        self._step = float(step_seconds)
        self._max_frames = int(max_frames)
        self._size_px = int(size_px)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    @Slot()
    def run(self):
        try:
            frames = fetch_frame_sequence(
                self._detector, self._start, self._end,
                step_seconds=self._step, size_px=self._size_px, max_frames=self._max_frames,
                progress_cb=lambda done, total, _dt: self.progress.emit(int(done), int(total)),
                cancel_cb=self._cancel.is_set,
            )
            self.finished.emit(frames)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class HelioviewerPreviewDialog(QDialog):
    """Newest LASCO C2/C3 quicklook still, plus a time-range flip-book movie."""

    def __init__(self, parent: QWidget | None = None, *, detector: str = "C2", theme: Any | None = None):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("SOHO/LASCO Near-Real-Time Preview (Helioviewer)")
        self.resize(640, 820)

        self._preview: HelioviewerPreview | None = None
        self._frames: list[HelioviewerFrame] = []
        self._frame_index = 0
        self._range_initialized = False
        self._cancel_requested = False

        self._thread: QThread | None = None
        self._worker: QObject | None = None

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_play_tick)

        self._build_ui()
        det = str(detector or "C2").strip().upper()
        idx = self.detector_combo.findData(det)
        if idx >= 0:
            self.detector_combo.setCurrentIndex(idx)
        self._refresh_playback_controls()
        self._start_still_fetch()

    # -- UI construction --------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Detector:"))
        self.detector_combo = QComboBox()
        self.detector_combo.addItem("SOHO/LASCO C2", userData="C2")
        self.detector_combo.addItem("SOHO/LASCO C3", userData="C3")
        top.addWidget(self.detector_combo)
        top.addStretch(1)
        self.refresh_btn = QPushButton("Latest Frame")
        self.refresh_btn.setToolTip("Reload the newest available quicklook still.")
        top.addWidget(self.refresh_btn)
        layout.addLayout(top)

        self.image_label = QLabel("Loading...")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(_PREVIEW_PX, _PREVIEW_PX)
        self.image_label.setStyleSheet("QLabel { background: #000; color: #ccc; border-radius: 4px; }")
        layout.addWidget(self.image_label, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.RichText)
        layout.addWidget(self.status_label)

        layout.addWidget(self._build_movie_group())
        layout.addLayout(self._build_playback_row())

        bottom = QHBoxLayout()
        self.open_browser_btn = QPushButton("Open in Browser")
        self.open_browser_btn.setEnabled(False)
        bottom.addWidget(self.open_browser_btn)
        bottom.addStretch(1)
        self.close_btn = QPushButton("Close")
        bottom.addWidget(self.close_btn)
        layout.addLayout(bottom)

        self.detector_combo.currentIndexChanged.connect(self._on_detector_changed)
        self.refresh_btn.clicked.connect(self._start_still_fetch)
        self.build_btn.clicked.connect(self._build_movie)
        self.cancel_btn.clicked.connect(self._cancel_fetch)
        self.open_browser_btn.clicked.connect(self._open_in_browser)
        self.close_btn.clicked.connect(self.close)

    def _build_movie_group(self) -> QGroupBox:
        group = QGroupBox("Movie (time range · step)")
        grid = QGridLayout(group)

        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        self.start_edit = QDateTimeEdit(QDateTime(now - timedelta(hours=6)))
        self.end_edit = QDateTimeEdit(QDateTime(now))
        for edit in (self.start_edit, self.end_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd HH:mm")

        self.step_spin = QSpinBox()
        self.step_spin.setRange(2, 1440)
        self.step_spin.setValue(30)
        self.step_spin.setSuffix(" min")
        self.step_spin.setToolTip("Time step between frames. Steps finer than the ~12 min LASCO cadence are de-duplicated.")

        self.max_frames_spin = QSpinBox()
        self.max_frames_spin.setRange(2, 120)
        self.max_frames_spin.setValue(48)
        self.max_frames_spin.setToolTip("Cap on the number of frames fetched (protects the Helioviewer service).")

        self.build_btn = QPushButton("Build Movie")
        self.build_btn.setToolTip("Fetch one Helioviewer frame per step across the selected range and play them.")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)

        self.movie_status_label = QLabel("")
        self.movie_status_label.setWordWrap(True)

        grid.addWidget(QLabel("Start (UTC)"), 0, 0)
        grid.addWidget(self.start_edit, 0, 1)
        grid.addWidget(QLabel("End (UTC)"), 0, 2)
        grid.addWidget(self.end_edit, 0, 3)
        grid.addWidget(QLabel("Step"), 1, 0)
        grid.addWidget(self.step_spin, 1, 1)
        grid.addWidget(QLabel("Max frames"), 1, 2)
        grid.addWidget(self.max_frames_spin, 1, 3)
        grid.addWidget(self.build_btn, 2, 0, 1, 2)
        grid.addWidget(self.cancel_btn, 2, 2, 1, 2)
        grid.addWidget(self.movie_status_label, 3, 0, 1, 4)
        return group

    def _build_playback_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.prev_btn = QPushButton("◀")
        self.play_btn = QPushButton("Play")
        self.pause_btn = QPushButton("Pause")
        self.next_btn = QPushButton("▶")
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setRange(0, 0)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(1.0, 30.0)
        self.fps_spin.setDecimals(0)
        self.fps_spin.setValue(6.0)
        self.fps_spin.setSuffix(" FPS")
        for widget in (self.prev_btn, self.play_btn, self.pause_btn, self.next_btn):
            widget.setMaximumWidth(70)
        row.addWidget(self.prev_btn)
        row.addWidget(self.play_btn)
        row.addWidget(self.pause_btn)
        row.addWidget(self.next_btn)
        row.addWidget(self.frame_slider, 1)
        row.addWidget(self.fps_spin)

        self.prev_btn.clicked.connect(lambda: self._step_frame(-1))
        self.next_btn.clicked.connect(lambda: self._step_frame(1))
        self.play_btn.clicked.connect(self._play)
        self.pause_btn.clicked.connect(self._pause)
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        return row

    # -- State helpers ----------------------------------------------------
    def _current_detector(self) -> str:
        return str(self.detector_combo.currentData() or "C2")

    def _is_loading(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.refresh_btn, self.detector_combo, self.build_btn,
            self.start_edit, self.end_edit, self.step_spin, self.max_frames_spin,
        ):
            widget.setEnabled(enabled)
        self.cancel_btn.setEnabled(not enabled)

    def _refresh_playback_controls(self) -> None:
        has_movie = len(self._frames) > 1
        playing = self._play_timer.isActive()
        loading = self._is_loading()
        self.frame_slider.setEnabled(has_movie and not loading)
        self.prev_btn.setEnabled(has_movie and not loading)
        self.next_btn.setEnabled(has_movie and not loading)
        self.fps_spin.setEnabled(has_movie)
        self.play_btn.setEnabled(has_movie and not playing and not loading)
        self.pause_btn.setEnabled(has_movie and playing)

    def _on_detector_changed(self, _index: int) -> None:
        self._range_initialized = False
        self._pause()
        self._start_still_fetch()

    # -- Still frame ------------------------------------------------------
    def _start_still_fetch(self) -> None:
        if self._is_loading():
            return
        detector = self._current_detector()
        self._cancel_requested = False
        self._set_inputs_enabled(False)
        self.open_browser_btn.setEnabled(False)
        self.image_label.setText(f"Loading latest SOHO/LASCO {detector} from Helioviewer...")
        self.status_label.setText("Contacting Helioviewer near-real-time service...")
        worker = _StillWorker(detector)
        worker.finished.connect(self._on_still_finished)
        worker.failed.connect(self._on_fetch_failed)
        self._start_thread(worker)

    @Slot(object)
    def _on_still_finished(self, preview_obj: object) -> None:
        preview = preview_obj if isinstance(preview_obj, HelioviewerPreview) else None
        if preview is None:
            self._on_fetch_failed("Unexpected preview payload.")
            return
        self._preview = preview
        # Movie playback (if any) owns the image; otherwise show the still.
        if not self._frames:
            if not self._set_image(preview.png_bytes):
                self._on_fetch_failed("Could not decode the preview image.")
                return
            self.status_label.setText(self._still_status_text(preview))
        self.open_browser_btn.setEnabled(True)
        self._init_default_range(preview.info.date)

    def _init_default_range(self, latest: datetime) -> None:
        if self._range_initialized:
            return
        self.end_edit.setDateTime(QDateTime(latest.replace(microsecond=0)))
        self.start_edit.setDateTime(QDateTime((latest - timedelta(hours=6)).replace(microsecond=0)))
        self._range_initialized = True

    def _still_status_text(self, preview: HelioviewerPreview) -> str:
        info = preview.info
        age = (datetime.now(timezone.utc).replace(tzinfo=None) - info.date).total_seconds()
        return (
            f"<b>{info.name}</b> &mdash; latest available frame<br>"
            f"Observed: {info.date:%Y-%m-%d %H:%M:%S} UTC ({self._format_age(age)})<br>"
            f"Source: Helioviewer near-real-time quicklook (browse image, not analysis-grade FITS)."
        )

    # -- Movie ------------------------------------------------------------
    def _build_movie(self) -> None:
        if self._is_loading():
            return
        start = self.start_edit.dateTime().toPython().replace(tzinfo=None)
        end = self.end_edit.dateTime().toPython().replace(tzinfo=None)
        if end <= start:
            self.movie_status_label.setText("End time must be after start time.")
            return
        detector = self._current_detector()
        step_seconds = int(self.step_spin.value()) * 60
        max_frames = int(self.max_frames_spin.value())

        self._pause()
        self._cancel_requested = False
        self._set_inputs_enabled(False)
        self.movie_status_label.setText(
            f"Building {detector} movie {start:%Y-%m-%d %H:%M} → {end:%H:%M} UTC ..."
        )
        worker = _MovieWorker(detector, start, end, step_seconds, max_frames)
        worker.progress.connect(self._on_movie_progress)
        worker.finished.connect(self._on_movie_finished)
        worker.failed.connect(self._on_fetch_failed)
        self._start_thread(worker)

    @Slot(int, int)
    def _on_movie_progress(self, done: int, total: int) -> None:
        self.movie_status_label.setText(f"Fetching frames: {done}/{total}...")
        self.image_label.setText(f"Fetching movie frames: {done}/{total}...")

    @Slot(object)
    def _on_movie_finished(self, frames_obj: object) -> None:
        frames = list(frames_obj) if isinstance(frames_obj, (list, tuple)) else []
        cancelled = self._cancel_requested
        if not frames:
            self.movie_status_label.setText(
                "Movie cancelled." if cancelled else "No frames were available for that range/step."
            )
            if self._preview is not None and self._set_image(self._preview.png_bytes):
                self.status_label.setText(self._still_status_text(self._preview))
            self._refresh_playback_controls()
            return
        self._frames = frames
        self._frame_index = 0
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, len(frames) - 1)
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)
        span = f"{frames[0].date:%Y-%m-%d %H:%M} → {frames[-1].date:%H:%M} UTC"
        suffix = " (cancelled early)" if cancelled else ""
        self.movie_status_label.setText(f"Loaded {len(frames)} frame(s): {span}{suffix}. Press Play.")
        self._render_frame(0)
        self._refresh_playback_controls()

    def _cancel_fetch(self) -> None:
        self._cancel_requested = True
        worker = self._worker
        if isinstance(worker, _MovieWorker):
            worker.cancel()
        self.cancel_btn.setEnabled(False)
        self.movie_status_label.setText("Cancelling...")

    # -- Playback ---------------------------------------------------------
    def _render_frame(self, index: int) -> None:
        if not self._frames:
            return
        index = max(0, min(int(index), len(self._frames) - 1))
        self._frame_index = index
        frame = self._frames[index]
        self._set_image(frame.png_bytes)
        detector = self._current_detector()
        self.status_label.setText(
            f"<b>SOHO/LASCO {detector} movie</b> &mdash; frame {index + 1}/{len(self._frames)}<br>"
            f"Observed: {frame.date:%Y-%m-%d %H:%M:%S} UTC<br>"
            f"Source: Helioviewer near-real-time quicklook."
        )
        if self.frame_slider.value() != index:
            self.frame_slider.blockSignals(True)
            self.frame_slider.setValue(index)
            self.frame_slider.blockSignals(False)

    def _on_slider_changed(self, value: int) -> None:
        self._render_frame(value)

    def _step_frame(self, delta: int) -> None:
        if self._frames:
            self._render_frame(self._frame_index + int(delta))

    def _play(self) -> None:
        if len(self._frames) <= 1:
            return
        if self._frame_index >= len(self._frames) - 1:
            self._render_frame(0)
        self._play_timer.start(self._frame_interval_ms())
        self._refresh_playback_controls()

    def _pause(self) -> None:
        self._play_timer.stop()
        self._refresh_playback_controls()

    def _on_play_tick(self) -> None:
        if not self._frames:
            self._pause()
            return
        if self._frame_index >= len(self._frames) - 1:
            self._pause()
            return
        self._render_frame(self._frame_index + 1)

    def _on_fps_changed(self, _value: float) -> None:
        if self._play_timer.isActive():
            self._play_timer.setInterval(self._frame_interval_ms())

    def _frame_interval_ms(self) -> int:
        fps = max(1.0, float(self.fps_spin.value() or 1.0))
        return max(1, int(round(1000.0 / fps)))

    # -- Shared -----------------------------------------------------------
    def _set_image(self, png_bytes: bytes) -> bool:
        pixmap = QPixmap()
        if not pixmap.loadFromData(png_bytes, "PNG"):
            return False
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.width(), self.image_label.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
        )
        return True

    @Slot(str)
    def _on_fetch_failed(self, message: str) -> None:
        if not self._frames:
            self.image_label.setText("Preview unavailable.")
        self.status_label.setText(
            f"<b>Could not load from Helioviewer.</b><br>{message}<br>"
            "Check your internet connection and try again."
        )
        self.movie_status_label.setText("")

    def _start_thread(self, worker: QObject) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        for signal_name in ("finished", "failed"):
            getattr(worker, signal_name).connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()
        self._refresh_playback_controls()

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_inputs_enabled(True)
        self._refresh_playback_controls()

    def _open_in_browser(self) -> None:
        url = ""
        if self._frames:
            url = self._frames[max(0, min(self._frame_index, len(self._frames) - 1))].image_url
        elif self._preview is not None:
            url = self._preview.image_url
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @staticmethod
    def _format_age(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 90:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} min ago"
        hours = minutes // 60
        rem = minutes % 60
        if hours < 48:
            return f"{hours} h {rem} min ago"
        return f"{hours // 24} day(s) ago"

    def closeEvent(self, event):
        self._play_timer.stop()
        worker = self._worker
        if isinstance(worker, _MovieWorker):
            worker.cancel()
        thread = self._thread
        if thread is not None:
            try:
                thread.quit()
                thread.wait(1500)
            except Exception:
                pass
        super().closeEvent(event)
