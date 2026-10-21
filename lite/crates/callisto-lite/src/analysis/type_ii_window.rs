//! Analysis → Type II Band-splitting (`TypeIIBandSplittingDialog`): points
//! picked along the upper and lower lanes of a split Type II burst, a power
//! law fitted to each, the Analyzer's shock speed and height turned into
//! the compression ratio, Alfvén Mach number, Alfvén speed and magnetic
//! field, and the magnetic field against shock height (BvR).

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use callisto_core::analysis::fit::{fit_power_law, power_law};
use callisto_core::analysis::session::{
    AnalysisSession, BandFitState, BandPoints, FitParams, TypeIIBlock, TypeIIInputs, TypeIIPlotStyle, UiBlock,
};
use callisto_core::analysis::type_ii::{BandFit, BvrProfile, TypeIIResults, calculate_b_vs_r_profile, calculate_type_ii_parameters};
use callisto_core::plot::Colormap;
use callisto_core::plot::qt_raster::{Pen, parse_color};
use callisto_core::radio::array::Array2;
use callisto_core::radio::frequency_axis::{axis_edges, finite_data_limits, frequency_edges};
use callisto_core::radio::numeric::linspace;
use egui::{Color32, Id, Rect, Sense, TextureHandle, TextureOptions, Ui, UiBuilder, pos2, vec2};

use super::band_icons::{self, BandIcon};
use super::richdoc::{self, Doc};
use super::type_ii_settings::{SettingsEvent, TypeIISettingsDialog};
use super::{StatusLine, WindowEvent, status_bar};
use crate::canvas::{CanvasEvent, ImageUpdate, PlotCanvas, Series, TextStyle};
use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;
use crate::ui::qlayout::{BoxLayout, Container, Geometry, IRect, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::qt;

const TITLE: &str = "Type II Band-splitting";
const BANDS: [&str; 2] = ["Upper Band", "Lower Band"];
const SPEEDS: [&str; 2] = ["Initial Shock Speed", "Average Shock Speed"];
/// `_ICON_BUTTON_SIZE` and `_ICON_SIZE`.
const BUTTON: f32 = 52.0;
const ICON: f32 = 40.0;
const GRID_ALPHA: f64 = 0.25;

/// `_detail_block`.
fn detail_block(title: &str, value_html: &str, muted: bool) -> String {
    let color = if muted { "#7a7a7a" } else { "#5f7693" };
    format!(
        "<div style='margin-bottom:6px;'><span style='font-size:11px; color:{color};'><b>{title}</b></span><br><span style='font-size:13px;'><b>{value_html}</b></span></div>"
    )
}

/// `_format_value`: "" for no value.
fn fmt(v: Option<f64>, digits: usize, suffix: &str) -> String {
    match v {
        Some(v) => format!("{}{suffix}", py_fixed(v, digits)),
        None => String::new(),
    }
}

/// Python's `f"{v:.{digits}f}"` (`nan`, `inf`).
fn py_fixed(v: f64, digits: usize) -> String {
    if v.is_nan() {
        "nan".into()
    } else if v.is_infinite() {
        if v > 0.0 { "inf".into() } else { "-inf".into() }
    } else {
        format!("{v:.digits$}")
    }
}

/// `_format_pm`.
fn fmt_pm(v: Option<f64>, err: Option<f64>, digits: usize, suffix: &str) -> String {
    let base = fmt(v, digits, "");
    if base.is_empty() {
        return "\u{2014}".into();
    }
    let e = fmt(err, digits, "");
    if e.is_empty() { format!("{base}{suffix}") } else { format!("{base} \u{b1} {e}{suffix}") }
}

fn or_dash(s: String) -> String {
    if s.is_empty() { "\u{2014}".into() } else { s }
}

/// `_time_step_seconds`: the median spacing of the time axis, else 1 s.
fn time_step(time: &[f64]) -> f64 {
    let mut d: Vec<f64> = time.windows(2).map(|w| (w[1] - w[0]).abs()).filter(|v| v.is_finite() && *v > 1e-9).collect();
    if d.is_empty() {
        return 1.0;
    }
    d.sort_by(f64::total_cmp);
    let n = d.len();
    if n % 2 == 1 { d[n / 2] } else { (d[n / 2 - 1] + d[n / 2]) / 2.0 }
}

fn arrays(points: &[(f64, f64)]) -> (Vec<f64>, Vec<f64>) {
    (points.iter().map(|p| p.0).collect(), points.iter().map(|p| p.1).collect())
}

/// `restore_session`'s points. A normalised session keeps an empty band as
/// `None`, and `np.asarray(None)` is one NaN: such a band comes back as a
/// single non-finite point (a full-app bug Lite reproduces).
fn restore_points(b: &BandPoints, normalized: bool) -> Vec<(f64, f64)> {
    if b.time_seconds.is_empty() && b.freqs.is_empty() {
        return if normalized { vec![(f64::NAN, f64::NAN)] } else { Vec::new() };
    }
    if b.time_seconds.len() != b.freqs.len() {
        return Vec::new();
    }
    b.time_seconds.iter().copied().zip(b.freqs.iter().copied()).collect()
}

/// `_finite_xy`.
fn finite_xy(xy: Option<(Vec<f64>, Vec<f64>)>) -> (Vec<f64>, Vec<f64>) {
    let (x, y) = xy.unwrap_or_default();
    if x.len() != y.len() {
        return (Vec::new(), Vec::new());
    }
    x.into_iter().zip(y).filter(|(a, b)| a.is_finite() && b.is_finite()).unzip()
}

/// The BvR fit over 400 heights from the lowest to the highest.
fn bvr_fit_curve(p: &BvrProfile) -> (Vec<f64>, Vec<f64>) {
    let lo = p.heights_rs.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = p.heights_rs.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let xs = linspace(lo, hi, 400);
    let ys = xs.iter().map(|&x| power_law(x, p.fit.a, p.fit.b)).collect();
    (xs, ys)
}

fn rgba(c: &str) -> [u8; 4] {
    parse_color(c).unwrap_or([255, 255, 255, 255])
}

/// A rendered tool icon: the icon, the dark set, the window colour a
/// disabled one was made over, its side in device pixels.
type IconKey = (BandIcon, bool, Option<[u8; 4]>, u32);

/// The spectrum the window shows (`set_spectrum`).
#[derive(Clone)]
pub struct Spectrum {
    pub display: Arc<Array2>,
    pub freqs: Vec<f64>,
    pub time: Vec<f64>,
    pub unit: String,
    pub cmap: Colormap,
    pub frequency_step_mhz: Option<f64>,
    /// The main window's noise clipping thresholds, in display units.
    pub display_levels: Option<(f64, f64)>,
}

pub struct TypeIIWindow {
    spectrum: Spectrum,
    filename: String,
    context: AnalysisSession,
    upper: Vec<(f64, f64)>,
    lower: Vec<(f64, f64)>,
    upper_fit: Option<BandFitState>,
    lower_fit: Option<BandFitState>,
    results: Option<TypeIIResults>,
    /// The all-`None` results record a normalised session carries: a
    /// non-empty dict, so the full app takes it for results (the averaging
    /// interval reads "[, ]") until a point, a fit or the speed source
    /// clears it.
    blank_results: bool,
    /// The fit and result labels as the full app left them when
    /// `_fit_both_bands` fitted the upper band, then failed on the lower
    /// one: it updates no label, so they show the old state until the
    /// next handler that sets them.
    stale_fits: Option<Vec<String>>,
    stale_results: Option<(Vec<String>, Vec<String>)>,
    bvr: bool,
    plot_style: TypeIIPlotStyle,
    current_style: TypeIIPlotStyle,
    band: usize,
    speed: usize,
    add_points: bool,
    canvas: PlotCanvas,
    canvas_dark: Option<bool>,
    status: StatusLine,
    notices: Vec<Notice>,
    events: Vec<WindowEvent>,
    settings: Option<TypeIISettingsDialog>,
    ui_family: String,
    families: Vec<String>,
    scroll: f32,
    icons: HashMap<IconKey, TextureHandle>,
    focus_due: bool,
    refresh_due: bool,
}

impl TypeIIWindow {
    /// `TypeIIBandSplittingDialog(spectrum, freqs, time, filename, session=…)`;
    /// `normalized` when the session went through `normalize_analysis_session`
    /// (every one but the main window's fresh, empty candidate).
    pub fn new(spectrum: Spectrum, filename: &str, session: &AnalysisSession, normalized: bool, ui_family: &str, families: Vec<String>) -> Self {
        // `os.path.splitext(os.path.basename(filename))[0]`: one suffix only.
        let base = std::path::Path::new(filename.trim()).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let base = if base.is_empty() { "dynamic_spectrum".to_owned() } else { base };
        let stem = match base.rfind('.') {
            Some(i) if i > 0 => base[..i].to_owned(),
            _ => base,
        };
        let mut w = Self {
            spectrum,
            filename: stem,
            context: session.clone(),
            upper: Vec::new(),
            lower: Vec::new(),
            upper_fit: None,
            lower_fit: None,
            results: None,
            blank_results: false,
            stale_fits: None,
            stale_results: None,
            bvr: false,
            plot_style: TypeIIPlotStyle::default(),
            current_style: TypeIIPlotStyle::default(),
            band: 0,
            speed: 0,
            add_points: false,
            canvas: PlotCanvas::new().with_id(Id::new("type_ii_plot")),
            canvas_dark: None,
            status: StatusLine::default(),
            notices: Vec::new(),
            events: Vec::new(),
            settings: None,
            ui_family: ui_family.to_owned(),
            families,
            scroll: 0.0,
            icons: HashMap::new(),
            focus_due: true,
            refresh_due: true,
        };
        w.status.show("Select an active band, enable 'Add Points', and click along the split lanes.", 0);
        w.restore(session, normalized);
        w
    }

    pub fn focus(&mut self) {
        self.focus_due = true;
    }

    /// `set_spectrum`: a new spectrum, the points and fits kept.
    pub fn set_spectrum(&mut self, spectrum: Spectrum) {
        self.spectrum = spectrum;
        self.refresh_due = true;
    }

    /// The graph settings' font family, for the app to install.
    pub fn font_family(&self) -> Option<String> {
        let f = self.current_style.font_family.trim();
        (!f.is_empty()).then(|| f.to_owned())
    }

    fn band_points(&self, band: usize) -> &Vec<(f64, f64)> {
        if band == 0 { &self.upper } else { &self.lower }
    }

    fn band_points_mut(&mut self, band: usize) -> &mut Vec<(f64, f64)> {
        if band == 0 { &mut self.upper } else { &mut self.lower }
    }

    fn band_fit(&self, band: usize) -> Option<BandFitState> {
        if band == 0 { self.upper_fit } else { self.lower_fit }
    }

    fn set_band_fit(&mut self, band: usize, fit: Option<BandFitState>) {
        if band == 0 {
            self.upper_fit = fit;
        } else {
            self.lower_fit = fit;
        }
    }

    fn band_text(&self) -> String {
        BANDS[self.band].to_lowercase()
    }

    // ----------------------------------------------------------- session

    /// `_analysis_inputs_from_context`: the Analyzer's shock summary, else
    /// what the session kept for Type II.
    fn inputs(&self) -> TypeIIInputs {
        let shock = &self.context.analyzer.shock_summary;
        let saved = &self.context.type_ii.analysis_inputs;
        TypeIIInputs {
            initial_shock_speed_km_s: shock.initial_shock_speed_km_s.or(saved.initial_shock_speed_km_s),
            avg_shock_speed_km_s: shock.avg_shock_speed_km_s.or(saved.avg_shock_speed_km_s),
            initial_shock_height_rs: shock.initial_shock_height_rs.or(saved.initial_shock_height_rs),
            avg_shock_height_rs: shock.avg_shock_height_rs.or(saved.avg_shock_height_rs),
            start_freq_mhz: shock.start_freq_mhz.or(saved.start_freq_mhz),
            avg_drift_mhz_s: shock.avg_drift_mhz_s.or(saved.avg_drift_mhz_s),
            avg_drift_err_mhz_s: shock.avg_drift_err_mhz_s.or(saved.avg_drift_err_mhz_s),
            fold: self.context.analyzer.fold.clamp(1, 4),
            average_speed: self.speed == 1,
        }
    }

    /// `_analysis_inputs_ready`.
    fn inputs_ready(i: &TypeIIInputs) -> bool {
        [i.initial_shock_speed_km_s, i.avg_shock_speed_km_s, i.initial_shock_height_rs, i.avg_shock_height_rs, i.start_freq_mhz]
            .iter()
            .all(|v| v.is_some_and(f64::is_finite))
    }

    /// `_selected_analysis_shock_speed`.
    fn selected_speed(&self, i: &TypeIIInputs) -> Option<f64> {
        let v = if self.speed == 1 { i.avg_shock_speed_km_s } else { i.initial_shock_speed_km_s };
        v.filter(|v| v.is_finite() && *v > 0.0)
    }

    /// The saved inputs as `normalize_analysis_session` leaves them: the
    /// Analyzer's shock summary first, its fold when it has values.
    fn saved_inputs(s: &AnalysisSession, normalized: bool) -> TypeIIInputs {
        let saved = &s.type_ii.analysis_inputs;
        if !normalized {
            return saved.clone();
        }
        let shock = &s.analyzer.shock_summary;
        let fields = [
            shock.initial_shock_speed_km_s,
            shock.avg_shock_speed_km_s,
            shock.initial_shock_height_rs,
            shock.avg_shock_height_rs,
            shock.start_freq_mhz,
            shock.avg_drift_mhz_s,
            shock.avg_drift_err_mhz_s,
        ];
        let fold = if fields.iter().any(Option::is_some) { shock.fold } else { saved.fold };
        TypeIIInputs {
            initial_shock_speed_km_s: shock.initial_shock_speed_km_s.or(saved.initial_shock_speed_km_s),
            avg_shock_speed_km_s: shock.avg_shock_speed_km_s.or(saved.avg_shock_speed_km_s),
            initial_shock_height_rs: shock.initial_shock_height_rs.or(saved.initial_shock_height_rs),
            avg_shock_height_rs: shock.avg_shock_height_rs.or(saved.avg_shock_height_rs),
            start_freq_mhz: shock.start_freq_mhz.or(saved.start_freq_mhz),
            avg_drift_mhz_s: shock.avg_drift_mhz_s.or(saved.avg_drift_mhz_s),
            avg_drift_err_mhz_s: shock.avg_drift_err_mhz_s.or(saved.avg_drift_err_mhz_s),
            fold: fold.clamp(1, 4),
            average_speed: saved.average_speed,
        }
    }

    /// `_analysis_inputs_match_saved`.
    fn inputs_match(saved: &TypeIIInputs, current: &TypeIIInputs) -> bool {
        if saved.average_speed != current.average_speed || saved.fold != current.fold {
            return false;
        }
        let pairs = [
            (saved.initial_shock_speed_km_s, current.initial_shock_speed_km_s),
            (saved.avg_shock_speed_km_s, current.avg_shock_speed_km_s),
            (saved.initial_shock_height_rs, current.initial_shock_height_rs),
            (saved.avg_shock_height_rs, current.avg_shock_height_rs),
            (saved.start_freq_mhz, current.start_freq_mhz),
        ];
        pairs.iter().all(|(a, b)| matches!((a, b), (Some(a), Some(b)) if (a - b).abs() <= 1e-9))
    }

    /// `session_state`.
    pub fn session_state(&self) -> AnalysisSession {
        let base = &self.context;
        let (ut, uf) = arrays(&self.upper);
        let (lt, lf) = arrays(&self.lower);
        let inputs = self.inputs();
        AnalysisSession {
            identity: Default::default(),
            source: base.source.clone(),
            max_intensity: base.max_intensity.clone(),
            analyzer: base.analyzer.clone(),
            type_ii: TypeIIBlock {
                upper: BandPoints { time_seconds: ut, freqs: uf },
                lower: BandPoints { time_seconds: lt, freqs: lf },
                upper_fit: self.upper_fit,
                lower_fit: self.lower_fit,
                fold: inputs.fold,
                analysis_inputs: inputs,
                results: self.results.clone(),
                plot_style: self.plot_style.clone(),
            },
            ui: UiBlock { restore_type_ii_window: true, ..base.ui.clone() },
        }
    }

    fn emit_session_changed(&mut self) {
        self.events.push(WindowEvent::SessionChanged(self.session_state()));
    }

    /// `restore_session`.
    pub fn restore_session(&mut self, s: &AnalysisSession) {
        self.restore(s, true);
    }

    /// `restore_session(session, emit_change=False)`.
    fn restore(&mut self, s: &AnalysisSession, normalized: bool) {
        self.context = s.clone();
        self.labels_updated(true, true);
        let t = &s.type_ii;
        self.upper = restore_points(&t.upper, normalized);
        self.lower = restore_points(&t.lower, normalized);
        self.upper_fit = t.upper_fit;
        self.lower_fit = t.lower_fit;
        self.results = t.results.clone();
        self.blank_results = normalized && self.results.is_none();
        self.plot_style = t.plot_style.clone();
        self.current_style = self.plot_style.clone();
        if let Some(d) = &mut self.settings {
            d.load_applied_style(&self.plot_style);
        }
        self.speed = usize::from(t.analysis_inputs.average_speed);
        let current = self.inputs();
        if (self.results.is_some() || self.blank_results) && !Self::inputs_match(&Self::saved_inputs(s, normalized), &current) {
            self.invalidate_results();
        }
        self.refresh_due = true;
    }

    // ------------------------------------------------------------ actions

    fn notice(&mut self, kind: NoticeKind, text: impl Into<String>) {
        self.notices.push(Notice { kind, title: TITLE.into(), text: text.into() });
    }

    /// `_invalidate_results`.
    fn invalidate_results(&mut self) {
        self.results = None;
        self.blank_results = false;
    }

    /// `_invalidate_band_state`.
    fn invalidate_band(&mut self, band: usize) {
        self.set_band_fit(band, None);
        self.invalidate_results();
        self.labels_updated(true, true);
    }

    /// `_update_fit_labels` / `_update_result_labels` ran.
    fn labels_updated(&mut self, fits: bool, results: bool) {
        if fits {
            self.stale_fits = None;
        }
        if results {
            self.stale_results = None;
        }
    }

    /// `_on_plot_clicked`.
    pub fn add_point(&mut self, x: f64, y: f64) {
        if self.bvr || !self.add_points || !(x.is_finite() && y.is_finite()) {
            return;
        }
        let band = self.band;
        self.band_points_mut(band).push((x, y));
        self.invalidate_band(band);
        self.refresh_due = true;
        self.status.show(format!("Added point to the {}.", self.band_text()), 2500);
        self.emit_session_changed();
    }

    fn undo(&mut self) {
        let band = self.band;
        if self.band_points(band).is_empty() {
            return self.status.show("No points to undo for the active band.", 2500);
        }
        self.band_points_mut(band).pop();
        self.invalidate_band(band);
        self.refresh_due = true;
        self.status.show(format!("Removed last point from the {}.", self.band_text()), 2500);
        self.emit_session_changed();
    }

    fn clear_band(&mut self) {
        let band = self.band;
        if self.band_points(band).is_empty() {
            return self.status.show("The active band has no points to clear.", 2500);
        }
        self.band_points_mut(band).clear();
        self.invalidate_band(band);
        self.refresh_due = true;
        self.status.show(format!("Cleared all points from the {}.", self.band_text()), 2500);
        self.emit_session_changed();
    }

    /// `_fit_band`.
    fn fit_band(&mut self, band: usize) -> Result<(), String> {
        let (t, f) = arrays(self.band_points(band));
        let fit = fit_power_law(&t, &f)?;
        let state = BandFitState {
            fit: FitParams {
                a: fit.a,
                b: fit.b,
                std_errs: fit.std_errs.map(|e| e.is_finite().then_some(e)),
                r2: Some(fit.r2),
                rmse: Some(fit.rmse),
            },
            point_count: (fit.point_count > 0).then_some(fit.point_count),
        };
        self.set_band_fit(band, Some(state));
        self.invalidate_results();
        Ok(())
    }

    pub fn fit_active(&mut self) {
        let band = self.band;
        if let Err(e) = self.fit_band(band) {
            self.status.show(e.clone(), 3500);
            return self.notice(NoticeKind::Warning, e);
        }
        self.labels_updated(true, true);
        self.refresh_due = true;
        self.status.show(format!("Fitted the {}.", self.band_text()), 2500);
        self.emit_session_changed();
    }

    /// `_fit_both_bands`.
    pub fn fit_both(&mut self) {
        let [_, fits, averaging, plasma] = self.labels();
        let r = self.fit_band(0).and_then(|_| self.fit_band(1));
        if let Err(e) = r {
            // The upper fit may have been taken; nothing on show changes.
            self.stale_fits.get_or_insert(fits);
            self.stale_results.get_or_insert((averaging, plasma));
            self.status.show(e.clone(), 3500);
            return self.notice(NoticeKind::Warning, e);
        }
        self.labels_updated(true, true);
        self.refresh_due = true;
        self.status.show("Fitted both split bands.", 2500);
        self.emit_session_changed();
    }

    fn band_fit_params(&self, band: usize) -> Option<BandFit> {
        self.band_fit(band).map(|f| BandFit { a: f.fit.a, b: f.fit.b })
    }

    /// `_calculate_parameters`.
    pub fn calculate(&mut self) {
        let (Some(uf), Some(lf)) = (self.band_fit_params(0), self.band_fit_params(1)) else {
            return self.notice(NoticeKind::Information, "Fit both bands before calculating.");
        };
        let inputs = self.inputs();
        if !Self::inputs_ready(&inputs) {
            let m = "Run the usual analysis window first so the initial/average shock speeds, shock heights, and starting frequency are available.";
            self.status.show(m, 5000);
            return self.notice(NoticeKind::Information, m);
        }
        let Some(speed) = self.selected_speed(&inputs) else {
            let m = "The selected analyzer shock speed is unavailable.";
            self.status.show(m, 4000);
            return self.notice(NoticeKind::Information, m);
        };
        let (ut, uf_) = arrays(&self.upper);
        let (lt, lf_) = arrays(&self.lower);
        let time = self.spectrum.time.clone();
        match calculate_type_ii_parameters((&ut, &uf_), (&lt, &lf_), uf, lf, inputs.start_freq_mhz.unwrap_or(f64::NAN), speed, Some(&time)) {
            Ok(r) => {
                let warning = r.warning.clone();
                self.results = Some(r);
                self.blank_results = false;
                self.labels_updated(false, true);
                if warning.is_empty() {
                    self.status.show("Calculated Type II band-splitting parameters.", 3000);
                } else {
                    self.status.show(warning, 5000);
                }
                self.emit_session_changed();
            }
            Err(e) => {
                self.status.show(e.clone(), 3500);
                self.notice(NoticeKind::Warning, e);
            }
        }
    }

    /// `_build_bvr_profile`.
    fn bvr_profile(&self) -> Result<BvrProfile, String> {
        let (Some(uf), Some(lf)) = (self.band_fit_params(0), self.band_fit_params(1)) else {
            return Err("Fit both bands before plotting B versus R.".into());
        };
        let inputs = self.inputs();
        if !Self::inputs_ready(&inputs) {
            return Err("Run the usual Analyzer window first so the shock-speed and height references are available.".into());
        }
        let speed = self.selected_speed(&inputs).ok_or("The selected analyzer shock speed is unavailable.")?;
        let (ut, uf_) = arrays(&self.upper);
        let (lt, lf_) = arrays(&self.lower);
        calculate_b_vs_r_profile((&ut, &uf_), (&lt, &lf_), uf, lf, speed, inputs.fold, Some(&self.spectrum.time))
    }

    /// `_on_bvr_toggled`.
    pub fn set_bvr(&mut self, on: bool) {
        if on {
            if let Err(e) = self.bvr_profile() {
                self.status.show(e.clone(), 4000);
                return self.notice(NoticeKind::Information, e);
            }
            self.bvr = true;
            self.add_points = false;
            self.status.show("Showing magnetic field versus shock height.", 3000);
        } else {
            self.bvr = false;
            self.status.show("Returned to the Type II dynamic spectrum.", 3000);
        }
        self.refresh_due = true;
    }

    /// `_on_add_points_toggled`.
    pub fn set_add_points(&mut self, on: bool) {
        self.add_points = on;
        if on {
            self.status.show(format!("Click along the {} on the dynamic spectrum.", self.band_text()), 0);
        } else {
            self.status.show("Point capture paused.", 0);
        }
    }

    /// The active band combo (`band_combo.setCurrentIndex`).
    pub fn set_band(&mut self, band: usize) {
        self.band = band.min(1);
    }

    /// `_on_speed_mode_changed`.
    fn set_speed(&mut self, speed: usize) {
        self.speed = speed.min(1);
        if self.results.is_some() || self.blank_results {
            self.invalidate_results();
            self.labels_updated(false, true);
            self.status.show("Shock speed source changed. Recalculate plasma parameters.", 3000);
            self.emit_session_changed();
        }
        if self.bvr {
            self.refresh_due = true;
        }
    }

    /// `_open_or_focus_settings_dialog`.
    fn open_settings(&mut self) {
        match &mut self.settings {
            Some(d) => d.focus(),
            None => {
                self.settings = Some(TypeIISettingsDialog::new(&self.plot_style, &TypeIIPlotStyle::default(), &self.ui_family, self.families.clone()));
            }
        }
    }

    /// `_apply_plot_style(style, persist=…)`.
    fn apply_style(&mut self, style: TypeIIPlotStyle, persist: bool) {
        self.current_style = style.clone();
        if persist {
            self.plot_style = style;
        }
        self.refresh_due = true;
    }

    // --------------------------------------------------------------- plot

    /// `_fit_curve_samples`: the fit over the band's positive times, 400 samples.
    fn fit_curve(&self, band: usize) -> Option<(Vec<f64>, Vec<f64>)> {
        let fit = self.band_fit(band)?;
        let points = self.band_points(band);
        if points.is_empty() {
            return None;
        }
        let times: Vec<f64> = points.iter().map(|p| p.0).filter(|t| t.is_finite() && *t > 0.0).collect();
        if times.len() < 2 {
            return None;
        }
        let (lo, hi) = (times.iter().copied().fold(f64::INFINITY, f64::min), times.iter().copied().fold(f64::NEG_INFINITY, f64::max));
        let xs = linspace(lo, hi, 400);
        let ys = xs.iter().map(|&x| power_law(x, fit.fit.a, fit.fit.b)).collect();
        Some((xs, ys))
    }

    fn text_style(&self) -> TextStyle {
        let s = &self.current_style;
        let family = crate::fonts::normalize_font_family(&s.font_family);
        TextStyle {
            family: None,
            extra_family: (!family.is_empty()).then_some(family),
            tick_px: s.tick_font_px.max(1) as u32,
            axis_px: s.axis_label_font_px.max(1) as u32,
            title_px: s.title_font_px.max(1) as u32,
            title_bold: s.title_bold,
            title_italic: s.title_italic,
            axis_bold: s.axis_bold,
            axis_italic: s.axis_italic,
            ticks_bold: s.ticks_bold,
            ticks_italic: s.ticks_italic,
        }
    }

    /// `_refresh_plot`.
    fn refresh_plot(&mut self, dark: bool) {
        self.canvas.set_dark(dark);
        self.canvas.set_grid(Some(GRID_ALPHA));
        self.canvas.set_text_style(self.text_style());
        if self.bvr {
            match self.bvr_profile().and_then(|p| {
                if p.heights_rs.len() < 2 || p.magnetic_field_g.len() < 2 {
                    Err("B versus R plotting requires a valid magnetic-field profile.".to_owned())
                } else {
                    Ok(p)
                }
            }) {
                Ok(p) => self.refresh_bvr(&p),
                Err(e) => {
                    self.bvr = false;
                    self.status.show(e, 4000);
                    self.refresh_spectrum();
                }
            }
        } else {
            self.refresh_spectrum();
        }
        self.canvas.enable_auto_range();
    }

    fn series(&self, bvr: Option<&BvrProfile>) -> Vec<Series> {
        let s = &self.current_style;
        // `_set_curve_item_data` / `_set_scatter_item_data`: the finite points only.
        let curve = |xy: Option<(Vec<f64>, Vec<f64>)>, color: &str, width: i64| {
            let (x, y) = finite_xy(xy);
            Series::Curve { x, y, pen: Pen::new(rgba(color), width.max(1) as f64), antialias: true }
        };
        let scatter = |points: Option<(Vec<f64>, Vec<f64>)>, color: &str, size: i64| {
            let (x, y) = finite_xy(points);
            Series::Scatter { x, y, size: size.max(1) as f64, pen: Pen::new(rgba(color), 1.3), brush: rgba(color) }
        };
        let none = || None;
        match bvr {
            None => vec![
                curve(self.fit_curve(0), &s.upper_line_color, s.upper_line_width),
                curve(self.fit_curve(1), &s.lower_line_color, s.lower_line_width),
                scatter(Some(arrays(&self.upper)), &s.upper_marker_color, s.upper_marker_size),
                scatter(Some(arrays(&self.lower)), &s.lower_marker_color, s.lower_marker_size),
                curve(none(), &s.bvr_line_color, s.bvr_line_width),
                scatter(none(), &s.bvr_marker_color, s.bvr_marker_size),
            ],
            Some(p) => {
                vec![
                    curve(none(), &s.upper_line_color, s.upper_line_width),
                    curve(none(), &s.lower_line_color, s.lower_line_width),
                    scatter(none(), &s.upper_marker_color, s.upper_marker_size),
                    scatter(none(), &s.lower_marker_color, s.lower_marker_size),
                    curve(Some(bvr_fit_curve(p)), &s.bvr_line_color, s.bvr_line_width),
                    scatter(Some((p.heights_rs.clone(), p.magnetic_field_g.clone())), &s.bvr_marker_color, s.bvr_marker_size),
                ]
            }
        }
    }

    /// `_refresh_spectrum_plot`.
    fn refresh_spectrum(&mut self) {
        let sp = &self.spectrum;
        let freq_edges = frequency_edges(&sp.freqs, sp.frequency_step_mhz.unwrap_or(1.0));
        let time_edges = axis_edges(&sp.time, time_step(&sp.time));
        let title = format!("{}_Type_II_Band_Splitting", self.filename);
        let colorbar_label = format!("Intensity [{}]", sp.unit);
        self.canvas.set_image_visible(true);
        self.canvas.set_colorbar_visible(true);
        let levels = sp.display_levels.or_else(|| finite_data_limits(&sp.display.data));
        match (levels, freq_edges.first(), freq_edges.last(), time_edges.first(), time_edges.last()) {
            (Some(levels), Some(&y0), Some(&y1), Some(&x0), Some(&x1)) => {
                self.canvas.update_image(ImageUpdate {
                    data: sp.display.clone(),
                    extent: [x0, x1, y0, y1],
                    cmap: sp.cmap,
                    gap_rows: None,
                    levels: Some(levels),
                    title: title.clone(),
                    x_label: "Time (s)".into(),
                    y_label: "Frequency (MHz)".into(),
                    colorbar_label: colorbar_label.clone(),
                    view: None,
                    freqs: &[],
                    log_freq: false,
                });
            }
            _ => self.canvas.set_labels(&title, "Time (s)", "Frequency (MHz)", &colorbar_label),
        }
        let series = self.series(None);
        self.canvas.set_series(series);
    }

    /// `_refresh_bvr_plot`.
    fn refresh_bvr(&mut self, p: &BvrProfile) {
        self.canvas.set_image_visible(false);
        self.canvas.set_colorbar_visible(false);
        let title = format!("{}_Magnetic_Field_vs_Shock_Height", self.filename);
        let colorbar_label = format!("Intensity [{}]", self.spectrum.unit);
        self.canvas.set_labels(&title, "Shock Height (R<sub>s</sub>)", "Magnetic Field (G)", &colorbar_label);
        let series = self.series(Some(p));
        self.canvas.set_series(series);
    }

    // ----------------------------------------------------------- the labels

    fn fit_labels(&self, band: usize) -> (String, String) {
        let (name, symbol) = if band == 0 { ("Upper Fit", "u") } else { ("Lower Fit", "l") };
        match self.band_fit(band) {
            None => (
                detail_block(name, &format!("f<sub>{symbol}</sub>(t) not fitted"), true),
                detail_block("Selected points", &self.band_points(band).len().to_string(), false),
            ),
            Some(f) => (
                detail_block(name, &format!("f<sub>{symbol}</sub>(t) = {:.2} t<sup>-{:.2}</sup>", f.fit.a, f.fit.b), false),
                detail_block("R<sup>2</sup>", &or_dash(fmt(f.fit.r2, 4, "")), false)
                    + &detail_block("RMSE", &or_dash(fmt(f.fit.rmse, 4, "")), false)
                    + &detail_block("Selected points", &f.point_count.unwrap_or(0).to_string(), false),
            ),
        }
    }

    fn input_labels(&self) -> Vec<String> {
        let i = self.inputs();
        let status = if Self::inputs_ready(&i) {
            "<span style='color:#2e7d32;'><b>Analyzer reference data loaded.</b></span>".to_owned()
        } else {
            "<span style='color:#b26a00;'><b>Analyzer input missing.</b></span><br>Run the usual Analyzer window first to populate the shock speeds, shock heights, and starting frequency.".to_owned()
        };
        vec![
            status,
            detail_block("Newkirk model", &format!("{}-fold", i.fold), false),
            detail_block("f<sub>start</sub>", &or_dash(fmt(i.start_freq_mhz, 4, " MHz")), false),
            detail_block("\u{27e8}df/dt\u{27e9}", &fmt_pm(i.avg_drift_mhz_s, i.avg_drift_err_mhz_s, 4, " MHz/s"), false),
            detail_block("V<sub>s,0</sub>", &or_dash(fmt(i.initial_shock_speed_km_s, 2, " km/s")), false),
            detail_block("\u{27e8}V<sub>s</sub>\u{27e9}", &or_dash(fmt(i.avg_shock_speed_km_s, 2, " km/s")), false),
            detail_block("R<sub>p,0</sub>", &or_dash(fmt(i.initial_shock_height_rs, 4, " R<sub>s</sub>")), false),
            detail_block("\u{27e8}R<sub>p</sub>\u{27e9}", &or_dash(fmt(i.avg_shock_height_rs, 4, " R<sub>s</sub>")), false),
        ]
    }

    /// The averaging and plasma labels: (averaging, plasma).
    fn result_labels(&self) -> (Vec<String>, Vec<String>) {
        let b = |t: &str, v: String| detail_block(t, &v, false);
        let dash = || "\u{2014}".to_owned();
        let Some(r) = &self.results else {
            // The all-`None` record formats each time as "".
            let interval = if self.blank_results { "[, ]".to_owned() } else { dash() };
            return (
                vec![
                    b("Averaging interval", interval),
                    b("t<sub>start</sub>", dash()),
                    b("f<sub>u</sub>(t<sub>start</sub>)", dash()) + &b("f<sub>l</sub>(t<sub>start</sub>)", dash()),
                    b("\u{27e8}f<sub>u</sub>\u{27e9}", dash()) + &b("\u{27e8}f<sub>l</sub>\u{27e9}", dash()),
                    b("\u{27e8}\u{394}f\u{27e9} = \u{27e8}f<sub>u</sub> - f<sub>l</sub>\u{27e9}", dash()),
                    b("\u{27e8}df<sub>u</sub>/dt\u{27e9}", dash()),
                    String::new(),
                ],
                vec![
                    b("X = (\u{27e8}f<sub>u</sub>\u{27e9} / \u{27e8}f<sub>l</sub>\u{27e9})<sup>2</sup>", dash()),
                    b("M<sub>A</sub>", dash()),
                    b("V<sub>A</sub>", dash()),
                    b("B", dash()),
                ],
            );
        };
        let s = |v: f64, d: usize, suffix: &str| or_dash(fmt(Some(v), d, suffix));
        let warning = r.warning.trim();
        (
            vec![
                b("Averaging interval", format!("[{}, {}]", fmt(Some(r.start_time_s), 4, " s"), fmt(Some(r.end_time_s), 4, " s"))),
                b("t<sub>start</sub>", s(r.start_time_s, 4, " s")),
                b("f<sub>u</sub>(t<sub>start</sub>)", s(r.upper_start_freq_mhz, 4, " MHz")) + &b("f<sub>l</sub>(t<sub>start</sub>)", s(r.lower_start_freq_mhz, 4, " MHz")),
                b("\u{27e8}f<sub>u</sub>\u{27e9}", s(r.avg_upper_freq_mhz, 4, " MHz")) + &b("\u{27e8}f<sub>l</sub>\u{27e9}", s(r.avg_lower_freq_mhz, 4, " MHz")),
                b("\u{27e8}\u{394}f\u{27e9} = \u{27e8}f<sub>u</sub> - f<sub>l</sub>\u{27e9}", s(r.bandwidth_mhz, 4, " MHz")),
                b("\u{27e8}df<sub>u</sub>/dt\u{27e9}", s(r.upper_avg_drift_mhz_s, 4, " MHz/s")),
                if warning.is_empty() { String::new() } else { format!("<span style='color:#b26a00;'><b>Warning:</b> {warning}</span>") },
            ],
            vec![
                b("X = (\u{27e8}f<sub>u</sub>\u{27e9} / \u{27e8}f<sub>l</sub>\u{27e9})<sup>2</sup>", s(r.compression_ratio, 4, "")),
                b("M<sub>A</sub>", s(r.alfven_mach_number, 4, "")),
                b("V<sub>A</sub>", s(r.alfven_speed_km_s, 2, " km/s")),
                b("B", s(r.magnetic_field_g, 4, " G")),
            ],
        )
    }

    /// The detail labels' rich text, top to bottom.
    pub fn detail_html(&self) -> Vec<String> {
        self.labels().concat()
    }

    /// Every detail label, by panel: analyzer, fits, averaging, plasma.
    fn labels(&self) -> [Vec<String>; 4] {
        let fits = self.stale_fits.clone().unwrap_or_else(|| {
            let (uf, us) = self.fit_labels(0);
            let (lf, ls) = self.fit_labels(1);
            vec![uf, us, lf, ls]
        });
        let (avg, plasma) = self.stale_results.clone().unwrap_or_else(|| self.result_labels());
        [self.input_labels(), fits, avg, plasma]
    }

    // --------------------------------------------------------- the window

    /// Whether each tool button is enabled (`_sync_controls`).
    fn enabled(&self, icon: BandIcon) -> bool {
        let spectrum = !self.bvr;
        let n = self.band_points(self.band).len();
        let fits = self.upper_fit.is_some() && self.lower_fit.is_some();
        match icon {
            BandIcon::AddPoints => spectrum,
            BandIcon::Undo | BandIcon::Clear => spectrum && n > 0,
            BandIcon::FitActive => spectrum && n >= 2,
            BandIcon::FitBoth => spectrum && self.upper.len() >= 2 && self.lower.len() >= 2,
            BandIcon::Bvr => fits && Self::inputs_ready(&self.inputs()),
            BandIcon::SavePlot | BandIcon::Settings => true,
        }
    }

    fn main_layout() -> Layout {
        let mut tools = BoxLayout::h([0; 4], 6);
        for icon in BandIcon::ALL {
            tools = tools.widget(WidgetSpec::new(icon_id(icon), Size::new(BUTTON as i32, BUTTON as i32), Policy::Fixed, Policy::Fixed));
        }
        let left = BoxLayout::v([0; 4], 6)
            .add(tools.stretch(1).into_item())
            .widget(WidgetSpec::new("plot", Size::new(912, 652), Policy::Expanding, Policy::Expanding).min_hint(Size::new(54, 54)));
        let content = BoxLayout::h([0; 4], 6)
            .add_stretched(left.into_item(), 3)
            .add_stretched(Item::Widget(WidgetSpec::new("panel", Size::new(242, 384), Policy::Expanding, Policy::Expanding).min_hint(Size::new(56, 56)).min_width(340)), 1);
        BoxLayout::v([11; 4], 6)
            .add_stretched(content.into_item(), 1)
            .widget(WidgetSpec::new("status", Size::new(20, 22), Policy::Preferred, Policy::Fixed))
            .into()
    }

    /// The scrolled panel's contents at the viewport's width.
    fn panel_layout(&self, ui: &Ui) -> Layout {
        const IDS: [[&str; 8]; 4] = [
            ["a_status", "a_fold", "a_start", "a_drift", "a_vs0", "a_vs", "a_rp0", "a_rp"],
            ["f_upper", "f_upper_stats", "f_lower", "f_lower_stats", "", "", "", ""],
            ["r_interval", "r_start_time", "r_start_freq", "r_avg", "r_bandwidth", "r_drift", "r_warning", ""],
            ["p_x", "p_mach", "p_va", "p_b", "", "", "", ""],
        ];
        let group = |id: &'static str, l: Layout| {
            Item::Container(Container { spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred), margins: [13, 27, 13, 13], layout: Box::new(l) })
        };
        let controls = BoxLayout::v([0; 4], 8)
            .widget(qt::label_spec(ui, "band_label", "Active Band:"))
            .widget(qt::combo_spec(ui, "band", &BANDS))
            .widget(qt::label_spec(ui, "speed_label", "Shock Speed:"))
            .widget(qt::combo_spec(ui, "speed", &SPEEDS))
            .widget(qt::button_spec(ui, "calculate", "Calculate", false));
        let mut root = BoxLayout::v([9; 4], 6).add(group("controls_group", controls.into()));
        for (k, labels) in self.labels().iter().enumerate() {
            let mut l = BoxLayout::v([10; 4], 6);
            for (j, html) in labels.iter().enumerate() {
                l = l.widget(richdoc::label_spec(ui, IDS[k][j], html, 12));
            }
            root = root.add(group(GROUP_IDS[k], l.into()));
        }
        root.stretch(1).into()
    }

    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Vec<WindowEvent> {
        qt::with_app_style(|| self.contents(ui, p));
        std::mem::take(&mut self.events)
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) {
        if self.canvas_dark != Some(p.dark) {
            self.canvas_dark = Some(p.dark);
            self.refresh_due = true;
        }
        if std::mem::take(&mut self.refresh_due) {
            self.refresh_plot(p.dark);
        }
        let r = ui.max_rect();
        let g = Self::main_layout().layout_window(Size::new(r.width().round() as i32, r.height().round() as i32));
        let rect = |id: &str| g.rect(id).to_egui(r.min);

        for icon in BandIcon::ALL {
            let enabled = self.enabled(icon);
            if self.tool_button(ui, rect(icon_id(icon)), icon, enabled, p) {
                match icon {
                    BandIcon::AddPoints => {
                        let on = !self.add_points;
                        self.set_add_points(on);
                    }
                    BandIcon::Undo => self.undo(),
                    BandIcon::Clear => self.clear_band(),
                    BandIcon::FitActive => self.fit_active(),
                    BandIcon::FitBoth => self.fit_both(),
                    BandIcon::SavePlot => self.save_plot(ui.ctx()),
                    BandIcon::Bvr => {
                        let on = !self.bvr;
                        self.set_bvr(on);
                    }
                    BandIcon::Settings => self.open_settings(),
                }
            }
        }
        if std::mem::take(&mut self.refresh_due) {
            self.refresh_plot(p.dark);
        }
        self.canvas.show(ui, rect("plot"));
        for e in self.canvas.take_events() {
            if let CanvasEvent::PlotClicked { x, y } = e {
                self.add_point(x, y);
            }
        }
        self.panel(ui, rect("panel"), p);
        let message = self.status.current(ui.ctx()).to_owned();
        status_bar(ui, rect("status"), &message, p);

        if let Some(d) = &mut self.settings {
            let events = d.show(ui.ctx(), p);
            for e in events {
                match e {
                    SettingsEvent::Preview(s) => self.apply_style(s, false),
                    SettingsEvent::Applied(s) => {
                        self.apply_style(s, true);
                        let applied = self.plot_style.clone();
                        if let Some(d) = &mut self.settings {
                            d.load_applied_style(&applied);
                        }
                        self.status.show("Type II graph style updated.", 2500);
                        self.emit_session_changed();
                    }
                    SettingsEvent::Closed => self.settings = None,
                }
            }
        }
        if let Some(n) = self.notices.first()
            && crate::ui::dialogs::message_box(ui.ctx(), n, p)
        {
            self.notices.remove(0);
        }
    }

    /// A `QToolButton` with an icon: transparent, a hover frame, the icon
    /// greyed when disabled. Returns true when clicked.
    fn tool_button(&mut self, ui: &Ui, rect: Rect, icon: BandIcon, enabled: bool, p: &Palette) -> bool {
        let resp = ui.interact(rect, Id::new(("t2_tool", icon_id(icon))), if enabled { Sense::click() } else { Sense::hover() });
        if enabled && resp.is_pointer_button_down_on() {
            crate::ui::widgets::qt_box(ui, rect, 10.0, p.pressed, p.border);
        } else if enabled && resp.hovered() {
            crate::ui::widgets::qt_box(ui, rect, 10.0, p.hover, p.border);
        }
        let ppp = ui.ctx().pixels_per_point();
        let side = (ICON * ppp).round() as u32;
        // A disabled icon is made over the palette's window colour.
        let disabled = (!enabled).then_some(p.qt.window);
        let key = (icon, p.dark, disabled.map(|c| c.to_array()), side);
        if let std::collections::hash_map::Entry::Vacant(e) = self.icons.entry(key)
            && let Some(img) = band_icons::render(icon, p.dark, side, disabled)
        {
            e.insert(ui.ctx().load_texture(format!("band-icon-{}-{}-{}", icon_id(icon), p.dark, !enabled), img, TextureOptions::NEAREST));
        }
        if let Some(tex) = self.icons.get(&key) {
            let off = ((BUTTON - ICON) / 2.0).floor();
            let at = pos2(((rect.left() + off) * ppp).round() / ppp, ((rect.top() + off) * ppp).round() / ppp);
            ui.painter().image(tex.id(), Rect::from_min_size(at, vec2(side as f32 / ppp, side as f32 / ppp)), Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
        }
        resp.on_hover_text(icon.label()).clicked()
    }

    /// The right-hand scroll area: a framed viewport in the palette's window
    /// colour (the full app's `QScrollArea`), the group boxes in it.
    fn panel(&mut self, ui: &mut Ui, area: Rect, p: &Palette) {
        let frame = darker(p.qt.window, 130);
        ui.painter().rect_filled(area, 0.0, frame);
        let viewport = Rect::from_min_max(area.min + vec2(1.0, 1.0), area.max - vec2(11.0, 1.0));
        ui.painter().rect_filled(viewport, 0.0, p.qt.window);
        let bar = Rect::from_min_max(pos2(viewport.right(), viewport.top()), pos2(area.right() - 1.0, viewport.bottom()));
        ui.painter().rect_filled(bar, 0.0, p.page_bg);
        let width = viewport.width().round() as i32;
        let layout = self.panel_layout(ui);
        let content_h = layout.height_for_width(width).max(viewport.height().round() as i32);
        let max_scroll = (content_h as f32 - viewport.height()).max(0.0);
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
            let groove = Rect::from_min_max(pos2(bar.left(), bar.top() + 4.0), pos2(bar.right(), bar.bottom() - 4.0));
            let len = (groove.height() * viewport.height() / content_h as f32).max(24.0);
            let top = groove.top() + (groove.height() - len) * self.scroll / max_scroll;
            let handle = Rect::from_min_size(pos2(groove.left(), top), vec2(groove.width(), len));
            let resp = ui.interact(handle, Id::new("t2_scroll_handle"), Sense::drag());
            if resp.dragged() {
                let per_px = max_scroll / (groove.height() - len).max(1.0);
                self.scroll = (self.scroll + resp.drag_delta().y * per_px).clamp(0.0, max_scroll);
            }
            ui.painter().rect_filled(handle, 5.0, p.border);
        }
        let mut g = Geometry::default();
        layout.set_geometry(IRect::new(0, 0, width, content_h), &mut g);
        let o = pos2(viewport.left(), (viewport.top() - self.scroll).round());
        let rect = |id: &str| g.rect(id).to_egui(o);
        let mut child = ui.new_child(UiBuilder::new().max_rect(viewport));
        child.set_clip_rect(viewport);
        let ui = &mut child;

        qt::group_box(ui, rect("controls_group"), "Controls", p);
        for (id, title) in GROUP_IDS.iter().zip(GROUP_TITLES) {
            qt::group_box(ui, rect(id), title, p);
        }
        qt::label(ui, rect("band_label"), "Active Band:", p.text);
        let bands: Vec<String> = BANDS.iter().map(|s| (*s).to_owned()).collect();
        if let Some(i) = qt::combo(ui, rect("band"), Id::new("t2_band"), &bands, Some(self.band), "", true, p) {
            self.set_band(i);
        }
        qt::label(ui, rect("speed_label"), "Shock Speed:", p.text);
        let inputs_ready = Self::inputs_ready(&self.inputs());
        let speeds: Vec<String> = SPEEDS.iter().map(|s| (*s).to_owned()).collect();
        if let Some(i) = qt::combo(ui, rect("speed"), Id::new("t2_speed"), &speeds, Some(self.speed), "", inputs_ready, p)
            && i != self.speed
        {
            self.set_speed(i);
        }
        let fits = self.upper_fit.is_some() && self.lower_fit.is_some();
        if qt::button(ui, rect("calculate"), Id::new("t2_calculate"), "Calculate", false, fits && inputs_ready, p).clicked() {
            self.calculate();
        }
        const IDS: [[&str; 8]; 4] = [
            ["a_status", "a_fold", "a_start", "a_drift", "a_vs0", "a_vs", "a_rp0", "a_rp"],
            ["f_upper", "f_upper_stats", "f_lower", "f_lower_stats", "", "", "", ""],
            ["r_interval", "r_start_time", "r_start_freq", "r_avg", "r_bandwidth", "r_drift", "r_warning", ""],
            ["p_x", "p_mach", "p_va", "p_b", "", "", "", ""],
        ];
        for (k, labels) in self.labels().iter().enumerate() {
            for (j, html) in labels.iter().enumerate() {
                let r = rect(IDS[k][j]);
                if r.intersects(viewport) {
                    richdoc::draw(ui, r, &Doc::parse(html, 12), p.text);
                }
            }
        }
    }

    /// `origin_figure` + `_save_plot`.
    fn save_plot(&mut self, ctx: &egui::Context) {
        let Some(path) = super::max_window::export_dialog(&format!("{}_Type_II_Band_Splitting", self.filename)) else { return };
        if cfg!(windows) && path.to_string_lossy().to_lowercase().starts_with("c:\\program files") {
            return self.notices.push(Notice {
                kind: NoticeKind::Warning,
                title: "Permission Denied".into(),
                text: "Windows does not allow saving files inside Program Files.\nPlease choose another folder such as Documents or Desktop.".into(),
            });
        }
        match self.save_figure(ctx, &path) {
            Ok(()) => {
                self.notices.push(Notice { kind: NoticeKind::Information, title: "Export Complete".into(), text: format!("Plot saved:\n{}", path.display()) });
                self.status.show("Export successful!", 3000);
            }
            Err(e) => {
                self.notices.push(Notice { kind: NoticeKind::Critical, title: "Export Failed".into(), text: format!("Could not save file:\n{e}") });
                self.status.show("Export failed!", 3000);
            }
        }
    }

    fn save_figure(&self, ctx: &egui::Context, path: &Path) -> Result<(), String> {
        let fig = self.origin_figure()?;
        crate::figure_export::save_figure_padded(ctx, &fig, path, 300.0, 0.08).map_err(|e| e.to_string())
    }

    /// `origin_figure`: the graph on show as an OriginPro graph.
    pub fn origin_figure(&self) -> Result<callisto_core::mpl::Figure, String> {
        use callisto_core::mpl::analysis_figures::GraphText;
        use callisto_core::mpl::type_ii_figures::{BandTrace, SpectrumImage, bvr_figure, spectrum_figure};
        let s = &self.current_style;
        let text = GraphText {
            tick_pt: s.tick_font_px as f64,
            label_pt: s.axis_label_font_px as f64,
            title_pt: s.title_font_px as f64,
            family: GraphText::family_id(&crate::fonts::normalize_font_family(&s.font_family)),
            title_bold: s.title_bold,
            title_italic: s.title_italic,
            axis_bold: s.axis_bold,
            axis_italic: s.axis_italic,
            ticks_bold: s.ticks_bold,
            ticks_italic: s.ticks_italic,
        };
        if self.bvr {
            let p = self.bvr_profile()?;
            if p.heights_rs.len() < 2 || p.magnetic_field_g.len() < 2 {
                return Err("B versus R plotting requires a valid magnetic-field profile.".into());
            }
            let (xs, ys) = bvr_fit_curve(&p);
            let title = format!("{}_Magnetic_Field_vs_Shock_Height", self.filename);
            return Ok(bvr_figure(&p.heights_rs, &p.magnetic_field_g, &xs, &ys, p.fit.a, p.fit.b, &title, text));
        }
        let sp = &self.spectrum;
        let freq_edges = frequency_edges(&sp.freqs, sp.frequency_step_mhz.unwrap_or(1.0));
        let time_edges = axis_edges(&sp.time, time_step(&sp.time));
        let levels = sp.display_levels.or_else(|| finite_data_limits(&sp.display.data));
        let image = match (freq_edges.first(), freq_edges.last(), time_edges.first(), time_edges.last()) {
            (Some(&f0), Some(&f1), Some(&t0), Some(&t1)) => Some(SpectrumImage {
                data: sp.display.clone(),
                extent: [t0, t1, f1, f0],
                cmap: sp.cmap,
                levels,
                colorbar_label: format!("Intensity [{}]", sp.unit),
            }),
            _ => None,
        };
        let band = |b: usize, label: &str| {
            let (marker, line, msize, lwidth) = if b == 0 {
                (&s.upper_marker_color, &s.upper_line_color, s.upper_marker_size, s.upper_line_width)
            } else {
                (&s.lower_marker_color, &s.lower_line_color, s.lower_marker_size, s.lower_line_width)
            };
            BandTrace {
                label: label.to_owned(),
                points: self.band_points(b).clone(),
                fit: self.fit_curve(b),
                marker_color: rgba(marker),
                line_color: rgba(line),
                // The settings are screen pixels; matplotlib sizes are points.
                marker_size: 0.75 * msize as f64,
                line_width: 0.75 * lwidth as f64,
            }
        };
        let bands = [band(0, "Upper band"), band(1, "Lower band")];
        Ok(spectrum_figure(image.as_ref(), &bands, &format!("{}_Type_II_Band_Splitting", self.filename), text))
    }

    /// Shows the window; returns its events and whether it closed (`closeEvent` emits the session).
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> (Vec<WindowEvent>, bool) {
        let size = crate::downloads::fit_to_screen(ctx, 1280.0, 760.0);
        let mut events = Vec::new();
        let mut closed = false;
        crate::downloads::window(ctx, "type_ii_window", TITLE, size, vec2(600.0, 400.0), p, |ui, close| {
            if std::mem::take(&mut self.focus_due) {
                ui.ctx().send_viewport_cmd(egui::ViewportCommand::Focus);
            }
            events = self.frame(ui, p);
            closed = close;
        });
        if closed {
            events.push(WindowEvent::SessionChanged(self.session_state()));
        }
        (events, closed)
    }
}

const GROUP_IDS: [&str; 4] = ["analyzer_group", "fits_group", "averaging_group", "plasma_group"];
const GROUP_TITLES: [&str; 4] = ["Analyzer Reference", "Fitted Bands", "Averaged Band-Splitting Quantities", "Plasma Parameters"];

fn icon_id(icon: BandIcon) -> &'static str {
    match icon {
        BandIcon::AddPoints => "t_add",
        BandIcon::Undo => "t_undo",
        BandIcon::Clear => "t_clear",
        BandIcon::FitActive => "t_fit_active",
        BandIcon::FitBoth => "t_fit_both",
        BandIcon::SavePlot => "t_save",
        BandIcon::Bvr => "t_bvr",
        BandIcon::Settings => "t_settings",
    }
}

/// `QColor::darker(factor)`.
fn darker(c: Color32, factor: u32) -> Color32 {
    let f = |v: u8| ((v as u32 * 100) / factor).min(255) as u8;
    Color32::from_rgb(f(c.r()), f(c.g()), f(c.b()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn values_format_like_the_full_app() {
        assert_eq!(fmt(Some(68.68406), 4, " MHz"), "68.6841 MHz");
        assert_eq!(fmt_pm(Some(-0.092014), Some(0.0033254), 4, " MHz/s"), "-0.0920 \u{b1} 0.0033 MHz/s");
        assert_eq!(fmt_pm(None, None, 4, " MHz/s"), "\u{2014}");
        assert_eq!(time_step(&[0.0, 0.25, 0.5, 0.75]), 0.25);
        assert_eq!(restore_points(&BandPoints::default(), true).len(), 1);
        assert!(restore_points(&BandPoints::default(), false).is_empty());
    }

    #[test]
    fn a_failed_lower_fit_leaves_the_labels_as_they_were() {
        let spectrum = Spectrum {
            display: Arc::new(Array2::filled(2, 2, 0.0)),
            freqs: vec![80.0, 40.0],
            time: vec![0.0, 1.0],
            unit: "Digits".into(),
            cmap: Colormap::by_name("inferno"),
            frequency_step_mhz: None,
            display_levels: None,
        };
        // A normalised session: each empty band comes back as one NaN point.
        let session = AnalysisSession {
            identity: Default::default(),
            source: Default::default(),
            max_intensity: None,
            analyzer: Default::default(),
            type_ii: TypeIIBlock::default(),
            ui: UiBlock::default(),
        };
        let mut w = TypeIIWindow::new(spectrum, "a.fit", &session, true, "Segoe UI", Vec::new());
        w.set_add_points(true);
        for (x, y) in [(300.0, 80.0), (340.0, 70.0), (380.0, 62.0)] {
            w.add_point(x, y);
        }
        w.set_band(1);
        w.add_point(300.0, 60.0);
        assert!(w.enabled(BandIcon::FitBoth), "the NaN point counts towards the two");
        let before = w.detail_html();
        w.fit_both();
        assert!(w.upper_fit.is_some() && w.lower_fit.is_none());
        assert_eq!(w.detail_html(), before);
        w.add_point(340.0, 52.0);
        assert!(w.detail_html()[8].contains("f<sub>u</sub>(t) = "), "the upper fit shows once the labels update");
    }
}
