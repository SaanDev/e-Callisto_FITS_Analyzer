//! The analysis windows: Maximum Intensities (`MaxIntensityPlotDialog`),
//! the Analyzer (`AnalyzeDialog`) and Type II band-splitting
//! (`TypeIIBandSplittingDialog`), each its own window like the full app's
//! non-modal dialogs, sharing the session the main window keeps
//! (`Session::analysis`, `_on_analysis_session_changed`).

pub mod analyzer_window;
pub mod max_window;
pub mod rich;
pub mod ridge_dialog;

use analyzer_window::AnalyzerWindow;
use max_window::{MaxWindow, RidgeSource};

use std::time::{Duration, Instant};

use callisto_core::analysis::session::AnalysisSession;
use egui::{Id, Rect, Ui, pos2, vec2};

use crate::menus::{Command, Item, Menu, WindowAction};
use crate::session::{Notice, Session};
use crate::theme::Palette;
use crate::ui::{menubar, qt, statusbar, widgets};

/// What a window asks of the main window.
#[derive(Clone, Debug)]
pub enum WindowEvent {
    /// `sessionChanged`: the window's state, to share with the others.
    SessionChanged(AnalysisSession),
    /// `requestOpenAnalyzer`.
    OpenAnalyzer(AnalysisSession),
    Notice(Notice),
}

/// Which window a session change came from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Source {
    Max,
    Analyzer,
    TypeII,
}

impl Source {
    fn tag(self) -> &'static str {
        match self {
            Source::Max => "max",
            Source::Analyzer => "analyzer",
            Source::TypeII => "type_ii",
        }
    }
}

/// The open analysis windows and the main window's side of them.
#[derive(Default)]
pub struct AnalysisWindows {
    pub max: Option<MaxWindow>,
    pub analyzer: Option<AnalyzerWindow>,
    sync_guard: bool,
}

impl AnalysisWindows {
    /// `_close_analysis_windows`.
    pub fn close_all(&mut self) {
        self.max = None;
        self.analyzer = None;
    }

    /// `_open_or_focus_analyzer_dialog`.
    pub fn open_or_focus_analyzer(&mut self, s: &mut Session, session: Option<AnalysisSession>, auto_clean: bool) {
        let mut candidate = s.with_analysis_context(session.as_ref()).or_else(|| s.with_analysis_context(s.analysis.as_ref()));
        match &candidate {
            None => candidate = s.build_analysis_seed(auto_clean),
            Some(c) if c.max_intensity.is_none() => candidate = s.merge_seed_with_existing(s.build_analysis_seed(auto_clean), Some(c)),
            _ => {}
        }
        let Some(candidate) = candidate else {
            return s.show_message("Analyzer requires max-intensity data first.", 4000);
        };
        if candidate.max_intensity.is_none() {
            return s.show_message("Analyzer requires max-intensity vectors.", 4000);
        }
        match &mut self.analyzer {
            None => self.analyzer = Some(AnalyzerWindow::new(&candidate, s.filename())),
            Some(w) => w.restore_session(&candidate),
        }
        let ut = s.data.as_ref().and_then(|d| d.ut_start_sec);
        if let Some(w) = &mut self.analyzer {
            // Lets a custom power-law t0 be entered in UT.
            w.set_ut_start_sec(ut);
            w.focus();
        }
    }

    /// `plot_max_intensities` (`prefer_current_plot`) and
    /// `_open_or_focus_max_dialog`.
    pub fn open_or_focus_max(&mut self, s: &mut Session, session: Option<AnalysisSession>, auto_open_analyzer: bool, prefer_current_plot: bool, auto_clean: bool) {
        let seed = || s.build_analysis_seed(auto_clean);
        let mut candidate = if prefer_current_plot { seed() } else { None };
        if candidate.is_none() {
            candidate = s.with_analysis_context(session.as_ref()).or_else(|| s.with_analysis_context(s.analysis.as_ref()));
        }
        if candidate.is_none() {
            candidate = seed();
        }
        let Some(mut candidate) = candidate else {
            return s.show_message("Analysis window requires a plotted dynamic spectrum.", 4000);
        };
        if candidate.max_intensity.is_none() {
            match s.merge_seed_with_existing(seed(), Some(&candidate)) {
                Some(c) => candidate = c,
                None => return s.show_message("Analysis window requires a plotted dynamic spectrum.", 4000),
            }
        }
        let auto_outlier_mode = (prefer_current_plot && s.is_isolated_burst_plot() && auto_clean) || candidate.ui.auto_outlier_cleaned;
        if candidate.max_intensity.is_none() {
            return s.show_message("Analysis session has no max-intensity vectors.", 4000);
        }
        match &mut self.max {
            None => self.max = Some(MaxWindow::new(&candidate, s.filename(), auto_outlier_mode)),
            Some(w) => {
                w.set_auto_outlier_mode(auto_outlier_mode);
                w.restore_session(&candidate);
            }
        }
        // Track Ridge follows the spectrum on show, the one the maxima came from.
        let ridge = match (s.current_dynamic_spectrum_source(), &s.data) {
            (Some(data), Some(d)) => Some(RidgeSource { data, freqs: d.freqs.clone(), time: d.time.clone() }),
            _ => None,
        };
        if let Some(w) = &mut self.max {
            w.set_ridge_source(ridge);
            w.focus();
        }
        self.on_session_changed(s, candidate.clone(), Source::Max, false);
        if auto_outlier_mode {
            let removed = candidate.ui.auto_removed_count;
            if removed > 0 {
                s.show_message(format!("Auto-cleaned isolated burst maxima: removed {removed} outlier columns."), 4500);
            } else {
                s.show_message("Auto-cleaned isolated burst maxima.", 3000);
            }
        }
        if auto_open_analyzer {
            self.open_or_focus_analyzer(s, Some(candidate), auto_clean);
        }
    }

    /// `_on_analysis_session_changed`: the shared session, the summary and
    /// the other windows follow a change.
    pub fn on_session_changed(&mut self, s: &mut Session, payload: AnalysisSession, source: Source, log: bool) {
        if self.sync_guard {
            return;
        }
        let Some(session) = s.with_analysis_context(Some(&payload)) else { return };
        self.sync_guard = true;
        s.analysis = Some(session.clone());
        if source != Source::Max
            && let Some(w) = &mut self.max
        {
            w.restore_session(&session);
        }
        if source != Source::Analyzer
            && let Some(w) = &mut self.analyzer
        {
            w.restore_session(&session);
        }
        self.sync_guard = false;
        if log {
            s.log_analysis(format!("Updated analysis session ({}).", source.tag()));
        }
    }

    /// Shows the open windows and acts on what they ask.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette, s: &mut Session, auto_clean: bool) {
        if std::mem::take(&mut s.analysis_windows_close_due) {
            self.close_all();
        }
        let mut events = Vec::new();
        if let Some(w) = &mut self.max {
            let (ev, closed) = w.show(ctx, p);
            events.extend(ev.into_iter().map(|e| (Source::Max, e)));
            if closed {
                self.max = None;
            }
        }
        if let Some(w) = &mut self.analyzer {
            let (ev, closed) = w.show(ctx, p);
            events.extend(ev.into_iter().map(|e| (Source::Analyzer, e)));
            if closed {
                self.analyzer = None;
            }
        }
        for (source, e) in events {
            match e {
                WindowEvent::SessionChanged(payload) => self.on_session_changed(s, payload, source, true),
                WindowEvent::OpenAnalyzer(payload) => self.open_or_focus_analyzer(s, Some(payload), auto_clean),
                WindowEvent::Notice(n) => s.notices.push(n),
            }
        }
    }
}

/// `QStatusBar.showMessage(text, timeout)`: the message until it times out.
#[derive(Clone, Debug, Default)]
pub struct StatusLine {
    text: String,
    until: Option<Instant>,
}

impl StatusLine {
    pub fn show(&mut self, text: impl Into<String>, timeout_ms: u64) {
        self.text = text.into();
        self.until = (timeout_ms > 0).then(|| Instant::now() + Duration::from_millis(timeout_ms));
    }

    /// The message on show (empty once timed out); asks for a repaint when it will.
    pub fn current(&mut self, ctx: &egui::Context) -> &str {
        if let Some(until) = self.until {
            let now = Instant::now();
            if now >= until {
                self.text.clear();
                self.until = None;
            } else {
                ctx.request_repaint_after(until - now);
            }
        }
        &self.text
    }
}

/// A menu of the window's own menu bar.
pub(crate) fn menu(title: &'static str, items: Vec<(&'static str, WindowAction, bool)>) -> Menu {
    Menu { title, items: items.into_iter().map(|(label, a, on)| Item::action(label, Command::Window(a)).enabled(on)).collect() }
}

/// The menu bar along the top (`layout.setMenuBar`); returns what was chosen.
pub(crate) fn window_menubar(ui: &mut Ui, width: f32, menus: &[Menu], st: &mut menubar::MenuBarState, p: &Palette) -> Vec<WindowAction> {
    let r = Rect::from_min_size(ui.max_rect().min, vec2(width, menubar::HEIGHT));
    let mut commands = Vec::new();
    menubar::show(ui, r, menus, st, &mut commands, p);
    commands.into_iter().filter_map(|c| if let Command::Window(a) = c { Some(a) } else { None }).collect()
}

/// A `QStatusBar` in a dialog's layout: the message and the size grip.
pub(crate) fn status_bar(ui: &Ui, rect: Rect, message: &str, p: &Palette) {
    statusbar::show(ui, rect, (!message.is_empty()).then_some(message), "", "", p);
}

/// A checkable `QPushButton` (`QPushButton:checked`: accent border and
/// soft accent fill). Returns true when clicked.
pub(crate) fn toggle_button(ui: &Ui, rect: Rect, id: Id, text: &str, checked: bool, enabled: bool, p: &Palette) -> bool {
    if !checked || !enabled {
        return qt::button(ui, rect, id, text, false, enabled, p).clicked();
    }
    let resp = ui.interact(rect, id, egui::Sense::click());
    widgets::qt_box(ui, rect, 10.0, p.accent_soft, p.accent);
    widgets::text_center(ui, rect.center(), text, qt::FONT_PX, crate::fonts::Weight::Regular, p.text);
    resp.clicked()
}

/// `QRadioButton` under the app stylesheet: a 15 px indicator with a 1 px
/// border, radius 7, the accent when checked, the text 7 px after it.
/// Returns true when an unchecked, enabled button is clicked.
pub(crate) fn radio(ui: &Ui, rect: Rect, id: Id, label: &str, checked: bool, enabled: bool, p: &Palette) -> bool {
    let resp = ui.interact(rect, id, if enabled { egui::Sense::click() } else { egui::Sense::hover() });
    let ind = Rect::from_min_size(pos2(rect.left(), rect.center().y - 8.5), vec2(17.0, 17.0));
    if checked {
        widgets::qt_box(ui, ind, 7.0, p.accent, p.accent);
    } else {
        widgets::qt_box(ui, ind, 7.0, p.input_bg, p.border);
    }
    let text = if enabled { p.text } else { p.disabled };
    widgets::text_left(ui, rect.left() + 24.0, rect.center().y, label, qt::FONT_PX, crate::fonts::Weight::Regular, text);
    enabled && resp.clicked() && !checked
}
