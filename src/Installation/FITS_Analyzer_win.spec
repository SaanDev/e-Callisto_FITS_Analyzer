# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# Import backend modules that exist on Windows
from matplotlib.backends import backend_pdf, backend_svg, backend_ps, backend_pgf

# FITS_Analyzer_win.spec is in: <root>/src/Installation/
if "SPECPATH" in globals():
    HERE = Path(SPECPATH).resolve()            # <root>/src/Installation
elif "__file__" in globals():
    HERE = Path(__file__).resolve().parent     # fallback for direct execution
else:
    HERE = Path.cwd().resolve()

PROJECT = HERE.parents[1]                      # <root>
ROOT = PROJECT / "src"                         # <root>/src

MAIN = PROJECT / "src" / "UI" / "main.py"

# Assets folder (prefer "assets", fallback to "assests" if your repo uses that)
ASSETS_DIR = PROJECT / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = PROJECT / "assests"

a = Analysis(
    [str(MAIN)],
    pathex=[str(PROJECT), str(ROOT)],
    binaries=[],

    datas=[
        (str(PROJECT / "icon.ico"), "."),
        (str(ASSETS_DIR / "FITS_analyzer.png"), "assets"),
        (str(ASSETS_DIR / "icons"), "assets/icons"),
        (str(ASSETS_DIR / "icons_dark"), "assets/icons_dark"),

        # Required backend files for exporting
        (backend_pdf.__file__, "matplotlib/backends"),
        (backend_svg.__file__, "matplotlib/backends"),
        (backend_ps.__file__, "matplotlib/backends"),
        (backend_pgf.__file__, "matplotlib/backends"),
    ],
    hiddenimports=[
        "PySide6",
        "matplotlib",
        "pyqtgraph",

        # Canvas backends
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qt5agg",

        # Export backends
        "matplotlib.backends.backend_pdf",
        "matplotlib.backends.backend_svg",
        "matplotlib.backends.backend_ps",
        "matplotlib.backends.backend_pgf",

        "matplotlib.figure",
        "matplotlib.ticker",
        "matplotlib.colors",
        "matplotlib.widgets",
        "matplotlib.path",
        "mpl_toolkits.axes_grid1",

        "astropy",
        "bs4",
        "requests",
        "scipy",
        "cftime",
        "netCDF4",
        'src',
        'src.UI',
        'src.Backend',

        "src.UI.callisto_downloader",
        "src.Backend.burst_processor",
        "src.UI.gui_main",
        "src.UI.matplotlib_widget",
        "src.UI.accelerated_plot_widget",
        "src.UI.soho_lasco_viewer",
        "src.UI.goes_xrs_gui",
        "src.UI.goes_sgps_gui",
        "src.UI.fits_header_viewer",
        "src.UI.theme_manager",
        "src.UI.mpl_style",

        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
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

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="e-Callisto FITS Analyzer",
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
    icon=str((PROJECT / "icon.ico") if (PROJECT / "icon.ico").exists() else (ASSETS_DIR / "icon.ico")),  # Windows icon must be .ico
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="e-Callisto FITS Analyzer",
)
