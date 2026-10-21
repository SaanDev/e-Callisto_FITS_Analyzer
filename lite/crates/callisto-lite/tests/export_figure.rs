//! File → Export Figure against the full app's (`parity/make_project_reference.py`):
//! each project the full app wrote is opened and its figure saved as PNG
//! at 300 dpi, trimmed to the drawing; the image must have the full app's
//! size and differ from it in only a small share of pixels (text is set by
//! a different rasteriser, without FreeType's hinting; edges are
//! antialiased differently).
//!
//! Skipped when the references have not been generated.
//! `CALLISTO_KEEP_FIGURES=1` keeps Lite's PNGs next to the scratch files.

use std::path::{Path, PathBuf};

use callisto_lite::fonts::SystemFonts;
use callisto_lite::project::ProjectState;
use callisto_lite::session::Session;

fn refs() -> Option<PathBuf> {
    let d = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/out/projects");
    d.join("processed_export_figure.png").exists().then_some(d)
}

fn scratch(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("callisto_export_figure_{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d.join(name)
}

/// The share of pixels where a channel differs by more than `tol`.
fn differing(a: &image::RgbaImage, b: &image::RgbaImage, tol: u8) -> f64 {
    let n = a.pixels().zip(b.pixels()).filter(|(p, q)| p.0.iter().zip(q.0.iter()).any(|(x, y)| x.abs_diff(*y) > tol)).count();
    n as f64 / (a.width() * a.height()) as f64
}

/// A PDF's `/MediaBox` or an EPS's `%%HiResBoundingBox` size, in points.
fn page_size(bytes: &[u8]) -> Option<(f64, f64)> {
    let text = String::from_utf8_lossy(bytes);
    let nums = |s: &str| -> Vec<f64> { s.split_whitespace().filter_map(|t| t.parse().ok()).collect() };
    let v = if let Some(i) = text.find("/MediaBox [") {
        nums(&text[i + 11..i + 11 + text[i + 11..].find(']')?])
    } else {
        let i = text.find("%%HiResBoundingBox:")?;
        nums(text[i + 19..].lines().next()?)
    };
    (v.len() == 4).then(|| (v[2] - v[0], v[3] - v[1]))
}

#[test]
fn export_figure_matches_the_full_app() {
    let Some(dir) = refs() else {
        eprintln!("note: no project references; run lite/parity/make_project_reference.py");
        return;
    };
    let ctx = egui::Context::default();
    let mut out = ctx.run_ui(egui::RawInput::default(), |ui| {
        let ctx = ui.ctx();
        SystemFonts::load().install(ctx);
    });
    out.textures_delta.clear();
    let mut failures = Vec::new();
    let cases = [("raw", false), ("processed", false), ("processed_log", true), ("analysis", false), ("combined", false)];
    let mut out = ctx.run_ui(egui::RawInput::default(), |ui| {
        let ctx = ui.ctx();
        for (name, log) in cases {
            let project = name.trim_end_matches("_log");
            let mut s = Session::new();
            let mut p = ProjectState::new(scratch(&format!("{name}_cache")));
            p.open(&mut s, &dir.join(format!("{project}.efaproj"))).unwrap();
            s.flush_deferred();
            let _ = s.take_pending_analysis_restore();
            if log {
                s.set_frequency_axis_log(true);
            }
            let graph = s.origin_export_graph().expect("a spectrum to export");
            let fig = callisto_core::mpl::spectrum_figures::dynamic_spectrum_figure(&graph);
            let out = scratch(&format!("{name}_export_figure.png"));
            callisto_lite::figure_export::save_figure_padded(ctx, &fig, &out, 300.0, 0.08).unwrap();
            if name == "processed" {
                // The vector formats: a well-formed PDF (each xref entry at
                // its object), a bounded EPS, and SVG.
                for ext in ["pdf", "eps", "svg"] {
                    let f = scratch(&format!("{name}_export_figure.{ext}"));
                    callisto_lite::figure_export::save_figure_padded(ctx, &fig, &f, 300.0, 0.08).unwrap();
                    let bytes = std::fs::read(&f).unwrap();
                    let text = String::from_utf8_lossy(&bytes);
                    let ok = match ext {
                        "pdf" => {
                            // Offsets are bytes: search the raw file.
                            let find = |hay: &[u8], needle: &[u8]| hay.windows(needle.len()).rposition(|w| w == needle);
                            let tail = find(&bytes, b"startxref
").unwrap() + 10;
                            let start: usize = String::from_utf8_lossy(&bytes[tail..]).lines().next().unwrap().parse().unwrap();
                            let table = String::from_utf8_lossy(&bytes[start..]).into_owned();
                            table.starts_with("xref")
                                && table.lines().skip(3).take_while(|l| l.ends_with(" n ")).enumerate().all(|(i, l)| {
                                    let at: usize = l[..10].parse().unwrap();
                                    bytes[at..].starts_with(format!("{} 0 obj", i + 1).as_bytes())
                                })
                        }
                        "eps" => text.starts_with("%!PS-Adobe-3.0 EPSF-3.0") &&text.ends_with("%%EOF\n"),
                        _ => text.contains("<svg"),
                    };
                    if !ok {
                        failures.push(format!("{name}.{ext} is malformed"));
                    }
                    // The page: the full app's tight box, in points.
                    if ext != "svg" {
                        let full = std::fs::read(dir.join(format!("{name}_export_figure.{ext}"))).unwrap();
                        let (a, b) = (page_size(&bytes), page_size(&full));
                        if a.zip(b).is_none_or(|(a, b)| (a.0 - b.0).abs() > 0.05 || (a.1 - b.1).abs() > 0.05) {
                            failures.push(format!("{name}.{ext}: page {a:?}, the full app {b:?}"));
                        }
                    }
                }
            }
            let lite = image::open(&out).unwrap().to_rgba8();
            let full = image::open(dir.join(format!("{name}_export_figure.png"))).unwrap().to_rgba8();
            if lite.dimensions() != full.dimensions() {
                failures.push(format!("{name}: {:?}, the full app {:?}", lite.dimensions(), full.dimensions()));
                continue;
            }
            let share = differing(&lite, &full, 40);
            eprintln!("{name}: {:.2} % of pixels differ", share * 100.0);
            // On a log axis Agg resamples through a mesh with an unwidened
            // filter, which aliases the noise: the few tenths of a pixel the
            // layout moves (FreeType's hinted text widths) change many pixels.
            let limit = if log { 0.05 } else { 0.03 };
            if share > limit {
                failures.push(format!("{name}: {:.2} % of pixels differ", share * 100.0));
            }
        }
    });
    out.textures_delta.clear();
    if std::env::var_os("CALLISTO_KEEP_FIGURES").is_some() {
        eprintln!("Lite's figures are in {}", scratch("").display());
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
