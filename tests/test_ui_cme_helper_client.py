"""
e-CALLISTO FITS Analyzer
Version 2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication

from src.UI.utils.cme_helper_client import CMEHelperClient


def _qt_app():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def test_open_movie_lazy_start_success(monkeypatch):
    _qt_app()
    client = CMEHelperClient()

    monkeypatch.setattr(client, "ensure_started", lambda: (True, ""))

    calls = []

    def fake_send_request(message_type, payload, expect_types, timeout_ms):
        calls.append((message_type, payload, expect_types, timeout_ms))
        return True, {"opened": True}, ""

    monkeypatch.setattr(client, "_send_request", fake_send_request)
    result = client.open_movie("https://example.com/view", raw_url="https://example.com/raw", title="CME")

    assert result.ok is True
    assert result.method == "ipc"
    assert calls and calls[0][0] == "open_movie"


def test_open_movie_restart_and_replay_once(monkeypatch):
    _qt_app()
    client = CMEHelperClient()

    monkeypatch.setattr(client, "ensure_started", lambda: (True, ""))
    monkeypatch.setattr(client, "_send_request", lambda *_args, **_kwargs: (False, {}, "socket timeout"))
    monkeypatch.setattr(client, "_restart_and_replay", lambda *_args, **_kwargs: (True, ""))

    result = client.open_movie("https://example.com/view")
    assert result.ok is True
    assert result.restart_attempted is True
    assert result.method == "ipc_restart_replay"


def test_open_movie_reports_error_after_retry(monkeypatch):
    _qt_app()
    client = CMEHelperClient()

    monkeypatch.setattr(client, "ensure_started", lambda: (False, "spawn failed"))
    monkeypatch.setattr(
        client,
        "_restart_and_replay",
        lambda *_args, **_kwargs: (False, "restart replay failed"),
    )

    result = client.open_movie("https://example.com/view")
    assert result.ok is False
    assert result.restart_attempted is True
    assert "restart replay failed" in result.error


def test_helper_command_uses_ipc_name(monkeypatch):
    _qt_app()
    client = CMEHelperClient(ipc_name="my_socket_name")

    monkeypatch.setattr("src.UI.utils.cme_helper_client.sys.frozen", False, raising=False)
    command = client._helper_command()
    assert "--ipc-name" in command
    assert "my_socket_name" in command
