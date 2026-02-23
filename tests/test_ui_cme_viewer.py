"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("bs4")

from src.UI import soho_lasco_viewer as cme_viewer
from src.UI.utils.cme_helper_client import HelperOpenResult
from src.UI.utils.cme_launcher import LaunchResult
from src.UI.utils.url_opener import OpenResult


def _sample_catalog_html(with_link: bool = True) -> str:
    link_cell = (
        '<td><a href="/CME_list/nrl_mpg/2025_11/251103_c2.mpg">movie</a></td>'
        if with_link
        else "<td></td>"
    )
    return f"""
<html><body>
<table>
  <tr><th>First C2 Appearance</th></tr>
  <tr>
    <td>2025/11/03</td>
    <td>03:12:05</td>
    <td>120</td>
    <td>090</td>
    <td>450</td>
    <td>-</td>
    <td>-</td>
    <td>1.2</td>
    <td>2.3e15</td>
    <td>9.1e30</td>
    <td>111</td>
    {link_cell}
    <td>none</td>
  </tr>
</table>
</body></html>
"""


def test_parse_catalog_rows_prefers_catalog_link():
    rows = cme_viewer.parse_catalog_rows(_sample_catalog_html(with_link=True), "2025", "11", "03")
    assert len(rows) == 1

    row = rows[0]
    assert row.catalog_movie_url == "https://cdaw.gsfc.nasa.gov/CME_list/nrl_mpg/2025_11/251103_c2.mpg"
    assert "make_javamovie.php" in row.fallback_movie_url
    assert row.preferred_movie_url == row.catalog_movie_url
    assert row.interactive_movie_url == row.fallback_movie_url


def test_parse_catalog_rows_falls_back_when_link_missing():
    rows = cme_viewer.parse_catalog_rows(_sample_catalog_html(with_link=False), "2025", "11", "03")
    assert len(rows) == 1

    row = rows[0]
    assert row.catalog_movie_url == ""
    assert "make_javamovie.php" in row.fallback_movie_url
    assert row.preferred_movie_url == row.fallback_movie_url
    assert row.interactive_movie_url == row.fallback_movie_url


def test_fetch_catalog_outcome_handles_unpublished_month(monkeypatch):
    class FakeResponse:
        status_code = 404
        text = ""

    def fake_get(*_args, **_kwargs):
        return FakeResponse()

    monkeypatch.setattr("src.UI.soho_lasco_viewer.requests.get", fake_get)
    outcome = cme_viewer.fetch_catalog_outcome("2026", "02", "01", max_attempts=1)

    assert outcome.status == cme_viewer.STATUS_MONTH_UNPUBLISHED
    assert "not published yet" in outcome.message.lower()


def test_launch_movie_with_fallback_when_helper_fails(monkeypatch):
    helper_result = LaunchResult(
        launched=False,
        command=["python", "main.py", "--mode=cme-helper"],
        exit_code=7,
        error="helper crashed",
    )
    fallback_result = OpenResult(opened=True, method="python_webbrowser")

    monkeypatch.setattr("src.UI.soho_lasco_viewer.launch_cme_helper", lambda *_a, **_k: helper_result)
    monkeypatch.setattr("src.UI.soho_lasco_viewer.open_url_robust", lambda *_a, **_k: fallback_result)

    outcome = cme_viewer.launch_movie_with_fallback("https://example.com/movie.mpg", "Test CME")
    assert outcome.opened is True
    assert outcome.method.startswith("browser:")
    assert "Helper could not open the movie" in outcome.message


def test_launch_movie_with_fallback_reports_error_when_all_openers_fail(monkeypatch):
    helper_result = LaunchResult(
        launched=False,
        command=["python", "main.py", "--mode=cme-helper"],
        exit_code=7,
        error="helper crashed",
    )
    fallback_result = OpenResult(opened=False, method="none", error="all failed")

    monkeypatch.setattr("src.UI.soho_lasco_viewer.launch_cme_helper", lambda *_a, **_k: helper_result)
    monkeypatch.setattr("src.UI.soho_lasco_viewer.open_url_robust", lambda *_a, **_k: fallback_result)

    outcome = cme_viewer.launch_movie_with_fallback("https://example.com/movie.mpg", "Test CME")
    assert outcome.opened is False
    assert outcome.method == "failed"
    assert "Fallback error" in outcome.message


def test_launch_movie_with_fallback_uses_persistent_client_when_available(monkeypatch):
    class FakeHelperClient:
        ipc_name = "socket-name"

        @staticmethod
        def helper_pid():
            return 9991

        @staticmethod
        def open_movie(_interactive_url, raw_url="", title=""):
            assert raw_url == "https://example.com/raw.mpg"
            assert "Test CME" in title
            return HelperOpenResult(ok=True, method="ipc", restart_attempted=False)

    monkeypatch.setattr("src.UI.soho_lasco_viewer._use_legacy_helper_mode", lambda: False)
    outcome = cme_viewer.launch_movie_with_fallback(
        "https://example.com/interactive",
        "Test CME",
        direct_movie_url="https://example.com/raw.mpg",
        helper_client=FakeHelperClient(),
    )

    assert outcome.opened is True
    assert outcome.method == "helper_ipc"
