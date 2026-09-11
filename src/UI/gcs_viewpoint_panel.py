"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

One viewpoint of the GCS fitting window (src/UI/gcs_viewpoint_panel.py).

Each panel is a self-contained observer: it searches and downloads its own
sequence from the archive, keeps its own colormap, contrast and difference
rendering, and reports the projection geometry the shared GCS fit needs. The
window above it owns nothing but the shared time, the shared model and the
shared style.

Panels are deliberately independent in what they *show* and identical in what
they are *told*. Three spacecraft rarely share a cadence — LASCO C3 runs about
twice an hour while COR2 can be every five minutes — so each panel snaps to its
own frame nearest the shared time rather than being forced onto a common index,
which would drift the three views apart in time without saying so.

No fitting maths lives here; that is ``src.Backend.gcs_model``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QSizePolicy,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.Backend.gcs_model import ObserverGeometry
from src.Backend.solar_data_analysis import (
    difference_sequence,
    extract_map_frames,
    frame_observation_time,
)
from src.UI.sunpy_plot_window import SunPyPlotCanvas

#: Colormaps offered per panel. Mirrors the analysis window's own list so a
#: coronagraph and a disk imager each have something sensible to hand.
COLORMAP_CHOICES: tuple[str, ...] = (
    "soholasco2",
    "soholasco3",
    "stereocor1",
    "stereocor2",
    "sdoaia171",
    "sdoaia193",
    "sdoaia211",
    "sdoaia304",
    "sohoeit195",
    "gray",
    "inferno",
    "magma",
    "viridis",
)

#: Difference modes, in the order they appear in the shared toolbar.
DIFFERENCE_MODES: tuple[tuple[str, str], ...] = (
    ("raw", "Raw"),
    ("running", "Running difference"),
    ("base", "Base difference"),
)


class GCSViewpointPanel(QWidget):
    """One observer: its own archive fetch, rendering controls and canvas."""

    #: Emitted when this panel's frames change, so the window can re-derive the
    #: shared time span and the observer separations.
    framesChanged = Signal(object)
    #: Emitted with a status line for the window's shared status bar.
    statusChanged = Signal(str)

    def __init__(
        self,
        label: str,
        *,
        cache_dir: Any = None,
        jsoc_email: str = "",
        theme: Any = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.label = str(label)
        self._cache_dir = cache_dir
        self._jsoc_email = str(jsoc_email or "")
        self.theme = theme

        self.frames: list[Any] = []
        self.observer: ObserverGeometry | None = None
        self._display: list[np.ndarray] = []
        self._difference_mode = "raw"
        self._index = 0
        self._search_result: Any = None
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._target_time: datetime | None = None
        #: Set by the window: where this panel's own Fetch button should centre.
        self.target_provider: Callable[[], datetime] | None = None

        self._build_ui()

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.title_label = QLabel(f"{self.label} · (not fetched)")
        self.title_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.title_label)

        self.canvas = SunPyPlotCanvas(theme=self.theme)
        # The image is the point of this panel, so it takes every pixel the
        # controls are not using — and the controls default to collapsed.
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Square mode stays ON. Turning it off does make the image fill the panel,
        # but the canvas pins both axes through setLimits, so the ViewBox has no
        # freedom to letterbox and simply stretches instead — a visibly elliptical
        # Sun. A correctly-shaped smaller image beats a bigger wrong one; the size
        # is won back by trimming chrome below and by collapsing the controls.
        self.canvas.setMinimumHeight(340)
        # Per-panel axis titles cost ~60 px of width each, and with three panels
        # side by side the image is width-limited, so that is width taken straight
        # out of the picture. The tick numbers stay, which is what is actually read.
        self.canvas.set_map_axis_titles_visible(False)
        layout.addWidget(self.canvas, 1)

        layout.addWidget(self._build_settings_section())

        self.fetch_btn.clicked.connect(self.start_fetch)
        self.colormap_combo.currentTextChanged.connect(lambda _t: self.render())
        self.low_slider.valueChanged.connect(lambda _v: self.render())
        self.high_slider.valueChanged.connect(lambda _v: self.render())

    def _build_settings_section(self) -> QWidget:
        """Source and display controls, collapsed by default.

        Collapsed because this window's job is to show three images large enough
        to fit a faint CME front against; the controls are touched once per fetch
        and then not again, so they should not be permanently eating the picture.
        """
        from src.UI.solar_data_analysis_window import populate_observable_combo
        from src.UI.solar_measure_tools import GCSParameterSlider
        from src.UI.widgets.collapsible_sections import CollapsibleSection

        body = QWidget()
        inner = QVBoxLayout(body)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(3)

        source = QGridLayout()
        source.setHorizontalSpacing(4)
        source.setVerticalSpacing(2)
        self.observable_combo = QComboBox()
        populate_observable_combo(self.observable_combo)
        self.window_spin = QSpinBox()
        self.window_spin.setRange(2, 720)
        self.window_spin.setValue(60)
        self.window_spin.setSuffix(" min")
        self.window_spin.setToolTip("Search this far either side of the target time.")
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 60)
        self.count_spin.setValue(12)
        self.count_spin.setPrefix("× ")
        self.count_spin.setToolTip(
            "How many frames to download. Two or more is what makes a running\n"
            "difference possible, which is usually what reveals the front."
        )
        self.fetch_btn = QPushButton("Fetch")
        source.addWidget(self.observable_combo, 0, 0, 1, 3)
        source.addWidget(self.window_spin, 1, 0)
        source.addWidget(self.count_spin, 1, 1)
        source.addWidget(self.fetch_btn, 1, 2)
        source.setColumnStretch(0, 1)
        inner.addLayout(source)

        style_row = QHBoxLayout()
        style_row.setSpacing(4)
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(COLORMAP_CHOICES)
        self.colormap_combo.setToolTip("Colormap for this viewpoint.")
        style_row.addWidget(QLabel("Map"))
        style_row.addWidget(self.colormap_combo, 1)
        inner.addLayout(style_row)

        # Contrast as a percentile clip, the same convention the analysis window
        # uses: robust against the few very bright pixels a coronagraph always
        # has, where a min/max stretch would wash the corona out entirely.
        self.low_slider = GCSParameterSlider(
            "Low", 1.0, minimum=0.0, maximum=20.0, unit="%", decimals=1
        )
        self.high_slider = GCSParameterSlider(
            "High", 99.5, minimum=80.0, maximum=100.0, unit="%", decimals=1
        )
        for slider in (self.low_slider, self.high_slider):
            slider.setToolTip(
                "Percentile clip for this panel's display stretch — the low and high\n"
                "ends of the colour scale."
            )
            inner.addWidget(slider)

        self.settings_section = CollapsibleSection(f"{self.label} · settings")
        self.settings_section.setContentWidget(body)
        self.settings_section.setExpanded(False, notify=False)
        return self.settings_section

    # -------------------------------------------------------------- frames
    def set_frames(self, frames: list[Any]) -> None:
        """Adopt a sequence, sorted in time, and rebuild the display arrays."""
        ordered = [frame for frame in frames if frame is not None]
        ordered.sort(key=lambda f: frame_observation_time(f) or datetime.min)
        self.frames = ordered
        self.observer = (
            ObserverGeometry.from_frame(ordered[0], label=self.label) if ordered else None
        )
        self._index = 0
        self._rebuild_display()
        self._apply_default_colormap()
        self.framesChanged.emit(self)
        self.render()

    def _apply_default_colormap(self) -> None:
        """Pick the colormap from the frame's own instrument, not from the combo.

        Same reasoning as :meth:`_frame_name`: the combo describes the *next*
        fetch. Reading it here would dress a LASCO frame in an AIA palette, which
        is not merely ugly — the AIA maps are tuned for a bright disk and make a
        faint coronagraph front nearly invisible.
        """
        frame = self.frames[0] if self.frames else None
        if frame is None:
            return
        detector = str(getattr(frame, "detector", "") or "").strip().upper()
        instrument = str(getattr(frame, "instrument", "") or "").strip().upper()
        wavelength = getattr(frame, "wavelength", None)
        value: Any = detector or None
        if instrument in {"AIA", "EIT", "SUVI"} and wavelength is not None:
            try:
                value = float(getattr(wavelength, "value", wavelength))
            except Exception:
                value = None
        from src.UI.solar_data_analysis_window import default_colormap_for_observable

        try:
            name = default_colormap_for_observable(instrument, value)
        except Exception:
            return
        if not name:
            return
        if self.colormap_combo.findText(name) < 0:
            self.colormap_combo.addItem(name)
        was = self.colormap_combo.blockSignals(True)
        self.colormap_combo.setCurrentText(name)
        self.colormap_combo.blockSignals(was)

    def set_difference_mode(self, mode: str) -> None:
        self._difference_mode = str(mode or "raw")
        self._rebuild_display()
        self.render()

    def can_difference(self) -> bool:
        """True when this panel has enough frames to form a difference image.

        ``difference_sequence`` silently hands back the raw array for a
        single-frame sequence, so without this a panel that fetched one frame
        would claim "Running difference" in its title while showing raw data —
        and, since panels fetch independently, that is exactly how a three-panel
        window ends up differencing only some of its views.
        """
        return len(self.frames) >= 2

    def difference_mode(self) -> str:
        """The mode actually in effect, which is ``raw`` when it cannot difference."""
        if self._difference_mode != "raw" and not self.can_difference():
            return "raw"
        return self._difference_mode

    def _rebuild_display(self) -> None:
        """Re-derive the arrays actually drawn, applying the difference mode."""
        if not self.frames:
            self._display = []
            return
        try:
            self._display = difference_sequence(
                self.frames, mode=self.difference_mode(), normalize=True
            )
        except Exception:
            self._display = [np.asarray(getattr(f, "data"), dtype=float) for f in self.frames]

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
        if best != self._index:
            self._index = best
        self.render()

    def current_frame(self) -> Any | None:
        if not self.frames:
            return None
        return self.frames[max(0, min(self._index, len(self.frames) - 1))]

    def current_time(self) -> datetime | None:
        frame = self.current_frame()
        return frame_observation_time(frame) if frame is not None else None

    # ------------------------------------------------------------ rendering
    def render(self) -> None:
        """Redraw the image for the current index, contrast and colormap."""
        if not self._display:
            self.canvas.clear_gcs_overlay()
            self.title_label.setText(f"{self.label} · (not fetched)")
            return
        index = max(0, min(self._index, len(self._display) - 1))
        data = np.asarray(self._display[index], dtype=float)
        finite = data[np.isfinite(data)]
        vmin = vmax = None
        if finite.size:
            low = float(self.low_slider.value())
            high = float(self.high_slider.value())
            if high <= low:
                high = min(100.0, low + 0.5)
            vmin = float(np.percentile(finite, low))
            vmax = float(np.percentile(finite, high))
            if not (np.isfinite(vmin) and np.isfinite(vmax) and vmax > vmin):
                vmin = vmax = None

        self.canvas.set_colormap_name(self.colormap_combo.currentText())
        frame = self.frames[max(0, min(index, len(self.frames) - 1))]
        self.canvas.plot_map_data(
            data,
            title="",
            vmin=vmin,
            vmax=vmax,
            axis_transform=self._axis_transform(frame, data.shape),
        )
        when = frame_observation_time(frame)
        stamp = f"{when:%Y-%m-%d %H:%M:%S}" if when else "—"
        mode = dict(DIFFERENCE_MODES).get(self.difference_mode(), "Raw")
        if self._difference_mode != "raw" and not self.can_difference():
            # Say so rather than showing raw pixels under a differenced label.
            mode = f"{dict(DIFFERENCE_MODES)[self._difference_mode]} unavailable — 1 frame"
        self.title_label.setText(
            f"{self.label} · {self._frame_name(frame)} · {stamp}"
            f"  ({index + 1}/{len(self._display)}, {mode})"
        )

    @staticmethod
    def _frame_name(frame: Any) -> str:
        """Name the frame from its own header, not from the observable combo.

        The combo says what will be fetched *next*; frames can also arrive seeded
        from the analyzer, and after a fetch the combo may have been changed. Using
        it here would confidently mislabel a LASCO frame as whatever is selected.
        """
        parts = [
            str(getattr(frame, "observatory", "") or "").strip(),
            str(getattr(frame, "detector", "") or "").strip(),
        ]
        if not parts[1]:
            parts[1] = str(getattr(frame, "instrument", "") or "").strip()
        wavelength = getattr(frame, "wavelength", None)
        if wavelength is not None:
            try:
                parts.append(f"{float(getattr(wavelength, 'value', wavelength)):g} Å")
            except Exception:
                pass
        name = " ".join(part for part in parts if part)
        return name or "unknown"

    @staticmethod
    def _axis_transform(frame: Any, _shape: tuple[int, ...]) -> dict | None:
        """Pixel-to-arcsec mapping for the canvas, from the map's own WCS keywords.

        Built here rather than borrowed from the analysis window: that window's
        version depends on several of its own helper methods and on its crop
        origin, none of which apply to a panel. Panels only ever hold real
        ``sunpy.map.Map`` objects straight from the archive, so the three
        attributes below are always present.
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
    def start_fetch(self, target: datetime | None = None) -> None:
        """Search the archive and download a sequence around ``target``."""
        if self._thread is not None:
            self.statusChanged.emit(f"{self.label}: a download is already running.")
            return
        when = target or self._target_time
        if when is None and self.target_provider is not None:
            when = self.target_provider()
        if when is None:
            when = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        data = self.observable_combo.currentData()
        if not (isinstance(data, (tuple, list)) and len(data) == 2):
            return
        from src.Backend.sunpy_archive import build_spec_for_observable
        from src.UI.sunpy_solar_viewer import SunPyWorker

        margin = timedelta(minutes=int(self.window_spin.value()))
        spec = build_spec_for_observable(
            str(data[0]).upper(),
            data[1],
            when - margin,
            when + margin,
            max_records=max(1, int(self.count_spin.value())) * 3,
        )
        self.fetch_btn.setEnabled(False)
        self.statusChanged.emit(
            f"{self.label}: searching {self.observable_combo.currentText()} near {when:%H:%M}…"
        )
        worker = SunPyWorker("search", query_spec=spec)
        worker.search_finished.connect(self._on_search_finished)
        worker.failed.connect(self._on_failed)
        self._launch(worker)

    def _on_search_finished(self, result: Any) -> None:
        self._teardown()
        rows = list(getattr(result, "rows", []) or [])
        if not rows:
            self.fetch_btn.setEnabled(True)
            self.statusChanged.emit(
                f"{self.label}: nothing found in the ± window — widen it or pick another observable."
            )
            return
        self._search_result = result
        wanted = max(1, int(self.count_spin.value()))
        picked = self._rows_around_target(rows, wanted)
        self.statusChanged.emit(
            f"{self.label}: found {len(rows)}, downloading {len(picked)}…"
        )
        from src.UI.sunpy_solar_viewer import SunPyWorker, _default_cache_dir

        worker = SunPyWorker(
            "fetch_load",
            search_result=result,
            selected_rows=picked,
            cache_dir=self._cache_dir or _default_cache_dir(),
            jsoc_email=self._jsoc_email,
            prefer_jsoc=False,
        )
        worker.load_finished.connect(self._on_load_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(lambda: self._on_failed("Download was cancelled."))
        self._launch(worker)

    def _rows_around_target(self, rows: list[Any], wanted: int) -> list[int]:
        """The ``wanted`` rows closest in time to the target, back in time order.

        Centred on the target rather than taken from the start of the window, so
        a running difference brackets the moment the user actually cares about.
        """
        target = self._target_time
        indexed = list(enumerate(rows))
        if target is not None:
            def gap(item: tuple[int, Any]) -> float:
                stamp = getattr(item[1], "start", None) or getattr(item[1], "time", None)
                if not isinstance(stamp, datetime):
                    return float(item[0])
                return abs((stamp - target).total_seconds())

            indexed.sort(key=gap)
        return sorted(index for index, _ in indexed[:wanted])

    def _on_load_finished(self, _fetch_result: Any, load_result: Any) -> None:
        self._teardown()
        self.fetch_btn.setEnabled(True)
        frames = extract_map_frames(getattr(load_result, "maps_or_timeseries", None))
        if not frames:
            self.statusChanged.emit(f"{self.label}: the download produced no loadable frames.")
            return
        self.set_frames(frames)
        self.statusChanged.emit(f"{self.label}: loaded {len(frames)} frame(s).")

    def _on_failed(self, message: str) -> None:
        self._teardown()
        self.fetch_btn.setEnabled(True)
        last = [line for line in str(message).strip().splitlines() if line.strip()]
        self.statusChanged.emit(f"{self.label}: {last[-1] if last else 'download failed'}")

    def _launch(self, worker: QObject) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _teardown(self) -> None:
        thread, worker = self._thread, self._worker
        self._thread = self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()

    def shutdown(self) -> None:
        """Stop any in-flight download; called when the window closes."""
        self._teardown()
