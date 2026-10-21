//! The startup splash (`StartupLoadingScreen`): a 540 × 300 gradient card
//! with the logo, the name and version, a summary, the loading status and
//! a progress bar, shown while the main window gets ready (rectangles
//! measured on the full app, see `lite/tools/capture_help.py`).

use egui::{Color32, Rect, Stroke, StrokeKind, Ui, pos2, vec2};

use crate::fonts::{Weight, ui_font};

pub const SIZE: egui::Vec2 = egui::vec2(540.0, 300.0);

/// The loading steps `_run_main_mode` reports.
pub const STEPS: [(i32, &str); 4] = [
    (15, "Loading analysis modules..."),
    (45, "Applying interface styling..."),
    (80, "Preparing the main workspace..."),
    (100, "Opening analyzer..."),
];

const SUMMARY: &str = "Preparing plotting tools, project recovery, and analysis controls for the main workspace.";

fn rgba(r: u8, g: u8, b: u8, a: f32) -> Color32 {
    Color32::from_rgba_unmultiplied(r, g, b, (a * 255.0).round() as u8)
}

fn lerp(a: Color32, b: Color32, t: f32) -> Color32 {
    let m = |x: u8, y: u8| (x as f32 + (y as f32 - x as f32) * t).round() as u8;
    Color32::from_rgb(m(a.r(), b.r()), m(a.g(), b.g()), m(a.b(), b.b()))
}

/// The diagonal gradient (`x1: 0, y1: 0, x2: 1, y2: 1`; stops #07131f,
/// #123049 at 0.55, #21607a).
fn gradient(t: f32) -> Color32 {
    let (a, b, c) =
        (Color32::from_rgb(0x07, 0x13, 0x1f), Color32::from_rgb(0x12, 0x30, 0x49), Color32::from_rgb(0x21, 0x60, 0x7a));
    if t < 0.55 { lerp(a, b, t / 0.55) } else { lerp(b, c, (t - 0.55) / 0.45) }
}

fn text(ui: &Ui, x: f32, center_y: f32, s: &str, px: f32, weight: Weight, color: Color32) {
    super::widgets::text_left(ui, x, center_y, s, px, weight, color);
}

/// Lines of `s` wrapped at `width` in `font`.
fn wrap(ui: &Ui, s: &str, px: f32, weight: Weight, width: f32) -> Vec<String> {
    let font = ui_font(px, weight);
    let mut lines = Vec::new();
    let mut line = String::new();
    for word in s.split(' ') {
        let candidate = if line.is_empty() { word.to_owned() } else { format!("{line} {word}") };
        let w = ui.ctx().fonts_mut(|f| f.layout_no_wrap(candidate.clone(), font.clone(), Color32::WHITE).size().x);
        if !line.is_empty() && w > width {
            lines.push(std::mem::replace(&mut line, word.to_owned()));
        } else {
            line = candidate;
        }
    }
    lines.push(line);
    lines
}

/// Draws the splash filling `ui`'s rectangle, at `progress` with `status`.
pub fn draw(ui: &Ui, progress: i32, status: &str) {
    let r = ui.max_rect();
    let at = |x: f32, y: f32, w: f32, h: f32| Rect::from_min_size(pos2(r.left() + x, r.top() + y), vec2(w, h));
    let painter = ui.painter();
    // The gradient, as a mesh of strips across the diagonal.
    let mut mesh = egui::Mesh::default();
    let n = 24;
    for i in 0..=n {
        for j in 0..=n {
            let (u, v) = (i as f32 / n as f32, j as f32 / n as f32);
            // In the box's own unit square (ObjectBoundingMode): halfway along both axes.
            let (w, h) = (r.width(), r.height());
            let t = (u + v) / 2.0;
            mesh.colored_vertex(pos2(r.left() + u * w, r.top() + v * h), gradient(t));
        }
    }
    for i in 0..n {
        for j in 0..n {
            let k = |i: u32, j: u32| i * (n + 1) + j;
            let (a, b, c, d) = (k(i, j), k(i + 1, j), k(i + 1, j + 1), k(i, j + 1));
            mesh.add_triangle(a, b, c);
            mesh.add_triangle(a, c, d);
        }
    }
    painter.add(mesh);
    painter.rect_stroke(r.shrink(0.5), 24.0, Stroke::new(1.0, rgba(255, 255, 255, 0.14)), StrokeKind::Middle);

    // The logo in its rounded box.
    let logo = at(28.0, 28.0, 108.0, 108.0);
    painter.rect_filled(logo, 20.0, rgba(255, 255, 255, 0.08));
    painter.rect_stroke(logo.shrink(0.5), 20.0, Stroke::new(1.0, rgba(255, 255, 255, 0.12)), StrokeKind::Middle);
    if let Some(tex) = logo_texture(ui.ctx()) {
        painter.image(
            tex.id(),
            Rect::from_center_size(logo.center(), vec2(78.0, 78.0)),
            Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)),
            Color32::WHITE,
        );
    }

    let x = r.left() + 154.0;
    text(ui, x, r.top() + 28.0 + 7.5, "Solar Radio Burst Toolkit", 11.0, Weight::Semibold, rgba(225, 241, 255, 0.72));
    let title = crate::APP_NAME;
    let title_lines = wrap(ui, title, 22.0, Weight::Bold, 358.0);
    let line_h = 30.0;
    let top = r.top() + 49.0 + (64.0 - line_h * title_lines.len() as f32) / 2.0;
    for (k, l) in title_lines.iter().enumerate() {
        text(ui, x, top + line_h * (k as f32 + 0.5), l, 22.0, Weight::Bold, Color32::from_rgb(0xf5, 0xfb, 0xff));
    }
    text(
        ui,
        x,
        r.top() + 119.0 + 9.0,
        &format!("Version {}", crate::APP_VERSION),
        13.0,
        Weight::Semibold,
        Color32::from_rgb(0xcd, 0xe7, 0xf5),
    );
    for (k, l) in wrap(ui, SUMMARY, 13.0, Weight::Regular, 358.0).iter().enumerate() {
        text(ui, x, r.top() + 149.0 + 18.0 * (k as f32 + 0.5), l, 13.0, Weight::Regular, rgba(233, 244, 251, 0.88));
    }
    text(
        ui,
        r.left() + 28.0,
        r.top() + 222.0 + 8.0,
        status,
        12.0,
        Weight::Semibold,
        Color32::from_rgb(0xf5, 0xfb, 0xff),
    );

    let bar = at(28.0, 259.0, 484.0, 20.0);
    painter.rect_filled(bar, 7.0, rgba(255, 255, 255, 0.14));
    painter.rect_stroke(bar.shrink(0.5), 7.0, Stroke::new(1.0, rgba(255, 255, 255, 0.18)), StrokeKind::Middle);
    let frac = progress.clamp(0, 100) as f32 / 100.0;
    if frac > 0.0 {
        let inner = bar.shrink(1.0);
        let chunk = Rect::from_min_size(inner.min, vec2((inner.width() * frac).floor(), inner.height()));
        painter.rect_filled(chunk, 7.0, Color32::from_rgb(0x8e, 0xe4, 0xff));
    }
}

fn logo_texture(ctx: &egui::Context) -> Option<egui::TextureHandle> {
    let id = egui::Id::new("splash_logo");
    if let Some(t) = ctx.data(|d| d.get_temp::<egui::TextureHandle>(id)) {
        return Some(t);
    }
    let icon = crate::icons::app_icon()?;
    let img = image::RgbaImage::from_raw(icon.width, icon.height, icon.rgba)?;
    let px = (78.0 * ctx.pixels_per_point()).round() as u32;
    let img = image::imageops::resize(&img, px, px, image::imageops::FilterType::Lanczos3);
    let color = egui::ColorImage::from_rgba_unmultiplied([px as usize, px as usize], img.as_raw());
    let t = ctx.load_texture("splash_logo", color, egui::TextureOptions::LINEAR);
    ctx.data_mut(|d| d.insert_temp(id, t.clone()));
    Some(t)
}
