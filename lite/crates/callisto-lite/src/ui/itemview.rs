//! Qt's item views as the full app shows them in the downloaders
//! (`QTreeWidget`, `QTableWidget`, `QListWidget` with the Modern
//! stylesheet): a framed viewport, an optional header, rows with check
//! indicators (Fusion's), tree branches, alternating colours, single
//! selection, keyboard navigation and row-by-row scrolling.

use egui::{Color32, CornerRadius, Id, Pos2, Rect, Sense, Shape, Stroke, StrokeKind, Ui, pos2, vec2};

use super::qt::FONT_PX;
use super::widgets::{text_center, text_left, text_left_clipped, text_width};
use crate::fonts::Weight;
use crate::theme::Palette;

/// `Qt::CheckState`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Check {
    #[default]
    Unchecked,
    Partial,
    Checked,
}

impl Check {
    pub fn is_checked(self) -> bool {
        self == Check::Checked
    }

    pub fn from_bool(b: bool) -> Self {
        if b { Check::Checked } else { Check::Unchecked }
    }
}

/// One row: its cells, check state (column 0), tree depth and expansion.
#[derive(Clone, Debug, Default)]
pub struct Row {
    pub cells: Vec<String>,
    pub check: Option<Check>,
    pub depth: usize,
    /// `Some(expanded)` for a tree item with children.
    pub expanded: Option<bool>,
    pub selectable: bool,
    pub tooltip: Option<String>,
}

impl Row {
    pub fn new(cells: Vec<String>) -> Self {
        Row { cells, check: None, depth: 0, expanded: None, selectable: true, tooltip: None }
    }

    pub fn checked(mut self, c: Check) -> Self {
        self.check = Some(c);
        self
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Kind {
    /// `QTreeWidget`: 20 px indentation, decorated root.
    Tree,
    /// `QTableWidget`.
    Table,
    /// `QListWidget`.
    List,
}

#[derive(Clone, Copy, Debug)]
pub struct Style {
    pub kind: Kind,
    pub row_h: f32,
    /// `::item { padding }` (the results tables' 5 px).
    pub item_pad: f32,
    /// The frame's padding (stylesheet `padding`): horizontal, vertical.
    pub pad: (f32, f32),
    pub radius: f32,
    pub alternate: bool,
    pub header: bool,
    /// Header text centred (tables) or left-aligned (trees).
    pub header_centered: bool,
    pub sortable: bool,
    /// `QListWidget:focus` turns the border accent (no ID rule overrides it).
    pub focus_border: bool,
}

impl Style {
    /// `#DownloaderFileTree`.
    pub fn tree() -> Self {
        Style { kind: Kind::Tree, row_h: 16.0, item_pad: 0.0, pad: (5.0, 5.0), radius: 8.0, alternate: false, header: true, header_centered: false, sortable: false, focus_border: false }
    }

    /// `#EventResultsTable`.
    pub fn results_table(sortable: bool) -> Self {
        Style { kind: Kind::Table, row_h: 30.0, item_pad: 5.0, pad: (5.0, 5.0), radius: 8.0, alternate: true, header: true, header_centered: true, sortable, focus_border: false }
    }

    /// `#EventStationList`.
    pub fn station_list() -> Self {
        Style { kind: Kind::List, row_h: 16.0, item_pad: 0.0, pad: (5.0, 5.0), radius: 8.0, alternate: false, header: false, header_centered: false, sortable: false, focus_border: false }
    }

    /// A plain `QListWidget` (Learmonth chunks): `padding: 6px 8px`, radius 10.
    pub fn plain_list() -> Self {
        Style { kind: Kind::List, row_h: 16.0, item_pad: 0.0, pad: (8.0, 6.0), radius: 10.0, alternate: false, header: false, header_centered: false, sortable: false, focus_border: true }
    }
}

/// Scroll position, current row and sort indicator, kept between frames.
#[derive(Clone, Debug, Default)]
pub struct ViewState {
    /// First visible row (Qt scrolls tables and lists item by item).
    pub first: usize,
    pub current: Option<usize>,
    /// `(column, ascending)`.
    pub sort: Option<(usize, bool)>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    /// The check box of a row was clicked (or Space pressed on it).
    Toggled(usize),
    /// The current row changed (click or arrow keys).
    Current(usize),
    /// A row was double-clicked.
    Activated(usize),
    /// A tree branch was opened or closed.
    Expand(usize, bool),
    /// A header section was clicked (sorting).
    Sort(usize),
}

const HEADER_H: f32 = 34.0;
const INDICATOR: f32 = 13.0;
const INDENT: f32 = 20.0;

/// Fusion's item-view check indicator (`PE_IndicatorItemViewItemCheck`),
/// on the view's white base, in the stylesheet's text colour.
pub fn check_indicator(ui: &Ui, rect: Rect, state: Check, selected: bool, p: &Palette) {
    let painter = ui.painter();
    let base = p.input_bg;
    let mark = darker(p.text, 1.2);
    if selected || state == Check::Partial || p.dark {
        painter.rect_filled(rect, CornerRadius::ZERO, base);
    }
    // The gradient's darker top edge (base.darker(115) fading out).
    painter.line_segment([pos2(rect.left(), rect.top() + 0.5), pos2(rect.right(), rect.top() + 0.5)], Stroke::new(1.0, darker(base, 1.08)));
    let pad = 1.0 + rect.width() * 0.13;
    match state {
        Check::Partial => {
            let inner = rect.shrink(pad);
            let top = with_alpha(mark, 80);
            let bottom = with_alpha(mark, 140);
            let mesh = gradient_rect(inner, top, bottom);
            painter.add(Shape::mesh(mesh));
            painter.rect_stroke(inner, CornerRadius::ZERO, Stroke::new(1.0, with_alpha(mark, 180)), StrokeKind::Middle);
        }
        Check::Checked => {
            let h = rect.height();
            let o = rect.min + vec2(-0.8, 0.5);
            let pts = [
                o + vec2(pad + h * 0.11, h * 0.47),
                o + vec2(h * 0.5, h - pad),
                o + vec2(h - pad, pad),
            ];
            let width = (1.5f32).max(0.13 * h).min(0.20 * h);
            painter.add(Shape::line(pts.to_vec(), Stroke::new(width, mark)));
        }
        Check::Unchecked => {}
    }
}

fn darker(c: Color32, f: f32) -> Color32 {
    Color32::from_rgb((c.r() as f32 / f) as u8, (c.g() as f32 / f) as u8, (c.b() as f32 / f) as u8)
}

fn with_alpha(c: Color32, a: u8) -> Color32 {
    Color32::from_rgba_unmultiplied(c.r(), c.g(), c.b(), a)
}

fn gradient_rect(r: Rect, top: Color32, bottom: Color32) -> egui::Mesh {
    let mut m = egui::Mesh::default();
    let i = m.vertices.len() as u32;
    m.colored_vertex(r.left_top(), top);
    m.colored_vertex(r.right_top(), top);
    m.colored_vertex(r.right_bottom(), bottom);
    m.colored_vertex(r.left_bottom(), bottom);
    m.add_triangle(i, i + 1, i + 2);
    m.add_triangle(i, i + 2, i + 3);
    m
}

/// The width a `ResizeToContents` column of an `EventResultsTable` needs.
/// The header: `QHeaderView` measures the text in bold (so a highlighted
/// section still fits), plus margins, `padding: 6px` and the left border:
/// 17 px, and 22 more for the sort indicator. The bold widths here run a
/// hair over DirectWrite's, hence the 0.1. A cell: the text plus
/// `::item { padding: 5px }` and the 3 px text margins, 16 px; a check
/// box adds 20.
pub fn content_width(ui: &Ui, header: &str, cells: impl Iterator<Item = String>, checkable: bool, sortable: bool) -> f32 {
    let head = if header.is_empty() {
        28.0
    } else {
        ((text_width(ui, header, FONT_PX, Weight::Bold) - 0.1).ceil() + 17.0 + if sortable { 22.0 } else { 0.0 }).max(28.0)
    };
    let cell = cells
        .map(|c| {
            let t = if c.is_empty() { 0.0 } else { text_width(ui, &c, FONT_PX, Weight::Regular).ceil() };
            t + 16.0 + if checkable { 20.0 } else { 0.0 }
        })
        .fold(0.0, f32::max);
    head.max(cell)
}

/// The viewport (inside frame and padding, below the header).
pub fn viewport(rect: Rect, style: &Style) -> Rect {
    let top = rect.top() + 1.0 + style.pad.1 + if style.header { HEADER_H } else { 0.0 };
    Rect::from_min_max(pos2(rect.left() + 1.0 + style.pad.0, top), pos2(rect.right() - 1.0 - style.pad.0, rect.bottom() - 1.0 - style.pad.1))
}

/// Whether `n` rows need the vertical scroll bar in `rect`.
pub fn needs_scrollbar(rect: Rect, style: &Style, n: usize) -> bool {
    n as f32 * style.row_h > viewport(rect, style).height()
}

/// Draws the view. `widths` are the column widths (the last stretches to
/// the viewport); `visible` maps display order to row indices (hidden or
/// sorted rows); events report row indices.
#[allow(clippy::too_many_arguments)]
pub fn item_view(
    ui: &Ui,
    rect: Rect,
    id: Id,
    headers: &[&str],
    widths: &[f32],
    rows: &[Row],
    visible: &[usize],
    style: &Style,
    state: &mut ViewState,
    enabled: bool,
    p: &Palette,
) -> Vec<Event> {
    let mut events = Vec::new();
    let focused = ui.memory(|m| m.has_focus(id));
    let border = if style.focus_border && focused { p.accent } else { p.border };
    super::qt::qt_box(ui, rect, style.radius, p.input_bg, border);
    let mut vp = viewport(rect, style);
    let scroll = visible.len() as f32 * style.row_h > vp.height();
    if scroll {
        vp.max.x -= 10.0;
    }
    let per_page = (vp.height() / style.row_h).floor().max(1.0) as usize;
    let max_first = visible.len().saturating_sub(per_page);
    state.first = state.first.min(max_first);

    // Header.
    let mut col_x: Vec<f32> = Vec::with_capacity(widths.len());
    {
        let mut x = vp.left();
        for w in widths {
            col_x.push(x);
            x += *w;
        }
    }
    let col_right = |i: usize| if i + 1 < widths.len() { col_x[i + 1] } else { vp.right() };
    if style.header {
        let top = rect.top() + 1.0 + style.pad.1;
        let painter = ui.painter_at(Rect::from_min_max(pos2(vp.left(), top), pos2(vp.right() + if scroll { 10.0 } else { 0.0 }, top + HEADER_H)));
        for (i, title) in headers.iter().enumerate() {
            let last = i + 1 == headers.len();
            let right = if last { vp.right() + if scroll { 10.0 } else { 0.0 } } else { col_right(i) };
            let r = Rect::from_min_max(pos2(col_x[i], top), pos2(right, top + HEADER_H));
            // `border: 1px solid; border-right: none` (the last section
            // keeps it): the background over a border-coloured section, so
            // each 1 px edge lands exactly where Qt draws it instead of as
            // a two-pixel antialiased line. Each section is clipped to its
            // own rectangle, rounded to device pixels as Qt rounds it.
            let section = painter.with_clip_rect(r);
            super::qt::fill_exact(&section, r, CornerRadius::ZERO, p.border);
            let inner = Rect::from_min_max(pos2(r.left() + 1.0, r.top() + 1.0), pos2(r.right() - if last { 1.0 } else { 0.0 }, r.bottom() - 1.0));
            super::qt::fill_exact(&section, inner, CornerRadius::ZERO, p.surface_alt);
            let resp = ui.interact(r, id.with(("header", i)), if style.sortable && enabled { Sense::click() } else { Sense::hover() });
            let sorted = state.sort.filter(|(c, _)| *c == i);
            let text_area = Rect::from_min_max(pos2(r.left() + 7.0, r.top()), pos2(r.right() - 7.0 - if sorted.is_some() { 19.0 } else { 0.0 }, r.bottom()));
            if style.header_centered {
                text_center(ui, text_area.center(), title, FONT_PX, Weight::Semibold, p.muted);
            } else {
                text_left(ui, text_area.left(), r.center().y, title, FONT_PX, Weight::Semibold, p.muted);
            }
            if let Some((_, asc)) = sorted {
                let c = pos2(r.right() - 7.0 - 6.0, r.center().y);
                let dir = if asc { -1.0 } else { 1.0 };
                let pts = vec![pos2(c.x - 4.0, c.y - 2.0 * dir), pos2(c.x + 4.0, c.y - 2.0 * dir), pos2(c.x, c.y + 2.0 * dir)];
                painter.add(Shape::convex_polygon(pts, p.muted, Stroke::NONE));
            }
            if resp.clicked() {
                events.push(Event::Sort(i));
            }
        }
    }

    // Rows.
    let painter = ui.painter_at(vp);
    let area = ui.interact(vp, id, if enabled { Sense::click() } else { Sense::hover() });
    if area.clicked() {
        area.request_focus();
    }
    for (k, &ri) in visible.iter().enumerate().skip(state.first).take(per_page + 1) {
        let row = &rows[ri];
        let y = vp.top() + (k - state.first) as f32 * style.row_h;
        let r = Rect::from_min_size(pos2(vp.left(), y), vec2(vp.width(), style.row_h));
        let selected = state.current == Some(ri) && row.selectable;
        if selected {
            painter.rect_filled(r, CornerRadius::ZERO, p.accent);
        } else if style.alternate && k % 2 == 1 {
            painter.rect_filled(r, CornerRadius::ZERO, p.surface_alt);
        }
        let text_color = if selected { Color32::WHITE } else if enabled { p.text } else { p.disabled };
        let indent = if style.kind == Kind::Tree { INDENT * (row.depth as f32 + 1.0) } else { 0.0 };
        if let Some(expanded) = row.expanded {
            let c = pos2(vp.left() + INDENT * row.depth as f32 + 10.0, r.center().y);
            let color = if selected { Color32::WHITE } else { Color32::from_rgb(0x78, 0x7c, 0x82) };
            let pts = if expanded {
                vec![pos2(c.x - 4.0, c.y - 2.0), pos2(c.x + 4.0, c.y - 2.0), pos2(c.x, c.y + 2.0)]
            } else {
                vec![pos2(c.x - 2.0, c.y - 4.0), pos2(c.x + 2.0, c.y), pos2(c.x - 2.0, c.y + 4.0)]
            };
            painter.add(Shape::convex_polygon(pts, color, Stroke::NONE));
            let branch = Rect::from_center_size(c, vec2(INDENT, style.row_h));
            if ui.interact(branch, id.with(("branch", ri)), if enabled { Sense::click() } else { Sense::hover() }).clicked() {
                events.push(Event::Expand(ri, !expanded));
            }
        }
        for (ci, cell) in row.cells.iter().enumerate().take(widths.len()) {
            let cell_left = col_x[ci] + if ci == 0 { indent } else { 0.0 } + style.item_pad;
            let cell_right = col_right(ci) - style.item_pad;
            let mut x = cell_left + 3.0;
            if ci == 0
                && let Some(check) = row.check
            {
                let ind = Rect::from_min_size(pos2(x, (r.center().y - INDICATOR / 2.0).round()), vec2(INDICATOR, INDICATOR));
                check_indicator(ui, ind, check, selected, p);
                let hit = ind.expand(2.0);
                if ui.interact(hit, id.with(("check", ri)), if enabled { Sense::click() } else { Sense::hover() }).clicked() {
                    events.push(Event::Toggled(ri));
                }
                x = ind.right() + 7.0;
            }
            if !cell.is_empty() {
                let clip = Rect::from_min_max(pos2(x, r.top()), pos2(cell_right.max(x), r.bottom())).intersect(vp);
                let shown = elide(ui, cell, cell_right - x);
                text_left_clipped(ui, clip, x, r.center().y, &shown, FONT_PX, Weight::Regular, text_color);
            }
        }
        if let Some(tip) = &row.tooltip
            && ui.rect_contains_pointer(r)
        {
            egui::Tooltip::always_open(ui.ctx().clone(), ui.layer_id(), id.with(("tip", ri)), egui::PopupAnchor::Pointer)
                .gap(12.0)
                .show(|ui| {
                    ui.label(tip);
                });
        }
    }
    if let Some(pos) = area.interact_pointer_pos()
        && (area.clicked() || area.double_clicked())
    {
        let k = state.first + ((pos.y - vp.top()) / style.row_h).floor() as usize;
        if let Some(&ri) = visible.get(k) {
            let in_branch = rows[ri].expanded.is_some() && pos.x < vp.left() + INDENT * (rows[ri].depth as f32 + 1.0);
            if area.double_clicked() {
                events.push(Event::Activated(ri));
                if let Some(e) = rows[ri].expanded
                    && !in_branch
                {
                    events.push(Event::Expand(ri, !e));
                }
            } else if rows[ri].selectable && state.current != Some(ri) && !in_branch {
                state.current = Some(ri);
                events.push(Event::Current(ri));
            }
        }
    }
    // Keyboard: arrows move the current row, Space toggles its check box.
    if focused && enabled && !visible.is_empty() {
        let (up, down, space) = ui.input(|i| (i.key_pressed(egui::Key::ArrowUp), i.key_pressed(egui::Key::ArrowDown), i.key_pressed(egui::Key::Space)));
        let pos = state.current.and_then(|c| visible.iter().position(|&v| v == c));
        if up || down {
            let mut k = match pos {
                Some(k) if up => k.saturating_sub(1),
                Some(k) => (k + 1).min(visible.len() - 1),
                None => 0,
            };
            while !rows[visible[k]].selectable && k + 1 < visible.len() && down {
                k += 1;
            }
            let ri = visible[k];
            if state.current != Some(ri) {
                state.current = Some(ri);
                events.push(Event::Current(ri));
            }
            if k < state.first {
                state.first = k;
            } else if k >= state.first + per_page {
                state.first = k + 1 - per_page;
            }
        }
        if space
            && let Some(c) = state.current
            && rows[c].check.is_some()
        {
            events.push(Event::Toggled(c));
        }
    }
    // Wheel: three rows per notch.
    if ui.rect_contains_pointer(rect) && scroll {
        let wheel: f32 = ui.input(|i| {
            i.events
                .iter()
                .filter_map(|e| match e {
                    egui::Event::MouseWheel { unit: egui::MouseWheelUnit::Line, delta, .. } => Some(delta.y),
                    egui::Event::MouseWheel { delta, .. } => Some(delta.y / 50.0),
                    _ => None,
                })
                .sum()
        });
        if wheel != 0.0 {
            let steps = (wheel.abs() * 3.0).round().max(1.0) as usize;
            state.first = if wheel > 0.0 { state.first.saturating_sub(steps) } else { (state.first + steps).min(max_first) };
        }
    }
    if scroll {
        scrollbar(ui, Rect::from_min_max(pos2(vp.right(), vp.top()), pos2(vp.right() + 10.0, vp.bottom())), id.with("vbar"), state, visible.len(), per_page, p);
    }
    events
}

/// `QScrollBar:vertical` (10 px, handle radius 5, 4 px end margins).
fn scrollbar(ui: &Ui, rect: Rect, id: Id, state: &mut ViewState, total: usize, per_page: usize, p: &Palette) {
    let track = Rect::from_min_max(pos2(rect.left(), rect.top() + 4.0), pos2(rect.right(), rect.bottom() - 4.0));
    let max_first = total.saturating_sub(per_page);
    let frac = per_page as f32 / total.max(1) as f32;
    let handle_h = (track.height() * frac).max(24.0).min(track.height());
    let travel = track.height() - handle_h;
    let y = track.top() + if max_first > 0 { travel * state.first as f32 / max_first as f32 } else { 0.0 };
    let handle = Rect::from_min_size(pos2(track.left(), y), vec2(10.0, handle_h));
    let resp = ui.interact(track, id, Sense::click_and_drag());
    if (resp.dragged() || resp.clicked())
        && let Some(pos) = resp.interact_pointer_pos()
        && travel > 0.0
    {
        let t = ((pos.y - track.top() - handle_h / 2.0) / travel).clamp(0.0, 1.0);
        state.first = (t * max_first as f32).round() as usize;
    }
    ui.painter().rect_filled(handle, CornerRadius::same(5), p.border);
}

/// Qt's `ElideRight` for text wider than `width`.
pub fn elide(ui: &Ui, text: &str, width: f32) -> String {
    if text_width(ui, text, FONT_PX, Weight::Regular) <= width + 0.5 {
        return text.to_owned();
    }
    let mut chars: Vec<char> = text.chars().collect();
    while !chars.is_empty() {
        chars.pop();
        let s: String = chars.iter().collect::<String>() + "\u{2026}";
        if text_width(ui, &s, FONT_PX, Weight::Regular) <= width {
            return s;
        }
    }
    String::new()
}

/// The rows one page shows (for callers sizing things).
pub fn rows_per_page(rect: Rect, style: &Style) -> usize {
    (viewport(rect, style).height() / style.row_h).floor() as usize
}

/// A point inside the viewport, for tests.
pub fn row_center(rect: Rect, style: &Style, k: usize) -> Pos2 {
    let vp = viewport(rect, style);
    pos2(vp.center().x, vp.top() + style.row_h * (k as f32 + 0.5))
}
