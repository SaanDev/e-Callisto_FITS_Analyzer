"""
e-CALLISTO FITS Analyzer
Version 1.7.4
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""
import os, sys
from setuptools import setup

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP = ['src/UI/main.py']
DATA_FILES = ['assests/icon.icns',
    ('assests/icons', [
        'assests/icons/open.svg',
        'assests/icons/export.svg',
        'assests/icons/undo.svg',
        'assests/icons/redo.svg',
        'assests/icons/download.svg',
        'assests/icons/drift.svg',
        'assests/icons/isolate.svg',
        'assests/icons/max.svg',
        'assests/icons/reset_selection.svg',
        'assests/icons/reset_all.svg',
    ])
]

OPTIONS = {
    'packages': [
        'src',
        'src.UI',
        'src.Backend',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'openpyxl',
        'astropy',
        'sklearn',
        'requests',
        'bs4',
        'netCDF4',
        'cftime',
    ],

    'includes': [
        # PySide6
        'PySide6',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'PySide6.QtNetwork',
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "matplotlib.backends.backend_qtagg",


        # Standard libs used dynamically
        'csv',
        'io',
        'os',
        're',
        'gc',
        'tempfile',
        'lzma',
        'backports.lzma',
        'importlib_metadata',

        # Matplotlib core
        'matplotlib',
        'matplotlib.figure',
        'matplotlib.ticker',
        'matplotlib.colors',
        'matplotlib.widgets',
        'matplotlib.path',
        'matplotlib.backends.backend_qt5agg',
        'mpl_toolkits.axes_grid1',

        # REQUIRED FOR EXPORTING PDF / SVG / EPS
        'matplotlib.backends.backend_pdf',
        'matplotlib.backends.backend_svg',
        'matplotlib.backends.backend_ps',
        'matplotlib.backends.backend_eps',

        # Project modules
        'src.UI.callisto_downloader',
        'src.Backend.burst_processor',
        'src.UI.gui_main',
        'src.UI.matplotlib_widget',
        'src.UI.soho_lasco_viewer',
        'src.UI.goes_xrs_gui',

        # Encoding libraries
        'charset_normalizer',
        'chardet',
    ],

    "qt_plugins": ["platforms", "imageformats", "iconengines", "styles"],


    'iconfile': 'assests/icon.icns',

    'resources': [],

    'plist': {
        'CFBundleName': 'e-Callisto FITS Analyzer',
        'CFBundleShortVersionString': '1.7.4',
        'CFBundleVersion': '1.7.4',
        'CFBundleIdentifier': 'com.sahansliyanage.callisto.fitsanalyzer',
    },
}

setup(
    app=APP,
    name='e-Callisto FITS Analyzer',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
    scripts=[],
    package_dir={'': '.'},
)
