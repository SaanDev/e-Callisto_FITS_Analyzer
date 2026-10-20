//! The multi-station comparison against the full app's
//! (`lite/tools/capture_tools.py`, `reference/windows/tools/window_comparison_*`
//! and `dialog_comparison_export*`): one file from the main window, four
//! stations, two more files that combine, noise clipping, per-station dB
//! scales, a ruler measurement, the export dialog and the grid export.
//!
//! Needs a GPU or software wgpu adapter, so it is opt-in:
//! `cargo test -p callisto-lite --test comparison_windows -- --ignored --nocapture`

use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use callisto_lite::app::{LiteApp, Settings};
use callisto_lite::fonts::SystemFonts;
use callisto_lite::gpu::GpuPreference;
use callisto_lite::theme::{Palette, ThemeMode};
use callisto_lite::tools::comparison::ComparisonWindow;
use callisto_lite::tools::comparison_export::{ExportDialog, ExportOptions};
use egui_kittest::Harness;
use image::{Rgba, RgbaImage};

fn lite_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn fixture(name: &str) -> String {
    lite_dir().join("parity/fixtures").join(name).to_string_lossy().into_owned()
}

fn compare_images(name: &str, reference: &RgbaImage, lite: &RgbaImage) -> f64 {
    let out = lite_dir().join("reference/compare");
    std::fs::create_dir_all(&out).unwrap();
    lite.save(out.join(format!("lite_{name}.png"))).unwrap();
    if (reference.width(), reference.height()) != (lite.width(), lite.height()) {
        println!("{name}: size {}x{}, full app {}x{}", lite.width(), lite.height(), reference.width(), reference.height());
    }
    let (w, h) = (reference.width().min(lite.width()), reference.height().min(lite.height()));
    let mut side = RgbaImage::from_pixel(w * 2 + 8, h, Rgba([255, 0, 255, 255]));
    image::imageops::overlay(&mut side, reference, 0, 0);
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
    differing as f64 / (w * h) as f64 * 100.0
}

fn compare(name: &str, lite: &RgbaImage) -> Option<f64> {
    let reference = image::open(lite_dir().join(format!("reference/windows/tools/{name}.png"))).ok()?.to_rgba8();
    Some(compare_images(name, &reference, lite))
}

fn reference_json(name: &str) -> serde_json::Value {
    serde_json::from_str(&std::fs::read_to_string(lite_dir().join(format!("reference/windows/tools/{name}.json"))).unwrap()).unwrap()
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

fn harness(size: [f32; 2], p: Palette, draw: impl FnMut(&mut egui::Ui) + 'static) -> Harness<'static, TestApp> {
    let app = TestApp { draw: Box::new(draw), p, frames: 0 };
    let mut h = Harness::builder()
        .with_size(size)
        .with_pixels_per_point(1.5)
        .wgpu_setup(callisto_lite::gpu::wgpu_options(GpuPreference::from_env()).wgpu_setup)
        .build_eframe(move |_| app);
    for _ in 0..8 {
        h.step();
    }
    h
}

/// One harness per theme: the window drawn every frame, each state
/// stepped in and rendered (as the window keeps its textures).
struct Shots {
    h: Harness<'static, TestApp>,
}

impl Shots {
    fn new(p: Palette, w: Rc<RefCell<ComparisonWindow>>) -> Self {
        let pp = p.clone();
        let h = harness([1400.0, 900.0], p, move |ui| {
            let ctx = ui.ctx().clone();
            w.borrow_mut().flush(&ctx);
            w.borrow_mut().frame(ui, &pp);
        });
        Shots { h }
    }

    /// Steps until the visible export has captured every panel.
    fn run_visible_export(&mut self, w: &Rc<RefCell<ComparisonWindow>>) {
        for _ in 0..200 {
            if !w.borrow().visible_capture_running() {
                return;
            }
            self.h.step();
        }
        panic!("the visible export never finished");
    }

    fn shot(&mut self, name: &str) -> Option<f64> {
        for _ in 0..4 {
            self.h.step();
        }
        let img = self.h.render().expect("render");
        compare(name, &img)
    }
}

/// The main window's visual settings with the capture's file loaded.
fn main_window_visual(theme: ThemeMode) -> callisto_core::view_config::VisualConfig {
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
    h.state_mut().open_paths(&[fixture("INDIA-GAURI_20261007_041500_02.fit.gz")]);
    h.run();
    h.state().session.batch_env().visual
}

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn comparison_windows_match_full_app() {
    let mut results = Vec::new();
    let mut failures = Vec::new();
    let ctx = egui::Context::default();
    for (theme, name, dark) in [(ThemeMode::Light, "light", false), (ThemeMode::Dark, "dark", true)] {
        let p = Palette::new(dark, true);
        let visual = main_window_visual(theme);
        let w = Rc::new(RefCell::new(ComparisonWindow::new(&[fixture("INDIA-GAURI_20261007_041500_02.fit.gz")], Some(&visual), dark)));
        let mut shots = Shots::new(p.clone(), w.clone());
        let mut check = |shots: &mut Shots, state: &str, w: &Rc<RefCell<ComparisonWindow>>| {
            let n = format!("window_comparison_{state}_{name}");
            results.push((n.clone(), shots.shot(&n)));
            let r = reference_json(&n);
            let got = w.borrow();
            if got.status() != r["status"].as_str().unwrap() {
                failures.push(format!("{n}: status {:?}, full app {}", got.status(), r["status"]));
            }
            if got.readout_text() != r["readout"].as_str().unwrap() {
                failures.push(format!("{n}: readout {:?}, full app {}", got.readout_text(), r["readout"]));
            }
            let labels: Vec<String> = r["panels"].as_array().unwrap().iter().map(|p| p["label"].as_str().unwrap().to_owned()).collect();
            if got.panel_labels() != labels {
                failures.push(format!("{n}: panels {:?}, full app {labels:?}", got.panel_labels()));
            }
        };
        check(&mut shots, "one", &w);

        let four = ["GERMANY-DLR_20261007_050000_01.fit.gz", "TRIEST_20261007_050000_57.fit.gz", "INDIA-GAURI_20261007_050000_02.fit.gz", "AUSTRIA-OE3FLB_20261007_051500_55.fit.gz"];
        w.borrow_mut().clear();
        w.borrow_mut().add_files(&four.map(fixture));
        check(&mut shots, "four", &w);

        w.borrow_mut().add_files(&[fixture("GERMANY-DLR_20261007_050000_02.fit.gz"), fixture("TRIEST_20261007_050000_59.fit.gz")]);
        check(&mut shots, "combined", &w);

        {
            let mut m = w.borrow_mut();
            m.flush(&ctx);
            m.set_noise_method(&ctx, 4);
            m.set_noise_slider(&ctx, true, 1800);
            m.set_noise_slider(&ctx, false, 2600);
        }
        check(&mut shots, "clip", &w);

        {
            let mut m = w.borrow_mut();
            m.set_noise_method(&ctx, 0);
            m.set_units(&ctx, true);
            m.set_colormap_name(&ctx, "inferno");
            m.set_color_scale(1);
            m.flush(&ctx);
        }
        check(&mut shots, "per_station", &w);

        {
            let r = reference_json(&format!("window_comparison_measure_{name}"));
            let pts = &r["measurement_points"];
            let pt = |i: usize| (pts[i][0].as_f64().unwrap(), pts[i][1].as_f64().unwrap());
            let mut m = w.borrow_mut();
            m.set_color_scale(0);
            m.set_units(&ctx, false);
            m.set_colormap_name(&ctx, "Custom");
            m.flush(&ctx);
            m.set_measurement(r["measurement_panel"].as_u64().unwrap() as usize, pt(0), pt(1));
        }
        check(&mut shots, "measure", &w);

        // The visible export: each panel as shown, stacked.
        w.borrow_mut().clear_ruler();
        let visible = std::env::temp_dir().join(format!("callisto_lite_visible_{name}_{}.png", std::process::id()));
        w.borrow_mut().start_visible_capture(visible.clone(), "png".into());
        shots.run_visible_export(&w);
        let notes = w.borrow_mut().take_notices();
        assert!(notes.iter().any(|n| n.title == "Export Comparison"), "visible export: {notes:?}");
        let lite = image::open(&visible).unwrap().to_rgba8();
        let full = image::open(lite_dir().join(format!("reference/windows/tools/comparison_export_{name}_visible.png"))).unwrap().to_rgba8();
        let label = format!("comparison_export_{name}_visible");
        results.push((label.clone(), Some(compare_images(&label, &full, &lite))));
        let _ = std::fs::remove_file(&visible);

        // The grid export of the combined comparison, at the capture's settings.
        let meta = reference_json(&format!("comparison_export_{name}"));
        let options = ExportOptions { grid: true, columns: None, title: meta["title"].as_str().unwrap().to_owned(), dpi: meta["dpi"].as_i64().unwrap() as i32 };
        w.borrow_mut().clear_ruler();
        let out = std::env::temp_dir().join(format!("callisto_lite_grid_{name}_{}.png", std::process::id()));
        {
            let ww = w.clone();
            let o = options.clone();
            let path = out.clone();
            let mut done = false;
            let mut h = harness([200.0, 100.0], p.clone(), move |ui| {
                if !done {
                    let ctx = ui.ctx().clone();
                    ww.borrow_mut().export_grid(&ctx, &path, &o).unwrap();
                    done = true;
                }
            });
            h.step();
        }
        let lite = image::open(&out).unwrap().to_rgba8();
        let full = image::open(lite_dir().join(format!("reference/windows/tools/comparison_export_{name}_grid.png"))).unwrap().to_rgba8();
        let label = format!("comparison_export_{name}_grid");
        results.push((label.clone(), Some(compare_images(&label, &full, &lite))));
        let _ = std::fs::remove_file(&out);

        // The export dialog, both layouts.
        let labels = w.borrow().panel_labels();
        let title = callisto_core::radio::comparison::default_grid_title(&labels);
        assert_eq!(title, options.title);
        let d = Rc::new(RefCell::new(ExportDialog::new(&ExportOptions { title, ..Default::default() })));
        for (state, grid) in [("dialog_comparison_export", false), ("dialog_comparison_export_grid", true)] {
            d.borrow_mut().set_grid(grid);
            let size = reference_json(&format!("{state}_{name}"))["size"].clone();
            let size = [size[0].as_f64().unwrap() as f32, size[1].as_f64().unwrap() as f32];
            let (dd, pp) = (d.clone(), p.clone());
            let mut wanted = None;
            let mut h = harness(size, p.clone(), move |ui| {
                callisto_lite::ui::qt::with_app_style(|| {
                    let s = dd.borrow_mut().window_size(ui);
                    wanted.get_or_insert(s);
                });
                dd.borrow_mut().frame(ui, &pp);
            });
            let img = h.render().expect("render");
            let n = format!("{state}_{name}");
            results.push((n.clone(), compare(&n, &img)));
        }
    }
    for (n, r) in &results {
        match r {
            Some(d) => println!("{n}: {d:.2}% differ"),
            None => println!("{n}: no reference"),
        }
    }
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
    // Text is most of the export dialog: ClearType against Lite's grey
    // anti-aliasing weighs more there (as in `analysis_dialogs`); the grid
    // PNG's text boxes come from FreeType's autohinter (see `tools_windows`).
    let limit = |name: &str| if name.starts_with("comparison_export_") || name.starts_with("dialog_") { 6.0 } else { 3.0 };
    let failing: Vec<_> = results.iter().filter(|(n, r)| r.is_some_and(|d| d >= limit(n))).collect();
    assert!(failing.is_empty(), "windows no longer match the full app: {failing:?}");
}
