//! The Maximum Intensities window (`MaxIntensityPlotDialog`): the peak
//! frequency of every time column as red dots, outliers picked with a lasso
//! and removed, the burst's ridge tracked through the spectrum in their
//! place, the points saved as CSV or exported as an OriginPro graph, and
//! the Analyzer opened on them.

use std::path::PathBuf;
use std::sync::Arc;

use callisto_core::analysis::analyzer::resolve_time_seconds;
use callisto_core::analysis::lasso::contains_points;
use callisto_core::analysis::ridge::{RidgeSettings, track_ridge};
use callisto_core::analysis::session::{AnalysisSession, AnalyzerBlock, MaxIntensityBlock, SessionSource, TypeIIBlock, UiBlock};
use callisto_core::mpl::analysis_figures::{FitGraph, GraphText, TAB_BLUE, fit_figure, max_intensity_figure};
use callisto_core::mpl::{Figure, Line2D};
use callisto_core::radio::array::Array2;
use egui::{Id, Pos2, Rect, Sense, Ui, vec2};

use super::ridge_dialog::RidgeSettingsDialog;
use super::{StatusLine, WindowEvent, menu, radio, status_bar, toggle_button, window_menubar};
use crate::figure_view::FigureView;
use crate::fonts::Weight;
use crate::menus::WindowAction;
use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;
use crate::ui::menubar::{self, MenuBarState};
use crate::ui::qlayout::{BoxLayout, Geometry, IRect, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

pub const TITLE: &str = "Maximum Intensities for Each Time Channel";
const MAX_TITLE: &str = "Maximum Intensity for Each Time Channel";

/// The dynamic spectrum Track Ridge follows, with its axes.
#[derive(Clone)]
pub struct RidgeSource {
    pub data: Arc<Array2>,
    pub freqs: Arc<Vec<f64>>,
    pub time: Arc<Vec<f64>>,
}

/// `LassoSelector` while drawing: the vertices in data coordinates.
#[derive(Default)]
struct Lasso {
    verts: Option<Vec<(f64, f64)>>,
}

pub struct MaxWindow {
    filename: String,
    source: SessionSource,
    time_channels: Vec<f64>,
    time_seconds: Vec<f64>,
    freqs: Vec<f64>,
    selected: Vec<bool>,
    harmonic: bool,
    /// The axes' title, and the one `_redraw_points` last set.
    title: String,
    plot_title: String,
    ridge_source: Option<RidgeSource>,
    ridge_settings: RidgeSettings,
    seed: Option<(f64, f64)>,
    /// The seed's star is on the axes (`_draw_ridge_seed` since the last redraw).
    seed_drawn: bool,
    pre_ridge: Option<(Vec<f64>, Vec<f64>, Vec<f64>)>,
    analyzer_state: Option<AnalyzerBlock>,
    type_ii_state: Option<TypeIIBlock>,
    lasso: Option<Lasso>,
    pick_start: bool,
    status: StatusLine,
    menubar: MenuBarState,
    view: FigureView,
    figure_due: bool,
    ridge_dialog: Option<RidgeSettingsDialog>,
    notice: Option<Notice>,
    events: Vec<WindowEvent>,
    /// `raise_()` and `activateWindow()` on the next frame.
    focus_due: bool,
}

/// Python's `repr` of a float, as `csv.writer` writes it.
fn py_repr(v: f64) -> String {
    if v.is_nan() {
        return "nan".into();
    }
    if v.is_infinite() {
        return if v > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let a = v.abs();
    if a != 0.0 && !(1e-4..1e16).contains(&a) {
        // 1e-05, 1.5e+16: at least two exponent digits.
        let s = format!("{v:e}");
        let (m, e) = s.split_once('e').unwrap_or((&s, "0"));
        let (sign, digits) = if let Some(d) = e.strip_prefix('-') { ('-', d) } else { ('+', e) };
        return format!("{m}e{sign}{digits:0>2}");
    }
    if v.fract() == 0.0 { format!("{v:.1}") } else { format!("{v}") }
}

impl MaxWindow {
    /// `MaxIntensityPlotDialog(time_channels, max_freqs, filename, session=…, auto_outlier_mode=…)`.
    pub fn new(session: &AnalysisSession, filename: &str, auto_outlier_mode: bool) -> Self {
        let block = session.max_intensity.clone().unwrap_or(MaxIntensityBlock {
            time_channels: Vec::new(),
            time_seconds: None,
            freqs: Vec::new(),
            fundamental: true,
            harmonic: false,
        });
        let time_seconds = resolve_time_seconds(&block.time_channels, block.time_seconds.as_deref());
        let mut w = MaxWindow {
            filename: filename.to_owned(),
            source: session.source.clone(),
            selected: vec![false; block.time_channels.len()],
            time_channels: block.time_channels,
            time_seconds,
            freqs: block.freqs,
            harmonic: false,
            title: MAX_TITLE.into(),
            plot_title: MAX_TITLE.into(),
            ridge_source: None,
            ridge_settings: RidgeSettings::default(),
            seed: None,
            seed_drawn: false,
            pre_ridge: None,
            analyzer_state: None,
            type_ii_state: None,
            lasso: None,
            pick_start: false,
            status: StatusLine::default(),
            menubar: MenuBarState::default(),
            view: FigureView::default(),
            figure_due: true,
            ridge_dialog: None,
            notice: None,
            events: Vec::new(),
            focus_due: true,
        };
        w.status.show("Ready", 0);
        w.set_auto_outlier_mode(auto_outlier_mode);
        w.restore_session(session);
        w
    }

    /// Raises the window.
    pub fn focus(&mut self) {
        self.focus_due = true;
    }

    /// `set_auto_outlier_mode`.
    pub fn set_auto_outlier_mode(&mut self, enabled: bool) {
        if enabled {
            self.status.show("Auto outlier cleaning enabled for isolated burst; manual outlier tools remain available.", 3500);
        }
    }

    /// `restore_session`: the points, the mode and the other windows' state.
    pub fn restore_session(&mut self, session: &AnalysisSession) {
        if let Some(m) = &session.max_intensity
            && m.time_channels.len() == m.freqs.len()
            && !m.time_channels.is_empty()
        {
            self.time_channels = m.time_channels.clone();
            self.time_seconds = resolve_time_seconds(&self.time_channels, m.time_seconds.as_deref());
            self.freqs = m.freqs.clone();
            self.selected = vec![false; self.time_channels.len()];
        }
        self.harmonic = session.max_intensity.as_ref().is_some_and(|m| m.harmonic);
        self.analyzer_state = session.analyzer.has_content().then(|| session.analyzer.clone());
        self.type_ii_state = session.type_ii.has_content().then(|| session.type_ii.clone());
        self.source = session.source.clone();
        self.redraw_points(MAX_TITLE);
    }

    /// `set_ridge_source`.
    pub fn set_ridge_source(&mut self, source: Option<RidgeSource>) {
        self.ridge_source = source.filter(|s| {
            let (rows, cols) = s.data.shape();
            rows == s.freqs.len() && cols == s.time.len() && rows * cols > 0
        });
        if self.ridge_source.is_none() {
            self.pick_start = false;
        }
    }

    /// `session_state`.
    pub fn session_state(&self) -> AnalysisSession {
        let analyzer = self.analyzer_state.clone().unwrap_or_default();
        let type_ii = self.type_ii_state.clone().unwrap_or_default();
        AnalysisSession {
            identity: Default::default(),
            source: SessionSource { filename: self.filename.clone(), ..self.source.clone() },
            max_intensity: Some(MaxIntensityBlock {
                time_channels: self.time_channels.clone(),
                time_seconds: Some(self.time_seconds.clone()),
                freqs: self.freqs.clone(),
                fundamental: !self.harmonic,
                harmonic: self.harmonic,
            }),
            ui: UiBlock {
                restore_max_window: true,
                restore_analyzer_window: self.analyzer_state.is_some(),
                restore_type_ii_window: self.type_ii_state.is_some(),
                ..UiBlock::default()
            },
            analyzer,
            type_ii,
        }
    }

    fn emit_session_changed(&mut self) {
        self.events.push(WindowEvent::SessionChanged(self.session_state()));
    }

    /// `_redraw_points`: a fresh axes with the points and `title`.
    fn redraw_points(&mut self, title: &str) {
        self.title = title.into();
        self.plot_title = title.into();
        self.seed_drawn = false;
        self.figure_due = true;
    }

    fn figure(&self) -> Figure {
        let seed = if self.seed_drawn { self.seed } else { None };
        let mut fig = max_intensity_figure(&self.time_seconds, &self.freqs, &self.title, "Time (s)", seed, 978.0, 564.0);
        if let Some(verts) = self.lasso.as_ref().and_then(|l| l.verts.as_ref()) {
            // The lasso's line: C0, `lines.linewidth`, over the points.
            let (x, y): (Vec<f64>, Vec<f64>) = verts.iter().copied().unzip();
            fig.axes[0].lines.push(Line2D {
                x: Arc::new(x),
                y: Arc::new(y),
                color: TAB_BLUE,
                width_pt: Some(2.0),
                marker: None,
                label: None,
                zorder: 2.0,
                dash: None,
            });
        }
        fig
    }

    /// `activate_lasso`.
    pub fn activate_lasso(&mut self) {
        self.pick_start = false;
        self.title = "Draw around outliers to remove".into();
        self.figure_due = true;
        self.lasso = Some(Lasso::default());
    }

    /// `on_lasso_select`.
    fn on_lasso_select(&mut self, verts: &[(f64, f64)]) {
        let points: Vec<(f64, f64)> = self.time_seconds.iter().copied().zip(self.freqs.iter().copied()).collect();
        self.selected = contains_points(verts, &points);
        self.lasso = None;
        self.figure_due = true;
        let n = self.selected.iter().filter(|s| **s).count();
        self.status.show(format!("{n} points selected"), 3000);
    }

    /// `remove_selected_outliers`.
    pub fn remove_selected_outliers(&mut self) {
        if !self.selected.iter().any(|s| *s) {
            self.status.show("No points selected for removal", 3000);
            return;
        }
        let keep = |v: &Vec<f64>| v.iter().zip(&self.selected).filter(|(_, s)| !**s).map(|(x, _)| *x).collect::<Vec<_>>();
        self.time_channels = keep(&self.time_channels);
        self.time_seconds = keep(&self.time_seconds);
        self.freqs = keep(&self.freqs);
        self.selected = vec![false; self.time_channels.len()];
        self.analyzer_state = None;
        self.redraw_points("Filtered Max Intensities");
        self.status.show("Selected outliers removed", 3000);
        self.emit_session_changed();
    }

    /// `_ridge_seed_index`: the picked start as (frequency row, time column).
    fn ridge_seed_index(&self) -> Option<(usize, usize)> {
        let (seed, src) = (self.seed?, self.ridge_source.as_ref()?);
        let nearest = |values: &[f64], v: f64| {
            values
                .iter()
                .enumerate()
                .filter(|(_, x)| !x.is_nan())
                .min_by(|a, b| (a.1 - v).abs().total_cmp(&(b.1 - v).abs()))
                .map(|(i, _)| i)
        };
        Some((nearest(&src.freqs, seed.1)?, nearest(&src.time, seed.0)?))
    }

    /// `track_ridge`.
    pub fn track_ridge(&mut self) -> bool {
        let Some(src) = self.ridge_source.clone() else {
            self.status.show("Ridge tracking needs the dynamic spectrum; reopen this window from the main plot.", 5000);
            return false;
        };
        let result = match track_ridge(&src.data, &src.freqs, self.ridge_seed_index(), &self.ridge_settings) {
            Ok(r) => r,
            Err(e) => {
                self.notice = Some(Notice { kind: NoticeKind::Warning, title: "Track Ridge".into(), text: e });
                return false;
            }
        };
        if self.pre_ridge.is_none() {
            self.pre_ridge = Some((self.time_channels.clone(), self.time_seconds.clone(), self.freqs.clone()));
        }
        self.time_channels = result.time_indices.iter().map(|&i| i as f64).collect();
        self.time_seconds = result.time_indices.iter().map(|&i| src.time[i]).collect();
        self.freqs = result.freqs_mhz.clone();
        self.selected = vec![false; self.time_channels.len()];
        self.analyzer_state = None;
        self.redraw_points("Tracked Burst Ridge");
        self.seed_drawn = self.seed.is_some();
        let (first, last) = (self.time_seconds.first().copied().unwrap_or(0.0), self.time_seconds.last().copied().unwrap_or(0.0));
        let mut message = format!("Tracked {} points from t = {first:.2} s to {last:.2} s.", result.time_indices.len());
        if !result.warnings.is_empty() {
            message.push(' ');
            message.push_str(&result.warnings.join(" "));
        }
        self.status.show(message, 6000);
        self.emit_session_changed();
        true
    }

    /// `restore_per_column_maxima`.
    pub fn restore_per_column_maxima(&mut self) {
        let Some((c, t, f)) = self.pre_ridge.take() else { return };
        self.time_channels = c;
        self.time_seconds = t;
        self.freqs = f;
        self.selected = vec![false; self.time_channels.len()];
        self.analyzer_state = None;
        self.redraw_points(MAX_TITLE);
        self.status.show("Restored the per-column maxima.", 3000);
        self.emit_session_changed();
    }

    /// `reset_all`.
    pub fn reset_all(&mut self) {
        self.selected = vec![false; self.time_channels.len()];
        self.analyzer_state = None;
        self.seed = None;
        self.pick_start = false;
        self.redraw_points(MAX_TITLE);
        self.status.show("Reset selections.", 3000);
        self.emit_session_changed();
    }

    /// `edit_ridge_settings`: opens the settings dialog (modal).
    pub fn edit_ridge_settings(&mut self) {
        self.ridge_dialog = Some(RidgeSettingsDialog::new(&self.ridge_settings));
    }

    /// `save_as_csv`.
    fn save_as_csv(&mut self) {
        let Some(path) = rfd::FileDialog::new().set_title("Save CSV File").add_filter("CSV files (*.csv)", &["csv"]).save_file() else {
            return;
        };
        let mut out = String::from("Time Channel,Time (s),Frequency (MHz)\r\n");
        for ((c, t), f) in self.time_channels.iter().zip(&self.time_seconds).zip(&self.freqs) {
            out.push_str(&format!("{},{},{}\r\n", py_repr(*c), py_repr(*t), py_repr(*f)));
        }
        match std::fs::write(&path, out) {
            Ok(()) => self.status.show(format!("Saved to {}", path.display()), 3000),
            Err(e) => self.status.show(format!("Error: {e}"), 3000),
        }
    }

    /// `origin_figure`: the points as an OriginPro graph, for Export.
    fn origin_figure(&self) -> Figure {
        let g = FitGraph {
            x: &self.time_seconds,
            y: &self.freqs,
            fit_x: &[],
            fit_y: &[],
            data_label: "",
            fit_label: "",
            title: &self.plot_title,
            x_label: "Time (s)",
            y_label: "Frequency (MHz)",
        };
        fit_figure(&g, GraphText::default(), 640.0, 480.0)
    }

    /// `export_figure`.
    fn export_figure(&mut self, ctx: &egui::Context) {
        if self.filename.is_empty() {
            self.notice = Some(Notice { kind: NoticeKind::Warning, title: "No File Loaded".into(), text: "Load a FITS file before exporting.".into() });
            return;
        }
        let base = self.filename.split('.').next().unwrap_or("").to_owned();
        let Some(path) = export_dialog(&format!("{base}_MaxIntensityPlot")) else { return };
        self.notice = Some(match crate::figure_export::save_figure_padded(ctx, &self.origin_figure(), &path, 300.0, 0.08) {
            Ok(()) => Notice { kind: NoticeKind::Information, title: "Export Complete".into(), text: format!("Figure saved:\n{}", path.display()) },
            Err(e) => Notice { kind: NoticeKind::Critical, title: "Export Failed".into(), text: format!("An error occurred:\n{e}") },
        });
    }

    fn about(&mut self) {
        self.notice = Some(Notice {
            kind: NoticeKind::Information,
            title: format!("About {}", crate::APP_NAME),
            text: format!(
                "{} version {}.\n\nDeveloped by Sahan S Liyanage\n\nAstronomical and Space Science Unit\nUniversity of Colombo, Sri Lanka\n\n2026\u{a9}Copyright, All Rights Reserved.",
                crate::APP_NAME,
                crate::APP_VERSION
            ),
        });
    }

    /// `open_analyze_window`: the main window opens (or focuses) the Analyzer.
    pub fn open_analyze_window(&mut self) {
        self.events.push(WindowEvent::OpenAnalyzer(self.session_state()));
    }

    fn layout(ui: &Ui) -> Layout {
        let radio_spec = |id: &'static str, text: &str| {
            let w = widgets::text_width(ui, text, qt::FONT_PX, Weight::Regular).ceil() as i32 + 24;
            WidgetSpec::new(id, Size::new(w, 17), Policy::Minimum, Policy::Fixed)
        };
        let row = BoxLayout::h([0; 4], 6)
            .widget(qt::button_spec(ui, "select", "Select Outliers", false).min_width(150))
            .widget(qt::button_spec(ui, "remove", "Remove Outliers", false).min_width(150))
            .widget(qt::button_spec(ui, "track", "Track Ridge", false))
            .widget(qt::button_spec(ui, "pick", "Pick Start", false))
            .widget(radio_spec("fundamental", "Fundamental"))
            .widget(radio_spec("harmonic", "Harmonic"))
            .widget(qt::button_spec(ui, "analyze", "Analyze Burst", false).min_width(150))
            .stretch(1);
        BoxLayout::v([11; 4], 6)
            .add(row.into_item())
            .widget(WidgetSpec::new("canvas", Size::new(1000, 600), Policy::Expanding, Policy::Expanding).min_hint(Size::new(10, 10)))
            .widget(WidgetSpec::new("status", Size::new(20, 22), Policy::Preferred, Policy::Preferred))
            .into()
    }

    fn geometry(ui: &Ui, r: Rect) -> Geometry {
        let mut g = Geometry::default();
        let top = menubar::HEIGHT as i32;
        Self::layout(ui).set_geometry(IRect::new(0, top, r.width().round() as i32, r.height().round() as i32 - top), &mut g);
        g
    }

    fn menus(&self) -> Vec<crate::menus::Menu> {
        use WindowAction as A;
        vec![
            menu("File", vec![("Save As", A::SaveAs, true), ("Export As", A::ExportAs, true)]),
            menu("Edit", vec![("Reset All", A::ResetAll, true), ("Restore Per-Column Maxima", A::RestoreMaxima, self.pre_ridge.is_some())]),
            menu("Analyze", vec![("Open Analyzer", A::OpenAnalyzer, true), ("Ridge Tracking Settings...", A::RidgeSettings, true)]),
            menu("About", vec![("About", A::About, true)]),
        ]
    }

    /// The canvas's mouse: the lasso, or the ridge's start.
    fn canvas_input(&mut self, ui: &Ui, canvas: Rect) {
        let Some(axes) = self.view.axes_rect(0) else { return };
        let events = ui.input(|i| i.events.clone());
        let pointer_over = |p: Pos2| canvas.contains(p) && ui.ctx().layer_id_at(p).is_none_or(|l| l == ui.layer_id());
        for e in events {
            match e {
                egui::Event::PointerButton { pos, pressed: true, .. } if pointer_over(pos) && axes.contains(pos) => {
                    let Some(d) = self.view.data_at(pos, 0) else { continue };
                    if self.pick_start {
                        // _on_ridge_seed_click.
                        self.seed = Some(d);
                        self.pick_start = false;
                        self.seed_drawn = true;
                        self.figure_due = true;
                        self.status.show(
                            format!("Tracking will start near t = {:.2} s, f = {:.2} MHz. Press Track Ridge.", d.0, d.1),
                            6000,
                        );
                    } else if let Some(l) = &mut self.lasso {
                        l.verts = Some(vec![d]);
                        self.figure_due = true;
                    }
                }
                egui::Event::PointerMoved(pos) => {
                    if let Some(verts) = self.lasso.as_mut().and_then(|l| l.verts.as_mut()) {
                        // Outside the axes the last point repeats (`_clean_event`).
                        let d = if axes.contains(pos) { self.view.data_at(pos, 0) } else { verts.last().copied() };
                        if let Some(d) = d {
                            verts.push(d);
                            self.figure_due = true;
                        }
                    }
                }
                egui::Event::PointerButton { pos, pressed: false, .. } => {
                    let Some(mut verts) = self.lasso.as_mut().and_then(|l| l.verts.take()) else { continue };
                    let d = if axes.contains(pos) { self.view.data_at(pos, 0) } else { verts.last().copied() };
                    verts.extend(d);
                    self.on_lasso_select(&verts);
                }
                _ => {}
            }
        }
    }

    /// The window's contents in `ui` (its own window, or a test harness).
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Vec<WindowEvent> {
        qt::with_app_style(|| self.contents(ui, p));
        std::mem::take(&mut self.events)
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) {
        let full = ui.max_rect();
        let g = Self::geometry(ui, full);
        let rect = |id: &str| g.rect(id).to_egui(full.min);
        let enabled_ridge = self.ridge_source.is_some();
        if qt::button(ui, rect("select"), Id::new("max_select"), "Select Outliers", false, true, p).clicked() {
            self.activate_lasso();
        }
        if qt::button(ui, rect("remove"), Id::new("max_remove"), "Remove Outliers", false, true, p).clicked() {
            self.remove_selected_outliers();
        }
        if qt::button(ui, rect("track"), Id::new("max_track"), "Track Ridge", false, enabled_ridge, p).clicked() {
            self.track_ridge();
        }
        if toggle_button(ui, rect("pick"), Id::new("max_pick"), "Pick Start", self.pick_start, enabled_ridge, p) {
            // _on_ridge_start_toggled.
            self.pick_start = !self.pick_start;
            if self.pick_start {
                self.lasso = None;
                self.status.show("Click a point on the burst to start tracking from there.", 5000);
            }
        }
        if radio(ui, rect("fundamental"), Id::new("max_fundamental"), "Fundamental", !self.harmonic, true, p) {
            self.harmonic = false;
            self.emit_session_changed();
        }
        if radio(ui, rect("harmonic"), Id::new("max_harmonic"), "Harmonic", self.harmonic, true, p) {
            self.harmonic = true;
            self.emit_session_changed();
        }
        if qt::button(ui, rect("analyze"), Id::new("max_analyze"), "Analyze Burst", false, true, p).clicked() {
            self.open_analyze_window();
        }

        let canvas = rect("canvas");
        let resp = ui.interact(canvas, Id::new("max_canvas"), Sense::click_and_drag());
        let _ = resp;
        self.canvas_input(ui, canvas);
        if self.figure_due {
            self.view.set(self.figure());
            self.figure_due = false;
        }
        ui.painter().rect_filled(canvas, 0.0, egui::Color32::WHITE);
        self.view.show(ui, canvas);
        let message = self.status.current(ui.ctx()).to_owned();
        status_bar(ui, rect("status"), &message, p);

        // The menu bar last, so its popups sit over the window.
        let menus = self.menus();
        for action in window_menubar(ui, full.width(), &menus, &mut self.menubar, p) {
            match action {
                WindowAction::SaveAs => self.save_as_csv(),
                WindowAction::ExportAs => self.export_figure(ui.ctx()),
                WindowAction::ResetAll => self.reset_all(),
                WindowAction::RestoreMaxima => self.restore_per_column_maxima(),
                WindowAction::OpenAnalyzer => self.open_analyze_window(),
                WindowAction::RidgeSettings => self.edit_ridge_settings(),
                WindowAction::About => self.about(),
            }
        }

        if let Some(dialog) = &mut self.ridge_dialog {
            crate::downloads::block_input(ui, Id::new("max_modal"));
            if let Some(result) = dialog.show(ui.ctx(), p) {
                if let Some(s) = result {
                    self.ridge_settings = s;
                }
                self.ridge_dialog = None;
            }
        }
        if let Some(n) = &self.notice
            && crate::ui::dialogs::message_box(ui.ctx(), n, p)
        {
            self.notice = None;
        }
    }

    /// Shows the window; returns its events and whether it closed (with
    /// its last state, `closeEvent` emitting `sessionChanged`).
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> (Vec<WindowEvent>, bool) {
        let size = crate::downloads::fit_to_screen(ctx, 1000.0, 700.0);
        let mut events = Vec::new();
        let mut closed = false;
        crate::downloads::window(ctx, "max_intensity_window", TITLE, size, vec2(400.0, 300.0), p, |ui, close| {
            if std::mem::take(&mut self.focus_due) {
                ui.ctx().send_viewport_cmd(egui::ViewportCommand::Focus);
            }
            events = self.frame(ui, p);
            if close {
                closed = true;
            }
        });
        if closed {
            events.push(WindowEvent::SessionChanged(self.session_state()));
        }
        (events, closed)
    }
}

/// `pick_export_path` with `FIGURE_EXPORT_FILTERS`, PNG by default.
pub(crate) fn export_dialog(default_name: &str) -> Option<PathBuf> {
    let mut path = rfd::FileDialog::new()
        .set_title("Export Figure")
        .set_file_name(default_name)
        .add_filter("PNG (*.png)", &["png"])
        .add_filter("PDF (*.pdf)", &["pdf"])
        .add_filter("EPS (*.eps)", &["eps"])
        .add_filter("SVG (*.svg)", &["svg"])
        .add_filter("TIFF (*.tiff *.tif)", &["tiff", "tif"])
        .add_filter("JPG (*.jpg *.jpeg)", &["jpg", "jpeg"])
        .save_file()?;
    if path.extension().is_none() {
        path.set_extension("png");
    }
    Some(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_like_python_repr() {
        assert_eq!(py_repr(262.25), "262.25");
        assert_eq!(py_repr(3.0), "3.0");
        assert_eq!(py_repr(1e-5), "1e-05");
        assert_eq!(py_repr(45.0625), "45.0625");
    }
}
