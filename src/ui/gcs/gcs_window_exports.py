"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Snapshot, movie and report exports of the GCS fitting window
(src/ui/gcs/gcs_window_exports.py).

Every export is drawn again from the data, never grabbed from the screen. With
hardware acceleration on — the default — each image panel is an OpenGL surface,
and ``QWidget.grab()`` returns those blank on macOS: that is what the old
snapshot saved. The panels hand over the arrays, display ranges, colour tables
and captions they draw (``GCSViewpointPanel.export_render``), the window adds
the model wireframes (``_panel_wireframes``), and ``src.backend.gcs.gcs_figures``
draws the page in light mode.

A movie or a report steps the panels through time exactly as playback does,
recorded shells included, and then puts back what was on screen: the time, the
working models, their formal errors and the front points. An export never
costs an uncommitted fit.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from src.ui.common.gui_shared import pick_export_path

#: Model keys, as in ``src.ui.gcs.gcs_fitting_window`` (not imported: it imports this module).
_GCS = "gcs"
_SHOCK = "shock"

#: Movie frames: 13 x 5.4 inches at this resolution is about 1560 x 650 pixels.
MOVIE_DPI = 120
#: Viewpoint images embedded in the PDF report: narrower than the snapshot, so
#: their lettering stays legible once scaled to the page width.
REPORT_IMAGE_DPI = 170
REPORT_IMAGE_WIDTH_IN = 10.5


class ExportCancelled(Exception):
    """The user cancelled a movie or report while it was being made."""


class GCSWindowExports:
    """Window mixin: exports drawn from the fitting state, in light mode."""

    # ------------------------------------------------------------ what is shown
    def _wireframe_style(self) -> Any:
        from src.backend.gcs.gcs_figures import WireframeStyle

        return WireframeStyle(
            gcs_rgb=tuple(self._wireframe_colour),
            shock_rgb=tuple(self._shock_colour),
            width_px=float(self.width_slider.value()),
            opacity=float(self.opacity_slider.value()) / 100.0,
        )

    def _export_views(self) -> list[Any]:
        """Each panel as the exports draw it: the image, the models and the front points."""
        active = self._active_panels()
        clicks = self._track().clicks
        banners = self.banner_check.isChecked()
        views = []
        for panel in self.panels:
            view = panel.export_render()
            if not banners:
                # Banners hidden on screen stay out of the exports; each panel keeps
                # only its name line ("A · SOHO C2") so the views can be told apart.
                view = replace(view, caption=(view.caption.splitlines() or [view.label])[0])
            gcs_xy, shock_xy = self._panel_wireframes(panel)
            # As on screen: only views that take part in the fit show front points.
            points = tuple(tuple(point) for point in clicks[panel.label]) if panel in active else ()
            views.append(replace(view, gcs_xy=gcs_xy, shock_xy=shock_xy, points=points, in_fit=panel in active))
        return views

    def _export_title(self, suffix: str = "") -> str:
        when = self._shared_time
        title = f"GCS CME fitting · {when:%Y-%m-%d %H:%M:%S} UTC" if when is not None else "GCS CME fitting"
        return f"{title} · {suffix}" if suffix else title

    def _export_footer(self) -> str:
        """Where each drawn shell comes from, and which views the fit leaves out."""
        parts = []
        for key, name, check in ((_GCS, "GCS", self.wireframe_check), (_SHOCK, "Shock", self.shock_check)):
            if check.isChecked():
                parts.append(f"{name}: {self._recorded_notes.get(key, 'working model, not recorded')}")
        excluded = [panel.label for panel in self.panels if panel.frames and not panel.is_synchronized()]
        if excluded:
            tolerance = self._sync_tolerance_seconds() / 60.0
            parts.append(f"{', '.join(excluded)} beyond the {tolerance:g}-min time tolerance: drawn, not fitted")
        return " · ".join(parts)

    def _viewpoints_figure(
        self, *, suffix: str = "", dpi: float = 100.0, width_in: float = 13.0, annotate: bool = True
    ) -> Any:
        """The page of three viewpoints; ``annotate`` adds the title and footer a
        standalone image needs (the report gives them a heading and caption)."""
        from src.backend.gcs.gcs_figures import viewpoints_figure

        return viewpoints_figure(
            self._export_views(),
            self._wireframe_style(),
            title=self._export_title(suffix) if annotate else "",
            footer=self._export_footer() if annotate else "",
            width_in=width_in,
            dpi=dpi,
        )

    def _report_image(self) -> bytes:
        from src.backend.common.figure_export import figure_png_bytes

        figure = self._viewpoints_figure(dpi=REPORT_IMAGE_DPI, width_in=REPORT_IMAGE_WIDTH_IN, annotate=False)
        return figure_png_bytes(figure, dpi=REPORT_IMAGE_DPI)

    def _exports_blocked(self) -> str:
        """Why a movie or report cannot start now, or an empty string."""
        if any(panel.is_fetching() for panel in self.panels):
            return "Wait for the channels to finish loading (or cancel the downloads) first."
        return ""

    # ------------------------------------------------------------ time travel
    def _show_time_index(self, index: int) -> None:
        """Move every panel to time-axis ``index`` as a playback step does."""
        was = self.time_slider.blockSignals(True)
        self.time_slider.setValue(int(index))
        self.time_slider.blockSignals(was)
        self._on_time_changed(int(index), user_initiated=False)

    def _time_index(self, when: datetime) -> int:
        axis = self._time_axis
        return min(range(len(axis)), key=lambda i: abs((axis[i] - when).total_seconds()))

    @contextmanager
    def _held_view_state(self) -> Iterator[None]:
        """Let an export step through time, then restore the window as it was.

        Stepping follows the recorded fits and drops refinements, as playback
        does. Afterwards the time, the working models, their refinements (the
        formal errors a commit records) and the front points are put back.
        """
        self.pause()
        index = self.time_slider.value()
        requested = self._requested_time
        notes = dict(self._recorded_notes)
        saved = {
            key: (
                track.params,
                track.last_refinement,
                {label: list(points) for label, points in track.clicks.items()},
                dict(track.frame_points),
                list(track.click_order),
            )
            for key, track in self._tracks.items()
        }
        try:
            yield
        finally:
            if self._time_axis:
                self._show_time_index(min(index, len(self._time_axis) - 1))
            self._requested_time = requested
            for key, (params, refinement, clicks, frame_points, order) in saved.items():
                track = self._tracks[key]
                track.params = params
                track.last_refinement = refinement
                track.clicks = clicks
                track.frame_points = frame_points
                track.click_order = order
            self._recorded_notes = notes
            self._sync_all()

    # ---------------------------------------------------------------- progress
    def _progress_dialog(self, title: str, maximum: int) -> QProgressDialog:
        dialog = QProgressDialog(title, "Cancel", 0, max(1, int(maximum)), self)
        dialog.setWindowTitle(title)
        # Application-modal: another window must not move this one's event or
        # time while an export steps through it.
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        QApplication.processEvents()
        return dialog

    @staticmethod
    def _progress_callbacks(dialog: QProgressDialog) -> tuple[Callable[..., None], Callable[[], bool]]:
        def progress(done: int, total: int | None = None, text: str | None = None) -> None:
            if total is not None and total != dialog.maximum():
                dialog.setMaximum(max(1, int(total)))
            if text:
                dialog.setLabelText(str(text))
            dialog.setValue(min(int(done), dialog.maximum()))
            QApplication.processEvents()

        return progress, dialog.wasCanceled

    # ---------------------------------------------------------------- snapshot
    def write_snapshot(self, path: str | Path) -> Path:
        """The three viewpoints as shown, in the format ``path``'s suffix names."""
        from src.backend.common.figure_export import save_figure

        return save_figure(self._viewpoints_figure(), path)

    def _save_snapshot(self) -> None:
        from src.backend.common.figure_export import FIGURE_EXPORT_FILTERS

        self.pause()
        stamp = f"_{self._shared_time:%Y%m%d_%H%M%S}" if self._shared_time is not None else ""
        path, _ext = pick_export_path(
            self, "Save GCS viewpoints", f"cme_gcs_viewpoints{stamp}.png", FIGURE_EXPORT_FILTERS
        )
        if not path:
            return
        try:
            written = self.write_snapshot(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Snapshot failed", str(exc))
            return
        self._set_status(f"Viewpoint snapshot saved: {written.name}. Export analysis JSON for numerical provenance.")

    # ------------------------------------------------------------------- movie
    def write_movie(
        self,
        path: str | Path,
        *,
        fps: float,
        progress_cb: Callable[..., None] | None = None,
        cancel_cb: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Every time step with its shells, as playback shows it, as a GIF or MP4.

        Returns the path written, or ``None`` when cancelled.
        """
        from src.backend.common.figure_export import figure_rgb
        from src.backend.solar.solar_data_analysis import write_movie_frames

        count = len(self._time_axis)
        if count < 2:
            raise ValueError("A movie needs at least two time steps — load the channels first.")

        def frames() -> Iterator[Any]:
            for index in range(count):
                self._show_time_index(index)
                yield figure_rgb(self._viewpoints_figure(suffix=f"frame {index + 1}/{count}", dpi=MOVIE_DPI))

        with self._held_view_state():
            return write_movie_frames(
                path, frames(), total=count, fps=fps, progress_cb=progress_cb, cancel_cb=cancel_cb
            )

    def _export_movie(self) -> None:
        self.pause()
        blocked = self._exports_blocked() or (
            "" if len(self._time_axis) > 1 else "Load the channels first: a movie needs at least two time steps."
        )
        if blocked:
            self._set_status(blocked)
            return
        start = self._time_axis[0]
        path, _ext = pick_export_path(
            self, "Export GCS movie", f"cme_gcs_movie_{start:%Y%m%d_%H%M}.mp4", "MP4 (*.mp4);;GIF (*.gif)"
        )
        if not path:
            return
        if Path(path).suffix.lower() == ".mp4" and not _ffmpeg_available():
            answer = QMessageBox.question(
                self,
                "MP4 unavailable",
                "MP4 export needs the 'imageio-ffmpeg' package, which isn't available.\n\nSave a GIF instead?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return
            path = str(Path(path).with_suffix(".gif"))
        dialog = self._progress_dialog("Rendering the GCS movie…", len(self._time_axis))
        progress, cancelled = self._progress_callbacks(dialog)
        try:
            written = self.write_movie(path, fps=float(self.fps_spin.value()), progress_cb=progress, cancel_cb=cancelled)
        except (OSError, ValueError, RuntimeError) as exc:
            dialog.close()
            QMessageBox.warning(self, "Movie export failed", str(exc))
            return
        dialog.close()
        if written is None:
            self._set_status("Movie export cancelled.")
        else:
            self._set_status(
                f"Movie saved: {Path(written).name} — {len(self._time_axis)} frames at {self.fps_spin.value()} fps."
            )

    # ------------------------------------------------------------------ report
    def _report_input(
        self,
        progress_cb: Callable[..., None] | None = None,
        cancel_cb: Callable[[], bool] | None = None,
    ) -> Any:
        """Gather the report's content, drawing each recorded fit on its own images."""
        from src.backend.gcs.gcs_model import observer_separation_deg
        from src.backend.gcs.gcs_report import GCSReportInput, ViewpointInfo
        from src.ui.gcs.gcs_viewpoint_panel import DIFFERENCE_MODES
        from src.version import APP_NAME, APP_VERSION

        def check_cancel() -> None:
            if cancel_cb is not None and cancel_cb():
                raise ExportCancelled()

        gcs, shock = self._tracks[_GCS], self._tracks[_SHOCK]
        times = sorted(set(gcs.fits) | set(shock.fits))
        total = len(times) + 2
        if progress_cb is not None:
            progress_cb(0, total, "Drawing the viewpoints on screen…")

        loaded = any(panel.frames for panel in self.panels)
        current = self._report_image() if loaded else None
        viewpoints = []
        for panel in self.panels:
            source = panel.source()
            stamps = panel.times()
            viewpoints.append(
                ViewpointInfo(
                    label=panel.label,
                    channel=source.label if source is not None else "—",
                    frames=len(panel.frames),
                    first_utc=min(stamps) if stamps else None,
                    last_utc=max(stamps) if stamps else None,
                    observer=panel.observer,
                )
            )
        observed = [panel for panel in self.panels if panel.observer is not None]
        separations = [
            (a.label, b.label, float(observer_separation_deg(a.observer, b.observer)))
            for index, a in enumerate(observed)
            for b in observed[index + 1:]
        ]
        data = GCSReportInput(
            generated_at=datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0),
            app_name=APP_NAME,
            app_version=APP_VERSION,
            event_range=self.event_range(),
            shared_time=self._shared_time,
            max_time_offset_s=self._sync_tolerance_seconds(),
            view_mode=dict(DIFFERENCE_MODES).get(self._difference_mode(), self._difference_mode()),
            frame_cap=int(self.max_frames_spin.value()),
            fit_order=int(self.tracking_panel.fit_order()),
            viewpoints=viewpoints,
            separations=separations,
            gcs=self.parameters(),
            shock=self.shock_parameters() if (self.shock_check.isChecked() or shock.fits) else None,
            gcs_refinement=gcs.last_refinement,
            shock_refinement=shock.last_refinement,
            current_observations=self._current_provenance(),
            current_figure=current,
            current_note=self._export_footer(),
            gcs_fits=dict(gcs.fits),
            gcs_provenance=dict(gcs.provenance),
            shock_fits=dict(shock.fits),
            shock_provenance=dict(shock.provenance),
            archived_gcs=dict(gcs.archived_fits),
            archived_shock=dict(shock.archived_fits),
        )
        if progress_cb is not None:
            progress_cb(1, total, "Drawing the recorded fits…")
        check_cancel()

        figures: dict[datetime, bytes | None] = {}
        captions: dict[datetime, str] = {}
        notes: dict[datetime, str] = {}
        if times and self._time_axis:
            with self._held_view_state():
                for number, when in enumerate(times, start=1):
                    check_cancel()
                    self._show_time_index(self._time_index(when))
                    # Draw a fit only on the images it was recorded from.
                    matched = [
                        key for key in (_GCS, _SHOCK)
                        if when in self._tracks[key].fits and self._recorded_time_for_current_frames(key) == when
                    ]
                    if matched:
                        figures[when] = self._report_image()
                        captions[when] = self._export_footer()
                    else:
                        figures[when] = None
                        notes[when] = "The images this fit was recorded from are no longer loaded."
                    if progress_cb is not None:
                        progress_cb(1 + number, total, f"Drawing recorded fit {number} of {len(times)}…")
        else:
            for when in times:
                figures[when] = None
                notes[when] = "The images this fit was recorded from are no longer loaded."
        data.fit_figures = figures
        data.fit_figure_captions = captions
        data.fit_figure_notes = notes
        return data

    def write_report(
        self,
        path: str | Path,
        *,
        progress_cb: Callable[..., None] | None = None,
        cancel_cb: Callable[[], bool] | None = None,
    ) -> Any:
        """Write the fitting report as a PDF; returns the result, or ``None`` when cancelled."""
        from src.backend.gcs.gcs_report import generate_gcs_report_pdf

        try:
            data = self._report_input(progress_cb, cancel_cb)
        except ExportCancelled:
            return None
        steps = len(data.fit_figures) + 2

        def layout_progress(percent: int, text: str) -> None:
            if progress_cb is not None:
                progress_cb(steps, steps + 1, text)

        return generate_gcs_report_pdf(path, data, progress_cb=layout_progress)

    def _export_report(self) -> None:
        self.pause()
        blocked = self._exports_blocked()
        if blocked:
            self._set_status(blocked)
            return
        start, _end = self.event_range()
        path, _ext = pick_export_path(
            self, "Export GCS fitting report", f"cme_gcs_report_{start:%Y%m%d_%H%M}.pdf", "PDF (*.pdf)"
        )
        if not path:
            return
        times = set(self._tracks[_GCS].fits) | set(self._tracks[_SHOCK].fits)
        dialog = self._progress_dialog("Writing the GCS fitting report…", len(times) + 3)
        progress, cancelled = self._progress_callbacks(dialog)
        try:
            result = self.write_report(path, progress_cb=progress, cancel_cb=cancelled)
        except (OSError, ValueError, RuntimeError) as exc:
            dialog.close()
            QMessageBox.warning(self, "Report export failed", str(exc))
            return
        dialog.close()
        if result is None:
            self._set_status("Report export cancelled.")
        else:
            self._set_status(f"Fitting report saved: {Path(result.path).name} ({result.figures_written} figures).")


def _ffmpeg_available() -> bool:
    try:
        import imageio_ffmpeg  # noqa: F401

        return True
    except Exception:
        return False
