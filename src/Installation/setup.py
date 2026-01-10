"""
e-CALLISTO FITS Analyzer
Version 1.7.5
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""
import os
import sys
from setuptools import setup

HERE = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

def R(*parts: str) -> str:
    return os.path.join(PROJECT_ROOT, *parts)

# Ensure project root is importable so py2app can find src.*
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP = [R("src", "UI", "main.py")]

DATA_FILES = [
    ("assets", [R("assets", "icon.icns")]),
    ("assets/icons", [
        R("assets", "icons", "open.svg"),
        R("assets", "icons", "export.svg"),
        R("assets", "icons", "undo.svg"),
        R("assets", "icons", "redo.svg"),
        R("assets", "icons", "download.svg"),
        R("assets", "icons", "drift.svg"),
        R("assets", "icons", "isolate.svg"),
        R("assets", "icons", "max.svg"),
        R("assets", "icons", "reset_selection.svg"),
        R("assets", "icons", "reset_all.svg"),
    ]),
    ("assets/icons_dark", [
        R("assets", "icons_dark", "open.svg"),
        R("assets", "icons_dark", "export.svg"),
        R("assets", "icons_dark", "undo.svg"),
        R("assets", "icons_dark", "redo.svg"),
        R("assets", "icons_dark", "download.svg"),
        R("assets", "icons_dark", "drift.svg"),
        R("assets", "icons_dark", "isolate.svg"),
        R("assets", "icons_dark", "max.svg"),
        R("assets", "icons_dark", "reset_selection.svg"),
        R("assets", "icons_dark", "reset_all.svg"),
    ]),
]

OPTIONS = {
    "packages": [
        "src",
        "src.UI",
        "src.Backend",
        "matplotlib",
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
        "PySide6.QtNetwork",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "matplotlib.backends.backend_qtagg",

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
        "src.Backend.burst_processor",
        "src.UI.gui_main",
        "src.UI.matplotlib_widget",
        "src.UI.soho_lasco_viewer",
        "src.UI.goes_xrs_gui",

        # Encoding
        "charset_normalizer",
        "chardet",
    ],

    "qt_plugins": ["platforms", "imageformats", "iconengines", "styles"],

    "iconfile": R("assets", "icon.icns"),

    "resources": [],

    "plist": {
        "CFBundleName": "e-Callisto FITS Analyzer",
        "CFBundleShortVersionString": "1.7.5",
        "CFBundleVersion": "1.7.5",
        "CFBundleIdentifier": "com.sahansliyanage.callisto.fitsanalyzer",
    },
}

setup(
    app=APP,
    name="e-Callisto FITS Analyzer",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
