"""Menus, scientific workflow guidance and reproducible GCS result exports."""

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

from src.Backend.gcs_model import GCSParameters, free_parameters
from src.UI.gcs_viewpoint_panel import _qdatetime_utc


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
        action(file_menu, "snapshot", "Save viewpoint &snapshot…", self._save_snapshot)
        file_menu.addSeparator()
        action(file_menu, "close", "&Close window", self.close, "Ctrl+W")

        event_menu = self.menuBar().addMenu("&Event")
        action(event_menu, "load", "&Load all channels", self._fetch_all, "Ctrl+L")
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
        for key, title, widget in (("wireframe", "Wireframe", self.wireframe_check),
                                   ("limb", "Solar limb", self.limb_check),
                                   ("axes", "Arcsecond axes", self.axes_check)):
            action(view_menu, key, title, widget.setChecked, checkable=True)
        action(view_menu, "zoom", "Reset image zoom", self._reset_image_zoom)
        action(view_menu, "layout_reset", "Fit layout to window", lambda: self._fit_splitter(force=True))

        fit_menu = self.menuBar().addMenu("&Fit")
        action(fit_menu, "pick", "Pick front points", self.pick_points_check.setChecked, checkable=True)
        action(fit_menu, "clear_points", "Clear current front points", self._clear_points)
        fit_menu.addSeparator()
        action(fit_menu, "refine", "&Refine current model", self._on_refine, "Ctrl+R")
        action(fit_menu, "commit", "&Record fit at current time", self._on_commit, "Ctrl+Return")
        action(fit_menu, "restore", "Restore recorded model at current time", self._restore_recorded_model)
        action(fit_menu, "delete", "Delete recorded fit at current time", self._delete_recorded_fit)
        fit_menu.addSeparator()
        action(fit_menu, "reset_model", "Reset model parameters", self._reset_model)
        action(fit_menu, "kinematics", "Fit recorded heights over time", self._on_fit_kinematics)
        action(fit_menu, "send", "Send parameters to analyzer", self._on_send)

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
        points = sum(view.n_clicks for view in self._viewpoints())
        enabled = {
            "load": not any(panel.is_fetching() for panel in self.panels),
            "export_csv": bool(self._fits), "snapshot": any(panel.frames for panel in self.panels),
            "jump": has_time, "refine": has_view and points >= len(free_parameters(self._viewpoints())) + 1,
            "commit": has_time and has_view, "restore": has_record, "delete": has_record,
            "kinematics": len(self._fits) >= self.tracking_panel.fit_order() + 1,
            "send": hasattr(getattr(self.parent(), "_measure", None), "set_gcs_parameters"),
        }
        for key in ("first", "previous", "next", "last", "play"):
            enabled[key] = len(self._time_axis) > 1
        for key, on in enabled.items():
            actions[key].setEnabled(on)
        selected = {
            "wireframe": self.wireframe_check.isChecked(), "limb": self.limb_check.isChecked(),
            "axes": self.axes_check.isChecked(), "pick": self.pick_points_check.isChecked(),
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

    def _reset_model(self):
        self.pause()
        self._params = None
        self._last_refinement = None
        self._sync_all()
        self._set_status("Model reset. Recorded fits are available in the kinematics table.")

    def _restore_recorded_model(self):
        entry = self._fits.get(self._recorded_time_for_current_frames())
        if entry is None:
            return
        self.pause()
        self._on_parameters(GCSParameters(entry.lon_deg, entry.lat_deg, entry.tilt_deg,
                                         entry.apex_height_rsun, entry.alpha_deg, entry.kappa))
        self._set_status("Restored recorded model. Refine again to calculate errors for the current points.")

    def _restore_fit_row(self, row, _column):
        times = sorted(self._fits)
        if 0 <= row < len(times):
            self.pause()
            self._seek_time(times[row])
            if self._shared_time == times[row]:
                self._restore_recorded_model()
            else:
                self._set_status("Reload the images for this recorded time before restoring its fit.")

    def _delete_recorded_fit(self):
        when = self._recorded_time_for_current_frames()
        if self._fits.pop(when, None) is not None:
            self._fit_provenance.pop(when, None)
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

    def _current_provenance(self):
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
            if mode != "raw" and panel._index > 0:
                from src.Backend.solar_data_analysis import frame_observation_time
                index = panel._index - 1 if mode == "running" else 0
                reference = frame_observation_time(panel.frames[index])
            frames.append({
                "panel": panel.label, "source_key": source.key if source else None,
                "instrument": panel._frame_name(frame), "observation_time_utc": stamp,
                "offset_seconds": (stamp - self._shared_time).total_seconds()
                    if stamp is not None and self._shared_time is not None else None,
                "included_in_fit": panel in active, "display_mode": mode,
                "difference_reference_utc": reference,
                "observer": asdict(panel.observer) if panel.observer is not None else None,
                "front_points_arcsec": list(self._clicks[panel.label]),
                "image_shape": list(frame.data.shape),
            })
        return {
            "target_time_utc": self._shared_time, "max_time_offset_seconds": self._sync_tolerance_seconds(),
            "frames": frames,
        }

    def _analysis_document(self):
        def records(fits, provenance):
            return [dict(parameters=entry._asdict(), observations=provenance.get(when))
                    for when, entry in sorted(fits.items())]

        return _json_value({
            "format": "e-callisto-gcs-analysis", "schema_version": 1,
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

    def _save_snapshot(self):
        self.pause()
        path, _ = QFileDialog.getSaveFileName(self, "Save GCS viewpoints", "cme_gcs_viewpoints.png", "PNG (*.png)")
        if path:
            if self.stage.grab().save(path, "PNG"):
                self._set_status("Viewpoint snapshot saved. Export analysis JSON for numerical provenance.")
            else:
                QMessageBox.warning(self, "Snapshot failed", "The image could not be written to that location.")

    def _show_fitting_guide(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("GCS fitting workflow")
        dialog.resize(680, 590)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml("""
            <h2>Fit the same CME ejecta in every view</h2>
            <ol>
            <li><b>Choose the event in UTC.</b> Load independent spacecraft views.
            LASCO C2 and C3 share an observer; opposite viewpoints also provide weak depth constraints.</li>
            <li><b>Check timing.</b> The timeline is the union of image timestamps. Each panel shows its
            nearest image and offset. Adjust Max time offset to suit the CME's evolution; a five-minute
            default is not an accuracy guarantee. Excluded panels do not constrain the fit.</li>
            <li><b>Inspect the ejecta front.</b> Use raw, running or base difference and adjust contrast.
            The first frame is raw when no earlier reference exists. A base image containing the CME
            can obscure its front. GCS represents a flux rope, not the outer shock.</li>
            <li><b>Align one shell in every usable view.</b> Longitude and latitude are Stonyhurst;
            height is the leading edge measured from Sun centre, not altitude above the surface.
            Adjust direction, tilt, half angle and aspect ratio before fine-tuning height.</li>
            <li><b>Pick and refine.</b> Left-click the ejecta front in multiple views, right-click to undo.
            Refine is a local nearest-mesh fit, so start close. A small residual does not establish a
            unique solution. Points belong to individual observed frames.</li>
            <li><b>Record successive times.</b> Record fit saves the current model and observation
            provenance. Repeat along the event and choose a polynomial order supported by the number
            of distinct times. The speed is model-dependent radial speed. Repeated image combinations
            do not provide independent height measurements.</li>
            <li><b>Export.</b> JSON includes all six parameters, frame times, offsets, observer geometry,
            points and archived fits. CSV contains the recorded numerical series; PNG saves the views.
            JSON is an analyzer export, not a PyThea session file.</li>
            </ol>
            <p><b>Scientific limits:</b> formal fit errors omit uncertainty from front selection,
            non-simultaneous observations, image preparation and the assumed shell geometry.
            Helioviewer JP2 images support morphological fitting, not calibrated intensity measurements.
            Inspect sensitivity to timing and alternative plausible shells.</p>
            <p><b>Keys while an image has focus:</b> Space plays/pauses; arrows step; Home goes to the
            first frame; 0 equalizes views; 1–3 focus A–C. Menus provide additional shortcuts.</p>
            <p>References: <a href="https://www.pythea.org/en/docs/geometrical_models.html">PyThea model definitions</a>
            · <a href="https://arxiv.org/abs/2302.00531">GCS reconstruction uncertainty</a></p>
        """)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
