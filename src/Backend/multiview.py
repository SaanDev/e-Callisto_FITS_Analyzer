"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Multi-viewpoint reprojection for STEREO + SDO/SOHO.

STEREO-A/B observe the Sun from large angular separations from the Sun-Earth
line, so the same CME looks completely different from STEREO than from an
Earth-view instrument (AIA, LASCO, SUVI). Reprojecting one map onto another's
WCS/observer places both in a common frame so they can be blinked or overlaid
for stereoscopic context.

This wraps sunpy's ``GenericMap.reproject_to`` (backed by the ``reproject``
package, already a dependency). Scope note: this delivers *co-aligned overlays*,
not full 3-D tie-point CME reconstruction (``scc_measure``-style) — that is a
possible future extension.

The heavy lifting is sunpy/reproject; this module keeps the orchestration small
and defensive so callers get a clear error when a map lacks the observer/WCS
metadata reprojection needs.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Sequence


# Where the emission is assumed to lie when mapping between two observers.
SCREEN_SURFACE = "surface"
SCREEN_SPHERICAL = "spherical"
SCREEN_PLANAR = "planar"


def _require_wcs(target: Any) -> Any:
    """Return a WCS from a map or pass a WCS through unchanged."""
    wcs = getattr(target, "wcs", None)
    if wcs is not None:
        return wcs
    # Already a WCS-like object (has pixel_to_world / to_header).
    if hasattr(target, "pixel_to_world") or hasattr(target, "to_header"):
        return target
    raise TypeError("reproject target must be a sunpy Map or an astropy WCS.")


def _target_observer(target: Any) -> Any:
    """Observer coordinate of a reprojection target (a Map or a bare WCS)."""
    observer = getattr(target, "observer_coordinate", None)
    if observer is not None:
        return observer
    # A bare WCS still carries the observer in its solar aux keywords.
    try:
        from astropy.wcs.utils import wcs_to_celestial_frame

        return getattr(wcs_to_celestial_frame(_require_wcs(target)), "observer", None)
    except Exception:
        return None


def _screen_context(target: Any, screen: str):
    """Context manager fixing where 2-D emission sits in 3-D during reprojection.

    Changing observer means deciding, for every pixel, how far along the line of
    sight the emission lies. sunpy's default answer is "on the solar surface",
    which is right for disk imagery (AIA, EUVI) and wrong for the optically-thin
    corona, where a white-light feature has no unique depth: reprojecting COR2
    onto LASCO under the surface assumption throws almost all of it away.

    ``spherical`` instead places emission on a sphere centred on the Sun and
    passing through the observer — the standard treatment for off-disk features.
    It stays an approximation: good for morphology and event identification, not
    for photometry.

    The screen must be centred on the **target** observer, not the source's.
    ``reproject`` walks the *output* pixels, turns each into a world coordinate
    in the target frame and only then transforms it to the source frame, so the
    2-D-to-3-D assumption is applied in the target's frame. Centring on the
    source observer silently loses signal as the baseline grows, and at large
    separations (STEREO behind the Sun) returns an entirely empty image.

    ``only_off_disk`` keeps on-disk emission on the photosphere where it really
    is, and applies the screen just to the corona around it.
    """
    key = str(screen or SCREEN_SURFACE).strip().lower()
    if key in ("", SCREEN_SURFACE):
        return contextlib.nullcontext()

    observer = _target_observer(target)
    if observer is None:
        return contextlib.nullcontext()

    try:
        from sunpy.coordinates.screens import PlanarScreen, SphericalScreen

        screen_cls = PlanarScreen if key == SCREEN_PLANAR else SphericalScreen
        try:
            return screen_cls(observer, only_off_disk=True)
        except TypeError:
            # Older sunpy without only_off_disk.
            return screen_cls(observer)
    except Exception:
        # Older sunpy without the screens API, or an observer coordinate the
        # screen rejects. The surface assumption still yields a usable image, so
        # degrade rather than abort — a batch composite must not die on one frame.
        return contextlib.nullcontext()


def reproject_map_to(
    source_map: Any,
    target: Any,
    *,
    algorithm: str = "interpolation",
    return_footprint: bool = False,
    screen: str = SCREEN_SURFACE,
) -> Any:
    """Reproject ``source_map`` onto ``target``'s WCS (and observer/frame).

    ``target`` may be a sunpy Map (its ``.wcs`` is used) or an astropy WCS. The
    returned map shares the target's pixel grid, so a STEREO map reprojected onto
    an Earth-view map's WCS becomes directly comparable pixel-for-pixel.

    ``screen`` selects the line-of-sight assumption — ``"surface"`` (default,
    correct for disk emission), ``"spherical"`` or ``"planar"`` (for off-disk
    coronal structure). See :func:`_screen_context`.
    """
    reproject_to = getattr(source_map, "reproject_to", None)
    if reproject_to is None:
        raise TypeError("source_map must be a sunpy GenericMap with reproject_to().")
    wcs = _require_wcs(target)
    with _screen_context(target, screen):
        try:
            return reproject_to(wcs, algorithm=algorithm, return_footprint=return_footprint)
        except TypeError:
            # Older/newer signatures may not accept every keyword.
            return reproject_to(wcs)


@dataclass(frozen=True)
class CoalignedView:
    """A reference map plus other maps reprojected onto its frame."""

    reference: Any
    reprojected: list[Any]

    @property
    def all_maps(self) -> list[Any]:
        return [self.reference, *self.reprojected]


def coalign_to_reference(
    reference_map: Any,
    other_maps: Sequence[Any],
    *,
    screen: str = SCREEN_SURFACE,
) -> CoalignedView:
    """Reproject every map in ``other_maps`` onto ``reference_map``'s frame.

    Useful for building a combined/blink view: pick the observer you want to view
    from (e.g. the Earth-view AIA or LASCO map) as the reference, then overlay the
    STEREO maps reprojected into that view.
    """
    reprojected = [reproject_map_to(m, reference_map, screen=screen) for m in other_maps]
    return CoalignedView(reference=reference_map, reprojected=reprojected)


def blink_pair(map_a: Any, map_b: Any, *, screen: str = SCREEN_SURFACE) -> tuple[Any, Any]:
    """Return ``(map_a, map_b_reprojected_onto_a)`` for a two-viewpoint blink."""
    return map_a, reproject_map_to(map_b, map_a, screen=screen)


def observer_separation_deg(map_a: Any, map_b: Any) -> float:
    """Heliographic (Stonyhurst) longitude separation between two maps' observers.

    A quick gauge of how much stereoscopic baseline two frames provide. Returns
    the absolute longitude difference in degrees.
    """
    import astropy.units as u

    lon_a = _observer_lon(map_a)
    lon_b = _observer_lon(map_b)
    diff = abs(float((lon_a - lon_b).to_value(u.deg)))
    # Wrap into [0, 180].
    diff = diff % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


def _observer_lon(smap: Any) -> Any:
    import astropy.units as u
    from sunpy.coordinates import HeliographicStonyhurst

    observer = smap.observer_coordinate.transform_to(HeliographicStonyhurst(obstime=smap.date))
    return observer.lon.to(u.deg)
