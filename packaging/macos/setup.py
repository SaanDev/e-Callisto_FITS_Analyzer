"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import shutil
import os
import sys
from glob import glob
from setuptools import setup

try:
    from py2app.build_app import py2app as py2app_cmd
except ImportError:
    py2app_cmd = None

HERE = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# py2app walks imports recursively with modulegraph; the scientific stack is deep
# enough that the default limit can be exceeded during standalone analysis.
sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))

LZMA_CANDIDATES = [
    "/opt/homebrew/opt/xz/lib/liblzma.5.dylib",
    "/usr/local/opt/xz/lib/liblzma.5.dylib",
]
LZMA_FRAMEWORKS = [path for path in LZMA_CANDIDATES if os.path.exists(path)]

def R(*parts: str) -> str:
    return os.path.join(PROJECT_ROOT, *parts)


def SVG_FILES(folder: str):
    files = sorted(glob(R("assets", folder, "*.svg")))
    if not files:
        raise SystemExit(f"No SVG files found under assets/{folder}")
    return files


def _replace_bundled_lzma(app_path: str) -> None:
    if sys.platform != "darwin":
        return

    bundled_lzma = os.path.join(app_path, "Contents", "Frameworks", "liblzma.5.dylib")
    source_lzma = next(
        (path for path in LZMA_FRAMEWORKS if os.path.basename(path) == os.path.basename(bundled_lzma)),
        None,
    )

    if not source_lzma or not os.path.exists(bundled_lzma):
        return

    # py2app rewrites Mach-O load commands while assembling the bundle, so the
    # copied library must be installed before the final signing pass. Do not
    # sign here: build_macos_dmg.sh signs every Mach-O file inside-out after
    # py2app has finished making all bundle changes.
    shutil.copy2(source_lzma, bundled_lzma)


if py2app_cmd is not None:
    class Py2AppCommand(py2app_cmd):
        def run(self):
            super().run()
            _replace_bundled_lzma(os.path.join(self.dist_dir, f"{self.distribution.get_name()}.app"))


    CMDCLASS = {"py2app": Py2AppCommand}
else:
    CMDCLASS = {}

# Ensure project root is importable so py2app can find src.*
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.version import APP_VERSION

APP = [R("src", "main.py")]

def STREAMTRACER_METADATA():
    """Bundle streamtracer's ``.dist-info`` into the application's import path.

    ``streamtracer/version.py`` calls ``importlib.metadata.version("streamtracer")``
    at import time. py2app copies package *directories*, but a distribution's
    ``.dist-info`` sits **alongside** the package rather than inside it, so it is
    left behind -- and the frozen app then raises ``PackageNotFoundError`` the
    moment ``sunkit_magex.pfss`` is imported, disabling PFSS in the shipped .app.

    This is the one case where py2app does *not* mask a PyInstaller packaging
    gap: the PyInstaller side needs ``copy_metadata`` in
    ``packaging/pyinstaller/hooks/hook-sunkit_magex.py`` for exactly the same
    reason. Verified 2026-09-21 with minimal probes for both freezers.

    Returns an empty list when streamtracer is absent, so a build without the
    optional PFSS stack still succeeds.
    """
    try:
        import importlib.metadata as _md

        dist = _md.distribution("streamtracer")
    except Exception:
        return []

    info_dir = getattr(dist, "_path", None)
    if info_dir is None or not os.path.isdir(str(info_dir)):
        return []
    info_dir = str(info_dir)

    files = [
        os.path.join(info_dir, name)
        for name in sorted(os.listdir(info_dir))
        if os.path.isfile(os.path.join(info_dir, name))
    ]
    if not files:
        return []

    # Destinations are relative to Contents/Resources, and that lib directory is
    # on the bundled interpreter's sys.path, which is where importlib.metadata
    # looks for distributions.
    lib = "lib/python{0}.{1}".format(*sys.version_info[:2])
    return [(f"{lib}/{os.path.basename(info_dir)}", files)]


DATA_FILES = [
    ("assets/branding", [
        R("assets", "branding", "icon.icns"),
        R("assets", "branding", "FITS_analyzer.png"),
    ]),
    ("assets/icons", SVG_FILES("icons")),
    ("assets/icons_dark", SVG_FILES("icons_dark")),
    ("assets/band_splitting_icons/light", SVG_FILES(os.path.join("band_splitting_icons", "light"))),
    ("assets/band_splitting_icons/dark", SVG_FILES(os.path.join("band_splitting_icons", "dark"))),
] + STREAMTRACER_METADATA()

OPTIONS = {
    "argv_emulation": False,
    "strip": False,
    "packages": [
        "src",
        "netCDF4",
        "cdflib",
        "cftime",
        "reportlab",
        "PIL",
        "imageio",
        "imageio_ffmpeg",
        # Copied whole: sunkit_magex ships data files, streamtracer is a compiled
        # extension, and scikit-image resolves submodules at runtime through
        # lazy_loader .pyi stubs that a module-level include would miss.
        "sunkit_magex",
        "streamtracer",
        "skimage",
    ],

    "includes": [
        # PySide6
        "PySide6",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "matplotlib.backends.backend_qtagg",
        "pyqtgraph",
        "sunkit_magex.pfss",
        "sunkit_magex.pfss.tracing",
        "sunkit_magex.pfss.utils",
        "skimage.measure",
        "lazy_loader",
        "pyqtgraph.exporters",
        "pyqtgraph.exporters.ImageExporter",
        "pyqtgraph.exporters.SVGExporter",

        # Standard libs used dynamically
        "csv",
        "io",
        "os",
        "re",
        "gc",
        "tempfile",
        "lzma",
        "backports.lzma",
        "importlib_metadata",

        # Matplotlib core
        "matplotlib",
        "reportlab",
        "reportlab.lib",
        "reportlab.pdfbase",
        "reportlab.pdfgen",
        "reportlab.platypus",
        "PIL",
        "PIL.Image",
        "imageio",
        "imageio_ffmpeg",
        "matplotlib.figure",
        "matplotlib.ticker",
        "matplotlib.colors",
        "matplotlib.widgets",
        "matplotlib.path",
        "matplotlib.backends.backend_qt5agg",
        "mpl_toolkits.axes_grid1",

        # Export backends
        "matplotlib.backends.backend_pdf",
        "matplotlib.backends.backend_svg",
        "matplotlib.backends.backend_ps",
        "matplotlib.backends.backend_eps",

        # netCDF4 imports sibling modules from its compiled extension at runtime
        "netCDF4.utils",

        # Project modules
        "src.ui.downloads.callisto_downloader",
        # Imported lazily from the Solar Events menu handler.
        "src.ui.downloads.swaves_downloader",
        "src.backend.radio.swaves",
        "src.ui.common.theme_manager",
        "src.ui.common.mpl_style",
        "src.ui.space_weather.dst_index_gui",
        "src.ui.radio.fits_header_viewer",
        "src.ui.space_weather.goes_sgps_gui",
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
        "src.ui.cme.cme_helper_client",
        "src.ui.cme.cme_ipc_protocol",
        "src.ui.space_weather.goes_xrs_gui",
        "src.ui.space_weather.kp_index_gui",
        "src.ui.solar.sunpy_solar_viewer",
        "src.ui.solar.solar_data_analysis_window",
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

        # Encoding
        "charset_normalizer",
        "chardet",
    ],

    "qt_plugins": ["platforms", "imageformats", "iconengines", "styles", "multimedia", "webengine"],

    "iconfile": R("assets", "branding", "icon.icns"),

    "resources": [],

    "frameworks": LZMA_FRAMEWORKS,

    "excludes": [
        # Build-only tooling can be present in the packaging venv; py2app should
        # not crawl those packages into the application dependency graph.
        "PyInstaller",
        "pyinstaller_hooks_contrib",
        "torch",
        "torchgen",
    ],

    "plist": {
        "CFBundleName": "e-Callisto FITS Analyzer",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "CFBundleIdentifier": "com.sahansliyanage.callisto.fitsanalyzer",
        "LSMinimumSystemVersion": os.environ.get("MACOSX_DEPLOYMENT_TARGET", "13.0"),
    },
}

setup(
    app=APP,
    cmdclass=CMDCLASS,
    name="e-Callisto FITS Analyzer",
    version=APP_VERSION,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
