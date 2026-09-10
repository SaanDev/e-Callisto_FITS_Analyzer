"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Multi-viewpoint GCS CME fitting (src/UI/gcs_fitting_dialog.py).

GCS is only well posed with two or more simultaneous views. From one vantage the
propagation direction trades off against the shell's height and width — a wide
CME pointed at you and a narrow one across the sky can project identically — so a
single-view fit looks convincing and is not determined. Two views separated by a
useful angle break that degeneracy, which is why this dialog exists alongside the
single-view overlay in the analysis window.

It subclasses :class:`~src.UI.multiview_dialog.MultiViewpointDialog` rather than
re-implementing it: the archive search, the fetch/reproject workers, the observer
separation read-out and the side-by-side canvases are all already there and
already tested. What is added here is one shared :class:`GCSParameters` driving
both panels at once, and a refine that costs both viewpoints together.

One parameter set, two projections: a slider move recomputes the mesh once and
projects it per panel, which is ~0.4 ms for two panels — so the sliders update
directly, with no debounce. See ``src.Backend.gcs_model`` for the measurements
and for why the projection is hand-rolled.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.Backend.gcs_model import (
    GCSParameters,
    GCSViewpoint,
    ObserverGeometry,
    apply_apex_drag,
    handle_positions_arcsec,
    refine_gcs,
    wireframe_arcsec,
)
from src.UI.multiview_dialog import MultiViewpointDialog
from src.UI.solar_measure_tools import GCSParameterPanel

#: Below this the two views see nearly the same projection, so longitude stays
#: degenerate however many points are clicked. Mirrors the backend's gate.
MIN_USEFUL_SEPARATION_DEG = 20.0


class GCSFittingDialog(MultiViewpointDialog):
    """Fit one GCS shell against two viewpoints at once."""

    def __init__(self, *args: Any, initial: GCSParameters | None = None, **kwargs: Any):
        self._gcs_params = initial
        self._gcs_clicks: dict[str, list[tuple[float, float]]] = {"A": [], "B": []}
        self._gcs_last_refinement: Any = None
        super().__init__(*args, **kwargs)
        self.setWindowTitle("GCS CME Fitting — Multi-Viewpoint")
        self._sync_gcs()

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        super()._build_ui()

        controls = QWidget()
        row = QVBoxLayout(controls)
        row.setContentsMargins(0, 6, 0, 0)
        self.gcs_panel = GCSParameterPanel()
        row.addWidget(self.gcs_panel)
        self.gcs_status = QLabel("")
        self.gcs_status.setWordWrap(True)
        row.addWidget(self.gcs_status)
        self.layout().addWidget(controls)

        self.gcs_panel.parametersChanged.connect(self._on_gcs_parameters)
        self.gcs_panel.refineRequested.connect(self._on_refine)
        self.gcs_panel.commitRequested.connect(self._on_commit)
        self.gcs_panel.commit_gcs_btn.setText("Send to window")
        self.gcs_panel.commit_gcs_btn.setToolTip(
            "Push this fit back to the Solar Image Analysis window, where Commit GCS "
            "records it for the current frame."
        )

        for canvas, key in ((self.canvas_a, "A"), (self.canvas_b, "B")):
            canvas.set_click_callback(
                lambda x, y, button, panel=key: self._on_canvas_click(panel, x, y, button)
            )
            canvas.set_gcs_handle_callback(
                lambda name, x, y, done, panel=key: self._on_handle(panel, name, x, y, done)
            )

    # -------------------------------------------------------------- geometry
    def parameters(self) -> GCSParameters:
        """The shared fit, seeded on first use from viewpoint A."""
        if self._gcs_params is None:
            observer = self._observer("A")
            lon = (observer.lon_deg + 90.0) if observer is not None else 90.0
            self._gcs_params = GCSParameters(
                lon_deg=((lon + 180.0) % 360.0) - 180.0,
                lat_deg=0.0,
                tilt_deg=0.0,
                height_rsun=8.0,
                alpha_deg=30.0,
                kappa=0.30,
            )
        return self._gcs_params

    def _frame_for(self, panel: str) -> Any | None:
        if panel == "A":
            return self._reference_map()
        return self._map_b_reprojected or self._map_b

    def _observer(self, panel: str) -> ObserverGeometry | None:
        frame = self._frame_for(panel)
        if frame is None:
            return None
        label = self._reference_label if panel == "A" else self.observable_combo.currentText()
        return ObserverGeometry.from_frame(frame, label=label)

    def _canvas_for(self, panel: str) -> Any:
        return self.canvas_a if panel == "A" else self.canvas_b

    # ------------------------------------------------------------- rendering
    def _sync_gcs(self) -> None:
        """Redraw the one shell on every panel that has a usable frame."""
        params = self.parameters()
        self.gcs_panel.show_parameters(params)
        for panel in ("A", "B"):
            canvas = self._canvas_for(panel)
            observer = self._observer(panel)
            if observer is None:
                canvas.clear_gcs_overlay()
                continue
            x, y, _ = wireframe_arcsec(params, observer)
            canvas.set_gcs_overlay(x, y)
            canvas.set_gcs_handles(handle_positions_arcsec(params, observer))
            clicks = self._gcs_clicks[panel]
            canvas.set_measurement_overlay(
                [point[0] for point in clicks], [point[1] for point in clicks], connect=False
            )
        self._refresh_status()

    def _refresh_status(self) -> None:
        observer_a = self._observer("A")
        observer_b = self._observer("B")
        clicks = sum(len(points) for points in self._gcs_clicks.values())
        if observer_b is None:
            self.gcs_status.setText(
                f"One viewpoint · {clicks} point(s) clicked. Fetch viewpoint B — a single "
                "view cannot constrain the propagation longitude, so it is held fixed."
            )
            return
        from src.Backend.gcs_model import observer_separation_deg

        separation = observer_separation_deg(observer_a, observer_b) if observer_a else 0.0
        note = ""
        if separation < MIN_USEFUL_SEPARATION_DEG:
            note = (
                f" — only {separation:.0f}° apart, too close to constrain longitude; "
                "it will be held fixed"
            )
        self.gcs_status.setText(
            f"Two viewpoints {separation:.0f}° apart · {clicks} point(s) clicked{note}"
        )

    # ------------------------------------------------------------ interaction
    def _on_gcs_parameters(self, params: GCSParameters) -> None:
        self._gcs_params = params
        self._sync_gcs()

    def _on_handle(self, panel: str, name: str, x: float, y: float, finished: bool) -> None:
        if name != "apex":
            return
        observer = self._observer(panel)
        if observer is None:
            return
        self._gcs_params = apply_apex_drag(self.parameters(), observer, (x, y))
        self._sync_gcs()

    def _on_canvas_click(self, panel: str, x: float, y: float, button: str) -> None:
        canvas = self._canvas_for(panel)
        # A handle drag ends with a press-release the scene also delivers here.
        if getattr(canvas, "gcs_drag_active", None) is not None and canvas.gcs_drag_active():
            return
        if button == "right":
            if self._gcs_clicks[panel]:
                self._gcs_clicks[panel].pop()
        elif button == "left":
            self._gcs_clicks[panel].append((float(x), float(y)))
        else:
            return
        self._sync_gcs()

    # --------------------------------------------------------------- refining
    def _viewpoints(self) -> list[GCSViewpoint]:
        out: list[GCSViewpoint] = []
        for panel in ("A", "B"):
            observer = self._observer(panel)
            if observer is None:
                continue
            out.append(
                GCSViewpoint(
                    observer,
                    np.asarray(self._gcs_clicks[panel], dtype=float).reshape(-1, 2),
                    observer.label or panel,
                )
            )
        return out

    def _on_refine(self) -> None:
        try:
            result = refine_gcs(self._viewpoints(), self.parameters())
        except ValueError as exc:
            self.gcs_status.setText(f"GCS refine: {exc}")
            return
        self._gcs_last_refinement = result
        self._gcs_params = result.parameters
        self._sync_gcs()
        self.gcs_status.setText(result.message)

    def _on_commit(self) -> None:
        """Push the fit back to the analysis window's GCS tool."""
        window = self._window_ref
        measure = getattr(window, "_measure", None)
        if measure is None or not hasattr(measure, "set_gcs_parameters"):
            self.gcs_status.setText("No analysis window to send this fit to.")
            return
        measure.set_gcs_parameters(self.parameters())
        self.gcs_status.setText(
            "Sent to the analysis window — use Commit GCS there to record it."
        )

    # ------------------------------------------------------------ base hooks
    def _render_reference(self) -> None:
        super()._render_reference()
        if hasattr(self, "gcs_panel"):
            self._sync_gcs()

    def _show_b(self, frame: Any, *, reprojected: bool, separation: Any) -> None:
        """Always show and fit viewpoint B's *own* view, never a reprojection.

        Reprojecting B onto A is the right thing for the blink comparison this
        dialog inherits, and the wrong thing here twice over: the reprojected
        frame carries A's observer, so fitting against it would silently restore
        the single-view degeneracy the second viewpoint exists to break — and the
        wireframe, computed for B, would not even line up with the picture on
        screen. So the original frame is what gets plotted and fitted.
        """
        own_view = self._map_b if reprojected and self._map_b is not None else frame
        super()._show_b(own_view, reprojected=False, separation=separation)
        self._map_b_reprojected = None
        self._sync_gcs()
