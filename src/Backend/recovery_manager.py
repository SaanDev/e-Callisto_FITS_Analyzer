"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Mapping

from src.Backend.project_session import ProjectPayload, read_project, write_project

try:
    from PySide6.QtCore import QStandardPaths
except Exception:  # pragma: no cover - optional in non-Qt test contexts
    QStandardPaths = None


DEFAULT_MAX_SNAPSHOTS = 10


@dataclass(frozen=True)
class RecoverySnapshot:
    path: str
    mtime: float


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _now_utc().astimezone().isoformat(timespec="seconds")


def _app_data_location() -> Path:
    if QStandardPaths is not None:
        try:
            raw = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
            if raw:
                return Path(raw)
        except Exception:
            pass
    return Path.home() / ".local" / "share" / "e-callisto-fits-analyzer"


def recovery_dir(base_dir: str | os.PathLike[str] | None = None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    return _app_data_location() / "recovery"


def ensure_recovery_dir(base_dir: str | os.PathLike[str] | None = None) -> Path:
    out = recovery_dir(base_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _snapshot_filename(prefix: str = "autosave") -> str:
    stamp = _now_utc().strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}_{stamp}.efaproj"


def list_snapshots(base_dir: str | os.PathLike[str] | None = None) -> list[RecoverySnapshot]:
    root = recovery_dir(base_dir)
    if not root.exists():
        return []

    out: list[RecoverySnapshot] = []
    for p in root.glob("*.efaproj"):
        try:
            stat = p.stat()
        except Exception:
            continue
        out.append(RecoverySnapshot(path=str(p), mtime=float(stat.st_mtime)))

    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def latest_snapshot_path(base_dir: str | os.PathLike[str] | None = None) -> str | None:
    snaps = list_snapshots(base_dir)
    return snaps[0].path if snaps else None


def prune_snapshots(
    *,
    max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
    base_dir: str | os.PathLike[str] | None = None,
) -> int:
    snaps = list_snapshots(base_dir)
    if max_snapshots < 0:
        max_snapshots = 0

    removed = 0
    for snap in snaps[max_snapshots:]:
        try:
            Path(snap.path).unlink(missing_ok=True)
            removed += 1
        except Exception:
            pass
    return removed


def save_recovery_snapshot(
    *,
    meta: Mapping[str, Any],
    arrays: Mapping[str, Any],
    source_project_path: str | None,
    reason: str,
    max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
    base_dir: str | os.PathLike[str] | None = None,
) -> str:
    root = ensure_recovery_dir(base_dir)
    path = root / _snapshot_filename("autosave")

    payload_meta = dict(meta)
    payload_meta["recovery_snapshot"] = True
    payload_meta["recovery_saved_at"] = _iso_now()
    payload_meta["recovery_reason"] = str(reason or "timer")
    payload_meta["recovery_source_project_path"] = source_project_path

    write_project(str(path), meta=payload_meta, arrays=arrays)
    prune_snapshots(max_snapshots=max_snapshots, base_dir=root)
    return str(path)


def load_recovery_snapshot(path: str) -> ProjectPayload:
    return read_project(path)
