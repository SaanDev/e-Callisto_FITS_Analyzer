"""
e-CALLISTO FITS Analyzer background workers.
"""

from __future__ import annotations

import os
import tempfile

import numpy as np
import requests
from PySide6.QtCore import QObject, Signal, Slot

from src.Backend.batch_processing import (
    build_unique_output_png_path,
    subtract_background,
    list_fit_files,
    save_background_subtracted_png,
)
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


class BatchProcessWorker(QObject):
    progress_text = Signal(str)
    progress_range = Signal(int, int)
    progress_value = Signal(int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        input_dir: str,
        output_dir: str,
        cmap_name: str = "Custom",
        output_mode: str = "background_subtracted",
        background_method: str = "mean",
        cold_digits: float = 0.0,
    ):
        super().__init__()
        self.input_dir = str(input_dir or "").strip()
        self.output_dir = str(output_dir or "").strip()
        self.cmap_name = str(cmap_name or "").strip() or "Custom"
        mode = str(output_mode or "").strip().lower()
        self.output_mode = "raw" if mode == "raw" else "background_subtracted"
        method = str(background_method or "").strip().lower()
        self.background_method = "median" if method == "median" else "mean"
        try:
            self.cold_digits = float(cold_digits)
        except Exception:
            self.cold_digits = 0.0
        self._cancel_requested = False

    @Slot()
    def request_cancel(self):
        self._cancel_requested = True

    @Slot()
    def run(self):
        if not self.input_dir:
            self.failed.emit("Input folder is required.")
            return
        if not self.output_dir:
            self.failed.emit("Output folder is required.")
            return
        if not os.path.isdir(self.input_dir):
            self.failed.emit("Input folder does not exist.")
            return

        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except Exception as e:
            self.failed.emit(f"Could not create output folder:\n{e}")
            return

        try:
            files = list_fit_files(self.input_dir, recursive=False)
        except Exception as e:
            self.failed.emit(f"Could not list FIT files:\n{e}")
            return

        total = len(files)
        self.progress_range.emit(0, max(0, total))
        self.progress_value.emit(0)

        if total == 0:
            self.progress_text.emit("No FIT files found in selected input folder.")
            self.finished.emit(
                {
                    "kind": "batch",
                    "input_dir": self.input_dir,
                    "output_dir": self.output_dir,
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "cancelled": False,
                    "output_mode": self.output_mode,
                    "background_method": self.background_method,
                    "cmap_name": self.cmap_name,
                    "cold_digits": self.cold_digits,
                    "results": [],
                    "errors": [],
                }
            )
            return

        results: list[dict] = []
        errors: list[dict] = []
        processed = 0

        for idx, file_path in enumerate(files, start=1):
            if self._cancel_requested:
                break

            processed = idx
            base = os.path.basename(file_path)
            self.progress_text.emit(f"Processing {base} ({idx}/{total})...")

            try:
                res = load_callisto_fits(file_path, memmap=False)
                if self.output_mode == "raw":
                    out_data = np.asarray(res.data, dtype=np.float32)
                    title_suffix = "Raw"
                else:
                    out_data = subtract_background(res.data, method=self.background_method)
                    method_label = "Mean" if self.background_method == "mean" else "Median"
                    title_suffix = f"Background Subtracted ({method_label})"

                out_path = build_unique_output_png_path(self.output_dir, base)

                lower = base.lower()
                if lower.endswith(".fit.gz"):
                    title_stem = base[:-7]
                elif lower.endswith(".fits.gz"):
                    title_stem = base[:-8]
                elif lower.endswith(".fit"):
                    title_stem = base[:-4]
                elif lower.endswith(".fits"):
                    title_stem = base[:-5]
                else:
                    title_stem = os.path.splitext(base)[0]

                title = f"{title_stem}-{title_suffix}"
                ut_start_sec = extract_ut_start_sec(res.header0)
                if ut_start_sec is None:
                    ut_start_sec = 0.0

                save_background_subtracted_png(
                    out_data,
                    res.freqs,
                    res.time,
                    out_path,
                    title,
                    self.cmap_name,
                    ut_start_sec=ut_start_sec,
                    cold_digits=self.cold_digits,
                )
                results.append({"input_path": file_path, "output_path": out_path})
            except Exception as e:
                errors.append({"input_path": file_path, "error": str(e)})

            self.progress_value.emit(idx)

        cancelled = self._cancel_requested and processed < total
        if cancelled:
            self.progress_text.emit("Batch processing cancelled.")
        else:
            self.progress_text.emit("Batch processing complete.")

        self.finished.emit(
            {
                "kind": "batch",
                "input_dir": self.input_dir,
                "output_dir": self.output_dir,
                "total": total,
                "processed": processed,
                "succeeded": len(results),
                "failed": len(errors),
                "cancelled": cancelled,
                "output_mode": self.output_mode,
                "background_method": self.background_method,
                "cmap_name": self.cmap_name,
                "cold_digits": self.cold_digits,
                "results": results,
                "errors": errors,
            }
        )
