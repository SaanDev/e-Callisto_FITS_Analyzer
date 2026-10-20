//! The analysis tools' dialogs against the full app's
//! (`lite/tools/capture_analysis.py`, `reference/windows/analysis/dialog_*`):
//! each dialog's contents drawn at the captured window size and 1.5×.
//!
//! Needs a GPU or software wgpu adapter, so it is opt-in:
//! `cargo test -p callisto-lite --test analysis_dialogs -- --ignored --nocapture`

use std::path::{Path, PathBuf};

use callisto_core::analysis::annotations::TextStyle;
use callisto_core::analysis::light_curve::LightCurveSettings;
use callisto_lite::fonts::SystemFonts;
use callisto_lite::gpu::GpuPreference;
use callisto_lite::theme::Palette;
use callisto_lite::ui::color_dialog::ColorDialog;
use callisto_lite::ui::dialogs::InputDialog;
use callisto_lite::ui::tool_dialogs::{FrequencyDialog, LightCurveSettingsDialog, TextAnnotationDialog, TextPurpose};
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

type Draw = Box<dyn FnMut(&mut egui::Ui)>;

struct TestApp {
    draw: Draw,
    p: Palette,
    frames: u32,
}

impl eframe::App for TestApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        if self.frames == 0 {
            // Fonts set now apply from the next frame.
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
fn analysis_dialogs_match_full_app() {
    let mut results = Vec::new();
    for (theme, dark) in [("light", false), ("dark", true)] {
        let p = Palette::new(dark, true);
        let ui_family = SystemFonts::load().ui_family().unwrap_or("Segoe UI").to_owned();
        let mut d = FrequencyDialog::new(30.0, 89.93800354003906, None);
        let pp = p.clone();
        results.push(("dialog_lc_frequency", theme, shot(&format!("dialog_lc_frequency_{theme}"), [252.0, 141.0], p.clone(), move |ui| {
            d.frame(ui, &pp);
        })));
        let mut d = LightCurveSettingsDialog::new(&LightCurveSettings::default());
        let pp = p.clone();
        results.push(("dialog_lc_settings", theme, shot(&format!("dialog_lc_settings_{theme}"), [420.0, 376.0], p.clone(), move |ui| {
            d.frame(ui, &pp);
        })));
        // The capture added a label before: the dialog starts from its style.
        let remembered = TextStyle { color: "#ffcc00".into(), font_family: String::new(), font_size: 16, font_bold: true, font_italic: false };
        let mut d = TextAnnotationDialog::new("Add Text Annotation", TextPurpose::Add, "", &remembered, &ui_family, Vec::new());
        let pp = p.clone();
        results.push(("dialog_text_annotation", theme, shot(&format!("dialog_text_annotation_{theme}"), [420.0, 305.0], p.clone(), move |ui| {
            d.frame(ui, &pp);
        })));
        let style = TextStyle { color: "#ffcc00".into(), font_family: String::new(), font_size: 16, font_bold: true, font_italic: false };
        let mut d = TextAnnotationDialog::new("Edit Text Label", TextPurpose::Edit(0), "Type III burst", &style, &ui_family, Vec::new());
        let pp = p.clone();
        results.push((
            "dialog_text_annotation_edit",
            theme,
            shot(&format!("dialog_text_annotation_edit_{theme}"), [420.0, 305.0], p.clone(), move |ui| {
                d.frame(ui, &pp);
            }),
        ));
        let mut d = ColorDialog::new("Choose light curve color", [0x00, 0xe5, 0xff, 255]);
        let pp = p.clone();
        results.push(("dialog_color", theme, shot(&format!("dialog_color_{theme}"), [653.0, 513.0], p.clone(), move |ui| {
            d.frame(ui, &pp);
        })));
        let labels = vec!["#1 Type III burst @ (600.00, 70.00)".to_owned(), "#3 Second label @ (100.00, 40.00)".to_owned()];
        let mut d = InputDialog::item("Move Text Label", "Choose label:", labels);
        let pp = p.clone();
        results.push(("dialog_choose_label", theme, shot(&format!("dialog_choose_label_{theme}"), [273.0, 138.0], p.clone(), move |ui| {
            d.frame(ui, &pp);
        })));
    }
    // Text is most of a small window: ClearType against Lite's grey
    // anti-aliasing weighs more there (the import progress dialog, 200 x 70,
    // is at 5 % for the same reason).
    let limit = |name: &str| if name == "dialog_lc_frequency" || name == "dialog_choose_label" { 7.0 } else { 4.0 };
    let mut failing = Vec::new();
    for (name, theme, d) in &results {
        match d {
            Some(d) => {
                println!("{name} {theme}: {d:.2}% of pixels differ by more than 32 levels");
                if *d >= limit(name) {
                    failing.push(format!("{name} {theme}"));
                }
            }
            None => println!("{name} {theme}: no reference"),
        }
    }
    assert!(failing.is_empty(), "dialogs no longer match the full app: {failing:?}");
}
