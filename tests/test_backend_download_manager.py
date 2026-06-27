"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Tests for the byte-accurate download engine (src/Backend/download_manager.py).
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.Backend.download_manager import (
    AggregateProgress,
    CacheByteMonitor,
    DownloadItem,
    DownloadManager,
    _SpeedMeter,
    compute_eta,
    format_bytes,
    format_eta,
    format_speed,
    parse_size_text,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("12.6 MByte", int(12.6 * 1024**2)),
        ("1 GB", 1024**3),
        ("512 KB", 512 * 1024),
        ("2048", 2048 * 1024**2),  # unit-less VSO numbers are MByte
        ("  3.5 MiB ", int(3.5 * 1024**2)),
        ("", None),
        (None, None),
        ("n/a", None),
        (0, None),
        (4096, 4096),
    ],
)
def test_parse_size_text(text, expected):
    assert parse_size_text(text) == expected


def test_format_helpers():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536).endswith("KB")
    assert format_bytes(None) == "?"
    assert format_speed(0) == "--"
    assert format_speed(1024**2).endswith("/s")
    assert format_eta(None) == "--"
    assert format_eta(5) == "5s"
    assert format_eta(125) == "2m 05s"
    assert format_eta(3700).startswith("1h")


def test_compute_eta():
    assert compute_eta(0, None, 100) is None
    assert compute_eta(0, 1000, 0) is None
    assert compute_eta(900, 1000, 100) == pytest.approx(1.0)
    assert compute_eta(1000, 1000, 100) == 0.0


def test_speed_meter_tracks_recent_rate():
    meter = _SpeedMeter(window=2.0)
    assert meter.update(0, now=0.0) == 0.0
    # 1000 bytes over 1s -> ~1000 B/s
    assert meter.update(1000, now=1.0) == pytest.approx(1000.0, rel=0.01)


def test_aggregate_progress_fraction():
    agg = AggregateProgress(files_total=4, files_done=2, bytes_done=50, bytes_total=200)
    assert agg.fraction == pytest.approx(0.25)
    assert agg.percent() == 25
    # Falls back to file count when byte total unknown.
    agg2 = AggregateProgress(files_total=4, files_done=1, bytes_done=0, bytes_total=None)
    assert agg2.fraction == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# CacheByteMonitor
# ---------------------------------------------------------------------------
def test_cache_byte_monitor_tracks_growth(tmp_path):
    monitor = CacheByteMonitor(tmp_path, expected_total=300)
    assert monitor.bytes_downloaded() == 0

    (tmp_path / "a.fits").write_bytes(b"x" * 100)
    assert monitor.bytes_downloaded() == 100

    (tmp_path / "a.fits").write_bytes(b"x" * 250)
    (tmp_path / "b.fits").write_bytes(b"y" * 50)
    snap = monitor.sample()
    assert snap.bytes_done == 300
    assert snap.bytes_total == 300


def test_cache_byte_monitor_ignores_preexisting(tmp_path):
    (tmp_path / "old.fits").write_bytes(b"z" * 500)
    monitor = CacheByteMonitor(tmp_path, expected_total=100)
    assert monitor.bytes_downloaded() == 0
    (tmp_path / "new.fits").write_bytes(b"n" * 100)
    assert monitor.bytes_downloaded() == 100


# ---------------------------------------------------------------------------
# aiohttp engine against a local Range-capable server
# ---------------------------------------------------------------------------
class _RangeHandler(BaseHTTPRequestHandler):
    payloads: dict[str, bytes] = {}

    def log_message(self, *args):  # silence test server logging
        pass

    def do_GET(self):
        body = self.payloads.get(self.path)
        if body is None:
            self.send_error(404)
            return
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng.split("=", 1)[1].split("-", 1)[0] or 0)
            chunk = body[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture()
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        yield base, _RangeHandler.payloads
    finally:
        server.shutdown()
        _RangeHandler.payloads.clear()


class _SlowHandler(BaseHTTPRequestHandler):
    """Streams a payload slowly so cancellation can be observed mid-transfer."""

    payload = b"S" * (400 * 1024)

    def log_message(self, *args):
        pass

    def do_GET(self):
        import time as _time

        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        step = 16 * 1024
        for i in range(0, len(self.payload), step):
            try:
                self.wfile.write(self.payload[i:i + step])
                self.wfile.flush()
            except Exception:
                return
            _time.sleep(0.03)


@pytest.fixture()
def slow_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


def test_download_cancels_mid_stream_promptly(slow_server, tmp_path):
    import time as _time

    flag = {"cancel": False}
    ticks = {"n": 0}

    def progress(_agg):
        ticks["n"] += 1
        if ticks["n"] >= 2:  # let a little data flow, then ask to stop
            flag["cancel"] = True

    mgr = DownloadManager(chunk_size=8192, progress_interval=0.0)
    started = _time.monotonic()
    result = mgr.download(
        [DownloadItem(url=f"{slow_server}/slow", dest=tmp_path / "slow.fits", expected_size=400 * 1024)],
        progress_cb=progress,
        cancel_cb=lambda: flag["cancel"],
    )
    elapsed = _time.monotonic() - started

    assert result.cancelled is True
    assert result.paths == []
    # Partial data is never promoted to the final destination on cancel.
    assert not (tmp_path / "slow.fits").exists()
    # The whole slow transfer would take ~0.75s; cancellation stops it early.
    assert elapsed < 0.6


def test_download_full_file_with_byte_progress(http_server, tmp_path):
    base, payloads = http_server
    payloads["/big.fits"] = b"A" * (256 * 1024)

    ticks: list[AggregateProgress] = []
    mgr = DownloadManager(max_concurrent=2, chunk_size=8192, progress_interval=0.0)
    result = mgr.download(
        [DownloadItem(url=f"{base}/big.fits", dest=tmp_path / "big.fits", expected_size=256 * 1024)],
        progress_cb=ticks.append,
    )

    assert result.errors == []
    assert len(result.paths) == 1
    out = tmp_path / "big.fits"
    assert out.read_bytes() == payloads["/big.fits"]
    # We received real intermediate byte progress, not just 0 then 100.
    assert any(0 < t.bytes_done < 256 * 1024 for t in ticks)
    assert ticks[-1].bytes_done == 256 * 1024


def test_download_resumes_from_partial(http_server, tmp_path):
    base, payloads = http_server
    payloads["/r.fits"] = b"B" * 1000
    # Pretend 600 bytes already arrived in a previous run.
    (tmp_path / "r.fits.part").write_bytes(b"B" * 600)

    mgr = DownloadManager(chunk_size=128, progress_interval=0.0)
    result = mgr.download([DownloadItem(url=f"{base}/r.fits", dest=tmp_path / "r.fits", expected_size=1000)])

    assert result.errors == []
    assert (tmp_path / "r.fits").read_bytes() == b"B" * 1000
    assert not (tmp_path / "r.fits.part").exists()


def test_download_skips_cached_file(http_server, tmp_path):
    base, payloads = http_server
    payloads["/c.fits"] = b"C" * 4096
    (tmp_path / "c.fits").write_bytes(b"C" * 4096)  # already complete

    mgr = DownloadManager(progress_interval=0.0)
    result = mgr.download([DownloadItem(url=f"{base}/c.fits", dest=tmp_path / "c.fits", expected_size=4096)])

    assert result.cached_count == 1
    assert result.errors == []
    assert len(result.paths) == 1


def test_download_cancellation(http_server, tmp_path):
    base, payloads = http_server
    payloads["/x.fits"] = b"D" * (512 * 1024)

    mgr = DownloadManager(chunk_size=4096, progress_interval=0.0)
    result = mgr.download(
        [DownloadItem(url=f"{base}/x.fits", dest=tmp_path / "x.fits", expected_size=512 * 1024)],
        cancel_cb=lambda: True,  # cancel immediately
    )
    assert result.cancelled is True
    assert result.paths == []


def test_download_reports_failure_for_missing_url(http_server, tmp_path):
    base, _ = http_server
    mgr = DownloadManager(progress_interval=0.0)
    result = mgr.download([DownloadItem(url=f"{base}/missing.fits", dest=tmp_path / "missing.fits")])
    assert result.paths == []
    assert len(result.errors) == 1
