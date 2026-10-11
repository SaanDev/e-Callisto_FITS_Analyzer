"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

One project for every window (src/ui/app/project_parts.py).

A project is made of *parts*: the radio analysis in the main window, the
Solar Image Analysis window, the GCS fitting window, and (from later phases)
the geomagnetic response, the event-context strip and the event-figure
composer. Each part knows how to capture itself into a :class:`PartPayload`
and how to restore itself from one; :class:`ProjectCoordinator`, owned by the
main window, gathers the parts into one ``.efaproj`` and hands a reopened file
back to them, opening the windows the file needs.

The radio part keeps its keys at the top level of ``meta.json``, exactly where
schema 1 wrote them, so the main window's existing capture and restore are
used unchanged. Every other part lives under its own key (``meta["solar"]``,
``meta["gcs"]`` …) and its own array-name prefix.

Dirtiness comes from every window: the solar and GCS windows emit
``projectModified`` and the coordinator forwards it to the main window, which
owns the single "unsaved changes" flag, the autosave timer and the save
prompts. Ctrl+S in any of the three windows saves the one project.
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QStandardPaths, Signal, Slot

from src.backend.session.project_session import (
    PART_KEYS,
    STORAGE_EMBED,
    STORAGE_LINK,
    ProjectPayload,
    ProjectSaveCancelled,
    estimate_files_size,
    project_parts,
    write_project,
)
from src.version import APP_VERSION


@dataclass
class PartPayload:
    """What one part contributes to a project file."""

    meta: dict[str, Any]
    arrays: dict[str, Any] = field(default_factory=dict)
    #: In-archive member name -> file on disk; only written in embed mode.
    files: dict[str, str] = field(default_factory=dict)


def projects_cache_root() -> Path:
    """``<app data>/sunpy_cache/projects``: where embedded files are unpacked.

    The same cache the Solar Image Analysis window downloads into, so clearing
    that cache clears unpacked projects too. ``ECALLISTO_PROJECTS_CACHE``
    overrides it (tests, portable installs).
    """
    override = os.environ.get("ECALLISTO_PROJECTS_CACHE", "").strip()
    if override:
        return Path(override)
    raw = ""
    try:
        raw = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
    except Exception:
        raw = ""
    base = Path(raw) if raw else Path.home() / ".local" / "share" / "e-callisto-fits-analyzer"
    return base / "sunpy_cache" / "projects"


def human_size(num_bytes: int) -> str:
    value = float(max(0, int(num_bytes)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


def _alive(widget: Any) -> bool:
    if widget is None:
        return False
    try:
        widget.windowTitle()
    except (RuntimeError, AttributeError):
        return False
    return True


# ---------------------------------------------------------------------- parts


class ProjectPart:
    """Base part: subclasses wire one window or controller into the project."""

    key = ""
    title = ""

    def __init__(self, coordinator: "ProjectCoordinator"):
        # Weak: the coordinator owns its parts, and a strong reference back
        # would tie the main window into a cycle only the garbage collector
        # can free — at interpreter exit, after the QApplication is gone.
        self._coordinator = weakref.ref(coordinator)

    @property
    def coordinator(self) -> "ProjectCoordinator":
        return self._coordinator()

    @property
    def main(self) -> Any:
        coordinator = self.coordinator
        return coordinator.main if coordinator is not None else None

    def has_content(self) -> bool:
        return False

    def has_images(self) -> bool:
        """True when Save As should offer link/embed for this part's images."""
        return False

    def capture(self, storage: str) -> PartPayload | None:
        return None

    def restore(self, payload: PartPayload, extracted_files: dict[str, str]) -> None:
        pass

    def clear(self) -> None:
        """The opened project does not hold this part: drop what is on screen."""

    def estimated_embed_bytes(self) -> int:
        return 0


class RadioPart(ProjectPart):
    """The main window's dynamic spectrum and every analysis built on it."""

    key = "radio"
    title = "Radio spectrum"

    def has_content(self) -> bool:
        return getattr(self.main, "raw_data", None) is not None

    def capture(self, storage: str) -> PartPayload | None:
        meta, arrays = self.main._capture_project_payload()
        return PartPayload(meta=dict(meta), arrays=dict(arrays))

    def restore(self, payload: PartPayload, extracted_files: dict[str, str]) -> None:
        self.main._apply_project_payload(payload.meta, payload.arrays)

    def clear(self) -> None:
        self.main._clear_radio_for_project()


class SolarPart(ProjectPart):
    """The Solar Image Analysis window."""

    key = "solar"
    title = "Solar Image Analysis"

    def window(self) -> Any:
        window = getattr(self.main, "_solar_data_analysis_window", None)
        return window if _alive(window) else None

    def has_content(self) -> bool:
        window = self.window()
        return bool(window is not None and window.project_has_content())

    def has_images(self) -> bool:
        return self.has_content()

    def capture(self, storage: str) -> PartPayload | None:
        window = self.window()
        if window is None:
            return None
        meta, files = window.project_part_payload(storage)
        return PartPayload(meta=meta, files=files if storage == STORAGE_EMBED else {})

    def restore(self, payload: PartPayload, extracted_files: dict[str, str]) -> None:
        window = self.main._ensure_solar_window()
        if window is None:
            return
        window.restore_project_part(payload.meta, extracted_files)

    def clear(self) -> None:
        window = self.window()
        if window is not None and window.project_has_content():
            window.clear_for_project()

    def estimated_embed_bytes(self) -> int:
        window = self.window()
        if window is None:
            return 0
        return estimate_files_size(window.project_part_payload(STORAGE_EMBED)[1])


class GCSPart(ProjectPart):
    """The three-viewpoint GCS fitting window."""

    key = "gcs"
    title = "GCS fitting"

    def window(self) -> Any:
        window = getattr(self.main, "_gcs_window", None)
        return window if _alive(window) else None

    def has_content(self) -> bool:
        window = self.window()
        return bool(window is not None and window.project_has_content())

    def has_images(self) -> bool:
        window = self.window()
        return bool(window is not None and window.project_has_images())

    def capture(self, storage: str) -> PartPayload | None:
        window = self.window()
        if window is None:
            return None
        meta, files = window.project_part_payload(storage)
        return PartPayload(meta=meta, files=files if storage == STORAGE_EMBED else {})

    def restore(self, payload: PartPayload, extracted_files: dict[str, str]) -> None:
        window = self.main._ensure_gcs_window()
        if window is None:
            return
        jp2 = {name: path for name, path in extracted_files.items() if name.startswith("gcs/")}
        window.restore_project_state(payload.meta, jp2)

    def clear(self) -> None:
        window = self.window()
        if window is not None and window.project_has_content():
            window.clear_project_state()

    def estimated_embed_bytes(self) -> int:
        window = self.window()
        if window is None:
            return 0
        return estimate_files_size(window.project_part_payload(STORAGE_EMBED)[1])


class ControllerPart(ProjectPart):
    """A part backed by a plain controller with ``project_*`` methods.

    The geomagnetic response, the event-context strip and the composer hold no
    images, so they only ever contribute meta and small arrays.
    """

    def __init__(self, coordinator: "ProjectCoordinator", key: str, attribute: str):
        super().__init__(coordinator)
        self.key = key
        #: The main window attribute holding the controller (a name, not a
        #: closure over the window, for the reason given in ProjectPart).
        self._attribute = str(attribute)

    def controller(self) -> Any:
        return getattr(self.main, self._attribute, None)

    def has_content(self) -> bool:
        controller = self.controller()
        return bool(controller is not None and controller.project_has_content())

    def capture(self, storage: str) -> PartPayload | None:
        controller = self.controller()
        if controller is None:
            return None
        meta, arrays = controller.project_capture()
        return PartPayload(meta=dict(meta or {}), arrays=dict(arrays or {}))

    def restore(self, payload: PartPayload, extracted_files: dict[str, str]) -> None:
        controller = self.controller()
        if controller is not None:
            controller.project_restore(payload.meta, payload.arrays)

    def clear(self) -> None:
        controller = self.controller()
        if controller is not None:
            controller.project_clear()


# ----------------------------------------------------------------- coordinator


class ProjectCoordinator(QObject):
    """Gathers every part into one project and restores them from one."""

    #: Some part changed; the main window marks the project dirty.
    projectModified = Signal()

    def __init__(self, main: Any):
        super().__init__(main)
        self._parts: dict[str, ProjectPart] = {}
        self._restoring = False
        self.project_id = uuid.uuid4().hex
        for part in (RadioPart(self), SolarPart(self), GCSPart(self)):
            self.register(part)

    @property
    def main(self) -> Any:
        """The owning main window: the Qt parent, so no Python reference cycle."""
        return self.parent()

    # ------------------------------------------------------------- parts
    def register(self, part: ProjectPart) -> None:
        self._parts[part.key] = part

    def part(self, key: str) -> ProjectPart | None:
        return self._parts.get(key)

    def parts(self) -> list[ProjectPart]:
        ordered = [self._parts[key] for key in PART_KEYS if key in self._parts]
        ordered += [part for key, part in self._parts.items() if key not in PART_KEYS]
        return ordered

    def has_content(self) -> bool:
        return any(self._safe(part.has_content) for part in self.parts())

    def has_images(self) -> bool:
        return any(self._safe(part.has_images) for part in self.parts())

    def content_parts(self) -> list[str]:
        return [part.key for part in self.parts() if self._safe(part.has_content)]

    def estimated_embed_bytes(self) -> int:
        total = 0
        for part in self.parts():
            if self._safe(part.has_content):
                try:
                    total += int(part.estimated_embed_bytes())
                except Exception:
                    pass
        return total

    @staticmethod
    def _safe(fn: Callable[[], Any]) -> bool:
        try:
            return bool(fn())
        except Exception:
            return False

    # --------------------------------------------------------- dirtiness
    @property
    def restoring(self) -> bool:
        return self._restoring

    @Slot()
    def notify_modified(self) -> None:
        if not self._restoring:
            self.projectModified.emit()

    def connect_window(self, window: Any) -> None:
        """Forward a window's ``projectModified`` into the one project flag."""
        signal = getattr(window, "projectModified", None)
        if signal is None:
            return
        try:
            signal.connect(self.notify_modified)
        except Exception:
            pass

    # ------------------------------------------------------------ saving
    def capture(self, storage: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
        """``(meta, arrays, files)`` for every part with content.

        ``files`` is empty in link mode: link projects reference images on disk.
        """
        storage = STORAGE_EMBED if storage == STORAGE_EMBED else STORAGE_LINK
        meta: dict[str, Any] = {}
        arrays: dict[str, Any] = {}
        files: dict[str, str] = {}
        flags = {key: False for key in PART_KEYS}
        for part in self.parts():
            if not self._safe(part.has_content):
                continue
            payload = part.capture(storage)
            if payload is None:
                continue
            flags[part.key] = True
            if part.key == "radio":
                meta.update(payload.meta)
            else:
                meta[part.key] = payload.meta
            arrays.update(payload.arrays or {})
            if storage == STORAGE_EMBED:
                files.update(payload.files or {})
        meta["parts"] = flags
        meta["storage"] = storage
        meta["project_id"] = self.project_id
        meta["app_version"] = APP_VERSION
        return meta, arrays, files

    def new_project(self) -> None:
        """A fresh identity: a Save As copy must never share an unpack folder."""
        self.project_id = uuid.uuid4().hex

    # ----------------------------------------------------------- opening
    def extract_dir_for(self, meta: dict[str, Any]) -> Path:
        project_id = "".join(ch for ch in str(meta.get("project_id") or "") if ch.isalnum()) or "legacy"
        target = projects_cache_root() / project_id
        # Start clean so a reopened project never mixes in stale files.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return target

    def apply(self, payload: ProjectPayload) -> list[str]:
        """Restore every part the file holds and clear the ones it does not.

        Returns the restored part keys. Windows the file needs are created and
        shown; a part's own restore may finish asynchronously (frames load on a
        worker), which is why each window keeps its pending restore itself.
        """
        meta = dict(payload.meta or {})
        flags = project_parts(meta)
        self.project_id = str(meta.get("project_id") or uuid.uuid4().hex)
        restored: list[str] = []
        self._restoring = True
        try:
            for part in self.parts():
                wanted = flags.get(part.key, False)
                if part.key == "radio":
                    # The radio keys sit at the top level; a file without radio
                    # data still resets the main window to an empty spectrum.
                    if wanted:
                        part.restore(PartPayload(meta=meta, arrays=dict(payload.arrays)), dict(payload.files))
                        restored.append(part.key)
                    else:
                        part.clear()
                    continue
                section = meta.get(part.key)
                if wanted and isinstance(section, dict):
                    part.restore(
                        PartPayload(meta=section, arrays=dict(payload.arrays)), dict(payload.files)
                    )
                    restored.append(part.key)
                else:
                    part.clear()
        finally:
            self._restoring = False
        return restored


# --------------------------------------------------------------- save worker


class ProjectSaveWorker(QObject):
    """Writes an embedded project off the GUI thread.

    State is captured on the GUI thread first; only the zip write — streaming
    possibly hundreds of megabytes of FITS — runs here.
    """

    progress = Signal(int, int, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, path: str, meta: dict, arrays: dict, files: dict):
        super().__init__()
        self.path = str(path)
        self.meta = meta
        self.arrays = arrays
        self.files = files
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            write_project(
                self.path,
                meta=self.meta,
                arrays=self.arrays,
                files=self.files,
                progress_cb=lambda done, total, text: self.progress.emit(
                    int(min(done, 2**31 - 1) * 1000 // max(total, 1)) if total else 0, 1000, str(text)
                ),
                cancel_cb=self._cancel.is_set,
            )
        except ProjectSaveCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # reported, never raised into Qt's event loop
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        self.finished.emit(self.path)


class ProjectSaveListener(QObject):
    """Receives a :class:`ProjectSaveWorker`'s signals on the GUI thread.

    A QObject created on the GUI thread makes every connection queued, so the
    progress dialog and the waiting event loop are only touched from there —
    a plain function slot could be called on the worker thread instead.
    """

    def __init__(self, dialog: Any, loop: Any):
        super().__init__()
        self.dialog = dialog
        self.loop = loop
        self.ok = False
        self.error = ""
        self.cancelled = False

    def done(self) -> bool:
        return self.ok or bool(self.error) or self.cancelled

    @Slot(int, int, str)
    def on_progress(self, value: int, maximum: int, text: str) -> None:
        self.dialog.setMaximum(int(maximum))
        self.dialog.setValue(int(value))
        if text:
            self.dialog.setLabelText(f"Embedding images in the project...\n{text}")

    @Slot(str)
    def on_finished(self, _path: str) -> None:
        self.ok = True
        self.loop.quit()

    @Slot(str)
    def on_failed(self, message: str) -> None:
        self.error = str(message) or "error"
        self.loop.quit()

    @Slot()
    def on_cancelled(self) -> None:
        self.cancelled = True
        self.loop.quit()


def describe_storage_choice(link_bytes_hint: int, embed_bytes: int) -> str:
    return (
        "This project contains solar or GCS images.\n\n"
        f"Link data (small): the project references the image files on this computer "
        f"(a few hundred KB). If they are moved or the cache is cleared, they are fetched again.\n\n"
        f"Embed data (portable): the images are stored inside the project "
        f"(about {human_size(embed_bytes)}), so it opens anywhere, even offline."
    )


def file_is_legacy_solar_session(path: str) -> bool:
    return str(path or "").lower().endswith(".ecsolar") and os.path.isfile(str(path))
