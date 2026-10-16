//! The main toolbar (`MainWindow._build_toolbar`): 36 px icons in 49 px
//! buttons, 6 px apart, three separators, and *Cite this Software* on the
//! right.

use egui::{CornerRadius, Id, Rect, Sense, Stroke, Ui, pos2, vec2};

use super::widgets;
use crate::fonts::Weight;
use crate::icons::Icon;
use crate::menus::Command;
use crate::session::ToolbarState;
use crate::theme::Palette;

pub const HEIGHT: f32 = 65.0;
const BUTTON: f32 = 49.0;
const ICON: f32 = 36.0;
const SPACING: f32 = 6.0;
const SEPARATOR: f32 = 6.0;

pub struct ToolButton {
    pub icon: Icon,
    pub tooltip: &'static str,
    pub command: Command,
    pub enabled: bool,
}

/// `None` marks a separator. Enable states follow `_sync_toolbar_enabled_states`.
pub fn buttons(t: &ToolbarState) -> Vec<Option<ToolButton>> {
    let b = |icon, tooltip, command, enabled| Some(ToolButton { icon, tooltip, command, enabled });
    vec![
        b(Icon::Open, "Open / Load", Command::Open, true),
        b(Icon::Download, "Download", Command::LaunchDownloader, true),
        b(Icon::Export, "Export", Command::ExportFigure, t.export),
        b(Icon::ExportFits, "Export as FITS", Command::ExportToFit, t.export),
        b(Icon::ExportProject, "Save Project", Command::SaveProject, t.save_project),
        None,
        b(Icon::Undo, "Undo", Command::Undo, t.undo),
        b(Icon::Redo, "Redo", Command::Redo, t.redo),
        None,
        b(Icon::Drift, "Estimate Drift Rate", Command::EstimateDriftRate, t.processed_tools),
        b(Icon::Isolate, "Isolate Burst", Command::IsolateBurst, t.processed_tools),
        b(Icon::Max, "Plot Maximum Intensities", Command::PlotMaximumIntensities, t.processed_tools),
        b(Icon::Zoom, "Rectangular Zooming", Command::RectangularZoom, t.rect_zoom),
        b(Icon::Lock, "Lock zooming and panning", Command::LockNavigation, t.lock),
        b(Icon::Unlock, "Unlock zooming and panning", Command::UnlockNavigation, t.unlock),
        None,
        b(Icon::ResetSelection, "Reset Selection", Command::ResetSelection, t.reset_selection),
        b(Icon::ResetToRaw, "Reset to Raw", Command::ResetToRaw, t.reset_to_raw),
        b(Icon::ResetAll, "Reset All", Command::ResetAll, t.reset_all),
    ]
}

pub fn show(ui: &Ui, rect: Rect, items: &[Option<ToolButton>], commands: &mut Vec<Command>, p: &Palette) {
    let painter = ui.painter();
    painter.rect_filled(rect, 0.0, p.surface_bg);
    painter.line_segment(
        [pos2(rect.left(), rect.bottom() - 0.5), pos2(rect.right(), rect.bottom() - 0.5)],
        Stroke::new(1.0, p.border),
    );

    let top = rect.top() + 8.0;
    let mut x = rect.left() + 8.0;
    for (i, item) in items.iter().enumerate() {
        if i > 0 {
            x += SPACING;
        }
        match item {
            None => {
                separator(ui, x, top, p);
                x += SEPARATOR;
            }
            Some(button) => {
                let r = Rect::from_min_size(pos2(x, top), vec2(BUTTON, BUTTON));
                tool_button(ui, r, button, commands, p);
                x += BUTTON;
            }
        }
    }

    // QPushButton#ToolbarCiteButton, right-aligned with the 8 px padding.
    let label = "Cite this Software";
    let cite = Rect::from_min_size(pos2(rect.right() - 8.0 - 121.0, rect.top() + 9.0), vec2(121.0, 46.0));
    let resp = ui.interact(cite, Id::new("cite_software"), Sense::click());
    let fill = if resp.is_pointer_button_down_on() {
        p.pressed
    } else if resp.hovered() {
        p.hover
    } else {
        p.surface_bg
    };
    widgets::qt_box(ui, cite, 10.0, fill, p.border);
    widgets::text_center(ui, cite.center(), label, 12.0, Weight::Regular, p.text);
    if resp.clicked() {
        commands.push(Command::CiteSoftware);
    }
    resp.on_hover_text("Open the citation window for this software");
}

fn tool_button(ui: &Ui, r: Rect, button: &ToolButton, commands: &mut Vec<Command>, p: &Palette) {
    let sense = if button.enabled { Sense::click() } else { Sense::hover() };
    let resp = ui.interact(r, Id::new(("toolbar", button.tooltip)), sense);
    if button.enabled && (resp.hovered() || resp.is_pointer_button_down_on()) {
        let fill = if resp.is_pointer_button_down_on() { p.pressed } else { p.hover };
        widgets::qt_box(ui, r, 10.0, fill, p.border);
    }
    let alpha = if button.enabled { 1.0 } else { p.disabled_icon_alpha };
    // Qt centres the icon 2/3 px up and left of the button centre, and puts
    // the pixmap on the next whole device pixel.
    let ppp = ui.ctx().pixels_per_point();
    let min = r.center() - vec2(2.0 / 3.0, 2.0 / 3.0) - vec2(ICON / 2.0, ICON / 2.0);
    let min = egui::pos2((min.x * ppp - 1e-3).ceil() / ppp, (min.y * ppp - 1e-3).ceil() / ppp);
    let icon = Rect::from_min_size(min, vec2(ICON, ICON));
    widgets::icon(ui, icon, button.icon, p.dark, alpha);
    if resp.clicked() {
        commands.push(button.command);
    }
    resp.on_hover_text(button.tooltip);
}

/// Fusion's toolbar separator: a 2 px dark line and a 1 px light line in
/// the palette's window colour.
fn separator(ui: &Ui, x: f32, top: f32, p: &Palette) {
    let painter = ui.painter();
    // Measured on the full app: dark from x+2.33 to x+3.67, light to x+4.33.
    let cx = x + 10.0 / 3.0;
    let (y0, y1) = (top + 16.0 / 3.0, top + 128.0 / 3.0);
    painter.rect_filled(
        Rect::from_min_max(pos2(cx - 1.0, y0), pos2(cx + 1.0 / 3.0, y1)),
        CornerRadius::ZERO,
        p.separator_dark(),
    );
    painter.rect_filled(
        Rect::from_min_max(pos2(cx + 1.0 / 3.0, y0), pos2(cx + 1.0, y1)),
        CornerRadius::ZERO,
        p.separator_light(),
    );
}
