//! Download → Launch FITS Downloader (`CallistoDownloaderApp`, with its
//! Single Station, Multi-Station Event, Spectral Overview and Burst List
//! tabs), the FITS preview windows, the Learmonth browser, and the import
//! progress dialog the main window shows while it prepares a selection.

pub mod batch;
pub mod burst;
pub mod event;
pub mod import;
pub mod jobs;
pub mod learmonth;
pub mod overview;
pub mod preview;
pub mod single;
pub mod stations;

use std::path::{Path, PathBuf};

use callisto_core::radio::cache;
use chrono::NaiveDate;
use egui::{Id, Pos2, Rect, Sense, Ui, ViewportBuilder, ViewportId, pos2, vec2};

use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;
use crate::ui::qlayout::{BoxLayout, Geometry, IRect, Layout, Size};
use crate::ui::qt;
use stations::StationService;

/// Opens a separate, resizable window (a `QDialog`). `body` returns
/// `true` to close it; the title bar's close button sets `close_requested`.
pub fn window(
    ctx: &egui::Context,
    id: &str,
    title: &str,
    size: egui::Vec2,
    min_size: egui::Vec2,
    p: &Palette,
    body: impl FnOnce(&mut Ui, bool),
) {
    let viewport = ViewportId::from_hash_of(id);
    let builder = ViewportBuilder::default()
        .with_title(title)
        .with_inner_size(size)
        .with_min_inner_size(min_size)
        .with_resizable(true)
        .with_minimize_button(false);
    let bg = p.page_bg;
    let mut body = Some(body);
    ctx.show_viewport_immediate(viewport, builder, move |ctx, _class| {
        let close = ctx.input(|i| i.viewport().close_requested());
        if close {
            // The dialog decides whether it closes (busy work cancels first).
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
        }
        egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| {
            if let Some(body) = body.take() {
                body(ui, close);
            }
        });
    });
}

/// `fit_window_to_screen`: the design size, clamped to the monitor.
pub fn fit_to_screen(ctx: &egui::Context, width: f32, height: f32) -> egui::Vec2 {
    let monitor = ctx.input(|i| i.viewport().monitor_size).unwrap_or(vec2(1920.0, 1080.0));
    vec2(width.min((monitor.x * 0.94).max(320.0)).round(), height.min((monitor.y * 0.90).max(240.0)).round())
}

/// A blocking overlay: the window below a modal dialog ignores the pointer.
pub fn block_input(ui: &Ui, id: Id) {
    let rect = ui.ctx().content_rect();
    egui::Area::new(id).order(egui::Order::Foreground).fixed_pos(rect.min).show(ui.ctx(), |ui| {
        ui.allocate_rect(rect, Sense::click_and_drag());
    });
}

/// A question (`QMessageBox.question`, Yes/No) shown over a dialog.
#[derive(Clone, Debug)]
pub struct Question {
    pub title: String,
    pub text: String,
}

/// Shows a Yes/No question; returns `Some(answer)` once answered.
pub fn question_box(ctx: &egui::Context, q: &Question, p: &Palette) -> Option<bool> {
    let text_w = 420.0;
    let probe = ctx.fonts_mut(|f| {
        f.layout(q.text.clone(), crate::fonts::ui_font(12.0, crate::fonts::Weight::Regular), p.text, text_w)
    });
    let size = vec2(24.0 + 48.0 + text_w, 36.0 + probe.size().y.max(32.0) + 46.0);
    let mut answer = None;
    crate::ui::dialogs::dialog_window(ctx, &format!("question:{}", q.title), &q.title, size, p, false, |ui| {
        let full = ui.max_rect();
        let icon = Rect::from_min_size(full.min + vec2(12.0, 12.0), vec2(32.0, 32.0));
        ui.painter().circle_filled(icon.center(), 16.0, egui::Color32::from_rgb(0x1f, 0x6f, 0xeb));
        ui.painter().text(
            icon.center(),
            egui::Align2::CENTER_CENTER,
            "?",
            crate::fonts::ui_font(20.0, crate::fonts::Weight::Bold),
            egui::Color32::WHITE,
        );
        let galley = ctx.fonts_mut(|f| {
            f.layout(q.text.clone(), crate::fonts::ui_font(12.0, crate::fonts::Weight::Regular), p.text, text_w)
        });
        ui.painter().galley(pos2(icon.right() + 16.0, full.top() + 12.0), galley, p.text);
        let by = full.bottom() - 12.0 - 34.0;
        let no = Rect::from_min_size(pos2(full.right() - 12.0 - 88.0, by), vec2(88.0, 34.0));
        let yes = Rect::from_min_size(pos2(no.left() - 8.0 - 88.0, by), vec2(88.0, 34.0));
        if qt::button(ui, yes, Id::new("q_yes"), "Yes", false, true, p).clicked() {
            answer = Some(true);
        }
        if qt::button(ui, no, Id::new("q_no"), "No", false, true, p).clicked()
            || ui.input(|i| i.key_pressed(egui::Key::Escape))
        {
            answer = Some(false);
        }
        answer
    })
    .or(answer)
}

/// What a downloader tab asks of the dialog and the main window.
#[derive(Default)]
pub struct Outbox {
    pub notices: Vec<Notice>,
    pub question: Option<(Question, QuestionPurpose)>,
    pub import: Option<Vec<String>>,
    pub compare: Option<Vec<String>>,
    pub previews: Vec<preview::PreviewWindow>,
    pub cache_changed: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum QuestionPurpose {
    ClearCache,
}

impl Outbox {
    pub fn notice(&mut self, kind: NoticeKind, title: &str, text: impl Into<String>) {
        self.notices.push(Notice { kind, title: title.into(), text: text.into() });
    }
}

/// What a tab sees each frame.
pub struct Shared<'a> {
    pub p: &'a Palette,
    pub ctx: egui::Context,
    pub svc: &'a mut StationService,
    pub resolved: &'a [NaiveDate],
    pub cache_root: &'a Path,
    pub out: &'a mut Outbox,
}

/// Lays `layout` out in `rect` and returns the widgets' rectangles in
/// screen coordinates (`rect` in whole logical pixels).
pub fn lay_out(layout: &Layout, rect: Rect) -> (Geometry, Pos2) {
    let origin = rect.min.round();
    let size = Size::new(rect.width().round() as i32, rect.height().round() as i32);
    let mut g = Geometry::default();
    layout.set_geometry(IRect::new(0, 0, size.w, size.h), &mut g);
    (g, origin)
}

/// The rectangle of `id` in `g`, on screen.
pub fn at(g: &Geometry, origin: Pos2, id: &str) -> Rect {
    g.rect(id).to_egui(origin)
}

/// The observation dates and times that survive a reopen (`date_time_state`).
#[derive(Clone, Debug)]
pub struct DateTimeState {
    pub tab: usize,
    pub single_date: NaiveDate,
    pub event_start: chrono::NaiveDateTime,
    pub event_stop: chrono::NaiveDateTime,
    pub overview_date: NaiveDate,
    pub burst_start: NaiveDate,
    pub burst_end: NaiveDate,
}

/// What the downloader asks the main window to do.
pub enum DownloaderAction {
    Import(Vec<String>),
    Compare(Vec<String>),
    Close,
}

pub struct DownloaderDialog {
    tab: usize,
    pub single: single::SingleTab,
    pub event: event::EventTab,
    pub overview: overview::OverviewTab,
    pub burst: burst::BurstTab,
    cache_root: PathBuf,
    cache_label: String,
    notices: Vec<Notice>,
    question: Option<(Question, QuestionPurpose)>,
    previews: Vec<preview::PreviewWindow>,
    /// Closing waits for busy work to stop (`done` / `closeEvent`).
    pending_close: bool,
    size: Option<egui::Vec2>,
    /// Another modal window (the import dialog) is in front.
    pub blocked: bool,
    /// Scripted steps, run one per frame (see `run_script`).
    pub script: std::collections::VecDeque<String>,
}

const TABS: [&str; 4] = ["Single Station", "Multi-Station Event", "Spectral Overview", "Burst List"];

impl DownloaderDialog {
    pub fn new(cache_root: PathBuf, svc: &mut StationService, state: Option<&DateTimeState>) -> Self {
        let mut d = DownloaderDialog {
            tab: 0,
            single: single::SingleTab::new(),
            event: event::EventTab::new(),
            overview: overview::OverviewTab::new(),
            burst: burst::BurstTab::new(),
            cache_label: String::new(),
            cache_root,
            notices: Vec::new(),
            question: None,
            previews: Vec::new(),
            pending_close: false,
            size: None,
            blocked: false,
            script: Default::default(),
        };
        if let Some(s) = state {
            d.tab = s.tab.min(3);
            d.single.date = s.single_date;
            d.event.start = s.event_start;
            d.event.stop = s.event_stop;
            d.overview.date = s.overview_date;
            let defaults = (d.burst.start, d.burst.end);
            d.burst.start = s.burst_start;
            d.burst.end = s.burst_end;
            if (s.burst_start, s.burst_end) != defaults {
                // `setDate` emits `dateChanged`.
                d.burst.dates_changed();
            }
        }
        d.single.request_stations(svc);
        d.overview.request_stations(svc);
        d.event.request_stations(svc);
        d.update_cache_label();
        d
    }

    pub fn date_time_state(&self) -> DateTimeState {
        DateTimeState {
            tab: self.tab,
            single_date: self.single.date,
            event_start: self.event.start,
            event_stop: self.event.stop,
            overview_date: self.overview.date,
            burst_start: self.burst.start,
            burst_end: self.burst.end,
        }
    }

    pub fn cache_root(&self) -> &Path {
        &self.cache_root
    }

    fn update_cache_label(&mut self) {
        self.cache_label = format!("Cache: {}", cache::format_size(cache::cache_size_bytes(&self.cache_root)));
    }

    /// `_cache_is_busy`.
    fn cache_busy(&self) -> bool {
        self.burst.is_busy() || self.single.is_busy() || self.event.is_busy() || self.overview.is_running()
    }

    /// The dialog's size (`_fit_to_available_screen`).
    pub fn initial_size(ctx: &egui::Context) -> egui::Vec2 {
        let monitor = ctx.input(|i| i.viewport().monitor_size).unwrap_or(vec2(1920.0, 1080.0));
        // Qt uses the work area; the taskbar is about 48 px on Windows.
        let avail = vec2(monitor.x, monitor.y - 48.0);
        let w = 1540f32.min((avail.x * 0.94).floor().max(760.0)).min(avail.x);
        let h = 980f32.min((avail.y * 0.90).floor().max(560.0)).min(avail.y);
        vec2(w, h)
    }

    /// Draws the dialog's window and its previews. Returns what the main
    /// window should do.
    pub fn show(&mut self, ctx: &egui::Context, svc: &mut StationService, p: &Palette) -> Option<DownloaderAction> {
        let size = *self.size.get_or_insert_with(|| Self::initial_size(ctx));
        let modal = !self.notices.is_empty() || self.question.is_some() || self.blocked;
        let mut out = Outbox::default();
        window(ctx, "callisto_downloader", "e-CALLISTO FITS Downloader", size, vec2(640.0, 480.0), p, |ui, close| {
            if close {
                self.pending_close = true;
            }
            out = self.frame(ui, svc, p);
            if modal {
                block_input(ui, Id::new("downloader_modal"));
            }
        });
        self.finish(ctx, out, p, true)
    }

    /// One frame of the dialog's contents in `ui` (its whole window).
    pub fn frame(&mut self, ui: &mut Ui, svc: &mut StationService, p: &Palette) -> Outbox {
        let ctx = ui.ctx().clone();
        let resolved = svc.poll(&ctx);
        let mut out = Outbox::default();
        let root = self.cache_root.clone();
        let mut sh = Shared { p, ctx, svc, resolved: &resolved, cache_root: &root, out: &mut out };
        if let Some(cmd) = self.script.pop_front() {
            self.run_script(&cmd, &mut sh);
        }
        self.body(ui, &mut sh);
        out
    }

    /// Takes in what a frame produced: message boxes, questions, previews
    /// (shown when `windows`), and the action for the main window.
    pub fn finish(
        &mut self,
        ctx: &egui::Context,
        mut out: Outbox,
        p: &Palette,
        windows: bool,
    ) -> Option<DownloaderAction> {
        let mut action = None;
        // Message boxes and questions belong to the dialog.
        self.notices.append(&mut out.notices);
        if out.question.is_some() {
            self.question = out.question.take();
        }
        self.previews.append(&mut out.previews);
        if out.cache_changed {
            self.update_cache_label();
        }
        if windows {
            if let Some(n) = self.notices.first()
                && crate::ui::dialogs::message_box(ctx, n, p)
            {
                self.notices.remove(0);
            }
            if let Some((q, purpose)) = &self.question
                && let Some(answer) = question_box(ctx, q, p)
            {
                let purpose = *purpose;
                self.question = None;
                if answer && purpose == QuestionPurpose::ClearCache {
                    let skipped = cache::clear_cache(&self.cache_root);
                    self.update_cache_label();
                    if skipped > 0 {
                        self.notices.push(Notice {
                            kind: NoticeKind::Warning,
                            title: "Cache Cleared".into(),
                            text: format!("Cache cleared with {skipped} item(s) skipped (in use)."),
                        });
                    }
                }
            }
            self.previews.retain_mut(|w| !w.show(ctx, p));
        }
        if let Some(urls) = out.import.take() {
            action = Some(DownloaderAction::Import(urls));
        }
        if let Some(urls) = out.compare.take() {
            // `comparison_request.emit(urls); self.accept()`.
            self.pending_close = true;
            action = Some(DownloaderAction::Compare(urls));
        }
        if self.pending_close {
            // Busy work is asked to stop; the dialog closes once it has.
            self.burst.request_cancel();
            self.overview.request_cancel();
            if !self.burst.is_busy() && !self.overview.is_running() {
                return action.or(Some(DownloaderAction::Close));
            }
        }
        action
    }

    /// The message boxes waiting to be shown (for tests).
    pub fn pending_notices(&self) -> &[Notice] {
        &self.notices
    }

    /// The preview windows the dialog opened (for tests).
    pub fn previews_mut(&mut self) -> &mut Vec<preview::PreviewWindow> {
        &mut self.previews
    }

    /// Scripted steps (tests and reference captures): `tab N`, then
    /// tab-specific commands, one per frame.
    fn run_script(&mut self, cmd: &str, sh: &mut Shared) {
        let parts: Vec<&str> = cmd.split('|').collect();
        match parts.as_slice() {
            ["tab", n] => self.tab = n.parse().unwrap_or(0),
            ["single", rest @ ..] => self.single.script(rest, sh),
            ["event", rest @ ..] => self.event.script(rest, sh),
            ["overview", rest @ ..] => self.overview.script(rest, sh),
            ["burst", rest @ ..] => self.burst.script(rest, sh),
            _ => {}
        }
    }

    /// Whether any tab is still working (for tests).
    pub fn busy(&self) -> bool {
        self.cache_busy() || self.single.is_busy() || self.event.is_busy()
    }

    /// Whether the main window should close the dialog after `action`.
    pub fn closing(&self) -> bool {
        self.pending_close
    }

    fn body(&mut self, ui: &mut Ui, sh: &mut Shared) {
        let full = ui.max_rect();
        let p = sh.p;
        // QVBoxLayout(14, 8): the tab widget, then the cache footer.
        let footer = BoxLayout::h([0; 4], 8)
            .widget(qt::status_spec(ui, "cache_label", &self.cache_label))
            .stretch(1)
            .widget(qt::button_spec(ui, "open_cache", "Open Cache Folder", false))
            .widget(qt::button_spec(ui, "clear_cache", "Clear Cache", false));
        let tabs = crate::ui::qlayout::WidgetSpec::new(
            "tabs",
            Size::new(844, 997),
            crate::ui::qlayout::Policy::Expanding,
            crate::ui::qlayout::Policy::Expanding,
        );
        let root: Layout = BoxLayout::v([14; 4], 8).widget(tabs).add(footer.into_item()).into();
        let (g, o) = lay_out(&root, full);
        let tabs_rect = at(&g, o, "tabs");

        // Tab bar and page (`::pane { top: 6px }`).
        let enabled = [
            !(self.overview.is_running() || self.burst.is_busy()) || self.tab == 0,
            !(self.overview.is_running() || self.burst.is_busy()) || self.tab == 1,
            !self.burst.is_busy() || self.tab == 2,
            !self.overview.is_running() || self.tab == 3,
        ];
        if let Some(t) = qt::tab_bar(ui, tabs_rect.min, Id::new("downloader_tabs"), &TABS, self.tab, &enabled, p)
            && enabled[t]
        {
            self.tab = t;
        }
        let page = Rect::from_min_max(pos2(tabs_rect.left(), tabs_rect.top() + 50.0), tabs_rect.max);
        // Every tab keeps working while another is shown.
        self.single.tick(sh);
        self.event.tick(sh);
        self.overview.tick(sh);
        self.burst.tick(sh);
        match self.tab {
            0 => self.single.show(ui, page, sh),
            1 => self.event.show(ui, page, sh),
            2 => self.overview.show(ui, page, sh),
            _ => self.burst.show(ui, page, sh),
        }

        qt::status(ui, at(&g, o, "cache_label"), &self.cache_label, p);
        if qt::button(ui, at(&g, o, "open_cache"), Id::new("open_cache"), "Open Cache Folder", false, true, p).clicked()
        {
            let _ = std::fs::create_dir_all(&self.cache_root);
            let _ = open::that_detached(&self.cache_root);
        }
        if qt::button(ui, at(&g, o, "clear_cache"), Id::new("clear_cache"), "Clear Cache", false, true, p).clicked() {
            if self.cache_busy() {
                sh.out.notice(
                    NoticeKind::Information,
                    "Cache Busy",
                    "Cannot clear the cache while a download or preview is running.",
                );
            } else {
                let size = cache::format_size(cache::cache_size_bytes(&self.cache_root));
                sh.out.question = Some((
                    Question {
                        title: "Clear Cache".into(),
                        text: format!(
                            "Delete all cached e-CALLISTO FITS files?\n\n{}\n({size})",
                            self.cache_root.display()
                        ),
                    },
                    QuestionPurpose::ClearCache,
                ));
            }
        }
    }
}

/// The status-label specs of a tab: a word-wrapped `#DownloaderStatusLabel`.
pub fn status_label(ui: &Ui, id: &'static str, text: &str) -> crate::ui::qlayout::WidgetSpec {
    qt::wrap_label_spec(ui, id, text, true)
}
