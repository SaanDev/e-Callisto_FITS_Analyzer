"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


from pathlib import Path

from scripts import install_requirements


ROOT = Path(__file__).resolve().parents[2]


def test_install_requirements_packages_list():
    assert "PySide6" in install_requirements.packages
    assert "shiboken6" in install_requirements.packages
    assert "matplotlib" in install_requirements.packages
    assert "reportlab" in install_requirements.packages
    assert "setuptools" in install_requirements.build_packages


def test_requirements_files_exist():
    assert install_requirements.RUNTIME_REQUIREMENTS.exists()
    assert install_requirements.BUILD_REQUIREMENTS.exists()


def test_specs_include_qtmultimedia_and_qtwebengine():
    spec_paths = [
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_linux.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_win.spec",
        ROOT / "packaging" / "macos" / "setup.py",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "PySide6.QtMultimedia" in text
        assert "PySide6.QtMultimediaWidgets" in text
        assert "PySide6.QtWebEngineCore" in text
        assert "PySide6.QtWebEngineWidgets" in text


def test_specs_include_cme_ipc_modules():
    spec_paths = [
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_linux.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_win.spec",
        ROOT / "packaging" / "macos" / "setup.py",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "src.ui.cme.cme_helper_client" in text
        assert "src.ui.cme.cme_ipc_protocol" in text


def test_build_scripts_install_from_pinned_requirements():
    windows_script = (ROOT / "packaging" / "windows" / "build_windows_installer.ps1").read_text(
        encoding="utf-8"
    )
    linux_script = (ROOT / "packaging" / "linux" / "build_deb_linux.sh").read_text(
        encoding="utf-8"
    )

    assert "requirements-build.txt" in windows_script
    assert "requirements-runtime.txt" in windows_script
    assert "requirements-build.txt" in linux_script
    assert "requirements-runtime.txt" in linux_script
    assert "ensurepip" in linux_script
    assert "python3-venv python3-pip" in linux_script
    assert "PYTHON_BIN" in linux_script
    assert "https://pypi.org/simple" in linux_script
    assert "BUILD_VENV" in linux_script
    assert 'rm -rf "$BUILD_VENV"' in linux_script
    assert "sudo apt install -y ./$(basename" in linux_script


def test_install_requirements_bootstraps_pip_or_shows_linux_hint():
    text = (ROOT / "scripts" / "install_requirements.py").read_text(
        encoding="utf-8"
    )
    assert "ensurepip" in text
    assert "python3-venv python3-pip" in text
    assert "validate_qtcore_import" in text
    assert "warm_runtime_imports" in text
    assert "matplotlib.pyplot" in text
    assert "repair_windows_venv.ps1" in text
    assert "Microsoft Visual C++ 2015-2022" in text
    assert "Redistributable (x64)" in text


def test_windows_venv_repair_script_exists_and_uses_rmdir_fallback():
    script = ROOT / "packaging" / "windows" / "repair_windows_venv.ps1"
    assert script.exists()

    text = script.read_text(encoding="utf-8")
    assert "cmd.exe" in text
    assert "rmdir /s /q" in text
    assert "install_requirements.py" in text
    assert 'pyExe -3 -m venv' not in text
    assert "will not silently use another Python version" in text
    assert "throw (" not in text


def test_smoke_script_exists_and_checks_helper_mode():
    text = (ROOT / "scripts" / "smoke_test_packaged.py").read_text(
        encoding="utf-8"
    )
    assert "--mode=cme-helper" in text
    assert "KNOWN_MOVIE_URL" in text


def test_specs_include_sunpy_modules_and_hook_path():
    spec_paths = [
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_linux.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_win.spec",
        ROOT / "packaging" / "macos" / "setup.py",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "sunpy" in text
        assert "src.ui.solar.sunpy_solar_viewer" in text
        assert "src.ui.solar.solar_data_analysis_window" in text
        assert "src.backend.solar.solar_data_analysis" in text
        assert "imageio" in text
        assert "imageio_ffmpeg" in text

    hook_path = ROOT / "packaging" / "pyinstaller" / "hooks" / "hook-sunpy.py"
    assert hook_path.exists()


def test_pyinstaller_specs_bundle_aiapy_with_its_data_files():
    """AIA level 1.5 imports aiapy, which reads its CITATION.rst on import.

    py2app copies such packages whole; PyInstaller needs hook-aiapy.py, or the
    Windows/Linux builds fail level 1.5 with "Install it with: pip install aiapy".
    """
    spec_paths = [
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_linux.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_win.spec",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert '"aiapy"' in text
        assert '"aiapy.calibrate"' in text
        assert 'hookspath=[str(HERE / "hooks")]' in text

    hook = (ROOT / "packaging" / "pyinstaller" / "hooks" / "hook-aiapy.py").read_text(
        encoding="utf-8"
    )
    assert "collect_data_files(" in hook
    assert '"aiapy"' in hook

    requirements = (ROOT / "requirements" / "requirements-runtime.txt").read_text(
        encoding="utf-8"
    )
    assert "aiapy==" in requirements


def test_pyinstaller_specs_bundle_sunkit_magex_with_its_compiled_tracer():
    """PFSS needs sunkit-magex, which hard-depends on the compiled streamtracer.

    Both are imported lazily from src.backend.solar.pfss_model, so the specs need
    explicit hidden imports. streamtracer's shared library additionally needs
    collect_dynamic_libs, or a bundled build imports sunkit_magex successfully and
    then dies on the first solve -- reported to the user as though the optional
    package were simply not installed.
    """
    spec_paths = [
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_linux.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_win.spec",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert '"sunkit_magex"' in text
        assert '"sunkit_magex.pfss"' in text
        assert '"sunkit_magex.pfss.tracing"' in text
        assert '"streamtracer"' in text
        assert '"skimage.measure"' in text
        assert 'hookspath=[str(HERE / "hooks")]' in text

    hook = (
        ROOT / "packaging" / "pyinstaller" / "hooks" / "hook-sunkit_magex.py"
    ).read_text(encoding="utf-8")
    assert "collect_data_files(" in hook
    assert "collect_dynamic_libs(" in hook
    assert '"streamtracer"' in hook

    macos_setup = (ROOT / "packaging" / "macos" / "setup.py").read_text(encoding="utf-8")
    # py2app copies these whole: sunkit_magex ships data, streamtracer is
    # compiled, and scikit-image resolves submodules through lazy_loader stubs.
    assert '"sunkit_magex"' in macos_setup
    assert '"streamtracer"' in macos_setup
    assert '"skimage"' in macos_setup

    requirements = (ROOT / "requirements" / "requirements-runtime.txt").read_text(
        encoding="utf-8"
    )
    assert "sunkit-magex==" in requirements
    assert "streamtracer==" in requirements
    assert "scikit-image==" in requirements


def test_specs_bundle_pyqtgraph_exporters():
    spec_paths = [
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_linux.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_win.spec",
        ROOT / "packaging" / "macos" / "setup.py",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "pyqtgraph.exporters" in text
        assert "pyqtgraph.exporters.ImageExporter" in text
        assert "pyqtgraph.exporters.SVGExporter" in text


def test_specs_bundle_reportlab_project_reports():
    spec_paths = [
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_linux.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_win.spec",
        ROOT / "packaging" / "macos" / "setup.py",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "reportlab" in text
        assert "reportlab.platypus" in text
        assert "PIL.Image" in text


def test_specs_bundle_type_ii_band_splitting_icons():
    spec_paths = [
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_linux.spec",
        ROOT / "packaging" / "pyinstaller" / "FITS_Analyzer_win.spec",
    ]
    for path in spec_paths:
        text = path.read_text(encoding="utf-8")
        assert "assets/band_splitting_icons" in text

    py2app_setup = (ROOT / "packaging" / "macos" / "setup.py").read_text(encoding="utf-8")
    assert "assets/band_splitting_icons/light" in py2app_setup
    assert "assets/band_splitting_icons/dark" in py2app_setup


def test_macos_signing_is_deferred_until_py2app_finishes():
    setup_text = (ROOT / "packaging" / "macos" / "setup.py").read_text(encoding="utf-8")
    build_text = (ROOT / "packaging" / "macos" / "build_macos_dmg.sh").read_text(encoding="utf-8")
    signer_text = (ROOT / "packaging" / "macos" / "codesign_macos_bundle.py").read_text(encoding="utf-8")
    repair_text = (ROOT / "packaging" / "macos" / "repair_macos_openssl.py").read_text(encoding="utf-8")

    assert "shutil.copy2(source_lzma, bundled_lzma)" in setup_text
    assert '"codesign", "--force", "--deep"' not in setup_text
    assert "codesign_macos_bundle.py" in build_text
    assert "repair_macos_openssl.py" in build_text
    assert build_text.index('echo "==> Repairing bundled OpenSSL libraries"') < build_text.index(
        'echo "==> Signing the bundle"'
    )
    assert 'TARGET_MACOS="${MACOSX_DEPLOYMENT_TARGET:-13.0}"' in build_text
    assert 'grep -q -- "--target-macos"' in build_text
    assert build_text.index('grep -q -- "--target-macos"') < build_text.index("Running py2app")
    assert "codesign --verify --deep --strict" in build_text
    assert "smoke_test_packaged.py" in build_text
    assert build_text.index("codesign --verify --deep --strict") < build_text.index("smoke_test_packaged.py")
    assert build_text.index("smoke_test_packaged.py") < build_text.index("Building disk image")
    assert 'libssl.3.dylib libcrypto.3.dylib libcurl.4.dylib' in build_text
    assert 'codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE" &&' not in build_text
    assert '["codesign", "--remove-signature"' in signer_text
    assert '["codesign", "--verify", "--strict"' in signer_text
    assert '["codesign", "--verify", "--deep", "--strict"' in signer_text
    assert '"--target-macos"' in signer_text
    assert "minimum_macos_versions" in signer_text
    assert "libssl.3.dylib" in repair_text
    assert "libcrypto.3.dylib" in repair_text
    assert "install_name_tool" in repair_text
    assert "@loader_path/{dependency_name}" in repair_text
    assert '"LSMinimumSystemVersion"' in setup_text


def test_runtime_requirements_include_sunpy_network_stack():
    text = (ROOT / "requirements" / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert "shiboken6==" in text
    assert "reportlab==" in text
    assert "sunpy[map,net,timeseries]" in text
    assert "lxml==" in text
    assert "drms==" in text
    assert "zeep==" in text
    assert "reproject==" in text
    assert "mpl-animators==" in text
    assert "imageio==" in text
    assert "imageio-ffmpeg==" in text
