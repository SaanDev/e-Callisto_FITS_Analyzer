"""
Provenance report generation utilities.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_provenance_payload(context: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "generated_at": _now_iso(),
        "app": dict(context.get("app") or {}),
        "data_source": dict(context.get("data_source") or {}),
        "processing": dict(context.get("processing") or {}),
        "rfi": dict(context.get("rfi") or {}),
        "annotations": list(context.get("annotations") or []),
        "max_intensity": context.get("max_intensity"),
        "time_sync": dict(context.get("time_sync") or {}),
        "operation_log": list(context.get("operation_log") or []),
    }
    return payload


def payload_to_markdown(payload: dict[str, Any]) -> str:
    app = dict(payload.get("app") or {})
    source = dict(payload.get("data_source") or {})
    proc = dict(payload.get("processing") or {})
    rfi = dict(payload.get("rfi") or {})
    ann = list(payload.get("annotations") or [])
    sync = dict(payload.get("time_sync") or {})
    ops = list(payload.get("operation_log") or [])

    lines: list[str] = []
    lines.append("# e-CALLISTO FITS Analyzer Provenance")
    lines.append("")
    lines.append(f"Generated at: `{payload.get('generated_at', '')}`")
    lines.append("")

    lines.append("## App")
    lines.append(f"- Name: `{app.get('name', '')}`")
    lines.append(f"- Version: `{app.get('version', '')}`")
    lines.append(f"- Platform: `{app.get('platform', '')}`")
    lines.append("")

    lines.append("## Data Source")
    for key in ("filename", "is_combined", "combined_mode", "shape", "freq_range_mhz", "time_range_s", "sources"):
        lines.append(f"- {key}: `{source.get(key)}`")
    lines.append("")

    lines.append("## Processing")
    for key in ("plot_type", "use_db", "use_utc", "slider_low", "slider_high", "cmap"):
        lines.append(f"- {key}: `{proc.get(key)}`")
    lines.append("")

    lines.append("## RFI")
    for key in ("enabled", "applied", "kernel_time", "kernel_freq", "channel_z_threshold", "percentile_clip"):
        lines.append(f"- {key}: `{rfi.get(key)}`")
    lines.append("")

    lines.append("## Annotations")
    lines.append(f"- count: `{len(ann)}`")
    for item in ann:
        lines.append(
            f"- [{item.get('kind')}] id={item.get('id')} visible={item.get('visible')} points={len(item.get('points') or [])}"
        )
    lines.append("")

    lines.append("## Time Sync")
    for key, value in sync.items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")

    lines.append("## Operation Log")
    if not ops:
        lines.append("- (none)")
    else:
        for item in ops:
            ts = item.get("ts", "") if isinstance(item, dict) else ""
            msg = item.get("msg", "") if isinstance(item, dict) else str(item)
            lines.append(f"- `{ts}` {msg}")
    lines.append("")

    return "\n".join(lines)


def dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def write_provenance_files(base_path: str, payload: dict[str, Any]) -> tuple[str, str]:
    stem = Path(base_path)
    if stem.suffix:
        stem = stem.with_suffix("")

    json_path = str(stem) + "_provenance.json"
    md_path = str(stem) + "_provenance.md"

    Path(json_path).write_text(dump_json(payload), encoding="utf-8")
    Path(md_path).write_text(payload_to_markdown(payload), encoding="utf-8")

    return json_path, md_path
