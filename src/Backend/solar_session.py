"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Self-contained session files for the Solar Image Analysis window.

A solar session is a single ``.ecsolar`` zip that bundles *everything* needed to
reopen an analysis exactly where it was left off:

* the original FITS frame files (raw bytes, so restore goes back through the
  normal ``Map()`` load path and keeps every header/WCS/colormap detail), and
* a ``meta.json`` describing the data source, the display/crop/playback state,
  and the hand-made CME height-time picks.

Frames are embedded rather than referenced so a session survives a cleared cache
or a move to another machine, at the cost of a large file. This module is pure
stdlib (no Qt, no SunPy, no numpy) so it unit-tests without a display or network.
The window layer (`solar_data_analysis_window`) is responsible for reading state
off its widgets into the ``meta`` dict and pushing it back after a restore.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

SOLAR_SESSION_MAGIC = "e-callisto-solar-analysis"
SOLAR_SCHEMA_VERSION = 1

META_FILENAME = "meta.json"
FRAMES_DIR = "frames"

_ARCNAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SolarSessionError(RuntimeError):
    """Raised when a ``.ecsolar`` file is malformed or unreadable."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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


# ------------------------------------------------------------------------- writing


@dataclass(frozen=True)
class SolarSessionResult:
    """What :func:`read_solar_session` hands back to the window."""

    meta: dict[str, Any]
    frame_paths: list[str]  # extracted FITS files, in the saved load order


def _frame_manifest(frame_paths: Sequence[str]) -> tuple[list[dict[str, str]], list[str]]:
    """Assign each source file a unique in-zip name, preserving order."""
    manifest: list[dict[str, str]] = []
    used: set[str] = set()
    kept_paths: list[str] = []
    for i, path in enumerate(frame_paths):
        text = str(path or "").strip()
        if not text:
            continue
        base = _safe_basename(text)
        arcname = f"{FRAMES_DIR}/{i:04d}_{base}"
        # f-string index already guarantees uniqueness, but guard anyway.
        suffix = 1
        while arcname in used:
            arcname = f"{FRAMES_DIR}/{i:04d}_{suffix}_{base}"
            suffix += 1
        used.add(arcname)
        manifest.append({"arcname": arcname, "name": base})
        kept_paths.append(text)
    return manifest, kept_paths


def write_solar_session(
    path: str,
    *,
    meta: Mapping[str, Any],
    frame_paths: Sequence[str],
) -> int:
    """Write a ``.ecsolar`` session bundling ``meta`` and the FITS frame files.

    ``frame_paths`` are copied byte-for-byte into the archive under ``frames/``.
    Returns the number of frame files actually embedded. Raises
    :class:`SolarSessionError` if none of the given files could be read (a
    session with no frames cannot be restored).
    """
    manifest, kept_paths = _frame_manifest(frame_paths)
    if not manifest:
        raise SolarSessionError("Cannot save a solar session with no frame files.")

    payload_meta = dict(meta)
    payload_meta["magic"] = SOLAR_SESSION_MAGIC
    payload_meta["schema_version"] = SOLAR_SCHEMA_VERSION
    payload_meta.setdefault("app", "e-CALLISTO FITS Analyzer")
    payload_meta.setdefault("created_at", _now_iso())
    payload_meta["frame_files"] = manifest

    embedded = 0
    tmp_path = f"{path}.tmp"
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for entry, source_path in zip(manifest, kept_paths):
                try:
                    with open(source_path, "rb") as handle:
                        data = handle.read()
                except OSError as exc:
                    raise SolarSessionError(
                        f"Could not read frame file for the session:\n{source_path}\n{exc}"
                    ) from exc
                zf.writestr(entry["arcname"], data)
                embedded += 1
            meta_bytes = json.dumps(
                payload_meta, indent=2, sort_keys=True, ensure_ascii=False
            ).encode("utf-8")
            zf.writestr(META_FILENAME, meta_bytes)
    except Exception:
        _quiet_remove(tmp_path)
        raise

    os.replace(tmp_path, path)
    return embedded


def _quiet_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ------------------------------------------------------------------------- reading


def read_solar_session(path: str, *, extract_dir: str) -> SolarSessionResult:
    """Read a ``.ecsolar`` session and extract its frames into ``extract_dir``.

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
            os.makedirs(extract_dir, exist_ok=True)
            frame_paths = _extract_frames(zf, manifest, extract_dir)
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
    """Reject absolute paths and ``..`` traversal; only allow ``frames/<file>``."""
    if not arcname or arcname != arcname.strip():
        return False
    normalized = arcname.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        return False
    parts = normalized.split("/")
    if len(parts) != 2 or parts[0] != FRAMES_DIR:
        return False
    leaf = parts[1]
    return bool(leaf) and leaf not in {".", ".."}


def _extract_frames(
    zf: zipfile.ZipFile, manifest: Sequence[Mapping[str, str]], extract_dir: str
) -> list[str]:
    frame_paths: list[str] = []
    used: set[str] = set()
    for entry in manifest:
        arcname = entry["arcname"]
        leaf = arcname.split("/")[-1]
        out_name = leaf
        suffix = 1
        while out_name.lower() in used:
            stem, ext = os.path.splitext(leaf)
            out_name = f"{stem}_{suffix}{ext}"
            suffix += 1
        used.add(out_name.lower())
        out_path = os.path.join(extract_dir, out_name)
        with zf.open(arcname) as src, open(out_path, "wb") as dst:
            dst.write(src.read())
        frame_paths.append(out_path)
    return frame_paths


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
