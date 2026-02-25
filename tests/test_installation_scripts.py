"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


from pathlib import Path

from src.Installation import install_requirements


ROOT = Path(__file__).resolve().parents[1]


def test_install_requirements_packages_list():
    assert "PySide6" in install_requirements.packages
    assert "matplotlib" in install_requirements.packages
    assert "setuptools" in install_requirements.build_packages


def test_requirements_files_exist():
    assert install_requirements.RUNTIME_REQUIREMENTS.exists()
    assert install_requirements.BUILD_REQUIREMENTS.exists()


def test_specs_include_qtmultimedia_and_qtwebengine():
    spec_paths = [
        ROOT / "src" / "Installation" / "FITS_Analyzer.spec",
        ROOT / "src" / "Installation" / "FITS_Analyzer_linux.spec",
        ROOT / "src" / "Installation" / "FITS_Analyzer_win.spec",
        ROOT / "src" / "Installation" / "setup.py",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "PySide6.QtMultimedia" in text
        assert "PySide6.QtMultimediaWidgets" in text
        assert "PySide6.QtWebEngineCore" in text
        assert "PySide6.QtWebEngineWidgets" in text


def test_specs_include_cme_ipc_modules():
    spec_paths = [
        ROOT / "src" / "Installation" / "FITS_Analyzer.spec",
        ROOT / "src" / "Installation" / "FITS_Analyzer_linux.spec",
        ROOT / "src" / "Installation" / "FITS_Analyzer_win.spec",
        ROOT / "src" / "Installation" / "setup.py",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "src.UI.utils.cme_helper_client" in text
        assert "src.UI.utils.cme_ipc_protocol" in text


def test_build_scripts_install_from_pinned_requirements():
    windows_script = (ROOT / "src" / "Installation" / "build_windows_installer.ps1").read_text(
        encoding="utf-8"
    )
    linux_script = (ROOT / "src" / "Installation" / "build_deb_linux.sh").read_text(
        encoding="utf-8"
    )

    assert "requirements-build.txt" in windows_script
    assert "requirements-runtime.txt" in windows_script
    assert "requirements-build.txt" in linux_script
    assert "requirements-runtime.txt" in linux_script


def test_smoke_script_exists_and_checks_helper_mode():
    text = (ROOT / "src" / "Installation" / "smoke_test_packaged.py").read_text(
        encoding="utf-8"
    )
    assert "--mode=cme-helper" in text
    assert "KNOWN_MOVIE_URL" in text


def test_specs_include_sunpy_modules_and_hook_path():
    spec_paths = [
        ROOT / "src" / "Installation" / "FITS_Analyzer.spec",
        ROOT / "src" / "Installation" / "FITS_Analyzer_linux.spec",
        ROOT / "src" / "Installation" / "FITS_Analyzer_win.spec",
        ROOT / "src" / "Installation" / "setup.py",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "sunpy" in text
        assert "src.UI.sunpy_solar_viewer" in text

    hook_path = ROOT / "src" / "Installation" / "pyinstaller_hooks" / "hook-sunpy.py"
    assert hook_path.exists()


def test_runtime_requirements_include_sunpy_network_stack():
    text = (ROOT / "src" / "Installation" / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert "sunpy[map,net,timeseries]" in text
    assert "lxml==" in text
    assert "drms==" in text
    assert "zeep==" in text
    assert "reproject==" in text
    assert "mpl-animators==" in text
