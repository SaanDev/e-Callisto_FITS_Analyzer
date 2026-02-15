"""
Annotation model helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4


ALLOWED_KINDS = {"polygon", "line", "text"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _norm_points(points: Iterable[Iterable[float]] | None) -> list[list[float]]:
    out: list[list[float]] = []
    for item in points or []:
        try:
            x, y = item
            out.append([float(x), float(y)])
        except Exception:
            continue
    return out


def make_annotation(
    *,
    kind: str,
    points: Iterable[Iterable[float]],
    text: str = "",
    color: str = "#00d4ff",
    line_width: float = 1.5,
    visible: bool = True,
) -> dict[str, Any]:
    kind_norm = str(kind or "").strip().lower()
    if kind_norm not in ALLOWED_KINDS:
        raise ValueError(f"Unsupported annotation kind: {kind}")

    payload = {
        "id": uuid4().hex,
        "kind": kind_norm,
        "points": _norm_points(points),
        "text": str(text or ""),
        "color": str(color or "#00d4ff"),
        "line_width": float(line_width),
        "visible": bool(visible),
        "created_at": _now_iso(),
    }
    return payload


def normalize_annotations(items: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).strip().lower()
        if kind not in ALLOWED_KINDS:
            continue

        points = _norm_points(raw.get("points", []))
        if kind in {"polygon", "line"} and len(points) < 2:
            continue
        if kind == "text" and len(points) < 1:
            continue

        out.append(
            {
                "id": str(raw.get("id") or uuid4().hex),
                "kind": kind,
                "points": points,
                "text": str(raw.get("text", "")),
                "color": str(raw.get("color") or "#00d4ff"),
                "line_width": float(raw.get("line_width", 1.5)),
                "visible": bool(raw.get("visible", True)),
                "created_at": str(raw.get("created_at") or _now_iso()),
            }
        )
    return out


def toggle_all_visibility(items: Iterable[dict[str, Any]], visible: bool) -> list[dict[str, Any]]:
    out = normalize_annotations(items)
    for item in out:
        item["visible"] = bool(visible)
    return out
