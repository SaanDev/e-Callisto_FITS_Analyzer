//! The app-wide Qt controls of the full app's Modern stylesheet
//! (`AppTheme._modern_qss`) and the downloader's additions
//! (`CallistoDownloaderApp._apply_downloader_style`): push buttons, labels,
//! line edits, combo boxes, date/time edits with calendar popups, spin
//! boxes, progress bars, group boxes and tab bars.
//!
//! Each control comes with its Qt size hint and size policy (measured from
//! the full app, see `lite/tools/capture_downloader.py`) for
//! [`super::qlayout`], and a painter for the rectangle the layout gives it.

use std::rc::Rc;

use chrono::{Datelike, NaiveDate, NaiveDateTime, NaiveTime, Timelike};
use egui::{Color32, CornerRadius, Id, Pos2, Rect, Response, Sense, Stroke, StrokeKind, Ui, pos2, vec2};

use super::qlayout::{Policy, Size, WidgetSpec};
use super::widgets::{text_left, text_left_clipped, text_width};
use crate::fonts::{Weight, ui_font};
use crate::theme::Palette;

pub const FONT_PX: f32 = 12.0;

thread_local! {
    /// Whether controls follow the app-wide stylesheet (see [`with_app_style`]).
    static APP_STYLE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
    /// Text fields draw their text transparent (see [`with_hidden_text`]).
    static HIDDEN_TEXT: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Runs `f` with text fields' own text hidden, for a caller that draws the
/// text itself (a `QLineEdit` scrolled to its end).
pub fn with_hidden_text<R>(f: impl FnOnce() -> R) -> R {
    let before = HIDDEN_TEXT.with(|a| a.replace(true));
    let out = f();
    HIDDEN_TEXT.with(|a| a.set(before));
    out
}

/// Draws `f`'s controls as the app-wide stylesheet styles them, for dialogs
/// that belong to the main window: drop-downs and spin buttons show the
/// arrow images (`chevron_down_small.svg`, `chevron_up_small.svg`, 12 × 12)
/// and editable text sits 10 px + border + Qt's 2 px margin in. The
/// downloader's windows replace the app's stylesheet with their own: empty
/// drop-downs, text 2 px further left.
pub fn with_app_style<R>(f: impl FnOnce() -> R) -> R {
    let before = APP_STYLE.with(|a| a.replace(true));
    let out = f();
    APP_STYLE.with(|a| a.set(before));
    out
}

fn app_style() -> bool {
    APP_STYLE.with(|a| a.get())
}

/// `QCheckBox` with the app-wide indicator: 15 px plus a 1 px border,
/// radius 4, the accent when checked, the text 7 px after it. Returns
/// true when toggled.
pub fn check_box(ui: &Ui, rect: Rect, id: Id, label: &str, checked: &mut bool, enabled: bool, p: &Palette) -> bool {
    let resp = ui.interact(rect, id, if enabled { Sense::click() } else { Sense::hover() });
    let ind = Rect::from_min_size(pos2(rect.left(), rect.center().y - 8.5), vec2(17.0, 17.0));
    if *checked {
        qt_box(ui, ind, 4.0, p.accent, p.accent);
    } else {
        qt_box(ui, ind, 4.0, p.input_bg, p.border);
    }
    text_left(ui, rect.left() + 24.0, rect.center().y, label, FONT_PX, Weight::Regular, text_color(enabled, p));
    if resp.clicked() {
        *checked = !*checked;
        return true;
    }
    false
}

fn arrow_icon(ui: &Ui, button: Rect, icon: crate::icons::Icon, p: &Palette) {
    if app_style() {
        // The arrow sits in the subcontrol's contents, right of its 1 px left border.
        let c = pos2(button.left() + 1.0 + (button.width() - 1.0) / 2.0, button.center().y);
        super::widgets::icon(ui, Rect::from_center_size(c, vec2(12.0, 12.0)), icon, p.dark, 1.0);
    }
}
/// `QFontMetrics::lineSpacing` / `height` of the 9 pt UI font.
pub const LINE: f32 = 16.0;
/// `fm.horizontalAdvance('x')`.
const X_ADVANCE: i32 = 6;

fn ceil_w(ui: &Ui, text: &str, weight: Weight) -> i32 {
    text_width(ui, text, FONT_PX, weight).ceil() as i32
}

/// A plain label's width hint for `text`.
pub fn text_w_hint(ui: &Ui, text: &str) -> i32 {
    ceil_w(ui, text, Weight::Regular)
}

fn round_w(ui: &Ui, text: &str, weight: Weight) -> i32 {
    text_width(ui, text, FONT_PX, weight).round() as i32
}

/// `#PrimaryDownloaderButton` hover colour (`accent_pressed`).
pub fn accent_pressed(p: &Palette) -> Color32 {
    if p.dark { Color32::from_rgb(0x2f, 0x83, 0xd8) } else { Color32::from_rgb(0x0f, 0x5f, 0xba) }
}

// ---------------------------------------------------------------- specs

/// A push button: text, 6×13 padding, 1 px border, min-height 32.
pub fn button_spec(ui: &Ui, id: &'static str, text: &str, primary: bool) -> WidgetSpec {
    let weight = if primary { Weight::Semibold } else { Weight::Regular };
    WidgetSpec::new(id, Size::new(ceil_w(ui, text, weight) + 28, 46), Policy::Minimum, Policy::Fixed).min_height(46)
}

/// A one-line `QLabel` (no padding).
pub fn label_spec(ui: &Ui, id: &'static str, text: &str) -> WidgetSpec {
    WidgetSpec::new(id, Size::new(ceil_w(ui, text, Weight::Regular), LINE as i32), Policy::Preferred, Policy::Preferred)
}

/// The lines Qt wraps `text` into at `width` (word wrap).
pub fn wrap_lines(ctx: &egui::Context, text: &str, width: f32, weight: Weight) -> Vec<String> {
    let mut out = Vec::new();
    for para in text.split('\n') {
        let galley =
            ctx.fonts_mut(|f| f.layout(para.to_owned(), ui_font(FONT_PX, weight), Color32::WHITE, width.max(1.0)));
        if galley.rows.is_empty() {
            out.push(String::new());
        }
        for row in &galley.rows {
            out.push(row.row.glyphs.iter().map(|g| g.chr).collect::<String>().trim_end().to_owned());
        }
    }
    out
}

/// A word-wrapping `QLabel` with `padding` (horizontal, vertical); status
/// labels (`#DownloaderStatusLabel`) also indent by Qt's frame rule.
pub fn wrap_label_spec(ui: &Ui, id: &'static str, text: &str, status: bool) -> WidgetSpec {
    let (pad_x, pad_y) = if status { (4, 2) } else { (0, 0) };
    let extra = if status { X_ADVANCE } else { 0 };
    let ctx = ui.ctx().clone();
    let owned = text.to_owned();
    let longest_word = text.split_whitespace().map(|w| ceil_w(ui, w, Weight::Regular)).max().unwrap_or(0);
    let full = ceil_w(ui, text, Weight::Regular);
    // `sizeForWidth(-1)`: 80 average characters, halved while it stays short.
    let try_w = (6 * 80) as f32;
    let hint_w = if full as f32 > try_w / 2.0 { (try_w / 2.0) as i32 } else { full };
    let hfw: super::qlayout::Hfw = Rc::new(move |w: i32| {
        let inner = (w - 2 * pad_x - extra).max(1) as f32;
        wrap_lines(&ctx, &owned, inner, Weight::Regular).len().max(1) as i32 * LINE as i32 + 2 * pad_y
    });
    let lines_at_hint = if full > hint_w { 2 } else { 1 };
    WidgetSpec::new(
        id,
        Size::new(hint_w + 2 * pad_x + extra, lines_at_hint * LINE as i32 + 2 * pad_y),
        Policy::Preferred,
        Policy::Preferred,
    )
    .min_hint(Size::new(longest_word + 2 * pad_x + extra, LINE as i32 + 2 * pad_y))
    .hfw(hfw)
}

/// A one-line status label (`#DownloaderStatusLabel`, no word wrap).
pub fn status_spec(ui: &Ui, id: &'static str, text: &str) -> WidgetSpec {
    WidgetSpec::new(
        id,
        Size::new(round_w(ui, text, Weight::Regular) + X_ADVANCE + 8, LINE as i32 + 4),
        Policy::Preferred,
        Policy::Preferred,
    )
}

pub fn line_edit_spec(id: &'static str) -> WidgetSpec {
    WidgetSpec::new(id, Size::new(128, 42), Policy::Expanding, Policy::Fixed).min_hint(Size::new(50, 42)).min_height(42)
}

/// A combo box sized to its longest item (`AdjustToContentsOnFirstShow`).
pub fn combo_spec(ui: &Ui, id: &'static str, items: &[&str]) -> WidgetSpec {
    let w = items.iter().map(|t| round_w(ui, t, Weight::Regular)).max().unwrap_or(0);
    WidgetSpec::new(id, Size::new(w + 72, 42), Policy::Preferred, Policy::Fixed).min_height(42)
}

/// A combo box sized to `n` characters (`AdjustToMinimumContentsLengthWithIcon`).
pub fn combo_len_spec(id: &'static str, n: i32) -> WidgetSpec {
    WidgetSpec::new(id, Size::new(n * 7 + 93, 42), Policy::Preferred, Policy::Fixed).min_height(42)
}

/// A date / date-time edit with a calendar popup, for a display format
/// whose widest text is `sample`.
pub fn date_spec(ui: &Ui, id: &'static str, sample: &str) -> WidgetSpec {
    let w = round_w(ui, &format!("{sample} "), Weight::Regular) + 2 + 73;
    WidgetSpec::new(id, Size::new(w, 45), Policy::Minimum, Policy::Fixed).min_height(42)
}

/// A spin box or time edit (up/down buttons), widest text `sample`.
pub fn spin_spec(ui: &Ui, id: &'static str, sample: &str) -> WidgetSpec {
    let w = round_w(ui, sample, Weight::Regular) + 2 + 95;
    WidgetSpec::new(id, Size::new(w, 45), Policy::Minimum, Policy::Fixed).min_height(42)
}

pub fn progress_spec(id: &'static str) -> WidgetSpec {
    WidgetSpec::new(id, Size::new(89, 26), Policy::Expanding, Policy::Fixed).min_hint(Size::new(89, 18)).min_height(20)
}

/// A tree, table or list view (`QAbstractScrollArea`).
pub fn view_spec(id: &'static str) -> WidgetSpec {
    WidgetSpec::new(id, Size::new(256, 192), Policy::Expanding, Policy::Expanding)
        .min_hint(Size::new(66, 66))
        .min_height(12)
}

// ---------------------------------------------------------------- painting

/// Text left-aligned in `rect`, vertically centred (`QLabel`'s default).
pub fn label(ui: &Ui, rect: Rect, text: &str, color: Color32) {
    text_left(ui, rect.left(), rect.center().y, text, FONT_PX, Weight::Regular, color);
}

/// A word-wrapped label: lines centred as a block in `rect`.
pub fn wrapped(ui: &Ui, rect: Rect, text: &str, color: Color32, status: bool) {
    let (pad_x, pad_y, indent) = if status { (4.0, 2.0, 3.0) } else { (0.0, 0.0, 0.0) };
    let inner = Rect::from_min_max(
        pos2(rect.left() + pad_x, rect.top() + pad_y),
        pos2(rect.right() - pad_x, rect.bottom() - pad_y),
    );
    // QLabel lays text out in contentsRect less its indent, but wraps
    // it at the width that also excludes the right indent.
    let lines = wrap_lines(ui.ctx(), text, inner.width() - 2.0 * indent, Weight::Regular);
    let total = lines.len() as f32 * LINE;
    let top = inner.center().y - total / 2.0;
    let painter_clip = ui.painter().clip_rect();
    let _ = painter_clip;
    for (i, line) in lines.iter().enumerate() {
        text_left(ui, inner.left() + indent, top + LINE * (i as f32 + 0.5), line, FONT_PX, Weight::Regular, color);
    }
}

/// `#DownloaderStatusLabel`: muted, 2×4 padding, indented 3 px.
pub fn status(ui: &Ui, rect: Rect, text: &str, p: &Palette) {
    wrapped(ui, rect, text, p.muted, true);
}

/// A status label ending in a "Retry" link (`StationAvailabilityLabel`).
/// Returns true when the link is clicked.
pub fn status_with_link(ui: &Ui, rect: Rect, id: Id, text: &str, link: &str, p: &Palette) -> bool {
    let x = rect.left() + 7.0;
    let r = text_left(ui, x, rect.center().y, text, FONT_PX, Weight::Regular, p.muted);
    let link_x = r.right() + text_width(ui, " ", FONT_PX, Weight::Regular);
    let lr = text_left(ui, link_x, rect.center().y, link, FONT_PX, Weight::Regular, p.accent);
    let underline_y = lr.bottom() - 2.0;
    ui.painter()
        .line_segment([pos2(lr.left(), underline_y), pos2(lr.right(), underline_y)], Stroke::new(1.0, p.accent));
    let resp = ui.interact(lr, id, Sense::click());
    if resp.hovered() {
        ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
    }
    resp.clicked()
}

/// A hyperlink label (`<a href=…>`), accent and underlined.
pub fn link(ui: &Ui, rect: Rect, id: Id, text: &str, p: &Palette) -> bool {
    let r = text_left(ui, rect.left(), rect.center().y, text, FONT_PX, Weight::Regular, p.accent);
    let y = r.bottom() - 2.0;
    ui.painter().line_segment([pos2(r.left(), y), pos2(r.right(), y)], Stroke::new(1.0, p.accent));
    let resp = ui.interact(rect, id, Sense::click());
    if resp.hovered() {
        ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
    }
    resp.clicked()
}

/// A stylesheet box (background and 1 px border) of the widget at `rect`.
pub(crate) fn qt_box(ui: &Ui, rect: Rect, radius: f32, fill: Color32, border: Color32) {
    qt_box_in(ui, rect, rect, radius, fill, border);
}

/// A stylesheet box at `rect` inside the widget at `widget`. Qt paints it
/// antialiased where it lies, often on half device pixels at 150 %, and
/// clips it to the widget's device rectangle; egui would snap it to whole
/// pixels.
fn qt_box_in(ui: &Ui, widget: Rect, rect: Rect, radius: f32, fill: Color32, border: Color32) {
    let shape = egui::epaint::RectShape::new(
        rect,
        CornerRadius::same(radius.round() as u8),
        fill,
        Stroke::new(1.0, border),
        StrokeKind::Inside,
    );
    ui.painter().with_clip_rect(widget).add(shape.with_round_to_pixels(false));
}

/// An antialiased fill exactly at `rect` (a stylesheet background or border
/// edge).
pub(crate) fn fill_exact(painter: &egui::Painter, rect: Rect, radius: CornerRadius, fill: Color32) {
    painter.add(egui::epaint::RectShape::filled(rect, radius, fill).with_round_to_pixels(false));
}

/// `QPushButton`, or `#PrimaryDownloaderButton` when `primary`.
pub fn button(ui: &Ui, rect: Rect, id: Id, text: &str, primary: bool, enabled: bool, p: &Palette) -> Response {
    let resp = ui.interact(rect, id, if enabled { Sense::click() } else { Sense::hover() });
    let down = resp.is_pointer_button_down_on();
    let (fill, border, color, weight) = match (primary, enabled) {
        (true, true) => {
            let c = if resp.hovered() || down { accent_pressed(p) } else { p.accent };
            (c, c, Color32::WHITE, Weight::Semibold)
        }
        (true, false) => (p.surface_alt, p.border, p.muted, Weight::Semibold),
        (false, true) => {
            let fill = if down {
                p.pressed
            } else if resp.hovered() {
                p.hover
            } else {
                p.surface_bg
            };
            (fill, p.border, p.text, Weight::Regular)
        }
        (false, false) => (p.surface_alt, p.border, p.disabled, Weight::Regular),
    };
    qt_box(ui, rect, 10.0, fill, border);
    super::widgets::text_center(ui, rect.center(), text, FONT_PX, weight, color);
    resp
}

/// `QGroupBox#DownloaderSection`: 8 px radius, the frame 12 px down, the
/// muted semibold title over it 10 px in. Returns nothing; the layout
/// places the contents (contents margins 11, 23, 11, 11).
pub fn section_box(ui: &Ui, rect: Rect, title: &str, p: &Palette) {
    titled_frame(ui, rect, 12.0, 8.0, rect.left() + 10.0, title, p);
}

/// The app-wide `QGroupBox`: 12 px radius, the frame 14 px down, the
/// title 12 px in. Contents margins 13, 27, 13, 13.
pub fn group_box(ui: &Ui, rect: Rect, title: &str, p: &Palette) {
    titled_frame(ui, rect, 14.0, 12.0, rect.left() + 12.0, title, p);
}

/// A group box frame and title as QStyleSheetStyle draws them: the
/// background half a pixel inside the border, the border cut out behind
/// the title rectangle (the text and a space, plus `padding: 0 5px`; the
/// device-pixel scissor rounds the cut as Qt rounds its clip region).
fn titled_frame(ui: &Ui, rect: Rect, frame_top: f32, radius: f32, title_x: f32, title: &str, p: &Palette) {
    let frame = Rect::from_min_max(pos2(rect.left(), rect.top() + frame_top), rect.max);
    let painter = ui.painter().with_clip_rect(rect);
    let corners = CornerRadius::same(radius as u8);
    fill_exact(&painter, frame.shrink(0.5), corners, p.surface_bg);
    let tw = text_width(ui, &format!("{title} "), FONT_PX, Weight::Semibold).round();
    let cut = Rect::from_min_max(pos2(title_x, rect.top()), pos2(title_x + tw + 10.0, rect.top() + LINE));
    let border = egui::epaint::RectShape::stroke(frame, corners, Stroke::new(1.0, p.border), StrokeKind::Inside)
        .with_round_to_pixels(false);
    for clip in [
        Rect::from_min_max(rect.min, pos2(cut.left(), rect.bottom())),
        Rect::from_min_max(pos2(cut.right(), rect.top()), rect.max),
        Rect::from_min_max(pos2(cut.left(), cut.bottom()), pos2(cut.right(), rect.bottom())),
    ] {
        painter.with_clip_rect(clip).add(border.clone());
    }
    // Measured on six titles: the text sits 1.5 px right of the padding.
    text_left(ui, cut.left() + 6.5, rect.top() + 8.0, title, FONT_PX, Weight::Semibold, p.muted);
}

/// The field box of line edits, combos, date and spin boxes.
fn field_box(ui: &Ui, rect: Rect, focused: bool, enabled: bool, p: &Palette) {
    let _ = enabled;
    qt_box(ui, rect, 10.0, p.input_bg, if focused { p.accent } else { p.border });
}

/// `::drop-down` of combos and calendar date edits: 26 px plus a 1 px left
/// border, right corners rounded, over the field's own border
/// (`subcontrol-origin: border`). Returns its rectangle.
fn drop_down(ui: &Ui, rect: Rect, hovered: bool, p: &Palette) -> Rect {
    let strip = Rect::from_min_max(pos2(rect.right() - 27.0, rect.top()), rect.max);
    let painter = ui.painter().with_clip_rect(rect);
    let fill = if hovered { p.hover } else { p.surface_alt };
    // The left border under the background: a 1 px rectangle of its own
    // would be drawn as an antialiased line, two device pixels wide.
    fill_exact(
        &painter,
        Rect::from_min_max(strip.min, pos2(strip.left() + 2.0, strip.bottom())),
        CornerRadius::ZERO,
        p.border,
    );
    fill_exact(
        &painter,
        Rect::from_min_max(pos2(strip.left() + 1.0, strip.top()), strip.max),
        CornerRadius { nw: 0, sw: 0, ne: 10, se: 10 },
        fill,
    );
    arrow_icon(ui, strip, crate::icons::Icon::ChevronDownSmall, p);
    strip
}

/// The up and down buttons, each 26 px plus a 1 px left border, half the
/// height with a 1 px gap. `QSpinBox` puts them over its border
/// (`subcontrol-origin: border`, outer corners rounded, a bottom border on
/// the up button); `QTimeEdit` inside it, square.
fn step_rects(rect: Rect, over_frame: bool) -> [Rect; 2] {
    let (frame, left) = if over_frame { (rect, rect.right() - 27.0) } else { (rect.shrink(1.0), rect.right() - 28.0) };
    let half = (frame.height() / 2.0).floor();
    [
        Rect::from_min_max(pos2(left, frame.top()), pos2(frame.right(), frame.top() + half)),
        Rect::from_min_max(pos2(left, frame.bottom() - half), frame.max),
    ]
}

fn step_buttons(ui: &Ui, rect: Rect, over_frame: bool, hovered: [bool; 2], p: &Palette) {
    let painter = ui.painter().with_clip_rect(rect);
    let ppp = ui.ctx().pixels_per_point();
    let snap = |v: f32| (v * ppp).round() / ppp;
    for (i, b) in step_rects(rect, over_frame).into_iter().enumerate() {
        let fill = if hovered[i] { p.hover } else { p.surface_alt };
        // Borders go under the background, two pixels wide (see drop_down).
        fill_exact(&painter, Rect::from_min_max(b.min, pos2(b.left() + 2.0, b.bottom())), CornerRadius::ZERO, p.border);
        if over_frame {
            let mut body = Rect::from_min_max(pos2(b.left() + 1.0, b.top()), b.max);
            let radius = if i == 0 {
                fill_exact(
                    &painter,
                    Rect::from_min_max(pos2(b.left(), b.bottom() - 2.0), b.max),
                    CornerRadius::ZERO,
                    p.border,
                );
                body.max.y -= 1.0;
                CornerRadius { ne: 10, ..CornerRadius::ZERO }
            } else {
                CornerRadius { se: 10, ..CornerRadius::ZERO }
            };
            fill_exact(&painter, body, radius, fill);
        } else {
            // A square background is Qt's aliased fillRect: whole pixels.
            let body =
                Rect::from_min_max(pos2(snap(b.left() + 1.0), snap(b.top())), pos2(snap(b.right()), snap(b.bottom())));
            fill_exact(&painter, body, CornerRadius::ZERO, fill);
        }
        let mut contents = b;
        if over_frame && i == 0 {
            contents.max.y -= 1.0;
        }
        let icon = if i == 0 { crate::icons::Icon::ChevronUpSmall } else { crate::icons::Icon::ChevronDownSmall };
        arrow_icon(ui, contents, icon, p);
    }
}

/// An editable text field; `text_x` is the text's inset. Returns the response.
fn text_field(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    text: &mut String,
    right_inset: f32,
    enabled: bool,
    color: Color32,
) -> Response {
    let color = if HIDDEN_TEXT.with(|h| h.get()) { Color32::TRANSPARENT } else { color };
    let inset = if app_style() { 13.0 } else { 11.0 };
    let field = Rect::from_min_max(
        pos2(rect.left() + inset, rect.top() + 1.0),
        pos2(rect.right() - right_inset, rect.bottom() - 1.0),
    );
    ui.add_enabled_ui(enabled, |ui| {
        let mut style = (**ui.style()).clone();
        style.spacing.item_spacing = vec2(0.0, 0.0);
        style.visuals.selection.bg_fill = Color32::from_rgb(0x14, 0x6f, 0xda);
        ui.set_style(style);
        ui.put(
            field,
            egui::TextEdit::singleline(text)
                .id(id)
                .font(ui_font(FONT_PX, Weight::Regular))
                .frame(egui::Frame::NONE)
                .margin(egui::Margin { left: 2, right: 2, top: 0, bottom: 0 })
                .vertical_align(egui::Align::Center)
                .text_color(color),
        )
    })
    .inner
}

/// `QLineEdit` with a placeholder. Returns true when the text changed.
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
    field_box(ui, rect, focused, enabled, p);
    let color = text_color(enabled, p);
    let resp = text_field(ui, rect, id, text, 11.0, enabled, color);
    if text.is_empty() {
        let ph = placeholder_color(p);
        text_left(ui, rect.left() + 13.0, rect.center().y, placeholder, FONT_PX, Weight::Regular, ph);
    }
    resp.changed()
}

/// Qt's placeholder text colour: the text colour at half opacity.
pub fn placeholder_color(p: &Palette) -> Color32 {
    let t = p.text;
    let b = p.input_bg;
    let mix = |a: u8, b: u8| ((a as u16 + b as u16) / 2) as u8;
    Color32::from_rgb(mix(t.r(), b.r()), mix(t.g(), b.g()), mix(t.b(), b.b()))
}

/// Snaps a rectangle's edges to device pixels (Qt's aliased fills).
fn snap_rect(ui: &Ui, r: Rect) -> Rect {
    let ppp = ui.ctx().pixels_per_point();
    let s = |v: f32| (v * ppp).round() / ppp;
    Rect::from_min_max(pos2(s(r.min.x), s(r.min.y)), pos2(s(r.max.x), s(r.max.y)))
}

/// Moves a rectangle onto whole device pixels, keeping its size.
fn snap_origin(ui: &Ui, r: Rect) -> Rect {
    let ppp = ui.ctx().pixels_per_point();
    Rect::from_min_size(pos2((r.min.x * ppp).round() / ppp, (r.min.y * ppp).round() / ppp), r.size())
}

/// The popup list of a combo box. Fusion opens it over the combo
/// (`SH_ComboBox_Popup`), shifted up so the current row lines up with the
/// field and scrolled to centre that row: a menu panel (`base.lighter(108)`
/// in a `base.darker(116)` outline) around the stylesheet's list
/// (`QComboBox QAbstractItemView`: 10 px radius, 4 px padding) of 24 px
/// rows, at most ten. Returns the item clicked.
#[allow(clippy::too_many_arguments)]
fn popup_list(
    ui: &Ui,
    anchor: Rect,
    id: Id,
    items: &[String],
    current: Option<usize>,
    open: &mut bool,
    owner: &Response,
    p: &Palette,
) -> Option<usize> {
    use crate::theme::{darker, lighter};
    let row_h = 24.0;
    let visible = items.len().clamp(1, 10);
    // Panel margin 4, list border 1 and padding 4, top and bottom.
    let height = visible as f32 * row_h + 18.0;
    let first_id = id.with("first");
    let max_first = items.len().saturating_sub(visible);
    let mut first: usize = ui
        .data(|d| d.get_temp(first_id))
        .unwrap_or_else(|| current.map_or(0, |c| c.saturating_sub(visible / 2)).min(max_first));
    let top_id = id.with("top");
    let screen = ui.ctx().content_rect();
    let top: f32 = ui.data(|d| d.get_temp(top_id)).unwrap_or_else(|| {
        let row = current.map_or(0, |c| c.saturating_sub(first)) as f32;
        (anchor.top() - row * row_h).min(screen.bottom() - height).max(screen.top())
    });
    ui.data_mut(|d| d.insert_temp(top_id, top));
    // A popup is its own window, placed on whole device pixels.
    let frame = snap_origin(ui, Rect::from_min_size(pos2(anchor.left(), top), vec2(anchor.width(), height)));
    let mut picked = None;
    let inner = egui::Area::new(id).order(egui::Order::Foreground).fixed_pos(frame.min).show(ui.ctx(), |ui| {
        ui.allocate_rect(frame, Sense::hover());
        let painter = ui.painter().with_clip_rect(frame);
        let edge = darker(p.qt.base, 116);
        fill_exact(&painter, frame, CornerRadius::ZERO, lighter(p.qt.base, 108));
        for line in [
            Rect::from_min_max(frame.min, pos2(frame.right(), frame.top() + 1.0)),
            Rect::from_min_max(pos2(frame.left(), frame.bottom() - 1.0), frame.max),
            Rect::from_min_max(frame.min, pos2(frame.left() + 1.0, frame.bottom())),
            Rect::from_min_max(pos2(frame.right() - 1.0, frame.top()), frame.max),
        ] {
            fill_exact(&painter, snap_rect(ui, line), CornerRadius::ZERO, edge);
        }
        let list = Rect::from_min_max(pos2(frame.left(), frame.top() + 4.0), pos2(frame.right(), frame.bottom() - 4.0));
        qt_box(ui, list, 10.0, p.surface_bg, p.border);
        let vp = list.shrink(5.0);
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
                first = if wheel > 0.0 { first.saturating_sub(step) } else { (first + step).min(max_first) };
            }
        }
        ui.data_mut(|d| d.insert_temp(first_id, first));
        for (row, (i, item)) in items.iter().enumerate().skip(first).take(visible).enumerate() {
            let r = Rect::from_min_size(pos2(vp.left(), vp.top() + row_h * row as f32), vec2(vp.width(), row_h));
            let resp = ui.interact(r, id.with(("item", i)), Sense::click());
            let hot = resp.hovered() || (Some(i) == current && !ui.rect_contains_pointer(frame));
            let color = if hot {
                fill_exact(ui.painter(), r.shrink(0.5), CornerRadius::ZERO, p.accent);
                Color32::WHITE
            } else {
                p.text
            };
            text_left(ui, r.left() + 7.0, r.center().y, item, FONT_PX, Weight::Regular, color);
            if resp.clicked() {
                picked = Some(i);
            }
        }
    });
    let clicked_elsewhere = ui.input(|i| i.pointer.any_click())
        && !owner.clicked()
        && !ui
            .input(|i| i.pointer.interact_pos())
            .is_some_and(|pos| inner.response.rect.contains(pos) || frame.contains(pos));
    if picked.is_some() || clicked_elsewhere || ui.input(|i| i.key_pressed(egui::Key::Escape)) {
        *open = false;
        ui.data_mut(|d| {
            d.remove::<usize>(first_id);
            d.remove::<f32>(top_id);
        });
    }
    picked
}

/// `QComboBox`; an empty list shows `placeholder`. Returns the item picked
/// (also the current one, as `textActivated`).
#[allow(clippy::too_many_arguments)]
pub fn combo(
    ui: &Ui,
    rect: Rect,
    id: Id,
    items: &[String],
    current: Option<usize>,
    placeholder: &str,
    enabled: bool,
    p: &Palette,
) -> Option<usize> {
    let resp = ui.interact(rect, id, if enabled { Sense::click() } else { Sense::hover() });
    let popup = id.with("popup");
    let mut open = ui.data(|d| d.get_temp::<bool>(popup)).unwrap_or(false);
    let focused = ui.memory(|m| m.has_focus(id));
    field_box(ui, rect, open || focused, enabled, p);
    drop_down(ui, rect, resp.hovered() && enabled, p);
    let text = current.and_then(|i| items.get(i));
    let color = text_color(enabled, p);
    match text {
        Some(t) => {
            // SC_ComboBoxEditField: inside the 32 px right padding and the
            // 26 px drop-down (Qt clips the label there).
            let clip = Rect::from_min_max(rect.min, pos2(rect.right() - 59.0, rect.bottom()));
            text_left_clipped(ui, clip, rect.left() + 11.0, rect.center().y, t, FONT_PX, Weight::Regular, color);
        }
        None => {
            text_left(
                ui,
                rect.left() + 11.0,
                rect.center().y,
                placeholder,
                FONT_PX,
                Weight::Regular,
                placeholder_color(p),
            );
        }
    }
    if resp.clicked() && !items.is_empty() {
        open = !open;
    }
    if !enabled || items.is_empty() {
        open = false;
    }
    let mut picked = None;
    if open {
        picked = popup_list(ui, rect, popup, items, current, &mut open, &resp, p);
    }
    ui.data_mut(|d| d.insert_temp(popup, open));
    picked
}

/// An editable combo box: free text with the items as a popup.
/// Returns true when the text changed (typed or picked).
pub fn editable_combo(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    text: &mut String,
    items: &[String],
    enabled: bool,
    p: &Palette,
) -> bool {
    let popup = id.with("popup");
    let mut open = ui.data(|d| d.get_temp::<bool>(popup)).unwrap_or(false);
    let edit_id = id.with("edit");
    let focused = ui.memory(|m| m.has_focus(edit_id));
    field_box(ui, rect, focused || open, enabled, p);
    let strip_rect = Rect::from_min_max(pos2(rect.right() - 27.0, rect.top()), rect.max);
    let strip_resp = ui.interact(strip_rect, id.with("strip"), if enabled { Sense::click() } else { Sense::hover() });
    drop_down(ui, rect, strip_resp.hovered() && enabled, p);
    let color = text_color(enabled, p);
    let resp = text_field(ui, rect, edit_id, text, 33.0, enabled, color);
    let mut changed = resp.changed();
    if strip_resp.clicked() && !items.is_empty() {
        open = !open;
    }
    if !enabled {
        open = false;
    }
    if open {
        let current = items.iter().position(|i| i == text);
        if let Some(i) = popup_list(ui, rect, popup, items, current, &mut open, &strip_resp, p) {
            *text = items[i].clone();
            changed = true;
        }
    }
    ui.data_mut(|d| d.insert_temp(popup, open));
    changed
}

// ---------------------------------------------------------------- date and time

/// Which part of a date/time field a key press or the wheel changes.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Section {
    Year,
    Month,
    Day,
    Hour,
    Minute,
    Second,
}

fn step_datetime(t: NaiveDateTime, section: Section, delta: i64) -> NaiveDateTime {
    match section {
        Section::Year => t.with_year(t.year() + delta as i32).unwrap_or(t),
        Section::Month => {
            let m0 = t.month0() as i64 + delta;
            let (y, m) = (t.year() + m0.div_euclid(12) as i32, m0.rem_euclid(12) as u32 + 1);
            let day = t.day().min(days_in_month(y, m));
            NaiveDate::from_ymd_opt(y, m, day).map_or(t, |d| d.and_time(t.time()))
        }
        Section::Day => t + chrono::Duration::days(delta),
        Section::Hour => t + chrono::Duration::hours(delta),
        Section::Minute => t + chrono::Duration::minutes(delta),
        Section::Second => t + chrono::Duration::seconds(delta),
    }
}

pub fn days_in_month(y: i32, m: u32) -> u32 {
    let next = if m == 12 { NaiveDate::from_ymd_opt(y + 1, 1, 1) } else { NaiveDate::from_ymd_opt(y, m + 1, 1) };
    next.and_then(|d| d.pred_opt()).map_or(30, |d| d.day())
}

/// The section of `fmt` (Qt's `yyyy-MM-dd HH:mm:ss` subset) at character `i`.
fn section_at(fmt: &str, i: usize) -> Section {
    let c = fmt.chars().nth(i.min(fmt.len().saturating_sub(1))).unwrap_or('y');
    let c = if matches!(c, '-' | ':' | ' ') { fmt.chars().nth(i.saturating_sub(1)).unwrap_or('y') } else { c };
    match c {
        'y' => Section::Year,
        'M' => Section::Month,
        'd' => Section::Day,
        'H' => Section::Hour,
        'm' => Section::Minute,
        _ => Section::Second,
    }
}

fn qt_format(t: NaiveDateTime, fmt: &str) -> String {
    let f = fmt
        .replace("yyyy", "%Y")
        .replace("MM", "%m")
        .replace("dd", "%d")
        .replace("HH", "%H")
        .replace("mm", "%M")
        .replace("ss", "%S");
    t.format(&f).to_string()
}

fn qt_parse(text: &str, fmt: &str, base: NaiveDateTime) -> Option<NaiveDateTime> {
    let f = fmt
        .replace("yyyy", "%Y")
        .replace("MM", "%m")
        .replace("dd", "%d")
        .replace("HH", "%H")
        .replace("mm", "%M")
        .replace("ss", "%S");
    let text = text.trim();
    if fmt.contains("yyyy") && fmt.contains("HH") {
        NaiveDateTime::parse_from_str(text, &f).ok()
    } else if fmt.contains("yyyy") {
        NaiveDate::parse_from_str(text, &f).ok().map(|d| d.and_time(base.time()))
    } else {
        NaiveTime::parse_from_str(text, &f).ok().map(|t| base.date().and_time(t))
    }
}

/// What a date/time field changed this frame.
#[derive(Default)]
pub struct DateOut {
    pub changed: bool,
}

/// A `QDateEdit` / `QDateTimeEdit` (calendar popup) or `QTimeEdit`
/// (up/down buttons) on `value`, in display format `fmt`.
pub fn datetime_edit(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    value: &mut NaiveDateTime,
    fmt: &str,
    enabled: bool,
    p: &Palette,
) -> bool {
    let before = *value;
    let calendar = fmt.contains("yyyy");
    let edit_id = id.with("edit");
    let popup = id.with("calendar");
    let mut open = ui.data(|d| d.get_temp::<bool>(popup)).unwrap_or(false);
    let focused = ui.memory(|m| m.has_focus(edit_id));
    field_box(ui, rect, focused || open, enabled, p);
    let strip_rect = Rect::from_min_max(pos2(rect.right() - 27.0, rect.top()), rect.max);
    let strip_resp = ui.interact(strip_rect, id.with("strip"), if enabled { Sense::click() } else { Sense::hover() });
    if calendar {
        drop_down(ui, rect, strip_resp.hovered() && enabled, p);
    }

    let text_id = id.with("text");
    let shown = qt_format(*value, fmt);
    let mut text: String = ui.data(|d| d.get_temp::<String>(text_id)).unwrap_or_else(|| shown.clone());
    let color = text_color(enabled, p);
    let resp = text_field(ui, rect, edit_id, &mut text, 33.0, enabled, color);
    // The section under the cursor: arrow keys and the wheel step it.
    let cursor = egui::TextEdit::load_state(ui.ctx(), edit_id)
        .and_then(|s| s.cursor.char_range())
        .map_or(0, |r| r.primary.index.into());
    let section = section_at(fmt, cursor);
    if resp.has_focus() {
        let (up, down, enter) = ui.input(|i| {
            (i.key_pressed(egui::Key::ArrowUp), i.key_pressed(egui::Key::ArrowDown), i.key_pressed(egui::Key::Enter))
        });
        if let Some(t) = qt_parse(&text, fmt, *value) {
            *value = t;
        }
        if up || down {
            *value = step_datetime(*value, section, if up { 1 } else { -1 });
            text = qt_format(*value, fmt);
        }
        if enter {
            text = qt_format(*value, fmt);
        }
        ui.data_mut(|d| d.insert_temp(text_id, text.clone()));
    } else {
        ui.data_mut(|d| d.remove::<String>(text_id));
    }
    if resp.hovered() && enabled {
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
            *value = step_datetime(
                *value,
                if resp.has_focus() {
                    section
                } else if calendar {
                    Section::Year
                } else {
                    Section::Hour
                },
                wheel.signum() as i64,
            );
        }
    }
    if !calendar {
        // Up/down buttons step the current section (hours before a click).
        let [up, down] = step_rects(rect, false);
        let sense = if enabled { Sense::click() } else { Sense::hover() };
        let buttons = [(ui.interact(up, id.with("up"), sense), 1), (ui.interact(down, id.with("down"), sense), -1)];
        step_buttons(ui, rect, false, [0, 1].map(|i| buttons[i].0.hovered() && enabled), p);
        for (b, delta) in &buttons {
            if b.clicked() {
                *value = step_datetime(*value, if focused { section } else { Section::Hour }, *delta);
            }
        }
    } else {
        if strip_resp.clicked() {
            open = !open;
        }
        if !enabled {
            open = false;
        }
        if open && let Some(day) = calendar_popup(ui, rect, popup, value.date(), &mut open, &strip_resp, p) {
            *value = day.and_time(value.time());
        }
        ui.data_mut(|d| d.insert_temp(popup, open));
    }
    *value != before
}

/// Gives a date/time field keyboard focus with its first section (the year)
/// selected, as Qt does for the first control of a newly shown dialog.
pub fn focus_first_section(ctx: &egui::Context, id: Id, len: usize) {
    let edit = id.with("edit");
    let mut state = egui::TextEdit::load_state(ctx, edit).unwrap_or_default();
    state.cursor.set_char_range(Some(egui::text::CCursorRange::two(
        egui::text::CCursor::new(0),
        egui::text::CCursor::new(len),
    )));
    state.store(ctx, edit);
    ctx.memory_mut(|m| m.request_focus(edit));
}

/// `QDateEdit` on a date.
pub fn date_edit(ui: &mut Ui, rect: Rect, id: Id, value: &mut NaiveDate, enabled: bool, p: &Palette) -> bool {
    let mut t = value.and_time(NaiveTime::MIN);
    let changed = datetime_edit(ui, rect, id, &mut t, "yyyy-MM-dd", enabled, p);
    *value = t.date();
    changed
}

/// `QTimeEdit` (`HH:mm:ss`) on a time of day.
pub fn time_edit(ui: &mut Ui, rect: Rect, id: Id, value: &mut NaiveTime, enabled: bool, p: &Palette) -> bool {
    let base = NaiveDate::from_ymd_opt(2000, 1, 1).unwrap();
    let mut t = base.and_time(*value);
    let changed = datetime_edit(ui, rect, id, &mut t, "HH:mm:ss", enabled, p);
    *value = t.time();
    let _ = t.hour();
    changed
}

/// The `QCalendarWidget` popup, 256 × 169, from the field's bottom edge
/// (above it when there is no room): a 29 px navigation bar in the
/// palette's highlight (Fusion's arrows, centred month and year buttons in
/// the stylesheet's text colour, the month's menu arrow at alpha 160), then
/// 32 × 20 cells: day names and ISO weeks on the alternate base, days on the
/// base, the month's weekends red, the chosen day in highlight. Returns the
/// day clicked.
fn calendar_popup(
    ui: &Ui,
    anchor: Rect,
    id: Id,
    current: NaiveDate,
    open: &mut bool,
    owner: &Response,
    p: &Palette,
) -> Option<NaiveDate> {
    let month_id = id.with("month");
    let mut shown: NaiveDate = ui.data(|d| d.get_temp(month_id)).unwrap_or_else(|| current.with_day(1).unwrap());
    let (w, h) = (256.0, 169.0);
    let screen = ui.ctx().content_rect();
    // positionCalendarPopup: at rect().bottomLeft(), one pixel up the edge.
    let below = anchor.bottom() - 1.0;
    let top = if below + h <= screen.bottom() { below } else { anchor.top() - h };
    let top = top.min(screen.bottom() - h).max(screen.top());
    let left = anchor.left().min(screen.right() - w).max(screen.left());
    let frame = snap_origin(ui, Rect::from_min_size(pos2(left, top), vec2(w, h)));
    let red = Color32::from_rgb(0xff, 0, 0);
    let mut picked = None;
    let inner = egui::Area::new(id).order(egui::Order::Foreground).fixed_pos(frame.min).show(ui.ctx(), |ui| {
        ui.allocate_rect(frame, Sense::hover());
        let painter = ui.painter().with_clip_rect(frame);
        let nav = Rect::from_min_size(frame.min, vec2(w, 29.0));
        fill_exact(&painter, snap_rect(ui, nav), CornerRadius::ZERO, p.qt.highlight);
        // qt_fusion_draw_arrow: a solid triangle, about 8 × 14, in the
        // 29 px button (centres measured from the full app).
        let arrow = |r: Rect, key: &str, left: bool| {
            let resp = ui.interact(r, id.with(key), Sense::click());
            let c = pos2(r.left() + if left { 13.8 } else { 14.5 }, r.top() + 14.1);
            let (hw, hh) = (3.9, 7.0);
            let pts = if left {
                vec![pos2(c.x + hw, c.y - hh), pos2(c.x + hw, c.y + hh), pos2(c.x - hw, c.y)]
            } else {
                vec![pos2(c.x - hw, c.y - hh), pos2(c.x - hw, c.y + hh), pos2(c.x + hw, c.y)]
            };
            painter.add(egui::Shape::convex_polygon(pts, Color32::from_rgb(0x0d, 0x0d, 0x0d), Stroke::NONE));
            resp.clicked()
        };
        if arrow(Rect::from_min_size(nav.min, vec2(29.0, 29.0)), "prev", true) {
            shown = step_datetime(shown.and_time(NaiveTime::MIN), Section::Month, -1).date();
        }
        if arrow(Rect::from_min_size(pos2(nav.right() - 29.0, nav.top()), vec2(29.0, 29.0)), "next", false) {
            shown = step_datetime(shown.and_time(NaiveTime::MIN), Section::Month, 1).date();
        }
        // Month and year tool buttons, centred between the arrows:
        // QToolButton::sizeHint is the text and two spaces, plus 13.
        let (month, year) = (shown.format("%B").to_string(), shown.year().to_string());
        let mw = text_width(ui, &month, FONT_PX, Weight::Regular).ceil() + 19.0;
        let yw = text_width(ui, &year, FONT_PX, Weight::Regular).ceil() + 19.0;
        let mx = nav.left() + 29.0 + ((w - 58.0 - mw - yw) / 2.0).floor();
        super::widgets::text_center(ui, pos2(mx + mw / 2.0, nav.center().y), &month, FONT_PX, Weight::Regular, p.text);
        super::widgets::text_center(
            ui,
            pos2(mx + mw + yw / 2.0, nav.center().y),
            &year,
            FONT_PX,
            Weight::Regular,
            p.text,
        );
        // The menu indicator: PE_IndicatorArrowDown in the button's last 16 px.
        let (ix, iy) = (mx + mw - 7.0, nav.top() + 22.0);
        painter.add(egui::Shape::convex_polygon(
            vec![pos2(ix - 4.0, iy - 2.29), pos2(ix + 4.0, iy - 2.29), pos2(ix, iy + 2.29)],
            p.text.gamma_multiply(160.0 / 255.0),
            Stroke::NONE,
        ));
        let wheel: f32 = if ui.rect_contains_pointer(frame) {
            ui.input(|i| {
                i.events
                    .iter()
                    .filter_map(|e| match e {
                        egui::Event::MouseWheel { delta, .. } => Some(delta.y),
                        _ => None,
                    })
                    .sum()
            })
        } else {
            0.0
        };
        if wheel != 0.0 {
            shown = step_datetime(shown.and_time(NaiveTime::MIN), Section::Month, -wheel.signum() as i64).date();
        }
        // The view: header row and week column, then the days.
        let grid_top = nav.bottom();
        let cell = |col: usize, row: usize| {
            Rect::from_min_size(pos2(frame.left() + 32.0 * col as f32, grid_top + 20.0 * row as f32), vec2(32.0, 20.0))
        };
        fill_exact(
            &painter,
            snap_rect(ui, Rect::from_min_max(pos2(frame.left(), grid_top), frame.max)),
            CornerRadius::ZERO,
            p.surface_alt,
        );
        fill_exact(
            &painter,
            snap_rect(ui, Rect::from_min_max(cell(1, 1).min, frame.max)),
            CornerRadius::ZERO,
            p.qt.base,
        );
        for (c, name) in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].iter().enumerate() {
            super::widgets::text_center(
                ui,
                cell(c + 1, 0).center(),
                name,
                FONT_PX,
                Weight::Regular,
                if c >= 5 { red } else { p.text },
            );
        }
        let offset = shown.weekday().num_days_from_monday() as i64;
        let start = shown - chrono::Duration::days(if offset == 0 { 7 } else { offset });
        for row in 0..6 {
            let week_day = start + chrono::Duration::days(row as i64 * 7);
            super::widgets::text_center(
                ui,
                cell(0, row + 1).center(),
                &week_day.iso_week().week().to_string(),
                FONT_PX,
                Weight::Regular,
                p.text,
            );
            for col in 0..7 {
                let day = week_day + chrono::Duration::days(col as i64);
                let r = cell(col + 1, row + 1);
                let resp = ui.interact(r, id.with(("day", day)), Sense::click());
                let in_month = day.month() == shown.month();
                let mut color = if col >= 5 && in_month { red } else { p.text };
                if day == current {
                    fill_exact(&painter, snap_rect(ui, r), CornerRadius::ZERO, p.qt.highlight);
                    color = Color32::WHITE;
                }
                super::widgets::text_center(ui, r.center(), &day.day().to_string(), FONT_PX, Weight::Regular, color);
                if resp.clicked() {
                    picked = Some(day);
                }
            }
        }
    });
    ui.data_mut(|d| d.insert_temp(month_id, shown));
    let clicked_elsewhere = ui.input(|i| i.pointer.any_click())
        && !owner.clicked()
        && !ui
            .input(|i| i.pointer.interact_pos())
            .is_some_and(|pos| inner.response.rect.contains(pos) || frame.contains(pos));
    if picked.is_some() || clicked_elsewhere || ui.input(|i| i.key_pressed(egui::Key::Escape)) {
        *open = false;
        ui.data_mut(|d| d.remove::<NaiveDate>(month_id));
    }
    picked
}

/// `QSpinBox` with an optional suffix and special text for the minimum.
/// Returns true when the value changed.
#[allow(clippy::too_many_arguments)]
pub fn spin_box(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    value: &mut i32,
    range: (i32, i32),
    suffix: &str,
    special: &str,
    enabled: bool,
    p: &Palette,
) -> bool {
    spin_box_step(ui, rect, id, value, range, 1, suffix, special, enabled, p)
}

/// [`spin_box`] with `singleStep` per arrow, key or wheel notch.
#[allow(clippy::too_many_arguments)]
pub fn spin_box_step(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    value: &mut i32,
    range: (i32, i32),
    step: i32,
    suffix: &str,
    special: &str,
    enabled: bool,
    p: &Palette,
) -> bool {
    let before = *value;
    let edit_id = id.with("edit");
    let focused = ui.memory(|m| m.has_focus(edit_id));
    field_box(ui, rect, focused, enabled, p);
    let [up, down] = step_rects(rect, true);
    let sense = if enabled { Sense::click() } else { Sense::hover() };
    let buttons = [(ui.interact(up, id.with("up"), sense), 1), (ui.interact(down, id.with("down"), sense), -1)];
    step_buttons(ui, rect, true, [0, 1].map(|i| buttons[i].0.hovered() && enabled), p);
    for (b, delta) in &buttons {
        if b.clicked() {
            *value += delta * step;
        }
    }
    let display =
        |v: i32| if v == range.0 && !special.is_empty() { special.to_owned() } else { format!("{v}{suffix}") };
    let text_id = id.with("text");
    let mut text: String = ui.data(|d| d.get_temp::<String>(text_id)).unwrap_or_else(|| display(*value));
    let color = text_color(enabled, p);
    let resp = text_field(ui, rect, edit_id, &mut text, 33.0, enabled, color);
    if resp.has_focus() {
        let t = text.trim().trim_end_matches(suffix.trim()).trim();
        if t == special {
            *value = range.0;
        } else if let Ok(v) = t.parse::<i32>() {
            *value = v;
        }
        let (up, down) = ui.input(|i| (i.key_pressed(egui::Key::ArrowUp), i.key_pressed(egui::Key::ArrowDown)));
        if up || down {
            *value += if up { step } else { -step };
            *value = (*value).clamp(range.0, range.1);
            text = display(*value);
        }
        ui.data_mut(|d| d.insert_temp(text_id, text.clone()));
    } else {
        ui.data_mut(|d| d.remove::<String>(text_id));
    }
    if resp.hovered() && enabled {
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
            *value += wheel.signum() as i32 * step;
        }
    }
    *value = (*value).clamp(range.0, range.1);
    *value != before
}

/// `QDoubleSpinBox`: `decimals` places, `step` per arrow or wheel notch,
/// an optional suffix. With `select_all`, the text is selected and focused
/// (`selectAll()` before the dialog shows). Returns true when the value changed.
#[allow(clippy::too_many_arguments)]
pub fn double_spin_box(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    value: &mut f64,
    range: (f64, f64),
    step: f64,
    decimals: usize,
    suffix: &str,
    select_all: bool,
    enabled: bool,
    p: &Palette,
) -> bool {
    let before = *value;
    let edit_id = id.with("edit");
    let focused = ui.memory(|m| m.has_focus(edit_id));
    field_box(ui, rect, focused, enabled, p);
    let [up, down] = step_rects(rect, true);
    let sense = if enabled { Sense::click() } else { Sense::hover() };
    let buttons = [(ui.interact(up, id.with("up"), sense), 1.0), (ui.interact(down, id.with("down"), sense), -1.0)];
    step_buttons(ui, rect, true, [0, 1].map(|i| buttons[i].0.hovered() && enabled), p);
    // QAbstractSpinBox rounds the stepped value to the decimals shown.
    let round = |v: f64| {
        let f = 10f64.powi(decimals as i32);
        (v * f).round() / f
    };
    for (b, sign) in &buttons {
        if b.clicked() {
            *value = round(*value + sign * step);
        }
    }
    let display = |v: f64| format!("{v:.decimals$}{suffix}");
    let text_id = id.with("text");
    let mut text: String = ui.data(|d| d.get_temp::<String>(text_id)).unwrap_or_else(|| display(*value));
    let color = text_color(enabled, p);
    if select_all {
        ui.memory_mut(|m| m.request_focus(edit_id));
        let number_len = display(*value).chars().count() - suffix.chars().count();
        let mut state = egui::text_edit::TextEditState::load(ui.ctx(), edit_id).unwrap_or_default();
        state.cursor.set_char_range(Some(egui::text::CCursorRange::two(
            egui::text::CCursor::new(0),
            egui::text::CCursor::new(number_len),
        )));
        state.store(ui.ctx(), edit_id);
    }
    let resp = text_field(ui, rect, edit_id, &mut text, 33.0, enabled, color);
    if resp.has_focus() {
        let t = text.trim().trim_end_matches(suffix.trim()).trim();
        if let Ok(v) = t.parse::<f64>() {
            *value = v;
        }
        let (up, down) = ui.input(|i| (i.key_pressed(egui::Key::ArrowUp), i.key_pressed(egui::Key::ArrowDown)));
        if up || down {
            *value = round(*value + if up { step } else { -step }).clamp(range.0, range.1);
            text = display(*value);
        }
        ui.data_mut(|d| d.insert_temp(text_id, text.clone()));
    } else {
        ui.data_mut(|d| d.remove::<String>(text_id));
    }
    if resp.hovered() && enabled {
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
            *value = round(*value + wheel.signum() as f64 * step);
        }
    }
    *value = value.clamp(range.0, range.1);
    *value != before
}

/// What a progress bar shows (`QProgressBar` range, value and format).
#[derive(Clone, Debug, PartialEq)]
pub struct Progress {
    pub min: i64,
    pub max: i64,
    pub value: i64,
    /// `%p%` or a fixed text.
    pub format: String,
}

impl Default for Progress {
    fn default() -> Self {
        Progress { min: 0, max: 100, value: 0, format: "%p%".into() }
    }
}

impl Progress {
    pub fn busy() -> Self {
        Progress { min: 0, max: 0, value: 0, format: "%p%".into() }
    }

    pub fn range(max: i64) -> Self {
        Progress { min: 0, max, value: 0, format: "%p%".into() }
    }

    pub fn percent(&self) -> i64 {
        if self.max <= self.min {
            return 0;
        }
        ((self.value - self.min) as f64 * 100.0 / (self.max - self.min) as f64) as i64
    }
}

/// `QProgressBar`: 7 px radius frame, accent chunk, centred text; a busy
/// bar (range 0..0) runs a chunk back and forth.
pub fn progress_bar(ui: &Ui, rect: Rect, prog: &Progress, p: &Palette) {
    qt_box(ui, rect, 7.0, p.input_bg, p.border);
    let inner = rect.shrink(1.0);
    if prog.max <= prog.min {
        let t = ui.input(|i| i.time);
        let w = inner.width() * 0.25;
        let phase = (t * 0.8).rem_euclid(2.0) as f32;
        let x = if phase < 1.0 { phase } else { 2.0 - phase } * (inner.width() - w);
        let chunk = Rect::from_min_size(pos2(inner.left() + x, inner.top()), vec2(w, inner.height()));
        ui.painter().rect_filled(chunk, CornerRadius::same(6), p.accent);
        ui.ctx().request_repaint();
        return;
    }
    let frac = ((prog.value - prog.min) as f64 / (prog.max - prog.min) as f64).clamp(0.0, 1.0) as f32;
    if frac > 0.0 {
        // QStyleSheetStyle truncates the chunk to whole pixels.
        let chunk = Rect::from_min_size(inner.min, vec2((inner.width() * frac).floor(), inner.height()));
        ui.painter().rect_filled(chunk, CornerRadius::same(6), p.accent);
    }
    let text = prog.format.replace("%p", &prog.percent().to_string());
    super::widgets::text_center(ui, rect.center(), &text, FONT_PX, Weight::Regular, p.text);
}

/// The downloader's tab bar (`QTabWidget#DownloaderTabs QTabBar::tab`):
/// 150 px minimum, 6×18 padding, 8 px radius, 6 px apart. Returns the tab
/// clicked.
pub fn tab_bar(
    ui: &Ui,
    origin: Pos2,
    id: Id,
    tabs: &[&str],
    current: usize,
    enabled: &[bool],
    p: &Palette,
) -> Option<usize> {
    let mut x = origin.x;
    let mut clicked = None;
    for (i, title) in tabs.iter().enumerate() {
        let w = tab_width(ui, title);
        let r = Rect::from_min_size(pos2(x, origin.y), vec2(w, 44.0));
        let on = enabled.get(i).copied().unwrap_or(true);
        let resp = ui.interact(r, id.with(i), if on { Sense::click() } else { Sense::hover() });
        let (fill, border, color) = if i == current {
            (p.accent_soft, p.accent, p.text)
        } else if resp.hovered() && on {
            (p.hover, p.border, p.text)
        } else {
            (p.surface_bg, p.border, if on { p.muted } else { p.disabled })
        };
        qt_box(ui, r, 8.0, fill, border);
        super::widgets::text_center(ui, r.center(), title, FONT_PX, Weight::Semibold, color);
        if resp.clicked() && i != current {
            clicked = Some(i);
        }
        x += w + 6.0;
    }
    clicked
}

/// One downloader tab's width: `max(min-width 150, text) + padding + border`.
pub fn tab_width(ui: &Ui, title: &str) -> f32 {
    (text_width(ui, title, FONT_PX, Weight::Semibold).ceil()).max(150.0) + 36.0 + 2.0
}

/// The preview window's `QTabWidget`, which no stylesheet rule styles, as
/// QFusionStyle paints it over the palette: the pane (`PE_FrameTabWidget`:
/// `tabFrameColor`, a `window.darker(140)` outline and a faint inner line)
/// and, with more than one page, a 28 px tab bar overlapping it by 2
/// (`CE_TabBarTabShape`: gradient tabs, the unselected ones 2 px lower, the
/// selected one merged into the pane). Labels take the stylesheet's text
/// colour. Returns the page's rectangle and the tab clicked.
pub fn fusion_tab_widget(
    ui: &Ui,
    rect: Rect,
    id: Id,
    tabs: &[String],
    current: usize,
    p: &Palette,
) -> (Rect, Option<usize>) {
    use crate::theme::{darker, lighter};
    let frame = p.qt.tab_frame();
    let outline = darker(p.qt.window, 140);
    let inner = Color32::from_rgba_unmultiplied(255, 255, 255, 30);
    let many = tabs.len() > 1;
    let painter = ui.painter().with_clip_rect(rect);
    let pane = Rect::from_min_max(pos2(rect.left(), rect.top() + if many { 26.0 } else { 0.0 }), rect.max);
    let stroke = |painter: &egui::Painter, r: Rect, c: Color32, radius: u8| {
        painter.add(
            egui::epaint::RectShape::stroke(r, CornerRadius::same(radius), Stroke::new(1.0, c), StrokeKind::Inside)
                .with_round_to_pixels(false),
        );
    };
    fill_exact(&painter, Rect::from_min_max(pane.min, pane.max - vec2(1.0, 1.0)), CornerRadius::ZERO, frame);
    fill_exact(
        &painter,
        Rect::from_min_max(pos2(pane.left(), pane.bottom() - 1.0), pane.max),
        CornerRadius::ZERO,
        Color32::from_black_alpha(15),
    );
    stroke(&painter, Rect::from_min_max(pane.min, pane.max - vec2(0.0, 1.0)), outline, 0);
    stroke(&painter, Rect::from_min_max(pane.min + vec2(1.0, 1.0), pane.max - vec2(1.0, 2.0)), inner, 0);
    let mut clicked = None;
    if many {
        let top = rect.top();
        let mut x = rect.left();
        let rects: Vec<Rect> = tabs
            .iter()
            .map(|t| {
                let w = text_width(ui, t, FONT_PX, Weight::Regular).ceil() + 24.0;
                let r = Rect::from_min_size(pos2(x, top), vec2(w, 28.0));
                x += w;
                r
            })
            .collect();
        // Unselected tabs first, the selected one over them.
        let order =
            (0..tabs.len()).filter(|&i| i != current).chain(std::iter::once(current).filter(|&i| i < tabs.len()));
        for i in order {
            let selected = i == current;
            let last = i + 1 == tabs.len();
            let r = Rect::from_min_max(rects[i].min, rects[i].max + vec2(if last { 0.0 } else { 1.0 }, 0.0));
            let shape =
                Rect::from_min_max(pos2(r.left(), r.top() + if selected { 0.0 } else { 2.0 }), r.max + vec2(0.0, 3.0));
            let clip = Rect::from_min_max(
                r.min - vec2(1.0, 1.0),
                pos2(r.right() + 1.0, r.bottom() - if selected { 1.0 } else { 2.0 }),
            );
            let tab = painter.with_clip_rect(clip);
            let stops: &[(f32, Color32)] = if selected {
                &[(0.0, lighter(frame, 104)), (1.0, frame)]
            } else {
                &[(0.0, darker(frame, 108)), (0.85, darker(frame, 108)), (1.0, darker(frame, 116))]
            };
            let body = shape.shrink(1.0);
            let mut mesh = egui::Mesh::default();
            for pair in stops.windows(2) {
                let y = |t: f32| r.top() + t * r.height();
                let band = Rect::from_min_max(
                    pos2(body.left(), y(pair[0].0).max(body.top())),
                    pos2(body.right(), y(pair[1].0).min(body.bottom())),
                );
                if band.height() > 0.0 {
                    let k = mesh.vertices.len() as u32;
                    mesh.colored_vertex(band.left_top(), pair[0].1);
                    mesh.colored_vertex(band.right_top(), pair[0].1);
                    mesh.colored_vertex(band.right_bottom(), pair[1].1);
                    mesh.colored_vertex(band.left_bottom(), pair[1].1);
                    mesh.add_triangle(k, k + 1, k + 2);
                    mesh.add_triangle(k, k + 2, k + 3);
                }
            }
            tab.add(mesh);
            stroke(&tab, shape, outline, 2);
            stroke(&tab, Rect::from_min_max(shape.min + vec2(1.0, 1.0), shape.max - vec2(1.0, 0.0)), inner, 2);
            if selected {
                // Over the pane's inner line beneath the tab (the tab's own
                // body already covers the outline).
                fill_exact(
                    &painter,
                    Rect::from_min_max(pos2(r.left() + 1.0, r.bottom() - 1.0), pos2(r.right() - 1.0, r.bottom() + 0.5)),
                    CornerRadius::ZERO,
                    frame,
                );
                for dx in [0.0, r.width() - 2.0, r.width() - 1.0] {
                    fill_exact(
                        &painter,
                        Rect::from_min_size(pos2(r.left() + dx, r.bottom() - 2.0), vec2(1.0, 1.0)),
                        CornerRadius::ZERO,
                        inner,
                    );
                }
            }
            // Measured: both kinds of label centred 15 px below the bar's top.
            let tr = rects[i];
            super::widgets::text_center(
                ui,
                pos2(tr.center().x, top + 15.0),
                &tabs[i],
                FONT_PX,
                Weight::Regular,
                p.text,
            );
            if ui.interact(tr, id.with(i), Sense::click()).clicked() {
                clicked = Some(i);
            }
        }
    }
    (pane.shrink(2.0), clicked)
}

/// A `QSplitterHandle` (Fusion): a row of grip dots in its middle.
pub fn splitter_handle(ui: &Ui, rect: Rect, id: Id) -> Response {
    let resp = ui.interact(rect, id, Sense::drag());
    if resp.hovered() || resp.dragged() {
        ui.ctx().set_cursor_icon(egui::CursorIcon::ResizeVertical);
    }
    // QFusionStyle CE_Splitter: six grips from the handle's integer centre,
    // each a 2 × 2 lightShade (white, alpha 90) with a 1 × 1 darkShade
    // (black, alpha 60) on top, filled on whole device pixels.
    let ppp = ui.ctx().pixels_per_point();
    let snap = |r: Rect| {
        Rect::from_min_max(
            pos2((r.min.x * ppp).round() / ppp, (r.min.y * ppp).round() / ppp),
            pos2((r.max.x * ppp).round() / ppp, (r.max.y * ppp).round() / ppp),
        )
    };
    let cx = rect.left() + ((rect.width() - 1.0) / 2.0).floor();
    let cy = rect.top() + ((rect.height() - 1.0) / 2.0).floor();
    for i in (-6..12).step_by(3) {
        let at = pos2(cx + i as f32, cy);
        fill_exact(
            ui.painter(),
            snap(Rect::from_min_size(at, vec2(2.0, 2.0))),
            CornerRadius::ZERO,
            Color32::from_rgba_unmultiplied(255, 255, 255, 90),
        );
        fill_exact(
            ui.painter(),
            snap(Rect::from_min_size(at, vec2(1.0, 1.0))),
            CornerRadius::ZERO,
            Color32::from_rgba_unmultiplied(0, 0, 0, 60),
        );
    }
    resp
}

/// Text color for disabled vs enabled labels.
/// A control's text colour. The app-wide stylesheet sets `color` on
/// labels, fields, check boxes and radio buttons with no `:disabled` rule,
/// so there they keep the text colour when disabled; only push buttons grey.
pub fn text_color(enabled: bool, p: &Palette) -> Color32 {
    if enabled || app_style() { p.text } else { p.disabled }
}

/// The wheel over `area`: three 20 px lines a notch (`QScrollArea`).
pub fn wheel_scroll(ui: &Ui, area: Rect, offset: &mut f32, max: f32) {
    if !ui.rect_contains_pointer(area) || max <= 0.0 {
        return;
    }
    let wheel: f32 = ui.input(|i| {
        i.events
            .iter()
            .filter_map(|e| match e {
                egui::Event::MouseWheel { unit, delta, .. } => Some(match unit {
                    egui::MouseWheelUnit::Line => delta.y * 60.0,
                    egui::MouseWheelUnit::Page => delta.y * area.height(),
                    egui::MouseWheelUnit::Point => delta.y,
                }),
                _ => None,
            })
            .sum()
    });
    *offset = (*offset - wheel).clamp(0.0, max);
}

/// The app's `QScrollBar` (10 px, transparent, a rounded handle in the
/// border colour, 4 px end margins, at least 24 px long).
#[allow(clippy::too_many_arguments)]
pub fn scroll_bar(ui: &Ui, bar: Rect, id: Id, vertical: bool, offset: &mut f32, content: f32, view: f32, p: &Palette) {
    let max = (content - view).max(0.0);
    if max <= 0.0 {
        return;
    }
    let groove = if vertical {
        Rect::from_min_max(pos2(bar.left(), bar.top() + 4.0), pos2(bar.right(), bar.bottom() - 4.0))
    } else {
        Rect::from_min_max(pos2(bar.left() + 4.0, bar.top()), pos2(bar.right() - 4.0, bar.bottom()))
    };
    let track = if vertical { groove.height() } else { groove.width() };
    let len = (track * view / content).max(24.0).min(track);
    let start = (track - len) * (*offset / max).clamp(0.0, 1.0);
    let handle = if vertical {
        Rect::from_min_size(pos2(groove.left(), groove.top() + start), vec2(groove.width(), len))
    } else {
        Rect::from_min_size(pos2(groove.left() + start, groove.top()), vec2(len, groove.height()))
    };
    let resp = ui.interact(handle, id, Sense::drag());
    if resp.dragged() {
        let d = if vertical { resp.drag_delta().y } else { resp.drag_delta().x };
        *offset = (*offset + d * max / (track - len).max(1.0)).clamp(0.0, max);
    }
    ui.painter().rect_filled(handle, 5.0, p.border);
}

/// `QPlainTextEdit` (or a plain-text `QTextEdit`): the field frame, the
/// text 9 + 4 px in and 7 + 4 px down (viewport inset and document
/// margin), wrapped at the viewport, the app's scroll bar when it
/// overflows. Read-only fields can still be selected and copied. Returns
/// true when the text changed.
#[allow(clippy::too_many_arguments)]
pub fn plain_text_edit(
    ui: &mut Ui,
    rect: Rect,
    id: Id,
    text: &mut String,
    placeholder: &str,
    read_only: bool,
    p: &Palette,
) -> bool {
    let focused = ui.memory(|m| m.has_focus(id));
    field_box(ui, rect, focused, true, p);
    let inner = Rect::from_min_max(rect.min + vec2(9.0, 7.0), rect.max - vec2(9.0, 7.0));
    let offset_id = id.with("scroll");
    let mut offset: f32 = ui.data(|d| d.get_temp(offset_id)).unwrap_or(0.0);
    let content_id = id.with("content_h");
    let content_h: f32 = ui.data(|d| d.get_temp(content_id)).unwrap_or(0.0);
    let bar = content_h > inner.height();
    let view = if bar { Rect::from_min_max(inner.min, pos2(inner.right() - 10.0, inner.bottom())) } else { inner };
    let max = (content_h - view.height()).max(0.0);
    wheel_scroll(ui, view, &mut offset, max);
    if bar {
        let bar_rect = Rect::from_min_max(pos2(view.right(), inner.top()), inner.max);
        scroll_bar(ui, bar_rect, id.with("vbar"), true, &mut offset, content_h, view.height(), p);
    }
    offset = offset.clamp(0.0, max);
    let mut changed = false;
    let mut out_h = content_h;
    ui.scope_builder(egui::UiBuilder::new().max_rect(view), |ui| {
        ui.set_clip_rect(view.intersect(ui.clip_rect()));
        let mut style = (**ui.style()).clone();
        style.spacing.item_spacing = vec2(0.0, 0.0);
        style.visuals.selection.bg_fill = Color32::from_rgb(0x14, 0x6f, 0xda);
        style.visuals.selection.stroke.color = Color32::WHITE;
        ui.set_style(style);
        let top = view.top() + 4.0 - offset;
        let edit_rect = Rect::from_min_size(
            pos2(view.left() + 4.0 - 2.0, top),
            vec2(view.width() - 8.0 + 4.0, f32::INFINITY.min(1.0e6)),
        );
        let mut child = ui.new_child(egui::UiBuilder::new().max_rect(edit_rect));
        let resp = if read_only {
            let mut shown = text.as_str();
            egui::TextEdit::multiline(&mut shown)
                .id(id)
                .font(ui_font(FONT_PX, Weight::Regular))
                .frame(egui::Frame::NONE)
                .margin(egui::Margin { left: 2, right: 2, top: 0, bottom: 0 })
                .desired_width(view.width() - 8.0)
                .desired_rows(1)
                .text_color(p.text)
                .show(&mut child)
        } else {
            egui::TextEdit::multiline(text)
                .id(id)
                .font(ui_font(FONT_PX, Weight::Regular))
                .frame(egui::Frame::NONE)
                .margin(egui::Margin { left: 2, right: 2, top: 0, bottom: 0 })
                .desired_width(view.width() - 8.0)
                .desired_rows(1)
                .text_color(p.text)
                .show(&mut child)
        };
        changed = resp.response.changed();
        out_h = resp.galley.size().y + 8.0;
        // Clicking the empty part of the field focuses it too.
        let bg = ui.interact(view, id.with("bg"), Sense::click());
        if bg.clicked() {
            ui.memory_mut(|m| m.request_focus(id));
        }
    });
    if text.is_empty() && !placeholder.is_empty() {
        let ph = placeholder_color(p);
        for (k, line) in placeholder.split('\n').enumerate() {
            let cy = view.top() + 4.0 + k as f32 * LINE + LINE / 2.0;
            text_left(ui, view.left() + 4.0, cy, line, FONT_PX, Weight::Regular, ph);
        }
    }
    ui.data_mut(|d| {
        d.insert_temp(offset_id, offset);
        d.insert_temp(content_id, out_h);
    });
    changed
}
