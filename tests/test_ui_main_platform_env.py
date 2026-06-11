"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import pytest
import builtins
import subprocess
import sys

pytest.importorskip("PySide6")

import src.UI.main as main_module


def _linux_wayland_env(monkeypatch):
    monkeypatch.setattr(main_module.sys, "platform", "linux")
    monkeypatch.setattr(main_module.sys, "argv", ["main.py"])
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("CALLISTO_ALLOW_QT_WAYLAND", raising=False)
    monkeypatch.delenv("CALLISTO_PREFER_QT_XCB", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")


def test_linux_wayland_does_not_override_platform_by_default(monkeypatch):
    _linux_wayland_env(monkeypatch)

    main_module._configure_platform_env()

    assert "QT_QPA_PLATFORM" not in main_module.os.environ


def test_linux_wayland_can_prefer_xcb_when_requested(monkeypatch):
    _linux_wayland_env(monkeypatch)
    monkeypatch.setenv("CALLISTO_PREFER_QT_XCB", "1")

    main_module._configure_platform_env()

    assert main_module.os.environ["QT_QPA_PLATFORM"] == "xcb;wayland"


def test_linux_wayland_respects_explicit_wayland_platform(monkeypatch):
    _linux_wayland_env(monkeypatch)
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
    monkeypatch.setenv("CALLISTO_PREFER_QT_XCB", "1")

    main_module._configure_platform_env()

    assert main_module.os.environ["QT_QPA_PLATFORM"] == "wayland"


def test_linux_wayland_respects_explicit_non_wayland_qpa_platform(monkeypatch):
    _linux_wayland_env(monkeypatch)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    main_module._configure_platform_env()

    assert main_module.os.environ["QT_QPA_PLATFORM"] == "offscreen"


def test_linux_wayland_can_be_opted_back_in(monkeypatch):
    _linux_wayland_env(monkeypatch)
    monkeypatch.setenv("CALLISTO_ALLOW_QT_WAYLAND", "1")

    main_module._configure_platform_env()

    assert "QT_QPA_PLATFORM" not in main_module.os.environ


def test_windows_development_runtime_preflight_reports_hashlib_failure(monkeypatch):
    monkeypatch.setattr(main_module.sys, "platform", "win32")
    monkeypatch.delattr(main_module.sys, "frozen", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "hashlib":
            raise ImportError("broken _hashlib")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="repair_windows_venv.ps1"):
        main_module._preflight_windows_development_runtime()


def test_windows_development_runtime_preflight_warms_plotting_imports(monkeypatch):
    monkeypatch.setattr(main_module.sys, "platform", "win32")
    monkeypatch.delattr(main_module.sys, "frozen", raising=False)
    imported = []

    monkeypatch.setattr(
        main_module.importlib,
        "import_module",
        lambda name: imported.append(name),
    )

    main_module._preflight_windows_development_runtime()

    assert "matplotlib.backends.backend_qtagg" in imported
    assert "matplotlib.widgets" in imported


def test_main_window_does_not_eagerly_import_pyplot():
    code = (
        "import sys; "
        "import src.UI.main_window; "
        "assert 'matplotlib.pyplot' not in sys.modules"
    )
    subprocess.check_call([sys.executable, "-c", code])
