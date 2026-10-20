//! The power law `f = a · x^(-b)` and its fit, as the Analyzer
//! (`AnalyzeDialog.plot_fit`) and the Type II band-splitting
//! (`type_ii_band_splitting.fit_power_law`) make it: `curve_fit` from a
//! log-log line, `a ≥ 1e-12`, `b ≥ 1e-9`, at most 10 000 evaluations.

use super::lsq::{covariance, least_squares_trf, polyfit_linear};

/// `a * x ** (-b)`.
pub fn power_law(x: f64, a: f64, b: f64) -> f64 {
    a * x.powf(-b)
}

/// `-a * b * x ** (-b - 1)`.
pub fn power_law_drift_rate(x: f64, a: f64, b: f64) -> f64 {
    -a * b * x.powf(-b - 1.0)
}

/// `power_law_fit_mask`: finite, positive time and frequency.
pub fn fit_mask(x: &[f64], y: &[f64]) -> Vec<bool> {
    x.iter().zip(y).map(|(&t, &f)| t.is_finite() && f.is_finite() && t > 0.0 && f > 0.0).collect()
}

/// `initial_power_law_guess`.
pub fn initial_guess(x: &[f64], y: &[f64]) -> (f64, f64) {
    let mask = fit_mask(x, y);
    if mask.iter().filter(|m| **m).count() >= 2 {
        let lx: Vec<f64> = x.iter().zip(&mask).filter(|(_, m)| **m).map(|(v, _)| v.ln()).collect();
        let ly: Vec<f64> = y.iter().zip(&mask).filter(|(_, m)| **m).map(|(v, _)| v.ln()).collect();
        if let Some((slope, intercept)) = polyfit_linear(&lx, &ly) {
            let a0 = intercept.exp();
            let b0 = (-slope).max(1e-9);
            if a0.is_finite() && a0 > 0.0 && b0.is_finite() {
                return (a0, b0);
            }
        }
    }
    let positive = y.iter().copied().filter(|v| v.is_finite() && *v > 0.0);
    match positive.fold(None, |m: Option<f64>, v| Some(m.map_or(v, |m| m.max(v)))) {
        Some(max) => (max, 0.5),
        None => (1.0, 0.5),
    }
}

/// `curve_fit(power_law, x, y, p0=initial_guess, bounds=([1e-12, 1e-9],
/// [inf, inf]), maxfev=10000)` on already-masked data: `(a, b)` and the
/// standard errors `sqrt(diag(pcov))`.
pub fn curve_fit_power_law(x: &[f64], y: &[f64]) -> Result<([f64; 2], [f64; 2]), String> {
    let p0 = initial_guess(x, y);
    let residuals = |p: &[f64]| -> Vec<f64> { x.iter().zip(y).map(|(&t, &f)| p[0] * t.powf(-p[1]) - f).collect() };
    let lb = [1e-12, 1e-9];
    let ub = [f64::INFINITY, f64::INFINITY];
    let res = least_squares_trf(&residuals, &[p0.0, p0.1], &lb, &ub, 10_000)?;
    if res.status <= 0 {
        return Err("Optimal parameters not found: The maximum number of function evaluations is exceeded.".into());
    }
    let pcov = covariance(&res);
    let std = [pcov.at(0, 0).sqrt(), pcov.at(1, 1).sqrt()];
    Ok(([res.x[0], res.x[1]], std))
}

/// `sklearn.metrics.r2_score` (single output, `force_finite=True`).
pub fn r2_score(y_true: &[f64], y_pred: &[f64]) -> f64 {
    let n = y_true.len() as f64;
    let mean = y_true.iter().sum::<f64>() / n;
    let numerator: f64 = y_true.iter().zip(y_pred).map(|(t, p)| (t - p) * (t - p)).sum();
    let denominator: f64 = y_true.iter().map(|t| (t - mean) * (t - mean)).sum();
    if denominator == 0.0 {
        return if numerator == 0.0 { 1.0 } else { 0.0 };
    }
    1.0 - numerator / denominator
}

/// `sklearn.metrics.mean_squared_error`.
pub fn mean_squared_error(y_true: &[f64], y_pred: &[f64]) -> f64 {
    y_true.iter().zip(y_pred).map(|(t, p)| (t - p) * (t - p)).sum::<f64>() / y_true.len() as f64
}

/// `fit_power_law` (Type II): the fit and its quality on the fitted points.
#[derive(Clone, Debug, PartialEq)]
pub struct PowerLawFit {
    pub a: f64,
    pub b: f64,
    pub std_errs: [f64; 2],
    pub r2: f64,
    pub rmse: f64,
    pub point_count: usize,
    pub fit_time: Vec<f64>,
    pub fit_freq: Vec<f64>,
}

/// `type_ii_band_splitting.fit_power_law`.
pub fn fit_power_law(time: &[f64], freq: &[f64]) -> Result<PowerLawFit, String> {
    let mask = fit_mask(time, freq);
    if mask.iter().filter(|m| **m).count() < 2 {
        return Err("Power-law fitting requires at least two points with time > 0 s and frequency > 0 MHz.".into());
    }
    let fit_time: Vec<f64> = time.iter().zip(&mask).filter(|(_, m)| **m).map(|(v, _)| *v).collect();
    let fit_freq: Vec<f64> = freq.iter().zip(&mask).filter(|(_, m)| **m).map(|(v, _)| *v).collect();
    let ([a, b], std_errs) = curve_fit_power_law(&fit_time, &fit_freq)?;
    let b = b.abs();
    let predicted: Vec<f64> = fit_time.iter().map(|&t| power_law(t, a, b)).collect();
    let residuals: Vec<f64> = fit_freq.iter().zip(&predicted).map(|(f, p)| f - p).collect();
    let rmse = (residuals.iter().map(|r| r * r).sum::<f64>() / residuals.len() as f64).sqrt();
    let ss_res: f64 = residuals.iter().map(|r| r * r).sum();
    let mean = fit_freq.iter().sum::<f64>() / fit_freq.len() as f64;
    let ss_tot: f64 = fit_freq.iter().map(|f| (f - mean) * (f - mean)).sum();
    let r2 = if ss_tot > 0.0 {
        1.0 - ss_res / ss_tot
    } else if ss_res <= 1e-12 {
        1.0
    } else {
        0.0
    };
    Ok(PowerLawFit { a, b, std_errs, r2, rmse, point_count: fit_time.len(), fit_time, fit_freq })
}

/// `power_law_label(a, b, prefix=..., symbol=..., variable=...)`: the text
/// before the superscript and the exponent, e.g. ("Best Fit: f = 123.45 x", "-0.85").
pub fn power_law_label(a: f64, b: f64, prefix: &str, symbol: &str, variable: &str) -> (String, String) {
    let head = if prefix.is_empty() { String::new() } else { format!("{prefix}: ") };
    (format!("{head}{symbol} = {a:.2} {variable}"), format!("{:.2}", -b))
}
