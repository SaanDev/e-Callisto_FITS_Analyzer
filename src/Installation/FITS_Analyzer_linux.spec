# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

# Import backend modules so PyInstaller collects the exporter backends
from matplotlib.backends import backend_pdf, backend_svg, backend_ps, backend_pgf

# FITS_Analyzer.spec is located at: <project_root>/src/Installation/FITS_Analyzer.spec
HERE = Path(SPECPATH).resolve()      # <project_root>/src/Installation
PROJECT = HERE.parents[1]            # <project_root>

MAIN = PROJECT / "src" / "UI" / "main.py"

# Assets folder (prefer "assets", fallback to "assests" if your repo uses that)
ASSETS_DIR = PROJECT / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = PROJECT / "assests"

# Simple guard to catch path issues early with a clear message
if not MAIN.exists():
    raise SystemExit(f"MAIN entry script not found: {MAIN}")
if not ASSETS_DIR.exists():
    raise SystemExit(f"Assets directory not found: {ASSETS_DIR}")

block_cipher = None

a = Analysis(
    [str(MAIN)],
    pathex=[str(PROJECT), str(PROJECT / "src")],
    binaries=[],
    datas=[
        (str(ASSETS_DIR / "FITS_analyzer.png"), "assets"),
        (str(ASSETS_DIR / "icons"), "assets/icons"),
        (str(ASSETS_DIR / "icons_dark"), "assets/icons_dark"),
        (str(ASSETS_DIR / "icons_light"), "assets/icons_light"),

        # Matplotlib exporter backends
        (backend_pdf.__file__, "matplotlib/backends"),
        (backend_svg.__file__, "matplotlib/backends"),
        (backend_ps.__file__, "matplotlib/backends"),
        (backend_pgf.__file__, "matplotlib/backends"),
    ],
    hiddenimports=[
        # Qt + Matplotlib
        "PySide6",
        "matplotlib",
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qt5agg",

        # Export backends
        "matplotlib.backends.backend_pdf",
        "matplotlib.backends.backend_svg",
        "matplotlib.backends.backend_ps",
        "matplotlib.backends.backend_pgf",

        # Common matplotlib internals that often get missed
        "matplotlib.figure",
        "matplotlib.ticker",
        "matplotlib.colors",
        "matplotlib.widgets",
        "matplotlib.path",
        "mpl_toolkits.axes_grid1",

        # Your dependencies
        "astropy",
        "bs4",
        "requests",
        "scipy",
        "cftime",
        "netCDF4",

        # Your packages and modules
        "src",
        "src.UI",
        "src.Backend",
        "src.UI.callisto_downloader",
        "src.Backend.burst_processor",
        "src.UI.gui_main",
        "src.UI.matplotlib_widget",
        "src.UI.soho_lasco_viewer",
        "src.UI.goes_xrs_gui",
        "src.UI.theme_manager",
        "src.UI.mpl_style",

        # Qt modules used by the app
        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="e-callisto-fits-analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Prefer PNG on Linux. If you do not want an icon, remove this line.
    icon=str(ASSETS_DIR / "FITS_analyzer.png"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="e-callisto-fits-analyzer",
)
