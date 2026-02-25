"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
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
    monkeypatch.setattr(sa, "_download_from_fetch_errors", lambda _fetch_result, row_template: [])

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
        lambda _fetch_result, row_template: [str(manual_path)],
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
