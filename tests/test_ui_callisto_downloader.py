"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


from datetime import date, datetime
import os

import pytest

pytest.importorskip("PySide6")
requests = pytest.importorskip("requests")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QSpinBox
from matplotlib.figure import Figure

from src.Backend.spectral_overview import SpectralOverviewResult
from src.UI.callisto_downloader import (
    BASE_URL,
    CallistoEventCandidate,
    CallistoDownloaderApp,
    EventFetchWorker,
    FetchWorker,
    SpectralOverviewWorker,
    extract_fits_links,
    filter_event_candidates,
    filter_spectral_overview_candidates,
    parse_callisto_archive_filename,
    select_spectral_overview_focus_code,
    utc_archive_dates_for_window,
)


def _app():
    return QApplication.instance() or QApplication([])


def test_fetch_worker_day_url():
    worker = FetchWorker(date(2024, 1, 2), "test")
    assert worker._day_url() == f"{BASE_URL}2024/01/02/"


def test_fetch_worker_uses_current_archive_host():
    assert BASE_URL == "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/"


def test_fetch_worker_check_server_handles_status():
    worker = FetchWorker(date(2024, 1, 2), "test")

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

        def close(self):
            pass

    class FakeClient:
        def head(self, *_args, **_kwargs):
            return FakeResponse(500)

        def get(self, *_args, **_kwargs):
            return FakeResponse(500)

    ok, msg = worker._check_server(FakeClient())
    assert ok is False
    assert "HTTP" in msg


def test_fetch_worker_check_server_falls_back_to_get_when_head_fails():
    worker = FetchWorker(date(2024, 1, 2), "test")

    class FakeResponse:
        status_code = 200

        def close(self):
            pass

    class FakeClient:
        def head(self, *_args, **_kwargs):
            raise requests.ConnectionError("reset by peer")

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    ok, msg = worker._check_server(FakeClient())
    assert ok is True
    assert msg == ""


def test_fetch_worker_run_uses_get_listing_when_head_probe_fails(monkeypatch):
    _app()
    worker = FetchWorker(date(2024, 1, 2), "ALASKA-ANCHORAGE")
    emitted = []

    class FakeResponse:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def head(self, url, **_kwargs):
            assert url == BASE_URL
            raise requests.ConnectionError("reset by peer")

        def get(self, url, **_kwargs):
            if url == BASE_URL:
                return FakeResponse(200)
            if url == f"{BASE_URL}2024/01/02/":
                return FakeResponse(
                    200,
                    """
                    <a href="ALASKA-ANCHORAGE_20240102_000000_01.fit.gz">match</a>
                    <a href="OTHER-STATION_20240102_000000_01.fit.gz">ignore</a>
                    """,
                )
            raise AssertionError(url)

    monkeypatch.setattr("src.UI.callisto_downloader.build_archive_session", lambda: FakeSession())
    worker.finished.connect(lambda payload: emitted.append(payload))

    worker.run()

    assert emitted == [[
        (
            "ALASKA-ANCHORAGE_20240102_000000_01.fit.gz",
            f"{BASE_URL}2024/01/02/ALASKA-ANCHORAGE_20240102_000000_01.fit.gz",
        )
    ]]


def test_extract_fits_links_filters_and_deduplicates():
    html = """
    <html><body>
    <a href="?C=N;O=D">Sort</a>
    <a href="ALASKA-ANCHORAGE_20240102_000000_01.fit.gz">First</a>
    <a href='ALASKA-ANCHORAGE_20240102_001500_01.FIT'>Second</a>
    <a href='ALASKA-ANCHORAGE_20240102_003000_01.fits.gz'>Third</a>
    <a href="ALASKA-ANCHORAGE_20240102_000000_01.fit.gz#fragment">Duplicate</a>
    <a href="/solarradio/data/2002-20yy_Callisto/2024/01/02/readme.txt">Ignore</a>
    </body></html>
    """

    assert extract_fits_links(html) == [
        "ALASKA-ANCHORAGE_20240102_000000_01.fit.gz",
        "ALASKA-ANCHORAGE_20240102_001500_01.FIT",
        "ALASKA-ANCHORAGE_20240102_003000_01.fits.gz",
    ]


@pytest.mark.parametrize(
    ("filename", "station", "observed", "receiver"),
    [
        (
            "ALASKA-ANCHORAGE_20240102_000000_01.fit.gz",
            "ALASKA-ANCHORAGE",
            datetime(2024, 1, 2, 0, 0, 0),
            "01",
        ),
        (
            "GREENLAND_20240102_001500_001600_02.fits",
            "GREENLAND",
            datetime(2024, 1, 2, 0, 15, 0),
            "02",
        ),
        (
            "Malaysia_Banting_20240102_235959_01.fit",
            "Malaysia_Banting",
            datetime(2024, 1, 2, 23, 59, 59),
            "01",
        ),
    ],
)
def test_parse_callisto_archive_filename_variants(filename, station, observed, receiver):
    parsed_station, parsed_observed, parsed_receiver = parse_callisto_archive_filename(filename)

    assert parsed_station == station
    assert parsed_observed == observed
    assert parsed_receiver == receiver


def test_parse_callisto_archive_filename_rejects_missing_receiver():
    with pytest.raises(ValueError):
        parse_callisto_archive_filename("BIR_20240102_000000.fit.gz")


def test_utc_archive_dates_for_cross_midnight_window():
    assert utc_archive_dates_for_window(
        datetime(2024, 1, 1, 23, 50),
        datetime(2024, 1, 2, 0, 10),
    ) == [date(2024, 1, 1), date(2024, 1, 2)]


def test_filter_event_candidates_uses_filename_utc_and_station_match():
    hrefs = [
        "BIR_20240101_235500_01.fit.gz",
        "BIR_20240102_000500_01.fit.gz",
        "BIR_20240102_001500_01.fit.gz",
        "GREENLAND_20240102_000500_01.fit.gz",
        "OTHER_20240102_000500_01.fit.gz",
    ]

    matches = filter_event_candidates(
        hrefs,
        day_url=f"{BASE_URL}2024/01/02/",
        selected_stations=["bir", "GREENLAND"],
        start_dt=datetime(2024, 1, 1, 23, 50),
        stop_dt=datetime(2024, 1, 2, 0, 10),
    )

    assert [item.filename for item in matches] == [
        "BIR_20240101_235500_01.fit.gz",
        "BIR_20240102_000500_01.fit.gz",
        "GREENLAND_20240102_000500_01.fit.gz",
    ]


def test_event_fetch_worker_fetches_each_window_date_once(monkeypatch):
    _app()
    worker = EventFetchWorker(
        datetime(2024, 1, 1, 23, 50),
        datetime(2024, 1, 2, 0, 10),
        ["BIR", "GREENLAND"],
    )
    emitted = []
    called_day_urls = []

    class FakeResponse:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def head(self, url, **_kwargs):
            assert url == BASE_URL
            return FakeResponse(200)

        def get(self, url, **_kwargs):
            if url == f"{BASE_URL}2024/01/01/":
                called_day_urls.append(url)
                return FakeResponse(
                    200,
                    '<a href="BIR_20240101_235500_01.fit.gz">match</a>',
                )
            if url == f"{BASE_URL}2024/01/02/":
                called_day_urls.append(url)
                return FakeResponse(
                    200,
                    """
                    <a href="GREENLAND_20240102_000500_01.fit.gz">match</a>
                    <a href="BIR_20240102_001500_01.fit.gz">outside</a>
                    """,
                )
            raise AssertionError(url)

    monkeypatch.setattr("src.UI.callisto_downloader.build_archive_session", lambda: FakeSession())
    worker.finished.connect(lambda payload: emitted.append(payload))

    worker.run()

    assert called_day_urls == [f"{BASE_URL}2024/01/01/", f"{BASE_URL}2024/01/02/"]
    assert [item.filename for item in emitted[0]["candidates"]] == [
        "BIR_20240101_235500_01.fit.gz",
        "GREENLAND_20240102_000500_01.fit.gz",
    ]
    assert emitted[0]["missing_stations"] == []


def test_downloader_event_tab_defaults_and_empty_state():
    _app()
    dlg = CallistoDownloaderApp()

    assert dlg.tabs.tabText(1) == "Multi-Station Event"
    assert dlg.event_auto_open_chk.isChecked() is True

    dlg.display_event_search_results({"candidates": [], "warnings": [], "missing_stations": ["BIR"]})

    assert dlg.event_results_table.rowCount() == 0
    assert dlg.event_download_btn.isEnabled() is False
    assert "No matching FITS files" in dlg.event_status_label.text()
    dlg.close()


def test_spectral_overview_candidates_require_exact_station_and_select_most_coverage():
    hrefs = [
        "BIR_20240102_000000_02.fit.gz",
        "BIR_20240102_001500_02.fit.gz",
        "BIR_20240102_003000_01.fit.gz",
        "BIR-EXTRA_20240102_000000_01.fit.gz",
        "BIR_20240103_000000_01.fit.gz",
    ]

    candidates = filter_spectral_overview_candidates(
        hrefs,
        day_url=f"{BASE_URL}2024/01/02/",
        station="bir",
        observation_date=date(2024, 1, 2),
    )
    focus_codes, selected_focus, selected = select_spectral_overview_focus_code(candidates)

    assert [candidate.filename for candidate in candidates] == hrefs[:3]
    assert focus_codes == ["01", "02"]
    assert selected_focus == "02"
    assert [candidate.receiver_id for candidate in selected] == ["02", "02"]


def test_spectral_overview_focus_tie_breaks_lexically():
    candidates = [
        CallistoEventCandidate("BIR", datetime(2024, 1, 2, 0, 0), "a.fit", "https://x/a.fit", "02"),
        CallistoEventCandidate("BIR", datetime(2024, 1, 2, 0, 15), "b.fit", "https://x/b.fit", "01"),
    ]

    _codes, selected_focus, selected = select_spectral_overview_focus_code(candidates)

    assert selected_focus == "01"
    assert selected[0].receiver_id == "01"


def test_downloader_spectral_overview_tab_defaults():
    _app()
    dlg = CallistoDownloaderApp()

    assert dlg.tabs.tabText(2) == "Spectral Overview"
    assert dlg.overview_date_edit.displayFormat() == "yyyy-MM-dd"
    assert dlg.overview_focus_combo.currentData() == ""
    assert "All available focus codes" in dlg.overview_focus_combo.currentText()
    assert dlg.overview_generate_btn.isEnabled() is True
    assert dlg.overview_cancel_btn.isEnabled() is False
    assert dlg.overview_export_btn.isEnabled() is False
    assert dlg.overview_plot_scroll.widgetResizable() is True
    assert dlg.overview_canvas.minimumWidth() == 0
    assert dlg.overview_canvas.minimumHeight() == 0
    dlg._set_overview_running(True)
    assert dlg.tabs.isTabEnabled(0) is False
    assert dlg.tabs.isTabEnabled(1) is False
    dlg._set_overview_running(False)
    dlg.close()


def test_spectral_overview_worker_continues_after_download_failure_and_cleans_temp(monkeypatch):
    _app()
    emitted = []
    errors = []
    focus = []
    captured_paths = []
    day_url = f"{BASE_URL}2024/01/02/"
    good_url = f"{day_url}BIR_20240102_000000_01.fit.gz"
    bad_url = f"{day_url}BIR_20240102_001500_01.fit.gz"

    class FakeResponse:
        def __init__(self, *, status_code=200, text="", data=b"fit"):
            self.status_code = status_code
            self.text = text
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

        def iter_content(self, chunk_size):
            yield self.data

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def head(self, url, **_kwargs):
            assert url == BASE_URL
            return FakeResponse()

        def get(self, url, **_kwargs):
            if url == day_url:
                return FakeResponse(
                    text=(
                        '<a href="BIR_20240102_000000_01.fit.gz">good</a>'
                        '<a href="BIR_20240102_001500_01.fit.gz">bad</a>'
                    )
                )
            if url == good_url:
                return FakeResponse(data=b"valid")
            if url == bad_url:
                raise requests.ConnectionError("download failed")
            raise AssertionError(url)

    def fake_build(sources, **_kwargs):
        captured_paths.extend(source.path for source in sources)
        assert all(os.path.exists(source.path) for source in sources)
        return SpectralOverviewResult(
            station="BIR",
            observation_date=date(2024, 1, 2),
            focus_code="01",
            segments=(),
            total_sources=len(sources),
            loaded_sources=len(sources),
            coverage_seconds=1.0,
        )

    monkeypatch.setattr("src.UI.callisto_downloader.build_archive_session", lambda: FakeSession())
    monkeypatch.setattr("src.UI.callisto_downloader.build_spectral_overview", fake_build)
    worker = SpectralOverviewWorker(date(2024, 1, 2), "BIR")
    worker.finished.connect(emitted.append)
    worker.failed.connect(errors.append)
    worker.focusCodesDiscovered.connect(lambda codes, selected: focus.append((list(codes), selected)))

    worker.run()

    assert errors == []
    assert focus == [(["01"], "")]
    result = emitted[0]["results"][0]
    assert result.loaded_sources == 1
    assert result.total_sources == 2
    assert any("download failed" in warning for warning in result.warnings)
    assert captured_paths and all(not os.path.exists(path) for path in captured_paths)


def test_spectral_overview_worker_generates_every_available_focus_code(monkeypatch):
    _app()
    emitted = []
    errors = []
    built_focus_codes = []
    day_url = f"{BASE_URL}2024/01/02/"

    class FakeResponse:
        def __init__(self, *, text="", data=b"fit"):
            self.status_code = 200
            self.text = text
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield self.data

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def head(self, *_args, **_kwargs):
            return FakeResponse()

        def get(self, url, **_kwargs):
            if url == day_url:
                return FakeResponse(
                    text=(
                        '<a href="BIR_20240102_000000_01.fit.gz">one</a>'
                        '<a href="BIR_20240102_001500_02.fit.gz">two</a>'
                    )
                )
            return FakeResponse()

    def fake_build(sources, **_kwargs):
        focus_code = sources[0].focus_code
        built_focus_codes.append(focus_code)
        return SpectralOverviewResult(
            station="BIR",
            observation_date=date(2024, 1, 2),
            focus_code=focus_code,
            segments=(),
            total_sources=len(sources),
            loaded_sources=len(sources),
            coverage_seconds=1.0,
        )

    monkeypatch.setattr("src.UI.callisto_downloader.build_archive_session", lambda: FakeSession())
    monkeypatch.setattr("src.UI.callisto_downloader.build_spectral_overview", fake_build)
    worker = SpectralOverviewWorker(date(2024, 1, 2), "BIR")
    worker.finished.connect(emitted.append)
    worker.failed.connect(errors.append)

    worker.run()

    assert errors == []
    assert built_focus_codes == ["01", "02"]
    assert [result.focus_code for result in emitted[0]["results"]] == ["01", "02"]


def test_spectral_overview_worker_emits_cancelled_before_start():
    _app()
    cancelled = []
    worker = SpectralOverviewWorker(date(2024, 1, 2), "BIR")
    worker.cancelled.connect(lambda: cancelled.append(True))

    worker.request_cancel()
    worker.run()

    assert cancelled == [True]


def test_downloader_displays_each_focus_code_in_screen_fitted_preview_tabs(monkeypatch):
    app = _app()
    dlg = CallistoDownloaderApp()
    results = [
        SpectralOverviewResult("BIR", date(2024, 1, 2), focus, (), 1, 1, 1.0)
        for focus in ("01", "02")
    ]
    monkeypatch.setattr("src.UI.callisto_downloader.render_spectral_overview_figure", lambda _result: Figure())

    dlg._on_spectral_overview_finished({"results": results, "errors": []})

    available = (dlg.screen() or app.primaryScreen()).availableGeometry()
    assert dlg.width() <= available.width()
    assert dlg.height() <= available.height()
    assert dlg.minimumWidth() <= available.width()
    assert dlg.minimumHeight() <= available.height()
    assert dlg.overview_preview_tabs.count() == 2
    assert [dlg.overview_preview_tabs.tabText(i) for i in range(2)] == ["Focus 01", "Focus 02"]
    for index, canvas in enumerate(dlg._overview_canvases.values()):
        scroll = dlg.overview_preview_tabs.widget(index)
        assert scroll.widgetResizable() is True
        assert canvas.minimumWidth() == 0
        assert canvas.minimumHeight() == 0
    dlg.overview_preview_tabs.setCurrentIndex(1)
    assert dlg._overview_result.focus_code == "02"
    dlg.close()


@pytest.mark.parametrize(
    ("extension", "expected_format"),
    [("png", "png"), ("pdf", "pdf"), ("svg", "svg"), ("eps", "eps"), ("tiff", "tiff")],
)
def test_spectral_overview_export_uses_original_figure(monkeypatch, tmp_path, extension, expected_format):
    _app()
    dlg = CallistoDownloaderApp()
    saved = []

    class FakeFigure:
        def savefig(self, path, **kwargs):
            saved.append((path, kwargs))

        def clear(self):
            pass

    dlg._overview_figure = FakeFigure()
    dlg._overview_result = SpectralOverviewResult(
        station="BIR",
        observation_date=date(2024, 1, 2),
        focus_code="01",
        segments=(),
        total_sources=1,
        loaded_sources=1,
        coverage_seconds=1.0,
    )
    out_path = tmp_path / f"overview.{extension}"
    monkeypatch.setattr(
        "src.UI.callisto_downloader.pick_export_path",
        lambda *_args, **_kwargs: (str(out_path), extension),
    )
    monkeypatch.setattr("src.UI.callisto_downloader.QMessageBox.information", lambda *_args, **_kwargs: None)

    dlg.export_spectral_overview()

    assert saved[0][0] == str(out_path)
    assert saved[0][1]["dpi"] == 300
    assert saved[0][1]["format"] == expected_format
    dlg.close()


def test_single_station_compare_emits_selected_urls_and_closes():
    _app()
    dlg = CallistoDownloaderApp()
    emitted = []
    dlg.comparison_request.connect(lambda urls: emitted.append(urls))
    dlg.display_fetched_files([
        ("BIR_20240102_000000_01.fit.gz", "https://example.test/bir.fit.gz"),
        ("GREENLAND_20240102_000000_01.fit.gz", "https://example.test/greenland.fit.gz"),
    ])
    for row in range(dlg.file_list.count()):
        dlg.file_list.item(row).setCheckState(Qt.Checked)

    dlg.handle_compare()

    assert emitted == [[
        "https://example.test/bir.fit.gz",
        "https://example.test/greenland.fit.gz",
    ]]
    assert dlg.result() == QDialog.Accepted


def test_event_results_default_to_checked_and_auto_open_emits(monkeypatch, tmp_path):
    _app()
    dlg = CallistoDownloaderApp()
    monkeypatch.setattr("src.UI.callisto_downloader.QMessageBox.information", lambda *_args, **_kwargs: None)
    emitted = []
    dlg.comparison_request.connect(lambda paths: emitted.append(paths))

    candidate = CallistoEventCandidate(
        station="BIR",
        observed_at_utc=datetime(2024, 1, 2, 0, 0),
        filename="BIR_20240102_000000_01.fit.gz",
        url="https://example.test/BIR_20240102_000000_01.fit.gz",
        receiver_id="01",
    )
    dlg._append_event_candidate_row(candidate)

    assert dlg._checked_event_candidates() == [candidate]

    path_a = str(tmp_path / "a.fit.gz")
    path_b = str(tmp_path / "b.fit.gz")
    dlg._event_download_total = 2
    dlg._event_download_success_paths = [path_a, path_b]
    dlg._event_download_failures = []
    dlg.finish_event_download()

    assert emitted == [[path_a, path_b]]
    dlg.close()


def test_event_compare_emits_candidate_urls_and_closes():
    _app()
    dlg = CallistoDownloaderApp()
    emitted = []
    dlg.comparison_request.connect(lambda urls: emitted.append(urls))

    first = CallistoEventCandidate(
        station="BIR",
        observed_at_utc=datetime(2024, 1, 2, 0, 0),
        filename="BIR_20240102_000000_01.fit.gz",
        url="https://example.test/bir.fit.gz",
        receiver_id="01",
    )
    second = CallistoEventCandidate(
        station="GREENLAND",
        observed_at_utc=datetime(2024, 1, 2, 0, 0),
        filename="GREENLAND_20240102_000000_01.fit.gz",
        url="https://example.test/greenland.fit.gz",
        receiver_id="01",
    )
    dlg._append_event_candidate_row(first)
    dlg._append_event_candidate_row(second)

    dlg.compare_selected_event_files()

    assert emitted == [["https://example.test/bir.fit.gz", "https://example.test/greenland.fit.gz"]]
    assert dlg.result() == QDialog.Accepted


def test_downloader_date_edit_shows_full_year():
    _app()
    dlg = CallistoDownloaderApp()
    year_edit = dlg.calendar_popup.findChild(QSpinBox, "qt_calendar_yearedit")

    assert dlg.date_edit.displayFormat() == "yyyy-MM-dd"
    assert dlg.date_edit.minimumWidth() >= 140
    assert year_edit is not None
    assert year_edit.minimumWidth() == 96
    assert year_edit.maximumWidth() == 96
    assert year_edit.minimumHeight() == 34

    dlg.close()
