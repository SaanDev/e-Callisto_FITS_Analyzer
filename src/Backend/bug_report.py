"""
Bug report payload and diagnostics bundle helpers.
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote_plus

from src.Backend.provenance import payload_to_markdown


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")


def default_bug_report_filename() -> str:
    return f"bug_report_{timestamp_slug()}.zip"


def _drop_large_sensitive_fields(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        out = {}
        for key, value in obj.items():
            k = str(key)
            low = k.strip().lower()
            if low in {
                "raw_data",
                "noise_reduced_data",
                "noise_reduced_original",
                "current_display_data",
                "_current_plot_source_data",
                "arrays",
            }:
                continue
            out[k] = _drop_large_sensitive_fields(value)
        return out
    if isinstance(obj, list):
        return [_drop_large_sensitive_fields(v) for v in obj]
    if isinstance(obj, tuple):
        return [_drop_large_sensitive_fields(v) for v in obj]
    return obj


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Mapping):
        return {str(k): _to_jsonable(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return _to_jsonable(tolist())
        except Exception:
            pass

    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _to_jsonable(item())
        except Exception:
            pass

    return str(value)


def build_bug_report_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    raw = _as_mapping(context)
    cleaned = _drop_large_sensitive_fields(raw)
    payload = {
        "generated_at": _now_iso(),
        "report": _to_jsonable(cleaned),
    }
    return payload


def build_github_issue_url(repo: str, title: str, body: str) -> str:
    repo_text = str(repo or "").strip().strip("/")
    if not repo_text:
        raise ValueError("Repository is required for GitHub issue URL.")
    title_text = str(title or "").strip() or "Bug Report"
    body_text = str(body or "").strip()
    return (
        f"https://github.com/{repo_text}/issues/new"
        f"?title={quote_plus(title_text)}"
        f"&body={quote_plus(body_text)}"
    )


def _payload_to_markdown(payload: Mapping[str, Any]) -> str:
    data = _as_mapping(payload)
    report = _as_mapping(data.get("report"))
    lines: list[str] = []
    lines.append("# Bug Report")
    lines.append("")
    lines.append(f"Generated at: `{data.get('generated_at', '')}`")
    lines.append("")

    summary = _as_mapping(report.get("summary"))
    if summary:
        lines.append("## Summary")
        for key, value in summary.items():
            lines.append(f"- {key}: `{value}`")
        lines.append("")

    env = _as_mapping(report.get("environment"))
    if env:
        lines.append("## Environment")
        for key, value in env.items():
            lines.append(f"- {key}: `{value}`")
        lines.append("")

    session = _as_mapping(report.get("session"))
    if session:
        lines.append("## Session")
        for key, value in session.items():
            lines.append(f"- {key}: `{value}`")
        lines.append("")

    ops = list(report.get("operation_log") or [])
    lines.append("## Operation Log")
    if not ops:
        lines.append("- (none)")
    else:
        for item in ops:
            if isinstance(item, Mapping):
                ts = item.get("ts", "")
                msg = item.get("msg", "")
                lines.append(f"- `{ts}` {msg}")
            else:
                lines.append(f"- {item}")
    lines.append("")

    return "\n".join(lines)


def write_bug_report_bundle(
    base_path: str,
    payload: Mapping[str, Any],
    provenance_payload: Mapping[str, Any] | None = None,
    notes_md: str = "",
) -> str:
    path = str(base_path or "").strip()
    if not path:
        raise ValueError("Output path is required for bug report bundle.")

    if not path.lower().endswith(".zip"):
        path = f"{path}.zip"

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    payload_dict = _as_mapping(payload)
    bug_json = json.dumps(_to_jsonable(payload_dict), indent=2, sort_keys=True, ensure_ascii=False)
    bug_md = _payload_to_markdown(payload_dict)
    notes = str(notes_md or "").strip()
    provenance = _as_mapping(provenance_payload) if provenance_payload else None

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bug_report.json", bug_json.encode("utf-8"))
        zf.writestr("bug_report.md", bug_md.encode("utf-8"))
        if notes:
            zf.writestr("issue_notes.md", notes.encode("utf-8"))
        if provenance is not None:
            zf.writestr(
                "provenance.json",
                json.dumps(_to_jsonable(provenance), indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8"),
            )
            zf.writestr("provenance.md", payload_to_markdown(provenance).encode("utf-8"))

    return path
