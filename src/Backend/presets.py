"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Iterable


PRESET_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_name(name: str) -> str:
    return " ".join(str(name or "").split()).strip()


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def normalize_preset(raw: dict[str, Any]) -> dict[str, Any]:
    name = normalize_name(raw.get("name", ""))
    if not name:
        raise ValueError("Preset name is required")

    settings = dict(raw.get("settings") or {})

    return {
        "name": name,
        "version": _safe_int(raw.get("version"), PRESET_SCHEMA_VERSION),
        "created_at": str(raw.get("created_at") or _now_iso()),
        "settings": {
            "lower_slider": _safe_int(settings.get("lower_slider"), 0),
            "upper_slider": _safe_int(settings.get("upper_slider"), 255),
            "use_db": bool(settings.get("use_db", False)),
            "use_utc": bool(settings.get("use_utc", False)),
            "cmap": str(settings.get("cmap") or "Custom"),
            "graph": dict(settings.get("graph") or {}),
            "rfi": dict(settings.get("rfi") or {}),
            "annotation_style_defaults": dict(settings.get("annotation_style_defaults") or {}),
        },
    }


def build_preset(name: str, settings: dict[str, Any]) -> dict[str, Any]:
    return normalize_preset(
        {
            "name": name,
            "version": PRESET_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "settings": dict(settings or {}),
        }
    )


def parse_presets_json(raw_text: str | None) -> list[dict[str, Any]]:
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
            out.append(normalize_preset(item))
        except Exception:
            continue
    return out


def dump_presets_json(items: Iterable[dict[str, Any]]) -> str:
    normalized: list[dict[str, Any]] = []
    for item in items:
        try:
            normalized.append(normalize_preset(dict(item)))
        except Exception:
            continue
    return json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False)


def upsert_preset(items: Iterable[dict[str, Any]], preset: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    normalized = normalize_preset(preset)
    key = normalized["name"].casefold()

    out: list[dict[str, Any]] = []
    replaced = False
    for item in items:
        try:
            cur = normalize_preset(dict(item))
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


def delete_preset(items: Iterable[dict[str, Any]], name: str) -> tuple[list[dict[str, Any]], bool]:
    key = normalize_name(name).casefold()
    if not key:
        return list(items), False

    out: list[dict[str, Any]] = []
    removed = False
    for item in items:
        try:
            cur = normalize_preset(dict(item))
        except Exception:
            continue
        if cur["name"].casefold() == key:
            removed = True
            continue
        out.append(cur)

    return out, removed
