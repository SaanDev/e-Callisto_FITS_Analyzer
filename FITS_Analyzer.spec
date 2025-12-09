# -*- mode: python ; coding: utf-8 -*-
from matplotlib.backends import backend_pdf, backend_svg, backend_ps, backend_pgf, backend_eps

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],

    # All data files bundled inside the app
    datas=[
        ('icon.icns', '.'),

        # Required Matplotlib backend files for export features
        (backend_pdf.__file__, 'matplotlib/backends'),
        (backend_svg.__file__, 'matplotlib/backends'),
        (backend_ps.__file__, 'matplotlib/backends'),
        (backend_pgf.__file__, 'matplotlib/backends'),
        (backend_eps.__file__, 'matplotlib/backends'),
    ],

    # Dynamic imports that PyInstaller cannot detect automatically
    hiddenimports=[
        # Core dependencies
        'PySide6',
        'matplotlib',

        # Canvas backends
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_qt5agg',

        # Export backends (required!)
        'matplotlib.backends.backend_pdf',
        'matplotlib.backends.backend_svg',
        'matplotlib.backends.backend_ps',
        'matplotlib.backends.backend_eps',
        'matplotlib.backends.backend_pgf',

        # Matplotlib utilities
        'matplotlib.figure',
        'matplotlib.ticker',
        'matplotlib.colors',
        'matplotlib.widgets',
        'matplotlib.path',
        'mpl_toolkits.axes_grid1',

        # Scientific libraries
        'astropy',
        'bs4',
        'requests',
        'scipy',
        'cftime',
        'netCDF4',

        # Your project modules
        'callisto_downloader',
        'burst_processor',
        'gui_main',
        'matplotlib_widget',
        'soho_lasco_viewer',
        'goes_xrs_gui',
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
    name='e-Callisto FITS Analyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.icns'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='e-Callisto FITS Analyzer',
)
