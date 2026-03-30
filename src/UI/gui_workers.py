"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import requests
from PySide6.QtCore import QCoreApplication, QObject, QStandardPaths, Signal, Slot

from src.Backend.batch_processing import (
    build_unique_output_png_path,
    subtract_background,
    list_fit_files,
    save_background_subtracted_png,
)
from src.Backend.fits_io import extract_ut_start_sec, load_callisto_fits
from src.Backend.goes_overlay import goes_overlay_payload_from_dict
from src.Backend.update_checker import check_for_updates


def _default_sunpy_cache_dir() -> Path:
    app_data = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
    if not app_data:
        app_data = str(Path.home() / ".local" / "share" / "e-callisto-fits-analyzer")
    out = Path(app_data) / "sunpy_cache"
    out.mkdir(parents=True, exist_ok=True)
    return out


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


class GoesOverlayLoadWorker(QObject):
    progress = Signal(object, object)
    finished = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str)

    def __init__(
        self,
        request_key: str,
        *,
        start_utc,
        end_utc,
        base_utc,
        satellite_numbers=None,
        cache_dir: str | Path | None = None,
    ):
        super().__init__()
        self.request_key = str(request_key or "").strip()
        self.start_utc = start_utc
        self.end_utc = end_utc
        self.base_utc = base_utc
        self.satellite_numbers = tuple(satellite_numbers or (16, 17, 18, 19))
        self.cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else _default_sunpy_cache_dir()
        self._cancel_requested = False

    @Slot()
    def request_cancel(self):
        self._cancel_requested = True

    def _emit_progress(self, value, text) -> None:
        if self._cancel_requested:
            return
        self.progress.emit(value, text)

    def _helper_command(self, request_path: str, response_path: str) -> list[str]:
        if getattr(sys, "frozen", False):
            binary = str(QCoreApplication.applicationFilePath() or sys.executable)
            return [binary, "--mode=goes-overlay-helper", "--request-file", request_path, "--response-file", response_path]

        main_py = Path(__file__).resolve().parent / "main.py"
        return [sys.executable, str(main_py), "--mode=goes-overlay-helper", "--request-file", request_path, "--response-file", response_path]

    def _terminate_process(self, proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
            return
        except Exception:
            pass
        try:
            proc.kill()
            proc.wait(timeout=1.5)
        except Exception:
            pass

    @staticmethod
    def _read_text_if_exists(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _format_subprocess_failure(returncode: int | None, stderr_text: str) -> str:
        code = int(returncode) if returncode is not None else -1
        if code in {-11, 139}:
            return (
                "GOES overlay helper crashed while loading SunPy archive data. "
                "The request was aborted to keep the main application running."
            )
        detail = (stderr_text or "").strip().splitlines()
        if detail:
            return detail[-1].strip()
        return f"GOES overlay helper exited with code {code}."

    @Slot()
    def run(self):
        if self._cancel_requested:
            self.cancelled.emit(self.request_key)
            return
        self._emit_progress(5, "Loading GOES overlay...")

        temp_dir = Path(tempfile.mkdtemp(prefix="callisto_goes_overlay_"))
        request_path = temp_dir / "request.json"
        response_path = temp_dir / "response.json"
        stderr_path = temp_dir / "stderr.log"
        proc = None

        request_payload = {
            "start_utc": self.start_utc.isoformat(),
            "end_utc": self.end_utc.isoformat(),
            "base_utc": self.base_utc.isoformat(),
            "cache_dir": str(self.cache_dir),
            "satellite_numbers": [int(item) for item in self.satellite_numbers],
        }

        try:
            request_path.write_text(json.dumps(request_payload), encoding="utf-8")

            with stderr_path.open("w", encoding="utf-8") as stderr_handle:
                proc = subprocess.Popen(
                    self._helper_command(str(request_path), str(response_path)),
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_handle,
                    stdin=subprocess.DEVNULL,
                    cwd=str(Path(__file__).resolve().parents[2]),
                )

            while proc.poll() is None:
                if self._cancel_requested:
                    self._terminate_process(proc)
                    self.cancelled.emit(self.request_key)
                    return
                time.sleep(0.15)

            if self._cancel_requested:
                self.cancelled.emit(self.request_key)
                return

            stderr_text = self._read_text_if_exists(stderr_path)
            raw_response = {}
            if response_path.exists():
                try:
                    raw_response = json.loads(response_path.read_text(encoding="utf-8"))
                except Exception:
                    raw_response = {}
            if proc.returncode != 0:
                if isinstance(raw_response, dict) and raw_response:
                    self.failed.emit(
                        self.request_key,
                        str(raw_response.get("error") or self._format_subprocess_failure(proc.returncode, stderr_text)),
                    )
                else:
                    self.failed.emit(self.request_key, self._format_subprocess_failure(proc.returncode, stderr_text))
                return

            if not response_path.exists():
                self.failed.emit(self.request_key, "GOES overlay helper exited without producing a response.")
                return

            if not bool(raw_response.get("ok")):
                self.failed.emit(self.request_key, str(raw_response.get("error") or "Could not load GOES XRS data."))
                return

            payload = goes_overlay_payload_from_dict(dict(raw_response.get("payload") or {}))
        except Exception as exc:
            if self._cancel_requested:
                self.cancelled.emit(self.request_key)
                return
            self.failed.emit(self.request_key, str(exc))
            return
        finally:
            self._terminate_process(proc)
            for path in (request_path, response_path, stderr_path):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
            try:
                temp_dir.rmdir()
            except Exception:
                pass

        if self._cancel_requested:
            self.cancelled.emit(self.request_key)
            return
        self.finished.emit(self.request_key, payload)


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
