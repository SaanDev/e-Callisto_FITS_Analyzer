"""
Persistent, local learning for assisted Type II burst detection.

Training examples live outside the application installation.  Models are stored
as JSON metadata plus numeric NPZ arrays, never as pickle/joblib objects, so an
imported library cannot execute code during loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Callable, Iterable, Literal, Mapping
import uuid
import zipfile

import numpy as np
from scipy.ndimage import gaussian_filter
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_score, recall_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

from src.Backend.type_ii_detection import (
    CANDIDATE_FEATURE_SCHEMA_VERSION,
    TypeIIBand,
    TypeIICandidate,
)

try:
    from PySide6.QtCore import QStandardPaths
except Exception:  # pragma: no cover - pure-backend environments
    QStandardPaths = None


LEARNING_SCHEMA_VERSION = 1
MODEL_SCHEMA_VERSION = 1
PIXEL_FEATURE_SCHEMA_VERSION = 1
EXPORT_MAGIC = "e-callisto-typeii-learning"
CancelCallback = Callable[[], bool]
ReviewKind = Literal["teach", "accepted", "rejected", "clean_range"]


class TypeIILearningCancelled(RuntimeError):
    pass


class TypeIILearningFormatError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _check_cancel(cancel_cb: CancelCallback | None) -> None:
    if cancel_cb is not None and bool(cancel_cb()):
        raise TypeIILearningCancelled("Type II model training was cancelled.")


def default_learning_root() -> Path:
    if QStandardPaths is not None:
        try:
            value = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
            if value:
                return Path(value) / "type_ii_learning"
        except Exception:
            pass
    return Path.home() / ".local" / "share" / "e-callisto-fits-analyzer" / "type_ii_learning"


def source_observation_hash(
    source: str,
    *,
    station: str = "",
    observation_time: str = "",
    shape: tuple[int, int] | None = None,
) -> str:
    text = "|".join(
        (
            str(source or ""),
            str(station or ""),
            str(observation_time or ""),
            "" if shape is None else f"{int(shape[0])}x{int(shape[1])}",
        )
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _array_to_blob(value: np.ndarray | None) -> bytes | None:
    if value is None:
        return None
    out = io.BytesIO()
    np.savez_compressed(out, value=np.asarray(value))
    return out.getvalue()


def _blob_to_array(value: bytes | None) -> np.ndarray | None:
    if value is None:
        return None
    with np.load(io.BytesIO(value), allow_pickle=False) as payload:
        return np.asarray(payload["value"])


@dataclass(frozen=True)
class TypeIITrainingExample:
    example_id: str
    source_hash: str
    label: int
    review_kind: ReviewKind
    station: str = ""
    event_id: str = ""
    band: TypeIIBand | None = None
    created_at: str = field(default_factory=_utc_now)
    feature_schema_version: int = CANDIDATE_FEATURE_SCHEMA_VERSION
    feature_vector: np.ndarray | None = field(default=None, repr=False, compare=False)
    snr_crop: np.ndarray | None = field(default=None, repr=False, compare=False)
    mask: np.ndarray | None = field(default=None, repr=False, compare=False)
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if int(self.label) not in (0, 1):
            raise ValueError("A Type II learning label must be 0 or 1.")
        if int(self.label) == 1 and self.band not in ("fundamental", "harmonic"):
            raise ValueError("Positive Type II examples require a fundamental or harmonic label.")
        if not str(self.source_hash or "").strip():
            raise ValueError("Type II examples require a source-observation hash.")

    @classmethod
    def create(
        cls,
        *,
        source_hash: str,
        label: int,
        review_kind: ReviewKind,
        station: str = "",
        event_id: str = "",
        band: TypeIIBand | None = None,
        feature_vector: np.ndarray | None = None,
        snr_crop: np.ndarray | None = None,
        mask: np.ndarray | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "TypeIITrainingExample":
        return cls(
            example_id=str(uuid.uuid4()),
            source_hash=source_hash,
            label=int(label),
            review_kind=review_kind,
            station=str(station or ""),
            event_id=str(event_id or ""),
            band=band,
            feature_vector=None if feature_vector is None else np.asarray(feature_vector, dtype=np.float64),
            snr_crop=None if snr_crop is None else np.asarray(snr_crop, dtype=np.float32),
            mask=None if mask is None else np.asarray(mask, dtype=bool),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class TypeIIModelManifest:
    model_id: str
    created_at: str
    parent_model_id: str | None
    promoted: bool
    candidate_model_ready: bool
    mask_model_ready: bool
    feature_schema_version: int
    pixel_feature_schema_version: int
    metrics: Mapping[str, Any] = field(default_factory=dict)
    sample_counts: Mapping[str, int] = field(default_factory=dict)
    station_calibrations: Mapping[str, float] = field(default_factory=dict)
    arrays_sha256: str = ""
    status_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_schema_version": MODEL_SCHEMA_VERSION,
            "model_id": self.model_id,
            "created_at": self.created_at,
            "parent_model_id": self.parent_model_id,
            "promoted": bool(self.promoted),
            "candidate_model_ready": bool(self.candidate_model_ready),
            "mask_model_ready": bool(self.mask_model_ready),
            "feature_schema_version": int(self.feature_schema_version),
            "pixel_feature_schema_version": int(self.pixel_feature_schema_version),
            "metrics": dict(self.metrics),
            "sample_counts": dict(self.sample_counts),
            "station_calibrations": {
                str(key): float(value) for key, value in self.station_calibrations.items()
            },
            "arrays_sha256": str(self.arrays_sha256 or ""),
            "status_reason": str(self.status_reason),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TypeIIModelManifest":
        if int(value.get("model_schema_version", 0)) != MODEL_SCHEMA_VERSION:
            raise TypeIILearningFormatError("Unsupported Type II model schema.")
        return cls(
            model_id=str(value.get("model_id") or ""),
            created_at=str(value.get("created_at") or ""),
            parent_model_id=(
                None if not value.get("parent_model_id") else str(value.get("parent_model_id"))
            ),
            promoted=bool(value.get("promoted", False)),
            candidate_model_ready=bool(value.get("candidate_model_ready", False)),
            mask_model_ready=bool(value.get("mask_model_ready", False)),
            feature_schema_version=int(value.get("feature_schema_version", 0)),
            pixel_feature_schema_version=int(value.get("pixel_feature_schema_version", 0)),
            metrics=dict(value.get("metrics") or {}),
            sample_counts={str(k): int(v) for k, v in dict(value.get("sample_counts") or {}).items()},
            station_calibrations={
                str(k): float(v) for k, v in dict(value.get("station_calibrations") or {}).items()
            },
            arrays_sha256=str(value.get("arrays_sha256") or ""),
            status_reason=str(value.get("status_reason") or ""),
        )


@dataclass(frozen=True)
class TypeIITrainingPolicy:
    min_positive_examples: int = 20
    min_negative_examples: int = 20
    min_validation_positive_groups: int = 5
    min_validation_negative_groups: int = 5
    min_precision: float = 0.90
    min_recall: float = 0.70
    max_false_candidates_per_15m: float = 0.10
    min_mask_iou: float = 0.65
    min_ridge_coverage: float = 0.85
    station_min_positive: int = 10
    station_min_negative: int = 10


@dataclass(frozen=True)
class TypeIITrainingOutcome:
    manifest: TypeIIModelManifest
    active_model_changed: bool


class TypeIILearningLibrary:
    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root) if root is not None else default_learning_root()
        self.database_path = self.root / "library.sqlite3"
        self.models_dir = self.root / "models"
        self.active_manifest_path = self.root / "active.json"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            current_version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if current_version not in (0, LEARNING_SCHEMA_VERSION):
                raise TypeIILearningFormatError(
                    f"Unsupported Type II learning library schema {current_version}."
                )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS examples (
                    example_id TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    label INTEGER NOT NULL,
                    review_kind TEXT NOT NULL,
                    station TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    band TEXT,
                    created_at TEXT NOT NULL,
                    feature_schema_version INTEGER NOT NULL,
                    feature_vector BLOB,
                    snr_crop BLOB,
                    mask BLOB,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_typeii_examples_source ON examples(source_hash)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_typeii_examples_label ON examples(label)")
            db.execute(f"PRAGMA user_version={LEARNING_SCHEMA_VERSION}")
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.database_path), timeout=30.0)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def add(self, example: TypeIITrainingExample) -> str:
        self.ensure()
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO examples (
                    example_id, source_hash, label, review_kind, station, event_id,
                    band, created_at, feature_schema_version, feature_vector,
                    snr_crop, mask, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    example.example_id,
                    example.source_hash,
                    int(example.label),
                    example.review_kind,
                    example.station,
                    example.event_id,
                    example.band,
                    example.created_at,
                    int(example.feature_schema_version),
                    _array_to_blob(example.feature_vector),
                    _array_to_blob(example.snr_crop),
                    _array_to_blob(example.mask),
                    json.dumps(dict(example.metadata), sort_keys=True),
                ),
            )
            db.commit()
        return example.example_id

    @staticmethod
    def _row_to_example(row: tuple[Any, ...]) -> TypeIITrainingExample:
        return TypeIITrainingExample(
            example_id=str(row[0]),
            source_hash=str(row[1]),
            label=int(row[2]),
            review_kind=str(row[3]),  # type: ignore[arg-type]
            station=str(row[4] or ""),
            event_id=str(row[5] or ""),
            band=None if row[6] is None else str(row[6]),  # type: ignore[arg-type]
            created_at=str(row[7]),
            feature_schema_version=int(row[8]),
            feature_vector=_blob_to_array(row[9]),
            snr_crop=_blob_to_array(row[10]),
            mask=_blob_to_array(row[11]),
            metadata=json.loads(str(row[12]) or "{}"),
        )

    def examples(self) -> list[TypeIITrainingExample]:
        self.ensure()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT example_id, source_hash, label, review_kind, station,
                       event_id, band, created_at, feature_schema_version,
                       feature_vector, snr_crop, mask, metadata_json
                FROM examples
                ORDER BY created_at, example_id
                """
            ).fetchall()
        return [self._row_to_example(row) for row in rows]

    def counts(self) -> dict[str, int]:
        self.ensure()
        with self._connect() as db:
            positive = int(db.execute("SELECT COUNT(*) FROM examples WHERE label=1").fetchone()[0])
            negative = int(db.execute("SELECT COUNT(*) FROM examples WHERE label=0").fetchone()[0])
            sources = int(db.execute("SELECT COUNT(DISTINCT source_hash) FROM examples").fetchone()[0])
        return {"positive": positive, "negative": negative, "sources": sources, "total": positive + negative}

    def manifest_for_model(self, model_id: str) -> TypeIIModelManifest | None:
        path = self.models_dir / f"{model_id}.json"
        if not path.exists():
            return None
        return TypeIIModelManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def active_manifest(self) -> TypeIIModelManifest | None:
        if not self.active_manifest_path.exists():
            return None
        try:
            pointer = json.loads(self.active_manifest_path.read_text(encoding="utf-8"))
            return self.manifest_for_model(str(pointer.get("model_id") or ""))
        except Exception:
            return None

    def model_history(self) -> list[TypeIIModelManifest]:
        self.models_dir.mkdir(parents=True, exist_ok=True)
        output: list[TypeIIModelManifest] = []
        for path in self.models_dir.glob("*.json"):
            try:
                output.append(TypeIIModelManifest.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        output.sort(key=lambda item: (item.created_at, item.model_id), reverse=True)
        return output

    def rollback(self) -> TypeIIModelManifest | None:
        active = self.active_manifest()
        promoted = [
            item
            for item in self.model_history()
            if item.promoted and (active is None or item.model_id != active.model_id)
        ]
        if not promoted:
            return None
        selected = promoted[0]
        _atomic_json(self.active_manifest_path, {"model_id": selected.model_id})
        return selected

    def export_package(self, path: str | os.PathLike[str]) -> str:
        examples = self.examples()
        entries = []
        package_path = Path(path)
        package_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for example in examples:
                name = f"samples/{example.example_id}.npz"
                buffer = io.BytesIO()
                arrays = {}
                if example.feature_vector is not None:
                    arrays["feature_vector"] = np.asarray(example.feature_vector)
                if example.snr_crop is not None:
                    arrays["snr_crop"] = np.asarray(example.snr_crop)
                if example.mask is not None:
                    arrays["mask"] = np.asarray(example.mask, dtype=np.uint8)
                np.savez_compressed(buffer, **arrays)
                payload = buffer.getvalue()
                archive.writestr(name, payload)
                entries.append(
                    {
                        "example_id": example.example_id,
                        "source_hash": example.source_hash,
                        "label": example.label,
                        "review_kind": example.review_kind,
                        "station": example.station,
                        "event_id": example.event_id,
                        "band": example.band,
                        "created_at": example.created_at,
                        "feature_schema_version": example.feature_schema_version,
                        "metadata": dict(example.metadata),
                        "sample_path": name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                )
            manifest = {
                "magic": EXPORT_MAGIC,
                "schema_version": LEARNING_SCHEMA_VERSION,
                "created_at": _utc_now(),
                "examples": entries,
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        return str(package_path)

    def import_package(self, path: str | os.PathLike[str]) -> int:
        imported = 0
        with zipfile.ZipFile(path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("magic") != EXPORT_MAGIC:
                raise TypeIILearningFormatError("Not an e-CALLISTO Type II learning package.")
            if int(manifest.get("schema_version", 0)) != LEARNING_SCHEMA_VERSION:
                raise TypeIILearningFormatError("Unsupported Type II learning package schema.")
            for entry in list(manifest.get("examples") or []):
                sample_path = str(entry.get("sample_path") or "")
                payload = archive.read(sample_path)
                if hashlib.sha256(payload).hexdigest() != str(entry.get("sha256") or ""):
                    raise TypeIILearningFormatError(f"Checksum mismatch for {sample_path}.")
                with np.load(io.BytesIO(payload), allow_pickle=False) as arrays:
                    feature_vector = arrays["feature_vector"] if "feature_vector" in arrays else None
                    snr_crop = arrays["snr_crop"] if "snr_crop" in arrays else None
                    mask = arrays["mask"].astype(bool) if "mask" in arrays else None
                before = self.counts()["total"]
                self.add(
                    TypeIITrainingExample(
                        example_id=str(entry.get("example_id") or ""),
                        source_hash=str(entry.get("source_hash") or ""),
                        label=int(entry.get("label", 0)),
                        review_kind=str(entry.get("review_kind") or "rejected"),  # type: ignore[arg-type]
                        station=str(entry.get("station") or ""),
                        event_id=str(entry.get("event_id") or ""),
                        band=None if not entry.get("band") else str(entry.get("band")),  # type: ignore[arg-type]
                        created_at=str(entry.get("created_at") or _utc_now()),
                        feature_schema_version=int(
                            entry.get("feature_schema_version", CANDIDATE_FEATURE_SCHEMA_VERSION)
                        ),
                        feature_vector=feature_vector,
                        snr_crop=snr_crop,
                        mask=mask,
                        metadata=dict(entry.get("metadata") or {}),
                    )
                )
                imported += int(self.counts()["total"] > before)
        return imported


def record_type_ii_example(
    example: TypeIITrainingExample,
    library: TypeIILearningLibrary | str | os.PathLike[str] | None = None,
) -> str:
    store = library if isinstance(library, TypeIILearningLibrary) else TypeIILearningLibrary(library)
    return store.add(example)


def training_example_from_candidate(
    candidate: TypeIICandidate,
    *,
    source_hash: str,
    label: int,
    review_kind: ReviewKind,
    station: str = "",
    event_id: str = "",
    band: TypeIIBand | None = None,
    reviewed_duration_s: float | None = None,
    mask: np.ndarray | None = None,
) -> TypeIITrainingExample:
    """Create a compact learning example from a reviewed candidate or lasso."""

    selected = np.asarray(candidate.mask if mask is None else mask, dtype=bool)
    crop_snr = None
    crop_mask = None
    bounds = None
    if candidate.snr_data is not None and selected.shape == np.asarray(candidate.snr_data).shape:
        rows, cols = np.where(selected)
        if rows.size:
            padding = 5
            r0 = max(0, int(rows.min()) - padding)
            r1 = min(selected.shape[0], int(rows.max()) + padding + 1)
            c0 = max(0, int(cols.min()) - padding)
            c1 = min(selected.shape[1], int(cols.max()) + padding + 1)
            crop_snr = np.asarray(candidate.snr_data[r0:r1, c0:c1], dtype=np.float32)
            crop_mask = np.asarray(selected[r0:r1, c0:c1], dtype=bool)
            bounds = [r0, r1, c0, c1]
    metadata = {
        "candidate": candidate.summary(),
        "crop_bounds": bounds,
        "reviewed_duration_s": float(
            candidate.duration_s if reviewed_duration_s is None else reviewed_duration_s
        ),
    }
    return TypeIITrainingExample.create(
        source_hash=source_hash,
        label=int(label),
        review_kind=review_kind,
        station=station,
        event_id=event_id,
        band=band,
        feature_vector=candidate.feature_vector,
        snr_crop=crop_snr,
        mask=crop_mask,
        metadata=metadata,
    )


def _sigmoid(value: np.ndarray | float) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    arr = np.clip(arr, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-arr))


def _pixel_features(snr: np.ndarray, base_mask: np.ndarray | None = None) -> np.ndarray:
    arr = np.asarray(snr, dtype=np.float32)
    finite = np.isfinite(arr)
    clean = np.where(finite, arr, -8.0)
    smooth1 = gaussian_filter(clean, sigma=(1.0, 1.0), mode="nearest")
    smooth2 = gaussian_filter(clean, sigma=(2.0, 2.0), mode="nearest")
    grad_freq, grad_time = np.gradient(smooth1)
    yy, xx = np.indices(arr.shape, dtype=np.float32)
    yy /= max(arr.shape[0] - 1, 1)
    xx /= max(arr.shape[1] - 1, 1)
    membership = (
        np.ones(arr.shape, dtype=np.float32)
        if base_mask is None
        else np.asarray(base_mask, dtype=np.float32)
    )
    return np.column_stack(
        (
            clean.ravel(),
            smooth1.ravel(),
            smooth2.ravel(),
            grad_freq.ravel(),
            grad_time.ravel(),
            yy.ravel(),
            xx.ravel(),
            membership.ravel(),
        )
    ).astype(np.float64)


class ActiveTypeIIModel:
    def __init__(self, manifest: TypeIIModelManifest, arrays: Mapping[str, np.ndarray]):
        self.manifest = manifest
        self.model_id = manifest.model_id
        self._candidate_mean = np.asarray(arrays["candidate_mean"], dtype=float)
        self._candidate_scale = np.asarray(arrays["candidate_scale"], dtype=float)
        self._candidate_coef = np.asarray(arrays["candidate_coef"], dtype=float).ravel()
        self._candidate_intercept = float(np.asarray(arrays["candidate_intercept"]).ravel()[0])
        self._mask_ready = bool(
            manifest.mask_model_ready
            and manifest.pixel_feature_schema_version == PIXEL_FEATURE_SCHEMA_VERSION
            and "mask_coef" in arrays
        )
        self._mask_mean = np.asarray(arrays.get("mask_mean", []), dtype=float)
        self._mask_scale = np.asarray(arrays.get("mask_scale", []), dtype=float)
        self._mask_coef = np.asarray(arrays.get("mask_coef", []), dtype=float).ravel()
        self._mask_intercept = float(np.asarray(arrays.get("mask_intercept", [0.0])).ravel()[0])

    def has_station_calibration(self, station: str) -> bool:
        return str(station or "") in self.manifest.station_calibrations

    def predict_candidate(self, features: np.ndarray, *, station: str = "") -> float:
        values = np.asarray(features, dtype=float).ravel()
        if values.shape != self._candidate_mean.shape:
            raise ValueError("Candidate feature schema does not match the active Type II model.")
        scaled = (values - self._candidate_mean) / np.where(self._candidate_scale > 0, self._candidate_scale, 1.0)
        logit = float(np.dot(scaled, self._candidate_coef) + self._candidate_intercept)
        logit += float(self.manifest.station_calibrations.get(str(station or ""), 0.0))
        return float(_sigmoid(logit))

    def refine_candidate_mask(
        self,
        candidate: TypeIICandidate,
        shape: tuple[int, int],
    ) -> np.ndarray | None:
        if not self._mask_ready or candidate.snr_data is None:
            return None
        base = np.asarray(candidate.mask, dtype=bool)
        rows, cols = np.where(base)
        if rows.size == 0:
            return None
        padding = 4
        r0, r1 = max(0, int(rows.min()) - padding), min(shape[0], int(rows.max()) + padding + 1)
        c0, c1 = max(0, int(cols.min()) - padding), min(shape[1], int(cols.max()) + padding + 1)
        crop = np.asarray(candidate.snr_data[r0:r1, c0:c1], dtype=np.float32)
        base_crop = base[r0:r1, c0:c1]
        features = _pixel_features(crop, base_crop)
        if features.shape[1] != self._mask_mean.size:
            return None
        scaled = (features - self._mask_mean) / np.where(self._mask_scale > 0, self._mask_scale, 1.0)
        probability = _sigmoid(scaled @ self._mask_coef + self._mask_intercept).reshape(crop.shape)
        refined_crop = (probability >= 0.5) & (gaussian_filter(base_crop.astype(float), 1.0) > 0.05)
        if not np.any(refined_crop):
            return None
        refined = np.zeros(shape, dtype=bool)
        refined[r0:r1, c0:c1] = refined_crop
        return refined


def load_active_type_ii_model(
    library: TypeIILearningLibrary | str | os.PathLike[str] | None = None,
) -> ActiveTypeIIModel | None:
    store = library if isinstance(library, TypeIILearningLibrary) else TypeIILearningLibrary(library)
    manifest = store.active_manifest()
    if manifest is None or not manifest.candidate_model_ready:
        return None
    if manifest.feature_schema_version != CANDIDATE_FEATURE_SCHEMA_VERSION:
        return None
    arrays_path = store.models_dir / f"{manifest.model_id}.npz"
    try:
        if manifest.arrays_sha256:
            actual_hash = hashlib.sha256(arrays_path.read_bytes()).hexdigest()
            if actual_hash != manifest.arrays_sha256:
                return None
        with np.load(arrays_path, allow_pickle=False) as payload:
            arrays = {key: np.asarray(payload[key]) for key in payload.files}
        return ActiveTypeIIModel(manifest, arrays)
    except Exception:
        return None


def _group_split(
    labels: np.ndarray,
    groups: np.ndarray,
    *,
    min_validation_positive_groups: int = 1,
    min_validation_negative_groups: int = 1,
) -> tuple[np.ndarray, np.ndarray] | None:
    unique_groups = np.unique(groups)
    positive_groups = set(groups[labels == 1].tolist())
    negative_groups = set(groups[labels == 0].tolist())
    min_positive = max(1, int(min_validation_positive_groups))
    min_negative = max(1, int(min_validation_negative_groups))
    # Validation must receive the requested independent observations while at
    # least one observation of each class remains available for fitting.
    if (
        unique_groups.size < 2
        or len(positive_groups) < min_positive + 1
        or len(negative_groups) < min_negative + 1
    ):
        return None

    dummy = np.zeros(labels.size)
    min_test_groups = max(min_positive, min_negative)
    # Mixed-label observations can satisfy both class-coverage requirements,
    # while disjoint observations may need min_positive + min_negative groups.
    # Try each deterministic group count until a leakage-free split is found.
    for test_group_count in range(min_test_groups, int(unique_groups.size)):
        splitter = GroupShuffleSplit(
            n_splits=max(64, min(512, int(unique_groups.size) * 24)),
            test_size=test_group_count,
            random_state=1009 + test_group_count,
        )
        for train_idx, val_idx in splitter.split(dummy, labels, groups):
            if np.unique(labels[train_idx]).size != 2:
                continue
            val_positive, val_negative = _validation_group_counts(
                labels,
                groups,
                np.asarray(val_idx, dtype=int),
            )
            if val_positive >= min_positive and val_negative >= min_negative:
                return np.asarray(train_idx, dtype=int), np.asarray(val_idx, dtype=int)
    return None


def _validation_group_counts(labels: np.ndarray, groups: np.ndarray, indices: np.ndarray) -> tuple[int, int]:
    positive = len(set(groups[indices][labels[indices] == 1].tolist()))
    negative = len(set(groups[indices][labels[indices] == 0].tolist()))
    return positive, negative


def _station_intercept(
    logits: np.ndarray,
    labels: np.ndarray,
) -> float:
    intercept = 0.0
    for _ in range(20):
        probs = _sigmoid(logits + intercept)
        grad = float(np.sum(labels - probs))
        hess = -float(np.sum(probs * (1.0 - probs)))
        if abs(hess) < 1e-8:
            break
        update = grad / hess
        intercept -= update
        if abs(update) < 1e-6:
            break
    return float(np.clip(intercept, -3.0, 3.0))


def _sample_pixel_rows(
    examples: Iterable[TypeIITrainingExample],
    *,
    maximum_per_example: int = 5000,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[np.ndarray, np.ndarray, str]]]:
    rng = np.random.default_rng(0)
    feature_rows: list[np.ndarray] = []
    label_rows: list[np.ndarray] = []
    group_rows: list[np.ndarray] = []
    validation_payload: list[tuple[np.ndarray, np.ndarray, str]] = []
    for example in examples:
        if example.snr_crop is None or example.mask is None:
            continue
        crop = np.asarray(example.snr_crop, dtype=np.float32)
        mask = np.asarray(example.mask, dtype=bool)
        if crop.shape != mask.shape or crop.ndim != 2 or not np.any(mask):
            continue
        features = _pixel_features(crop)
        labels = mask.ravel().astype(int)
        available = np.arange(labels.size)
        if available.size > maximum_per_example:
            positives = available[labels == 1]
            negatives = available[labels == 0]
            pos_count = min(positives.size, maximum_per_example // 2)
            neg_count = min(negatives.size, maximum_per_example - pos_count)
            selected = np.concatenate(
                (
                    rng.choice(positives, size=pos_count, replace=False) if pos_count else np.empty(0, int),
                    rng.choice(negatives, size=neg_count, replace=False) if neg_count else np.empty(0, int),
                )
            )
        else:
            selected = available
        feature_rows.append(features[selected])
        label_rows.append(labels[selected])
        group_rows.append(np.full(selected.size, example.source_hash, dtype=object))
        validation_payload.append((features, mask, example.source_hash))
    if not feature_rows:
        return (
            np.empty((0, 8), dtype=float),
            np.empty(0, dtype=int),
            np.empty(0, dtype=object),
            [],
        )
    return (
        np.vstack(feature_rows),
        np.concatenate(label_rows),
        np.concatenate(group_rows),
        validation_payload,
    )


def _mask_validation_metrics(
    scaler: StandardScaler,
    classifier: LogisticRegression,
    payload: list[tuple[np.ndarray, np.ndarray, str]],
    validation_groups: set[str],
) -> tuple[float, float]:
    ious: list[float] = []
    coverages: list[float] = []
    for features, truth, group in payload:
        if group not in validation_groups:
            continue
        probability = classifier.predict_proba(scaler.transform(features))[:, 1].reshape(truth.shape)
        predicted = probability >= 0.5
        union = np.count_nonzero(predicted | truth)
        ious.append(float(np.count_nonzero(predicted & truth) / max(union, 1)))
        ridge_hits = 0
        ridge_total = 0
        for col in range(truth.shape[1]):
            rows = np.where(truth[:, col])[0]
            if rows.size:
                ridge_total += 1
                ridge_hits += int(bool(predicted[int(np.median(rows)), col]))
        coverages.append(float(ridge_hits / max(ridge_total, 1)))
    return (
        float(np.median(ious)) if ious else 0.0,
        float(np.median(coverages)) if coverages else 0.0,
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _atomic_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    try:
        with open(temp_name, "wb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def train_type_ii_model(
    library: TypeIILearningLibrary | str | os.PathLike[str] | None = None,
    *,
    cancel_cb: CancelCallback | None = None,
    policy: TypeIITrainingPolicy | None = None,
) -> TypeIITrainingOutcome:
    store = library if isinstance(library, TypeIILearningLibrary) else TypeIILearningLibrary(library)
    config = policy or TypeIITrainingPolicy()
    examples = [
        item
        for item in store.examples()
        if item.feature_schema_version == CANDIDATE_FEATURE_SCHEMA_VERSION
        and item.feature_vector is not None
    ]
    _check_cancel(cancel_cb)
    positive_count = sum(item.label == 1 for item in examples)
    negative_count = sum(item.label == 0 for item in examples)
    counts = {
        "positive": int(positive_count),
        "negative": int(negative_count),
        "sources": len({item.source_hash for item in examples}),
    }
    parent = store.active_manifest()
    model_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]

    def outcome(reason: str) -> TypeIITrainingOutcome:
        manifest = TypeIIModelManifest(
            model_id=model_id,
            created_at=_utc_now(),
            parent_model_id=None if parent is None else parent.model_id,
            promoted=False,
            candidate_model_ready=False,
            mask_model_ready=False,
            feature_schema_version=CANDIDATE_FEATURE_SCHEMA_VERSION,
            pixel_feature_schema_version=PIXEL_FEATURE_SCHEMA_VERSION,
            metrics={},
            sample_counts=counts,
            status_reason=reason,
        )
        store.models_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(store.models_dir / f"{model_id}.json", manifest.to_dict())
        return TypeIITrainingOutcome(manifest=manifest, active_model_changed=False)

    if positive_count < config.min_positive_examples or negative_count < config.min_negative_examples:
        return outcome(
            f"Need at least {config.min_positive_examples} positive and "
            f"{config.min_negative_examples} negative examples."
        )

    feature_lengths = {np.asarray(item.feature_vector).size for item in examples}
    if len(feature_lengths) != 1:
        return outcome("Candidate feature vectors do not share one schema.")
    X = np.vstack([np.asarray(item.feature_vector, dtype=float).ravel() for item in examples])
    y = np.asarray([item.label for item in examples], dtype=int)
    groups = np.asarray([item.source_hash for item in examples], dtype=object)
    split = _group_split(
        y,
        groups,
        min_validation_positive_groups=config.min_validation_positive_groups,
        min_validation_negative_groups=config.min_validation_negative_groups,
    )
    if split is None:
        positive_sources = len(set(groups[y == 1].tolist()))
        negative_sources = len(set(groups[y == 0].tolist()))
        return outcome(
            "Grouped validation needs enough independent observations to reserve "
            f"{config.min_validation_positive_groups} positive and "
            f"{config.min_validation_negative_groups} negative sources while retaining "
            "both classes for training "
            f"(found {positive_sources} positive and {negative_sources} negative sources)."
        )
    train_idx, val_idx = split
    val_positive_groups, val_negative_groups = _validation_group_counts(y, groups, val_idx)
    if (
        val_positive_groups < config.min_validation_positive_groups
        or val_negative_groups < config.min_validation_negative_groups
    ):
        return outcome(
            "Grouped validation needs at least "
            f"{config.min_validation_positive_groups} positive and "
            f"{config.min_validation_negative_groups} negative source observations."
        )

    candidate_scaler = StandardScaler().fit(X[train_idx])
    candidate_classifier = LogisticRegression(
        class_weight="balanced",
        max_iter=1500,
        random_state=0,
    ).fit(candidate_scaler.transform(X[train_idx]), y[train_idx])
    val_probability = candidate_classifier.predict_proba(candidate_scaler.transform(X[val_idx]))[:, 1]
    val_predicted = (val_probability >= 0.5).astype(int)
    precision = float(precision_score(y[val_idx], val_predicted, zero_division=0))
    recall = float(recall_score(y[val_idx], val_predicted, zero_division=0))
    negative_examples = [examples[int(idx)] for idx in val_idx if y[int(idx)] == 0]
    reviewed_exposure: dict[tuple[str, str], float] = {}
    for item in negative_examples:
        review_key = (item.source_hash, item.event_id or item.example_id)
        duration = max(float(dict(item.metadata).get("reviewed_duration_s", 900.0)), 1.0)
        reviewed_exposure[review_key] = max(reviewed_exposure.get(review_key, 0.0), duration)
    negative_minutes = sum(reviewed_exposure.values()) / 900.0
    false_count = int(np.count_nonzero((val_predicted == 1) & (y[val_idx] == 0)))
    false_per_15m = float(false_count / max(negative_minutes, 1.0))
    current_precision = 0.0 if parent is None else float(parent.metrics.get("precision", 0.0))
    candidate_ready = (
        precision >= config.min_precision
        and recall >= config.min_recall
        and false_per_15m <= config.max_false_candidates_per_15m
        and precision >= current_precision
    )
    _check_cancel(cancel_cb)

    train_logits = candidate_classifier.decision_function(candidate_scaler.transform(X[train_idx]))
    station_calibrations: dict[str, float] = {}
    stations = np.asarray([examples[int(idx)].station for idx in train_idx], dtype=object)
    train_labels = y[train_idx]
    for station in sorted({str(value) for value in stations if str(value)}):
        station_mask = stations == station
        station_y = train_labels[station_mask]
        if (
            np.count_nonzero(station_y == 1) >= config.station_min_positive
            and np.count_nonzero(station_y == 0) >= config.station_min_negative
        ):
            station_calibrations[station] = _station_intercept(
                np.asarray(train_logits)[station_mask],
                station_y,
            )

    mask_examples = [item for item in examples if item.label == 1 and item.snr_crop is not None and item.mask is not None]
    mask_X, mask_y, mask_groups, mask_payload = _sample_pixel_rows(mask_examples)
    mask_ready = False
    mask_iou = 0.0
    ridge_coverage = 0.0
    mask_scaler: StandardScaler | None = None
    mask_classifier: LogisticRegression | None = None
    if mask_X.size and np.unique(mask_y).size == 2 and len(set(mask_groups.tolist())) >= 4:
        mask_train_groups = set(groups[train_idx][y[train_idx] == 1].tolist())
        mask_val_groups = set(groups[val_idx][y[val_idx] == 1].tolist())
        mask_train = np.asarray([group in mask_train_groups for group in mask_groups], dtype=bool)
        if np.any(mask_train) and np.unique(mask_y[mask_train]).size == 2 and mask_val_groups:
            mask_scaler = StandardScaler().fit(mask_X[mask_train])
            mask_classifier = LogisticRegression(
                class_weight="balanced",
                max_iter=1000,
                random_state=0,
            ).fit(mask_scaler.transform(mask_X[mask_train]), mask_y[mask_train])
            mask_iou, ridge_coverage = _mask_validation_metrics(
                mask_scaler,
                mask_classifier,
                mask_payload,
                mask_val_groups,
            )
            mask_ready = (
                mask_iou >= config.min_mask_iou
                and ridge_coverage >= config.min_ridge_coverage
            )

    metrics = {
        "precision": precision,
        "recall": recall,
        "false_candidates_per_15m": false_per_15m,
        "validation_positive_groups": val_positive_groups,
        "validation_negative_groups": val_negative_groups,
        "mask_iou": mask_iou,
        "ridge_coverage": ridge_coverage,
    }
    validation_failures: list[str] = []
    if precision < config.min_precision:
        validation_failures.append(
            f"precision {precision:.3f} < {config.min_precision:.3f}"
        )
    if recall < config.min_recall:
        validation_failures.append(
            f"recall {recall:.3f} < {config.min_recall:.3f}"
        )
    if false_per_15m > config.max_false_candidates_per_15m:
        validation_failures.append(
            "false candidates/15 min "
            f"{false_per_15m:.3f} > {config.max_false_candidates_per_15m:.3f}"
        )
    if precision < current_precision:
        validation_failures.append(
            f"precision regressed from {current_precision:.3f} to {precision:.3f}"
        )
    status_reason = (
        "Validation passed."
        if candidate_ready
        else "Not promoted: " + "; ".join(validation_failures)
    )
    manifest = TypeIIModelManifest(
        model_id=model_id,
        created_at=_utc_now(),
        parent_model_id=None if parent is None else parent.model_id,
        promoted=candidate_ready,
        candidate_model_ready=candidate_ready,
        mask_model_ready=bool(candidate_ready and mask_ready),
        feature_schema_version=CANDIDATE_FEATURE_SCHEMA_VERSION,
        pixel_feature_schema_version=PIXEL_FEATURE_SCHEMA_VERSION,
        metrics=metrics,
        sample_counts=counts,
        station_calibrations=station_calibrations,
        status_reason=status_reason,
    )
    arrays: dict[str, np.ndarray] = {
        "candidate_mean": np.asarray(candidate_scaler.mean_, dtype=np.float64),
        "candidate_scale": np.asarray(candidate_scaler.scale_, dtype=np.float64),
        "candidate_coef": np.asarray(candidate_classifier.coef_[0], dtype=np.float64),
        "candidate_intercept": np.asarray(candidate_classifier.intercept_, dtype=np.float64),
    }
    if candidate_ready and mask_ready and mask_scaler is not None and mask_classifier is not None:
        arrays.update(
            {
                "mask_mean": np.asarray(mask_scaler.mean_, dtype=np.float64),
                "mask_scale": np.asarray(mask_scaler.scale_, dtype=np.float64),
                "mask_coef": np.asarray(mask_classifier.coef_[0], dtype=np.float64),
                "mask_intercept": np.asarray(mask_classifier.intercept_, dtype=np.float64),
            }
        )
    arrays_path = store.models_dir / f"{model_id}.npz"
    _atomic_npz(arrays_path, arrays)
    manifest = TypeIIModelManifest(
        model_id=manifest.model_id,
        created_at=manifest.created_at,
        parent_model_id=manifest.parent_model_id,
        promoted=manifest.promoted,
        candidate_model_ready=manifest.candidate_model_ready,
        mask_model_ready=manifest.mask_model_ready,
        feature_schema_version=manifest.feature_schema_version,
        pixel_feature_schema_version=manifest.pixel_feature_schema_version,
        metrics=manifest.metrics,
        sample_counts=manifest.sample_counts,
        station_calibrations=manifest.station_calibrations,
        arrays_sha256=hashlib.sha256(arrays_path.read_bytes()).hexdigest(),
        status_reason=manifest.status_reason,
    )
    _atomic_json(store.models_dir / f"{model_id}.json", manifest.to_dict())
    if candidate_ready:
        _atomic_json(store.active_manifest_path, {"model_id": model_id})
        _prune_promoted_models(store, keep=4)
    return TypeIITrainingOutcome(manifest=manifest, active_model_changed=bool(candidate_ready))


def _prune_promoted_models(store: TypeIILearningLibrary, *, keep: int) -> None:
    active = store.active_manifest()
    promoted = [item for item in store.model_history() if item.promoted]
    protected = {item.model_id for item in promoted[: max(1, int(keep))]}
    if active is not None:
        protected.add(active.model_id)
    for item in promoted[max(1, int(keep)) :]:
        if item.model_id in protected:
            continue
        for suffix in (".json", ".npz"):
            try:
                (store.models_dir / f"{item.model_id}{suffix}").unlink(missing_ok=True)
            except Exception:
                pass
