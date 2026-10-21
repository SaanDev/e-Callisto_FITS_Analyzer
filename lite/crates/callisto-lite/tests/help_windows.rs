//! The help windows against the full app's (`lite/tools/capture_help.py`,
//! `reference/windows/help`): the user guide's text layout block by block,
//! the message boxes' sizes, and (opt-in, with wgpu) each window drawn at
//! the captured size and 1.5×:
//! `cargo test -p callisto-lite --test help_windows -- --ignored --nocapture`

use std::path::{Path, PathBuf};

use callisto_core::updates::UpdateCheck;
use callisto_lite::fonts::SystemFonts;
use callisto_lite::gpu::GpuPreference;
use callisto_lite::help::bug_report::BugReportDialog;
use callisto_lite::help::cite::CitationDialog;
use callisto_lite::help::guide::UserGuide;
use callisto_lite::help::{about_box, updates};
use callisto_lite::theme::Palette;
use callisto_lite::ui::message_box::{Icon, MessageBox};
use callisto_lite::ui::qt;
use egui_kittest::Harness;
use image::{Rgba, RgbaImage};

fn lite_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn reference(name: &str) -> Option<serde_json::Value> {
    let text = std::fs::read_to_string(lite_dir().join(format!("reference/windows/help/{name}.json"))).ok()?;
    serde_json::from_str(&text).ok()
}

/// Runs `f` in a context with the system fonts at 1.5×, as the captures.
fn with_fonts<R>(f: impl FnOnce(&egui::Context) -> R) -> R {
    let ctx = egui::Context::default();
    let input = || egui::RawInput {
        viewports: std::iter::once((
            egui::ViewportId::ROOT,
            egui::ViewportInfo { native_pixels_per_point: Some(1.5), ..Default::default() },
        ))
        .collect(),
        ..Default::default()
    };
    let mut out = ctx.run_ui(input(), |ui| SystemFonts::load().install(ui.ctx()).map(|_| ()).unwrap_or(()));
    out.textures_delta.clear();
    let mut result = None;
    let mut f = Some(f);
    let mut out = ctx.run_ui(input(), |ui| {
        if let Some(f) = f.take() {
            result = Some(f(ui.ctx()));
        }
    });
    out.textures_delta.clear();
    result.unwrap()
}

#[test]
fn guide_layout_matches_full_app() {
    let Some(r) = reference("window_user_guide_light") else {
        eprintln!("no reference: run lite/tools/capture_help.py");
        return;
    };
    let width = r["viewport"][0].as_f64().unwrap() as f32;
    let qt: Vec<(String, [f32; 4], usize)> = r["blocks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|b| {
            let rect: Vec<f32> = b["rect"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap() as f32).collect();
            (
                b["text"].as_str().unwrap().to_owned(),
                [rect[0], rect[1], rect[2], rect[3]],
                b["lines"].as_u64().unwrap() as usize,
            )
        })
        // The empty blocks Qt keeps after each table.
        .filter(|(t, rect, _)| !(t.is_empty() && rect[0] > 4.0))
        .collect();
    let (lite, height) = with_fonts(|ctx| {
        let mut g = UserGuide::new();
        (g.block_boxes(ctx, width, false), g.document_height(ctx, width))
    });
    let mut bad = Vec::new();
    for (i, ((text, rect, lines), b)) in qt.iter().zip(&lite).enumerate() {
        let start: String = b.text.chars().take(20).collect();
        let qt_start: String = text.chars().take(20).collect();
        if start != qt_start {
            bad.push(format!("#{i}: text {qt_start:?} vs {start:?}"));
            break;
        }
        if (b.rect.top() - rect[1]).abs() > 0.6 || (b.rect.height() - rect[3]).abs() > 0.6 || b.lines != *lines {
            bad.push(format!(
                "#{i} {qt_start:?}: y {} h {} lines {} vs y {} h {} lines {}",
                rect[1],
                rect[3],
                lines,
                b.rect.top(),
                b.rect.height(),
                b.lines
            ));
        }
    }
    assert_eq!(qt.len(), lite.len(), "block count");
    let qt_h = r["document_size"][1].as_f64().unwrap() as f32;
    assert!((height - qt_h).abs() < 0.6, "document height {height} vs {qt_h}");
    assert!(bad.is_empty(), "{} blocks differ:\n{}", bad.len(), bad.join("\n"));
}

fn update_check() -> UpdateCheck {
    UpdateCheck {
        update_available: true,
        current_version: "1.0.0".into(),
        latest_version: "1.1.0".into(),
        release_name: "Lite 1.1.0".into(),
        release_url: "https://github.com/SaanDev/e-Callisto_FITS_Analyzer/releases/tag/lite-v1.1.0".into(),
        download_url: "https://example.invalid/e-CALLISTO_FITS_Analyzer_Lite_v1.1.0.exe".into(),
        published_at: "2026-11-02T10:00:00Z".into(),
        notes: "## What's New\n- Faster loading of combined files\n- Fixes for the Type II window".into(),
    }
}

/// The boxes as the capture builds them.
fn boxes() -> Vec<(&'static str, MessageBox)> {
    let mut details = updates::available_box(&update_check());
    details.set_details_shown(true);
    vec![
        ("box_guide_not_found", MessageBox::ok(Icon::Information, "Find", "\"burst ridge\" was not found.")),
        ("box_copied", MessageBox::ok(Icon::Information, "Copied", "Citation text copied to clipboard.")),
        ("box_about", about_box()),
        ("box_update_available", updates::available_box(&update_check())),
        ("box_update_available_details", details),
        ("box_up_to_date", updates::up_to_date_box()),
        (
            "box_update_failed",
            MessageBox::ok(
                Icon::Warning,
                "Update Check Failed",
                "Could not check for updates.\n\nNetwork error while checking updates: timed out",
            ),
        ),
        (
            "box_update_downloaded",
            updates::downloaded_box(Path::new("C:\\Users\\me\\Downloads\\e-CALLISTO_FITS_Analyzer_Lite_v1.1.0.exe")),
        ),
    ]
}

fn reference_size(name: &str) -> Option<(i32, i32)> {
    let r = reference(&format!("{name}_light"))?;
    Some((r["size"][0].as_i64()? as i32, r["size"][1].as_i64()? as i32))
}

#[test]
fn message_boxes_are_sized_as_the_full_app() {
    let mut bad = Vec::new();
    with_fonts(|ctx| {
        for (name, b) in boxes() {
            let Some(want) = reference_size(name) else { continue };
            let got = b.size(ctx);
            // A label's width is its text's, rounded up: Qt's DirectWrite
            // advances and egui's differ by a fraction of a pixel.
            if (got.w - want.0).abs() > 1 || got.h != want.1 {
                bad.push(format!("{name}: {}x{} vs {}x{}", got.w, got.h, want.0, want.1));
            }
        }
    });
    assert!(bad.is_empty(), "{}", bad.join("\n"));
}

fn compare(name: &str, lite: &RgbaImage) -> Option<f64> {
    let out = lite_dir().join("reference/compare");
    std::fs::create_dir_all(&out).unwrap();
    lite.save(out.join(format!("lite_{name}.png"))).unwrap();
    let reference = image::open(lite_dir().join(format!("reference/windows/help/{name}.png"))).ok()?.to_rgba8();
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

fn shot(name: &str, p: Palette, draw: impl FnMut(&mut egui::Ui) + 'static) -> Option<f64> {
    let base = name.trim_end_matches("_light").trim_end_matches("_dark");
    let (w, h) = reference_size(base)?;
    let app = TestApp { draw: Box::new(draw), p, frames: 0 };
    let mut h = Harness::builder()
        .with_size([w as f32, h as f32])
        .with_pixels_per_point(1.5)
        .wgpu_setup(callisto_lite::gpu::wgpu_options(GpuPreference::from_env()).wgpu_setup)
        .build_eframe(move |_| app);
    for _ in 0..6 {
        h.step();
    }
    let img = h.render().expect("render");
    compare(name, &img)
}

type Setup = fn(&mut UserGuide);

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn help_windows_match_full_app() {
    let mut results = Vec::new();
    for (theme, dark) in [("light", false), ("dark", true)] {
        let p = Palette::new(dark, true);
        let guide_states: [(&str, Setup); 4] = [
            ("window_user_guide", |_| {}),
            ("window_user_guide_section", |g| g.show_anchor("menu-file")),
            ("window_user_guide_find", |g| g.find_text("ruler")),
            ("window_user_guide_find_next", |g| g.find_text("ruler")),
        ];
        for (i, (name, setup)) in guide_states.into_iter().enumerate() {
            let mut g = UserGuide::new();
            let pp = p.clone();
            let mut frames = 0;
            results.push((
                name,
                theme,
                shot(&format!("{name}_{theme}"), p.clone(), move |ui| {
                    // Find Next once more for the second match, a frame after the first.
                    if frames == 0 || (i == 3 && frames == 1) {
                        setup(&mut g);
                    }
                    frames += 1;
                    g.frame(ui, &pp);
                }),
            ));
        }
        let mut c = CitationDialog::new();
        let pp = p.clone();
        results.push((
            "dialog_cite",
            theme,
            shot(&format!("dialog_cite_{theme}"), p.clone(), move |ui| {
                c.frame(ui, &pp);
            }),
        ));
        let provider = || (Default::default(), callisto_core::pyjson::Json::object(), String::new());
        let mut b = BugReportDialog::new("SaanDev/e-Callisto_FITS_Analyzer");
        let pp = p.clone();
        results.push((
            "dialog_bug_report",
            theme,
            shot(&format!("dialog_bug_report_{theme}"), p.clone(), move |ui| {
                b.frame(ui, &pp, &provider);
            }),
        ));
        let mut b = BugReportDialog::new("SaanDev/e-Callisto_FITS_Analyzer");
        b.title = "Crash when exporting PNG".into();
        b.details = "1. Opened a file\n2. Pressed Ctrl+E\n3. The app closed".into();
        b.set_bundle(Some(PathBuf::from("C:\\Users\\me\\Downloads\\bug_report_20261021_101500.zip")));
        let pp = p.clone();
        results.push((
            "dialog_bug_report_filled",
            theme,
            shot(&format!("dialog_bug_report_filled_{theme}"), p.clone(), move |ui| {
                b.frame(ui, &pp, &provider);
            }),
        ));
        for (name, mut b) in boxes() {
            let pp = p.clone();
            results.push((
                name,
                theme,
                shot(&format!("{name}_{theme}"), p.clone(), move |ui| {
                    b.frame(ui, &pp);
                }),
            ));
        }
        for (value, status) in callisto_lite::ui::splash::STEPS {
            let name = format!("splash_{value}");
            results.push((
                Box::leak(name.clone().into_boxed_str()),
                theme,
                shot(&format!("{name}_{theme}"), p.clone(), move |ui| {
                    callisto_lite::ui::splash::draw(ui, value, status);
                }),
            ));
        }
        for (name, label, progress) in [
            ("dialog_update_download", "Downloading update...", qt::Progress::busy()),
            (
                "dialog_update_download_progress",
                "Downloading update... 38.0% (19.0 MB / 50.0 MB)",
                qt::Progress { min: 0, max: 52_428_800, value: 19_922_944, format: "%p%".into() },
            ),
        ] {
            let pp = p.clone();
            results.push((
                name,
                theme,
                shot(&format!("{name}_{theme}"), p.clone(), move |ui| {
                    qt::with_app_style(|| updates::download_dialog_body(ui, label, &progress, true, &pp));
                }),
            ));
        }
    }
    let mut worst: f64 = 0.0;
    for (name, theme, r) in &results {
        match r {
            Some(d) => {
                println!("{name} {theme}: {d:.2}% differ");
                worst = worst.max(*d);
            }
            None => println!("{name} {theme}: no reference"),
        }
    }
    assert!(worst < 15.0, "a help window no longer matches the full app ({worst:.2}%)");
}
