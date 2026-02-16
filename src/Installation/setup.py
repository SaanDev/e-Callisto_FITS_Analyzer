"""
e-CALLISTO FITS Analyzer
Version 2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import os
import sys
from glob import glob
from setuptools import setup

HERE = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

LZMA_CANDIDATES = [
    "/opt/homebrew/opt/xz/lib/liblzma.5.dylib",
    "/usr/local/opt/xz/lib/liblzma.5.dylib",
]
LZMA_FRAMEWORKS = [path for path in LZMA_CANDIDATES if os.path.exists(path)]

def R(*parts: str) -> str:
    return os.path.join(PROJECT_ROOT, *parts)


def SVG_FILES(folder: str):
    files = sorted(glob(R("assets", folder, "*.svg")))
    if not files:
        raise SystemExit(f"No SVG files found under assets/{folder}")
    return files

# Ensure project root is importable so py2app can find src.*
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.version import APP_VERSION

APP = [R("src", "UI", "main.py")]

DATA_FILES = [
    ("assets", [
        R("assets", "icon.icns"),
        R("assets", "FITS_analyzer.png"),
    ]),
    ("assets/icons", SVG_FILES("icons")),
    ("assets/icons_dark", SVG_FILES("icons_dark")),
]

OPTIONS = {
    "argv_emulation": False,
    "packages": [
        "src",
        "src.UI",
        "src.Backend",
        "matplotlib",
        "pyqtgraph",
        "numpy",
        "pandas",
        "scipy",
        "openpyxl",
        "astropy",
        "sklearn",
        "requests",
        "bs4",
        "netCDF4",
        "cftime",
    ],

    "includes": [
        # PySide6
        "PySide6",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "matplotlib.backends.backend_qtagg",
        "pyqtgraph",

        # Standard libs used dynamically
        "csv",
        "io",
        "os",
        "re",
        "gc",
        "tempfile",
        "lzma",
        "backports.lzma",
        "importlib_metadata",

        # Matplotlib core
        "matplotlib",
        "matplotlib.figure",
        "matplotlib.ticker",
        "matplotlib.colors",
        "matplotlib.widgets",
        "matplotlib.path",
        "matplotlib.backends.backend_qt5agg",
        "mpl_toolkits.axes_grid1",

        # Export backends
        "matplotlib.backends.backend_pdf",
        "matplotlib.backends.backend_svg",
        "matplotlib.backends.backend_ps",
        "matplotlib.backends.backend_eps",

        # Project modules
        "src.UI.callisto_downloader",
        "src.UI.theme_manager",
        "src.UI.mpl_style",
        "src.UI.fits_header_viewer",
        "src.UI.goes_sgps_gui",
        "src.Backend.burst_processor",
        "src.UI.gui_main",
        "src.UI.main_window",
        "src.UI.gui_shared",
        "src.UI.gui_workers",
        "src.UI.dialogs",
        "src.UI.dialogs.analyze_dialog",
        "src.UI.dialogs.max_intensity_dialog",
        "src.UI.dialogs.rfi_control_dialog",
        "src.UI.dialogs.combine_dialogs",
        "src.UI.matplotlib_widget",
        "src.UI.accelerated_plot_widget",
        "src.UI.soho_lasco_viewer",
        "src.UI.cme_movie_helper",
        "src.UI.utils.cme_helper_client",
        "src.UI.utils.cme_ipc_protocol",
        "src.UI.goes_xrs_gui",
        "src.UI.utils.cme_launcher",
        "src.UI.utils.url_opener",

        # Encoding
        "charset_normalizer",
        "chardet",
    ],

    "qt_plugins": ["platforms", "imageformats", "iconengines", "styles", "multimedia", "webengine"],

    "iconfile": R("assets", "icon.icns"),

    "resources": [],

    "frameworks": LZMA_FRAMEWORKS,

    "plist": {
        "CFBundleName": "e-Callisto FITS Analyzer",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "CFBundleIdentifier": "com.sahansliyanage.callisto.fitsanalyzer",
    },
}

setup(
    app=APP,
    name="e-Callisto FITS Analyzer",
    version=APP_VERSION,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
