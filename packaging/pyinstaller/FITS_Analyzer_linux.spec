# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

# Import backend modules so PyInstaller collects the exporter backends
from matplotlib.backends import backend_pdf, backend_svg, backend_ps, backend_pgf

# FITS_Analyzer.spec is located at: <project_root>/packaging/pyinstaller/FITS_Analyzer.spec
HERE = Path(SPECPATH).resolve()      # <project_root>/packaging/pyinstaller
PROJECT = HERE.parents[1]            # <project_root>

MAIN = PROJECT / "src" / "main.py"

# Assets folder (prefer "assets", fallback to "assests" if your repo uses that)
ASSETS_DIR = PROJECT / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = PROJECT / "assests"

# Simple guard to catch path issues early with a clear message
if not MAIN.exists():
    raise SystemExit(f"MAIN entry script not found: {MAIN}")
if not ASSETS_DIR.exists():
    raise SystemExit(f"Assets directory not found: {ASSETS_DIR}")

block_cipher = None

a = Analysis(
    [str(MAIN)],
    pathex=[str(PROJECT), str(PROJECT / "src")],
    binaries=[],
    datas=[
        (str(ASSETS_DIR / "branding"), "assets/branding"),
        (str(ASSETS_DIR / "icons"), "assets/icons"),
        (str(ASSETS_DIR / "icons_dark"), "assets/icons_dark"),
        (str(ASSETS_DIR / "band_splitting_icons"), "assets/band_splitting_icons"),

        # Matplotlib exporter backends
        (backend_pdf.__file__, "matplotlib/backends"),
        (backend_svg.__file__, "matplotlib/backends"),
        (backend_ps.__file__, "matplotlib/backends"),
        (backend_pgf.__file__, "matplotlib/backends"),
    ],
    hiddenimports=[
        # Qt + Matplotlib
        "PySide6",
        "matplotlib",
        "reportlab",
        "reportlab.lib",
        "reportlab.pdfbase",
        "reportlab.pdfgen",
        "reportlab.platypus",
        "PIL",
        "PIL.Image",
        "pyqtgraph",
        "pyqtgraph.exporters",
        "pyqtgraph.exporters.ImageExporter",
        "pyqtgraph.exporters.SVGExporter",
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qt5agg",

        # Export backends
        "matplotlib.backends.backend_pdf",
        "matplotlib.backends.backend_svg",
        "matplotlib.backends.backend_ps",
        "matplotlib.backends.backend_pgf",

        # Common matplotlib internals that often get missed
        "matplotlib.figure",
        "matplotlib.ticker",
        "matplotlib.colors",
        "matplotlib.widgets",
        "matplotlib.path",
        "mpl_toolkits.axes_grid1",

        # Your dependencies
        "astropy",
        "bs4",
        "requests",
        "scipy",
        "cftime",
        "netCDF4",
        "cdflib",
        "lxml",
        "drms",
        "zeep",
        "reproject",
        "mpl_animators",
        "imageio",
        "imageio_ffmpeg",
        "sunpy",
        "parfive",

        # SunPy async download stack (parfive -> aiohttp) + TLS trust store.
        # Kept in parity with the Windows spec so Fido.fetch behaves the same
        # across packaged platforms.
        "aiohttp",
        "aiohttp.client",
        "aiohttp.resolver",
        "certifi",
        "multidict",
        "yarl",
        "frozenlist",
        "aiosignal",
        "aiohappyeyeballs",

        # Your packages and modules
        "src",
        "src.ui",
        "src.backend",
        "src.ui.downloads.callisto_downloader",
        # Imported lazily from the Solar Events menu handler, so the
        # static analysis cannot see it.
        "src.ui.downloads.swaves_downloader",
        "src.backend.radio.swaves",
        "src.backend.radio.burst_processor",
        "src.backend.radio.spectral_overview",
        "src.backend.radio.multi_station_comparison",
        "src.backend.session.view_config",
        "src.ui.gui_main",
        "src.ui.app.main_window",
        "src.ui.common.gui_shared",
        "src.ui.app.gui_workers",
        "src.ui.radio.dialogs",
        "src.ui.radio.dialogs.analyze_dialog",
        "src.ui.radio.dialogs.batch_processing_dialog",
        "src.ui.radio.dialogs.display_range_dialog",
        "src.ui.radio.dialogs.max_intensity_dialog",
        "src.ui.radio.dialogs.multi_station_comparison_dialog",
        "src.ui.radio.dialogs.rfi_control_dialog",
        "src.ui.radio.dialogs.combine_dialogs",
        "src.ui.widgets.matplotlib_widget",
        "src.ui.widgets.accelerated_plot_widget",
        "src.ui.cme.soho_lasco_viewer",
        "src.ui.cme.cme_movie_helper",
        "src.ui.space_weather.dst_index_gui",
        "src.ui.space_weather.goes_xrs_gui",
        "src.ui.space_weather.goes_sgps_gui",
        "src.ui.space_weather.kp_index_gui",
        "src.ui.solar.sunpy_solar_viewer",
        "src.ui.solar.solar_data_analysis_window",
        "src.ui.solar.helioviewer_preview_dialog",
        "src.backend.solar.helioviewer",
        "src.ui.radio.fits_header_viewer",
        "src.ui.common.theme_manager",
        "src.ui.common.mpl_style",
        "src.ui.common",
        "src.ui.cme.cme_helper_client",
        "src.ui.cme.cme_ipc_protocol",
        "src.ui.cme.cme_launcher",
        "src.ui.common.url_opener",
        "src.backend.space_weather.dst_index",
        "src.backend.space_weather.kp_index",
        "src.backend.space_weather.sep_proton",
        "src.backend.solar.sunpy_archive",
        "src.backend.solar.sunpy_analysis",
        "src.backend.solar.solar_data_analysis",
        "sunpy.map",
        "sunpy.net",
        "sunpy.timeseries",

        # AIA level 1.5 in the Solar Image Analysis window. Imported lazily;
        # hook-aiapy.py bundles the CITATION.rst that aiapy reads on import.
        "aiapy",
        "aiapy.calibrate",

        # PFSS modelling in the Solar Image Analysis window. Imported lazily, so
        # static analysis cannot see it; hook-sunkit_magex.py also collects the
        # compiled streamtracer library that the field-line tracer needs.
        "sunkit_magex",
        "sunkit_magex.pfss",
        "sunkit_magex.pfss.tracing",
        "sunkit_magex.pfss.utils",
        "streamtracer",
        "skimage",
        "skimage.measure",
        "lazy_loader",

        # Qt modules used by the app
        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
    ],
    hookspath=[str(HERE / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="e-callisto-fits-analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Prefer PNG on Linux. If you do not want an icon, remove this line.
    icon=str(ASSETS_DIR / "branding" / "FITS_analyzer.png"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="e-callisto-fits-analyzer",
)
