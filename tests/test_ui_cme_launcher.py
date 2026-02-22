"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import sys

import pytest

pytest.importorskip("PySide6")

from src.UI.utils import cme_launcher


def test_build_cme_helper_command_source_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    command = cme_launcher.build_cme_helper_command(
        "https://example.com/movie.mpg",
        "Movie Title",
    )

    assert command[0] == sys.executable
    assert "--mode=cme-helper" in command
    assert "--movie-url" in command
    assert "main.py" in " ".join(command)


def test_build_cme_helper_command_frozen_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("src.UI.utils.cme_launcher._frozen_executable_path", lambda: "/tmp/app")
    command = cme_launcher.build_cme_helper_command("https://example.com/movie.mpg")

    assert command[0] == "/tmp/app"
    assert command[1:3] == ["--mode=cme-helper", "--movie-url"]


def test_build_cme_helper_command_includes_direct_url(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    command = cme_launcher.build_cme_helper_command(
        "https://example.com/view",
        "Title",
        direct_movie_url="https://example.com/raw.mpg",
    )
    assert "--movie-direct-url" in command
    assert "https://example.com/raw.mpg" in command


def test_launch_cme_helper_success(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    class FakeProc:
        pid = 3210

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr("src.UI.utils.cme_launcher.subprocess.Popen", lambda *_a, **_k: FakeProc())
    result = cme_launcher.launch_cme_helper(
        "https://example.com/movie.mpg",
        "Test",
        startup_timeout=0,
    )

    assert result.launched is True
    assert result.pid == 3210


def test_launch_cme_helper_quick_exit(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    class FakeProc:
        pid = 444

        @staticmethod
        def poll():
            return 9

    monkeypatch.setattr("src.UI.utils.cme_launcher.subprocess.Popen", lambda *_a, **_k: FakeProc())
    monkeypatch.setattr("src.UI.utils.cme_launcher.time.sleep", lambda *_a, **_k: None)

    result = cme_launcher.launch_cme_helper(
        "https://example.com/movie.mpg",
        startup_timeout=0.1,
    )

    assert result.launched is False
    assert result.exit_code == 9
    assert "exited immediately" in result.error
