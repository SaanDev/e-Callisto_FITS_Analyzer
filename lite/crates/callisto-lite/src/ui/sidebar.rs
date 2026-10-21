//! The left sidebar: the full app's section cards, in the same order, sizes
//! and states.
//!
//! Geometry follows `src/ui/app/main_window.py` and
//! `src/ui/widgets/collapsible_sections.py`: the panel has 10 px margins and
//! 10 px between cards; each card is a 49 px header plus a body inset by
//! 14 px left/right/bottom, whose layout has a 2 px top margin and 8 px
//! spacing. Qt adds no spacing before a fixed spacer (`addSpacing`), only
//! after it, and `Column` does the same.

use std::collections::BTreeMap;

use egui::scroll_area::ScrollBarVisibility;
use egui::{CornerRadius, Id, Rect, ScrollArea, Sense, Shape, Stroke, StrokeKind, Ui, UiBuilder, pos2, vec2};

use super::widgets::{self, Arrow, ButtonKind};
use crate::fonts::Weight;
use crate::menus::Command;
use crate::session::{BACKGROUND_METHODS, ClipScale, NOISE_SLIDER_MAX, Session};
use crate::theme::Palette;
use callisto_core::plot::Colormap;

/// `QScrollArea#SidebarScroll` width: SIDEBAR_W (268) + 16.
pub const SIDEBAR_W: f32 = 284.0;
const SCROLLBAR_W: f32 = 8.0;
const PANEL_MARGIN: f32 = 10.0;
const CARD_GAP: f32 = 10.0;
const CARD_W: f32 = 267.0;
const HEADER_H: f32 = 49.0;
const BODY_SIDE: f32 = 14.0;
const BODY_TOP: f32 = 2.0;
const BODY_BOTTOM: f32 = 14.0;
const SPACING: f32 = 8.0;

/// The sidebar's own control state; dataset values live in the `Session`.
pub struct SidebarState {
    pub colormaps: Vec<&'static str>,
    /// "Default", then the installed families (`QFontDatabase.families()`).
    pub font_families: Vec<String>,
    pub graph_title: String,
    pub analysis_summary: String,
    /// `timeline_step_combo`: observations added per click.
    pub timeline_step: usize,
    scroll_to: Option<f32>,
}

impl Default for SidebarState {
    fn default() -> Self {
        Self {
            colormaps: Colormap::names().collect(),
            font_families: vec!["Default".into()],
            graph_title: String::new(),
            analysis_summary: "No analysis session loaded.".into(),
            timeline_step: 1,
            scroll_to: None,
        }
    }
}

/// What the sidebar depends on besides its own controls.
pub struct SidebarContext<'a> {
    pub session: &'a mut Session,
    pub sections: &'a mut BTreeMap<String, bool>,
    pub commands: &'a mut Vec<Command>,
}

impl SidebarContext<'_> {
    fn has_data(&self) -> bool {
        self.session.has_data()
    }
}

/// Draws one card's body.
type CardBody = fn(&mut Ui, &mut Column, &mut SidebarState, &mut SidebarContext<'_>, &Palette);

/// Vertical layout with Qt's QBoxLayout spacing rules.
struct Column {
    x: f32,
    w: f32,
    y: f32,
    pending_spacing: bool,
}

impl Column {
    fn new(x: f32, y: f32, w: f32) -> Self {
        Self { x, w, y, pending_spacing: false }
    }

    fn next(&mut self, h: f32) -> Rect {
        if self.pending_spacing {
            self.y += SPACING;
        }
        let r = Rect::from_min_size(pos2(self.x, self.y), vec2(self.w, h));
        self.y += h;
        self.pending_spacing = true;
        r
    }

    /// `QBoxLayout::addSpacing`: no spacing before it, the usual one after.
    fn spacer(&mut self, h: f32) {
        self.y += h;
    }
}

pub fn show(ui: &mut Ui, rect: Rect, st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) {
    ui.painter().rect_filled(rect, 0.0, p.sidebar_panel);
    let viewport = Rect::from_min_max(rect.min, pos2(rect.right() - SCROLLBAR_W, rect.bottom()));

    let mut area = ScrollArea::vertical()
        .id_salt("sidebar_scroll")
        .auto_shrink(false)
        .content_margin(0)
        .scroll_bar_visibility(ScrollBarVisibility::AlwaysHidden);
    if let Some(offset) = st.scroll_to.take() {
        area = area.vertical_scroll_offset(offset);
    }
    let out = ui
        .scope_builder(UiBuilder::new().max_rect(viewport), |ui| {
            area.show(ui, |ui| {
                // Qt scrolls by whole pixels; egui can leave a fractional offset.
                let origin = ui.cursor().min;
                let origin = pos2(origin.x, viewport.top() + (origin.y - viewport.top()).round());
                let bottom = cards(ui, origin, st, cx, p);
                let height = (bottom - origin.y).max(viewport.height());
                ui.allocate_exact_size(vec2(viewport.width(), height), Sense::hover());
            })
        })
        .inner;
    scrollbar(ui, rect, out.state.offset.y, out.content_size.y, viewport.height(), st, p);
}

/// Draws every card from `origin`; returns the bottom of the panel content.
fn cards(ui: &mut Ui, origin: egui::Pos2, st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) -> f32 {
    let x = origin.x + PANEL_MARGIN;
    let mut y = origin.y + PANEL_MARGIN;
    let order: [(&str, CardBody); 8] = [
        ("Timeline", timeline),
        ("Background Subtraction", background),
        ("Noise Clipping Thresholds", noise_thresholds),
        ("Units", units),
        ("Axis", axis),
        ("Graph Properties", graph_properties),
        ("Analysis Summary", analysis_summary),
        ("Ruler Measurement", ruler),
    ];
    for (i, (title, body)) in order.into_iter().enumerate() {
        if i > 0 {
            y += CARD_GAP;
        }
        y = card(ui, x, y, title, body, st, cx, p);
    }
    y + PANEL_MARGIN
}

#[allow(clippy::too_many_arguments)]
fn card(
    ui: &mut Ui,
    x: f32,
    y: f32,
    title: &str,
    body: CardBody,
    st: &mut SidebarState,
    cx: &mut SidebarContext<'_>,
    p: &Palette,
) -> f32 {
    let expanded = cx.sections.get(title).copied().unwrap_or(true);
    let background = ui.painter().add(Shape::Noop);

    let header = Rect::from_min_size(pos2(x, y), vec2(CARD_W, HEADER_H));
    let resp = ui.interact(header, Id::new(("section", title)), Sense::click());
    if resp.hovered() {
        ui.painter().rect_filled(header, CornerRadius::same(12), p.control);
    }
    let arrow_center = pos2(x + 24.0, y + 24.2);
    if expanded {
        widgets::arrow(ui, arrow_center, Arrow::Down, 3.4, 1.75, p.heading);
    } else {
        widgets::arrow(ui, arrow_center, Arrow::Right, 3.4, 1.75, p.heading);
    }
    widgets::text_left(ui, x + 42.0, header.center().y, title, 15.0, Weight::Semibold, p.heading);
    if resp.clicked() {
        cx.sections.insert(title.to_owned(), !expanded);
    }

    let mut bottom = header.bottom();
    if expanded {
        let mut col = Column::new(x + BODY_SIDE, header.bottom() + BODY_TOP, CARD_W - 2.0 * BODY_SIDE);
        body(ui, &mut col, st, cx, p);
        bottom = col.y + BODY_BOTTOM;
    }

    let rect = Rect::from_min_max(pos2(x, y), pos2(x + CARD_W, bottom));
    let fill = if expanded { p.card } else { p.card_collapsed };
    ui.painter().set(
        background,
        Shape::Rect(egui::epaint::RectShape::new(
            rect,
            CornerRadius::same(12),
            fill,
            Stroke::new(1.0, p.hairline),
            StrokeKind::Inside,
        )),
    );
    bottom
}

// ------------------------------------------------------------- the cards

fn label(ui: &Ui, col: &mut Column, text: &str, p: &Palette) {
    let r = col.next(16.0);
    widgets::text_left(ui, r.left(), r.center().y, text, 12.0, Weight::Regular, p.card_text);
}

/// A word-wrapping `QLabel`: 16 px per line.
fn wrapped_label(ui: &Ui, col: &mut Column, text: &str, p: &Palette) {
    let lines = super::qt::wrap_lines(ui.ctx(), text, col.w, Weight::Regular);
    let r = col.next(16.0 * lines.len().max(1) as f32);
    for (i, line) in lines.iter().enumerate() {
        let y = r.top() + 8.0 + 16.0 * i as f32;
        widgets::text_left(ui, r.left(), y, line, 12.0, Weight::Regular, p.card_text);
    }
}

/// `QLabel#SectionLabel` / `[section="true"]`: margin-top 10, margin-bottom 2.
fn section_label(ui: &Ui, col: &mut Column, text: &str, p: &Palette) {
    let r = col.next(26.0);
    // Qt indents a label that has stylesheet margins by about 2.7 px.
    widgets::text_left(ui, r.left() + 8.0 / 3.0, r.top() + 17.0, text, 10.0, Weight::Bold, p.card_muted);
}

/// `#TimelineStatusLabel` / `#BackgroundStatusLabel`: 11 px, padding-top 2.
fn status_label(ui: &Ui, col: &mut Column, text: &str, height: f32, p: &Palette) {
    let r = col.next(height);
    // padding-top: 2px, then the text centred in the rest.
    let center = r.top() + 2.0 + (height - 2.0) / 2.0;
    // ... and one with stylesheet padding by 2 px.
    widgets::text_left(ui, r.left() + 2.0, center, text, 11.0, Weight::Regular, p.card_muted);
}

fn timeline(ui: &mut Ui, col: &mut Column, st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) {
    let rows = cx.session.timeline_rows();
    if rows.is_empty() {
        // Without segments only the status line shows.
        let text = cx.session.timeline_status();
        status_label(ui, col, &text, 32.0, p);
        return;
    }
    // Prev | xN | Next, 4 px apart; the combo is 51 px.
    let r = col.next(32.0);
    let side = (r.width() - 51.0 - 8.0) / 2.0;
    let prev = Rect::from_min_size(r.min, vec2(side, 32.0));
    let combo = Rect::from_min_size(pos2(prev.right() + 4.0, r.top()), vec2(51.0, 32.0));
    let next = Rect::from_min_max(pos2(combo.right() + 4.0, r.top()), r.max);
    // Disabled while a fetch runs, and where the archive probe found nothing.
    let busy = cx.session.timeline_busy();
    let probe = cx.session.timeline_available.clone();
    let (next_ok, next_why) = probe.as_ref().map_or((true, String::new()), |p| p[0].clone());
    let (prev_ok, prev_why) = probe.as_ref().map_or((true, String::new()), |p| p[1].clone());
    let prev_label = "\u{25c0} Prev";
    let prev_resp =
        widgets::push_button(ui, prev, Id::new("timeline_prev"), prev_label, ButtonKind::Primary, !busy && prev_ok, p);
    if prev_resp.clicked() {
        cx.commands.push(Command::AddPreviousObservation);
    }
    prev_resp.on_hover_text(if prev_ok {
        "Add the preceding observation (Ctrl+Shift+Left)".to_owned()
    } else {
        prev_why
    });
    let steps = [1usize, 2, 4, 8];
    let labels: Vec<String> = steps.iter().map(|n| format!("\u{d7}{n}")).collect();
    let current = steps.iter().position(|n| *n == st.timeline_step).unwrap_or(0);
    let label_refs: Vec<&str> = labels.iter().map(String::as_str).collect();
    if let Some(i) = widgets::combo(ui, combo, Id::new("timeline_step"), &label_refs, current, !busy, p) {
        st.timeline_step = steps[i];
    }
    let next_label = "Next \u{25b6}";
    let next_resp =
        widgets::push_button(ui, next, Id::new("timeline_next"), next_label, ButtonKind::Primary, !busy && next_ok, p);
    if next_resp.clicked() {
        cx.commands.push(Command::AddNextObservation);
    }
    next_resp.on_hover_text(if next_ok {
        "Add the following observation (Ctrl+Shift+Right)".to_owned()
    } else {
        next_why
    });

    // The segment list: 26 px rows, up to five, inside a 4 px frame.
    let visible = rows.len().min(5);
    let r = col.next(visible as f32 * 26.0 + 8.0);
    widgets::segment_list(ui, r, &rows, p);

    let r = col.next(32.0);
    let half = (r.width() - 4.0) / 2.0;
    let start = Rect::from_min_size(r.min, vec2(half.ceil(), 32.0));
    let end = Rect::from_min_max(pos2(start.right() + 4.0, r.top()), r.max);
    let can_trim = rows.len() > 1 && !busy;
    if widgets::push_button(ui, start, Id::new("trim_start"), "Trim Start", ButtonKind::Normal, can_trim, p).clicked() {
        cx.commands.push(Command::TrimTimelineStart);
    }
    if widgets::push_button(ui, end, Id::new("trim_end"), "Trim End", ButtonKind::Normal, can_trim, p).clicked() {
        cx.commands.push(Command::TrimTimelineEnd);
    }
    let text = cx.session.timeline_status();
    status_label(ui, col, &text, 17.0, p);
}

fn background(ui: &mut Ui, col: &mut Column, _st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) {
    label(ui, col, "Method", p);
    let r = col.next(32.0);
    let labels: Vec<&str> = BACKGROUND_METHODS.iter().map(|(_, l)| *l).collect();
    let current = BACKGROUND_METHODS.iter().position(|(m, _)| *m == cx.session.background_method).unwrap_or(0);
    if let Some(i) = widgets::combo(ui, r, Id::new("background_method"), &labels, current, true, p) {
        cx.session.set_background_method(BACKGROUND_METHODS[i].0);
    }
    let r = col.next(32.0);
    let enabled = cx.has_data();
    if widgets::push_button(
        ui,
        r,
        Id::new("subtract_background"),
        "Subtract Background",
        ButtonKind::Primary,
        enabled,
        p,
    )
    .clicked()
    {
        cx.commands.push(Command::SubtractBackground);
    }
    let status = cx.session.background_status();
    status_label(ui, col, &status, 17.0, p);
}

/// A threshold's label and value card. In dB over digits the card grows
/// a second, smaller line with the dB value (`NoiseThresholdSecondary`).
fn threshold_row(ui: &Ui, col: &mut Column, text: &str, value: &(String, Option<String>), p: &Palette) {
    let h = if value.1.is_some() { 44.0 } else { 30.0 };
    let r = col.next(h);
    widgets::text_left(ui, r.left(), r.center().y, text, 12.0, Weight::Regular, p.card_text);
    let card = Rect::from_min_size(pos2(r.right() - 104.0, r.top()), vec2(104.0, h));
    widgets::value_card(ui, card, &value.0, value.1.as_deref(), p);
}

fn threshold_slider(ui: &Ui, col: &mut Column, lower: bool, cx: &mut SidebarContext<'_>, p: &Palette) {
    let r = col.next(15.0);
    let id = Id::new(if lower { "lower_threshold" } else { "upper_threshold" });
    let value = if lower { cx.session.lower_slider } else { cx.session.upper_slider };
    let out = widgets::slider(ui, r, id, value, NOISE_SLIDER_MAX, 50, p);
    if out.pressed {
        cx.session.on_noise_slider_pressed();
    }
    if let Some(v) = out.value {
        cx.session.on_noise_slider_value_changed(lower, v);
    }
    if out.released {
        cx.session.on_noise_slider_released();
    }
}

fn noise_thresholds(ui: &mut Ui, col: &mut Column, _st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) {
    let [low, high] = cx.session.noise_clip_labels();
    threshold_row(ui, col, "Lower Threshold", &low, p);
    threshold_slider(ui, col, true, cx, p);
    threshold_row(ui, col, "Upper Threshold", &high, p);
    threshold_slider(ui, col, false, cx, p);
    let r = col.next(23.0);
    let mut log = cx.session.noise_clip_scale == ClipScale::SignedLog;
    if widgets::checkbox(ui, r, Id::new("log_threshold"), "Logarithmic Threshold Scale", &mut log, true, p) {
        cx.session.on_noise_scale_mode_toggled(log);
    }
}

fn units(ui: &mut Ui, col: &mut Column, _st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) {
    // Two columns, 24 px apart, each with 6 px spacing (not normalised).
    let block = col.next(84.0);
    let left_w = 108.0;
    let right_x = block.left() + left_w + 24.0;
    // Median (dB) output is already in dB: the intensity switch shows dB and is disabled.
    let db_product = cx.session.background_product_is_db();
    let show_db = db_product || cx.session.use_db;
    let unit = cx.session.intensity_linear_unit();
    let columns = [
        (block.left(), left_w, "Intensity", [unit, "dB"], show_db, "units_intensity", !db_product),
        (right_x, block.right() - right_x, "Time", ["Seconds", "UT"], cx.session.use_utc, "units_time", true),
    ];
    let mut picked = [None, None];
    for (i, (x, w, title, options, second, id, enabled)) in columns.into_iter().enumerate() {
        let mut c = Column::new(x, block.top(), w);
        let r = c.next(26.0);
        widgets::text_left(ui, r.left() + 8.0 / 3.0, r.top() + 17.0, title, 10.0, Weight::Bold, p.card_muted);
        for (j, option) in options.into_iter().enumerate() {
            c.y += 6.0;
            let r = Rect::from_min_size(pos2(c.x, c.y), vec2(w, 23.0));
            c.y += 23.0;
            if widgets::radio(ui, r, Id::new((id, j)), option, (j == 1) == second, enabled, p) {
                picked[i] = Some(j == 1);
            }
        }
    }
    if let Some(v) = picked[0] {
        cx.session.set_units_mode(v);
    }
    if let Some(v) = picked[1] {
        cx.session.set_time_axis(v);
    }
}

fn axis(ui: &mut Ui, col: &mut Column, _st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) {
    section_label(ui, col, "Frequency scale", p);
    let log = cx.session.freq_axis_log;
    let r = col.next(23.0);
    if widgets::radio(ui, r, Id::new("axis_linear"), "Linear", !log, true, p) {
        cx.session.set_frequency_axis_log(false);
    }
    let r = col.next(23.0);
    if widgets::radio(ui, r, Id::new("axis_log"), "Log", log, true, p) {
        cx.session.set_frequency_axis_log(true);
    }
}

fn graph_properties(ui: &mut Ui, col: &mut Column, st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) {
    // The whole group is disabled until data is loaded (graph_group.setEnabled(False)).
    let enabled = cx.has_data();
    section_label(ui, col, "Appearance", p);
    label(ui, col, "Colormap", p);
    let r = col.next(32.0);
    let current = st.colormaps.iter().position(|c| *c == cx.session.graph.cmap).unwrap_or(0);
    if let Some(i) = widgets::combo(ui, r, Id::new("colormap"), &st.colormaps, current, enabled, p) {
        cx.session.change_cmap(st.colormaps[i]);
    }
    label(ui, col, "Font family", p);
    let r = col.next(32.0);
    let families: Vec<&str> = st.font_families.iter().map(String::as_str).collect();
    let current = if cx.session.graph.font_family.is_empty() {
        0
    } else {
        families.iter().position(|f| *f == cx.session.graph.font_family).unwrap_or(0)
    };
    if let Some(i) = widgets::combo(ui, r, Id::new("font_family"), &families, current, enabled, p) {
        cx.session.graph.font_family = if i == 0 { String::new() } else { families[i].to_owned() };
        cx.session.apply_graph_properties_live();
    }
    col.spacer(8.0);

    section_label(ui, col, "Text", p);
    label(ui, col, "Graph title", p);
    let r = col.next(32.0);
    // Remove Titles disables the title inputs (apply_graph_properties_live).
    let titles = enabled && !cx.session.graph.remove_titles;
    let placeholder = "Custom title (leave empty for default)";
    if widgets::line_edit(ui, r, Id::new("graph_title"), &mut st.graph_title, placeholder, titles, p) {
        cx.session.graph.title_override = st.graph_title.trim().to_owned();
        cx.session.apply_graph_properties_live();
    }
    let r = col.next(23.0);
    let mut remove = cx.session.graph.remove_titles;
    if widgets::checkbox(ui, r, Id::new("remove_titles"), "Remove Titles", &mut remove, enabled, p) {
        cx.session.graph.remove_titles = remove;
        cx.session.apply_graph_properties_live();
    }
    col.spacer(8.0);

    section_label(ui, col, "Font sizes", p);
    let rows = [("Tick labels (px)", 60), ("Axis labels (px)", 60), ("Title (px)", 80)];
    for (i, (text, max)) in rows.into_iter().enumerate() {
        let r = col.next(35.0);
        widgets::text_left(ui, r.left(), r.center().y, text, 12.0, Weight::Regular, p.card_text);
        let spin = Rect::from_min_size(pos2(r.right() - 110.0, r.top()), vec2(110.0, 35.0));
        let g = &mut cx.session.graph;
        let slot = match i {
            0 => &mut g.tick_px,
            1 => &mut g.axis_px,
            _ => &mut g.title_px,
        };
        let mut value = *slot as i32;
        if widgets::spin_box(ui, spin, Id::new(("font_px", i)), &mut value, (6, max), enabled, p) {
            *slot = value as u32;
            // Any size change leaves the canvas's own 14/16/22 px defaults.
            g.hw_default_sizes = false;
            cx.session.apply_graph_properties_live();
        }
    }
    col.spacer(8.0);

    section_label(ui, col, "Text style", p);
    let g = &cx.session.graph;
    let rows: [(&str, &str, bool, bool); 3] = [
        ("Title", "title", g.title_bold, g.title_italic),
        ("Axis labels", "axis", g.axis_bold, g.axis_italic),
        ("Tick labels", "ticks", g.ticks_bold, g.ticks_italic),
    ];
    let mut new_values = Vec::new();
    let mut changed = false;
    for (text, key, mut bold, mut italic) in rows {
        let r = col.next(23.0);
        widgets::text_left(ui, r.left(), r.center().y, text, 12.0, Weight::Regular, p.card_text);
        let bold_rect = Rect::from_min_size(pos2(r.left() + 134.0, r.top()), vec2(48.0, 23.0));
        let italic_rect = Rect::from_min_size(pos2(r.left() + 190.0, r.top()), vec2(49.0, 23.0));
        // Remove Titles also disables the title and axis label styles.
        let on = enabled && (key == "ticks" || !cx.session.graph.remove_titles);
        changed |= widgets::checkbox(ui, bold_rect, Id::new((key, "bold")), "Bold", &mut bold, on, p);
        changed |= widgets::checkbox(ui, italic_rect, Id::new((key, "italic")), "Italic", &mut italic, on, p);
        new_values.push((bold, italic));
    }
    if changed {
        let g = &mut cx.session.graph;
        (g.title_bold, g.title_italic) = new_values[0];
        (g.axis_bold, g.axis_italic) = new_values[1];
        (g.ticks_bold, g.ticks_italic) = new_values[2];
        cx.session.apply_graph_properties_live();
    }
}

fn analysis_summary(ui: &mut Ui, col: &mut Column, st: &mut SidebarState, _cx: &mut SidebarContext<'_>, p: &Palette) {
    wrapped_label(ui, col, &st.analysis_summary.clone(), p);
}

/// `MeasurementReadout`: the readout, then Copy and Clear.
fn ruler(ui: &mut Ui, col: &mut Column, _st: &mut SidebarState, cx: &mut SidebarContext<'_>, p: &Palette) {
    let readout = cx.session.tools.ruler.clone();
    wrapped_label(ui, col, &readout.text, p);
    let r = col.next(32.0);
    let copy = Rect::from_min_size(r.min, vec2(117.0, 32.0));
    let clear = Rect::from_min_max(pos2(r.left() + 123.0, r.top()), r.max);
    if widgets::push_button(ui, copy, Id::new("ruler_copy"), "Copy", ButtonKind::Normal, readout.copy_enabled, p)
        .clicked()
    {
        ui.ctx().copy_text(readout.text.clone());
    }
    if widgets::push_button(ui, clear, Id::new("ruler_clear"), "Clear", ButtonKind::Normal, readout.clear_enabled, p)
        .clicked()
    {
        cx.commands.push(Command::ClearRulerMeasurement);
    }
}

// --------------------------------------------------------- the scrollbar

/// `QScrollArea#SidebarScroll QScrollBar:vertical`: 8 px wide, handle inset
/// 4 px top/bottom and 2 px right, radius 4, min length 32.
fn scrollbar(ui: &mut Ui, rect: Rect, offset: f32, content_h: f32, view_h: f32, st: &mut SidebarState, p: &Palette) {
    if content_h <= view_h + 0.5 {
        return;
    }
    let track = Rect::from_min_max(
        pos2(rect.right() - SCROLLBAR_W, rect.top() + 4.0),
        pos2(rect.right() - 2.0, rect.bottom() - 4.0),
    );
    let len = (track.height() * view_h / content_h).max(32.0);
    let max_offset = content_h - view_h;
    let travel = track.height() - len;
    let t = (offset / max_offset).clamp(0.0, 1.0);
    let handle = Rect::from_min_size(pos2(track.left(), track.top() + t * travel), vec2(track.width(), len));
    let resp = ui.interact(handle, Id::new("sidebar_scroll_handle"), Sense::drag());
    if resp.dragged() && travel > 0.0 {
        let delta = resp.drag_delta().y * max_offset / travel;
        st.scroll_to = Some((offset + delta).clamp(0.0, max_offset));
    }
    let color = if resp.hovered() || resp.dragged() { p.sidebar_handle_hover } else { p.sidebar_handle };
    ui.painter().rect_filled(handle, CornerRadius::same(3), color);
}
