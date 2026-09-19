"""Menus, scientific workflow guidance and reproducible GCS and shock result exports."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math

from PySide6.QtCore import QIODevice, QSaveFile, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog, QLabel,
    QMessageBox, QTextBrowser, QVBoxLayout,
)

from src.backend.gcs.gcs_model import GCSParameters
from src.ui.gcs.gcs_viewpoint_panel import _qdatetime_utc

#: Model keys, as in ``src.ui.gcs.gcs_fitting_window`` (not imported: it imports this module).
_GCS = "gcs"
_SHOCK = "shock"


def _json_value(value):
    """Use interoperable JSON: UTC timestamps and null for unavailable errors."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat() + "Z"
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class GCSWindowActions:
    """Small window mixin; all actions operate on the visible fitting state."""

    def _build_menus(self):
        self.menu_actions = {}
        # A local bar remains discoverable on macOS as well as other platforms.
        self.menuBar().setNativeMenuBar(False)

        def action(menu, key, label, slot, shortcut=None, checkable=False):
            item = QAction(label, self)
            item.setCheckable(checkable)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            item.triggered.connect(slot)
            menu.addAction(item)
            self.menu_actions[key] = item
            return item

        file_menu = self.menuBar().addMenu("&File")
        action(file_menu, "export_analysis", "Export analysis &JSON…", self._export_analysis, "Ctrl+Shift+S")
        action(file_menu, "export_csv", "Export recorded fits &CSV…", self.tracking_panel.export_csv)
        action(file_menu, "export_graph", "Export height–time &graph…", self.tracking_panel.export_graph)
        file_menu.addSeparator()
        action(file_menu, "snapshot", "Save viewpoint &snapshot…", self._save_snapshot)
        action(file_menu, "movie", "Export &movie (GIF/MP4)…", self._export_movie)
        action(file_menu, "report", "Export fitting &report (PDF)…", self._export_report)
        file_menu.addSeparator()
        action(file_menu, "close", "&Close window", self.close, "Ctrl+W")

        event_menu = self.menuBar().addMenu("&Event")
        action(event_menu, "load", "&Load all channels", self._fetch_all, "Ctrl+L")
        action(event_menu, "cancel", "Cancel downloads", self._cancel_downloads)
        action(event_menu, "ranges", "Apply event range to all channels", self._apply_event_range_to_channels)
        action(event_menu, "jump", "Go to &UTC time…", self._jump_to_time, "Ctrl+G")
        action(event_menu, "first", "First frame", self._rewind)
        action(event_menu, "previous", "Previous frame", self.previous_frame)
        action(event_menu, "next", "Next frame", self.next_frame)
        action(event_menu, "last", "Last frame", lambda: self.time_slider.setValue(self.time_slider.maximum()))
        event_menu.addSeparator()
        action(event_menu, "play", "Play / pause", self.toggle_play)

        view_menu = self.menuBar().addMenu("&View")
        modes = QActionGroup(self)
        modes.setExclusive(True)
        for key, title in (("raw", "Raw"), ("running", "Running difference"), ("base", "Base difference")):
            item = action(view_menu, "mode_" + key, title,
                          lambda _on, mode=key: self._on_difference_mode(mode), checkable=True)
            modes.addAction(item)
        view_menu.addSeparator()
        layouts = QActionGroup(self)
        layouts.setExclusive(True)
        for key, title in (("equal", "Equal viewpoints"), ("focus:0", "Focus A"),
                           ("focus:1", "Focus B"), ("focus:2", "Focus C")):
            item = action(view_menu, "layout_" + key, title,
                          lambda _on, mode=key: self._on_layout_button(mode), checkable=True)
            layouts.addAction(item)
        view_menu.addSeparator()
        for key, title, widget in (("wireframe", "GCS wireframe", self.wireframe_check),
                                   ("shock", "Shock wireframe", self.shock_check),
                                   ("limb", "Solar limb", self.limb_check),
                                   ("axes", "Arcsecond axes", self.axes_check),
                                   ("banner", "Image banners", self.banner_check)):
            # A lambda, not widget.setChecked: PySide calls a bound method of a
            # Python QCheckBox subclass without the checked argument.
            action(view_menu, key, title, lambda checked, target=widget: target.setChecked(checked), checkable=True)
        action(view_menu, "zoom", "Reset image zoom", self._reset_image_zoom)
        action(view_menu, "layout_reset", "Fit layout to window", lambda: self._fit_splitter(force=True))

        fit_menu = self.menuBar().addMenu("&Fit")
        editing = QActionGroup(self)
        editing.setExclusive(True)
        for key, title in ((_GCS, "Edit &GCS flux rope"), (_SHOCK, "Edit s&hock")):
            item = action(fit_menu, "edit_" + key, title,
                          lambda _on, model=key: self.set_editing_model(model), checkable=True)
            editing.addAction(item)
        fit_menu.addSeparator()
        action(fit_menu, "pick", "Pick front points",
               lambda checked: self.pick_points_check.setChecked(checked), checkable=True)
        action(fit_menu, "undo_point", "&Undo last front point", self._undo_last_point, "Ctrl+Z")
        action(fit_menu, "clear_points", "Clear current front points", self._clear_points)
        fit_menu.addSeparator()
        action(fit_menu, "refine", "&Refine current model", self._on_refine, "Ctrl+R")
        action(fit_menu, "commit", "&Record fit at current time", self._on_commit, "Ctrl+Return")
        action(fit_menu, "restore", "Restore recorded model at current time", self._restore_recorded_model)
        action(fit_menu, "delete", "Delete recorded fit at current time", self._delete_recorded_fit)
        fit_menu.addSeparator()
        action(fit_menu, "reset_model", "Reset model parameters", self._reset_model)
        action(fit_menu, "kinematics", "Fit recorded heights over time", self._on_fit_kinematics)

        help_menu = self.menuBar().addMenu("&Help")
        action(help_menu, "guide", "Fitting &workflow and scientific limits…", self._show_fitting_guide, "F1")
        for menu in (file_menu, event_menu, view_menu, fit_menu, help_menu):
            menu.aboutToShow.connect(self._sync_menu_actions)
        self._sync_menu_actions()

    def _sync_menu_actions(self):
        actions = self.menu_actions
        has_time = self._shared_time is not None and bool(self._time_axis)
        has_record = self._recorded_time_for_current_frames() is not None
        has_view = bool(self._active_panels())
        fits = self._track().fits
        fetching = any(panel.is_fetching() for panel in self.panels)
        loaded = any(panel.frames for panel in self.panels)
        enabled = {
            "load": not fetching,
            "cancel": fetching,
            "export_csv": bool(fits), "export_graph": bool(fits), "snapshot": loaded,
            "movie": len(self._time_axis) > 1 and not fetching,
            "report": (loaded or any(track.fits for track in self._tracks.values())) and not fetching,
            "jump": has_time, "refine": has_view,
            "commit": has_time and has_view, "restore": has_record, "delete": has_record,
            "undo_point": self._last_point_entry() is not None,
            "kinematics": len(fits) >= self.tracking_panel.fit_order() + 1,
        }
        for key in ("first", "previous", "next", "last", "play"):
            enabled[key] = len(self._time_axis) > 1
        for key, on in enabled.items():
            actions[key].setEnabled(on)
        selected = {
            "wireframe": self.wireframe_check.isChecked(), "shock": self.shock_check.isChecked(),
            "limb": self.limb_check.isChecked(), "banner": self.banner_check.isChecked(),
            "axes": self.axes_check.isChecked(), "pick": self.pick_points_check.isChecked(),
            "edit_" + _GCS: self._editing == _GCS, "edit_" + _SHOCK: self._editing == _SHOCK,
        }
        for mode in ("raw", "running", "base"):
            selected["mode_" + mode] = mode == self._difference_mode()
        layout = "equal" if self.layout_mode() == "equal" else f"focus:{self.stage.focus_index()}"
        for mode in ("equal", "focus:0", "focus:1", "focus:2"):
            selected["layout_" + mode] = layout == mode
        for key, on in selected.items():
            was = actions[key].blockSignals(True)
            actions[key].setChecked(on)
            actions[key].blockSignals(was)

    def _reset_image_zoom(self):
        for panel in self.panels:
            panel.canvas.reset_map_view()
            panel.render()

    def _cancel_downloads(self):
        for panel in self.panels:
            panel.cancel_fetch()
        self._set_status("Download cancellation requested; waiting for current requests to finish.")

    def _reset_model(self):
        """Reset the edited model; the shock starts again along the GCS direction."""
        self.pause()
        track = self._track()
        track.params = None
        track.last_refinement = None
        self._forget_recorded_note(self._editing)
        self._sync_all()
        self._set_status("Model reset. Recorded fits are available in the kinematics table.")

    def _recorded_parameters(self, when, model=_GCS):
        """A model's fit recorded at ``when``, as parameters its shell can be drawn from."""
        entry = self._tracks[model].fits[when]
        if model == _SHOCK:
            from src.ui.solar.solar_measure_tools import shock_entry_parameters

            return shock_entry_parameters(entry)
        return GCSParameters(entry.lon_deg, entry.lat_deg, entry.tilt_deg,
                             entry.apex_height_rsun, entry.alpha_deg, entry.kappa)

    def _restore_recorded_model(self):
        model = self._editing
        when = self._recorded_time_for_current_frames(model)
        if when not in self._track(model).fits:
            return
        self.pause()
        params = self._recorded_parameters(when, model)
        if model == _GCS:
            self._on_parameters(params)
        else:
            self._on_shock_parameters(params)
        self._set_status("Restored recorded model. Refine again to calculate errors for the current points.")

    def _restore_fit_row(self, row, _column):
        times = sorted(self._track().fits)
        if 0 <= row < len(times):
            self.pause()
            self._seek_time(times[row])
            if self._shared_time == times[row]:
                self._restore_recorded_model()
            else:
                self._set_status("Reload the images for this recorded time before restoring its fit.")

    def _delete_recorded_fit(self):
        track = self._track()
        when = self._recorded_time_for_current_frames()
        if track.fits.pop(when, None) is not None:
            track.provenance.pop(when, None)
            self._forget_recorded_note(self._editing)
            self._refresh_fits()
            self._set_status("Recorded fit at the current time deleted.")

    def _jump_to_time(self):
        if not self._time_axis:
            return
        self.pause()
        dialog = QDialog(self)
        dialog.setWindowTitle("Go to UTC time")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose a UTC time; the timeline selects its nearest available frame."))
        editor = QDateTimeEdit()
        editor.setTimeSpec(Qt.UTC)
        editor.setDateTime(_qdatetime_utc(self._shared_time or self._time_axis[0]))
        editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss 'UTC'")
        editor.setCalendarPopup(True)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self._seek_time(editor.dateTime().toPython().replace(tzinfo=None))

    def _seek_time(self, when):
        if when.tzinfo is not None:
            when = when.astimezone(timezone.utc).replace(tzinfo=None)
        if self._time_axis:
            self.time_slider.setValue(min(range(len(self._time_axis)),
                                           key=lambda i: abs((self._time_axis[i] - when).total_seconds())))

    def _current_provenance(self, model=None):
        """What the current fit of ``model`` (the edited one by default) was made from."""
        clicks = self._track(model).clicks
        active = self._active_panels()
        frames = []
        for panel in self.panels:
            frame = panel.current_frame()
            if frame is None:
                continue
            stamp = panel.current_time()
            source = panel.source()
            reference = None
            mode = panel.rendered_mode()
            if mode != "raw":
                from src.backend.solar.solar_data_analysis import frame_observation_time
                reference = frame_observation_time(panel.difference_reference())
            frames.append({
                "panel": panel.label, "source_key": source.key if source else None,
                "instrument": panel._frame_name(frame), "observation_time_utc": stamp,
                "offset_seconds": (stamp - self._shared_time).total_seconds()
                    if stamp is not None and self._shared_time is not None else None,
                "included_in_fit": panel in active, "display_mode": mode,
                "difference_reference_utc": reference,
                "observer": asdict(panel.observer) if panel.observer is not None else None,
                "front_points_arcsec": list(clicks[panel.label]),
                "image_shape": list(frame.data.shape),
            })
        return {
            "target_time_utc": self._shared_time, "max_time_offset_seconds": self._sync_tolerance_seconds(),
            "frames": frames,
        }

    def _analysis_document(self):
        from src.backend.gcs.shock_model import shock_semi_axes
        from src.ui.solar.solar_measure_tools import shock_entry_axes

        def records(fits, provenance, axes=None):
            out = []
            for when, entry in sorted(fits.items()):
                parameters = entry._asdict()
                if axes is not None:
                    parameters.update(asdict(axes(entry)))
                out.append(dict(parameters=parameters, observations=provenance.get(when)))
            return out

        shock = self._tracks[_SHOCK]
        shock_params = self.shock_parameters()
        return _json_value({
            # Version 2 adds the "shock" section; every version-1 key is unchanged.
            "format": "e-callisto-gcs-analysis", "schema_version": 2,
            "exported_at_utc": datetime.now(timezone.utc),
            "model": "Graduated Cylindrical Shell",
            "coordinate_system": "Heliographic Stonyhurst at observation time; angles in degrees",
            "height_definition": "Leading-edge heliocentric distance in solar radii",
            "image_product": "Helioviewer JPEG2000 display images; not calibrated radiometry",
            "uncertainty_note": "Formal local geometric-fit errors exclude timing, front-selection and model uncertainty.",
            "event_range_utc": self.event_range(),
            "current_model": asdict(self.parameters()), "current_observations": self._current_provenance(),
            "recorded_fits": records(self._fits, self._fit_provenance),
            "archived_fits": records(getattr(self, "_archived_fits", {}),
                                     getattr(self, "_archived_provenance", {})),
            "kinematics_polynomial_order": self.tracking_panel.fit_order(),
            "shock": {
                "model": "Spheroid or ellipsoid shock surface",
                "parameter_convention": "PyThea (Kouloumvakos et al. 2022, doi:10.3389/fspas.2022.974137)",
                "height_definition": "Apex heliocentric distance in solar radii, rcenter + radaxis",
                "fitted_feature": "Projected outline of the shock surface",
                "current_model": {**asdict(shock_params), **asdict(shock_semi_axes(shock_params))},
                "current_observations": self._current_provenance(_SHOCK),
                "recorded_fits": records(shock.fits, shock.provenance, shock_entry_axes),
                "archived_fits": records(shock.archived_fits, shock.archived_provenance, shock_entry_axes),
            },
        })

    def _write_analysis(self, path):
        data = json.dumps(self._analysis_document(), indent=2, allow_nan=False).encode("utf-8")
        output = QSaveFile(str(path))
        if not output.open(QIODevice.WriteOnly):
            raise OSError(output.errorString())
        if output.write(data) != len(data):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())

    def _export_analysis(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export GCS analysis", "cme_gcs_analysis.json", "JSON (*.json)")
        if not path:
            return
        try:
            self._write_analysis(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self._set_status("Analysis exported with model parameters, observation times and clicked points.")

    def _show_fitting_guide(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("GCS fitting workflow")
        dialog.resize(680, 590)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml("""
            <h2>Fit the same CME feature in every view</h2>
            <ol>
            <li><b>Choose the event in UTC.</b> Load independent spacecraft views.
            LASCO C2 and C3 share an observer; opposite viewpoints also provide weak depth constraints.</li>
            <li><b>Check timing.</b> The timeline is the union of image timestamps. Each panel shows its
            nearest image and offset. Adjust Max time offset to suit the CME's evolution; a five-minute
            default is not an accuracy guarantee. Excluded panels do not constrain the fit, but the
            models are still drawn on them for comparison.</li>
            <li><b>Inspect the ejecta front.</b> Use raw, running or base difference and adjust contrast.
            The first loaded frame is running-differenced against the archive frame just before the
            range; it is raw only when no earlier frame exists. A base image containing the CME
            can obscure its front. GCS represents a flux rope, not the outer shock.</li>
            <li><b>Align one shell in every usable view.</b> Longitude and latitude are Stonyhurst;
            height is the leading edge measured from Sun centre, not altitude above the surface.
            Adjust direction, tilt, half angle and aspect ratio before fine-tuning height.</li>
            <li><b>Pick and refine.</b> Left-click the ejecta front in multiple views; right-click removes that
            panel's last point, and Undo point (Ctrl+Z) the last one clicked in any panel.
            Refine is a local nearest-mesh fit, so start close. It fits as many parameters as the
            points support: 2 points fit the height, 4 in two separated views add the direction,
            then tilt, α and κ (7 points for all six); the status bar names what was fitted and
            how many more points would free the next parameter. A small residual does not
            establish a unique solution. Points belong to individual observed frames.</li>
            <li><b>Record successive times.</b> Record fit saves the current model and observation
            provenance. Repeat along the event and choose a polynomial order supported by the number
            of distinct times. The speed is model-dependent radial speed. Repeated image combinations
            do not provide independent height measurements. Stepping, the time slider and playback all
            move the shells with the recorded fits: each recorded fit is drawn on its own images, fits
            are interpolated in time between recorded times (a display, not a fit) and held before the
            first and after the last. A model with nothing recorded keeps its sliders; commit before
            stepping away, or uncommitted changes give way to the recorded shell.</li>
            <li><b>Fit the shock too.</b> Choose <i>Edit: Shock</i> to fit the faint outer envelope
            the CME drives with a spheroid or ellipsoid, drawn alongside the GCS shell in its own colour.
            Parameters follow PyThea: height is the apex distance, κ = b/(height − 1 R☉) sets the lateral
            size, ε stretches the shock radially (positive) or flattens it (negative), and an ellipsoid
            adds α = b/c and a tilt about the radial axis. Click the shock front — the model's outline
            is what Refine pulls through the points. Front points, recorded fits and kinematics are kept
            separately per model. With fewer than three views ε and tilt are weakly constrained; prefer
            a spheroid.</li>
            <li><b>Export.</b> JSON includes every model parameter, frame times, offsets, observer geometry,
            points and archived fits for both models, with the shock's centre and semi-axes. CSV contains
            the recorded series of the model shown in Kinematics, and the height–time graph saves it with
            its fit. The snapshot saves the views, the movie every time step with its shells, and the PDF
            report the whole fit with its images, graphs and details. Figures are redrawn from the data
            in light mode (graphs in the OriginPro style) as PNG, PDF, EPS, SVG, TIFF or JPG.
            JSON is an analyzer export, not a PyThea session file.</li>
            </ol>
            <p><b>Scientific limits:</b> formal fit errors omit uncertainty from front selection,
            non-simultaneous observations, image preparation and the assumed shell or shock geometry.
            Helioviewer JP2 images support morphological fitting, not calibrated intensity measurements.
            Inspect sensitivity to timing and alternative plausible shells.</p>
            <p><b>Keys while an image has focus:</b> Space plays/pauses; arrows step; Home goes to the
            first frame; B shows or hides the image banners; 0 equalizes views; 1–3 focus A–C.
            Ctrl+Z undoes the last front point. Menus provide additional shortcuts.</p>
            <p>References: <a href="https://www.pythea.org/en/docs/geometrical_models.html">PyThea model definitions</a>
            · <a href="https://arxiv.org/abs/2302.00531">GCS reconstruction uncertainty</a>
            · <a href="https://doi.org/10.3847/1538-4357/ab15d7">Kouloumvakos et al. (2019), spheroid shock model</a>
            · <a href="https://doi.org/10.1088/0004-637X/794/2/148">Kwon et al. (2014), ellipsoid shock model</a></p>
        """)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
