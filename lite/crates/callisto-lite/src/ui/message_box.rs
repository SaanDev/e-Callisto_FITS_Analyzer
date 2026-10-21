//! `QMessageBox`: an icon, the text, an informative text, the buttons in
//! the platform's order and, when there is one, the detailed text behind
//! Show Details... — laid out as `QMessageBoxPrivate::setupLayout` and
//! `updateSize` lay it out (measured on the full app, see
//! `lite/tools/capture_help.py`).

use egui::{Color32, Id, Pos2, Rect, Ui, ViewportBuilder, ViewportId, pos2, vec2};

use super::qlayout::{Align, BoxLayout, GridLayout, Item, Layout, Policy, Size, VAlign, WidgetSpec};
use super::{qt, widgets};
use crate::fonts::{Weight, ui_font};
use crate::theme::Palette;

/// One line of 12 px text.
const LINE: i32 = 16;
/// `updateSize`'s soft limit on the width before the text wraps.
const SOFT_LIMIT: i32 = 500;
/// Its hard limit (`qMin(screen width - 480, 1000)` on a large screen).
const HARD_LIMIT: i32 = 1000;
/// The informative label's contents margins, above and below.
const INFORMATIVE_MARGIN: i32 = 7;
/// The detailed text's height (`detailsText->setFixedHeight(100)`).
const DETAILS_H: i32 = 100;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Icon {
    NoIcon,
    Information,
    Warning,
    Critical,
    Question,
    /// The application icon at 64 × 64 (`QMessageBox::about`).
    App,
}

/// `QMessageBox::ButtonRole`, for the order the buttons are shown in.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Role {
    Reset,
    Yes,
    Accept,
    Alternate,
    Destructive,
    No,
    Action,
    Reject,
    Apply,
    Help,
}

impl Role {
    /// Where the role sits in the platform's button box layout
    /// (`QPlatformDialogHelper::buttonLayout`): Windows' everywhere but
    /// macOS, where the affirmative button comes last.
    fn order(self) -> i32 {
        if cfg!(target_os = "macos") {
            match self {
                Role::Help => 0,
                Role::Reset | Role::Apply | Role::Action => 1,
                Role::Destructive => 2,
                Role::Alternate => 3,
                Role::Reject | Role::No => 4,
                Role::Accept | Role::Yes => 5,
            }
        } else {
            match self {
                Role::Reset => 0,
                Role::Yes => 1,
                Role::Accept => 2,
                Role::Alternate => 3,
                Role::Destructive => 4,
                Role::No => 5,
                Role::Action => 6,
                Role::Reject => 7,
                Role::Apply => 8,
                Role::Help => 9,
            }
        }
    }
}

#[derive(Clone, Debug)]
pub struct MessageBox {
    pub title: String,
    pub icon: Icon,
    pub text: String,
    pub informative: Option<String>,
    pub details: Option<String>,
    pub buttons: Vec<(String, Role)>,
    /// The button Enter presses; the first accepting one otherwise.
    pub default: Option<usize>,
    /// The button Escape and the window's close button press; Qt's rule
    /// (the only button, else the rejecting one, else No) otherwise.
    pub escape: Option<usize>,
    details_shown: bool,
}

/// What a frame of the box gave.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Answer {
    /// The button at this index of [`MessageBox::buttons`].
    Button(usize),
    /// Closed without a button (no escape button).
    Closed,
}

const DETAILS_SHOW: &str = "Show Details...";
const DETAILS_HIDE: &str = "Hide Details...";

impl MessageBox {
    pub fn new(icon: Icon, title: &str, text: &str) -> Self {
        MessageBox {
            title: title.to_owned(),
            icon,
            text: text.to_owned(),
            informative: None,
            details: None,
            buttons: Vec::new(),
            default: None,
            escape: None,
            details_shown: false,
        }
    }

    /// The box `QMessageBox.information(parent, title, text)` and its kin show.
    pub fn ok(icon: Icon, title: &str, text: &str) -> Self {
        Self::new(icon, title, text).button("OK", Role::Accept)
    }

    pub fn informative(mut self, text: &str) -> Self {
        self.informative = Some(text.to_owned());
        self
    }

    pub fn details(mut self, text: &str) -> Self {
        self.details = Some(text.to_owned());
        self
    }

    pub fn button(mut self, label: &str, role: Role) -> Self {
        self.buttons.push((label.to_owned(), role));
        self
    }

    pub fn default_button(mut self, i: usize) -> Self {
        self.default = Some(i);
        self
    }

    pub fn escape_button(mut self, i: usize) -> Self {
        self.escape = Some(i);
        self
    }

    /// Shows or hides the detailed text (Show Details...).
    pub fn set_details_shown(&mut self, shown: bool) {
        self.details_shown = shown;
    }

    fn escape_index(&self) -> Option<usize> {
        if self.escape.is_some() {
            return self.escape;
        }
        if self.buttons.len() == 1 {
            return Some(0);
        }
        self.buttons
            .iter()
            .position(|(_, r)| *r == Role::Reject)
            .or_else(|| self.buttons.iter().position(|(_, r)| *r == Role::No))
    }

    fn default_index(&self) -> Option<usize> {
        self.default.or_else(|| self.buttons.iter().position(|(_, r)| matches!(r, Role::Accept | Role::Yes)))
    }

    /// The buttons as shown, left to right: `(index, label)`, the details
    /// toggle as `None` (it is added as an action button before the others
    /// when the detailed text is set first, as here).
    fn shown_buttons(&self) -> Vec<(Option<usize>, &str)> {
        let mut items: Vec<(i32, usize, Option<usize>, &str)> = Vec::new();
        if self.details.is_some() {
            let label = if self.details_shown { DETAILS_HIDE } else { DETAILS_SHOW };
            items.push((Role::Action.order(), 0, None, label));
        }
        for (i, (label, role)) in self.buttons.iter().enumerate() {
            items.push((role.order(), i + 1, Some(i), label.as_str()));
        }
        items.sort_by_key(|&(order, seq, _, _)| (order, seq));
        items.into_iter().map(|(_, _, i, l)| (i, l)).collect()
    }

    fn icon_size(&self) -> i32 {
        match self.icon {
            Icon::NoIcon => 0,
            Icon::App => 64,
            _ => 48,
        }
    }

    /// The grid `setupLayout` builds, with the label wrapping or not and the
    /// informative label as `updateSize` sets them.
    fn grid(&self, ctx: &egui::Context, wrap_label: bool) -> Layout {
        let icon = self.icon_size();
        let text_col = if icon > 0 { 2 } else { 1 };
        let mut g = GridLayout::new([11; 4], 6, 6);
        if icon > 0 {
            g.items.push((
                Item::Widget(WidgetSpec::new("icon", Size::new(icon, icon), Policy::Fixed, Policy::Fixed)),
                0,
                0,
                2,
                1,
                Align { h: None, v: Some(VAlign::Top) },
            ));
        }
        let indent = if icon > 0 { 7 } else { 15 };
        g = g.add(
            Item::Spacer { size: Size::new(indent, 1), hpolicy: Policy::Fixed, vpolicy: Policy::Fixed },
            0,
            if icon > 0 { 1 } else { 0 },
            2,
            1,
        );
        let label_policy = if self.informative.is_some() { Policy::Minimum } else { Policy::Preferred };
        g = g.widget(label_spec(ctx, "label", &self.text, wrap_label, 0, label_policy), 0, text_col);
        if let Some(info) = &self.informative {
            g = g.widget(label_spec(ctx, "informative", info, true, INFORMATIVE_MARGIN, Policy::Minimum), 1, text_col);
        }
        let mut buttons = BoxLayout::h([0; 4], 6).stretch(1);
        let details_w = [DETAILS_SHOW, DETAILS_HIDE].iter().map(|t| text_w(ctx, t) + 28).max().unwrap_or(0);
        for (i, label) in self.shown_buttons() {
            let id = match i {
                Some(i) => BUTTON_IDS[i.min(BUTTON_IDS.len() - 1)],
                None => "details_button",
            };
            let spec = match i {
                Some(_) => WidgetSpec::new(id, Size::new(text_w(ctx, label) + 28, 46), Policy::Minimum, Policy::Fixed)
                    .min_height(46),
                None => WidgetSpec::new(id, Size::new(details_w, 46), Policy::Fixed, Policy::Fixed),
            };
            buttons = buttons.widget(spec);
        }
        let cols = text_col + 1;
        g = g.add(buttons.into_item(), 2, 0, 1, cols);
        if self.details.is_some() && self.details_shown {
            let details = BoxLayout::v([0; 4], 6)
                .widget(WidgetSpec::new("details_line", Size::new(-1, 3), Policy::Minimum, Policy::Fixed))
                .widget(
                    WidgetSpec::new("details", Size::new(256, DETAILS_H), Policy::Expanding, Policy::Expanding)
                        .min_height(DETAILS_H)
                        .max_height(DETAILS_H),
                );
            g = g.add(details.into_item(), 3, 0, 1, cols);
        }
        Layout::Grid(g)
    }

    /// `updateSize`: the box's fixed size.
    pub fn size(&self, ctx: &egui::Context) -> Size {
        let mut grid = self.grid(ctx, false);
        let mut width = grid.minimum_size().w;
        if width > SOFT_LIMIT {
            grid = self.grid(ctx, true);
            width = SOFT_LIMIT.max(grid.minimum_size().w).min(HARD_LIMIT);
        }
        if self.informative.is_some() {
            width = width.max(grid.minimum_size().w).min(HARD_LIMIT);
        }
        // The window title must fit (`QMdiSubWindowTitleBar` font + 50).
        width = width.max((text_w(ctx, &self.title) + 50).min(HARD_LIMIT));
        let hfw = grid.height_for_width(width);
        let height = if hfw >= 0 { hfw } else { grid.minimum_size().h };
        Size::new(width, height)
    }

    fn wraps(&self, ctx: &egui::Context) -> bool {
        self.grid(ctx, false).minimum_size().w > SOFT_LIMIT
    }

    /// The box's contents in `ui`; the answer once a button is pressed.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<Answer> {
        let ctx = ui.ctx().clone();
        let wrap = self.wraps(&ctx);
        let size = self.size(&ctx);
        let r = ui.max_rect();
        let g = self.grid(&ctx, wrap).layout_window(size);
        let rect = |id: &str| g.get(id).map(|i| i.to_egui(r.min));
        if let Some(icon) = rect("icon") {
            draw_icon(ui, icon, self.icon);
        }
        if let Some(l) = rect("label") {
            let lines = wrap_lines(&ctx, &self.text, if wrap { l.width() } else { f32::INFINITY });
            let h = lines.len() as f32 * LINE as f32;
            let top = l.top() + ((l.height() - h) / 2.0).max(0.0);
            draw_lines(ui, l.left(), top, &lines, p.text);
        }
        if let (Some(l), Some(info)) = (rect("informative"), &self.informative) {
            let lines = wrap_lines(&ctx, info, l.width());
            draw_lines(ui, l.left(), l.top() + INFORMATIVE_MARGIN as f32, &lines, p.text);
        }
        if let (Some(line), Some(d), Some(text)) = (rect("details_line"), rect("details"), &self.details) {
            // A sunken `QFrame::HLine`.
            let y = line.center().y.floor();
            ui.painter().rect_filled(
                Rect::from_min_max(pos2(line.left(), y), pos2(line.right(), y + 1.0)),
                0.0,
                p.border,
            );
            qt::qt_box(ui, d, 10.0, p.input_bg, p.border);
            let inner = Rect::from_min_max(d.min + vec2(9.0 + 4.0, 7.0 + 4.0), d.max - vec2(9.0 + 4.0, 7.0));
            let lines = wrap_lines(&ctx, text, inner.width());
            let painter = ui.painter().with_clip_rect(inner.expand2(vec2(4.0, 0.0)));
            for (k, l) in lines.iter().enumerate() {
                let y = inner.top() + k as f32 * LINE as f32 + LINE as f32 / 2.0;
                let g = ctx.fonts_mut(|f| f.layout_no_wrap(l.clone(), ui_font(qt::FONT_PX, Weight::Regular), p.text));
                painter.galley(pos2(inner.left(), y - g.size().y / 2.0), g, p.text);
            }
        }
        let mut answer = None;
        let shown: Vec<(Option<usize>, String)> =
            self.shown_buttons().into_iter().map(|(i, l)| (i, l.to_owned())).collect();
        for (i, label) in shown {
            let id = match i {
                Some(i) => BUTTON_IDS[i.min(BUTTON_IDS.len() - 1)],
                None => "details_button",
            };
            let Some(b) = rect(id) else { continue };
            if qt::button(ui, b, Id::new(("message_box", id)), &label, false, true, p).clicked() {
                match i {
                    Some(i) => answer = Some(Answer::Button(i)),
                    None => {
                        self.details_shown = !self.details_shown;
                        let s = self.size(&ctx);
                        ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(vec2(s.w as f32, s.h as f32)));
                    }
                }
            }
        }
        if answer.is_none() {
            if ui.input(|i| i.key_pressed(egui::Key::Enter)) {
                answer = self.default_index().map(Answer::Button);
            } else if ui.input(|i| i.key_pressed(egui::Key::Escape)) {
                answer = self.escape_index().map(Answer::Button);
            }
        }
        answer
    }

    /// The box in its own window, under `id`. Closing the window presses
    /// the escape button.
    pub fn show(&mut self, ctx: &egui::Context, id: &str, p: &Palette) -> Option<Answer> {
        let size = self.size(ctx);
        let builder = ViewportBuilder::default()
            .with_title(self.title.clone())
            .with_inner_size(vec2(size.w as f32, size.h as f32))
            .with_resizable(false)
            .with_minimize_button(false)
            .with_maximize_button(false);
        let bg = p.page_bg;
        ctx.show_viewport_immediate(ViewportId::from_hash_of(("message_box", id)), builder, |ctx, _| {
            let mut out = None;
            egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| out = self.frame(ui, p));
            if out.is_none() && ctx.input(|i| i.viewport().close_requested()) {
                out = Some(self.escape_index().map(Answer::Button).unwrap_or(Answer::Closed));
            }
            out
        })
    }
}

const BUTTON_IDS: [&str; 8] = ["button0", "button1", "button2", "button3", "button4", "button5", "button6", "button7"];

fn text_w(ctx: &egui::Context, text: &str) -> i32 {
    ctx.fonts_mut(|f| f.layout_no_wrap(text.to_owned(), ui_font(qt::FONT_PX, Weight::Regular), Color32::WHITE).size().x)
        .ceil() as i32
}

/// The width of `text` in the UI font. (Qt's DirectWrite advances run a
/// little under the face's design widths; egui's are closer to them.)
fn width_f(ctx: &egui::Context, text: &str) -> f32 {
    if text.is_empty() {
        return 0.0;
    }
    ctx.fonts_mut(|f| f.layout_no_wrap(text.to_owned(), ui_font(qt::FONT_PX, Weight::Regular), Color32::WHITE).size().x)
}

/// `QTextDocument::adjustSize` on plain text: the width Qt settles on
/// for a word-wrapped label's size hint.
fn adjusted_width(ctx: &egui::Context, text: &str) -> i32 {
    let mw = width_f(ctx, "x").floor() as i32 * 80;
    let height = |w: i32| wrap_lines(ctx, text, w as f32).len() as i64 * LINE as i64;
    let ideal =
        |w: i32| wrap_lines(ctx, text, w as f32).iter().map(|l| width_f(ctx, l)).fold(0.0f32, f32::max).ceil() as i32;
    let mut w = mw;
    let h = height(mw);
    if h != 0 {
        w = ((5 * h * mw as i64 / 3) as f64).sqrt() as i32;
        let h2 = height(w.min(mw));
        if (w as i64) * 3 < 5 * h2 {
            w = ((2 * h2 * w as i64) as f64).sqrt() as i32;
        }
        w = w.min(mw);
    }
    ideal(w)
}

/// Qt's line breaks for plain text at `width`: the text's own lines, each
/// wrapped where UAX #14 allows (after spaces, hyphens, slashes).
pub fn wrap_lines(ctx: &egui::Context, text: &str, width: f32) -> Vec<String> {
    let mut out = Vec::new();
    for para in text.split('\n') {
        let chars: Vec<char> = para.chars().collect();
        let mut words: Vec<String> = Vec::new();
        let mut start = 0;
        for i in 0..chars.len() {
            if crate::help::guide::break_after(&chars, i) || i + 1 == chars.len() {
                words.push(chars[start..=i].iter().collect());
                start = i + 1;
            }
        }
        let mut line = String::new();
        for w in words {
            let candidate = format!("{line}{w}");
            if !line.is_empty() && width_f(ctx, candidate.trim_end()) > width {
                out.push(line.trim_end().to_owned());
                line = w;
            } else {
                line = candidate;
            }
        }
        out.push(line.trim_end().to_owned());
    }
    out
}

/// The widest piece of `text` that cannot be broken.
fn longest_word(ctx: &egui::Context, text: &str) -> i32 {
    let mut best = 0.0f32;
    for para in text.split('\n') {
        let chars: Vec<char> = para.chars().collect();
        let mut start = 0;
        for i in 0..chars.len() {
            if crate::help::guide::break_after(&chars, i) || i + 1 == chars.len() {
                let w: String = chars[start..=i].iter().collect();
                best = best.max(width_f(ctx, w.trim_end()));
                start = i + 1;
            }
        }
    }
    best.ceil() as i32
}

/// A `QLabel` of plain text: one line per `\n` unless it wraps, `margin`
/// above and below.
fn label_spec(
    ctx: &egui::Context,
    id: &'static str,
    text: &str,
    wrap: bool,
    margin: i32,
    hpolicy: Policy,
) -> WidgetSpec {
    let natural = text.split('\n').map(|l| width_f(ctx, l)).fold(0.0f32, f32::max).ceil() as i32;
    let lines = text.split('\n').count() as i32;
    let spec = WidgetSpec::new(id, Size::new(natural, lines * LINE + 2 * margin), hpolicy, Policy::Preferred);
    if !wrap {
        return spec;
    }
    let owned = text.to_owned();
    let ctx2 = ctx.clone();
    let hint_w = adjusted_width(ctx, text);
    let hint = Size::new(hint_w, wrap_lines(ctx, text, hint_w as f32).len() as i32 * LINE + 2 * margin);
    let min_w = longest_word(ctx, text);
    WidgetSpec::new(id, hint, hpolicy, Policy::Preferred)
        .min_hint(Size::new(min_w, hint.h))
        .hfw(std::rc::Rc::new(move |w| wrap_lines(&ctx2, &owned, w as f32).len() as i32 * LINE + 2 * margin))
}

fn draw_lines(ui: &Ui, x: f32, top: f32, lines: &[String], color: Color32) {
    for (k, l) in lines.iter().enumerate() {
        let cy = top + k as f32 * LINE as f32 + LINE as f32 / 2.0;
        widgets::text_left(ui, x, cy, l, qt::FONT_PX, Weight::Regular, color);
    }
}

/// The standard icons, drawn after Windows 11's (a gradient disc or
/// triangle with a white glyph), and the application icon.
fn draw_icon(ui: &Ui, rect: Rect, icon: Icon) {
    let painter = ui.painter();
    let c = rect.center();
    let r = rect.width() / 2.0;
    let disc = |top: Color32, bottom: Color32| {
        // A vertical gradient across the disc, as a fan.
        let mut mesh = egui::Mesh::default();
        let ring = 64;
        let centre_color = lerp(top, bottom, 0.5);
        mesh.colored_vertex(c, centre_color);
        for k in 0..=ring {
            let a = k as f32 / ring as f32 * std::f32::consts::TAU;
            let q = c + vec2(a.cos(), a.sin()) * r;
            let t = ((q.y - (c.y - r)) / (2.0 * r)).clamp(0.0, 1.0);
            mesh.colored_vertex(q, lerp(top, bottom, t));
        }
        for k in 0..ring {
            mesh.add_triangle(0, k + 1, k + 2);
        }
        painter.add(mesh);
        // The mesh has hard edges: an anti-aliased rim smooths them.
        painter.circle_stroke(c, r - 0.3, egui::Stroke::new(0.8, lerp(top, bottom, 0.6)));
    };
    match icon {
        Icon::NoIcon => {}
        Icon::App => {
            let tex = app_icon_texture(ui.ctx());
            if let Some(t) = tex {
                painter.image(t.id(), rect, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
            }
        }
        Icon::Information | Icon::Question => {
            disc(Color32::from_rgb(0x1a, 0x9b, 0xe1), Color32::from_rgb(0x01, 0x79, 0xd4));
            if icon == Icon::Information {
                let s = r / 24.0;
                painter.rect_filled(
                    Rect::from_min_max(c + vec2(-1.6 * s, -3.5 * s), c + vec2(1.6 * s, 11.0 * s)),
                    1.6 * s,
                    Color32::WHITE,
                );
                painter.circle_filled(c + vec2(0.0, -10.0 * s), 2.3 * s, Color32::WHITE);
            } else {
                glyph(ui, c, "?", r, Color32::WHITE);
            }
        }
        Icon::Critical => {
            disc(Color32::from_rgb(0xe8, 0x45, 0x45), Color32::from_rgb(0xc4, 0x1e, 0x1e));
            let d = r * 0.32;
            let stroke = egui::Stroke::new(r * 0.12, Color32::WHITE);
            painter.line_segment([c + vec2(-d, -d), c + vec2(d, d)], stroke);
            painter.line_segment([c + vec2(-d, d), c + vec2(d, -d)], stroke);
        }
        Icon::Warning => {
            let top = pos2(c.x, rect.top() + r * 0.12);
            let left = pos2(rect.left() + r * 0.04, rect.bottom() - r * 0.16);
            let right = pos2(rect.right() - r * 0.04, rect.bottom() - r * 0.16);
            painter.add(egui::Shape::convex_polygon(
                vec![top, right, left],
                Color32::from_rgb(0xff, 0xc8, 0x3d),
                egui::Stroke::new(r * 0.08, Color32::from_rgb(0xf2, 0xb1, 0x1c)),
            ));
            glyph(ui, c + vec2(0.0, r * 0.18), "!", r * 0.9, Color32::from_rgb(0x1f, 0x1f, 0x1f));
        }
    }
}

fn glyph(ui: &Ui, center: Pos2, text: &str, r: f32, color: Color32) {
    ui.painter().text(center, egui::Align2::CENTER_CENTER, text, ui_font(r * 1.15, Weight::Bold), color);
}

fn lerp(a: Color32, b: Color32, t: f32) -> Color32 {
    let m = |x: u8, y: u8| (x as f32 + (y as f32 - x as f32) * t).round() as u8;
    Color32::from_rgb(m(a.r(), b.r()), m(a.g(), b.g()), m(a.b(), b.b()))
}

fn app_icon_texture(ctx: &egui::Context) -> Option<egui::TextureHandle> {
    let id = Id::new("message_box_app_icon");
    if let Some(t) = ctx.data(|d| d.get_temp::<egui::TextureHandle>(id)) {
        return Some(t);
    }
    let icon = crate::icons::app_icon()?;
    let img = image::RgbaImage::from_raw(icon.width, icon.height, icon.rgba)?;
    // Qt picks the icon's pixmap for 64 × 64 at the screen's scale.
    let px = (64.0 * ctx.pixels_per_point()).round() as u32;
    let img = image::imageops::resize(&img, px, px, image::imageops::FilterType::Lanczos3);
    let color = egui::ColorImage::from_rgba_unmultiplied([px as usize, px as usize], img.as_raw());
    let t = ctx.load_texture("message_box_app_icon", color, egui::TextureOptions::LINEAR);
    ctx.data_mut(|d| d.insert_temp(id, t.clone()));
    Some(t)
}
