//! The Analyzer (`AnalyzeDialog`): the maximum-intensity points and their
//! power-law best fit `f = a (t − t₀)^−b` (OriginPro-style), the fit's
//! metrics, the shock parameters under the chosen density model and fold
//! number with every model side by side, the shock plots, Save Graph and
//! Save Data (an Excel row, to a new or an existing workbook).

use std::collections::BTreeMap;

use callisto_core::analysis::analyzer::{
    AnalyzerFit, ShockSummary, ShockUpdate, T0Mode, analysis_time_axis, analyzer_fit, burst_onset_t0, resolve_time_seconds,
    update_shock_parameters,
};
use callisto_core::analysis::density::{DensityModel, ShockParameters};
use callisto_core::analysis::session::{AnalysisSession, AnalyzerBlock, FitParams, MaxIntensityBlock, SessionSource, TypeIIBlock, UiBlock};
use callisto_core::mpl::Figure;
use callisto_core::mpl::analysis_figures::{
    BLUE, FitGraph, GREEN, GraphText, PURPLE, RED, analyzer_scatter_figure, empty_axes_figure, fit_figure, power_law_label,
};
use egui::{Color32, Id, Rect, Sense, Ui, UiBuilder, pos2, vec2};

use super::max_window::export_dialog;
use super::{StatusLine, WindowEvent, rich, status_bar};
use crate::figure_view::FigureView;
use crate::fonts::{Weight, ui_font};
use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;
use crate::ui::qlayout::{BoxLayout, Geometry, IRect, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

pub const TITLE: &str = "Analyzer";

/// `_COMPARISON_COLUMNS`: (header, digits).
const COLUMNS: [(&str, usize); 4] = [("v\u{2080} (km/s)", 1), ("h\u{2080} (R\u{2609})", 3), ("v\u{304} (km/s)", 1), ("h\u{304} (R\u{2609})", 3)];

const EXTRA_PLOTS: [&str; 3] = ["Shock Speed vs Shock Height", "Shock Speed vs Frequency", "Shock Height vs Frequency"];

/// What the canvas shows, as `fit_graph_figure` arguments (Save Graph).
#[derive(Clone, Debug)]
struct Graph {
    x: Vec<f64>,
    y: Vec<f64>,
    fit_x: Vec<f64>,
    fit_y: Vec<f64>,
    data_label: String,
    fit_label: String,
    title: String,
    x_label: String,
    y_label: String,
}

/// The canvas's graph.
#[derive(Clone, Debug)]
enum Canvas {
    Empty,
    Scatter { x: Vec<f64>, y: Vec<f64>, color: [u8; 4], title: String, x_label: String, y_label: String },
    Fit,
}

pub struct AnalyzerWindow {
    filename: String,
    source: SessionSource,
    time_channels: Vec<f64>,
    time_seconds: Vec<f64>,
    time: Vec<f64>,
    freq: Vec<f64>,
    fundamental: bool,
    harmonic: bool,
    fold: usize,
    model: DensityModel,
    t0_mode: T0Mode,
    custom_t0: f64,
    custom_t0_initialized: bool,
    ut_start_sec: Option<f64>,
    fit: Option<AnalyzerFit>,
    fit_params: Option<FitParams>,
    shock: Option<ShockUpdate>,
    summary: ShockSummary,
    comparison: BTreeMap<DensityModel, ShockParameters>,
    shock_header: String,
    summary_shown: bool,
    equation: String,
    r2_text: String,
    rmse_text: String,
    canvas: Canvas,
    /// The axes the best fit was drawn on (`origin_graph_style`) stays.
    origin_axes: bool,
    graph: Option<Graph>,
    current_plot_title: String,
    extra_choice: usize,
    existing_excel: bool,
    type_ii_state: Option<TypeIIBlock>,
    status: StatusLine,
    view: FigureView,
    figure_due: bool,
    scroll: f32,
    notice: Option<Notice>,
    events: Vec<WindowEvent>,
    focus_due: bool,
    suppress_emit: bool,
}

/// `_f` in `_set_summary_labels_from_dict`: blank when unknown, a dash
/// outside the model's range.
fn fmt_value(v: Option<f64>, digits: usize) -> String {
    match v {
        None => String::new(),
        Some(v) if v.is_finite() => format!("{v:.digits$}"),
        Some(_) => "\u{2014}".into(),
    }
}

impl AnalyzerWindow {
    /// `AnalyzeDialog(time_channels, freqs, filename, …, session=…)`.
    pub fn new(session: &AnalysisSession, filename: &str) -> Self {
        let m = session.max_intensity.clone().unwrap_or(MaxIntensityBlock {
            time_channels: Vec::new(),
            time_seconds: None,
            freqs: Vec::new(),
            fundamental: true,
            harmonic: false,
        });
        let time_seconds = resolve_time_seconds(&m.time_channels, m.time_seconds.as_deref());
        let stem = filename.split('.').next().unwrap_or("").to_owned();
        let mut w = AnalyzerWindow {
            current_plot_title: format!("{stem}_Best_Fit"),
            filename: stem,
            source: session.source.clone(),
            time: analysis_time_axis(&time_seconds),
            time_seconds,
            time_channels: m.time_channels,
            freq: m.freqs,
            fundamental: m.fundamental,
            harmonic: m.harmonic,
            fold: 1,
            model: DensityModel::Newkirk,
            t0_mode: T0Mode::FileStart,
            custom_t0: 0.0,
            custom_t0_initialized: false,
            ut_start_sec: None,
            fit: None,
            fit_params: None,
            shock: None,
            summary: ShockSummary::default(),
            comparison: BTreeMap::new(),
            shock_header: "<b>Shock Parameters:</b>".into(),
            summary_shown: false,
            equation: String::new(),
            r2_text: "R\u{b2} = ".into(),
            rmse_text: "RMSE = ".into(),
            canvas: Canvas::Empty,
            origin_axes: false,
            graph: None,
            extra_choice: 0,
            existing_excel: false,
            type_ii_state: None,
            status: StatusLine::default(),
            view: FigureView::default(),
            figure_due: true,
            scroll: 0.0,
            notice: None,
            events: Vec::new(),
            focus_due: true,
            suppress_emit: false,
        };
        w.status.show("Ready", 0);
        w.restore_session(session);
        w
    }

    pub fn focus(&mut self) {
        self.focus_due = true;
    }

    /// `set_ut_start_sec`: a custom t₀ may then be entered in UT.
    pub fn set_ut_start_sec(&mut self, ut: Option<f64>) {
        self.ut_start_sec = ut.filter(|v| v.is_finite());
    }

    // ------------------------------------------------------------ t0

    /// `_resolved_t0`.
    fn resolved_t0(&self) -> f64 {
        match self.t0_mode {
            T0Mode::FileStart => 0.0,
            T0Mode::BurstOnset => burst_onset_t0(&self.time, &self.freq),
            T0Mode::Custom => self.custom_t0,
        }
    }

    /// `_on_t0_mode_changed`.
    fn set_t0_mode(&mut self, mode: T0Mode) {
        self.t0_mode = mode;
        if mode == T0Mode::Custom && !self.custom_t0_initialized {
            self.custom_t0 = burst_onset_t0(&self.time, &self.freq);
            self.custom_t0_initialized = true;
        }
        self.refit_after_t0_change();
    }

    /// `_refit_after_t0_change`: a new t₀ changes the fit itself.
    fn refit_after_t0_change(&mut self) {
        let Some(fit) = &self.fit else {
            self.emit_session_changed();
            return;
        };
        if (self.resolved_t0() - fit.t0).abs() < 1e-9 {
            return;
        }
        self.plot_fit(None);
    }

    // ------------------------------------------------------------ session

    fn emit_session_changed(&mut self) {
        if !self.suppress_emit {
            self.events.push(WindowEvent::SessionChanged(self.session_state()));
        }
    }

    /// `session_state`.
    pub fn session_state(&self) -> AnalysisSession {
        let mut shock = self.summary.clone();
        if self.shock.is_some() || shock.has_values() {
            shock.fold = if shock.fold > 0 { shock.fold } else { self.fold as i64 };
        }
        AnalysisSession {
            source: SessionSource { filename: self.filename.clone(), ..self.source.clone() },
            max_intensity: Some(MaxIntensityBlock {
                time_channels: self.time_channels.clone(),
                time_seconds: Some(self.time_seconds.clone()),
                freqs: self.freq.clone(),
                fundamental: self.fundamental,
                harmonic: self.harmonic,
            }),
            analyzer: AnalyzerBlock {
                fit_params: self.fit_params,
                fold: self.fold as i64,
                shock_summary: shock,
                density_model: self.model,
                t0_mode: self.t0_mode,
                t0_s: self.fit.as_ref().map_or_else(|| self.resolved_t0(), |f| f.t0),
            },
            type_ii: self.type_ii_state.clone().unwrap_or_default(),
            ui: UiBlock {
                restore_max_window: true,
                restore_analyzer_window: true,
                restore_type_ii_window: self.type_ii_state.is_some(),
                ..UiBlock::default()
            },
        }
    }

    /// `restore_session`.
    pub fn restore_session(&mut self, s: &AnalysisSession) {
        self.suppress_emit = true;
        if let Some(m) = &s.max_intensity {
            if m.time_channels.len() == m.freqs.len() && !m.time_channels.is_empty() {
                self.time_channels = m.time_channels.clone();
                self.time_seconds = resolve_time_seconds(&self.time_channels, m.time_seconds.as_deref());
                self.time = analysis_time_axis(&self.time_seconds);
                self.freq = m.freqs.clone();
            }
            self.fundamental = m.fundamental;
            self.harmonic = m.harmonic;
        }
        let a = &s.analyzer;
        self.fold = a.fold.clamp(1, 4) as usize;
        self.model = a.density_model;
        if a.t0_mode == T0Mode::Custom {
            self.custom_t0 = if a.t0_s.is_finite() { a.t0_s } else { 0.0 };
            self.custom_t0_initialized = true;
        }
        self.t0_mode = a.t0_mode;
        if let Some(fit) = a.fit_params {
            let std = match fit.std_errs {
                [Some(x), Some(y)] => Some([x, y]),
                _ => None,
            };
            self.plot_fit(Some(([fit.a, fit.b], std)));
        } else {
            self.summary = a.shock_summary.clone();
            self.set_summary_labels();
        }
        self.type_ii_state = s.type_ii.has_content().then(|| s.type_ii.clone());
        self.suppress_emit = false;
    }

    // ------------------------------------------------------------ plots

    /// `plot_max`.
    pub fn plot_max(&mut self) {
        let title = format!("{}_Maximum_Intensity", self.filename);
        self.canvas = Canvas::Scatter {
            x: self.time.clone(),
            y: self.freq.clone(),
            color: BLUE,
            title: title.clone(),
            x_label: "Time (s)".into(),
            y_label: "Frequency (MHz)".into(),
        };
        self.figure_due = true;
        self.current_plot_title = title.clone();
        self.graph = Some(Graph {
            x: self.time.clone(),
            y: self.freq.clone(),
            fit_x: Vec::new(),
            fit_y: Vec::new(),
            data_label: String::new(),
            fit_label: String::new(),
            title,
            x_label: "Time (s)".into(),
            y_label: "Frequency (MHz)".into(),
        });
        self.equation.clear();
        self.status.show("Max intensities plotted successfully!", 3000);
    }

    /// `plot_fit`: fit (or reuse `params`), draw the OriginPro graph, the
    /// metrics and the shock parameters.
    pub fn plot_fit(&mut self, params: Option<([f64; 2], Option<[f64; 2]>)>) {
        let t0 = self.resolved_t0();
        let fit = match analyzer_fit(&self.time, &self.freq, t0, params) {
            Ok(f) => f,
            Err(e) => {
                self.notice = Some(Notice { kind: NoticeKind::Warning, title: "Analyzer".into(), text: e });
                self.status.show("Best fit failed: insufficient positive time samples.", 3000);
                return;
            }
        };
        self.current_plot_title = format!("{}_Best_Fit", self.filename);
        let variable = if t0 == 0.0 { "x".to_owned() } else { format!("(x \u{2212} {t0:.2})") };
        self.graph = Some(Graph {
            x: self.time.clone(),
            y: self.freq.clone(),
            fit_x: fit.time_fit.iter().map(|t| t + t0).collect(),
            fit_y: fit.freq_fit.clone(),
            data_label: "Original Data".into(),
            fit_label: power_law_label(fit.a, fit.b, "Best Fit", &variable),
            title: self.current_plot_title.clone(),
            x_label: "Time (s)".into(),
            y_label: "Frequency (MHz)".into(),
        });
        self.canvas = Canvas::Fit;
        self.origin_axes = true;
        self.figure_due = true;
        self.equation = if t0 == 0.0 {
            format!("<b>f(x) = {:.2} \u{b7} x<sup>-{:.2}</sup></b>", fit.a, fit.b)
        } else {
            format!("<b>f(x) = {:.2} \u{b7} (x \u{2212} {t0:.2})<sup>-{:.2}</sup></b>", fit.a, fit.b)
        };
        self.r2_text = format!("R\u{b2} = {:.4}", fit.r2);
        self.rmse_text = format!("RMSE = {:.4}", fit.rmse);
        self.fit_params = Some(FitParams {
            a: fit.a,
            b: fit.b,
            std_errs: [Some(fit.std_errs[0]), Some(fit.std_errs[1])],
            r2: Some(fit.r2),
            rmse: Some(fit.rmse),
        });
        self.fit = Some(fit);
        self.update_shock_parameters(self.fold);
        self.status.show("Best fit plotted successfully!", 3000);
        self.emit_session_changed();
    }

    /// `_update_shock_parameters`.
    fn update_shock_parameters(&mut self, n: usize) {
        let Some(fit) = &self.fit else { return };
        let u = update_shock_parameters(fit, &self.freq, self.fundamental, self.harmonic, n as i64, self.model);
        self.summary = u.summary.clone();
        self.comparison = u.comparison.clone();
        self.shock = Some(u);
        self.set_summary_labels();
    }

    /// `recalculate_shock_parameters` (Calculate).
    fn recalculate_shock_parameters(&mut self) {
        if self.fit.is_none() {
            self.notice = Some(Notice { kind: NoticeKind::Information, title: "Analyzer".into(), text: "Please click 'Best Fit' first.".into() });
            return;
        }
        self.update_shock_parameters(self.fold);
        self.status.show(format!("Updated using {} {}-fold model.", self.model.label(), self.fold), 3000);
        self.emit_session_changed();
    }

    /// `_on_density_model_changed`.
    fn density_model_changed(&mut self) {
        if self.fit.is_some() {
            self.update_shock_parameters(self.fold);
            self.status.show(format!("Updated using the {} density model.", self.model.label()), 3000);
        }
        self.emit_session_changed();
    }

    /// `_set_summary_labels_from_dict`: the header names the model and fold.
    fn set_summary_labels(&mut self) {
        let fold = if self.summary.fold > 0 { self.summary.fold } else { self.fold as i64 };
        let model = self.summary.density_model.unwrap_or(self.model);
        self.shock_header = format!("<b>Shock Parameters ({} {fold}-fold):</b>", model.label());
        self.summary_shown = true;
    }

    /// The shock parameter labels' texts, in order.
    fn summary_lines(&self) -> Vec<String> {
        if !self.summary_shown {
            return vec![String::new(); 7];
        }
        let s = &self.summary;
        let pair = |label: &str, v: Option<f64>, e: Option<f64>, d: usize, unit: &str| {
            format!("{label}: <b>{} \u{b1} {}</b> {unit}", fmt_value(v, d), fmt_value(e, d))
        };
        vec![
            pair("Average Frequency", s.avg_freq_mhz, s.avg_freq_err_mhz, 2, "MHz"),
            pair("Average Drift Rate", s.avg_drift_mhz_s, s.avg_drift_err_mhz_s, 4, "MHz/s"),
            pair("Starting Frequency", s.start_freq_mhz, s.start_freq_err_mhz, 2, "MHz"),
            pair("Initial Shock Speed", s.initial_shock_speed_km_s, s.initial_shock_speed_err_km_s, 2, "km/s"),
            pair("Initial Shock Height", s.initial_shock_height_rs, s.initial_shock_height_err_rs, 3, "R\u{209b}"),
            pair("Average Shock Speed", s.avg_shock_speed_km_s, s.avg_shock_speed_err_km_s, 2, "km/s"),
            pair("Average Shock Height", s.avg_shock_height_rs, s.avg_shock_height_err_rs, 3, "R\u{209b}"),
        ]
    }

    /// The extra plot `choice` (`extra_plot_combo.setCurrentIndex`, then `plot_extra`).
    pub fn plot_extra_choice(&mut self, choice: usize) {
        self.extra_choice = choice.min(EXTRA_PLOTS.len() - 1);
        self.plot_extra();
    }

    /// `plot_extra`.
    fn plot_extra(&mut self) {
        let Some(u) = &self.shock else { return };
        let freq_axis = if !u.shock_freq_values.is_empty() {
            u.shock_freq_values.clone()
        } else {
            self.fit.as_ref().map_or_else(|| self.freq.clone(), |f| f.fit_freq.clone())
        };
        let (x, y, x_label, y_label, suffix, color) = match self.extra_choice {
            0 => (u.shock_height.clone(), u.shock_speed.clone(), "Shock Height (R\u{209b})", "Shock Speed (km/s)", "Shock_Speed_vs_Shock_Height", GREEN),
            1 => (freq_axis, u.shock_speed.clone(), "Frequency (MHz)", "Shock Speed (km/s)", "Shock_Speed_vs_Frequency", PURPLE),
            _ => (u.shock_height.clone(), freq_axis, "Shock Height (R\u{209b})", "Frequency (MHz)", "Rs_vs_Freq", RED),
        };
        let title = format!("{}_{suffix}", self.filename);
        self.current_plot_title = title.clone();
        self.graph = Some(Graph {
            x: x.clone(),
            y: y.clone(),
            fit_x: Vec::new(),
            fit_y: Vec::new(),
            data_label: String::new(),
            fit_label: String::new(),
            title: title.clone(),
            x_label: x_label.into(),
            y_label: y_label.into(),
        });
        self.canvas = Canvas::Scatter { x, y, color, title, x_label: x_label.into(), y_label: y_label.into() };
        self.figure_due = true;
        self.status.show(format!("{} plotted successfully!", EXTRA_PLOTS[self.extra_choice]), 3000);
    }

    fn figure(&self) -> Figure {
        let (w, h) = (772.0, 598.0);
        match &self.canvas {
            Canvas::Empty => empty_axes_figure(w, h),
            Canvas::Scatter { x, y, color, title, x_label, y_label } => {
                analyzer_scatter_figure(x, y, *color, title, x_label, y_label, self.origin_axes, w, h)
            }
            Canvas::Fit => match &self.graph {
                Some(g) => fit_figure(&self.fit_graph(g), GraphText::default(), w, h),
                None => empty_axes_figure(w, h),
            },
        }
    }

    fn fit_graph<'a>(&self, g: &'a Graph) -> FitGraph<'a> {
        FitGraph {
            x: &g.x,
            y: &g.y,
            fit_x: &g.fit_x,
            fit_y: &g.fit_y,
            data_label: &g.data_label,
            fit_label: &g.fit_label,
            title: &g.title,
            x_label: &g.x_label,
            y_label: &g.y_label,
        }
    }

    // ------------------------------------------------------------ saving

    /// `save_graph`: the graph on show as an OriginPro figure.
    fn save_graph(&mut self, ctx: &egui::Context) {
        let Some(g) = self.graph.clone() else {
            self.notice = Some(Notice {
                kind: NoticeKind::Information,
                title: "Save Graph".into(),
                text: "Plot the maximum intensities or the best fit first.".into(),
            });
            return;
        };
        let Some(path) = export_dialog(&self.current_plot_title) else { return };
        let fig = fit_figure(&self.fit_graph(&g), GraphText::default(), 640.0, 480.0);
        match crate::figure_export::save_figure_padded(ctx, &fig, &path, 300.0, 0.08) {
            Ok(()) => {
                self.notice = Some(Notice { kind: NoticeKind::Information, title: "Export Complete".into(), text: format!("Plot saved:\n{}", path.display()) });
                self.status.show("Export successful!", 3000);
            }
            Err(e) => {
                self.notice = Some(Notice { kind: NoticeKind::Critical, title: "Export Failed".into(), text: format!("Could not save file:\n{e}") });
                self.status.show("Export failed!", 3000);
            }
        }
    }

    /// The row Save Data writes (`save_data`), from the labels' texts.
    fn data_row(&self) -> Vec<callisto_core::xlsx::Cell> {
        use callisto_core::xlsx::Cell;
        let station = callisto_core::analysis::export::station_from_filename(&self.filename);
        let date = callisto_core::analysis::export::date_from_filename(&self.filename);
        let best_fit = rich::plain(&self.equation);
        let r2 = self.r2_text.rsplit('=').next().unwrap_or("").trim().to_owned();
        let rmse = self.rmse_text.rsplit('=').next().unwrap_or("").trim().to_owned();
        let lines = self.summary_lines();
        let vals: Vec<(String, String)> = lines.iter().map(|l| callisto_core::analysis::export::value_and_error(&rich::plain(l))).collect();
        let avg_drift_abs = vals[1].0.parse::<f64>().map(|v| Cell::Num(v.abs())).unwrap_or(Cell::Str(String::new()));
        let t0 = self.fit.as_ref().map_or(0.0, |f| f.t0);
        let mut row = vec![Cell::Str(date), Cell::Str(station), Cell::Str(best_fit), Cell::Str(r2), Cell::Str(rmse)];
        for (v, e) in &vals {
            row.push(Cell::Str(v.clone()));
            row.push(Cell::Str(e.clone()));
        }
        row.push(avg_drift_abs);
        row.push(Cell::Str(self.model.label().into()));
        row.push(Cell::Num(t0));
        row
    }

    /// `save_data`.
    fn save_data(&mut self) {
        use callisto_core::xlsx;
        let row = self.data_row();
        let result = if self.existing_excel {
            let Some(path) = rfd::FileDialog::new().set_title("Select Existing Excel File").add_filter("Excel Files (*.xlsx)", &["xlsx"]).pick_file() else {
                return;
            };
            xlsx::append_row(&path, &row).map_err(|e| ("Load Error", format!("Could not open Excel file:\n{e}")))
        } else {
            let Some(path) = rfd::FileDialog::new()
                .set_title("Save as Excel")
                .set_file_name(format!("{}_data.xlsx", self.filename))
                .add_filter("Excel Files (*.xlsx)", &["xlsx"])
                .save_file()
            else {
                return;
            };
            let headers: Vec<xlsx::Cell> = callisto_core::analysis::export::ANALYZER_HEADERS.iter().map(|h| xlsx::Cell::Str((*h).into())).collect();
            xlsx::write_rows(&path, &[headers, row]).map_err(|e| ("Write Error", format!("Could not write to Excel file:\n{e}")))
        };
        match result {
            Ok(()) => self.status.show("\u{2705} Data saved to Excel successfully!", 3000),
            Err((title, text)) => {
                self.notice = Some(Notice { kind: NoticeKind::Critical, title: title.into(), text });
                self.status.show("\u{274c} Failed to save data to Excel.", 3000);
            }
        }
    }

    // ------------------------------------------------------------ layout

    fn main_layout(ui: &Ui, custom: Option<bool>) -> Layout {
        let mut row = BoxLayout::h([0; 4], 6)
            .widget(qt::button_spec(ui, "max", "Maximum Intensities", false))
            .widget(qt::button_spec(ui, "fit", "Best Fit", false))
            .add(Item::Spacer { size: Size::new(12, 0), hpolicy: Policy::Fixed, vpolicy: Policy::Minimum })
            .widget(qt::label_spec(ui, "t0_label", "t\u{2080}:"))
            .widget(qt::combo_spec(ui, "t0", &T0Mode::ALL.map(|m| m.label())));
        match custom {
            Some(true) => row = row.widget(qt::spin_spec(ui, "t0_time", "00:00:00.000")),
            Some(false) => row = row.widget(qt::spin_spec(ui, "t0_seconds", "-86400.00 s")),
            None => {}
        }
        let left = BoxLayout::v([0; 4], 6)
            .add(row.stretch(1).into_item())
            .widget(WidgetSpec::new("canvas", Size::new(800, 500), Policy::Expanding, Policy::Expanding).min_hint(Size::new(10, 10)));
        let right = WidgetSpec::new("panel", Size::new(288, 384), Policy::Expanding, Policy::Expanding).min_width(300).min_hint(Size::new(56, 56));
        let main = BoxLayout::h([0; 4], 6).add_stretched(left.into_item(), 3).add_stretched(Item::Widget(right), 1);
        BoxLayout::v([11; 4], 6)
            .add(main.into_item())
            .widget(WidgetSpec::new("status", Size::new(20, 22), Policy::Preferred, Policy::Preferred))
            .into()
    }

    /// The scrolled panel's contents (`right_inner`), at the viewport's width.
    fn panel_layout(&self, ui: &Ui) -> Layout {
        let line = |id: &'static str| WidgetSpec::new(id, Size::new(10, 16), Policy::Preferred, Policy::Preferred);
        let model_row = BoxLayout::h([0; 4], 6)
            .widget(qt::label_spec(ui, "model_label", "Density model:"))
            .add_stretched(Item::Widget(qt::combo_spec(ui, "model", &DensityModel::ALL.map(|m| m.label()))), 1);
        let fold_row = BoxLayout::h([0; 4], 6)
            .widget(qt::label_spec(ui, "fold_label", "Fold-number:"))
            .widget(qt::combo_len_spec("fold", 2).min_width(107).max_width(107))
            .widget(qt::button_spec(ui, "calculate", "Calculate", false));
        let equation_h = if self.equation.is_empty() { 29 } else { 30 };
        let mut v = BoxLayout::v([9; 4], 6)
            .add(model_row.into_item())
            .add(fold_row.into_item())
            .widget(line("equation_label"))
            .widget(WidgetSpec::new("equation", Size::new(15, equation_h), Policy::Preferred, Policy::Preferred))
            .widget(line("stats_header"))
            .widget(line("r2"))
            .widget(line("rmse"))
            .widget(line("shock_header"));
        for id in ["s0", "s1", "s2", "s3", "s4", "s5", "s6"] {
            v = v.widget(line(id));
        }
        v.widget(line("comparison_header"))
            .widget(WidgetSpec::new("table", Size::new(256, 224), Policy::Expanding, Policy::Fixed).min_height(224))
            .widget(qt::button_spec(ui, "save_graph", "Save Graph", false))
            .widget(qt::button_spec(ui, "save_data", "Save Data", false))
            .widget(WidgetSpec::new("existing", Size::new(116, 17), Policy::Minimum, Policy::Fixed))
            .widget(line("extra_label"))
            .widget(qt::combo_spec(ui, "extra", &EXTRA_PLOTS))
            .widget(qt::button_spec(ui, "plot_extra", "Plot", false))
            .stretch(0)
            .stretch(0)
            .into()
    }

    // ------------------------------------------------------------ drawing

    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Vec<WindowEvent> {
        qt::with_app_style(|| self.contents(ui, p));
        std::mem::take(&mut self.events)
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) {
        let full = ui.max_rect();
        let custom = (self.t0_mode == T0Mode::Custom).then_some(self.ut_start_sec.is_some());
        let g = Self::main_layout(ui, custom).layout_window(Size::new(full.width().round() as i32, full.height().round() as i32));
        let rect = |id: &str| g.rect(id).to_egui(full.min);
        if qt::button(ui, rect("max"), Id::new("an_max"), "Maximum Intensities", false, true, p).clicked() {
            self.plot_max();
        }
        if qt::button(ui, rect("fit"), Id::new("an_fit"), "Best Fit", false, true, p).clicked() {
            self.plot_fit(None);
        }
        let l = rect("t0_label");
        widgets::text_left(ui, l.left(), l.center().y, "t\u{2080}:", qt::FONT_PX, Weight::Regular, p.text);
        let items: Vec<String> = T0Mode::ALL.iter().map(|m| m.label().to_owned()).collect();
        let current = T0Mode::ALL.iter().position(|m| *m == self.t0_mode);
        if let Some(i) = qt::combo(ui, rect("t0"), Id::new("an_t0"), &items, current, "", true, p) {
            self.set_t0_mode(T0Mode::ALL[i]);
        }
        match custom {
            Some(false) => {
                let mut v = self.custom_t0;
                if qt::double_spin_box(ui, rect("t0_seconds"), Id::new("an_t0_seconds"), &mut v, (-86400.0, 86400.0), 1.0, 2, " s", false, true, p) {
                    self.custom_t0 = v;
                    self.refit_after_t0_change();
                }
            }
            Some(true) => {
                let ut = self.ut_start_sec.unwrap_or(0.0);
                let ms = (((ut + self.custom_t0).rem_euclid(86400.0)) * 1000.0).round() as i64;
                let base = chrono::NaiveDate::from_ymd_opt(2000, 1, 1).unwrap().and_hms_opt(0, 0, 0).unwrap();
                let mut t = base + chrono::Duration::milliseconds(ms);
                if qt::datetime_edit(ui, rect("t0_time"), Id::new("an_t0_time"), &mut t, "HH:mm:ss", true, p) {
                    let secs = (t - base).num_milliseconds() as f64 / 1000.0;
                    let mut offset = secs - ut;
                    // A burst that runs past midnight UT.
                    if offset < -43200.0 {
                        offset += 86400.0;
                    } else if offset > 43200.0 {
                        offset -= 86400.0;
                    }
                    self.custom_t0 = offset;
                    self.refit_after_t0_change();
                }
            }
            None => {}
        }

        let canvas = rect("canvas");
        if self.figure_due {
            self.view.set(self.figure());
            self.figure_due = false;
        }
        ui.painter().rect_filled(canvas, 0.0, Color32::WHITE);
        self.view.show(ui, canvas);

        self.panel(ui, rect("panel"), p);

        let message = self.status.current(ui.ctx()).to_owned();
        status_bar(ui, rect("status"), &message, p);

        if let Some(n) = &self.notice
            && crate::ui::dialogs::message_box(ui.ctx(), n, p)
        {
            self.notice = None;
        }
    }

    /// The scroll area on the right: a framed viewport in the palette's
    /// window colour, its contents scrolled, the scroll bar beside it.
    fn panel(&mut self, ui: &mut Ui, area: Rect, p: &Palette) {
        let frame = darker(p.qt.window, 130);
        ui.painter().rect_filled(area, 0.0, frame);
        let viewport = Rect::from_min_max(area.min + vec2(1.0, 1.0), area.max - vec2(11.0, 1.0));
        ui.painter().rect_filled(viewport, 0.0, p.qt.window);
        // The scroll bar's column shows the window behind it.
        let bar = Rect::from_min_max(pos2(viewport.right(), viewport.top()), pos2(area.right() - 1.0, viewport.bottom()));
        ui.painter().rect_filled(bar, 0.0, p.page_bg);

        let width = viewport.width().round() as i32;
        let layout = self.panel_layout(ui);
        let content_h = layout.summary().hint.h.max(viewport.height().round() as i32);
        let max_scroll = (content_h as f32 - viewport.height()).max(0.0);
        // The wheel: three lines of 20 px a notch (`QScrollArea`).
        if ui.rect_contains_pointer(area) {
            let wheel: f32 = ui.input(|i| {
                i.events
                    .iter()
                    .filter_map(|e| match e {
                        egui::Event::MouseWheel { unit, delta, .. } => Some(match unit {
                            egui::MouseWheelUnit::Line => delta.y * 60.0,
                            egui::MouseWheelUnit::Page => delta.y * viewport.height(),
                            egui::MouseWheelUnit::Point => delta.y,
                        }),
                        _ => None,
                    })
                    .sum()
            });
            self.scroll = (self.scroll - wheel).clamp(0.0, max_scroll);
        }
        self.scroll = self.scroll.clamp(0.0, max_scroll);
        if max_scroll > 0.0 {
            // QScrollBar: 10 px, margin 4 0 4 0, a rounded handle.
            let groove = Rect::from_min_max(pos2(bar.left(), bar.top() + 4.0), pos2(bar.right(), bar.bottom() - 4.0));
            let len = (groove.height() * viewport.height() / content_h as f32).max(24.0);
            let top = groove.top() + (groove.height() - len) * self.scroll / max_scroll;
            let handle = Rect::from_min_size(pos2(groove.left(), top), vec2(groove.width(), len));
            let resp = ui.interact(handle, Id::new("an_scroll_handle"), Sense::drag());
            if resp.dragged() {
                let per_px = max_scroll / (groove.height() - len).max(1.0);
                self.scroll = (self.scroll + resp.drag_delta().y * per_px).clamp(0.0, max_scroll);
            }
            ui.painter().rect_filled(handle, 5.0, p.border);
        }

        let mut g = Geometry::default();
        let origin_y = (viewport.top() - self.scroll).round();
        layout.set_geometry(IRect::new(0, 0, width, content_h), &mut g);
        let o = pos2(viewport.left(), origin_y);
        let rect = |id: &str| g.rect(id).to_egui(o);
        let mut child = ui.new_child(UiBuilder::new().max_rect(viewport));
        child.set_clip_rect(viewport);
        let ui = &mut child;

        let text = p.text;
        let lbl = |ui: &Ui, id: &str, html: &str| rich::label(ui, rect(id), html, qt::FONT_PX, text);
        lbl(ui, "model_label", "Density model:");
        let models: Vec<String> = DensityModel::ALL.iter().map(|m| m.label().to_owned()).collect();
        let current = DensityModel::ALL.iter().position(|m| *m == self.model);
        if let Some(i) = qt::combo(ui, rect("model"), Id::new("an_model"), &models, current, "", true, p) {
            self.model = DensityModel::ALL[i];
            self.density_model_changed();
        }
        lbl(ui, "fold_label", "Fold-number:");
        let folds: Vec<String> = (1..=4).map(|n| n.to_string()).collect();
        if let Some(i) = qt::combo(ui, rect("fold"), Id::new("an_fold"), &folds, Some(self.fold - 1), "", true, p) {
            self.fold = i + 1;
        }
        if qt::button(ui, rect("calculate"), Id::new("an_calculate"), "Calculate", false, self.fit.is_some(), p).clicked() {
            self.recalculate_shock_parameters();
        }
        lbl(ui, "equation_label", "Best Fit Equation:");
        // `font-size: 16px; padding: 4px`: a styled frame, so QLabel also
        // indents the text by half an "x".
        let indent = (crate::fonts::design_width("x", 16.0, Weight::Regular).unwrap_or(8.0) / 2.0).floor();
        let eq = rect("equation").shrink(4.0);
        let eq = Rect::from_min_max(pos2(eq.left() + indent, eq.top()), eq.max);
        rich::label(ui, eq, &self.equation, 16.0, text);
        lbl(ui, "stats_header", "<b>Fit Metrics:</b>");
        lbl(ui, "r2", &self.r2_text);
        lbl(ui, "rmse", &self.rmse_text);
        lbl(ui, "shock_header", &self.shock_header);
        for (i, line) in self.summary_lines().iter().enumerate() {
            lbl(ui, ["s0", "s1", "s2", "s3", "s4", "s5", "s6"][i], line);
        }
        lbl(ui, "comparison_header", &format!("<b>Density model comparison ({}-fold):</b>", self.fold));
        self.comparison_table(ui, rect("table"), p);
        if qt::button(ui, rect("save_graph"), Id::new("an_save_graph"), "Save Graph", false, true, p).clicked() {
            self.save_graph(ui.ctx());
        }
        if qt::button(ui, rect("save_data"), Id::new("an_save_data"), "Save Data", false, true, p).clicked() {
            self.save_data();
        }
        qt::check_box(ui, rect("existing"), Id::new("an_existing"), "Existing Excel File", &mut self.existing_excel, true, p);
        lbl(ui, "extra_label", "Extra Plots:");
        let extras: Vec<String> = EXTRA_PLOTS.iter().map(|s| (*s).to_owned()).collect();
        if let Some(i) = qt::combo(ui, rect("extra"), Id::new("an_extra"), &extras, Some(self.extra_choice), "", true, p) {
            self.extra_choice = i;
        }
        if qt::button(ui, rect("plot_extra"), Id::new("an_plot_extra"), "Plot", false, true, p).clicked() {
            self.plot_extra();
        }
    }

    /// The density-model comparison (`QTableWidget`): a rounded frame, the
    /// corner button, column headers stretched over the width (clipped),
    /// the models' row headers, right-aligned cells, the selected model bold.
    fn comparison_table(&self, ui: &Ui, r: Rect, p: &Palette) {
        let painter = ui.painter();
        widgets::qt_box(ui, r, 10.0, p.input_bg, p.border);
        let c = Rect::from_min_max(r.min + vec2(9.0, 7.0), r.max - vec2(9.0, 7.0));
        let header_w = 110.0;
        let row_h = 34.0;
        let corner = Rect::from_min_size(c.min, vec2(header_w, row_h));
        // The header views' background is the palette's window.
        let vhead = Rect::from_min_max(pos2(c.left(), corner.bottom()), pos2(corner.right(), c.bottom()));
        painter.rect_filled(vhead, 0.0, p.qt.window);
        fusion_header(ui, corner, p);
        // Stretched sections: the first `remainder` one pixel wider.
        let total = (c.width() - header_w).round() as i32;
        let n = COLUMNS.len() as i32;
        let widths: Vec<f32> = (0..n).map(|i| (total / n + i32::from(i < total % n)) as f32).collect();
        let line = |a: egui::Pos2, b: egui::Pos2| painter.line_segment([a, b], egui::Stroke::new(1.0, p.border));
        let mut x = corner.right();
        for (i, ((header, _), w)) in COLUMNS.iter().zip(&widths).enumerate() {
            let s = Rect::from_min_size(pos2(x, c.top()), vec2(*w, row_h));
            painter.rect_filled(s, 0.0, p.surface_alt);
            line(pos2(s.left(), s.top() + 0.5), pos2(s.right(), s.top() + 0.5));
            line(pos2(s.left(), s.bottom() - 0.5), pos2(s.right(), s.bottom() - 0.5));
            line(pos2(s.left() + 0.5, s.top()), pos2(s.left() + 0.5, s.bottom()));
            if i == COLUMNS.len() - 1 {
                line(pos2(s.right() - 0.5, s.top()), pos2(s.right() - 0.5, s.bottom()));
            }
            // Centred in the section's padding (6 px), clipped to it.
            let inner = Rect::from_min_max(pos2(s.left() + 7.0, s.top() + 1.0), pos2(s.right() - 6.0, s.bottom() - 1.0));
            let clip = ui.painter_at(inner);
            let galley = ui.ctx().fonts_mut(|f| f.layout_no_wrap((*header).to_owned(), ui_font(qt::FONT_PX, Weight::Semibold), p.muted));
            let top = pos2((inner.center().x - galley.size().x / 2.0).round(), (inner.center().y - galley.size().y / 2.0).round());
            clip.galley(top, galley, p.muted);
            x += w;
        }
        for (row, model) in DensityModel::ALL.iter().enumerate() {
            let y = corner.bottom() + row_h * row as f32;
            let bold = *model == self.model;
            let h = Rect::from_min_size(pos2(c.left(), y), vec2(header_w, row_h));
            painter.rect_filled(h, 0.0, p.surface_alt);
            line(pos2(h.left(), h.top() + 0.5), pos2(h.right(), h.top() + 0.5));
            line(pos2(h.left(), h.bottom() - 0.5), pos2(h.right(), h.bottom() - 0.5));
            line(pos2(h.left() + 0.5, h.top()), pos2(h.left() + 0.5, h.bottom()));
            // The stylesheet's weight (600) wins over the item's bold font.
            widgets::text_left(ui, h.left() + 7.0, h.center().y, model.label(), qt::FONT_PX, Weight::Semibold, p.muted);
            let values = self.comparison.get(model);
            let mut x = h.right();
            for ((field, digits), w) in [0usize, 1, 2, 3].iter().zip(COLUMNS.iter().map(|c| c.1)).zip(&widths) {
                let cell = Rect::from_min_size(pos2(x, y), vec2(*w, row_h));
                line(pos2(cell.left(), cell.bottom() - 0.5), pos2(cell.right(), cell.bottom() - 0.5));
                line(pos2(cell.right() - 0.5, cell.top()), pos2(cell.right() - 0.5, cell.bottom()));
                let v = values.map(|s| match field {
                    0 => s.initial_shock_speed_km_s,
                    1 => s.initial_shock_height_rs,
                    2 => s.avg_shock_speed_km_s,
                    _ => s.avg_shock_height_rs,
                });
                let text = match v {
                    Some(v) if v.is_finite() => format!("{v:.digits$}"),
                    _ => "\u{2014}".into(),
                };
                let cw = if bold { Weight::Bold } else { Weight::Regular };
                // The cell less the grid line, less the 3 px text margins.
                let shown = elide(ui, &text, cw, *w - 7.0);
                widgets::text_right(ui, cell.right() - 4.0, cell.center().y, &shown, qt::FONT_PX, cw, p.text);
                x += w;
            }
        }
    }

    /// Shows the window; returns its events and whether it closed.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> (Vec<WindowEvent>, bool) {
        let size = crate::downloads::fit_to_screen(ctx, 1100.0, 700.0);
        let mut events = Vec::new();
        let mut closed = false;
        crate::downloads::window(ctx, "analyzer_window", TITLE, size, vec2(715.0, 300.0), p, |ui, close| {
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

/// `QColor::darker(factor)`.
fn darker(c: Color32, factor: u32) -> Color32 {
    let f = |v: u8| ((v as u32 * 100) / factor).min(255) as u8;
    Color32::from_rgb(f(c.r()), f(c.g()), f(c.b()))
}

/// Fusion's header section (`CE_HeaderSection`, the table's corner
/// button): the button colour lifted as Fusion lifts it, in a gradient.
fn fusion_header(ui: &Ui, r: Rect, p: &Palette) {
    let b = p.qt.button;
    let gray = (b.r() as u32 * 11 + b.g() as u32 * 16 + b.b() as u32 * 5) / 32;
    let lift = 100 + (180u32.saturating_sub(gray) / 6).max(1);
    let lighter = |c: Color32, f: u32| {
        let g = |v: u8| ((v as u32 * f) / 100).min(255) as u8;
        Color32::from_rgb(g(c.r()), g(c.g()), g(c.b()))
    };
    let base = lighter(b, lift);
    let (top, bottom) = (lighter(base, 104), darker(base, 102));
    let mut mesh = egui::Mesh::default();
    for (pos, col) in [(r.left_top(), top), (r.right_top(), top), (r.right_bottom(), bottom), (r.left_bottom(), bottom)] {
        mesh.vertices.push(egui::epaint::Vertex { pos, uv: egui::epaint::WHITE_UV, color: col });
    }
    mesh.indices.extend([0, 1, 2, 0, 2, 3]);
    ui.painter().add(egui::Shape::mesh(mesh));
}

/// `QFontMetrics::elidedText(…, Qt::ElideRight, width)`, by the face's
/// design advances as Qt measures them.
fn elide(ui: &Ui, text: &str, weight: Weight, width: f32) -> String {
    let w = |s: &str| crate::fonts::design_width(s, qt::FONT_PX, weight).unwrap_or_else(|| widgets::text_width(ui, s, qt::FONT_PX, weight));
    if w(text) <= width {
        return text.to_owned();
    }
    let chars: Vec<char> = text.chars().collect();
    for n in (0..chars.len()).rev() {
        let s: String = chars[..n].iter().collect::<String>() + "\u{2026}";
        if w(&s) <= width {
            return s;
        }
    }
    "\u{2026}".into()
}
