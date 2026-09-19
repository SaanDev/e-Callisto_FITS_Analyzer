"""Prepare Burst List previews through the downloader's shared FITS cache."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

from src.backend.radio import callisto_cache
from src.backend.radio.callisto_archive import build_archive_session

if TYPE_CHECKING:
    from src.ui.downloads.callisto_downloader import CallistoEventCandidate


class _PreviewCancelled(Exception):
    """Stop preparation without turning user cancellation into an error."""


class BurstPreviewWorker(QObject):
    """Download selected files and build the existing preview panel payloads."""

    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(self, candidates: list[CallistoEventCandidate]):
        super().__init__()
        self.candidates = list(candidates or [])
        self._cancel_requested = Event()

    @Slot()
    def request_cancel(self):
        # The GUI calls this directly while run() occupies the worker thread.
        self._cancel_requested.set()

    def _check_cancelled(self):
        if self._cancel_requested.is_set():
            raise _PreviewCancelled()

    @Slot()
    def run(self):
        # The downloader imports the Burst List tab, so defer this import to
        # avoid a module cycle while retaining its existing preview behavior.
        from src.ui.downloads.callisto_downloader import build_preview_panels

        local_files: list[tuple[str, str]] = []
        errors: list[str] = []
        cached_count = 0
        count = len(self.candidates)
        total = max(1, count * 1000)

        try:
            self._check_cancelled()
            if not count:
                raise ValueError("Select at least one FITS file to preview.")
            self.progress.emit(0, total, "Preparing selected FITS files...")
            with build_archive_session() as session:
                for index, candidate in enumerate(self.candidates):
                    self._check_cancelled()
                    self.progress.emit(
                        index * 900, total,
                        f"Preparing {candidate.filename} ({index + 1}/{count})...",
                    )

                    def report_progress(fraction, message):
                        self._check_cancelled()
                        fraction = max(0.0, min(1.0, float(fraction)))
                        # Reserve the final 10% for reading and combining FITS.
                        self.progress.emit(
                            index * 900 + int(fraction * 900), total,
                            f"{message} ({index + 1}/{count})",
                        )

                    try:
                        path, was_cached = callisto_cache.fetch_cached(
                            candidate.url, candidate.filename, session=session,
                            progress_cb=report_progress,
                        )
                        self._check_cancelled()
                        cached_count += int(bool(was_cached))
                        local_files.append((str(path), candidate.filename))
                    except _PreviewCancelled:
                        raise
                    except Exception as exc:
                        self._check_cancelled()
                        errors.append(f"{candidate.filename}: {exc}")
                    self.progress.emit(
                        (index + 1) * 900, total,
                        f"Prepared {index + 1} of {count} FITS files.",
                    )

            self._check_cancelled()
            if not local_files:
                raise ValueError(
                    "None of the selected files could be prepared.\n\n"
                    + "\n".join(errors[:8])
                )
            self.progress.emit(count * 900, total, "Building preview...")
            self._check_cancelled()
            panels, panel_errors = build_preview_panels(local_files)
            self._check_cancelled()
            errors.extend(panel_errors)
            if not panels:
                raise ValueError("No preview could be produced.\n\n" + "\n".join(errors[:8]))

            try:
                callisto_cache.enforce_cache_limit()
            except Exception:
                pass
            self._check_cancelled()
            self.progress.emit(total, total, "Preview ready.")
            self.finished.emit({
                "panels": panels,
                "errors": errors,
                "cached_count": cached_count,
                "total": count,
            })
        except _PreviewCancelled:
            self.finished.emit({"cancelled": True})
        except Exception as exc:
            if self._cancel_requested.is_set():
                self.finished.emit({"cancelled": True})
            else:
                self.finished.emit({"error": str(exc)})
