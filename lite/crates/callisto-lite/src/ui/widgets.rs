//! Controls drawn to the full app's stylesheet geometry.
//!
//! egui's built-in widgets have their own sizes and shapes, so the controls
//! that appear in the main window are painted here directly, with the
//! paddings, radii and colours of the full app's Qt stylesheets
//! (`theme_manager.py` and `main_window.py`, see `crate::theme`).

use egui::{Color32, CornerRadius, Id, Pos2, Rect, Response, Sense, Shape, Stroke, StrokeKind, Ui, pos2, vec2};

use crate::fonts::{Weight, ui_font};
use crate::icons::Icon;
use crate::theme::Palette;

pub fn text_width(ui: &Ui, text: &str, px: f32, weight: Weight) -> f32 {
    ui.ctx().fonts_mut(|f| f.layout_no_wrap(text.to_owned(), ui_font(px, weight), Color32::WHITE).size().x)
}

/// Draw one line of text the way Qt places it: centred on `center_y` using
/// the font's own (unrounded) line height, with the baseline rounded to a
/// device pixel. egui would round the galley's top instead, which puts
/// text a pixel off whenever a widget sits on a half device pixel.
#[allow(clippy::too_many_arguments)]
fn qt_text(
    ui: &Ui,
    x: f32,
    center_y: f32,
    align: egui::Align,
    text: &str,
    px: f32,
    weight: Weight,
    color: Color32,
) -> Rect {
    qt_text_clipped(ui, None, x, center_y, align, text, px, weight, color)
}

#[allow(clippy::too_many_arguments)]
fn qt_text_clipped(
    ui: &Ui,
    clip: Option<Rect>,
    x: f32,
    center_y: f32,
    align: egui::Align,
    text: &str,
    px: f32,
    weight: Weight,
    color: Color32,
) -> Rect {
    let font = ui_font(px, weight);
    let galley = ui.ctx().fonts_mut(|f| f.layout_no_wrap(text.to_owned(), font.clone(), color));
    let ppp = ui.ctx().pixels_per_point();
    let (mut height, mut ascent, baseline_in_galley) =
        match galley.rows.first().and_then(|r| r.row.glyphs.first().map(|g| (r, g))) {
            Some((row, g)) => (g.font_height, g.font_ascent, row.pos.y + g.pos.y),
            None => (galley.size().y, galley.size().y * 0.8, galley.size().y * 0.8),
        };
    if let Some(m) = crate::fonts::qt_metrics(&font) {
        (height, ascent) = (m.height(), m.ascent);
    }
    let baseline = ((center_y - height / 2.0 + ascent) * ppp).round();
    let top = (baseline - (baseline_in_galley * ppp).round()) / ppp;
    let width = galley.size().x;
    let left = match align {
        egui::Align::Min => x,
        egui::Align::Center => x - width / 2.0,
        egui::Align::Max => x - width,
    };
    let rect = Rect::from_min_size(pos2(left, top), galley.size());
    match clip {
        Some(c) => ui.painter().with_clip_rect(c.intersect(ui.clip_rect())).galley(rect.min, galley, color),
        None => ui.painter().galley(rect.min, galley, color),
    }
    rect
}

/// [`text_left`], clipped to `clip`.
#[allow(clippy::too_many_arguments)]
pub fn text_left_clipped(
    ui: &Ui,
    clip: Rect,
    x: f32,
    center_y: f32,
    text: &str,
    px: f32,
    weight: Weight,
    color: Color32,
) -> Rect {
    qt_text_clipped(ui, Some(clip), x, center_y, egui::Align::Min, text, px, weight, color)
}

/// Text with its left edge at `x`, centred vertically on `center_y`.
/// Text with a Qt mnemonic (`&File`): the `&` dropped and the letter after
/// it underlined, one device pixel below the baseline. 12 px regular.
pub fn mnemonic_text(ui: &Ui, x: f32, center_y: f32, align: egui::Align, text: &str, color: Color32) -> Rect {
    let (plain, at) = match text.find('&') {
        Some(i) if i + 1 < text.len() => (format!("{}{}", &text[..i], &text[i + 1..]), Some(i)),
        _ => (text.to_owned(), None),
    };
    let rect = qt_text(ui, x, center_y, align, &plain, 12.0, Weight::Regular, color);
    if let Some(i) = at {
        let font = ui_font(12.0, Weight::Regular);
        let width = |t: &str| ui.ctx().fonts_mut(|f| f.layout_no_wrap(t.to_owned(), font.clone(), color)).size().x;
        let ch_len = plain[i..].chars().next().map_or(1, char::len_utf8);
        let (x0, x1) = (rect.left() + width(&plain[..i]), rect.left() + width(&plain[..i + ch_len]));
        let ppp = ui.ctx().pixels_per_point();
        let (height, ascent) = crate::fonts::qt_metrics(&font).map_or((16.0, 13.0), |m| (m.height(), m.ascent));
        let baseline = ((center_y - height / 2.0 + ascent) * ppp).round();
        let snap = |v: f32| (v * ppp).round() / ppp;
        let line = Rect::from_min_max(pos2(snap(x0), (baseline + 1.0) / ppp), pos2(snap(x1), (baseline + 2.0) / ppp));
        ui.painter()
            .add(egui::Shape::Rect(egui::epaint::RectShape::filled(line, 0.0, color).with_round_to_pixels(false)));
    }
    rect
}

pub fn text_left(ui: &Ui, x: f32, center_y: f32, text: &str, px: f32, weight: Weight, color: Color32) -> Rect {
    qt_text(ui, x, center_y, egui::Align::Min, text, px, weight, color)
}

pub fn text_right(ui: &Ui, x: f32, center_y: f32, text: &str, px: f32, weight: Weight, color: Color32) -> Rect {
    qt_text(ui, x, center_y, egui::Align::Max, text, px, weight, color)
}

pub fn text_center(ui: &Ui, center: Pos2, text: &str, px: f32, weight: Weight, color: Color32) -> Rect {
    qt_text(ui, center.x, center.y, egui::Align::Center, text, px, weight, color)
}

/// The line height Qt centres text with: the font's ascent + descent.
pub fn line_height(ui: &Ui, px: f32, weight: Weight) -> f32 {
    if let Some(m) = crate::fonts::qt_metrics(&ui_font(px, weight)) {
        return m.height();
    }
    let galley = ui.ctx().fonts_mut(|f| f.layout_no_wrap("0".to_owned(), ui_font(px, weight), Color32::WHITE));
    galley.rows.first().and_then(|r| r.row.glyphs.first()).map_or(px * 1.33, |g| g.font_height)
}

/// A filled rounded rectangle with a 1 px border drawn inside it, like a
/// Qt stylesheet box (`border: 1px solid …; border-radius: …`).
pub fn qt_box(ui: &Ui, rect: Rect, radius: f32, fill: Color32, border: Color32) {
    ui.painter().rect(
        rect,
        CornerRadius::same(radius.round() as u8),
        fill,
        Stroke::new(1.0, border),
        StrokeKind::Inside,
    );
}

pub fn icon(ui: &Ui, rect: Rect, icon: Icon, dark: bool, alpha: f32) {
    egui::Image::new(icon.source(dark))
        .fit_to_exact_size(rect.size())
        .tint(Color32::from_white_alpha((alpha * 255.0).round() as u8))
        .paint_at(ui, rect);
}

/// The small solid triangles Qt's Fusion style draws for arrows.
pub enum Arrow {
    Down,
    Right,
    Left,
}

pub fn arrow(ui: &Ui, center: Pos2, dir: Arrow, half_w: f32, half_h: f32, color: Color32) {
    let points = match dir {
        Arrow::Down => vec![
            pos2(center.x - half_w, center.y - half_h),
            pos2(center.x + half_w, center.y - half_h),
            pos2(center.x, center.y + half_h),
        ],
        Arrow::Right => vec![
            pos2(center.x - half_h, center.y - half_w),
            pos2(center.x + half_h, center.y),
            pos2(center.x - half_h, center.y + half_w),
        ],
        Arrow::Left => vec![
            pos2(center.x + half_h, center.y - half_w),
            pos2(center.x - half_h, center.y),
            pos2(center.x + half_h, center.y + half_w),
        ],
    };
    ui.painter().add(Shape::convex_polygon(points, color, Stroke::NONE));
}

// ---------------------------------------------------------------- sidebar

/// Sidebar push buttons: `QPushButton` in `_modern_sidebar_qss`.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ButtonKind {
    Normal,
    /// `#TimelineStepButton`, `#BackgroundSubtractButton`.
    Primary,
}

pub fn push_button(ui: &Ui, rect: Rect, id: Id, label: &str, kind: ButtonKind, enabled: bool, p: &Palette) -> Response {
    let resp = ui.interact(rect, id, if enabled { Sense::click() } else { Sense::hover() });
    let (fill, border, text, weight) = if !enabled {
        let weight = if kind == ButtonKind::Primary { Weight::Semibold } else { Weight::Regular };
        (Color32::TRANSPARENT, p.hairline, p.card_muted, weight)
    } else {
        match kind {
            ButtonKind::Normal => {
                let fill = if resp.is_pointer_button_down_on() {
                    p.card_accent_soft
                } else if resp.hovered() {
                    p.control_hover
                } else {
                    p.control
                };
                let border = if resp.hovered() { p.card_accent } else { p.hairline };
                (fill, border, p.card_text, Weight::Regular)
            }
            ButtonKind::Primary => {
                let border = if resp.hovered() { p.card_accent } else { p.card_accent_soft };
                (p.card_accent_soft, border, p.card_accent_text, Weight::Semibold)
            }
        }
    };
    qt_box(ui, rect, 8.0, fill, border);
    text_center(ui, rect.center(), label, 12.0, weight, text);
    resp
}

/// The app-wide `QPushButton` (`AppTheme._modern_qss`): 32 px content,
/// 6×13 px padding, 1 px border, radius 10. Dialog buttons use it.
pub fn dialog_button(ui: &Ui, rect: Rect, id: Id, label: &str, enabled: bool, p: &Palette) -> Response {
    let resp = ui.interact(rect, id, if enabled { Sense::click() } else { Sense::hover() });
    let fill = if !enabled {
        p.surface_alt
    } else if resp.is_pointer_button_down_on() {
        p.pressed
    } else if resp.hovered() {
        p.hover
    } else {
        p.surface_bg
    };
    qt_box(ui, rect, 10.0, fill, p.border);
    text_center(ui, rect.center(), label, 12.0, Weight::Regular, if enabled { p.text } else { p.disabled });
    resp
}

/// A dialog button's width for `label`: text plus padding and border.
pub fn dialog_button_width(ui: &Ui, label: &str) -> f32 {
    (text_width(ui, label, 12.0, Weight::Regular) + 2.0 * 13.0 + 2.0).ceil().max(80.0)
}

/// The app-wide `QSpinBox` / `QDoubleSpinBox`: an editable value with
/// up/down buttons (26 px wide) on the right. Returns true when the value changed.
#[allow(clippy::too_many_arguments)]
pub fn spin_field(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    value: &mut f64,
    range: (f64, f64),
    step: f64,
    decimals: usize,
    suffix: &str,
    enabled: bool,
    p: &Palette,
) -> bool {
    let before = *value;
    qt_box(ui, rect, 10.0, if enabled { p.input_bg } else { p.surface_alt }, p.border);
    let buttons =
        Rect::from_min_max(pos2(rect.right() - 27.0, rect.top() + 1.0), pos2(rect.right() - 1.0, rect.bottom() - 1.0));
    let mid = buttons.center().y;
    let up = Rect::from_min_max(buttons.min, pos2(buttons.right(), mid));
    let down = Rect::from_min_max(pos2(buttons.left(), mid), buttons.max);
    for (r, delta, icon_kind, key) in
        [(up, step, Icon::ChevronUpSmall, "up"), (down, -step, Icon::ChevronDownSmall, "down")]
    {
        let resp = ui.interact(r, id.with(key), if enabled { Sense::click() } else { Sense::hover() });
        let fill = if resp.hovered() && enabled { p.hover } else { p.surface_alt };
        ui.painter().rect_filled(r, CornerRadius::ZERO, fill);
        icon(
            ui,
            Rect::from_center_size(r.center(), vec2(12.0, 12.0)),
            icon_kind,
            p.dark,
            if enabled { 1.0 } else { 0.4 },
        );
        if resp.clicked() {
            *value += delta;
        }
    }
    let painter = ui.painter();
    painter.line_segment(
        [pos2(buttons.left(), buttons.top()), pos2(buttons.left(), buttons.bottom())],
        Stroke::new(1.0, p.border),
    );
    painter.line_segment([pos2(buttons.left(), mid), pos2(buttons.right(), mid)], Stroke::new(1.0, p.border));

    // The text: editable, committed on Enter or when focus leaves.
    let text_id = id.with("text");
    let mut text: String =
        ui.data(|d| d.get_temp::<String>(text_id)).unwrap_or_else(|| format!("{:.*}{suffix}", decimals, *value));
    let field =
        Rect::from_min_max(pos2(rect.left() + 9.0, rect.top() + 1.0), pos2(buttons.left() - 2.0, rect.bottom() - 1.0));
    let resp = ui
        .add_enabled_ui(enabled, |ui| {
            ui.put(
                field,
                egui::TextEdit::singleline(&mut text)
                    .font(ui_font(12.0, Weight::Regular))
                    .frame(egui::Frame::NONE)
                    .vertical_align(egui::Align::Center)
                    .text_color(if enabled { p.text } else { p.disabled }),
            )
        })
        .inner;
    if resp.has_focus() {
        ui.data_mut(|d| d.insert_temp(text_id, text.clone()));
    } else {
        if resp.lost_focus()
            && let Ok(v) = text.trim().trim_end_matches(suffix.trim()).trim().parse::<f64>()
        {
            *value = v;
        }
        ui.data_mut(|d| d.remove::<String>(text_id));
    }
    if resp.hovered() && enabled {
        let notches = ui.input(|i| {
            i.events
                .iter()
                .filter_map(|e| match e {
                    egui::Event::MouseWheel { delta, .. } => Some(delta.y),
                    _ => None,
                })
                .sum::<f32>()
        });
        if notches != 0.0 {
            *value += step * notches.signum() as f64;
        }
    }
    *value = value.clamp(range.0, range.1);
    let scale = 10f64.powi(decimals as i32);
    *value = (*value * scale).round() / scale;
    *value != before
}

/// The app-wide `QTimeEdit` with `HH:mm:ss`: seconds of the day. The
/// buttons step the hour, as Qt's do before a section is clicked.
pub fn time_field(ui: &mut Ui, rect: Rect, id: Id, seconds: &mut i64, enabled: bool, p: &Palette) -> bool {
    let mut hours = (*seconds / 3600) as f64;
    let before = *seconds;
    qt_box(ui, rect, 10.0, if enabled { p.input_bg } else { p.surface_alt }, p.border);
    let buttons =
        Rect::from_min_max(pos2(rect.right() - 27.0, rect.top() + 1.0), pos2(rect.right() - 1.0, rect.bottom() - 1.0));
    let mid = buttons.center().y;
    for (r, delta, kind, key) in [
        (Rect::from_min_max(buttons.min, pos2(buttons.right(), mid)), 1.0, Icon::ChevronUpSmall, "up"),
        (Rect::from_min_max(pos2(buttons.left(), mid), buttons.max), -1.0, Icon::ChevronDownSmall, "down"),
    ] {
        let resp = ui.interact(r, id.with(key), if enabled { Sense::click() } else { Sense::hover() });
        ui.painter().rect_filled(
            r,
            CornerRadius::ZERO,
            if resp.hovered() && enabled { p.hover } else { p.surface_alt },
        );
        icon(ui, Rect::from_center_size(r.center(), vec2(12.0, 12.0)), kind, p.dark, if enabled { 1.0 } else { 0.4 });
        if resp.clicked() {
            hours += delta;
        }
    }
    let new_hours = (hours as i64).rem_euclid(24);
    *seconds = new_hours * 3600 + seconds.rem_euclid(3600);
    let text_id = id.with("text");
    let shown = format!("{:02}:{:02}:{:02}", *seconds / 3600, (*seconds % 3600) / 60, *seconds % 60);
    let mut text: String = ui.data(|d| d.get_temp::<String>(text_id)).unwrap_or(shown);
    let field =
        Rect::from_min_max(pos2(rect.left() + 9.0, rect.top() + 1.0), pos2(buttons.left() - 2.0, rect.bottom() - 1.0));
    let resp = ui
        .add_enabled_ui(enabled, |ui| {
            ui.put(
                field,
                egui::TextEdit::singleline(&mut text)
                    .font(ui_font(12.0, Weight::Regular))
                    .frame(egui::Frame::NONE)
                    .vertical_align(egui::Align::Center)
                    .text_color(if enabled { p.text } else { p.disabled }),
            )
        })
        .inner;
    if resp.has_focus() {
        ui.data_mut(|d| d.insert_temp(text_id, text.clone()));
    } else {
        if resp.lost_focus() {
            let parts: Vec<i64> = text.trim().split(':').filter_map(|v| v.trim().parse().ok()).collect();
            if let [h, m, sec] = parts[..]
                && (0..24).contains(&h)
                && (0..60).contains(&m)
                && (0..60).contains(&sec)
            {
                *seconds = h * 3600 + m * 60 + sec;
            }
        }
        ui.data_mut(|d| d.remove::<String>(text_id));
    }
    *seconds != before
}

/// The app-wide `QGroupBox`: a 12 px-radius card with a semibold title above
/// its contents. Returns the contents' rect.
pub fn group_box(ui: &Ui, rect: Rect, title: &str, enabled: bool, p: &Palette) -> Rect {
    // margin-top: 14px; the frame starts half the title height down.
    let frame = Rect::from_min_max(pos2(rect.left(), rect.top() + 7.0), rect.max);
    ui.painter().rect(frame, CornerRadius::same(12), p.surface_bg, Stroke::new(1.0, p.border), StrokeKind::Inside);
    let title_w = text_width(ui, title, 12.0, Weight::Semibold) + 10.0;
    let title_rect = Rect::from_min_size(pos2(rect.left() + 12.0, rect.top()), vec2(title_w, 14.0));
    ui.painter().rect_filled(title_rect, CornerRadius::ZERO, p.page_bg);
    let color = if enabled { p.muted } else { p.disabled };
    text_left(ui, title_rect.left() + 5.0, rect.top() + 7.0, title, 12.0, Weight::Semibold, color);
    Rect::from_min_max(pos2(frame.left() + 12.0, frame.top() + 12.0), pos2(frame.right() - 12.0, frame.bottom() - 12.0))
}

/// `QComboBox` in the sidebar: field box, text, chevron.
pub fn combo_field(ui: &Ui, rect: Rect, id: Id, text: &str, enabled: bool, p: &Palette) -> Response {
    let resp = ui.interact(rect, id, if enabled { Sense::click() } else { Sense::hover() });
    let border = if resp.has_focus() { p.card_accent } else { p.hairline };
    qt_box(ui, rect, 8.0, p.field, border);
    text_left(ui, rect.left() + 9.0, rect.center().y, text, 12.0, Weight::Regular, p.card_text);
    let chevron = Rect::from_center_size(pos2(rect.right() - 10.0, rect.center().y), vec2(12.0, 12.0));
    icon(ui, chevron, Icon::ChevronDownSmall, p.dark, 1.0);
    resp
}

/// `QLineEdit` in the sidebar, editable; shows the placeholder when empty.
/// Returns true when the text changed (`textChanged`).
#[allow(clippy::too_many_arguments)]
pub fn line_edit(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    text: &mut String,
    placeholder: &str,
    enabled: bool,
    p: &Palette,
) -> bool {
    let focused = ui.memory(|m| m.has_focus(id));
    qt_box(ui, rect, 8.0, p.field, if focused { p.card_accent } else { p.hairline });
    let field =
        Rect::from_min_max(pos2(rect.left() + 9.0, rect.top() + 1.0), pos2(rect.right() - 9.0, rect.bottom() - 1.0));
    let resp = ui
        .add_enabled_ui(enabled, |ui| {
            ui.put(
                field,
                egui::TextEdit::singleline(text)
                    .id(id)
                    .font(ui_font(12.0, Weight::Regular))
                    .frame(egui::Frame::NONE)
                    .vertical_align(egui::Align::Center)
                    .text_color(if enabled { p.card_text } else { p.card_muted }),
            )
        })
        .inner;
    if text.is_empty() && !resp.has_focus() {
        text_left(ui, rect.left() + 9.0, rect.center().y, placeholder, 12.0, Weight::Regular, p.card_muted);
    }
    resp.changed()
}

/// `QSpinBox` in the sidebar: an editable integer with the app stylesheet's
/// up/down buttons. Returns true when the value changed (`valueChanged`).
#[allow(clippy::too_many_arguments)]
pub fn spin_box(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    value: &mut i32,
    range: (i32, i32),
    enabled: bool,
    p: &Palette,
) -> bool {
    let before = *value;
    qt_box(ui, rect, 8.0, p.field, p.hairline);
    let buttons =
        Rect::from_min_max(pos2(rect.right() - 26.0, rect.top() + 1.0), pos2(rect.right() - 1.0, rect.bottom() - 1.0));
    let mid = buttons.center().y;
    for (r, delta, kind, key) in [
        (Rect::from_min_max(buttons.min, pos2(buttons.right(), mid)), 1, Icon::ChevronUpSmall, "up"),
        (Rect::from_min_max(pos2(buttons.left(), mid), buttons.max), -1, Icon::ChevronDownSmall, "down"),
    ] {
        let resp = ui.interact(r, id.with(key), if enabled { Sense::click() } else { Sense::hover() });
        if resp.hovered() && enabled {
            ui.painter().rect_filled(r, CornerRadius::ZERO, p.control_hover);
        }
        icon(ui, Rect::from_center_size(r.center(), vec2(12.0, 12.0)), kind, p.dark, if enabled { 1.0 } else { 0.4 });
        if resp.clicked() {
            *value += delta;
        }
    }
    let painter = ui.painter();
    painter.line_segment(
        [pos2(buttons.left(), buttons.top()), pos2(buttons.left(), buttons.bottom())],
        Stroke::new(1.0, p.border),
    );
    painter.line_segment([pos2(buttons.left(), mid), pos2(buttons.right(), mid)], Stroke::new(1.0, p.border));
    let text_id = id.with("text");
    let mut text: String = ui.data(|d| d.get_temp::<String>(text_id)).unwrap_or_else(|| value.to_string());
    let field =
        Rect::from_min_max(pos2(rect.left() + 10.0, rect.top() + 1.0), pos2(buttons.left() - 2.0, rect.bottom() - 1.0));
    let resp = ui
        .add_enabled_ui(enabled, |ui| {
            ui.put(
                field,
                egui::TextEdit::singleline(&mut text)
                    .font(ui_font(12.0, Weight::Regular))
                    .frame(egui::Frame::NONE)
                    .vertical_align(egui::Align::Center)
                    .text_color(if enabled { p.card_text } else { p.card_muted }),
            )
        })
        .inner;
    if resp.has_focus() {
        ui.data_mut(|d| d.insert_temp(text_id, text.clone()));
        if let Ok(v) = text.trim().parse::<i32>()
            && (range.0..=range.1).contains(&v)
        {
            *value = v;
        }
    } else {
        ui.data_mut(|d| d.remove::<String>(text_id));
    }
    *value = (*value).clamp(range.0, range.1);
    *value != before
}

/// `QCheckBox` in the sidebar. Returns true when toggled.
pub fn checkbox(ui: &Ui, rect: Rect, id: Id, label: &str, checked: &mut bool, enabled: bool, p: &Palette) -> bool {
    let resp = ui.interact(rect, id, if enabled { Sense::click() } else { Sense::hover() });
    // `width: 15px` is the content box; the 1 px border makes it 17 px.
    let ind = Rect::from_min_size(pos2(rect.left(), rect.center().y - 8.5), vec2(17.0, 17.0));
    if *checked {
        qt_box(ui, ind, 4.0, p.card_accent, p.card_accent);
    } else {
        qt_box(ui, ind, 4.0, p.field, p.hairline);
    }
    text_left(ui, rect.left() + 24.0, rect.center().y, label, 12.0, Weight::Regular, p.card_text);
    if resp.clicked() {
        *checked = !*checked;
        return true;
    }
    false
}

/// `QRadioButton`: indicator from the app stylesheet (it has no sidebar rule).
/// Returns true when an unchecked, enabled button is clicked.
pub fn radio(ui: &Ui, rect: Rect, id: Id, label: &str, checked: bool, enabled: bool, p: &Palette) -> bool {
    let resp = ui.interact(rect, id, if enabled { Sense::click() } else { Sense::hover() });
    // 15 px content box + 1 px border = 17 px, like the check box.
    let center = pos2(rect.left() + 8.5, rect.center().y);
    let painter = ui.painter();
    let fade = |c: Color32| if enabled { c } else { c.gamma_multiply(0.45) };
    if checked {
        painter.circle_filled(center, 8.5, fade(p.accent));
    } else {
        painter.circle_filled(center, 8.5, fade(p.border));
        painter.circle_filled(center, 7.5, p.input_bg);
    }
    let text = if enabled { p.card_text } else { p.card_muted };
    text_left(ui, rect.left() + 24.0, rect.center().y, label, 12.0, Weight::Regular, text);
    enabled && resp.clicked() && !checked
}

/// What a slider did this frame, as `QSlider`'s signals report it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct SliderOut {
    /// `sliderPressed`: the handle was grabbed.
    pub pressed: bool,
    /// `valueChanged`.
    pub value: Option<i32>,
    /// `sliderReleased`.
    pub released: bool,
}

#[derive(Clone, Copy, Debug)]
struct SliderDrag {
    /// Grabbed the handle (`Some(offset from its centre)`) or paging on the groove.
    handle_offset: Option<f32>,
    /// When the next page step repeats.
    next_repeat: f64,
}

/// `QSlider` in the sidebar, 0..=`max`: drag the handle, click the groove to
/// page towards the pointer (repeating while held), arrows and the wheel to step.
pub fn slider(ui: &Ui, rect: Rect, id: Id, value: i32, max: i32, page_step: i32, p: &Palette) -> SliderOut {
    // Qt's handle is its 15 px content box plus a 2 px border each side
    // (19 1/3 px wide), clipped to the slider's height: a rounded bar.
    const HANDLE_W: f32 = 58.0 / 3.0;
    const HANDLE_H: f32 = 44.0 / 3.0;
    // QAbstractSlider's auto-repeat: 500 ms before the first repeat, then every 50 ms.
    const REPEAT_DELAY: f64 = 0.5;
    const REPEAT_EVERY: f64 = 0.05;

    let resp = ui.interact(rect, id, Sense::click_and_drag());
    let span = (rect.width() - HANDLE_W).max(1.0);
    let t = |v: i32| v.clamp(0, max) as f32 / max.max(1) as f32;
    let handle_x = |v: i32| rect.left() + HANDLE_W / 2.0 + t(v) * span;
    let mut out = SliderOut::default();
    let mut current = value;
    let set = |v: i32, out: &mut SliderOut, current: &mut i32| {
        let v = v.clamp(0, max);
        if v != *current {
            *current = v;
            out.value = Some(v);
        }
    };

    let now = ui.input(|i| i.time);
    let mut drag: Option<SliderDrag> = ui.data(|d| d.get_temp(id));
    if resp.is_pointer_button_down_on() || resp.dragged() {
        if let Some(pos) = resp.interact_pointer_pos() {
            match drag {
                None => {
                    let on_handle = (pos.x - handle_x(current)).abs() <= HANDLE_W / 2.0;
                    if on_handle {
                        drag = Some(SliderDrag { handle_offset: Some(pos.x - handle_x(current)), next_repeat: 0.0 });
                        out.pressed = true;
                    } else {
                        let step = if pos.x < handle_x(current) { -page_step } else { page_step };
                        set(current + step, &mut out, &mut current);
                        drag = Some(SliderDrag { handle_offset: None, next_repeat: now + REPEAT_DELAY });
                    }
                }
                Some(SliderDrag { handle_offset: Some(off), .. }) => {
                    let x = pos.x - off - rect.left() - HANDLE_W / 2.0;
                    let v = ((x / span).clamp(0.0, 1.0) * max as f32).round() as i32;
                    set(v, &mut out, &mut current);
                }
                Some(SliderDrag { handle_offset: None, next_repeat }) => {
                    let hx = handle_x(current);
                    let towards = pos.x - hx;
                    if now >= next_repeat && towards.abs() > HANDLE_W / 2.0 {
                        let step = if towards < 0.0 { -page_step } else { page_step };
                        set(current + step, &mut out, &mut current);
                        drag = Some(SliderDrag { handle_offset: None, next_repeat: now + REPEAT_EVERY });
                    }
                    ui.ctx().request_repaint();
                }
            }
        }
    } else if let Some(d) = drag.take()
        && d.handle_offset.is_some()
    {
        out.released = true;
    }
    ui.data_mut(|m| match drag {
        Some(d) => {
            m.insert_temp(id, d);
        }
        None => {
            m.remove::<SliderDrag>(id);
        }
    });

    if resp.hovered() {
        // QAbstractSlider wheel: three single steps per notch.
        let notches = ui.input(|i| {
            i.events
                .iter()
                .filter_map(|e| match e {
                    egui::Event::MouseWheel { unit: egui::MouseWheelUnit::Line, delta, .. } => Some(delta.y),
                    egui::Event::MouseWheel { delta, .. } => Some(delta.y / 50.0),
                    _ => None,
                })
                .sum::<f32>()
        });
        if notches != 0.0 {
            set(current + (notches * 3.0).round() as i32, &mut out, &mut current);
        }
    }
    if resp.has_focus() {
        let (left, right) = ui.input(|i| (i.key_pressed(egui::Key::ArrowLeft), i.key_pressed(egui::Key::ArrowRight)));
        if left {
            set(current - 1, &mut out, &mut current);
        }
        if right {
            set(current + 1, &mut out, &mut current);
        }
    }
    if resp.clicked() {
        resp.request_focus();
    }

    let groove =
        Rect::from_min_max(pos2(rect.left(), rect.center().y - 2.5), pos2(rect.right(), rect.center().y + 2.5));
    let painter = ui.painter();
    painter.rect_filled(groove, CornerRadius::same(3), p.field_alt);
    let hx = handle_x(current);
    let filled = Rect::from_min_max(groove.min, pos2(hx, groove.max.y));
    painter.rect_filled(filled, CornerRadius::same(3), p.card_accent);
    let fill = if resp.hovered() { p.card_accent_soft } else { p.card };
    let handle = Rect::from_center_size(pos2(hx, rect.center().y - 1.0 / 6.0), vec2(HANDLE_W, HANDLE_H));
    painter.rect(handle, CornerRadius::same(7), fill, Stroke::new(2.0, p.card_accent), StrokeKind::Inside);
    out
}

/// The framed value box next to a noise threshold (`#NoiseThresholdValueCard`):
/// `NoiseThresholdPrimary` (13 px bold) and, below it, `NoiseThresholdSecondary`.
pub fn value_card(ui: &Ui, rect: Rect, primary: &str, secondary: Option<&str>, p: &Palette) {
    qt_box(ui, rect, 8.0, p.field, p.hairline);
    let right = rect.right() - 9.0;
    // NoiseThresholdPrimary: an 18 px label 6 px down, text centred in it;
    // NoiseThresholdSecondary: 14 px below that, text at its top.
    text_right(ui, right, rect.top() + 15.0, primary, 13.0, Weight::Bold, p.card_text);
    if let Some(sub) = secondary {
        let center = rect.top() + 24.0 + line_height(ui, 10.0, Weight::Regular) / 2.0;
        text_right(ui, right, center, sub, 10.0, Weight::Regular, p.card_muted);
    }
}

/// `QListWidget#TimelineSegmentList`: framed, alternating 26 px rows.
pub fn segment_list(ui: &Ui, rect: Rect, rows: &[String], p: &Palette) {
    qt_box(ui, rect, 8.0, p.field, p.hairline);
    let inner = rect.shrink(4.0);
    let painter = ui.painter_at(inner);
    for (i, row) in rows.iter().take(5).enumerate() {
        let r = Rect::from_min_size(pos2(inner.left(), inner.top() + 26.0 * i as f32), vec2(inner.width(), 26.0));
        if i % 2 == 1 {
            painter.rect_filled(r, CornerRadius::ZERO, p.field_alt);
        }
        let hovered = ui.rect_contains_pointer(r);
        if hovered {
            painter.rect_filled(r, CornerRadius::ZERO, p.card_accent_soft);
        }
        text_left(ui, r.left() + 8.0, r.center().y, row, 12.0, Weight::Regular, p.card_text);
    }
}

/// A sidebar `QComboBox` with its popup list. Returns the index picked.
pub fn combo(ui: &Ui, rect: Rect, id: Id, items: &[&str], current: usize, enabled: bool, p: &Palette) -> Option<usize> {
    let text = items.get(current).copied().unwrap_or("");
    let resp = combo_field(ui, rect, id, text, enabled, p);
    let popup_id = id.with("popup");
    let mut open = ui.data(|d| d.get_temp::<bool>(popup_id)).unwrap_or(false);
    if resp.clicked() {
        open = !open;
    }
    if !enabled {
        open = false;
    }
    let mut picked = None;
    if open {
        // `QComboBox QAbstractItemView`: 1 px hairline, radius 8, 4 px padding, card background.
        // QComboBox.maxVisibleItems: 10, the rest scroll.
        let row_h = 24.0;
        let visible = items.len().min(10);
        let height = visible as f32 * row_h + 2.0 * 5.0;
        let top = rect.bottom() + 2.0;
        let area = egui::Area::new(popup_id).order(egui::Order::Foreground).fixed_pos(pos2(rect.left(), top));
        let inner = area.show(ui.ctx(), |ui| {
            let frame = Rect::from_min_size(pos2(rect.left(), top), vec2(rect.width(), height));
            ui.allocate_rect(frame, Sense::hover());
            qt_box(ui, frame, 8.0, p.card, p.hairline);
            let first_id = popup_id.with("first");
            let mut first: usize = ui
                .data(|d| d.get_temp(first_id))
                .unwrap_or_else(|| current.saturating_sub(visible / 2).min(items.len().saturating_sub(visible)));
            if ui.rect_contains_pointer(frame) {
                let wheel: f32 = ui.input(|i| {
                    i.events
                        .iter()
                        .filter_map(|e| match e {
                            egui::Event::MouseWheel { delta, .. } => Some(delta.y),
                            _ => None,
                        })
                        .sum()
                });
                if wheel != 0.0 {
                    let step = (wheel.abs().ceil() as usize).max(1) * 3;
                    first = if wheel > 0.0 { first.saturating_sub(step) } else { first + step };
                    first = first.min(items.len().saturating_sub(visible));
                }
            }
            ui.data_mut(|d| d.insert_temp(first_id, first));
            for (row, (i, item)) in items.iter().enumerate().skip(first).take(visible).enumerate() {
                let r = Rect::from_min_size(
                    pos2(frame.left() + 5.0, frame.top() + 5.0 + row_h * row as f32),
                    vec2(frame.width() - 10.0, row_h),
                );
                let item_resp = ui.interact(r, popup_id.with(i), Sense::click());
                let highlighted = item_resp.hovered() || (i == current && !ui.rect_contains_pointer(frame));
                let color = if highlighted {
                    ui.painter().rect_filled(r, CornerRadius::same(4), p.card_accent);
                    Color32::WHITE
                } else {
                    p.card_text
                };
                text_left(ui, r.left() + 4.0, r.center().y, item, 12.0, Weight::Regular, color);
                if item_resp.clicked() {
                    picked = Some(i);
                }
            }
            frame
        });
        let clicked_elsewhere = ui.input(|i| i.pointer.any_click())
            && !resp.clicked()
            && !ui.input(|i| i.pointer.interact_pos()).is_some_and(|pos| inner.inner.contains(pos));
        if picked.is_some() || clicked_elsewhere || ui.input(|i| i.key_pressed(egui::Key::Escape)) {
            open = false;
            ui.data_mut(|d| d.remove::<usize>(popup_id.with("first")));
        }
    }
    ui.data_mut(|d| d.insert_temp(popup_id, open));
    picked.filter(|i| *i != current)
}
