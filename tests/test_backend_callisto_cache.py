"""
Backend tests for the shared e-CALLISTO download cache.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.Backend import callisto_cache


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    root = tmp_path / "appdata"
    monkeypatch.setattr(callisto_cache, "_app_data_root", lambda: root)
    monkeypatch.setattr(callisto_cache, "max_cache_bytes", lambda: callisto_cache.DEFAULT_MAX_CACHE_BYTES)
    return root / callisto_cache.CACHE_DIR_NAME


class FakeResponse:
    def __init__(self, payload: bytes, *, status_ok=True, chunk_size=4):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}
        self._status_ok = status_ok
        self._chunk_size = chunk_size

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP 404")

    def iter_content(self, chunk_size=None):
        step = chunk_size or self._chunk_size
        for start in range(0, len(self.payload), step):
            yield self.payload[start:start + step]


class FakeSession:
    def __init__(self, payload: bytes = b"fits-bytes", *, status_ok=True):
        self.payload = payload
        self.status_ok = status_ok
        self.calls: list[str] = []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        return FakeResponse(self.payload, status_ok=self.status_ok)

    def close(self):
        return None


def _url(name: str) -> str:
    return f"https://archive.test/2024/01/02/{name}"


def test_cache_path_uses_station_and_date(cache_dir):
    name = "AUSTRIA-OE3FLB_20230312_101500_01.fit.gz"
    assert callisto_cache.cache_path_for(_url(name)) == cache_dir / "AUSTRIA-OE3FLB" / "2023-03-12" / name


def test_cache_path_falls_back_to_unsorted_for_unparsable_names(cache_dir):
    assert callisto_cache.cache_path_for(_url("mystery.fit")) == (
        cache_dir / callisto_cache.UNSORTED_DIR_NAME / "mystery.fit"
    )


def test_cache_path_rejects_a_url_without_a_filename(cache_dir):
    with pytest.raises(ValueError):
        callisto_cache.cache_path_for("https://archive.test/2024/01/02/")


def test_fetch_downloads_once_then_serves_from_cache(cache_dir):
    name = "BIR_20240102_000000_01.fit.gz"
    session = FakeSession(b"spectrogram")

    path, was_cached = callisto_cache.fetch_cached(_url(name), session=session)
    assert was_cached is False
    assert path.read_bytes() == b"spectrogram"
    assert session.calls == [_url(name)]

    again, was_cached = callisto_cache.fetch_cached(_url(name), session=session)
    assert was_cached is True
    assert again == path
    assert session.calls == [_url(name)], "a cache hit must not touch the network"


def test_failed_download_leaves_no_partial_entry(cache_dir):
    name = "BIR_20240102_000000_01.fit.gz"
    session = FakeSession(b"", status_ok=False)

    with pytest.raises(RuntimeError):
        callisto_cache.fetch_cached(_url(name), session=session)

    target = callisto_cache.cache_path_for(_url(name))
    assert not target.exists()
    assert not target.with_name(target.name + ".part").exists()
    assert callisto_cache.cached_file(_url(name)) is None


def test_zero_byte_entry_is_treated_as_a_miss(cache_dir):
    name = "BIR_20240102_000000_01.fit.gz"
    target = callisto_cache.cache_path_for(_url(name))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")

    assert callisto_cache.cached_file(_url(name)) is None

    session = FakeSession(b"real-bytes")
    path, was_cached = callisto_cache.fetch_cached(_url(name), session=session)
    assert was_cached is False
    assert path.read_bytes() == b"real-bytes"


def test_enforce_limit_evicts_oldest_first(cache_dir):
    session = FakeSession(b"0123456789")
    names = [
        "BIR_20240102_000000_01.fit.gz",
        "BIR_20240102_001500_01.fit.gz",
        "BIR_20240102_003000_01.fit.gz",
    ]
    paths = []
    for index, name in enumerate(names):
        path, _ = callisto_cache.fetch_cached(_url(name), session=session)
        os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
        paths.append(path)

    assert callisto_cache.cache_size_bytes() == 30

    freed = callisto_cache.enforce_cache_limit(15)

    assert freed == 20
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()
    assert callisto_cache.cache_size_bytes() == 10


def test_cache_hit_refreshes_mtime_so_eviction_is_least_recently_used(cache_dir):
    session = FakeSession(b"0123456789")
    old_name = "BIR_20240102_000000_01.fit.gz"
    new_name = "BIR_20240102_001500_01.fit.gz"

    old_path, _ = callisto_cache.fetch_cached(_url(old_name), session=session)
    os.utime(old_path, (1_700_000_000, 1_700_000_000))
    new_path, _ = callisto_cache.fetch_cached(_url(new_name), session=session)
    os.utime(new_path, (1_700_000_100, 1_700_000_100))

    # Touching the older entry must save it from the next eviction.
    callisto_cache.cached_file(_url(old_name))
    callisto_cache.enforce_cache_limit(10)

    assert old_path.exists()
    assert not new_path.exists()


def test_enforce_limit_is_a_no_op_below_the_cap(cache_dir):
    session = FakeSession(b"0123456789")
    path, _ = callisto_cache.fetch_cached(_url("BIR_20240102_000000_01.fit.gz"), session=session)

    assert callisto_cache.enforce_cache_limit(1024) == 0
    assert path.exists()


def test_clear_cache_empties_the_folder_but_keeps_it(cache_dir):
    session = FakeSession(b"0123456789")
    callisto_cache.fetch_cached(_url("BIR_20240102_000000_01.fit.gz"), session=session)
    callisto_cache.fetch_cached(_url("mystery.fit"), session=session)
    assert callisto_cache.cache_size_bytes() == 20

    assert callisto_cache.clear_cache() == 0
    assert cache_dir.is_dir()
    assert callisto_cache.cache_size_bytes() == 0


def test_format_size_scales_units():
    assert callisto_cache.format_size(0) == "0 B"
    assert callisto_cache.format_size(2048) == "2.0 KB"
    assert callisto_cache.format_size(432_012_345) == "412.0 MB"


def test_eviction_never_deletes_an_in_flight_part_file(cache_dir):
    session = FakeSession(b"0123456789")
    done, _ = callisto_cache.fetch_cached(_url("BIR_20240102_000000_01.fit.gz"), session=session)
    in_flight = cache_dir / "BIR" / "2024-01-02" / "BIR_20240102_001500_01.fit.gz.part"
    in_flight.write_bytes(b"half-written")

    callisto_cache.enforce_cache_limit(0)

    assert not done.exists()
    assert in_flight.exists(), "a download still being written must survive eviction"
    assert callisto_cache.cache_size_bytes() == 0
