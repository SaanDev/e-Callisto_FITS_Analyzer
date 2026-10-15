//! The status bar: temporary messages on the left; the update status, the
//! cursor readout and a size grip on the right (`MainWindow.__init__`).

use egui::{CornerRadius, Rect, Stroke, Ui, pos2, vec2};

use super::widgets;
use crate::fonts::Weight;
use crate::theme::Palette;

pub const HEIGHT: f32 = 22.0;

pub fn show(ui: &Ui, rect: Rect, message: Option<&str>, update_status: &str, cursor: &str, p: &Palette) {
    let painter = ui.painter();
    painter.rect_filled(rect, 0.0, p.surface_bg);
    painter.line_segment(
        [pos2(rect.left(), rect.top() + 0.5), pos2(rect.right(), rect.top() + 0.5)],
        Stroke::new(1.0, p.border),
    );

    let cy = rect.top() + 11.5;
    if let Some(message) = message {
        widgets::text_left(ui, rect.left() + 6.0, cy, message, 12.0, Weight::Regular, p.text);
    }

    // Permanent widgets, laid out from the right as QStatusBar does: the
    // size grip (17 px), the cursor label (padding-right 8), then the update
    // label (padding-right 12), with Qt's 7 px item spacing between them.
    let grip_left = rect.right() - 17.0;
    let cursor_w = widgets::text_width(ui, cursor, 12.0, Weight::Regular);
    let cursor_right = grip_left - 7.0 + 0.0;
    let cursor_left = cursor_right - cursor_w - 8.0 - 6.0;
    if !cursor.is_empty() {
        widgets::text_left(ui, cursor_left, cy, cursor, 12.0, Weight::Regular, p.text);
    }
    let update_w = widgets::text_width(ui, update_status, 12.0, Weight::Regular);
    let update_right = cursor_left - 6.0;
    widgets::text_left(
        ui,
        update_right - 12.0 - update_w - 10.0 / 3.0,
        cy,
        update_status,
        12.0,
        Weight::Regular,
        p.text,
    );

    size_grip(ui, Rect::from_min_size(pos2(grip_left, rect.bottom() - 17.0), vec2(17.0, 17.0)), p);
}

/// Fusion's size grip: ten 1 px dots, 3 px apart, in a triangle with its
/// right angle at the bottom right (measured on the full app).
fn size_grip(ui: &Ui, rect: Rect, p: &Palette) {
    let painter = ui.painter();
    let color = if p.dark { egui::Color32::from_rgb(0x69, 0x6e, 0x76) } else { egui::Color32::from_gray(0xc3) };
    for row in 0..4 {
        for col in 0..=row {
            let x = rect.right() - 3.0 - 3.0 * col as f32;
            let y = rect.top() + 5.0 + 3.0 * row as f32;
            painter.rect_filled(Rect::from_min_size(pos2(x, y), vec2(1.0, 1.0)), CornerRadius::ZERO, color);
        }
    }
}
