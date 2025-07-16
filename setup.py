from setuptools import setup

APP = ['main.py']  # <- this is your entry point
DATA_FILES = []
OPTIONS = {
    # 'argv_emulation': True,  # keep disabled
    'packages': ['matplotlib', 'numpy', 'pandas', 'scipy'],
    'includes': [
        'PySide6',
        'sklearn',
        'importlib_metadata',
        'csv',
    ],
    'iconfile': 'icon.icns',  # optional
}


setup(
    app=APP,
    name='e-Callisto FITS Analyzer',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
