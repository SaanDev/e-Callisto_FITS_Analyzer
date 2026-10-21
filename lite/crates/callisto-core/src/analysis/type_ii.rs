//! Type II band-splitting (`src/backend/radio/type_ii_band_splitting.py`):
//! the upper and lower lanes' power laws give the compression ratio, the
//! Alfvén Mach number, the Alfvén speed and the magnetic field, and a
//! magnetic-field profile against shock height.

use super::fit::{PowerLawFit, fit_mask, fit_power_law, power_law, power_law_drift_rate};
use crate::radio::numeric::linspace;

pub const MU_0: f64 = 4.0 * std::f64::consts::PI * 1e-7;
pub const PROTON_MASS_KG: f64 = 1.67262192369e-27;
pub const PLASMA_FREQ_COEFF_MHZ: f64 = 0.00898;

/// A lane's fitted power law, as the session keeps it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BandFit {
    pub a: f64,
    pub b: f64,
}

impl From<&PowerLawFit> for BandFit {
    fn from(f: &PowerLawFit) -> Self {
        Self { a: f.a, b: f.b }
    }
}

/// `electron_density_cm3_from_frequency_mhz`.
pub fn electron_density_cm3(freq_mhz: f64) -> Result<f64, String> {
    if !freq_mhz.is_finite() || freq_mhz <= 0.0 {
        return Err("Frequency must be positive to derive density.".into());
    }
    Ok((freq_mhz / PLASMA_FREQ_COEFF_MHZ).powi(2))
}

/// `magnetic_field_gauss_from_alfven_speed`.
pub fn magnetic_field_gauss(alfven_speed_km_s: f64, electron_density_cm3: f64) -> Result<f64, String> {
    if !alfven_speed_km_s.is_finite() || alfven_speed_km_s <= 0.0 {
        return Err("Alfven speed must be positive.".into());
    }
    if !electron_density_cm3.is_finite() || electron_density_cm3 <= 0.0 {
        return Err("Electron density must be positive.".into());
    }
    let va_m_s = alfven_speed_km_s * 1000.0;
    let ne_m3 = electron_density_cm3 * 1e6;
    let b_t = va_m_s * (MU_0 * PROTON_MASS_KG * ne_m3).sqrt();
    Ok(b_t * 1e4)
}

/// `newkirk_height_rs_from_frequency_mhz` for every frequency.
pub fn newkirk_heights_rs(freqs: &[f64], fold: i64) -> Result<Vec<f64>, String> {
    let denom = fold.max(1) as f64 * 3.385;
    let ratios: Vec<f64> = freqs.iter().map(|f| f * f / denom).collect();
    if ratios.iter().any(|r| !r.is_finite() || *r <= 1.0) {
        return Err("Frequencies must stay above the Newkirk cutoff to derive shock height.".into());
    }
    Ok(ratios.iter().map(|r| 4.32 * std::f64::consts::LN_10 / r.ln()).collect())
}

/// `_sampling_times_for_interval`.
fn sampling_times(t_start: f64, t_end: f64, upper_fit_times: &[f64], available: Option<&[f64]>) -> Vec<f64> {
    let unique = |mut v: Vec<f64>| {
        v.sort_by(f64::total_cmp);
        v.dedup();
        v
    };
    if let Some(samples) = available {
        let inside: Vec<f64> =
            samples.iter().copied().filter(|v| v.is_finite() && *v >= t_start && *v <= t_end).collect();
        if inside.len() >= 2 {
            return unique(inside);
        }
    }
    let samples: Vec<f64> = unique(upper_fit_times.to_vec())
        .into_iter()
        .filter(|v| v.is_finite() && *v >= t_start && *v <= t_end)
        .collect();
    if samples.len() >= 2 {
        return samples;
    }
    if (t_start - t_end).abs() <= 1e-9 {
        return vec![t_start];
    }
    linspace(t_start, t_end, 512)
}

struct Sampled {
    t_start: f64,
    t_end: f64,
    sample_times: Vec<f64>,
    upper_curve: Vec<f64>,
    lower_curve: Vec<f64>,
    upper_drift: Vec<f64>,
    compression: Vec<f64>,
    lower_extrapolated: bool,
    warning: String,
}

/// `_sample_type_ii_fits`.
fn sample_fits(
    upper: (&[f64], &[f64]),
    lower: (&[f64], &[f64]),
    upper_fit: BandFit,
    lower_fit: BandFit,
    available: Option<&[f64]>,
) -> Result<Sampled, String> {
    let upper_mask = fit_mask(upper.0, upper.1);
    let lower_mask = fit_mask(lower.0, lower.1);
    if upper_mask.iter().filter(|m| **m).count() < 2 || lower_mask.iter().filter(|m| **m).count() < 2 {
        return Err("Both bands need at least two positive-time points before calculations.".into());
    }
    let fit_upper: Vec<f64> = upper.0.iter().zip(&upper_mask).filter(|(_, m)| **m).map(|(v, _)| *v).collect();
    let fit_lower: Vec<f64> = lower.0.iter().zip(&lower_mask).filter(|(_, m)| **m).map(|(v, _)| *v).collect();
    let min = |v: &[f64]| v.iter().copied().fold(f64::INFINITY, f64::min);
    let max = |v: &[f64]| v.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let (t_start, t_end) = (min(&fit_upper), max(&fit_upper));
    let sample_times = sampling_times(t_start, t_end, &fit_upper, available);
    let upper_curve: Vec<f64> = sample_times.iter().map(|&t| power_law(t, upper_fit.a, upper_fit.b)).collect();
    let lower_curve: Vec<f64> = sample_times.iter().map(|&t| power_law(t, lower_fit.a, lower_fit.b)).collect();
    let upper_drift: Vec<f64> =
        sample_times.iter().map(|&t| power_law_drift_rate(t, upper_fit.a, upper_fit.b)).collect();
    let compression: Vec<f64> = upper_curve.iter().zip(&lower_curve).map(|(u, l)| (u / l).powi(2)).collect();
    if compression.iter().any(|x| !x.is_finite() || *x <= 0.0 || *x >= 4.0) {
        return Err("Compression ratio X must stay between 0 and 4 over the selected interval.".into());
    }
    let lower_extrapolated = t_start < min(&fit_lower) || t_end > max(&fit_lower);
    let warning = if lower_extrapolated {
        "Lower-band fit was extrapolated over part of the averaging interval.".to_owned()
    } else {
        String::new()
    };
    Ok(Sampled {
        t_start,
        t_end,
        sample_times,
        upper_curve,
        lower_curve,
        upper_drift,
        compression,
        lower_extrapolated,
        warning,
    })
}

/// `calculate_type_ii_parameters`.
#[derive(Clone, Debug, PartialEq)]
pub struct TypeIIResults {
    pub start_time_s: f64,
    pub end_time_s: f64,
    pub upper_start_freq_mhz: f64,
    pub lower_start_freq_mhz: f64,
    pub avg_upper_freq_mhz: f64,
    pub avg_lower_freq_mhz: f64,
    pub bandwidth_mhz: f64,
    pub upper_avg_drift_mhz_s: f64,
    pub compression_ratio: f64,
    pub alfven_mach_number: f64,
    pub alfven_speed_km_s: f64,
    pub magnetic_field_g: f64,
    pub lower_extrapolated: bool,
    pub warning: String,
}

fn mean(v: &[f64]) -> f64 {
    v.iter().sum::<f64>() / v.len() as f64
}

#[allow(clippy::too_many_arguments)]
pub fn calculate_type_ii_parameters(
    upper: (&[f64], &[f64]),
    lower: (&[f64], &[f64]),
    upper_fit: BandFit,
    lower_fit: BandFit,
    analysis_start_freq_mhz: f64,
    analysis_shock_speed_km_s: f64,
    available: Option<&[f64]>,
) -> Result<TypeIIResults, String> {
    let s = sample_fits(upper, lower, upper_fit, lower_fit, available)?;
    let avg_upper = mean(&s.upper_curve);
    let avg_lower = mean(&s.lower_curve);
    let bandwidth = mean(&s.upper_curve.iter().zip(&s.lower_curve).map(|(u, l)| u - l).collect::<Vec<_>>());
    let avg_upper_drift = mean(&s.upper_drift);
    let x = (avg_upper / avg_lower).powi(2);
    let mach = ((x * (x + 5.0)) / (2.0 * (4.0 - x))).sqrt();
    if mach <= 0.0 || !mach.is_finite() {
        return Err("Failed to derive a valid Alfven Mach number.".into());
    }
    if !analysis_start_freq_mhz.is_finite() || analysis_start_freq_mhz <= 0.0 {
        return Err("Analyzer starting frequency must be positive.".into());
    }
    if !analysis_shock_speed_km_s.is_finite() || analysis_shock_speed_km_s <= 0.0 {
        return Err("Analyzer shock speed must be positive.".into());
    }
    let alfven = analysis_shock_speed_km_s / mach;
    let density = electron_density_cm3(analysis_start_freq_mhz)?;
    let field = magnetic_field_gauss(alfven, density)?;
    Ok(TypeIIResults {
        start_time_s: s.t_start,
        end_time_s: s.t_end,
        upper_start_freq_mhz: s.upper_curve[0],
        lower_start_freq_mhz: s.lower_curve[0],
        avg_upper_freq_mhz: avg_upper,
        avg_lower_freq_mhz: avg_lower,
        bandwidth_mhz: bandwidth,
        upper_avg_drift_mhz_s: avg_upper_drift,
        compression_ratio: x,
        alfven_mach_number: mach,
        alfven_speed_km_s: alfven,
        magnetic_field_g: field,
        lower_extrapolated: s.lower_extrapolated,
        warning: s.warning,
    })
}

/// `calculate_b_vs_r_profile`.
#[derive(Clone, Debug, PartialEq)]
pub struct BvrProfile {
    pub sample_times_s: Vec<f64>,
    pub heights_rs: Vec<f64>,
    pub magnetic_field_g: Vec<f64>,
    pub alfven_mach_number: Vec<f64>,
    pub alfven_speed_km_s: Vec<f64>,
    pub compression_ratio: Vec<f64>,
    pub fit: PowerLawFit,
    pub lower_extrapolated: bool,
    pub warning: String,
    pub start_time_s: f64,
    pub end_time_s: f64,
}

pub fn calculate_b_vs_r_profile(
    upper: (&[f64], &[f64]),
    lower: (&[f64], &[f64]),
    upper_fit: BandFit,
    lower_fit: BandFit,
    analysis_shock_speed_km_s: f64,
    fold: i64,
    available: Option<&[f64]>,
) -> Result<BvrProfile, String> {
    let s = sample_fits(upper, lower, upper_fit, lower_fit, available)?;
    if !analysis_shock_speed_km_s.is_finite() || analysis_shock_speed_km_s <= 0.0 {
        return Err("Analyzer shock speed must be positive.".into());
    }
    let mach: Vec<f64> = s.compression.iter().map(|x| ((x * (x + 5.0)) / (2.0 * (4.0 - x))).sqrt()).collect();
    if mach.iter().any(|m| !m.is_finite() || *m <= 0.0) {
        return Err("Failed to derive a valid Alfven Mach number over the selected interval.".into());
    }
    let alfven: Vec<f64> = mach.iter().map(|m| analysis_shock_speed_km_s / m).collect();
    let densities: Vec<f64> = s.upper_curve.iter().map(|f| (f / PLASMA_FREQ_COEFF_MHZ).powi(2)).collect();
    let field =
        alfven.iter().zip(&densities).map(|(va, ne)| magnetic_field_gauss(*va, *ne)).collect::<Result<Vec<_>, _>>()?;
    let heights = newkirk_heights_rs(&s.upper_curve, fold)?;
    // np.argsort (quicksort): ties keep no particular order; heights are distinct here.
    let mut order: Vec<usize> = (0..heights.len()).collect();
    order.sort_by(|&i, &j| heights[i].total_cmp(&heights[j]));
    let pick = |v: &[f64]| order.iter().map(|&i| v[i]).collect::<Vec<_>>();
    let heights_sorted = pick(&heights);
    let field_sorted = pick(&field);
    let fit = fit_power_law(&heights_sorted, &field_sorted)?;
    Ok(BvrProfile {
        sample_times_s: s.sample_times,
        heights_rs: heights_sorted,
        magnetic_field_g: field_sorted,
        alfven_mach_number: pick(&mach),
        alfven_speed_km_s: pick(&alfven),
        compression_ratio: pick(&s.compression),
        fit,
        lower_extrapolated: s.lower_extrapolated,
        warning: s.warning,
        start_time_s: s.t_start,
        end_time_s: s.t_end,
    })
}
