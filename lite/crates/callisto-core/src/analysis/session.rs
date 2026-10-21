//! The analysis session the maximum-intensity window, the Analyzer and the
//! Type II band-splitting window share (`src/backend/session/analysis_session.py`
//! and the main window's `_on_analysis_session_changed`), and the sidebar's
//! Analysis Summary text.

use super::analyzer::{ShockSummary, T0Mode};
use super::density::DensityModel;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct SessionSource {
    pub filename: String,
    pub is_combined: bool,
    pub combined_mode: Option<String>,
    pub combined_sources: Vec<String>,
    pub shape: Option<[usize; 2]>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MaxIntensityBlock {
    pub time_channels: Vec<f64>,
    pub time_seconds: Option<Vec<f64>>,
    pub freqs: Vec<f64>,
    pub fundamental: bool,
    pub harmonic: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FitParams {
    pub a: f64,
    pub b: f64,
    pub std_errs: [Option<f64>; 2],
    pub r2: Option<f64>,
    pub rmse: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct AnalyzerBlock {
    pub fit_params: Option<FitParams>,
    pub fold: i64,
    pub shock_summary: ShockSummary,
    pub density_model: DensityModel,
    pub t0_mode: T0Mode,
    pub t0_s: f64,
}

impl Default for AnalyzerBlock {
    fn default() -> Self {
        Self {
            fit_params: None,
            fold: 1,
            shock_summary: ShockSummary { fold: 1, fundamental: true, harmonic_number: 1, ..Default::default() },
            density_model: DensityModel::Newkirk,
            t0_mode: T0Mode::FileStart,
            t0_s: 0.0,
        }
    }
}

impl AnalyzerBlock {
    /// `has_analyzer` in `normalize_session`.
    pub fn has_content(&self) -> bool {
        self.fit_params.is_some() || self.shock_summary.has_values()
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct BandPoints {
    pub time_seconds: Vec<f64>,
    pub freqs: Vec<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BandFitState {
    pub fit: FitParams,
    pub point_count: Option<usize>,
}

/// `type_ii.analysis_inputs`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct TypeIIInputs {
    pub initial_shock_speed_km_s: Option<f64>,
    pub avg_shock_speed_km_s: Option<f64>,
    pub initial_shock_height_rs: Option<f64>,
    pub avg_shock_height_rs: Option<f64>,
    pub start_freq_mhz: Option<f64>,
    pub avg_drift_mhz_s: Option<f64>,
    pub avg_drift_err_mhz_s: Option<f64>,
    pub fold: i64,
    /// "initial" or "average".
    pub average_speed: bool,
}

impl TypeIIInputs {
    pub fn has_values(&self) -> bool {
        [
            self.initial_shock_speed_km_s,
            self.avg_shock_speed_km_s,
            self.initial_shock_height_rs,
            self.avg_shock_height_rs,
            self.start_freq_mhz,
            self.avg_drift_mhz_s,
            self.avg_drift_err_mhz_s,
        ]
        .iter()
        .any(Option::is_some)
    }
}

/// `TYPE_II_PLOT_STYLE_DEFAULTS`.
#[derive(Clone, Debug, PartialEq)]
pub struct TypeIIPlotStyle {
    pub font_family: String,
    pub title_font_px: i64,
    pub axis_label_font_px: i64,
    pub tick_font_px: i64,
    pub title_bold: bool,
    pub title_italic: bool,
    pub axis_bold: bool,
    pub axis_italic: bool,
    pub ticks_bold: bool,
    pub ticks_italic: bool,
    pub upper_line_color: String,
    pub upper_marker_color: String,
    pub upper_line_width: i64,
    pub upper_marker_size: i64,
    pub lower_line_color: String,
    pub lower_marker_color: String,
    pub lower_line_width: i64,
    pub lower_marker_size: i64,
    pub bvr_line_color: String,
    pub bvr_marker_color: String,
    pub bvr_line_width: i64,
    pub bvr_marker_size: i64,
}

impl Default for TypeIIPlotStyle {
    fn default() -> Self {
        Self {
            font_family: String::new(),
            title_font_px: 14,
            axis_label_font_px: 12,
            tick_font_px: 11,
            title_bold: false,
            title_italic: false,
            axis_bold: false,
            axis_italic: false,
            ticks_bold: false,
            ticks_italic: false,
            upper_line_color: "#ff5a1f".into(),
            upper_marker_color: "#ff8c42".into(),
            upper_line_width: 2,
            upper_marker_size: 9,
            lower_line_color: "#0ea5e9".into(),
            lower_marker_color: "#38bdf8".into(),
            lower_line_width: 2,
            lower_marker_size: 9,
            bvr_line_color: "#f59e0b".into(),
            bvr_marker_color: "#34d399".into(),
            bvr_line_width: 2,
            bvr_marker_size: 8,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct TypeIIBlock {
    pub upper: BandPoints,
    pub lower: BandPoints,
    pub upper_fit: Option<BandFitState>,
    pub lower_fit: Option<BandFitState>,
    pub analysis_inputs: TypeIIInputs,
    pub fold: i64,
    pub results: Option<super::type_ii::TypeIIResults>,
    pub plot_style: TypeIIPlotStyle,
}

impl TypeIIBlock {
    /// `_has_type_ii_payload`.
    pub fn has_content(&self) -> bool {
        !self.upper.time_seconds.is_empty()
            || !self.lower.time_seconds.is_empty()
            || self.upper_fit.is_some()
            || self.lower_fit.is_some()
            || self.results.is_some()
            || self.analysis_inputs.has_values()
            || self.plot_style != TypeIIPlotStyle::default()
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct UiBlock {
    pub restore_max_window: bool,
    pub restore_analyzer_window: bool,
    pub restore_type_ii_window: bool,
    pub auto_outlier_cleaned: bool,
    pub auto_removed_count: usize,
}

/// `version`, `analysis_run_id` and `updated_at`. Empty until the main
/// window normalises the session: windows' payloads carry none, so each of
/// their changes gets a new run id, as in the full app.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SessionIdentity {
    pub version: i64,
    pub run_id: String,
    pub updated_at: String,
}

impl SessionIdentity {
    /// `normalize_session`'s defaults: version 1, `str(uuid4())`, now.
    pub fn ensured(mut self) -> Self {
        if self.version == 0 {
            self.version = 1;
        }
        if self.run_id.is_empty() {
            let h = super::annotations::new_id();
            self.run_id = format!("{}-{}-{}-{}-{}", &h[..8], &h[8..12], &h[12..16], &h[16..20], &h[20..]);
        }
        if self.updated_at.is_empty() {
            self.updated_at = super::annotations::now_iso();
        }
        self
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct AnalysisSession {
    pub source: SessionSource,
    pub max_intensity: Option<MaxIntensityBlock>,
    pub analyzer: AnalyzerBlock,
    pub type_ii: TypeIIBlock,
    pub ui: UiBlock,
    pub identity: SessionIdentity,
}

impl AnalysisSession {
    /// Whether `normalize_session` would keep it (anything to restore).
    pub fn is_meaningful(&self) -> bool {
        self.max_intensity.as_ref().is_some_and(|m| !m.time_channels.is_empty() && m.time_channels.len() == m.freqs.len())
            || self.analyzer.has_content()
            || self.type_ii.has_content()
    }

    /// `_merge_seed_with_existing_analysis`: the seed's points with the
    /// existing analyzer, Type II state and window flags.
    pub fn merged_with(seed: AnalysisSession, existing: Option<&AnalysisSession>) -> AnalysisSession {
        let Some(existing) = existing else { return seed };
        let mut ui = seed.ui.clone();
        ui.restore_analyzer_window = existing.ui.restore_analyzer_window;
        ui.restore_type_ii_window = existing.ui.restore_type_ii_window;
        AnalysisSession {
            source: seed.source,
            max_intensity: seed.max_intensity,
            analyzer: if existing.analyzer.has_content() { existing.analyzer.clone() } else { seed.analyzer },
            type_ii: existing.type_ii.clone(),
            ui,
            identity: SessionIdentity::default(),
        }
    }

    /// `_refresh_analysis_summary_panel`.
    pub fn summary_text(&self) -> String {
        let a = &self.analyzer;
        let shock = &a.shock_summary;
        let mut lines = vec!["Session restored and synced.".to_owned()];
        if let Some(fit) = a.fit_params {
            let variable = if a.t0_s == 0.0 { "x".to_owned() } else { format!("(x - {:.2})", a.t0_s) };
            lines.push(format!("Fit: f(x) = {:.2} * {variable}^-{:.2}", fit.a, fit.b.abs()));
            if let Some(r2) = fit.r2 {
                lines.push(format!("R2: {r2:.4}"));
            }
        }
        if a.density_model != DensityModel::Newkirk {
            lines.push(format!("Density model: {}", a.density_model.label()));
        }
        lines.push(format!("Fold: {}", a.fold));
        if let Some(v) = shock.avg_shock_speed_km_s {
            lines.push(format!("Avg shock speed: {v:.2} km/s"));
        }
        if let Some(v) = shock.avg_shock_height_rs {
            lines.push(format!("Avg shock height: {v:.3} Rs"));
        }
        if let Some(r) = &self.type_ii.results {
            lines.push(format!("Type II X: {:.4}", r.compression_ratio));
            lines.push(format!("Type II M_A: {:.4}", r.alfven_mach_number));
            lines.push(format!("Type II V_A: {:.2} km/s", r.alfven_speed_km_s));
            lines.push(format!("Type II B: {:.4} G", r.magnetic_field_g));
        }
        lines.join("\n")
    }
}
