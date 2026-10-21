//! Dialog windows: message boxes and the frequency-combine options.
//!
//! Each opens as its own window, as Qt's dialogs do (egui draws it inside
//! the main window where the platform has no separate windows).

use callisto_core::radio::combine::{CombineOptions, FrequencyRelation, GapFill, Overlap};
use callisto_core::radio::naming::basename;
use callisto_core::radio::processing::RfiSettings;
use egui::{Color32, Id, Rect, Sense, Ui, ViewportBuilder, ViewportId, pos2, vec2};

use super::message_box::{Answer, Icon, MessageBox, Role};
use super::widgets::{self, ButtonKind};
use crate::fonts::{Weight, ui_font};
use crate::session::{Notice, NoticeKind, PendingCombine};
use crate::theme::Palette;

const MARGIN: f32 = 12.0;
const BUTTON_W: f32 = 88.0;
const BUTTON_H: f32 = 32.0;

/// Text wrapped to `width`, as a QLabel with word wrap lays it out.
fn wrapped(ui: &Ui, text: &str, width: f32, color: Color32) -> std::sync::Arc<egui::Galley> {
    ui.ctx().fonts_mut(|f| f.layout(text.to_owned(), ui_font(12.0, Weight::Regular), color, width))
}

/// Run `body` in a dialog window. Returns `body`'s result; `None` from
/// `body` keeps the dialog open, closing the window gives `on_close`.
pub(crate) fn dialog_window<R>(
    ctx: &egui::Context,
    id: &str,
    title: &str,
    size: egui::Vec2,
    p: &Palette,
    on_close: R,
    body: impl FnOnce(&mut Ui) -> Option<R>,
) -> Option<R> {
    let viewport = ViewportId::from_hash_of(id);
    let builder = ViewportBuilder::default()
        .with_title(title)
        .with_inner_size(size)
        .with_resizable(false)
        .with_minimize_button(false)
        .with_maximize_button(false);
    let bg = p.page_bg;
    let mut body = Some(body);
    let mut on_close = Some(on_close);
    ctx.show_viewport_immediate(viewport, builder, move |ctx, _class| {
        let mut out = None;
        egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| {
            if let Some(body) = body.take() {
                out = body(ui);
            }
        });
        if out.is_none() && ctx.input(|i| i.viewport().close_requested()) {
            out = on_close.take();
        }
        out
    })
}

/// `QMessageBox`: icon, text, OK. Returns true once dismissed.
pub fn message_box(ctx: &egui::Context, notice: &Notice, p: &Palette) -> bool {
    let icon = match notice.kind {
        NoticeKind::Information => Icon::Information,
        NoticeKind::Warning => Icon::Warning,
        NoticeKind::Critical => Icon::Critical,
    };
    let mut b = MessageBox::ok(icon, &notice.title, &notice.text);
    b.show(ctx, &format!("notice:{}:{}", notice.title, notice.text), p).is_some()
}

/// `QMessageBox.question`: the question icon, the text, and `buttons` in
/// order, `default` taking Enter. Returns the clicked button's index;
/// closing the window or Escape picks the last (Cancel).
pub fn question_box(
    ctx: &egui::Context,
    title: &str,
    text: &str,
    buttons: &[&str],
    default: usize,
    p: &Palette,
) -> Option<usize> {
    let mut b = MessageBox::new(Icon::Question, title, text);
    for label in buttons {
        b = b.button(label, Role::Action);
    }
    let mut b = b.default_button(default).escape_button(buttons.len().saturating_sub(1));
    match b.show(ctx, &format!("question:{title}:{text}"), p)? {
        Answer::Button(i) => Some(i),
        Answer::Closed => Some(buttons.len().saturating_sub(1)),
    }
}

/// `FrequencyCombineOptionsDialog`.
pub struct CombineOptionsDialog {
    relation: FrequencyRelation,
    gap_fill: usize,
    overlap: usize,
    connection: f64,
    connection_range: (f64, f64),
}

const GAP_FILLS: [(GapFill, &str); 4] = [
    (GapFill::Background, "Interpolated background fill"),
    (GapFill::Hatched, "Gray-hatched blank gap"),
    (GapFill::Average, "Average edge background fill"),
    (GapFill::Zero, "Zero fill"),
];

impl CombineOptionsDialog {
    pub fn new(pending: &PendingCombine) -> Self {
        let relation = pending.relation.clone().unwrap_or_default();
        let (lo, hi) = relation
            .overlaps
            .iter()
            .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), o| (lo.min(o.low), hi.max(o.high)));
        let range = if lo.is_finite() && hi.is_finite() { (lo.min(hi), lo.max(hi)) } else { (0.0, 10000.0) };
        let connection = if relation.overlaps.is_empty() { 0.0 } else { 0.5 * (range.0 + range.1) };
        Self { relation, gap_fill: 0, overlap: 0, connection, connection_range: range }
    }

    fn summary(&self) -> String {
        let mut lines = vec!["The selected files can be frequency-combined, but require a merge choice.".to_owned()];
        let join = |items: &[callisto_core::radio::combine::BandRelation]| {
            items.iter().map(|g| format!("{:.3}-{:.3} MHz", g.low, g.high)).collect::<Vec<_>>().join(", ")
        };
        if self.relation.has_gap() {
            lines.push(format!("Gap detected: {}.", join(&self.relation.gaps)));
        }
        if self.relation.has_overlap() {
            lines.push(format!("Overlap detected: {}.", join(&self.relation.overlaps)));
        }
        lines.join("\n")
    }

    fn overlap_labels(&self) -> Vec<String> {
        let one = self.relation.overlaps.len() == 1;
        let low = if one {
            format!("Use lower-frequency file ({})", basename(&self.relation.overlaps[0].lower_file))
        } else {
            "Use lower-frequency file(s)".into()
        };
        let high = if one {
            format!("Use higher-frequency file ({})", basename(&self.relation.overlaps[0].higher_file))
        } else {
            "Use higher-frequency file(s)".into()
        };
        vec!["Split at connection frequency".into(), low, high, "Reject overlap".into()]
    }

    fn options(&self) -> CombineOptions {
        let overlap = [Overlap::Split, Overlap::Low, Overlap::High, Overlap::Reject][self.overlap];
        let split = self.relation.has_overlap() && overlap == Overlap::Split;
        CombineOptions {
            gap_fill: GAP_FILLS[self.gap_fill].0,
            overlap_policy: overlap,
            overlap_connection_mhz: split.then_some(self.connection),
        }
    }

    /// `Some(Some(options))` on OK, `Some(None)` on Cancel, `None` while open.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Option<CombineOptions>> {
        let width = 520.0;
        let summary = self.summary();
        let group_h = |rows: usize| 30.0 + rows as f32 * 40.0;
        let mut height = MARGIN * 2.0 + 16.0 * (summary.lines().count() as f32) + 12.0 + BUTTON_H;
        if self.relation.has_gap() {
            height += group_h(1) + 8.0;
        }
        if self.relation.has_overlap() {
            height += group_h(2) + 8.0;
        }
        let overlap_labels = self.overlap_labels();
        dialog_window(
            ctx,
            "frequency_combine_options",
            "Frequency Combine Options",
            vec2(width, height),
            p,
            None,
            |ui| {
                let full = ui.max_rect();
                let mut y = full.top() + MARGIN;
                let galley = wrapped(ui, &summary, width - 2.0 * MARGIN, p.text);
                let h = galley.size().y;
                ui.painter().galley(pos2(full.left() + MARGIN, y), galley, p.text);
                y += h + 12.0;
                let label_w = 150.0;
                let field_x = full.left() + MARGIN + 12.0 + label_w;
                let field_w = full.right() - MARGIN - 12.0 - field_x;
                let group = |ui: &Ui, y: f32, title: &str, rows: usize| {
                    let r =
                        Rect::from_min_size(pos2(full.left() + MARGIN, y), vec2(width - 2.0 * MARGIN, group_h(rows)));
                    widgets::qt_box(ui, r, 10.0, p.surface_bg, p.border);
                    widgets::text_left(ui, r.left() + 12.0, r.top() + 14.0, title, 12.0, Weight::Semibold, p.text);
                    r.top() + 30.0
                };
                if self.relation.has_gap() {
                    let row = group(ui, y, "Frequency Gap", 1);
                    widgets::text_left(
                        ui,
                        full.left() + MARGIN + 12.0,
                        row + 16.0,
                        "Gap handling",
                        12.0,
                        Weight::Regular,
                        p.text,
                    );
                    let labels: Vec<&str> = GAP_FILLS.iter().map(|(_, l)| *l).collect();
                    let r = Rect::from_min_size(pos2(field_x, row), vec2(field_w, 32.0));
                    if let Some(i) = widgets::combo(ui, r, Id::new("combine_gap_fill"), &labels, self.gap_fill, true, p)
                    {
                        self.gap_fill = i;
                    }
                    y += group_h(1) + 8.0;
                }
                if self.relation.has_overlap() {
                    let row = group(ui, y, "Frequency Overlap", 2);
                    widgets::text_left(
                        ui,
                        full.left() + MARGIN + 12.0,
                        row + 16.0,
                        "Overlap handling",
                        12.0,
                        Weight::Regular,
                        p.text,
                    );
                    let labels: Vec<&str> = overlap_labels.iter().map(String::as_str).collect();
                    let r = Rect::from_min_size(pos2(field_x, row), vec2(field_w, 32.0));
                    if let Some(i) = widgets::combo(ui, r, Id::new("combine_overlap"), &labels, self.overlap, true, p) {
                        self.overlap = i;
                    }
                    let row2 = row + 40.0;
                    widgets::text_left(
                        ui,
                        full.left() + MARGIN + 12.0,
                        row2 + 16.0,
                        "Connection frequency",
                        12.0,
                        Weight::Regular,
                        p.text,
                    );
                    let r = Rect::from_min_size(pos2(field_x, row2), vec2(field_w, 32.0));
                    let enabled = self.overlap == 0;
                    let (lo, hi) = self.connection_range;
                    ui.scope_builder(egui::UiBuilder::new().max_rect(r), |ui| {
                        ui.add_enabled(
                            enabled,
                            egui::DragValue::new(&mut self.connection)
                                .range(lo..=hi)
                                .fixed_decimals(3)
                                .suffix(" MHz")
                                .speed(0.01),
                        );
                    });
                    ui.allocate_rect(r, Sense::hover());
                }
                let cancel = Rect::from_min_size(
                    pos2(full.right() - MARGIN - BUTTON_W, full.bottom() - MARGIN - BUTTON_H),
                    vec2(BUTTON_W, BUTTON_H),
                );
                let ok = cancel.translate(vec2(-BUTTON_W - 6.0, 0.0));
                if widgets::push_button(ui, ok, Id::new("combine_ok"), "OK", ButtonKind::Normal, true, p).clicked() {
                    return Some(Some(self.options()));
                }
                if widgets::push_button(ui, cancel, Id::new("combine_cancel"), "Cancel", ButtonKind::Normal, true, p)
                    .clicked()
                    || ui.input(|i| i.key_pressed(egui::Key::Escape))
                {
                    return Some(None);
                }
                None
            },
        )
    }
}

/// `FitsHeaderViewerDialog`: the header as plain text, read-only, with
/// Save as .txt and Close.
pub struct HeaderViewer {
    pub title: String,
    pub default_name: String,
    pub text: String,
}

/// What the header viewer asked for this frame.
pub enum HeaderViewerAction {
    Close,
    Save,
}

impl HeaderViewer {
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<HeaderViewerAction> {
        let id = format!("fits_header:{}", self.title);
        let title = self.title.clone();
        dialog_window(ctx, &id, &title, vec2(900.0, 650.0), p, HeaderViewerAction::Close, |ui| {
            let full = ui.max_rect().shrink(9.0);
            let save_w = widgets::dialog_button_width(ui, "Save as .txt");
            let close_w = widgets::dialog_button_width(ui, "Close");
            let button_h = 46.0;
            let text_rect = Rect::from_min_max(full.min, pos2(full.right(), full.bottom() - button_h - 6.0));
            widgets::qt_box(ui, text_rect, 10.0, p.input_bg, p.border);
            let inner = Rect::from_min_max(text_rect.min + vec2(9.0, 7.0), text_rect.max - vec2(9.0, 7.0));
            ui.scope_builder(egui::UiBuilder::new().max_rect(inner), |ui| {
                egui::ScrollArea::both().auto_shrink(false).show(ui, |ui| {
                    let mut text = self.text.as_str();
                    ui.add(
                        egui::TextEdit::multiline(&mut text)
                            .font(ui_font(12.0, Weight::Regular))
                            .frame(egui::Frame::NONE)
                            .desired_width(f32::INFINITY)
                            .text_color(p.text),
                    );
                });
            });
            let close =
                Rect::from_min_size(pos2(full.right() - close_w, full.bottom() - button_h), vec2(close_w, button_h));
            let save = Rect::from_min_size(pos2(close.left() - 6.0 - save_w, close.top()), vec2(save_w, button_h));
            if widgets::dialog_button(ui, save, Id::new("header_save"), "Save as .txt", true, p).clicked() {
                return Some(HeaderViewerAction::Save);
            }
            if widgets::dialog_button(ui, close, Id::new("header_close"), "Close", true, p).clicked() {
                return Some(HeaderViewerAction::Close);
            }
            None
        })
    }
}

/// A form row with a spin box: label, id, range, step, decimals.
type SpinRow = (&'static str, &'static str, (f64, f64), f64, usize);

/// `RFIControlDialog`: the RFI cleaning settings, non-modal.
pub struct RfiDialog {
    pub enabled: bool,
    pub kernel_time: f64,
    pub kernel_freq: f64,
    pub z_threshold: f64,
    pub percentile: f64,
}

pub enum RfiAction {
    Preview(RfiSettings),
    Apply(RfiSettings),
    Reset,
    Close,
}

impl RfiDialog {
    pub fn new(s: &RfiSettings) -> Self {
        let mut d = Self { enabled: true, kernel_time: 3.0, kernel_freq: 3.0, z_threshold: 6.0, percentile: 99.5 };
        d.set(s);
        d
    }

    pub fn set(&mut self, s: &RfiSettings) {
        self.enabled = s.enabled;
        self.kernel_time = s.kernel_time as f64;
        self.kernel_freq = s.kernel_freq as f64;
        self.z_threshold = s.channel_z_threshold;
        self.percentile = s.percentile_clip;
    }

    fn values(&self) -> RfiSettings {
        RfiSettings {
            enabled: self.enabled,
            kernel_time: self.kernel_time.round() as usize,
            kernel_freq: self.kernel_freq.round() as usize,
            channel_z_threshold: self.z_threshold,
            percentile_clip: self.percentile,
        }
    }

    pub fn show(&mut self, ctx: &egui::Context, masked_text: &str, p: &Palette) -> Option<RfiAction> {
        let masked_text = masked_text.to_owned();
        dialog_window(ctx, "rfi_cleaning", "RFI Cleaning", vec2(420.0, 280.0), p, RfiAction::Close, |ui| {
            let full = ui.max_rect().shrink(9.0);
            let mut y = full.top();
            let r = Rect::from_min_size(pos2(full.left(), y), vec2(full.width(), 23.0));
            let mut enabled = self.enabled;
            if widgets::checkbox(ui, r, Id::new("rfi_enabled"), "Enable RFI cleaning", &mut enabled, true, p) {
                self.enabled = enabled;
            }
            y += 23.0 + 6.0;
            let label_w = 140.0;
            let rows: [SpinRow; 4] = [
                ("Kernel (time)", "rfi_kt", (1.0, 31.0), 2.0, 0),
                ("Kernel (freq)", "rfi_kf", (1.0, 31.0), 2.0, 0),
                ("Channel Z threshold", "rfi_z", (0.5, 20.0), 0.5, 2),
                ("Percentile clip", "rfi_pct", (90.0, 99.99), 0.1, 2),
            ];
            for (i, (label, key, range, step, decimals)) in rows.into_iter().enumerate() {
                let r = Rect::from_min_size(pos2(full.left(), y), vec2(full.width(), 32.0));
                widgets::text_left(ui, r.left(), r.center().y, label, 12.0, Weight::Regular, p.text);
                let field = Rect::from_min_max(pos2(r.left() + label_w, r.top()), r.max);
                let value = match i {
                    0 => &mut self.kernel_time,
                    1 => &mut self.kernel_freq,
                    2 => &mut self.z_threshold,
                    _ => &mut self.percentile,
                };
                widgets::spin_field(ui, field, Id::new(key), value, range, step, decimals, "", true, p);
                y += 32.0 + 6.0;
            }
            widgets::text_left(ui, full.left(), y + 8.0, &masked_text, 12.0, Weight::Regular, p.text);

            let h = 46.0;
            let top = full.bottom() - h;
            let mut x = full.left();
            let mut action = None;
            for (label, key) in [("Preview", "rfi_preview"), ("Apply", "rfi_apply"), ("Reset", "rfi_reset")] {
                let w = widgets::dialog_button_width(ui, label);
                let r = Rect::from_min_size(pos2(x, top), vec2(w, h));
                if widgets::dialog_button(ui, r, Id::new(key), label, true, p).clicked() {
                    action = Some(match key {
                        "rfi_preview" => RfiAction::Preview(self.values()),
                        "rfi_apply" => RfiAction::Apply(self.values()),
                        _ => RfiAction::Reset,
                    });
                }
                x += w + 6.0;
            }
            let w = widgets::dialog_button_width(ui, "Close");
            let r = Rect::from_min_size(pos2(full.right() - w, top), vec2(w, h));
            if widgets::dialog_button(ui, r, Id::new("rfi_close"), "Close", true, p).clicked() {
                action = Some(RfiAction::Close);
            }
            action
        })
    }
}

/// `DisplayRangeDialog`: the result as seconds from the file start, or UT
/// clock times (seconds of the day) for the caller to place.
pub enum DisplayRangeResult {
    Seconds { time: (f64, f64), freq: (f64, f64) },
    Ut { seconds_of_day: (i64, i64), freq: (f64, f64) },
}

pub struct DisplayRangeDialog {
    req: crate::session::DisplayRangeRequest,
    ut: bool,
    start_s: f64,
    stop_s: f64,
    start_ut: i64,
    stop_ut: i64,
    freq_start: f64,
    freq_stop: f64,
}

impl DisplayRangeDialog {
    pub fn new(req: crate::session::DisplayRangeRequest) -> Self {
        let [t0, t1, f0, f1] = req.initial;
        let sod = |v: f64| (v.round_ties_even() as i64).rem_euclid(86400);
        let (start_ut, stop_ut) = req.ut_start_sec.map_or((0, 0), |u| (sod(u + t0), sod(u + t1)));
        Self { req, ut: req.ut_mode, start_s: t0, stop_s: t1, start_ut, stop_ut, freq_start: f0, freq_stop: f1 }
    }

    /// `Some(Some(..))` on OK, `Some(None)` on Cancel.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Option<DisplayRangeResult>> {
        let has_ut = self.req.ut_start_sec.is_some();
        let lines = if has_ut { 2.0 } else { 3.0 };
        let group_h = 7.0 + 12.0 + 32.0 + 6.0 + 32.0 + 12.0;
        let height = 2.0 * 9.0 + lines * 22.0 + 29.0 + 3.0 * (group_h + 6.0) + 46.0;
        dialog_window(ctx, "display_range", "Set Display Range", vec2(420.0, height), p, None, |ui| {
            let full = ui.max_rect().shrink(9.0);
            let mut y = full.top();
            let (tx0, tx1) = self.req.full.xlim;
            let (fy0, fy1) = self.req.full.ylim;
            let mut lines = vec![
                format!("Time range: {tx0:.3} to {tx1:.3} s"),
                format!("Frequency range: {fy0:.3} to {fy1:.3} MHz"),
            ];
            if !has_ut {
                lines.push("UT input is unavailable because this file has no TIME-OBS value.".into());
            }
            for line in &lines {
                widgets::text_left(ui, full.left(), y + 11.0, line, 12.0, Weight::Regular, p.text);
                y += 22.0;
            }
            let r = Rect::from_min_size(pos2(full.left(), y + 3.0), vec2(200.0, 23.0));
            if widgets::radio(ui, r, Id::new("dr_seconds"), "Use seconds from file start", !self.ut, true, p) {
                self.ut = false;
            }
            let r = Rect::from_min_size(pos2(full.left() + 210.0, y + 3.0), vec2(180.0, 23.0));
            if widgets::radio(ui, r, Id::new("dr_ut"), "Use UT clock time", self.ut, has_ut, p) {
                self.ut = true;
            }
            y += 29.0;
            let form_rows = |ui: &mut Ui, inner: Rect| {
                let a = Rect::from_min_size(inner.min, vec2(inner.width(), 32.0));
                let b = Rect::from_min_size(pos2(inner.left(), inner.top() + 38.0), vec2(inner.width(), 32.0));
                widgets::text_left(ui, a.left(), a.center().y, "Start", 12.0, Weight::Regular, p.text);
                widgets::text_left(ui, b.left(), b.center().y, "Stop", 12.0, Weight::Regular, p.text);
                let fx = inner.left() + 50.0;
                (Rect::from_min_max(pos2(fx, a.top()), a.max), Rect::from_min_max(pos2(fx, b.top()), b.max))
            };
            let big = (-1_000_000_000.0, 1_000_000_000.0);
            for (title, key) in [("Seconds", 0), ("UT", 1), ("Frequency", 2)] {
                let rect = Rect::from_min_size(pos2(full.left(), y), vec2(full.width(), group_h));
                let enabled = match key {
                    0 => !self.ut,
                    1 => self.ut && has_ut,
                    _ => true,
                };
                let inner = widgets::group_box(ui, rect, title, enabled, p);
                let (a, b) = form_rows(ui, inner);
                match key {
                    0 => {
                        widgets::spin_field(ui, a, Id::new("dr_s0"), &mut self.start_s, big, 1.0, 3, " s", enabled, p);
                        widgets::spin_field(ui, b, Id::new("dr_s1"), &mut self.stop_s, big, 1.0, 3, " s", enabled, p);
                    }
                    1 => {
                        widgets::time_field(ui, a, Id::new("dr_u0"), &mut self.start_ut, enabled, p);
                        widgets::time_field(ui, b, Id::new("dr_u1"), &mut self.stop_ut, enabled, p);
                    }
                    _ => {
                        widgets::spin_field(
                            ui,
                            a,
                            Id::new("dr_f0"),
                            &mut self.freq_start,
                            big,
                            1.0,
                            3,
                            " MHz",
                            true,
                            p,
                        );
                        widgets::spin_field(ui, b, Id::new("dr_f1"), &mut self.freq_stop, big, 1.0, 3, " MHz", true, p);
                    }
                }
                y += group_h + 6.0;
            }
            let cancel = Rect::from_min_size(pos2(full.right() - BUTTON_W, full.bottom() - 46.0), vec2(BUTTON_W, 46.0));
            let ok = cancel.translate(vec2(-BUTTON_W - 6.0, 0.0));
            if widgets::dialog_button(ui, ok, Id::new("dr_ok"), "OK", true, p).clicked() {
                let freq = (self.freq_start, self.freq_stop);
                return Some(Some(if self.ut && has_ut {
                    DisplayRangeResult::Ut { seconds_of_day: (self.start_ut, self.stop_ut), freq }
                } else {
                    DisplayRangeResult::Seconds { time: (self.start_s, self.stop_s), freq }
                }));
            }
            if widgets::dialog_button(ui, cancel, Id::new("dr_cancel"), "Cancel", true, p).clicked()
                || ui.input(|i| i.key_pressed(egui::Key::Escape))
            {
                return Some(None);
            }
            None
        })
    }
}

/// `QInputDialog.getText` / `getItem`: one line of text, or a choice.
pub struct InputDialog {
    pub title: String,
    pub label: String,
    pub items: Option<Vec<String>>,
    pub text: String,
    pub choice: usize,
    window: Option<egui::Vec2>,
    first_frame: bool,
}

impl InputDialog {
    pub fn text(title: &str, label: &str) -> Self {
        Self {
            title: title.into(),
            label: label.into(),
            items: None,
            text: String::new(),
            choice: 0,
            window: None,
            first_frame: true,
        }
    }

    pub fn item(title: &str, label: &str, items: Vec<String>) -> Self {
        Self {
            title: title.into(),
            label: label.into(),
            items: Some(items),
            text: String::new(),
            choice: 0,
            window: None,
            first_frame: true,
        }
    }

    /// The label, the field and the button box (`QInputDialogPrivate::ensureLayout`).
    fn layout(&self, ui: &Ui) -> super::qlayout::Layout {
        use super::qlayout::{BoxLayout, Policy, Size, WidgetSpec};
        let label_w = super::qt::text_w_hint(ui, &self.label);
        let label = WidgetSpec::new("input_label", Size::new(label_w, 16), Policy::Minimum, Policy::Fixed);
        let field = match &self.items {
            Some(items) => {
                let refs: Vec<&str> = items.iter().map(String::as_str).collect();
                super::qt::combo_spec(ui, "input_field", &refs)
            }
            None => super::qt::line_edit_spec("input_field"),
        };
        let buttons = BoxLayout::h([0; 4], 6)
            .stretch(1)
            .widget(super::qt::button_spec(ui, "input_ok", "OK", false))
            .widget(super::qt::button_spec(ui, "input_cancel", "Cancel", false));
        BoxLayout::v([11; 4], 6).widget(label).widget(field).add(buttons.into_item()).into()
    }

    /// The contents; `Some(Some(value))` on OK, `Some(None)` on Cancel.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<String>> {
        super::qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<String>> {
        let layout = self.layout(ui);
        let r = ui.max_rect();
        let g = layout.layout_window(super::qlayout::Size::new(r.width().round() as i32, r.height().round() as i32));
        let at = |id: &str| g.rect(id).to_egui(r.min);
        let label = at("input_label");
        widgets::text_left(ui, label.left(), label.center().y, &self.label, 12.0, Weight::Regular, p.text);
        let field = at("input_field");
        let mut enter = false;
        let first = std::mem::take(&mut self.first_frame);
        match &self.items {
            Some(items) => {
                // The combo box has the focus when the dialog opens.
                if first {
                    ui.memory_mut(|m| m.request_focus(Id::new("input_choice")));
                }
                if let Some(i) =
                    super::qt::combo(ui, field, Id::new("input_choice"), items, Some(self.choice), "", true, p)
                {
                    self.choice = i;
                }
            }
            None => {
                super::qt::line_edit(ui, field, Id::new("input_text"), &mut self.text, "", true, p);
                ui.memory_mut(|m| m.request_focus(Id::new("input_text")));
                enter = ui.input(|i| i.key_pressed(egui::Key::Enter));
            }
        }
        if super::qt::button(ui, at("input_ok"), Id::new("input_ok"), "OK", false, true, p).clicked() || enter {
            let value = match &self.items {
                Some(items) => items.get(self.choice).cloned().unwrap_or_default(),
                None => self.text.clone(),
            };
            return Some(Some(value));
        }
        if super::qt::button(ui, at("input_cancel"), Id::new("input_cancel"), "Cancel", false, true, p).clicked()
            || ui.input(|i| i.key_pressed(egui::Key::Escape))
        {
            return Some(None);
        }
        None
    }

    /// `Some(Some(value))` on OK, `Some(None)` on Cancel.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Option<String>> {
        let id = format!("input:{}", self.title);
        let title = self.title.clone();
        let size = self.window.unwrap_or(vec2(273.0, 138.0));
        dialog_window(ctx, &id, &title, size, p, None, |ui| {
            let hint = self.layout(ui).size_hint();
            let want = vec2(hint.w as f32, hint.h as f32);
            if self.window != Some(want) {
                self.window = Some(want);
                ui.ctx().send_viewport_cmd(egui::ViewportCommand::InnerSize(want));
            }
            self.frame(ui, p)
        })
    }
}
