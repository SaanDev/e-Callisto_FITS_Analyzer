"""
e-CALLISTO FITS Analyzer
Version 3.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Length and area tools for the Solar Image Analysis window: filament length,
sunspot (umbra/penumbra) area, post-flare arcade and ribbon area.

``SurfaceTool`` is the click/drag state machine behind the toolbar's Length
and Area buttons; ``MeasurementController`` owns one and forwards canvas input
to it while either mode is armed. ``SurfaceMeasurePanel`` is the "Surface" tab
beside the canvas: the area shape, the threshold settings and the live result.
All of the measuring itself is ``src/backend/solar/surface_measure.py``.

Shapes are held in helioprojective arcsec, as clicked, so a shape drawn on
one frame can be re-measured on the next; only the mask is rebuilt per frame.
Thresholds always run on the frame's own data, never on a difference image,
because "a fraction of the quiet Sun" means nothing once the disk has been
subtracted.
"""

from __future__ import annotations

import html
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import timezone
from typing import Any

import numpy as np
import pyqtgraph as pg
from scipy import ndimage
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QApplication,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.backend.solar.solar_data_analysis import frame_observation_time
from src.backend.solar.surface_records import (
    SurfaceRecord,
    area_values,
    feature_series,
    group_rows_by_feature,
    length_values,
    linear_rate,
    write_csv,
    write_json,
)
from src.backend.solar.surface_measure import (
    DEFAULT_PENUMBRA_LEVEL,
    DEFAULT_UMBRA_LEVEL,
    LIMB_WARNING_MU,
    QuietSunProfile,
    SurfaceArea,
    SurfaceGeometry,
    SurfaceLength,
    ellipse_mask,
    fit_quiet_sun_profile,
    geometry_for_frame,
    mask_surface_area,
    normalize_to_quiet_sun,
    polygon_mask_from_arcsec,
    polyline_surface_length,
    DEFAULT_MU_MIN,
    MAX_BACKGROUND_SPREAD,
    background_spread,
    disk_interior_mask,
    excluded_pixel_mask,
    rectangle_mask,
    rotate_points,
    sunspot_masks,
    threshold_mask,
)


# Area shapes, in the order the panel lists them.
SHAPES = ("polygon", "lasso", "ellipse", "rectangle")
SHAPE_LABELS = {
    "polygon": "Polygon (click corners)",
    "lasso": "Freehand lasso (drag)",
    "ellipse": "Ellipse (drag)",
    "rectangle": "Rectangle (drag)",
}
DRAG_SHAPES = ("lasso", "ellipse", "rectangle")

# Threshold modes.
THRESHOLDS = ("none", "sunspot", "dark", "bright")
THRESHOLD_LABELS = {
    "none": "Selected region (whole shape)",
    "sunspot": "Sunspot only: umbra / penumbra",
    "dark": "Darker than",
    "bright": "Brighter than",
}
DEFAULT_DARK_LEVEL = 0.85
DEFAULT_BRIGHT_LEVEL = 1.5
DEFAULT_MIN_CLUSTER_PX = 3

# Mask tints (RGB).
UMBRA_COLOR = (70, 130, 255)
REGION_COLOR = (90, 200, 255)  # measured pixels of a whole-region measurement
EXCLUDED_COLOR = (235, 60, 60)  # selected but not integrated: off the disc or below the mu cutoff
#: Threshold step used for the segmentation sensitivity range (plan section 9).
SENSITIVITY_LEVEL_STEP = 0.02
PENUMBRA_COLOR = (255, 170, 50)
DARK_COLOR = (60, 210, 255)
BRIGHT_COLOR = (110, 255, 110)

# Freehand points closer than this (in screen-independent arcsec, as a
# fraction of R_sun) are dropped, so a slow drag does not store thousands.
_LASSO_MIN_STEP_RSUN = 1.0 / 2000.0


@dataclass
class SurfaceShape:
    """One drawn shape, in helioprojective arcsec."""

    kind: str  # "length" or one of SHAPES
    points: list[tuple[float, float]] = field(default_factory=list)

    @property
    def is_area(self) -> bool:
        return self.kind != "length"

    def outline(self) -> tuple[np.ndarray, np.ndarray]:
        """Closed outline (or the polyline, for a length) to draw on the canvas."""
        pts = np.asarray(self.points, dtype=float).reshape(-1, 2)
        if self.kind in ("polygon", "lasso"):
            if pts.shape[0] >= 2:
                pts = np.vstack([pts, pts[:1]])
            return pts[:, 0], pts[:, 1]
        if self.kind in ("ellipse", "rectangle") and pts.shape[0] >= 2:
            (x0, y0), (x1, y1) = pts[0], pts[-1]
            if self.kind == "rectangle":
                xs = np.array([x0, x1, x1, x0, x0])
                ys = np.array([y0, y0, y1, y1, y0])
                return xs, ys
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            a, b = abs(x1 - x0) / 2.0, abs(y1 - y0) / 2.0
            t = np.linspace(0.0, 2.0 * math.pi, 121)
            return cx + a * np.cos(t), cy + b * np.sin(t)
        return pts[:, 0], pts[:, 1]

    def is_complete(self) -> bool:
        n = len(self.points)
        if self.kind == "length":
            return n >= 2
        if self.kind in ("polygon", "lasso"):
            return n >= 3
        if n < 2:
            return False
        (x0, y0), (x1, y1) = self.points[0], self.points[-1]
        return abs(x1 - x0) > 0.0 and abs(y1 - y0) > 0.0

    def weights(self, shape: tuple[int, int], geometry: SurfaceGeometry, subsamples: int = 1) -> np.ndarray:
        """Fractional pixel coverage of the shape (plan section 5.2).

        ``subsamples = 1`` is the pixel-centre mask (0 or 1). Larger values
        rasterize the shape on an ``n x n`` finer grid -- only around the shape,
        never the whole frame -- and average back to pixels, so a boundary pixel
        counts by how much of it the shape covers.
        """
        base = self.mask(shape, geometry)
        n = max(1, int(subsamples))
        if n == 1:
            return base.astype(float)
        ny, nx = int(shape[0]), int(shape[1])
        pts = np.asarray(self.points, dtype=float).reshape(-1, 2)
        cols, rows = geometry.arcsec_to_pixel(pts[:, 0], pts[:, 1])
        c0 = max(0, int(math.floor(float(np.min(cols)))) - 1)
        c1 = min(nx - 1, int(math.ceil(float(np.max(cols)))) + 1)
        r0 = max(0, int(math.floor(float(np.min(rows)))) - 1)
        r1 = min(ny - 1, int(math.ceil(float(np.max(rows)))) + 1)
        if c1 < c0 or r1 < r0:
            return base.astype(float)
        hi_shape = ((r1 - r0 + 1) * n, (c1 - c0 + 1) * n)
        # Pixel coordinates on the fine local grid: centre of pixel p is (p - origin + 0.5) n - 0.5.
        hc = (cols - c0 + 0.5) * n - 0.5
        hr = (rows - r0 + 0.5) * n - 0.5
        if self.kind in ("polygon", "lasso"):
            hi = polygon_mask(hi_shape, np.column_stack((hc, hr)))
        elif self.kind == "rectangle":
            hi = rectangle_mask(hi_shape, (float(hc[0]), float(hr[0])), (float(hc[-1]), float(hr[-1])))
        else:
            centre = ((float(hc[0]) + float(hc[-1])) / 2.0, (float(hr[0]) + float(hr[-1])) / 2.0)
            semi = (abs(float(hc[-1]) - float(hc[0])) / 2.0, abs(float(hr[-1]) - float(hr[0])) / 2.0)
            hi = ellipse_mask(hi_shape, centre, semi)
        local = hi.reshape(r1 - r0 + 1, n, c1 - c0 + 1, n).mean(axis=(1, 3))
        out = np.zeros((ny, nx), dtype=float)
        out[r0 : r1 + 1, c0 : c1 + 1] = local
        return out

    def mask(self, shape: tuple[int, int], geometry: SurfaceGeometry) -> np.ndarray:
        """Pixel mask of the shape on an image of ``shape``."""
        if self.kind in ("polygon", "lasso"):
            return polygon_mask_from_arcsec(shape, self.points, geometry)
        (x0, y0), (x1, y1) = self.points[0], self.points[-1]
        c0, r0 = geometry.arcsec_to_pixel(x0, y0)
        c1, r1 = geometry.arcsec_to_pixel(x1, y1)
        if self.kind == "rectangle":
            return rectangle_mask(shape, (float(c0), float(r0)), (float(c1), float(r1)))
        centre = ((float(c0) + float(c1)) / 2.0, (float(r0) + float(r1)) / 2.0)
        semi = (abs(float(c1) - float(c0)) / 2.0, abs(float(r1) - float(r0)) / 2.0)
        return ellipse_mask(shape, centre, semi)


@dataclass(frozen=True)
class ThresholdSettings:
    mode: str = "none"
    umbra_level: float = DEFAULT_UMBRA_LEVEL
    penumbra_level: float = DEFAULT_PENUMBRA_LEVEL
    level: float = DEFAULT_DARK_LEVEL
    min_cluster_px: int = DEFAULT_MIN_CLUSTER_PX


AREA_METHOD_LABELS = {
    "finite_distance": "Finite-distance WCS integration",
    "orthographic": "Orthographic approximation (comparison)",
}


@dataclass(frozen=True)
class AreaSettings:
    """How areas are integrated (plan sections 4, 5.2 and 6.2)."""

    method: str = "finite_distance"
    mu_min: float = DEFAULT_MU_MIN
    subsamples: int = 1  # 1 = pixel centres, 4 = 4 x 4 subpixel coverage


@dataclass(frozen=True)
class AreaResult:
    """Everything one area measurement reports."""

    shape_kind: str
    region: SurfaceArea  # the whole drawn shape
    threshold: ThresholdSettings
    selected: SurfaceArea | None = None  # thresholded pixels (dark / bright / whole spot)
    umbra: SurfaceArea | None = None
    penumbra: SurfaceArea | None = None
    intensity_unit: str = "DN"
    quicklook: bool = False
    #: ``(mask, rgb)`` tints for the canvas, in drawing order.
    layers: tuple = ()
    #: Pixels of the shape a dark threshold left out: off the disk or within
    #: the outermost limb (mu below the quiet-Sun fit), where nothing is a spot.
    limb_excluded_px: int = 0
    area_settings: AreaSettings = field(default_factory=AreaSettings)
    #: Sensitivity range of the main area (MSH): (low, high, how it was varied).
    sensitivity: tuple[float, float, str] | None = None
    #: Header fallbacks and warnings met while reading the geometry.
    notes: tuple[str, ...] = ()
    #: The load-time pixel check of the frame (size, plate scale, limb).
    image_check: str | None = None
    #: Spread (p95 - p5) of the quiet-Sun-normalised disk background; a
    #: threshold result on a frame above MAX_BACKGROUND_SPREAD is unreliable.
    background_spread: float | None = None

    @property
    def focus(self) -> SurfaceArea:
        """The area being reported: the thresholded pixels, else the whole region."""
        return self.selected if self.selected is not None else self.region

    @property
    def status(self) -> str:
        """OK / PARTIAL / INVALID for the reported area.

        A valid region with no detected spot is a valid zero, not an invalid
        measurement (plan section 10.1).
        """
        focus = self.focus
        if self.selected is not None and focus.status == "empty":
            return "invalid" if self.region.status in ("invalid", "empty") else "ok"
        return "invalid" if focus.status == "empty" else focus.status


def _plain(rich: str) -> str:
    """Rich label text back to plain lines."""
    return html.unescape(re.sub(r"<[^>]+>", "", rich.replace("<br>", "\n")))


def _field_label(text: str) -> QLabel:
    """The analysis window's small muted field caption (``SolarFieldLabel``)."""
    label = QLabel(text)
    label.setObjectName("SolarFieldLabel")
    return label


def _subheading(text: str) -> QLabel:
    """The analysis window's uppercase section divider (``SolarSubheading``)."""
    label = QLabel(str(text).upper())
    label.setObjectName("SolarSubheading")
    return label


def _unix(when) -> float:
    """UTC Unix seconds; naive datetimes are taken as UTC (as frame times are)."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


# ------------------------------------------------------------------ formatting
def format_lonlat(lonlat: tuple[float, float] | None) -> str:
    """Stonyhurst ``(lon, lat)`` as the usual ``N12.3° W45.6°``; east is negative."""
    if lonlat is None:
        return "off disk"
    lon, lat = lonlat
    ns = "N" if lat >= 0 else "S"
    ew = "W" if lon >= 0 else "E"
    return f"{ns}{abs(lat):.1f}° {ew}{abs(lon):.1f}°"


def _mm(km: float) -> str:
    return f"{km / 1000.0:,.1f} Mm"


def _mm2(km2: float) -> str:
    return f"{km2 / 1e6:,.0f} Mm²"


def _foreshortening(area: SurfaceArea) -> str:
    """The correction factor, once it is large enough to be worth reading.

    Near disk centre the ratio sits just under 1 (the surface there is one
    solar radius nearer than the plane of sky the image scale refers to), which
    would read as a confusing "×0.99 foreshortening".
    """
    factor = area.correction_factor
    if factor is None:
        if area.on_disk_plane_of_sky_km2 <= 0.0:
            return ""
        factor = area.on_disk_km2 / area.on_disk_plane_of_sky_km2
    return f" (×{factor:.2f} foreshortening)" if factor >= 1.05 else ""


def _seen_km2(area: SurfaceArea) -> float:
    """Projected (line-of-sight) area of the valid part, km^2 (plan section 4.4)."""
    if area.projected_km2 is not None:
        return float(area.projected_km2)
    return float(area.on_disk_plane_of_sky_km2)


def _seen_msh(area: SurfaceArea) -> float:
    """The on-disk area as it appears on the image (no curvature correction), in MSH."""
    from src.backend.solar.surface_measure import km2_to_msh

    return km2_to_msh(_seen_km2(area))


def _area_core(area: SurfaceArea, *, corrected: bool = True) -> str:
    """``152.3 MSH · 463 Mm²`` plus any off-limb part.

    Corrected: the true area on the sphere, with its foreshortening factor.
    As seen: the area exactly as it appears on the image, in MSH, with the
    corrected value alongside so the two are never confused.
    """
    parts = []
    if area.on_disk_pixels > 0.0:
        if corrected:
            parts.append(f"{area.on_disk_msh:,.1f} MSH · {_mm2(area.on_disk_km2)}{_foreshortening(area)}")
        else:
            parts.append(
                f"{_seen_msh(area):,.1f} MSH as seen · {_mm2(_seen_km2(area))}"
                f" (not corrected for curvature; corrected {area.on_disk_msh:,.1f} MSH)"
            )
    if area.has_off_limb:
        parts.append(f"off-limb {_mm2(area.off_limb_km2)} (plane of sky, not corrected)")
    return " + ".join(parts) if parts else "0 MSH"


def format_length(result: SurfaceLength, *, corrected: bool = True) -> str:
    """One-line Details entry for a length."""
    if not corrected:
        text = (
            f"Length  ·  {_mm(result.plane_of_sky_km)} as seen, {result.plane_of_sky_arcsec:,.1f}″"
            f" (not corrected for curvature; {_mm(result.total_km)} along the surface)"
            f"  ·  {result.n_vertices} points"
        )
        if result.start_lonlat is not None or result.end_lonlat is not None:
            text += f"  ·  {format_lonlat(result.start_lonlat)} → {format_lonlat(result.end_lonlat)}"
        return text
    text = f"Length  ·  {_mm(result.total_km)}"
    if result.off_limb_km > 0.0 and result.on_disk_km > 0.0:
        text += f" ({_mm(result.on_disk_km)} on the surface + {_mm(result.off_limb_km)} off-limb, plane of sky)"
    elif result.on_disk_km > 0.0:
        text += " along the surface"
    else:
        text += " (off-limb, plane of sky)"
    text += f"  ·  plane of sky {_mm(result.plane_of_sky_km)}, {result.plane_of_sky_arcsec:,.1f}″"
    text += f"  ·  {result.n_vertices} points"
    if result.start_lonlat is not None or result.end_lonlat is not None:
        text += f"  ·  {format_lonlat(result.start_lonlat)} → {format_lonlat(result.end_lonlat)}"
    if result.near_limb:
        text += f"  ·  ⚠ reaches μ={result.min_mu:.2f}: close to the limb, the correction is large"
    return text


def _status_text(result: AreaResult) -> str:
    """``PARTIAL: 120 px below mu 0.30, 4 px off the disc excluded`` etc."""
    focus = result.focus
    status = result.status
    cutoff = result.area_settings.mu_min
    if status == "invalid":
        if result.selected is not None and result.region.is_valid:
            return "INVALID: no valid pixels in the detected mask"
        return f"INVALID: no valid pixels (off the disc or below μ {cutoff:.2f})"
    if status != "partial":
        return "OK"
    parts = []
    if focus.near_limb_px > 0:
        parts.append(f"{focus.near_limb_px:,.0f} px below μ {cutoff:.2f}")
    if focus.off_disc_px > 0:
        parts.append(f"{focus.off_disc_px:,.0f} px off the disc")
    return "PARTIAL: " + " and ".join(parts) + " excluded; area of the valid part only"


def format_area(result: AreaResult, *, corrected: bool = True) -> str:
    """One-line Details entry for an area (corrected for curvature, or as seen)."""
    region = result.region
    if result.status == "invalid":
        label = result.shape_kind
        return f"Area ({label})  ·  no valid area  ·  {_status_text(result)}"
    if result.selected is not None and result.selected.status == "empty":
        word = {"sunspot": "sunspot", "dark": "dark pixel", "bright": "bright pixel"}.get(result.threshold.mode, "pixel")
        return (
            f"Area ({result.shape_kind})  ·  no {word} detected: 0 MSH (valid, in {region.on_disk_msh:,.1f} MSH "
            f"of measured region)  ·  {_status_text(result) if region.status == 'partial' else 'OK'}"
        )
    label = {"polygon": "polygon", "lasso": "lasso", "ellipse": "ellipse", "rectangle": "rectangle"}.get(
        result.shape_kind, result.shape_kind
    )
    mode = result.threshold.mode
    if mode == "sunspot" and result.selected is not None:
        umbra = result.umbra.on_disk_msh if corrected else _seen_msh(result.umbra)
        penumbra = result.penumbra.on_disk_msh if corrected else _seen_msh(result.penumbra)
        seen = "" if corrected else " as seen"
        text = (
            f"Sunspot ({label})  ·  spot {_area_core(result.selected, corrected=corrected)}"
            f"  ·  umbra {umbra:,.1f} MSH{seen}, penumbra {penumbra:,.1f} MSH{seen}"
            f" (I/I_qs < {result.threshold.umbra_level:.2f} / < {result.threshold.penumbra_level:.2f})"
        )
        focus = result.selected
    elif mode in ("dark", "bright") and result.selected is not None:
        word = "darker than" if mode == "dark" else "brighter than"
        shape_msh = region.on_disk_msh if corrected else _seen_msh(region)
        text = (
            f"Area ({label}, {word} {result.threshold.level:.2f} I_qs)  ·  "
            f"{_area_core(result.selected, corrected=corrected)}"
            f"  ·  of a {shape_msh:,.1f} MSH shape"
        )
        focus = result.selected
    else:
        text = f"Area ({label})  ·  {_area_core(region, corrected=corrected)}"
        focus = region
    if focus.on_disk_pixels > 0.0:
        text += f"  ·  centroid {format_lonlat(focus.centroid_lonlat)}"
        if focus.mean_mu is not None:
            text += f", μ={focus.mean_mu:.2f}"
    if focus.mean_intensity is not None and not result.quicklook:
        text += f"  ·  mean {focus.mean_intensity:,.1f} {result.intensity_unit}"
    if result.status == "partial":
        text += f"  ·  ⚠ {_status_text(result)}"
    if result.limb_excluded_px and mode in ("sunspot", "dark"):
        text += f"  ·  {result.limb_excluded_px:,} px off the disk or at the extreme limb left out of the threshold"
    if result.sensitivity is not None:
        low, high, how = result.sensitivity
        text += f"  ·  range {low:,.1f}–{high:,.1f} MSH ({how})"
    if result.area_settings.method != "finite_distance":
        text += f"  ·  {AREA_METHOD_LABELS[result.area_settings.method]}"
    for note in result.notes:
        text += f"  ·  ⚠ {note}"
    return text


_SUPERSCRIPT = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def _sci_km2(km2: float) -> str:
    """``5.55 × 10⁹ km²``."""
    if km2 <= 0.0 or not math.isfinite(km2):
        return "0 km²"
    exponent = int(math.floor(math.log10(km2)))
    return f"{km2 / 10**exponent:.2f} × 10{str(exponent).translate(_SUPERSCRIPT)} km²"


def area_report(result: AreaResult, *, corrected: bool = True) -> list[str]:
    """The result card, laid out as in the plan's section 7."""
    focus = result.focus
    lines: list[str] = []
    status = result.status
    if status != "ok":
        lines.append("● " + _status_text(result))
    mode = result.threshold.mode
    what = {"sunspot": "Sunspot", "dark": "Dark area", "bright": "Bright area"}.get(mode, "Selected region")
    if status == "invalid":
        lines.append(f"{what} ({result.shape_kind}) — no valid area")
    elif result.selected is not None and result.selected.status == "empty":
        lines.append(f"{what} ({result.shape_kind}) — none detected: 0 MSH (valid)")
    else:
        if corrected:
            lines.append(
                f"{what} ({result.shape_kind}) — corrected {focus.on_disk_msh:,.1f} MSH · "
                f"{_mm2(focus.on_disk_km2)} · {_sci_km2(focus.on_disk_km2)}"
            )
        else:
            lines.append(
                f"{what} ({result.shape_kind}) — as seen {_seen_msh(focus):,.1f} MSH · {_mm2(_seen_km2(focus))} "
                f"(corrected {focus.on_disk_msh:,.1f} MSH)"
            )
        if mode == "sunspot" and result.umbra is not None and result.penumbra is not None:
            u = result.umbra.on_disk_msh if corrected else _seen_msh(result.umbra)
            p = result.penumbra.on_disk_msh if corrected else _seen_msh(result.penumbra)
            lines.append(f"Umbra {u:,.1f} MSH · penumbra {p:,.1f} MSH")
        factor = focus.correction_factor
        detail = f"Projected {_mm2(_seen_km2(focus))}"
        if factor is not None:
            detail += f" · correction ×{factor:.2f}"
        if focus.min_mu is not None:
            detail += f" · min μ {focus.min_mu:.2f}"
        lines.append(detail)
        if result.sensitivity is not None:
            low, high, how = result.sensitivity
            lines.append(f"Sensitivity {low:,.1f}–{high:,.1f} MSH ({how})")
        if focus.centroid_lonlat is not None:
            lines.append(f"Centroid {format_lonlat(focus.centroid_lonlat)}")
    lines.append(
        f"Pixels {focus.selected_px:,} selected / {focus.valid_px:,.0f} valid · μ ≥ {result.area_settings.mu_min:.2f} · "
        f"quality {status.upper()}"
    )
    refine = "pixel centres" if result.area_settings.subsamples == 1 else f"subpixel {result.area_settings.subsamples}×{result.area_settings.subsamples}"
    lines.append(f"Method: {AREA_METHOD_LABELS[result.area_settings.method]}, {refine}")
    if result.image_check:
        lines.append(f"Image: {result.image_check}")
    for note in result.notes:
        lines.append(f"⚠ {note}")
    return lines


# ----------------------------------------------------------------------- panel
class SurfaceMeasurePanel(QWidget):
    """The "Surface" tab: shape choice, threshold settings, live result and the table."""

    shapeChanged = Signal(str)
    thresholdChanged = Signal()
    #: The corrected / as-seen choice changed.
    curvatureChanged = Signal()
    #: The area method (geometry, mu cutoff, boundary refinement) changed.
    areaSettingsChanged = Signal()
    #: A table row was picked by the user (row index).
    recordSelected = Signal(int)
    #: A feature name was edited: (row index, new name).
    recordRenamed = Signal(int, str)

    # The centroid rides in each row's tooltip (and the CSV): six columns do
    # not fit the panel beside the map without squeezing the name to nothing.
    COLUMNS = ("Name", "Time (UT)", "Measure", "Result", "μ")

    def __init__(self, parent: Any = None):
        super().__init__(parent)
        # Laid out like the CME Tracking tab beside it (table, one control
        # row, graph, status line, one row of three buttons) and styled with
        # the analysis window's own label roles, so the two tabs read as one
        # panel. Everything sits in a borderless scroll area: the tab holds
        # more than the tracking panel, and its full height must never become
        # the window's minimum.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("SolarSurfaceScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.viewport().setAutoFillBackground(False)
        outer.addWidget(self.scroll_area)
        content = QWidget()
        content.setObjectName("SolarSurfaceContent")
        content.setAutoFillBackground(False)
        content.setMinimumHeight(520)  # below this the tab scrolls rather than squash the table
        self.scroll_area.setWidget(content)
        self._content = content
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(6)

        self.hint_label = QLabel("Pick Length or Area in the toolbar, then draw on the image.")
        self.hint_label.setObjectName("SolarHintLabel")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        # Corrected for the Sun's curvature, or as the image shows it. Affects
        # what is displayed (result, table, graph); the CSV always has both.
        values_row = QHBoxLayout()
        values_row.setSpacing(6)
        self.values_label = _field_label("Values")
        values_row.addWidget(self.values_label)
        self.curvature_combo = QComboBox()
        self.curvature_combo.addItem("Corrected for curvature", "corrected")
        self.curvature_combo.addItem("As seen (uncorrected)", "as_seen")
        self.curvature_combo.setToolTip(
            "Corrected for curvature: the true size on the solar surface. Each pixel is\n"
            "put back on the sphere, so a feature near the limb measures what it would\n"
            "at disk centre (lengths along the surface, areas in MSH). This is how\n"
            "sunspot catalogues report areas.\n"
            "As seen (uncorrected): the size exactly as it appears on the image, with no\n"
            "correction for foreshortening (lengths in the plane of the sky, areas still\n"
            "in MSH). Smaller than the true size, increasingly so towards the limb.\n"
            "The CSV export always contains both."
        )
        self.curvature_combo.currentIndexChanged.connect(lambda _i: self.curvatureChanged.emit())
        values_row.addWidget(self.curvature_combo, 1)
        layout.addLayout(values_row)

        # --- drawing: shape (Area only) and the in-progress line/polygon buttons
        self.draw_section = QWidget()
        draw = QVBoxLayout(self.draw_section)
        draw.setContentsMargins(0, 0, 0, 0)
        draw.setSpacing(6)
        self.shape_label = _field_label("Shape")
        self.shape_combo = QComboBox()
        for key in SHAPES:
            self.shape_combo.addItem(SHAPE_LABELS[key], key)
        self.shape_combo.setToolTip(
            "How the Area tool draws its region:\n"
            "  Polygon — click the corners, double-click (or Finish) to close.\n"
            "  Freehand lasso — drag around the feature.\n"
            "  Ellipse / Rectangle — drag from one corner to the opposite one."
        )
        self.shape_combo.currentIndexChanged.connect(lambda _i: self.shapeChanged.emit(self.shape()))
        shape_row = QHBoxLayout()
        shape_row.setSpacing(6)
        shape_row.addWidget(self.shape_label)
        shape_row.addWidget(self.shape_combo, 1)
        draw.addLayout(shape_row)
        self.undo_btn = QPushButton("Undo Point")
        self.undo_btn.setToolTip("Remove the last clicked point of the line or polygon being drawn.")
        self.finish_btn = QPushButton("Finish")
        self.finish_btn.setToolTip("Finish the line or polygon being drawn (or double-click the last point).")
        self.clear_shape_btn = QPushButton("Clear Shape")
        self.clear_shape_btn.setToolTip("Remove the shape of the active tool.")
        draw_buttons = QHBoxLayout()
        draw_buttons.setSpacing(6)
        for btn in (self.undo_btn, self.finish_btn, self.clear_shape_btn):
            draw_buttons.addWidget(btn, 1)
        draw.addLayout(draw_buttons)
        layout.addWidget(self.draw_section)

        # --- threshold (Area only): a subheading and field rows, as in the sidebar
        self.threshold_group = QWidget()
        self._threshold_tip = (
            "Measure only the pixels inside the shape that pass a threshold. Intensities\n"
            "are divided by the quiet-Sun level at the same distance from disk centre\n"
            "(fitted over the whole disk, so limb darkening or brightening cancels), and\n"
            "off-limb pixels by the level at the limb. Always uses the frame's own data,\n"
            "even when a difference image is displayed.\n"
            "Sunspot mode is the standard intensity-threshold method: a patch counts only if\n"
            "it is a real depression (its darkest pixel at least 0.10 below the penumbra\n"
            "level), so granulation is never counted. Dark thresholds ignore the sky and the\n"
            "outermost limb. Areas are corrected for foreshortening, in MSH. They read about\n"
            "1.4x NOAA/USAF region areas, which are known to be low (Meadows 2020, MNRAS)."
        )
        self.threshold_group.setToolTip(self._threshold_tip)
        grid = QGridLayout(self.threshold_group)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        grid.addWidget(_subheading("Threshold · fraction of quiet Sun"), 0, 0, 1, 4)
        self.threshold_combo = QComboBox()
        for key in THRESHOLDS:
            self.threshold_combo.addItem(THRESHOLD_LABELS[key], key)
        self.select_label = _field_label("Select")
        grid.addWidget(self.select_label, 1, 0)
        grid.addWidget(self.threshold_combo, 1, 1, 1, 3)
        self.umbra_spin = self._level_spin(DEFAULT_UMBRA_LEVEL, "Umbra: pixels below this fraction of the quiet Sun.")
        self.penumbra_spin = self._level_spin(
            DEFAULT_PENUMBRA_LEVEL, "Penumbra: pixels between the umbra level and this one."
        )
        self.umbra_label = _field_label("Umbra <")
        self.penumbra_label = _field_label("Penumbra <")
        self.level_spin = self._level_spin(DEFAULT_DARK_LEVEL, "Threshold as a fraction (or multiple) of the quiet Sun.")
        self.level_spin.setRange(0.01, 100.0)
        self.level_label = _field_label("Level")
        # Sunspot levels and the dark/bright level share a row; only one set shows.
        grid.addWidget(self.umbra_label, 2, 0)
        grid.addWidget(self.umbra_spin, 2, 1)
        grid.addWidget(self.penumbra_label, 2, 2)
        grid.addWidget(self.penumbra_spin, 2, 3)
        grid.addWidget(self.level_label, 2, 0)
        grid.addWidget(self.level_spin, 2, 1)
        self.cluster_spin = QSpinBox()
        self.cluster_spin.setRange(0, 100_000)
        self.cluster_spin.setValue(DEFAULT_MIN_CLUSTER_PX)
        self.cluster_spin.setSuffix(" px")
        self.cluster_spin.setToolTip("Drop selected patches smaller than this (isolated noise pixels).")
        self.cluster_label = _field_label("Min patch")
        self.cluster_label.setToolTip(self.cluster_spin.toolTip())
        self.tint_check = QCheckBox("Tint pixels")
        self.tint_check.setToolTip("Tint the selected pixels on the image.")
        self.tint_check.setChecked(True)
        grid.addWidget(self.cluster_label, 3, 0)
        grid.addWidget(self.cluster_spin, 3, 1)
        grid.addWidget(self.tint_check, 3, 2, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addWidget(self.threshold_group)

        # --- area method (Area only): plan sections 4, 5.2 and 6.2
        self.area_method_group = QWidget()
        method_grid = QGridLayout(self.area_method_group)
        method_grid.setContentsMargins(0, 0, 0, 0)
        method_grid.setHorizontalSpacing(6)
        method_grid.setVerticalSpacing(6)
        method_grid.addWidget(_subheading("Area method"), 0, 0, 1, 4)
        self.geometry_label = _field_label("Geometry")
        self.geometry_combo = QComboBox()
        self.geometry_combo.addItem("Finite-distance (default)", "finite_distance")
        self.geometry_combo.addItem("Orthographic (comparison)", "orthographic")
        self.geometry_combo.setToolTip(
            "Finite-distance: every pixel's line of sight is followed from the spacecraft\n"
            "to the spherical photosphere (D from DSUN_OBS, R from RSUN_REF), and its area\n"
            "is d²·ΔΩ/μ. This is the correct method and the default.\n"
            "Orthographic: the old parallel-ray approximation (pixel area R²/R_p² ÷ μ),\n"
            "kept only for comparison."
        )
        self.cutoff_label = _field_label("Limb cutoff")
        self.cutoff_spin = QDoubleSpinBox()
        self.cutoff_spin.setRange(0.0, 0.9)
        self.cutoff_spin.setDecimals(2)
        self.cutoff_spin.setSingleStep(0.05)
        self.cutoff_spin.setPrefix("μ ≥ ")
        self.cutoff_spin.setValue(DEFAULT_MU_MIN)
        self.cutoff_spin.setToolTip(
            "Pixels closer to the limb than this (μ = cos of the viewing angle) are not\n"
            "integrated; they are counted, tinted red and the result is marked PARTIAL.\n"
            "0.30 is ~72.5° from disc centre, at most ×3.33 correction. A quality limit,\n"
            "not the edge of the disc; lower it only with care."
        )
        self.boundary_label = _field_label("Boundary")
        self.boundary_combo = QComboBox()
        self.boundary_combo.addItem("Pixel centres", 1)
        self.boundary_combo.addItem("Subpixel 4×4 (slower)", 4)
        self.boundary_combo.setToolTip(
            "Pixel centres: a pixel counts if its centre is inside the shape.\n"
            "Subpixel 4×4: boundary pixels count by how much of them the shape covers,\n"
            "and the geometry is integrated per subpixel. Better for small spots and\n"
            "irregular outlines; slower on very large shapes."
        )
        method_grid.addWidget(self.geometry_label, 1, 0)
        method_grid.addWidget(self.geometry_combo, 1, 1, 1, 3)
        method_grid.addWidget(self.cutoff_label, 2, 0)
        method_grid.addWidget(self.cutoff_spin, 2, 1)
        method_grid.addWidget(self.boundary_label, 2, 2)
        method_grid.addWidget(self.boundary_combo, 2, 3)
        method_grid.setColumnStretch(1, 1)
        method_grid.setColumnStretch(3, 1)
        layout.addWidget(self.area_method_group)
        for widget in (self.geometry_combo, self.boundary_combo):
            widget.currentIndexChanged.connect(lambda _i: self.areaSettingsChanged.emit())
        self.cutoff_spin.valueChanged.connect(lambda _v: self.areaSettingsChanged.emit())

        # --- live result, in a card like the Details panel; hidden when empty
        self.result_label = QLabel("")
        self.result_label.setObjectName("SolarSurfaceResult")
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.RichText)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.result_label.setVisible(False)
        self.result_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(self.result_label)

        # --- recorded measurements. A finished shape adds a row; Record adds
        # the shape's measurement on the shown frame under the same name, which
        # is how one feature is followed through a sequence.
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        self.table.setColumnWidth(0, 96)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        # Only the name is editable (double-click it).
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setMinimumWidth(300)
        self.table.setMinimumHeight(110)
        self.table.setToolTip(
            "Recorded measurements. Click a row to go back to its frame and shape;\n"
            "double-click a name to rename the feature; right-click for more. Rows\n"
            "that share a name are the same feature on different frames."
        )
        layout.addWidget(self.table, 1)

        # The one control row between table and graph, as "Fit:" is next door.
        self.rotate_check = QCheckBox("Rotate shapes with the Sun when tracking")
        self.rotate_check.setChecked(True)
        self.rotate_check.setToolTip(
            "Track Frames carries the shape across frames with solar differential\n"
            "rotation (Howard et al. 1990, as SunPy), so it stays on a sunspot or\n"
            "filament as the disk turns. Off-limb corners stay put. Untick to measure\n"
            "the same sky position on every frame, e.g. for an arcade at the limb."
        )
        layout.addWidget(self.rotate_check)

        # Time series of the selected feature. Software viewport for the same
        # reason as the tracking panel's graph: a GL view created hidden in a
        # tab renders black on some drivers.
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        try:
            self.plot.useOpenGL(False)
        except Exception:
            pass
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Time (UT)")
        self.plot.setLabel("left", "Area (MSH)")
        self.plot.setMinimumHeight(110)
        self.plot.getViewBox().setMouseEnabled(x=False, y=False)
        # Same orange / blue as the tracking graph's points and fit.
        self._series_main = pg.PlotDataItem(
            pen=pg.mkPen("#e8a33d", width=1.6), symbol="o", symbolSize=8,
            symbolBrush="#e8a33d", symbolPen=pg.mkPen("#e8a33d"),
        )
        self._series_second = pg.PlotDataItem(
            pen=pg.mkPen("#5a8fd6", width=1.4), symbol="s", symbolSize=7,
            symbolBrush="#5a8fd6", symbolPen=pg.mkPen("#5a8fd6"),
        )
        self._series_fit = pg.PlotCurveItem(pen=pg.mkPen("#5a8fd6", width=1.6, style=Qt.DashLine))
        for item in (self._series_fit, self._series_main, self._series_second):
            self.plot.addItem(item)
        self.apply_theme(dark=self._detect_dark())
        layout.addWidget(self.plot, 1)

        self.series_label = QLabel("Select a row to plot its feature over time.")
        self.series_label.setWordWrap(True)
        layout.addWidget(self.series_label)

        # --- one row of three buttons, like Fit / Clear / Export next door
        buttons = QHBoxLayout()
        self.record_btn = QPushButton("Record")
        self.record_btn.setEnabled(False)
        self.record_btn.setToolTip(
            "Add the current result to the table (and the Details panel). On the\n"
            "frame where the shape was drawn this updates its row; on another frame\n"
            "it adds a row for the same feature."
        )
        self.track_btn = QPushButton("Track Frames")
        self.track_btn.setEnabled(False)
        self.track_btn.setToolTip(
            "Measure the selected row's feature on every loaded frame, with the same\n"
            "shape and threshold, and plot it against time. Rows already recorded for\n"
            "this feature on other frames are replaced."
        )
        # Table actions live in one menu (and the table's right-click menu), so
        # the row stays three buttons wide.
        self.table_btn = QPushButton("Table")
        self.table_btn.setEnabled(False)
        self.table_btn.setToolTip("Export the table as CSV, delete the selected row, or clear the table.")
        table_menu = QMenu(self.table_btn)
        self.export_action = table_menu.addAction("Export Table as CSV…")
        self.export_action.setToolTip("Every recorded measurement, with its shape and settings.")
        self.export_json_action = table_menu.addAction("Export with Provenance (JSON)…")
        self.export_json_action.setToolTip(
            "Full provenance: geometry (R, D, RSUN_OBS), method, cutoff, counts by exclusion\n"
            "cause, thresholds, ROI vertices, source file and WCS summary."
        )
        table_menu.addSeparator()
        self.delete_row_action = table_menu.addAction("Delete Selected Row")
        self.delete_row_action.setShortcut(QKeySequence.Delete)
        self.delete_row_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.clear_table_action = table_menu.addAction("Clear Table…")
        self.table_btn.setMenu(table_menu)
        self._table_menu = table_menu
        for action in (self.export_action, self.export_json_action, self.delete_row_action, self.clear_table_action):
            action.setEnabled(False)
        self.table.setContextMenuPolicy(Qt.ActionsContextMenu)
        self.table.addActions([self.export_action, self.export_json_action, self.delete_row_action, self.clear_table_action])
        for btn in (self.record_btn, self.track_btn, self.table_btn):
            buttons.addWidget(btn)
        layout.addLayout(buttons)

        self._quicklook = False
        self._last_mode = "none"
        self.threshold_combo.currentIndexChanged.connect(self._on_threshold_mode_changed)
        for spin in (self.umbra_spin, self.penumbra_spin, self.level_spin):
            spin.valueChanged.connect(lambda _v: self.thresholdChanged.emit())
        self.cluster_spin.valueChanged.connect(lambda _v: self.thresholdChanged.emit())
        self.tint_check.toggled.connect(lambda _on: self.thresholdChanged.emit())
        self.table.itemSelectionChanged.connect(self._on_table_selection)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self._sync_threshold_rows()
        self.set_tool(None)
        # One caption column for every row, so the fields start in one line.
        captions = (
            self.values_label, self.shape_label, self.select_label, self.umbra_label, self.level_label,
            self.cluster_label, self.geometry_label, self.cutoff_label,
        )
        width = max(label.sizeHint().width() for label in captions)
        for label in captions:
            label.setFixedWidth(width)
        # Only height may scroll: reserve the content's full width (plus the
        # scrollbar) so nothing is ever clipped at the right edge. The content
        # widens as the table fills, so this is re-measured on every relayout.
        self._content.installEventFilter(self)
        self._fit_scroll_width()

    def _fit_scroll_width(self) -> None:
        scrollbar = self.scroll_area.verticalScrollBar().sizeHint().width()
        needed = self._content.minimumSizeHint().width() + scrollbar + 2
        if self.scroll_area.minimumWidth() != needed:
            self.scroll_area.setMinimumWidth(needed)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if watched is getattr(self, "_content", None) and event.type() == QEvent.LayoutRequest:
            self._fit_scroll_width()
        return super().eventFilter(watched, event)

    @staticmethod
    def _detect_dark() -> bool:
        """Match the app theme from the palette (as the tracking panel does)."""
        app = QApplication.instance()
        if app is None:
            return False
        try:
            return app.palette().window().color().lightness() < 128
        except Exception:
            return False

    def apply_theme(self, *, dark: bool) -> None:
        """Light/dark plot colours, so the graph never shows pyqtgraph's black default."""
        background = (12, 12, 12) if dark else (250, 252, 255)
        foreground = (220, 224, 228) if dark else (40, 44, 52)
        self.plot.setBackground(background)
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(foreground))
            axis.setTextPen(pg.mkPen(foreground))
        self._plot_foreground = foreground

    @staticmethod
    def _level_spin(value: float, tip: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.01, 2.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.05)
        spin.setValue(value)
        spin.setToolTip(tip)
        return spin

    # ----------------------------------------------------------------- state
    def shape(self) -> str:
        return str(self.shape_combo.currentData() or "polygon")

    def set_shape(self, shape: str) -> None:
        index = self.shape_combo.findData(shape)
        if index >= 0:
            self.shape_combo.setCurrentIndex(index)

    def threshold_settings(self) -> ThresholdSettings:
        mode = str(self.threshold_combo.currentData() or "none")
        if self._quicklook:
            mode = "none"
        return ThresholdSettings(
            mode=mode,
            umbra_level=float(self.umbra_spin.value()),
            penumbra_level=float(self.penumbra_spin.value()),
            level=float(self.level_spin.value()),
            min_cluster_px=int(self.cluster_spin.value()),
        )

    def set_threshold_mode(self, mode: str) -> None:
        index = self.threshold_combo.findData(mode)
        if index >= 0:
            self.threshold_combo.setCurrentIndex(index)

    def tint_enabled(self) -> bool:
        return self.tint_check.isChecked()

    def area_settings(self) -> AreaSettings:
        return AreaSettings(
            method=str(self.geometry_combo.currentData() or "finite_distance"),
            mu_min=float(self.cutoff_spin.value()),
            subsamples=int(self.boundary_combo.currentData() or 1),
        )

    def apply_area_settings(self, settings: dict[str, Any]) -> None:
        """Set the area-method controls without a change signal per widget."""
        widgets = (self.geometry_combo, self.cutoff_spin, self.boundary_combo)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            index = self.geometry_combo.findData(str(settings.get("method", "finite_distance")))
            if index >= 0:
                self.geometry_combo.setCurrentIndex(index)
            if settings.get("mu_min") is not None:
                self.cutoff_spin.setValue(float(settings["mu_min"]))
            index = self.boundary_combo.findData(int(settings.get("subsamples", 1) or 1))
            if index >= 0:
                self.boundary_combo.setCurrentIndex(index)
        finally:
            for widget in widgets:
                widget.blockSignals(False)

    def corrected(self) -> bool:
        """Whether values are shown corrected for curvature (else as seen)."""
        return str(self.curvature_combo.currentData() or "corrected") == "corrected"

    def set_corrected(self, corrected: bool) -> None:
        self.curvature_combo.setCurrentIndex(self.curvature_combo.findData("corrected" if corrected else "as_seen"))

    def set_quicklook(self, quicklook: bool) -> None:
        """JP2 quicklook pixels are display bytes: no thresholds, no intensities."""
        self._quicklook = bool(quicklook)
        self.threshold_group.setEnabled(not self._quicklook)
        self.threshold_group.setToolTip(
            "Not available for JP2 quicklook frames: their pixels are 8-bit display "
            "values, not calibrated data. Load FITS to threshold."
            if self._quicklook
            else self._threshold_tip
        )

    def set_tool(self, mode: str | None) -> None:
        """Adapt the controls to the active tool (``"length"``, ``"area"`` or None)."""
        area = mode == "area"
        # Only what the active tool uses is shown; with no tool the tab is just
        # the table and its graph, like the tracking tab.
        self.draw_section.setVisible(mode in ("length", "area"))
        self.shape_label.setVisible(area)
        self.shape_combo.setVisible(area)
        self.threshold_group.setVisible(area)
        self.area_method_group.setVisible(area)
        if not self._quicklook:
            self.threshold_group.setEnabled(True)
        if mode == "length":
            self.hint_label.setText(
                "Length: click along the feature (a filament, say), then double-click the last "
                "point or press Finish. Each segment is followed over the solar surface."
            )
        elif area:
            self.hint_label.setText(self._area_hint(self.shape()))
        else:
            self.hint_label.setText("Pick Length or Area in the toolbar, then draw on the image.")

    @staticmethod
    def _area_hint(shape: str) -> str:
        if shape == "polygon":
            return "Area: click the corners of the region, then double-click or press Finish to close it."
        if shape == "lasso":
            return "Area: drag around the feature; releasing the mouse closes the outline."
        return f"Area: drag from one corner of the {shape} to the opposite corner."

    def set_drawing_state(self, *, can_undo: bool, can_finish: bool, has_shape: bool) -> None:
        self.undo_btn.setEnabled(can_undo)
        self.finish_btn.setEnabled(can_finish)
        self.clear_shape_btn.setEnabled(has_shape)

    def show_result(self, text: str, *, recordable: bool) -> None:
        """Live result card: the first line is its heading, a ``●`` line a notice."""
        lines = [line for line in str(text or "").splitlines() if line.strip()]
        if not lines:
            self.result_label.clear()
            self.result_label.setVisible(False)
        else:
            parts = []
            for i, line in enumerate(lines):
                safe = html.escape(line)
                if line.startswith("●"):
                    parts.append(f"<b>{safe}</b>")
                elif i == 0 or (i == 1 and lines[0].startswith("●")):
                    parts.append(f"<span style='font-weight:600'>{safe}</span>")
                else:
                    parts.append(safe)
            self.result_label.setText("<br>".join(parts))
            self.result_label.setVisible(True)
        self.record_btn.setEnabled(recordable)

    def result_text(self) -> str:
        """The live result as plain text (what the card shows)."""
        return self.result_label.text() and _plain(self.result_label.text())

    def apply_threshold_settings(self, settings: dict[str, Any]) -> None:
        """Set the threshold controls without emitting a change per widget."""
        widgets = (
            self.threshold_combo,
            self.umbra_spin,
            self.penumbra_spin,
            self.level_spin,
            self.cluster_spin,
        )
        for widget in widgets:
            widget.blockSignals(True)
        try:
            index = self.threshold_combo.findData(str(settings.get("mode", "none")))
            if index >= 0:
                self.threshold_combo.setCurrentIndex(index)
            self._last_mode = str(settings.get("mode", "none"))
            for spin, key in (
                (self.umbra_spin, "umbra_level"),
                (self.penumbra_spin, "penumbra_level"),
                (self.level_spin, "level"),
            ):
                if settings.get(key) is not None:
                    spin.setValue(float(settings[key]))
            if settings.get("min_cluster_px") is not None:
                self.cluster_spin.setValue(int(settings["min_cluster_px"]))
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self._sync_threshold_rows()

    def set_shape_silently(self, shape: str) -> None:
        self.shape_combo.blockSignals(True)
        self.set_shape(shape)
        self.shape_combo.blockSignals(False)

    def show_records(self, records: list[SurfaceRecord], active: int | None) -> None:
        """Refill the table; ``active`` is the selected row."""
        corrected = self.corrected()
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(records))
            for row, record in enumerate(records):
                when = record.time.strftime("%H:%M:%S") if record.time else f"frame {record.frame_index + 1}"
                v = record.values
                if record.kind == "length":
                    centroid = format_lonlat(
                        (v["start_lon_deg"], v["start_lat_deg"])
                        if v.get("start_lon_deg") is not None and v.get("start_lat_deg") is not None
                        else None
                    )
                    mu = v.get("min_mu")
                else:
                    centroid = format_lonlat(
                        (v["centroid_lon_deg"], v["centroid_lat_deg"])
                        if v.get("centroid_lon_deg") is not None and v.get("centroid_lat_deg") is not None
                        else None
                    )
                    mu = v.get("mean_mu")
                status = str(record.values.get("status", "ok"))
                result_text = record.headline_for(corrected=corrected)
                if status == "partial":
                    result_text += " ⚠"
                elif status == "invalid":
                    result_text = "invalid"
                cells = (
                    record.name,
                    when,
                    record.measure_label,
                    result_text,
                    "—" if mu is None else f"{float(mu):.2f}",
                )
                where = "start" if record.kind == "length" else "centroid"
                tooltip = f"{record.line}\n{where.capitalize()}: {centroid}"
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(str(text))
                    flags = item.flags()
                    if col != 0:
                        flags &= ~Qt.ItemIsEditable
                    item.setFlags(flags)
                    item.setToolTip(tooltip)
                    self.table.setItem(row, col, item)
            if active is not None and 0 <= active < len(records):
                self.table.selectRow(active)
            else:
                self.table.clearSelection()
        finally:
            self.table.blockSignals(False)
        has_rows = bool(records)
        self.table_btn.setEnabled(has_rows)
        self.export_action.setEnabled(has_rows)
        self.export_json_action.setEnabled(has_rows)
        self.clear_table_action.setEnabled(has_rows)
        self.delete_row_action.setEnabled(has_rows and active is not None)
        self.track_btn.setEnabled(has_rows and active is not None)

    def show_series(
        self,
        title: str,
        times: list[float],
        values: list[float],
        *,
        y_label: str,
        second: list[float] | None = None,
        second_label: str = "",
        fit: tuple[list[float], list[float]] | None = None,
        text: str = "",
    ) -> None:
        """Plot one feature against UTC time (``times`` in Unix seconds)."""
        self.plot.setLabel("left", y_label)
        self._series_main.setData(times, values)
        if second is not None:
            self._series_second.setData(times, second)
        else:
            self._series_second.setData([], [])
        if fit is not None:
            self._series_fit.setData(*fit)
        else:
            self._series_fit.setData([], [])
        self.plot.enableAutoRange()
        legend = f"  ·  blue: {second_label}" if second is not None and values else ""
        self.series_label.setText(text + legend)

    def clear_series(self, text: str) -> None:
        for item in (self._series_main, self._series_second):
            item.setData([], [])
        self._series_fit.setData([], [])
        self.series_label.setText(text)

    def selected_row(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return int(rows[0].row()) if rows else None

    def _on_table_selection(self) -> None:
        row = self.selected_row()
        self.delete_row_action.setEnabled(row is not None)
        if row is not None:
            self.recordSelected.emit(row)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.recordRenamed.emit(item.row(), item.text())

    def _on_threshold_mode_changed(self, _index: int) -> None:
        mode = str(self.threshold_combo.currentData() or "none")
        # Each mode wants a different default level; keep the user's value when
        # it already suits the mode.
        if mode == "dark" and self._last_mode != "dark" and self.level_spin.value() > 1.0:
            self.level_spin.blockSignals(True)
            self.level_spin.setValue(DEFAULT_DARK_LEVEL)
            self.level_spin.blockSignals(False)
        elif mode == "bright" and self._last_mode != "bright" and self.level_spin.value() <= 1.0:
            self.level_spin.blockSignals(True)
            self.level_spin.setValue(DEFAULT_BRIGHT_LEVEL)
            self.level_spin.blockSignals(False)
        self._last_mode = mode
        self._sync_threshold_rows()
        self.thresholdChanged.emit()

    def _sync_threshold_rows(self) -> None:
        mode = str(self.threshold_combo.currentData() or "none")
        sunspot = mode == "sunspot"
        level = mode in ("dark", "bright")
        for widget in (self.umbra_label, self.umbra_spin, self.penumbra_label, self.penumbra_spin):
            widget.setVisible(sunspot)
        for widget in (self.level_label, self.level_spin):
            widget.setVisible(level)
        self.level_label.setText("Below:" if mode == "dark" else "At least:")
        self.level_spin.setSuffix(" × I_qs")
        self.cluster_spin.setEnabled(mode != "none")
        self.cluster_label.setEnabled(mode != "none")
        # The tint also shows a whole-region measurement: measured vs excluded pixels.
        self.tint_check.setEnabled(True)


# ------------------------------------------------------------------------ tool
class SurfaceTool:
    """Drawing and measuring behind the Length and Area modes.

    One shape per tool is kept (``shapes["length"]`` / ``shapes["area"]``); a
    new one replaces it. ``drawing`` is the shape under construction.
    """

    def __init__(self, controller: Any):
        self.controller = controller
        self.window = controller.window
        self.shapes: dict[str, SurfaceShape] = {}
        self.drawing: SurfaceShape | None = None
        self.last_result: SurfaceLength | AreaResult | None = None
        self._last_geometry: SurfaceGeometry | None = None
        self._profile_cache: dict[tuple[int, int], QuietSunProfile] = {}
        self._dragging = False
        # Recorded measurements (the Surface tab's table).
        self.records: list[SurfaceRecord] = []
        #: Row the current shape belongs to; threshold edits update it in place.
        self.active: int | None = None
        self._active_shape: SurfaceShape | None = None
        self._name_counts: dict[str, int] = {}

    # ---------------------------------------------------------------- state
    @property
    def mode(self) -> str | None:
        mode = getattr(self.controller, "mode", None)
        return mode if mode in ("length", "area") else None

    def _panel(self) -> SurfaceMeasurePanel | None:
        return getattr(self.window, "surface_panel", None)

    def _new_kind(self) -> str:
        if self.mode == "length":
            return "length"
        panel = self._panel()
        return panel.shape() if panel is not None else "polygon"

    def activate(self, mode: str | None) -> None:
        """The controller switched modes."""
        self.drawing = None
        self._dragging = False
        canvas = self._canvas()
        if canvas is not None:
            canvas.set_drag_callback(self.on_drag if mode == "area" else None)
            canvas.set_double_click_callback(self.on_double_click if mode in ("length", "area") else None)
        panel = self._panel()
        if panel is not None:
            panel.set_tool(mode)
        if mode in ("length", "area"):
            self.remeasure(record=False)
        self.refresh_overlay()
        self._sync_panel()

    def shape_changed(self) -> None:
        """The panel's area shape changed: drop a half-drawn shape."""
        if self.mode == "area":
            self.drawing = None
            panel = self._panel()
            if panel is not None:
                panel.set_tool("area")
            self.refresh_overlay()
            self._sync_panel()

    # -------------------------------------------------------------- input
    def on_click(self, x_arc: float, y_arc: float) -> None:
        kind = self._new_kind()
        if kind in DRAG_SHAPES:
            self.controller._status(SurfaceMeasurePanel._area_hint(kind))
            return
        if self.drawing is None or self.drawing.kind != kind:
            self.drawing = SurfaceShape(kind=kind)
        self.drawing.points.append((float(x_arc), float(y_arc)))
        self.refresh_overlay()
        self._sync_panel()
        if kind == "length" and len(self.drawing.points) >= 2:
            try:
                running = polyline_surface_length(self.drawing.points, self._geometry())
                self.controller._status(
                    f"Length so far: {_mm(running.total_km)} ({len(self.drawing.points)} points) — "
                    "double-click or Finish to end."
                )
            except Exception:
                pass
        elif kind == "polygon":
            self.controller._status(
                f"Polygon: {len(self.drawing.points)} corner(s) — double-click or Finish to close."
            )

    def on_double_click(self, x_arc: float, y_arc: float) -> None:
        if self.mode is None:
            return
        if self.drawing is None or self.drawing.kind in DRAG_SHAPES:
            return
        # The first click of the double click already placed this point.
        self.finish()

    def on_drag(self, phase: str, x_arc: float, y_arc: float) -> bool:
        """Canvas drag hook; returns whether the drag is taken (on ``start``)."""
        if self.mode != "area":
            return False
        kind = self._new_kind()
        if kind not in DRAG_SHAPES:
            return False
        if getattr(self.window, "_current_map_data", None) is None:
            return False
        point = (float(x_arc), float(y_arc))
        if phase == "start":
            self.drawing = SurfaceShape(kind=kind, points=[point])
            self._dragging = True
            return True
        if not self._dragging or self.drawing is None:
            return False
        if kind == "lasso":
            last = self.drawing.points[-1]
            step = _LASSO_MIN_STEP_RSUN * self._rsun_arcsec()
            if math.hypot(point[0] - last[0], point[1] - last[1]) >= step or phase == "finish":
                self.drawing.points.append(point)
        else:
            self.drawing.points = [self.drawing.points[0], point]
        if phase == "finish":
            self._dragging = False
            self.finish()
        else:
            self.refresh_overlay()
        return True

    def undo_point(self) -> None:
        if self.drawing is None or not self.drawing.points:
            return
        self.drawing.points.pop()
        if not self.drawing.points:
            self.drawing = None
        self.refresh_overlay()
        self._sync_panel()

    def cancel(self) -> None:
        """Esc / right click: drop the shape being drawn, keep the finished one."""
        self.drawing = None
        self._dragging = False
        self.refresh_overlay()
        self._sync_panel()

    def clear_shape(self) -> None:
        mode = self.mode
        self.drawing = None
        if mode is not None:
            self.shapes.pop(mode, None)
        self.last_result = None
        self.refresh_overlay()
        self._show_result("", recordable=False)
        self._sync_panel()

    def clear(self) -> None:
        self.shapes.clear()
        self.drawing = None
        self._dragging = False
        self.last_result = None
        self._profile_cache.clear()
        self._show_result("", recordable=False)
        canvas = self._canvas()
        if canvas is not None:
            canvas.clear_surface_mask_overlay()
        self.clear_records()
        self._sync_panel()

    def finish(self) -> None:
        shape = self.drawing
        if shape is None:
            return
        if not shape.is_complete():
            need = "two points" if shape.kind == "length" else "three corners"
            if shape.kind in ("ellipse", "rectangle"):
                self.drawing = None
                self.refresh_overlay()
                self._sync_panel()
                self.controller._status("Drag a larger shape.")
                return
            self.controller._status(f"Need at least {need}.")
            return
        self.drawing = None
        self.shapes["length" if shape.kind == "length" else "area"] = shape
        self.remeasure(record=True, new_feature=True)
        self.refresh_overlay()
        self._sync_panel()

    # ------------------------------------------------------------ measuring
    def on_frame_changed(self) -> None:
        """Re-measure the finished shape on the newly shown frame (not during playback)."""
        if self.mode is None:
            return
        timer = getattr(self.window, "_play_timer", None)
        self._follow_feature_onto_frame()
        if timer is not None and timer.isActive():
            self.refresh_overlay()
            return
        self.remeasure(record=False)
        self.refresh_overlay()

    def area_settings_changed(self) -> None:
        if self.mode == "area" and "area" in self.shapes:
            self.remeasure(record=False)
            self.refresh_overlay()

    def threshold_changed(self) -> None:
        # Only the live result follows the settings: the table changes on an
        # explicit Record, so adjusting the threshold for the next shape can
        # never rewrite a row already recorded.
        if self.mode == "area" and "area" in self.shapes:
            self.remeasure(record=False)
            self.refresh_overlay()

    def record(self) -> None:
        """Record button: update the shape's own row, or add one on this frame."""
        result = self.last_result
        mode = self.mode
        shape = self.shapes.get(mode) if mode is not None else None
        if result is None or shape is None:
            return
        if self._active_updatable(shape):
            self._update_active(result, shape)
            verb = "updated"
        else:
            name = None
            if self.active is not None and self._active_shape is shape:
                name = self.records[self.active].name  # same feature, another frame
            self._append_record(result, shape, name=name)
            verb = "added"
        self.controller._append_analysis(self.records[self.active].line)
        self.controller._status(f"{self.records[self.active].name}: row {verb}.")
        self._show_result(self._panel_text(result), recordable=True)

    # -------------------------------------------------------------- records
    def _active_updatable(self, shape: SurfaceShape) -> bool:
        """Whether the active row is this shape's measurement on this frame."""
        if self.active is None or not 0 <= self.active < len(self.records):
            return False
        record = self.records[self.active]
        return self._active_shape is shape and record.frame_index == self._frame_index()

    def _frame_index(self) -> int:
        return int(getattr(self.window, "_current_frame_index", 0))

    def _new_name(self, result: SurfaceLength | AreaResult) -> str:
        if isinstance(result, SurfaceLength):
            label = "Length"
        elif result.threshold.mode == "sunspot":
            label = "Sunspot"
        else:
            label = "Area"
        count = self._name_counts.get(label, 0) + 1
        taken = {r.name for r in self.records}
        while f"{label} {count}" in taken:
            count += 1
        self._name_counts[label] = count
        return f"{label} {count}"

    def _build_record(
        self,
        result: SurfaceLength | AreaResult,
        shape: SurfaceShape,
        name: str,
        *,
        frame_index: int | None = None,
        geometry: SurfaceGeometry | None = None,
    ) -> SurfaceRecord:
        index = self._frame_index() if frame_index is None else int(frame_index)
        geometry = self._last_geometry if geometry is None else geometry
        if isinstance(result, SurfaceLength):
            values = length_values(result, geometry)
            threshold: dict[str, Any] = {}
        else:
            values = area_values(result, geometry)
            threshold = asdict(result.threshold)
        values.update(self._frame_provenance(index))
        return SurfaceRecord(
            name=name,
            kind="length" if shape.kind == "length" else "area",
            shape=shape.kind,
            points=list(shape.points),
            frame_index=index,
            time=self._frame_time(index),
            threshold=threshold,
            values=values,
            summary=self._format(result),
        )

    def _frame_provenance(self, index: int) -> dict[str, Any]:
        """Source file, series, time and WCS summary of a frame, for export."""
        win = self.window
        frames = getattr(win, "_map_frames", None) or []
        out: dict[str, Any] = {}
        paths = list(getattr(win, "_loaded_paths", None) or [])
        if 0 <= index < len(paths):
            out["source_file"] = os.path.basename(str(paths[index]))
        if 0 <= index < len(frames):
            meta = dict(getattr(frames[index], "meta", None) or {})
            lower = {str(k).lower(): v for k, v in meta.items()}
            for key in ("telescop", "instrume", "content", "t_obs", "t_rec", "date-obs", "ctype1", "ctype2",
                        "crpix1", "crpix2", "crval1", "crval2", "cdelt1", "cdelt2", "crota2"):
                value = lower.get(key)
                if value is not None:
                    out[f"hdr_{key.replace('-', '_')}"] = value if isinstance(value, (int, float, str)) else str(value)
        north_up = getattr(win, "north_up_check", None)
        if north_up is not None:
            out["north_up"] = bool(north_up.isChecked())
        out["algorithm"] = "surface_measure finite-distance WCS integration (plan 2026-11-19)"
        return out

    def export_json(self, path: str | None = None) -> str | None:
        if not self.records:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self.window, "Export Surface Measurements (JSON)", "surface_measurements.json", "JSON files (*.json)"
            )
            if not path:
                return None
            if not path.lower().endswith(".json"):
                path += ".json"
        try:
            count = write_json(path, self.records, provenance={"application": "e-CALLISTO FITS Analyzer"})
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.window, "Export Surface Measurements", f"Could not write the file:\n{exc}")
            return None
        self.controller._status(f"Exported {count} measurement(s) with provenance to {path}.")
        return path

    def _frame_time(self, index: int):
        frames = getattr(self.window, "_map_frames", None) or []
        if not 0 <= index < len(frames):
            return None
        try:
            return frame_observation_time(frames[index])
        except Exception:
            return None

    def _append_record(self, result, shape: SurfaceShape, *, name: str | None = None) -> None:
        record = self._build_record(result, shape, name or self._new_name(result))
        self.records.append(record)
        self.active = len(self.records) - 1
        self._active_shape = shape
        self._show_records()
        self.controller._notify_modified()

    def _update_active(self, result, shape: SurfaceShape) -> None:
        old = self.records[self.active]
        self.records[self.active] = self._build_record(result, shape, old.name)
        self._show_records()
        self.controller._notify_modified()

    def rename_record(self, row: int, name: str) -> None:
        name = str(name).strip()
        if not 0 <= row < len(self.records) or not name:
            self._show_records()
            return
        old = self.records[row].name
        if name == old:
            return
        self.records[row].name = name
        self._show_records()
        self.controller._notify_modified()

    def delete_record(self, row: int | None = None) -> None:
        if row is None:
            panel = self._panel()
            row = panel.selected_row() if panel is not None else None
        if row is None or not 0 <= row < len(self.records):
            return
        del self.records[row]
        if self.active is not None:
            if self.active == row:
                self.active = None
                self._active_shape = None
            elif self.active > row:
                self.active -= 1
        self._show_records()
        self.controller._notify_modified()

    def clear_records(self) -> None:
        had = bool(self.records)
        self.records = []
        self.active = None
        self._active_shape = None
        self._name_counts = {}
        self._show_records()
        if had:
            self.controller._notify_modified()

    def restore_records(self, records: list[SurfaceRecord]) -> None:
        """Replace the table with saved records (project open)."""
        self.records = list(records)
        self.active = None
        self._active_shape = None
        self._name_counts = {}
        for record in self.records:
            label, _, number = record.name.rpartition(" ")
            if label and number.isdigit():
                self._name_counts[label] = max(self._name_counts.get(label, 0), int(number))
        self._show_records()

    def restore_shapes(self, shapes: dict[str, tuple[str, list[tuple[float, float]]]]) -> None:
        self.shapes = {tool: SurfaceShape(kind=kind, points=list(points)) for tool, (kind, points) in shapes.items()}
        self.drawing = None
        self.remeasure(record=False)
        self.refresh_overlay()
        self._sync_panel()

    def select_record(self, row: int) -> None:
        """Go back to a recorded row: its tool, settings, shape and frame."""
        if not 0 <= row < len(self.records):
            return
        record = self.records[row]
        win = self.window
        shape = SurfaceShape(kind=record.shape, points=list(record.points))
        self.shapes[record.kind] = shape
        self.drawing = None
        self.active = row
        self._active_shape = shape
        panel = self._panel()
        if panel is not None and record.kind == "area":
            panel.apply_threshold_settings(record.threshold)
            panel.set_shape_silently(record.shape)
        # Switching the tool re-measures the shape just put in place.
        button = getattr(win, f"{record.kind}_tool_btn", None)
        if self.mode != record.kind and button is not None:
            button.setChecked(True)
        n_frames = len(getattr(win, "_map_frames", []) or [])
        if record.frame_index != self._frame_index() and 0 <= record.frame_index < n_frames:
            setter = getattr(win, "_set_frame_index", None)
            if setter is not None:
                setter(record.frame_index)  # renders, and re-measures through on_frame_changed
        else:
            self.remeasure(record=False)
            self.refresh_overlay()
        self._sync_panel()

    def export_csv(self, path: str | None = None) -> str | None:
        if not self.records:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self.window, "Export Surface Measurements", "surface_measurements.csv", "CSV files (*.csv)"
            )
            if not path:
                return None
            if not path.lower().endswith(".csv"):
                path += ".csv"
        try:
            count = write_csv(path, self.records)
        except OSError as exc:
            QMessageBox.warning(self.window, "Export Surface Measurements", f"Could not write the file:\n{exc}")
            return None
        self.controller._status(f"Exported {count} measurement(s) to {path}.")
        return path

    def _show_records(self) -> None:
        panel = self._panel()
        if panel is not None:
            panel.show_records(self.records, self.active)
        self._refresh_series()

    def _refresh_series(self) -> None:
        panel = self._panel()
        if panel is None:
            return
        if self.active is None or not 0 <= self.active < len(self.records):
            panel.clear_series("Select a row to plot its feature over time.")
            return
        name = self.records[self.active].name
        series = feature_series(self.records, name, corrected=self._corrected())
        if series is None or not series.values:
            panel.clear_series(f"{name}: no observation times to plot.")
            return
        times = [_unix(t) for t in series.times]
        rate = linear_rate(series.seconds, series.values)
        n = len(series.values)
        text = f"{name}  ·  {n} frame{'s' if n != 1 else ''}"
        fit = None
        if rate is not None:
            span_h = (series.seconds[-1] - series.seconds[0]) / 3600.0
            text += f"  ·  {rate.text(series.unit, span_hours=span_h)}"
            intercept = sum(series.values) / n - rate.per_hour * sum(series.seconds) / n / 3600.0
            fit = (
                [times[0], times[-1]],
                [intercept + rate.per_hour * series.seconds[0] / 3600.0,
                 intercept + rate.per_hour * series.seconds[-1] / 3600.0],
            )
        elif n == 1:
            text += "  ·  Track Frames, or Record on other frames, to see it change."
        if series.skipped:
            text += f"  ·  {series.skipped} row(s) without a time left out"
        panel.show_series(
            name,
            times,
            series.values,
            y_label=series.label,
            second=series.second,
            second_label=series.second_label,
            fit=fit,
            text=text,
        )

    # ------------------------------------------------------- tracking
    def track_feature(self) -> int:
        """Measure the selected row's feature on every loaded frame; returns rows written."""
        win = self.window
        if self.active is None or not 0 <= self.active < len(self.records):
            self.controller._status("Select a row in the table first.")
            return 0
        frames = getattr(win, "_map_frames", None) or []
        if len(frames) < 2:
            self.controller._status("Load a sequence of frames to track a feature through it.")
            return 0
        source = self.records[self.active]
        try:
            _frame, _raw, source_geometry = self._frame_context(source.frame_index)
        except Exception as exc:
            self.controller._status(f"Cannot read the row's own frame: {exc}")
            return 0
        settings = ThresholdSettings(**{k: v for k, v in source.threshold.items() if k in ThresholdSettings.__dataclass_fields__})
        panel = self._panel()
        rotate = bool(panel.rotate_check.isChecked()) if panel is not None else True
        t0 = source.time
        if rotate and t0 is None:
            rotate = False

        progress = QProgressDialog(f"Measuring {source.name} on {len(frames)} frames…", "Cancel", 0, len(frames), win)
        progress.setWindowTitle("Track Through Frames")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(400)
        written = 0
        hidden = 0
        failed: list[str] = []
        cancelled = False
        new_rows: dict[int, SurfaceRecord] = {}
        try:
            for index in range(len(frames)):
                progress.setValue(index)
                QApplication.processEvents()
                if progress.wasCanceled():
                    cancelled = True
                    break
                if index == source.frame_index:
                    continue
                try:
                    _f, raw, geometry = self._frame_context(index)
                    points = list(source.points)
                    when = self._frame_time(index)
                    if rotate and when is not None:
                        moved = rotate_points(points, source_geometry, geometry, (when - t0).total_seconds())
                        if moved.any_hidden:
                            hidden += 1
                            continue
                        points = moved.points
                    shape = SurfaceShape(kind=source.shape, points=points)
                    if shape.kind == "length":
                        result: SurfaceLength | AreaResult = polyline_surface_length(points, geometry)
                    else:
                        result = self._measure_area(shape, geometry, raw, settings, index)
                    new_rows[index] = self._build_record(result, shape, source.name, frame_index=index, geometry=geometry)
                    written += 1
                except Exception as exc:
                    failed.append(f"frame {index + 1}: {exc}")
        finally:
            progress.setValue(len(frames))
            progress.close()

        # Replace this feature's rows on the frames just measured, keep the rest.
        kept = [r for r in self.records if not (r.name == source.name and r.frame_index in new_rows)]
        self.records = group_rows_by_feature(kept + list(new_rows.values()))
        self.active = self.records.index(source)
        self._show_records()
        self.controller._notify_modified()

        message = f"{source.name}: measured on {written} more frame(s)"
        if rotate:
            message += ", rotating with the Sun"
        if hidden:
            message += f"; {hidden} skipped (the shape rotated behind the limb)"
        if failed:
            message += f"; {len(failed)} failed ({failed[0]})"
        if cancelled:
            message += "; cancelled"
        message += "."
        self.controller._append_analysis(f"[{source.name}] Track through frames  ·  {message}")
        self.controller._status(message)
        return written

    def _follow_feature_onto_frame(self) -> None:
        """On a frame change, switch to the active feature's row on that frame, if any."""
        if self.active is None or not 0 <= self.active < len(self.records):
            return
        active = self.records[self.active]
        if active.kind != self.mode:
            return
        index = self._frame_index()
        for row, record in enumerate(self.records):
            if record.name == active.name and record.frame_index == index and record.kind == active.kind:
                shape = SurfaceShape(kind=record.shape, points=list(record.points))
                self.shapes[record.kind] = shape
                self.active = row
                self._active_shape = shape
                panel = self._panel()
                if panel is not None:
                    panel.show_records(self.records, self.active)
                return

    def _is_recorded(self, result: SurfaceLength | AreaResult, shape: SurfaceShape) -> bool:
        """Whether the table already holds exactly this result (shape, frame, settings)."""
        if not self._active_updatable(shape):
            return False
        record = self.records[self.active]
        live = self._build_record(result, shape, record.name)
        return live.values == record.values and live.threshold == record.threshold

    def remeasure(self, *, record: bool, new_feature: bool = False) -> None:
        mode = self.mode
        shape = self.shapes.get(mode) if mode is not None else None
        if shape is None:
            self.last_result = None
            self._show_result("", recordable=False)
            return
        win = self.window
        if getattr(win, "_current_map_data", None) is None:
            return
        panel = self._panel()
        settings = panel.threshold_settings() if panel is not None else ThresholdSettings()
        try:
            result, geometry = self.measure_on_frame(shape, self._frame_index(), settings)
            self._last_geometry = geometry
        except Exception as exc:
            self.last_result = None
            self._show_result(f"Could not measure: {exc}", recordable=False)
            self.controller._status(f"Could not measure: {exc}")
            return
        self.last_result = result
        text = self._format(result)
        if new_feature:
            self._append_record(result, shape)
            text = self.records[self.active].line
        panel_text = self._panel_text(result)
        if not new_feature and not self._is_recorded(result, shape):
            panel_text = "● Not in the table — press Record to add it.\n" + panel_text
        self._show_result(panel_text, recordable=True)
        if record:
            self.controller._append_analysis(text)
        self.controller._status(text)

    # ----------------------------------------------------- any-frame measuring
    def measure_on_frame(
        self, shape: SurfaceShape, index: int, settings: ThresholdSettings
    ) -> tuple[SurfaceLength | AreaResult, SurfaceGeometry]:
        """Measure ``shape`` on loaded frame ``index`` (not necessarily the shown one)."""
        frame, raw, geometry = self._frame_context(index)
        if shape.kind == "length":
            return polyline_surface_length(shape.points, geometry), geometry
        return self._measure_area(shape, geometry, raw, settings, index), geometry

    def _frame_context(self, index: int) -> tuple[Any, np.ndarray, SurfaceGeometry]:
        """``(frame, raw data, geometry)`` of loaded frame ``index``.

        The shown frame uses the window's own axis transform and display shape,
        so the numbers match what is on screen; any other frame derives them
        from its own header exactly as the window would when showing it.
        """
        win = self.window
        frames = getattr(win, "_map_frames", None) or []
        if not 0 <= index < len(frames):
            raise ValueError(f"no frame {index + 1}")
        frame = frames[index]
        current = index == self._frame_index() and getattr(win, "_current_map_data", None) is not None
        raw: np.ndarray | None = None
        try:
            raw = np.asarray(win._prepare_map_array(getattr(frame, "data"), "frame"), dtype=float)
        except Exception:
            raw = None
        if current:
            display = np.asarray(win._current_map_data)
            if raw is None or raw.shape[:2] != display.shape[:2]:
                raw = np.asarray(display, dtype=float)
            transform = dict(getattr(win, "_current_axis_transform", None) or {})
        else:
            if raw is None:
                raise ValueError(f"frame {index + 1} has no readable data")
            transform = dict(win._axis_transform_for_arcsec(frame=frame, data_shape=raw.shape))
        try:
            rsun = float(win._solar_radius_arcsec(frame))
        except Exception:
            rsun = 960.0
        # Observer and image rotation from the map itself (header keys only as
        # a fallback): a rotated frame (raw HMI is stored ~180 deg round) would
        # otherwise put every centroid on the wrong side of the disk.
        geometry = geometry_for_frame(frame, transform, rsun_arcsec=rsun)
        return frame, raw, geometry

    def _area_settings(self) -> AreaSettings:
        panel = self._panel()
        return panel.area_settings() if panel is not None else AreaSettings()

    def _measure_area(
        self,
        shape: SurfaceShape,
        geometry: SurfaceGeometry,
        raw: np.ndarray,
        settings: ThresholdSettings,
        index: int,
        area: AreaSettings | None = None,
    ) -> AreaResult:
        """Measure one shape following the area plan.

        Selected region: every on-disc pixel the shape covers. Sunspot only (or
        dark / bright): the shape intersected with an intensity mask from the
        quiet-Sun-normalised frame. Both are integrated sample by sample with
        the finite-distance geometry (or the orthographic comparison), with
        samples below the mu cutoff excluded and counted.
        """
        if not geometry.metadata_valid:
            raise ValueError("; ".join(geometry.metadata_notes) + " -- fix the header before measuring")
        area = area or self._area_settings()
        win = self.window
        weights = shape.weights(raw.shape[:2], geometry, area.subsamples)
        region = weights > 0.0
        if not region.any():
            raise ValueError("the shape covers no pixel centres; draw it larger")
        quicklook = bool(getattr(win, "_loaded_is_quicklook", lambda: False)())
        intensity = None if quicklook else raw
        unit = "DN"
        # Subpixel refinement integrates the geometry per subpixel as well as the
        # coverage (plan section 5.2), not the parent-centre correction.
        integrate = dict(mu_min=area.mu_min, method=area.method, subsamples=area.subsamples)

        def measure(w: np.ndarray, image: np.ndarray | None = intensity) -> SurfaceArea:
            return mask_surface_area(w, geometry, image=image, **integrate)

        region_area = measure(weights)
        notes = tuple(geometry.metadata_notes)
        image_check = geometry.image_check
        if settings.mode == "none" or quicklook:
            excluded = excluded_pixel_mask(region, geometry, mu_min=area.mu_min, method=area.method)
            layers = ((region & ~excluded, REGION_COLOR), (excluded, EXCLUDED_COLOR))
            hard = weights > 0.5
            low = measure(ndimage.binary_erosion(hard).astype(float), None).on_disk_msh
            high = measure(ndimage.binary_dilation(hard).astype(float), None).on_disk_msh
            return AreaResult(
                shape.kind, region_area, settings, intensity_unit=unit, quicklook=quicklook,
                layers=layers, area_settings=area, sensitivity=(min(low, high), max(low, high), "mask ±1 px"), notes=notes,
                image_check=image_check,
            )

        profile = self._quiet_sun(raw, geometry, index)
        spread = self._background_spread(raw, geometry, index, profile)
        if spread is not None and spread > MAX_BACKGROUND_SPREAD:
            notes = notes + (
                f"the quiet-Sun background is not radially symmetric on this image (it varies by "
                f"{100 * spread:.0f}% across the disk after limb-darkening correction; HMI continuum: ~2%), "
                f"so threshold areas here are unreliable -- use an HMI continuum image",
            )
        normalized = normalize_to_quiet_sun(raw, geometry, profile, region=region)
        # Thresholds only look where the quiet-Sun level is known and a spot can
        # be: on the disc, inside the profile's mu range. Bright ones keep
        # off-limb pixels (an arcade above the limb is what they are for).
        dark_region = region
        excluded_px = 0
        if settings.mode in ("sunspot", "dark"):
            dark_region = disk_interior_mask(region, geometry, mu_min=profile.mu_min)
            excluded_px = int(region.sum() - dark_region.sum())

        def segment(level_shift: float = 0.0):
            if settings.mode == "sunspot":
                return sunspot_masks(
                    normalized,
                    dark_region,
                    umbra_level=settings.umbra_level,
                    penumbra_level=settings.penumbra_level + level_shift,
                    min_cluster_px=settings.min_cluster_px,
                )
            bounds = (
                {"below": settings.level + level_shift} if settings.mode == "dark" else {"above": settings.level + level_shift}
            )
            return threshold_mask(normalized, dark_region, min_cluster_px=settings.min_cluster_px, **bounds)

        def spot_weights(mask: np.ndarray) -> np.ndarray:
            return np.where(mask, weights, 0.0)

        # Sensitivity: the documented threshold range (plan section 9).
        variants = []
        for shift in (-SENSITIVITY_LEVEL_STEP, SENSITIVITY_LEVEL_STEP):
            seg = segment(shift)
            mask = seg.spot if settings.mode == "sunspot" else seg
            variants.append(measure(spot_weights(mask), None).on_disk_msh)
        word = "penumbra level" if settings.mode == "sunspot" else "threshold"
        sensitivity = (min(variants), max(variants), f"{word} ±{SENSITIVITY_LEVEL_STEP:.2f}")

        if settings.mode == "sunspot":
            masks = segment()
            spot_area = measure(spot_weights(masks.spot))
            excluded = excluded_pixel_mask(masks.spot, geometry, mu_min=area.mu_min, method=area.method)
            return AreaResult(
                shape.kind,
                region_area,
                settings,
                selected=spot_area,
                umbra=measure(spot_weights(masks.umbra)),
                penumbra=measure(spot_weights(masks.penumbra)),
                intensity_unit=unit,
                layers=((masks.penumbra & ~excluded, PENUMBRA_COLOR), (masks.umbra & ~excluded, UMBRA_COLOR), (excluded, EXCLUDED_COLOR)),
                limb_excluded_px=excluded_px,
                area_settings=area,
                sensitivity=sensitivity,
                notes=notes,
                image_check=image_check,
                background_spread=spread,
            )
        selected = segment()
        excluded = excluded_pixel_mask(selected, geometry, mu_min=area.mu_min, method=area.method)
        color = DARK_COLOR if settings.mode == "dark" else BRIGHT_COLOR
        return AreaResult(
            shape.kind,
            region_area,
            settings,
            selected=measure(spot_weights(selected)),
            intensity_unit=unit,
            layers=((selected & ~excluded, color), (excluded, EXCLUDED_COLOR)),
            limb_excluded_px=excluded_px,
            area_settings=area,
            sensitivity=sensitivity,
            notes=notes,
            image_check=image_check,
            background_spread=spread,
        )

    def _quiet_sun(self, raw: np.ndarray, geometry: SurfaceGeometry, index: int) -> QuietSunProfile:
        frames = getattr(self.window, "_map_frames", None) or []
        frame = frames[index] if 0 <= index < len(frames) else None
        key = (int(index), id(frame))
        profile = self._profile_cache.get(key)
        if profile is None:
            try:
                profile = fit_quiet_sun_profile(raw, geometry)
            except ValueError as exc:
                raise ValueError(f"no quiet-Sun level for frame {index + 1} ({exc})") from exc
            # Keep the shown frame's fit; a track through frames refits each.
            self._profile_cache = {key: profile}
        return profile

    def _background_spread(
        self, raw: np.ndarray, geometry: SurfaceGeometry, index: int, profile: QuietSunProfile
    ) -> float | None:
        """Flatness of the normalised background of frame ``index`` (cached with its fit)."""
        # The profile object identifies the frame's fit (it is refitted, so a
        # new object, whenever the frame changes); keep a reference, not an id.
        cached = getattr(self, "_spread_cache", None)
        if cached is not None and cached[0] is profile:
            return cached[1]
        try:
            value = background_spread(raw, geometry, profile)
        except Exception:
            value = None
        self._spread_cache = (profile, value)
        return value

    def threshold_layers(self) -> list[tuple[np.ndarray, tuple[int, int, int]]]:
        """Masks to tint for the current area result."""
        result = self.last_result
        if not isinstance(result, AreaResult):
            return []
        return list(result.layers)

    # ------------------------------------------------------------- geometry
    def _current_frame(self) -> Any | None:
        return self.controller._current_frame()

    def _rsun_arcsec(self) -> float:
        frame = self._current_frame()
        try:
            return float(self.window._solar_radius_arcsec(frame)) if frame is not None else 960.0
        except Exception:
            return 960.0

    def _geometry(self) -> SurfaceGeometry:
        return self._frame_context(self._frame_index())[2]

    # -------------------------------------------------------------- display
    def _canvas(self) -> Any | None:
        return getattr(self.window, "pyqt_canvas", None)

    def refresh_overlay(self) -> None:
        canvas = self._canvas()
        if canvas is None:
            return
        mode = self.mode
        if mode is None:
            canvas.clear_surface_mask_overlay()
            return
        shape = self.drawing or self.shapes.get(mode)
        if shape is None:
            canvas.clear_measurement_overlay()
            canvas.clear_surface_mask_overlay()
            return
        markers = shape.points if shape.kind in ("length", "polygon") else []
        xs = [p[0] for p in markers]
        ys = [p[1] for p in markers]
        drawing_open = shape is self.drawing and shape.kind == "polygon"
        if shape.kind == "length" or drawing_open:
            canvas.set_measurement_overlay(xs, ys, connect=True)
        else:
            curve_x, curve_y = shape.outline()
            canvas.set_measurement_overlay(xs, ys, curve_x=curve_x, curve_y=curve_y)
        panel = self._panel()
        tint = panel.tint_enabled() if panel is not None else True
        if mode == "area" and shape is not self.drawing and tint:
            try:
                canvas.set_surface_mask_overlay(self.threshold_layers())
            except Exception:
                canvas.clear_surface_mask_overlay()
        else:
            canvas.clear_surface_mask_overlay()

    def _corrected(self) -> bool:
        panel = self._panel()
        return panel.corrected() if panel is not None else True

    def _format(self, result: SurfaceLength | AreaResult) -> str:
        corrected = self._corrected()
        if isinstance(result, SurfaceLength):
            return format_length(result, corrected=corrected)
        return format_area(result, corrected=corrected)

    def curvature_changed(self) -> None:
        """Show results, table and graph corrected or as seen (nothing is re-measured)."""
        result = self.last_result
        if result is not None and self.mode is not None:
            shape = self.shapes.get(self.mode)
            text = self._panel_text(result)
            if shape is not None and not self._is_recorded(result, shape):
                text = "● Not in the table — press Record to add it.\n" + text
            self._show_result(text, recordable=True)
        self._show_records()

    def _panel_text(self, result: SurfaceLength | AreaResult) -> str:
        """Frame and time, then the plan's area report (areas) or three lines (lengths)."""
        idx = self._frame_index()
        when = self._frame_time(idx)
        header = f"Frame {idx + 1}" + (f" · {when:%Y-%m-%d %H:%M:%S} UT" if when else "")
        if isinstance(result, AreaResult):
            report = area_report(result, corrected=self._corrected())
            notices = [line for line in report if line.startswith("●")]
            body = [line for line in report if not line.startswith("●")]
            return "\n".join(notices + [header] + body)
        parts = self._format(result).split("  ·  ")
        lines = [header, " — ".join(parts[:2])]
        if len(parts) > 2:
            lines.append(" · ".join(parts[2:]))
        return "\n".join(lines)

    def _show_result(self, text: str, *, recordable: bool) -> None:
        panel = self._panel()
        if panel is not None:
            panel.show_result(text, recordable=recordable)

    def _sync_panel(self) -> None:
        panel = self._panel()
        if panel is None:
            return
        drawing = self.drawing
        clickable = drawing is not None and drawing.kind in ("length", "polygon")
        mode = self.mode
        panel.set_drawing_state(
            can_undo=clickable and bool(drawing.points),
            can_finish=clickable and drawing.is_complete(),
            has_shape=mode is not None and (mode in self.shapes or drawing is not None),
        )

