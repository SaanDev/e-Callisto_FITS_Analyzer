//! The analysis tools on the main window against the full app's
//! `lite/tools/capture_analysis.py` captures (`reference/windows/analysis`):
//! the same clicks and drags at the same plot coordinates, sent through
//! egui's input like a mouse.
//!
//! Needs a GPU or software wgpu adapter, so it is opt-in:
//! `cargo test -p callisto-lite --test analysis_screenshots -- --ignored --nocapture`

use std::path::{Path, PathBuf};

use callisto_core::analysis::annotations::TextStyle;
use callisto_lite::app::{LiteApp, Settings};
use callisto_lite::gpu::GpuPreference;
use callisto_lite::theme::ThemeMode;
use egui::{Event, PointerButton, Pos2};
use egui_kittest::Harness;
use image::{Rgba, RgbaImage};

fn lite_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn fixture(name: &str) -> String {
    lite_dir().join("parity/fixtures").join(name).to_string_lossy().into_owned()
}

fn harness(theme: ThemeMode) -> Harness<'static, LiteApp> {
    let mut h = Harness::builder()
        .with_size([1600.0, 1000.0])
        .with_pixels_per_point(1.5)
        .wgpu_setup(callisto_lite::gpu::wgpu_options(GpuPreference::from_env()).wgpu_setup)
        .build_eframe(move |cc| {
            let mut app = LiteApp::with_settings(&cc.egui_ctx, Settings { theme_mode: theme, ..Default::default() });
            app.os_dark_override = Some(true);
            app.session.set_archive(None, false);
            app
        });
    for _ in 0..6 {
        h.step();
    }
    h.run();
    h
}

/// The share of pixels that differ by more than 32 levels, writing the
/// comparison images to `reference/compare/`.
fn compare(name: &str, lite: &RgbaImage, reference: &Path) -> Option<f64> {
    let out = lite_dir().join("reference/compare");
    std::fs::create_dir_all(&out).unwrap();
    lite.save(out.join(format!("lite_{name}.png"))).unwrap();
    let Ok(reference) = image::open(reference) else {
        eprintln!("{name}: no reference at {}", reference.display());
        return None;
    };
    let reference = reference.to_rgba8();
    let (w, h) = (reference.width().min(lite.width()), reference.height().min(lite.height()));
    let mut side = RgbaImage::from_pixel(w * 2 + 8, h, Rgba([255, 0, 255, 255]));
    image::imageops::overlay(&mut side, &reference, 0, 0);
    image::imageops::overlay(&mut side, lite, (w + 8) as i64, 0);
    side.save(out.join(format!("side_by_side_{name}.png"))).unwrap();
    let mut diff = RgbaImage::new(w, h);
    let mut differing = 0u64;
    for y in 0..h {
        for x in 0..w {
            let (a, b) = (reference.get_pixel(x, y), lite.get_pixel(x, y));
            let d = (0..3).map(|c| (a[c] as i32 - b[c] as i32).unsigned_abs()).max().unwrap();
            if d > 32 {
                differing += 1;
            }
            let v = (d * 4).min(255) as u8;
            diff.put_pixel(x, y, Rgba([v, v, v, 255]));
        }
    }
    diff.save(out.join(format!("diff_{name}.png"))).unwrap();
    Some(differing as f64 / (w * h) as f64 * 100.0)
}

fn pos(h: &Harness<'_, LiteApp>, x: f64, y: f64) -> Pos2 {
    h.state().session.canvas.screen_pos(x, y).expect("the canvas has been drawn")
}

/// A press and release in one frame, as a quick click.
fn click(h: &mut Harness<'_, LiteApp>, x: f64, y: f64, button: PointerButton) {
    let p = pos(h, x, y);
    let events = &mut h.input_mut().events;
    events.push(Event::PointerMoved(p));
    for pressed in [true, false] {
        events.push(Event::PointerButton { pos: p, button, pressed, modifiers: Default::default() });
    }
    h.step();
}

fn left(h: &mut Harness<'_, LiteApp>, (x, y): (f64, f64)) {
    click(h, x, y, PointerButton::Primary);
}

/// Two clicks a frame (0.25 s) apart: egui's double-click.
fn double_click(h: &mut Harness<'_, LiteApp>, xy: (f64, f64)) {
    left(h, xy);
    left(h, xy);
}

fn move_to(h: &mut Harness<'_, LiteApp>, (x, y): (f64, f64)) {
    let p = pos(h, x, y);
    h.input_mut().events.push(Event::PointerMoved(p));
    h.step();
}

fn button(h: &mut Harness<'_, LiteApp>, (x, y): (f64, f64), pressed: bool) {
    let p = pos(h, x, y);
    let events = &mut h.input_mut().events;
    events.push(Event::PointerMoved(p));
    events.push(Event::PointerButton { pos: p, button: PointerButton::Primary, pressed, modifiers: Default::default() });
    h.step();
}

// The plot coordinates in capture_analysis.py.
const RULER: [(f64, f64); 2] = [(150.0, 80.0), (420.0, 45.0)];
const DRIFT: [(f64, f64); 4] = [(100.0, 85.0), (220.0, 70.0), (380.0, 52.0), (560.0, 38.0)];
const LIGHT_CURVES: [f64; 3] = [62.0, 47.3, 81.0];
const POLYGON: [(f64, f64); 3] = [(120.0, 85.0), (300.0, 75.0), (250.0, 50.0)];
const POLYGON_CURSOR: (f64, f64) = (140.0, 45.0);
const LINE: [(f64, f64); 2] = [(500.0, 85.0), (800.0, 40.0)];
const TEXT: (f64, f64) = (600.0, 70.0);
const LASSO: [(f64, f64); 6] = [(100.0, 85.0), (300.0, 88.0), (500.0, 70.0), (520.0, 45.0), (350.0, 35.0), (150.0, 50.0)];

type Step<'a> = (&'a str, Box<dyn Fn(&mut Harness<'static, LiteApp>) + 'a>);

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn analysis_tools_match_full_app() {
    use callisto_core::radio::processing::BackgroundMethod;
    let file = fixture("INDIA-GAURI_20261007_041500_02.fit.gz");
    let mut worst: Vec<(String, f64)> = Vec::new();
    for (theme, name) in [(ThemeMode::Light, "light"), (ThemeMode::Dark, "dark")] {
        let mut h = harness(theme);
        let steps: Vec<Step<'_>> = vec![
            (
                "base",
                Box::new(|h| {
                    h.state_mut().open_paths(std::slice::from_ref(&file));
                    h.run();
                    let s = &mut h.state_mut().session;
                    s.set_background_method(BackgroundMethod::Mean);
                    s.apply_background_subtraction();
                }),
            ),
            (
                "ruler_pending",
                Box::new(|h| {
                    h.state_mut().session.activate_ruler_tool();
                    h.run();
                    left(h, RULER[0]);
                }),
            ),
            ("ruler", Box::new(|h| left(h, RULER[1]))),
            (
                "drift_points",
                Box::new(|h| {
                    h.state_mut().session.clear_ruler_measurement(true);
                    h.state_mut().session.activate_drift_tool();
                    h.run();
                    for p in &DRIFT[..3] {
                        left(h, *p);
                    }
                }),
            ),
            ("drift", Box::new(|h| double_click(h, DRIFT[3]))),
            (
                "light_curve",
                Box::new(|h| {
                    let s = &mut h.state_mut().session;
                    s.canvas.show_drift_points(&[], false);
                    s.plot_light_curve_at_frequency(LIGHT_CURVES[0], true);
                }),
            ),
            (
                "light_curves",
                Box::new(|h| {
                    let s = &mut h.state_mut().session;
                    let mut settings = s.tools.light_curve_settings.clone();
                    settings.multiple = true;
                    s.set_light_curve_settings(settings);
                    for f in &LIGHT_CURVES[1..] {
                        s.plot_light_curve_at_frequency(*f, true);
                    }
                }),
            ),
            (
                "light_curves_dashed",
                Box::new(|h| {
                    let s = &mut h.state_mut().session;
                    let mut settings = s.tools.light_curve_settings.clone();
                    settings.line_style = "dashed".into();
                    settings.line_width = 3.0;
                    settings.opacity = 0.6;
                    settings.show_frequency_label = false;
                    s.set_light_curve_settings(settings);
                }),
            ),
            (
                "light_curves_dotted",
                Box::new(|h| {
                    let s = &mut h.state_mut().session;
                    let mut settings = s.tools.light_curve_settings.clone();
                    settings.line_style = "dotted".into();
                    settings.line_width = 1.0;
                    settings.opacity = 1.0;
                    settings.vertical_scale = 2.5;
                    s.set_light_curve_settings(settings);
                }),
            ),
            (
                "annotation_capture",
                Box::new(|h| {
                    h.state_mut().session.clear_light_curve();
                    h.state_mut().session.start_annotation_polygon();
                    h.run();
                    for p in POLYGON {
                        left(h, p);
                    }
                    move_to(h, POLYGON_CURSOR);
                }),
            ),
            (
                "annotations",
                Box::new(|h| {
                    click(h, POLYGON_CURSOR.0, POLYGON_CURSOR.1, PointerButton::Secondary);
                    h.state_mut().session.start_annotation_line();
                    h.run();
                    for p in LINE {
                        left(h, p);
                    }
                    let style = TextStyle {
                        color: "#ffcc00".into(),
                        font_family: String::new(),
                        font_size: 16,
                        font_bold: true,
                        font_italic: false,
                    };
                    h.state_mut().session.start_annotation_text("Type III burst", style);
                    h.run();
                    left(h, TEXT);
                }),
            ),
            (
                "lasso_drawing",
                Box::new(|h| {
                    h.state_mut().session.clear_annotations();
                    h.state_mut().session.activate_lasso();
                    h.run();
                    button(h, LASSO[0], true);
                    for p in &LASSO[1..] {
                        move_to(h, *p);
                    }
                }),
            ),
            ("isolated", Box::new(|h| button(h, LASSO[5], false))),
        ];
        for (state, action) in steps {
            action(&mut h);
            h.run();
            let img = h.render().expect("render");
            let reference = lite_dir().join(format!("reference/windows/analysis/analysis_{state}_{name}.png"));
            if let Some(d) = compare(&format!("analysis_{state}_{name}"), &img, &reference) {
                let status = h.state_mut().session.status_message().map(|(t, _)| t.to_owned()).unwrap_or_default();
                println!("{state} {name}: {d:.2}% differ; status: {status}");
                worst.push((format!("{state} {name}"), d));
            }
        }
    }
    let failing: Vec<&(String, f64)> = worst.iter().filter(|(_, d)| *d >= 2.0).collect();
    assert!(failing.is_empty(), "states no longer match the full app: {failing:?}");
}
