"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The GCS window as a part of the one project (src/ui/gcs/gcs_window_project.py).

``project_state`` snapshots everything needed to rebuild the window and
``restore_project_state`` rebuilds it, in the only order that works:

1. the embedded JP2s go into the JP2 disk cache under their normal names, and
   each panel's frame listing is seeded, so the reload never needs the network;
2. the event range and each channel's source and range are set, then only the
   channels that had frames are loaded;
3. once every load has finished, the display settings are applied — loading
   resets each panel to gray, running difference — then the front points are
   mapped back onto the new frame objects by observation time, and the models
   and recorded fits are put back.

Front points are keyed by ``id(frame)`` in the window, which a reload changes;
on disk they are keyed by (panel, frame observation time).
"""

from __future__ import annotations

import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QTimer

from src.backend.gcs.gcs_model import GCSParameters
from src.backend.gcs.gcs_session import deserialize_gcs_state, match_frame_time, serialize_gcs_state
from src.backend.session.project_session import STORAGE_EMBED, safe_member_name
from src.backend.solar import helioviewer_jp2 as hvjp2
from src.backend.solar.solar_data_analysis import frame_observation_time

#: Model keys, as in ``src.ui.gcs.gcs_fitting_window`` (not imported: it imports this module).
_GCS = "gcs"
_SHOCK = "shock"


class GCSWindowProject:
    """Window mixin: capture into and restore from the project's ``gcs`` part."""

    # ------------------------------------------------------------ dirtiness
    def _notify_project_modified(self) -> None:
        if getattr(self, "_restoring_project", False) or getattr(self, "_pending_project_restore", None):
            return
        signal = getattr(self, "projectModified", None)
        if signal is not None:
            signal.emit()

    # -------------------------------------------------------------- content
    def project_has_images(self) -> bool:
        return any(panel.frames for panel in self.panels)

    def project_has_content(self) -> bool:
        if self.project_has_images():
            return True
        for track in self._tracks.values():
            if track.fits or track.archived_fits or any(track.clicks.values()) or track.frame_points:
                return True
        return False

    def thumbnail_near(self, when: datetime, *, tolerance_s: float = 1800.0) -> Any:
        """The loaded viewpoint frame nearest ``when``, as displayed (mode, contrast, colormap)."""
        from src.backend.common.event_figure import ThumbnailImage

        best = None
        best_gap = None
        for panel in self.panels:
            for index, frame in enumerate(panel.frames):
                stamp = frame_observation_time(frame)
                if stamp is None:
                    continue
                gap = abs((stamp - when.replace(tzinfo=None)).total_seconds())
                if gap <= tolerance_s and (best_gap is None or gap < best_gap):
                    best, best_gap = (panel, index, frame, stamp), gap
        if best is None:
            return None
        panel, index, frame, stamp = best
        try:
            data = np.asarray(panel.display_array(index), dtype=float)
            levels = panel._current_levels(index, data, panel.difference_mode(), panel.rendered_mode())
        except Exception:
            return None
        import matplotlib

        name = panel.colormap_combo.currentText()
        try:
            import sunpy.visualization.colormaps  # noqa: F401  (registers soholasco2 & co)
        except Exception:
            pass
        try:
            cmap = matplotlib.colormaps[name]
        except KeyError:
            cmap = "gray"
        valid = levels if levels and None not in levels else None
        return ThumbnailImage(stamp, panel._frame_name(frame), data, "loaded", cmap=cmap, levels=valid)

    # -------------------------------------------------------------- capture
    def _frame_times_by_id(self) -> dict[tuple[str, int], datetime]:
        out: dict[tuple[str, int], datetime] = {}
        for panel in self.panels:
            for frame in panel.frames:
                when = frame_observation_time(frame)
                if when is not None:
                    out[(panel.label, id(frame))] = when
        return out

    def project_state(self) -> dict[str, Any]:
        """A plain snapshot of the window (datetimes, tuples, dicts) — no ids."""
        # The points on the displayed frames live in ``clicks``; file them under
        # their frames first, exactly as stepping to another time would.
        for panel in self.panels:
            current = self._displayed_frames.get(panel.label)
            if current is None:
                continue
            for track in self._tracks.values():
                points = list(track.clicks.get(panel.label, []))
                if points:
                    track.frame_points[(panel.label, current)] = points
                else:
                    track.frame_points.pop((panel.label, current), None)
        times = self._frame_times_by_id()

        tracks: dict[str, Any] = {}
        for key, track in self._tracks.items():
            frame_points = [
                {"panel": label, "frame_time": times[(label, frame_id)], "points": list(points)}
                for (label, frame_id), points in track.frame_points.items()
                if points and (label, frame_id) in times
            ]
            click_order = [
                {
                    "panel": label,
                    "frame_time": times.get((label, frame_id)) if frame_id is not None else None,
                    "point": point,
                }
                for label, frame_id, point in track.click_order
            ]
            tracks[key] = {
                "params": asdict(track.params) if track.params is not None else None,
                "clicks": {label: list(points) for label, points in track.clicks.items()},
                "frame_points": frame_points,
                "click_order": click_order,
                "fits": [entry._asdict() for _when, entry in sorted(track.fits.items())],
                "provenance": dict(track.provenance),
                "archived_fits": [entry._asdict() for _when, entry in sorted(track.archived_fits.items())],
                "archived_provenance": dict(track.archived_provenance),
            }

        panels = []
        for panel in self.panels:
            start, end = panel.date_range()
            panels.append(
                {
                    "label": panel.label,
                    "source_key": panel.source_combo.currentData(),
                    "start": start,
                    "end": end,
                    "colormap": panel.colormap_combo.currentText(),
                    "low": float(panel.low_slider.value()),
                    "high": float(panel.high_slider.value()),
                    "frame_times": panel.times(),
                    "listing": panel.listing_times(),
                }
            )
        stage_mode = self.stage.mode()
        return {
            "event_range": self.event_range(),
            "shared_time": self._shared_time,
            "requested_time": self._requested_time,
            "editing": self._editing,
            "max_frames": int(self.max_frames_spin.value()),
            "panels": panels,
            "display": {
                "difference_mode": self._difference_mode(),
                "layout": stage_mode,
                "focus": int(self.stage.focus_index()),
                "limb": bool(self.limb_check.isChecked()),
                "axes": bool(self.axes_check.isChecked()),
                "banner": bool(self.banner_check.isChecked()),
                "wireframe": bool(self.wireframe_check.isChecked()),
                "shock": bool(self.shock_check.isChecked()),
                "pick_points": bool(self.pick_points_check.isChecked()),
                "width": float(self.width_slider.value()),
                "opacity": float(self.opacity_slider.value()),
                "density": float(self.density_slider.value()),
                "wireframe_colour": tuple(self._wireframe_colour),
                "shock_colour": tuple(self._shock_colour),
                "fps": int(self.fps_spin.value()),
                "sync_tolerance_min": float(self.sync_tolerance_spin.value()),
                "fit_order": int(self.tracking_panel.fit_order()),
            },
            "tracks": tracks,
        }

    def project_part_payload(self, storage: str) -> tuple[dict[str, Any], dict[str, str]]:
        """``(meta, files)`` for the project's ``gcs`` part.

        Embed mode adds every cached JP2 behind the loaded frames as
        ``gcs/jp2/<panel>/hv<id>_<time>.jp2``.
        """
        state = self.project_state()
        files: dict[str, str] = {}
        if storage == STORAGE_EMBED:
            for panel, entry in zip(self.panels, state["panels"]):
                members = []
                for path in panel.cached_jp2_paths():
                    arcname = f"gcs/jp2/{safe_member_name(panel.label, 'X')}/{safe_member_name(path.name, 'frame.jp2')}"
                    files[arcname] = str(path)
                    members.append(arcname)
                entry["jp2_members"] = members
        meta = serialize_gcs_state(state)
        meta["storage"] = str(storage)
        return meta, files

    # -------------------------------------------------------------- restore
    def clear_project_state(self) -> None:
        """Empty the window: no frames, no points, no fits, default models."""
        self.pause()
        self._pending_project_restore = None
        was = getattr(self, "_restoring_project", False)
        self._restoring_project = True
        try:
            for panel in self.panels:
                panel.cancel_fetch(discard_result=True)
                if panel.frames:
                    panel.set_frames([])
                panel.picked_times = ()
                panel.previous_listing_time = None
            for track in self._tracks.values():
                track.params = None
                track.clicks = {label: [] for label in track.clicks}
                track.frame_points = {}
                track.click_order = []
                track.fits = {}
                track.provenance = {}
                track.archived_fits = {}
                track.archived_provenance = {}
                track.last_refinement = None
            self._forget_recorded_note()
            self._rebuild_time_axis()
            self._refresh_fits()
            self._sync_all()
        finally:
            self._restoring_project = was

    def _install_embedded_jp2s(self, jp2_files: dict[str, str]) -> int:
        """Copy embedded JP2s into the disk cache under their normal names."""
        installed = 0
        if not jp2_files or not self.panels:
            return 0
        directory = Path(self.panels[0].jp2_cache_directory())
        for name, path in jp2_files.items():
            leaf = Path(str(name)).name
            if not leaf.lower().endswith(".jp2"):
                continue
            target = directory / leaf
            try:
                if not target.is_file() or target.stat().st_size != Path(path).stat().st_size:
                    directory.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                installed += 1
            except OSError:
                continue
        return installed

    def restore_project_state(self, meta: dict[str, Any], jp2_files: dict[str, str] | None = None) -> None:
        """Rebuild the window from a project's ``gcs`` part (see the module docstring)."""
        state = deserialize_gcs_state(meta)
        if not state:
            return
        self.clear_project_state()
        self._restoring_project = True
        try:
            self._install_embedded_jp2s(dict(jp2_files or {}))
            if state.get("max_frames"):
                self.max_frames_spin.setValue(int(state["max_frames"]))
            display = state.get("display") or {}
            if display.get("sync_tolerance_min") is not None:
                self.sync_tolerance_spin.setValue(float(display["sync_tolerance_min"]))

            event = state.get("event_range")
            if event is not None:
                from src.ui.gcs.gcs_viewpoint_panel import _qdatetime_utc

                for editor, value in ((self.event_start_edit, event[0]), (self.event_end_edit, event[1])):
                    was = editor.blockSignals(True)
                    editor.setDateTime(_qdatetime_utc(value))
                    editor.blockSignals(was)
                # Keep fits: the restored ones are put back below anyway.
                self._last_event_range = None
                self._apply_event_range_to_channels()

            waiting: list[Any] = []
            by_label = {panel.label: panel for panel in self.panels}
            for saved in state.get("panels") or []:
                panel = by_label.get(saved["label"])
                if panel is None:
                    continue
                if saved.get("source_key"):
                    panel.select_source(saved["source_key"])
                if saved.get("start") is not None and saved.get("end") is not None:
                    panel.set_date_range(saved["start"], saved["end"])
                source = panel.source()
                listing = saved.get("listing") or []
                if source is not None and listing and saved.get("start") and saved.get("end"):
                    start, end = panel.date_range()
                    hvjp2.seed_listing_cache(source, start, end, listing)
                if saved.get("frame_times"):
                    waiting.append(panel)

            requested = state.get("shared_time") or state.get("requested_time")
            if requested is not None:
                self._requested_time = requested
            self._pending_project_restore = {"state": state}
        finally:
            self._restoring_project = False

        started = [panel for panel in waiting if panel.start_fetch()]
        self._sync_busy()
        if started:
            self._set_status(f"Reopening the project: loading {len(started)} channel(s)…")
        else:
            QTimer.singleShot(0, self._check_project_restore_done)

    def _check_project_restore_done(self) -> None:
        """Finish a pending restore once no channel is loading any more."""
        if not getattr(self, "_pending_project_restore", None):
            return
        if any(panel.is_fetching() for panel in self.panels):
            return
        pending, self._pending_project_restore = self._pending_project_restore, None
        self._restoring_project = True
        try:
            self._finish_project_restore(pending["state"])
        finally:
            self._restoring_project = False

    def _finish_project_restore(self, state: dict[str, Any]) -> None:
        from src.ui.solar.solar_measure_tools import GCSFitEntry, ShockFitEntry

        display = state.get("display") or {}
        layout = display.get("layout")
        if layout:
            self.set_layout_mode(str(layout), display.get("focus"))
        for check, key in (
            (self.limb_check, "limb"),
            (self.axes_check, "axes"),
            (self.banner_check, "banner"),
            (self.wireframe_check, "wireframe"),
            (self.shock_check, "shock"),
            (self.pick_points_check, "pick_points"),
        ):
            if key in display:
                check.setChecked(bool(display[key]))
        for slider, key in ((self.width_slider, "width"), (self.opacity_slider, "opacity"), (self.density_slider, "density")):
            if display.get(key) is not None:
                slider.setValue(float(display[key]))
        if display.get("wireframe_colour"):
            self._wireframe_colour = tuple(display["wireframe_colour"])
        if display.get("shock_colour"):
            self._shock_colour = tuple(display["shock_colour"])
        if display.get("fps"):
            self.fps_spin.setValue(int(display["fps"]))
        if display.get("fit_order"):
            self.tracking_panel.set_fit_order(int(display["fit_order"]))

        # Loading reset every panel to gray / running difference; put back what was saved.
        mode = display.get("difference_mode")
        if mode:
            self._on_difference_mode(str(mode))
        by_label = {panel.label: panel for panel in self.panels}
        for saved in state.get("panels") or []:
            panel = by_label.get(saved["label"])
            if panel is None:
                continue
            if saved.get("colormap"):
                panel.colormap_combo.setCurrentText(saved["colormap"])
            for slider, key in ((panel.low_slider, "low"), (panel.high_slider, "high")):
                if saved.get(key) is not None:
                    slider.setValue(float(saved[key]))

        # Front points: (panel, time) -> (panel, id of the reloaded frame).
        ids_by_label: dict[str, dict[datetime, int]] = {}
        for panel in self.panels:
            ids_by_label[panel.label] = {
                when: id(frame) for frame in panel.frames if (when := frame_observation_time(frame)) is not None
            }
        missing = 0
        for key, saved_track in (state.get("tracks") or {}).items():
            track = self._tracks.get(key)
            if track is None:
                continue
            frame_points: dict[tuple[str, int], list[tuple[float, float]]] = {}
            for row in saved_track.get("frame_points") or []:
                ids = ids_by_label.get(row["panel"], {})
                when = match_frame_time(row["frame_time"], ids.keys())
                if when is None:
                    missing += 1
                    continue
                frame_points[(row["panel"], ids[when])] = list(row["points"])
            track.frame_points = frame_points
            order = []
            for row in saved_track.get("click_order") or []:
                ids = ids_by_label.get(row["panel"], {})
                when = match_frame_time(row.get("frame_time"), ids.keys())
                if when is not None:
                    order.append((row["panel"], ids[when], tuple(row["point"])))
            track.click_order = order
            track.clicks = {label: [] for label in track.clicks}

            entry_type = GCSFitEntry if key == _GCS else ShockFitEntry

            def entries(rows: list[dict[str, Any]]) -> dict[datetime, Any]:
                out = {}
                for row in rows:
                    values = {field: row.get(field, float("nan")) for field in entry_type._fields}
                    if key == _SHOCK and not values.get("model"):
                        values["model"] = "spheroid"
                    out[row["when"]] = entry_type(**values)
                return out

            track.fits = entries(saved_track.get("fits") or [])
            track.provenance = dict(saved_track.get("provenance") or {})
            track.archived_fits = entries(saved_track.get("archived_fits") or [])
            track.archived_provenance = dict(saved_track.get("archived_provenance") or {})
            track.last_refinement = None

        # Re-snap to the saved time with no displayed frame, so every panel
        # picks its points up from the remapped store.
        for label in self._displayed_frames:
            self._displayed_frames[label] = None
        self._shared_time = None
        self._rebuild_time_axis()

        # The working models last: following the recorded fits on the seek
        # above must not overwrite what was on the sliders when it was saved.
        for key, saved_track in (state.get("tracks") or {}).items():
            track = self._tracks.get(key)
            params = saved_track.get("params")
            if track is None or not params:
                continue
            try:
                if key == _GCS:
                    track.params = GCSParameters(**{name: float(params[name]) for name in (
                        "lon_deg", "lat_deg", "tilt_deg", "height_rsun", "alpha_deg", "kappa")})
                else:
                    from src.backend.gcs.shock_model import ShockParameters

                    track.params = ShockParameters(**params)
            except (KeyError, TypeError, ValueError):
                continue
        self._forget_recorded_note()
        editing = state.get("editing") or _GCS
        self.set_editing_model(editing)
        if editing == _GCS and display.get("shock") is False:
            self.shock_check.setChecked(False)
        self._refresh_fits()
        self._sync_all()
        loaded = sum(1 for panel in self.panels if panel.frames)
        fits = sum(len(track.fits) for track in self._tracks.values())
        message = f"Project reopened: {loaded} channel(s) loaded, {fits} recorded fit(s)."
        if missing:
            message += f" {missing} set(s) of front points had no matching frame and were dropped."
        self._set_status(message)
