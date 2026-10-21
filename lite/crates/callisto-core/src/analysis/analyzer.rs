//! The Analyzer's numbers (`src/ui/radio/dialogs/analyze_dialog.py`): the
//! power-law best fit of the maximum-intensity points, its drift rate and
//! errors, and the shock speed and height under every density model.

use std::collections::BTreeMap;

use super::density::{DensityModel, ShockParameters, shock_parameters};
use super::fit::{curve_fit_power_law, fit_mask, mean_squared_error, power_law, power_law_drift_rate, r2_score};
use crate::radio::numeric::{linspace, percentile_sorted};

/// Where the power law's time origin sits (`analysis_session.T0_MODES`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum T0Mode {
    #[default]
    FileStart,
    BurstOnset,
    Custom,
}

impl T0Mode {
    pub const ALL: [T0Mode; 3] = [Self::FileStart, Self::BurstOnset, Self::Custom];

    pub fn key(self) -> &'static str {
        match self {
            Self::FileStart => "file_start",
            Self::BurstOnset => "burst_onset",
            Self::Custom => "custom",
        }
    }

    /// `T0_CHOICES` labels.
    pub fn label(self) -> &'static str {
        match self {
            Self::FileStart => "File start",
            Self::BurstOnset => "Burst onset",
            Self::Custom => "Custom time",
        }
    }

    /// `normalize_t0_mode`.
    pub fn parse(value: &str) -> Self {
        let v = value.trim().to_lowercase();
        Self::ALL.into_iter().find(|m| m.key() == v).unwrap_or_default()
    }
}

/// `_resolve_time_seconds`: the given seconds when they match the channels,
/// otherwise 0.25 s per channel.
pub fn resolve_time_seconds(time_channels: &[f64], time_seconds: Option<&[f64]>) -> Vec<f64> {
    match time_seconds {
        Some(ts) if ts.len() == time_channels.len() => ts.to_vec(),
        _ => time_channels.iter().map(|c| c * 0.25).collect(),
    }
}

/// `_build_analysis_time_axis`.
pub fn analysis_time_axis(time_seconds: &[f64]) -> Vec<f64> {
    if time_seconds.is_empty() || !time_seconds.iter().any(|v| v.is_finite()) {
        return (0..time_seconds.len()).map(|i| i as f64).collect();
    }
    time_seconds.iter().map(|&v| if v.is_finite() { v } else { f64::NAN }).collect()
}

fn median(mut v: Vec<f64>) -> f64 {
    v.sort_by(f64::total_cmp);
    let n = v.len();
    if n % 2 == 1 { v[n / 2] } else { (v[n / 2 - 1] + v[n / 2]) / 2.0 }
}

/// `_burst_onset_t0`: one sample before the first point.
pub fn burst_onset_t0(time: &[f64], freq: &[f64]) -> f64 {
    if time.len() != freq.len() {
        return 0.0;
    }
    let mut ordered: Vec<f64> =
        time.iter().zip(freq).filter(|(t, f)| t.is_finite() && f.is_finite() && **f > 0.0).map(|(t, _)| *t).collect();
    if ordered.is_empty() {
        return 0.0;
    }
    ordered.sort_by(f64::total_cmp);
    ordered.dedup();
    let steps: Vec<f64> = ordered.windows(2).map(|w| w[1] - w[0]).filter(|s| *s > 0.0).collect();
    let step = if steps.is_empty() { 0.25 } else { median(steps) };
    ordered[0] - step
}

/// What `plot_fit` keeps: the fit, its samples and the drift along it.
#[derive(Clone, Debug, PartialEq)]
pub struct AnalyzerFit {
    pub a: f64,
    pub b: f64,
    pub std_errs: [f64; 2],
    pub r2: f64,
    pub rmse: f64,
    /// The t₀ the fit was made with (seconds from the file start).
    pub t0: f64,
    pub fit_mask: Vec<bool>,
    /// The fitted points, time since t₀.
    pub fit_time: Vec<f64>,
    pub fit_freq: Vec<f64>,
    /// 400 samples of the fit, time since t₀.
    pub time_fit: Vec<f64>,
    pub freq_fit: Vec<f64>,
    pub drift_vals: Vec<f64>,
    pub drift_errs: Vec<f64>,
    pub shock_calc_drift_vals: Vec<f64>,
    pub shock_calc_drift_errs: Vec<f64>,
    /// `np.std` of the residuals.
    pub freq_err: f64,
}

/// `plot_fit`: fit (or, given `params`, reuse) the power law in time since `t0`.
pub fn analyzer_fit(
    time: &[f64],
    freq: &[f64],
    t0: f64,
    params: Option<([f64; 2], Option<[f64; 2]>)>,
) -> Result<AnalyzerFit, String> {
    let shifted: Vec<f64> = time.iter().map(|t| t - t0).collect();
    let mask = fit_mask(&shifted, freq);
    if mask.iter().filter(|m| **m).count() < 2 {
        let place = if t0 == 0.0 { "time > 0 s".to_owned() } else { format!("time after t₀ = {t0:.2} s") };
        return Err(format!("Power-law fitting requires at least two points with {place} and frequency > 0 MHz."));
    }
    let fit_time: Vec<f64> = shifted.iter().zip(&mask).filter(|(_, m)| **m).map(|(v, _)| *v).collect();
    let fit_freq: Vec<f64> = freq.iter().zip(&mask).filter(|(_, m)| **m).map(|(v, _)| *v).collect();
    let (a, b, std_errs) = match params {
        None => {
            let ([a, b], std) = curve_fit_power_law(&fit_time, &fit_freq)?;
            (a, b, std)
        }
        Some(([a, b], std)) => (a, b.abs(), std.unwrap_or([f64::NAN, f64::NAN])),
    };
    let (tmin, tmax) = fit_time.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| (lo.min(v), hi.max(v)));
    let time_fit = linspace(tmin, tmax, 400);
    let freq_fit: Vec<f64> = time_fit.iter().map(|&t| power_law(t, a, b)).collect();
    let predicted: Vec<f64> = fit_time.iter().map(|&t| power_law(t, a, b)).collect();
    let r2 = r2_score(&fit_freq, &predicted);
    let rmse = mean_squared_error(&fit_freq, &predicted).sqrt();

    let drift_vals: Vec<f64> = fit_time.iter().map(|&t| power_law_drift_rate(t, a, b)).collect();
    let shock_calc_drift_vals: Vec<f64> = time_fit.iter().map(|&t| power_law_drift_rate(t, a, b)).collect();
    let residuals: Vec<f64> = fit_freq.iter().zip(&predicted).map(|(f, p)| f - p).collect();
    let n = residuals.len() as f64;
    let mean = residuals.iter().sum::<f64>() / n;
    let freq_err = (residuals.iter().map(|r| (r - mean) * (r - mean)).sum::<f64>() / n).sqrt();
    let b_err_denom = b.abs().max(1e-9);
    let rel = ((std_errs[0] / a).powi(2) + (std_errs[1] / b_err_denom).powi(2)).sqrt();
    let drift_errs = drift_vals.iter().map(|d| d.abs() * rel).collect();
    let shock_calc_drift_errs = shock_calc_drift_vals.iter().map(|d| d.abs() * rel).collect();
    Ok(AnalyzerFit {
        a,
        b,
        std_errs,
        r2,
        rmse,
        t0,
        fit_mask: mask,
        fit_time,
        fit_freq,
        time_fit,
        freq_fit,
        drift_vals,
        drift_errs,
        shock_calc_drift_vals,
        shock_calc_drift_errs,
        freq_err,
    })
}

/// `_shock_summary` (`analysis_session.SHOCK_SUMMARY_FIELDS` and the model).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ShockSummary {
    pub avg_freq_mhz: Option<f64>,
    pub avg_freq_err_mhz: Option<f64>,
    pub avg_drift_mhz_s: Option<f64>,
    pub avg_drift_err_mhz_s: Option<f64>,
    pub start_freq_mhz: Option<f64>,
    pub start_freq_err_mhz: Option<f64>,
    pub initial_shock_speed_km_s: Option<f64>,
    pub initial_shock_speed_err_km_s: Option<f64>,
    pub initial_shock_height_rs: Option<f64>,
    pub initial_shock_height_err_rs: Option<f64>,
    pub avg_shock_speed_km_s: Option<f64>,
    pub avg_shock_speed_err_km_s: Option<f64>,
    pub avg_shock_height_rs: Option<f64>,
    pub avg_shock_height_err_rs: Option<f64>,
    pub fold: i64,
    pub fundamental: bool,
    pub harmonic: bool,
    pub harmonic_number: i64,
    pub observed_avg_freq_mhz: Option<f64>,
    pub observed_avg_drift_mhz_s: Option<f64>,
    pub observed_start_freq_mhz: Option<f64>,
    pub density_model: Option<DensityModel>,
}

impl ShockSummary {
    /// Whether any number is set (`has_analyzer` in `normalize_session`).
    pub fn has_values(&self) -> bool {
        [
            self.avg_freq_mhz,
            self.avg_freq_err_mhz,
            self.avg_drift_mhz_s,
            self.avg_drift_err_mhz_s,
            self.start_freq_mhz,
            self.start_freq_err_mhz,
            self.initial_shock_speed_km_s,
            self.initial_shock_speed_err_km_s,
            self.initial_shock_height_rs,
            self.initial_shock_height_err_rs,
            self.avg_shock_speed_km_s,
            self.avg_shock_speed_err_km_s,
            self.avg_shock_height_rs,
            self.avg_shock_height_err_rs,
            self.observed_avg_freq_mhz,
            self.observed_avg_drift_mhz_s,
            self.observed_start_freq_mhz,
        ]
        .iter()
        .any(Option::is_some)
    }
}

/// `_update_shock_parameters`: the summary, the per-model table and the
/// arrays the extra plots use.
#[derive(Clone, Debug, PartialEq)]
pub struct ShockUpdate {
    pub summary: ShockSummary,
    pub comparison: BTreeMap<DensityModel, ShockParameters>,
    /// The selected model's speed and height along the fit.
    pub shock_speed: Vec<f64>,
    pub shock_height: Vec<f64>,
    pub shock_freq_values: Vec<f64>,
    pub shock_drift_vals: Vec<f64>,
}

fn mean(v: &[f64]) -> f64 {
    v.iter().sum::<f64>() / v.len() as f64
}

fn std(v: &[f64]) -> f64 {
    let m = mean(v);
    (v.iter().map(|x| (x - m) * (x - m)).sum::<f64>() / v.len() as f64).sqrt()
}

pub fn update_shock_parameters(
    fit: &AnalyzerFit,
    max_intensity_freqs: &[f64],
    fundamental: bool,
    harmonic: bool,
    fold: i64,
    model: DensityModel,
) -> ShockUpdate {
    let observed_freq_values = &fit.fit_freq;
    let observed_drift_vals = &fit.drift_vals;
    let max_freqs: Vec<f64> = max_intensity_freqs.iter().copied().filter(|v| v.is_finite() && *v > 0.0).collect();
    let harmonic_number = if harmonic { 2.0 } else { 1.0 };
    let shock_freq_values: Vec<f64> = fit.freq_fit.iter().map(|v| v / harmonic_number).collect();
    let shock_drift_vals: Vec<f64> = fit.shock_calc_drift_vals.iter().map(|v| v / harmonic_number).collect();
    let shock_drift_errs: Vec<f64> = fit.shock_calc_drift_errs.iter().map(|v| v / harmonic_number).collect();
    let shock_freq_err = fit.freq_err / harmonic_number;
    let comparison: BTreeMap<DensityModel, ShockParameters> = DensityModel::ALL
        .into_iter()
        .map(|m| {
            (
                m,
                shock_parameters(
                    &shock_freq_values,
                    &shock_drift_vals,
                    &shock_drift_errs,
                    shock_freq_err,
                    m,
                    fold as f64,
                    90.0,
                ),
            )
        })
        .collect();
    let selected = &comparison[&model];
    let avg_freq_values: Vec<f64> = if max_freqs.is_empty() {
        shock_freq_values.clone()
    } else {
        max_freqs.iter().map(|v| v / harmonic_number).collect()
    };
    let avg_freq = mean(&avg_freq_values);
    let avg_freq_err = std(&avg_freq_values) / (avg_freq_values.len() as f64).sqrt();
    let avg_drift = mean(&shock_drift_vals);
    let avg_drift_err = std(&shock_drift_vals) / (shock_drift_vals.len() as f64).sqrt();
    let mut sorted_obs = observed_freq_values.clone();
    sorted_obs.sort_by(f64::total_cmp);
    let summary = ShockSummary {
        avg_freq_mhz: Some(avg_freq),
        avg_freq_err_mhz: Some(avg_freq_err),
        avg_drift_mhz_s: Some(avg_drift),
        avg_drift_err_mhz_s: Some(avg_drift_err),
        start_freq_mhz: Some(selected.start_freq_mhz),
        start_freq_err_mhz: Some(shock_freq_err),
        initial_shock_speed_km_s: Some(selected.initial_shock_speed_km_s),
        initial_shock_speed_err_km_s: Some(selected.initial_shock_speed_err_km_s),
        initial_shock_height_rs: Some(selected.initial_shock_height_rs),
        initial_shock_height_err_rs: Some(selected.initial_shock_height_err_rs),
        avg_shock_speed_km_s: Some(selected.avg_shock_speed_km_s),
        avg_shock_speed_err_km_s: Some(selected.avg_shock_speed_err_km_s),
        avg_shock_height_rs: Some(selected.avg_shock_height_rs),
        avg_shock_height_err_rs: Some(selected.avg_shock_height_err_rs),
        fold,
        fundamental,
        harmonic,
        harmonic_number: harmonic_number as i64,
        observed_avg_freq_mhz: Some(if max_freqs.is_empty() { mean(observed_freq_values) } else { mean(&max_freqs) }),
        observed_avg_drift_mhz_s: Some(mean(observed_drift_vals)),
        observed_start_freq_mhz: percentile_sorted(&sorted_obs, 90.0),
        density_model: Some(model),
    };
    ShockUpdate {
        summary,
        shock_speed: selected.shock_speed_km_s.clone(),
        shock_height: selected.shock_height_rs.clone(),
        comparison,
        shock_freq_values,
        shock_drift_vals,
    }
}
