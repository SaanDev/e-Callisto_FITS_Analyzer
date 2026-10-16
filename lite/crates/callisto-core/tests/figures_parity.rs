//! The downloader figures laid out as matplotlib lays them out.
//!
//! `lite/parity/make_figure_reference.py` draws the full app's FITS preview
//! and spectral overview with matplotlib and records every axes rectangle and
//! every text's window extent, plus the renderer's text metrics. Here the
//! same figures are laid out by `callisto_core::mpl` with those metrics, so
//! any difference is in the layout, not in the fonts.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use callisto_core::mpl::figures::{QtColors, overview_figure, preview_figure};
use callisto_core::mpl::{Prim, PxRect, TextExtent, TextMeasure};
use callisto_core::radio::fits_io::load_callisto_fits;
use callisto_core::radio::naming::parse_archive_filename;
use callisto_core::radio::spectral_overview::{DEFAULT_PANEL_RENDER_COLUMNS, OverviewSource, build_spectral_overview};
use serde_json::Value;

fn parity_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity")
}

/// matplotlib's own text metrics, by text and size.
struct Recorded(HashMap<String, (f64, f64, f64)>);

impl TextMeasure for Recorded {
    fn measure(&self, text: &str, size_px: f64) -> TextExtent {
        let pt = (size_px * 72.0 / 100.0 * 1000.0).round() / 1000.0;
        let key = format!("{text}|{pt:?}");
        let (w, h, d) = *self.0.get(&key).unwrap_or_else(|| panic!("no metrics for {key:?}"));
        TextExtent { width: w, height: h, descent: d }
    }
}

fn rect(v: &Value) -> [f64; 4] {
    let a = v.as_array().unwrap();
    [a[0].as_f64().unwrap(), a[1].as_f64().unwrap(), a[2].as_f64().unwrap(), a[3].as_f64().unwrap()]
}

fn close(a: [f64; 4], b: &PxRect, tol: f64) -> bool {
    (a[0] - b.x0).abs() <= tol && (a[1] - b.y0).abs() <= tol && (a[2] - b.x1).abs() <= tol && (a[3] - b.y1).abs() <= tol
}

fn colors(v: &Value) -> QtColors {
    let c = |k: &str| {
        let s = v[k].as_str().unwrap().trim_start_matches('#');
        let n = u32::from_str_radix(s, 16).unwrap();
        [(n >> 16) as u8, (n >> 8) as u8, n as u8, 255]
    };
    QtColors { window: c("window"), base: c("base"), text: c("text"), mid: c("mid") }
}

#[test]
fn figures_match_matplotlib() {
    let Ok(text) = std::fs::read_to_string(parity_dir().join("out/figures.json")) else {
        assert!(std::env::var_os("CALLISTO_PARITY_REQUIRED").is_none(), "figures.json missing");
        eprintln!("note: no figure references; run lite/parity/make_figure_reference.py");
        return;
    };
    let cases: Vec<Value> = serde_json::from_str(&text).unwrap();
    let fixture = |n: &str| parity_dir().join("fixtures").join(n);
    let mut failures = Vec::new();
    for case in &cases {
        let name = case["name"].as_str().unwrap();
        let (w, h) = (case["width"].as_f64().unwrap(), case["height"].as_f64().unwrap());
        let fig = match case["kind"].as_str().unwrap() {
            "preview" => {
                let r = load_callisto_fits(&fixture(case["file"].as_str().unwrap())).unwrap();
                preview_figure(&r.data, &r.freqs, &r.time, w, h, colors(&case["colors"]))
            }
            _ => {
                let sources: Vec<OverviewSource> = case["files"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|f| {
                        let f = f.as_str().unwrap();
                        let (station, observed_at, focus_code) = parse_archive_filename(f).unwrap();
                        OverviewSource {
                            path: fixture(f).to_string_lossy().into_owned(),
                            station,
                            observed_at,
                            focus_code,
                            filename: f.to_owned(),
                        }
                    })
                    .collect();
                let result = build_spectral_overview(&sources, DEFAULT_PANEL_RENDER_COLUMNS, &|| false, &mut |_| {}).unwrap();
                overview_figure(&result, w, h)
            }
        };
        let metrics: HashMap<String, (f64, f64, f64)> = case["metrics"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), (v[0].as_f64().unwrap(), v[1].as_f64().unwrap(), v[2].as_f64().unwrap())))
            .collect();
        let list = fig.layout(&Recorded(metrics));
        for (i, ax) in case["axes"].as_array().unwrap().iter().enumerate() {
            if !close(rect(&ax["rect"]), &list.axes_rects[i], 0.01) {
                failures.push(format!("{name}: axes {i}: {:?} vs {:?}", rect(&ax["rect"]), list.axes_rects[i]));
            }
        }
        let ours: Vec<(&str, f64, &PxRect)> = list
            .prims
            .iter()
            .filter_map(|p| match p {
                Prim::Text { text, rotation, bbox, .. } => Some((text.as_str(), *rotation, bbox)),
                _ => None,
            })
            .collect();
        let refs = case["texts"].as_array().unwrap();
        if ours.len() != refs.len() {
            failures.push(format!("{name}: {} texts vs matplotlib's {}", ours.len(), refs.len()));
        }
        for t in refs {
            let (s, rot, want) = (t["text"].as_str().unwrap(), t["rotation"].as_f64().unwrap(), rect(&t["bbox"]));
            let found = ours.iter().filter(|o| o.0 == s && (o.1 - rot).abs() < 1e-9).min_by(|a, b| {
                let d = |r: &PxRect| (r.x0 - want[0]).abs() + (r.y0 - want[1]).abs();
                d(a.2).total_cmp(&d(b.2))
            });
            match found {
                Some(o) if close(want, o.2, 0.01) => {}
                Some(o) => failures.push(format!("{name}: {s:?} at {:?}, matplotlib {want:?}", o.2)),
                None => failures.push(format!("{name}: {s:?} missing")),
            }
        }
    }
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}
