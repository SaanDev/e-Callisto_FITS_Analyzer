
#e-CALLISTO FITS Analyzer
#Version 1.7.6
#Sahan S Liyanage (sahanslst@gmail.com)
#Astronomical and Space Science Unit, University of Colombo, Sri Lanka.


# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# Import backend modules that exist on Windows
from matplotlib.backends import backend_pdf, backend_svg, backend_ps, backend_pgf

# FITS_Analyzer.spec is in: <root>/src/Installation/
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]          # <root>/src
PROJECT = ROOT.parent           # <root>

MAIN = PROJECT / "src" / "UI" / "main.py"

#fix
# Change this if your folder name is different.
ASSETS_DIR = PROJECT / "assests"

a = Analysis(
    [str(MAIN)],
    pathex=[str(PROJECT), str(PROJECT / "src")],
    binaries=[],
    datas=[
        (str(ASSETS_DIR / "FITS_analyzer.png"), "."),
        (str(ASSETS_DIR / "icons"), "assests/icons"),

        # Required backend files for exporting
        (backend_pdf.__file__, "matplotlib/backends"),
        (backend_svg.__file__, "matplotlib/backends"),
        (backend_ps.__file__, "matplotlib/backends"),
        (backend_pgf.__file__, "matplotlib/backends"),
    ],
    hiddenimports=[
        "PySide6",
        "matplotlib",

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
        "src.UI.soho_lasco_viewer",
        "src.UI.goes_xrs_gui",
        "src.UI.theme_manager",
        "src.UI.mpl_style",

        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],   # removed Linux-only hook
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
    icon=str(ASSETS_DIR / "icon.ico"),  # Windows icon must be .ico
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
