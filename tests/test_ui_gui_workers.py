"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("astropy")

from src.UI.gui_workers import DownloaderImportWorker
from tests.helpers_learmonth import write_test_callisto_fit


def _run_worker(worker):
    finished = []
    failed = []
    worker.finished.connect(lambda payload: finished.append(payload))
    worker.failed.connect(lambda message: failed.append(message))
    worker.run()
    return finished, failed


def test_downloader_import_worker_loads_local_single_fit(tmp_path):
    fit_path = write_test_callisto_fit(
        tmp_path / "LEARMONTH_20240401_000000_01.fit",
        data=np.arange(6, dtype=np.uint8).reshape(2, 3),
        freqs=np.array([25.0, 26.0]),
        time=np.array([0.0, 3.0, 16.0]),
        date_obs="2024/04/01",
        time_obs="00:00:00",
    )

    finished, failed = _run_worker(DownloaderImportWorker([str(fit_path)]))

    assert failed == []
    assert finished[0]["kind"] == "single"
    assert finished[0]["filename"] == "LEARMONTH_20240401_000000_01.fit"
    assert finished[0]["source_path"] == str(fit_path)


def test_downloader_import_worker_combines_local_fits_across_midnight(tmp_path):
    freqs = np.array([25.0, 26.0])
    time = np.array([0.0, 3.0])
    path1 = write_test_callisto_fit(
        tmp_path / "LEARMONTH_20240331_235500_01.fit",
        data=np.ones((2, 2), dtype=np.uint8),
        freqs=freqs,
        time=time,
        date_obs="2024/03/31",
        time_obs="23:55:00",
    )
    path2 = write_test_callisto_fit(
        tmp_path / "LEARMONTH_20240401_001100_01.fit",
        data=np.zeros((2, 2), dtype=np.uint8),
        freqs=freqs,
        time=time,
        date_obs="2024/04/01",
        time_obs="00:11:00",
    )

    finished, failed = _run_worker(DownloaderImportWorker([str(path1), str(path2)]))

    assert failed == []
    assert finished[0]["kind"] == "combined"
    assert finished[0]["combined"]["combine_type"] == "time"
    assert finished[0]["combined"]["data"].shape == (2, 4)


def test_downloader_import_worker_requests_options_for_frequency_combine(tmp_path):
    time = np.array([0.0, 3.0])
    path1 = write_test_callisto_fit(
        tmp_path / "LEARMONTH_20240401_000000_01.fit",
        data=np.ones((2, 2), dtype=np.uint8),
        freqs=np.array([20.0, 10.0]),
        time=time,
        date_obs="2024/04/01",
        time_obs="00:00:00",
    )
    path2 = write_test_callisto_fit(
        tmp_path / "LEARMONTH_20240401_000000_02.fit",
        data=np.zeros((2, 2), dtype=np.uint8),
        freqs=np.array([50.0, 40.0]),
        time=time,
        date_obs="2024/04/01",
        time_obs="00:00:00",
    )

    finished, failed = _run_worker(DownloaderImportWorker([str(path1), str(path2)]))

    assert failed == []
    assert finished[0]["kind"] == "frequency_options_required"
    assert finished[0]["files"] == [str(path1), str(path2)]
    assert finished[0]["relation"]["has_gap"] is True
    assert finished[0]["relation"]["has_overlap"] is False


def test_downloader_import_worker_marks_non_consecutive_local_fits_invalid(tmp_path):
    freqs = np.array([25.0, 26.0])
    time = np.array([0.0, 3.0])
    path1 = write_test_callisto_fit(
        tmp_path / "LEARMONTH_20240401_000000_01.fit",
        data=np.ones((2, 2), dtype=np.uint8),
        freqs=freqs,
        time=time,
        date_obs="2024/04/01",
        time_obs="00:00:00",
    )
    path2 = write_test_callisto_fit(
        tmp_path / "LEARMONTH_20240401_030000_01.fit",
        data=np.zeros((2, 2), dtype=np.uint8),
        freqs=freqs,
        time=time,
        date_obs="2024/04/01",
        time_obs="03:00:00",
    )

    finished, failed = _run_worker(DownloaderImportWorker([str(path1), str(path2)]))

    assert failed == []
    assert finished[0]["kind"] == "invalid"
