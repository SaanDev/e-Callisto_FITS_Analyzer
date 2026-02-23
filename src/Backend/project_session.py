"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import numpy as np

MAGIC = "e-callisto-fits-analyzer-project"
SCHEMA_VERSION = 1

META_FILENAME = "meta.json"
ARRAYS_FILENAME = "arrays.npz"


class ProjectFormatError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _json_default(obj: Any):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (tuple, set)):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def write_project(
    path: str,
    *,
    meta: Mapping[str, Any],
    arrays: Mapping[str, Any] | None = None,
) -> None:
    payload_meta = dict(meta)
    payload_meta.setdefault("magic", MAGIC)
    payload_meta.setdefault("schema_version", SCHEMA_VERSION)
    payload_meta.setdefault("created_at", _now_iso())

    arrays = dict(arrays or {})
    arrays_payload: dict[str, np.ndarray] = {}
    for key, value in arrays.items():
        if value is None:
            continue
        arrays_payload[str(key)] = np.asarray(value)

    meta_bytes = json.dumps(
        payload_meta,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")

    arrays_buf = io.BytesIO()
    np.savez_compressed(arrays_buf, **arrays_payload)
    arrays_bytes = arrays_buf.getvalue()

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(META_FILENAME, meta_bytes)
        zf.writestr(ARRAYS_FILENAME, arrays_bytes)


@dataclass(frozen=True)
class ProjectPayload:
    meta: dict[str, Any]
    arrays: dict[str, np.ndarray]


def read_project(path: str) -> ProjectPayload:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            try:
                meta_raw = zf.read(META_FILENAME)
                arrays_raw = zf.read(ARRAYS_FILENAME)
            except KeyError as e:
                raise ProjectFormatError(f"Missing required file in project: {e}") from e
    except zipfile.BadZipFile as e:
        raise ProjectFormatError("Not a valid project file (bad zip).") from e

    try:
        meta = json.loads(meta_raw.decode("utf-8"))
    except Exception as e:
        raise ProjectFormatError("Invalid meta.json (not valid UTF-8 JSON).") from e

    if meta.get("magic") != MAGIC:
        raise ProjectFormatError("Unrecognized project file (magic mismatch).")
    version = int(meta.get("schema_version", 0))
    if version != SCHEMA_VERSION:
        raise ProjectFormatError(f"Unsupported project schema_version={version}.")

    arrays: dict[str, np.ndarray] = {}
    try:
        with np.load(io.BytesIO(arrays_raw), allow_pickle=False) as npz:
            for key in npz.files:
                arrays[key] = npz[key]
    except Exception as e:
        raise ProjectFormatError("Invalid arrays.npz (cannot be read safely).") from e

    return ProjectPayload(meta=meta, arrays=arrays)

