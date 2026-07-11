"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil
import threading
import time
import traceback
from typing import Any

import numpy as np
from matplotlib import colormaps as mpl_colormaps
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from pyqtgraph.exporters import SVGExporter
from PySide6.QtCore import QDateTime, QEasingCurve, QObject, QPropertyAnimation, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QPainter, QPdfWriter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QSizePolicy,
    QWidget,
)

from src.Backend.solar_data_analysis import (
    AiaCompositeSpec,
    AiaMovieExportSpec,
    AiaRegion,
    CropBounds,
    apply_display_scale,
    crop_maps,
    detect_active_regions,
    export_movie,
    exposures_differ,
    extract_map_frames,
    extract_region_lightcurve,
    fetch_active_region_metadata,
    frame_exposure_time,
    frame_observation_time,
    label_regions_with_metadata,
    load_aia_maps_streaming,
    make_composite,
    make_magnetogram_composite,
    partition_frames_by_config,
    radio_euv_lag,
    write_cropped_fits,
)
from src.Backend.download_manager import format_bytes, format_eta
from src.Backend.image_measure import ruler_measurement
from src.Backend.solar_session import (
    SolarSessionError,
    deserialize_picks,
    read_solar_session,
    serialize_picks,
    session_frame_count,
    session_pick_count,
    write_solar_session,
)
from src.Backend.solar_grid import (
    FRAME_DISPLAY_NAMES as SOLAR_FRAME_DISPLAY_NAMES,
    FRAME_KEYS as SOLAR_FRAME_KEYS,
    FRAME_LABELS as SOLAR_FRAME_LABELS,
    frame_key_from_display as solar_frame_key_from_display,
    graticule_arcsec,
    point_lonlat,
)
from src.Backend.instrument_profiles import (
    CORONAGRAPH,
    DISK_EUV,
    HELIOSPHERIC,
    MAGNETOGRAPH,
    UNKNOWN,
    classify_frame,
    classify_observable,
)
from src.Backend.hmi_vector_field import (
    VectorOverlayOptions,
    build_overlay_geometry,
    load_vector_frames,
    nearest_vector_frame,
    parse_trec_time,
    vector_display_frame,
)
from src.Backend.jsoc_client import (
    SIZE_BIN2,
    SIZE_BIN4,
    SIZE_CUTOUT,
    SIZE_FULL,
    JsocError,
    estimate_download,
    size_process,
)
from src.Backend.sunpy_archive import (
    DATA_KIND_MAP,
    SunPyFetchResult,
    SunPyLoadResult,
    SunPyQuerySpec,
    SunPySearchResult,
)
from src.UI.download_queue_panel import DownloadProgressPanel
from src.UI.font_utils import preferred_monospace_font_family
from src.UI.gui_shared import fit_window_to_screen, pick_export_path, screen_available_geometry
from src.UI.sunpy_plot_window import SunPyPlotCanvas
from src.UI.sunpy_solar_viewer import SunPyWorker, _default_cache_dir, _get_theme


# The solar image analysis window is a young, experimental feature; the title
# and About dialog flag it as a beta so users calibrate their expectations and
# know where to report problems.
SOLAR_WINDOW_VERSION = "Beta v1.0"
SOLAR_WINDOW_TITLE = f"Solar Image Analysis (Experimental) {SOLAR_WINDOW_VERSION}"
SOLAR_ISSUES_URL = "https://github.com/SaanDev/e-Callisto_FITS_Analyzer/issues"

AIA_WAVELENGTHS = (94, 131, 171, 193, 211, 304, 335, 1600, 1700)
AIA_COLORMAPS = tuple(f"sdoaia{value}" for value in AIA_WAVELENGTHS)
AIA_FULL_RESOLUTION = 1.0
AIA_HIGH_RES_WARN_ROWS = 8

# SDO/HMI line-of-sight observables, with the display colormap each one reads
# best in (magnetogram bipolar grey, continuum/Doppler in grey/diverging).
HMI_OBSERVABLES = (
    ("magnetogram", "HMI Magnetogram"),
    ("continuum", "HMI Continuum (Intensitygram)"),
    ("dopplergram", "HMI Dopplergram"),
)
HMI_COLORMAPS = {"magnetogram": "hmimag", "continuum": "gray", "dopplergram": "RdBu_r"}
HMI_PRODUCT_CONTENT = {  # FITS CONTENT keyword -> product, to recolour loaded HMI frames
    "magnetogram": "magnetogram",
    "continuum intensity": "continuum",
    "continuum": "continuum",
    "dopplergram": "dopplergram",
}

# SOHO/LASCO white-light coronagraphs. These are VSO-only (no JSOC fast path),
# have no EUV wavelength or HMI product, and read best in SunPy's dedicated
# soholasco2/3 colormaps. The detector string doubles as the observable value.
LASCO_DETECTORS = (
    ("C2", "SOHO/LASCO C2"),
    ("C3", "SOHO/LASCO C3"),
)
LASCO_COLORMAPS = {"C2": "soholasco2", "C3": "soholasco3"}

# STEREO/SECCHI observables on both spacecraft (A still operating; B is historical,
# 2007-2014). Like LASCO these are VSO-only (no JSOC fast path). EUVI is an EUV
# disk imager selected per wavelength; COR1/COR2 are white-light coronagraphs and
# HI1/HI2 wide-field heliospheric imagers (no wavelength). The observable value is
# a (spacecraft, detector, wavelength_or_None) tuple.
STEREO_SPACECRAFT = (("STEREO_A", "STEREO-A"), ("STEREO_B", "STEREO-B"))
STEREO_EUVI_WAVELENGTHS = (171, 195, 284, 304)
STEREO_WHITE_LIGHT_DETECTORS = (("COR1", "COR1"), ("COR2", "COR2"), ("HI1", "HI1"), ("HI2", "HI2"))

# GOES/SUVI EUV passbands (GOES-16, Level 1b by default). Observable value is the
# wavelength; sunpy ships dedicated goes-rsuvi* colormaps.
SUVI_WAVELENGTHS = (94, 131, 171, 195, 284, 304)


def _secchi_colormap_name(detector: str | None, wavelength: Any | None) -> str:
    """Colormap for a STEREO/SECCHI detector (sunpy stereocor*/stereohi*/euvi*)."""
    det = str(detector or "").strip().upper()
    if det == "COR1":
        return "stereocor1"
    if det == "COR2":
        return "stereocor2"
    if det == "HI1":
        return "stereohi1"
    if det == "HI2":
        return "stereohi2"
    try:
        rounded = int(round(float(wavelength)))
    except (TypeError, ValueError):
        rounded = 195
    return f"euvi{rounded}" if rounded in STEREO_EUVI_WAVELENGTHS else "euvi195"


def _suvi_colormap_name(wavelength: Any | None) -> str:
    """Colormap for a GOES/SUVI passband (sunpy goes-rsuvi*)."""
    try:
        rounded = int(round(float(wavelength)))
    except (TypeError, ValueError):
        rounded = 171
    return f"goes-rsuvi{rounded}" if rounded in SUVI_WAVELENGTHS else "goes-rsuvi171"


def populate_observable_combo(combo: Any) -> None:
    """Fill a QComboBox with every supported observable.

    userData is a tuple: ("AIA", wavelength_float), ("HMI", product_str),
    ("LASCO", "C2"/"C3"), ("SECCHI", (spacecraft, detector, wavelength_or_None))
    or ("SUVI", wavelength_float). Shared by the main Data Source selector and
    the Compare Viewpoint dialog so both always offer the same missions.
    """
    for value in AIA_WAVELENGTHS:
        combo.addItem(f"AIA {value} A", userData=("AIA", float(value)))
    combo.insertSeparator(combo.count())
    for product, label in HMI_OBSERVABLES:
        combo.addItem(label, userData=("HMI", product))
    combo.insertSeparator(combo.count())
    for detector, label in LASCO_DETECTORS:
        combo.addItem(label, userData=("LASCO", detector))
    combo.insertSeparator(combo.count())
    for sc_code, sc_label in STEREO_SPACECRAFT:
        for wl in STEREO_EUVI_WAVELENGTHS:
            combo.addItem(f"{sc_label}/EUVI {wl} A", userData=("SECCHI", (sc_code, "EUVI", float(wl))))
        for det_code, det_label in STEREO_WHITE_LIGHT_DETECTORS:
            combo.addItem(f"{sc_label}/{det_label}", userData=("SECCHI", (sc_code, det_code, None)))
    combo.insertSeparator(combo.count())
    for wl in SUVI_WAVELENGTHS:
        combo.addItem(f"GOES/SUVI {wl} A", userData=("SUVI", float(wl)))


class SolarMetadataWorker(QObject):
    progress = Signal(object, object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, start_dt: datetime, end_dt: datetime):
        super().__init__()
        self.start_dt = start_dt
        self.end_dt = end_dt

    @Slot()
    def run(self):
        try:
            self.progress.emit(None, "Fetching HEK/SRS active-region metadata...")
            metadata = fetch_active_region_metadata(self.start_dt, self.end_dt)
            self.progress.emit(100, f"Fetched {len(metadata)} metadata region(s).")
            self.finished.emit(metadata)
        except Exception:
            self.failed.emit(traceback.format_exc())


def _imageio_ffmpeg_available() -> bool:
    try:
        import imageio_ffmpeg  # noqa: F401

        return True
    except Exception:
        return False


class MovieExportWorker(QObject):
    """Renders and writes a movie off the UI thread, streaming one frame at a
    time so full-resolution exports stay responsive and memory-light."""

    export_progress = Signal(int, int)  # done, total
    finished = Signal(str)              # output path
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, frames: list[Any], spec: AiaMovieExportSpec):
        super().__init__()
        self._frames = list(frames)
        self._spec = spec
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    @Slot()
    def run(self):
        try:
            export_movie(
                self._frames,
                self._spec,
                progress_cb=lambda done, total: self.export_progress.emit(int(done), int(total)),
                cancel_cb=self._cancel.is_set,
            )
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.finished.emit(str(self._spec.path))
        except Exception:
            self.failed.emit(traceback.format_exc())


class MapLoadWorker(QObject):
    """Loads local AIA FITS off the UI thread, one file at a time, so uploading
    a large set of high-resolution frames does not freeze the window."""

    load_progress = Signal(int, int)            # done, total
    finished = Signal(object, object, object)   # frames, paths, metadata
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, paths: list[str]):
        super().__init__()
        self._paths = list(paths)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    @Slot()
    def run(self):
        try:
            frame_set = load_aia_maps_streaming(
                self._paths,
                progress_cb=lambda done, total: self.load_progress.emit(int(done), int(total)),
                cancel_cb=self._cancel.is_set,
            )
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.finished.emit(
                    list(frame_set.maps), list(frame_set.paths), dict(frame_set.metadata)
                )
        except Exception:
            self.failed.emit(traceback.format_exc())


class VectorFieldLoadWorker(QObject):
    """Loads local hmi.B_720s segment FITS off the UI thread and assembles
    them into vector-field time steps."""

    progress = Signal(object, object)  # value 0-100 (or None), text
    finished = Signal(object)          # list[HmiVectorFrame]
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, paths: list[str]):
        super().__init__()
        self._paths = list(paths)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    @Slot()
    def run(self):
        try:
            frames = load_vector_frames(
                self._paths,
                progress_cb=lambda done, total: self.progress.emit(
                    int(done * 100 / max(total, 1)),
                    f"Loading vector segment {done}/{total}...",
                ),
                cancel_cb=self._cancel.is_set,
            )
            if self._cancel.is_set() or not frames:
                self.cancelled.emit()
            else:
                self.finished.emit(frames)
        except Exception:
            self.failed.emit(traceback.format_exc())


class VectorFieldDownloadWorker(QObject):
    """Downloads the hmi.B_720s vector segments via the JSOC fast path and
    loads them into vector-field frames, entirely off the UI thread."""

    progress = Signal(object, object)  # value 0-100 (or None), text
    byte_progress = Signal(object)     # DownloadManager aggregate
    finished = Signal(object)          # list[HmiVectorFrame]
    no_records = Signal(object)        # newest available T_REC text ('' if unknown)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        *,
        start_dt: datetime,
        end_dt: datetime,
        cadence_seconds: int,
        email: str,
        cache_dir: Path,
    ):
        super().__init__()
        self._start_dt = start_dt
        self._end_dt = end_dt
        self._cadence = int(cadence_seconds)
        self._email = str(email)
        self._cache_dir = Path(cache_dir)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    @Slot()
    def run(self):
        try:
            from src.Backend.download_manager import DownloadItem, DownloadManager
            from src.Backend.jsoc_client import JsocEmptyRecordSetError, export_hmi_vector_urls

            self.progress.emit(2, "Resolving hmi.B_720s vector segments via JSOC...")
            try:
                export = export_hmi_vector_urls(
                    start=self._start_dt,
                    end=self._end_dt,
                    email=self._email,
                    cadence_seconds=self._cadence,
                )
            except JsocEmptyRecordSetError as exc:
                # Not a failure of the app: the window simply has no vector
                # records (yet). Let the UI offer the newest available data.
                self.no_records.emit(str(getattr(exc, "latest_trec", "") or ""))
                return
            if self._cancel.is_set():
                self.cancelled.emit()
                return

            self._cache_dir.mkdir(parents=True, exist_ok=True)
            # Every record exports one URL per segment, all with the same
            # record id — name local files record + segment so a full time
            # step never collides and the cache stays deterministic.
            items = []
            used_names: set[str] = set()
            for index, entry in enumerate(export.urls):
                segment_name = Path(str(entry.url).split("?", 1)[0]).name or str(entry.filename or "")
                record_part = re.sub(r"[^A-Za-z0-9._-]+", "_", str(entry.record or "")).strip("_.")
                name = f"{record_part}.{segment_name}" if record_part else segment_name
                if not name:
                    name = f"hmi_vector_{index:04d}.fits"
                candidate = name
                suffix = 1
                while candidate in used_names:
                    candidate = f"{suffix}_{name}"
                    suffix += 1
                used_names.add(candidate)
                items.append(
                    DownloadItem(
                        url=entry.url,
                        dest=self._cache_dir / candidate,
                        expected_size=entry.size,
                        record_id=entry.record,
                        label=segment_name,
                    )
                )

            manager = DownloadManager(max_concurrent=4, progress_interval=0.2)
            result = manager.download(
                items, progress_cb=self.byte_progress.emit, cancel_cb=self._cancel.is_set
            )
            if self._cancel.is_set() or result.cancelled:
                self.cancelled.emit()
                return
            if not result.paths:
                detail = f"\n{result.errors[0]}" if result.errors else ""
                raise RuntimeError("No hmi.B_720s segment files could be downloaded." + detail)

            self.progress.emit(86, "Assembling vector field time steps...")
            frames = load_vector_frames(
                result.paths,
                progress_cb=lambda done, total: self.progress.emit(
                    86 + int(done * 13 / max(total, 1)),
                    f"Reading vector segment {done}/{total}...",
                ),
                cancel_cb=self._cancel.is_set,
            )
            if self._cancel.is_set() or not frames:
                self.cancelled.emit()
            else:
                self.finished.emit(frames)
        except Exception:
            self.failed.emit(traceback.format_exc())


class SolarMatplotlibCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None, theme: Any | None = None):
        super().__init__(parent)
        self.theme = theme
        self._axis_transform = self._default_axis_transform()
        self._colormap_name = "inferno"
        self._colorbar_visible = True
        self._image_artist = None
        self._colorbar_artist = None
        self._overlay_artists: list[Any] = []
        self._regions: list[Any] = []
        self._limb_x: np.ndarray | None = None
        self._limb_y: np.ndarray | None = None
        self._limb_visible = False
        self._vector_geometry: Any | None = None
        self._graticule_polylines: list[tuple[np.ndarray, np.ndarray]] = []
        self._graticule_labels: list[tuple[str, float, float]] = []
        self._graticule_visible = False
        self._last_plot: dict[str, Any] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.figure = Figure(figsize=(7, 7), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)
        self.ax = self.figure.add_subplot(111)
        self.apply_theme()

    def backend_name(self) -> str:
        return "matplotlib"

    def apply_theme(self) -> None:
        dark = self._is_dark_ui()
        bg = "#0c0c0c" if dark else "#ffffff"
        fg = "#e1e8f0" if dark else "#1e2a38"
        self.figure.patch.set_facecolor(bg)
        if getattr(self, "ax", None) is not None:
            self.ax.set_facecolor(bg)
            self.ax.tick_params(colors=fg)
            self.ax.xaxis.label.set_color(fg)
            self.ax.yaxis.label.set_color(fg)
            self.ax.title.set_color(fg)
            for spine in self.ax.spines.values():
                spine.set_color(fg)
        self.canvas.draw_idle()

    def _is_dark_ui(self) -> bool:
        theme = getattr(self, "theme", None)
        if theme is not None and hasattr(theme, "is_dark"):
            try:
                return bool(theme.is_dark())
            except Exception:
                pass
        return self.palette().color(self.backgroundRole()).lightness() < 128

    def set_colormap_name(self, name: str) -> None:
        self._colormap_name = str(name or "inferno").strip() or "inferno"
        if self._last_plot is not None:
            self.plot_map_data(**self._last_plot)

    def colormap_name(self) -> str:
        return self._colormap_name

    def set_colorbar_visible(self, visible: bool) -> None:
        self._colorbar_visible = bool(visible)
        if self._last_plot is not None:
            self.plot_map_data(**self._last_plot)

    def has_visible_colorbar(self) -> bool:
        return self._colorbar_artist is not None

    def reset_map_view(self) -> None:
        # Matplotlib re-applies set_xlim/set_ylim to the data extent on every
        # render, so the axes always match the current (cropped) range. Provided
        # for interface parity with the PyQtGraph canvas.
        return None

    def set_grid_visible(self, visible: bool) -> None:
        # The "Coordinate Grid" control now draws the curvilinear solar-coordinate
        # graticule (see set_solar_graticule); keep the plain rectilinear grid off
        # so the two do not overlap.
        if getattr(self, "ax", None) is not None:
            self.ax.grid(False)
            self.canvas.draw_idle()

    def clear_plot(self) -> None:
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self._image_artist = None
        self._colorbar_artist = None
        self._overlay_artists = []
        self._vector_geometry = None
        self._last_plot = None
        self.apply_theme()

    def has_plot_content(self) -> bool:
        return self._image_artist is not None

    def set_map_title(self, title: str) -> None:
        """Set the graph title without a full re-render (used for the empty
        'No image data loaded.' state)."""
        if getattr(self, "ax", None) is None:
            return
        fg = "#e1e8f0" if self._is_dark_ui() else "#1e2a38"
        self.ax.set_title(str(title), color=fg)
        self.canvas.draw_idle()

    def plot_map_data(
        self,
        image_data: np.ndarray,
        title: str,
        *,
        vmin: float | None = None,
        vmax: float | None = None,
        axis_transform: dict[str, float] | None = None,
    ) -> None:
        self._last_plot = {
            "image_data": np.asarray(image_data),
            "title": title,
            "vmin": vmin,
            "vmax": vmax,
            "axis_transform": dict(axis_transform or self._default_axis_transform()),
        }
        self._axis_transform = dict(axis_transform or self._default_axis_transform())
        arr = np.asarray(image_data)
        is_rgb = bool(arr.ndim == 3 and arr.shape[-1] in (3, 4))
        x0, y0, width, height = self._map_rect_from_transform(arr.shape)
        extent = (x0, x0 + width, y0, y0 + height)

        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        dark = self._is_dark_ui()
        bg = "#0c0c0c" if dark else "#ffffff"
        fg = "#e1e8f0" if dark else "#1e2a38"
        self.figure.patch.set_facecolor(bg)
        self.ax.set_facecolor(bg)
        self.ax.set_box_aspect(1)

        if is_rgb:
            self._image_artist = self.ax.imshow(arr, origin="lower", extent=extent, interpolation="nearest")
            self._colorbar_artist = None
        else:
            cmap = self._matplotlib_colormap(self._colormap_name)
            self._image_artist = self.ax.imshow(
                np.asarray(arr, dtype=float),
                origin="lower",
                extent=extent,
                interpolation="nearest",
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
            )
            if self._colorbar_visible:
                self._colorbar_artist = self.figure.colorbar(self._image_artist, ax=self.ax, fraction=0.046, pad=0.035)
                self._colorbar_artist.set_label("Intensity", color=fg)
                self._colorbar_artist.ax.tick_params(colors=fg)
                self._colorbar_artist.outline.set_edgecolor(fg)
            else:
                self._colorbar_artist = None

        self.ax.set_title(title, color=fg)
        self.ax.set_xlabel("Solar X (arcsec)", color=fg)
        self.ax.set_ylabel("Solar Y (arcsec)", color=fg)
        self.ax.tick_params(colors=fg)
        for spine in self.ax.spines.values():
            spine.set_color(fg)
        # The rectilinear grid is replaced by the solar-coordinate graticule,
        # which is drawn (when enabled) as part of _draw_overlays below.
        self.ax.grid(False)
        # Square the view around the data centre so the equal-aspect image, the
        # square box and the axis limits all agree (otherwise matplotlib warns
        # and drops the y-limits to satisfy the fixed data aspect).
        ex0, ex1 = min(extent[0], extent[1]), max(extent[0], extent[1])
        ey0, ey1 = min(extent[2], extent[3]), max(extent[2], extent[3])
        half = (max(ex1 - ex0, ey1 - ey0) / 2.0) or 1.0
        cx = (ex0 + ex1) / 2.0
        cy = (ey0 + ey1) / 2.0
        self.ax.set_xlim(cx - half, cx + half)
        self.ax.set_ylim(cy - half, cy + half)
        self._draw_overlays()
        self.figure.subplots_adjust(left=0.10, right=0.88 if self._colorbar_artist else 0.94, bottom=0.10, top=0.92)
        self.canvas.draw_idle()

    def _matplotlib_colormap(self, name: str):
        text = str(name or "inferno").strip() or "inferno"
        try:
            import sunpy.visualization.colormaps  # noqa: F401
        except Exception:
            pass
        try:
            cmap = mpl_colormaps.get_cmap(text)
        except Exception:
            cmap = self._fallback_matplotlib_colormap(text)
        try:
            cmap = cmap.copy()
        except Exception:
            cmap = ListedColormap(cmap(np.linspace(0, 1, 256)))
        return cmap

    def _fallback_matplotlib_colormap(self, name: str):
        palettes = {
            "sdoaia94": ((0, 0, 0), (16, 91, 64), (64, 142, 128), (145, 196, 192), (255, 255, 255)),
            "sdoaia131": ((0, 0, 0), (0, 92, 92), (15, 185, 185), (136, 255, 255), (255, 255, 255)),
            "sdoaia171": ((0, 0, 0), (92, 64, 0), (185, 128, 0), (255, 192, 7), (255, 255, 255)),
            "sdoaia193": ((0, 0, 0), (128, 64, 16), (181, 128, 64), (221, 192, 145), (255, 255, 255)),
            "sdoaia211": ((0, 0, 0), (128, 64, 91), (181, 128, 142), (221, 192, 196), (255, 255, 255)),
            "sdoaia304": ((0, 0, 0), (70, 0, 18), (170, 28, 20), (255, 128, 34), (255, 244, 180)),
            "sdoaia335": ((0, 0, 0), (16, 64, 128), (64, 128, 181), (145, 192, 221), (255, 255, 255)),
            "sdoaia1600": ((0, 0, 0), (91, 91, 16), (142, 142, 64), (196, 196, 145), (255, 255, 255)),
            "sdoaia1700": ((0, 0, 0), (128, 64, 64), (181, 128, 128), (221, 192, 192), (255, 255, 255)),
            "soholasco2": ((0, 0, 0), (20, 20, 90), (30, 90, 165), (120, 185, 220), (255, 255, 255)),
            "soholasco3": ((0, 0, 0), (60, 20, 12), (150, 62, 22), (222, 150, 60), (255, 252, 220)),
        }
        colors = np.asarray(palettes.get(str(name or "").lower(), palettes["sdoaia193"]), dtype=float) / 255.0
        stops = np.linspace(0.0, 1.0, colors.shape[0])
        samples = np.linspace(0.0, 1.0, 256)
        rgb = np.column_stack([np.interp(samples, stops, colors[:, channel]) for channel in range(3)])
        return ListedColormap(rgb)

    def set_aia_limb_overlay(
        self,
        x_arcsec: np.ndarray | None,
        y_arcsec: np.ndarray | None,
        *,
        visible: bool,
    ) -> None:
        self._limb_x = None if x_arcsec is None else np.asarray(x_arcsec, dtype=float)
        self._limb_y = None if y_arcsec is None else np.asarray(y_arcsec, dtype=float)
        self._limb_visible = bool(visible)
        self._draw_overlays()
        self.canvas.draw_idle()

    def has_aia_limb_overlay(self) -> bool:
        return bool(self._limb_visible and self._limb_x is not None and self._limb_x.size >= 3)

    def set_region_overlays(self, regions: list[Any] | None, *, visible: bool = True) -> None:
        self._regions = list(regions or []) if visible else []
        self._draw_overlays()
        self.canvas.draw_idle()

    def region_overlay_count(self) -> int:
        return len(self._regions)

    def set_vector_field_overlay(self, geometry: Any | None) -> None:
        self._vector_geometry = geometry
        self._draw_overlays()
        self.canvas.draw_idle()

    def set_solar_graticule(
        self,
        polylines: Any | None,
        labels: Any | None = None,
        *,
        visible: bool = True,
    ) -> None:
        if visible and polylines:
            self._graticule_polylines = [
                (np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)) for xs, ys in polylines
            ]
            self._graticule_labels = list(labels or [])
            self._graticule_visible = True
        else:
            self._graticule_polylines = []
            self._graticule_labels = []
            self._graticule_visible = False
        self._draw_overlays()
        self.canvas.draw_idle()

    def has_solar_graticule(self) -> bool:
        return bool(self._graticule_visible and self._graticule_polylines)

    def has_vector_field_overlay(self) -> bool:
        geometry = self._vector_geometry
        if geometry is None:
            return False
        is_empty = getattr(geometry, "is_empty", None)
        if callable(is_empty):
            try:
                return not bool(is_empty())
            except Exception:
                return True
        return True

    def _draw_overlays(self) -> None:
        if getattr(self, "ax", None) is None:
            return
        for artist in list(self._overlay_artists):
            try:
                artist.remove()
            except Exception:
                pass
        self._overlay_artists = []
        if self._graticule_visible and self._graticule_polylines:
            for xs, ys in self._graticule_polylines:
                if xs.size < 2 or xs.size != ys.size or not np.any(np.isfinite(xs) & np.isfinite(ys)):
                    continue
                # NaN gaps break the curve cleanly at the limb (near/far side).
                (line,) = self.ax.plot(xs, ys, color="#96c8ff", linewidth=0.8, linestyle=":", alpha=0.6, zorder=2)
                self._overlay_artists.append(line)
            for label in self._graticule_labels:
                try:
                    text, lx, ly = label
                    lx = float(lx)
                    ly = float(ly)
                except Exception:
                    continue
                if not (np.isfinite(lx) and np.isfinite(ly)):
                    continue
                item = self.ax.text(lx, ly, str(text), color="#aad2ff", fontsize=7, ha="center", va="center", zorder=2)
                self._overlay_artists.append(item)
        if self._limb_visible and self._limb_x is not None and self._limb_y is not None:
            (line,) = self.ax.plot(self._limb_x, self._limb_y, color="#45ff9a", linewidth=1.4)
            self._overlay_artists.append(line)
        geometry = self._vector_geometry
        if geometry is not None:
            rgba = getattr(geometry, "magnitude_rgba", None)
            rect = getattr(geometry, "magnitude_rect", None)
            if rgba is not None and rect is not None:
                x0, y0, width, height = [float(v) for v in rect]
                artist = self.ax.imshow(
                    np.asarray(rgba, dtype=np.uint8),
                    origin="lower",
                    extent=(x0, x0 + width, y0, y0 + height),
                    interpolation="nearest",
                    zorder=2,
                )
                self._overlay_artists.append(artist)
            # NaN-separated polylines: matplotlib breaks lines at NaN natively.
            for x_data, y_data, color, lw in (
                (getattr(geometry, "stream_x", None), getattr(geometry, "stream_y", None), "#ffd24d", 0.9),
                (getattr(geometry, "arrows_pos_x", None), getattr(geometry, "arrows_pos_y", None), "#ff5050", 1.1),
                (getattr(geometry, "arrows_neg_x", None), getattr(geometry, "arrows_neg_y", None), "#509aff", 1.1),
            ):
                if x_data is None or y_data is None:
                    continue
                x_arr = np.asarray(x_data, dtype=float)
                y_arr = np.asarray(y_data, dtype=float)
                if x_arr.size == 0 or x_arr.size != y_arr.size:
                    continue
                (line,) = self.ax.plot(x_arr, y_arr, color=color, linewidth=lw, zorder=3)
                self._overlay_artists.append(line)
        for region in self._regions:
            bbox = getattr(region, "bbox", None)
            if not bbox or len(bbox) != 4:
                continue
            try:
                x0, x1, y0, y1 = [float(v) for v in bbox]
            except Exception:
                continue
            ax0, ay0 = self.map_arcsec_from_pixel(x0, y0)
            ax1, ay1 = self.map_arcsec_from_pixel(x1, y1)
            x_low, x_high = sorted((ax0, ax1))
            y_low, y_high = sorted((ay0, ay1))
            rect = Rectangle((x_low, y_low), x_high - x_low, y_high - y_low, fill=False, edgecolor="#00dcff", linewidth=1.2)
            self.ax.add_patch(rect)
            self._overlay_artists.append(rect)
            label = str(getattr(region, "label", "") or f"R{getattr(region, 'region_id', '')}").strip()
            if label:
                text = self.ax.text(x_low, y_high, label, color="#00dcff", va="bottom", fontsize=8)
                self._overlay_artists.append(text)

    def map_axis_labels(self) -> tuple[str, str]:
        return ("Solar X (arcsec)", "Solar Y (arcsec)")

    def map_background_lightness(self) -> int:
        color = np.asarray(self.figure.get_facecolor()[:3], dtype=float)
        return int(round(float(np.mean(color) * 255.0)))

    def map_low_color_lightness(self) -> int:
        rgba = np.asarray(self._matplotlib_colormap(self._colormap_name)(0.0)[:3], dtype=float)
        return int(round(float(np.mean(rgba) * 255.0)))

    def map_viewbox_size(self) -> tuple[int, int]:
        try:
            bbox = self.ax.get_window_extent().transformed(self.figure.dpi_scale_trans.inverted())
            return int(round(bbox.width * self.figure.dpi)), int(round(bbox.height * self.figure.dpi))
        except Exception:
            return int(self.canvas.width()), int(self.canvas.height())

    def map_view_rect(self) -> tuple[float, float, float, float]:
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        return (float(min(x0, x1)), float(min(y0, y1)), float(abs(x1 - x0)), float(abs(y1 - y0)))

    def map_arcsec_from_pixel(self, x_pix: float, y_pix: float) -> tuple[float, float]:
        tx = self._axis_transform
        x_arc = tx["x_ref_arcsec"] + (float(x_pix) - tx["x_ref_pix"]) * tx["x_scale_arcsec_per_pix"]
        y_arc = tx["y_ref_arcsec"] + (float(y_pix) - tx["y_ref_pix"]) * tx["y_scale_arcsec_per_pix"]
        return float(x_arc), float(y_arc)

    def save_plot(self, path: str) -> None:
        self.figure.savefig(path, facecolor=self.figure.get_facecolor(), bbox_inches="tight")

    def _map_rect_from_transform(self, shape: tuple[int, ...]) -> tuple[float, float, float, float]:
        ny = int(shape[0]) if len(shape) >= 1 else 1
        nx = int(shape[1]) if len(shape) >= 2 else 1
        tx = self._axis_transform
        x_scale = float(tx.get("x_scale_arcsec_per_pix", 1.0))
        y_scale = float(tx.get("y_scale_arcsec_per_pix", 1.0))
        x_ref_pix = float(tx.get("x_ref_pix", 0.0))
        y_ref_pix = float(tx.get("y_ref_pix", 0.0))
        x_ref_arcsec = float(tx.get("x_ref_arcsec", 0.0))
        y_ref_arcsec = float(tx.get("y_ref_arcsec", 0.0))
        x0 = x_ref_arcsec - (x_ref_pix + 0.5) * x_scale
        y0 = y_ref_arcsec - (y_ref_pix + 0.5) * y_scale
        return float(x0), float(y0), float(nx) * x_scale, float(ny) * y_scale

    def _default_axis_transform(self) -> dict[str, float]:
        return {
            "x_ref_pix": 0.0,
            "y_ref_pix": 0.0,
            "x_scale_arcsec_per_pix": 1.0,
            "y_scale_arcsec_per_pix": 1.0,
            "x_ref_arcsec": 0.0,
            "y_ref_arcsec": 0.0,
        }


class PercentSlider(QWidget):
    """Horizontal slider for a 0–100% value with a live readout.

    A drop-in for the clip-percentile spin boxes: it exposes ``value()`` /
    ``setValue()`` in float percent and a ``valueChanged(float)`` signal, with
    0.1% resolution so image contrast can be tuned smoothly by dragging.
    """

    valueChanged = Signal(float)

    def __init__(self, value: float, *, minimum: float = 0.0, maximum: float = 100.0, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(int(round(minimum * 10)), int(round(maximum * 10)))
        self.slider.setSingleStep(1)   # 0.1%
        self.slider.setPageStep(10)    # 1%
        self.readout = QLabel(self)
        self.readout.setMinimumWidth(46)
        self.readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.readout, 0)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.setValue(value)

    def _on_slider_changed(self, raw: int) -> None:
        val = raw / 10.0
        self.readout.setText(f"{val:.1f}%")
        self.valueChanged.emit(val)

    def value(self) -> float:
        return self.slider.value() / 10.0

    def setValue(self, value: float) -> None:
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(int(round(float(value) * 10)))
        self.slider.blockSignals(blocked)
        self.readout.setText(f"{self.value():.1f}%")

    def setRange(self, minimum: float, maximum: float) -> None:
        self.slider.setRange(int(round(minimum * 10)), int(round(maximum * 10)))


class RegionLightcurveDialog(QDialog):
    """Plots a region light curve (DN/s vs time) with optional radio overlay.

    This is the cross-instrument view: the intensity time profile over a
    region, with the e-Callisto radio burst window shaded so the timing of the
    EUV brightening relative to the radio burst onset can be read directly.
    """

    def __init__(
        self,
        lightcurve: Any,
        *,
        radio_window: tuple[datetime, datetime] | None = None,
        instrument_label: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Region Light Curve")
        self.resize(760, 460)
        self._instrument_label = str(instrument_label or "").strip()
        layout = QVBoxLayout(self)
        self._figure = Figure(figsize=(6.5, 3.8))
        self.canvas = FigureCanvas(self._figure)
        layout.addWidget(self.canvas)
        self._plot(lightcurve, radio_window)

    def _plot(self, lc: Any, radio_window: tuple[datetime, datetime] | None) -> None:
        ax = self._figure.add_subplot(111)
        pairs = [
            (t, v)
            for t, v in zip(lc.times, np.asarray(lc.values, dtype=float))
            if t is not None and np.isfinite(v)
        ]
        if not pairs:
            ax.text(0.5, 0.5, "No time-stamped frames to plot.", ha="center", va="center",
                    transform=ax.transAxes)
            self.canvas.draw_idle()
            return

        times = [p[0] for p in pairs]
        values = [p[1] for p in pairs]
        source = " ".join(
            part for part in (self._instrument_label or "AIA", str(lc.wavelength or "").strip()) if part
        ).strip()
        label = source + f" · {lc.statistic} {lc.unit}"
        ax.plot(times, values, marker="o", markersize=3, linewidth=1.3, color="#e8a33d", label=label)

        peak_time = lc.peak_time()
        title = f"{source} region light curve ({lc.unit})".replace("  ", " ").strip()
        if peak_time is not None and peak_time in times:
            peak_val = values[times.index(peak_time)]
            ax.axvline(peak_time, color="#d04545", linestyle="--", linewidth=1.0, alpha=0.8)
            ax.annotate("EUV peak", xy=(peak_time, peak_val), xytext=(4, 4),
                        textcoords="offset points", fontsize=8, color="#d04545")

        if radio_window:
            r_start, r_end = radio_window
            ax.axvspan(r_start, r_end, color="#5a8fd6", alpha=0.15, label="Radio burst window")
            ax.axvline(r_start, color="#5a8fd6", linewidth=1.0)
            lag = radio_euv_lag(r_start, peak_time)
            if lag is not None:
                title += f"  ·  EUV peak {lag:+.0f}s vs radio onset"

        ax.set_xlabel("Time (UTC)")
        ax.set_ylabel(lc.unit)
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, loc="best")
        try:
            self._figure.autofmt_xdate()
        except Exception:
            pass
        self._figure.tight_layout()
        self.canvas.draw_idle()


class SolarDataAnalysisWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(SOLAR_WINDOW_TITLE)
        fit_window_to_screen(self, 1440, 900)

        self.theme = _get_theme()
        self.cache_dir = _default_cache_dir()

        self._search_result: SunPySearchResult | None = None
        self._loaded_paths: list[str] = []
        # Self-contained analysis session (.ecsolar) state. When opening a
        # session the frame load is asynchronous, so the restore payload is
        # stashed here and applied once _apply_loaded_frames lands.
        self._session_path: str | None = None
        self._pending_session_restore: dict[str, Any] | None = None
        # Whether the loaded frames are currently cropped, and the arcsec bounds
        # that were applied (frozen at Apply Crop time so a session restores the
        # exact crop even if the spin fields are edited afterwards).
        self._crop_applied = False
        self._applied_crop_arcsec: list[float] | None = None
        self._original_frames: list[Any] = []
        self._map_frames: list[Any] = []
        self._map_metadata: dict[str, Any] = {}
        self._regions: list[AiaRegion] = []
        self._metadata_regions: list[Any] = []
        self._current_frame_index = 0
        self._current_map_data: np.ndarray | None = None
        self._current_axis_transform: dict[str, float] = self._default_axis_transform()
        # Observation-config bookkeeping for the loaded sequence (see
        # partition_frames_by_config / exposures_differ).
        self._loaded_config_key: tuple | None = None
        self._exposure_varies = False
        self._active_thread: QThread | None = None
        self._active_worker: QObject | None = None
        self._save_target_dir: str | None = None
        self._pending_latest = False
        self._helioviewer_dialog: Any | None = None
        self._overlay_magnetogram: Any | None = None
        self._vector_frames: list[Any] = []
        self._vector_geometry_cache: dict[Any, Any] = {}
        self._pending_vector_download = False
        self._busy = False
        self._progress_target = 0
        self._progress_value = 0
        self._progress_activity = False
        self._progress_soft_cap = 0
        self._progress_last_pulse = 0.0
        self._byte_active = False
        self._byte_bar_value = 0
        self._pending_close = False
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(24)
        self._progress_timer.timeout.connect(self._tick_progress)

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self.next_frame)

        # Throttle live clip-slider re-renders to ~30 fps so dragging stays smooth.
        self._clip_render_pending = False
        self._clip_render_timer = QTimer(self)
        self._clip_render_timer.setInterval(33)
        self._clip_render_timer.timeout.connect(self._flush_clip_render)

        self._build_ui()
        self._build_menu_bar()
        # Click-driven measurement tools (ruler / profile / height-time / stats).
        from src.UI.solar_measure_tools import MeasurementController

        self._measure = MeasurementController(self)
        self.pyqt_canvas.set_click_callback(self._measure.on_canvas_click)
        self._connect_signals()
        self._restore_jsoc_settings()
        self.jsoc_email_edit.editingFinished.connect(self._save_jsoc_settings)
        self.source_combo.currentIndexChanged.connect(lambda _i: self._save_jsoc_settings())
        if self.theme is not None and hasattr(self.theme, "themeChanged"):
            try:
                self.theme.themeChanged.connect(lambda _dark: self._apply_sidebar_style())
            except Exception:
                pass
        self.use_analyzer_time_window(auto_query=False)
        self._set_loaded_state(False)
        self._apply_observable_download_gating()
        self._apply_instrument_visibility()
        self._update_size_estimate()
        # Restore the sidebar collapse choice from the previous session.
        try:
            collapsed = self._app_settings().value("solar_analysis/sidebar_collapsed", False)
            collapsed = str(collapsed).strip().lower() in ("true", "1", "yes")
        except Exception:
            collapsed = False
        if collapsed:
            self._set_sidebar_collapsed(True, animate=False)
        # Quick-start guidance in the details drawer until the first result
        # replaces it; mirrors the numbered sidebar workflow.
        self.analysis_text.setPlainText(
            "Welcome to Solar Image Analysis.\n"
            "  1. Data Source — pick an observable and time range, then Fetch Records\n"
            "     (or Upload FITS to load local files directly).\n"
            "  2. Archive Results — check the rows you want and click Load Selected.\n"
            "  3. Analysis — plot, difference, composite, measure and track CMEs;\n"
            "     use the playback bar under the image to step through the sequence.\n"
            "Tip: hover the image for solar coordinates (HCI lon/lat, R☉, position angle, pixel)."
        )
        self.statusBar().showMessage("Ready.")

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

        # The sidebar was designed 520-680 px wide; on laptop screens that
        # starves the plot, so scale it down with the available width.
        avail = screen_available_geometry(self)
        if avail is not None and avail.width() < 1440:
            self._sidebar_min_width = max(360, int(avail.width() * 0.34))
        else:
            self._sidebar_min_width = 520
        self._sidebar_max_width = self._sidebar_min_width + 160
        self.controls_scroll = QScrollArea(self)
        self.controls_scroll.setObjectName("SolarControlsScroll")
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.controls_scroll.setMinimumWidth(self._sidebar_min_width)
        self.controls_scroll.setMaximumWidth(self._sidebar_max_width)
        controls_panel = QWidget()
        controls_panel.setObjectName("SolarControlsPanel")
        controls_panel.setMinimumWidth(max(340, self._sidebar_min_width - 20))
        self.controls_panel = controls_panel
        self.controls_scroll.setWidget(controls_panel)
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(14, 12, 14, 16)
        controls_layout.setSpacing(12)

        # The sidebar reads top-to-bottom as the workflow: get data, pick the
        # records, analyse — then per-topic display/export/instrument cards.
        self._build_data_source_group(controls_layout)
        self._build_archive_results_group(controls_layout)
        self._build_mode_group(controls_layout)
        self._build_plot_controls_group(controls_layout)
        self._build_movie_group(controls_layout)
        self._build_coronagraph_group(controls_layout)
        self._build_hi_group(controls_layout)
        self._build_vector_field_group(controls_layout)
        self._build_region_group(controls_layout)
        controls_layout.addStretch(1)

        # Viewer pane: header bar, measurement toolbar, canvas (+ tracking
        # panel), a video-player-style playback bar, and the details drawer.
        viewer_panel = QWidget()
        viewer_panel.setObjectName("SolarViewerPanel")
        # Left edge of the viewer: a full-height collapse handle for the
        # sidebar (VS Code style) — impossible to miss, one click either way.
        outer_layout = QHBoxLayout(viewer_panel)
        outer_layout.setContentsMargins(3, 8, 10, 8)
        outer_layout.setSpacing(5)
        self.sidebar_toggle_btn = QToolButton()
        self.sidebar_toggle_btn.setObjectName("SolarSidebarHandle")
        self.sidebar_toggle_btn.setArrowType(Qt.LeftArrow)
        self.sidebar_toggle_btn.setAutoRaise(True)
        self.sidebar_toggle_btn.setFixedWidth(18)
        self.sidebar_toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.sidebar_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.sidebar_toggle_btn.setToolTip("Hide the controls sidebar")
        self.sidebar_toggle_btn.clicked.connect(
            lambda: self._set_sidebar_collapsed(not getattr(self, "_sidebar_collapsed", False))
        )
        outer_layout.addWidget(self.sidebar_toggle_btn)
        plot_layout = QVBoxLayout()
        plot_layout.setSpacing(8)
        outer_layout.addLayout(plot_layout, 1)

        header_bar = QWidget()
        header_bar.setObjectName("SolarViewerHeader")
        top_row = QHBoxLayout(header_bar)
        top_row.setContentsMargins(12, 7, 10, 7)
        top_row.setSpacing(10)
        # The frame title (instrument/wavelength/time) is shown at the top of the
        # graph itself, so the header bar carries ONLY the live coordinate readout
        # — on small screens there isn't room for both, and the readout must stay
        # legible while the cursor moves. This label is kept (hidden) as the
        # canonical current-title store used elsewhere.
        self.plot_title_label = QLabel("No image data loaded.", header_bar)
        self.plot_title_label.setObjectName("SolarPlotTitle")
        self.plot_title_label.hide()
        # Live cursor position: R☉ + position angle + pixel.
        self.coord_readout_label = QLabel("")
        self.coord_readout_label.setObjectName("SolarCoordReadout")
        self.coord_readout_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.quick_mp4_btn = QPushButton("Export MP4")
        self.quick_mp4_btn.setEnabled(False)
        self.quick_mp4_btn.setToolTip(
            "One-click MP4 of the loaded sequence with the current display settings."
        )
        top_row.addWidget(self.coord_readout_label, 1)
        top_row.addWidget(self.quick_mp4_btn)
        plot_layout.addWidget(header_bar)

        # Measurement toolbar: click-driven tools on the displayed map.
        measure_bar = QWidget()
        measure_bar.setObjectName("SolarMeasureBar")
        measure_row = QHBoxLayout(measure_bar)
        measure_row.setContentsMargins(2, 0, 2, 0)
        measure_row.setSpacing(6)
        self.ruler_tool_btn = QPushButton("Ruler")
        self.ruler_tool_btn.setCheckable(True)
        self.ruler_tool_btn.setToolTip(
            "Click two points to measure the plane-of-sky distance (arcsec / Mm / R☉)\n"
            "and position angle (N→E)."
        )
        self.profile_tool_btn = QPushButton("Profile")
        self.profile_tool_btn.setCheckable(True)
        self.profile_tool_btn.setToolTip(
            "Click two points to plot the intensity along the cut (e.g. across a\n"
            "loop, filament or CME front)."
        )
        self.stats_tool_btn = QPushButton("Region Stats")
        self.stats_tool_btn.setToolTip(
            "Summarise the crop rectangle: pixel count, mean/median/min/max/σ and\n"
            "the intensity-weighted centroid (arcsec). Enable Rectangle crop to\n"
            "choose the region first."
        )
        self.height_time_btn = QPushButton("Track CME")
        self.height_time_btn.setCheckable(True)
        self.height_time_btn.setToolTip(
            "Continuous CME tracking: click the leading edge; the frame advances\n"
            "automatically and each pick lands in the tracking table with a live\n"
            "height–time fit. Works on any imager (EUVI/AIA/SUVI/COR/LASCO/HI)."
        )
        self.clear_measure_btn = QPushButton("Clear")
        self.clear_measure_btn.setToolTip("Clear and reset all measurements: picks, table and overlays.")
        for btn in (
            self.ruler_tool_btn,
            self.profile_tool_btn,
            self.stats_tool_btn,
            self.height_time_btn,
            self.clear_measure_btn,
        ):
            btn.setEnabled(False)
        # Master switch: the measurement tools and the CME tracking panel stay
        # unavailable until this is ticked (the panel is greyed out, not hidden).
        self.measurements_check = QCheckBox("Measurements")
        self.measurements_check.setToolTip(
            "Enable the measurement tools (ruler, profile, region stats, CME\n"
            "tracking) and make the CME tracking panel on the right available."
        )
        self.measurements_check.toggled.connect(self._on_measurements_toggled)
        measure_row.addWidget(self.measurements_check)
        measure_row.addWidget(self.ruler_tool_btn)
        measure_row.addWidget(self.profile_tool_btn)
        measure_row.addWidget(self.stats_tool_btn)
        measure_row.addWidget(self.height_time_btn)
        measure_row.addWidget(self.clear_measure_btn)
        measure_row.addStretch(1)
        # Interactive pan/zoom of the loaded image; the zoom is kept while the
        # movie plays so a sequence can be reviewed zoomed-in.
        self.pan_zoom_check = QCheckBox("Pan / Zoom")
        self.pan_zoom_check.setToolTip(
            "Drag to pan and scroll to zoom the loaded image. The zoom is kept as\n"
            "the movie plays; untick to snap back to the full frame."
        )
        self.pan_zoom_check.setEnabled(False)
        self.pan_zoom_check.toggled.connect(self._on_pan_zoom_toggled)
        measure_row.addWidget(self.pan_zoom_check)
        plot_layout.addWidget(measure_bar)

        self.pyqt_canvas = SunPyPlotCanvas(theme=self.theme, enable_colorbar=True)
        self.pyqt_canvas.map_plot.showGrid(x=True, y=True, alpha=0.25)
        self.pyqt_canvas.set_roi_callback(self._on_crop_roi_selected)
        self.pyqt_canvas.set_hover_callback(self._on_canvas_hover)
        self.matplotlib_canvas = SolarMatplotlibCanvas(theme=self.theme)
        self.canvas = self.pyqt_canvas
        self.plot_canvas_stack = QStackedWidget()
        self.plot_canvas_stack.addWidget(self.pyqt_canvas)
        self.plot_canvas_stack.addWidget(self.matplotlib_canvas)

        # Canvas | CME-tracking panel (table + live height-time plot). The panel
        # is always part of the layout so the viewer stays fully filled; it is
        # merely disabled (greyed out) until the Measurements switch is ticked.
        from src.UI.solar_measure_tools import TrackingPanel

        self.tracking_panel = TrackingPanel(self)
        self.tracking_panel.setEnabled(False)
        # Back-compat aliases: the fit/clear controls used to live in the
        # Coronagraph Tools group and are referenced by name elsewhere.
        self.ht_fit_btn = self.tracking_panel.fit_btn
        self.ht_clear_btn = self.tracking_panel.clear_btn
        self.plot_splitter = QSplitter(Qt.Horizontal)
        self.plot_splitter.addWidget(self.plot_canvas_stack)
        self.plot_splitter.addWidget(self.tracking_panel)
        # Both panes stretch with the window so no dead gutter is left on the
        # right; the map keeps the larger share.
        self.plot_splitter.setStretchFactor(0, 3)
        self.plot_splitter.setStretchFactor(1, 1)
        self.plot_splitter.setCollapsible(1, False)
        self.plot_splitter.setSizes([1000, 380])

        # Playback bar: transport controls live directly under the image like
        # a video player, so stepping through a sequence never means hunting
        # through the sidebar.
        playback_bar = self._build_playback_bar()

        canvas_area = QWidget()
        canvas_layout = QVBoxLayout(canvas_area)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(6)
        canvas_layout.addWidget(self.plot_splitter, 1)
        canvas_layout.addWidget(playback_bar)

        # Details drawer: an IDE-style output panel under the canvas. The header
        # is always visible; the body collapses with the arrow and its height is
        # drag-adjustable through the vertical splitter handle.
        details_container = QWidget()
        details_container.setObjectName("SolarDetailsPanel")
        details_layout = QVBoxLayout(details_container)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(2)
        self.details_toggle_btn = QToolButton()
        self.details_toggle_btn.setText("Details")
        self.details_toggle_btn.setCheckable(True)
        self.details_toggle_btn.setChecked(True)
        self.details_toggle_btn.setArrowType(Qt.DownArrow)
        self.details_toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.details_toggle_btn.setAutoRaise(True)
        self.details_toggle_btn.setToolTip(
            "Operation results and warnings. Drag the divider above to resize; "
            "click to collapse."
        )
        self.details_toggle_btn.toggled.connect(self._on_details_toggled)
        details_layout.addWidget(self.details_toggle_btn)
        self.analysis_text = QTextEdit()
        self.analysis_text.setReadOnly(True)
        # Never squeeze below ~4 readable lines while expanded; the splitter
        # handle adjusts anything above that and the header collapses the rest.
        self.analysis_text.setMinimumHeight(72)
        details_layout.addWidget(self.analysis_text, 1)
        self.details_container = details_container

        self.content_splitter = QSplitter(Qt.Vertical)
        self.content_splitter.addWidget(canvas_area)
        self.content_splitter.addWidget(details_container)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.setCollapsible(0, False)
        self.content_splitter.setSizes([760, 110])
        plot_layout.addWidget(self.content_splitter, 1)

        self.progress_panel = DownloadProgressPanel(self)
        self.progress_panel.setVisible(False)
        # Existing progress logic drives the bar directly; keep a reference for
        # back-compat while the panel adds the honest byte-level read-out.
        self.progress = self.progress_panel.bar
        plot_layout.addWidget(self.progress_panel)

        splitter.addWidget(self.controls_scroll)
        splitter.addWidget(viewer_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 1040])
        self.main_splitter = splitter
        self._make_sidebar_groups_collapsible()
        self._apply_sidebar_style()

    def _build_playback_bar(self) -> QWidget:
        """Transport bar under the canvas: step buttons, frame scrubber, FPS."""
        bar = QWidget()
        bar.setObjectName("SolarPlaybackBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(4)

        self.rewind_btn = QPushButton()
        self.prev_btn = QPushButton()
        self.play_btn = QPushButton()
        self.pause_btn = QPushButton()
        self.next_btn = QPushButton()
        transport = (
            (self.rewind_btn, QStyle.SP_MediaSkipBackward, "Rewind to the first frame"),
            (self.prev_btn, QStyle.SP_MediaSeekBackward, "Previous frame"),
            (self.play_btn, QStyle.SP_MediaPlay, "Play the sequence"),
            (self.pause_btn, QStyle.SP_MediaPause, "Pause"),
            (self.next_btn, QStyle.SP_MediaSeekForward, "Next frame"),
        )
        for btn, icon, tip in transport:
            btn.setIcon(self.style().standardIcon(icon))
            btn.setToolTip(tip)
            btn.setFixedWidth(40)
            btn.setCursor(Qt.PointingHandCursor)
            layout.addWidget(btn)

        layout.addSpacing(8)
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setEnabled(False)
        self.frame_slider.setToolTip("Scrub through the loaded frame sequence.")
        layout.addWidget(self.frame_slider, 1)
        self.frame_label = QLabel("Frame 0 / 0")
        self.frame_label.setObjectName("SolarFrameLabel")
        self.frame_label.setMinimumWidth(96)
        self.frame_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.frame_label)

        layout.addSpacing(8)
        speed_label = QLabel("Speed")
        speed_label.setObjectName("SolarFieldLabel")
        layout.addWidget(speed_label)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(1.0, 30.0)
        self.fps_spin.setDecimals(1)
        self.fps_spin.setSingleStep(1.0)
        self.fps_spin.setValue(8.0)
        self.fps_spin.setSuffix(" fps")
        self.fps_spin.setToolTip(
            "Frames per second — used for playback here and for exported movies."
        )
        layout.addWidget(self.fps_spin)
        return bar

    def _make_sidebar_groups_collapsible(self) -> None:
        """Accordion behaviour: each sidebar group collapses to its title.

        Qt's checkable QGroupBox provides the click affordance; unchecking hides
        the group's children so the box shrinks to a single row. An arrow in the
        title shows the state (the built-in indicator is hidden by the style).
        """
        for group in self.controls_panel.findChildren(QGroupBox, options=Qt.FindDirectChildrenOnly):
            group.setProperty("baseTitle", group.title())
            group.setCheckable(True)
            group.setChecked(True)
            self._set_group_title_arrow(group, True)
            group.toggled.connect(
                lambda expanded, g=group: self._on_sidebar_group_toggled(g, expanded)
            )

    @staticmethod
    def _set_group_title_arrow(group: QGroupBox, expanded: bool) -> None:
        base = str(group.property("baseTitle") or group.title())
        group.setTitle(("▾  " if expanded else "▸  ") + base)

    def _on_sidebar_group_toggled(self, group: QGroupBox, expanded: bool) -> None:
        self._set_group_title_arrow(group, bool(expanded))
        for child in group.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
            child.setVisible(bool(expanded))
        if expanded:
            # A checkable QGroupBox re-enables every child wholesale when it is
            # re-checked, so restore the real gating/visibility state.
            self._reapply_gating()

    def _reapply_gating(self) -> None:
        """Re-derive every enable/visibility rule from the current state."""
        self._set_loaded_state(bool(self._map_frames))
        self._apply_observable_download_gating()
        self._apply_instrument_visibility()
        self._on_frame_size_changed()
        self._sync_nrgf_enabled()

    def _set_sidebar_collapsed(self, collapsed: bool, *, animate: bool = True) -> None:
        """Slide the controls sidebar away (or back) to maximise the plot area."""
        collapsed = bool(collapsed)
        self._sidebar_collapsed = collapsed
        self.sidebar_toggle_btn.setArrowType(Qt.RightArrow if collapsed else Qt.LeftArrow)
        self.sidebar_toggle_btn.setToolTip(
            "Show the controls sidebar" if collapsed else "Hide the controls sidebar"
        )
        if collapsed:
            self.controls_scroll.setMinimumWidth(0)
        end_width = 0 if collapsed else self._sidebar_max_width

        def _finish() -> None:
            # Belt-and-braces: pin the splitter panes so the collapse holds even
            # where max-width alone would not move the splitter.
            total = max(1, sum(self.main_splitter.sizes()))
            if collapsed:
                self.main_splitter.setSizes([0, total])
            else:
                self.controls_scroll.setMinimumWidth(self._sidebar_min_width)
                self.controls_scroll.setMaximumWidth(self._sidebar_max_width)
                open_width = self._sidebar_min_width + 40
                self.main_splitter.setSizes([open_width, max(total - open_width, 1)])

        if animate:
            animation = QPropertyAnimation(self.controls_scroll, b"maximumWidth", self)
            animation.setDuration(220)
            animation.setStartValue(self.controls_scroll.width())
            animation.setEndValue(end_width)
            animation.setEasingCurve(QEasingCurve.InOutCubic)
            animation.finished.connect(_finish)
            self._sidebar_animation = animation  # keep alive for the duration
            animation.start()
        else:
            self.controls_scroll.setMaximumWidth(end_width)
            _finish()
        try:
            self._app_settings().setValue("solar_analysis/sidebar_collapsed", collapsed)
        except Exception:
            pass

    def _build_menu_bar(self) -> None:
        self.session_menu = self.menuBar().addMenu("Session")
        self.open_session_action = QAction("Open Session…", self)
        self.save_session_action = QAction("Save Session", self)
        self.save_session_action.setShortcut("Ctrl+S")
        self.save_session_as_action = QAction("Save Session As…", self)
        self.save_session_as_action.setShortcut("Ctrl+Shift+S")
        self.session_menu.addAction(self.open_session_action)
        self.session_menu.addSeparator()
        self.session_menu.addAction(self.save_session_action)
        self.session_menu.addAction(self.save_session_as_action)

        self.data_menu = self.menuBar().addMenu("Data")
        self.fetch_action = QAction("Fetch Archive Records", self)
        self.find_latest_action = QAction("Find Latest Available", self)
        self.live_preview_action = QAction("SOHO/LASCO Live Preview (Helioviewer)", self)
        self.load_selected_action = QAction("Load Selected (to cache)", self)
        self.save_disk_action = QAction("Save Selected to Disk…", self)
        self.upload_action = QAction("Upload FITS Files", self)
        self.use_analyzer_action = QAction("Use Analyzer Window", self)
        self.stop_action = QAction("Stop Download/Search", self)
        self.reset_all_action = QAction("Reset All (clear cache && defaults)", self)
        for action in (
            self.fetch_action,
            self.find_latest_action,
            self.live_preview_action,
            self.load_selected_action,
            self.save_disk_action,
            self.upload_action,
            self.use_analyzer_action,
            self.stop_action,
        ):
            self.data_menu.addAction(action)
        self.data_menu.addSeparator()
        self.data_menu.addAction(self.reset_all_action)

        self.analysis_menu = self.menuBar().addMenu("Analysis")
        self.plot_action = QAction("Plot Frames", self)
        self.running_diff_action = QAction("Running Difference", self)
        self.composite_action = QAction("Composite", self)
        self.detect_regions_action = QAction("Identify Active Regions", self)
        self.labels_action = QAction("Fetch NOAA/HEK Labels", self)
        self.compare_viewpoint_action = QAction("Compare Viewpoint…", self)
        self.reset_frames_action = QAction("Reset Loaded Frames", self)
        for action in (
            self.plot_action,
            self.running_diff_action,
            self.composite_action,
            self.detect_regions_action,
            self.labels_action,
            self.compare_viewpoint_action,
            self.reset_frames_action,
        ):
            self.analysis_menu.addAction(action)

        self.movie_menu = self.menuBar().addMenu("Movie")
        self.rewind_action = QAction("Rewind", self)
        self.previous_action = QAction("Back", self)
        self.play_action = QAction("Play", self)
        self.pause_action = QAction("Pause", self)
        self.next_action = QAction("Forward", self)
        self.build_movie_action = QAction("Build Movie", self)
        for action in (
            self.rewind_action,
            self.previous_action,
            self.play_action,
            self.pause_action,
            self.next_action,
            self.build_movie_action,
        ):
            self.movie_menu.addAction(action)

        self.export_menu = self.menuBar().addMenu("Export")
        self.export_plot_action = QAction("Export Plot", self)
        self.export_crop_action = QAction("Export Cropped FITS", self)
        self.export_regions_action = QAction("Export Regions CSV", self)
        self.quick_mp4_action = QAction("Export MP4", self)
        for action in (
            self.export_plot_action,
            self.export_crop_action,
            self.export_regions_action,
            self.quick_mp4_action,
        ):
            self.export_menu.addAction(action)

        # "About" is its own top-level menu, sitting to the right of Export.
        self.about_menu = self.menuBar().addMenu("About")
        self.about_action = QAction("About Solar Image Analysis…", self)
        # macOS auto-relocates actions whose text looks like "About…" into the
        # native application menu (the default TextHeuristicRole), which would
        # make this vanish from the window's own menu bar. NoRole keeps it here,
        # visibly under the About menu, on every platform.
        self.about_action.setMenuRole(QAction.MenuRole.NoRole)
        self.about_action.triggered.connect(self._show_about_dialog)
        self.about_menu.addAction(self.about_action)

        self._sync_menu_action_state(loaded=False)

    def _show_about_dialog(self) -> None:
        """Beta notice for the solar image analysis window with a link to report
        issues on GitHub."""
        box = QMessageBox(self)
        box.setWindowTitle("About Solar Image Analysis")
        box.setIcon(QMessageBox.Information)
        box.setTextFormat(Qt.RichText)
        box.setText(f"<b>Solar Image Analysis</b><br>{SOLAR_WINDOW_VERSION} (Experimental)")
        box.setInformativeText(
            "<p>This window is an <b>experimental beta</b> feature. Some tools may be "
            "incomplete, change between releases, or behave unexpectedly &mdash; please "
            "double-check any results before relying on them for scientific work.</p>"
            "<p>Found a bug or have a suggestion? Please submit it via GitHub issues at "
            f'<a href="{SOLAR_ISSUES_URL}">{SOLAR_ISSUES_URL}</a>. '
            "Including the steps to reproduce and, where possible, the data you were "
            "working with helps a great deal.</p>"
        )
        # Make the issues link clickable and open in the user's browser.
        label = box.findChild(QLabel, "qt_msgbox_informativelabel")
        if label is not None:
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _apply_sidebar_style(self) -> None:
        """Theme-aware stylesheet for the whole window (sidebar + viewer)."""
        central = self.centralWidget()
        if central is None:
            return
        dark = self._is_dark_ui()
        if dark:
            panel_bg = "#0f1522"
            viewer_bg = "#0b101a"
            card_bg = "#161f30"
            border = "#2c3a52"
            field_bg = "#0e1626"
            field_border = "#34435d"
            text = "#e7edf7"
            muted = "#9aa9c0"
            disabled_text = "#7c8ba1"
            disabled_bg = "#1b2434"
            accent = "#4f8ff7"
            accent_hover = "#3c7ce4"
            accent_soft = "#16365f"
            accent_muted = "#7ea6dd"
            hover = "#1e2940"
        else:
            panel_bg = "#eef2f8"
            viewer_bg = "#e7edf5"
            card_bg = "#ffffff"
            border = "#d3dce9"
            field_bg = "#f9fbfe"
            field_border = "#c3d0e2"
            text = "#1c2637"
            muted = "#5c6d85"
            disabled_text = "#8a97ab"
            disabled_bg = "#eef2f8"
            accent = "#0f62d8"
            accent_hover = "#0d55bb"
            accent_soft = "#e3eeff"
            accent_muted = "#7ba3dd"
            hover = "#f0f5fc"
        mono_font = preferred_monospace_font_family()
        central.setStyleSheet(
            f"""
            QScrollArea#SolarControlsScroll {{
                background: {panel_bg};
                border: 0px;
                border-right: 1px solid {border};
            }}
            QWidget#SolarControlsPanel {{
                background: {panel_bg};
            }}
            QWidget#SolarViewerPanel {{
                background: {viewer_bg};
            }}
            QWidget#SolarViewerHeader,
            QWidget#SolarPlaybackBar {{
                background: {card_bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QWidget#SolarMeasureBar,
            QWidget#SolarDetailsPanel {{
                background: transparent;
            }}
            QGroupBox {{
                background: {card_bg};
                border: 1px solid {border};
                border-radius: 10px;
                margin-top: 22px;
                padding: 14px 12px 12px 12px;
                font-weight: 600;
                color: {text};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 8px;
                top: 2px;
                padding: 2px 6px;
                color: {text};
                background: transparent;
            }}
            QGroupBox::indicator {{
                width: 0px;
                height: 0px;
            }}
            QLabel {{
                color: {text};
                background: transparent;
            }}
            QLabel#SolarPlotTitle {{
                font-weight: 600;
            }}
            QLabel#SolarCoordReadout {{
                color: {muted};
                font-family: "{mono_font}", monospace;
            }}
            QLabel#SolarFrameLabel,
            QLabel#SolarLoadSummary,
            QLabel#SolarResultsStatus,
            QLabel#SolarHintLabel,
            QLabel#SizeEstimateLabel {{
                color: {muted};
            }}
            QLabel#SolarFieldLabel {{
                color: {muted};
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#SolarSubheading {{
                color: {muted};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
                margin-top: 6px;
            }}
            QLineEdit,
            QComboBox,
            QDateTimeEdit,
            QSpinBox,
            QDoubleSpinBox {{
                min-height: 30px;
                padding: 4px 8px;
                border: 1px solid {field_border};
                border-radius: 7px;
                background: {field_bg};
                color: {text};
                selection-background-color: {accent};
                selection-color: #ffffff;
            }}
            QLineEdit:focus,
            QComboBox:focus,
            QDateTimeEdit:focus,
            QSpinBox:focus,
            QDoubleSpinBox:focus {{
                border-color: {accent};
            }}
            QLineEdit:disabled,
            QComboBox:disabled,
            QDateTimeEdit:disabled,
            QSpinBox:disabled,
            QDoubleSpinBox:disabled {{
                color: {disabled_text};
                background: {disabled_bg};
            }}
            QComboBox QAbstractItemView {{
                background: {card_bg};
                color: {text};
                border: 1px solid {border};
                selection-background-color: {accent_soft};
                selection-color: {text};
            }}
            QTableWidget {{
                border: 1px solid {border};
                border-radius: 8px;
                background: {field_bg};
                color: {text};
                gridline-color: {border};
                alternate-background-color: {card_bg};
                selection-background-color: {accent_soft};
                selection-color: {text};
            }}
            QHeaderView::section {{
                background: {disabled_bg};
                color: {muted};
                border: 0px;
                border-bottom: 1px solid {border};
                border-right: 1px solid {border};
                padding: 5px 6px;
                font-weight: 600;
            }}
            QTableCornerButton::section {{
                background: {disabled_bg};
                border: 0px;
            }}
            QPushButton {{
                min-height: 30px;
                padding: 5px 12px;
                border: 1px solid {field_border};
                border-radius: 7px;
                background: {card_bg};
                color: {text};
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {hover};
                border-color: {accent};
            }}
            QPushButton:pressed {{
                background: {accent_soft};
            }}
            QPushButton:checked {{
                background: {accent};
                border-color: {accent};
                color: #ffffff;
            }}
            QPushButton:disabled {{
                background: {disabled_bg};
                color: {disabled_text};
                border-color: {border};
            }}
            QPushButton#SolarPrimaryAction {{
                min-height: 34px;
                background: {accent};
                border-color: {accent};
                color: #ffffff;
                font-weight: 700;
            }}
            QPushButton#SolarPrimaryAction:hover {{
                background: {accent_hover};
            }}
            QPushButton#SolarPrimaryAction:disabled {{
                background: {accent_soft};
                color: {accent_muted};
                border-color: transparent;
            }}
            QWidget#SolarMeasureBar QPushButton {{
                min-height: 26px;
                padding: 3px 14px;
                border-radius: 13px;
            }}
            QWidget#SolarPlaybackBar QPushButton {{
                min-height: 26px;
                padding: 2px 6px;
                border-radius: 6px;
                background: transparent;
                border: 1px solid transparent;
            }}
            QWidget#SolarPlaybackBar QPushButton:hover {{
                background: {hover};
                border-color: {border};
            }}
            QWidget#SolarPlaybackBar QPushButton:disabled {{
                background: transparent;
                color: {disabled_text};
            }}
            QCheckBox {{
                color: {text};
                spacing: 7px;
                background: transparent;
            }}
            QCheckBox:disabled {{
                color: {disabled_text};
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid {field_border};
                background: {field_bg};
            }}
            QCheckBox::indicator:hover {{
                border-color: {accent};
            }}
            QCheckBox::indicator:checked {{
                background: {accent};
                border-color: {accent};
            }}
            QCheckBox::indicator:checked:disabled {{
                background: {border};
                border-color: {border};
            }}
            QCheckBox::indicator:disabled {{
                background: {disabled_bg};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                border-radius: 2px;
                background: {field_border};
            }}
            QSlider::sub-page:horizontal {{
                background: {accent};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
                background: {accent};
            }}
            QSlider::handle:horizontal:hover {{
                background: {accent_hover};
            }}
            QSlider::handle:horizontal:disabled {{
                background: {disabled_text};
            }}
            QSlider::sub-page:horizontal:disabled {{
                background: {border};
            }}
            QToolButton {{
                border: none;
                background: transparent;
                color: {text};
                padding: 2px 6px;
                border-radius: 6px;
                font-weight: 600;
            }}
            QToolButton:hover {{
                background: {hover};
            }}
            QToolButton#SolarSidebarHandle {{
                background: {card_bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QToolButton#SolarSidebarHandle:hover {{
                background: {hover};
                border-color: {accent};
            }}
            QTextEdit {{
                background: {field_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 4px;
            }}
            QSplitter::handle {{
                background: transparent;
            }}
            QSplitter::handle:hover {{
                background: {accent_soft};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {field_border};
                border-radius: 5px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {muted};
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: {field_border};
                border-radius: 5px;
                min-width: 30px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {muted};
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{
                width: 0px;
                height: 0px;
            }}
            QScrollBar::add-page, QScrollBar::sub-page {{
                background: transparent;
            }}
            """
        )

    def _is_dark_ui(self) -> bool:
        theme = getattr(self, "theme", None)
        if theme is not None and hasattr(theme, "is_dark"):
            try:
                return bool(theme.is_dark())
            except Exception:
                pass
        return self.palette().color(self.backgroundRole()).lightness() < 128

    def _build_data_source_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("1 · Data Source")
        self.data_source_group = group
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        parent_layout.addWidget(group)

        self.search_btn = QPushButton("Fetch Records")
        self.search_btn.setObjectName("SolarPrimaryAction")
        self.search_btn.setToolTip(
            "Search the archive for the selected observable and time range.\n"
            "Matching records appear below in Archive Results."
        )
        self.find_latest_btn = QPushButton("Find Latest")
        self.find_latest_btn.setToolTip(
            "Jump to the newest data actually available in the archive for the selected observable.\n"
            "Especially useful for SOHO/LASCO, whose calibrated FITS archive can lag real time by many\n"
            "months. This scans the archive backwards and may take up to a minute or two."
        )
        self.live_preview_btn = QPushButton("Live Preview (Helioviewer)")
        self.live_preview_btn.setToolTip(
            "Show the newest SOHO/LASCO C2/C3 quicklook image from Helioviewer (updated within ~an hour).\n"
            "A near-real-time browse image for situational awareness — not analysis-grade FITS."
        )
        self.load_local_btn = QPushButton("Upload FITS…")
        self.load_local_btn.setToolTip("Load solar FITS files straight from disk — no archive search needed.")
        self.use_analyzer_btn = QPushButton("Use Analyzer Time Window")
        self.use_analyzer_btn.setToolTip(
            "Copy the start/end times from the main e-Callisto analyzer window, so the\n"
            "solar images match the radio burst you are looking at."
        )
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("Cancel the running search or download. Completed files stay in the cache.")

        # Observable selector: AIA EUV/UV channels, HMI line-of-sight products,
        # SOHO/LASCO coronagraph detectors, STEREO/SECCHI detectors and GOES/SUVI
        # passbands. userData is a tuple: ("AIA", wavelength_float),
        # ("HMI", product_str), ("LASCO", "C2"/"C3"),
        # ("SECCHI", (spacecraft, detector, wavelength_or_None)) or ("SUVI", wavelength_float).
        self.wavelength_combo = QComboBox()
        self.wavelength_combo.setToolTip(
            "Choose the observable: an SDO/AIA wavelength, an SDO/HMI product, a "
            "SOHO/LASCO coronagraph, a STEREO/SECCHI detector (EUVI/COR1/COR2/HI1/HI2) "
            "or a GOES/SUVI passband."
        )
        populate_observable_combo(self.wavelength_combo)
        self.wavelength_combo.setCurrentText("AIA 193 A")

        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        start_default = now - timedelta(hours=2)
        self.start_dt_edit = QDateTimeEdit(QDateTime(start_default))
        self.end_dt_edit = QDateTimeEdit(QDateTime(now))
        for edit in (self.start_dt_edit, self.end_dt_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        self.sample_seconds_spin = QSpinBox()
        self.sample_seconds_spin.setRange(0, 3600)
        self.sample_seconds_spin.setValue(120)
        self.sample_seconds_spin.setToolTip("Temporal cadence in seconds. High resolution still uses this cadence to limit frame count.")
        self.max_records_spin = QSpinBox()
        self.max_records_spin.setRange(1, 5000)
        self.max_records_spin.setValue(120)
        self.high_resolution_check = QCheckBox("High resolution AIA (best crop)")
        self.high_resolution_check.setChecked(False)
        self.high_resolution_check.setToolTip(
            "Request SunPy/VSO full-resolution AIA products. Files are larger, but cropped views preserve more detail."
        )

        self.source_combo = QComboBox()
        self.source_combo.addItem("Auto (JSOC → VSO)", userData="auto")
        self.source_combo.addItem("JSOC (fast)", userData="jsoc")
        self.source_combo.addItem("VSO (classic)", userData="vso")
        self.source_combo.setToolTip(
            "JSOC returns direct, compressed AIA files and is usually much faster than VSO.\n"
            "Auto tries JSOC first and falls back to VSO automatically.\n"
            "JSOC requires a one-time registered notify e-mail (set at right)."
        )
        self.jsoc_email_edit = QLineEdit()
        self.jsoc_email_edit.setPlaceholderText("you@example.org (register at JSOC)")
        self.jsoc_email_edit.setToolTip(
            "Your JSOC export e-mail. Register once (free) at:\n"
            "https://jsoc.stanford.edu/ajax/register_email.html"
        )

        self.frame_size_combo = QComboBox()
        self.frame_size_combo.addItem("Full disk (4096²)", userData=SIZE_FULL)
        self.frame_size_combo.addItem("Binned ½ (2048²)", userData=SIZE_BIN2)
        self.frame_size_combo.addItem("Binned ¼ (1024²)", userData=SIZE_BIN4)
        self.frame_size_combo.addItem("Cutout (region)", userData=SIZE_CUTOUT)
        self.frame_size_combo.setToolTip(
            "Smaller frames download much faster. Binned and cutout frames are\n"
            "produced server-side by JSOC, so only the reduced data is transferred\n"
            "(these modes require the JSOC source + e-mail)."
        )

        # Cutout centre/box (arcsec from disk centre), shown only for Cutout mode.
        self.cutout_widget = QWidget()
        cutout_layout = QGridLayout(self.cutout_widget)
        cutout_layout.setContentsMargins(0, 0, 0, 0)
        cutout_layout.setHorizontalSpacing(6)
        cutout_layout.setVerticalSpacing(4)
        self.cutout_x_spin = self._make_arcsec_spin(0.0)
        self.cutout_y_spin = self._make_arcsec_spin(0.0)
        self.cutout_w_spin = self._make_arcsec_spin(500.0)
        self.cutout_h_spin = self._make_arcsec_spin(500.0)
        for spin in (self.cutout_w_spin, self.cutout_h_spin):
            spin.setRange(20.0, 2400.0)
        cutout_layout.addWidget(QLabel("Centre X″"), 0, 0)
        cutout_layout.addWidget(QLabel("Centre Y″"), 0, 1)
        cutout_layout.addWidget(self.cutout_x_spin, 1, 0)
        cutout_layout.addWidget(self.cutout_y_spin, 1, 1)
        cutout_layout.addWidget(QLabel("Width″"), 2, 0)
        cutout_layout.addWidget(QLabel("Height″"), 2, 1)
        cutout_layout.addWidget(self.cutout_w_spin, 3, 0)
        cutout_layout.addWidget(self.cutout_h_spin, 3, 1)
        self.cutout_widget.setVisible(False)

        self.size_estimate_label = QLabel("")
        self.size_estimate_label.setWordWrap(True)
        self.size_estimate_label.setObjectName("SizeEstimateLabel")

        row = 0
        layout.addWidget(self._field_label("Observable"), row, 0, 1, 2)
        row += 1
        layout.addWidget(self.wavelength_combo, row, 0, 1, 2)

        row += 1
        layout.addWidget(self._field_label("Start (UTC)"), row, 0)
        layout.addWidget(self._field_label("End (UTC)"), row, 1)
        row += 1
        layout.addWidget(self.start_dt_edit, row, 0)
        layout.addWidget(self.end_dt_edit, row, 1)

        row += 1
        layout.addWidget(self._field_label("Cadence (s)"), row, 0)
        layout.addWidget(self._field_label("Max records"), row, 1)
        row += 1
        layout.addWidget(self.sample_seconds_spin, row, 0)
        layout.addWidget(self.max_records_spin, row, 1)

        row += 1
        layout.addWidget(self.use_analyzer_btn, row, 0, 1, 2)

        row += 1
        layout.addWidget(self._subheading("Download options"), row, 0, 1, 2)
        row += 1
        layout.addWidget(self._field_label("Source"), row, 0)
        layout.addWidget(self._field_label("JSOC notify e-mail"), row, 1)
        row += 1
        layout.addWidget(self.source_combo, row, 0)
        layout.addWidget(self.jsoc_email_edit, row, 1)

        row += 1
        layout.addWidget(self._field_label("Frame size"), row, 0)
        layout.addWidget(self.frame_size_combo, row, 1)
        row += 1
        layout.addWidget(self.cutout_widget, row, 0, 1, 2)
        row += 1
        layout.addWidget(self.high_resolution_check, row, 0, 1, 2)
        row += 1
        layout.addWidget(self.size_estimate_label, row, 0, 1, 2)

        row += 1
        layout.addWidget(self.search_btn, row, 0)
        layout.addWidget(self.stop_btn, row, 1)
        row += 1
        layout.addWidget(self.find_latest_btn, row, 0)
        layout.addWidget(self.load_local_btn, row, 1)
        row += 1
        layout.addWidget(self.live_preview_btn, row, 0, 1, 2)

    def _build_archive_results_group(self, parent_layout: QVBoxLayout) -> None:
        self.archive_results_group = QGroupBox("2 · Archive Results")
        self.archive_results_group.setMinimumHeight(300)
        self.archive_results_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self.archive_results_group)
        layout.setContentsMargins(10, 16, 10, 10)
        layout.setSpacing(8)
        parent_layout.addWidget(self.archive_results_group)

        self.archive_results_status_label = QLabel("Run Fetch to list matching archive files.")
        self.archive_results_status_label.setObjectName("SolarResultsStatus")
        self.archive_results_status_label.setWordWrap(True)
        layout.addWidget(self.archive_results_status_label)

        table_controls = QHBoxLayout()
        table_controls.setContentsMargins(0, 0, 0, 0)
        self.select_all_results_btn = QPushButton("Select All")
        self.deselect_all_results_btn = QPushButton("Deselect All")
        self.save_disk_btn = QPushButton("Save to Disk…")
        self.save_disk_btn.setToolTip(
            "Download the checked rows to a folder you choose (kept on disk).\n"
            "Use 'Load Selected' instead to download into the working cache."
        )
        self.select_all_results_btn.setEnabled(False)
        self.deselect_all_results_btn.setEnabled(False)
        self.save_disk_btn.setEnabled(False)
        table_controls.addWidget(self.select_all_results_btn)
        table_controls.addWidget(self.deselect_all_results_btn)
        table_controls.addStretch(1)
        table_controls.addWidget(self.save_disk_btn)
        layout.addLayout(table_controls)

        self.results_table = QTableWidget(0, 5)
        self.results_table.setObjectName("SolarArchiveResultsTable")
        self.results_table.setHorizontalHeaderLabels(["Use", "Start UTC", "Source", "Size", "File ID"])
        self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.results_table.setMinimumHeight(225)
        self.results_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setWordWrap(False)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.verticalHeader().setDefaultSectionSize(26)
        self.results_table.setShowGrid(False)
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setMinimumSectionSize(42)
        self.results_table.setColumnWidth(0, 46)
        self.results_table.setColumnWidth(1, 150)
        self.results_table.setColumnWidth(2, 72)
        self.results_table.setColumnWidth(3, 72)
        layout.addWidget(self.results_table)

        # The step-2 action sits right under the table it acts on.
        self.download_load_btn = QPushButton("Load Selected")
        self.download_load_btn.setObjectName("SolarPrimaryAction")
        self.download_load_btn.setEnabled(False)
        self.download_load_btn.setToolTip(
            "Download the checked rows into the working cache and load them for analysis."
        )
        layout.addWidget(self.download_load_btn)

    def _build_mode_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("3 · Analysis")
        self.mode_group = group
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        parent_layout.addWidget(group)

        # What is actually in memory right now (instrument, frame count, config).
        self.load_summary_label = QLabel("No data loaded.")
        self.load_summary_label.setObjectName("SolarLoadSummary")
        self.load_summary_label.setWordWrap(True)

        self.plot_mode_btn = QPushButton("Plot Frames")
        self.plot_mode_btn.setObjectName("SolarPrimaryAction")
        self.plot_mode_btn.setToolTip("Render the loaded solar frame sequence in the embedded plot area.")
        self.difference_mode_btn = QPushButton("Running Diff")
        self.base_diff_btn = QPushButton("Base Diff")
        self.base_diff_btn.setToolTip("Show each frame minus the first frame (eruptions, dimmings, EUV waves).")
        self.composite_btn = QPushButton("Composite")
        self.composite_btn.setToolTip(
            "AIA RGB tri-colour from the first 3 frames — or, with a magnetogram overlay\n"
            "loaded, AIA with HMI polarity contours (red = +, blue = −)."
        )
        self.magnetogram_btn = QPushButton("Mag Overlay…")
        self.magnetogram_btn.setToolTip(
            "Load an HMI magnetogram FITS to overlay as ± polarity contours on the\n"
            "current AIA frame when you click Composite."
        )
        self.mag_threshold_spin = QSpinBox()
        self.mag_threshold_spin.setRange(10, 2000)
        self.mag_threshold_spin.setSingleStep(10)
        self.mag_threshold_spin.setValue(100)
        self.mag_threshold_spin.setSuffix(" G")
        self.mag_threshold_spin.setToolTip("Magnetogram contour level (Gauss).")
        self.lightcurve_btn = QPushButton("Light Curve")
        self.lightcurve_btn.setToolTip(
            "Plot region intensity (DN/s) vs time, with the e-Callisto radio burst window overlaid\n"
            "so the EUV brightening can be timed against the radio burst."
        )
        self.detect_regions_btn = QPushButton("Active Regions")
        self.fetch_labels_btn = QPushButton("NOAA/HEK Labels")
        self.compare_viewpoint_btn = QPushButton("Compare Viewpoint…")
        self.compare_viewpoint_btn.setToolTip(
            "Fetch a second observable near this frame's time (e.g. STEREO vs the\n"
            "Earth view), reproject it onto the current view and compare side-by-side\n"
            "with a blink toggle."
        )
        self.reset_loaded_btn = QPushButton("Reset Frames")
        layout.addWidget(self.load_summary_label, 0, 0, 1, 2)
        layout.addWidget(self.plot_mode_btn, 1, 0, 1, 2)
        layout.addWidget(self.difference_mode_btn, 2, 0)
        layout.addWidget(self.base_diff_btn, 2, 1)
        layout.addWidget(self.composite_btn, 3, 0)
        layout.addWidget(self.lightcurve_btn, 3, 1)
        layout.addWidget(self.magnetogram_btn, 4, 0)
        layout.addWidget(self.mag_threshold_spin, 4, 1)
        layout.addWidget(self.detect_regions_btn, 5, 0)
        layout.addWidget(self.fetch_labels_btn, 5, 1)
        layout.addWidget(self.compare_viewpoint_btn, 6, 0, 1, 2)
        layout.addWidget(self.reset_loaded_btn, 7, 0, 1, 2)

    def _build_movie_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("Movie Export")
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        parent_layout.addWidget(group)

        self.movie_content_combo = QComboBox()
        self.movie_content_combo.addItems(["Frames", "Running Difference", "Base Difference"])
        self.movie_content_combo.setToolTip(
            "What the movie (and the viewer) shows: plain frames, frame-to-frame\n"
            "running differences, or differences against the first frame."
        )
        self.movie_format_combo = QComboBox()
        self.movie_format_combo.addItems(["MP4", "GIF"])
        self.export_movie_btn = QPushButton("Build Movie…")

        hint = QLabel(
            "Movies use the playback speed (fps) under the viewer and the current "
            "colormap, scale, clip and crop settings."
        )
        hint.setObjectName("SolarHintLabel")
        hint.setWordWrap(True)

        layout.addWidget(self._field_label("Content"), 0, 0)
        layout.addWidget(self.movie_content_combo, 0, 1)
        layout.addWidget(self._field_label("Format"), 1, 0)
        layout.addWidget(self.movie_format_combo, 1, 1)
        layout.addWidget(self.export_movie_btn, 2, 0, 1, 2)
        layout.addWidget(hint, 3, 0, 1, 2)

    def _build_plot_controls_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("Display & Crop")
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        parent_layout.addWidget(group)

        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(
            [
                *AIA_COLORMAPS,
                "hmimag",
                "soholasco2",
                "soholasco3",
                "stereocor1",
                "stereocor2",
                "stereohi1",
                "stereohi2",
                *(f"euvi{wl}" for wl in STEREO_EUVI_WAVELENGTHS),
                *(f"goes-rsuvi{wl}" for wl in SUVI_WAVELENGTHS),
                "inferno",
                "magma",
                "plasma",
                "viridis",
                "cividis",
                "gray",
                "RdBu_r",
                "hot",
            ]
        )
        self.colormap_combo.setCurrentText("sdoaia193")
        self.renderer_combo = QComboBox()
        self.renderer_combo.addItems(["PyQtGraph", "Matplotlib"])
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["linear", "log"])
        self.clip_low_slider = PercentSlider(1.0, minimum=0.0, maximum=99.0)
        self.clip_low_slider.setToolTip("Lower percentile clip for image contrast. Drag for a live preview.")
        self.clip_high_slider = PercentSlider(99.9, minimum=1.0, maximum=100.0)
        self.clip_high_slider.setToolTip("Upper percentile clip for image contrast. Drag for a live preview.")

        self.crop_check = QCheckBox("Rectangle crop")
        self.crop_check.setToolTip(
            "Optional: show a draggable rectangle on the PyQtGraph image that fills in the\n"
            "X/Y arcsec boxes for you. You can also just type the bounds and click Apply Crop."
        )
        self.crop_x0_spin = self._make_arcsec_spin(-1100.0)
        self.crop_x1_spin = self._make_arcsec_spin(1100.0)
        self.crop_y0_spin = self._make_arcsec_spin(-1100.0)
        self.crop_y1_spin = self._make_arcsec_spin(1100.0)
        self.apply_crop_btn = QPushButton("Apply Crop")
        self.apply_crop_btn.setToolTip("Crop all loaded frames to the X/Y arcsec bounds above.")
        self.export_crop_btn = QPushButton("Export Cropped FITS")

        self.solar_limb_check = QCheckBox("Solar Limb")
        self.grid_check = QCheckBox("Coordinate Grid")
        self.grid_check.setChecked(True)
        self.grid_check.setToolTip(
            "Overlay a solar-coordinate graticule (meridians and parallels) on the\n"
            "disk, projected from the selected reference frame."
        )
        self.grid_frame_combo = QComboBox()
        self.grid_frame_combo.addItems([SOLAR_FRAME_DISPLAY_NAMES[key] for key in SOLAR_FRAME_KEYS])
        self.grid_frame_combo.setCurrentText(SOLAR_FRAME_DISPLAY_NAMES["HCI"])
        self.grid_frame_combo.setToolTip(
            "Reference frame for the coordinate grid and the live hover readout:\n"
            "  • HCI  — Heliocentric Inertial (fixed in space)\n"
            "  • Stonyhurst / Carrington — heliographic longitude systems"
        )
        self.colorbar_check = QCheckBox("Colorbar")
        self.colorbar_check.setChecked(True)
        self.region_overlay_check = QCheckBox("Region Overlays")
        self.region_overlay_check.setChecked(True)

        self.export_plot_btn = QPushButton("Export Plot")
        self.export_regions_btn = QPushButton("Export Regions CSV")

        row = 0
        layout.addWidget(self._field_label("Renderer"), row, 0)
        layout.addWidget(self.renderer_combo, row, 1)
        row += 1
        layout.addWidget(self._field_label("Colormap"), row, 0)
        layout.addWidget(self.colormap_combo, row, 1)
        row += 1
        layout.addWidget(self._field_label("Scale"), row, 0)
        layout.addWidget(self.scale_combo, row, 1)
        row += 1
        layout.addWidget(self._field_label("Clip low"), row, 0)
        layout.addWidget(self.clip_low_slider, row, 1)
        row += 1
        layout.addWidget(self._field_label("Clip high"), row, 0)
        layout.addWidget(self.clip_high_slider, row, 1)
        row += 1
        layout.addWidget(self.solar_limb_check, row, 0)
        layout.addWidget(self.grid_check, row, 1)
        row += 1
        layout.addWidget(self._field_label("Grid frame"), row, 0)
        layout.addWidget(self.grid_frame_combo, row, 1)
        row += 1
        layout.addWidget(self.colorbar_check, row, 0)
        layout.addWidget(self.region_overlay_check, row, 1)
        row += 1
        layout.addWidget(self._subheading("Crop (arcsec)"), row, 0, 1, 2)
        row += 1
        layout.addWidget(self.crop_check, row, 0, 1, 2)
        row += 1
        layout.addWidget(self._field_label("X min / max"), row, 0)
        layout.addWidget(self._two_widgets(self.crop_x0_spin, self.crop_x1_spin), row, 1)
        row += 1
        layout.addWidget(self._field_label("Y min / max"), row, 0)
        layout.addWidget(self._two_widgets(self.crop_y0_spin, self.crop_y1_spin), row, 1)
        row += 1
        layout.addWidget(self.apply_crop_btn, row, 0)
        layout.addWidget(self.export_crop_btn, row, 1)
        row += 1
        layout.addWidget(self._subheading("Export stills"), row, 0, 1, 2)
        row += 1
        layout.addWidget(self.export_plot_btn, row, 0)
        layout.addWidget(self.export_regions_btn, row, 1)

    def _build_coronagraph_group(self, parent_layout: QVBoxLayout) -> None:
        """White-light coronagraph tools (STEREO COR1/COR2, SOHO/LASCO).

        CME height-time tracking lives in the Measure toolbar (it applies to
        every imager); this group keeps the coronagraph-specific display tool.
        """
        group = QGroupBox("Coronagraph Tools")
        self.coronagraph_group = group
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        parent_layout.addWidget(group)

        self.nrgf_check = QCheckBox("NRGF radial filter")
        self.nrgf_check.setToolTip(
            "Normalizing-Radial-Graded Filter: flattens the corona's steep radial\n"
            "brightness fall-off so faint CME fronts become visible at all heights.\n"
            "Applies to the plain frame view (differences already remove the background)."
        )
        self.nrgf_check.setEnabled(False)
        layout.addWidget(self.nrgf_check, 0, 0, 1, 2)
        group.setVisible(False)

    def _build_hi_group(self, parent_layout: QVBoxLayout) -> None:
        """Heliospheric Imager tools (STEREO HI1/HI2): starfield/F-corona
        background subtraction and time-elongation J-maps. Shown only for HI."""
        group = QGroupBox("Heliospheric Imager (J-map)")
        self.hi_group = group
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        parent_layout.addWidget(group)

        self.hi_background_combo = QComboBox()
        self.hi_background_combo.addItem("Median background", userData="median")
        self.hi_background_combo.addItem("Previous frame", userData="previous")
        self.hi_background_combo.setToolTip(
            "Background removal before the J-map: the per-pixel temporal median\n"
            "removes the static F-corona and star field; 'previous frame' is a\n"
            "plain running difference."
        )
        self.hi_pa_spin = QSpinBox()
        self.hi_pa_spin.setRange(0, 359)
        self.hi_pa_spin.setValue(90)
        self.hi_pa_spin.setSuffix("°")
        self.hi_pa_spin.setToolTip(
            "Position angle of the J-map slit, counter-clockwise from image +x\n"
            "(90° points up). Track the CME direction."
        )
        self.hi_jmap_btn = QPushButton("Build J-map")
        self.hi_jmap_btn.setEnabled(False)
        self.hi_jmap_btn.setToolTip(
            "Stack per-frame slit profiles into a time–elongation map; an outward-\n"
            "moving CME appears as a slanted bright track."
        )
        layout.addWidget(self._field_label("Background"), 0, 0)
        layout.addWidget(self.hi_background_combo, 0, 1)
        layout.addWidget(self._field_label("Position angle"), 1, 0)
        layout.addWidget(self.hi_pa_spin, 1, 1)
        layout.addWidget(self.hi_jmap_btn, 2, 0, 1, 2)
        group.setVisible(False)

    def _build_vector_field_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("Magnetic Vector Field (HMI)")
        self.vector_group = group
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        parent_layout.addWidget(group)

        self.vector_load_btn = QPushButton("Load Vector FITS…")
        self.vector_load_btn.setToolTip(
            "Load hmi.B_720s vector magnetogram segments from disk: the field,\n"
            "inclination and azimuth FITS of each time step (plus the optional\n"
            "disambig segment, applied automatically when present)."
        )
        self.vector_download_btn = QPushButton("Download Vector (JSOC)")
        self.vector_download_btn.setToolTip(
            "Download the true measured vector field (hmi.B_720s) for the query\n"
            "time window via the JSOC fast path. Needs the registered JSOC notify\n"
            "e-mail above. Each 720 s time step is 4 segments ≈ 50 MB.\n"
            "If no HMI image is loaded, the derived vertical-field (Bz) magnetogram\n"
            "is plotted automatically with the vectors overlaid."
        )
        self.vector_show_check = QCheckBox("Show vector field")
        self.vector_show_check.setToolTip(
            "Overlay the magnetic vector field on the displayed HMI frame\n"
            "(magnetogram, continuum or Doppler). Arrows show the transverse\n"
            "component; red = vertical field toward the observer (+Bz), blue = away."
        )
        self.vector_arrows_check = QCheckBox("Arrows")
        self.vector_arrows_check.setChecked(True)
        self.vector_arrows_check.setToolTip("Quiver arrows of the transverse field (length ∝ strength).")
        self.vector_stream_check = QCheckBox("Streamlines")
        self.vector_stream_check.setToolTip("Field-direction streamlines traced through the transverse field.")
        self.vector_mag_check = QCheckBox("|B| layer")
        self.vector_mag_check.setToolTip("Semi-transparent tint by total field strength |B|.")

        self.vector_spacing_spin = QSpinBox()
        self.vector_spacing_spin.setRange(16, 512)
        self.vector_spacing_spin.setSingleStep(8)
        self.vector_spacing_spin.setValue(64)
        self.vector_spacing_spin.setSuffix(" px")
        self.vector_spacing_spin.setToolTip("Arrow grid spacing in detector pixels (smaller = denser).")
        self.vector_threshold_spin = QSpinBox()
        self.vector_threshold_spin.setRange(50, 2000)
        self.vector_threshold_spin.setSingleStep(50)
        self.vector_threshold_spin.setValue(200)
        self.vector_threshold_spin.setSuffix(" G")
        self.vector_threshold_spin.setToolTip(
            "Minimum transverse field strength to draw. The hmi.B_720s transverse\n"
            "noise floor is ~100 G, so values below that mostly show noise."
        )

        self.vector_status_label = QLabel("No vector field data loaded.")
        self.vector_status_label.setWordWrap(True)

        layout.addWidget(self.vector_load_btn, 0, 0)
        layout.addWidget(self.vector_download_btn, 0, 1)
        layout.addWidget(self.vector_show_check, 1, 0, 1, 2)
        style_row = QHBoxLayout()
        style_row.setContentsMargins(0, 0, 0, 0)
        style_row.addWidget(self.vector_arrows_check)
        style_row.addWidget(self.vector_stream_check)
        style_row.addWidget(self.vector_mag_check)
        style_row.addStretch(1)
        style_widget = QWidget()
        style_widget.setLayout(style_row)
        layout.addWidget(style_widget, 2, 0, 1, 2)
        layout.addWidget(self._field_label("Spacing / Min B⊥"), 3, 0)
        layout.addWidget(self._two_widgets(self.vector_spacing_spin, self.vector_threshold_spin), 3, 1)
        layout.addWidget(self.vector_status_label, 4, 0, 1, 2)

    def _build_region_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("Active Regions")
        self.region_group = group
        layout = QVBoxLayout(group)
        parent_layout.addWidget(group)

        settings = QHBoxLayout()
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(50, 100)
        self.threshold_spin.setValue(98)
        self.min_area_spin = QSpinBox()
        self.min_area_spin.setRange(1, 100000)
        self.min_area_spin.setValue(12)
        self.metadata_status_label = QLabel("Metadata: not loaded")
        settings.addWidget(self._field_label("Threshold"))
        settings.addWidget(self.threshold_spin)
        settings.addWidget(self._field_label("Min px"))
        settings.addWidget(self.min_area_spin)
        layout.addLayout(settings)
        layout.addWidget(self.metadata_status_label)

        self.region_table = QTableWidget(0, 7)
        self.region_table.setHorizontalHeaderLabels(["ID", "Label", "NOAA", "Centroid", "Area", "Peak", "Source"])
        self.region_table.setMaximumHeight(190)
        self.region_table.setColumnWidth(1, 90)
        self.region_table.setColumnWidth(3, 100)
        layout.addWidget(self.region_table)

    def _make_arcsec_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-10000.0, 10000.0)
        spin.setDecimals(1)
        spin.setSingleStep(10.0)
        spin.setSuffix('"')
        spin.setValue(float(value))
        return spin

    def _field_label(self, text: str) -> QLabel:
        """Small muted caption above/beside an input field."""
        label = QLabel(text)
        label.setObjectName("SolarFieldLabel")
        return label

    def _subheading(self, text: str) -> QLabel:
        """Uppercase section divider inside a sidebar card."""
        label = QLabel(str(text).upper())
        label.setObjectName("SolarSubheading")
        return label

    def _two_widgets(self, left: QWidget, right: QWidget) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(left)
        layout.addWidget(right)
        return widget

    def _connect_signals(self):
        self.search_btn.clicked.connect(self.search_archives)
        self.download_load_btn.clicked.connect(self.download_and_load_selected)
        self.find_latest_btn.clicked.connect(self.find_latest_data)
        self.live_preview_btn.clicked.connect(self.open_helioviewer_preview)
        self.load_local_btn.clicked.connect(self.load_local_files)
        self.use_analyzer_btn.clicked.connect(lambda: self.use_analyzer_time_window(auto_query=False))
        self.stop_btn.clicked.connect(self.stop_active_operation)
        self.select_all_results_btn.clicked.connect(self.select_all_results)
        self.deselect_all_results_btn.clicked.connect(self.deselect_all_results)
        self.save_disk_btn.clicked.connect(self.save_selected_to_disk)
        self.wavelength_combo.currentIndexChanged.connect(self._on_query_wavelength_changed)
        self.frame_size_combo.currentIndexChanged.connect(lambda _i: self._on_frame_size_changed())
        for _spin in (self.cutout_w_spin, self.cutout_h_spin):
            _spin.valueChanged.connect(lambda _v: self._update_size_estimate())
        self.results_table.itemChanged.connect(lambda _item: self._update_size_estimate())
        self.plot_mode_btn.clicked.connect(lambda: self._set_difference_mode("raw"))
        self.difference_mode_btn.clicked.connect(lambda: self._set_difference_mode("running"))
        self.base_diff_btn.clicked.connect(lambda: self._set_difference_mode("base"))
        self.lightcurve_btn.clicked.connect(self.show_region_lightcurve)
        self.composite_btn.clicked.connect(self.show_composite_plot)
        self.magnetogram_btn.clicked.connect(self.load_magnetogram_overlay)
        self.detect_regions_btn.clicked.connect(self.detect_active_regions)
        self.fetch_labels_btn.clicked.connect(self.fetch_active_region_labels)
        self.compare_viewpoint_btn.clicked.connect(self.open_multiview_dialog)
        self.reset_loaded_btn.clicked.connect(self.reset_loaded_frames)
        self.frame_slider.valueChanged.connect(self._on_frame_slider_changed)
        self.rewind_btn.clicked.connect(self.rewind_frames)
        self.prev_btn.clicked.connect(self.previous_frame)
        self.play_btn.clicked.connect(self.play_frames)
        self.pause_btn.clicked.connect(self.pause_frames)
        self.next_btn.clicked.connect(self.next_frame)
        self.fps_spin.valueChanged.connect(lambda _v: self._refresh_play_timer())
        self.movie_content_combo.currentTextChanged.connect(lambda _text: self._render_current_frame())
        self.renderer_combo.currentTextChanged.connect(self._on_renderer_changed)
        self.colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        self.scale_combo.currentTextChanged.connect(lambda _text: self._render_current_frame())
        self.clip_low_slider.valueChanged.connect(lambda _v: self._schedule_clip_render())
        self.clip_high_slider.valueChanged.connect(lambda _v: self._schedule_clip_render())
        self.crop_check.toggled.connect(self._on_crop_toggled)
        self.apply_crop_btn.clicked.connect(self.apply_axis_crop)
        self.solar_limb_check.toggled.connect(lambda _checked: self._render_current_frame())
        # Measurement tools (controller owns the click state machine).
        self.ruler_tool_btn.toggled.connect(lambda on: self._on_measure_tool_toggled("ruler", on))
        self.profile_tool_btn.toggled.connect(lambda on: self._on_measure_tool_toggled("profile", on))
        self.stats_tool_btn.clicked.connect(lambda: self._measure.report_region_stats())
        self.height_time_btn.toggled.connect(lambda on: self._on_measure_tool_toggled("height_time", on))
        self.clear_measure_btn.clicked.connect(self.clear_all_measurements)
        self.ht_fit_btn.clicked.connect(lambda: self._measure.finish_height_time())
        self.ht_clear_btn.clicked.connect(
            lambda: (self._measure.clear_height_time(), self._sync_tracking_panel_visibility())
        )
        self.nrgf_check.toggled.connect(lambda _checked: self._render_current_frame())
        self.movie_content_combo.currentTextChanged.connect(lambda _t: self._sync_nrgf_enabled())
        self.hi_jmap_btn.clicked.connect(self.build_hi_jmap)
        self.vector_load_btn.clicked.connect(self.load_vector_field_files)
        self.vector_download_btn.clicked.connect(self.download_vector_field)
        self.vector_show_check.toggled.connect(lambda _checked: self._refresh_vector_overlay())
        self.vector_arrows_check.toggled.connect(lambda _checked: self._refresh_vector_overlay())
        self.vector_stream_check.toggled.connect(lambda _checked: self._refresh_vector_overlay())
        self.vector_mag_check.toggled.connect(lambda _checked: self._refresh_vector_overlay())
        self.vector_spacing_spin.valueChanged.connect(lambda _value: self._refresh_vector_overlay())
        self.vector_threshold_spin.valueChanged.connect(lambda _value: self._refresh_vector_overlay())
        self.grid_check.toggled.connect(self._on_grid_toggled)
        self.grid_frame_combo.currentTextChanged.connect(lambda _text: self._refresh_graticule_overlay())
        self.colorbar_check.toggled.connect(self._on_colorbar_toggled)
        self.region_overlay_check.toggled.connect(self._refresh_region_overlays)
        self.export_plot_btn.clicked.connect(self.export_plot)
        self.export_crop_btn.clicked.connect(self.export_cropped_fits)
        self.export_regions_btn.clicked.connect(self.export_regions_csv)
        self.export_movie_btn.clicked.connect(self.export_movie)
        self.quick_mp4_btn.clicked.connect(lambda: self.export_movie(default_suffix=".mp4"))
        self.open_session_action.triggered.connect(self.open_session)
        self.save_session_action.triggered.connect(self.save_session)
        self.save_session_as_action.triggered.connect(self.save_session_as)
        self.fetch_action.triggered.connect(self.search_archives)
        self.find_latest_action.triggered.connect(self.find_latest_data)
        self.live_preview_action.triggered.connect(self.open_helioviewer_preview)
        self.load_selected_action.triggered.connect(self.download_and_load_selected)
        self.save_disk_action.triggered.connect(self.save_selected_to_disk)
        self.upload_action.triggered.connect(self.load_local_files)
        self.use_analyzer_action.triggered.connect(lambda: self.use_analyzer_time_window(auto_query=False))
        self.stop_action.triggered.connect(self.stop_active_operation)
        self.reset_all_action.triggered.connect(self.reset_all)
        self.plot_action.triggered.connect(lambda: self._set_difference_mode("raw"))
        self.running_diff_action.triggered.connect(lambda: self._set_difference_mode("running"))
        self.composite_action.triggered.connect(self.show_composite_plot)
        self.detect_regions_action.triggered.connect(self.detect_active_regions)
        self.labels_action.triggered.connect(self.fetch_active_region_labels)
        self.compare_viewpoint_action.triggered.connect(self.open_multiview_dialog)
        self.reset_frames_action.triggered.connect(self.reset_loaded_frames)
        self.rewind_action.triggered.connect(self.rewind_frames)
        self.previous_action.triggered.connect(self.previous_frame)
        self.play_action.triggered.connect(self.play_frames)
        self.pause_action.triggered.connect(self.pause_frames)
        self.next_action.triggered.connect(self.next_frame)
        self.build_movie_action.triggered.connect(self.export_movie)
        self.export_plot_action.triggered.connect(self.export_plot)
        self.export_crop_action.triggered.connect(self.export_cropped_fits)
        self.export_regions_action.triggered.connect(self.export_regions_csv)
        self.quick_mp4_action.triggered.connect(lambda: self.export_movie(default_suffix=".mp4"))

    def _active_canvas(self):
        if hasattr(self, "renderer_combo") and self.renderer_combo.currentText().lower().startswith("matplotlib"):
            return self.matplotlib_canvas
        return self.pyqt_canvas

    def _all_plot_canvases(self) -> tuple[Any, ...]:
        return (self.pyqt_canvas, self.matplotlib_canvas)

    def _on_renderer_changed(self, _text: str) -> None:
        active = self._active_canvas()
        self.plot_canvas_stack.setCurrentWidget(active)
        if active is not self.pyqt_canvas and self.crop_check.isChecked():
            self._set_crop_mode_checked(False)
        active.set_colorbar_visible(self.colorbar_check.isChecked())
        active.set_colormap_name(self._resolved_colormap_name())
        active.set_grid_visible(self.grid_check.isChecked()) if hasattr(active, "set_grid_visible") else None
        self._render_current_frame()

    def _sdo_only_widgets(self) -> tuple[QWidget, ...]:
        """Controls that only make sense for SDO/EUV disk imagery (AIA RGB
        composite, HMI magnetogram overlay, disk active-region detection)."""
        return (
            self.composite_btn,
            self.magnetogram_btn,
            self.mag_threshold_spin,
            self.detect_regions_btn,
            self.fetch_labels_btn,
        )

    def _loaded_is_lasco(self) -> bool:
        """True when the loaded frame sequence is a SOHO/LASCO coronagraph, for
        which AIA/HMI composites and disk active-region tools do not apply."""
        if not self._map_frames:
            return False
        frame = self._map_frames[0]
        instrument = str(getattr(frame, "instrument", "") or "").upper()
        if "LASCO" in instrument:
            return True
        detector = str(getattr(frame, "detector", "") or "").upper()
        observatory = str(getattr(frame, "observatory", "") or "").upper()
        return "SOHO" in observatory and detector in ("C2", "C3")

    def _loaded_instrument_class(self) -> str:
        """Science class (disk_euv/coronagraph/heliospheric/magnetograph) of the
        loaded frames, or UNKNOWN when nothing is loaded."""
        if not self._map_frames:
            return UNKNOWN
        return classify_frame(self._map_frames[0])

    def _selected_observable_class(self) -> str:
        """Science class of the currently selected observable."""
        instrument, value = self._current_observable()
        return classify_observable(instrument, value)

    def _effective_instrument_class(self) -> str:
        """Class that should drive tool visibility: loaded data wins, otherwise
        the observable the user is about to fetch."""
        loaded = self._loaded_instrument_class()
        return loaded if loaded != UNKNOWN else self._selected_observable_class()

    def _set_loaded_state(self, loaded: bool):
        self.plot_mode_btn.setEnabled(True)
        for widget in (
            self.difference_mode_btn,
            self.base_diff_btn,
            self.composite_btn,
            self.magnetogram_btn,
            self.mag_threshold_spin,
            self.detect_regions_btn,
            self.fetch_labels_btn,
            self.compare_viewpoint_btn,
            self.reset_loaded_btn,
            self.frame_slider,
            self.rewind_btn,
            self.prev_btn,
            self.play_btn,
            self.pause_btn,
            self.next_btn,
            self.export_movie_btn,
            self.quick_mp4_btn,
            self.crop_check,
            self.crop_x0_spin,
            self.crop_x1_spin,
            self.crop_y0_spin,
            self.crop_y1_spin,
            self.apply_crop_btn,
            self.export_crop_btn,
            self.export_plot_btn,
        ):
            widget.setEnabled(bool(loaded))
        if not loaded:
            self._set_crop_mode_checked(False)
        # A light curve is a time profile: it needs at least two frames.
        self.lightcurve_btn.setEnabled(bool(loaded) and len(self._map_frames) >= 2)
        # Measurement tools follow the loaded state AND the Measurements switch;
        # height-time additionally needs a time sequence, NRGF a raw-mode
        # coronagraph view.
        many_frames = bool(loaded) and len(self._map_frames) >= 2
        measure_on = bool(loaded) and self.measurements_check.isChecked()
        for widget in (
            self.ruler_tool_btn,
            self.profile_tool_btn,
            self.stats_tool_btn,
            self.clear_measure_btn,
        ):
            widget.setEnabled(measure_on)
        self.height_time_btn.setEnabled(measure_on and len(self._map_frames) >= 2)
        self.hi_jmap_btn.setEnabled(many_frames)
        # Pan/zoom is a view control, available whenever an image is loaded.
        self.pan_zoom_check.setEnabled(bool(loaded))
        if not loaded and self.pan_zoom_check.isChecked():
            self.pan_zoom_check.setChecked(False)
        self._sync_nrgf_enabled()
        if not loaded and hasattr(self, "_measure"):
            for btn in (self.ruler_tool_btn, self.profile_tool_btn, self.height_time_btn):
                if btn.isChecked():
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
            self._measure.set_mode(None)
            self._measure.clear_height_time()
            self._sync_tracking_panel_visibility()
        # White-light coronagraph / heliospheric frames have no EUV RGB
        # composite, HMI overlay or disk active-region concept — keep those
        # disk-only tools disabled (LASCO, STEREO COR1/COR2, HI1/HI2).
        if not hasattr(self, "_sdo_only_tooltips"):
            self._sdo_only_tooltips = {w: w.toolTip() for w in self._sdo_only_widgets()}
        loaded_class = self._loaded_instrument_class()
        block_disk_tools = bool(loaded and loaded_class in (CORONAGRAPH, HELIOSPHERIC))
        for widget in self._sdo_only_widgets():
            if block_disk_tools:
                widget.setEnabled(False)
                widget.setToolTip(
                    "Not applicable to white-light coronagraph / heliospheric imagery "
                    "(solar-disk tool)."
                )
            else:
                widget.setToolTip(self._sdo_only_tooltips.get(widget, ""))
        self.export_regions_btn.setEnabled(bool(self._regions))
        self._sync_menu_action_state(loaded=bool(loaded))

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = bool(busy)
        self.search_btn.setEnabled(not busy)
        self.download_load_btn.setEnabled((not busy) and bool(self._search_result and self._search_result.rows))
        self.find_latest_btn.setEnabled(not busy)
        self.load_local_btn.setEnabled(not busy)
        self.vector_load_btn.setEnabled(not busy)
        self.vector_download_btn.setEnabled(not busy)
        # SDO-only download controls (source/e-mail/frame-size/cutout/high-res)
        # are gated by observable as well as busy state.
        self._apply_observable_download_gating()
        self.select_all_results_btn.setEnabled((not busy) and bool(self._search_result and self._search_result.rows))
        self.deselect_all_results_btn.setEnabled((not busy) and bool(self._search_result and self._search_result.rows))
        self.save_disk_btn.setEnabled((not busy) and bool(self._search_result and self._search_result.rows))
        self.stop_btn.setEnabled(bool(busy))
        self.progress_panel.setVisible(bool(busy))
        if busy:
            self.progress_panel.reset()
            self.progress.setRange(0, 100)
            self._progress_value = 0
            self._progress_target = 0
            self._progress_activity = False
            self._progress_soft_cap = 0
            self._progress_last_pulse = time.monotonic()
            self._byte_active = False
            self._byte_bar_value = 0
            self.progress.setValue(0)
            if text:
                self.statusBar().showMessage(text)
        else:
            self._progress_timer.stop()
            self._progress_activity = False
            self._progress_soft_cap = 0
            self._byte_active = False
            self.progress_panel.reset()
        self._sync_menu_action_state(loaded=bool(self._map_frames))

    def _sync_menu_action_state(self, *, loaded: bool) -> None:
        if not hasattr(self, "fetch_action"):
            return
        busy = bool(getattr(self, "_busy", False))
        has_results = bool(self._search_result and self._search_result.rows)
        has_regions = bool(self._regions)
        if hasattr(self, "open_session_action"):
            self.open_session_action.setEnabled(not busy)
            # Saving a session embeds the loaded FITS, so it needs loaded frames.
            self.save_session_action.setEnabled((not busy) and bool(loaded))
            self.save_session_as_action.setEnabled((not busy) and bool(loaded))
        for action in (self.fetch_action, self.find_latest_action, self.upload_action, self.use_analyzer_action):
            action.setEnabled(not busy)
        self.load_selected_action.setEnabled((not busy) and has_results)
        self.save_disk_action.setEnabled((not busy) and has_results)
        self.reset_all_action.setEnabled(not busy)
        self.stop_action.setEnabled(busy)
        for action in (
            self.running_diff_action,
            self.composite_action,
            self.detect_regions_action,
            self.labels_action,
            self.compare_viewpoint_action,
            self.reset_frames_action,
            self.rewind_action,
            self.previous_action,
            self.play_action,
            self.pause_action,
            self.next_action,
            self.build_movie_action,
            self.export_plot_action,
            self.export_crop_action,
            self.quick_mp4_action,
        ):
            action.setEnabled((not busy) and bool(loaded))
        # Disk-only analysis actions stay disabled for white-light coronagraph
        # and heliospheric frames (LASCO, STEREO COR1/COR2, HI1/HI2).
        if loaded and self._loaded_instrument_class() in (CORONAGRAPH, HELIOSPHERIC):
            for action in (self.composite_action, self.detect_regions_action, self.labels_action):
                action.setEnabled(False)
        self.plot_action.setEnabled(not busy)
        self.export_regions_action.setEnabled((not busy) and has_regions)

    def _tick_progress(self) -> None:
        if self.progress.maximum() <= 0:
            return
        target = int(max(0, min(100, self._progress_target)))
        current = int(max(0, min(100, self._progress_value)))
        if current >= target:
            if self._progress_activity and current < int(self._progress_soft_cap):
                now = time.monotonic()
                if now - float(self._progress_last_pulse) >= 0.7:
                    self._progress_last_pulse = now
                    current = min(int(self._progress_soft_cap), current + 1)
                    self._progress_value = current
                    self.progress.setValue(current)
                return
            if self._progress_activity and current >= int(self._progress_soft_cap):
                self.progress.setRange(0, 0)
                self._progress_timer.stop()
                return
            self._progress_timer.stop()
            return
        delta = target - current
        step = max(1, min(10, int(round(delta * 0.35))))
        current = min(target, current + step)
        self._progress_value = current
        self.progress.setValue(current)

    def _update_progress_activity(self, value: object, text: object) -> None:
        message = str(text or "").lower()
        active = any(word in message for word in ("downloading", "downloaded", "fetched", "fetching"))
        loading = any(word in message for word in ("loading", "finalizing", "finalising"))
        if value is None:
            self._progress_activity = False
            self._progress_soft_cap = 0
            return
        if active:
            self._progress_activity = True
            self._progress_soft_cap = 86
            return
        if loading:
            self._progress_activity = True
            self._progress_soft_cap = 96
            return
        if self._progress_target >= 95:
            self._progress_activity = False
            self._progress_soft_cap = 0

    def _current_observable(self) -> tuple[str, Any]:
        """Return (instrument, value) for the selected observable.

        AIA -> ("AIA", wavelength_float); HMI -> ("HMI", product_str).
        """
        data = self.wavelength_combo.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            return str(data[0]).upper(), data[1]
        return "AIA", 193.0

    def _build_query_spec(self) -> SunPyQuerySpec:
        start_dt = self.start_dt_edit.dateTime().toPython().replace(tzinfo=None)
        end_dt = self.end_dt_edit.dateTime().toPython().replace(tzinfo=None)
        sample_seconds = int(self.sample_seconds_spin.value() or 0)
        instrument, value = self._current_observable()
        if instrument == "HMI":
            return SunPyQuerySpec(
                start_dt=start_dt,
                end_dt=end_dt,
                spacecraft="SDO",
                instrument="HMI",
                product=str(value),
                sample_seconds=sample_seconds if sample_seconds > 0 else None,
                max_records=int(self.max_records_spin.value()),
            )
        if instrument == "LASCO":
            # SOHO/LASCO coronagraph: VSO-only, selected by detector (C2/C3),
            # no EUV wavelength, no HMI product, no JSOC resolution/cutout.
            return SunPyQuerySpec(
                start_dt=start_dt,
                end_dt=end_dt,
                spacecraft="SOHO",
                instrument="LASCO",
                detector=str(value),
                sample_seconds=sample_seconds if sample_seconds > 0 else None,
                max_records=int(self.max_records_spin.value()),
            )
        if instrument == "SECCHI":
            # STEREO/SECCHI (VSO-only): value is (spacecraft, detector, wavelength).
            # EUVI carries a wavelength; COR1/COR2/HI1/HI2 do not.
            spacecraft, detector, wavelength = value
            return SunPyQuerySpec(
                start_dt=start_dt,
                end_dt=end_dt,
                spacecraft=str(spacecraft),
                instrument="SECCHI",
                detector=str(detector),
                wavelength_angstrom=float(wavelength) if wavelength else None,
                sample_seconds=sample_seconds if sample_seconds > 0 else None,
                max_records=int(self.max_records_spin.value()),
            )
        if instrument == "SUVI":
            # GOES/SUVI EUV imager (NOAA dataretriever), L1b. GOES-18 carries the
            # operational SUVI for current dates (16 retired to storage in 2025;
            # 19 is not registered in sunpy 7.1's SUVIClient).
            return SunPyQuerySpec(
                start_dt=start_dt,
                end_dt=end_dt,
                spacecraft="GOES",
                instrument="SUVI",
                wavelength_angstrom=float(value),
                satellite_number=18,
                level="1b",
                max_records=int(self.max_records_spin.value()),
            )
        return SunPyQuerySpec(
            start_dt=start_dt,
            end_dt=end_dt,
            spacecraft="SDO",
            instrument="AIA",
            wavelength_angstrom=float(value or 193.0),
            sample_seconds=sample_seconds if sample_seconds > 0 else None,
            resolution=AIA_FULL_RESOLUTION if self.high_resolution_check.isChecked() else None,
            max_records=int(self.max_records_spin.value()),
        )

    def set_time_window(self, start_dt: datetime, end_dt: datetime, *, auto_query: bool = False) -> bool:
        if end_dt <= start_dt:
            return False
        self.start_dt_edit.setDateTime(QDateTime(start_dt.replace(tzinfo=None)))
        self.end_dt_edit.setDateTime(QDateTime(end_dt.replace(tzinfo=None)))
        if auto_query:
            self.search_archives()
        return True

    def use_analyzer_time_window(self, *, auto_query: bool = False):
        parent = self.parent()
        if parent is None or not hasattr(parent, "_current_time_window_utc"):
            return False
        try:
            window = parent._current_time_window_utc()
        except Exception:
            window = None
        if not window:
            return False
        return self.set_time_window(window[0], window[1], auto_query=auto_query)

    def search_archives(self):
        try:
            spec = self._build_query_spec()
        except Exception as exc:
            QMessageBox.warning(self, "Solar Image Analysis", f"Invalid query inputs: {exc}")
            return
        self._set_busy(True, "Searching solar image archives...")
        self._start_worker(SunPyWorker("search", query_spec=spec))

    def find_latest_data(self):
        """Jump to the newest records actually available in the archive.

        Definitive VSO archives — notably SOHO/LASCO — can lag real time by many
        months, so a plain search over recent dates returns nothing. This walks
        back to the archive's data frontier, sets the query window to the newest
        available frames and lists them for download.
        """
        try:
            spec = self._build_query_spec()
        except Exception as exc:
            QMessageBox.warning(self, "Solar Image Analysis", f"Invalid query inputs: {exc}")
            return
        self._pending_latest = True
        self._set_busy(
            True,
            "Scanning the archive for the latest available data (this can take up to a minute)...",
        )
        self._start_worker(SunPyWorker("find_latest", query_spec=spec))

    def open_helioviewer_preview(self):
        """Open the near-real-time SOHO/LASCO quicklook preview (Helioviewer).

        These are browse images updated within ~an hour — the fresh view the
        calibrated (months-behind) VSO/SDAC FITS archive cannot provide.
        """
        instrument, value = self._current_observable()
        if instrument != "LASCO":
            QMessageBox.information(
                self,
                "Live Preview",
                "The near-real-time preview is available for SOHO/LASCO. "
                "Select the SOHO/LASCO C2 or C3 observable first.",
            )
            return
        detector = str(value or "C2")
        try:
            from src.UI.helioviewer_preview_dialog import HelioviewerPreviewDialog
        except Exception as exc:  # noqa: BLE001 - surface an import/runtime failure cleanly
            QMessageBox.critical(self, "Live Preview", f"The preview could not be opened:\n{exc}")
            return
        existing = self._helioviewer_dialog
        if existing is not None:
            try:
                existing.close()
            except Exception:
                pass
        dialog = HelioviewerPreviewDialog(self, detector=detector, theme=self.theme)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        self._helioviewer_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def save_selected_to_disk(self):
        """Download the selected rows into a user-chosen folder (kept on disk)."""
        if self._search_result is None:
            QMessageBox.information(self, "Solar Image Analysis", "Run an archive search first.")
            return
        if not self._checked_rows():
            QMessageBox.information(self, "Solar Image Analysis", "Select at least one result row to save.")
            return
        target_dir = QFileDialog.getExistingDirectory(self, "Save Selected FITS To Folder")
        if not target_dir:
            return
        self.download_and_load_selected(target_dir=Path(target_dir))

    def download_and_load_selected(self, target_dir: Path | None = None):
        # Connected Qt signals (clicked/triggered) pass a bool; ignore it so the
        # download still defaults to the cache folder.
        if not isinstance(target_dir, (str, Path)):
            target_dir = None
        if self._search_result is None:
            QMessageBox.information(self, "Solar Image Analysis", "Run an archive search first.")
            return
        selected_rows = self._checked_rows()
        if not selected_rows:
            QMessageBox.information(self, "Solar Image Analysis", "Select at least one result row.")
            return
        if not self._confirm_high_resolution_download(selected_rows):
            return

        # SOHO/LASCO is VSO-only: no JSOC fast path, no server-side binning or
        # cutout, so bypass the JSOC e-mail / frame-size handling entirely.
        is_lasco = self._current_observable()[0] == "LASCO"
        if is_lasco:
            email, prefer_jsoc, process = "", False, None
        else:
            email, prefer_jsoc = self._jsoc_params()
            size_mode = self._frame_size_mode()
            needs_jsoc = size_mode != SIZE_FULL

            if (str(self.source_combo.currentData() or "") == "jsoc" or needs_jsoc) and not email:
                reason = (
                    f"The '{self.frame_size_combo.currentText()}' frame size is produced server-side by JSOC"
                    if needs_jsoc
                    else "The JSOC source"
                )
                QMessageBox.information(
                    self,
                    "Solar Image Analysis",
                    f"{reason}, which needs a registered notify e-mail.\n"
                    "Enter it in the JSOC Notify E-mail field, switch Frame Size to Full disk, "
                    "or switch the source to VSO.\n\n"
                    "Register once (free) at https://jsoc.stanford.edu/ajax/register_email.html",
                )
                return

            # Binned/cutout require JSOC; force it on (with email present).
            if needs_jsoc:
                prefer_jsoc = True

            # Reference time for cutout tracking: the start of the selection.
            t_ref = None
            try:
                rows = self._search_result.rows
                t_ref = min(rows[i].start for i in selected_rows).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                t_ref = None
            try:
                process = self._frame_size_process(t_ref=t_ref) if prefer_jsoc else None
            except JsocError as exc:
                QMessageBox.warning(self, "Solar Image Analysis", f"Invalid cutout settings: {exc}")
                return

        effective_cache = Path(target_dir) if target_dir else self.cache_dir
        self._save_target_dir = str(effective_cache) if target_dir else None
        if target_dir:
            text = f"Downloading selected files to {effective_cache} ..."
        elif process is not None:
            text = "Downloading reduced frames via JSOC..."
        elif prefer_jsoc:
            text = "Downloading frames via JSOC (fast path)..."
        elif self._search_result_is_high_resolution():
            text = "Downloading high-resolution sequence..."
        else:
            text = "Downloading selected files..."
        self._set_busy(True, text)
        self._start_worker(
            SunPyWorker(
                "fetch_load",
                search_result=self._search_result,
                selected_rows=selected_rows,
                cache_dir=effective_cache,
                jsoc_email=email,
                prefer_jsoc=prefer_jsoc,
                jsoc_process=process,
            )
        )

    def _jsoc_params(self) -> tuple[str, bool]:
        """Return (email, prefer_jsoc) from the Data Source controls."""
        source = str(self.source_combo.currentData() or "auto")
        email = str(self.jsoc_email_edit.text() or "").strip()
        prefer = source in ("auto", "jsoc") and bool(email)
        return email, prefer

    def _app_settings(self):
        # Reuse the viewer's QSettings factory; accessed through the module so
        # the test suite's settings isolation (conftest) takes effect.
        from src.UI import sunpy_solar_viewer

        return sunpy_solar_viewer._make_settings()

    def _restore_jsoc_settings(self) -> None:
        try:
            settings = self._app_settings()
            email = str(settings.value("sdo/jsoc_email", "") or "")
            source = str(settings.value("sdo/source", "auto") or "auto")
        except Exception:
            return
        if email:
            self.jsoc_email_edit.setText(email)
        idx = self.source_combo.findData(source)
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)

    def _save_jsoc_settings(self) -> None:
        try:
            settings = self._app_settings()
            settings.setValue("sdo/jsoc_email", str(self.jsoc_email_edit.text() or "").strip())
            settings.setValue("sdo/source", str(self.source_combo.currentData() or "auto"))
        except Exception:
            pass

    # -- Frame size (Phase 3) ---------------------------------------------
    def _frame_size_mode(self) -> str:
        return str(self.frame_size_combo.currentData() or SIZE_FULL)

    def _on_frame_size_changed(self) -> None:
        is_cutout = self._frame_size_mode() == SIZE_CUTOUT
        # Never re-show the cutout fields inside a collapsed accordion card.
        source_expanded = (
            not self.data_source_group.isCheckable() or self.data_source_group.isChecked()
        )
        self.cutout_widget.setVisible(is_cutout and source_expanded)
        self._update_size_estimate()

    def _estimated_frame_count(self) -> int:
        try:
            return len(self._checked_rows())
        except Exception:
            return 0

    def _update_size_estimate(self) -> None:
        mode = self._frame_size_mode()
        n_frames = self._estimated_frame_count()
        total_bytes, seconds = estimate_download(n_frames, mode)
        if n_frames <= 0:
            self.size_estimate_label.setText("Select frames to see a size estimate.")
            return
        needs_jsoc = mode != SIZE_FULL
        suffix = "  (JSOC only)" if needs_jsoc else ""
        self.size_estimate_label.setText(
            f"≈ {n_frames} frame(s) · ~{format_bytes(total_bytes)} · ~{format_eta(seconds)}{suffix}"
        )

    def _frame_size_process(self, *, t_ref: str | None = None) -> dict | None:
        """Build the JSOC process dict for the chosen frame size (or None)."""
        mode = self._frame_size_mode()
        if mode == SIZE_CUTOUT:
            cutout = (
                float(self.cutout_x_spin.value()),
                float(self.cutout_y_spin.value()),
                float(self.cutout_w_spin.value()),
                float(self.cutout_h_spin.value()),
            )
            return size_process(mode, cutout=cutout, t_ref=t_ref)
        return size_process(mode)

    def _confirm_high_resolution_download(self, selected_rows: list[int]) -> bool:
        if not self._search_result_is_high_resolution():
            return True
        if len(selected_rows) <= AIA_HIGH_RES_WARN_ROWS:
            return True
        response = QMessageBox.question(
            self,
            "High Resolution AIA Download",
            (
                f"You selected {len(selected_rows)} high-resolution AIA frame(s).\n\n"
                "This can be slow because full-resolution files are large. The downloader will prioritize "
                "the request and update progress continuously. If you press Stop, files already downloaded "
                "will stay in the cache and will be loaded if possible.\n\n"
                "Continue?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return response == QMessageBox.Yes

    def _search_result_is_high_resolution(self) -> bool:
        return bool(self._search_result is not None and getattr(self._search_result.spec, "resolution", None) is not None)

    def _start_worker(self, worker: QObject):
        if self._active_thread is not None:
            QMessageBox.information(self, "Solar Image Analysis", "Another operation is still running.")
            return
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        if hasattr(worker, "progress"):
            worker.progress.connect(self._on_worker_progress)
        if isinstance(worker, SunPyWorker):
            worker.failed.connect(self._on_worker_failed)
            worker.byte_progress.connect(self._on_byte_progress)
            worker.partial_warning.connect(self._on_partial_warning)
            worker.cancelled.connect(self._on_worker_cancelled)
            worker.search_finished.connect(self._on_search_finished)
            worker.load_finished.connect(self._on_load_finished)
            worker.failed.connect(thread.quit)
            worker.cancelled.connect(thread.quit)
            worker.search_finished.connect(thread.quit)
            worker.load_finished.connect(thread.quit)
        elif isinstance(worker, SolarMetadataWorker):
            worker.failed.connect(self._on_worker_failed)
            worker.finished.connect(self._on_metadata_finished)
            worker.failed.connect(thread.quit)
            worker.finished.connect(thread.quit)
        elif isinstance(worker, MovieExportWorker):
            worker.export_progress.connect(self._on_export_progress)
            worker.finished.connect(self._on_export_finished)
            worker.failed.connect(self._on_worker_failed)
            worker.cancelled.connect(self._on_worker_cancelled)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.cancelled.connect(thread.quit)
        elif isinstance(worker, MapLoadWorker):
            worker.load_progress.connect(self._on_load_maps_progress)
            worker.finished.connect(self._on_local_maps_loaded)
            worker.failed.connect(self._on_worker_failed)
            worker.cancelled.connect(self._on_worker_cancelled)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.cancelled.connect(thread.quit)
        elif isinstance(worker, (VectorFieldLoadWorker, VectorFieldDownloadWorker)):
            if hasattr(worker, "byte_progress"):
                worker.byte_progress.connect(self._on_byte_progress)
            worker.finished.connect(self._on_vector_frames_loaded)
            worker.failed.connect(self._on_worker_failed)
            worker.cancelled.connect(self._on_worker_cancelled)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.cancelled.connect(thread.quit)
            if hasattr(worker, "no_records"):
                worker.no_records.connect(self._on_vector_no_records)
                worker.no_records.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_worker_stopped)
        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _on_worker_stopped(self):
        self._active_thread = None
        self._active_worker = None
        # Clear the find-latest flag so a failed/cancelled scan never taints the
        # next ordinary search (it is consumed on success in _on_search_finished).
        self._pending_latest = False
        self._set_busy(False)
        # A close requested mid-download was deferred until the worker stopped;
        # complete it now that nothing is running.
        if self._pending_close:
            self._pending_close = False
            self.close()
            return
        # A vector re-download accepted while the previous worker was still
        # winding down starts once the thread has fully stopped.
        if self._pending_vector_download:
            self._pending_vector_download = False
            self.download_vector_field()

    def is_operation_running(self) -> bool:
        thread = self._active_thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except Exception:
            return True

    @Slot(object, object)
    def _on_worker_progress(self, value, text):
        # While real byte-level progress is driving the download window
        # (worker maps downloading to the 5..85 band), ignore the coarse
        # file-count ticks so the honest byte bar is not overwritten. Search
        # (<5) and the post-download loading phase (>85) still flow through.
        if self._byte_active and value is not None and 5 <= int(value) <= 85:
            if text:
                self.statusBar().showMessage(str(text))
            return
        if value is not None and int(value) > 85:
            self._byte_active = False

        if value is None:
            self.progress.setRange(0, 0)
            self._progress_timer.stop()
        else:
            if self.progress.maximum() <= 0:
                self.progress.setRange(0, 100)
                self.progress.setValue(max(0, min(100, int(self._progress_value))))
            self._progress_target = max(0, min(100, int(value)))
            if not self._progress_timer.isActive():
                self._progress_timer.start()
        self._update_progress_activity(value, text)
        if self._progress_activity and not self._progress_timer.isActive() and self.progress.maximum() > 0:
            self._progress_timer.start()
        if text:
            self.statusBar().showMessage(str(text))

    @Slot(object)
    def _on_byte_progress(self, agg: object):
        """Render honest byte-level download progress from the fetch poller.

        Drives the bar within the worker's 5..85 download band (search and
        loading own the head/tail), stops the simulated creep timer, and feeds
        the detailed MB / MB·s / ETA read-out in the panel.
        """
        if agg is None:
            return
        self._byte_active = True
        self._progress_timer.stop()
        try:
            # File-count based fraction (plus in-flight partials): honest and
            # smooth even when the source doesn't report total bytes upfront.
            getter = getattr(agg, "progress_fraction", None)
            fraction = float(getter()) if callable(getter) else float(getattr(agg, "fraction", 0.0) or 0.0)
        except Exception:
            fraction = 0.0
        bar_value = 5 + int(round(max(0.0, min(1.0, fraction)) * 80))
        # Monotonic: never let a transient cache re-read drag the bar backwards.
        bar_value = max(self._byte_bar_value, bar_value)
        self._byte_bar_value = bar_value
        self._progress_value = bar_value
        if self.progress.maximum() <= 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, bar_value)))
        try:
            self.progress_panel.update_aggregate(agg, drive_bar=False)
        except Exception:
            pass

    @Slot(str)
    def _on_worker_failed(self, tb_text: str):
        # A failed load must not leave a session restore pending for the next one.
        self._pending_session_restore = None
        short = str(tb_text).strip().splitlines()[-1] if tb_text else "Unknown error"
        self.statusBar().showMessage("Solar data operation failed.", 5000)
        self.analysis_text.setPlainText("Operation failed.\n\n" + short)
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Solar Image Analysis")
        msg.setText(short)
        msg.setDetailedText(tb_text or "")
        msg.exec()

    @Slot(str)
    def _on_partial_warning(self, message: str):
        QMessageBox.warning(self, "Partial Download", message)

    @Slot()
    def _on_worker_cancelled(self):
        self._pending_session_restore = None
        self.statusBar().showMessage("Operation cancelled.", 5000)
        self.analysis_text.setPlainText("Operation cancelled by user.")

    @Slot(object)
    def _spec_target_label(self, spec: Any) -> str:
        """Human label for a query target, e.g. 'SDO/AIA 193 Å', 'SOHO/LASCO C2'."""
        sc = str(getattr(spec, "spacecraft", "") or "").upper()
        inst = str(getattr(spec, "instrument", "") or "").upper()
        det = str(getattr(spec, "detector", "") or "").upper()
        wl = getattr(spec, "wavelength_angstrom", None)
        prod = getattr(spec, "product", None)
        base = "/".join([x for x in (sc, inst) if x]) or "archive"
        if det:
            return f"{base} {det}"
        if wl:
            return f"{base} {int(round(float(wl)))} Å"
        if prod:
            return f"{base} {prod}"
        return base

    def _sync_time_fields_from_spec(self, spec: Any) -> None:
        try:
            self.start_dt_edit.setDateTime(QDateTime(spec.start_dt.replace(tzinfo=None)))
            self.end_dt_edit.setDateTime(QDateTime(spec.end_dt.replace(tzinfo=None)))
        except Exception:
            pass

    def _announce_latest(self, result: SunPySearchResult, target: str) -> None:
        latest = max((r.start for r in result.rows), default=None)
        oldest = min((r.start for r in result.rows), default=None)
        if latest is None:
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age_days = max(0, int((now - latest).total_seconds() // 86400))
        lines = [
            f"Latest available {target} data in the VSO archive:",
            f"  Newest frame: {latest:%Y-%m-%d %H:%M:%S} UTC  (~{age_days} day(s) behind real time)",
            f"  Loaded window: {oldest:%Y-%m-%d %H:%M} → {latest:%Y-%m-%d %H:%M} UTC · {len(result.rows)} record(s).",
            "Select rows and click Load Selected to download and plot the newest frames.",
        ]
        if "LASCO" in target.upper():
            lines.append(
                "Note: the calibrated SOHO/LASCO FITS archive lags real time by many months. "
                "For near-real-time coronagraph movies, use Solar Events → CMEs → SOHO/LASCO CME Catalog."
            )
        self.analysis_text.setPlainText("\n".join(lines))
        self.statusBar().showMessage(
            f"Latest {target}: {latest:%Y-%m-%d %H:%M} UTC (~{age_days} day(s) behind).", 8000
        )

    def _on_search_finished(self, result_obj: object):
        result = result_obj if isinstance(result_obj, SunPySearchResult) else None
        if result is None:
            self._on_worker_failed("Search worker returned an unexpected payload.")
            return
        latest_mode = bool(getattr(self, "_pending_latest", False))
        self._pending_latest = False
        self._search_result = result
        self._populate_results_table(result)
        self.download_load_btn.setEnabled(bool(result.rows))
        self._set_results_selection_controls_enabled(bool(result.rows))
        self._sync_menu_action_state(loaded=bool(self._map_frames))
        target = self._spec_target_label(result.spec)
        if not result.rows:
            self.archive_results_status_label.setText(f"No {target} archive records found for this query.")
            hint = (
                "\nTip: the calibrated archives lag real time — SOHO/LASCO is often a year "
                "or more behind — so recent windows can be empty even while the instrument "
                "is observing. Use Data → Find Latest Available to jump to the newest records"
            )
            if str(result.spec.instrument or "").upper() == "LASCO":
                hint += ", or the Live Preview button for near-real-time Helioviewer quicklooks"
            self.analysis_text.setPlainText(
                f"No {target} records found for the selected time range.{hint}."
            )
            return
        if latest_mode:
            self._sync_time_fields_from_spec(result.spec)
        quality_text = " high-resolution" if result.spec.resolution is not None else ""
        self.archive_results_status_label.setText(
            f"{len(result.rows)}{quality_text} record(s) found. Checked rows will be downloaded with Load Selected."
        )
        if latest_mode:
            self._announce_latest(result, target)
        else:
            self.analysis_text.setPlainText(f"Found {len(result.rows)} {target}{quality_text} archive records.")
        self.statusBar().showMessage(f"Found {len(result.rows)} {target} records.", 5000)

    @Slot(object, object)
    def _on_load_finished(self, fetch_obj: object, load_obj: object):
        fetch_result = fetch_obj if isinstance(fetch_obj, SunPyFetchResult) else None
        load_result = load_obj if isinstance(load_obj, SunPyLoadResult) else None
        if fetch_result is None or load_result is None:
            self._on_worker_failed("Download worker returned an unexpected payload.")
            return
        if load_result.data_kind != DATA_KIND_MAP:
            self._on_worker_failed("Solar Data Analysis supports map products only.")
            return
        frames = extract_map_frames(load_result.maps_or_timeseries)
        metadata = dict(load_result.metadata or {})
        metadata["resolution_requested"] = getattr(self._search_result.spec, "resolution", None) is not None
        self._apply_loaded_frames(frames, paths=list(fetch_result.paths), metadata=metadata)

    @Slot(object)
    def _on_metadata_finished(self, metadata_obj: object):
        self._metadata_regions = list(metadata_obj or [])
        self.metadata_status_label.setText(f"Metadata: {len(self._metadata_regions)} region(s)")
        if self._regions:
            self._regions = label_regions_with_metadata(self._regions, self._metadata_regions)
            self._populate_region_table()
            self._refresh_region_overlays()
        self.statusBar().showMessage("Active-region metadata updated.", 5000)

    def _populate_results_table(self, result: SunPySearchResult):
        self.results_table.setRowCount(len(result.rows))
        for row_index, row in enumerate(result.rows):
            select_item = QTableWidgetItem("")
            select_item.setFlags(select_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            select_item.setCheckState(Qt.Checked if row.selected else Qt.Unchecked)
            select_item.setTextAlignment(Qt.AlignCenter)
            self.results_table.setItem(row_index, 0, select_item)
            values = [
                row.start.strftime("%Y-%m-%d %H:%M:%S"),
                str(row.source or row.instrument or ""),
                str(row.size or ""),
                str(row.fileid or ""),
            ]
            for col, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.results_table.setItem(row_index, col, item)
        self.results_table.resizeRowsToContents()
        self._set_results_selection_controls_enabled(bool(result.rows))

    def _set_results_selection_controls_enabled(self, enabled: bool) -> None:
        self.select_all_results_btn.setEnabled(bool(enabled))
        self.deselect_all_results_btn.setEnabled(bool(enabled))
        self.save_disk_btn.setEnabled(bool(enabled))

    def select_all_results(self) -> None:
        self._set_all_result_check_states(Qt.Checked)

    def deselect_all_results(self) -> None:
        self._set_all_result_check_states(Qt.Unchecked)

    def _set_all_result_check_states(self, state: Qt.CheckState) -> None:
        for i in range(self.results_table.rowCount()):
            item = self.results_table.item(i, 0)
            if item is not None:
                item.setCheckState(state)

    def _checked_rows(self) -> list[int]:
        checked: list[int] = []
        for i in range(self.results_table.rowCount()):
            item = self.results_table.item(i, 0)
            if item is not None and item.checkState() == Qt.Checked:
                checked.append(i)
        return checked

    def load_local_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Load Local Solar FITS",
            "",
            "FITS files (*.fit *.fits *.fit.gz *.fits.gz *.fts)",
        )
        if paths:
            self.load_local_paths(paths)

    def load_local_paths(self, paths: list[str]) -> None:
        paths = [str(p) for p in (paths or []) if str(p).strip()]
        if not paths:
            return
        if self.is_operation_running():
            QMessageBox.information(self, "Load Local Solar FITS", "Another operation is still running.")
            return
        self._save_target_dir = None
        self._set_busy(True, f"Loading {len(paths)} FITS file(s)…")
        self.progress_panel.set_status_text("Reading files…")
        self._start_worker(MapLoadWorker(paths))

    @Slot(int, int)
    def _on_load_maps_progress(self, done: int, total: int):
        total = max(1, int(total))
        pct = int(max(0, min(100, int(done) * 100 / total)))
        if self.progress.maximum() <= 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(pct)
        self.progress_panel.set_status_text(f"Loading frame {int(done)} of {total}  ·  {pct}%")
        self.statusBar().showMessage(f"Loading FITS: frame {int(done)}/{total}", 2000)

    @Slot(object, object, object)
    def _on_local_maps_loaded(self, frames: object, paths: object, metadata: object):
        try:
            self._apply_loaded_frames(
                list(frames or []), paths=list(paths or []), metadata=dict(metadata or {})
            )
        except Exception as exc:
            self._on_worker_failed(str(exc))

    def _sort_frames_by_time(self, frames: list[Any]) -> list[Any]:
        """Return frames sorted by observation time (stable).

        Frames whose header carries no usable time keep their original relative
        order and are placed after the timed ones, so a missing timestamp never
        scrambles the rest.
        """
        timed: list[tuple[datetime, int, Any]] = []
        untimed: list[Any] = []
        for index, frame in enumerate(frames):
            when = None
            try:
                when = frame_observation_time(frame)
            except Exception:
                when = None
            if when is not None:
                timed.append((when, index, frame))
            else:
                untimed.append(frame)
        timed.sort(key=lambda item: (item[0], item[1]))
        return [frame for _, _, frame in timed] + untimed

    def _apply_loaded_frames(self, frames: list[Any], *, paths: list[str], metadata: dict[str, Any]):
        if not frames:
            QMessageBox.information(self, "Solar Image Analysis", "No map frames were loaded.")
            return
        # Order frames chronologically so the time-lapse / movie plays in
        # observation order regardless of how the files were uploaded or the
        # order downloads completed.
        frames = self._sort_frames_by_time(list(frames))
        # Keep a single consistent observing configuration. Archive windows mix
        # frames that must never be differenced against each other: STEREO/SECCHI
        # COR sequences interleave polarizer triplets (POLAR=0/120/240) with
        # total-brightness frames (POLAR=1001) at the same image size, plus small
        # browse "double" frames; mixed uploads can span wavelengths. Keeping only
        # the science group prevents raw-looking frames sneaking into running/base
        # difference views and movies.
        partition = partition_frames_by_config(frames)
        dropped_note = ""
        self._loaded_config_key = partition.kept_key
        if partition.dropped and len(partition.kept) >= 2:
            frames = partition.kept
            dropped_note = f"\nNote: {partition.note}"
        elif partition.dropped:
            # Too few compatible frames to auto-filter; keep everything but warn.
            dropped_note = (
                "\nWarning: this sequence mixes observing configurations "
                "(polarizer states, wavelengths or image sizes); running/base "
                "differences may be unreliable."
            )
        # Differencing frames with unequal exposure times in raw DN creates
        # false signal, so remember whether normalisation to DN/s is needed.
        self._exposure_varies = exposures_differ(frames)
        self._crop_applied = False
        self._applied_crop_arcsec = None
        self._loaded_paths = list(paths or [])
        self._original_frames = list(frames)
        self._map_frames = list(frames)
        self._map_metadata = dict(metadata or {})
        self._regions = []
        self._metadata_regions = []
        self._current_frame_index = 0
        self.region_table.setRowCount(0)
        self.metadata_status_label.setText("Metadata: not loaded")
        self._set_crop_mode_checked(False)
        self._set_loaded_state(True)
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, max(0, len(self._map_frames) - 1))
        self.frame_slider.setValue(0)
        self.frame_slider.setEnabled(len(self._map_frames) > 1)
        self.frame_slider.blockSignals(False)
        self._select_default_colormap_for_wavelength(self._map_frames[0])
        self._render_current_frame()
        status = self._loaded_frame_status_text("Loaded", self._map_frames)
        save_dir = getattr(self, "_save_target_dir", None)
        if save_dir:
            status += f"\nSaved {len(self._loaded_paths)} file(s) to {save_dir}."
            self.statusBar().showMessage(f"Saved {len(self._loaded_paths)} file(s) to {save_dir}", 7000)
        else:
            self.statusBar().showMessage(
                f"Loaded {len(self._map_frames)} {self._loaded_instrument_label()} frame(s).", 5000
            )
        self._save_target_dir = None
        self.analysis_text.setPlainText(status + dropped_note)
        details_bar = self.analysis_text.verticalScrollBar()
        details_bar.setValue(details_bar.maximum())
        self._update_load_summary()
        self._apply_instrument_visibility()
        # If these frames were loaded to restore a saved session, replay the
        # saved display state and CME picks now that the sequence is in memory.
        if self._pending_session_restore is not None:
            pending, self._pending_session_restore = self._pending_session_restore, None
            try:
                self._apply_session_restore(pending)
            except Exception as exc:  # noqa: BLE001 - a bad session must not crash the load
                QMessageBox.warning(
                    self,
                    "Open Session",
                    f"Frames loaded, but the saved analysis state could not be fully "
                    f"restored:\n{exc}",
                )

    def _update_load_summary(self) -> None:
        """Refresh the always-visible 'what is loaded' line in the sidebar."""
        if not hasattr(self, "load_summary_label"):
            return
        if not self._map_frames:
            self.load_summary_label.setText("No data loaded.")
            return
        parts = [f"Loaded: {self._loaded_instrument_label()}", f"{len(self._map_frames)} frame(s)"]
        key = self._loaded_config_key
        if key is not None and self._loaded_instrument_class() == CORONAGRAPH:
            polar_state = key[3]
            parts.append(
                "total-brightness" if polar_state == "total" else f"polarizer {polar_state[3:]}°"
            )
        if self._exposure_varies:
            parts.append("differences in DN/s")
        self.load_summary_label.setText(" · ".join(parts))

    def _loaded_instrument_label(self, frames: list[Any] | None = None) -> str:
        """Short instrument label for status text, e.g. 'AIA', 'HMI', 'LASCO C2',
        'STEREO-A COR2', 'SUVI 171'."""
        frames = frames if frames is not None else self._map_frames
        if not frames:
            return "image"
        frame = frames[0]
        inst = str(getattr(frame, "instrument", "") or "").upper()
        det = str(getattr(frame, "detector", "") or "").strip().upper()
        obs = str(getattr(frame, "observatory", "") or "").strip().upper()
        if "LASCO" in inst:
            return f"LASCO {det}".strip()
        if "AIA" in inst:
            return "AIA"
        if "HMI" in inst:
            return "HMI"
        if "SECCHI" in inst or det in ("EUVI", "COR1", "COR2", "HI1", "HI2"):
            # "STEREO_A"/"STEREO A" -> "STEREO-A"
            craft = obs.replace("_", "-").replace(" ", "-") if "STEREO" in obs else "STEREO"
            return f"{craft} {det}".strip()
        if "SUVI" in inst or "SOLAR ULTRAVIOLET IMAGER" in inst:
            wl = self._frame_wavelength_value(frame)
            return f"SUVI {int(round(wl))}" if wl else "SUVI"
        return (inst or det or "image").strip() or "image"

    def _frames_word(self) -> str:
        """Instrument word for dialogs: the loaded label, else a neutral term."""
        label = self._loaded_instrument_label()
        return label if label != "image" else "solar image"

    def _export_basename(self, stem: str) -> str:
        """Instrument-aware default export filename, e.g. 'stereo_a_cor2_movie'."""
        label = self._loaded_instrument_label()
        if label == "image":
            return f"solar_{stem}"
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        return f"{slug}_{stem}" if slug else f"solar_{stem}"

    def _loaded_frame_status_text(self, action: str, frames: list[Any]) -> str:
        label = self._loaded_instrument_label(frames)
        if "LASCO" in label.upper():
            tools = "Use the embedded timeline, crop, colormap, difference and movie tools in this window."
        else:
            tools = "Use the embedded timeline, crop controls, colormap, and active-region tools in this window."
        return (
            f"{action} {len(frames)} {label} frame(s).\n"
            f"{self._frame_resolution_status(frames)}\n"
            f"{tools}"
        )

    def _frame_resolution_status(self, frames: list[Any]) -> str:
        if not frames:
            return "Resolution: no frame loaded."
        shape = np.asarray(getattr(frames[0], "data", np.empty((0, 0)))).shape
        if len(shape) < 2:
            return "Resolution: unknown."
        height = int(shape[0])
        width = int(shape[1])
        requested = bool(self._map_metadata.get("resolution_requested"))
        quality = "full-resolution requested" if requested else "native archive/file resolution"
        if min(width, height) >= 3000:
            quality += ", high-detail frame"
        elif max(width, height) <= 1200:
            quality += ", reduced-size frame"
        return f"Resolution: {width} x {height} px ({quality})."

    def _radio_reference_window(self) -> tuple[datetime, datetime] | None:
        """The e-Callisto radio burst time window from the parent analyzer."""
        parent = self.parent()
        if parent is None or not hasattr(parent, "_current_time_window_utc"):
            return None
        try:
            window = parent._current_time_window_utc()
        except Exception:
            return None
        if not window or len(window) < 2:
            return None
        try:
            start = window[0].replace(tzinfo=None)
            end = window[1].replace(tzinfo=None)
        except Exception:
            return None
        return (start, end)

    def show_region_lightcurve(self) -> None:
        if not self._map_frames:
            QMessageBox.information(
                self,
                "Region Light Curve",
                f"Load or upload {self._frames_word()} frames first. The light curve needs the time sequence.",
            )
            return
        if len(self._map_frames) < 2:
            QMessageBox.information(
                self,
                "Region Light Curve",
                "A light curve needs at least two frames over time. Download a longer sequence.",
            )
            return
        bounds = None
        if self.crop_check.isChecked() and self._current_map_data is not None:
            bounds = self._crop_bounds_from_axis_fields(self._current_map_data.shape)
        try:
            lightcurve = extract_region_lightcurve(self._map_frames, bounds, normalize=True)
        except Exception as exc:
            QMessageBox.critical(self, "Region Light Curve", f"Could not build the light curve:\n{exc}")
            return
        dialog = RegionLightcurveDialog(
            lightcurve,
            radio_window=self._radio_reference_window(),
            instrument_label=self._frames_word(),
            parent=self,
        )
        dialog.show()
        # Keep a reference so the non-modal dialog is not garbage collected.
        self._lightcurve_dialog = dialog

    def _set_difference_mode(self, mode: str) -> None:
        if not self._map_frames:
            QMessageBox.information(
                self,
                "Plot Solar Images",
                "Load selected archive records or upload local FITS files before plotting.",
            )
            return
        if mode == "running":
            self.movie_content_combo.setCurrentText("Running Difference")
        elif mode == "base":
            self.movie_content_combo.setCurrentText("Base Difference")
        else:
            self.movie_content_combo.setCurrentText("Frames")
        self._render_current_frame()

    def _on_frame_slider_changed(self, value: int) -> None:
        self._current_frame_index = max(0, min(int(value), max(0, len(self._map_frames) - 1)))
        self._render_current_frame()

    def play_frames(self) -> None:
        if len(self._map_frames) <= 1:
            return
        if self._current_frame_index >= len(self._map_frames) - 1:
            self.rewind_frames()
        self._refresh_play_timer()
        self._play_timer.start()

    def pause_frames(self) -> None:
        self._play_timer.stop()

    def _refresh_play_timer(self) -> None:
        if self._play_timer.isActive():
            fps = max(1.0, float(self.fps_spin.value() or 1.0))
            self._play_timer.setInterval(max(1, int(round(1000.0 / fps))))

    def rewind_frames(self) -> None:
        self.pause_frames()
        self._set_frame_index(0)

    def previous_frame(self) -> None:
        self.pause_frames()
        self._set_frame_index(self._current_frame_index - 1)

    def next_frame(self) -> None:
        if not self._map_frames:
            return
        if self._current_frame_index >= len(self._map_frames) - 1:
            self.pause_frames()
            return
        self._set_frame_index(self._current_frame_index + 1)

    def _set_frame_index(self, index: int) -> None:
        index = max(0, min(int(index), max(0, len(self._map_frames) - 1)))
        self._current_frame_index = index
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(index)
        self.frame_slider.blockSignals(False)
        self._render_current_frame()

    def _schedule_clip_render(self) -> None:
        """Throttle live clip-slider re-renders (leading edge + ~30 fps).

        Renders immediately on the first change, then at most once per ~33 ms
        while the handle keeps moving, with a final render after the last
        change. Keeps dragging smooth without re-rendering on every 0.1% tick.
        """
        if self._clip_render_timer.isActive():
            self._clip_render_pending = True
            return
        self._render_current_frame()
        self._clip_render_timer.start()

    def _flush_clip_render(self) -> None:
        if self._clip_render_pending:
            self._clip_render_pending = False
            self._render_current_frame()
        else:
            self._clip_render_timer.stop()

    def _render_current_frame(self) -> None:
        if not self._map_frames:
            for canvas in self._all_plot_canvases():
                canvas.clear_plot()
                canvas.set_map_title("No image data loaded.")
            self.frame_label.setText("Frame 0 / 0")
            self.plot_title_label.setText("No image data loaded.")
            return

        idx = max(0, min(self._current_frame_index, len(self._map_frames) - 1))
        frame = self._map_frames[idx]
        current = self._prepare_map_array(getattr(frame, "data"), "current frame")
        title = self._frame_title(frame, idx)

        mode = self._movie_mode()
        # Frames with unequal EXPTIME must be differenced in rate units (DN/s):
        # subtracting raw DN would show the exposure ratio as false brightening.
        normalize = bool(self._exposure_varies) and mode in ("base", "running")
        unit_suffix = ", DN/s" if normalize else ""

        def _diff_ready(source_frame: Any, arr: np.ndarray) -> np.ndarray:
            if not normalize:
                return arr
            exptime = frame_exposure_time(source_frame)
            return arr / exptime if exptime and exptime > 0 else arr

        if mode == "base" and len(self._map_frames) > 1:
            base = self._prepare_map_array(getattr(self._map_frames[0], "data"), "base frame")
            if base.shape == current.shape:
                current = _diff_ready(frame, current) - _diff_ready(self._map_frames[0], base)
                title += f" (Base Difference{unit_suffix})"
            else:
                title += " (raw — size differs from base frame)"
        elif mode == "running" and len(self._map_frames) > 1:
            # Difference against the neighbour only when it shares this frame's
            # size; never label a raw frame as a difference (see the load-time
            # config partitioning that normally prevents this).
            differenced = False
            if idx == 0:
                other = self._prepare_map_array(getattr(self._map_frames[1], "data"), "next frame")
                if other.shape == current.shape:
                    current = _diff_ready(self._map_frames[1], other) - _diff_ready(frame, current)
                    differenced = True
            else:
                prev_frame = self._map_frames[idx - 1]
                prev = self._prepare_map_array(getattr(prev_frame, "data"), "previous frame")
                if prev.shape == current.shape:
                    current = _diff_ready(frame, current) - _diff_ready(prev_frame, prev)
                    differenced = True
            title += (
                f" (Running Difference{unit_suffix})"
                if differenced
                else " (raw — no matching frame to difference)"
            )
        elif (
            mode == "raw"
            and getattr(self, "nrgf_check", None) is not None
            and self.nrgf_check.isChecked()
            and self._effective_instrument_class() == CORONAGRAPH
        ):
            # NRGF flattens the corona's radial fall-off on plain frames only;
            # difference modes already remove the static background.
            current = self._nrgf_filter_frame(frame, current)
            title += " (NRGF)"

        # Shared with the movie exporter so the preview and the exported video
        # use identical scaling.
        display_data = apply_display_scale(current, self.scale_combo.currentText())

        finite = display_data[np.isfinite(display_data)]
        vmin = None
        vmax = None
        if finite.size > 0:
            lo = min(float(self.clip_low_slider.value()), float(self.clip_high_slider.value()) - 0.1)
            hi = max(float(self.clip_high_slider.value()), lo + 0.1)
            vmin = float(np.nanpercentile(finite, lo))
            vmax = float(np.nanpercentile(finite, hi))
            if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
                vmin = None
                vmax = None

        self._current_map_data = current
        self._current_frame_index = idx
        self._current_axis_transform = self._axis_transform_for_arcsec(frame=frame, data_shape=current.shape)
        canvas = self._active_canvas()
        canvas.set_colorbar_visible(self.colorbar_check.isChecked())
        canvas.set_colormap_name(self._resolved_colormap_name())
        canvas.plot_map_data(display_data, title=title, vmin=vmin, vmax=vmax, axis_transform=self._current_axis_transform)
        self.frame_label.setText(f"Frame {idx + 1} / {len(self._map_frames)}")
        self.plot_title_label.setText(title)
        self._refresh_limb_overlay()
        self._refresh_region_overlays()
        self._refresh_graticule_overlay()
        self._refresh_vector_overlay()
        if hasattr(self, "_measure"):
            self._measure.on_frame_changed()

    def _nrgf_filter_frame(self, frame: Any, current: np.ndarray) -> np.ndarray:
        """NRGF-filter a coronagraph frame; fall back to the raw array on error."""
        try:
            from src.Backend.coronagraph import nrgf, solar_center_from_meta

            center = solar_center_from_meta(getattr(frame, "meta", None), data_shape=current.shape)
            filtered = nrgf(current, center)
            if np.isfinite(filtered).any():
                return filtered
        except Exception:
            pass
        return current

    def _movie_mode(self) -> str:
        text = self.movie_content_combo.currentText().lower()
        if "running" in text:
            return "running"
        if "base" in text:
            return "base"
        return "raw"

    def _resolved_colormap_name(self) -> str:
        text = self.colormap_combo.currentText().strip()
        return text or self._default_aia_colormap_name()

    def _apply_observable_download_gating(self) -> None:
        """Enable the SDO-only download controls only for SDO observables.

        SOHO/LASCO is VSO-only and full-disk only, with no JSOC fast path, no
        server-side binning/cutout and no reduced-resolution product, so the
        Download Source, JSOC e-mail, Frame Size, cutout and high-resolution
        controls are greyed out (and Frame Size forced to Full disk) when a
        LASCO observable is selected. Re-evaluated whenever the observable
        changes or the busy state toggles.
        """
        if not hasattr(self, "source_combo"):
            return
        instrument = self._current_observable()[0]
        is_sdo = instrument in ("AIA", "HMI")
        is_aia = instrument == "AIA"
        is_lasco = instrument == "LASCO"
        busy = bool(getattr(self, "_busy", False))
        for widget in (self.source_combo, self.jsoc_email_edit, self.frame_size_combo, self.cutout_widget):
            widget.setEnabled(is_sdo and not busy)
        # Helioviewer near-real-time preview only applies to SOHO/LASCO.
        if hasattr(self, "live_preview_btn"):
            self.live_preview_btn.setEnabled(is_lasco)
        if hasattr(self, "live_preview_action"):
            self.live_preview_action.setEnabled(is_lasco)
        # High-resolution VSO is an AIA-only product (HMI/LASCO have none).
        self.high_resolution_check.setEnabled(is_aia and not busy)
        if not is_aia:
            self.high_resolution_check.setChecked(False)
        if not is_sdo:
            # Force full-disk VSO so the "needs JSOC e-mail" prompt path (binned
            # or cutout frame sizes) can never trigger for LASCO.
            idx = self.frame_size_combo.findData(SIZE_FULL)
            if idx >= 0 and self.frame_size_combo.currentIndex() != idx:
                was = self.frame_size_combo.blockSignals(True)
                self.frame_size_combo.setCurrentIndex(idx)
                self.frame_size_combo.blockSignals(was)
            self.cutout_widget.setVisible(False)

    def _apply_instrument_visibility(self) -> None:
        """Show only the tool groups that apply to the effective instrument.

        The sidebar serves six missions; showing every group at once buries the
        relevant tools. Loaded data wins over the selected observable so the
        panel matches what is actually on screen.
        """
        if not hasattr(self, "coronagraph_group"):
            return
        cls = self._effective_instrument_class()
        self.vector_group.setVisible(cls == MAGNETOGRAPH)
        self.region_group.setVisible(cls in (DISK_EUV, MAGNETOGRAPH, UNKNOWN))
        self.coronagraph_group.setVisible(cls == CORONAGRAPH)
        self.hi_group.setVisible(cls == HELIOSPHERIC)
        # Disk-only mode buttons vanish for coronagraph/heliospheric work
        # (they are also disabled — see _set_loaded_state — so tests that only
        # check enabled state keep passing). Never re-show a widget inside a
        # collapsed accordion group.
        mode_expanded = not self.mode_group.isCheckable() or self.mode_group.isChecked()
        disk_visible = cls in (DISK_EUV, MAGNETOGRAPH, UNKNOWN) and mode_expanded
        for widget in (
            self.composite_btn,
            self.magnetogram_btn,
            self.mag_threshold_spin,
            self.detect_regions_btn,
            self.fetch_labels_btn,
        ):
            widget.setVisible(disk_visible)
        # Helioviewer quicklook only exists for SOHO/LASCO.
        source_expanded = (
            not self.data_source_group.isCheckable() or self.data_source_group.isChecked()
        )
        is_lasco_observable = self._current_observable()[0] == "LASCO"
        self.live_preview_btn.setVisible(is_lasco_observable and source_expanded)

    def _on_canvas_hover(self, x_arcsec: float | None, y_arcsec: float | None) -> None:
        """Live solar coordinate readout as the cursor moves over the map.

        Shows the point's heliographic longitude/latitude in the selected frame
        (HCI by default) when it lands on the disk, alongside the distance from
        Sun centre in solar radii, the position angle (N→E) and the pixel
        position. The canvas plots in helioprojective view coordinates where disk
        centre is (0, 0), so radius and PA follow directly from the hover
        position; lon/lat comes from projecting the point onto the solar surface.
        """
        if x_arcsec is None or y_arcsec is None or self._current_map_data is None:
            self.coord_readout_label.setText("")
            return
        try:
            x_pix, y_pix = self.pyqt_canvas.map_pixel_from_arcsec(float(x_arcsec), float(y_arcsec))
        except Exception:
            self.coord_readout_label.setText("")
            return
        ny, nx = self._current_map_data.shape[:2]
        if not (0 <= x_pix < nx and 0 <= y_pix < ny):
            self.coord_readout_label.setText("")
            return
        frame = (
            self._map_frames[max(0, min(self._current_frame_index, len(self._map_frames) - 1))]
            if self._map_frames
            else None
        )
        rsun = self._solar_radius_arcsec(frame) if frame is not None else 960.0
        r_rsun = float(np.hypot(x_arcsec, y_arcsec)) / rsun if rsun > 0 else float("nan")
        pa_deg = ruler_measurement((0.0, 0.0), (x_arcsec, y_arcsec)).position_angle_deg
        lonlat_text = self._hover_lonlat_text(frame, float(x_arcsec), float(y_arcsec), int(x_pix), int(y_pix))
        self.coord_readout_label.setText(
            f"{lonlat_text}r = {r_rsun:.2f} R☉  ·  PA {pa_deg:.1f}°  ·  x={int(x_pix)} y={int(y_pix)} px"
        )

    def _hover_lonlat_text(self, frame: Any, x_arcsec: float, y_arcsec: float, x_pix: int, y_pix: int) -> str:
        """Selected-frame lon/lat prefix for the hover readout ('' when off-disk).

        The heliographic transform is comparatively expensive, so its result is
        cached per (frame, frame key, integer pixel) — the readout only recomputes
        when the cursor actually crosses into a new pixel.
        """
        if frame is None:
            return ""
        frame_key = self._grid_frame_key()
        cache_key = (id(frame), frame_key, x_pix, y_pix)
        if getattr(self, "_hover_lonlat_key", None) == cache_key:
            return self._hover_lonlat_value
        try:
            lonlat = point_lonlat(x_arcsec, y_arcsec, frame, frame_key=frame_key)
        except Exception:
            lonlat = None
        if lonlat is None:
            text = ""
        else:
            lon, lat = lonlat
            text = f"{SOLAR_FRAME_LABELS.get(frame_key, frame_key)} lon={lon:.1f}° lat={lat:.1f}°  ·  "
        self._hover_lonlat_key = cache_key
        self._hover_lonlat_value = text
        return text

    def _on_measure_tool_toggled(self, mode: str, on: bool) -> None:
        """Keep the checkable measurement tools mutually exclusive (and exclusive
        with the crop-ROI drag, which also consumes canvas mouse input)."""
        buttons = {
            "ruler": self.ruler_tool_btn,
            "profile": self.profile_tool_btn,
            "height_time": self.height_time_btn,
        }
        if not on:
            if self._measure.mode == mode:
                self._measure.set_mode(None)
            if mode == "height_time":
                self._sync_tracking_panel_visibility()
            return
        for other_mode, btn in buttons.items():
            if other_mode != mode and btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        if self.crop_check.isChecked():
            self._set_crop_mode_checked(False)
        self._measure.set_mode(mode)
        self._sync_tracking_panel_visibility()
        hints = {
            "ruler": "Ruler: click the first point on the image.",
            "profile": "Profile: click the start of the cut.",
            "height_time": (
                "Tracking: click the CME leading edge — the frame auto-advances and "
                "each pick lands in the table."
            ),
        }
        self.statusBar().showMessage(hints[mode], 8000)

    def _sync_tracking_panel_visibility(self) -> None:
        """The tracking panel is a permanent part of the layout now; its
        availability is gated by the Measurements switch, not by whether a
        tracking mode is active or picks exist."""
        self.tracking_panel.setVisible(True)

    def _on_measurements_toggled(self, checked: bool) -> None:
        """Gate the measurement tools and the CME tracking panel behind one
        switch. The panel stays in the layout but is unavailable until this is
        ticked."""
        checked = bool(checked)
        self.tracking_panel.setEnabled(checked)
        if not checked:
            # Leaving measurement mode: drop any active tool so stray canvas
            # clicks stop landing as picks/overlays.
            for btn in (self.ruler_tool_btn, self.profile_tool_btn, self.height_time_btn):
                if btn.isChecked():
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
            if hasattr(self, "_measure"):
                self._measure.set_mode(None)
        # Re-apply the enabled state of every tool button for the new switch.
        self._set_loaded_state(bool(self._map_frames))

    def _on_pan_zoom_toggled(self, checked: bool) -> None:
        """Enable interactive pan/zoom on the loaded image. The zoom persists
        across frames so a movie can play zoomed-in."""
        self.pyqt_canvas.set_pan_zoom_enabled(bool(checked))

    def _on_details_toggled(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self.analysis_text.setVisible(expanded)
        self.details_toggle_btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        if expanded:
            # Free the drawer and give it back a sensible share of the height.
            self.details_container.setMaximumHeight(16777215)
            total = max(1, sum(self.content_splitter.sizes()))
            self.content_splitter.setSizes([max(total - 110, 1), 110])
        else:
            # Pin the drawer to its header so the canvas gets all the space.
            self.details_container.setMaximumHeight(self.details_toggle_btn.sizeHint().height() + 4)

    def clear_all_measurements(self) -> None:
        """Clear and reset every measurement (toolbar Clear button)."""
        for btn in (self.ruler_tool_btn, self.profile_tool_btn, self.height_time_btn):
            if btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._measure.set_mode(None)
        self._measure.clear_all()
        self._sync_tracking_panel_visibility()

    def _sync_nrgf_enabled(self) -> None:
        """NRGF applies to plain frames only — differences already remove the
        radial background, so the toggle greys out in difference modes."""
        is_raw = self._movie_mode() == "raw"
        is_coronagraph = self._effective_instrument_class() == CORONAGRAPH
        self.nrgf_check.setEnabled(bool(self._map_frames) and is_raw and is_coronagraph)
        if not is_raw and self.nrgf_check.isChecked():
            self.nrgf_check.blockSignals(True)
            self.nrgf_check.setChecked(False)
            self.nrgf_check.blockSignals(False)
            self._render_current_frame()

    def build_hi_jmap(self) -> None:
        """Background-subtract the HI sequence and show its time–elongation J-map."""
        from src.Backend.hi_jmap import build_jmap, subtract_background
        from src.Backend.coronagraph import solar_center_from_meta
        from src.UI.solar_measure_tools import JMapDialog

        if len(self._map_frames) < 2:
            QMessageBox.information(self, "HI J-map", "Load at least two HI frames first.")
            return
        try:
            arrays = [np.asarray(getattr(f, "data"), dtype=float) for f in self._map_frames]
            method = str(self.hi_background_combo.currentData() or "median")
            subtracted = subtract_background(arrays, method=method)
            frame0 = self._map_frames[0]
            try:
                center = solar_center_from_meta(getattr(frame0, "meta", None), data_shape=arrays[0].shape)
            except Exception:
                ny, nx = arrays[0].shape
                center = ((nx - 1) / 2.0, (ny - 1) / 2.0)
            jmap = build_jmap(subtracted, center, float(self.hi_pa_spin.value()), half_width=1)
            scale = abs(float(self._current_axis_transform.get("x_scale_arcsec_per_pix", 1.0))) or 1.0
            radii_arcsec = jmap.radii_pixels * scale
        except Exception as exc:
            QMessageBox.critical(self, "HI J-map", f"Could not build the J-map:\n{exc}")
            return
        title = f"{self._frames_word()}  ·  PA {int(self.hi_pa_spin.value())}°  ·  {method} background"
        dialog = JMapDialog(jmap.image, radii_arcsec, title=title, parent=self)
        self._jmap_dialog = dialog
        dialog.show()
        self.statusBar().showMessage("J-map built from the loaded HI sequence.", 6000)

    def open_multiview_dialog(self) -> None:
        """Compare the loaded view against a second observable's viewpoint."""
        if not self._map_frames:
            QMessageBox.information(self, "Compare Viewpoint", "Load frames first.")
            return
        from src.UI.multiview_dialog import MultiViewpointDialog

        # Pass the original loader outputs: crop/composite replace _map_frames
        # with derived wrappers that cannot be reprojected (no reproject_to).
        reference = self._original_frames or self._map_frames
        index = max(0, min(self._current_frame_index, len(reference) - 1))
        dialog = MultiViewpointDialog(
            self,
            reference_frames=reference,
            reference_index=index,
            reference_label=self._frames_word(),
            cache_dir=self.cache_dir,
            jsoc_email=str(self.jsoc_email_edit.text() or "").strip(),
            theme=self.theme,
        )
        self._multiview_dialog = dialog
        dialog.show()

    def _on_query_wavelength_changed(self, _index: int) -> None:
        # High-resolution VSO / JSOC / composite only apply to SDO; gate the
        # SDO-only download controls for HMI and SOHO/LASCO observables, and
        # adapt the visible tool groups to the selected instrument.
        self._apply_observable_download_gating()
        self._apply_instrument_visibility()
        if self._map_frames:
            return
        self._select_default_colormap_for_wavelength()
        self._render_current_frame()

    def _select_default_colormap_for_wavelength(self, frame: Any | None = None) -> None:
        name = self._default_aia_colormap_name(frame)
        index = self.colormap_combo.findText(name)
        if index < 0:
            return
        was_blocked = self.colormap_combo.blockSignals(True)
        self.colormap_combo.setCurrentIndex(index)
        self.colormap_combo.blockSignals(was_blocked)
        for canvas in self._all_plot_canvases():
            canvas.set_colormap_name(name)

    def _frame_lasco_detector(self, frame: Any | None = None) -> str | None:
        """If the frame (or the selected observable) is SOHO/LASCO, return its
        detector ('C2'/'C3'), else None."""
        source = frame
        if source is None and self._map_frames:
            source = self._map_frames[max(0, min(self._current_frame_index, len(self._map_frames) - 1))]
        if source is not None:
            instrument = str(getattr(source, "instrument", "") or "").upper()
            if "LASCO" in instrument:
                detector = str(getattr(source, "detector", "") or "").strip().upper()
                return detector if detector in LASCO_COLORMAPS else "C2"
            # An explicit non-LASCO frame must not fall back to the combo.
            if frame is not None:
                return None
        if frame is None:
            instrument, value = self._current_observable()
            if instrument == "LASCO":
                detector = str(value or "").strip().upper()
                return detector if detector in LASCO_COLORMAPS else "C2"
        return None

    def _frame_hmi_product(self, frame: Any | None = None) -> str | None:
        """If the frame (or the selected observable) is HMI, return its product."""
        source = frame
        if source is None and self._map_frames:
            source = self._map_frames[max(0, min(self._current_frame_index, len(self._map_frames) - 1))]
        if source is not None:
            instrument = str(getattr(source, "instrument", "") or "").upper()
            meta = getattr(source, "meta", {}) or {}
            content = str((meta.get("content") if isinstance(meta, dict) else "") or "").strip().lower()
            if content in HMI_PRODUCT_CONTENT:
                return HMI_PRODUCT_CONTENT[content]
            if "HMI" in instrument:
                return "magnetogram"
            if source is not None and frame is not None:
                return None
        if frame is None:
            instrument, value = self._current_observable()
            if instrument == "HMI":
                return str(value)
        return None

    def _frame_stereo_suvi_colormap(self, frame: Any | None = None) -> str | None:
        """Colormap for a STEREO/SECCHI or GOES/SUVI frame (or selected observable)."""
        source = frame
        if source is None and self._map_frames:
            source = self._map_frames[max(0, min(self._current_frame_index, len(self._map_frames) - 1))]
        if source is not None:
            instrument = str(getattr(source, "instrument", "") or "").upper()
            detector = str(getattr(source, "detector", "") or "").strip().upper()
            if "SUVI" in instrument:
                return _suvi_colormap_name(self._frame_wavelength_value(source))
            if "SECCHI" in instrument or detector in ("COR1", "COR2", "EUVI", "HI1", "HI2"):
                return _secchi_colormap_name(detector, self._frame_wavelength_value(source))
            # An explicit non-STEREO/SUVI frame must not fall back to the combo.
            if frame is not None:
                return None
        if frame is None:
            instrument, value = self._current_observable()
            if instrument == "SUVI":
                return _suvi_colormap_name(value)
            if instrument == "SECCHI":
                _spacecraft, det, wavelength = value
                return _secchi_colormap_name(det, wavelength)
        return None

    def _default_aia_colormap_name(self, frame: Any | None = None) -> str:
        detector = self._frame_lasco_detector(frame)
        if detector is not None:
            return LASCO_COLORMAPS.get(detector, "soholasco2")
        stereo_suvi = self._frame_stereo_suvi_colormap(frame)
        if stereo_suvi is not None:
            return stereo_suvi
        product = self._frame_hmi_product(frame)
        if product is not None:
            return HMI_COLORMAPS.get(product, "gray")
        wavelength = self._frame_wavelength_value(frame)
        if wavelength is None:
            instrument, value = self._current_observable()
            if instrument == "AIA":
                try:
                    wavelength = float(value)
                except Exception:
                    wavelength = None
        if wavelength is None:
            return "sdoaia193"
        rounded = int(round(float(wavelength)))
        if rounded in AIA_WAVELENGTHS:
            return f"sdoaia{rounded}"
        return "sdoaia193"

    def _frame_wavelength_value(self, frame: Any | None = None) -> float | None:
        source = frame
        if source is None and self._map_frames:
            source = self._map_frames[max(0, min(self._current_frame_index, len(self._map_frames) - 1))]
        if source is None:
            return None
        candidates = [
            getattr(source, "wavelength", None),
            getattr(source, "meta", {}).get("wavelnth") if isinstance(getattr(source, "meta", None), dict) else None,
            getattr(source, "meta", {}).get("wave_len") if isinstance(getattr(source, "meta", None), dict) else None,
        ]
        for value in candidates:
            parsed = self._parse_wavelength_value(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _parse_wavelength_value(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            pass
        try:
            numeric = getattr(value, "value", None)
            if numeric is not None:
                return float(numeric)
        except Exception:
            pass
        match = re.search(r"(\d+(?:\.\d+)?)", str(value))
        if not match:
            return None
        try:
            return float(match.group(1))
        except Exception:
            return None

    def _on_colormap_changed(self, _text: str) -> None:
        self._render_current_frame()

    def _on_grid_toggled(self, checked: bool) -> None:
        # Keep the plain rectilinear grid off on both canvases (it is replaced by
        # the solar-coordinate graticule) and (re)draw the graticule itself.
        for canvas in self._all_plot_canvases():
            if hasattr(canvas, "set_grid_visible"):
                canvas.set_grid_visible(False)
        # The frame combo also selects the frame for the live hover readout, so it
        # stays enabled even when the graticule itself is hidden.
        self._refresh_graticule_overlay()

    def _on_colorbar_toggled(self, checked: bool) -> None:
        for canvas in self._all_plot_canvases():
            canvas.set_colorbar_visible(bool(checked))

    def _on_crop_toggled(self, checked: bool) -> None:
        if not checked:
            self.pyqt_canvas.disable_roi_selector()
            return
        if self._current_map_data is None:
            self._set_crop_mode_checked(False)
            return
        # Crop-ROI dragging and click-measurement modes both consume canvas
        # mouse input — turning one on turns the other off.
        if hasattr(self, "_measure") and self._measure.mode is not None:
            for btn in (self.ruler_tool_btn, self.profile_tool_btn, self.height_time_btn):
                if btn.isChecked():
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
            self._measure.set_mode(None)
        if self._active_canvas() is not self.pyqt_canvas:
            self.renderer_combo.setCurrentText("PyQtGraph")
        self.pyqt_canvas.enable_roi_selector()

    def _set_crop_mode_checked(self, checked: bool) -> None:
        was_blocked = self.crop_check.blockSignals(True)
        self.crop_check.setChecked(bool(checked))
        self.crop_check.blockSignals(was_blocked)
        if not checked:
            self.pyqt_canvas.disable_roi_selector()

    def _on_crop_roi_selected(self, bounds: tuple[int, int, int, int] | None) -> None:
        if bounds is None or self._current_map_data is None:
            return
        self._set_crop_fields_from_pixel_bounds(bounds)
        x0, x1, y0, y1 = bounds
        self.statusBar().showMessage(f"Crop rectangle: x=[{x0},{x1}], y=[{y0},{y1}] px", 4000)

    def _set_crop_fields_from_pixel_bounds(self, bounds: tuple[int, int, int, int]) -> None:
        x0, x1, y0, y1 = self._axis_bounds_from_pixel_bounds(bounds)
        spins = (self.crop_x0_spin, self.crop_x1_spin, self.crop_y0_spin, self.crop_y1_spin)
        old_blocks = [spin.blockSignals(True) for spin in spins]
        try:
            self.crop_x0_spin.setValue(float(x0))
            self.crop_x1_spin.setValue(float(x1))
            self.crop_y0_spin.setValue(float(y0))
            self.crop_y1_spin.setValue(float(y1))
        finally:
            for spin, blocked in zip(spins, old_blocks):
                spin.blockSignals(blocked)

    def _axis_bounds_from_pixel_bounds(self, bounds: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
        x_low, x_high, y_low, y_high = [float(v) for v in bounds]
        tx = self._current_axis_transform
        x_ref_pix = float(tx.get("x_ref_pix", 0.0))
        y_ref_pix = float(tx.get("y_ref_pix", 0.0))
        x_scale = float(tx.get("x_scale_arcsec_per_pix", 1.0)) or 1.0
        y_scale = float(tx.get("y_scale_arcsec_per_pix", 1.0)) or 1.0
        x_ref_arcsec = float(tx.get("x_ref_arcsec", 0.0))
        y_ref_arcsec = float(tx.get("y_ref_arcsec", 0.0))
        x0 = x_ref_arcsec + (x_low - x_ref_pix) * x_scale
        x1 = x_ref_arcsec + (x_high - x_ref_pix) * x_scale
        y0 = y_ref_arcsec + (y_low - y_ref_pix) * y_scale
        y1 = y_ref_arcsec + (y_high - y_ref_pix) * y_scale
        x_min, x_max = sorted((float(x0), float(x1)))
        y_min, y_max = sorted((float(y0), float(y1)))
        return x_min, x_max, y_min, y_max

    def _sync_crop_fields_to_view(self) -> None:
        rect = self._active_canvas().map_view_rect()
        x0, y0, w, h = rect
        self.crop_x0_spin.setValue(float(x0))
        self.crop_x1_spin.setValue(float(x0 + w))
        self.crop_y0_spin.setValue(float(y0))
        self.crop_y1_spin.setValue(float(y0 + h))

    def _crop_bounds_from_axis_fields(self, shape: tuple[int, ...]) -> CropBounds:
        x0_arc = float(self.crop_x0_spin.value())
        x1_arc = float(self.crop_x1_spin.value())
        y0_arc = float(self.crop_y0_spin.value())
        y1_arc = float(self.crop_y1_spin.value())
        x0 = self._axis_x_to_pixel(x0_arc)
        x1 = self._axis_x_to_pixel(x1_arc)
        y0 = self._axis_y_to_pixel(y0_arc)
        y1 = self._axis_y_to_pixel(y1_arc)
        nx = int(shape[1])
        ny = int(shape[0])
        x_low, x_high = sorted((int(np.floor(x0)), int(np.ceil(x1))))
        y_low, y_high = sorted((int(np.floor(y0)), int(np.ceil(y1))))
        x_low = max(0, min(nx, x_low))
        x_high = max(0, min(nx, x_high))
        y_low = max(0, min(ny, y_low))
        y_high = max(0, min(ny, y_high))
        if x_high <= x_low or y_high <= y_low:
            raise ValueError("Crop region does not overlap the current image.")
        return (x_low, x_high, y_low, y_high)

    def _axis_x_to_pixel(self, x_arcsec: float) -> float:
        tx = self._current_axis_transform
        scale = float(tx.get("x_scale_arcsec_per_pix", 1.0)) or 1.0
        return (float(x_arcsec) - float(tx.get("x_ref_arcsec", 0.0))) / scale + float(tx.get("x_ref_pix", 0.0))

    def _axis_y_to_pixel(self, y_arcsec: float) -> float:
        tx = self._current_axis_transform
        scale = float(tx.get("y_scale_arcsec_per_pix", 1.0)) or 1.0
        return (float(y_arcsec) - float(tx.get("y_ref_arcsec", 0.0))) / scale + float(tx.get("y_ref_pix", 0.0))

    def apply_axis_crop(self):
        if not self._map_frames or self._current_map_data is None:
            QMessageBox.information(
                self,
                "Apply Crop",
                "Load or upload solar frames first, then enter the X/Y arcsec bounds to crop.",
            )
            return
        try:
            bounds = self._crop_bounds_from_axis_fields(self._current_map_data.shape)
            self._map_frames = crop_maps(self._map_frames, bounds)
            self._regions = []
            self.region_table.setRowCount(0)
            self._current_frame_index = 0
            self.frame_slider.blockSignals(True)
            self.frame_slider.setRange(0, max(0, len(self._map_frames) - 1))
            self.frame_slider.setValue(0)
            self.frame_slider.blockSignals(False)
            # Cropping changes the image extent, so force every canvas to snap
            # its view + axes to the new (smaller) region instead of keeping the
            # previous full-disk range.
            self._reset_canvas_views()
            self._render_current_frame()
            self._set_crop_mode_checked(False)
            # Remember the applied crop so a saved session can reproduce it.
            self._crop_applied = True
            self._applied_crop_arcsec = [
                float(self.crop_x0_spin.value()),
                float(self.crop_x1_spin.value()),
                float(self.crop_y0_spin.value()),
                float(self.crop_y1_spin.value()),
            ]
            self.analysis_text.setPlainText(
                f"Applied crop x=[{bounds[0]},{bounds[1]}], y=[{bounds[2]},{bounds[3]}].\n"
                + self._frame_resolution_status(self._map_frames)
            )
        except Exception as exc:
            QMessageBox.critical(self, "Apply Crop", str(exc))

    def _reset_canvas_views(self) -> None:
        for canvas in self._all_plot_canvases():
            reset = getattr(canvas, "reset_map_view", None)
            if callable(reset):
                try:
                    reset()
                except Exception:
                    pass

    def reset_loaded_frames(self):
        if not self._original_frames:
            return
        self._map_frames = list(self._original_frames)
        self._crop_applied = False
        self._applied_crop_arcsec = None
        self._regions = []
        self.region_table.setRowCount(0)
        self._current_frame_index = 0
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, max(0, len(self._map_frames) - 1))
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)
        self._set_crop_mode_checked(False)
        self._reset_canvas_views()
        self._render_current_frame()
        self.analysis_text.setPlainText(self._loaded_frame_status_text("Restored", self._map_frames))
        self._update_load_summary()
        self._apply_instrument_visibility()

    def reset_all(self):
        """Return the tool to a clean slate: clear data + UI defaults + cache."""
        if self._busy or self.is_operation_running():
            QMessageBox.information(
                self, "Reset All", "Wait for the current operation to finish before resetting."
            )
            return
        reply = QMessageBox.question(
            self,
            "Reset All",
            "Reset Solar Image Analysis to defaults and delete the download cache?\n\n"
            f"Cache folder:\n{self.cache_dir}\n\n"
            "Loaded frames, search results and active regions will be cleared. "
            "Your saved JSOC e-mail is kept.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # 1) Clear loaded / analysis state.
        self._search_result = None
        self._loaded_paths = []
        self._original_frames = []
        self._map_frames = []
        self._loaded_config_key = None
        self._exposure_varies = False
        self._map_metadata = {}
        self._regions = []
        self._metadata_regions = []
        self._current_frame_index = 0
        self._current_map_data = None
        self._current_axis_transform = self._default_axis_transform()
        self._save_target_dir = None
        self._overlay_magnetogram = None
        self._vector_frames = []
        self._vector_geometry_cache = {}
        self.results_table.setRowCount(0)
        self.region_table.setRowCount(0)
        self.archive_results_status_label.setText("Run Fetch to list matching archive files.")
        self.metadata_status_label.setText("Metadata: not loaded")
        self.download_load_btn.setEnabled(False)
        self._set_results_selection_controls_enabled(False)

        # 2) Reset query + display controls to their construction defaults.
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        self.wavelength_combo.setCurrentText("AIA 193 A")
        self.start_dt_edit.setDateTime(QDateTime(now - timedelta(hours=2)))
        self.end_dt_edit.setDateTime(QDateTime(now))
        self.sample_seconds_spin.setValue(120)
        self.max_records_spin.setValue(120)
        self.high_resolution_check.setChecked(False)
        idx = self.source_combo.findData("auto")
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)
        idx = self.frame_size_combo.findData(SIZE_FULL)
        if idx >= 0:
            self.frame_size_combo.setCurrentIndex(idx)
        self.cutout_x_spin.setValue(0.0)
        self.cutout_y_spin.setValue(0.0)
        self.cutout_w_spin.setValue(500.0)
        self.cutout_h_spin.setValue(500.0)
        self.clip_low_slider.setValue(1.0)
        self.clip_high_slider.setValue(99.9)
        self.colormap_combo.setCurrentText("sdoaia193")
        self.scale_combo.setCurrentText("linear")
        self.renderer_combo.setCurrentText("PyQtGraph")
        self.solar_limb_check.setChecked(False)
        self.grid_check.setChecked(True)
        self.colorbar_check.setChecked(True)
        self.region_overlay_check.setChecked(True)
        self.crop_x0_spin.setValue(-1100.0)
        self.crop_x1_spin.setValue(1100.0)
        self.crop_y0_spin.setValue(-1100.0)
        self.crop_y1_spin.setValue(1100.0)
        self._set_crop_mode_checked(False)
        self.vector_show_check.setChecked(False)
        self.vector_arrows_check.setChecked(True)
        self.vector_stream_check.setChecked(False)
        self.vector_mag_check.setChecked(False)
        self.vector_spacing_spin.setValue(64)
        self.vector_threshold_spin.setValue(200)
        self.vector_status_label.setText("No vector field data loaded.")

        # 3) Clear the plot + analysis panels.
        self._set_loaded_state(False)
        for canvas in self._all_plot_canvases():
            try:
                canvas.clear_plot()
                canvas.set_map_title("No image data loaded.")
            except Exception:
                pass
        self.plot_title_label.setText("No image data loaded.")
        self.analysis_text.clear()
        self._update_load_summary()
        self._apply_instrument_visibility()
        self._update_size_estimate()

        # 4) Delete the download cache.
        removed = self._clear_cache_folder()
        self.statusBar().showMessage(f"Reset complete. Cache cleared ({removed}).", 6000)

    def _clear_cache_folder(self) -> str:
        cache_path = Path(self.cache_dir)
        skipped = 0
        try:
            for child in cache_path.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except Exception:
                    skipped += 1
        except FileNotFoundError:
            pass
        except Exception:
            pass
        try:
            cache_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return "some items in use were skipped" if skipped else f"{cache_path}"

    def detect_active_regions(self):
        if self._current_map_data is None:
            QMessageBox.information(self, "Active Regions", "Load or render a solar disk frame first.")
            return
        try:
            regions = detect_active_regions(
                self._current_map_data,
                threshold_percentile=float(self.threshold_spin.value()),
                min_area_px=int(self.min_area_spin.value()),
                max_regions=25,
                axis_transform=self._current_axis_transform,
            )
            if self._metadata_regions:
                regions = label_regions_with_metadata(regions, self._metadata_regions)
            self._regions = list(regions)
            self._populate_region_table()
            self._refresh_region_overlays()
            self.analysis_text.setPlainText(f"Detected {len(self._regions)} active-region candidate(s) in the current frame.")
            self.export_regions_btn.setEnabled(bool(self._regions))
            self._sync_menu_action_state(loaded=bool(self._map_frames))
        except Exception as exc:
            QMessageBox.critical(self, "Active Regions", str(exc))

    def fetch_active_region_labels(self):
        try:
            spec = self._build_query_spec()
        except Exception as exc:
            QMessageBox.warning(self, "NOAA/HEK Labels", str(exc))
            return
        self._set_busy(True, "Fetching active-region metadata...")
        self._start_worker(SolarMetadataWorker(spec.start_dt, spec.end_dt))

    def _populate_region_table(self):
        self.region_table.setRowCount(len(self._regions))
        for row, region in enumerate(self._regions):
            values = [
                str(region.region_id),
                region.label,
                region.noaa_number,
                f"({region.centroid_x_arcsec:.1f}, {region.centroid_y_arcsec:.1f})",
                str(region.area_px),
                f"{region.peak:.6g}",
                region.metadata_source,
            ]
            for col, value in enumerate(values):
                self.region_table.setItem(row, col, QTableWidgetItem(value))

    def _refresh_region_overlays(self) -> None:
        self._active_canvas().set_region_overlays(self._regions, visible=self.region_overlay_check.isChecked())

    def _refresh_limb_overlay(self) -> None:
        if not self.solar_limb_check.isChecked() or not self._map_frames:
            self._active_canvas().set_aia_limb_overlay(None, None, visible=False)
            return
        frame = self._map_frames[self._current_frame_index]
        radius = self._solar_radius_arcsec(frame)
        theta = np.linspace(0.0, 2.0 * np.pi, 720)
        self._active_canvas().set_aia_limb_overlay(radius * np.cos(theta), radius * np.sin(theta), visible=True)

    def _grid_frame_key(self) -> str:
        return solar_frame_key_from_display(self.grid_frame_combo.currentText())

    def _refresh_graticule_overlay(self) -> None:
        """Draw the solar-coordinate graticule for the current frame and frame key.

        Degrades silently (hides the graticule) for frames without a usable solar
        coordinate system, mirroring how the limb overlay handles non-solar data.
        """
        canvas = self._active_canvas()
        if not hasattr(canvas, "set_solar_graticule"):
            return
        if not self.grid_check.isChecked() or not self._map_frames:
            canvas.set_solar_graticule(None, None, visible=False)
            return
        frame = self._map_frames[self._current_frame_index]
        frame_key = self._grid_frame_key()
        # Cache per (frame, frame key) so stepping/playing a sequence with the grid
        # on does not recompute the ~dozens of coordinate transforms each render.
        cache = getattr(self, "_graticule_cache", None)
        if cache is None:
            cache = self._graticule_cache = {}
        cache_key = (id(frame), frame_key)
        if cache_key in cache:
            polylines, labels = cache[cache_key]
        else:
            try:
                polylines, labels = graticule_arcsec(frame, frame_key=frame_key)
            except Exception:
                polylines, labels = [], []
            if len(cache) > 128:
                cache.clear()
            cache[cache_key] = (polylines, labels)
        canvas.set_solar_graticule(polylines, labels, visible=bool(polylines))

    def _solar_radius_arcsec(self, frame: Any) -> float:
        for attr in ("rsun_obs", "rsun_arcseconds"):
            value = getattr(frame, attr, None)
            radius = self._as_float(value, unit_hint="arcsec")
            if radius is not None and np.isfinite(radius) and radius > 0:
                return float(radius)
        meta = getattr(frame, "meta", {}) or {}
        for key in ("rsun_obs", "RSUN_OBS"):
            if key in meta:
                radius = self._as_float(meta.get(key), unit_hint="arcsec")
                if radius is not None and np.isfinite(radius) and radius > 0:
                    return float(radius)
        return 960.0

    def load_magnetogram_overlay(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Load HMI Magnetogram (overlay)",
            "",
            "FITS files (*.fit *.fits *.fit.gz *.fits.gz)",
        )
        if not paths:
            return
        try:
            frame_set = load_aia_maps_streaming([paths[0]])
            self._overlay_magnetogram = frame_set.maps[0]
        except Exception as exc:
            QMessageBox.critical(self, "Magnetogram Overlay", str(exc))
            return
        self.statusBar().showMessage(
            f"Magnetogram overlay loaded: {Path(paths[0]).name}. Click Composite to apply.", 6000
        )
        if self._map_frames:
            self.show_composite_plot()

    def load_vector_field_files(self):
        """Load hmi.B_720s vector segments (field/inclination/azimuth[/disambig])
        from disk and assemble them into overlay time steps."""
        if self.is_operation_running():
            QMessageBox.information(self, "Vector Field", "Another operation is still running.")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Load HMI Vector FITS (hmi.B_720s segments)",
            "",
            "FITS files (*.fit *.fits *.fit.gz *.fits.gz)",
        )
        if not paths:
            return
        self._set_busy(True, "Loading HMI vector field segments...")
        self._start_worker(VectorFieldLoadWorker(list(paths)))

    def download_vector_field(self):
        """Download the measured full-disk vector field (hmi.B_720s) for the
        query time window via the JSOC fast path."""
        if self.is_operation_running():
            QMessageBox.information(self, "Vector Field", "Another operation is still running.")
            return
        email, _prefer = self._jsoc_params()
        if not email:
            QMessageBox.information(
                self,
                "Vector Field",
                "The hmi.B_720s vector field is served by JSOC, which needs a registered "
                "notify e-mail.\nEnter it in the JSOC Notify E-mail field first.\n\n"
                "Register once (free) at https://jsoc.stanford.edu/ajax/register_email.html",
            )
            return
        start_dt = self.start_dt_edit.dateTime().toPython().replace(tzinfo=None)
        end_dt = self.end_dt_edit.dateTime().toPython().replace(tzinfo=None)
        if end_dt <= start_dt:
            QMessageBox.warning(self, "Vector Field", "End time must be after start time.")
            return
        sample = int(self.sample_seconds_spin.value() or 0)
        # The vector pipeline runs at a fixed 720 s cadence; honour a coarser
        # requested sampling but never ask for more than the series provides.
        cadence = max(720, sample if sample > 0 else 720)
        steps = max(1, int((end_dt - start_dt).total_seconds() // cadence) + 1)
        if steps > 6:
            approx_mb = steps * 4 * 13
            reply = QMessageBox.question(
                self,
                "Vector Field",
                f"This window covers about {steps} vector time steps "
                f"(4 segments each, ≈ {approx_mb} MB total).\n\nDownload anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._set_busy(True, "Downloading hmi.B_720s vector field via JSOC...")
        self._start_worker(
            VectorFieldDownloadWorker(
                start_dt=start_dt,
                end_dt=end_dt,
                cadence_seconds=cadence,
                email=email,
                cache_dir=self.cache_dir,
            )
        )

    @Slot(object)
    def _on_vector_frames_loaded(self, frames_obj: object):
        frames = list(frames_obj or [])
        if not frames:
            self._on_worker_failed("The vector field worker returned no usable time steps.")
            return
        self._vector_frames = frames
        self._vector_geometry_cache = {}
        times = sorted(frame.time for frame in frames if frame.time is not None)
        label = f"{len(frames)} vector time step(s) loaded"
        if times:
            label += f" · {times[0]:%Y-%m-%d %H:%M} → {times[-1]:%H:%M} UTC"
        self.vector_status_label.setText(label)
        # The overlay needs an HMI base image in the plot area; when none is
        # loaded, plot the vector field's own Bz magnetogram so the download
        # is visible immediately.
        plotted_base = self._ensure_vector_base_frames(frames)
        if not self.vector_show_check.isChecked():
            self.vector_show_check.setChecked(True)  # toggled() refreshes the overlay
        else:
            self._refresh_vector_overlay()
        extra = (
            "\nPlotted the vertical-field (Bz) magnetogram derived from the vector data "
            "as the base image."
            if plotted_base
            else ""
        )
        self.analysis_text.setPlainText(
            "Loaded the HMI vector magnetic field (hmi.B_720s Milne-Eddington inversion).\n"
            f"{label}.{extra}\n"
            "Arrows show the transverse component (red = vertical field toward the "
            "observer, blue = away); the nearest time step is matched to each displayed "
            "HMI frame automatically."
        )
        self.statusBar().showMessage(label, 6000)

    @Slot(object)
    def _on_vector_no_records(self, latest_obj: object):
        """The requested window has no hmi.B_720s records — offer the newest.

        The definitive vector series lags real time by days to weeks, so a
        'recent' window is routinely empty. When JSOC told us the newest
        record that does exist, offer to move the query window there and
        re-download instead of leaving a dead-end error.
        """
        latest_text = str(latest_obj or "").strip()
        latest_dt = parse_trec_time(latest_text) if latest_text else None
        base = (
            "JSOC has no hmi.B_720s vector magnetic field records in the requested "
            "time window.\nThe definitive vector pipeline lags real time by days to "
            "weeks, so very recent windows are usually empty."
        )
        self.statusBar().showMessage("No hmi.B_720s vector records in the requested window.", 8000)
        self.analysis_text.setPlainText(base)
        if latest_dt is None:
            QMessageBox.information(
                self, "Vector Field", base + "\n\nChoose an earlier time window and try again."
            )
            return
        reply = QMessageBox.question(
            self,
            "Vector Field",
            base
            + f"\n\nNewest available record: {latest_text}\n"
            "Move the query time window there and download the newest vector data?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        span_seconds = (
            self.end_dt_edit.dateTime().toPython() - self.start_dt_edit.dateTime().toPython()
        ).total_seconds()
        span_seconds = min(max(span_seconds, 720.0), 6 * 3600.0)
        self.start_dt_edit.setDateTime(QDateTime(latest_dt - timedelta(seconds=span_seconds)))
        self.end_dt_edit.setDateTime(QDateTime(latest_dt + timedelta(minutes=1)))
        # The failed worker's thread may still be winding down (this handler
        # can run from inside its notification); defer the retry if so.
        if self.is_operation_running():
            self._pending_vector_download = True
        else:
            self.download_vector_field()

    def _ensure_vector_base_frames(self, vframes: list[Any]) -> bool:
        """Guarantee the plot area shows something the overlay can draw on.

        Keeps an already-loaded HMI sequence untouched (the overlay lands on
        it). With nothing loaded, plots Bz frames derived from the vector
        data; with a non-HMI sequence loaded (AIA/LASCO, where the overlay
        cannot be drawn), asks before replacing it. Returns True when the Bz
        base frames were plotted.
        """
        if self._map_frames:
            idx = max(0, min(self._current_frame_index, len(self._map_frames) - 1))
            if self._frame_hmi_product(self._map_frames[idx]) is not None:
                return False
            reply = QMessageBox.question(
                self,
                "Vector Field",
                "The loaded frames are not HMI, so the vector field cannot be overlaid "
                "on them.\n\nPlot the vector field's own vertical-field (Bz) magnetogram "
                "instead? This replaces the currently loaded frames.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return False
        try:
            base_frames = [vector_display_frame(vf) for vf in vframes]
        except Exception as exc:  # noqa: BLE001 - overlay data is still usable
            self.statusBar().showMessage(f"Could not build Bz base frames: {exc}", 6000)
            return False
        paths = [str(vf.source_paths[0]) for vf in vframes if vf.source_paths]
        times = [vf.time for vf in vframes if vf.time is not None]
        metadata = {
            "n_frames": len(base_frames),
            "observatory": "SDO",
            "instrument": "HMI",
            "detector": "",
            "wavelength": "",
            "date": times[0].isoformat() if times else "",
        }
        self._apply_loaded_frames(base_frames, paths=paths, metadata=metadata)
        return True

    def _vector_overlay_options(self) -> VectorOverlayOptions:
        return VectorOverlayOptions(
            show_arrows=self.vector_arrows_check.isChecked(),
            show_streamlines=self.vector_stream_check.isChecked(),
            show_magnitude=self.vector_mag_check.isChecked(),
            grid_step_px=int(self.vector_spacing_spin.value()),
            min_transverse_gauss=float(self.vector_threshold_spin.value()),
        )

    def _refresh_vector_overlay(self) -> None:
        canvas = self._active_canvas()
        setter = getattr(canvas, "set_vector_field_overlay", None)
        if setter is None:
            return
        if not (self.vector_show_check.isChecked() and self._vector_frames and self._map_frames):
            setter(None)
            return
        idx = max(0, min(self._current_frame_index, len(self._map_frames) - 1))
        frame = self._map_frames[idx]
        # HMI-only: the vector data shares HMI's stored (CCD) orientation, so it
        # aligns on HMI products but not on AIA/LASCO frames.
        if self._frame_hmi_product(frame) is None:
            setter(None)
            self.vector_status_label.setText(
                "Vector field overlay applies to HMI frames only — load or select an "
                "HMI observable to see it."
            )
            return
        target_time = frame_observation_time(frame)
        vframe = nearest_vector_frame(self._vector_frames, target_time)
        if vframe is None:
            setter(None)
            return
        options = self._vector_overlay_options()
        if not (options.show_arrows or options.show_streamlines or options.show_magnitude):
            setter(None)
            return
        key = (id(vframe), options)
        geometry = self._vector_geometry_cache.get(key)
        if geometry is None:
            try:
                geometry = build_overlay_geometry(vframe, options)
            except Exception as exc:  # noqa: BLE001 - never take the render loop down
                setter(None)
                self.statusBar().showMessage(f"Vector field overlay failed: {exc}", 6000)
                return
            if len(self._vector_geometry_cache) > 24:
                self._vector_geometry_cache.clear()
            self._vector_geometry_cache[key] = geometry
        setter(geometry)
        status = f"Overlaying {geometry.arrow_count} arrow(s)"
        if geometry.streamline_count:
            status += f", {geometry.streamline_count} streamline(s)"
        if vframe.time is not None and target_time is not None:
            delta = abs((vframe.time - target_time).total_seconds())
            status += f" · Δt to frame {delta:.0f} s"
        self.vector_status_label.setText(status)

    def show_composite_plot(self):
        if not self._map_frames:
            QMessageBox.information(self, "Composite", "Load or upload EUV disk frames first.")
            return
        try:
            if self._overlay_magnetogram is not None:
                base = self._map_frames[max(0, min(self._current_frame_index, len(self._map_frames) - 1))]
                composite = make_magnetogram_composite(
                    base,
                    self._overlay_magnetogram,
                    base_colormap=self._resolved_colormap_name(),
                    base_scale=self.scale_combo.currentText(),
                    base_percentile_low=float(self.clip_low_slider.value()),
                    base_percentile_high=float(self.clip_high_slider.value()),
                    threshold_gauss=float(self.mag_threshold_spin.value()),
                )
                note = (
                    "Composited the current AIA frame with HMI magnetogram polarity contours "
                    f"(red = +, blue = −, ±{int(self.mag_threshold_spin.value())} G)."
                )
            else:
                composite = make_composite(self._map_frames, AiaCompositeSpec(frame_indexes=(0, 1, 2)))
                note = "Rendered an RGB AIA composite from the first three loaded frames."
            self._map_frames = [composite]
            self._current_frame_index = 0
            self.frame_slider.blockSignals(True)
            self.frame_slider.setRange(0, 0)
            self.frame_slider.setValue(0)
            self.frame_slider.blockSignals(False)
            self._reset_canvas_views()
            self._render_current_frame()
            self.analysis_text.setPlainText(note)
        except Exception as exc:
            QMessageBox.critical(self, "Composite Plot", str(exc))

    # ------------------------------------------------------------ sessions
    def _session_extract_dir(self, session_path: str) -> Path:
        """A per-session folder under the cache to unpack embedded frames into."""
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(session_path).stem) or "session"
        return Path(self.cache_dir) / "sessions" / stem

    def _default_session_name(self) -> str:
        label = re.sub(r"[^A-Za-z0-9]+", "_", self._loaded_instrument_label() or "solar").strip("_")
        label = label or "solar"
        stamp = ""
        if self._map_frames:
            try:
                when = frame_observation_time(self._map_frames[0])
                if isinstance(when, datetime):
                    stamp = when.strftime("_%Y%m%d_%H%M")
            except Exception:
                stamp = ""
        return f"{label}{stamp}.ecsolar"

    def _collect_session_meta(self) -> dict[str, Any]:
        """Snapshot the display state and CME picks for a saved session.

        The FITS frames themselves are embedded separately by the writer; this
        captures only what cannot be re-derived from the files.
        """
        picks = getattr(getattr(self, "_measure", None), "picks", {}) or {}
        frame_times: list[str | None] = []
        for frame in self._map_frames:
            when = None
            try:
                when = frame_observation_time(frame)
            except Exception:
                when = None
            frame_times.append(when.isoformat() if isinstance(when, datetime) else None)
        view = {
            "renderer": self.renderer_combo.currentText(),
            "colormap": self.colormap_combo.currentText(),
            "scale": self.scale_combo.currentText(),
            "clip_low": float(self.clip_low_slider.value()),
            "clip_high": float(self.clip_high_slider.value()),
            "difference_mode": self.movie_content_combo.currentText(),
            "movie_format": self.movie_format_combo.currentText(),
            "nrgf": bool(self.nrgf_check.isChecked()),
            "fps": float(self.fps_spin.value()),
            "solar_limb": bool(self.solar_limb_check.isChecked()),
            "grid": bool(self.grid_check.isChecked()),
            "grid_frame": self.grid_frame_combo.currentText(),
            "colorbar": bool(self.colorbar_check.isChecked()),
            "region_overlay": bool(self.region_overlay_check.isChecked()),
            "mag_threshold": int(self.mag_threshold_spin.value()),
            "crop_applied": bool(self._crop_applied),
            "crop_bounds": list(self._applied_crop_arcsec) if self._applied_crop_arcsec else None,
            "current_frame_index": int(self._current_frame_index),
            "frame_count": len(self._map_frames),
        }
        source = {
            "instrument_label": self._loaded_instrument_label(),
            "observable_index": int(self.wavelength_combo.currentIndex()),
            "observable_text": self.wavelength_combo.currentText(),
            "start": self.start_dt_edit.dateTime().toPython().replace(tzinfo=None).isoformat(),
            "end": self.end_dt_edit.dateTime().toPython().replace(tzinfo=None).isoformat(),
            "sample_seconds": int(self.sample_seconds_spin.value()),
            "max_records": int(self.max_records_spin.value()),
            "high_resolution": bool(self.high_resolution_check.isChecked()),
            "exposure_varies": bool(self._exposure_varies),
            "frame_count": len(self._map_frames),
            "frame_times": frame_times,
        }
        return {
            "source": source,
            "view": view,
            "measurements": {"height_time_picks": serialize_picks(picks)},
        }

    def save_session(self) -> bool:
        """Save to the current session file, or prompt if there isn't one yet."""
        if self._session_path:
            return self._write_session_to(self._session_path)
        return self.save_session_as()

    def save_session_as(self) -> bool:
        if not self._map_frames or not self._loaded_paths:
            QMessageBox.information(
                self,
                "Save Session",
                "Load or upload solar frames before saving a session.",
            )
            return False
        default = self._default_session_name()
        if self._session_path:
            default = str(Path(self._session_path).with_name(default))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Solar Session", default, "Solar session (*.ecsolar)"
        )
        if not path:
            return False
        if not path.lower().endswith(".ecsolar"):
            path += ".ecsolar"
        return self._write_session_to(path)

    def _write_session_to(self, path: str) -> bool:
        try:
            meta = self._collect_session_meta()
            count = write_solar_session(path, meta=meta, frame_paths=self._loaded_paths)
        except SolarSessionError as exc:
            QMessageBox.critical(self, "Save Session Failed", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - surface any I/O failure to the user
            QMessageBox.critical(self, "Save Session Failed", f"Could not save session:\n{exc}")
            return False
        self._session_path = path
        picks = session_pick_count(meta)
        self.statusBar().showMessage(
            f"Session saved: {Path(path).name}  ·  {count} frame(s), {picks} CME pick(s) embedded.",
            7000,
        )
        return True

    def open_session(self) -> None:
        if self._busy or self.is_operation_running():
            QMessageBox.information(
                self, "Open Session", "Wait for the current operation to finish before opening a session."
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Solar Session", "", "Solar session (*.ecsolar);;All files (*)"
        )
        if not path:
            return
        extract_dir = self._session_extract_dir(path)
        try:
            # Start from a clean extraction folder so a re-open never mixes in
            # stale frames from a previous session with the same name.
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            result = read_solar_session(path, extract_dir=str(extract_dir))
        except SolarSessionError as exc:
            QMessageBox.critical(self, "Open Session Failed", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - surface any I/O failure to the user
            QMessageBox.critical(self, "Open Session Failed", f"Could not open session:\n{exc}")
            return
        self._session_path = path
        self._pending_session_restore = result.meta
        self.statusBar().showMessage(
            f"Opening session: {session_frame_count(result.meta)} frame(s), "
            f"{session_pick_count(result.meta)} CME pick(s)…",
            6000,
        )
        # Frames load asynchronously; _apply_loaded_frames applies the restore.
        self.load_local_paths(result.frame_paths)

    def _apply_session_restore(self, meta: dict[str, Any]) -> None:
        """Replay saved display state and CME picks after the frames reload."""
        view = dict(meta.get("view") or {})
        source = dict(meta.get("source") or {})
        n = len(self._map_frames)

        saved_count = int(source.get("frame_count") or view.get("frame_count") or 0)
        if saved_count and saved_count != n:
            self.statusBar().showMessage(
                f"Session had {saved_count} frame(s) but {n} reloaded — "
                "picks are realigned by frame index.",
                8000,
            )

        self._restore_source_widgets(source)
        self._restore_view_widgets(view)

        # Frames always reload uncropped, so re-apply the saved crop.
        if view.get("crop_applied"):
            bounds = view.get("crop_bounds") or []
            if len(bounds) == 4:
                spins = (self.crop_x0_spin, self.crop_x1_spin, self.crop_y0_spin, self.crop_y1_spin)
                for spin, value in zip(spins, bounds):
                    was = spin.blockSignals(True)
                    spin.setValue(float(value))
                    spin.blockSignals(was)
                self.apply_axis_crop()
                n = len(self._map_frames)

        # Restore height-time picks, dropping any that fall outside the range.
        picks = deserialize_picks((meta.get("measurements") or {}).get("height_time_picks"))
        picks = {idx: entry for idx, entry in picks.items() if 0 <= idx < n}
        if picks and not self.measurements_check.isChecked():
            # Restored picks need the tracking panel available to be seen.
            self.measurements_check.setChecked(True)
        if hasattr(self, "_measure"):
            self._measure.restore_picks(picks)
        self._sync_tracking_panel_visibility()

        # Land on the saved frame and render with the restored display state.
        target = int(view.get("current_frame_index", 0) or 0)
        self._set_frame_index(max(0, min(target, max(0, n - 1))))
        self._refresh_graticule_overlay()
        self._refresh_region_overlays()

        # Recompute the fit so the tracking panel shows the CME kinematics again.
        if len(picks) >= 2 and hasattr(self, "_measure"):
            self._measure.finish_height_time()

        self.statusBar().showMessage(
            f"Session restored: {n} frame(s), {len(picks)} CME pick(s).", 7000
        )

    def _restore_source_widgets(self, source: dict[str, Any]) -> None:
        idx = source.get("observable_index")
        if isinstance(idx, int) and 0 <= idx < self.wavelength_combo.count():
            was = self.wavelength_combo.blockSignals(True)
            self.wavelength_combo.setCurrentIndex(idx)
            self.wavelength_combo.blockSignals(was)
        for key, edit in (("start", self.start_dt_edit), ("end", self.end_dt_edit)):
            text = source.get(key)
            if not text:
                continue
            try:
                dt = datetime.fromisoformat(str(text)).replace(tzinfo=None)
            except ValueError:
                continue
            was = edit.blockSignals(True)
            edit.setDateTime(QDateTime(dt))
            edit.blockSignals(was)

    def _restore_view_widgets(self, view: dict[str, Any]) -> None:
        for combo, text in (
            (self.renderer_combo, view.get("renderer")),
            (self.colormap_combo, view.get("colormap")),
            (self.scale_combo, view.get("scale")),
            (self.movie_content_combo, view.get("difference_mode")),
            (self.movie_format_combo, view.get("movie_format")),
            (self.grid_frame_combo, view.get("grid_frame")),
        ):
            if text:
                was = combo.blockSignals(True)
                combo.setCurrentText(str(text))
                combo.blockSignals(was)
        for check, value in (
            (self.grid_check, view.get("grid")),
            (self.colorbar_check, view.get("colorbar")),
            (self.region_overlay_check, view.get("region_overlay")),
            (self.solar_limb_check, view.get("solar_limb")),
            (self.nrgf_check, view.get("nrgf")),
        ):
            if value is not None:
                was = check.blockSignals(True)
                check.setChecked(bool(value))
                check.blockSignals(was)
        for slider, value in (
            (self.clip_low_slider, view.get("clip_low")),
            (self.clip_high_slider, view.get("clip_high")),
        ):
            if value is not None:
                was = slider.blockSignals(True)
                slider.setValue(float(value))
                slider.blockSignals(was)
        for spin, value in (
            (self.fps_spin, view.get("fps")),
            (self.mag_threshold_spin, view.get("mag_threshold")),
        ):
            if value is not None:
                was = spin.blockSignals(True)
                spin.setValue(type(spin.value())(value))
                spin.blockSignals(was)
        # Swap to the saved renderer and push colorbar/colormap/grid onto the
        # now-active canvas (their toggle handlers were bypassed above).
        self._on_renderer_changed(self.renderer_combo.currentText())
        self._on_colorbar_toggled(self.colorbar_check.isChecked())
        self._sync_nrgf_enabled()

    def export_plot(self):
        canvas = self._active_canvas()
        if hasattr(canvas, "has_plot_content"):
            has_content = canvas.has_plot_content()
        else:
            has_content = getattr(getattr(canvas, "map_image", None), "image", None) is not None
        if not has_content:
            QMessageBox.information(self, "Export Plot", "No plot is available yet.")
            return
        path, _ = pick_export_path(
            self,
            "Export Solar Plot",
            f"{self._export_basename('solar_analysis')}.png",
            "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tiff *.tif);;JPG (*.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            self._save_canvas_plot(path)
            self.statusBar().showMessage(f"Plot saved: {path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export Plot", str(exc))

    def _save_canvas_plot(self, path: str) -> None:
        canvas = self._active_canvas()
        if hasattr(canvas, "save_plot"):
            canvas.save_plot(path)
            return
        ext = Path(path).suffix.lower()
        if ext == ".svg":
            SVGExporter(self.pyqt_canvas.map_plot.plotItem).export(path)
            return
        pixmap = self.pyqt_canvas.map_plot.grab()
        if ext == ".pdf":
            writer = QPdfWriter(path)
            painter = QPainter(writer)
            try:
                viewport = painter.viewport()
                scaled = pixmap.scaled(viewport.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = int((viewport.width() - scaled.width()) / 2)
                y = int((viewport.height() - scaled.height()) / 2)
                painter.drawPixmap(x, y, scaled)
            finally:
                painter.end()
            return
        if not pixmap.save(path):
            raise RuntimeError(f"Failed to save plot to '{path}'.")

    def export_cropped_fits(self):
        if not self._map_frames or self._current_map_data is None:
            QMessageBox.information(
                self,
                "Export Cropped FITS",
                "Load or upload solar frames first, then enter the X/Y arcsec bounds to export a crop.",
            )
            return
        path, _ = pick_export_path(
            self,
            "Export Cropped FITS",
            f"{self._export_basename('crop')}.fits",
            "FITS (*.fits *.fit);;Compressed FITS (*.fits.gz *.fit.gz)",
        )
        if not path:
            return
        try:
            bounds = self._crop_bounds_from_axis_fields(self._current_map_data.shape)
            frame = self._map_frames[self._current_frame_index]
            write_cropped_fits(frame, bounds, path)
            self.statusBar().showMessage(f"Cropped FITS saved: {path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export Cropped FITS", str(exc))

    def export_regions_csv(self):
        if not self._regions:
            QMessageBox.information(self, "Export Regions", "Detect active regions first.")
            return
        path, _ = pick_export_path(
            self, "Export Regions CSV", f"{self._export_basename('active_regions')}.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            keys = list(asdict(self._regions[0]).keys())
            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=keys)
                writer.writeheader()
                for region in self._regions:
                    writer.writerow(asdict(region))
            self.statusBar().showMessage(f"Region CSV saved: {path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export Regions", str(exc))

    def export_movie(self, *, default_suffix: str | None = None):
        if not self._map_frames:
            return
        if self.is_operation_running():
            QMessageBox.information(self, "Export Movie", "Another operation is still running.")
            return
        suffix = default_suffix or (".gif" if self.movie_format_combo.currentText().upper() == "GIF" else ".mp4")
        default_name = f"{self._export_basename('movie')}{suffix}"
        path, _ = pick_export_path(self, "Export Movie", default_name, "MP4 (*.mp4);;GIF (*.gif)")
        if not path:
            return

        # Resolve the MP4/ffmpeg question up front (on the UI thread) so the
        # background worker never has to pop a dialog mid-render.
        if path.lower().endswith(".mp4") and not _imageio_ffmpeg_available():
            reply = QMessageBox.question(
                self,
                "Export Movie",
                "MP4 export needs the 'imageio-ffmpeg' package, which isn't available.\n\n"
                "Export an animated GIF instead?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
            path = str(Path(path).with_suffix(".gif"))

        crop_bounds = None
        if self.crop_check.isChecked() and self._current_map_data is not None:
            crop_bounds = self._crop_bounds_from_axis_fields(self._current_map_data.shape)

        spec = AiaMovieExportSpec(
            path=path,
            fps=float(self.fps_spin.value()),
            mode=self._movie_mode(),
            crop_bounds=crop_bounds,
            percentile_low=float(self.clip_low_slider.value()),
            percentile_high=float(self.clip_high_slider.value()),
            colormap_name=self._resolved_colormap_name(),
            scale=self.scale_combo.currentText(),
            # Match the preview: difference movies normalise unequal exposures.
            normalize_exposure=bool(self._exposure_varies) and self._movie_mode() != "raw",
        )
        self._set_busy(True, f"Exporting movie ({len(self._map_frames)} frame(s))…")
        self.progress_panel.set_status_text("Preparing export…")
        self._start_worker(MovieExportWorker(self._map_frames, spec))

    @Slot(int, int)
    def _on_export_progress(self, done: int, total: int):
        total = max(1, int(total))
        pct = int(max(0, min(100, int(done) * 100 / total)))
        if self.progress.maximum() <= 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(pct)
        self.progress_panel.set_status_text(f"Rendering frame {int(done)} of {total}  ·  {pct}%")
        self.statusBar().showMessage(f"Exporting movie: frame {int(done)}/{total}", 2000)

    @Slot(str)
    def _on_export_finished(self, out_path: str):
        self.statusBar().showMessage(f"Movie saved: {out_path}", 6000)
        self.analysis_text.setPlainText(f"Movie exported to:\n{out_path}")

    def stop_active_operation(self):
        worker = self._active_worker
        if worker is not None and hasattr(worker, "cancel"):
            try:
                worker.cancel()
            except Exception:
                pass
        if worker is None or not self.is_operation_running():
            return
        self.stop_btn.setEnabled(False)
        if hasattr(self, "stop_action"):
            self.stop_action.setEnabled(False)
        self.statusBar().showMessage(
            "Cancelling... completed downloads stay in cache and will load if available.",
            7000,
        )

    def closeEvent(self, event):
        if self.is_operation_running():
            # Don't trap the user behind a running download: cancel it, hide the
            # window so it visually closes at once, and finish closing for real
            # in _on_worker_stopped once the worker thread has actually stopped.
            self._pending_close = True
            worker = self._active_worker
            if isinstance(worker, SunPyWorker):
                try:
                    worker.cancel()
                except Exception:
                    pass
            self.stop_btn.setEnabled(False)
            if hasattr(self, "stop_action"):
                self.stop_action.setEnabled(False)
            self.statusBar().showMessage(
                "Cancelling download… the window will close once it stops. "
                "Completed files stay in the cache.",
                7000,
            )
            self._play_timer.stop()
            self.hide()
            event.ignore()
            return
        self._play_timer.stop()
        super().closeEvent(event)

    def _frame_title(self, frame: Any, frame_index: int) -> str:
        obs = self._safe_text(getattr(frame, "observatory", None))
        inst = self._safe_text(getattr(frame, "instrument", None))
        det = self._safe_text(getattr(frame, "detector", None))
        wl = self._safe_text(getattr(frame, "wavelength", None))
        date = self._safe_text(getattr(frame, "date", None))
        chunks = ["/".join([x for x in (obs, inst) if x])]
        # Show the detector (e.g. LASCO C2/C3) when it adds information beyond
        # the instrument name.
        if det and det.upper() != (inst or "").upper():
            chunks.append(det)
        if wl:
            chunks.append(wl)
        if date:
            chunks.append(f"{date} UTC" if "UTC" not in date.upper() else date)
        return " | ".join([x for x in chunks if x]) or f"Frame {frame_index + 1}"

    def _frame_wavelength_text(self) -> str:
        if not self._map_frames:
            return ""
        return self._safe_text(getattr(self._map_frames[self._current_frame_index], "wavelength", ""))

    def _prepare_map_array(self, raw_data: Any, label: str) -> np.ndarray:
        arr = np.asarray(raw_data)
        arr = np.squeeze(arr)
        if arr.ndim == 2:
            return np.asarray(arr, dtype=float)
        if arr.ndim == 3 and arr.shape[-1] in (3, 4):
            return np.asarray(arr, dtype=float)
        if arr.ndim > 2:
            arr2 = np.asarray(arr[0]).squeeze()
            if arr2.ndim == 2:
                return np.asarray(arr2, dtype=float)
        raise ValueError(f"Unsupported map array shape for {label}: {arr.shape}")

    def _axis_transform_for_arcsec(self, frame: Any, data_shape: tuple[int, ...]) -> dict[str, float]:
        ny = int(data_shape[0]) if len(data_shape) >= 1 else 0
        nx = int(data_shape[1]) if len(data_shape) >= 2 else 0
        x_ref_pix = (max(nx, 1) - 1) / 2.0
        y_ref_pix = (max(ny, 1) - 1) / 2.0
        x_scale = 1.0
        y_scale = 1.0
        x_ref_arcsec = 0.0
        y_ref_arcsec = 0.0

        scale = getattr(frame, "scale", None)
        x_scale_val = self._as_float(self._pick_component(scale, ("axis1", "x")), unit_hint="arcsec / pix")
        y_scale_val = self._as_float(self._pick_component(scale, ("axis2", "y")), unit_hint="arcsec / pix")
        meta = getattr(frame, "meta", {}) or {}
        if x_scale_val is None:
            x_scale_val = self._as_float(self._meta_get(meta, "cdelt1", "CDELT1"), unit_hint="arcsec / pix")
        if y_scale_val is None:
            y_scale_val = self._as_float(self._meta_get(meta, "cdelt2", "CDELT2"), unit_hint="arcsec / pix")
        if x_scale_val is not None and np.isfinite(x_scale_val):
            x_scale = x_scale_val
        if y_scale_val is not None and np.isfinite(y_scale_val):
            y_scale = y_scale_val

        ref_pixel = getattr(frame, "reference_pixel", None)
        x_ref_val = self._as_float(self._pick_component(ref_pixel, ("x", "axis1")), unit_hint="pix")
        y_ref_val = self._as_float(self._pick_component(ref_pixel, ("y", "axis2")), unit_hint="pix")
        x_ref_from_attr = x_ref_val is not None
        y_ref_from_attr = y_ref_val is not None
        if x_ref_val is None:
            x_ref_meta = self._as_float(self._meta_get(meta, "crpix1", "CRPIX1"), unit_hint="pix")
            if x_ref_meta is not None:
                x_ref_val = x_ref_meta - 1.0
        if y_ref_val is None:
            y_ref_meta = self._as_float(self._meta_get(meta, "crpix2", "CRPIX2"), unit_hint="pix")
            if y_ref_meta is not None:
                y_ref_val = y_ref_meta - 1.0
        crop_origin = getattr(frame, "_crop_origin_px", None)
        if crop_origin is not None:
            try:
                crop_x, crop_y = float(crop_origin[0]), float(crop_origin[1])
            except Exception:
                crop_x, crop_y = 0.0, 0.0
            if x_ref_from_attr and x_ref_val is not None:
                x_ref_val = x_ref_val - crop_x
            if y_ref_from_attr and y_ref_val is not None:
                y_ref_val = y_ref_val - crop_y
        if x_ref_val is not None and np.isfinite(x_ref_val):
            x_ref_pix = x_ref_val
        if y_ref_val is not None and np.isfinite(y_ref_val):
            y_ref_pix = y_ref_val

        ref_coord = getattr(frame, "reference_coordinate", None)
        x_arc_val = self._as_float(self._pick_component(ref_coord, ("Tx", "x", "lon")), unit_hint="arcsec")
        y_arc_val = self._as_float(self._pick_component(ref_coord, ("Ty", "y", "lat")), unit_hint="arcsec")
        if x_arc_val is None:
            x_arc_val = self._as_float(self._meta_get(meta, "crval1", "CRVAL1"), unit_hint="arcsec")
        if y_arc_val is None:
            y_arc_val = self._as_float(self._meta_get(meta, "crval2", "CRVAL2"), unit_hint="arcsec")
        if x_arc_val is not None and np.isfinite(x_arc_val):
            x_ref_arcsec = x_arc_val
        if y_arc_val is not None and np.isfinite(y_arc_val):
            y_ref_arcsec = y_arc_val

        return {
            "x_ref_pix": float(x_ref_pix),
            "y_ref_pix": float(y_ref_pix),
            "x_scale_arcsec_per_pix": float(x_scale),
            "y_scale_arcsec_per_pix": float(y_scale),
            "x_ref_arcsec": float(x_ref_arcsec),
            "y_ref_arcsec": float(y_ref_arcsec),
        }

    def _default_axis_transform(self) -> dict[str, float]:
        return {
            "x_ref_pix": 0.0,
            "y_ref_pix": 0.0,
            "x_scale_arcsec_per_pix": 1.0,
            "y_scale_arcsec_per_pix": 1.0,
            "x_ref_arcsec": 0.0,
            "y_ref_arcsec": 0.0,
        }

    def _pick_component(self, obj: Any, attrs: tuple[str, ...]) -> Any:
        if obj is None:
            return None
        for attr in attrs:
            if hasattr(obj, attr):
                try:
                    value = getattr(obj, attr)
                    if value is not None:
                        return value
                except Exception:
                    continue
        return None

    def _meta_get(self, meta: dict[str, Any], *keys: str) -> Any:
        if not meta:
            return None
        for key in keys:
            if key in meta:
                return meta.get(key)
            lower = key.lower()
            if lower in meta:
                return meta.get(lower)
            upper = key.upper()
            if upper in meta:
                return meta.get(upper)
        return None

    def _as_float(self, value: Any, unit_hint: str | None = None) -> float | None:
        if value is None:
            return None
        to_value = getattr(value, "to_value", None)
        if callable(to_value):
            if unit_hint:
                try:
                    return float(to_value(unit_hint))
                except Exception:
                    pass
            try:
                return float(to_value())
            except Exception:
                pass
        raw = getattr(value, "value", None)
        if raw is not None:
            try:
                return float(raw)
            except Exception:
                pass
        try:
            return float(value)
        except Exception:
            return None

    def _safe_text(self, value: Any) -> str:
        if value is None:
            return ""
        try:
            return str(value).strip()
        except Exception:
            return repr(value)
