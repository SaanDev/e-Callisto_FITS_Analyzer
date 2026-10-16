//! The menu bar and its popups, drawn like the full app's QMenuBar/QMenu.
//!
//! Geometry from `AppTheme._modern_qss` and the captured menus
//! (`lite/reference/windows/reference_*.json`):
//! * bar: 34 px, items 28 px high with 10 px side padding, 6 px apart,
//!   starting at x = 13, y = 3;
//! * popup: 1 px border, 6 px padding, radius 10; items 30 px with padding
//!   7/16/7/10 and radius 7; separators 11 px with 8 px side margins;
//! * width: longest label + longest shortcut (+12 px before shortcuts) +
//!   41 px, plus an 18 px check column when any item is checkable.
//!
//! Disabled items look like enabled ones (the stylesheet's text colour wins
//! over Qt's disabled colour) but do not react.

use egui::{Area, CornerRadius, Id, Order, Pos2, Rect, Sense, Shape, Stroke, Ui, pos2, vec2};

use super::widgets::{self, Arrow};
use crate::fonts::Weight;
use crate::menus::{Command, Item, Menu};
use crate::theme::Palette;

pub const HEIGHT: f32 = 34.0;
const BAR_X: f32 = 13.0;
const BAR_ITEM_Y: f32 = 3.0;
const BAR_ITEM_H: f32 = 28.0;
const BAR_ITEM_PAD: f32 = 10.0;
const BAR_ITEM_GAP: f32 = 6.0;

const FRAME: f32 = 7.0; // 1 px border + 6 px padding
const ITEM_H: f32 = 30.0;
const SEPARATOR_H: f32 = 11.0;
const PAD_LEFT: f32 = 10.0;
const PAD_RIGHT: f32 = 16.0;
const CHECK_COL: f32 = 18.0;
const CHECK_TEXT_SHIFT: f32 = 14.0;
const SHORTCUT_GAP: f32 = 12.0;
const BASE_WIDTH: f32 = 41.0;

#[derive(Default)]
pub struct MenuBarState {
    /// Index of the open top-level menu, then of each open submenu.
    open: Vec<usize>,
}

impl MenuBarState {
    pub fn is_open(&self) -> bool {
        !self.open.is_empty()
    }

    pub fn close(&mut self) {
        self.open.clear();
    }

    /// For tests and screenshots: open a menu path by labels.
    pub fn open_path(&mut self, menus: &[Menu], path: &[&str]) {
        self.open.clear();
        let Some((first, rest)) = path.split_first() else { return };
        let Some(i) = menus.iter().position(|m| m.title == *first) else { return };
        self.open.push(i);
        let mut items = &menus[i].items;
        for label in rest {
            let Some(j) = items.iter().position(|it| it.label() == Some(*label)) else { return };
            self.open.push(j);
            match &items[j] {
                Item::Submenu { items: sub, .. } => items = sub,
                _ => return,
            }
        }
    }
}

pub fn show(ui: &mut Ui, rect: Rect, menus: &[Menu], st: &mut MenuBarState, commands: &mut Vec<Command>, p: &Palette) {
    let painter = ui.painter();
    painter.rect_filled(rect, 0.0, p.surface_bg);
    painter.line_segment(
        [pos2(rect.left(), rect.bottom() - 0.5), pos2(rect.right(), rect.bottom() - 0.5)],
        Stroke::new(1.0, p.border),
    );

    let mut hit_rects = vec![];
    let mut x = rect.left() + BAR_X;
    let mut popup_anchor = None;
    for (i, menu) in menus.iter().enumerate() {
        // Qt measures text in whole pixels.
        let w = widgets::text_width(ui, menu.title, 12.0, Weight::Regular).ceil() + 2.0 * BAR_ITEM_PAD;
        let item = Rect::from_min_size(pos2(x, rect.top() + BAR_ITEM_Y), vec2(w, BAR_ITEM_H));
        let resp = ui.interact(item, Id::new(("menubar", i)), Sense::click());
        let open = st.open.first() == Some(&i);
        if resp.clicked() {
            if open {
                st.close();
            } else {
                st.open = vec![i];
            }
        } else if resp.hovered() && st.is_open() && !open {
            st.open = vec![i];
        }
        if open || resp.hovered() {
            ui.painter().rect_filled(item, CornerRadius::same(7), p.hover);
        }
        widgets::text_center(ui, item.center(), menu.title, 12.0, Weight::Regular, p.text);
        if st.open.first() == Some(&i) {
            popup_anchor = Some(item.left_bottom());
        }
        hit_rects.push(item);
        x = item.right() + BAR_ITEM_GAP;
    }

    let Some(anchor) = popup_anchor else { return };
    let top = st.open[0];
    let mut level_items: &[Item] = &menus[top].items;
    let mut origin = anchor;
    let mut depth = 0;
    let mut clicked_inside = false;
    loop {
        let outcome = popup(ui, origin, level_items, depth, st, commands, p);
        hit_rects.push(outcome.rect);
        clicked_inside |= outcome.clicked_inside;
        if outcome.closed {
            st.close();
            return;
        }
        let Some((sub_index, sub_origin)) = outcome.submenu else { break };
        match &level_items[sub_index] {
            Item::Submenu { items, .. } => {
                level_items = items;
                origin = sub_origin;
                depth += 1;
            }
            _ => break,
        }
    }

    let (clicked, escape) = ui.input(|i| (i.pointer.any_click(), i.key_pressed(egui::Key::Escape)));
    let pointer = ui.input(|i| i.pointer.interact_pos());
    let outside = pointer.is_some_and(|pos| !hit_rects.iter().any(|r| r.contains(pos)));
    if escape || (clicked && outside && !clicked_inside) {
        st.close();
    }
}

struct PopupOutcome {
    rect: Rect,
    /// Index of an open submenu and where its popup goes.
    submenu: Option<(usize, Pos2)>,
    closed: bool,
    clicked_inside: bool,
}

pub fn popup_size(ui: &Ui, items: &[Item]) -> egui::Vec2 {
    let width = |s: &str| widgets::text_width(ui, s, 12.0, Weight::Regular);
    let mut max_text: f32 = 0.0;
    let mut max_text_with_shortcut: f32 = 0.0;
    let mut max_shortcut: f32 = 0.0;
    let mut checkable = false;
    let mut height = 2.0 * FRAME;
    for item in items {
        match item {
            Item::Separator => height += SEPARATOR_H,
            Item::Action { label, shortcut, checked, .. } => {
                max_text = max_text.max(width(label));
                if let Some(s) = shortcut {
                    max_shortcut = max_shortcut.max(width(s));
                    max_text_with_shortcut = max_text_with_shortcut.max(width(label));
                }
                checkable |= checked.is_some();
                height += ITEM_H;
            }
            Item::Submenu { label, .. } => {
                max_text = max_text.max(width(label));
                height += ITEM_H;
            }
        }
    }
    let mut content = max_text + max_shortcut;
    if max_shortcut > 0.0 {
        content = content.max(max_text_with_shortcut + SHORTCUT_GAP + max_shortcut);
    }
    let w = content + BASE_WIDTH + if checkable { CHECK_COL } else { 0.0 };
    vec2(w.round(), height)
}

fn popup(
    ui: &mut Ui,
    origin: Pos2,
    items: &[Item],
    depth: usize,
    st: &mut MenuBarState,
    commands: &mut Vec<Command>,
    p: &Palette,
) -> PopupOutcome {
    let size = popup_size(ui, items);
    let checkable = items.iter().any(|it| matches!(it, Item::Action { checked: Some(_), .. }));
    let rect = Rect::from_min_size(origin, size);
    let mut outcome = PopupOutcome { rect, submenu: None, closed: false, clicked_inside: false };

    Area::new(Id::new(("menu_popup", depth))).order(Order::Foreground).fixed_pos(origin).interactable(true).show(
        ui.ctx(),
        |ui| {
            let (rect, _) = ui.allocate_exact_size(size, Sense::click());
            widgets::qt_box(ui, rect, 10.0, p.surface_bg, p.border);
            let text_x = rect.left() + FRAME + PAD_LEFT + if checkable { CHECK_TEXT_SHIFT } else { 0.0 };
            let item_right = rect.right() - FRAME;
            let max_shortcut = items
                .iter()
                .filter_map(|it| match it {
                    Item::Action { shortcut: Some(s), .. } => Some(widgets::text_width(ui, s, 12.0, Weight::Regular)),
                    _ => None,
                })
                .fold(0.0_f32, f32::max);
            // Measured: Qt starts the shortcut column 2 2/3 px before the padding.
            let shortcut_x = item_right - PAD_RIGHT - max_shortcut - 8.0 / 3.0;

            let mut y = rect.top() + FRAME;
            for (i, item) in items.iter().enumerate() {
                if matches!(item, Item::Separator) {
                    let line_y = y + 5.0;
                    let line = Rect::from_min_max(
                        pos2(rect.left() + FRAME + 8.0, line_y),
                        pos2(item_right - 8.0, line_y + 1.0),
                    );
                    ui.painter().rect_filled(line, CornerRadius::ZERO, p.border);
                    y += SEPARATOR_H;
                    continue;
                }
                let r = Rect::from_min_max(pos2(rect.left() + FRAME, y), pos2(item_right, y + ITEM_H));
                y += ITEM_H;
                let enabled = !matches!(item, Item::Action { enabled: false, .. });
                let resp = ui.interact(
                    r,
                    Id::new(("menu_item", depth, i)),
                    if enabled { Sense::click() } else { Sense::hover() },
                );
                let sub_open = st.open.get(depth + 1) == Some(&i);
                if enabled && (resp.hovered() || sub_open) {
                    ui.painter().rect_filled(r, CornerRadius::same(7), p.hover);
                }
                let cy = r.center().y;
                match item {
                    Item::Action { label, shortcut, checked, command, .. } => {
                        if *checked == Some(true) {
                            check_mark(ui, pos2(r.left() + 7.0, cy + 1.0), p);
                        }
                        widgets::text_left(ui, text_x, cy, label, 12.0, Weight::Regular, p.text);
                        if let Some(s) = shortcut {
                            widgets::text_left(ui, shortcut_x, cy, s, 12.0, Weight::Regular, p.text);
                        }
                        if resp.hovered() && enabled {
                            st.open.truncate(depth + 1);
                        }
                        if resp.clicked() {
                            commands.push(*command);
                            outcome.closed = true;
                        }
                    }
                    Item::Submenu { label, .. } => {
                        widgets::text_left(ui, text_x, cy, label, 12.0, Weight::Regular, p.text);
                        let arrow_color = p.text.gamma_multiply(0.7);
                        widgets::arrow(ui, pos2(item_right - 16.5, cy), Arrow::Right, 3.0, 1.75, arrow_color);
                        if resp.hovered() || resp.clicked() {
                            st.open.truncate(depth + 1);
                            st.open.push(i);
                        }
                    }
                    Item::Separator => {}
                }
                if resp.clicked() {
                    outcome.clicked_inside = true;
                }
                if st.open.get(depth + 1) == Some(&i) && matches!(item, Item::Submenu { .. }) {
                    // Qt lines the submenu's first item up with this one.
                    outcome.submenu = Some((i, pos2(rect.right() - 1.0, r.top() - FRAME)));
                }
            }
        },
    );
    outcome
}

/// Fusion's menu check mark: a short and a long stroke.
fn check_mark(ui: &Ui, center: Pos2, p: &Palette) {
    // Traced from the full app's Edit menu at 1.5x.
    let pts = vec![
        pos2(center.x - 3.0, center.y - 2.0),
        pos2(center.x - 0.7, center.y + 1.2),
        pos2(center.x + 3.3, center.y - 4.2),
    ];
    ui.painter().add(Shape::line(pts, Stroke::new(2.0, p.text)));
}
