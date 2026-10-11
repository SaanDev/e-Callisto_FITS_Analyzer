"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Legacy ``.ecsolar`` sessions and the solar measurement serializers.

Until v3.1 the Solar Image Analysis window saved its own ``.ecsolar`` file: a
zip of ``meta.json`` plus the original FITS frames under ``frames/``. Since
v3.2 the solar analysis is a part of the one ``.efaproj`` project (see
``project_session`` and ``src.ui.app.project_parts``) and the app never writes
``.ecsolar`` again. Old sessions are still imported, read-only, with
:func:`import_legacy_solar_session`.

The picks / circle-fit / PFSS serializers here are shared by both formats: a
project's ``meta["solar"]`` is the same session meta, so the solar window keeps
one capture and one restore path. This module is pure stdlib (no Qt, no SunPy,
no numpy) so it unit-tests without a display or network.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from src.backend.session.project_session import ProjectFormatError, extract_members, is_safe_arcname

SOLAR_SESSION_MAGIC = "e-callisto-solar-analysis"
SOLAR_SCHEMA_VERSION = 1

META_FILENAME = "meta.json"
FRAMES_DIR = "frames"

_ARCNAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SolarSessionError(RuntimeError):
    """Raised when a ``.ecsolar`` file is malformed or unreadable."""


def _safe_basename(path: str) -> str:
    """A collision-free, path-traversal-free display name for a source file."""
    name = os.path.basename(str(path or "").replace("\\", "/")).strip()
    name = _ARCNAME_RE.sub("_", name)
    return name or "frame.fits"


def _parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- picks


def serialize_picks(picks: Mapping[int, Sequence[Any]] | None) -> list[dict[str, Any]]:
    """Flatten the controller's ``{frame_index: (when, h, x, y, pa)}`` picks.

    ``when`` is a ``datetime`` (or ``None``); everything else is a float. The
    output is JSON-safe and ordered by frame index so a saved session reads
    deterministically.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(picks, Mapping):
        return out
    for idx in sorted(picks.keys(), key=lambda k: _safe_int(k, 0)):
        entry = picks[idx]
        if not isinstance(entry, Sequence) or len(entry) < 5:
            continue
        when, height, x_arc, y_arc, pa = entry[0], entry[1], entry[2], entry[3], entry[4]
        when_iso: str | None
        if isinstance(when, datetime):
            when_iso = when.isoformat()
        else:
            when_iso = str(when) if when else None
        out.append(
            {
                "frame_index": _safe_int(idx, 0),
                "time": when_iso,
                "height_rsun": _safe_float(height),
                "x_arc": _safe_float(x_arc),
                "y_arc": _safe_float(y_arc),
                "pa_deg": _safe_float(pa),
            }
        )
    return out


def deserialize_picks(raw: Any) -> dict[int, tuple[datetime | None, float, float, float, float]]:
    """Rebuild the controller pick map from :func:`serialize_picks` output.

    Rows missing a numeric coordinate are dropped rather than restored with a
    bogus 0.0, which would silently corrupt a height-time fit.
    """
    out: dict[int, tuple[datetime | None, float, float, float, float]] = {}
    if not isinstance(raw, Iterable):
        return out
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        height = _safe_float(row.get("height_rsun"))
        x_arc = _safe_float(row.get("x_arc"))
        y_arc = _safe_float(row.get("y_arc"))
        pa = _safe_float(row.get("pa_deg"))
        if None in (height, x_arc, y_arc, pa):
            continue
        idx = _safe_int(row.get("frame_index"), 0)
        out[idx] = (_parse_iso(row.get("time")), float(height), float(x_arc), float(y_arc), float(pa))
    return out


# ------------------------------------------------------------------- circle fits


def serialize_circle_fits(circles: Mapping[int, Sequence[Any]] | None) -> list[dict[str, Any]]:
    """Flatten the controller's ``{frame_index: CircleFitEntry}`` circle fits.

    Entries are read positionally, exactly like :func:`serialize_picks` reads a
    pick tuple, so this module keeps knowing nothing about the UI layer's types.

    These live under a *new* key in ``meta["measurements"]`` rather than behind a
    schema bump: ``_load_meta`` rejects any version but the current one, so a bump
    would make today's sessions unreadable by the new build and tomorrow's
    unreadable by every shipped one. An unknown key is simply ignored on read,
    which degrades to "the circles are missing" instead of "the file is refused".
    """
    out: list[dict[str, Any]] = []
    if not isinstance(circles, Mapping):
        return out
    for idx in sorted(circles.keys(), key=lambda k: _safe_int(k, 0)):
        entry = circles[idx]
        if not isinstance(entry, Sequence) or len(entry) < 10:
            continue
        when = entry[0]
        when_iso: str | None
        if isinstance(when, datetime):
            when_iso = when.isoformat()
        else:
            when_iso = str(when) if when else None
        out.append(
            {
                "frame_index": _safe_int(idx, 0),
                "time": when_iso,
                "height_rsun": _safe_float(entry[1]),
                "radius_rsun": _safe_float(entry[9]),
                "center_x_arc": _safe_float(entry[2]),
                "center_y_arc": _safe_float(entry[3]),
                "radius_arcsec": _safe_float(entry[4]),
                "leading_edge_rsun": _safe_float(entry[5]),
                "rms_arcsec": _safe_float(entry[6]),
                "n_points": _safe_int(entry[7], 0),
                "center_pa_deg": _safe_float(entry[8]),
            }
        )
    return out


def deserialize_circle_fits(raw: Any) -> dict[int, tuple]:
    """Rebuild the circle-fit map from :func:`serialize_circle_fits` output.

    Rows without a centre, or with neither a height nor a radius, are dropped: a
    bogus 0.0 there would silently corrupt the height-time fit. The reported
    extras (leading edge, rms, position angle) are re-derived or defaulted when
    missing instead.

    Sessions saved before the height was stored carry only ``radius_rsun``,
    which those builds fitted as if it were the height. The spherical-bubble
    height ``1 + 2r`` (``image_measure.spherical_bubble_height_rsun``, restated
    here because this module stays stdlib-only) is re-derived from it, so an old
    analysis reopens with the corrected kinematics.
    """
    out: dict[int, tuple] = {}
    if not isinstance(raw, Iterable):
        return out
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        height_rsun = _safe_float(row.get("height_rsun"))
        radius_rsun = _safe_float(row.get("radius_rsun"))
        if height_rsun is None and radius_rsun is not None:
            height_rsun = 1.0 + 2.0 * radius_rsun
        elif radius_rsun is None and height_rsun is not None:
            radius_rsun = (height_rsun - 1.0) / 2.0
        center_x = _safe_float(row.get("center_x_arc"))
        center_y = _safe_float(row.get("center_y_arc"))
        radius_arcsec = _safe_float(row.get("radius_arcsec"))
        if None in (height_rsun, radius_rsun, center_x, center_y, radius_arcsec):
            continue
        lead = _safe_float(row.get("leading_edge_rsun"))
        if lead is None:
            lead = radius_rsun
        rms = _safe_float(row.get("rms_arcsec"))
        pa = _safe_float(row.get("center_pa_deg"))
        idx = _safe_int(row.get("frame_index"), 0)
        out[idx] = (
            _parse_iso(row.get("time")),
            float(height_rsun),
            float(center_x),
            float(center_y),
            float(radius_arcsec),
            float(lead),
            float(rms) if rms is not None else 0.0,
            _safe_int(row.get("n_points"), 0),
            float(pa) if pa is not None else 0.0,
            float(radius_rsun),
        )
    return out


# --- PFSS -----------------------------------------------------------------------


def serialize_pfss_state(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """Flatten the PFSS section's settings for a saved session.

    Settings only -- never the solution. A solve takes a couple of seconds and
    depends on a magnetogram that lives in the shared cache, so storing the
    traced field would bloat the session for something cheap to reproduce. The
    magnetogram's identity is kept so a restored session says which boundary
    condition the numbers came from.

    Stored under a new key, deliberately not behind a schema bump: an unknown key
    is ignored by an older reader, whereas bumping ``SOLAR_SCHEMA_VERSION`` makes
    every existing session file be *rejected* rather than read.

    Every value is cast to a JSON-native type here, because the session writer
    calls ``json.dumps`` without a ``default=`` handler -- a stray ``numpy``
    scalar would abort the whole save.
    """
    if not isinstance(state, Mapping):
        return {}
    out: dict[str, Any] = {
        "source": str(state.get("source") or ""),
        "nrho": _safe_int(state.get("nrho"), 35),
        "rss": _safe_float(state.get("rss")),
        "seed_mode": str(state.get("seed_mode") or ""),
        "seed_density": _safe_int(state.get("seed_density"), 24),
        "realization": _safe_int(state.get("realization"), 0),
        "show_open": bool(state.get("show_open", True)),
        "show_closed": bool(state.get("show_closed", True)),
        "show_boundaries": bool(state.get("show_boundaries", True)),
        "near_side_only": bool(state.get("near_side_only", True)),
    }
    for key in ("magnetogram_path", "magnetogram_label", "carrington_rotation"):
        value = state.get(key)
        if value in (None, ""):
            continue
        out[key] = _safe_int(value, 0) if key == "carrington_rotation" else str(value)
    clicked = state.get("clicked_arcsec")
    if isinstance(clicked, Sequence):
        points: list[list[float]] = []
        for item in clicked:
            if isinstance(item, Sequence) and len(item) >= 2:
                points.append([_safe_float(item[0]) or 0.0, _safe_float(item[1]) or 0.0])
        if points:
            out["clicked_arcsec"] = points
    return out


def deserialize_pfss_state(payload: Any) -> dict[str, Any]:
    """Read back :func:`serialize_pfss_state`, tolerating a missing key."""
    if not isinstance(payload, Mapping):
        return {}
    out = dict(payload)
    clicked = out.get("clicked_arcsec")
    if isinstance(clicked, Sequence):
        out["clicked_arcsec"] = [
            (float(item[0]), float(item[1]))
            for item in clicked
            if isinstance(item, Sequence) and len(item) >= 2
        ]
    return out


def serialize_circle_points(points: Mapping[int, Sequence[Any]] | None) -> list[dict[str, Any]]:
    """Flatten the in-progress circle clicks ``{frame_index: [(x, y), ...]}``.

    Saved alongside the fits so reopening a session can redraw the arc a user
    actually clicked, and let them add a point rather than start the frame over.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(points, Mapping):
        return out
    for idx in sorted(points.keys(), key=lambda k: _safe_int(k, 0)):
        coords: list[list[float]] = []
        for point in list(points[idx] or []):
            if not isinstance(point, Sequence) or len(point) < 2:
                continue
            x_arc = _safe_float(point[0])
            y_arc = _safe_float(point[1])
            if x_arc is None or y_arc is None:
                continue
            coords.append([float(x_arc), float(y_arc)])
        if coords:
            out.append({"frame_index": _safe_int(idx, 0), "points": coords})
    return out


def deserialize_circle_points(raw: Any) -> dict[int, list[tuple[float, float]]]:
    """Rebuild the click map from :func:`serialize_circle_points` output."""
    out: dict[int, list[tuple[float, float]]] = {}
    if not isinstance(raw, Iterable):
        return out
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        coords: list[tuple[float, float]] = []
        for point in list(row.get("points") or []):
            if not isinstance(point, Sequence) or len(point) < 2:
                continue
            x_arc = _safe_float(point[0])
            y_arc = _safe_float(point[1])
            if x_arc is None or y_arc is None:
                continue
            coords.append((float(x_arc), float(y_arc)))
        if coords:
            out[_safe_int(row.get("frame_index"), 0)] = coords
    return out


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


# ------------------------------------------------------------------------- reading


@dataclass(frozen=True)
class SolarSessionResult:
    """What :func:`import_legacy_solar_session` hands back to the window."""

    meta: dict[str, Any]
    frame_paths: list[str]  # extracted FITS files, in the saved load order



def import_legacy_solar_session(path: str, *, extract_dir: str) -> SolarSessionResult:
    """Read a legacy ``.ecsolar`` session and extract its frames into ``extract_dir``.

    The returned ``frame_paths`` are in the original load order so replaying them
    through the normal FITS loader reproduces the same frame sequence (and hence
    keeps the index-keyed height-time picks aligned).
    """
    try:
        with zipfile.ZipFile(path, "r") as zf:
            try:
                meta_raw = zf.read(META_FILENAME)
            except KeyError as exc:
                raise SolarSessionError("Not a valid solar session (missing meta.json).") from exc
            meta = _load_meta(meta_raw)
            manifest = _validate_manifest(meta, zf)
            try:
                extracted = extract_members(zf, [entry["arcname"] for entry in manifest], extract_dir)
            except ProjectFormatError as exc:
                raise SolarSessionError(str(exc)) from exc
            frame_paths = [extracted[entry["arcname"]] for entry in manifest]
    except zipfile.BadZipFile as exc:
        raise SolarSessionError("Not a valid solar session file (bad zip).") from exc

    if not frame_paths:
        raise SolarSessionError("Solar session contains no frame files.")
    return SolarSessionResult(meta=meta, frame_paths=frame_paths)


def _load_meta(meta_raw: bytes) -> dict[str, Any]:
    try:
        meta = json.loads(meta_raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - report any decode/parse failure uniformly
        raise SolarSessionError("Invalid session metadata (not valid UTF-8 JSON).") from exc
    if not isinstance(meta, dict):
        raise SolarSessionError("Invalid session metadata (expected a JSON object).")
    if meta.get("magic") != SOLAR_SESSION_MAGIC:
        raise SolarSessionError("Unrecognized session file (magic mismatch).")
    version = _safe_int(meta.get("schema_version"), 0)
    if version != SOLAR_SCHEMA_VERSION:
        raise SolarSessionError(f"Unsupported session schema_version={version}.")
    return meta


def _validate_manifest(meta: Mapping[str, Any], zf: zipfile.ZipFile) -> list[dict[str, str]]:
    raw_manifest = meta.get("frame_files")
    if not isinstance(raw_manifest, list) or not raw_manifest:
        raise SolarSessionError("Session metadata lists no frame files.")
    names = set(zf.namelist())
    manifest: list[dict[str, str]] = []
    for entry in raw_manifest:
        if not isinstance(entry, Mapping):
            continue
        arcname = str(entry.get("arcname") or "")
        if not _is_safe_frame_arcname(arcname):
            raise SolarSessionError(f"Session contains an unsafe frame path: {arcname!r}")
        if arcname not in names:
            raise SolarSessionError(f"Session is missing an embedded frame: {arcname}")
        manifest.append({"arcname": arcname, "name": str(entry.get("name") or _safe_basename(arcname))})
    if not manifest:
        raise SolarSessionError("Session metadata lists no usable frame files.")
    return manifest


def _is_safe_frame_arcname(arcname: str) -> bool:
    """A safe member path (see ``project_session.is_safe_arcname``) directly under ``frames/``."""
    if not is_safe_arcname(arcname):
        return False
    parts = arcname.split("/")
    return len(parts) == 2 and parts[0] == FRAMES_DIR


# ---------------------------------------------------------------------- summaries


def session_frame_count(meta: Mapping[str, Any] | None) -> int:
    if not isinstance(meta, Mapping):
        return 0
    manifest = meta.get("frame_files")
    return len(manifest) if isinstance(manifest, list) else 0


def session_pick_count(meta: Mapping[str, Any] | None) -> int:
    if not isinstance(meta, Mapping):
        return 0
    measurements = meta.get("measurements")
    if not isinstance(measurements, Mapping):
        return 0
    picks = measurements.get("height_time_picks")
    return len(picks) if isinstance(picks, list) else 0


def session_circle_count(meta: Mapping[str, Any] | None) -> int:
    if not isinstance(meta, Mapping):
        return 0
    measurements = meta.get("measurements")
    if not isinstance(measurements, Mapping):
        return 0
    circles = measurements.get("circle_fits")
    return len(circles) if isinstance(circles, list) else 0
