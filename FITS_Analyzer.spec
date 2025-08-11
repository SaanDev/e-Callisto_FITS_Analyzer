# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.ico', '.'),  # Include icon
    ],
    hiddenimports=[
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_qt5agg',
        *collect_submodules('PySide6'),
        *collect_submodules('astropy'),
        *collect_submodules('matplotlib'),
        *collect_submodules('bs4'),
        *collect_submodules('scipy'),
        *collect_submodules('requests'),
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='e-CALLISTO_FITS_Analyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Set to True if you want console logs
    icon='icon.icns'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='e-CALLISTO_FITS_Analyzer'
)
