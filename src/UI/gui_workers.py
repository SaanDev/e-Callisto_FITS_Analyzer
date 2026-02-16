"""
e-CALLISTO FITS Analyzer background workers.
"""

from __future__ import annotations

import os
import tempfile

import requests
from PySide6.QtCore import QObject, Signal, Slot

from src.Backend.fits_io import extract_ut_start_sec, load_callisto_fits
from src.Backend.update_checker import check_for_updates

class DownloaderImportWorker(QObject):
    progress_text = Signal(str)
    progress_range = Signal(int, int)
    progress_value = Signal(int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, urls):
        super().__init__()
        self.urls = list(urls or [])

    @Slot()
    def run(self):
        if not self.urls:
            self.failed.emit("No files were received from the downloader.")
            return

        local_files = []
        self.progress_text.emit("Downloading selected FITS files...")
        self.progress_range.emit(0, len(self.urls))
        self.progress_value.emit(0)

        try:
            for i, url in enumerate(self.urls, start=1):
                r = requests.get(url, timeout=25)
                r.raise_for_status()

                original_name = str(url).split("/")[-1] or f"import_{i}.fit"
                temp_dir = tempfile.gettempdir()
                local_path = os.path.join(temp_dir, original_name)

                with open(local_path, "wb") as f:
                    f.write(r.content)

                local_files.append(local_path)
                self.progress_value.emit(i)
        except Exception as e:
            self.failed.emit(f"Failed to download one or more FITS files:\n{e}")
            return

        if len(local_files) == 1:
            try:
                res = load_callisto_fits(local_files[0], memmap=False)
                payload = {
                    "kind": "single",
                    "filename": os.path.basename(local_files[0]),
                    "source_path": local_files[0],
                    "data": res.data,
                    "freqs": res.freqs,
                    "time": res.time,
                    "header0": res.header0.copy(),
                    "ut_start_sec": extract_ut_start_sec(res.header0),
                }
                self.finished.emit(payload)
            except Exception as e:
                self.failed.emit(f"Could not load FITS file:\n{e}")
            return

        from src.Backend.burst_processor import (
            are_time_combinable,
            are_frequency_combinable,
            combine_time,
            combine_frequency,
        )

        try:
            self.progress_text.emit("Checking file compatibility...")
            if are_time_combinable(local_files):
                self.progress_text.emit("Combining files (time mode)...")
                combined = combine_time(local_files)
                self.finished.emit({"kind": "combined", "combined": combined})
                return

            if are_frequency_combinable(local_files):
                self.progress_text.emit("Combining files (frequency mode)...")
                combined = combine_frequency(local_files)
                self.finished.emit({"kind": "combined", "combined": combined})
                return

            self.finished.emit({"kind": "invalid"})
        except Exception as e:
            self.failed.emit(f"An error occurred while combining files:\n{e}")


class UpdateCheckWorker(QObject):
    finished = Signal(object)

    def __init__(self, current_version: str):
        super().__init__()
        self.current_version = str(current_version or "").strip()

    @Slot()
    def run(self):
        result = check_for_updates(self.current_version)
        self.finished.emit(result)


class UpdateDownloadWorker(QObject):
    progress = Signal(int, int)  # downloaded_bytes, total_bytes (0 if unknown)
    finished = Signal(str)       # destination path
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, url: str, destination_path: str):
        super().__init__()
        self.url = str(url or "").strip()
        self.destination_path = str(destination_path or "").strip()
        self._cancel_requested = False

    @Slot()
    def request_cancel(self):
        self._cancel_requested = True

    @Slot()
    def run(self):
        if not self.url:
            self.failed.emit("Missing update download URL.")
            return
        if not self.destination_path:
            self.failed.emit("Missing destination path for update download.")
            return

        temp_path = f"{self.destination_path}.part"
        try:
            dest_dir = os.path.dirname(self.destination_path)
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)

            with requests.get(self.url, stream=True, timeout=30) as response:
                response.raise_for_status()
                total_raw = str(response.headers.get("Content-Length", "0") or "0").strip()
                try:
                    total_bytes = max(0, int(total_raw))
                except Exception:
                    total_bytes = 0

                downloaded = 0
                self.progress.emit(downloaded, total_bytes)

                with open(temp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=1024 * 128):
                        if self._cancel_requested:
                            try:
                                f.close()
                            except Exception:
                                pass
                            try:
                                if os.path.exists(temp_path):
                                    os.remove(temp_path)
                            except Exception:
                                pass
                            self.cancelled.emit()
                            return

                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        self.progress.emit(downloaded, total_bytes)

            if self._cancel_requested:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass
                self.cancelled.emit()
                return

            os.replace(temp_path, self.destination_path)
            self.finished.emit(self.destination_path)
        except Exception as e:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            self.failed.emit(str(e))
