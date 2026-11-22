"""
e-CALLISTO FITS Analyzer
Version 3.3.0
Sahan S Liyanage
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Checking a loaded solar image's pixel geometry before anything is measured.

SDO full-disc frames come as 4096^2 (full resolution), 2048^2 (JSOC boxcar
rebin by 1/2) or 1024^2 (rebin by 1/4, or AIA synoptic). Every length and
area depends on the header describing those pixels: the plate scale CDELT and
the reference pixel CRPIX. A frame whose pixels were binned but whose header
was not updated is off by 2x in length and 4x in area per binning step, with
nothing on screen to show it.

:func:`check_frame` compares the header with the image itself:

* the size class (4096^2 / 2048^2 / 1024^2, else a cutout or crop);
* for images with a sharp photospheric limb (HMI continuum, AIA 1700/4500 A),
  the disc radius and centre measured from the pixels against the header's
  ``asin(R/D) / CDELT`` and reference pixel;
* for other full-disc SDO images (AIA EUV, HMI magnetograms), ``N x CDELT``
  against the instrument's full-disc field of view.

When the pixels are binned by exactly 2 or 4 relative to the header, the
header is corrected as JSOC's rebin does (``CDELT x f``, ``CRPIX -> (CRPIX -
0.5) / f + 0.5``) and the fix is reported. Any other disagreement is reported
as an error so the measurement can be refused rather than guessed.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

#: Full-disc SDO frame sizes and what they are.
STANDARD_SIZES = {
    4096: "4096² full resolution",
    2048: "2048² (binned ½)",
    1024: "1024² (binned ¼ or synoptic)",
}
#: Nominal full-resolution plate scales (arcsec/pixel) of SDO full-disc frames.
NOMINAL_SCALE = {"HMI": 0.504, "AIA": 0.600}
#: Measured and header disc radii must agree to this fraction.
RADIUS_TOLERANCE = 0.015
#: The disc centre in the pixels may sit this fraction of R (but at least
#: CENTRE_MIN_PX) from where the header's reference pixel puts it. Real SDO
#: frames agree to ~0.25 px; a wrong CRPIX/CRVAL moves every mu and area.
CENTRE_TOLERANCE = 0.005
CENTRE_MIN_PX = 1.5
#: Binning factors a stale header is recognised and corrected for.
BINNING_FACTORS = (2, 4)


@dataclass(frozen=True)
class DiscFit:
    """The solar disc found in the pixels (0-based pixel coordinates)."""

    centre_x: float
    centre_y: float
    radius_px: float


@dataclass(frozen=True)
class FrameCheck:
    """What the pixel-geometry check found for one frame."""

    shape: tuple[int, int]
    size_class: str
    cdelt_arcsec: float | None
    expected_radius_px: float | None  # from the header
    measured: DiscFit | None  # from the pixels (sharp-limb images only)
    binning_fix: int | None = None  # the stale-header factor corrected, if any
    expected_centre: tuple[float, float] | None = None  # 0-based, from the (corrected) header
    error: str | None = None  # an inconsistency that was not corrected
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        """``1024² (binned ¼ or synoptic) · 2.016″/px · limb 482.9 px (pixels 483.1) ✓``."""
        parts = [self.size_class]
        if self.cdelt_arcsec is not None:
            parts.append(f"{self.cdelt_arcsec:.3f}″/px")
        if self.expected_radius_px is not None:
            limb = f"limb {self.expected_radius_px:.1f} px"
            if self.measured is not None:
                limb += f" (pixels {self.measured.radius_px:.1f})"
            parts.append(limb)
        text = " · ".join(parts)
        if self.error:
            return f"{text} · ✗ {self.error}"
        if self.binning_fix:
            return f"{text} · header corrected for {self.binning_fix}× binning"
        return f"{text} · ✓"


def _meta_value(meta: dict[str, Any], key: str) -> Any:
    for variant in (key.lower(), key.upper()):
        if variant in meta:
            return meta[variant]
    return None


def _meta_float(meta: dict[str, Any], key: str) -> float | None:
    try:
        value = float(_meta_value(meta, key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _instrument(meta: dict[str, Any]) -> str:
    text = " ".join(str(_meta_value(meta, k) or "") for k in ("instrume", "telescop", "detector")).upper()
    if "HMI" in text:
        return "HMI"
    if "AIA" in text:
        return "AIA"
    return ""


def has_sharp_limb(meta: dict[str, Any]) -> bool:
    """Photospheric continuum images, whose limb is a sharp intensity edge."""
    instrument = _instrument(meta)
    content = str(_meta_value(meta, "content") or "").upper()
    wavelength = _meta_float(meta, "wavelnth")
    if instrument == "HMI":
        return "CONTINUUM" in content or "INTENSITY" in content or "LIMB" in content
    if instrument == "AIA":
        return wavelength is not None and round(wavelength) in (1700, 4500)
    return "CONTINUUM" in content or "WHITE" in content


#: Discs are fitted on frames binned down to at most this many pixels a side.
FIT_SIZE = 1024


def fit_disc(data: np.ndarray, *, rays: int = 720) -> DiscFit | None:
    """Find the solar disc in a continuum image: centre and limb radius (pixels).

    Frames larger than :data:`FIT_SIZE` are block-averaged down first and the
    result scaled back: at full resolution single-pixel noise and fine
    structure (AIA 4500's streaks) out-gradient the limb, while at 1024 px the
    fit is good to ~0.1 binned px. The disc is then located roughly (pixels
    brighter than 20% of the bright level; their centroid and equivalent
    radius). That guess can be tens of pixels off -- AIA 4500 has a wide
    scattered-light halo and a strong brightness gradient across the disc -- so
    the centre is refined on the limb itself: along ``rays`` radial lines the
    limb is the steepest intensity drop within a band about the median-profile
    limb, a circle is fitted to those edge points (outliers such as plage or
    streaks rejected), and the rays are re-cast from the new centre with a
    narrowing band until it settles. The radius is finally read from the
    steepest drop of the median radial profile about that centre. HMI keeps
    faint finite pixels out to ~1.05 R, so the NaN boundary is not the limb.
    """
    image = np.asarray(data, dtype=float)
    if image.ndim != 2:
        return None
    ny, nx = image.shape
    factor = max(1, math.ceil(max(ny, nx) / FIT_SIZE))
    if factor > 1:
        binned = _block_mean(image, factor)
        fit = _fit_disc_core(binned, rays)
        if fit is None:
            return None
        # Binned pixel j covers full pixels j*f .. j*f + f - 1 (0-based centres).
        offset = (factor - 1) / 2.0
        return DiscFit(fit.centre_x * factor + offset, fit.centre_y * factor + offset, fit.radius_px * factor)
    return _fit_disc_core(image, rays)


def _block_mean(image: np.ndarray, factor: int) -> np.ndarray:
    """``factor`` x ``factor`` block means (NaN-aware), edges trimmed."""
    ny, nx = (image.shape[0] // factor) * factor, (image.shape[1] // factor) * factor
    blocks = image[:ny, :nx].reshape(ny // factor, factor, nx // factor, factor)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(blocks, axis=(1, 3))


def _fit_disc_core(image: np.ndarray, rays: int) -> DiscFit | None:
    ny, nx = image.shape
    stride = max(1, max(ny, nx) // 1024)
    coarse = image[::stride, ::stride]
    finite = np.isfinite(coarse)
    if finite.sum() < 500:
        return None
    bright_level = float(np.nanpercentile(coarse[finite], 95))
    if not math.isfinite(bright_level) or bright_level <= 0:
        return None
    disc = finite & (coarse > 0.2 * bright_level)
    count = int(disc.sum())
    if count < 500:
        return None
    rows, cols = np.nonzero(disc)
    cy = float(rows.mean()) * stride + (stride - 1) / 2.0
    cx = float(cols.mean()) * stride + (stride - 1) / 2.0
    r_guess = math.sqrt(count / math.pi) * stride
    angles = np.linspace(0.0, 2.0 * math.pi, int(rays), endpoint=False)
    cos_a, sin_a = np.cos(angles), np.sin(angles)

    def cast(cx: float, cy: float, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Nearest-neighbour samples along every ray, radii lo..hi in 1/4 px."""
        radii = np.arange(lo, hi, 0.25)
        xs = np.rint(cx + cos_a[:, None] * radii[None, :]).astype(int)
        ys = np.rint(cy + sin_a[:, None] * radii[None, :]).astype(int)
        inside = (xs >= 0) & (xs < nx) & (ys >= 0) & (ys < ny)
        samples = np.full(xs.shape, np.nan)
        samples[inside] = image[ys[inside], xs[inside]]
        samples = np.where(np.isfinite(samples), samples, 0.0)
        samples[~inside] = np.nan
        return radii, samples, inside

    def limb_radius(cx: float, cy: float, around: float) -> float | None:
        """Steepest drop of the median radial profile about (cx, cy)."""
        radii, samples, _inside = cast(cx, cy, 0.9 * around, 1.08 * around)
        with np.errstate(all="ignore"):
            profile = np.nanmedian(samples, axis=0)
        good = np.isfinite(profile)
        if good.sum() < 10:
            return None
        gradient = np.gradient(np.interp(radii, radii[good], profile[good]))
        return float(radii[int(np.argmin(gradient))])

    radius = r_guess
    # The per-ray search stays in a narrow band about the median-profile limb:
    # a wide band lets bright plage (AIA 1700) or streaks win over the limb.
    for band in (0.12, 0.08, 0.06, 0.04, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02):
        limb = limb_radius(cx, cy, radius)
        if limb is None:
            return DiscFit(cx, cy, radius)
        radius = limb
        radii, samples, inside = cast(cx, cy, (1.0 - band) * radius, (1.0 + band) * radius)
        grad = np.gradient(np.nan_to_num(samples, nan=0.0), axis=1)
        idx = np.argmin(grad, axis=1)
        rows = np.arange(len(angles))
        ok = inside[rows, idx] & (grad[rows, idx] < 0)
        # A minimum at the band's edge means the limb lies outside the band.
        ok &= (idx > 0) & (idx < len(radii) - 1)
        if ok.sum() < rays // 3:
            break
        fitted = _fit_circle(cx + cos_a[ok] * radii[idx[ok]], cy + sin_a[ok] * radii[idx[ok]])
        if fitted is None:
            break
        shift = math.hypot(fitted[0] - cx, fitted[1] - cy)
        cx, cy = fitted[0], fitted[1]
        if shift < 0.05 and band <= 0.02:
            break

    limb = limb_radius(cx, cy, radius)
    return DiscFit(cx, cy, radius if limb is None else limb)


def _fit_circle(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float] | None:
    """Least-squares circle (Kasa) through edge points, with outliers clipped."""
    keep = np.ones(len(x), dtype=bool)
    result = None
    for _ in range(4):
        if keep.sum() < 10:
            break
        xs, ys = x[keep], y[keep]
        a = np.column_stack([xs, ys, np.ones_like(xs)])
        b = xs * xs + ys * ys
        try:
            (p, q, c), *_ = np.linalg.lstsq(a, b, rcond=None)
        except np.linalg.LinAlgError:
            break
        cx, cy = p / 2.0, q / 2.0
        r2 = c + cx * cx + cy * cy
        if not (math.isfinite(r2) and r2 > 0):
            break
        result = (float(cx), float(cy), math.sqrt(r2))
        resid = np.hypot(x - cx, y - cy) - result[2]
        mad = float(np.median(np.abs(resid[keep] - np.median(resid[keep]))))
        new_keep = np.abs(resid) <= max(3.0 * 1.4826 * mad, 1.0)
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
    return result


def _header_radius_px(meta: dict[str, Any], cdelt: float) -> float | None:
    rsun_ref = _meta_float(meta, "rsun_ref")
    dsun = _meta_float(meta, "dsun_obs")
    radius_arcsec = None
    if rsun_ref and dsun and dsun > rsun_ref > 0:
        radius_arcsec = math.degrees(math.asin(rsun_ref / dsun)) * 3600.0
    if radius_arcsec is None:
        radius_arcsec = _meta_float(meta, "rsun_obs")
    return None if radius_arcsec is None else radius_arcsec / abs(cdelt)


def _header_disc_centre(meta: dict[str, Any]) -> tuple[float, float] | None:
    """Where the header puts the Sun's centre (HPC 0,0), as 0-based pixels.

    FITS: x_i = CDELT_i * sum_j PC_ij (p_j - CRPIX_j); the centre is the pixel
    whose intermediate coordinates are -CRVAL (small-angle, fine at < 1 deg).
    """
    values = {k: _meta_float(meta, k) for k in ("crpix1", "crpix2", "cdelt1", "cdelt2")}
    if any(v is None for v in values.values()) or not values["cdelt1"] or not values["cdelt2"]:
        return None
    crval = []
    for axis in ("1", "2"):
        value = _meta_float(meta, f"crval{axis}") or 0.0
        unit = str(_meta_value(meta, f"cunit{axis}") or "arcsec").strip().lower()
        scale = {"deg": 3600.0, "arcmin": 60.0, "rad": math.degrees(1.0) * 3600.0}.get(unit, 1.0)
        crval.append(value * scale)
    pc = [_meta_float(meta, k) for k in ("pc1_1", "pc1_2", "pc2_1", "pc2_2")]
    if all(v is not None for v in pc):
        matrix = np.array([[pc[0], pc[1]], [pc[2], pc[3]]], dtype=float)
    else:
        rho = _meta_float(meta, "crota2")
        rho = _meta_float(meta, "crota") if rho is None else rho
        rho = math.radians(rho or 0.0)
        ratio = values["cdelt2"] / values["cdelt1"]
        matrix = np.array([[math.cos(rho), -math.sin(rho) * ratio],
                           [math.sin(rho) / ratio, math.cos(rho)]])
    target = np.array([-crval[0] / values["cdelt1"], -crval[1] / values["cdelt2"]])
    try:
        offset = np.linalg.solve(matrix, target)
    except np.linalg.LinAlgError:
        return None
    return (values["crpix1"] - 1.0 + float(offset[0]), values["crpix2"] - 1.0 + float(offset[1]))


def _centre_error(measured: DiscFit, centre: tuple[float, float] | None, radius_px: float) -> str | None:
    if centre is None:
        return None
    off = math.hypot(measured.centre_x - centre[0], measured.centre_y - centre[1])
    if off <= max(CENTRE_MIN_PX, CENTRE_TOLERANCE * radius_px):
        return None
    return (f"the disc centre in the pixels ({measured.centre_x + 1:.1f}, {measured.centre_y + 1:.1f}) is "
            f"{off:.1f} px from where the header puts it ({centre[0] + 1:.1f}, {centre[1] + 1:.1f}; FITS 1-based)")


def _match_binning(ratio: float) -> int | None:
    """The binning factor f when ``ratio`` (pixels / header radius) is ~1/f."""
    for factor in BINNING_FACTORS:
        if abs(ratio * factor - 1.0) <= 2.0 * RADIUS_TOLERANCE:
            return factor
    return None


def check_frame(frame: Any) -> FrameCheck:
    """Check one frame's header against its pixels (see the module docstring)."""
    data = np.asarray(getattr(frame, "data", np.zeros((0, 0))))
    meta = dict(getattr(frame, "meta", None) or {})
    shape = tuple(int(v) for v in data.shape[:2]) if data.ndim >= 2 else (0, 0)
    ny, nx = shape if len(shape) == 2 else (0, 0)
    if ny == nx and nx in STANDARD_SIZES:
        size_class = STANDARD_SIZES[nx]
    else:
        size_class = f"{nx}×{ny} (cutout or crop)"
    cdelt1 = _meta_float(meta, "cdelt1")
    cdelt2 = _meta_float(meta, "cdelt2")
    if cdelt1 is None or cdelt2 is None or cdelt1 == 0 or cdelt2 == 0:
        return FrameCheck(shape, size_class, None, None, None, error="no plate scale (CDELT) in the header")
    notes: list[str] = []
    if abs(abs(cdelt1) - abs(cdelt2)) > 0.01 * abs(cdelt1):
        notes.append(f"non-square pixels (CDELT1 {cdelt1:.4f}, CDELT2 {cdelt2:.4f})")
    cdelt = abs(cdelt1)
    expected = _header_radius_px(meta, cdelt)

    if has_sharp_limb(meta) and nx in STANDARD_SIZES:
        try:
            measured = fit_disc(data)
        except Exception:
            measured = None
        if measured is not None and expected:
            ratio = measured.radius_px / expected
            centre = _header_disc_centre(meta)
            if abs(ratio - 1.0) <= RADIUS_TOLERANCE:
                return FrameCheck(shape, size_class, cdelt, expected, measured, expected_centre=centre,
                                  error=_centre_error(measured, centre, expected), notes=tuple(notes))
            factor = _match_binning(ratio)
            if factor is not None:
                # JSOC rebin: CRPIX -> (CRPIX - 0.5)/f + 0.5, i.e. 0-based (c + 0.5)/f - 0.5.
                binned = None if centre is None else tuple((c + 0.5) / factor - 0.5 for c in centre)
                error = _centre_error(measured, binned, expected / factor)
                return FrameCheck(shape, size_class, cdelt, expected, measured,
                                  binning_fix=None if error else factor, expected_centre=binned,
                                  error=error, notes=tuple(notes))
            return FrameCheck(
                shape, size_class, cdelt, expected, measured, expected_centre=centre,
                error=(f"the header's plate scale puts the limb at {expected:.1f} px but the image shows it at "
                       f"{measured.radius_px:.1f} px ({100 * (ratio - 1):+.1f}%)"),
                notes=tuple(notes),
            )

    # No sharp limb: compare N x CDELT with the instrument's full-disc field of view.
    instrument = _instrument(meta)
    if instrument in NOMINAL_SCALE and nx in STANDARD_SIZES:
        nominal = NOMINAL_SCALE[instrument] * 4096.0 / nx
        ratio = nominal / cdelt
        if abs(ratio - 1.0) <= 0.03:
            return FrameCheck(shape, size_class, cdelt, expected, None, notes=tuple(notes))
        factor = _match_binning(1.0 / ratio) if ratio > 1 else None
        if factor is not None:
            return FrameCheck(shape, size_class, cdelt, expected, None, binning_fix=factor, notes=tuple(notes))
        return FrameCheck(
            shape, size_class, cdelt, expected, None,
            error=(f"plate scale {cdelt:.3f}″/px does not fit a {nx}² {instrument} full-disc frame "
                   f"(expected about {nominal:.3f}″/px)"),
            notes=tuple(notes),
        )
    return FrameCheck(shape, size_class, cdelt, expected, None, notes=tuple(notes))


#: Header keys the check writes onto a frame so later steps can report it.
CHECK_KEY = "ECA_PIXCHK"
FIX_KEY = "ECA_PIXFIX"
ERROR_KEY = "ECA_PIXERR"


def apply_check(frame: Any, check: FrameCheck) -> Any:
    """``frame`` with a stale binned header corrected and the check recorded in its meta.

    Returns a new SunPy map when the header changes, else the same frame with
    the summary added to its meta. Frames that are not SunPy maps are returned
    unchanged apart from the recorded summary.
    """
    meta = getattr(frame, "meta", None)
    if check.binning_fix:
        try:
            import sunpy.map

            new_meta = dict(meta or {})
            lower = {str(k).lower(): k for k in new_meta}
            f = float(check.binning_fix)

            def setkey(key: str, value: float) -> None:
                new_meta[lower.get(key, key)] = value

            for axis in ("1", "2"):
                cdelt = float(new_meta[lower.get(f"cdelt{axis}", f"cdelt{axis}")])
                crpix = float(new_meta[lower.get(f"crpix{axis}", f"crpix{axis}")])
                setkey(f"cdelt{axis}", cdelt * f)
                setkey(f"crpix{axis}", (crpix - 0.5) / f + 0.5)
            for key in ("cd1_1", "cd1_2", "cd2_1", "cd2_2"):
                if key in lower:
                    setkey(key, float(new_meta[lower[key]]) * f)
            new_meta[FIX_KEY] = f"CDELT x{check.binning_fix}, CRPIX moved to the binned grid (header was not updated for {check.binning_fix}x binning)"
            # Describe the corrected header, not the stale one.
            corrected = replace(
                check,
                cdelt_arcsec=None if check.cdelt_arcsec is None else check.cdelt_arcsec * f,
                expected_radius_px=None if check.expected_radius_px is None else check.expected_radius_px / f,
            )
            new_meta[CHECK_KEY] = corrected.summary()
            return sunpy.map.Map(np.asarray(frame.data), new_meta)
        except Exception:
            pass
    try:
        if meta is not None:
            meta[CHECK_KEY] = check.summary()
            if check.error:
                meta[ERROR_KEY] = check.error
    except Exception:
        pass
    return frame
