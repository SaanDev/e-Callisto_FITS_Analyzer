"""
e-CALLISTO FITS Analyzer
Version 1.7.3
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from setuptools import setup

APP = ['main.py']
DATA_FILES = ['icon.icns']

OPTIONS = {
    'packages': [
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
        'callisto_downloader',
        'burst_processor',
        'gui_main',
        'matplotlib_widget',
        'soho_lasco_viewer',
        'goes_xrs_gui',

        # Encoding libraries
        'charset_normalizer',
        'chardet',
    ],

    'iconfile': 'icon.icns',

    'resources': [],

    'plist': {
        'CFBundleName': 'e-Callisto FITS Analyzer',
        'CFBundleShortVersionString': '1.7.3',
        'CFBundleVersion': '1.7.3',
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
