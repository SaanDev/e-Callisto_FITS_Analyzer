//! The data-tool windows against the full app's (`lite/tools/capture_tools.py`,
//! `reference/windows/tools/`): the Batch FIT Processing dialog fresh, with
//! folders and options set, and after a real run over three fixtures, whose
//! summary and output PNGs are checked too.
//!
//! Needs a GPU or software wgpu adapter, so it is opt-in:
//! `cargo test -p callisto-lite --test tools_windows -- --ignored --nocapture`

use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::{Duration, Instant};

use callisto_lite::app::{LiteApp, Settings};
use callisto_lite::fonts::SystemFonts;
use callisto_lite::gpu::GpuPreference;
use callisto_lite::theme::{Palette, ThemeMode};
use callisto_lite::tools::batch::{BatchDialog, BatchEnv};
use egui_kittest::Harness;
use image::{Rgba, RgbaImage};

fn lite_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// Percent of pixels differing by more than 32 in a channel; writes the
/// Lite image, a side-by-side and a diff to `reference/compare`.
fn compare_images(name: &str, reference: &RgbaImage, lite: &RgbaImage) -> f64 {
    let out = lite_dir().join("reference/compare");
    std::fs::create_dir_all(&out).unwrap();
    let (w, h) = (reference.width().min(lite.width()), reference.height().min(lite.height()));
    let mut side = RgbaImage::from_pixel(w * 2 + 8, h, Rgba([255, 0, 255, 255]));
    image::imageops::overlay(&mut side, reference, 0, 0);
    image::imageops::overlay(&mut side, lite, (w + 8) as i64, 0);
    side.save(out.join(format!("side_by_side_{name}.png"))).unwrap();
    lite.save(out.join(format!("lite_{name}.png"))).unwrap();
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
    let out = lite_dir().join("reference/compare");
    std::fs::create_dir_all(&out).unwrap();
    let reference = image::open(lite_dir().join(format!("reference/windows/tools/{name}.png"))).ok()?.to_rgba8();
    if (reference.width(), reference.height()) != (lite.width(), lite.height()) {
        println!(
            "{name}: size {}x{}, full app {}x{}",
            lite.width(),
            lite.height(),
            reference.width(),
            reference.height()
        );
    }
    Some(compare_images(name, &reference, lite))
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

/// Draws the dialog until `ready` (at least six frames), then renders it.
fn shot(
    name: &str,
    p: Palette,
    d: Rc<RefCell<BatchDialog>>,
    env: BatchEnv,
    ready: impl Fn(&mut BatchDialog) -> bool,
) -> Option<f64> {
    let (dd, pp) = (d.clone(), p.clone());
    let app = TestApp {
        draw: Box::new(move |ui| {
            dd.borrow_mut().frame(ui, &env, &pp);
        }),
        p,
        frames: 0,
    };
    let mut h = Harness::builder()
        .with_size([760.0, 626.0])
        .with_pixels_per_point(1.5)
        .wgpu_setup(callisto_lite::gpu::wgpu_options(GpuPreference::from_env()).wgpu_setup)
        .build_eframe(move |_| app);
    for _ in 0..6 {
        h.step();
    }
    let deadline = Instant::now() + Duration::from_secs(300);
    while !ready(&mut d.borrow_mut()) {
        assert!(Instant::now() < deadline, "{name}: the dialog never got ready");
        std::thread::sleep(Duration::from_millis(50));
        h.step();
    }
    for _ in 0..3 {
        h.step();
    }
    let img = h.render().expect("render");
    compare(name, &img)
}

/// The main window's providers with the capture's file loaded.
fn main_window_env(theme: ThemeMode) -> BatchEnv {
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
    h.state().session.batch_env()
}

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn tools_windows_match_full_app() {
    let mut results = Vec::new();
    for (theme, name, dark) in [(ThemeMode::Light, "light", false), (ThemeMode::Dark, "dark", true)] {
        let p = Palette::new(dark, true);
        // The capture's scratch folders, as its line edits show them.
        let raw: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(lite_dir().join(format!("reference/windows/tools/dialog_batch_raw_{name}.json")))
                .unwrap(),
        )
        .unwrap();
        let edits: Vec<String> = raw["layout"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|w| w["class"] == "QLineEdit")
            .map(|w| w["text"].as_str().unwrap().to_owned())
            .collect();
        let (shown_in, shown_out) = (edits[0].clone(), edits[1].clone());
        let env = main_window_env(theme);
        assert_eq!(env.cmap, "Custom");
        assert!(env.display_range.is_some(), "the main window has a display range");

        let d = Rc::new(RefCell::new(BatchDialog::new(&env)));
        let n = format!("dialog_batch_{name}");
        results.push((n.clone(), shot(&n, p.clone(), d.clone(), env.clone(), |_| true)));

        d.borrow_mut().set_folders(&shown_in, &shown_out);
        d.borrow_mut().set_options(true, 0, "Custom", false, false);
        let n = format!("dialog_batch_raw_{name}");
        results.push((n.clone(), shot(&n, p.clone(), d.clone(), env.clone(), |_| true)));

        d.borrow_mut().set_options(false, 2, "inferno", true, false);
        let n = format!("dialog_batch_filled_{name}");
        results.push((n.clone(), shot(&n, p.clone(), d.clone(), env.clone(), |_| true)));

        // A real run: three fixtures and a file that is not FIT.
        let scratch = std::env::temp_dir().join(format!("callisto_lite_batch_{name}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&scratch);
        let (input, output) = (scratch.join("batch_in"), scratch.join("batch_out"));
        std::fs::create_dir_all(&input).unwrap();
        for f in [
            "BIR_20261007_101459_01.fit.gz",
            "INDIA-GAURI_20261007_041500_02.fit.gz",
            "GERMANY-DLR_20261007_050000_01.fit.gz",
        ] {
            std::fs::copy(lite_dir().join("parity/fixtures").join(f), input.join(f)).unwrap();
        }
        std::fs::write(input.join("notes.txt"), "not a FIT file").unwrap();
        d.borrow_mut().set_folders(&input.to_string_lossy(), &output.to_string_lossy());
        d.borrow_mut().set_options(false, 2, "inferno", false, false);
        let reference: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(lite_dir().join(format!("reference/windows/tools/batch_outputs_{name}.json")))
                .unwrap(),
        )
        .unwrap();
        let summary = reference["summaries"][0].clone();
        let n = format!("dialog_batch_done_{name}");
        d.borrow_mut().click_start();
        results.push((
            n.clone(),
            shot(&n, p.clone(), d.clone(), env.clone(), move |d| {
                if d.is_running() || d.last_summary.is_none() {
                    return false;
                }
                // The summary box, then the capture's folder texts.
                let notices = d.take_notices();
                assert_eq!(notices.len(), 1, "one summary box: {notices:?}");
                assert_eq!(notices[0].title, summary["title"].as_str().unwrap());
                assert_eq!(notices[0].text, summary["text"].as_str().unwrap());
                d.set_folders(&shown_in, &shown_out);
                true
            }),
        ));
        let mut outputs: Vec<String> = std::fs::read_dir(&output)
            .unwrap()
            .flatten()
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        outputs.sort();
        let want: Vec<String> =
            reference["outputs"].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_owned()).collect();
        assert_eq!(outputs, want, "the run wrote the full app's files");
        for f in &outputs {
            let lite = image::open(output.join(f)).unwrap().to_rgba8();
            let full = image::open(lite_dir().join(format!("reference/windows/tools/batch_png_{name}_{f}")))
                .unwrap()
                .to_rgba8();
            let label = format!("batch_png_{name}_{}", f.trim_end_matches(".png"));
            if (lite.width(), lite.height()) != (full.width(), full.height()) {
                println!(
                    "{label}: size {}x{}, full app {}x{}",
                    lite.width(),
                    lite.height(),
                    full.width(),
                    full.height()
                );
            }
            results.push((label.clone(), Some(compare_images(&label, &full, &lite))));
        }
        let _ = std::fs::remove_dir_all(&scratch);
    }
    for (n, r) in &results {
        match r {
            Some(d) => println!("{n}: {d:.2}% differ"),
            None => println!("{n}: no reference"),
        }
    }
    // The dialogs differ by ClearType against Lite's grey anti-aliasing. The
    // PNGs' text boxes come from FreeType's autohinter in the full app (whole
    // pixels high, advances moved by stem snapping), which Lite approximates
    // to under a pixel or two at 300 dpi: the noisy spectra shift with them.
    let limit = |name: &str| if name.starts_with("batch_png_") { 6.0 } else { 3.0 };
    let failing: Vec<_> = results.iter().filter(|(n, r)| r.is_some_and(|d| d >= limit(n))).collect();
    assert!(failing.is_empty(), "windows no longer match the full app: {failing:?}");
}
