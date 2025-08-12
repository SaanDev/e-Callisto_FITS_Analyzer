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
    ],
    'includes': [
        'PySide6',
        'importlib_metadata',
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
        'callisto_downloader',  # your module
        'burst_processor',
        'gui_main',
        'matplotlib_widget',
    ],
    'iconfile': 'icon.icns',
    'resources': [],  # If you include sounds/images/fonts, list them here
    'plist': {
        'CFBundleName': 'e-Callisto FITS Analyzer',
        'CFBundleShortVersionString': '1.5.1',
        'CFBundleVersion': '1.5.1',
        'CFBundleIdentifier': 'com.sahansliyanage.callisto.fitsanalyzer',
    }
}

setup(
    app=APP,
    name='e-Callisto FITS Analyzer',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
