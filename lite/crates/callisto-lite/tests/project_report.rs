//! File → Generate Project Report against the full app's
//! (`parity/make_report_reference.py`, after `make_project_reference.py`):
//! Lite opens each project, builds its report from its own state, figures
//! included, and lays it out. Every flowable must land where ReportLab put
//! the full app's (same pages, positions, sizes, line breaks, table rows);
//! only the application's name and version (and the generation time) are
//! taken from the full app's report. Each figure must have the full app's
//! size and differ from it in only a small share of pixels.
//!
//! Skipped when the references have not been generated.

use std::path::{Path, PathBuf};

use callisto_core::report::project::{Val, lay_out};
use callisto_lite::fonts::SystemFonts;
use callisto_lite::project::ProjectState;
use callisto_lite::session::Session;

fn refs() -> Option<PathBuf> {
    let d = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/out/projects");
    // The full app opened the projects by their plain absolute paths.
    let d = std::fs::canonicalize(&d).ok()?;
    let d = PathBuf::from(d.to_string_lossy().trim_start_matches(r"\\?\").to_owned());
    d.join("raw_report_layout.json").exists().then_some(d)
}

fn scratch(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("callisto_project_report_{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d.join(name)
}

fn json(path: &Path) -> serde_json::Value {
    // NaN included (`json.dumps` writes it): read as Python reads it.
    callisto_core::pyjson::to_serde(&callisto_core::pyjson::loads(&std::fs::read_to_string(path).unwrap()).unwrap())
}

/// The share of pixels where a channel differs by more than `tol`, with
/// `b` shifted by up to a pixel either way (the least of those): an image
/// pasted at a rounded position moves a pixel when the layout moves a
/// fraction of one.
fn differing(a: &image::RgbaImage, b: &image::RgbaImage, tol: u8) -> f64 {
    let (w, h) = (a.width().min(b.width()) as i64, a.height().min(b.height()) as i64);
    let mut best = f64::INFINITY;
    for dy in -1i64..=1 {
        for dx in -1i64..=1 {
            let mut n = 0usize;
            for y in 1..h - 1 {
                for x in 1..w - 1 {
                    let p = a.get_pixel(x as u32, y as u32);
                    let q = b.get_pixel((x + dx) as u32, (y + dy) as u32);
                    if p.0.iter().zip(q.0.iter()).any(|(u, v)| u.abs_diff(*v) > tol) {
                        n += 1;
                    }
                }
            }
            best = best.min(n as f64 / ((w - 2) * (h - 2)) as f64);
        }
    }
    best
}

#[test]
fn project_report_matches_the_full_app() {
    let Some(dir) = refs() else {
        eprintln!("note: no report references; run lite/parity/make_report_reference.py");
        return;
    };
    let ctx = egui::Context::default();
    let mut out = ctx.run_ui(egui::RawInput::default(), |ui| {
        SystemFonts::load().install(ui.ctx());
    });
    out.textures_delta.clear();
    let mut failures = Vec::new();
    let mut out = ctx.run_ui(egui::RawInput::default(), |ui| {
        let ctx = ui.ctx();
        for name in ["raw", "processed", "analysis", "combined"] {
            let mut s = Session::new();
            let mut p = ProjectState::new(scratch(&format!("{name}_cache")));
            let path = dir.join(format!("{name}.efaproj"));
            p.open(&mut s, &path).unwrap();
            s.flush_deferred();
            let _ = s.take_pending_analysis_restore();
            let mut input = s.project_report_input(ctx, Some(&path), None);
            let reference = json(&dir.join(format!("{name}_report.json")));
            input.app_name = Val::Str(reference["app"]["name"].as_str().unwrap().to_owned());
            input.app_version = Val::Str(reference["app"]["version"].as_str().unwrap().to_owned());
            input.generated_at = "2026-10-21T09:00:00+05:30".into();

            // The figures: which there are, their notes and captions, their pixels.
            let want = reference["figures"].as_array().unwrap();
            if want.len() != input.figures.len() {
                failures.push(format!("{name}: {} figures, the full app {}", input.figures.len(), want.len()));
            }
            for (i, (got, w)) in input.figures.iter().zip(want).enumerate() {
                let same = got.title == w["title"].as_str().unwrap()
                    && got.caption == w["caption"].as_str().unwrap()
                    && got.availability_note == w["availability_note"].as_str().unwrap()
                    && got.source_filename == w["source_filename"].as_str().unwrap();
                if !same {
                    failures.push(format!("{name} figure {i}: {:?} {:?} {:?}, the full app {w}", got.title, got.caption, got.availability_note));
                }
                match (&got.png, w["png_size"].as_array()) {
                    (None, None) => {}
                    (Some((png, gw, gh)), Some(size)) => {
                        let (fw, fh) = (size[0].as_u64().unwrap() as u32, size[1].as_u64().unwrap() as u32);
                        // FreeType's hinted text widths can move the tight box a pixel or two.
                        if gw.abs_diff(fw) > 3 || gh.abs_diff(fh) > 3 {
                            failures.push(format!("{name} figure {i} ({}): {gw}x{gh}, the full app {fw}x{fh}", got.title));
                            continue;
                        }
                        let lite = image::load_from_memory(png).unwrap().to_rgba8();
                        let full = image::open(dir.join(format!("{name}_report_figure{i}.png"))).unwrap().to_rgba8();
                        let share = differing(&lite, &full, 40);
                        eprintln!("{name} figure {i} ({}): {:.2} % of pixels differ", got.title, share * 100.0);
                        if share > 0.1 {
                            failures.push(format!("{name} figure {i} ({}): {:.2} % of pixels differ", got.title, share * 100.0));
                        }
                        if std::env::var_os("CALLISTO_KEEP_FIGURES").is_some() {
                            std::fs::write(scratch(&format!("{name}_report_figure{i}.png")), png.as_slice()).unwrap();
                        }
                    }
                    (g, w) => failures.push(format!("{name} figure {i} ({}): drawn {}, the full app {}", got.title, g.is_some(), w.is_some())),
                }
            }

            // The layout: every flowable where ReportLab put it, the figures
            // at the full app's sizes (checked above to a few pixels).
            for (got, w) in input.figures.iter_mut().zip(want) {
                if let (Some((_, gw, gh)), Some(size)) = (&mut got.png, w["png_size"].as_array()) {
                    (*gw, *gh) = (size[0].as_u64().unwrap() as u32, size[1].as_u64().unwrap() as u32);
                }
            }
            let (laid, _) = lay_out(&input);
            if std::env::var_os("CALLISTO_KEEP_FIGURES").is_some() {
                let pdf = scratch(&format!("{name}_report.pdf"));
                callisto_core::report::project::generate_project_report_pdf(&pdf, &input, &mut |_, _| true).unwrap();
                eprintln!("{name}: wrote {}", pdf.display());
            }
            let layout = json(&dir.join(format!("{name}_report_layout.json")));
            let layout = layout.as_array().unwrap();
            if layout.len() != laid.placed.len() {
                failures.push(format!("{name}: {} flowables, ReportLab {}", laid.placed.len(), layout.len()));
            }
            for (i, (got, w)) in laid.placed.iter().zip(layout).enumerate() {
                let f = |k: &str| w[k].as_f64().unwrap_or(0.0);
                let lines: Vec<String> = w["lines"].as_array().map(|l| l.iter().map(|x| x.as_str().unwrap().to_owned()).collect()).unwrap_or_default();
                let near = |a: f64, b: f64| (a - b).abs() < 1e-6;
                let ok = got.page as f64 == f("page")
                    && got.kind == w["kind"].as_str().unwrap()
                    && near(got.x, f("x"))
                    && near(got.y, f("y"))
                    && near(got.width, f("width"))
                    && near(got.height, f("height"))
                    && (got.kind != "Paragraph" && got.kind != "Preformatted" || got.lines == lines);
                if !ok {
                    failures.push(format!(
                        "{name} #{i}: {} {} p{} ({:.2}, {:.2}) {:.2}x{:.2} {:?}\n   ReportLab {} {} p{} ({:.2}, {:.2}) {:.2}x{:.2} {:?}",
                        got.kind, got.style, got.page, got.x, got.y, got.width, got.height, got.lines,
                        w["kind"], w["style"], f("page"), f("x"), f("y"), f("width"), f("height"), lines
                    ));
                    if failures.len() > 30 {
                        break;
                    }
                }
            }
        }
    });
    out.textures_delta.clear();
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}
