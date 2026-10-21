//! Lite's analysis results against the full app's.
//!
//! `lite/parity/make_analysis_reference.py` runs the full app's own analysis
//! code (the Analyzer dialog, ridge tracking, the main window's helpers, the
//! density models, the Type II backend) and writes
//! `lite/parity/out/analysis.json` plus `.npy` inputs. This test replays every
//! case with `callisto_core::analysis` and lists all mismatches before failing.
//!
//! Without references the test only prints a note, unless
//! `CALLISTO_PARITY_REQUIRED=1`.

use std::path::{Path, PathBuf};

use callisto_core::analysis::analyzer::{
    analysis_time_axis, analyzer_fit, burst_onset_t0, resolve_time_seconds, update_shock_parameters,
};
use callisto_core::analysis::density::{self, DensityModel};
use callisto_core::analysis::fit::fit_power_law;
use callisto_core::analysis::lasso::lasso_mask;
use callisto_core::analysis::light_curve::{LightCurveSettings, build_overlay, nearest_frequency};
use callisto_core::analysis::maxima::analysis_seed;
use callisto_core::analysis::measure::{drift_summary, two_point};
use callisto_core::analysis::ridge::{RidgeSettings, track_ridge};
use callisto_core::analysis::type_ii::{BandFit, calculate_b_vs_r_profile, calculate_type_ii_parameters};
use callisto_core::radio::array::Array2;
use callisto_core::radio::frequency_axis::{frequency_step_mhz, matplotlib_extent};
use serde_json::Value;

fn out_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/out")
}

fn read_npy(path: &Path) -> (Vec<usize>, Vec<f64>) {
    let bytes = std::fs::read(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    assert_eq!(&bytes[..6], b"\x93NUMPY", "{} is not a .npy file", path.display());
    let (header_len, header_start) = match bytes[6] {
        1 => (u16::from_le_bytes([bytes[8], bytes[9]]) as usize, 10),
        _ => (u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize, 12),
    };
    let header = std::str::from_utf8(&bytes[header_start..header_start + header_len]).unwrap();
    let shape_text = header.split("'shape': (").nth(1).unwrap().split(')').next().unwrap();
    let shape: Vec<usize> = shape_text.split(',').filter_map(|s| s.trim().parse().ok()).collect();
    let data = bytes[header_start + header_len..].as_chunks::<8>().0.iter().map(|c| f64::from_le_bytes(*c)).collect();
    (shape, data)
}

fn num(v: &Value) -> f64 {
    match v {
        Value::Number(n) => n.as_f64().unwrap(),
        Value::String(s) if s == "nan" => f64::NAN,
        Value::String(s) if s == "inf" => f64::INFINITY,
        Value::String(s) if s == "-inf" => f64::NEG_INFINITY,
        Value::Null => f64::NAN,
        Value::Bool(b) => *b as i32 as f64,
        other => panic!("not a number: {other}"),
    }
}

fn nums(v: &Value) -> Vec<f64> {
    v.as_array().unwrap().iter().map(num).collect()
}

struct Checker {
    failures: Vec<String>,
    compared: usize,
}

impl Checker {
    fn value(&mut self, case: &str, what: &str, got: f64, want: f64, rtol: f64, atol: f64) {
        self.compared += 1;
        let ok = if want.is_nan() {
            got.is_nan()
        } else if want.is_infinite() {
            got == want
        } else {
            (got - want).abs() <= atol + rtol * want.abs()
        };
        if !ok {
            self.failures.push(format!("{case}: {what}: got {got:e}, want {want:e}"));
        }
    }

    fn values(&mut self, case: &str, what: &str, got: &[f64], want: &[f64], rtol: f64, atol: f64) {
        if got.len() != want.len() {
            self.failures.push(format!("{case}: {what}: length {} vs {}", got.len(), want.len()));
            return;
        }
        let before = self.failures.len();
        for (i, (g, w)) in got.iter().zip(want).enumerate() {
            self.value(case, &format!("{what}[{i}]"), *g, *w, rtol, atol);
            if self.failures.len() > before + 3 {
                self.failures.push(format!("{case}: {what}: more mismatches not shown"));
                return;
            }
        }
    }

    fn text(&mut self, case: &str, what: &str, got: &str, want: &str) {
        self.compared += 1;
        if got != want {
            self.failures.push(format!("{case}: {what}: got {got:?}, want {want:?}"));
        }
    }
}

fn array(out: &Path, v: &Value) -> Array2 {
    let (shape, data) = read_npy(&out.join(v.as_str().unwrap()));
    Array2::new(shape[0], shape[1], data.iter().map(|x| *x as f32).collect())
}

fn vector(out: &Path, v: &Value) -> Vec<f64> {
    read_npy(&out.join(v.as_str().unwrap())).1
}

#[test]
fn analysis_matches_full_app() {
    let out = out_dir();
    let Ok(text) = std::fs::read_to_string(out.join("analysis.json")) else {
        if std::env::var("CALLISTO_PARITY_REQUIRED").is_ok() {
            panic!("no analysis references; run lite/parity/make_analysis_reference.py");
        }
        eprintln!("no analysis references; run lite/parity/make_analysis_reference.py");
        return;
    };
    let cases: Vec<Value> = serde_json::from_str(&text).unwrap();
    let mut c = Checker { failures: Vec::new(), compared: 0 };
    for case in &cases {
        let name = case["name"].as_str().unwrap();
        match case["kind"].as_str().unwrap() {
            "analyzer" => analyzer(&mut c, name, case),
            "ridge" => ridge(&mut c, name, case, &out),
            "lasso" => lasso(&mut c, name, case, &out),
            "seed" => seed(&mut c, name, case, &out),
            "light_curve" => light_curve(&mut c, name, case, &out),
            "ruler" => ruler(&mut c, name, case),
            "drift" => drift(&mut c, name, case),
            "density" => density_models(&mut c, name, case),
            "type_ii" => type_ii(&mut c, name, case),
            other => c.failures.push(format!("{name}: unknown kind {other}")),
        }
    }
    println!("{} analysis cases, {} values compared", cases.len(), c.compared);
    if !c.failures.is_empty() {
        panic!("{} mismatches:\n{}", c.failures.len(), c.failures.join("\n"));
    }
}

fn analyzer(c: &mut Checker, name: &str, case: &Value) {
    if case.get("error").is_some() {
        return;
    }
    let channels = nums(&case["time_channels"]);
    let seconds = nums(&case["time_seconds"]);
    let freqs = nums(&case["freqs"]);
    let harmonic = case["harmonic"].as_bool().unwrap();
    let time = analysis_time_axis(&resolve_time_seconds(&channels, Some(&seconds)));
    let want_t0 = num(&case["t0"]);
    let t0 = match case["t0_mode"].as_str().unwrap() {
        "burst_onset" => {
            let t0 = burst_onset_t0(&time, &freqs);
            c.value(name, "t0", t0, want_t0, 0.0, 1e-12);
            t0
        }
        "custom" => want_t0,
        _ => 0.0,
    };
    let fit = match analyzer_fit(&time, &freqs, t0, None) {
        Ok(f) => f,
        Err(e) => return c.failures.push(format!("{name}: fit failed: {e}")),
    };
    let f = &case["fit"];
    // The fit stops within SciPy's tolerances, so allow 1e-7 relative.
    c.value(name, "a", fit.a, num(&f["a"]), 1e-7, 1e-12);
    c.value(name, "b", fit.b, num(&f["b"]), 1e-7, 1e-12);
    let errs = nums(&f["std_errs"]);
    c.value(name, "std_err a", fit.std_errs[0], errs[0], 1e-5, 1e-12);
    c.value(name, "std_err b", fit.std_errs[1], errs[1], 1e-5, 1e-12);
    c.value(name, "r2", fit.r2, num(&f["r2"]), 1e-9, 1e-12);
    c.value(name, "rmse", fit.rmse, num(&f["rmse"]), 1e-7, 1e-12);
    c.value(name, "freq_err", fit.freq_err, num(&case["freq_err"]), 1e-7, 1e-12);
    c.values(name, "fit_time", &fit.fit_time, &nums(&case["fit_time"]), 1e-12, 1e-12);
    c.values(name, "time_fit", &fit.time_fit, &nums(&case["time_fit"]), 1e-12, 1e-12);
    c.values(name, "drift_vals", &fit.drift_vals, &nums(&case["drift_vals"]), 1e-6, 1e-12);
    c.values(name, "drift_errs", &fit.drift_errs, &nums(&case["drift_errs"]), 1e-5, 1e-12);
    // The shock parameters from the full app's own a and b: SciPy stops the
    // fit within its 1e-8 tolerance, and the Newkirk pole would magnify that.
    let errs = [errs[0], errs[1]];
    let fit = analyzer_fit(&time, &freqs, t0, Some(([num(&f["a"]), num(&f["b"])], Some(errs)))).unwrap();
    for run in case["runs"].as_array().unwrap() {
        let fold = run["fold"].as_i64().unwrap();
        let model = DensityModel::parse(run["model"].as_str().unwrap());
        let tag = format!("{name} fold {fold} {}", model.key());
        let u = update_shock_parameters(&fit, &freqs, !harmonic, harmonic, fold, model);
        let s = &run["summary"];
        let pairs = [
            ("avg_freq_mhz", u.summary.avg_freq_mhz),
            ("avg_freq_err_mhz", u.summary.avg_freq_err_mhz),
            ("avg_drift_mhz_s", u.summary.avg_drift_mhz_s),
            ("avg_drift_err_mhz_s", u.summary.avg_drift_err_mhz_s),
            ("start_freq_mhz", u.summary.start_freq_mhz),
            ("start_freq_err_mhz", u.summary.start_freq_err_mhz),
            ("initial_shock_speed_km_s", u.summary.initial_shock_speed_km_s),
            ("initial_shock_speed_err_km_s", u.summary.initial_shock_speed_err_km_s),
            ("initial_shock_height_rs", u.summary.initial_shock_height_rs),
            ("initial_shock_height_err_rs", u.summary.initial_shock_height_err_rs),
            ("avg_shock_speed_km_s", u.summary.avg_shock_speed_km_s),
            ("avg_shock_speed_err_km_s", u.summary.avg_shock_speed_err_km_s),
            ("avg_shock_height_rs", u.summary.avg_shock_height_rs),
            ("avg_shock_height_err_rs", u.summary.avg_shock_height_err_rs),
            ("observed_avg_freq_mhz", u.summary.observed_avg_freq_mhz),
            ("observed_avg_drift_mhz_s", u.summary.observed_avg_drift_mhz_s),
            ("observed_start_freq_mhz", u.summary.observed_start_freq_mhz),
        ];
        for (key, got) in pairs {
            c.value(&tag, key, got.unwrap_or(f64::NAN), num(&s[key]), 1e-6, 1e-9);
        }
        c.value(&tag, "harmonic_number", u.summary.harmonic_number as f64, num(&s["harmonic_number"]), 0.0, 0.0);
        for (key, res) in run["comparison"].as_object().unwrap() {
            let m = DensityModel::parse(key);
            let got = &u.comparison[&m];
            c.value(&tag, &format!("{key} start_freq"), got.start_freq_mhz, num(&res["start_freq_mhz"]), 1e-9, 1e-12);
            c.value(&tag, &format!("{key} start_index"), got.start_index as f64, num(&res["start_index"]), 0.0, 0.0);
            for (field, value) in [
                ("initial_shock_speed_km_s", got.initial_shock_speed_km_s),
                ("initial_shock_speed_err_km_s", got.initial_shock_speed_err_km_s),
                ("initial_shock_height_rs", got.initial_shock_height_rs),
                ("initial_shock_height_err_rs", got.initial_shock_height_err_rs),
                ("avg_shock_speed_km_s", got.avg_shock_speed_km_s),
                ("avg_shock_speed_err_km_s", got.avg_shock_speed_err_km_s),
                ("avg_shock_height_rs", got.avg_shock_height_rs),
                ("avg_shock_height_err_rs", got.avg_shock_height_err_rs),
            ] {
                c.value(&tag, &format!("{key} {field}"), value, num(&res[field]), 1e-6, 1e-9);
            }
        }
        c.values(&tag, "shock_speed", &u.shock_speed, &nums(&run["shock_speed"]), 1e-6, 1e-9);
        c.values(&tag, "shock_height", &u.shock_height, &nums(&run["shock_height"]), 1e-7, 1e-12);
        c.values(&tag, "shock_freq", &u.shock_freq_values, &nums(&run["shock_freq"]), 1e-7, 1e-12);
    }
}

fn ridge(c: &mut Checker, name: &str, case: &Value, out: &Path) {
    let data = array(out, &case["data"]);
    let freqs = vector(out, &case["freqs"]);
    for run in case["runs"].as_array().unwrap() {
        let label = run["label"].as_str().unwrap();
        let tag = format!("{name} {label}");
        let seed = run["seed"].as_array().map(|s| (s[0].as_u64().unwrap() as usize, s[1].as_u64().unwrap() as usize));
        let settings = RidgeSettings {
            search_channels: run.get("search_channels").and_then(Value::as_u64).unwrap_or(3) as usize,
            threshold_sigma: run.get("threshold_sigma").map(num).unwrap_or(3.0),
            max_gap: run.get("max_gap").and_then(Value::as_u64).unwrap_or(8) as usize,
            falling_only: run.get("falling").and_then(Value::as_bool).unwrap_or(false),
        };
        match (track_ridge(&data, &freqs, seed, &settings), run.get("error")) {
            (Err(e), Some(want)) => c.text(&tag, "error", &e, want.as_str().unwrap()),
            (Err(e), None) => c.failures.push(format!("{tag}: failed: {e}")),
            (Ok(_), Some(want)) => c.failures.push(format!("{tag}: succeeded, want error {want}")),
            (Ok(r), None) => {
                let idx: Vec<f64> = r.time_indices.iter().map(|v| *v as f64).collect();
                c.values(&tag, "time_indices", &idx, &nums(&run["time_indices"]), 0.0, 0.0);
                c.values(&tag, "freqs", &r.freqs_mhz, &nums(&run["freqs"]), 1e-12, 1e-12);
                c.values(&tag, "peaks", &r.peak_values, &nums(&run["peaks"]), 1e-9, 1e-6);
                c.value(&tag, "threshold", r.threshold, num(&run["threshold"]), 1e-9, 1e-9);
                let s = nums(&run["seed_index"]);
                c.value(&tag, "seed row", r.seed_index.0 as f64, s[0], 0.0, 0.0);
                c.value(&tag, "seed col", r.seed_index.1 as f64, s[1], 0.0, 0.0);
            }
        }
    }
}

fn lasso(c: &mut Checker, name: &str, case: &Value, out: &Path) {
    let data = array(out, &case["data"]);
    let freqs = vector(out, &case["freqs"]);
    let time = vector(out, &case["time"]);
    let step = frequency_step_mhz(&freqs, 1.0);
    let extent = matplotlib_extent(&freqs, &time, Some(step)).unwrap();
    c.values(name, "extent", &extent, &nums(&case["extent"]), 1e-12, 1e-12);
    let verts: Vec<(f64, f64)> = case["verts"].as_array().unwrap().iter().map(|v| (num(&v[0]), num(&v[1]))).collect();
    let mask = lasso_mask(&verts, extent, data.rows, data.cols);
    let got: Vec<f64> = mask.iter().map(|m| *m as i32 as f64).collect();
    c.values(name, "mask", &got, &vector(out, &case["mask"]), 0.0, 0.0);
}

fn seed(c: &mut Checker, name: &str, case: &Value, out: &Path) {
    let data = array(out, &case["data"]);
    let freqs = vector(out, &case["freqs"]);
    let time = vector(out, &case["time"]);
    let Some(s) = analysis_seed(&data, &freqs, Some(&time), case["clean"].as_bool().unwrap()) else {
        return c.failures.push(format!("{name}: no seed"));
    };
    c.values(name, "time_channels", &s.time_channels, &nums(&case["time_channels"]), 0.0, 0.0);
    c.values(name, "time_seconds", &s.time_seconds, &nums(&case["time_seconds"]), 0.0, 0.0);
    c.values(name, "freqs", &s.freqs, &nums(&case["max_freqs"]), 0.0, 0.0);
    c.value(name, "auto_removed", s.auto_removed as f64, num(&case["auto_removed"]), 0.0, 0.0);
}

fn light_curve(c: &mut Checker, name: &str, case: &Value, out: &Path) {
    let data = array(out, &case["data"]);
    let freqs = vector(out, &case["freqs"]);
    let time = vector(out, &case["time"]);
    let settings = LightCurveSettings::default();
    for curve in case["curves"].as_array().unwrap() {
        let requested = num(&curve["requested"]);
        let tag = format!("{name} {requested:.3}");
        let Some((row, actual)) = nearest_frequency(&freqs, requested) else {
            c.failures.push(format!("{tag}: no nearest frequency"));
            continue;
        };
        let series: Vec<f64> = data.row(row).iter().map(|v| *v as f64).collect();
        let index = curve.get("index").and_then(Value::as_u64).unwrap_or(0) as usize;
        match build_overlay(&time, &series, &freqs, requested, row, actual, &settings, index) {
            Err(e) => match curve.get("error") {
                Some(want) => c.text(&tag, "error", &e, want.as_str().unwrap()),
                None => c.failures.push(format!("{tag}: failed: {e}")),
            },
            Ok(o) => {
                c.value(&tag, "row", o.row_index as f64, num(&curve["row"]), 0.0, 0.0);
                c.value(&tag, "frequency", o.frequency_mhz, num(&curve["frequency"]), 0.0, 0.0);
                c.values(&tag, "y", &o.y, &nums(&curve["y"]), 1e-12, 1e-12);
                c.text(&tag, "color", &o.color, curve["color"].as_str().unwrap());
                c.text(&tag, "label", &o.label, curve["label"].as_str().unwrap());
                c.value(&tag, "label_x", o.label_x, num(&curve["label_x"]), 0.0, 0.0);
                c.value(&tag, "label_y", o.label_y, num(&curve["label_y"]), 1e-12, 1e-12);
            }
        }
    }
}

fn ruler(c: &mut Checker, name: &str, case: &Value) {
    for run in case["runs"].as_array().unwrap() {
        let a = nums(&run["a"]);
        let b = nums(&run["b"]);
        match (two_point((a[0], a[1]), (b[0], b[1])), run.get("error")) {
            (Err(e), Some(w)) => c.text(name, "error", &e, w.as_str().unwrap()),
            (Ok(m), None) => {
                c.value(name, "duration", m.duration_s, num(&run["duration"]), 0.0, 0.0);
                c.value(name, "delta", m.frequency_delta_mhz, num(&run["delta"]), 0.0, 0.0);
                c.value(name, "slope", m.slope_mhz_s, num(&run["slope"]), 0.0, 0.0);
            }
            (r, w) => c.failures.push(format!("{name}: {r:?} vs {w:?}")),
        }
    }
}

fn drift(c: &mut Checker, name: &str, case: &Value) {
    for run in case["runs"].as_array().unwrap() {
        let points: Vec<(f64, f64)> =
            run["points"].as_array().unwrap().iter().map(|p| (num(&p[0]), num(&p[1]))).collect();
        match (drift_summary(&points), run.get("avg")) {
            (None, None) => {}
            (Some(s), Some(avg)) => {
                c.value(name, "avg", s.avg_drift_mhz_s, num(avg), 1e-15, 0.0);
                c.value(name, "start", s.start_frequency_mhz, num(&run["start"]), 0.0, 0.0);
                c.value(name, "end", s.end_frequency_mhz, num(&run["end"]), 0.0, 0.0);
                c.value(name, "duration", s.duration_s, num(&run["duration"]), 0.0, 0.0);
            }
            (s, w) => c.failures.push(format!("{name}: {s:?} vs {w:?}")),
        }
    }
}

fn density_models(c: &mut Checker, name: &str, case: &Value) {
    let freqs = nums(&case["freqs"]);
    for (key, v) in case["models"].as_object().unwrap() {
        let (model, fold) = key.split_once('/').unwrap();
        let model = DensityModel::parse(model);
        let fold: f64 = fold.parse().unwrap();
        let tag = format!("{name} {key}");
        let h: Vec<f64> = freqs.iter().map(|&f| density::height_rs(f, model, fold)).collect();
        let g: Vec<f64> = freqs.iter().map(|&f| density::height_gradient(f, model, fold)).collect();
        let s: Vec<f64> = freqs.iter().map(|&f| density::shock_speed_km_s(f, -0.35, model, fold)).collect();
        c.values(&tag, "height", &h, &nums(&v["height"]), 1e-12, 1e-12);
        c.values(&tag, "gradient", &g, &nums(&v["gradient"]), 1e-11, 1e-12);
        c.values(&tag, "speed", &s, &nums(&v["speed"]), 1e-11, 1e-9);
    }
}

fn type_ii(c: &mut Checker, name: &str, case: &Value) {
    let (ut, uf) = (nums(&case["upper_t"]), nums(&case["upper_f"]));
    let (lt, lf) = (nums(&case["lower_t"]), nums(&case["lower_f"]));
    let available = nums(&case["available"]);
    let upper = fit_power_law(&ut, &uf).unwrap();
    let lower = fit_power_law(&lt, &lf).unwrap();
    for (fit, want, tag) in [(&upper, &case["upper_fit"], "upper"), (&lower, &case["lower_fit"], "lower")] {
        c.value(name, &format!("{tag} a"), fit.a, num(&want["a"]), 1e-7, 1e-12);
        c.value(name, &format!("{tag} b"), fit.b, num(&want["b"]), 1e-7, 1e-12);
        c.value(name, &format!("{tag} r2"), fit.r2, num(&want["r2"]), 1e-9, 1e-12);
        c.value(name, &format!("{tag} rmse"), fit.rmse, num(&want["rmse"]), 1e-7, 1e-12);
        let e = nums(&want["std_errs"]);
        c.value(name, &format!("{tag} std a"), fit.std_errs[0], e[0], 1e-5, 1e-12);
        c.value(name, &format!("{tag} std b"), fit.std_errs[1], e[1], 1e-5, 1e-12);
    }
    let (uf_b, lf_b) = (BandFit::from(&upper), BandFit::from(&lower));
    for run in case["runs"].as_array().unwrap() {
        let speed = num(&run["speed"]);
        let start = num(&run["start_freq"]);
        let fold = run["fold"].as_i64().unwrap();
        let tag = format!("{name} v={speed}");
        match calculate_type_ii_parameters((&ut, &uf), (&lt, &lf), uf_b, lf_b, start, speed, Some(&available)) {
            Ok(r) => {
                let p = &run["params"];
                for (key, got) in [
                    ("start_time_s", r.start_time_s),
                    ("end_time_s", r.end_time_s),
                    ("upper_start_freq_mhz", r.upper_start_freq_mhz),
                    ("lower_start_freq_mhz", r.lower_start_freq_mhz),
                    ("avg_upper_freq_mhz", r.avg_upper_freq_mhz),
                    ("avg_lower_freq_mhz", r.avg_lower_freq_mhz),
                    ("bandwidth_mhz", r.bandwidth_mhz),
                    ("upper_avg_drift_mhz_s", r.upper_avg_drift_mhz_s),
                    ("compression_ratio", r.compression_ratio),
                    ("alfven_mach_number", r.alfven_mach_number),
                    ("alfven_speed_km_s", r.alfven_speed_km_s),
                    ("magnetic_field_g", r.magnetic_field_g),
                ] {
                    c.value(&tag, key, got, num(&p[key]), 1e-6, 1e-12);
                }
                c.text(&tag, "warning", &r.warning, p["warning"].as_str().unwrap());
            }
            Err(e) => match run.get("params_error") {
                Some(w) => c.text(&tag, "params error", &e, w.as_str().unwrap()),
                None => c.failures.push(format!("{tag}: params failed: {e}")),
            },
        }
        match calculate_b_vs_r_profile((&ut, &uf), (&lt, &lf), uf_b, lf_b, speed, fold, Some(&available)) {
            Ok(p) => {
                let b = &run["bvr"];
                c.values(&tag, "heights", &p.heights_rs, &nums(&b["heights"]), 1e-7, 1e-12);
                c.values(&tag, "field", &p.magnetic_field_g, &nums(&b["field"]), 1e-6, 1e-12);
                c.value(&tag, "bvr a", p.fit.a, num(&b["fit_a"]), 1e-6, 1e-12);
                c.value(&tag, "bvr b", p.fit.b, num(&b["fit_b"]), 1e-6, 1e-12);
                c.value(&tag, "bvr r2", p.fit.r2, num(&b["fit_r2"]), 1e-9, 1e-12);
            }
            Err(e) => match run.get("bvr_error") {
                Some(w) => c.text(&tag, "bvr error", &e, w.as_str().unwrap()),
                None => c.failures.push(format!("{tag}: bvr failed: {e}")),
            },
        }
    }
}
