from datetime import datetime
from io import BytesIO

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
fits = pytest.importorskip("astropy.io.fits")
pytest.importorskip("matplotlib")

from PySide6.QtCore import QThread, Qt

from src.Backend import callisto_cache
from src.UI import burst_preview, callisto_downloader
from src.UI.burst_preview import BurstPreviewWorker
from src.UI.callisto_downloader import CallistoEventCandidate, EventDownloadTask, deliver_archive_file


class _Response:
    def __init__(self, payload, *, known_length=True, before_chunk=None):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))} if known_length else {}
        self.before_chunk = before_chunk
        self.chunks_read = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for index, start in enumerate(range(0, len(self.payload), chunk_size)):
            if self.before_chunk:
                self.before_chunk(index)
            self.chunks_read += 1
            yield self.payload[start:start + chunk_size]


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.closed = True

    def get(self, url, **_kwargs):
        self.calls.append(url)
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def _candidate(clock="120000"):
    name = f"GLASGOW_20240102_{clock}_01.fit"
    return CallistoEventCandidate(
        "GLASGOW", datetime.strptime("20240102" + clock, "%Y%m%d%H%M%S"),
        name, "https://archive.test/2024/01/02/" + name, "01",
    )


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(callisto_cache, "_app_data_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(callisto_cache, "_CHUNK_SIZE", 1024)
    monkeypatch.setattr(callisto_cache, "enforce_cache_limit", lambda: 0)


def _fits_bytes():
    data = np.arange(24, dtype=np.float32).reshape(3, 8)
    header = fits.Header({
        "DATE-OBS": "2024-01-02", "TIME-OBS": "12:00:00",
        "DATE-END": "2024-01-02", "TIME-END": "12:00:02",
        "CRVAL1": 43200.0, "CDELT1": 0.25, "CRPIX1": 1.0,
        "CRVAL2": 100.0, "CDELT2": -1.0, "CRPIX2": 1.0,
    })
    handle = BytesIO()
    fits.PrimaryHDU(data=data, header=header).writeto(handle)
    return handle.getvalue(), data


def _run_in_thread(worker):
    """Exercise QObject thread migration and run without borrowing the GUI loop."""
    thread = QThread()
    results, progress = [], []
    worker.finished.connect(results.append, Qt.ConnectionType.DirectConnection)
    worker.progress.connect(lambda *args: progress.append(args), Qt.ConnectionType.DirectConnection)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    worker.finished.connect(worker.deleteLater)
    thread.start()
    try:
        assert thread.wait(10000), "Preview worker did not finish."
    finally:
        if thread.isRunning():
            worker.request_cancel()
            thread.quit()
            thread.wait(10000)
    assert len(results) == 1
    return results[0], progress


def test_preview_downloads_valid_fits_in_worker_thread_and_reuses_cache(isolated_cache, monkeypatch):
    candidate = _candidate()
    payload, expected = _fits_bytes()
    session = _Session({candidate.url: _Response(payload)})
    monkeypatch.setattr(burst_preview, "build_archive_session", lambda: session)

    result, progress = _run_in_thread(BurstPreviewWorker([candidate]))

    assert result["cached_count"] == 0
    assert result["total"] == 1
    assert result["errors"] == []
    assert len(result["panels"]) == 1
    panel = result["panels"][0]
    assert panel["subtitle"] == candidate.filename
    assert np.array_equal(panel["data"], expected)
    assert np.allclose(panel["freqs"], [100, 99, 98])
    assert len(panel["time"]) == 8
    assert session.closed
    assert any(0 < current < 900 for current, _total, _message in progress)
    assert [step[0] for step in progress] == sorted(step[0] for step in progress)
    assert all(current < total for current, total, _message in progress[:-1])
    assert progress[-2] == (900, 1000, "Building preview...")
    assert progress[-1] == (1000, 1000, "Preview ready.")

    cached, cached_progress = _run_in_thread(BurstPreviewWorker([candidate]))

    assert cached["cached_count"] == 1
    assert np.array_equal(cached["panels"][0]["data"], expected)
    assert session.calls == [candidate.url]
    assert any("Using cached" in message for _current, _total, message in cached_progress)


@pytest.mark.parametrize("known_length", [True, False])
def test_preview_cancel_during_transfer_cleans_partial_file_and_never_builds(
    isolated_cache, monkeypatch, known_length,
):
    candidate = _candidate()
    worker = BurstPreviewWorker([candidate])
    response = _Response(
        b"x" * 8192, known_length=known_length,
        before_chunk=lambda index: worker.request_cancel() if index == 1 else None,
    )
    session = _Session({candidate.url: response})
    monkeypatch.setattr(burst_preview, "build_archive_session", lambda: session)
    monkeypatch.setattr(callisto_downloader, "build_preview_panels", lambda *_args: pytest.fail("Cancelled preview built panels"))

    result, progress = _run_in_thread(worker)

    assert result == {"cancelled": True}
    assert response.chunks_read == 2
    assert session.closed
    path = callisto_cache.cache_path_for(candidate.url, candidate.filename)
    assert not path.exists()
    assert not path.with_name(path.name + ".part").exists()
    assert all(current < total for current, total, _message in progress)


def test_preview_cancel_while_building_discards_panels(isolated_cache, monkeypatch):
    candidate = _candidate()
    worker = BurstPreviewWorker([candidate])
    monkeypatch.setattr(burst_preview, "build_archive_session", lambda: _Session({candidate.url: _Response(b"fits")}))

    def build_panels(_files):
        worker.request_cancel()
        return [{"title": "Must not open"}], []

    monkeypatch.setattr(callisto_downloader, "build_preview_panels", build_panels)
    result, progress = _run_in_thread(worker)

    assert result == {"cancelled": True}
    assert progress[-1] == (900, 1000, "Building preview...")


def test_preview_keeps_valid_panels_when_another_download_fails(isolated_cache, monkeypatch):
    good, bad = _candidate(), _candidate("121500")
    payload, _data = _fits_bytes()
    session = _Session({good.url: _Response(payload), bad.url: OSError("HTTP 503")})
    monkeypatch.setattr(burst_preview, "build_archive_session", lambda: session)

    result, progress = _run_in_thread(BurstPreviewWorker([bad, good]))

    assert result["total"] == 2
    assert len(result["panels"]) == 1
    assert result["errors"] == [f"{bad.filename}: HTTP 503"]
    assert progress[-1][:2] == (2000, 2000)


def test_preview_reports_all_failed_downloads_without_building(isolated_cache, monkeypatch):
    candidate = _candidate()
    monkeypatch.setattr(burst_preview, "build_archive_session", lambda: _Session({candidate.url: OSError("HTTP 503")}))
    monkeypatch.setattr(callisto_downloader, "build_preview_panels", lambda *_args: pytest.fail("No valid files"))

    result, progress = _run_in_thread(BurstPreviewWorker([candidate]))

    assert "HTTP 503" in result["error"]
    assert all(current < total for current, total, _message in progress)


def test_preview_empty_selection_and_early_cancel_do_not_open_network(monkeypatch):
    monkeypatch.setattr(burst_preview, "build_archive_session", lambda: pytest.fail("No network request expected"))
    result, progress = _run_in_thread(BurstPreviewWorker([]))
    assert "Select at least one" in result["error"]
    assert progress == []

    worker = BurstPreviewWorker([_candidate()])
    worker.request_cancel()
    result, progress = _run_in_thread(worker)
    assert result == {"cancelled": True}
    assert progress == []


def test_event_download_reports_transfer_progress_and_finishes_after_copy(
    isolated_cache, tmp_path, monkeypatch,
):
    candidate = _candidate()
    payload = b"fits" * 2048
    session = _Session({candidate.url: _Response(payload)})
    monkeypatch.setattr(callisto_cache, "build_archive_session", lambda: session)

    for index in range(2):
        destination = tmp_path / f"download-{index}.fit"
        task = EventDownloadTask(candidate, str(destination))
        progress, done = [], []

        def record_progress(name, fraction, message):
            progress.append((name, fraction, message))
            if fraction == 1:
                assert destination.read_bytes() == payload

        task.progress.connect(record_progress)
        task.done.connect(lambda *args: done.append(args))
        task.run()

        assert progress[0][1] == 0
        assert progress[-1][1] == 1
        assert all(step[1] < 1 for step in progress[:-1])
        assert all(step[0] == candidate.filename for step in progress)
        assert done == [(candidate.filename, str(destination), True, "")]
        if index == 0:
            assert any(0 < step[1] < 0.99 for step in progress)
        else:
            assert any("Using cached" in step[2] for step in progress)
    assert session.calls == [candidate.url]


def test_event_download_copy_error_does_not_report_completion(isolated_cache, tmp_path, monkeypatch):
    candidate = _candidate()
    monkeypatch.setattr(callisto_cache, "build_archive_session", lambda: _Session({candidate.url: _Response(b"fits")}))

    def fail_copy(*_args):
        raise OSError("Destination is read-only")

    monkeypatch.setattr(callisto_downloader.shutil, "copy2", fail_copy)
    destination = str(tmp_path / "out.fit")
    task = EventDownloadTask(candidate, destination)
    progress, done = [], []
    task.progress.connect(lambda *_args: progress.append(_args))
    task.done.connect(lambda *_args: done.append(_args))
    task.run()

    assert all(step[1] < 1 for step in progress)
    assert done == [(candidate.filename, destination, False, "Destination is read-only")]


def test_deliver_archive_file_preserves_callers_without_callback(tmp_path, monkeypatch):
    source = tmp_path / "cached.fit"
    source.write_bytes(b"fits")
    calls = []

    def fetch(url, filename):
        calls.append((url, filename))
        return source, True

    monkeypatch.setattr(callisto_cache, "fetch_cached", fetch)
    target = tmp_path / "download.fit"
    assert deliver_archive_file("https://archive.test/file.fit", str(target), "file.fit") is True
    assert target.read_bytes() == b"fits"
    assert calls == [("https://archive.test/file.fit", "file.fit")]
