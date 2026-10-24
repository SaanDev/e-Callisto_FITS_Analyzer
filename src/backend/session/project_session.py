"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The ``.efaproj`` project file.

A project is a zip holding ``meta.json``, an optional ``arrays.npz`` and, from
schema 2 on, any number of embedded files (FITS frames, JPEG2000 images):

* ``meta.json`` — the radio analysis keys at the top level (exactly as schema 1
  wrote them), plus ``parts`` (which parts the project holds), ``storage``
  (``"link"`` or ``"embed"``) and one section per non-radio part.
* ``arrays.npz`` — numeric arrays, every part's under its own key prefix.
* ``solar/frames/NNNN_<name>`` and ``gcs/jp2/<panel>/hv<id>_<time>.jp2`` —
  embedded image files. They are stored uncompressed (they are compressed
  already, or too noisy to gain anything); everything else is deflated.

Writes are atomic: the archive is written next to the target and renamed over
it only once it is complete, so a failed save never leaves a partial project.
Schema 1 files (radio only) still open; v3.1 builds refuse schema 2 with their
"Unsupported schema_version" error.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import numpy as np

MAGIC = "e-callisto-fits-analyzer-project"
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)

META_FILENAME = "meta.json"
ARRAYS_FILENAME = "arrays.npz"

STORAGE_LINK = "link"
STORAGE_EMBED = "embed"
STORAGE_MODES = (STORAGE_LINK, STORAGE_EMBED)

#: Every part a project can hold, in the order they are restored.
PART_KEYS = ("radio", "solar", "gcs", "geomagnetic", "event_context", "composer")

#: Embedded members stored without compression.
_STORED_SUFFIXES = (".fits", ".fit", ".fts", ".fz", ".gz", ".jp2", ".jpx")

_MEMBER_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_COPY_CHUNK = 1024 * 1024


class ProjectFormatError(RuntimeError):
    pass


class ProjectSaveCancelled(RuntimeError):
    """The caller cancelled a save; no file was written."""


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
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ----------------------------------------------------------------- member names


def safe_member_name(path: str, default: str = "file") -> str:
    """A path-traversal-free leaf name for a file embedded in a project."""
    name = os.path.basename(str(path or "").replace("\\", "/")).strip()
    name = _MEMBER_NAME_RE.sub("_", name).strip(".")
    return name or default


def is_safe_arcname(arcname: str) -> bool:
    """True for a relative, forward-slash member path with no traversal.

    Rejects absolute paths, drive letters, ``..`` and ``.`` segments, empty
    segments, backslashes and the two reserved top-level members.
    """
    text = str(arcname or "")
    if not text or text != text.strip():
        return False
    if "\\" in text or ":" in text or text.startswith("/"):
        return False
    if text in (META_FILENAME, ARRAYS_FILENAME):
        return False
    parts = text.split("/")
    return all(part and part not in {".", ".."} for part in parts)


def _compression_for(arcname: str) -> int:
    lower = arcname.lower()
    return zipfile.ZIP_STORED if lower.endswith(_STORED_SUFFIXES) else zipfile.ZIP_DEFLATED


# ----------------------------------------------------------------------- parts


def project_parts(meta: Mapping[str, Any] | None) -> dict[str, bool]:
    """Which parts a project holds.

    Schema 1 files predate parts: they are radio-only projects.
    """
    out = {key: False for key in PART_KEYS}
    if not isinstance(meta, Mapping):
        return out
    raw = meta.get("parts")
    if isinstance(raw, Mapping):
        for key in PART_KEYS:
            out[key] = bool(raw.get(key, False))
        return out
    out["radio"] = True
    return out


def project_storage(meta: Mapping[str, Any] | None) -> str:
    value = str((meta or {}).get("storage") or STORAGE_LINK) if isinstance(meta, Mapping) else STORAGE_LINK
    return value if value in STORAGE_MODES else STORAGE_LINK


def estimate_files_size(files: Mapping[str, str] | None) -> int:
    """Bytes the given files would add to a project (their size on disk)."""
    total = 0
    for src in dict(files or {}).values():
        try:
            total += int(os.path.getsize(src))
        except OSError:
            continue
    return total


# --------------------------------------------------------------------- writing


def write_project(
    path: str,
    *,
    meta: Mapping[str, Any],
    arrays: Mapping[str, Any] | None = None,
    files: Mapping[str, str] | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> None:
    """Write a project atomically.

    ``files`` maps an in-archive member name to a file on disk; the bytes are
    streamed in, so large FITS sequences never sit in memory. ``progress_cb``
    receives ``(bytes_done, bytes_total, text)``. A ``cancel_cb`` returning True
    aborts with :class:`ProjectSaveCancelled` and leaves no file behind.
    """
    payload_meta = dict(meta)
    payload_meta.setdefault("magic", MAGIC)
    payload_meta["schema_version"] = SCHEMA_VERSION
    payload_meta.setdefault("created_at", _now_iso())

    arrays_payload: dict[str, np.ndarray] = {}
    for key, value in dict(arrays or {}).items():
        if value is None:
            continue
        arrays_payload[str(key)] = np.asarray(value)

    members: list[tuple[str, str]] = []
    for arcname, src in dict(files or {}).items():
        arcname = str(arcname)
        if not is_safe_arcname(arcname):
            raise ProjectFormatError(f"Refusing to embed an unsafe member name: {arcname!r}")
        members.append((arcname, str(src)))
    members.sort(key=lambda item: item[0])
    payload_meta["files"] = [arcname for arcname, _src in members]

    meta_bytes = json.dumps(
        payload_meta,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")

    arrays_bytes = b""
    if arrays_payload:
        arrays_buf = io.BytesIO()
        np.savez_compressed(arrays_buf, **arrays_payload)
        arrays_bytes = arrays_buf.getvalue()

    total = estimate_files_size({arc: src for arc, src in members}) + len(meta_bytes) + len(arrays_bytes)
    done = 0

    def report(text: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(int(done), int(max(total, 1)), text)
            except Exception:
                pass

    def check_cancel() -> None:
        try:
            cancelled = bool(cancel_cb and cancel_cb())
        except Exception:
            cancelled = False
        if cancelled:
            raise ProjectSaveCancelled("Project save cancelled.")

    tmp_path = f"{path}.tmp"
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr(META_FILENAME, meta_bytes)
            done += len(meta_bytes)
            if arrays_bytes:
                zf.writestr(ARRAYS_FILENAME, arrays_bytes)
                done += len(arrays_bytes)
            report("Writing project…")
            for arcname, src in members:
                check_cancel()
                try:
                    handle = open(src, "rb")
                except OSError as exc:
                    raise ProjectFormatError(f"Could not read a file to embed:\n{src}\n{exc}") from exc
                info = zipfile.ZipInfo(arcname, date_time=_zip_timestamp(src))
                info.compress_type = _compression_for(arcname)
                info.external_attr = 0o644 << 16
                with handle, zf.open(info, "w", force_zip64=True) as dst:
                    while True:
                        check_cancel()
                        chunk = handle.read(_COPY_CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
                        done += len(chunk)
                report(f"Embedded {safe_member_name(arcname)}")
        check_cancel()
    except BaseException:
        _quiet_remove(tmp_path)
        raise
    os.replace(tmp_path, path)
    report("Project saved.")


def _zip_timestamp(path: str) -> tuple[int, int, int, int, int, int]:
    try:
        stamp = datetime.fromtimestamp(os.path.getmtime(path))
    except (OSError, OverflowError, ValueError):
        stamp = datetime.now()
    if stamp.year < 1980:
        stamp = datetime(1980, 1, 1)
    return (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)


def _quiet_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------- reading


@dataclass(frozen=True)
class ProjectPayload:
    meta: dict[str, Any]
    arrays: dict[str, np.ndarray]
    #: Embedded member name -> extracted path (empty unless extracted).
    files: dict[str, str] = field(default_factory=dict)


def _load_meta(meta_raw: bytes) -> dict[str, Any]:
    try:
        meta = json.loads(meta_raw.decode("utf-8"))
    except Exception as e:
        raise ProjectFormatError("Invalid meta.json (not valid UTF-8 JSON).") from e
    if not isinstance(meta, dict):
        raise ProjectFormatError("Invalid meta.json (expected a JSON object).")
    if meta.get("magic") != MAGIC:
        raise ProjectFormatError("Unrecognized project file (magic mismatch).")
    try:
        version = int(meta.get("schema_version", 0))
    except (TypeError, ValueError):
        version = 0
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ProjectFormatError(f"Unsupported project schema_version={version}.")
    return meta


def read_project_meta(path: str) -> dict[str, Any]:
    """Only the validated ``meta.json`` of a project (cheap: no arrays, no files)."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            try:
                return _load_meta(zf.read(META_FILENAME))
            except KeyError as e:
                raise ProjectFormatError(f"Missing required file in project: {e}") from e
    except zipfile.BadZipFile as e:
        raise ProjectFormatError("Not a valid project file (bad zip).") from e


def read_project(
    path: str,
    *,
    extract_dir: str | os.PathLike[str] | Callable[[dict[str, Any]], Any] | None = None,
) -> ProjectPayload:
    """Read a project; extract embedded files when ``extract_dir`` is given.

    ``extract_dir`` may be a callable receiving the validated meta, for callers
    whose extraction folder depends on the project's identity. Every member
    name is checked before anything is written: an archive with an unsafe name
    is refused as a whole.
    """
    try:
        with zipfile.ZipFile(path, "r") as zf:
            try:
                meta_raw = zf.read(META_FILENAME)
            except KeyError as e:
                raise ProjectFormatError(f"Missing required file in project: {e}") from e
            meta = _load_meta(meta_raw)
            names = zf.namelist()
            arrays_raw = zf.read(ARRAYS_FILENAME) if ARRAYS_FILENAME in names else None
            if arrays_raw is None and int(meta.get("schema_version", 0)) < 2:
                raise ProjectFormatError(f"Missing required file in project: {ARRAYS_FILENAME!r}")
            members = [name for name in names if name not in (META_FILENAME, ARRAYS_FILENAME)]
            members = [name for name in members if not name.endswith("/")]
            for name in members:
                if not is_safe_arcname(name):
                    raise ProjectFormatError(f"Project contains an unsafe file path: {name!r}")

            files: dict[str, str] = {}
            if members:
                # Only a project with embedded files touches the folder: a linked
                # one may reference files unpacked there by an earlier open.
                target = extract_dir(meta) if callable(extract_dir) else extract_dir
                if target is not None:
                    files = extract_members(zf, members, str(target))
    except zipfile.BadZipFile as e:
        raise ProjectFormatError("Not a valid project file (bad zip).") from e

    arrays: dict[str, np.ndarray] = {}
    if arrays_raw:
        try:
            with np.load(io.BytesIO(arrays_raw), allow_pickle=False) as npz:
                for key in npz.files:
                    arrays[key] = npz[key]
        except Exception as e:
            raise ProjectFormatError("Invalid arrays.npz (cannot be read safely).") from e

    return ProjectPayload(meta=meta, arrays=arrays, files=files)


def extract_members(zf: zipfile.ZipFile, members: list[str], extract_dir: str) -> dict[str, str]:
    """Stream ``members`` of an open archive into ``extract_dir``, keeping their paths.

    Each name is re-checked with :func:`is_safe_arcname` and the resolved path
    must stay inside ``extract_dir``; the legacy ``.ecsolar`` importer uses this too.
    """
    root = os.path.realpath(extract_dir)
    os.makedirs(root, exist_ok=True)
    out: dict[str, str] = {}
    for name in members:
        if not is_safe_arcname(name):
            raise ProjectFormatError(f"Project contains an unsafe file path: {name!r}")
        dest = os.path.realpath(os.path.join(root, *name.split("/")))
        # Belt and braces after is_safe_arcname: never write outside the root.
        if os.path.commonpath([root, dest]) != root:
            raise ProjectFormatError(f"Project contains an unsafe file path: {name!r}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zf.open(name) as src, open(dest, "wb") as dst:
            while True:
                chunk = src.read(_COPY_CHUNK)
                if not chunk:
                    break
                dst.write(chunk)
        out[name] = dest
    return out
