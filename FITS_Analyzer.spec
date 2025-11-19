# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
    ('icon.icns', '.'),
],
    hiddenimports=[
    'PySide6',
    'matplotlib.backends.backend_qtagg',
    'matplotlib.backends.backend_qt5agg',
    ' matplotlib',
    'mpl_toolkits.axes_grid1',
    'matplotlib.colors',
    'matplotlib.widgets',
    'matplotlib.path',
    'astropy',
    'bs4',
    'requests',
    'scipy',
    'cftime',
    'netCDF4',
    'callisto_downloader',
    'burst_processor',
    'gui_main',
    'matplotlib_widget',
    'soho_lasco_viewer',
    'goes_xrs_gui'
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
