"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from pathlib import Path

from src.UI.runtime_paths import find_startup_logo_path, project_base_path, runtime_base_paths


def test_project_base_path_prefers_meipass_for_frozen_pyinstaller(tmp_path: Path):
    meipass = tmp_path / "_internal"
    meipass.mkdir()
    exe = tmp_path / "dist" / "e-Callisto FITS Analyzer.exe"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")

    result = project_base_path(
        module_file="/repo/src/UI/main.py",
        executable=str(exe),
        frozen=True,
        meipass=str(meipass),
        platform_name="win32",
    )

    assert result == str(meipass.resolve())


def test_project_base_path_uses_executable_dir_for_frozen_windows_without_meipass(tmp_path: Path):
    exe = tmp_path / "dist" / "e-Callisto FITS Analyzer.exe"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")

    result = project_base_path(
        module_file="/repo/src/UI/main.py",
        executable=str(exe),
        frozen=True,
        meipass="",
        platform_name="win32",
    )

    assert result == str(exe.parent.resolve())


def test_find_startup_logo_path_checks_runtime_locations(tmp_path: Path):
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir()
    logo = exe_dir / "assets" / "FITS_analyzer.png"
    logo.parent.mkdir(parents=True)
    logo.write_text("png", encoding="utf-8")

    result = find_startup_logo_path(
        base_path=str(tmp_path / "missing-resources"),
        executable=str(exe_dir / "e-Callisto FITS Analyzer.exe"),
        meipass="",
    )

    assert result == str(logo.resolve())


def test_runtime_base_paths_deduplicates_equivalent_entries(tmp_path: Path):
    base = tmp_path / "bundle"
    base.mkdir()

    result = runtime_base_paths(
        base_path=str(base),
        executable=str(base / "app.exe"),
        meipass=str(base),
    )

    assert result == [str(base.resolve())]
