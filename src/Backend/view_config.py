"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Iterable


VIEW_CONFIG_SCHEMA_VERSION = 1
VIEW_CONFIG_APP_NAME = "e-CALLISTO FITS Analyzer"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_name(name: str) -> str:
    return " ".join(str(name or "").split()).strip()


def _safe_float(value: Any, default: float | None = None) -> float:
    try:
        out = float(value)
    except Exception:
        if default is None:
            raise ValueError(f"Invalid numeric value: {value!r}")
        out = float(default)
    if not math.isfinite(out):
        if default is None:
            raise ValueError(f"Invalid finite numeric value: {value!r}")
        return float(default)
    return float(out)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_noise_clip_scale(value: Any) -> str:
    return "signed_log" if str(value or "").strip().lower() == "signed_log" else "linear"


def normalize_display_range(raw: dict[str, Any] | None) -> dict[str, float]:
    payload = dict(raw or {})
    time_start = _safe_float(payload.get("time_start_s"))
    time_stop = _safe_float(payload.get("time_stop_s"))
    freq_a = _safe_float(payload.get("freq_min_mhz", payload.get("freq_start_mhz")))
    freq_b = _safe_float(payload.get("freq_max_mhz", payload.get("freq_stop_mhz")))

    if time_stop <= time_start:
        raise ValueError("Display range stop time must be later than start time.")
    if abs(freq_b - freq_a) <= 1e-9:
        raise ValueError("Display range frequency bounds must be different.")

    freq_min, freq_max = sorted((freq_a, freq_b))
    return {
        "time_start_s": float(time_start),
        "time_stop_s": float(time_stop),
        "freq_min_mhz": float(freq_min),
        "freq_max_mhz": float(freq_max),
    }


def normalize_visual_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(raw or {})
    graph = dict(payload.get("graph") or {})
    return {
        "use_db": bool(payload.get("use_db", False)),
        "use_utc": bool(payload.get("use_utc", False)),
        "noise_clip_low": _safe_float(payload.get("noise_clip_low", 0.0), 0.0),
        "noise_clip_high": _safe_float(payload.get("noise_clip_high", 0.0), 0.0),
        "noise_clip_scale": _normalize_noise_clip_scale(payload.get("noise_clip_scale")),
        "cmap": str(payload.get("cmap") or "Custom"),
        "graph": graph,
    }


def normalize_display_range_preset(raw: dict[str, Any]) -> dict[str, Any]:
    name = normalize_name(raw.get("name", ""))
    if not name:
        raise ValueError("Display range preset name is required.")
    version = _safe_int(raw.get("version"), VIEW_CONFIG_SCHEMA_VERSION)
    if version != VIEW_CONFIG_SCHEMA_VERSION:
        raise ValueError(f"Unsupported display range preset version: {version}")
    return {
        "name": name,
        "version": version,
        "created_at": str(raw.get("created_at") or _now_iso()),
        "range": normalize_display_range(raw.get("range") or raw),
        "time_mode": "ut" if str(raw.get("time_mode") or "").strip().lower() == "ut" else "seconds",
    }


def build_display_range_preset(name: str, display_range: dict[str, Any], *, time_mode: str = "seconds") -> dict[str, Any]:
    return normalize_display_range_preset(
        {
            "name": name,
            "version": VIEW_CONFIG_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "range": normalize_display_range(display_range),
            "time_mode": time_mode,
        }
    )


def parse_display_range_presets_json(raw_text: str | None) -> list[dict[str, Any]]:
    if not raw_text:
        return []
    try:
        parsed = json.loads(raw_text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            out.append(normalize_display_range_preset(item))
        except Exception:
            continue
    return out


def dump_display_range_presets_json(items: Iterable[dict[str, Any]]) -> str:
    normalized: list[dict[str, Any]] = []
    for item in items:
        try:
            normalized.append(normalize_display_range_preset(dict(item)))
        except Exception:
            continue
    return json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False)


def upsert_display_range_preset(items: Iterable[dict[str, Any]], preset: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    normalized = normalize_display_range_preset(preset)
    key = normalized["name"].casefold()
    out: list[dict[str, Any]] = []
    replaced = False
    for item in items:
        try:
            cur = normalize_display_range_preset(dict(item))
        except Exception:
            continue
        if cur["name"].casefold() == key:
            out.append(normalized)
            replaced = True
        else:
            out.append(cur)
    if not replaced:
        out.append(normalized)
    out.sort(key=lambda p: p["name"].casefold())
    return out, replaced


def delete_display_range_preset(items: Iterable[dict[str, Any]], name: str) -> tuple[list[dict[str, Any]], bool]:
    key = normalize_name(name).casefold()
    if not key:
        return list(items), False
    out: list[dict[str, Any]] = []
    removed = False
    for item in items:
        try:
            cur = normalize_display_range_preset(dict(item))
        except Exception:
            continue
        if cur["name"].casefold() == key:
            removed = True
            continue
        out.append(cur)
    return out, removed


def normalize_view_config(raw: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw or {})
    version = _safe_int(payload.get("version"), VIEW_CONFIG_SCHEMA_VERSION)
    if version != VIEW_CONFIG_SCHEMA_VERSION:
        raise ValueError(f"Unsupported view config version: {version}")
    range_raw = payload.get("range", None)
    range_payload = normalize_display_range(range_raw) if isinstance(range_raw, dict) else None
    return {
        "version": version,
        "created_at": str(payload.get("created_at") or _now_iso()),
        "app": str(payload.get("app") or VIEW_CONFIG_APP_NAME),
        "range": range_payload,
        "time_mode": "ut" if str(payload.get("time_mode") or "").strip().lower() == "ut" else "seconds",
        "visual": normalize_visual_config(payload.get("visual") or {}),
    }


def build_view_config(
    *,
    display_range: dict[str, Any] | None = None,
    visual: dict[str, Any] | None = None,
    time_mode: str = "seconds",
) -> dict[str, Any]:
    return normalize_view_config(
        {
            "version": VIEW_CONFIG_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "app": VIEW_CONFIG_APP_NAME,
            "range": normalize_display_range(display_range) if display_range else None,
            "time_mode": time_mode,
            "visual": normalize_visual_config(visual or {}),
        }
    )


def parse_view_config_json(raw_text: str | None) -> dict[str, Any]:
    if not raw_text:
        raise ValueError("View config JSON is empty.")
    try:
        parsed = json.loads(raw_text)
    except Exception as exc:
        raise ValueError("View config JSON is invalid.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("View config JSON must contain an object.")
    return normalize_view_config(parsed)


def dump_view_config_json(config: dict[str, Any]) -> str:
    return json.dumps(normalize_view_config(config), indent=2, sort_keys=True, ensure_ascii=False)
