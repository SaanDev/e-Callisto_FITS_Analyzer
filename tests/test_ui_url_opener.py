"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import pytest

pytest.importorskip("PySide6")

from src.UI.utils import url_opener


def test_open_url_robust_uses_qt_desktop(monkeypatch):
    monkeypatch.setattr("src.UI.utils.url_opener.QDesktopServices.openUrl", lambda *_a, **_k: True)
    result = url_opener.open_url_robust("https://example.com")
    assert result.opened is True
    assert result.method == "qt_desktop"


def test_open_url_robust_falls_back_to_webbrowser(monkeypatch):
    monkeypatch.setattr("src.UI.utils.url_opener.QDesktopServices.openUrl", lambda *_a, **_k: False)
    monkeypatch.setattr("src.UI.utils.url_opener.webbrowser.open", lambda *_a, **_k: True)

    result = url_opener.open_url_robust("https://example.com")
    assert result.opened is True
    assert result.method == "python_webbrowser"


def test_open_url_robust_falls_back_to_os_command(monkeypatch):
    calls = []

    monkeypatch.setattr("src.UI.utils.url_opener.QDesktopServices.openUrl", lambda *_a, **_k: False)
    monkeypatch.setattr("src.UI.utils.url_opener.webbrowser.open", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "src.UI.utils.url_opener._platform_open_command",
        lambda *_a, **_k: ["xdg-open", "https://example.com"],
    )

    def fake_popen(command, **_kwargs):
        calls.append(command)
        return object()

    monkeypatch.setattr("src.UI.utils.url_opener.subprocess.Popen", fake_popen)
    result = url_opener.open_url_robust("https://example.com")

    assert result.opened is True
    assert result.method == "os_command"
    assert calls


def test_open_url_robust_reports_error_when_all_methods_fail(monkeypatch):
    monkeypatch.setattr("src.UI.utils.url_opener.QDesktopServices.openUrl", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "src.UI.utils.url_opener.webbrowser.open",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("webbrowser down")),
    )
    monkeypatch.setattr("src.UI.utils.url_opener._platform_open_command", lambda *_a, **_k: [])

    result = url_opener.open_url_robust("https://example.com")
    assert result.opened is False
    assert "webbrowser down" in result.error

