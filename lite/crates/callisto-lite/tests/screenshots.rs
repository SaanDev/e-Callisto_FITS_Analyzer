//! Renders Lite's main window offscreen and compares it with the full app's
//! reference screenshots in `lite/reference/windows` (1600 × 1000 logical px
//! at 1.5×, captured by `lite/tools/capture_reference.py` on a PC set to the
//! dark scheme).
//!
//! Writes, per theme, to `lite/reference/compare/`:
//! `lite_<name>.png`, `side_by_side_<name>.png` (full app left, Lite right)
//! and `diff_<name>.png` (bright = different).
//!
//! Needs a GPU or software wgpu adapter, so it is opt-in:
//! `cargo test -p callisto-lite --test screenshots -- --ignored --nocapture`

use std::path::{Path, PathBuf};

use callisto_lite::app::{LiteApp, Settings};
use callisto_lite::gpu::GpuPreference;
use callisto_lite::theme::ThemeMode;
use egui_kittest::Harness;
use image::{Rgba, RgbaImage};

fn lite_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn harness(theme: ThemeMode, setup: impl FnOnce(&mut LiteApp)) -> Harness<'static, LiteApp> {
    // CALLISTO_LITE_GPU=software reproduces a CI runner, which has no GPU.
    harness_with(theme, GpuPreference::from_env(), setup)
}

fn harness_with(theme: ThemeMode, gpu: GpuPreference, setup: impl FnOnce(&mut LiteApp)) -> Harness<'static, LiteApp> {
    let mut setup = Some(setup);
    let mut h = Harness::builder()
        .with_size([1600.0, 1000.0])
        .with_pixels_per_point(1.5)
        .wgpu_setup(callisto_lite::gpu::wgpu_options(gpu).wgpu_setup)
        .build_eframe(move |cc| {
            let mut app = LiteApp::with_settings(&cc.egui_ctx, Settings { theme_mode: theme, ..Default::default() });
            // The references were captured with Windows in dark mode.
            app.os_dark_override = Some(true);
            // No archive probing: the screenshots must not depend on the network.
            app.session.set_archive(None, false);
            if let Some(f) = setup.take() {
                f(&mut app);
            }
            app
        });
    // Let fonts and SVG icons load.
    for _ in 0..6 {
        h.step();
    }
    h.run();
    h
}

struct Comparison {
    differing: f64,
    mean_abs: f64,
}

fn compare(name: &str, lite: &RgbaImage, reference: &Path) -> Option<Comparison> {
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
    let (mut differing, mut total) = (0u64, 0u64);
    for y in 0..h {
        for x in 0..w {
            let a = reference.get_pixel(x, y);
            let b = lite.get_pixel(x, y);
            let d = (0..3).map(|c| (a[c] as i32 - b[c] as i32).unsigned_abs()).max().unwrap();
            total += d as u64;
            if d > 32 {
                differing += 1;
            }
            let v = (d * 4).min(255) as u8;
            diff.put_pixel(x, y, Rgba([v, v, v, 255]));
        }
    }
    diff.save(out.join(format!("diff_{name}.png"))).unwrap();
    let n = (w * h) as f64;
    Some(Comparison { differing: differing as f64 / n * 100.0, mean_abs: total as f64 / n })
}

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn main_window_matches_full_app() {
    for (theme, name) in [(ThemeMode::Light, "light"), (ThemeMode::Dark, "dark")] {
        let mut h = harness(theme, |_| {});
        let img = h.render().expect("render");
        // The full app's empty canvas, rendered from its scene (capture_states.py).
        let reference = lite_dir().join(format!("reference/windows/state_empty_{name}.png"));
        if let Some(c) = compare(&format!("main_{name}"), &img, &reference) {
            println!(
                "main {name}: {:.2}% of pixels differ by more than 32 levels; mean difference {:.2}",
                c.differing, c.mean_abs
            );
            // Today 0.7-0.8 %, mostly text anti-aliasing (Qt uses ClearType)
            // and the shorter sidebar (no geomagnetic card in Lite).
            assert!(c.differing < 2.0, "main window ({name}) no longer matches the full app");
        }
    }
}

/// The software adapter (WARP on Windows, llvmpipe on Linux) must draw the
/// same window as the GPU, for PCs without usable graphics drivers.
#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn software_adapter_draws_the_same() {
    let gpu = harness_with(ThemeMode::Light, GpuPreference::Hardware, |_| {}).render().expect("GPU render");
    let soft = harness_with(ThemeMode::Light, GpuPreference::Software, |_| {}).render().expect("software render");
    let reference = lite_dir().join("reference/compare/lite_main_gpu.png");
    gpu.save(&reference).unwrap();
    let c = compare("main_software_vs_gpu", &soft, &reference).unwrap();
    println!("software vs GPU: {:.3}% differ; mean {:.3}", c.differing, c.mean_abs);
    assert!(c.differing < 0.05, "the software adapter draws differently");
}

/// Menus whose items are the same in Lite as in the full app, with the
/// x position of their menu-bar item (from the captured reference).
const SAME_MENUS: &[(&str, u32)] = &[("File", 13), ("Edit", 57), ("Download", 104), ("About", 478), ("Help", 537)];

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn menus_match_full_app() {
    for (theme, name) in [(ThemeMode::Light, "light"), (ThemeMode::Dark, "dark")] {
        let menus = callisto_lite::menus::menu_bar(&callisto_lite::menus::MenuState {
            theme,
            prefetch_adjacent: true,
            auto_clean_max_outliers: true,
            ..Default::default()
        });
        for &(title, x) in SAME_MENUS {
            let mut h = harness(theme, |app| app.menubar.open_path(&menus, &[title]));
            let img = h.render().expect("render");
            let slug = title.to_lowercase();
            let reference = lite_dir().join(format!("reference/windows/menu_{slug}_{name}.png"));
            let Ok(ref_img) = image::open(&reference) else { continue };
            // Qt's popup opens at the menu-bar item's bottom-left: (x, 31).
            let (px, py) = ((x as f32 * 1.5).round() as u32, (31.0 * 1.5_f32).round() as u32);
            let popup = image::imageops::crop_imm(&img, px, py, ref_img.width(), ref_img.height()).to_image();
            if let Some(c) = compare(&format!("menu_{slug}_{name}"), &popup, &reference) {
                println!("menu {title} {name}: {:.2}% differ; mean {:.2}", c.differing, c.mean_abs);
                // Today 4-9 %, most of it the popup's rounded corners, which
                // Qt's capture fills with white.
                assert!(c.differing < 12.0, "menu {title} ({name}) no longer matches the full app");
            }
        }
    }
}

/// A captured state and how to get there from the previous one.
type Step<'a> = (&'a str, Box<dyn Fn(&mut LiteApp) + 'a>);

fn fixture(name: &str) -> String {
    lite_dir().join("parity/fixtures").join(name).to_string_lossy().into_owned()
}

/// The data states `lite/tools/capture_states.py` drives the full app
/// through, in the same order (the canvas sizes its axes from the text it
/// drew before, so the order matters).
#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn data_states_match_full_app() {
    use callisto_core::radio::processing::BackgroundMethod;
    let gauri = [fixture("INDIA-GAURI_20261007_041500_02.fit.gz"), fixture("INDIA-GAURI_20261007_043000_02.fit.gz")];
    for (theme, name) in [(ThemeMode::Light, "light"), (ThemeMode::Dark, "dark")] {
        let mut h = harness(theme, |_| {});
        let steps: [Step<'_>; 7] = [
            ("empty", Box::new(|_| {})),
            (
                "single",
                Box::new(|app| {
                    app.open_paths(&gauri[..1]);
                }),
            ),
            (
                "combined_time",
                Box::new(|app| {
                    app.open_paths(&gauri);
                }),
            ),
            (
                "bg_mean",
                Box::new(|app| {
                    app.session.set_background_method(BackgroundMethod::Mean);
                    app.session.apply_background_subtraction();
                }),
            ),
            ("units_db", Box::new(|app| app.session.set_units_mode(true))),
            ("time_ut", Box::new(|app| app.session.set_time_axis(true))),
            ("axis_log", Box::new(|app| app.session.set_frequency_axis_log(true))),
        ];
        for (state, action) in steps {
            action(h.state_mut());
            h.run();
            let img = h.render().expect("render");
            let reference = lite_dir().join(format!("reference/windows/state_{state}_{name}.png"));
            if let Some(c) = compare(&format!("state_{state}_{name}"), &img, &reference) {
                println!("{state} {name}: {:.2}% differ by more than 32 levels; mean {:.2}", c.differing, c.mean_abs);
                // Today 0.8-1.1 %: Qt's ClearType text and kerning, and the
                // sidebar cards Lite does not have (Geomagnetic Response).
                assert!(c.differing < 2.0, "{state} ({name}) no longer matches the full app");
            }
        }
    }
}
