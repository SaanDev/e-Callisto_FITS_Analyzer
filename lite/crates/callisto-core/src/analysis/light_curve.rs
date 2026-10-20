//! A light curve drawn over the spectrogram (`MainWindow._build_light_curve_overlay`):
//! one frequency row's intensity, offset from its first value, scaled by its
//! 95th-percentile swing and laid along that frequency.

use crate::radio::numeric::{nanmedian, nanpercentile};

/// `LIGHT_CURVE_SETTINGS_DEFAULTS`, normalized.
#[derive(Clone, Debug, PartialEq)]
pub struct LightCurveSettings {
    pub multiple: bool,
    pub line_color: String,
    pub line_width: f64,
    pub show_frequency_label: bool,
    pub vertical_scale: f64,
    pub opacity: f64,
    /// "solid", "dashed" or "dotted".
    pub line_style: String,
}

impl Default for LightCurveSettings {
    fn default() -> Self {
        Self {
            multiple: false,
            line_color: "#00e5ff".into(),
            line_width: 2.0,
            show_frequency_label: true,
            vertical_scale: 1.0,
            opacity: 0.95,
            line_style: "solid".into(),
        }
    }
}

impl LightCurveSettings {
    /// `_normalize_light_curve_settings`: clamped and with a valid style.
    pub fn normalized(mut self) -> Self {
        self.line_width = if self.line_width.is_finite() { self.line_width.clamp(0.5, 12.0) } else { 2.0 };
        self.vertical_scale = if self.vertical_scale.is_finite() { self.vertical_scale.clamp(0.1, 5.0) } else { 1.0 };
        self.opacity = if self.opacity.is_finite() { self.opacity.clamp(0.1, 1.0) } else { 0.95 };
        let style = self.line_style.to_lowercase();
        self.line_style = if ["solid", "dashed", "dotted"].contains(&style.as_str()) { style } else { "solid".into() };
        self
    }
}

/// `LIGHT_CURVE_COLOR_PALETTE`.
pub const COLOR_PALETTE: [&str; 8] = ["#00e5ff", "#ffb000", "#7bd88f", "#ff66c4", "#ffffff", "#c6a4ff", "#f95d6a", "#00d084"];

/// `_light_curve_color_for_index`: the chosen colour first, then the palette without it.
pub fn color_for_index(selected: &str, index: usize) -> String {
    let mut palette = vec![selected.to_owned()];
    palette.extend(COLOR_PALETTE.iter().filter(|c| !c.eq_ignore_ascii_case(selected)).map(|c| (*c).to_owned()));
    palette[index % palette.len()].clone()
}

/// `_light_curve_frequency_limits`.
pub fn frequency_limits(freqs: &[f64]) -> Option<(f64, f64)> {
    let finite: Vec<f64> = freqs.iter().copied().filter(|v| v.is_finite()).collect();
    if finite.is_empty() {
        return None;
    }
    Some((finite.iter().copied().fold(f64::INFINITY, f64::min), finite.iter().copied().fold(f64::NEG_INFINITY, f64::max)))
}

/// `_nearest_light_curve_frequency`: the row and its frequency.
pub fn nearest_frequency(freqs: &[f64], requested: f64) -> Option<(usize, f64)> {
    if !requested.is_finite() {
        return None;
    }
    let mut best: Option<(usize, f64)> = None;
    for (i, &f) in freqs.iter().enumerate() {
        if !f.is_finite() {
            continue;
        }
        let d = (f - requested).abs();
        if best.is_none_or(|(_, bd)| d < bd) {
            best = Some((i, d));
        }
    }
    best.map(|(i, _)| (i, freqs[i]))
}

/// `_light_curve_visual_scale`: 8 % of the span, at least 1.5 channel steps
/// (capped at 20 % of the span).
pub fn visual_scale(freqs: &[f64]) -> f64 {
    let Some((lo, hi)) = frequency_limits(freqs) else { return 1.0 };
    let mut span = (hi - lo).abs();
    if !span.is_finite() || span <= 0.0 {
        span = 1.0;
    }
    let mut unique: Vec<f64> = freqs.iter().copied().filter(|v| v.is_finite()).collect();
    unique.sort_by(f64::total_cmp);
    unique.dedup();
    let diffs: Vec<f64> = unique.windows(2).map(|w| w[1] - w[0]).filter(|d| d.is_finite() && *d > 0.0).collect();
    let step = if diffs.is_empty() { None } else { nanmedian(diffs.iter().copied()) };
    let mut scale = span * 0.08;
    if let Some(step) = step
        && step.is_finite()
        && step > 0.0
    {
        scale = scale.max((step * 1.5).min(span * 0.2));
    }
    if scale.is_finite() && scale > 0.0 { scale } else { 1.0 }
}

/// What the canvas draws for one light curve.
#[derive(Clone, Debug, PartialEq)]
pub struct LightCurveOverlay {
    pub frequency_mhz: f64,
    pub requested_mhz: f64,
    pub row_index: usize,
    pub time: Vec<f64>,
    pub y: Vec<f64>,
    pub color: String,
    pub line_width: f64,
    pub opacity: f64,
    pub line_style: String,
    pub show_label: bool,
    pub label: String,
    pub label_x: f64,
    pub label_y: f64,
}

/// `_build_light_curve_overlay` from the displayed row `series` at `row_index`.
#[allow(clippy::too_many_arguments)]
pub fn build_overlay(
    time: &[f64],
    series: &[f64],
    freqs: &[f64],
    requested: f64,
    row_index: usize,
    actual: f64,
    settings: &LightCurveSettings,
    curve_index: usize,
) -> Result<LightCurveOverlay, String> {
    let n = time.len().min(series.len());
    if n == 0 {
        return Err("The current plot has no time samples.".into());
    }
    let (time, series) = (&time[..n], &series[..n]);
    let Some(first) = series.iter().copied().find(|v| v.is_finite()) else {
        return Err(format!("No finite intensity values at {actual:.3} MHz."));
    };
    let delta: Vec<f64> = series.iter().map(|v| v - first).collect();
    let finite_abs: Vec<f64> = delta.iter().copied().filter(|v| v.is_finite()).map(f64::abs).collect();
    let normalized: Vec<f64> = if finite_abs.is_empty() {
        vec![0.0; n]
    } else {
        let mut amplitude = nanpercentile(finite_abs.iter().copied(), 95.0).unwrap_or(f64::NAN);
        if !amplitude.is_finite() || amplitude <= 1e-12 {
            amplitude = finite_abs.iter().copied().fold(f64::NAN, |m, v| if m.is_nan() || v > m { v } else { m });
        }
        if !amplitude.is_finite() || amplitude <= 1e-12 {
            vec![0.0; n]
        } else {
            // np.clip keeps NaN.
            delta.iter().map(|d| if d.is_nan() { f64::NAN } else { (d / amplitude).clamp(-1.5, 1.5) }).collect()
        }
    };
    let base_scale = visual_scale(freqs);
    let mut y: Vec<f64> = normalized.iter().map(|v| actual + v * base_scale * settings.vertical_scale).collect();
    for i in 0..n {
        if !time[i].is_finite() || !series[i].is_finite() {
            y[i] = f64::NAN;
        }
    }
    let first_plot = (0..n).find(|&i| time[i].is_finite() && y[i].is_finite()).unwrap_or(0);
    let label_offset = (base_scale * 0.14).max(0.05);
    Ok(LightCurveOverlay {
        frequency_mhz: actual,
        requested_mhz: requested,
        row_index,
        time: time.to_vec(),
        label_x: time[first_plot],
        label_y: y[first_plot] + label_offset,
        y,
        color: color_for_index(&settings.line_color, curve_index),
        line_width: settings.line_width,
        opacity: settings.opacity,
        line_style: settings.line_style.clone(),
        show_label: settings.show_frequency_label,
        label: format!("{actual:.3} MHz"),
    })
}
