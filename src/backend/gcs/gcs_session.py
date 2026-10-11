"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

GCS fitting state for the project file (src/backend/gcs/gcs_session.py).

Qt-free. The GCS window hands :func:`serialize_gcs_state` a plain snapshot —
datetimes, tuples, the fit entries' ``_asdict()`` rows and provenance dicts —
and gets plain data back from :func:`deserialize_gcs_state` to rebuild itself.

Everything a reopened window needs travels here: per model the parameters,
the front points, the click history, the recorded and archived fits with
their observation provenance; per panel the source, range, colormap, contrast
and the frame-time listing that lets an embedded project reload offline; and
the shared display settings.

Front points are keyed by *(panel, frame observation time)*. In the window they
are keyed by the frame object's ``id()``, which means nothing once the frames
are reloaded, so the window converts both ways and this module never sees an id.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

GCS_STATE_FORMAT = "e-callisto-gcs-project-state"
GCS_STATE_VERSION = 1

MODEL_KEYS = ("gcs", "shock")

#: Fit-entry fields that are not floats.
_INT_FIELDS = {"n_points", "n_viewpoints"}
_BOOL_FIELDS = {"refined"}
_TEXT_FIELDS = {"model"}

#: Provenance values that are timestamps.
_PROVENANCE_TIME_KEYS = {"target_time_utc", "observation_time_utc", "difference_reference_utc", "obstime"}


# ------------------------------------------------------------------ primitives


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _json(value: Any) -> Any:
    """JSON-safe copy: ISO timestamps, lists for tuples, ``None`` for NaN."""
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


def _point(raw: Any) -> tuple[float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    x, y = _float(raw[0]), _float(raw[1])
    if x is None or y is None:
        return None
    return (x, y)


def _points(raw: Any) -> list[tuple[float, float]]:
    out = []
    for item in raw if isinstance(raw, (list, tuple)) else ():
        point = _point(item)
        if point is not None:
            out.append(point)
    return out


def _colour(raw: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        try:
            return tuple(max(0, min(255, int(raw[i]))) for i in range(3))  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass
    return default


# --------------------------------------------------------------------- fits


def _fit_row(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        key = str(key)
        if key == "when":
            out[key] = _iso(value)
        elif key in _TEXT_FIELDS:
            out[key] = str(value)
        elif key in _BOOL_FIELDS:
            out[key] = bool(value)
        elif key in _INT_FIELDS:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                out[key] = 0
        else:
            out[key] = _float(value)
    return out


def _read_fit_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    when = _parse_time(row.get("when"))
    height = _float(row.get("apex_height_rsun"))
    if when is None or height is None:
        # Without its time or height a record cannot be placed or fitted.
        return None
    out: dict[str, Any] = {"when": when}
    for key, value in row.items():
        key = str(key)
        if key == "when":
            continue
        if key in _TEXT_FIELDS:
            out[key] = str(value or "")
        elif key in _BOOL_FIELDS:
            out[key] = bool(value)
        elif key in _INT_FIELDS:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                out[key] = 0
        else:
            number = _float(value)
            out[key] = number if number is not None else float("nan")
    return out


def _provenance(value: Any) -> Any:
    """Restore the timestamps inside a provenance record, recursively."""
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if key in _PROVENANCE_TIME_KEYS:
                out[key] = _parse_time(item) if item is not None else None
            elif key == "front_points_arcsec":
                out[key] = _points(item)
            else:
                out[key] = _provenance(item)
        return out
    if isinstance(value, list):
        return [_provenance(item) for item in value]
    return value


def _serialize_track(track: Mapping[str, Any]) -> dict[str, Any]:
    def fits(rows: Iterable[Any]) -> list[dict[str, Any]]:
        return [_fit_row(row) for row in rows if isinstance(row, Mapping)]

    def provenance(mapping: Any) -> dict[str, Any]:
        if not isinstance(mapping, Mapping):
            return {}
        return {_iso(when) or str(when): _json(record) for when, record in mapping.items()}

    clicks = track.get("clicks") or {}
    return {
        "params": _json(track.get("params")) if track.get("params") is not None else None,
        "clicks": {str(panel): _json(points) for panel, points in dict(clicks).items()},
        "frame_points": [
            {"panel": str(row.get("panel")), "frame_time": _iso(row.get("frame_time")), "points": _json(row.get("points"))}
            for row in track.get("frame_points") or ()
            if isinstance(row, Mapping) and isinstance(row.get("frame_time"), datetime)
        ],
        "click_order": [
            {"panel": str(row.get("panel")), "frame_time": _iso(row.get("frame_time")), "point": _json(row.get("point"))}
            for row in track.get("click_order") or ()
            if isinstance(row, Mapping)
        ],
        "fits": fits(track.get("fits") or ()),
        "provenance": provenance(track.get("provenance")),
        "archived_fits": fits(track.get("archived_fits") or ()),
        "archived_provenance": provenance(track.get("archived_provenance")),
    }


def _deserialize_track(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, Mapping) else {}
    params = raw.get("params")
    if isinstance(params, Mapping):
        parsed: dict[str, Any] = {}
        for key, value in params.items():
            if key == "model":
                parsed[key] = str(value)
            else:
                number = _float(value)
                if number is not None:
                    parsed[str(key)] = number
        params = parsed or None
    else:
        params = None

    def fits(rows: Any) -> list[dict[str, Any]]:
        out = []
        for row in rows if isinstance(rows, list) else ():
            parsed_row = _read_fit_row(row)
            if parsed_row is not None:
                out.append(parsed_row)
        return out

    def provenance(mapping: Any) -> dict[datetime, Any]:
        out = {}
        if isinstance(mapping, Mapping):
            for key, record in mapping.items():
                when = _parse_time(key)
                if when is not None:
                    out[when] = _provenance(record)
        return out

    frame_points = []
    for row in raw.get("frame_points") or ():
        if not isinstance(row, Mapping):
            continue
        when = _parse_time(row.get("frame_time"))
        if when is None:
            continue
        frame_points.append({"panel": str(row.get("panel")), "frame_time": when, "points": _points(row.get("points"))})

    click_order = []
    for row in raw.get("click_order") or ():
        if not isinstance(row, Mapping):
            continue
        point = _point(row.get("point"))
        if point is None:
            continue
        click_order.append(
            {"panel": str(row.get("panel")), "frame_time": _parse_time(row.get("frame_time")), "point": point}
        )

    clicks = raw.get("clicks") if isinstance(raw.get("clicks"), Mapping) else {}
    return {
        "params": params,
        "clicks": {str(panel): _points(points) for panel, points in clicks.items()},
        "frame_points": frame_points,
        "click_order": click_order,
        "fits": fits(raw.get("fits")),
        "provenance": provenance(raw.get("provenance")),
        "archived_fits": fits(raw.get("archived_fits")),
        "archived_provenance": provenance(raw.get("archived_provenance")),
    }


# ------------------------------------------------------------------- panels


def _serialize_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "label": str(panel.get("label") or ""),
        "source_key": str(panel.get("source_key") or ""),
        "start": _iso(panel.get("start")),
        "end": _iso(panel.get("end")),
        "colormap": str(panel.get("colormap") or ""),
        "low": _float(panel.get("low")),
        "high": _float(panel.get("high")),
        "frame_times": [_iso(when) for when in panel.get("frame_times") or () if isinstance(when, datetime)],
        "listing": [_iso(when) for when in panel.get("listing") or () if isinstance(when, datetime)],
        "jp2_members": [str(name) for name in panel.get("jp2_members") or ()],
    }


def _deserialize_panel(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not raw.get("label"):
        return None

    def times(values: Any) -> list[datetime]:
        out = [_parse_time(value) for value in values or ()]
        return sorted(when for when in out if when is not None)

    return {
        "label": str(raw.get("label")),
        "source_key": str(raw.get("source_key") or ""),
        "start": _parse_time(raw.get("start")),
        "end": _parse_time(raw.get("end")),
        "colormap": str(raw.get("colormap") or ""),
        "low": _float(raw.get("low")),
        "high": _float(raw.get("high")),
        "frame_times": times(raw.get("frame_times")),
        "listing": times(raw.get("listing")),
        "jp2_members": [str(name) for name in raw.get("jp2_members") or ()],
    }


# --------------------------------------------------------------------- state


def serialize_gcs_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """The GCS window's snapshot as JSON-safe data for ``meta["gcs"]``."""
    if not isinstance(state, Mapping):
        return {}
    event = state.get("event_range") or (None, None)
    tracks = state.get("tracks") or {}
    return {
        "format": GCS_STATE_FORMAT,
        "version": GCS_STATE_VERSION,
        "event_range": [_iso(event[0]), _iso(event[1])] if len(event) == 2 else [None, None],
        "shared_time": _iso(state.get("shared_time")),
        "requested_time": _iso(state.get("requested_time")),
        "editing": str(state.get("editing") or "gcs"),
        "max_frames": int(state.get("max_frames") or 0) or None,
        "panels": [_serialize_panel(panel) for panel in state.get("panels") or () if isinstance(panel, Mapping)],
        "display": _json(dict(state.get("display") or {})),
        "tracks": {key: _serialize_track(tracks.get(key) or {}) for key in MODEL_KEYS},
    }


def deserialize_gcs_state(raw: Any) -> dict[str, Any]:
    """Read :func:`serialize_gcs_state` output back; tolerant of missing keys."""
    if not isinstance(raw, Mapping):
        return {}
    event = raw.get("event_range") or [None, None]
    start = _parse_time(event[0]) if len(event) > 0 else None
    end = _parse_time(event[1]) if len(event) > 1 else None
    display = dict(raw.get("display") or {}) if isinstance(raw.get("display"), Mapping) else {}
    for key, default in (("wireframe_colour", (255, 140, 40)), ("shock_colour", (86, 180, 233))):
        if key in display:
            display[key] = _colour(display[key], default)
    tracks = raw.get("tracks") if isinstance(raw.get("tracks"), Mapping) else {}
    editing = str(raw.get("editing") or "gcs")
    try:
        max_frames = int(raw.get("max_frames") or 0) or None
    except (TypeError, ValueError):
        max_frames = None
    return {
        "event_range": (start, end) if start is not None and end is not None and end > start else None,
        "shared_time": _parse_time(raw.get("shared_time")),
        "requested_time": _parse_time(raw.get("requested_time")),
        "editing": editing if editing in MODEL_KEYS else "gcs",
        "max_frames": max_frames,
        "panels": [panel for panel in (_deserialize_panel(item) for item in raw.get("panels") or ()) if panel],
        "display": display,
        "tracks": {key: _deserialize_track(tracks.get(key)) for key in MODEL_KEYS},
    }


def gcs_state_has_content(raw: Any) -> bool:
    """Whether a serialized state holds anything worth reopening."""
    state = raw if isinstance(raw, Mapping) else {}
    tracks = state.get("tracks") or {}
    for track in tracks.values() if isinstance(tracks, Mapping) else ():
        if isinstance(track, Mapping) and (track.get("fits") or track.get("frame_points") or track.get("archived_fits")):
            return True
    return any(panel.get("frame_times") for panel in state.get("panels") or () if isinstance(panel, Mapping))


def gcs_fit_count(raw: Any) -> int:
    state = raw if isinstance(raw, Mapping) else {}
    tracks = state.get("tracks") or {}
    return sum(
        len(track.get("fits") or ())
        for track in (tracks.values() if isinstance(tracks, Mapping) else ())
        if isinstance(track, Mapping)
    )


def match_frame_time(when: datetime | None, candidates: Iterable[datetime], *, tolerance_s: float = 1.0) -> datetime | None:
    """The candidate equal to ``when`` within ``tolerance_s`` (nearest), else None.

    JP2 headers and the archive listing round observation times differently, so
    a reloaded frame's time can differ from the saved one by a fraction of a
    second; anything further apart is a different frame.
    """
    if when is None:
        return None
    best = None
    best_gap = None
    for candidate in candidates:
        if candidate is None:
            continue
        gap = abs((candidate - when).total_seconds())
        if gap <= tolerance_s and (best_gap is None or gap < best_gap):
            best, best_gap = candidate, gap
    return best
