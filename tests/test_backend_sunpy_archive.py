"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import threading
import warnings

from src.Backend import sunpy_archive as sa


class _Unit:
    def __init__(self, name: str):
        self.name = name

    def __rmul__(self, value):
        return (float(value), self.name)


class _FakeUnits:
    angstrom = _Unit("angstrom")
    second = _Unit("second")


class _FakeAttrs:
    class goes:
        class SatelliteNumber:
            def __init__(self, value):
                self.value = int(value)

    @staticmethod
    def Time(start, end):
        return ("Time", start, end)

    @staticmethod
    def Source(value):
        return ("Source", str(value))

    @staticmethod
    def Instrument(value):
        return ("Instrument", str(value))

    @staticmethod
    def Detector(value):
        return ("Detector", str(value))

    @staticmethod
    def Wavelength(value):
        return ("Wavelength", value)

    @staticmethod
    def Sample(value):
        return ("Sample", value)

    @staticmethod
    def Resolution(value):
        return ("Resolution", value)


def _mk_query(**kwargs):
    defaults = dict(
        start_dt=datetime(2026, 2, 10, 1, 0, 0),
        end_dt=datetime(2026, 2, 10, 2, 0, 0),
        spacecraft="SDO",
        instrument="AIA",
    )
    defaults.update(kwargs)
    return sa.SunPyQuerySpec(**defaults)


def test_build_attrs_for_aia_includes_wavelength():
    spec = _mk_query(wavelength_angstrom=193.0)
    attrs = sa.build_attrs(spec, attrs_module=_FakeAttrs, units_module=_FakeUnits)
    assert attrs[0][0] == "Time"
    assert attrs[1] == ("Source", "SDO")
    assert attrs[2] == ("Instrument", "AIA")
    assert any(item[0] == "Wavelength" for item in attrs if isinstance(item, tuple))


def test_build_attrs_for_aia_can_request_full_resolution():
    spec = _mk_query(wavelength_angstrom=193.0, resolution=1.0)
    attrs = sa.build_attrs(spec, attrs_module=_FakeAttrs, units_module=_FakeUnits)
    assert ("Resolution", 1.0) in attrs


def test_build_attrs_for_goes_includes_satellite():
    spec = _mk_query(spacecraft="GOES", instrument="XRS", satellite_number=18)
    attrs = sa.build_attrs(spec, attrs_module=_FakeAttrs, units_module=_FakeUnits)
    sat_attrs = [item for item in attrs if isinstance(item, _FakeAttrs.goes.SatelliteNumber)]
    assert len(sat_attrs) == 1
    assert sat_attrs[0].value == 18


def test_search_normalizes_rows():
    class FakeFido:
        def __init__(self):
            self.last_attrs = None

        def search(self, *attrs):
            self.last_attrs = attrs
            return [
                [
                    {
                        "Start Time": "2026-02-10 01:00:00",
                        "End Time": "2026-02-10 01:02:00",
                        "Source": "SDO",
                        "Instrument": "AIA",
                        "Provider": "VSO",
                        "fileid": "aia_1.fits",
                        "Size": "1.0 MB",
                    }
                ]
            ]

    fido = FakeFido()
    result = sa.search(_mk_query(), fido_client=fido, attrs_module=_FakeAttrs, units_module=_FakeUnits)

    assert len(result.rows) == 1
    assert result.rows[0].source == "SDO"
    assert result.rows[0].instrument == "AIA"
    assert result.rows[0].provider == "VSO"
    assert result.rows[0].fileid == "aia_1.fits"
    assert result.row_index_map == [(0, 0)]


def test_fetch_returns_partial_failures(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_1.fits",
            size="1 MB",
        ),
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 3, 0),
            end=datetime(2026, 2, 10, 1, 4, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_2.fits",
            size="1 MB",
        ),
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "aia_1.fits"}, {"fileid": "aia_2.fits"}]],
        row_index_map=[(0, 0), (0, 1)],
    )

    class FakeFido:
        def __init__(self):
            self.calls = []

        def fetch(self, query_slice, path):
            self.calls.append((query_slice, path))
            if len(query_slice) > 1:
                raise RuntimeError("batch unsupported in fake client")
            fileid = query_slice[0]["fileid"]
            if fileid == "aia_2.fits":
                raise RuntimeError("download failed")
            return [tmp_path / "aia_1.fits"]

    fido = FakeFido()
    out = sa.fetch(search_result, tmp_path, selected_rows=[0, 1], fido_client=fido)
    assert out.requested_count == 2
    assert out.failed_count == 1
    assert out.paths == [str((tmp_path / "aia_1.fits").resolve())]
    assert len(fido.calls) >= 2
    assert fido.calls[0][1].endswith("{file}")


def test_fetch_counts_empty_row_result_as_failure(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_1.fits",
            size="1 MB",
        )
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "aia_1.fits"}]],
        row_index_map=[(0, 0)],
    )

    class FakeFido:
        @staticmethod
        def fetch(_query_slice, path):
            assert path.endswith("{file}")
            return []

    out = sa.fetch(search_result, tmp_path, selected_rows=[0], fido_client=FakeFido())
    assert out.requested_count == 1
    assert out.failed_count == 1
    assert out.paths == []
    assert "returned no files" in out.errors[0]


def test_fetch_suppresses_non_main_thread_warning(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_1.fits",
            size="1 MB",
        )
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "aia_1.fits"}]],
        row_index_map=[(0, 0)],
    )

    class FakeFido:
        @staticmethod
        def fetch(_query_slice, path, progress=True):
            assert path.endswith("{file}")
            assert progress is False
            warnings.warn(
                "This download has been started in a thread which is not the main thread. "
                "You will not be able to interrupt the download.",
                UserWarning,
            )
            return [tmp_path / "aia_1.fits"]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = sa.fetch(search_result, tmp_path, selected_rows=[0], fido_client=FakeFido())

    assert out.requested_count == 1
    assert out.failed_count == 0
    assert out.paths == [str((tmp_path / "aia_1.fits").resolve())]
    assert not any("not the main thread" in str(w.message) for w in caught)


def test_fetch_batches_contiguous_rows_into_single_call(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_1.fits",
            size="1 MB",
        ),
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 3, 0),
            end=datetime(2026, 2, 10, 1, 4, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_2.fits",
            size="1 MB",
        ),
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 5, 0),
            end=datetime(2026, 2, 10, 1, 6, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_3.fits",
            size="1 MB",
        ),
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "aia_1.fits"}, {"fileid": "aia_2.fits"}, {"fileid": "aia_3.fits"}]],
        row_index_map=[(0, 0), (0, 1), (0, 2)],
    )

    class FakeFido:
        def __init__(self):
            self.calls = []

        def fetch(self, query_slice, path, progress=False):
            self.calls.append((query_slice, path, progress))
            return [
                tmp_path / "aia_1.fits",
                tmp_path / "aia_2.fits",
                tmp_path / "aia_3.fits",
            ]

    fido = FakeFido()
    out = sa.fetch(search_result, tmp_path, selected_rows=[0, 1, 2], fido_client=fido)
    assert out.requested_count == 3
    assert out.failed_count == 0
    assert len(out.paths) == 3
    assert len(fido.calls) == 1
    assert fido.calls[0][2] is False


def test_fetch_batch_failure_splits_recursively_before_row_fallback(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size="1 MB",
        )
        for i in range(4)
    ]
    raw_block = [{"fileid": f"aia_{i + 1}.fits"} for i in range(4)]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[raw_block],
        row_index_map=[(0, 0), (0, 1), (0, 2), (0, 3)],
    )

    class FakeFido:
        def __init__(self):
            self.calls = []

        def fetch(self, query_slice, path, progress=False, max_conn=None):
            size = len(query_slice)
            self.calls.append(size)
            if size > 2:
                raise RuntimeError("force split")
            return [tmp_path / f"{query_slice[i]['fileid']}" for i in range(size)]

    fido = FakeFido()
    out = sa.fetch(search_result, tmp_path, selected_rows=[0, 1, 2, 3], fido_client=fido)

    assert out.requested_count == 4
    assert out.failed_count == 0
    assert len(out.paths) == 4
    assert fido.calls[0] == 4
    assert 2 in fido.calls
    assert 1 not in fido.calls[1:]


def test_fetch_splits_batch_when_fetch_returns_only_errors(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size="1 MB",
        )
        for i in range(4)
    ]
    raw_block = [{"fileid": f"aia_{i + 1}.fits"} for i in range(4)]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[raw_block],
        row_index_map=[(0, 0), (0, 1), (0, 2), (0, 3)],
    )

    class FakeResult(list):
        def __init__(self, paths, errors):
            super().__init__(paths)
            self.errors = errors

    class FakeFido:
        def __init__(self):
            self.calls = []

        def fetch(self, query_slice, path, progress=False, max_conn=None):
            size = len(query_slice)
            self.calls.append(size)
            if size > 1:
                return FakeResult([], [("https://example.test/file", "Timeout on reading data from socket")])
            return FakeResult([tmp_path / f"{query_slice[0]['fileid']}"], [])

    fido = FakeFido()
    out = sa.fetch(search_result, tmp_path, selected_rows=[0, 1, 2, 3], fido_client=fido)

    assert out.requested_count == 4
    assert out.failed_count == 0
    assert len(out.paths) == 4
    assert fido.calls[0] == 4
    assert 2 in fido.calls
    assert 1 in fido.calls


def test_fetch_fast_fails_large_timeout_only_nascom_batch(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ECALLISTO_SUNPY_FAST_FAIL_TIMEOUT_BATCH", "1")
    monkeypatch.setenv("ECALLISTO_SUNPY_FAST_FAIL_MIN_BATCH", "8")
    monkeypatch.setattr(sa, "_download_from_fetch_errors", lambda _fetch_result, row_template, **_kwargs: [])

    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size="1 MB",
        )
        for i in range(12)
    ]
    raw_block = [{"fileid": f"aia_{i + 1}.fits"} for i in range(12)]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[raw_block],
        row_index_map=[(0, i) for i in range(12)],
    )

    class FakeResult(list):
        def __init__(self, errors):
            super().__init__([])
            self.errors = errors

    class FakeFido:
        def __init__(self):
            self.calls = []

        def fetch(self, query_slice, path, progress=False, max_conn=None):
            size = len(query_slice)
            self.calls.append(size)
            errors = [
                (
                    "https://sdo7.nascom.nasa.gov/cgi-bin/drms_export.cgi?record=test",
                    "Timeout on reading data from socket",
                )
                for _ in range(size)
            ]
            return FakeResult(errors)

    fido = FakeFido()
    out = sa.fetch(search_result, tmp_path, selected_rows=list(range(12)), fido_client=fido)

    assert out.requested_count == 12
    assert out.failed_count == 12
    assert out.paths == []
    assert len(fido.calls) <= 3
    assert "Archive server timed out for all records" in out.errors[0]


def test_fetch_retries_single_row_after_timeout_result(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ECALLISTO_SUNPY_FETCH_RETRY_BACKOFF", "0")
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_1.fits",
            size="1 MB",
        )
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "aia_1.fits"}]],
        row_index_map=[(0, 0)],
    )

    class FakeResult(list):
        def __init__(self, paths, errors):
            super().__init__(paths)
            self.errors = errors

    class FakeFido:
        def __init__(self):
            self.calls = 0

        def fetch(self, _query_slice, path, progress=False, max_conn=None):
            assert path.endswith("{file}")
            self.calls += 1
            if self.calls == 1:
                return FakeResult([], [("https://example.test/file", "Timeout on reading data from socket")])
            return FakeResult([tmp_path / "aia_1.fits"], [])

    fido = FakeFido()
    out = sa.fetch(search_result, tmp_path, selected_rows=[0], fido_client=fido)

    assert out.requested_count == 1
    assert out.failed_count == 0
    assert out.paths == [str((tmp_path / "aia_1.fits").resolve())]
    assert fido.calls >= 2


def test_fetch_single_row_uses_manual_error_url_fallback(tmp_path: Path, monkeypatch):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="aia_1.fits",
            size="1 MB",
        )
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "aia_1.fits"}]],
        row_index_map=[(0, 0)],
    )

    class FakeResult(list):
        def __init__(self, paths, errors):
            super().__init__(paths)
            self.errors = errors

    class FakeFido:
        @staticmethod
        def fetch(_query_slice, path, progress=False, max_conn=None):
            assert path.endswith("{file}")
            return FakeResult([], [("https://example.test/one", "Timeout on reading data from socket")])

    manual_path = (tmp_path / "manual_retry.fits").resolve()
    manual_path.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(
        sa,
        "_download_from_fetch_errors",
        lambda _fetch_result, row_template, **_kwargs: [str(manual_path)],
    )

    out = sa.fetch(search_result, tmp_path, selected_rows=[0], fido_client=FakeFido())
    assert out.requested_count == 1
    assert out.failed_count == 0
    assert out.paths == [str(manual_path)]


def test_load_downloaded_map_and_timeseries_metadata(tmp_path: Path):
    a = tmp_path / "a.fits"
    b = tmp_path / "b.fits"
    a.write_text("x", encoding="utf-8")
    b.write_text("y", encoding="utf-8")

    class FakeMap:
        observatory = "SDO"
        instrument = "AIA"
        detector = ""
        wavelength = "193 Angstrom"
        date = "2026-02-10T01:00:00"
        data = [[1.0, 2.0], [3.0, 4.0]]

    class FakeSeq:
        def __init__(self):
            self.maps = [FakeMap(), FakeMap()]

    def fake_map_loader(paths, sequence=False):
        assert sequence is True
        assert len(paths) == 2
        return FakeSeq()

    map_out = sa.load_downloaded(
        [a, b],
        data_kind=sa.DATA_KIND_MAP,
        map_loader=fake_map_loader,
    )
    assert map_out.data_kind == sa.DATA_KIND_MAP
    assert map_out.metadata["n_frames"] == 2
    assert map_out.metadata["instrument"] == "AIA"

    class FakeFrame:
        columns = ["xrsa", "xrsb"]

        def __len__(self):
            return 42

    class FakeTimeSeries:
        @staticmethod
        def to_dataframe():
            return FakeFrame()

    def fake_ts_loader(paths, concatenate=True):
        assert concatenate is True
        assert len(paths) == 2
        return FakeTimeSeries()

    ts_out = sa.load_downloaded(
        [a, b],
        data_kind=sa.DATA_KIND_TIMESERIES,
        timeseries_loader=fake_ts_loader,
    )
    assert ts_out.data_kind == sa.DATA_KIND_TIMESERIES
    assert ts_out.metadata["n_files"] == 2
    assert ts_out.metadata["columns"] == ["xrsa", "xrsb"]
    assert ts_out.metadata["n_samples"] == 42


# ---------------------------------------------------------------------------
# Packaged-Windows download fix: diagnostics, urllib record fallback, asyncio
# ---------------------------------------------------------------------------


def test_configure_fetch_logging_writes_to_file(tmp_path: Path):
    log_path = sa.configure_fetch_logging(tmp_path)
    assert log_path == tmp_path / "sunpy_fetch.log"
    assert sa.get_fetch_log_path() == log_path
    sa.get_sunpy_logger().info("unit-test-marker-line")
    contents = log_path.read_text(encoding="utf-8")
    assert "unit-test-marker-line" in contents


def test_extract_record_urls_finds_http_values():
    record = {"fileid": "aia_1.fits", "url": "https://example.test/aia_1.fits"}
    urls = sa._extract_record_urls(record)
    assert "https://example.test/aia_1.fits" in urls


def test_extract_record_urls_empty_for_non_url_record():
    assert sa._extract_record_urls({"fileid": "aia_1.fits", "Size": "1 MB"}) == []


def test_download_from_row_record_uses_urllib_with_record_url(tmp_path: Path, monkeypatch):
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=[
            sa.SunPySearchRow(
                start=datetime(2026, 2, 10, 1, 0, 0),
                end=datetime(2026, 2, 10, 1, 2, 0),
                source="SDO",
                instrument="AIA",
                provider="VSO",
                fileid="https://example.test/x.fits",
                size="1 MB",
            )
        ],
        raw_response=[[{"fileid": "x", "url": "https://example.test/x.fits"}]],
        row_index_map=[(0, 0)],
    )

    captured = {}

    def fake_dl(url, *, target_dir, retries, timeout_seconds, backoff_seconds, **_kwargs):
        captured["url"] = url
        return str((tmp_path / "x.fits").resolve())

    monkeypatch.setattr(sa, "_download_url_with_retries", fake_dl)
    out = sa._download_from_row_record(search_result, 0, row_template=str(tmp_path / "{file}"))
    assert out == [str((tmp_path / "x.fits").resolve())]
    assert captured["url"] == "https://example.test/x.fits"


def test_fetch_recovers_via_record_url_when_parfive_returns_nothing(tmp_path: Path, monkeypatch):
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=[
            sa.SunPySearchRow(
                start=datetime(2026, 2, 10, 1, 0, 0),
                end=datetime(2026, 2, 10, 1, 2, 0),
                source="SDO",
                instrument="AIA",
                provider="VSO",
                fileid="https://example.test/x.fits",
                size="1 MB",
            )
        ],
        raw_response=[[{"fileid": "x", "url": "https://example.test/x.fits"}]],
        row_index_map=[(0, 0)],
    )

    class FakeFido:
        @staticmethod
        def fetch(_query_slice, path, progress=False, max_conn=None):
            assert path.endswith("{file}")
            return []  # parfive yields nothing and exposes no error URLs

    def fake_dl(url, *, target_dir, retries, timeout_seconds, backoff_seconds, **_kwargs):
        out_path = (tmp_path / "x.fits").resolve()
        out_path.write_text("ok", encoding="utf-8")
        return str(out_path)

    monkeypatch.setattr(sa, "_download_url_with_retries", fake_dl)
    out = sa.fetch(search_result, tmp_path, selected_rows=[0], fido_client=FakeFido())
    assert out.requested_count == 1
    assert out.failed_count == 0
    assert out.paths == [str((tmp_path / "x.fits").resolve())]


def test_registry_spacecraft_and_instrument_helpers():
    spacecraft = sa.registry_spacecraft_list()
    assert spacecraft[0] == "SDO"
    assert "PROBA2" in spacecraft
    assert spacecraft.count("GOES") == 1
    assert sa.registry_instruments_for("GOES") == ["XRS"]
    assert sa.registry_instruments_for("SOHO") == ["LASCO"]
    assert sa.registry_instruments_for("PROBA2") == ["SWAP"]


def test_registry_detectors_and_lookup():
    assert sa.registry_detectors_for("SOHO", "LASCO") == ["C2", "C3"]
    assert sa.registry_detectors_for("SDO", "AIA") == []
    swap = sa.registry_lookup("PROBA2", "SWAP")
    assert swap is not None
    assert swap.data_kind == sa.DATA_KIND_MAP
    assert not swap.supports_wavelength and not swap.supports_satellite
    assert sa.registry_lookup("NOPE", "NOPE") is None


def test_build_attrs_for_new_missions():
    swap_spec = _mk_query(spacecraft="PROBA2", instrument="SWAP")
    swap = sa.build_attrs(swap_spec, attrs_module=_FakeAttrs, units_module=_FakeUnits)
    assert ("Source", "PROBA2") in swap
    assert ("Instrument", "SWAP") in swap
    assert not any(isinstance(x, tuple) and x[0] == "Wavelength" for x in swap)


def test_configure_sunpy_logging_quiets_logger(monkeypatch):
    import logging

    monkeypatch.setattr(sa, "_sunpy_logging_configured", False)
    monkeypatch.delenv("ECALLISTO_SUNPY_LOG_LEVEL", raising=False)
    sa._configure_sunpy_logging()
    assert logging.getLogger("sunpy").level == logging.WARNING


def test_configure_sunpy_logging_respects_env_override(monkeypatch):
    import logging

    monkeypatch.setattr(sa, "_sunpy_logging_configured", False)
    monkeypatch.setenv("ECALLISTO_SUNPY_LOG_LEVEL", "ERROR")
    sa._configure_sunpy_logging()
    assert logging.getLogger("sunpy").level == logging.ERROR


def test_failed_rows_from_errors_parses_indices():
    errors = ["Row 1: boom", "Row 3: timeout", "not a row line", "Row 3: dup"]
    assert sa._failed_rows_from_errors(errors) == [0, 2]


def test_fetch_populates_failed_rows(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size="1 MB",
        )
        for i in range(2)
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "aia_1.fits"}, {"fileid": "aia_2.fits"}]],
        row_index_map=[(0, 0), (0, 1)],
    )

    class FakeFido:
        def fetch(self, query_slice, path):
            if len(query_slice) > 1:
                raise RuntimeError("batch unsupported in fake client")
            fileid = query_slice[0]["fileid"]
            if fileid == "aia_2.fits":
                raise RuntimeError("download failed")
            return [tmp_path / "aia_1.fits"]

    out = sa.fetch(search_result, tmp_path, selected_rows=[0, 1], fido_client=FakeFido())
    assert out.failed_count == 1
    assert out.failed_rows == [1]
    assert out.cancelled is False


def test_fetch_cancel_stops_before_any_download(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size="1 MB",
        )
        for i in range(3)
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": f"aia_{i + 1}.fits"} for i in range(3)]],
        row_index_map=[(0, 0), (0, 1), (0, 2)],
    )

    class FakeFido:
        def __init__(self):
            self.calls = 0

        def fetch(self, *_args, **_kwargs):
            self.calls += 1
            return []

    fido = FakeFido()
    out = sa.fetch(
        search_result,
        tmp_path,
        selected_rows=[0, 1, 2],
        cancel_cb=lambda: True,
        fido_client=fido,
    )
    assert out.cancelled is True
    assert out.paths == []
    assert fido.calls == 0


def test_fetch_high_resolution_defaults_to_row_paced_downloads(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ECALLISTO_SUNPY_HIGH_RES_BATCH_SIZE", raising=False)
    monkeypatch.delenv("ECALLISTO_SUNPY_HIGH_RES_MAX_CONN", raising=False)
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size="12 MB",
        )
        for i in range(5)
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(resolution=1.0),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": row.fileid} for row in rows]],
        row_index_map=[(0, i) for i in range(5)],
    )

    class FakeFido:
        def __init__(self):
            self.calls = []

        def fetch(self, query_slice, path, progress=False, max_conn=None):
            self.calls.append((len(query_slice), max_conn))
            return [tmp_path / f"batch_{len(self.calls)}.fits"]

    fido = FakeFido()
    out = sa.fetch(search_result, tmp_path, selected_rows=list(range(5)), fido_client=fido)

    assert out.failed_count == 0
    assert [call[0] for call in fido.calls] == [1, 1, 1, 1, 1]
    assert fido.calls[0][1] == 8
    assert all(call[1] == 8 for call in fido.calls)


def test_fetch_high_resolution_batch_size_can_be_overridden(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ECALLISTO_SUNPY_HIGH_RES_BATCH_SIZE", "2")
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size="12 MB",
        )
        for i in range(5)
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(resolution=1.0),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": row.fileid} for row in rows]],
        row_index_map=[(0, i) for i in range(5)],
    )

    class FakeFido:
        def __init__(self):
            self.calls = []

        def fetch(self, query_slice, path, progress=False, max_conn=None):
            self.calls.append((len(query_slice), max_conn))
            return [tmp_path / f"batch_{len(self.calls)}.fits"]

    fido = FakeFido()
    out = sa.fetch(search_result, tmp_path, selected_rows=list(range(5)), fido_client=fido)

    assert out.failed_count == 0
    assert [call[0] for call in fido.calls] == [2, 2, 1]


def test_high_resolution_download_defaults_are_stall_resistant(monkeypatch):
    for key in (
        "ECALLISTO_SUNPY_HIGH_RES_BATCH_SIZE",
        "ECALLISTO_SUNPY_HIGH_RES_FETCH_RETRIES",
        "ECALLISTO_SUNPY_HIGH_RES_FETCH_TIMEOUT",
        "ECALLISTO_SUNPY_HIGH_RES_FETCH_READ_TIMEOUT",
        "ECALLISTO_SUNPY_HIGH_RES_MANUAL_FETCH_RETRIES",
        "ECALLISTO_SUNPY_HIGH_RES_MANUAL_FETCH_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)

    assert sa._resolve_fetch_batch_size(priority=True) == 1
    assert sa._resolve_fetch_retry_count(priority=True) == 1
    assert sa._row_fetch_connection_candidates(8, priority=True) == [8, 1]
    assert sa._resolve_fetch_timeout_seconds(priority=True) < sa._resolve_fetch_timeout_seconds(priority=False)
    assert sa._resolve_fetch_read_timeout_seconds(priority=True) < sa._resolve_fetch_read_timeout_seconds(priority=False)
    assert sa._resolve_manual_fetch_retries(priority=True) == 1
    assert sa._resolve_manual_fetch_timeout_seconds(priority=True) < sa._resolve_manual_fetch_timeout_seconds(priority=False)


def test_priority_fetch_runtime_guards_skip_unbounded_default_attempt(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sa, "_build_parfive_downloader", lambda *, max_conn, priority=False: None)

    class FakeResult(list):
        errors = [("https://example.test/aia.fits", "Timeout on reading data from socket")]

    class FakeFido:
        def __init__(self):
            self.calls = []

        def fetch(self, _query_slice, path=None, progress=False, max_conn=None):
            self.calls.append(max_conn)
            return FakeResult()

    fido = FakeFido()
    result = sa._fetch_row_with_runtime_guards(
        fido,
        [{"fileid": "aia_1.fits"}],
        path_template=str(tmp_path / "{file}"),
        max_conn=8,
        priority=True,
        retry_count=1,
        conn_candidates=[8, 1],
    )

    assert isinstance(result, FakeResult)
    assert fido.calls == [8, 1]


def test_fetch_cancel_after_completed_batch_keeps_cached_paths(tmp_path: Path):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 2, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size="12 MB",
        )
        for i in range(4)
    ]
    search_result = sa.SunPySearchResult(
        spec=_mk_query(resolution=1.0),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": row.fileid} for row in rows]],
        row_index_map=[(0, i) for i in range(4)],
    )

    class FakeFido:
        def __init__(self):
            self.calls = 0

        def fetch(self, query_slice, path, progress=False, max_conn=None):
            self.calls += 1
            return [tmp_path / f"done_{self.calls}.fits"]

    fido = FakeFido()
    cancel_state = {"calls": 0}

    def cancel_after_first_batch():
        cancel_state["calls"] += 1
        return fido.calls >= 1

    out = sa.fetch(search_result, tmp_path, selected_rows=list(range(4)), cancel_cb=cancel_after_first_batch, fido_client=fido)

    assert out.cancelled is True
    assert out.paths == [str((tmp_path / "done_1.fits").resolve())]
    assert fido.calls == 1


def test_ensure_event_loop_creates_loop_in_worker_thread():
    result = {}

    def worker():
        sa._ensure_event_loop()
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            result["ok"] = loop is not None and not loop.is_closed()
        except Exception as exc:  # pragma: no cover - failure path
            result["err"] = repr(exc)
        finally:
            try:
                asyncio.get_event_loop_policy().get_event_loop().close()
            except Exception:
                pass

    thread = threading.Thread(target=worker, name="loop-ensure-test")
    thread.start()
    thread.join()
    assert result.get("ok") is True, result


def _byte_progress_rows(sizes):
    rows = [
        sa.SunPySearchRow(
            start=datetime(2026, 2, 10, 1, i, 0),
            end=datetime(2026, 2, 10, 1, i + 1, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid=f"aia_{i + 1}.fits",
            size=size,
        )
        for i, size in enumerate(sizes)
    ]
    raw = [[{"fileid": r.fileid} for r in rows]]
    return sa.SunPySearchResult(
        spec=_mk_query(),
        data_kind=sa.DATA_KIND_MAP,
        rows=rows,
        raw_response=raw,
        row_index_map=[(0, i) for i in range(len(rows))],
    )


def test_expected_bytes_for_rows_sums_known_sizes():
    result = _byte_progress_rows(["1 MB", "3 MB"])
    assert sa._expected_bytes_for_rows(result, [0, 1]) == 4 * 1024**2


def test_expected_bytes_for_rows_extrapolates_unknowns():
    # Two rows, only one reports a size -> scale the known average over both.
    result = _byte_progress_rows(["2 MB", ""])
    assert sa._expected_bytes_for_rows(result, [0, 1]) == 4 * 1024**2


def test_expected_bytes_for_rows_returns_none_without_sizes():
    result = _byte_progress_rows(["", ""])
    assert sa._expected_bytes_for_rows(result, [0, 1]) is None


def test_fetch_emits_byte_progress(tmp_path: Path):
    result = _byte_progress_rows(["1 MB", "1 MB"])

    class FakeFido:
        def fetch(self, query_slice, path):
            if len(query_slice) > 1:
                raise RuntimeError("batch unsupported in fake client")
            fileid = query_slice[0]["fileid"]
            out = tmp_path / fileid
            out.write_bytes(b"x" * 4096)
            return [out]

    snapshots = []
    out = sa.fetch(
        result,
        tmp_path,
        selected_rows=[0, 1],
        fido_client=FakeFido(),
        byte_progress_cb=snapshots.append,
    )
    assert out.failed_count == 0
    # The poller always emits at least a final settled sample carrying the
    # file-count totals folded in by the fetch loop.
    assert snapshots, "expected at least one byte-progress snapshot"
    last = snapshots[-1]
    assert last.files_total == 2
    assert last.bytes_done >= 0


def test_fetch_via_jsoc_orchestrates_export_and_download(tmp_path: Path):
    search_result = _byte_progress_rows(["1 MB", "1 MB"])

    class _FakeReq:
        urls = [
            {"record": "r1", "filename": "a.fits", "url": "http://jsoc/a.fits", "size": 100},
            {"record": "r2", "filename": "b.fits", "url": "http://jsoc/b.fits", "size": 100},
        ]

    class _FakeJsoc:
        def __init__(self):
            self.calls = []

        def export(self, recordset, method=None, protocol=None, email=None, process=None):
            self.calls.append({"recordset": recordset, "method": method, "protocol": protocol})
            return _FakeReq()

    class _FakeDM:
        def __init__(self):
            self.items = None

        def download(self, items, progress_cb=None, cancel_cb=None):
            from src.Backend.download_manager import AggregateProgress, DownloadResult

            self.items = list(items)
            if progress_cb is not None:
                progress_cb(
                    AggregateProgress(
                        files_total=len(self.items),
                        files_done=len(self.items),
                        bytes_done=200,
                        bytes_total=200,
                    )
                )
            return DownloadResult(
                paths=[str(it.dest) for it in self.items], errors=[], cached_count=0, cancelled=False
            )

    jsoc = _FakeJsoc()
    dm = _FakeDM()
    coarse: list = []
    byte_snaps: list = []
    out = sa.fetch_via_jsoc(
        search_result,
        tmp_path,
        selected_rows=[0, 1],
        email="sci@example.org",
        jsoc_client=jsoc,
        download_manager=dm,
        progress_cb=lambda v, t: coarse.append((v, t)),
        byte_progress_cb=byte_snaps.append,
    )
    assert out.requested_count == 2
    assert out.failed_count == 0
    assert len(out.paths) == 2
    assert dm.items is not None and len(dm.items) == 2
    # Fast path used the quick/as-is export.
    assert jsoc.calls[0]["method"] == "url_quick"
    assert jsoc.calls[0]["protocol"] == "as-is"
    # Progress was forwarded both as rich byte snapshots and coarse text.
    assert byte_snaps
    assert coarse and "JSOC" in coarse[-1][1]
