"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


CropBounds = tuple[int, int, int, int]


@dataclass(frozen=True)
class AiaFrameSet:
    paths: list[str]
    maps: list[Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AiaRegion:
    region_id: int
    bbox: CropBounds
    centroid_x: float
    centroid_y: float
    centroid_x_arcsec: float
    centroid_y_arcsec: float
    area_px: int
    peak: float
    mean: float
    label: str = ""
    noaa_number: str = ""
    metadata_source: str = ""
    metadata_distance_arcsec: float | None = None


@dataclass(frozen=True)
class AiaMetadataRegion:
    label: str
    noaa_number: str = ""
    event_type: str = ""
    center_x_arcsec: float | None = None
    center_y_arcsec: float | None = None
    bbox_arcsec: tuple[float, float, float, float] | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    source: str = "HEK/SRS"


@dataclass(frozen=True)
class AiaCompositeSpec:
    frame_indexes: tuple[int, ...] = (0, 1, 2)
    percentile_low: float = 1.0
    percentile_high: float = 99.0


@dataclass(frozen=True)
class AiaMovieExportSpec:
    path: str
    fps: float = 4.0
    mode: str = "raw"
    crop_bounds: CropBounds | None = None
    percentile_low: float = 1.0
    percentile_high: float = 99.0
    colormap_name: str = "inferno"


class AiaArrayMap:
    """Small SunPy-map-like wrapper for derived arrays.

    The plot window only needs a map object with ``data`` and a handful of
    metadata attributes. Keeping this wrapper light avoids forcing every
    derived product through FITS/WCS reconstruction.
    """

    def __init__(self, data: Any, source_map: Any | None = None, *, nickname: str = ""):
        self.data = np.asarray(data)
        self.meta = dict(getattr(source_map, "meta", {}) or {})
        self.observatory = _safe_text(getattr(source_map, "observatory", "")) or _safe_text(
            self.meta.get("obsrvtry", "")
        )
        self.instrument = _safe_text(getattr(source_map, "instrument", "")) or _safe_text(
            self.meta.get("instrume", "")
        )
        self.detector = _safe_text(getattr(source_map, "detector", "")) or _safe_text(self.meta.get("detector", ""))
        self.wavelength = _safe_text(getattr(source_map, "wavelength", ""))
        self.date = _safe_text(getattr(source_map, "date", ""))
        self.nickname = str(nickname or _safe_text(getattr(source_map, "nickname", ""))).strip()
        self.source = _safe_text(getattr(source_map, "source", ""))

        for attr in ("scale", "reference_pixel", "reference_coordinate", "coordinate_frame", "rsun_meters"):
            if hasattr(source_map, attr):
                try:
                    setattr(self, attr, getattr(source_map, attr))
                except Exception:
                    pass


def load_aia_maps(paths: Iterable[str | Path], *, map_loader: Any | None = None) -> AiaFrameSet:
    normalized = [str(Path(p).expanduser().resolve()) for p in paths if str(p).strip()]
    if not normalized:
        raise ValueError("Select at least one AIA FITS file.")

    if map_loader is None:
        try:
            from sunpy.map import Map as map_loader
        except Exception as exc:
            raise RuntimeError(f"SunPy map loading is not available: {exc}") from exc

    loaded = map_loader(normalized, sequence=True)
    maps = extract_map_frames(loaded)
    if not maps:
        raise RuntimeError("No AIA map frames were loaded.")

    first = maps[0]
    metadata = {
        "n_frames": len(maps),
        "observatory": _safe_text(getattr(first, "observatory", "")),
        "instrument": _safe_text(getattr(first, "instrument", "")),
        "detector": _safe_text(getattr(first, "detector", "")),
        "wavelength": _safe_text(getattr(first, "wavelength", "")),
        "date": _safe_text(getattr(first, "date", "")),
    }
    return AiaFrameSet(paths=normalized, maps=maps, metadata=metadata)


def extract_map_frames(loaded_obj: Any) -> list[Any]:
    maps_attr = getattr(loaded_obj, "maps", None)
    if maps_attr is not None:
        try:
            return list(maps_attr)
        except Exception:
            pass
    if isinstance(loaded_obj, (list, tuple)):
        return list(loaded_obj)
    return [loaded_obj]


def clip_crop_bounds(shape: Sequence[int], bounds: CropBounds | None) -> CropBounds:
    if len(shape) < 2:
        raise ValueError("AIA image data must be at least two-dimensional.")
    ny = int(shape[0])
    nx = int(shape[1])
    if bounds is None:
        return (0, nx, 0, ny)

    x0, x1, y0, y1 = bounds
    x_low, x_high = sorted((int(x0), int(x1)))
    y_low, y_high = sorted((int(y0), int(y1)))
    x_low = max(0, min(nx, x_low))
    x_high = max(0, min(nx, x_high))
    y_low = max(0, min(ny, y_low))
    y_high = max(0, min(ny, y_high))
    if x_high <= x_low or y_high <= y_low:
        raise ValueError("Crop region does not overlap the image.")
    return (x_low, x_high, y_low, y_high)


def crop_array(data: Any, bounds: CropBounds | None) -> np.ndarray:
    arr = _prepare_image_array(data)
    x0, x1, y0, y1 = clip_crop_bounds(arr.shape, bounds)
    if arr.ndim == 3:
        return np.asarray(arr[y0:y1, x0:x1, :])
    return np.asarray(arr[y0:y1, x0:x1])


def crop_maps(frames: Sequence[Any], bounds: CropBounds | None) -> list[AiaArrayMap]:
    out: list[AiaArrayMap] = []
    for idx, frame in enumerate(frames):
        arr = _prepare_image_array(getattr(frame, "data"))
        x0, x1, y0, y1 = clip_crop_bounds(arr.shape, bounds)
        cropped = AiaArrayMap(arr[y0:y1, x0:x1, ...] if arr.ndim == 3 else arr[y0:y1, x0:x1], frame, nickname=f"Cropped frame {idx + 1}")
        prior_origin = getattr(frame, "_crop_origin_px", (0, 0))
        try:
            prior_x, prior_y = int(prior_origin[0]), int(prior_origin[1])
        except Exception:
            prior_x, prior_y = 0, 0
        cropped._crop_origin_px = (prior_x + int(x0), prior_y + int(y0))
        _shift_crpix_metadata(cropped.meta, x0=int(x0), y0=int(y0))
        out.append(cropped)
    return out


def difference_sequence(frames: Sequence[Any], *, mode: str = "running", crop_bounds: CropBounds | None = None) -> list[np.ndarray]:
    arrays = [crop_array(getattr(frame, "data"), crop_bounds) for frame in frames]
    if not arrays:
        return []
    mode_key = str(mode or "running").strip().lower()
    if mode_key in {"raw", "none"}:
        return arrays
    if mode_key in {"base", "base_difference"}:
        base = np.asarray(arrays[0], dtype=float)
        return [np.asarray(arr, dtype=float) - base for arr in arrays]
    if mode_key not in {"running", "running_difference"}:
        raise ValueError(f"Unsupported difference mode: {mode}")

    out: list[np.ndarray] = []
    for idx, arr in enumerate(arrays):
        current = np.asarray(arr, dtype=float)
        if len(arrays) == 1:
            out.append(current.copy())
            continue
        if idx == 0:
            out.append(np.asarray(arrays[1], dtype=float) - current)
        else:
            out.append(current - np.asarray(arrays[idx - 1], dtype=float))
    return out


def make_composite(frames: Sequence[Any], spec: AiaCompositeSpec | None = None) -> AiaArrayMap:
    if not frames:
        raise ValueError("At least one AIA frame is required for a composite.")
    spec = spec or AiaCompositeSpec()
    indexes = [idx for idx in spec.frame_indexes if 0 <= int(idx) < len(frames)]
    if not indexes:
        indexes = [0]

    channels = []
    for idx in indexes[:3]:
        arr = crop_array(getattr(frames[int(idx)], "data"), None)
        channels.append(_normalize_to_unit(arr, spec.percentile_low, spec.percentile_high))

    while len(channels) < 3:
        channels.append(channels[-1].copy())

    rgb = np.dstack(channels[:3])
    return AiaArrayMap(rgb, frames[indexes[0]], nickname="AIA Composite")


def detect_active_regions(
    data: Any,
    *,
    threshold_percentile: float = 98.0,
    min_area_px: int = 12,
    max_regions: int = 25,
    axis_transform: dict[str, float] | None = None,
) -> list[AiaRegion]:
    arr = np.asarray(crop_array(data, None), dtype=float)
    if arr.ndim == 3:
        arr = np.nanmean(arr[..., :3], axis=2)

    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return []

    threshold = float(np.nanpercentile(finite, float(threshold_percentile)))
    mask = np.isfinite(arr) & (arr >= threshold)

    try:
        from scipy import ndimage
    except Exception:
        return _detect_regions_without_scipy(arr, mask, min_area_px=min_area_px, max_regions=max_regions, axis_transform=axis_transform)

    labeled, count = ndimage.label(mask)
    objects = ndimage.find_objects(labeled)
    regions: list[AiaRegion] = []
    region_id = 1
    for label_idx in range(1, int(count) + 1):
        slc = objects[label_idx - 1] if label_idx - 1 < len(objects) else None
        if slc is None:
            continue
        y_slice, x_slice = slc
        component = labeled[slc] == label_idx
        area = int(np.count_nonzero(component))
        if area < int(min_area_px):
            continue
        values = arr[slc][component]
        if values.size == 0:
            continue
        cy, cx = ndimage.center_of_mass(mask, labeled, label_idx)
        if not np.isfinite(cx) or not np.isfinite(cy):
            cy, cx = _component_centroid(component, y_slice.start, x_slice.start)
        x0, x1 = int(x_slice.start), int(x_slice.stop)
        y0, y1 = int(y_slice.start), int(y_slice.stop)
        x_arc, y_arc = _pixel_to_arcsec(cx, cy, axis_transform)
        regions.append(
            AiaRegion(
                region_id=region_id,
                bbox=(x0, x1, y0, y1),
                centroid_x=float(cx),
                centroid_y=float(cy),
                centroid_x_arcsec=float(x_arc),
                centroid_y_arcsec=float(y_arc),
                area_px=area,
                peak=float(np.nanmax(values)),
                mean=float(np.nanmean(values)),
            )
        )
        region_id += 1

    regions.sort(key=lambda r: (r.peak, r.area_px), reverse=True)
    limited = regions[: max(0, int(max_regions))]
    return [replace(region, region_id=idx + 1) for idx, region in enumerate(limited)]


def label_regions_with_metadata(
    regions: Sequence[AiaRegion],
    metadata_regions: Sequence[AiaMetadataRegion],
    *,
    max_distance_arcsec: float = 180.0,
) -> list[AiaRegion]:
    out: list[AiaRegion] = []
    for region in regions:
        best: tuple[float, AiaMetadataRegion] | None = None
        for meta in metadata_regions:
            dist = _metadata_region_distance(region, meta)
            if dist is None:
                continue
            if best is None or dist < best[0]:
                best = (dist, meta)
        if best is None or best[0] > float(max_distance_arcsec):
            out.append(region)
            continue
        meta = best[1]
        label = str(meta.label or meta.noaa_number or "").strip()
        out.append(
            replace(
                region,
                label=label,
                noaa_number=str(meta.noaa_number or ""),
                metadata_source=str(meta.source or ""),
                metadata_distance_arcsec=float(best[0]),
            )
        )
    return out


def fetch_active_region_metadata(
    start_dt: datetime,
    end_dt: datetime,
    *,
    hek_client: Any | None = None,
    attrs_module: Any | None = None,
) -> list[AiaMetadataRegion]:
    if hek_client is None or attrs_module is None:
        try:
            from sunpy.net import attrs as sunpy_attrs
            from sunpy.net.hek import HEKClient
        except Exception as exc:
            raise RuntimeError(f"SunPy HEK support is not available: {exc}") from exc
        attrs_module = attrs_module or sunpy_attrs
        hek_client = hek_client or HEKClient()

    results = hek_client.search(
        attrs_module.Time(start_dt, end_dt),
        attrs_module.hek.EventType("AR"),
    )
    return [_metadata_region_from_hek(row) for row in results]


def render_movie_frames(
    frames: Sequence[Any],
    *,
    mode: str = "raw",
    crop_bounds: CropBounds | None = None,
    percentile_low: float = 1.0,
    percentile_high: float = 99.0,
    colormap_name: str = "inferno",
) -> list[np.ndarray]:
    arrays = difference_sequence(frames, mode=mode, crop_bounds=crop_bounds)
    rendered = []
    for arr in arrays:
        rendered.append(
            _array_to_rgb_uint8(
                arr,
                percentile_low=percentile_low,
                percentile_high=percentile_high,
                colormap_name=colormap_name,
            )
        )
    return rendered


def export_movie(frames: Sequence[Any], spec: AiaMovieExportSpec) -> None:
    out_path = Path(spec.path).expanduser()
    if not out_path.suffix:
        raise ValueError("Movie export path must include .gif or .mp4.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_movie_frames(
        frames,
        mode=spec.mode,
        crop_bounds=spec.crop_bounds,
        percentile_low=spec.percentile_low,
        percentile_high=spec.percentile_high,
        colormap_name=spec.colormap_name,
    )
    if not rendered:
        raise ValueError("No frames are available for movie export.")

    try:
        import imageio.v2 as imageio
    except Exception as exc:
        raise RuntimeError("Movie export requires imageio. Install the pinned runtime dependencies.") from exc

    suffix = out_path.suffix.lower()
    fps = max(0.1, float(spec.fps or 4.0))
    if suffix == ".gif":
        imageio.mimsave(str(out_path), rendered, duration=1.0 / fps)
        return
    if suffix == ".mp4":
        try:
            writer = imageio.get_writer(str(out_path), fps=fps, codec="libx264", macro_block_size=16)
        except Exception as exc:
            raise RuntimeError(
                "MP4 export requires imageio-ffmpeg or a working ffmpeg backend."
            ) from exc
        try:
            for frame in rendered:
                writer.append_data(frame)
        finally:
            writer.close()
        return
    raise ValueError("Movie export supports only .gif and .mp4 files.")


def write_cropped_fits(frame: Any, bounds: CropBounds | None, path: str | Path) -> None:
    try:
        from astropy.io import fits
    except Exception as exc:
        raise RuntimeError(f"FITS export requires astropy: {exc}") from exc

    out_path = Path(path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = crop_array(getattr(frame, "data"), bounds)
    header = None
    meta = getattr(frame, "meta", None)
    if meta:
        try:
            header = fits.Header()
            for key, value in dict(meta).items():
                text_key = str(key).upper()[:8]
                if not text_key:
                    continue
                try:
                    header[text_key] = value
                except Exception:
                    continue
            if bounds is not None:
                x0, _x1, y0, _y1 = clip_crop_bounds(np.asarray(getattr(frame, "data")).shape, bounds)
                _shift_header_crpix(header, x0=int(x0), y0=int(y0))
        except Exception:
            header = None
    fits.PrimaryHDU(data=np.asarray(data), header=header).writeto(out_path, overwrite=True)


def _shift_crpix_metadata(meta: dict[str, Any], *, x0: int, y0: int) -> None:
    if not meta:
        return
    for key, offset in (("crpix1", x0), ("CRPIX1", x0), ("crpix2", y0), ("CRPIX2", y0)):
        if key not in meta:
            continue
        try:
            meta[key] = float(meta[key]) - float(offset)
        except Exception:
            pass


def _shift_header_crpix(header: Any, *, x0: int, y0: int) -> None:
    for key, offset in (("CRPIX1", x0), ("CRPIX2", y0)):
        if key not in header:
            continue
        try:
            header[key] = float(header[key]) - float(offset)
        except Exception:
            pass


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return repr(value)


def _prepare_image_array(data: Any) -> np.ndarray:
    arr = np.asarray(data)
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[-1] in (3, 4):
        return arr
    if arr.ndim > 2:
        arr2 = np.asarray(arr[0]).squeeze()
        if arr2.ndim == 2:
            return arr2
    raise ValueError(f"Unsupported AIA image shape: {arr.shape}")


def _normalize_to_unit(data: Any, p_low: float, p_high: float) -> np.ndarray:
    arr = np.asarray(data, dtype=float)
    if arr.ndim == 3:
        arr = np.nanmean(arr[..., :3], axis=2)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape[:2], dtype=float)
    lo = float(np.nanpercentile(finite, float(p_low)))
    hi = float(np.nanpercentile(finite, float(p_high)))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(finite))
        hi = float(np.nanmax(finite))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.zeros(arr.shape[:2], dtype=float)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def _array_to_rgb_uint8(
    arr: Any,
    *,
    percentile_low: float,
    percentile_high: float,
    colormap_name: str = "inferno",
) -> np.ndarray:
    data = np.asarray(arr)
    if data.ndim == 3 and data.shape[-1] in (3, 4):
        rgb = np.asarray(data[..., :3], dtype=float)
        if np.nanmax(rgb) <= 1.0:
            rgb = rgb * 255.0
        return np.clip(rgb, 0, 255).astype(np.uint8)

    norm = _normalize_to_unit(data, percentile_low, percentile_high)
    try:
        try:
            import sunpy.visualization.colormaps  # noqa: F401
        except Exception:
            pass
        from matplotlib import colormaps

        rgba = colormaps.get_cmap(str(colormap_name or "inferno"))(norm)
        rgb = np.asarray(rgba[..., :3] * 255.0, dtype=np.uint8)
        return rgb
    except Exception:
        fallback = _fallback_colormap_rgb(norm, str(colormap_name or "inferno"))
        if fallback is not None:
            return fallback
        gray = np.asarray(norm * 255.0, dtype=np.uint8)
        return np.dstack([gray, gray, gray])


def _fallback_colormap_rgb(norm: np.ndarray, name: str) -> np.ndarray | None:
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
    }
    colors = palettes.get(str(name or "").lower())
    if not colors:
        return None
    stops = np.linspace(0.0, 1.0, len(colors))
    flat = np.asarray(norm, dtype=float).ravel()
    rgb = np.empty((flat.size, 3), dtype=np.uint8)
    color_arr = np.asarray(colors, dtype=float)
    for channel in range(3):
        rgb[:, channel] = np.clip(np.interp(flat, stops, color_arr[:, channel]), 0, 255).astype(np.uint8)
    return rgb.reshape((*norm.shape, 3))


def _component_centroid(component: np.ndarray, y_start: int, x_start: int) -> tuple[float, float]:
    y_idx, x_idx = np.where(component)
    if x_idx.size == 0:
        return float(y_start), float(x_start)
    return float(y_start + np.mean(y_idx)), float(x_start + np.mean(x_idx))


def _pixel_to_arcsec(x_pix: float, y_pix: float, axis_transform: dict[str, float] | None) -> tuple[float, float]:
    if not axis_transform:
        return float(x_pix), float(y_pix)
    x_ref_pix = float(axis_transform.get("x_ref_pix", 0.0))
    y_ref_pix = float(axis_transform.get("y_ref_pix", 0.0))
    x_scale = float(axis_transform.get("x_scale_arcsec_per_pix", 1.0)) or 1.0
    y_scale = float(axis_transform.get("y_scale_arcsec_per_pix", 1.0)) or 1.0
    x_ref_arcsec = float(axis_transform.get("x_ref_arcsec", 0.0))
    y_ref_arcsec = float(axis_transform.get("y_ref_arcsec", 0.0))
    return (
        x_ref_arcsec + (float(x_pix) - x_ref_pix) * x_scale,
        y_ref_arcsec + (float(y_pix) - y_ref_pix) * y_scale,
    )


def _detect_regions_without_scipy(
    arr: np.ndarray,
    mask: np.ndarray,
    *,
    min_area_px: int,
    max_regions: int,
    axis_transform: dict[str, float] | None,
) -> list[AiaRegion]:
    y_idx, x_idx = np.where(mask)
    if x_idx.size < int(min_area_px):
        return []
    values = arr[y_idx, x_idx]
    x0, x1 = int(np.min(x_idx)), int(np.max(x_idx)) + 1
    y0, y1 = int(np.min(y_idx)), int(np.max(y_idx)) + 1
    cx = float(np.mean(x_idx))
    cy = float(np.mean(y_idx))
    x_arc, y_arc = _pixel_to_arcsec(cx, cy, axis_transform)
    return [
        AiaRegion(
            region_id=1,
            bbox=(x0, x1, y0, y1),
            centroid_x=cx,
            centroid_y=cy,
            centroid_x_arcsec=x_arc,
            centroid_y_arcsec=y_arc,
            area_px=int(x_idx.size),
            peak=float(np.nanmax(values)),
            mean=float(np.nanmean(values)),
        )
    ][: max(0, int(max_regions))]


def _metadata_region_distance(region: AiaRegion, meta: AiaMetadataRegion) -> float | None:
    if meta.bbox_arcsec is not None:
        x0, x1, y0, y1 = meta.bbox_arcsec
        if min(x0, x1) <= region.centroid_x_arcsec <= max(x0, x1) and min(y0, y1) <= region.centroid_y_arcsec <= max(y0, y1):
            return 0.0
    if meta.center_x_arcsec is None or meta.center_y_arcsec is None:
        return None
    return float(
        np.hypot(
            float(region.centroid_x_arcsec) - float(meta.center_x_arcsec),
            float(region.centroid_y_arcsec) - float(meta.center_y_arcsec),
        )
    )


def _metadata_region_from_hek(row: Any) -> AiaMetadataRegion:
    get = row.get if hasattr(row, "get") else lambda key, default=None: getattr(row, key, default)
    noaa = _safe_text(get("ar_noaanum", "")) or _safe_text(get("frm_identifier", ""))
    label = f"NOAA {noaa}" if noaa else _safe_text(get("event_type", "AR")) or "Active Region"
    center_x = _as_float(get("hpc_x", None))
    center_y = _as_float(get("hpc_y", None))
    bbox = _parse_hek_bbox(get("hpc_bbox", None))
    return AiaMetadataRegion(
        label=label,
        noaa_number=noaa,
        event_type=_safe_text(get("event_type", "AR")),
        center_x_arcsec=center_x,
        center_y_arcsec=center_y,
        bbox_arcsec=bbox,
        start_time=get("event_starttime", None),
        end_time=get("event_endtime", None),
        source="HEK",
    )


def _parse_hek_bbox(value: Any) -> tuple[float, float, float, float] | None:
    text = _safe_text(value)
    if not text:
        return None
    import re

    nums = [float(match.group(0)) for match in re.finditer(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)]
    if len(nums) < 4:
        return None
    xs = nums[0::2]
    ys = nums[1::2]
    return (float(min(xs)), float(max(xs)), float(min(ys)), float(max(ys)))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    to_value = getattr(value, "to_value", None)
    if callable(to_value):
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
