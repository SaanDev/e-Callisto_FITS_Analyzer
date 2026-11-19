"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Recorded length / area measurements: the rows of the Surface tab's table.

A record is one measurement of one named feature on one frame. Recording the
same feature on several frames (same ``name``) is what later turns into an
area- or length-against-time series. Each record keeps the shape exactly as
drawn (arcsec vertices) and its threshold settings, so it can be redrawn and
re-measured, plus a flat ``values`` dict of the numbers it produced, which is
what the CSV export and the project file store.

Pure stdlib (no numpy, no Qt): the result objects are read by attribute, so
this module also works with any object shaped like ``SurfaceLength`` /
``SurfaceArea``.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence


@dataclass
class SurfaceRecord:
    """One row of the measurements table."""

    name: str
    kind: str  # "length" or "area"
    shape: str  # "length", "polygon", "lasso", "ellipse" or "rectangle"
    points: list[tuple[float, float]]
    frame_index: int
    time: datetime | None = None
    threshold: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    summary: str = ""  # the measurement text, without the name

    @property
    def line(self) -> str:
        """The Details-panel line: ``[Sunspot 1] Sunspot (polygon) · ...``."""
        return f"[{self.name}] {self.summary}" if self.summary else f"[{self.name}]"

    @property
    def headline(self) -> str:
        """The main number (corrected for curvature), as the table shows it."""
        return self.headline_for(corrected=True)

    def headline_for(self, *, corrected: bool) -> str:
        """The main number, corrected for curvature or as seen on the image."""
        v = self.values
        if self.kind == "length":
            key = "length_total_mm" if corrected else "length_plane_of_sky_mm"
            return _fmt(v.get(key), "{:,.1f} Mm")
        area = self.value("area_msh", corrected=corrected)
        main = _msh(area)
        umbra = self.value("umbra_msh", corrected=corrected)
        if self.threshold.get("mode") == "sunspot" and umbra is not None:
            main += f" (U {_msh(umbra, unit=False)})"
        if not area and v.get("off_limb_mm2"):
            main = _fmt(v.get("off_limb_mm2"), "{:,.0f} Mm² off-limb")
        return main

    def value(self, key: str, *, corrected: bool = True) -> float | None:
        """A stored number; ``corrected=False`` gives its as-seen counterpart.

        As-seen areas are the on-disk area exactly as it appears on the image
        (no curvature correction), still expressed in MSH. Rows recorded before
        they were stored derive the whole-spot one from the plane-of-sky area.
        """
        v = self.values
        if corrected:
            return _num(v.get(key))
        seen_key = {
            "area_msh": "area_as_seen_msh",
            "umbra_msh": "umbra_as_seen_msh",
            "penumbra_msh": "penumbra_as_seen_msh",
            "region_msh": "region_as_seen_msh",
            "length_total_mm": "length_plane_of_sky_mm",
        }.get(key, key)
        if seen_key in v:
            return _num(v.get(seen_key))
        if key == "area_msh" and v.get("area_plane_of_sky_mm2") is not None:
            return _msh_from_km2(float(v["area_plane_of_sky_mm2"]) * 1e6)
        return None

    @property
    def measure_label(self) -> str:
        """What was measured: ``Length``, ``Sunspot``, ``Area > 1.50`` ..."""
        if self.kind == "length":
            return "Length"
        mode = self.threshold.get("mode", "none")
        if mode == "sunspot":
            return "Sunspot"
        if mode == "dark":
            return f"Area < {float(self.threshold.get('level', 0.0)):.2f}"
        if mode == "bright":
            return f"Area ≥ {float(self.threshold.get('level', 0.0)):.2f}"
        return "Area"


_RSUN_KM = 695_700.0


def _msh_from_km2(km2: float, rsun_km: float = _RSUN_KM) -> float | None:
    """Area in km^2 as millionths of the solar hemisphere (2 pi R^2)."""
    value = _num(km2)
    return None if value is None else value / (2.0 * math.pi * float(rsun_km) ** 2) * 1e6


def _msh(value: Any, *, unit: bool = True) -> str:
    """MSH compactly for the table: one decimal below 100, whole numbers above."""
    number = _num(value)
    if number is None:
        return "—"
    text = f"{number:,.1f}" if abs(number) < 100.0 else f"{number:,.0f}"
    return f"{text} MSH" if unit else text


def _fmt(value: Any, spec: str) -> str:
    if value is None:
        return "—"
    try:
        return spec.format(float(value))
    except (TypeError, ValueError):
        return "—"


def _num(value: Any) -> float | None:
    """A finite float, or None (JSON and CSV both choke on NaN)."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _lonlat(pair: Any) -> tuple[float | None, float | None]:
    if not pair:
        return None, None
    return _num(pair[0]), _num(pair[1])


# ------------------------------------------------------------------- values
def length_values(result: Any, geometry: Any | None = None) -> dict[str, Any]:
    """Flat numbers of a ``SurfaceLength``."""
    start_lon, start_lat = _lonlat(getattr(result, "start_lonlat", None))
    end_lon, end_lat = _lonlat(getattr(result, "end_lonlat", None))
    values = {
        "length_total_mm": _num(result.total_km / 1000.0),
        "length_on_disk_mm": _num(result.on_disk_km / 1000.0),
        "length_off_limb_mm": _num(result.off_limb_km / 1000.0),
        "length_plane_of_sky_mm": _num(result.plane_of_sky_km / 1000.0),
        "length_plane_of_sky_arcsec": _num(result.plane_of_sky_arcsec),
        "n_points": int(result.n_vertices),
        "min_mu": _num(result.min_mu),
        "near_limb": bool(result.near_limb),
        "crosses_limb": bool(result.crosses_limb),
        "start_lon_deg": start_lon,
        "start_lat_deg": start_lat,
        "end_lon_deg": end_lon,
        "end_lat_deg": end_lat,
    }
    values.update(_geometry_values(geometry))
    return values


def area_values(result: Any, geometry: Any | None = None) -> dict[str, Any]:
    """Flat numbers of an area result (``AreaResult``-shaped).

    ``area_*`` describe what was measured: the thresholded pixels when a
    threshold was on, otherwise the whole shape, which is always also given as
    ``region_*``.
    """
    region = result.region
    focus = result.selected if getattr(result, "selected", None) is not None else region
    lon, lat = _lonlat(focus.centroid_lonlat)
    cx, cy = _lonlat(focus.centroid_arcsec)
    rsun_km = _num(getattr(geometry, "rsun_km", None)) or _RSUN_KM

    def seen(area: Any) -> float | None:
        # The on-disk area as it appears on the image, in MSH, with no
        # correction for the curvature of the Sun.
        return _msh_from_km2(area.on_disk_plane_of_sky_km2, rsun_km)

    values = {
        "area_msh": _num(focus.on_disk_msh),
        "area_as_seen_msh": seen(focus),
        "area_mm2": _num(focus.on_disk_km2 / 1e6),
        "area_plane_of_sky_mm2": _num(focus.on_disk_plane_of_sky_km2 / 1e6),
        "off_limb_mm2": _num(focus.off_limb_km2 / 1e6),
        "n_pixels": int(focus.n_pixels),
        "region_msh": _num(region.on_disk_msh),
        "region_as_seen_msh": seen(region),
        "region_off_limb_mm2": _num(region.off_limb_km2 / 1e6),
        "umbra_msh": None,
        "umbra_as_seen_msh": None,
        "umbra_mm2": None,
        "penumbra_msh": None,
        "penumbra_as_seen_msh": None,
        "penumbra_mm2": None,
        "centroid_lon_deg": lon,
        "centroid_lat_deg": lat,
        "centroid_x_arcsec": cx,
        "centroid_y_arcsec": cy,
        "mean_mu": _num(focus.mean_mu),
        "min_mu": _num(focus.min_mu),
        "mean_intensity": None if getattr(result, "quicklook", False) else _num(focus.mean_intensity),
        "intensity_unit": str(getattr(result, "intensity_unit", "") or ""),
        "near_limb": bool(focus.near_limb),
    }
    umbra = getattr(result, "umbra", None)
    penumbra = getattr(result, "penumbra", None)
    if umbra is not None:
        values["umbra_msh"] = _num(umbra.on_disk_msh)
        values["umbra_as_seen_msh"] = seen(umbra)
        values["umbra_mm2"] = _num(umbra.on_disk_km2 / 1e6)
    if penumbra is not None:
        values["penumbra_msh"] = _num(penumbra.on_disk_msh)
        values["penumbra_as_seen_msh"] = seen(penumbra)
        values["penumbra_mm2"] = _num(penumbra.on_disk_km2 / 1e6)
    values.update(_geometry_values(geometry))
    return values


def _geometry_values(geometry: Any | None) -> dict[str, Any]:
    if geometry is None:
        return {}
    return {
        "rsun_arcsec": _num(getattr(geometry, "rsun_arcsec", None)),
        "b0_deg": _num(getattr(geometry, "b0_deg", None)),
        "observer_lon_deg": _num(getattr(geometry, "observer_lon_deg", None)),
    }


# ----------------------------------------------------------------------- CSV
CSV_COLUMNS = (
    "name",
    "measurement",
    "shape",
    "frame",
    "time_utc",
    "threshold",
    "umbra_level",
    "penumbra_level",
    "level",
    "min_cluster_px",
    "length_total_mm",
    "length_on_disk_mm",
    "length_off_limb_mm",
    "length_plane_of_sky_mm",
    "length_plane_of_sky_arcsec",
    "n_points",
    "start_lon_deg",
    "start_lat_deg",
    "end_lon_deg",
    "end_lat_deg",
    "area_msh",
    "area_as_seen_msh",
    "area_mm2",
    "area_plane_of_sky_mm2",
    "off_limb_mm2",
    "umbra_msh",
    "umbra_as_seen_msh",
    "umbra_mm2",
    "penumbra_msh",
    "penumbra_as_seen_msh",
    "penumbra_mm2",
    "region_msh",
    "region_off_limb_mm2",
    "n_pixels",
    "centroid_lon_deg",
    "centroid_lat_deg",
    "centroid_x_arcsec",
    "centroid_y_arcsec",
    "mean_mu",
    "min_mu",
    "near_limb",
    "mean_intensity",
    "intensity_unit",
    "rsun_arcsec",
    "b0_deg",
    "observer_lon_deg",
    "vertices_arcsec",
)

#: Header comment lines, so a CSV read on its own still says what its units are.
CSV_NOTES = (
    "# e-CALLISTO FITS Analyzer - solar surface measurements",
    "# Lengths in Mm along the solar surface; areas in MSH (millionths of the solar hemisphere) and Mm^2,",
    "# corrected for foreshortening with the observer's true distance (perspective projection).",
    "# off_limb_* parts are plane-of-sky values at the Sun's distance and are NOT corrected.",
    "# area_* = thresholded pixels when a threshold was used, else the whole shape (region_*).",
    "# *_as_seen_msh = the same area as it appears on the image, NOT corrected for curvature, in MSH.",
    "# frame is 1-based; vertices_arcsec are helioprojective (Tx Ty) pairs separated by ';'.",
)


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.6g}" if math.isfinite(value) else ""
    return value


def csv_row(record: SurfaceRecord) -> dict[str, Any]:
    threshold = record.threshold or {}
    mode = str(threshold.get("mode", "none")) if record.kind == "area" else ""
    row: dict[str, Any] = {
        "name": record.name,
        "measurement": record.measure_label,
        "shape": record.shape,
        "frame": int(record.frame_index) + 1,
        "time_utc": record.time.isoformat() if record.time else "",
        "threshold": mode,
        "umbra_level": threshold.get("umbra_level") if mode == "sunspot" else None,
        "penumbra_level": threshold.get("penumbra_level") if mode == "sunspot" else None,
        "level": threshold.get("level") if mode in ("dark", "bright") else None,
        "min_cluster_px": threshold.get("min_cluster_px") if mode not in ("", "none") else None,
        "vertices_arcsec": "; ".join(f"{x:.2f} {y:.2f}" for x, y in record.points),
    }
    for column in CSV_COLUMNS:
        if column not in row:
            row[column] = record.values.get(column)
    return {column: _cell(row.get(column)) for column in CSV_COLUMNS}


def write_csv(path: str, records: Iterable[SurfaceRecord]) -> int:
    """Write the records to ``path``; returns how many rows were written."""
    rows = [csv_row(r) for r in records]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        for note in CSV_NOTES:
            handle.write(note + "\n")
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ------------------------------------------------------------- serialization
def serialize_records(records: Iterable[SurfaceRecord]) -> list[dict[str, Any]]:
    """JSON-ready records for the project file."""
    out = []
    for record in records:
        out.append(
            {
                "name": str(record.name),
                "kind": str(record.kind),
                "shape": str(record.shape),
                "points": [[float(x), float(y)] for x, y in record.points],
                "frame_index": int(record.frame_index),
                "time": record.time.isoformat() if record.time else None,
                "threshold": _json_safe(record.threshold),
                "values": _json_safe(record.values),
                "summary": str(record.summary or ""),
            }
        )
    return out


def deserialize_records(raw: Any) -> list[SurfaceRecord]:
    """Rebuild records; malformed rows are skipped rather than refusing the file."""
    out: list[SurfaceRecord] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind") or "")
        shape = str(row.get("shape") or "")
        if kind not in ("length", "area") or not shape:
            continue
        points = _points(row.get("points"))
        if len(points) < 2:
            continue
        try:
            frame_index = int(row.get("frame_index", 0))
        except (TypeError, ValueError):
            continue
        out.append(
            SurfaceRecord(
                name=str(row.get("name") or kind.title()),
                kind=kind,
                shape=shape,
                points=points,
                frame_index=frame_index,
                time=_parse_time(row.get("time")),
                threshold=dict(row.get("threshold") or {}) if isinstance(row.get("threshold"), Mapping) else {},
                values=dict(row.get("values") or {}) if isinstance(row.get("values"), Mapping) else {},
                summary=str(row.get("summary") or ""),
            )
        )
    return out


def serialize_shapes(shapes: Mapping[str, Any]) -> dict[str, Any]:
    """The tools' current (unrecorded) shapes: ``{"length"|"area": {"kind", "points"}}``."""
    out: dict[str, Any] = {}
    for tool, shape in (shapes or {}).items():
        kind = getattr(shape, "kind", None)
        points = getattr(shape, "points", None)
        if tool in ("length", "area") and kind and points:
            out[tool] = {"kind": str(kind), "points": [[float(x), float(y)] for x, y in points]}
    return out


def deserialize_shapes(raw: Any) -> dict[str, tuple[str, list[tuple[float, float]]]]:
    out: dict[str, tuple[str, list[tuple[float, float]]]] = {}
    if not isinstance(raw, Mapping):
        return out
    for tool in ("length", "area"):
        entry = raw.get(tool)
        if not isinstance(entry, Mapping):
            continue
        kind = str(entry.get("kind") or "")
        points = _points(entry.get("points"))
        if kind and len(points) >= 2:
            out[tool] = (kind, points)
    return out


def _points(raw: Any) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return points
    for pair in raw:
        try:
            x, y = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            points.append((x, y))
    return points


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _json_safe(mapping: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in dict(mapping or {}).items():
        if isinstance(value, bool) or value is None or isinstance(value, str):
            out[str(key)] = value
        elif isinstance(value, int):
            out[str(key)] = int(value)
        else:
            out[str(key)] = _num(value)
    return out


# ------------------------------------------------------------------ series
@dataclass(frozen=True)
class FeatureSeries:
    """One feature's recorded values against time, ready to plot."""

    name: str
    kind: str
    times: list[datetime]
    values: list[float]
    unit: str  # "Mm", "MSH" or "Mm² (plane of sky)"
    label: str  # y-axis label
    second: list[float] | None = None  # umbra, for sunspots
    second_label: str = ""
    skipped: int = 0  # rows of the feature with no observation time

    @property
    def seconds(self) -> list[float]:
        """Seconds since the first time."""
        if not self.times:
            return []
        t0 = self.times[0]
        return [(t - t0).total_seconds() for t in self.times]


def feature_series(records: Iterable[SurfaceRecord], name: str, *, corrected: bool = True) -> FeatureSeries | None:
    """The rows called ``name``, ordered by time (one point per frame).

    Areas plot the on-disk MSH -- corrected for curvature, or as seen on the
    image with ``corrected=False`` -- and lengths the surface (or plane-of-sky)
    length. A feature that is entirely off-limb (an arcade above the limb)
    plots its plane-of-sky Mm² instead. Sunspots add the umbra as a second series.
    """
    rows = [r for r in records if r.name == name]
    if not rows:
        return None
    kind = rows[0].kind
    rows = [r for r in rows if r.kind == kind]
    timed = sorted((r for r in rows if r.time is not None), key=lambda r: (r.time, r.frame_index))
    skipped = len(rows) - len(timed)
    # One point per frame: the last recorded row for a frame wins.
    by_frame: dict[int, SurfaceRecord] = {}
    for record in timed:
        by_frame[record.frame_index] = record
    timed = sorted(by_frame.values(), key=lambda r: (r.time, r.frame_index))
    seen = "" if corrected else ", as seen"
    if kind == "length":
        values = [r.value("length_total_mm", corrected=corrected) for r in timed]
        unit, label = "Mm", f"Length (Mm{seen})"
        second = None
        second_label = ""
    else:
        disk = [r.value("area_msh", corrected=corrected) for r in timed]
        if any(v for v in disk if v):
            values, unit, label = disk, "MSH", f"Area (MSH{seen})"
        else:
            values = [_num(r.values.get("off_limb_mm2")) for r in timed]
            unit, label = "Mm² (plane of sky)", "Off-limb area (Mm²)"
        sunspot = any(r.threshold.get("mode") == "sunspot" for r in timed)
        second = [r.value("umbra_msh", corrected=corrected) for r in timed] if sunspot and unit == "MSH" else None
        second_label = f"umbra (MSH{seen})" if second is not None else ""
    keep = [i for i, v in enumerate(values) if v is not None]
    return FeatureSeries(
        name=name,
        kind=kind,
        times=[timed[i].time for i in keep],
        values=[float(values[i]) for i in keep],
        unit=unit,
        label=label,
        second=[float(second[i]) if second[i] is not None else math.nan for i in keep] if second else None,
        second_label=second_label,
        skipped=skipped,
    )


@dataclass(frozen=True)
class Rate:
    """A straight-line growth rate with its 1-sigma error (None below 3 points)."""

    per_hour: float
    error_per_hour: float | None
    n: int

    def text(self, unit: str, *, span_hours: float) -> str:
        """``+12.3 ± 1.1 MSH/day`` (per day once the series spans two days)."""
        scale, per = (24.0, "day") if span_hours >= 48.0 else (1.0, "h")
        value = self.per_hour * scale
        out = f"{value:+,.2f}"
        if self.error_per_hour is not None:
            out += f" ± {self.error_per_hour * scale:,.2f}"
        return f"{out} {unit}/{per}"


def linear_rate(seconds: Sequence[float], values: Sequence[float]) -> Rate | None:
    """Least-squares slope of ``values`` against time; None without two distinct times."""
    pairs = [(float(t), float(v)) for t, v in zip(seconds, values) if math.isfinite(float(v))]
    if len(pairs) < 2:
        return None
    ts = [p[0] / 3600.0 for p in pairs]
    vs = [p[1] for p in pairs]
    n = len(pairs)
    mean_t = sum(ts) / n
    mean_v = sum(vs) / n
    sxx = sum((t - mean_t) ** 2 for t in ts)
    if sxx <= 0.0:
        return None
    slope = sum((t - mean_t) * (v - mean_v) for t, v in zip(ts, vs)) / sxx
    error = None
    if n >= 3:
        intercept = mean_v - slope * mean_t
        residual = sum((v - (intercept + slope * t)) ** 2 for t, v in zip(ts, vs))
        error = math.sqrt(residual / (n - 2) / sxx)
    return Rate(per_hour=slope, error_per_hour=error, n=n)


def group_rows_by_feature(records: list[SurfaceRecord]) -> list[SurfaceRecord]:
    """Rows grouped by feature (in order of first appearance), each by frame."""
    first: dict[str, int] = {}
    for i, record in enumerate(records):
        first.setdefault(record.name, i)
    return sorted(records, key=lambda r: (first[r.name], r.frame_index))
