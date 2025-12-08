"""
e-CALLISTO FITS Analyzer
Version 1.7.2
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from setuptools import setup

APP = ['main.py']  # Entry point
DATA_FILES = ['icon.icns']  # Add any other static resources if needed (images, etc.)

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
        'PySide6',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'PySide6.QtNetwork',
        'charset_normalizer',
        'chardet',
        'lzma',
        'importlib_metadata',
        'backports.lzma',
        '_lzma',
        'csv',
        'io',
        'os',
        're',
        'gc',
        'tempfile',
        'matplotlib.backends.backend_qt5agg',
        'matplotlib.figure',
        'matplotlib.ticker',
        'matplotlib.colors',
        'mpl_toolkits.axes_grid1',
        'matplotlib.widgets',
        'matplotlib.path',
        'matplotlib',
        'callisto_downloader',
        'burst_processor',
        'gui_main',
        'matplotlib_widget',
        'chardet',
        'charset_normalizer',
    ],


    'iconfile': 'icon.icns',
    'resources': [],
    'plist': {
        'CFBundleName': 'e-Callisto FITS Analyzer',
        'CFBundleShortVersionString': '1.7.1',
        'CFBundleVersion': '1.7.1',
        'CFBundleIdentifier': 'com.sahansliyanage.callisto.fitsanalyzer',
    }
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
