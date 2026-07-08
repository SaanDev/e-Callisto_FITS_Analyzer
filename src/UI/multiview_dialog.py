"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Compare Viewpoint dialog: multi-viewpoint solar comparison.

STEREO-A/B observe the Sun far from the Sun–Earth line, so the same eruption
looks completely different from STEREO than from an Earth-view imager (AIA,
LASCO, SUVI). This dialog keeps the analyzer's currently loaded frame as
viewpoint A, fetches a second observable near the same observation time
(viewpoint B), reprojects B onto A's world coordinates
(``multiview.reproject_map_to``) and shows the two side by side with a blink
toggle and the observers' heliographic separation.

Search/fetch reuse the shared ``SunPyWorker``; reprojection runs in its own
worker thread because ``reproject`` can take seconds on large maps.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from src.Backend.multiview import observer_separation_deg, reproject_map_to
from src.Backend.solar_data_analysis import extract_map_frames, frame_observation_time
from src.Backend.sunpy_archive import SunPyQuerySpec, SunPySearchRow
from src.UI.sunpy_plot_window import SunPyPlotCanvas


def build_spec_for_observable(
    instrument: str,
    value: Any,
    start_dt: datetime,
    end_dt: datetime,
    *,
    max_records: int = 24,
) -> SunPyQuerySpec:
    """Query spec for an observable-combo selection (same userData convention
    as the main window's selector)."""
    if instrument == "HMI":
        return SunPyQuerySpec(
            start_dt=start_dt, end_dt=end_dt, spacecraft="SDO", instrument="HMI",
            product=str(value), max_records=max_records,
        )
    if instrument == "LASCO":
        return SunPyQuerySpec(
            start_dt=start_dt, end_dt=end_dt, spacecraft="SOHO", instrument="LASCO",
            detector=str(value), max_records=max_records,
        )
    if instrument == "SECCHI":
        spacecraft, detector, wavelength = value
        return SunPyQuerySpec(
            start_dt=start_dt, end_dt=end_dt, spacecraft=str(spacecraft), instrument="SECCHI",
            detector=str(detector),
            wavelength_angstrom=float(wavelength) if wavelength else None,
            max_records=max_records,
        )
    if instrument == "SUVI":
        # GOES-18 serves the operational SUVI for current dates (see registry).
        return SunPyQuerySpec(
            start_dt=start_dt, end_dt=end_dt, spacecraft="GOES", instrument="SUVI",
            wavelength_angstrom=float(value), satellite_number=18, level="1b",
            max_records=max_records,
        )
    return SunPyQuerySpec(
        start_dt=start_dt, end_dt=end_dt, spacecraft="SDO", instrument="AIA",
        wavelength_angstrom=float(value or 193.0), max_records=max_records,
    )


class ReprojectWorker(QObject):
    """Reprojects map B onto map A's frame off the UI thread."""

    finished = Signal(object, object)  # (reprojected_map_b, separation_deg_or_None)
    failed = Signal(str)

    def __init__(self, map_a: Any, map_b: Any):
        super().__init__()
        self.map_a = map_a
        self.map_b = map_b

    @Slot()
    def run(self):
        try:
            reprojected = reproject_map_to(self.map_b, self.map_a)
            try:
                separation = observer_separation_deg(self.map_a, self.map_b)
            except Exception:
                separation = None
            self.finished.emit(reprojected, separation)
        except Exception:
            self.failed.emit(traceback.format_exc())


class MultiViewpointDialog(QDialog):
    """Side-by-side + blink comparison of two viewpoints of the same time."""

    def __init__(
        self,
        parent: Any = None,
        *,
        reference_frames: list[Any],
        reference_index: int = 0,
        reference_label: str = "Loaded view",
        cache_dir: str | Path | None = None,
        jsoc_email: str = "",
        theme: Any | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Compare Viewpoint")
        self.resize(1080, 620)

        self._window_ref = parent
        self._reference_frames = list(reference_frames or [])
        self._reference_index = max(0, min(int(reference_index), max(0, len(self._reference_frames) - 1)))
        self._reference_label = str(reference_label or "Loaded view")
        self._cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self._jsoc_email = str(jsoc_email or "")
        self.theme = theme

        self._search_result = None
        self._map_b: Any | None = None
        self._map_b_reprojected: Any | None = None
        self._active_thread: QThread | None = None
        self._active_worker: QObject | None = None
        self._blink_showing_b = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._on_blink_tick)

        self._build_ui()
        self._render_reference()

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        controls = QGridLayout()
        controls.setHorizontalSpacing(8)
        self.observable_combo = QComboBox()
        from src.UI.solar_data_analysis_window import populate_observable_combo

        populate_observable_combo(self.observable_combo)
        self.window_spin = QSpinBox()
        self.window_spin.setRange(1, 720)
        self.window_spin.setValue(30)
        self.window_spin.setSuffix(" min")
        self.window_spin.setToolTip("Search this far either side of the reference frame's time.")
        self.fetch_btn = QPushButton("Fetch Viewpoint B")
        self.blink_check = QCheckBox("Blink")
        self.blink_check.setEnabled(False)
        self.blink_check.toggled.connect(self._on_blink_toggled)
        self.separation_label = QLabel("Observer separation: —")
        controls.addWidget(QLabel("Second viewpoint"), 0, 0)
        controls.addWidget(self.observable_combo, 0, 1)
        controls.addWidget(QLabel("±"), 0, 2)
        controls.addWidget(self.window_spin, 0, 3)
        controls.addWidget(self.fetch_btn, 0, 4)
        controls.addWidget(self.blink_check, 0, 5)
        controls.addWidget(self.separation_label, 0, 6)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)

        panels = QHBoxLayout()
        left = QVBoxLayout()
        self.label_a = QLabel(f"A · {self._reference_label}")
        self.canvas_a = SunPyPlotCanvas(theme=self.theme)
        left.addWidget(self.label_a)
        left.addWidget(self.canvas_a, 1)
        right = QVBoxLayout()
        self.label_b = QLabel("B · (not fetched)")
        self.canvas_b = SunPyPlotCanvas(theme=self.theme)
        right.addWidget(self.label_b)
        right.addWidget(self.canvas_b, 1)
        panels.addLayout(left, 1)
        panels.addLayout(right, 1)
        layout.addLayout(panels, 1)

        self.status_label = QLabel("Pick the second observable and fetch.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.fetch_btn.clicked.connect(self._start_search)

    # -------------------------------------------------------------- helpers
    def _reference_map(self) -> Any | None:
        if not self._reference_frames:
            return None
        return self._reference_frames[self._reference_index]

    def _reference_time(self) -> datetime | None:
        frame = self._reference_map()
        return frame_observation_time(frame) if frame is not None else None

    def _transform_for(self, frame: Any, shape: tuple[int, ...]) -> dict | None:
        window = self._window_ref
        if window is not None and hasattr(window, "_axis_transform_for_arcsec"):
            try:
                return window._axis_transform_for_arcsec(frame=frame, data_shape=shape)
            except Exception:
                pass
        return None

    def _plot_on(self, canvas: SunPyPlotCanvas, frame: Any, title: str) -> None:
        data = np.asarray(getattr(frame, "data"), dtype=float)
        finite = data[np.isfinite(data)]
        vmin = vmax = None
        if finite.size:
            vmin, vmax = (float(np.nanpercentile(finite, 1)), float(np.nanpercentile(finite, 99)))
            if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
                vmin = vmax = None
        cmap = None
        settings = getattr(frame, "plot_settings", None)
        if isinstance(settings, dict):
            cmap = getattr(settings.get("cmap"), "name", None) or (
                settings.get("cmap") if isinstance(settings.get("cmap"), str) else None
            )
        if cmap:
            canvas.set_colormap_name(str(cmap))
        canvas.plot_map_data(
            data, title=title, vmin=vmin, vmax=vmax,
            axis_transform=self._transform_for(frame, data.shape),
        )

    def _render_reference(self) -> None:
        frame = self._reference_map()
        if frame is None:
            self.status_label.setText("No reference frames were provided.")
            self.fetch_btn.setEnabled(False)
            return
        when = self._reference_time()
        stamp = f" @ {when:%Y-%m-%d %H:%M}" if when else ""
        self._plot_on(self.canvas_a, frame, f"A · {self._reference_label}{stamp}")
        if not hasattr(frame, "reproject_to"):
            self.status_label.setText(
                "Note: the loaded frames are derived arrays without full WCS "
                "(e.g. cropped/composited locally) — viewpoint B will be shown "
                "side-by-side without reprojection onto A."
            )

    @staticmethod
    def _choose_nearest_row(rows: list[SunPySearchRow], target: datetime) -> int:
        """Index of the search row whose start time is closest to ``target``."""
        best_index = 0
        best_delta = None
        for i, row in enumerate(rows):
            start = getattr(row, "start", None)
            if start is None:
                continue
            delta = abs((start - target).total_seconds())
            if best_delta is None or delta < best_delta:
                best_index, best_delta = i, delta
        return best_index

    # ---------------------------------------------------------------- search
    def _start_search(self) -> None:
        if self._active_thread is not None:
            self.status_label.setText("Another operation is still running.")
            return
        when = self._reference_time()
        if when is None:
            self.status_label.setText("The reference frame has no observation time to match against.")
            return
        data = self.observable_combo.currentData()
        if not (isinstance(data, (tuple, list)) and len(data) == 2):
            return
        margin = timedelta(minutes=int(self.window_spin.value()))
        spec = build_spec_for_observable(str(data[0]).upper(), data[1], when - margin, when + margin)

        from src.UI.sunpy_solar_viewer import SunPyWorker

        self.status_label.setText(f"Searching for {self.observable_combo.currentText()} near {when:%H:%M}…")
        self.fetch_btn.setEnabled(False)
        worker = SunPyWorker("search", query_spec=spec)
        worker.search_finished.connect(self._on_search_finished)
        worker.failed.connect(self._on_worker_failed)
        self._launch(worker)

    def _on_search_finished(self, result: Any) -> None:
        self._teardown_worker()
        rows = list(getattr(result, "rows", []) or [])
        if not rows:
            self.status_label.setText(
                "No records found in the ± window — widen it or pick another observable."
            )
            self.fetch_btn.setEnabled(True)
            return
        self._search_result = result
        when = self._reference_time()
        nearest = self._choose_nearest_row(rows, when) if when else 0
        self.status_label.setText(f"Found {len(rows)} record(s); downloading the nearest…")
        self._start_fetch(nearest)

    def _start_fetch(self, row_index: int) -> None:
        from src.UI.sunpy_solar_viewer import SunPyWorker, _default_cache_dir

        cache = self._cache_dir or _default_cache_dir()
        worker = SunPyWorker(
            "fetch_load",
            search_result=self._search_result,
            selected_rows=[int(row_index)],
            cache_dir=cache,
            jsoc_email=self._jsoc_email,
            prefer_jsoc=False,
        )
        worker.load_finished.connect(self._on_load_finished)
        worker.failed.connect(self._on_worker_failed)
        worker.cancelled.connect(lambda: self._on_worker_failed("Download was cancelled."))
        self._launch(worker)

    def _on_load_finished(self, _fetch_result: Any, load_result: Any) -> None:
        self._teardown_worker()
        frames = extract_map_frames(getattr(load_result, "maps_or_timeseries", None))
        if not frames:
            self._on_worker_failed("The download produced no loadable map frames.")
            return
        when = self._reference_time()
        if when is not None and len(frames) > 1:
            frames.sort(key=lambda f: abs(((frame_observation_time(f) or when) - when).total_seconds()))
        self._map_b = frames[0]
        map_a = self._reference_map()
        if hasattr(map_a, "reproject_to") and hasattr(self._map_b, "wcs"):
            self.status_label.setText("Reprojecting viewpoint B onto A's frame…")
            self._start_reprojection(self._map_b)
        else:
            # Derived/cropped reference: show B natively, still side-by-side.
            self._show_b(self._map_b, reprojected=False, separation=None)

    # ----------------------------------------------------------- reprojection
    def _start_reprojection(self, map_b: Any) -> None:
        map_a = self._reference_map()
        worker = ReprojectWorker(map_a, map_b)
        worker.finished.connect(self._on_reprojected)
        worker.failed.connect(self._on_reproject_failed)
        self._launch(worker)

    def _on_reprojected(self, map_b_reprojected: Any, separation: Any) -> None:
        self._teardown_worker()
        self._show_b(map_b_reprojected, reprojected=True, separation=separation)

    def _on_reproject_failed(self, tb_text: str) -> None:
        self._teardown_worker()
        last = [line for line in str(tb_text).strip().splitlines() if line.strip()]
        self.status_label.setText(
            "Reprojection failed — showing viewpoint B in its own frame. "
            f"({last[-1] if last else 'unknown error'})"
        )
        if self._map_b is not None:
            self._show_b(self._map_b, reprojected=False, separation=None)

    def _show_b(self, frame: Any, *, reprojected: bool, separation: Any) -> None:
        self._map_b_reprojected = frame if reprojected else None
        when = frame_observation_time(frame)
        stamp = f" @ {when:%Y-%m-%d %H:%M}" if when else ""
        suffix = " (reprojected onto A)" if reprojected else ""
        label = self.observable_combo.currentText()
        self.label_b.setText(f"B · {label}{suffix}")
        self._plot_on(self.canvas_b, frame, f"B · {label}{stamp}{suffix}")
        if separation is not None:
            self.separation_label.setText(f"Observer separation: {float(separation):.1f}°")
        else:
            self.separation_label.setText("Observer separation: n/a")
        self.blink_check.setEnabled(reprojected)
        self.fetch_btn.setEnabled(True)
        self.status_label.setText(
            "Viewpoints ready — use Blink to flip panel A between the two views."
            if reprojected
            else "Viewpoint B loaded (no reprojection available for this pair)."
        )

    # ------------------------------------------------------------------ blink
    def _on_blink_toggled(self, on: bool) -> None:
        if on and self._map_b_reprojected is not None:
            self._blink_showing_b = False
            self._blink_timer.start()
        else:
            self._blink_timer.stop()
            self._blink_showing_b = False
            frame = self._reference_map()
            if frame is not None:
                self._plot_on(self.canvas_a, frame, f"A · {self._reference_label}")

    def _on_blink_tick(self) -> None:
        if self._map_b_reprojected is None:
            self._blink_timer.stop()
            return
        self._blink_showing_b = not self._blink_showing_b
        if self._blink_showing_b:
            self._plot_on(self.canvas_a, self._map_b_reprojected, "A ⇄ B (reprojected)")
        else:
            frame = self._reference_map()
            if frame is not None:
                self._plot_on(self.canvas_a, frame, f"A · {self._reference_label}")

    # --------------------------------------------------------------- workers
    def _launch(self, worker: QObject) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _teardown_worker(self) -> None:
        thread = self._active_thread
        worker = self._active_worker
        self._active_thread = None
        self._active_worker = None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()

    def _on_worker_failed(self, message: str) -> None:
        self._teardown_worker()
        last = [line for line in str(message).strip().splitlines() if line.strip()]
        self.status_label.setText(f"Failed: {last[-1] if last else 'unknown error'}")
        self.fetch_btn.setEnabled(True)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._blink_timer.stop()
        worker = self._active_worker
        if worker is not None and hasattr(worker, "cancel"):
            try:
                worker.cancel()
            except Exception:
                pass
        self._teardown_worker()
        super().closeEvent(event)
