//! Lite's results against the full app's, on real files.
//!
//! `lite/parity/make_reference.py` runs the full app's Python functions and
//! writes `lite/parity/out/cases.json` plus `.npy` arrays. This test replays
//! every case with `callisto-core` and compares. All mismatches are listed
//! before the test fails.
//!
//! Without references (no Python environment) the test only prints a note,
//! unless `CALLISTO_PARITY_REQUIRED=1`, which CI sets.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use callisto_core::radio::array::Array2;
use callisto_core::radio::combine::{self, CombineOptions, GapFill, Overlap};
use callisto_core::radio::processing::{self, BackgroundMethod, RfiSettings};
use callisto_core::radio::{archive, fits_io, frequency_axis, timeline};
use serde_json::Value;

fn parity_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity")
}

/// Reads a little-endian float64 `.npy` file written by `np.save`.
fn read_npy(path: &Path) -> (Vec<usize>, Vec<f64>) {
    let bytes = std::fs::read(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    assert_eq!(&bytes[..6], b"\x93NUMPY", "{} is not a .npy file", path.display());
    let (header_len, header_start) = match bytes[6] {
        1 => (u16::from_le_bytes([bytes[8], bytes[9]]) as usize, 10),
        _ => (u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize, 12),
    };
    let header = std::str::from_utf8(&bytes[header_start..header_start + header_len]).unwrap();
    assert!(header.contains("'descr': '<f8'"), "expected float64 data in {}", path.display());
    let shape_text = header.split("'shape': (").nth(1).unwrap().split(')').next().unwrap();
    let shape: Vec<usize> = shape_text.split(',').filter_map(|s| s.trim().parse().ok()).collect();
    let data = bytes[header_start + header_len..].as_chunks::<8>().0.iter().map(|c| f64::from_le_bytes(*c)).collect();
    (shape, data)
}

struct Checker {
    out: PathBuf,
    failures: Vec<String>,
    checked: BTreeMap<String, usize>,
    inputs: BTreeMap<String, Array2>,
}

impl Checker {
    fn npy(&self, v: &Value) -> (Vec<usize>, Vec<f64>) {
        read_npy(&self.out.join(v.as_str().unwrap()))
    }

    fn fail(&mut self, case: &str, msg: String) {
        self.failures.push(format!("{case}: {msg}"));
    }

    /// Arrays equal within `atol + rtol * |expected|`, NaN where NaN.
    fn close(&mut self, case: &str, what: &str, got: &[f64], want: &[f64], atol: f64, rtol: f64) {
        if got.len() != want.len() {
            return self.fail(case, format!("{what}: length {} vs {}", got.len(), want.len()));
        }
        let mut worst: Option<(usize, f64, f64)> = None;
        let mut bad = 0;
        for (i, (g, w)) in got.iter().zip(want).enumerate() {
            let ok = if w.is_nan() || g.is_nan() {
                w.is_nan() && g.is_nan()
            } else if w.is_infinite() || g.is_infinite() {
                g == w
            } else {
                (g - w).abs() <= atol + rtol * w.abs()
            };
            if !ok {
                bad += 1;
                let err = (g - w).abs();
                if worst.is_none_or(|(_, e, _)| err > e || err.is_nan()) {
                    worst = Some((i, err, *w));
                }
            }
        }
        if let Some((i, err, w)) = worst {
            self.fail(
                case,
                format!(
                    "{what}: {bad} of {} values differ; worst at {i}: got {} want {w} (|d|={err:e})",
                    want.len(),
                    got[i]
                ),
            );
        }
    }

    fn eq<T: PartialEq + std::fmt::Debug>(&mut self, case: &str, what: &str, got: T, want: T) {
        if got != want {
            self.fail(case, format!("{what}: got {got:?}, want {want:?}"));
        }
    }
}

fn f64s(v: &Value) -> Vec<f64> {
    v.as_array().map(|a| a.iter().map(|x| x.as_f64().unwrap_or(f64::NAN)).collect()).unwrap_or_default()
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().map(|x| x.as_str().unwrap().to_string()).collect()).unwrap_or_default()
}

fn as_f64(a: &Array2) -> Vec<f64> {
    a.data.iter().map(|&v| v as f64).collect()
}

fn fixture(name: &str) -> String {
    parity_dir().join("fixtures").join(name).to_string_lossy().into_owned()
}

fn options(v: &Value) -> CombineOptions {
    CombineOptions {
        gap_fill: v["gap_fill"].as_str().map(|s| GapFill::parse(s).unwrap()).unwrap_or_default(),
        overlap_policy: v["overlap_policy"].as_str().map(|s| Overlap::parse(s).unwrap()).unwrap_or_default(),
        overlap_connection_mhz: v["overlap_connection_mhz"].as_f64(),
    }
}

fn base(p: &str) -> String {
    Path::new(p).file_name().unwrap().to_string_lossy().into_owned()
}

fn run_case(c: &mut Checker, case: &Value) {
    let name = case["name"].as_str().unwrap().to_string();
    let kind = case["kind"].as_str().unwrap().to_string();
    *c.checked.entry(kind.clone()).or_default() += 1;
    let n = name.as_str();
    match kind.as_str() {
        "load" => {
            let path = fixture(case["file"].as_str().unwrap());
            let r = fits_io::load_callisto_fits(Path::new(&path)).unwrap();
            let (shape, want) = c.npy(&case["data"]);
            c.eq(n, "shape", (r.data.rows, r.data.cols), (shape[0], shape[1]));
            c.close(n, "data", &as_f64(&r.data), &want, 0.0, 0.0);
            c.close(n, "freqs", &r.freqs, &f64s(&case["freqs"]), 1e-12, 0.0);
            c.close(n, "time", &r.time, &f64s(&case["time"]), 1e-12, 0.0);
            c.eq(n, "ut_start_sec", fits_io::extract_ut_start_sec(&r.header0), case["ut_start_sec"].as_f64());
            let p = fits_io::preview_callisto_fits(Path::new(&path)).unwrap();
            let ps = f64s(&case["preview_shape"]);
            c.eq(n, "preview shape", p.data_shape, (ps[0] as usize, ps[1] as usize));
            c.close(n, "preview freqs", &p.freqs, &f64s(&case["preview_freqs"]), 1e-12, 0.0);
            c.close(n, "preview time", &p.time, &f64s(&case["preview_time"]), 1e-12, 0.0);
            c.eq(n, "freq source", p.freq_source, case["freq_source"].as_str().unwrap());
            c.eq(n, "time source", p.time_source, case["time_source"].as_str().unwrap());
        }
        "inspect" => {
            let paths: Vec<String> = strings(&case["paths"]).iter().map(|p| fixture(p)).collect();
            let ins = combine::inspect_combination(&paths);
            c.eq(n, "valid", ins.valid, case["valid"].as_bool().unwrap());
            c.eq(
                n,
                "combine type",
                ins.combine_type.map(|t| t.key().to_string()),
                case["combine_type"].as_str().map(String::from),
            );
            c.eq(n, "error", ins.error.clone(), case["error"].as_str().map(String::from));
            c.eq(n, "focus codes", ins.focus_codes.clone(), strings(&case["focus_codes"]));
            let groups: Vec<Vec<String>> =
                ins.timestamp_groups.iter().map(|g| g.iter().map(|p| base(p)).collect()).collect();
            let want_groups: Vec<Vec<String>> = case["groups"].as_array().unwrap().iter().map(strings).collect();
            c.eq(n, "groups", groups, want_groups);
            let rel = ins.frequency_relation.clone().unwrap_or_default();
            let gaps: Vec<f64> = rel.gaps.iter().flat_map(|g| [g.low, g.high]).collect();
            let overlaps: Vec<f64> = rel.overlaps.iter().flat_map(|g| [g.low, g.high]).collect();
            let flat = |v: &Value| v.as_array().unwrap().iter().flat_map(f64s).collect::<Vec<_>>();
            c.close(n, "gaps", &gaps, &flat(&case["gaps"]), 1e-9, 0.0);
            c.close(n, "overlaps", &overlaps, &flat(&case["overlaps"]), 1e-9, 0.0);
        }
        "combine" => {
            let paths: Vec<String> = strings(&case["paths"]).iter().map(|p| fixture(p)).collect();
            let o = options(&case["options"]);
            let result = if n.starts_with("combine time x") {
                combine::combine_time_frequency(&paths, o)
            } else if n.starts_with("combine time") {
                combine::combine_time(&paths)
            } else {
                combine::combine_frequency(&paths, o)
            };
            match (result, case["error"].as_str()) {
                (Err(e), Some(want)) => c.eq(n, "error", e.to_string(), want.to_string()),
                (Err(e), None) => c.fail(n, format!("failed: {e:#}")),
                (Ok(_), Some(want)) => c.fail(n, format!("succeeded, but the full app failed with: {want}")),
                (Ok(r), None) => {
                    let (shape, want) = c.npy(&case["data"]);
                    c.eq(n, "shape", (r.data.rows, r.data.cols), (shape[0], shape[1]));
                    // Values are copied (exact) or interpolated in f64 and stored as f32.
                    c.close(n, "data", &as_f64(&r.data), &want, 1e-4, 1e-6);
                    c.close(n, "freqs", &r.freqs, &f64s(&case["freqs"]), 1e-9, 0.0);
                    let (_, want_time) = c.npy(&case["time_npy"]);
                    c.close(n, "time", &r.time, &want_time, 1e-9, 0.0);
                    c.eq(n, "filename", r.filename.clone(), case["filename"].as_str().unwrap().to_string());
                    c.eq(
                        n,
                        "sources",
                        r.sources.iter().map(|s| base(s)).collect::<Vec<_>>(),
                        strings(&case["sources"]),
                    );
                    c.eq(n, "ut start", r.ut_start_sec, case["ut_start_sec"].as_f64());
                    let want_mask: Option<Vec<bool>> =
                        case["gap_row_mask"].as_array().map(|a| a.iter().map(|v| v.as_bool().unwrap()).collect());
                    c.eq(n, "gap mask", r.gap_row_mask.clone(), want_mask);
                    if let Some(step) = case["frequency_step_mhz"].as_f64() {
                        c.close(n, "step", &[r.frequency_step_mhz.unwrap_or(f64::NAN)], &[step], 1e-12, 0.0);
                    }
                    for (key, want) in case["header"].as_object().unwrap() {
                        match want {
                            Value::Bool(b) => c.eq(n, key, r.header0.get_bool(key), Some(*b)),
                            Value::String(s) => c.eq(n, key, r.header0.get_str(key), Some(s.clone())),
                            Value::Number(x) => {
                                let got = r.header0.get_f64(key).unwrap_or(f64::NAN);
                                c.close(n, key, &[got], &[x.as_f64().unwrap()], 1e-9, 0.0)
                            }
                            _ => {}
                        }
                    }
                    let history: Vec<String> =
                        r.header0.cards.iter().filter(|c| c.key == "HISTORY").map(|c| c.comment.clone()).collect();
                    c.eq(n, "history", history, strings(&case["history"]));
                }
            }
        }
        "input" => {
            let (shape, data) = c.npy(&case["data"]);
            let label = n.trim_start_matches("input ").to_string();
            c.inputs.insert(label, Array2::new(shape[0], shape[1], data.iter().map(|&v| v as f32).collect()));
        }
        "background" => {
            let input = c.inputs[case["input"].as_str().unwrap()].clone();
            let method = BackgroundMethod::normalize(case["method"].as_str().unwrap(), true).unwrap();
            let out = processing::subtract_background(
                &input,
                method,
                None,
                case["equalize"].as_bool().unwrap(),
                case["db_scale"].as_f64().unwrap(),
            );
            let (_, want) = c.npy(&case["out"]);
            c.close(n, "out", &as_f64(&out), &want, 2e-5, 1e-5);
        }
        "quantiles" => {
            let input = c.inputs[case["input"].as_str().unwrap()].clone();
            let q = processing::row_quantiles(&input, &f64s(&case["qs"]));
            let got: Vec<f64> = q.iter().flatten().map(|&v| v as f64).collect();
            let (_, want) = c.npy(&case["out"]);
            c.close(n, "quantiles", &got, &want, 0.0, 0.0);
        }
        "noise_scale" => {
            let input = c.inputs[case["input"].as_str().unwrap()].clone();
            let got: Vec<f64> = processing::rowwise_noise_scale(&input, None).iter().map(|&v| v as f64).collect();
            let (_, want) = c.npy(&case["out"]);
            c.close(n, "noise scale", &got, &want, 0.0, 1e-6);
        }
        "rfi" => {
            let input = c.inputs[case["input"].as_str().unwrap()].clone();
            let s = RfiSettings {
                kernel_time: case["kernel_time"].as_u64().unwrap() as usize,
                kernel_freq: case["kernel_freq"].as_u64().unwrap() as usize,
                ..Default::default()
            };
            let r = processing::clean_rfi(&input, s);
            let (_, want) = c.npy(&case["out"]);
            c.close(n, "data", &as_f64(&r.data), &want, 0.0, 1e-6);
            let masked: Vec<u64> = case["masked"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap()).collect();
            c.eq(n, "masked", r.masked_channel_indices.iter().map(|&i| i as u64).collect::<Vec<_>>(), masked);
        }
        "db" => {
            let input = c.inputs[case["input"].as_str().unwrap()].clone();
            let out = processing::convert_digits_to_db(
                &input,
                case["cold"].as_f64().unwrap(),
                case["scale"].as_f64().unwrap(),
            );
            let (_, want) = c.npy(&case["out"]);
            c.close(n, "out", &as_f64(&out), &want, 0.0, 1e-6);
        }
        "limits" => {
            let input = c.inputs[case["input"].as_str().unwrap()].clone();
            let p = frequency_axis::percentile_data_limits(&input.data, 5.0, 98.0).unwrap();
            c.close(n, "percentile", &[p.0, p.1], &f64s(&case["percentile"]), 0.0, 1e-12);
            let f = frequency_axis::finite_data_limits(&input.data).unwrap();
            c.close(n, "finite", &[f.0, f.1], &f64s(&case["finite"]), 0.0, 1e-12);
        }
        "log_ticks" => {
            let (labels, minor) =
                frequency_axis::log_frequency_ticks_mhz(case["lo"].as_f64().unwrap(), case["hi"].as_f64().unwrap(), 10);
            c.close(n, "labels", &labels, &f64s(&case["labels"]), 1e-12, 1e-12);
            c.close(n, "minor", &minor, &f64s(&case["minor"]), 1e-12, 1e-12);
        }
        "format_mhz" => c.eq(
            n,
            "text",
            frequency_axis::format_frequency_mhz(case["value"].as_f64().unwrap()),
            case["text"].as_str().unwrap().to_string(),
        ),
        "log_resample" => {
            let r = fits_io::load_callisto_fits(Path::new(&fixture(case["input_file"].as_str().unwrap()))).unwrap();
            let step = frequency_axis::frequency_step_mhz(&r.freqs, 1.0);
            c.close(n, "step", &[step], &[case["step"].as_f64().unwrap()], 0.0, 1e-12);
            let edges = frequency_axis::frequency_edges(&r.freqs, step);
            c.close(n, "edges", &edges, &f64s(&case["edges"]), 1e-12, 0.0);
            let b = f64s(&case["bounds"]);
            let (out, rows) =
                frequency_axis::resample_rows_to_log_frequency(&r.data, &r.freqs, r.data.rows, Some((b[0], b[1])))
                    .unwrap();
            c.close(n, "log rows", &rows, &f64s(&case["log_rows"]), 1e-12, 0.0);
            let (_, want) = c.npy(&case["out"]);
            c.close(n, "data", &as_f64(&out), &want, 1e-4, 1e-6);
        }
        "timeline" => {
            let paths: Vec<String> = strings(&case["paths"]).iter().map(|p| fixture(p)).collect();
            let s = timeline::describe_timeline(&paths).unwrap();
            c.eq(n, "station", s.station.clone(), case["station"].as_str().unwrap().to_string());
            c.eq(n, "focus codes", s.focus_codes.clone(), strings(&case["focus_codes"]));
            c.eq(n, "step", s.step_seconds, case["step"].as_f64().unwrap());
            let segs: Vec<Vec<String>> = s.segments.iter().map(|g| g.paths.iter().map(|p| base(p)).collect()).collect();
            let want: Vec<Vec<String>> = case["segments"].as_array().unwrap().iter().map(strings).collect();
            c.eq(n, "segments", segs, want);
            let iso = |t: chrono::NaiveDateTime| t.format("%Y-%m-%dT%H:%M:%S").to_string();
            c.eq(
                n,
                "next",
                iso(timeline::target_timestamp(&s, timeline::Direction::Next)),
                case["next"].as_str().unwrap().to_string(),
            );
            c.eq(
                n,
                "previous",
                iso(timeline::target_timestamp(&s, timeline::Direction::Previous)),
                case["previous"].as_str().unwrap().to_string(),
            );
            let (trimmed, shift) = timeline::trimmed_paths(&s, true).unwrap();
            c.eq(n, "trimmed", trimmed.iter().map(|p| base(p)).collect::<Vec<_>>(), strings(&case["trimmed_start"]));
            c.eq(n, "shift", shift, case["shift"].as_f64().unwrap());
        }
        "links" => {
            let html = std::fs::read_to_string(parity_dir().join(case["html"].as_str().unwrap())).unwrap();
            let links = archive::extract_fits_links(&html);
            c.eq(n, "links", links.clone(), strings(&case["links"]));
            c.eq(n, "stations", archive::stations_in_listing(&links), strings(&case["stations"]));
        }
        other => c.fail(n, format!("unknown case kind {other}")),
    }
}

#[test]
fn results_match_full_app() {
    let out = parity_dir().join("out");
    let Ok(text) = std::fs::read_to_string(out.join("cases.json")) else {
        let msg = format!("no parity references in {}; run lite/parity/make_reference.py", out.display());
        if std::env::var("CALLISTO_PARITY_REQUIRED").is_ok_and(|v| v == "1") {
            panic!("{msg}");
        }
        eprintln!("skipped: {msg}");
        return;
    };
    let cases: Vec<Value> = serde_json::from_str(&text).unwrap();
    let mut c = Checker { out, failures: Vec::new(), checked: BTreeMap::new(), inputs: BTreeMap::new() };
    for case in &cases {
        run_case(&mut c, case);
    }
    let summary: Vec<String> = c.checked.iter().map(|(k, v)| format!("{k} {v}")).collect();
    println!("checked {} cases: {}", cases.len(), summary.join(", "));
    for f in &c.failures {
        println!("MISMATCH {f}");
    }
    assert!(c.failures.is_empty(), "{} mismatches with the full app (listed above)", c.failures.len());
}
