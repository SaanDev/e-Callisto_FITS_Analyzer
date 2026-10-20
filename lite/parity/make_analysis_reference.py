"""Write the full app's analysis results, for Lite's tests.

Runs the full app's own code, not re-implementations: the Analyzer dialog
(``AnalyzeDialog.plot_fit`` and its shock parameters for every density model,
fold, t0 mode and harmonic setting), ridge tracking, the main window's
maximum-intensity seed, lasso mask, light-curve overlay and drift estimate,
the ruler, the density models and the Type II band-splitting backend.

Inputs Lite needs (spectrograms, point sets) are written as ``.npy`` next to
``lite/parity/out/analysis.json``; the Rust test
``crates/callisto-core/tests/analysis_parity.rs`` replays every case.

usage (from the repository root, in the full app's environment):
    venv/Scripts/python.exe lite/parity/make_analysis_reference.py
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from matplotlib.path import Path as MplPath  # noqa: E402

from src.backend.radio import density_models as dm  # noqa: E402
from src.backend.radio import ridge_tracking as rt  # noqa: E402
from src.backend.radio import type_ii_band_splitting as t2  # noqa: E402
from src.backend.radio.fits_io import load_callisto_fits  # noqa: E402
from src.backend.radio.frequency_axis import frequency_step_mhz, matplotlib_extent  # noqa: E402
from src.backend.session.measurements import calculate_two_point_measurement  # noqa: E402
from src.ui.app.main_window import MainWindow  # noqa: E402
from src.ui.radio.dialogs.analyze_dialog import AnalyzeDialog  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")
OUT = Path(__file__).with_name("out")
OUT.mkdir(exist_ok=True)
CASES: list[dict] = []


def save(name: str, arr) -> str:
    fname = f"analysis.{name}.npy"
    np.save(OUT / fname, np.ascontiguousarray(np.asarray(arr, dtype=np.float64)))
    return fname


def floats(values) -> list:
    """JSON-safe floats: NaN and infinities as strings."""
    out = []
    for v in np.asarray(values, dtype=float).reshape(-1):
        if np.isnan(v):
            out.append("nan")
        elif np.isinf(v):
            out.append("inf" if v > 0 else "-inf")
        else:
            out.append(float(v))
    return out


def scalar(v):
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, str):
        return v
    v = float(v)
    if np.isnan(v):
        return "nan"
    if np.isinf(v):
        return "inf" if v > 0 else "-inf"
    return v


def case(name: str, kind: str, **fields) -> None:
    CASES.append({"name": name, "kind": kind, **fields})


def background_subtracted(name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A fixture with each row's median removed, as float32 (a noise-reduced stand-in)."""
    res = load_callisto_fits(str(FIXTURES / name), memmap=False)
    data = np.asarray(res.data, dtype=np.float32)
    data = (data - np.nanmedian(data, axis=1, keepdims=True)).astype(np.float32)
    return data, np.asarray(res.freqs, dtype=float), np.asarray(res.time, dtype=float)


# --------------------------------------------------------------- the Analyzer


def analyzer_cases(tag: str, time_channels, freqs, time_seconds) -> None:
    for harmonic in (False, True):
        for t0_mode in ("file_start", "burst_onset", "custom"):
            dlg = AnalyzeDialog(
                time_channels,
                freqs,
                f"{tag}.fit",
                fundamental=not harmonic,
                harmonic=harmonic,
                time_seconds=time_seconds,
            )
            index = dlg.t0_combo.findData(t0_mode)
            dlg.t0_combo.setCurrentIndex(index)
            if t0_mode == "custom":
                dlg._set_custom_t0_seconds(float(np.nanmin(dlg.time)) - 1.5)
            try:
                dlg.plot_fit()
            except Exception as exc:  # noqa: BLE001
                case(f"analyzer {tag} {t0_mode} h={harmonic}", "analyzer", error=str(exc))
                continue
            if dlg._fit_params is None:
                case(f"analyzer {tag} {t0_mode} h={harmonic}", "analyzer", error="no fit")
                continue
            runs = []
            for fold in (1, 2, 3, 4):
                dlg.fold_combo.setCurrentIndex(fold - 1)
                for model in dm.DENSITY_MODEL_ORDER:
                    dlg._set_density_model(model)
                    dlg._update_shock_parameters(fold)
                    summary = {k: scalar(v) for k, v in dlg._shock_summary.items()}
                    comparison = {
                        key: {k: scalar(v) for k, v in res.items() if not isinstance(v, np.ndarray)}
                        for key, res in dlg._model_comparison.items()
                    }
                    runs.append(
                        {
                            "fold": fold,
                            "model": model,
                            "summary": summary,
                            "comparison": comparison,
                            "shock_speed": floats(dlg.shock_speed),
                            "shock_height": floats(dlg.R_p),
                            "shock_freq": floats(dlg._shock_freq_values),
                        }
                    )
            case(
                f"analyzer {tag} {t0_mode} h={harmonic}",
                "analyzer",
                time_channels=floats(time_channels),
                time_seconds=floats(time_seconds),
                freqs=floats(freqs),
                harmonic=harmonic,
                t0_mode=t0_mode,
                t0=float(dlg._fit_t0_s),
                fit={k: (floats(v) if isinstance(v, list) else scalar(v)) for k, v in dlg._fit_params.items()},
                freq_err=float(dlg.freq_err),
                fit_time=floats(dlg._fit_time),
                time_fit=floats(dlg._shock_calc_time),
                drift_vals=floats(dlg._drift_vals),
                drift_errs=floats(dlg._drift_errs),
                runs=runs,
            )
            dlg.deleteLater()


rng = np.random.default_rng(20261007)
t = np.arange(1, 241) * 0.25
synthetic = 250.0 * t ** -0.6 + rng.normal(0.0, 1.5, t.size)
analyzer_cases("synthetic", np.arange(t.size, dtype=float), synthetic, t)
steep = 400.0 * (t + 3.0) ** -1.4 + rng.normal(0.0, 0.8, t.size)
analyzer_cases("steep", np.arange(t.size, dtype=float), steep, t)

# --------------------------------------------------- maxima, ridge and lasso

for fixture_name, tag in (("BIR_20261007_101459_01.fit.gz", "bir"), ("INDIA-GAURI_20261007_041500_02.fit.gz", "gauri")):
    data, freqs, time = background_subtracted(fixture_name)
    data_file = save(f"{tag}.data", data)
    freqs_file = save(f"{tag}.freqs", freqs)
    time_file = save(f"{tag}.time", time)

    # Ridge tracking: from the brightest sample, from a seed, falling only.
    ridge_runs = []
    first = rt.track_ridge(data, freqs)
    middle = first.time_indices.size // 2
    on_ridge_col = int(first.time_indices[middle])
    on_ridge_row = int(np.argmin(np.abs(freqs - first.freqs_mhz[middle])))
    for label, seed, settings in (
        ("default", None, rt.RidgeSettings()),
        ("seeded", (min(on_ridge_row + 2, data.shape[0] - 1), on_ridge_col), rt.RidgeSettings(search_channels=4)),
        ("off-burst", (int(data.shape[0] * 0.6), int(data.shape[1] * 0.4)), rt.RidgeSettings()),
        ("falling", None, rt.RidgeSettings(drift=rt.DRIFT_FALLING, threshold_sigma=2.0, max_gap=4)),
    ):
        try:
            result = rt.track_ridge(data, freqs, seed=seed, settings=settings)
            ridge_runs.append(
                {
                    "label": label,
                    "seed": list(seed) if seed else None,
                    "search_channels": settings.search_channels,
                    "threshold_sigma": settings.threshold_sigma,
                    "max_gap": settings.max_gap,
                    "falling": settings.drift == rt.DRIFT_FALLING,
                    "time_indices": [int(v) for v in result.time_indices],
                    "freqs": floats(result.freqs_mhz),
                    "peaks": floats(result.peak_values),
                    "seed_index": list(result.seed_index),
                    "threshold": float(result.threshold),
                    "warnings": list(result.warnings),
                }
            )
        except ValueError as exc:
            ridge_runs.append({"label": label, "seed": list(seed) if seed else None, "error": str(exc)})
    case(f"ridge {tag}", "ridge", data=data_file, freqs=freqs_file, runs=ridge_runs)

    # The lasso: a polygon around the middle of the spectrogram.
    step = frequency_step_mhz(freqs, default=1.0)
    extent = matplotlib_extent(freqs, time, default_step=step)
    x0, x1, y0, y1 = (float(v) for v in extent)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    w, h = (x1 - x0) * 0.3, (y1 - y0) * 0.3
    verts = [(cx - w, cy - h * 0.2), (cx + w * 0.4, cy - h), (cx + w, cy + h * 0.5), (cx - w * 0.3, cy + h)]
    stub = types.SimpleNamespace(freqs=freqs, time=time, _frequency_step_mhz=step)
    stub._rendered_axis_centers = MainWindow._rendered_axis_centers
    xs, ys = MainWindow._lasso_mask_coordinates(stub, data.shape[0], data.shape[1])
    verts_arr = np.asarray(verts, dtype=float)
    verts_arr = np.vstack([verts_arr, verts_arr[0]])
    path = MplPath(verts_arr, closed=True)
    X, Y = np.meshgrid(xs, ys)
    mask = path.contains_points(np.column_stack((X.flatten(), Y.flatten()))).reshape(data.shape)
    isolated = np.zeros_like(data, dtype=np.float32)
    isolated[mask] = data[mask]
    case(
        f"lasso {tag}",
        "lasso",
        data=data_file,
        freqs=freqs_file,
        time=time_file,
        verts=[list(v) for v in verts],
        extent=floats(extent),
        mask=save(f"{tag}.mask", mask.astype(float)),
    )

    # The maximum-intensity seed of the isolated burst, cleaned and not.
    for clean in (False, True):
        seed_stub = types.SimpleNamespace(
            freqs=freqs,
            time=time,
            current_plot_type="Isolated Burst",
            _max_auto_clean_isolated=clean,
            filename=fixture_name,
            raw_data=data,
        )
        seed_stub._current_dynamic_spectrum_source_data = lambda iso=isolated: iso
        seed_stub._is_isolated_burst_plot = lambda: True
        seed_stub._auto_filter_isolated_maxima = types.MethodType(MainWindow._auto_filter_isolated_maxima, seed_stub)
        seed_stub._analysis_source_context = lambda: {"filename": fixture_name}
        seed = MainWindow._build_analysis_seed_from_current_data(seed_stub)
        block = dict(seed["max_intensity"])
        case(
            f"seed {tag} clean={clean}",
            "seed",
            data=save(f"{tag}.isolated", isolated),
            freqs=freqs_file,
            time=time_file,
            clean=clean,
            time_channels=floats(block["time_channels"]),
            time_seconds=floats(block["time_seconds"]),
            max_freqs=floats(block["freqs"]),
            auto_removed=int(seed["ui"]["auto_removed_count"]),
        )
    # The Analyzer on the real (cleaned) maxima.
    analyzer_cases(f"{tag}-maxima", np.asarray(block["time_channels"]), np.asarray(block["freqs"]), np.asarray(block["time_seconds"]))

    # Light curves at a few requested frequencies.
    settings = dict(MainWindow.LIGHT_CURVE_SETTINGS_DEFAULTS)
    lc_stub = types.SimpleNamespace(freqs=freqs, time=time, _current_plot_source_data=data, noise_reduced_data=None, raw_data=data)
    lc_stub._light_curve_settings = settings
    for name in (
        "_current_light_curve_source_data",
        "_nearest_light_curve_frequency",
        "_light_curve_frequency_limits",
        "_light_curve_visual_scale",
        "_light_curve_settings_payload",
        "_normalize_light_curve_settings",
        "_default_light_curve_settings",
        "_light_curve_color_for_index",
    ):
        setattr(lc_stub, name, types.MethodType(getattr(MainWindow, name), lc_stub))
    lc_stub.LIGHT_CURVE_SETTINGS_DEFAULTS = MainWindow.LIGHT_CURVE_SETTINGS_DEFAULTS
    lc_stub.LIGHT_CURVE_COLOR_PALETTE = MainWindow.LIGHT_CURVE_COLOR_PALETTE
    lc_stub._intensity_for_display = lambda arr: np.asarray(arr, dtype=float)
    curves = []
    lo, hi = float(np.nanmin(freqs)), float(np.nanmax(freqs))
    for index, requested in enumerate((lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.61, hi + 5.0)):
        overlay, reason = MainWindow._build_light_curve_overlay(lc_stub, requested, curve_index=index)
        if overlay is None:
            curves.append({"requested": requested, "error": reason})
            continue
        curves.append(
            {
                "requested": requested,
                "index": index,
                "frequency": overlay["frequency_mhz"],
                "row": overlay["row_index"],
                "y": floats(overlay["y"]),
                "color": overlay["color"],
                "label": overlay["label"],
                "label_x": overlay["label_x"],
                "label_y": scalar(overlay["label_y"]),
            }
        )
    case(f"light curve {tag}", "light_curve", data=data_file, freqs=freqs_file, time=time_file, curves=curves)

# ------------------------------------------------------------ ruler, drift

measurements = []
for a, b in (((10.0, 80.0), (25.5, 60.25)), ((40.0, 50.0), (12.0, 70.0)), ((5.0, 50.0), (5.0, 60.0))):
    try:
        r = calculate_two_point_measurement(a, b)
        measurements.append({"a": list(a), "b": list(b), "duration": r.duration_s, "delta": r.frequency_delta_mhz, "slope": r.slope_mhz_s})
    except ValueError as exc:
        measurements.append({"a": list(a), "b": list(b), "error": str(exc)})
case("ruler", "ruler", runs=measurements)

drifts = []
for points in (
    [(10.0, 80.0), (12.5, 75.0), (11.0, 79.0), (20.0, 60.0)],
    [(5.0, 50.0), (5.0, 40.0)],
    [(1.0, 2.0), (float("nan"), 3.0), (3.0, 1.0), (3.0, 0.5), (6.0, 0.25)],
):
    s = MainWindow._drift_summary(points)
    drifts.append(
        {"points": [floats(p) for p in points]}
        | (
            {}
            if s is None
            else {
                "avg": s["avg_drift_mhz_s"],
                "start": s["start_frequency_mhz"],
                "end": s["end_frequency_mhz"],
                "duration": s["duration_s"],
                "sorted": [list(map(float, p)) for p in s["points"]],
            }
        )
    )
case("drift", "drift", runs=drifts)

# ---------------------------------------------------------- density models

grid = np.array([0.5, 2.0, 15.0, 45.0, 80.0, 150.0, 300.0, 800.0, 3000.0])
models = {}
for key in dm.DENSITY_MODEL_ORDER:
    for fold in (1, 2, 4):
        models[f"{key}/{fold}"] = {
            "height": floats(dm.height_rs_from_frequency(grid, key, fold)),
            "gradient": floats(dm.height_gradient_rs_per_mhz(grid, key, fold)),
            "speed": floats(dm.shock_speed_km_s(grid, -0.35, key, fold)),
        }
case("density", "density", freqs=floats(grid), models=models)

# ------------------------------------------------------------- Type II

tt = np.linspace(2.0, 30.0, 18)
upper_t = tt + rng.normal(0.0, 0.05, tt.size)
upper_f = 120.0 * upper_t ** -0.5 + rng.normal(0.0, 0.3, tt.size)
lower_t = tt[2:-1] + rng.normal(0.0, 0.05, tt.size - 3)
lower_f = 100.0 * lower_t ** -0.5 + rng.normal(0.0, 0.3, lower_t.size)
upper_fit = t2.fit_power_law(upper_t, upper_f)
lower_fit = t2.fit_power_law(lower_t, lower_f)
available = np.arange(0, 140) * 0.25
type_ii_runs = []
for speed, start_freq, fold in ((850.0, 45.0, 1), (1200.0, 60.0, 2)):
    entry = {"speed": speed, "start_freq": start_freq, "fold": fold}
    try:
        entry["params"] = {
            k: scalar(v)
            for k, v in t2.calculate_type_ii_parameters(
                upper_time_seconds=upper_t,
                upper_freqs_mhz=upper_f,
                lower_time_seconds=lower_t,
                lower_freqs_mhz=lower_f,
                upper_fit=upper_fit,
                lower_fit=lower_fit,
                analysis_start_freq_mhz=start_freq,
                analysis_shock_speed_km_s=speed,
                available_time_seconds=available,
            ).items()
        }
    except ValueError as exc:
        entry["params_error"] = str(exc)
    try:
        prof = t2.calculate_b_vs_r_profile(
            upper_time_seconds=upper_t,
            upper_freqs_mhz=upper_f,
            lower_time_seconds=lower_t,
            lower_freqs_mhz=lower_f,
            upper_fit=upper_fit,
            lower_fit=lower_fit,
            analysis_shock_speed_km_s=speed,
            fold=fold,
            available_time_seconds=available,
        )
        entry["bvr"] = {
            "heights": floats(prof["heights_rs"]),
            "field": floats(prof["magnetic_field_g"]),
            "fit_a": prof["fit"]["a"],
            "fit_b": prof["fit"]["b"],
            "fit_r2": prof["fit"]["r2"],
        }
    except ValueError as exc:
        entry["bvr_error"] = str(exc)
    type_ii_runs.append(entry)


def fit_dict(fit):
    return {"a": fit["a"], "b": fit["b"], "std_errs": floats(fit["std_errs"]), "r2": fit["r2"], "rmse": fit["rmse"], "n": fit["point_count"]}


case(
    "type ii",
    "type_ii",
    upper_t=floats(upper_t),
    upper_f=floats(upper_f),
    lower_t=floats(lower_t),
    lower_f=floats(lower_f),
    available=floats(available),
    upper_fit=fit_dict(upper_fit),
    lower_fit=fit_dict(lower_fit),
    runs=type_ii_runs,
)

(OUT / "analysis.json").write_text(json.dumps(CASES, indent=1), encoding="utf-8")
print(f"{len(CASES)} analysis cases written to {OUT / 'analysis.json'}")
