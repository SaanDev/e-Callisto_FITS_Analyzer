"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Three-viewpoint GCS CME fitting (src/UI/gcs_fitting_window.py).

A standalone window that views the Sun from three places at once and fits one
Graduated Cylindrical Shell against all of them simultaneously.

Why three, and why one set of controls
--------------------------------------
GCS is ill posed from a single vantage: a wide CME pointed at the observer and a
narrow one travelling across the sky project almost identically, so direction
trades off against height and width and a single-view fit looks convincing while
meaning very little. Two well-separated views break that. A third — the classic
STEREO-A / SOHO / STEREO-B triad — over-constrains it, which is what turns a
plausible fit into a measured one and gives the residuals somewhere to show
disagreement.

That is also why there is exactly one set of parameter sliders. The whole point
is that a single shell must satisfy every viewpoint at once; per-panel controls
would let the three drift into three different CMEs and quietly destroy the
constraint the extra spacecraft were there to provide.

What is shared and what is not
------------------------------
Shared: the model parameters, the wireframe style, the difference mode and the
observation time. Per panel: the archive source, the colormap and the contrast,
because a LASCO C3 frame and an AIA frame have nothing in common photometrically
and forcing one stretch on both would make at least one unreadable.

The time is shared as a *timestamp*, not an index. Three spacecraft rarely share
a cadence, so each panel snaps to its own nearest frame; forcing a common index
would silently show three different moments side by side.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
from PySide6.QtCore import QDateTime, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from src.Backend.gcs_model import (
    MIN_USEFUL_SEPARATION_DEG,
    GCSParameters,
    GCSViewpoint,
    apply_apex_drag,
    gcs_mesh,
    handle_positions_arcsec,
    observer_separation_deg,
    refine_gcs,
    wireframe_arcsec,
)
from src.UI.gcs_viewpoint_panel import DIFFERENCE_MODES, GCSViewpointPanel

def _utc_now() -> datetime:
    """Naive UTC now, matching the convention the analyzer's date editors use.

    ``datetime.utcnow()`` is deprecated on the Python this ships with, and the
    QDateTimeEdit widgets here are naive, so the tzinfo is stripped rather than
    carried.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


#: Panel labels, in the order they appear left to right.
PANEL_LABELS: tuple[str, ...] = ("A", "B", "C")


class GCSFittingWindow(QMainWindow):
    """Fit one GCS shell against up to three simultaneous viewpoints."""

    #: Emitted with the fitted parameters when the user sends them onward.
    parametersCommitted = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        seed_frames: list[Any] | None = None,
        seed_label: str = "Loaded view",
        cache_dir: Any = None,
        jsoc_email: str = "",
        theme: Any = None,
        initial: GCSParameters | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("GCS CME Fitting — Three Viewpoints")
        self.theme = theme
        self._params = initial
        self._clicks: dict[str, list[tuple[float, float]]] = {key: [] for key in PANEL_LABELS}
        self._last_refinement: Any = None
        self._mesh: Any = None
        self._mesh_key: tuple[float, float, float] | None = None
        self._shared_time: datetime | None = None
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._advance_frame)
        #: Recorded fits, keyed by the shared time they were committed at.
        self._fits: dict[datetime, Any] = {}

        self._build_ui(cache_dir=cache_dir, jsoc_email=jsoc_email)

        if seed_frames:
            self.panels[0].label = seed_label
            self.panels[0].set_frames(list(seed_frames))
        self._sync_all()

        try:
            from src.UI.gui_shared import fit_window_to_screen

            fit_window_to_screen(self, 1600, 1000)
        except Exception:
            self.resize(1600, 1000)

    # ------------------------------------------------------------------- UI
    def _build_ui(self, *, cache_dir: Any, jsoc_email: str) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        panels_row = QHBoxLayout()
        panels_row.setSpacing(6)
        self.panels: list[GCSViewpointPanel] = []
        for label in PANEL_LABELS:
            panel = GCSViewpointPanel(
                label, cache_dir=cache_dir, jsoc_email=jsoc_email, theme=self.theme
            )
            panel.framesChanged.connect(self._on_panel_frames_changed)
            panel.statusChanged.connect(self._set_status)
            # Each panel's own Fetch asks the window where to centre, so all three
            # agree on the moment even when fetched one at a time.
            panel.target_provider = self.target_time
            panel.canvas.set_click_callback(
                lambda x, y, button, key=label: self._on_canvas_click(key, x, y, button)
            )
            panel.canvas.set_gcs_handle_callback(
                lambda name, x, y, done, key=label: self._on_handle(key, name, x, y, done)
            )
            panels_row.addWidget(panel, 1)
            self.panels.append(panel)
        outer.addLayout(panels_row, 1)
        # Panels take the stretch; the rows below are sized to their content, so
        # collapsing either card hands the space straight back to the images.

        outer.addWidget(self._build_time_row())
        outer.addWidget(self._build_controls())

        self.status_label = QLabel("Fetch a viewpoint in each panel to begin.")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

    def _build_time_row(self) -> QWidget:
        """Transport bar: fetch target, step/play controls, scrubber, view mode."""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)

        # A target time of its own, because this window opens with nothing loaded
        # and must still be usable: without it every fetch would search around
        # "now", where the archives have nothing. Seeded frames overwrite it.
        self.target_edit = QDateTimeEdit(QDateTime(_utc_now()))
        self.target_edit.setCalendarPopup(True)
        self.target_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.target_edit.setToolTip(
            "UTC time to centre every fetch on. Set this first when the window is\n"
            "opened with no frames loaded."
        )
        self.target_edit.dateTimeChanged.connect(self._on_target_changed)
        self.fetch_all_btn = QPushButton("Fetch all")
        self.fetch_all_btn.setToolTip(
            "Search and download every panel around the target time."
        )
        self.fetch_all_btn.clicked.connect(self._fetch_all)
        row.addWidget(QLabel("Target (UTC)"))
        row.addWidget(self.target_edit)
        row.addWidget(self.fetch_all_btn)
        row.addSpacing(10)

        # Transport, matching the analyzer's playback bar so the two feel the same.
        self.rewind_btn = QPushButton()
        self.prev_btn = QPushButton()
        self.play_btn = QPushButton()
        self.pause_btn = QPushButton()
        self.next_btn = QPushButton()
        transport = (
            (self.rewind_btn, QStyle.SP_MediaSkipBackward, "First frame", self._rewind),
            (self.prev_btn, QStyle.SP_MediaSeekBackward, "Previous frame", self.previous_frame),
            (self.play_btn, QStyle.SP_MediaPlay, "Play the sequence", self.play),
            (self.pause_btn, QStyle.SP_MediaPause, "Pause", self.pause),
            (self.next_btn, QStyle.SP_MediaSeekForward, "Next frame", self.next_frame),
        )
        for button, icon, tip, slot in transport:
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(tip)
            button.setFixedWidth(38)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            row.addWidget(button)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 30)
        self.fps_spin.setValue(4)
        self.fps_spin.setSuffix(" fps")
        self.fps_spin.setToolTip("Playback speed.")
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        row.addWidget(self.fps_spin)

        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setRange(0, 0)
        self.time_slider.setToolTip(
            "Step through the event. Every panel snaps to its own frame nearest this\n"
            "time, so instruments with different cadences stay simultaneous."
        )
        self.time_slider.valueChanged.connect(self._on_time_changed)
        self.time_label = QLabel("—")
        self.time_label.setMinimumWidth(150)
        row.addSpacing(8)
        row.addWidget(self.time_slider, 1)
        row.addWidget(self.time_label)

        row.addSpacing(10)
        row.addWidget(QLabel("View"))
        self._difference_group = QButtonGroup(self)
        for index, (key, label) in enumerate(DIFFERENCE_MODES):
            button = QRadioButton(label)
            button.setChecked(key == "raw")
            button.toggled.connect(
                lambda on, mode=key: self._on_difference_mode(mode) if on else None
            )
            self._difference_group.addButton(button, index)
            row.addWidget(button)
        self._sync_transport()
        return bar

    # ------------------------------------------------------------- playback
    def _frame_count(self) -> int:
        return self.time_slider.maximum() + 1 if self.time_slider.maximum() > 0 else 0

    def _sync_transport(self) -> None:
        """Enable the transport only when there is a sequence to move through."""
        many = self._frame_count() > 1
        playing = self._play_timer.isActive()
        for button in (self.rewind_btn, self.prev_btn, self.next_btn):
            button.setEnabled(many)
        self.play_btn.setEnabled(many and not playing)
        self.pause_btn.setEnabled(playing)
        self.time_slider.setEnabled(many)

    def _on_fps_changed(self, value: int) -> None:
        self._play_timer.setInterval(int(1000 / max(1, int(value))))

    def play(self) -> None:
        if self._frame_count() <= 1:
            return
        self._play_timer.setInterval(int(1000 / max(1, int(self.fps_spin.value()))))
        self._play_timer.start()
        self._sync_transport()

    def pause(self) -> None:
        self._play_timer.stop()
        self._sync_transport()

    def _advance_frame(self) -> None:
        """Playback tick: wrap at the end rather than stopping dead."""
        if self._frame_count() <= 1:
            self.pause()
            return
        nxt = self.time_slider.value() + 1
        self.time_slider.setValue(0 if nxt > self.time_slider.maximum() else nxt)

    def next_frame(self) -> None:
        self.time_slider.setValue(
            min(self.time_slider.value() + 1, self.time_slider.maximum())
        )

    def previous_frame(self) -> None:
        self.time_slider.setValue(max(self.time_slider.value() - 1, 0))

    def _rewind(self) -> None:
        self.time_slider.setValue(0)

    def _build_controls(self) -> QWidget:
        """The fit controls, in a card that collapses to give the images room."""
        from src.UI.solar_measure_tools import GCSParameterPanel, GCSParameterSlider
        from src.UI.widgets.collapsible_sections import CollapsibleSection

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # One parameter set for every panel — see the module docstring.
        self.gcs_panel = GCSParameterPanel()
        self.gcs_panel.parametersChanged.connect(self._on_parameters)
        self.gcs_panel.refineRequested.connect(self._on_refine)
        self.gcs_panel.commitRequested.connect(self._on_commit)
        self.gcs_panel.commit_gcs_btn.setText("Commit GCS")
        self.gcs_panel.commit_gcs_btn.setToolTip(
            "Record this fit at the current time. Commit across several times to build\n"
            "a de-projected 3-D height-time profile."
        )
        layout.addWidget(self.gcs_panel, 3)

        # The fits table, the 3-D height-time plot and CSV export are TrackingPanel's
        # existing "gcs" source — embedded rather than reimplemented, so the fit,
        # its order selector and its error bars behave exactly as in the analyzer.
        from src.UI.solar_measure_tools import TrackingPanel

        self.tracking_panel = TrackingPanel(self)
        self.tracking_panel.set_source("gcs")
        self.tracking_panel.fit_btn.clicked.connect(self._on_fit_kinematics)
        self.tracking_panel.clear_btn.clicked.connect(self._on_clear_fits)
        layout.addWidget(self.tracking_panel, 3)

        style = QVBoxLayout()
        style.setSpacing(2)
        style.addWidget(QLabel("Wireframe"))
        self.width_slider = GCSParameterSlider(
            "Width", 2.6, minimum=0.5, maximum=8.0, unit=" px", decimals=1
        )
        self.opacity_slider = GCSParameterSlider(
            "Opacity", 100.0, minimum=10.0, maximum=100.0, unit="%", decimals=0
        )
        # 1 = coarsest, 8 = every ring. Named for what the user sees, and
        # deliberately defaulting well short of the maximum: at a heavy pen width
        # a full-density mesh fills in and reads as a solid blob rather than a grid.
        self.density_slider = GCSParameterSlider(
            "Density", 5.0, minimum=1.0, maximum=8.0, unit="", decimals=0
        )
        self.density_slider.setToolTip(
            "How fine the drawn mesh is. Higher is denser; back it off when a thick\n"
            "pen starts filling the shell in instead of outlining it."
        )
        for slider in (self.width_slider, self.opacity_slider, self.density_slider):
            style.addWidget(slider)
        self.colour_btn = QPushButton("Wireframe colour…")
        self.colour_btn.clicked.connect(self._pick_colour)
        style.addWidget(self.colour_btn)
        self.send_btn = QPushButton("Send to analyzer")
        self.send_btn.setToolTip(
            "Push the current parameters back to the Solar Image Analysis window."
        )
        self.send_btn.clicked.connect(self._on_send)
        style.addWidget(self.send_btn)
        self.clear_points_btn = QPushButton("Clear clicked points")
        self.clear_points_btn.clicked.connect(self._clear_points)
        style.addWidget(self.clear_points_btn)
        style.addStretch(1)
        layout.addLayout(style, 1)

        self.width_slider.valueChanged.connect(lambda _v: self._apply_style())
        self.opacity_slider.valueChanged.connect(lambda _v: self._apply_style())
        self.density_slider.valueChanged.connect(lambda _v: self._sync_all())
        self._wireframe_colour = (255, 140, 40)

        # Capped, because this card and the images compete for the same column of
        # pixels and the images are the point. The cap is the tallest thing inside
        # it — the tracking panel reports a 363 px minimum — because going under
        # that does not shrink the card, it just overlaps its own contents. The
        # card collapses entirely when even this is too much.
        container.setMaximumHeight(
            max(370, self.tracking_panel.minimumSizeHint().height() + 8)
        )
        self.controls_section = CollapsibleSection("Fit controls")
        self.controls_section.setContentWidget(container)
        # Starts open: this is where the fitting happens, and a user who wants the
        # images larger still can collapse it — unlike the per-panel settings,
        # which are touched once per fetch and start closed.
        self.controls_section.setExpanded(True, notify=False)
        self.controls_section.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        return self.controls_section

    # ------------------------------------------------------------- geometry
    def parameters(self) -> GCSParameters:
        """The one shared fit, seeded from whichever panel has frames first."""
        if self._params is None:
            observer = next(
                (panel.observer for panel in self.panels if panel.observer is not None), None
            )
            lon = (observer.lon_deg + 90.0) if observer is not None else 90.0
            self._params = GCSParameters(
                lon_deg=((lon + 180.0) % 360.0) - 180.0,
                lat_deg=0.0,
                tilt_deg=0.0,
                height_rsun=8.0,
                alpha_deg=30.0,
                kappa=0.30,
            )
        return self._params

    def _panel(self, key: str) -> GCSViewpointPanel:
        return self.panels[PANEL_LABELS.index(key)]

    def _active_panels(self) -> list[GCSViewpointPanel]:
        return [panel for panel in self.panels if panel.observer is not None]

    def _cached_mesh(self, params: GCSParameters) -> Any:
        key = (params.height_rsun, params.alpha_deg, params.kappa)
        if self._mesh is None or self._mesh_key != key:
            self._mesh = gcs_mesh(params)
            self._mesh_key = key
        return self._mesh

    # ------------------------------------------------------------- rendering
    def _sync_all(self) -> None:
        """Redraw the one shell on every panel that has a usable observer."""
        params = self.parameters()
        self.gcs_panel.show_parameters(params)
        mesh = self._cached_mesh(params)
        # Slider is "density", the backend wants a ring stride, so invert.
        density = int(round(self.density_slider.value())) if hasattr(self, "density_slider") else 5
        stride = max(1, 9 - density)

        for panel in self.panels:
            canvas = panel.canvas
            if panel.observer is None:
                canvas.clear_gcs_overlay()
                continue
            x, y, _ = wireframe_arcsec(
                params,
                panel.observer,
                mesh=mesh,
                ring_stride=stride,
                n_longitudinal=max(4, 12 - stride),
            )
            canvas.set_gcs_overlay(x, y)
            canvas.set_gcs_handles(handle_positions_arcsec(params, panel.observer))
            points = self._clicks[panel.label] if panel.label in self._clicks else []
            canvas.set_measurement_overlay(
                [point[0] for point in points], [point[1] for point in points], connect=False
            )
        self._apply_style()
        self._refresh_status()

    def _apply_style(self) -> None:
        for panel in self.panels:
            panel.canvas.set_gcs_style(
                width=self.width_slider.value(),
                color=self._wireframe_colour,
                opacity=self.opacity_slider.value() / 100.0,
            )

    def _pick_colour(self) -> None:
        from PySide6.QtGui import QColor

        current = QColor(*self._wireframe_colour)
        chosen = QColorDialog.getColor(current, self, "Wireframe colour")
        if chosen.isValid():
            self._wireframe_colour = (chosen.red(), chosen.green(), chosen.blue())
            self._apply_style()

    # ------------------------------------------------------------------ time
    def _on_panel_frames_changed(self, _panel: Any) -> None:
        self._rebuild_time_axis()
        self._sync_all()

    def _rebuild_time_axis(self) -> None:
        """Span every panel's frames, so the slider covers the whole event."""
        stamps: list[datetime] = []
        for panel in self.panels:
            stamps.extend(panel.times())
        if not stamps:
            self.time_slider.setRange(0, 0)
            self.time_label.setText("—")
            self.pause()
            self._sync_transport()
            return
        self._time_axis = sorted(set(stamps))
        was = self.time_slider.blockSignals(True)
        self.time_slider.setRange(0, len(self._time_axis) - 1)
        if self._shared_time is not None:
            nearest = min(
                range(len(self._time_axis)),
                key=lambda i: abs((self._time_axis[i] - self._shared_time).total_seconds()),
            )
            self.time_slider.setValue(nearest)
        self.time_slider.blockSignals(was)
        self._sync_transport()
        self._on_time_changed(self.time_slider.value())

    def _on_time_changed(self, index: int) -> None:
        axis = getattr(self, "_time_axis", None)
        if not axis:
            return
        when = axis[max(0, min(int(index), len(axis) - 1))]
        self._shared_time = when
        self.time_label.setText(f"{when:%Y-%m-%d %H:%M:%S}")
        was = self.target_edit.blockSignals(True)
        self.target_edit.setDateTime(QDateTime(when))
        self.target_edit.blockSignals(was)
        for panel in self.panels:
            panel.set_shared_time(when)
        self._sync_all()

    def _difference_mode(self) -> str:
        """The view mode the toolbar currently has selected."""
        for index, (key, _label) in enumerate(DIFFERENCE_MODES):
            button = self._difference_group.button(index)
            if button is not None and button.isChecked():
                return key
        return "raw"

    def _on_difference_mode(self, mode: str) -> None:
        # Keep the radios in step when this is driven from code, so the toolbar
        # can never disagree with what the panels are actually showing.
        for index, (key, _label) in enumerate(DIFFERENCE_MODES):
            button = self._difference_group.button(index)
            if button is not None and button.isChecked() != (key == mode):
                was = button.blockSignals(True)
                button.setChecked(key == mode)
                button.blockSignals(was)
        for panel in self.panels:
            panel.set_difference_mode(mode)
        self._sync_all()

    def target_time(self) -> datetime:
        """Where fetches are centred: the stepped time once frames exist, else
        whatever the user typed in the target box."""
        if self._shared_time is not None:
            return self._shared_time
        return self.target_edit.dateTime().toPython().replace(tzinfo=None)

    def _on_target_changed(self, _value: Any) -> None:
        # Only meaningful before anything is loaded; afterwards the slider owns
        # the time and the panels are already synced to it.
        if not any(panel.frames for panel in self.panels):
            for panel in self.panels:
                panel.set_shared_time(self.target_time())

    def _fetch_all(self) -> None:
        when = self.target_time()
        for panel in self.panels:
            panel.start_fetch(when)

    # ------------------------------------------------------------ interaction
    def _on_parameters(self, params: GCSParameters) -> None:
        self._params = params
        self._sync_all()

    def _on_handle(self, key: str, name: str, x: float, y: float, _finished: bool) -> None:
        if name != "apex":
            return
        panel = self._panel(key)
        if panel.observer is None:
            return
        # Dragging on any panel moves the one shared shell, which is the whole
        # point: you steer from whichever view shows the front most clearly.
        self._params = apply_apex_drag(self.parameters(), panel.observer, (x, y))
        self._sync_all()

    def _on_canvas_click(self, key: str, x: float, y: float, button: str) -> None:
        canvas = self._panel(key).canvas
        if canvas.gcs_drag_active():
            return
        if button == "right":
            if self._clicks[key]:
                self._clicks[key].pop()
        elif button == "left":
            self._clicks[key].append((float(x), float(y)))
        else:
            return
        self._sync_all()

    def _clear_points(self) -> None:
        for key in self._clicks:
            self._clicks[key] = []
        self._sync_all()

    # --------------------------------------------------------------- fitting
    def _viewpoints(self) -> list[GCSViewpoint]:
        out: list[GCSViewpoint] = []
        for panel in self.panels:
            if panel.observer is None:
                continue
            out.append(
                GCSViewpoint(
                    panel.observer,
                    np.asarray(self._clicks[panel.label], dtype=float).reshape(-1, 2),
                    panel.observer.label or panel.label,
                )
            )
        return out

    def _on_refine(self) -> None:
        try:
            result = refine_gcs(self._viewpoints(), self.parameters())
        except ValueError as exc:
            self._set_status(f"GCS refine: {exc}")
            return
        self._last_refinement = result
        self._params = result.parameters
        self._sync_all()
        self._set_status(result.message)

    def _on_commit(self) -> None:
        """Record the current fit at the current shared time."""
        from src.UI.solar_measure_tools import GCSFitEntry

        when = self._shared_time
        if when is None:
            self._set_status("Nothing to commit — fetch a viewpoint first.")
            return
        result = self._last_refinement
        sigma = result.sigma if result is not None else {}
        active = self._active_panels()
        separations = [
            observer_separation_deg(a.observer, b.observer)
            for index, a in enumerate(active)
            for b in active[index + 1 :]
        ]
        params = self.parameters()
        self._fits[when] = GCSFitEntry(
            when=when,
            apex_height_rsun=params.height_rsun,
            lon_deg=params.lon_deg,
            lat_deg=params.lat_deg,
            tilt_deg=params.tilt_deg,
            alpha_deg=params.alpha_deg,
            kappa=params.kappa,
            rms_arcsec=float(getattr(result, "rms_arcsec", float("nan"))),
            n_points=sum(len(points) for points in self._clicks.values()),
            n_viewpoints=len(active),
            separation_deg=max(separations) if separations else 0.0,
            lon_err_deg=float(sigma.get("lon_deg", float("nan"))),
            lat_err_deg=float(sigma.get("lat_deg", float("nan"))),
            height_err_rsun=float(sigma.get("height_rsun", float("nan"))),
            refined=result is not None,
        )
        self._refresh_fits()
        self._set_status(
            f"Committed {when:%H:%M:%S} → apex {params.height_rsun:.2f} R☉ "
            f"({len(self._fits)} frame(s) recorded)."
        )

    def _refresh_fits(self) -> None:
        self.tracking_panel.refresh_gcs(
            {index: entry for index, entry in enumerate(self._fits.values())}
        )

    def _on_clear_fits(self) -> None:
        self._fits.clear()
        self._refresh_fits()
        self._set_status("Recorded GCS fits cleared.")

    def _on_fit_kinematics(self) -> None:
        """Fit apex height(t) over the recorded fits — a true 3-D radial speed."""
        from src.Backend.coronagraph import RSUN_KM, fit_height_time

        entries = sorted(self._fits.values(), key=lambda item: item[0])
        if len(entries) < 2:
            self._set_status("A height-time fit needs commits at two or more times.")
            return
        times = [entry[0] for entry in entries]
        if max(times) <= min(times):
            self._set_status("Every commit shares one time — there is no speed to fit.")
            return
        seconds = [(when - times[0]).total_seconds() for when in times]
        try:
            fit = fit_height_time(
                seconds,
                [float(entry[1]) * RSUN_KM for entry in entries],
                order=self.tracking_panel.fit_order(),
            )
        except ValueError as exc:
            self._set_status(f"Fit: {exc}")
            return
        self.tracking_panel.show_fit(fit)
        # "3-D" is the whole point: unlike a plane-of-sky track this height is
        # de-projected, so the speed is the real radial one.
        self._set_status(
            f"3-D (de-projected) kinematics over {len(entries)} commit(s): "
            + self.tracking_panel.fit_summary(fit, noun="commits")
        )

    def _on_send(self) -> None:
        """Push the parameters back to the Solar Image Analysis window."""
        self.parametersCommitted.emit(self.parameters())
        measure = getattr(self.parent(), "_measure", None)
        if measure is not None and hasattr(measure, "set_gcs_parameters"):
            measure.set_gcs_parameters(self.parameters())
            self._set_status("Sent to the Solar Image Analysis window.")
            return
        self._set_status("Parameters ready — no analyzer window is listening.")

    # ---------------------------------------------------------------- status
    def _set_status(self, text: str) -> None:
        self.status_label.setText(str(text))

    def _refresh_status(self) -> None:
        active = self._active_panels()
        clicks = sum(len(points) for points in self._clicks.values())
        if len(active) < 2:
            self._set_status(
                f"{len(active)} viewpoint(s) · {clicks} point(s) clicked — "
                "one view cannot constrain the propagation direction, so it is held fixed. "
                "Fetch at least one more."
            )
            return
        separations = [
            observer_separation_deg(a.observer, b.observer)
            for index, a in enumerate(active)
            for b in active[index + 1 :]
        ]
        widest = max(separations)
        summary = ", ".join(f"{value:.0f}°" for value in separations)
        note = ""
        if widest < MIN_USEFUL_SEPARATION_DEG:
            note = " — too close together to constrain longitude; it is held fixed"
        message = (
            f"{len(active)} viewpoints · separations {summary} · {clicks} point(s) clicked{note}"
        )
        # A panel that fetched a single frame cannot difference, and would
        # otherwise sit there showing raw pixels while the toolbar says otherwise.
        stuck = [
            panel.label
            for panel in self.panels
            if panel.frames and not panel.can_difference()
        ]
        if stuck and self._difference_mode() != "raw":
            message += (
                f" — panel {', '.join(stuck)} has one frame, so it cannot difference; "
                "raise its frame count and fetch again"
            )
        self._set_status(message)

    # ----------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._play_timer.stop()
        for panel in self.panels:
            panel.shutdown()
        super().closeEvent(event)
