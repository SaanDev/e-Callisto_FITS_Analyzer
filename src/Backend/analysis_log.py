"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
import csv


CSV_COLUMNS = [
    "timestamp_utc",
    "project_path",
    "fits_primary",
    "fits_sources",
    "combined_mode",
    "station",
    "date_obs",
    "fit_a",
    "fit_b",
    "fit_r2",
    "fit_rmse",
    "fold",
    "fundamental",
    "harmonic",
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
]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_number_or_blank(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return bool(value)
    try:
        return float(value)
    except Exception:
        return ""


def _fmt_number(value: Any, *, digits: int = 4) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return ""


def build_log_row(
    *,
    project_path: str | None,
    fits_primary: str | None,
    fits_sources: Iterable[str] | None,
    combined_mode: str | None,
    station: str | None,
    date_obs: str | None,
    session: Mapping[str, Any] | None,
) -> dict[str, Any]:
    srcs = [str(s) for s in (fits_sources or []) if str(s).strip()]

    analyzer = dict((session or {}).get("analyzer") or {}) if isinstance(session, Mapping) else {}
    fit = dict(analyzer.get("fit_params") or {})
    shock = dict(analyzer.get("shock_summary") or {})
    max_block = dict((session or {}).get("max_intensity") or {}) if isinstance(session, Mapping) else {}

    row: dict[str, Any] = {
        "timestamp_utc": _now_utc_iso(),
        "analysis_run_id": str((session or {}).get("analysis_run_id", "")) if isinstance(session, Mapping) else "",
        "project_path": str(project_path or ""),
        "fits_primary": str(fits_primary or ""),
        "fits_sources": "|".join(srcs),
        "combined_mode": str(combined_mode or ""),
        "station": str(station or ""),
        "date_obs": str(date_obs or ""),
        "fit_a": _as_number_or_blank(fit.get("a")),
        "fit_b": _as_number_or_blank(fit.get("b")),
        "fit_r2": _as_number_or_blank(fit.get("r2")),
        "fit_rmse": _as_number_or_blank(fit.get("rmse")),
        "fold": _as_number_or_blank(analyzer.get("fold", shock.get("fold"))),
        "fundamental": bool(max_block.get("fundamental", shock.get("fundamental", True))) if session else "",
        "harmonic": bool(max_block.get("harmonic", shock.get("harmonic", False))) if session else "",
        "avg_freq_mhz": _as_number_or_blank(shock.get("avg_freq_mhz")),
        "avg_freq_err_mhz": _as_number_or_blank(shock.get("avg_freq_err_mhz")),
        "avg_drift_mhz_s": _as_number_or_blank(shock.get("avg_drift_mhz_s")),
        "avg_drift_err_mhz_s": _as_number_or_blank(shock.get("avg_drift_err_mhz_s")),
        "start_freq_mhz": _as_number_or_blank(shock.get("start_freq_mhz")),
        "start_freq_err_mhz": _as_number_or_blank(shock.get("start_freq_err_mhz")),
        "initial_shock_speed_km_s": _as_number_or_blank(shock.get("initial_shock_speed_km_s")),
        "initial_shock_speed_err_km_s": _as_number_or_blank(shock.get("initial_shock_speed_err_km_s")),
        "initial_shock_height_rs": _as_number_or_blank(shock.get("initial_shock_height_rs")),
        "initial_shock_height_err_rs": _as_number_or_blank(shock.get("initial_shock_height_err_rs")),
        "avg_shock_speed_km_s": _as_number_or_blank(shock.get("avg_shock_speed_km_s")),
        "avg_shock_speed_err_km_s": _as_number_or_blank(shock.get("avg_shock_speed_err_km_s")),
        "avg_shock_height_rs": _as_number_or_blank(shock.get("avg_shock_height_rs")),
        "avg_shock_height_err_rs": _as_number_or_blank(shock.get("avg_shock_height_err_rs")),
    }

    for key in CSV_COLUMNS:
        row.setdefault(key, "")
    return row


def append_csv_log(path: str, row: Mapping[str, Any]) -> str:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = (not out_path.exists()) or out_path.stat().st_size == 0
    with out_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})

    return str(out_path)


def _txt_block(row: Mapping[str, Any]) -> str:
    fit_line = ""
    if row.get("fit_a", "") != "" and row.get("fit_b", "") != "":
        fit_line = (
            f"f(t) = {_fmt_number(row.get('fit_a'), digits=4)} * t^"
            f"{_fmt_number(row.get('fit_b'), digits=4)}"
        )

    lines = [
        "=" * 88,
        f"Analysis Log Entry UTC: {row.get('timestamp_utc', '')}",
        f"Analysis Run ID: {row.get('analysis_run_id', '')}",
        f"Project: {row.get('project_path', '')}",
        f"FITS primary: {row.get('fits_primary', '')}",
        f"FITS sources: {row.get('fits_sources', '')}",
        f"Combined mode: {row.get('combined_mode', '')}",
        f"Station: {row.get('station', '')}",
        f"Date obs: {row.get('date_obs', '')}",
        "",
        "Fit:",
        f"  Equation: {fit_line}",
        f"  R2: {_fmt_number(row.get('fit_r2'))}",
        f"  RMSE: {_fmt_number(row.get('fit_rmse'))}",
        f"  Fold: {row.get('fold', '')}",
        f"  Fundamental: {row.get('fundamental', '')}",
        f"  Harmonic: {row.get('harmonic', '')}",
        "",
        "Shock parameters:",
        f"  Avg frequency (MHz): {_fmt_number(row.get('avg_freq_mhz'))} +/- {_fmt_number(row.get('avg_freq_err_mhz'))}",
        f"  Avg drift (MHz/s): {_fmt_number(row.get('avg_drift_mhz_s'))} +/- {_fmt_number(row.get('avg_drift_err_mhz_s'))}",
        f"  Start frequency (MHz): {_fmt_number(row.get('start_freq_mhz'))} +/- {_fmt_number(row.get('start_freq_err_mhz'))}",
        (
            "  Initial shock speed (km/s): "
            f"{_fmt_number(row.get('initial_shock_speed_km_s'))} +/- "
            f"{_fmt_number(row.get('initial_shock_speed_err_km_s'))}"
        ),
        (
            "  Initial shock height (Rs): "
            f"{_fmt_number(row.get('initial_shock_height_rs'))} +/- "
            f"{_fmt_number(row.get('initial_shock_height_err_rs'))}"
        ),
        (
            "  Avg shock speed (km/s): "
            f"{_fmt_number(row.get('avg_shock_speed_km_s'))} +/- "
            f"{_fmt_number(row.get('avg_shock_speed_err_km_s'))}"
        ),
        (
            "  Avg shock height (Rs): "
            f"{_fmt_number(row.get('avg_shock_height_rs'))} +/- "
            f"{_fmt_number(row.get('avg_shock_height_err_rs'))}"
        ),
        "",
    ]
    return "\n".join(lines)


def append_txt_summary(path: str, row: Mapping[str, Any]) -> str:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(_txt_block(row))
    return str(out_path)
