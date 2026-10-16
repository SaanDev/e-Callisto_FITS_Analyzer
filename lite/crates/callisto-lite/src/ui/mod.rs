//! The main window, laid out like the full app's (`MainWindow`):
//! menu bar, toolbar, then an 8 px-margin central area holding the sidebar,
//! a splitter handle, the sidebar toggle and the plot; the status bar below.

pub mod combine_dialog;
pub mod dialogs;
pub mod itemview;
pub mod menubar;
pub mod sidebar;
pub mod statusbar;
pub mod toolbar;
pub mod qlayout;
pub mod qt;
pub mod widgets;

use egui::{CornerRadius, Id, Rect, Sense, Ui, pos2, vec2};

use crate::session::Session;

use crate::menus::{self, Command, MenuState};
use crate::theme::Palette;
use widgets::Arrow;

pub const CENTRAL_MARGIN: f32 = 8.0;
const SPLITTER_HANDLE: f32 = 4.0;
const TOGGLE_LEFT: f32 = 3.0;
const TOGGLE_W: f32 = 18.0;
const TOGGLE_GAP: f32 = 5.0;

pub struct WindowState<'a> {
    pub menu: MenuState,
    pub menubar: &'a mut menubar::MenuBarState,
    pub sidebar: &'a mut sidebar::SidebarState,
    pub sections: &'a mut std::collections::BTreeMap<String, bool>,
    pub sidebar_collapsed: bool,
    pub session: &'a mut Session,
    pub update_status: &'a str,
    pub commands: &'a mut Vec<Command>,
}

pub fn main_window(ui: &mut Ui, w: WindowState<'_>, p: &Palette) {
    let full = ui.max_rect();
    ui.painter().rect_filled(full, 0.0, p.page_bg);

    let menubar_rect = Rect::from_min_size(full.min, vec2(full.width(), menubar::HEIGHT));
    let toolbar_rect =
        Rect::from_min_size(pos2(full.left(), menubar_rect.bottom()), vec2(full.width(), toolbar::HEIGHT));
    let status_rect = Rect::from_min_max(pos2(full.left(), full.bottom() - statusbar::HEIGHT), full.max);
    let central = Rect::from_min_max(
        pos2(full.left() + CENTRAL_MARGIN, toolbar_rect.bottom() + CENTRAL_MARGIN),
        pos2(full.right() - CENTRAL_MARGIN, status_rect.top() - CENTRAL_MARGIN),
    );

    let session = w.session;
    let buttons = toolbar::buttons(&session.toolbar_state());
    toolbar::show(ui, toolbar_rect, &buttons, w.commands, p);

    // Sidebar | splitter handle | toggle | plot.
    let mut x = central.left();
    if !w.sidebar_collapsed {
        let side = Rect::from_min_size(central.min, vec2(sidebar::SIDEBAR_W, central.height()));
        let mut cx = sidebar::SidebarContext { session, sections: w.sections, commands: w.commands };
        sidebar::show(ui, side, w.sidebar, &mut cx, p);
        x = side.right();
        splitter_grip(ui, Rect::from_min_size(pos2(x, central.top()), vec2(SPLITTER_HANDLE, central.height())), p);
        x += SPLITTER_HANDLE;
    }
    let toggle = Rect::from_min_size(pos2(x + TOGGLE_LEFT, central.top()), vec2(TOGGLE_W, central.height()));
    sidebar_toggle(ui, toggle, w.sidebar_collapsed, w.commands, p);
    let plot = Rect::from_min_max(pos2(toggle.right() + TOGGLE_GAP, central.top()), central.max);
    session.canvas.set_dark(p.dark);
    session.canvas.show(ui, plot);
    session.handle_canvas_events();

    let message = session.status_message().map(|(t, until)| {
        if let Some(until) = until {
            ui.ctx().request_repaint_after(until.saturating_duration_since(std::time::Instant::now()));
        }
        t.to_owned()
    });
    statusbar::show(ui, status_rect, message.as_deref(), w.update_status, &session.cursor_text, p);

    let menus = menus::menu_bar(&w.menu);
    menubar::show(ui, menubar_rect, &menus, w.menubar, w.commands, p);
}

/// Fusion's splitter grip: a column of six 1⅓ px dots, 3 px apart.
fn splitter_grip(ui: &Ui, rect: Rect, p: &Palette) {
    let color =
        if p.dark { egui::Color32::from_rgb(0x64, 0x68, 0x6d) } else { egui::Color32::from_rgb(0xbe, 0xbf, 0xc1) };
    let mid = rect.center().y;
    for k in 0..6 {
        let y = mid - 6.5 + 3.0 * k as f32;
        let dot = Rect::from_min_size(pos2(rect.left() + 2.0, y), vec2(4.0 / 3.0, 4.0 / 3.0));
        ui.painter().rect_filled(dot, CornerRadius::ZERO, color);
    }
}

/// `QToolButton#SidebarToggleButton`: a full-height strip with an arrow.
fn sidebar_toggle(ui: &Ui, rect: Rect, collapsed: bool, commands: &mut Vec<Command>, p: &Palette) {
    let resp = ui.interact(rect, Id::new("sidebar_toggle"), Sense::click());
    if resp.hovered() {
        widgets::qt_box(ui, rect, 10.0, p.hover, p.border);
    }
    let center = pos2(rect.left() + 9.0, rect.center().y - 0.15);
    let dir = if collapsed { Arrow::Right } else { Arrow::Left };
    widgets::arrow(ui, center, dir, 3.6, 1.8, p.text);
    if resp.clicked() {
        commands.push(Command::ToggleSidebar);
    }
    let tip = if collapsed { "Show the controls sidebar" } else { "Hide the controls sidebar" };
    resp.on_hover_text(tip);
}
