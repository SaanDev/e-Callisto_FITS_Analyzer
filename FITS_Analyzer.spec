# -*- mode: python ; coding: utf-8 -*-

# Import the Matplotlib backends that actually exist on Windows
from matplotlib.backends import backend_pdf, backend_svg, backend_ps, backend_pgf

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],

    datas=[
        # Bundle icon
        ('icon.icns', '.'),

        # Bundle Matplotlib export backend source files
        (backend_pdf.__file__, 'matplotlib/backends'),
        (backend_svg.__file__, 'matplotlib/backends'),
        (backend_ps.__file__, 'matplotlib/backends'),
        (backend_pgf.__file__, 'matplotlib/backends'),
    ],

    hiddenimports=[
        # PySide
        'PySide6',

        # Matplotlib and required components
        'matplotlib',
        'matplotlib.backends.backend_qt5agg',
        'matplotlib.backends.backend_qtagg',

        # Export backends that DO exist
        'matplotlib.backends.backend_pdf',
        'matplotlib.backends.backend_svg',
        'matplotlib.backends.backend_ps',
        'matplotlib.backends.backend_pgf',

        # Matplotlib utilities
        'matplotlib.figure',
        'matplotlib.ticker',
        'matplotlib.colors',
        'matplotlib.path',
        'matplotlib.widgets',
        'mpl_toolkits.axes_grid1',

        # Scientific libs
        'numpy',
        'scipy',
        'astropy',
        'requests',
        'bs4',
        'cftime',
        'netCDF4',

        # Your project modules
        'callisto_downloader',
        'burst_processor',
        'gui_main',
        'matplotlib_widget',
        'goes_xrs_gui',
        'soho_lasco_viewer',
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
    name='e-CALLISTO FITS Analyzer',
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
    name='e-CALLISTO FITS Analyzer',
)
