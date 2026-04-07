"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

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

TYPE_II_POINT_FIELDS = ("time_seconds", "freqs")
TYPE_II_ANALYSIS_INPUT_FIELDS = (
    "initial_shock_speed_km_s",
    "avg_shock_speed_km_s",
    "initial_shock_height_rs",
    "avg_shock_height_rs",
    "start_freq_mhz",
    "fold",
    "speed_mode",
)
TYPE_II_ANALYSIS_INPUT_NUMERIC_FIELDS = (
    "initial_shock_speed_km_s",
    "avg_shock_speed_km_s",
    "initial_shock_height_rs",
    "avg_shock_height_rs",
    "start_freq_mhz",
)
TYPE_II_RESULT_FIELDS = (
    "start_time_s",
    "upper_start_freq_mhz",
    "lower_start_freq_mhz",
    "bandwidth_mhz",
    "compression_ratio",
    "alfven_mach_number",
    "alfven_speed_km_s",
    "magnetic_field_g",
    "lower_extrapolated",
    "warning",
)
TYPE_II_RESULT_NUMERIC_FIELDS = (
    "start_time_s",
    "upper_start_freq_mhz",
    "lower_start_freq_mhz",
    "bandwidth_mhz",
    "compression_ratio",
    "alfven_mach_number",
    "alfven_speed_km_s",
    "magnetic_field_g",
)
TYPE_II_LEGACY_RESULT_NUMERIC_FIELDS = (
    "upper_drift_mhz_s",
    "shock_speed_km_s",
    "shock_height_rs",
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
    b = abs(float(b))

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


def _normalize_type_ii_points(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    src = dict(raw or {})
    time_seconds = _to_1d_float_array(src.get("time_seconds"))
    freqs = _to_1d_float_array(src.get("freqs"))
    if time_seconds is None or freqs is None or len(time_seconds) != len(freqs) or len(time_seconds) == 0:
        time_seconds = None
        freqs = None
    return {
        "time_seconds": time_seconds,
        "freqs": freqs,
    }


def _normalize_type_ii_fit(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    fit = _normalize_fit_params(raw)
    if fit is None:
        return None
    point_count = max(0, _safe_int((raw or {}).get("point_count", 0), 0))
    fit["point_count"] = point_count or None
    return fit


def _normalize_type_ii_speed_mode(value: Any) -> str:
    mode = str(value or "initial").strip().lower()
    return mode if mode in {"initial", "average"} else "initial"


def _normalize_type_ii_analysis_inputs(
    raw: Mapping[str, Any] | None,
    *,
    analyzer_fold: int,
    shock_summary: Mapping[str, Any] | None,
    legacy_fold: Any = None,
) -> dict[str, Any]:
    src = dict(raw or {})
    shock = dict(shock_summary or {})
    out: dict[str, Any] = {}
    for key in TYPE_II_ANALYSIS_INPUT_NUMERIC_FIELDS:
        value = _safe_float(shock.get(key))
        if value is None:
            value = _safe_float(src.get(key))
        out[key] = value

    shock_has_numeric_inputs = any(_safe_float(shock.get(key)) is not None for key in TYPE_II_ANALYSIS_INPUT_NUMERIC_FIELDS)
    if shock_has_numeric_inputs:
        fold_source = shock.get("fold", analyzer_fold)
        fold_default = analyzer_fold if analyzer_fold > 0 else _safe_int(legacy_fold, 1)
    else:
        fold_source = src.get("fold", legacy_fold)
        fold_default = _safe_int(legacy_fold, analyzer_fold if analyzer_fold > 0 else 1)

    fold_value = _safe_int(fold_source, fold_default)
    out["fold"] = max(1, min(4, int(fold_value or 1)))
    out["speed_mode"] = _normalize_type_ii_speed_mode(src.get("speed_mode"))
    return out


def _normalize_type_ii_results(raw: Mapping[str, Any] | None, *, fold: int) -> dict[str, Any]:
    src = dict(raw or {})
    out: dict[str, Any] = {}
    for key in TYPE_II_RESULT_NUMERIC_FIELDS:
        out[key] = _safe_float(src.get(key))
    out["lower_extrapolated"] = _safe_bool(src.get("lower_extrapolated", False), False)
    out["warning"] = str(src.get("warning") or "").strip()
    return out


def _has_type_ii_payload(type_ii: Mapping[str, Any] | None) -> bool:
    if not isinstance(type_ii, Mapping):
        return False
    upper = dict(type_ii.get("upper") or {})
    lower = dict(type_ii.get("lower") or {})
    results = dict(type_ii.get("results") or {})
    analysis_inputs = dict(type_ii.get("analysis_inputs") or {})
    if upper.get("time_seconds") is not None or lower.get("time_seconds") is not None:
        return True
    if type_ii.get("upper_fit") is not None or type_ii.get("lower_fit") is not None:
        return True
    if any(results.get(key) is not None for key in TYPE_II_RESULT_NUMERIC_FIELDS):
        return True
    if any(results.get(key) is not None for key in TYPE_II_LEGACY_RESULT_NUMERIC_FIELDS):
        return True
    if any(analysis_inputs.get(key) is not None for key in TYPE_II_ANALYSIS_INPUT_NUMERIC_FIELDS):
        return True
    return bool(results.get("warning", ""))


def normalize_session(session: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a session payload into the canonical in-memory structure."""
    if not isinstance(session, Mapping):
        return None

    source = dict(session.get("source") or {})

    max_block_raw = dict(session.get("max_intensity") or {})
    time_channels = _to_1d_float_array(max_block_raw.get("time_channels"))
    time_seconds = _to_1d_float_array(max_block_raw.get("time_seconds"))
    freqs = _to_1d_float_array(max_block_raw.get("freqs"))

    if time_channels is None:
        time_channels = _to_1d_float_array(session.get("time_channels"))
    if time_seconds is None:
        time_seconds = _to_1d_float_array(session.get("time_seconds"))
    if freqs is None:
        freqs = _to_1d_float_array(session.get("freqs"))

    if time_seconds is not None and time_channels is not None and len(time_seconds) != len(time_channels):
        time_seconds = None
    if time_seconds is not None and freqs is not None and len(time_seconds) != len(freqs):
        time_seconds = None

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

    type_ii_raw = dict(session.get("type_ii") or {})
    type_ii_fold = max(1, min(4, _safe_int(type_ii_raw.get("fold", 1), 1)))
    type_ii = {
        "upper": _normalize_type_ii_points(type_ii_raw.get("upper")),
        "lower": _normalize_type_ii_points(type_ii_raw.get("lower")),
        "upper_fit": _normalize_type_ii_fit(type_ii_raw.get("upper_fit")),
        "lower_fit": _normalize_type_ii_fit(type_ii_raw.get("lower_fit")),
        "analysis_inputs": _normalize_type_ii_analysis_inputs(
            type_ii_raw.get("analysis_inputs"),
            analyzer_fold=fold,
            shock_summary=shock_summary,
            legacy_fold=type_ii_raw.get("fold", type_ii_fold),
        ),
        "fold": type_ii_fold,
        "results": _normalize_type_ii_results(type_ii_raw.get("results"), fold=type_ii_fold),
    }
    type_ii["fold"] = int(type_ii["analysis_inputs"].get("fold", type_ii_fold) or type_ii_fold)

    has_max = time_channels is not None and freqs is not None and len(time_channels) == len(freqs) and len(time_channels) > 0
    has_analyzer = fit_params is not None or any(
        v is not None for k, v in shock_summary.items() if k not in {"fold", "fundamental", "harmonic"}
    )
    has_type_ii = _has_type_ii_payload(type_ii)
    if not has_max and not has_analyzer and not has_type_ii:
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
            "time_seconds": time_seconds,
            "freqs": freqs,
            "fundamental": bool(fundamental),
            "harmonic": bool(harmonic),
        },
        "analyzer": {
            "fit_params": fit_params,
            "fold": fold,
            "shock_summary": shock_summary,
        },
        "type_ii": type_ii,
        "ui": {
            "restore_max_window": _safe_bool((session.get("ui") or {}).get("restore_max_window", has_max), has_max),
            "restore_analyzer_window": _safe_bool(
                (session.get("ui") or {}).get("restore_analyzer_window", has_analyzer),
                has_analyzer,
            ),
            "restore_type_ii_window": _safe_bool(
                (session.get("ui") or {}).get("restore_type_ii_window", has_type_ii),
                has_type_ii,
            ),
            "auto_outlier_cleaned": _safe_bool((session.get("ui") or {}).get("auto_outlier_cleaned", False), False),
            "auto_removed_count": max(0, _safe_int((session.get("ui") or {}).get("auto_removed_count", 0), 0)),
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
    time_seconds = max_block.pop("time_seconds", None)
    freqs = max_block.pop("freqs", None)

    if time_channels is not None:
        arrays["analysis_time_channels"] = np.asarray(time_channels, dtype=float)
        max_block["point_count"] = int(arrays["analysis_time_channels"].shape[0])
    if time_seconds is not None:
        arrays["analysis_time_seconds"] = np.asarray(time_seconds, dtype=float)
        if "point_count" not in max_block:
            max_block["point_count"] = int(arrays["analysis_time_seconds"].shape[0])
    if freqs is not None:
        arrays["analysis_freqs"] = np.asarray(freqs, dtype=float)
        if "point_count" not in max_block:
            max_block["point_count"] = int(arrays["analysis_freqs"].shape[0])

    meta_session["max_intensity"] = max_block

    type_ii = dict(meta_session.get("type_ii") or {})
    for band_name in ("upper", "lower"):
        band_meta = dict(type_ii.get(band_name) or {})
        band_time = band_meta.pop("time_seconds", None)
        band_freqs = band_meta.pop("freqs", None)
        if band_time is not None:
            arrays[f"type_ii_{band_name}_time_seconds"] = np.asarray(band_time, dtype=float)
            band_meta["point_count"] = int(arrays[f"type_ii_{band_name}_time_seconds"].shape[0])
        if band_freqs is not None:
            arrays[f"type_ii_{band_name}_freqs"] = np.asarray(band_freqs, dtype=float)
            if "point_count" not in band_meta:
                band_meta["point_count"] = int(arrays[f"type_ii_{band_name}_freqs"].shape[0])
        type_ii[band_name] = band_meta

    meta_session["type_ii"] = type_ii
    return meta_session, arrays


def from_legacy_max_intensity(meta: Mapping[str, Any], arrays: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build canonical session from legacy `meta.max_intensity` + arrays fields."""
    legacy = dict((meta or {}).get("max_intensity") or {})
    if not legacy.get("present"):
        return None

    time_channels = _to_1d_float_array((arrays or {}).get("max_time_channels"))
    time_seconds = _to_1d_float_array((arrays or {}).get("max_time_seconds"))
    freqs = _to_1d_float_array((arrays or {}).get("max_freqs"))
    if time_channels is None or freqs is None:
        return None
    if len(time_channels) == 0 or len(time_channels) != len(freqs):
        return None
    if time_seconds is not None and len(time_seconds) != len(time_channels):
        time_seconds = None

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
            "time_seconds": time_seconds,
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
            "restore_type_ii_window": False,
        },
        "updated_at": _now_iso(),
    }
    return normalize_session(session)


def validate_session_for_source(
    session: Mapping[str, Any] | None,
    *,
    current_shape: Sequence[int] | None = None,
) -> tuple[bool, str]:
    """Validate analysis vectors against the current source shape."""
    normalized = normalize_session(session)
    if normalized is None:
        return False, "No analysis session payload."

    max_block = dict(normalized.get("max_intensity") or {})
    time_channels = _to_1d_float_array(max_block.get("time_channels"))
    time_seconds = _to_1d_float_array(max_block.get("time_seconds"))
    freqs = _to_1d_float_array(max_block.get("freqs"))

    if time_channels is not None or freqs is not None:
        if time_channels is None or freqs is None:
            return False, "Analysis session has incomplete max-intensity vectors."
        if len(time_channels) != len(freqs):
            return False, "Analysis vectors have mismatched lengths."
        if time_seconds is not None and len(time_seconds) != len(freqs):
            return False, "Analysis time-axis values do not match max-intensity vector length."

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
