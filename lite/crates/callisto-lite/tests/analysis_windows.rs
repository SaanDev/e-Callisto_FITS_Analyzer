//! The analysis windows against the full app's (`lite/tools/capture_analysis.py`,
//! `reference/windows/analysis/window_*` and the settings dialogs): the
//! main window isolates the burst with the same lasso, the windows open on
//! its maxima, and each is drawn at the captured window size and 1.5×.
//!
//! Needs a GPU or software wgpu adapter, so it is opt-in:
//! `cargo test -p callisto-lite --test analysis_windows -- --ignored --nocapture`

use std::path::{Path, PathBuf};

use callisto_core::analysis::ridge::RidgeSettings;
use callisto_lite::analysis::analyzer_window::AnalyzerWindow;
use callisto_lite::analysis::max_window::MaxWindow;
use callisto_lite::analysis::ridge_dialog::RidgeSettingsDialog;
use callisto_lite::app::{LiteApp, Settings};
use callisto_lite::fonts::SystemFonts;
use callisto_lite::gpu::GpuPreference;
use callisto_lite::theme::{Palette, ThemeMode};
use egui::{Event, PointerButton};
use egui_kittest::Harness;
use image::{Rgba, RgbaImage};

fn lite_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn compare(name: &str, lite: &RgbaImage) -> Option<f64> {
    let out = lite_dir().join("reference/compare");
    std::fs::create_dir_all(&out).unwrap();
    lite.save(out.join(format!("lite_{name}.png"))).unwrap();
    let reference = image::open(lite_dir().join(format!("reference/windows/analysis/{name}.png"))).ok()?.to_rgba8();
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

const LASSO: [(f64, f64); 6] = [(100.0, 85.0), (300.0, 88.0), (500.0, 70.0), (520.0, 45.0), (350.0, 35.0), (150.0, 50.0)];

/// The main window with the burst isolated, as the capture leaves it.
fn isolated(theme: ThemeMode) -> Harness<'static, LiteApp> {
    use callisto_core::radio::processing::BackgroundMethod;
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
    let file = lite_dir().join("parity/fixtures/INDIA-GAURI_20261007_041500_02.fit.gz").to_string_lossy().into_owned();
    h.state_mut().open_paths(&[file]);
    h.run();
    let s = &mut h.state_mut().session;
    s.set_background_method(BackgroundMethod::Mean);
    s.apply_background_subtraction();
    h.run();
    h.state_mut().session.activate_lasso();
    h.run();
    let button = |h: &mut Harness<'static, LiteApp>, (x, y): (f64, f64), pressed: Option<bool>| {
        let p = h.state().session.canvas.screen_pos(x, y).expect("the canvas has been drawn");
        let events = &mut h.input_mut().events;
        events.push(Event::PointerMoved(p));
        if let Some(pressed) = pressed {
            events.push(Event::PointerButton { pos: p, button: PointerButton::Primary, pressed, modifiers: Default::default() });
        }
        h.step();
    };
    button(&mut h, LASSO[0], Some(true));
    for p in &LASSO[1..] {
        button(&mut h, *p, None);
    }
    button(&mut h, LASSO[5], Some(false));
    h.run();
    assert_eq!(h.state().session.current_plot_type, "Isolated Burst");
    h
}

type Draw = Box<dyn FnMut(&mut egui::Ui)>;

struct TestApp {
    draw: Draw,
    p: Palette,
    frames: u32,
}

impl eframe::App for TestApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        if self.frames == 0 {
            egui_extras::install_image_loaders(ui.ctx());
            SystemFonts::load().install(ui.ctx());
            ui.ctx().set_theme(if self.p.dark { egui::Theme::Dark } else { egui::Theme::Light });
            self.p.apply(ui.ctx());
        }
        self.frames += 1;
        if self.frames < 2 {
            return;
        }
        ui.painter().rect_filled(ui.max_rect(), 0.0, self.p.page_bg);
        (self.draw)(ui);
    }
}

fn shot(name: &str, size: [f32; 2], p: Palette, draw: impl FnMut(&mut egui::Ui) + 'static) -> Option<f64> {
    let app = TestApp { draw: Box::new(draw), p, frames: 0 };
    let mut h = Harness::builder()
        .with_size(size)
        .with_pixels_per_point(1.5)
        .wgpu_setup(callisto_lite::gpu::wgpu_options(GpuPreference::from_env()).wgpu_setup)
        .build_eframe(move |_| app);
    for _ in 0..6 {
        h.step();
    }
    let img = h.render().expect("render");
    compare(name, &img)
}

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn analysis_windows_match_full_app() {
    let mut results = Vec::new();
    for (theme, name, dark) in [(ThemeMode::Light, "light", false), (ThemeMode::Dark, "dark", true)] {
        let p = Palette::new(dark, true);
        let mut h = isolated(theme);
        let app = h.state_mut();
        app.analysis.open_or_focus_max(&mut app.session, None, false, true, true);
        let status = app.session.status_message().map(|(t, _)| t.to_owned()).unwrap_or_default();
        println!("main window status: {status}");
        let window: MaxWindow = app.analysis.max.take().expect("the window opened");
        let shared = std::rc::Rc::new(std::cell::RefCell::new(window));

        let (w, pp) = (shared.clone(), p.clone());
        results.push((format!("window_max_{name}"), shot(&format!("window_max_{name}"), [1000.0, 700.0], p.clone(), move |ui| {
            w.borrow_mut().frame(ui, &pp);
        })));
        assert!(shared.borrow_mut().track_ridge(), "the ridge was tracked");
        let (w, pp) = (shared.clone(), p.clone());
        results.push((format!("window_max_ridge_{name}"), shot(&format!("window_max_ridge_{name}"), [1000.0, 700.0], p.clone(), move |ui| {
            w.borrow_mut().frame(ui, &pp);
        })));
        // The Analyzer, opened on the per-column maxima again.
        shared.borrow_mut().restore_per_column_maxima();
        let session = shared.borrow().session_state();
        let ut = h.state().session.data.as_ref().and_then(|d| d.ut_start_sec);
        let mut an = AnalyzerWindow::new(&session, "INDIA-GAURI_20261007_041500_02.fit.gz");
        an.set_ut_start_sec(ut);
        let an = std::rc::Rc::new(std::cell::RefCell::new(an));
        type Step = (&'static str, Box<dyn Fn(&mut AnalyzerWindow)>);
        let steps: Vec<Step> = vec![
            ("window_analyzer", Box::new(|_| {})),
            ("window_analyzer_max", Box::new(|w| w.plot_max())),
            ("window_analyzer_fit", Box::new(|w| w.plot_fit(None))),
            ("window_analyzer_extra", Box::new(|w| w.plot_extra_choice(0))),
        ];
        for (state, step) in steps {
            step(&mut an.borrow_mut());
            let (w, pp) = (an.clone(), p.clone());
            let shot_name = format!("{state}_{name}");
            results.push((shot_name.clone(), shot(&shot_name, [1100.0, 700.0], p.clone(), move |ui| {
                w.borrow_mut().frame(ui, &pp);
            })));
        }
        let mut d = RidgeSettingsDialog::new(&RidgeSettings::default());
        let pp = p.clone();
        results.push((format!("dialog_ridge_settings_{name}"), shot(&format!("dialog_ridge_settings_{name}"), [291.0, 244.0], p.clone(), move |ui| {
            d.frame(ui, &pp);
        })));
    }
    for (n, r) in &results {
        match r {
            Some(d) => println!("{n}: {d:.2}% differ"),
            None => println!("{n}: no reference"),
        }
    }
    // Text is most of the small settings dialog: ClearType against Lite's
    // grey anti-aliasing weighs more there (as in `analysis_dialogs`).
    let limit = |name: &str| if name.starts_with("dialog_") { 6.0 } else { 2.0 };
    let failing: Vec<_> = results.iter().filter(|(n, r)| r.is_some_and(|d| d >= limit(n))).collect();
    assert!(failing.is_empty(), "windows no longer match the full app: {failing:?}");
}
