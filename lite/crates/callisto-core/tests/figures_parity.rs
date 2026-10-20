//! The downloader and analysis figures laid out as matplotlib lays them out.
//!
//! `lite/parity/make_figure_reference.py` draws the full app's FITS preview,
//! spectral overview and analysis graphs (the Maximum Intensities scatter,
//! the Analyzer's maxima and OriginPro best fit) with matplotlib and records
//! every axes rectangle and view, every text's window extent, the legend's
//! frame and handles and mathtext's glyph layout, plus the renderer's text
//! and glyph metrics. Here the same figures are laid out by
//! `callisto_core::mpl` with those metrics, so any difference is in the
//! layout, not in the fonts.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use callisto_core::mpl::analysis_figures::{BLUE, FitGraph, GraphText, analyzer_scatter_figure, fit_figure, max_intensity_figure, power_law_label};
use callisto_core::mpl::figures::{QtColors, batch_figure, overview_figure, preview_figure};
use callisto_core::radio::batch::{OutputMode, batch_figure_spec};
use callisto_core::radio::processing::BackgroundMethod;
use callisto_core::view_config::ViewConfig;
use callisto_core::mpl::figures::{GridOptions, comparison_grid_figure};
use callisto_core::radio::comparison::{Alignment, ColorScale, PanelOptions, datasets_from_paths, panel_payloads};
use callisto_core::mpl::mathtext::{self, GlyphMetrics};
use callisto_core::mpl::{Face, Prim, PxRect, TextExtent, TextMeasure};
use callisto_core::radio::fits_io::load_callisto_fits;
use callisto_core::radio::naming::parse_archive_filename;
use callisto_core::radio::spectral_overview::{DEFAULT_PANEL_RENDER_COLUMNS, OverviewSource, build_spectral_overview};
use serde_json::Value;

fn parity_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity")
}

/// matplotlib's own text metrics, by text and size in thousandths of a
/// point; mathtext's glyph metrics and kerning by character and size.
#[derive(Default)]
struct Recorded {
    text: HashMap<(String, i64), (f64, f64, f64)>,
    glyphs: HashMap<String, GlyphMetrics>,
    kerns: HashMap<String, f64>,
    x_height: HashMap<i64, f64>,
}

fn milli(v: f64) -> i64 {
    (v * 1000.0).round() as i64
}

impl Recorded {
    fn new(case: &Value) -> Self {
        let mut r = Recorded::default();
        for (k, v) in case["metrics"].as_object().unwrap() {
            let (text, size) = k.rsplit_once('|').unwrap();
            let size: f64 = size.parse().unwrap();
            r.text.insert((text.to_owned(), milli(size)), (v[0].as_f64().unwrap(), v[1].as_f64().unwrap(), v[2].as_f64().unwrap()));
        }
        for ax in case["analysis"].as_array().into_iter().flatten() {
            for m in ax["legend"]["math"].as_array().into_iter().flatten() {
                for (k, v) in m["glyph_metrics"].as_object().unwrap() {
                    let f = |i: usize| v[i].as_f64().unwrap();
                    let g = GlyphMetrics { advance: f(0), width: f(1), height: f(2), xmin: f(3), xmax: f(4), ymin: f(5), ymax: f(6), iceberg: f(7) };
                    r.glyphs.insert(k.clone(), g);
                }
                for (k, v) in m["kerns"].as_object().unwrap() {
                    r.kerns.insert(k.clone(), v.as_f64().unwrap());
                }
                r.x_height.insert(milli(m["size_px"].as_f64().unwrap()), m["x_height"].as_f64().unwrap());
            }
        }
        r
    }
}

impl TextMeasure for Recorded {
    fn measure(&self, text: &str, size_px: f64, _face: Face) -> TextExtent {
        let key = (text.to_owned(), milli(size_px * 72.0 / 100.0));
        let (w, h, d) = *self.text.get(&key).unwrap_or_else(|| panic!("no metrics for {key:?}"));
        TextExtent { width: w, height: h, descent: d }
    }

    fn glyph(&self, c: char, size_px: f64, _face: Face) -> Option<GlyphMetrics> {
        let key = format!("{c}|{size_px:.6}");
        Some(*self.glyphs.get(&key).unwrap_or_else(|| panic!("no glyph metrics for {key:?}")))
    }

    fn kerning(&self, a: char, b: char, size_px: f64, _face: Face) -> f64 {
        self.kerns.get(&format!("{a}{b}|{size_px:.6}")).copied().unwrap_or(0.0)
    }

    fn x_height(&self, size_px: f64, _face: Face) -> f64 {
        *self.x_height.get(&milli(size_px)).expect("no x-height")
    }
}

fn floats(v: &Value) -> Vec<f64> {
    v.as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect()
}

fn point(v: &Value) -> (f64, f64) {
    (v[0].as_f64().unwrap(), v[1].as_f64().unwrap())
}

/// The analysis figures as the windows build them, from the recorded data.
fn analysis_figure(case: &Value, w: f64, h: f64) -> callisto_core::mpl::Figure {
    let (x, y) = (floats(&case["x"]), floats(&case["y"]));
    let stem = "INDIA-GAURI_20261007_041500_02";
    match case["name"].as_str().unwrap() {
        "max scatter" => max_intensity_figure(&x, &y, "Maximum Intensity for Each Time Channel", "Time (s)", None, w, h),
        "analyzer max" => {
            analyzer_scatter_figure(&x, &y, BLUE, &format!("{stem}_Maximum_Intensity"), "Time (s)", "Frequency (MHz)", false, w, h)
        }
        _ => {
            let (a, b) = (case["a"].as_f64().unwrap(), case["b"].as_f64().unwrap());
            let pos: Vec<f64> = x.iter().zip(&y).filter(|(t, f)| **t > 0.0 && **f > 0.0).map(|(t, _)| *t).collect();
            let (lo, hi) = (pos.iter().copied().fold(f64::INFINITY, f64::min), pos.iter().copied().fold(f64::NEG_INFINITY, f64::max));
            let fit_x: Vec<f64> = (0..400).map(|i| lo + (hi - lo) * i as f64 / 399.0).collect();
            let fit_y: Vec<f64> = fit_x.iter().map(|t| a * t.powf(-b)).collect();
            let label = power_law_label(a, b, "Best Fit", "x");
            let g = FitGraph {
                x: &x,
                y: &y,
                fit_x: &fit_x,
                fit_y: &fit_y,
                data_label: "Original Data",
                fit_label: &label,
                title: &format!("{stem}_Best_Fit"),
                x_label: "Time (s)",
                y_label: "Frequency (MHz)",
            };
            fit_figure(&g, GraphText::default(), w, h)
        }
    }
}

/// The analysis-specific checks: views, data positions, the legend's
/// frame and handles, mathtext's glyphs.
fn check_analysis(name: &str, case: &Value, fig: &callisto_core::mpl::Figure, list: &callisto_core::mpl::DrawList, m: &Recorded, failures: &mut Vec<String>) {
    let near = |a: f64, b: f64, tol: f64| (a - b).abs() <= tol * (1.0 + b.abs());
    let ax = &case["axes"][0];
    let (xl, yl) = (floats(&ax["xlim"]), floats(&ax["ylim"]));
    let ours = &fig.axes[0];
    if !(near(ours.xlim.0, xl[0], 1e-9) && near(ours.xlim.1, xl[1], 1e-9) && near(ours.ylim.0, yl[0], 1e-9) && near(ours.ylim.1, yl[1], 1e-9)) {
        failures.push(format!("{name}: view {:?} {:?}, matplotlib {xl:?} {yl:?}", ours.xlim, ours.ylim));
    }
    let extra = &case["analysis"][0];
    let markers: Vec<&Prim> = list.prims.iter().filter(|p| matches!(p, Prim::Markers { .. } | Prim::Path { .. })).collect();
    // Data positions: the first and last of each collection, then each line.
    let refs: Vec<&Value> = extra["collections"].as_array().unwrap().iter().chain(extra["lines"].as_array().unwrap()).collect();
    let mut data = markers.iter().filter(|p| match p {
        Prim::Markers { clip, .. } | Prim::Path { clip, .. } => clip.is_some(),
        _ => false,
    });
    let mut seen_line_marker = false;
    for r in refs {
        let want = (point(&r["first"][0]), point(&r["last"][0]));
        // A marker-only line is one Markers primitive; a plain line one Path.
        let Some(p) = data.next() else {
            failures.push(format!("{name}: missing data primitive"));
            break;
        };
        let pts = match p {
            Prim::Markers { centers, .. } => centers.clone(),
            Prim::Path { points, .. } => points.clone(),
            _ => unreachable!(),
        };
        seen_line_marker |= matches!(p, Prim::Markers { snapped: true, .. });
        let got = (pts[0], pts[pts.len() - 1]);
        let close2 = |a: (f64, f64), b: (f64, f64)| (a.0 - b.0).abs() < 1e-6 && (a.1 - b.1).abs() < 1e-6;
        if pts.len() != r["n"].as_u64().unwrap() as usize || !close2(got.0, want.0) || !close2(got.1, want.1) {
            failures.push(format!("{name}: data {} points {got:?}, matplotlib {} {want:?}", pts.len(), r["n"]));
        }
    }
    let _ = seen_line_marker;
    let Some(leg) = extra["legend"].as_object() else { return };
    let frame = list.prims.iter().find_map(|p| if let Prim::Frame { rect, .. } = p { Some(rect.clone()) } else { None });
    match frame {
        Some(f) if close(rect(&leg["frame"]), &f, 0.01) => {}
        f => failures.push(format!("{name}: legend frame {f:?}, matplotlib {:?}", rect(&leg["frame"]))),
    }
    // Handles: the legend's (unclipped) markers and lines, by handle.
    let handle_prims: Vec<&Prim> = markers
        .iter()
        .copied()
        .filter(|p| matches!(p, Prim::Markers { clip: None, .. } | Prim::Path { clip: None, .. }))
        .collect();
    for (i, h) in leg["handles"].as_array().unwrap().iter().enumerate() {
        let pts: Vec<(f64, f64)> = h["points"].as_array().unwrap().iter().map(point).collect();
        let Some(p) = handle_prims.get(i) else {
            failures.push(format!("{name}: legend handle {i} missing"));
            continue;
        };
        let ok = match p {
            Prim::Markers { centers, .. } => (centers[0].0 - pts[1].0).abs() < 1e-6 && (centers[0].1 - pts[1].1).abs() < 1e-6,
            Prim::Path { points, .. } => points.iter().zip(&pts).all(|(a, b)| (a.0 - b.0).abs() < 1e-6 && (a.1 - b.1).abs() < 1e-6),
            _ => false,
        };
        if !ok {
            failures.push(format!("{name}: legend handle {i} differs from {pts:?}"));
        }
    }
    for mt in leg["math"].as_array().unwrap() {
        let text = mt["text"].as_str().unwrap();
        let l = mathtext::layout(text, mt["size_px"].as_f64().unwrap(), Face::Origin, m);
        let want: Vec<(String, f64, f64)> = mt["glyphs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|g| (g[0].as_str().unwrap().to_owned(), g[2].as_f64().unwrap(), g[3].as_f64().unwrap()))
            .collect();
        let got: Vec<(String, f64, f64)> = l.glyphs.iter().map(|g| (g.c.to_string(), g.x, g.y)).collect();
        let same = want.len() == got.len() && want.iter().zip(&got).all(|(a, b)| a.0 == b.0 && (a.1 - b.1).abs() < 1e-6 && (a.2 - b.2).abs() < 1e-6);
        if !same {
            failures.push(format!("{name}: mathtext glyphs {got:?}
  matplotlib {want:?}"));
        }
        let agg = floats(&mt["agg"]);
        if !((l.width - agg[0]).abs() < 1e-6 && (l.height - agg[1]).abs() < 1e-6 && (l.descent - agg[2]).abs() < 1e-6) {
            failures.push(format!("{name}: mathtext extent {} {} {}, matplotlib {agg:?}", l.width, l.height, l.descent));
        }
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
    let mut analysis_checked = 0;
    for case in &cases {
        let name = case["name"].as_str().unwrap();
        let (w, h) = (case["width"].as_f64().unwrap(), case["height"].as_f64().unwrap());
        let fig = match case["kind"].as_str().unwrap() {
            "preview" => {
                let r = load_callisto_fits(&fixture(case["file"].as_str().unwrap())).unwrap();
                preview_figure(&r.data, &r.freqs, &r.time, w, h, colors(&case["colors"]))
            }
            "analysis" => analysis_figure(case, w, h),
            "comparison grid" => {
                let files: Vec<String> = case["files"].as_array().unwrap().iter().map(|f| fixture(f.as_str().unwrap()).to_string_lossy().into_owned()).collect();
                let datasets = datasets_from_paths(&files).unwrap();
                let processed: Vec<_> = datasets.iter().map(|d| d.data.clone()).collect();
                let cold = vec![0.0; datasets.len()];
                let per_station = case["scale"] == "per_station";
                let options = PanelOptions {
                    alignment: if case["mode"] == "ut" { Alignment::Ut } else { Alignment::Seconds },
                    use_db: case["use_db"].as_bool().unwrap(),
                    color_scale: if per_station { ColorScale::PerStation } else { ColorScale::Shared },
                    manual_limits: None,
                };
                let (payloads, effective, _) = panel_payloads(&datasets, &processed, &cold, &options);
                let range = &case["display_range"];
                let grid = GridOptions {
                    alignment: effective,
                    time_range: (!range.is_null()).then(|| (range["time_start_s"].as_f64().unwrap(), range["time_stop_s"].as_f64().unwrap())),
                    use_db: options.use_db,
                    cmap: case["cmap"].as_str().unwrap().to_owned(),
                    per_station,
                    columns: case["columns"].as_u64().map(|c| c as usize),
                    title: case["title"].as_str().unwrap().to_owned(),
                };
                let fig = comparison_grid_figure(&payloads, &grid);
                for (i, ax) in case["axes"].as_array().unwrap().iter().enumerate() {
                    let (xl, yl) = (&ax["xlim"], &ax["ylim"]);
                    let near = |a: f64, b: &Value| (a - b.as_f64().unwrap()).abs() <= 1e-6 * (1.0 + a.abs());
                    let Some(o) = fig.axes.get(i) else { break };
                    if !(near(o.xlim.0, &xl[0]) && near(o.xlim.1, &xl[1]) && near(o.ylim.0, &yl[0]) && near(o.ylim.1, &yl[1])) {
                        failures.push(format!("{name}: axes {i} view {:?} {:?}, matplotlib {xl} {yl}", o.xlim, o.ylim));
                    }
                }
                fig
            }
            "batch" => {
                let file = case["file"].as_str().unwrap();
                let r = load_callisto_fits(&fixture(file)).unwrap();
                let mode = match case["mode"].as_str().unwrap() {
                    "raw" => OutputMode::Raw,
                    _ => OutputMode::BackgroundSubtracted(BackgroundMethod::normalize(case["method"].as_str().unwrap(), true).unwrap()),
                };
                let cfg = (!case["view_config"].is_null()).then(|| ViewConfig::from_json(&case["view_config"]).unwrap());
                let spec = batch_figure_spec(&r, file, mode, case["cmap"].as_str().unwrap(), 0.0, cfg.as_ref()).unwrap();
                let fig = batch_figure(&spec);
                // The views: the image extent or the locked range, the levels.
                for (i, ax) in case["axes"].as_array().unwrap().iter().enumerate() {
                    let (xl, yl) = (&ax["xlim"], &ax["ylim"]);
                    let near = |a: f64, b: &Value| (a - b.as_f64().unwrap()).abs() <= 1e-6 * (1.0 + a.abs());
                    let o = &fig.axes[i];
                    if !(near(o.xlim.0, &xl[0]) && near(o.xlim.1, &xl[1]) && near(o.ylim.0, &yl[0]) && near(o.ylim.1, &yl[1])) {
                        failures.push(format!("{name}: axes {i} view {:?} {:?}, matplotlib {xl} {yl}", o.xlim, o.ylim));
                    }
                }
                fig
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
        let recorded = Recorded::new(case);
        let list = fig.layout(&recorded);
        if case["kind"] == "analysis" {
            check_analysis(name, case, &fig, &list, &recorded, &mut failures);
            analysis_checked += 1;
        }
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
    eprintln!("{} figures checked, {analysis_checked} of them analysis graphs", cases.len());
}
