"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

PFSS sidebar section for the Solar Image Analysis window.

Mixed into ``SolarDataAnalysisWindow`` rather than written inline, the way
``src.ui.gcs.gcs_window_actions`` and ``gcs_window_exports`` already split that
window up: the solar window is long enough without another few hundred lines.

The section owns the whole workflow -- choose a photospheric boundary condition,
solve the potential field, trace field lines, project them onto the displayed
frame -- and keeps three rules that are easy to get wrong:

  * **Visibility toggles never recompute.** Ticking "closed loops" re-projects a
    solution already in memory (~20 ms). Only the Compute button solves.
  * **Every frame re-projects, nothing re-solves.** A synoptic magnetogram is
    valid across a rotation while the observer's view changes frame to frame, so
    stepping a sequence transforms the same traced field into each frame's WCS.
  * **The model needs only ``frame.coordinate_frame``.** Cropped frames become
    ``AiaArrayMap`` and lose ``wcs``/``observer_coordinate``, so gating on
    ``_has_world_coordinates`` would refuse PFSS on exactly the zoomed-in views
    where it is most wanted.

``sunkit_magex`` is optional. When it is missing the section stays visible but
disabled, carrying the install hint, so the feature is discoverable rather than
silently absent.
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
)

from src.backend.solar.pfss_magnetograms import (
    MAGNETOGRAM_SOURCES,
    SOURCE_ADAPT,
    SOURCE_DESCRIPTIONS,
    SOURCE_GONG,
    SOURCE_LABELS,
    SOURCE_LOCAL,
    MagnetogramError,
    MagnetogramRow,
    describe_magnetogram,
    fetch_magnetogram,
    load_magnetogram,
    search_magnetograms,
)
from src.backend.solar.pfss_model import (
    DEFAULT_NRHO,
    DEFAULT_RSS,
    DEFAULT_SEED_DENSITY,
    SEED_CLICK,
    SEED_MODE_LABELS,
    SEED_MODES,
    SEED_ROI,
    PfssParameters,
    PfssUnavailableError,
    build_seeds,
    field_lines_arcsec,
    import_pfss,
    pfss_available,
    pfss_unavailable_reason,
    solve_pfss,
    trace_field_lines,
)


# --------------------------------------------------------------------------- #
# Workers
# --------------------------------------------------------------------------- #

class PfssSolveWorker(QObject):
    """The whole PFSS pipeline: find, fetch, normalise, solve, trace.

    Deliberately one worker rather than a fetch worker chained to a solve worker.
    ``_start_worker`` allows one operation at a time and refuses a second with a
    *modal* dialog, and a worker's ``finished`` signal arrives before its QThread
    has finished unwinding -- so chaining a second operation from the first one's
    completion pops that dialog and wedges the window. One worker also means one
    progress stream and one cancel for what the user thinks of as one action.

    ``pfss()`` is a single call into compiled code and cannot be interrupted, so
    the cancel flag is honoured *between* stages and the result discarded. That is
    the pattern the vector-field workers already use for the opaque JSOC export;
    the status text says as much rather than implying Stop is instant.

    Progress values stay above 85 because ``_on_worker_progress`` discards
    anything in 5..85 while byte-level download progress is active.
    """

    progress = Signal(object, object)
    finished = Signal(object, object, object)   # (solution, traced, provenance)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        params: PfssParameters,
        *,
        magnetogram_path: str = "",
        source: str = SOURCE_GONG,
        cache_dir: str | Path = "",
        local_path: str = "",
        email: str = "",
        realization: int = 0,
        frame: Any = None,
        frame_time: Any = None,
        clicked_arcsec: list[tuple[float, float]] | None = None,
        roi_bounds_arcsec: tuple[float, float, float, float] | None = None,
    ):
        super().__init__()
        self._path = str(magnetogram_path or "")
        self._params = params
        self._source = str(source)
        self._cache_dir = cache_dir
        self._local_path = str(local_path or "")
        self._email = str(email or "")
        self._realization = int(realization)
        self._frame = frame
        self._frame_time = frame_time
        self._clicked = list(clicked_arcsec or [])
        self._roi = roi_bounds_arcsec
        self._cancel = threading.Event()

    def _acquire_magnetogram(self) -> str:
        """Return a local magnetogram path, downloading one if necessary."""
        if self._path and Path(self._path).is_file():
            return self._path
        if self._source == SOURCE_LOCAL:
            row = MagnetogramRow(source=SOURCE_LOCAL, path=self._local_path)
        else:
            self.progress.emit(None, "Searching for a synoptic magnetogram...")
            rows = search_magnetograms(self._source, self._frame_time, email=self._email)
            if not rows:
                raise MagnetogramError(
                    f"No {SOURCE_LABELS.get(self._source, self._source)} magnetogram was found "
                    "near this observation. Try another source, or a different date."
                )
            row = rows[0]
        self.progress.emit(None, "Downloading the magnetogram...")
        return str(
            fetch_magnetogram(
                row, self._cache_dir, email=self._email, cancel_cb=self._cancel.is_set
            )
        )

    def cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            if self._cancel.is_set():
                self.cancelled.emit()
                return

            # Load the solver before searching and downloading, not after: the
            # window only checks that the stack is installed, so a stack that is
            # installed but broken should fail here, not after a download.
            self.progress.emit(None, "Loading the PFSS solver...")
            import_pfss()
            if self._cancel.is_set():
                self.cancelled.emit()
                return

            path = self._acquire_magnetogram()
            if self._cancel.is_set():
                self.cancelled.emit()
                return

            self.progress.emit(88, "Reading the magnetogram...")
            magnetogram, provenance = load_magnetogram(
                path,
                source=self._source,
                realization=self._realization,
                frame_time=self._frame_time,
            )

            if self._cancel.is_set():
                self.cancelled.emit()
                return

            self.progress.emit(90, "Solving the potential field…")
            solution = solve_pfss(magnetogram, self._params, provenance=provenance)

            if self._cancel.is_set():
                self.cancelled.emit()
                return

            self.progress.emit(94, "Tracing field lines…")
            seeds = build_seeds(
                solution,
                frame=self._frame,
                params=self._params,
                clicked_arcsec=self._clicked,
                roi_bounds_arcsec=self._roi,
            )
            traced = trace_field_lines(solution, seeds, cancel_cb=self._cancel.is_set)

            if self._cancel.is_set():
                self.cancelled.emit()
                return

            self.progress.emit(99, "Projecting field lines...")
            provenance = dict(provenance)
            provenance["path"] = path
            self.finished.emit(solution, traced, provenance)
        except InterruptedError:
            self.cancelled.emit()
        except PfssUnavailableError as exc:
            self.failed.emit(str(exc))
        except MagnetogramError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())


# --------------------------------------------------------------------------- #
# The sidebar section
# --------------------------------------------------------------------------- #

class PfssControlsMixin:
    """The "Potential Field (PFSS)" sidebar card and its behaviour."""

    # ----------------------------------------------------------------- build
    def _build_pfss_group(self, parent_layout) -> None:
        """Potential-field extrapolation from a synoptic magnetogram.

        Shown for disk imagers and magnetographs: these are the views where
        footpoints and loop topology can be compared against the model.
        """
        group = QGroupBox("Potential Field (PFSS)")
        self.pfss_group = group
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        parent_layout.addWidget(group)

        self._pfss_solution = None
        self._pfss_traced = None
        self._pfss_provenance: dict[str, Any] = {}
        self._pfss_magnetogram_path = ""
        self._pfss_local_path = ""
        self._pfss_clicked: list[tuple[float, float]] = []
        self._pfss_overlay_cache: dict[tuple, Any] = {}

        row = 0
        self.pfss_source_combo = QComboBox()
        for source in MAGNETOGRAM_SOURCES:
            self.pfss_source_combo.addItem(SOURCE_LABELS[source], source)
        self.pfss_source_combo.setToolTip(
            "Photospheric boundary condition for the extrapolation.\n\n"
            + "\n\n".join(
                f"{SOURCE_LABELS[s]}:\n{SOURCE_DESCRIPTIONS[s]}" for s in MAGNETOGRAM_SOURCES
            )
        )
        self.pfss_browse_btn = QPushButton("Browse…")
        self.pfss_browse_btn.setToolTip("Choose a synoptic magnetogram FITS file from disk.")
        self.pfss_browse_btn.setVisible(False)
        layout.addWidget(self._field_label("Magnetogram"), row, 0)
        layout.addWidget(self.pfss_source_combo, row, 1)
        layout.addWidget(self.pfss_browse_btn, row, 2)

        row += 1
        self.pfss_realization_spin = QSpinBox()
        self.pfss_realization_spin.setRange(0, 11)
        self.pfss_realization_spin.setToolTip(
            "Which of ADAPT's 12 flux-transport realizations to use. They differ\n"
            "mostly on the far side, which no instrument observed directly."
        )
        self.pfss_realization_label = self._field_label("Realization")
        layout.addWidget(self.pfss_realization_label, row, 0)
        layout.addWidget(self.pfss_realization_spin, row, 1, 1, 2)
        self.pfss_realization_label.setVisible(False)
        self.pfss_realization_spin.setVisible(False)

        row += 1
        self.pfss_magnetogram_label = QLabel("No magnetogram loaded.")
        self.pfss_magnetogram_label.setObjectName("SolarHintLabel")
        self.pfss_magnetogram_label.setWordWrap(True)
        self.pfss_magnetogram_label.setToolTip(
            "A synoptic map is assembled over a whole Carrington rotation, so the\n"
            "model describes the global field around this observation rather than\n"
            "the instantaneous field. The offset from the displayed frame is the\n"
            "number to judge it by."
        )
        layout.addWidget(self.pfss_magnetogram_label, row, 0, 1, 3)

        row += 1
        layout.addWidget(self._subheading("Model"), row, 0, 1, 3)

        row += 1
        self.pfss_rss_spin = QDoubleSpinBox()
        self.pfss_rss_spin.setRange(1.5, 10.0)
        self.pfss_rss_spin.setDecimals(2)
        self.pfss_rss_spin.setSingleStep(0.1)
        self.pfss_rss_spin.setValue(DEFAULT_RSS)
        self.pfss_rss_spin.setSuffix(" R☉")
        self.pfss_rss_spin.setKeyboardTracking(False)
        self.pfss_rss_spin.setToolTip(
            "Source surface height: the radius at which the field is forced\n"
            "radial, standing in for the solar wind dragging it open. 2.5 R☉ is\n"
            "the long-standing convention; lowering it opens more field."
        )
        layout.addWidget(self._field_label("Source surface"), row, 0)
        layout.addWidget(self.pfss_rss_spin, row, 1, 1, 2)

        row += 1
        self.pfss_nrho_spin = QSpinBox()
        self.pfss_nrho_spin.setRange(10, 150)
        self.pfss_nrho_spin.setValue(DEFAULT_NRHO)
        self.pfss_nrho_spin.setKeyboardTracking(False)
        self.pfss_nrho_spin.setToolTip(
            "Number of radial grid cells between the photosphere and the source\n"
            "surface. More is smoother and slower; 35 is the usual choice."
        )
        layout.addWidget(self._field_label("Radial cells"), row, 0)
        layout.addWidget(self.pfss_nrho_spin, row, 1, 1, 2)

        row += 1
        self.pfss_seed_combo = QComboBox()
        for mode in SEED_MODES:
            self.pfss_seed_combo.addItem(SEED_MODE_LABELS[mode], mode)
        self.pfss_seed_combo.setToolTip(
            "Where field lines start.\n\n"
            "Uniform grid: an equal-area sample over the visible hemisphere.\n"
            "Open-field regions: only footpoints that reach the source surface,\n"
            "  which is the clean way to show coronal-hole connectivity.\n"
            "Current crop: concentrate lines inside the crop rectangle.\n"
            "Clicked points: trace one line per click on the disk."
        )
        layout.addWidget(self._field_label("Seeds"), row, 0)
        layout.addWidget(self.pfss_seed_combo, row, 1, 1, 2)

        row += 1
        self.pfss_density_spin = QSpinBox()
        self.pfss_density_spin.setRange(4, 64)
        self.pfss_density_spin.setValue(DEFAULT_SEED_DENSITY)
        self.pfss_density_spin.setKeyboardTracking(False)
        self.pfss_density_spin.setToolTip(
            "Seed grid density. Tracing costs roughly 1.6 ms per field line, so\n"
            "this is also the speed control."
        )
        self.pfss_density_hint = QLabel("")
        self.pfss_density_hint.setObjectName("SolarHintLabel")
        layout.addWidget(self._field_label("Density"), row, 0)
        layout.addWidget(self.pfss_density_spin, row, 1)
        layout.addWidget(self.pfss_density_hint, row, 2)

        row += 1
        self.pfss_compute_btn = QPushButton("Compute PFSS")
        self.pfss_compute_btn.setObjectName("SolarPrimaryAction")
        self.pfss_compute_btn.setToolTip(
            "Download the magnetogram if needed, solve the potential field and\n"
            "trace field lines. Runs in the background; typically a few seconds."
        )
        self.pfss_clear_btn = QPushButton("Clear")
        self.pfss_clear_btn.setToolTip("Remove the PFSS overlay and drop the solution.")
        layout.addWidget(self.pfss_compute_btn, row, 0, 1, 2)
        layout.addWidget(self.pfss_clear_btn, row, 2)

        row += 1
        layout.addWidget(self._subheading("Show"), row, 0, 1, 3)

        row += 1
        self.pfss_open_check = QCheckBox("Open field")
        self.pfss_open_check.setChecked(True)
        self.pfss_open_check.setToolTip(
            "Field lines reaching the source surface, coloured by the sign of Br\n"
            "at the footpoint: red outward, blue inward."
        )
        self.pfss_closed_check = QCheckBox("Closed loops")
        self.pfss_closed_check.setChecked(True)
        self.pfss_closed_check.setToolTip("Field lines returning to the photosphere (grey).")
        layout.addWidget(self.pfss_open_check, row, 0, 1, 2)
        layout.addWidget(self.pfss_closed_check, row, 2)

        row += 1
        self.pfss_regions_check = QCheckBox("Open-field boundaries")
        self.pfss_regions_check.setChecked(True)
        self.pfss_regions_check.setToolTip(
            "Outline the footpoint regions whose field is open — the model's\n"
            "coronal holes, to compare against the dark areas in 193/211 Å."
        )
        self.pfss_near_side_check = QCheckBox("Near side only")
        self.pfss_near_side_check.setChecked(True)
        self.pfss_near_side_check.setToolTip(
            "Hide field lines rooted on the hemisphere facing away from us.\n\n"
            "They are not occulted once they rise above the limb, so they would\n"
            "otherwise be drawn as arcs outside the disk with no visible\n"
            "footpoint. Clear this for the geometrically complete picture."
        )
        layout.addWidget(self.pfss_regions_check, row, 0, 1, 2)
        layout.addWidget(self.pfss_near_side_check, row, 2)

        row += 1
        self.pfss_diagnostics_btn = QPushButton("Diagnostics…")
        self.pfss_diagnostics_btn.setToolTip(
            "Open the input magnetogram, the source-surface field with its\n"
            "neutral line, the open/closed map and the solution statistics."
        )
        self.pfss_status_label = QLabel("")
        self.pfss_status_label.setObjectName("SolarHintLabel")
        self.pfss_status_label.setWordWrap(True)
        layout.addWidget(self.pfss_diagnostics_btn, row, 0, 1, 3)
        row += 1
        layout.addWidget(self.pfss_status_label, row, 0, 1, 3)

        self._sync_pfss_density_hint()
        self._apply_pfss_availability()
        group.setVisible(False)

    def _connect_pfss_signals(self) -> None:
        """Wire the section up. Called from the window's _connect_signals."""
        self.pfss_source_combo.currentIndexChanged.connect(lambda _i: self._on_pfss_source_changed())
        self.pfss_browse_btn.clicked.connect(self._on_pfss_browse)
        self.pfss_compute_btn.clicked.connect(self._on_pfss_compute)
        self.pfss_clear_btn.clicked.connect(self._on_pfss_clear)
        self.pfss_diagnostics_btn.clicked.connect(self._on_pfss_diagnostics)
        self.pfss_density_spin.valueChanged.connect(lambda _v: self._sync_pfss_density_hint())
        self.pfss_seed_combo.currentIndexChanged.connect(lambda _i: self._sync_pfss_density_hint())
        # Visibility toggles only re-project what is already in memory.
        for check in (
            self.pfss_open_check,
            self.pfss_closed_check,
            self.pfss_regions_check,
            self.pfss_near_side_check,
        ):
            check.toggled.connect(lambda _on: self._refresh_pfss_overlay())

    # ----------------------------------------------------------- availability
    def _apply_pfss_availability(self) -> None:
        """Disable the section with an install hint when sunkit-magex is absent.

        Runs while the window is being built, so it must stay cheap:
        ``pfss_available`` only checks that the packages are installed. A stack
        that is installed but broken is caught by the first solve instead; see
        ``_on_pfss_failed``.
        """
        available = pfss_available()
        self._pfss_available = available
        for widget in (
            self.pfss_source_combo,
            self.pfss_browse_btn,
            self.pfss_realization_spin,
            self.pfss_rss_spin,
            self.pfss_nrho_spin,
            self.pfss_seed_combo,
            self.pfss_density_spin,
            self.pfss_compute_btn,
            self.pfss_clear_btn,
            self.pfss_diagnostics_btn,
            self.pfss_open_check,
            self.pfss_closed_check,
            self.pfss_regions_check,
            self.pfss_near_side_check,
        ):
            widget.setEnabled(available)
        if not available:
            self.pfss_status_label.setText(
                pfss_unavailable_reason()
                or "PFSS modelling needs the optional 'sunkit-magex' package.\n"
                "Install it with: python3 -m pip install sunkit-magex"
            )

    def _on_pfss_failed(self, _message: str) -> None:
        """Disable the section if the solve failed because the stack is broken.

        Connected ahead of the window's generic failure dialog, which reports
        the error itself; this only brings the section into line with it.
        """
        if not pfss_available():
            self._apply_pfss_availability()

    def _pfss_seed_mode(self) -> str:
        return str(self.pfss_seed_combo.currentData() or SEED_MODES[0])

    def _pfss_source(self) -> str:
        return str(self.pfss_source_combo.currentData() or SOURCE_GONG)

    def _pfss_parameters(self) -> PfssParameters:
        return PfssParameters(
            nrho=int(self.pfss_nrho_spin.value()),
            rss=float(self.pfss_rss_spin.value()),
            seed_mode=self._pfss_seed_mode(),
            seed_density=int(self.pfss_density_spin.value()),
        ).normalized()

    def _sync_pfss_density_hint(self) -> None:
        """Show how many seeds the current density means, and the rough cost."""
        density = int(self.pfss_density_spin.value())
        mode = self._pfss_seed_mode()
        if mode == SEED_CLICK:
            self.pfss_density_hint.setText(f"{len(self._pfss_clicked)} clicked")
            self.pfss_density_spin.setEnabled(bool(self._pfss_available))
            return
        # The grid is 2*density in longitude by density in latitude, and roughly
        # half of it sits on the far side and is dropped before tracing.
        seeds = max(1, 2 * density * density)
        self.pfss_density_hint.setText(f"≈{seeds // 2} lines")

    def _on_pfss_source_changed(self) -> None:
        source = self._pfss_source()
        is_adapt = source == SOURCE_ADAPT
        is_local = source == SOURCE_LOCAL
        # Sub-widgets of a collapsed card must not be re-shown: the section hides
        # its content's direct children, so isHidden() answers truthfully.
        expanded = self._pfss_group_expanded()
        self.pfss_browse_btn.setVisible(is_local and expanded)
        self.pfss_realization_label.setVisible(is_adapt and expanded)
        self.pfss_realization_spin.setVisible(is_adapt and expanded)
        # Never overwrite the install hint: when the optional stack is missing
        # that message is the only thing telling the user why the card is inert.
        if getattr(self, "_pfss_available", False):
            self.pfss_status_label.setText(SOURCE_DESCRIPTIONS.get(source, ""))

    def _pfss_group_expanded(self) -> bool:
        try:
            from src.ui.widgets.collapsible_sections import is_group_expanded

            return bool(is_group_expanded(self.pfss_group))
        except Exception:
            return True

    def _on_pfss_browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a synoptic magnetogram", "", "FITS files (*.fits *.fts *.fits.gz *.fts.gz);;All files (*)"
        )
        if not path:
            return
        self._pfss_local_path = path
        self.pfss_status_label.setText(f"Selected {Path(path).name}")

    # -------------------------------------------------------------- compute
    def _on_pfss_compute(self) -> None:
        if not getattr(self, "_pfss_available", False):
            self._apply_pfss_availability()
            return
        if not self._map_frames:
            self.pfss_status_label.setText("Load solar frames first.")
            return
        # Checked here as well as in _start_worker: that one refuses with a modal
        # dialog, which is the wrong way to say "busy" for a button press.
        if self.is_operation_running():
            self.pfss_status_label.setText("Another operation is still running.")
            return

        frame = self._pfss_current_frame()
        if frame is None or getattr(frame, "coordinate_frame", None) is None:
            self.pfss_status_label.setText(
                "This frame has no usable solar coordinate system, so field lines "
                "cannot be projected onto it."
            )
            return

        source = self._pfss_source()
        if source == SOURCE_LOCAL and not self._pfss_local_path:
            self.pfss_status_label.setText("Choose a magnetogram file first.")
            return

        mode = self._pfss_seed_mode()
        if mode == SEED_CLICK and not self._pfss_clicked:
            self.pfss_status_label.setText("Click the solar disk to place seed points first.")
            return
        roi = self._pfss_roi_bounds() if mode == SEED_ROI else None
        if mode == SEED_ROI and roi is None:
            self.pfss_status_label.setText("Set a crop rectangle first, or pick another seed mode.")
            return

        frame_time = self._pfss_frame_time(frame)
        worker = PfssSolveWorker(
            self._pfss_parameters(),
            magnetogram_path=self._pfss_magnetogram_path,
            source=source,
            cache_dir=self.cache_dir,
            local_path=self._pfss_local_path,
            email=self._pfss_email(),
            realization=int(self.pfss_realization_spin.value()),
            frame=frame,
            frame_time=frame_time,
            clicked_arcsec=list(self._pfss_clicked),
            roi_bounds_arcsec=roi,
        )
        self.pfss_status_label.setText(
            "Working... Stop takes effect between stages; the solve itself cannot "
            "be interrupted."
        )
        self._start_worker(worker)

    def _on_pfss_solved(self, solution: object, traced: object, provenance: object) -> None:
        self._pfss_solution = solution
        self._pfss_traced = traced
        if provenance:
            self._pfss_provenance = dict(provenance)
            # Keep the resolved path so changing a model parameter and re-solving
            # does not go back to the network for the same magnetogram.
            self._pfss_magnetogram_path = str(self._pfss_provenance.get("path", "") or "")
            self.pfss_magnetogram_label.setText(describe_magnetogram(self._pfss_provenance))
        self._pfss_overlay_cache = {}

        lines = int(getattr(traced, "line_count", 0))
        open_count = int(np.count_nonzero(getattr(traced, "open_mask", np.zeros(0, bool))))
        seconds = float(getattr(solution, "solve_seconds", 0.0))
        self.pfss_status_label.setText(
            f"{lines} field lines ({open_count} open, {lines - open_count} closed); "
            f"solve {seconds:.1f} s."
        )
        self._warn_if_pfss_overlay_exceeds_view()
        self._refresh_pfss_overlay()
        self._sync_pfss_diagnostics()

    def _on_pfss_clear(self) -> None:
        self._pfss_solution = None
        self._pfss_traced = None
        self._pfss_clicked = []
        self._pfss_overlay_cache = {}
        self.pfss_status_label.setText("")
        self._sync_pfss_density_hint()
        for canvas in self._all_plot_canvases():
            if hasattr(canvas, "clear_pfss_overlay"):
                canvas.clear_pfss_overlay()
        self._sync_pfss_diagnostics()

    # --------------------------------------------------------------- overlay
    def _refresh_pfss_overlay(self) -> None:
        """Project the traced field onto the displayed frame.

        Degrades silently to a hidden overlay for frames with no usable solar
        coordinate system, mirroring how the graticule handles derived arrays that
        lost their WCS.
        """
        canvas = self._active_canvas()
        if not hasattr(canvas, "set_pfss_overlay"):
            return
        traced = getattr(self, "_pfss_traced", None)
        if traced is None or not self._map_frames:
            canvas.set_pfss_overlay(None, visible=False)
            return

        frame = self._pfss_current_frame()
        if frame is None or getattr(frame, "coordinate_frame", None) is None:
            canvas.set_pfss_overlay(None, visible=False)
            return

        show_open = self.pfss_open_check.isChecked()
        show_closed = self.pfss_closed_check.isChecked()
        show_regions = self.pfss_regions_check.isChecked()
        near_side = self.pfss_near_side_check.isChecked()
        if not (show_open or show_closed or show_regions):
            canvas.set_pfss_overlay(None, visible=False)
            return

        # Cache on the frame's observation time rather than id(frame): the window
        # replaces _map_frames wholesale on every crop or level change, and id()
        # is reused after garbage collection. A stale graticule is cosmetic; a
        # stale field overlay would draw the wrong rotation's field.
        cache_key = (
            str(self._pfss_frame_time(frame)),
            str(self._pfss_provenance.get("path", "")),
            id(traced),
            show_open,
            show_closed,
            show_regions,
            near_side,
        )
        overlay = self._pfss_overlay_cache.get(cache_key)
        if overlay is None:
            try:
                overlay = field_lines_arcsec(
                    traced,
                    frame,
                    show_open=show_open,
                    show_closed=show_closed,
                    show_boundaries=show_regions,
                    near_side_only=near_side,
                )
            except Exception:
                canvas.set_pfss_overlay(None, visible=False)
                return
            if len(self._pfss_overlay_cache) > 64:
                self._pfss_overlay_cache.clear()
            self._pfss_overlay_cache[cache_key] = overlay

        canvas.set_pfss_overlay(overlay, visible=not overlay.is_empty())

    def _warn_if_pfss_overlay_exceeds_view(self) -> None:
        """Say so when the source surface is mostly outside the visible field.

        The view is pinned to the image extent, so on a cropped frame -- a few
        hundred arcsec across -- a 2.5 R☉ overlay is almost entirely off-view and
        would otherwise just look broken.
        """
        frame = self._pfss_current_frame()
        if frame is None:
            return
        try:
            radius = float(self._solar_radius_arcsec(frame))
            half_width = abs(float(np.shape(frame.data)[1])) * 0.5 * abs(
                float(self._current_axis_transform.get("x_scale_arcsec_per_pix", 0.0) or 0.0)
            )
        except Exception:
            return
        reach = float(self.pfss_rss_spin.value()) * radius
        if half_width > 0 and reach > half_width * 1.5:
            self.pfss_status_label.setText(
                self.pfss_status_label.text()
                + f"\nThe {self.pfss_rss_spin.value():.1f} R☉ source surface reaches "
                f"{reach:.0f}″, beyond this view (±{half_width:.0f}″); outer field "
                "lines run off-screen."
            )

    # ----------------------------------------------------------------- misc
    def _pfss_current_frame(self) -> Any:
        if not self._map_frames:
            return None
        index = max(0, min(self._current_frame_index, len(self._map_frames) - 1))
        return self._map_frames[index]

    def _pfss_frame_time(self, frame: Any) -> Any:
        try:
            from src.backend.solar.solar_data_analysis import frame_observation_time

            return frame_observation_time(frame)
        except Exception:
            return None

    def _pfss_email(self) -> str:
        """Reuse the JSOC email already stored for SDO downloads."""
        for attr in ("jsoc_email_edit", "sdo_email_edit"):
            widget = getattr(self, attr, None)
            if widget is not None and hasattr(widget, "text"):
                return str(widget.text() or "").strip()
        return ""

    def _pfss_roi_bounds(self) -> tuple[float, float, float, float] | None:
        """Arcsec bounds of the active crop, for crop-restricted seeding."""
        transform = getattr(self, "_current_axis_transform", None)
        data = getattr(self, "_current_map_data", None)
        if not transform or data is None:
            return None
        try:
            height, width = int(np.shape(data)[0]), int(np.shape(data)[1])
            x_scale = float(transform["x_scale_arcsec_per_pix"])
            y_scale = float(transform["y_scale_arcsec_per_pix"])
            x_ref = float(transform["x_ref_pix"])
            y_ref = float(transform["y_ref_pix"])
            x0 = (0 - x_ref - 0.5) * x_scale
            x1 = (width - x_ref - 0.5) * x_scale
            y0 = (0 - y_ref - 0.5) * y_scale
            y1 = (height - y_ref - 0.5) * y_scale
        except Exception:
            return None
        if not all(np.isfinite(v) for v in (x0, x1, y0, y1)):
            return None
        return (x0, y0, x1, y1)

    def _on_pfss_canvas_click(self, x_arcsec: float, y_arcsec: float) -> None:
        """Record a seed point when the click-to-seed mode is active."""
        if self._pfss_seed_mode() != SEED_CLICK:
            return
        if not (np.isfinite(x_arcsec) and np.isfinite(y_arcsec)):
            return
        self._pfss_clicked.append((float(x_arcsec), float(y_arcsec)))
        self._sync_pfss_density_hint()
        self.pfss_status_label.setText(
            f"{len(self._pfss_clicked)} seed point(s) placed. Compute to trace them."
        )

    def _sync_pfss_seed_modes(self) -> None:
        """Disable click-to-seed on the renderer that cannot report clicks.

        Only the pyqtgraph canvas plumbs clicks; the publication renderer has no
        click or hover plumbing at all.
        """
        canvas = self._active_canvas()
        clickable = hasattr(canvas, "set_click_callback")
        index = self.pfss_seed_combo.findData(SEED_CLICK)
        if index < 0:
            return
        model = self.pfss_seed_combo.model()
        item = model.item(index) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(bool(clickable))
        if not clickable and self._pfss_seed_mode() == SEED_CLICK:
            self.pfss_seed_combo.setCurrentIndex(self.pfss_seed_combo.findData(SEED_MODES[0]))
            self.pfss_status_label.setText(
                "Click-to-seed needs the interactive renderer; switched to a uniform grid."
            )

    # ---------------------------------------------------------- session I/O
    def _collect_pfss_state(self) -> dict[str, Any]:
        """Settings worth saving with a session (never the solution itself)."""
        if not hasattr(self, "pfss_source_combo"):
            return {}
        state: dict[str, Any] = {
            "source": self._pfss_source(),
            "nrho": int(self.pfss_nrho_spin.value()),
            "rss": float(self.pfss_rss_spin.value()),
            "seed_mode": self._pfss_seed_mode(),
            "seed_density": int(self.pfss_density_spin.value()),
            "realization": int(self.pfss_realization_spin.value()),
            "show_open": bool(self.pfss_open_check.isChecked()),
            "show_closed": bool(self.pfss_closed_check.isChecked()),
            "show_boundaries": bool(self.pfss_regions_check.isChecked()),
            "near_side_only": bool(self.pfss_near_side_check.isChecked()),
            "clicked_arcsec": list(self._pfss_clicked),
        }
        provenance = getattr(self, "_pfss_provenance", {}) or {}
        if provenance.get("path"):
            state["magnetogram_path"] = str(provenance["path"])
        if provenance.get("carrington_rotation"):
            state["carrington_rotation"] = provenance["carrington_rotation"]
        if provenance:
            state["magnetogram_label"] = describe_magnetogram(provenance)
        return state

    def _restore_pfss_state(self, state: dict[str, Any]) -> None:
        """Replay saved PFSS settings.

        Settings only, deliberately: re-solving needs a background worker, and
        the synchronous restore path cannot chain one. The controls come back so
        the work is one click away and the user is told, rather than left
        wondering why the overlay is missing -- the same contract
        ``_restore_processing_widgets`` uses for level and derotation.
        """
        if not state or not hasattr(self, "pfss_source_combo"):
            return

        def _set(widget, value, setter="setValue"):
            if value is None:
                return
            was = widget.blockSignals(True)
            try:
                getattr(widget, setter)(value)
            except Exception:
                pass
            widget.blockSignals(was)

        index = self.pfss_source_combo.findData(str(state.get("source") or ""))
        if index >= 0:
            _set(self.pfss_source_combo, index, "setCurrentIndex")
        seed_index = self.pfss_seed_combo.findData(str(state.get("seed_mode") or ""))
        if seed_index >= 0:
            _set(self.pfss_seed_combo, seed_index, "setCurrentIndex")

        _set(self.pfss_nrho_spin, state.get("nrho"))
        _set(self.pfss_rss_spin, state.get("rss"))
        _set(self.pfss_density_spin, state.get("seed_density"))
        _set(self.pfss_realization_spin, state.get("realization"))
        for widget, key in (
            (self.pfss_open_check, "show_open"),
            (self.pfss_closed_check, "show_closed"),
            (self.pfss_regions_check, "show_boundaries"),
            (self.pfss_near_side_check, "near_side_only"),
        ):
            if key in state:
                _set(widget, bool(state[key]), "setChecked")

        self._pfss_clicked = [
            (float(x), float(y)) for x, y in (state.get("clicked_arcsec") or [])
        ]
        saved_path = str(state.get("magnetogram_path") or "")
        if saved_path and Path(saved_path).is_file():
            # Still in the cache, so a re-solve will not go back to the network.
            self._pfss_magnetogram_path = saved_path
            if state.get("source") == SOURCE_LOCAL:
                self._pfss_local_path = saved_path

        label = str(state.get("magnetogram_label") or "")
        if label:
            self.pfss_magnetogram_label.setText(label)
        self._on_pfss_source_changed()
        self._sync_pfss_density_hint()
        self.pfss_status_label.setText(
            "Saved PFSS settings restored — click Compute PFSS to reproduce the overlay."
        )

    def _on_pfss_diagnostics(self) -> None:
        """Open the detached diagnostics window for the current solution."""
        if self._pfss_solution is None:
            self.pfss_status_label.setText("Compute a PFSS solution first.")
            return
        try:
            from src.ui.solar.pfss_diagnostics_window import PfssDiagnosticsWindow
        except Exception as exc:
            self.pfss_status_label.setText(f"The diagnostics window is unavailable: {exc}")
            return
        # Reuse an open window rather than stacking a new one on every click,
        # matching how the GCS window is reopened with fresh state.
        window = getattr(self, "_pfss_diagnostics_window", None)
        if window is not None:
            try:
                window.set_solution(
                    self._pfss_solution, self._pfss_traced, self._pfss_provenance
                )
                window.show()
                window.raise_()
                return
            except RuntimeError:
                # The user closed it and Qt already deleted the C++ object.
                window = None

        window = PfssDiagnosticsWindow(
            self,
            solution=self._pfss_solution,
            traced=self._pfss_traced,
            provenance=self._pfss_provenance,
            theme=getattr(self, "theme", None),
        )
        self._pfss_diagnostics_window = window
        window.show()
        window.raise_()

    def _sync_pfss_diagnostics(self) -> None:
        """Push a new solution into the diagnostics window if it is open."""
        window = getattr(self, "_pfss_diagnostics_window", None)
        if window is None:
            return
        try:
            window.set_solution(self._pfss_solution, self._pfss_traced, self._pfss_provenance)
        except RuntimeError:
            self._pfss_diagnostics_window = None
