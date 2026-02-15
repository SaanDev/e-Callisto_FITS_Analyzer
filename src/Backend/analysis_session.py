"""Canonical Type-II analysis session helpers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import uuid
from typing import Any, Mapping, Sequence

import numpy as np

SESSION_SCHEMA_VERSION = 1

SHOCK_SUMMARY_FIELDS = (
    "avg_freq_mhz",
    "avg_freq_err_mhz",
    "avg_drift_mhz_s",
    "avg_drift_err_mhz_s",
    "start_freq_mhz",
    "start_freq_err_mhz",
    "initial_shock_speed_km_s",
    "initial_shock_speed_err_km_s",
    "initial_shock_height_rs",
    "initial_shock_height_err_rs",
    "avg_shock_speed_km_s",
    "avg_shock_speed_err_km_s",
    "avg_shock_height_rs",
    "avg_shock_height_err_rs",
    "fold",
    "fundamental",
    "harmonic",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return bool(value)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _shape_from(value: Any) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        if value.ndim < 2:
            return None
        return [int(value.shape[0]), int(value.shape[1])]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) < 2:
            return None
        try:
            return [int(value[0]), int(value[1])]
        except Exception:
            return None
    return None


def _to_1d_float_array(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    try:
        arr = np.asarray(value, dtype=float)
    except Exception:
        return None
    if arr.ndim == 0:
        return None
    return arr.reshape(-1).copy()


def _normalize_fit_params(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    a = _safe_float(raw.get("a"))
    b = _safe_float(raw.get("b"))
    if a is None or b is None:
        return None

    std_errs_in = raw.get("std_errs")
    std_errs = [None, None]
    if isinstance(std_errs_in, Sequence) and not isinstance(std_errs_in, (str, bytes)) and len(std_errs_in) >= 2:
        std_errs = [_safe_float(std_errs_in[0]), _safe_float(std_errs_in[1])]

    return {
        "a": float(a),
        "b": float(b),
        "std_errs": [
            float(std_errs[0]) if std_errs[0] is not None else None,
            float(std_errs[1]) if std_errs[1] is not None else None,
        ],
        "r2": _safe_float(raw.get("r2")),
        "rmse": _safe_float(raw.get("rmse")),
    }


def _normalize_shock_summary(raw: Mapping[str, Any] | None, *, fold: int, fundamental: bool, harmonic: bool) -> dict[str, Any]:
    src = dict(raw or {})
    out: dict[str, Any] = {}
    for key in SHOCK_SUMMARY_FIELDS:
        if key in ("fold", "fundamental", "harmonic"):
            continue
        out[key] = _safe_float(src.get(key))
    out["fold"] = int(_safe_int(src.get("fold", fold), fold))
    out["fundamental"] = _safe_bool(src.get("fundamental", fundamental), fundamental)
    out["harmonic"] = _safe_bool(src.get("harmonic", harmonic), harmonic)
    return out


def normalize_session(session: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a session payload into the canonical in-memory structure."""
    if not isinstance(session, Mapping):
        return None

    source = dict(session.get("source") or {})

    max_block_raw = dict(session.get("max_intensity") or {})
    time_channels = _to_1d_float_array(max_block_raw.get("time_channels"))
    freqs = _to_1d_float_array(max_block_raw.get("freqs"))

    # Compatibility: accept previous top-level keys.
    if time_channels is None:
        time_channels = _to_1d_float_array(session.get("time_channels"))
    if freqs is None:
        freqs = _to_1d_float_array(session.get("freqs"))

    fundamental = _safe_bool(max_block_raw.get("fundamental", True), True)
    harmonic = _safe_bool(max_block_raw.get("harmonic", False), False)

    analyzer_raw = dict(session.get("analyzer") or {})
    fit_params = _normalize_fit_params(analyzer_raw.get("fit_params"))
    fold = max(1, min(4, _safe_int(analyzer_raw.get("fold", 1), 1)))
    shock_summary = _normalize_shock_summary(
        analyzer_raw.get("shock_summary"),
        fold=fold,
        fundamental=fundamental,
        harmonic=harmonic,
    )

    has_max = time_channels is not None and freqs is not None and len(time_channels) == len(freqs) and len(time_channels) > 0
    has_analyzer = fit_params is not None or any(v is not None for k, v in shock_summary.items() if k not in {"fold", "fundamental", "harmonic"})
    if not has_max and not has_analyzer:
        return None

    normalized = {
        "version": _safe_int(session.get("version", SESSION_SCHEMA_VERSION), SESSION_SCHEMA_VERSION),
        "analysis_run_id": str(session.get("analysis_run_id") or uuid.uuid4()),
        "source": {
            "filename": str(source.get("filename") or ""),
            "is_combined": _safe_bool(source.get("is_combined", False), False),
            "combined_mode": source.get("combined_mode"),
            "combined_sources": list(source.get("combined_sources") or []),
            "shape": _shape_from(source.get("shape")),
        },
        "max_intensity": {
            "time_channels": time_channels,
            "freqs": freqs,
            "fundamental": bool(fundamental),
            "harmonic": bool(harmonic),
        },
        "analyzer": {
            "fit_params": fit_params,
            "fold": fold,
            "shock_summary": shock_summary,
        },
        "ui": {
            "restore_max_window": _safe_bool((session.get("ui") or {}).get("restore_max_window", has_max), has_max),
            "restore_analyzer_window": _safe_bool(
                (session.get("ui") or {}).get("restore_analyzer_window", has_analyzer),
                has_analyzer,
            ),
        },
        "updated_at": str(session.get("updated_at") or _now_iso()),
    }
    return normalized


def to_project_payload(session: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, np.ndarray]]:
    """Return project-meta friendly session + arrays payload."""
    normalized = normalize_session(session)
    if normalized is None:
        return None, {}

    meta_session = deepcopy(normalized)
    arrays: dict[str, np.ndarray] = {}

    max_block = dict(meta_session.get("max_intensity") or {})
    time_channels = max_block.pop("time_channels", None)
    freqs = max_block.pop("freqs", None)

    if time_channels is not None:
        arrays["analysis_time_channels"] = np.asarray(time_channels, dtype=float)
        max_block["point_count"] = int(arrays["analysis_time_channels"].shape[0])
    if freqs is not None:
        arrays["analysis_freqs"] = np.asarray(freqs, dtype=float)
        if "point_count" not in max_block:
            max_block["point_count"] = int(arrays["analysis_freqs"].shape[0])

    meta_session["max_intensity"] = max_block
    return meta_session, arrays


def from_legacy_max_intensity(meta: Mapping[str, Any], arrays: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build canonical session from legacy `meta.max_intensity` + arrays fields."""
    legacy = dict((meta or {}).get("max_intensity") or {})
    if not legacy.get("present"):
        return None

    time_channels = _to_1d_float_array((arrays or {}).get("max_time_channels"))
    freqs = _to_1d_float_array((arrays or {}).get("max_freqs"))
    if time_channels is None or freqs is None:
        return None
    if len(time_channels) == 0 or len(time_channels) != len(freqs):
        return None

    analyzer_legacy = dict(legacy.get("analyzer") or {})
    fold = max(1, min(4, _safe_int(analyzer_legacy.get("fold", 1), 1)))
    fundamental = _safe_bool(legacy.get("fundamental", analyzer_legacy.get("fundamental", True)), True)
    harmonic = _safe_bool(legacy.get("harmonic", analyzer_legacy.get("harmonic", False)), False)

    source_shape = _shape_from((meta or {}).get("shape"))

    session = {
        "version": SESSION_SCHEMA_VERSION,
        "source": {
            "filename": str((meta or {}).get("filename") or ""),
            "is_combined": _safe_bool((meta or {}).get("is_combined", False), False),
            "combined_mode": (meta or {}).get("combined_mode"),
            "combined_sources": list((meta or {}).get("combined_sources") or []),
            "shape": source_shape,
        },
        "max_intensity": {
            "time_channels": time_channels,
            "freqs": freqs,
            "fundamental": bool(fundamental),
            "harmonic": bool(harmonic),
        },
        "analyzer": {
            "fit_params": _normalize_fit_params(analyzer_legacy.get("fit_params")),
            "fold": int(fold),
            "shock_summary": _normalize_shock_summary(
                analyzer_legacy.get("shock_summary"),
                fold=fold,
                fundamental=fundamental,
                harmonic=harmonic,
            ),
        },
        "ui": {
            "restore_max_window": True,
            "restore_analyzer_window": bool(analyzer_legacy),
        },
        "updated_at": _now_iso(),
    }
    return normalize_session(session)


def validate_session_for_source(
    session: Mapping[str, Any] | None,
    *,
    current_shape: Sequence[int] | None = None,
) -> tuple[bool, str]:
    """Validate max-intensity vectors against the current source shape."""
    normalized = normalize_session(session)
    if normalized is None:
        return False, "No analysis session payload."

    max_block = dict(normalized.get("max_intensity") or {})
    time_channels = _to_1d_float_array(max_block.get("time_channels"))
    freqs = _to_1d_float_array(max_block.get("freqs"))
    if time_channels is None or freqs is None:
        return False, "Analysis session has no max-intensity vectors."

    if len(time_channels) != len(freqs):
        return False, "Analysis vectors have mismatched lengths."

    if current_shape is not None:
        try:
            current_cols = int(current_shape[1])
        except Exception:
            current_cols = 0
        if current_cols > 0 and len(time_channels) != current_cols:
            return False, "Analysis vector length does not match current dataset time-axis length."

    src_shape = _shape_from((normalized.get("source") or {}).get("shape"))
    cur_shape = _shape_from(current_shape)
    if src_shape and cur_shape and src_shape != cur_shape:
        return False, "Saved analysis source shape does not match current dataset shape."

    return True, ""
