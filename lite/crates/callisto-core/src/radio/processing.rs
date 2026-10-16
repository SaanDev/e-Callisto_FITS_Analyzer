//! Ports of the full app's per-channel processing:
//! * `src/backend/common/array_stats.py` — row quantiles from one sort;
//! * `src/backend/radio/noise_reduction.py` — row baselines, noise scales,
//!   background subtraction with optional noise equalisation;
//! * `src/backend/radio/batch_processing.py` — the sidebar's methods
//!   (Mean, Median, Median dB) and the dB constants;
//! * `src/backend/radio/rfi_filters.py` — RFI cleaning.
//!
//! The full app works in float32 here, as do these functions; rows run in
//! parallel. Quantiles interpolate as `row_quantiles` does: `a*(1-w) + b*w`
//! in float64, then cast.

use super::array::Array2;
use super::frequency_axis::invalid_row_mask;
use super::numeric::{nanmedian, nanstd, percentile_sorted};
use crate::util::par_rows;

pub const ROBUST_BASELINE_PERCENTILE: f64 = 25.0;
pub const NOISE_EQUALIZE_TARGET_PERCENTILE: f64 = 25.0;
const ROBUST_SIGMA_SCALE: f32 = 1.4826;
const IQR_TO_SIGMA: f32 = 1.349;
const MIN_NOISE_SCALE: f32 = 1e-6;

/// 2500 mV over 256 digits against a 25.4 mV/dB detector.
pub const DEFAULT_DB_SCALE: f64 = 2500.0 / 256.0 / 25.4;
/// The CALLISTO Plotutil recipe uses 255 digits.
pub const PLOTUTIL_DB_SCALE: f64 = 2500.0 / 255.0 / 25.4;

// ---------------------------------------------------------- row quantiles

/// Sorted copy of a row (NaN last) and its count of non-NaN values.
fn sorted_row(row: &[f32]) -> (Vec<f32>, usize) {
    let mut v = row.to_vec();
    v.sort_by(f32::total_cmp);
    // total_cmp puts negative NaN first; move all NaNs to the end as np.sort does.
    let n = v.iter().filter(|x| !x.is_nan()).count();
    if n < v.len() {
        v.retain(|x| !x.is_nan());
        v.resize(row.len(), f32::NAN);
    }
    (v, n)
}

fn quantile_of_sorted(sorted: &[f32], count: usize, q: f64) -> f32 {
    if count == 0 {
        return f32::NAN;
    }
    let span = (count - 1) as f64;
    let position = span * (q / 100.0);
    let n = sorted.len();
    let lower = (position.floor() as usize).min(n - 1);
    let upper = (position.ceil() as usize).min(n - 1);
    let w = position - position.floor();
    (sorted[lower] as f64 * (1.0 - w) + sorted[upper] as f64 * w) as f32
}

/// The `lower` and `upper` order statistics (0-based, `lower <= upper`) of
/// `values`, found by selection rather than a full sort; `values` is reordered.
fn order_stats<T: Copy + PartialOrd>(
    values: &mut [T],
    lower: usize,
    upper: usize,
    cmp: fn(&T, &T) -> std::cmp::Ordering,
) -> (T, T) {
    let (_, lo, rest) = values.select_nth_unstable_by(lower, cmp);
    let lo = *lo;
    if upper == lower {
        return (lo, lo);
    }
    // The next order statistic is the smallest of what lies above.
    let hi = rest.iter().copied().reduce(|a, b| if cmp(&b, &a).is_lt() { b } else { a }).unwrap_or(lo);
    (lo, hi)
}

/// `np.nanpercentile`'s linear interpolation between order statistics, as
/// `quantile_of_sorted` computes it, without sorting.
fn select_quantile(finite: &mut [f32], q: f64) -> f32 {
    let count = finite.len();
    if count == 0 {
        return f32::NAN;
    }
    let position = (count - 1) as f64 * (q / 100.0);
    let lower = (position.floor() as usize).min(count - 1);
    let upper = (position.ceil() as usize).min(count - 1);
    let w = position - position.floor();
    let (a, b) = order_stats(finite, lower, upper, f32::total_cmp);
    (a as f64 * (1.0 - w) + b as f64 * w) as f32
}

/// `row_quantiles(data, qs)`: shape `(qs.len(), rows)`; row `i` of quantile
/// `q` equals `np.nanpercentile(data[i], q)`.
pub fn row_quantiles(data: &Array2, qs: &[f64]) -> Vec<Vec<f32>> {
    let mut out = vec![vec![f32::NAN; data.rows]; qs.len()];
    if data.rows == 0 || data.cols == 0 {
        return out;
    }
    let mut per_row = vec![0f32; data.rows * qs.len()];
    par_rows(&mut per_row, qs.len(), |r, slot| {
        if qs.len() <= 2 {
            // One or two quantiles: selection is linear, a sort is not.
            let mut finite: Vec<f32> = data.row(r).iter().copied().filter(|v| !v.is_nan()).collect();
            for (k, q) in qs.iter().enumerate() {
                slot[k] = select_quantile(&mut finite, *q);
            }
        } else {
            let (sorted, n) = sorted_row(data.row(r));
            for (k, q) in qs.iter().enumerate() {
                slot[k] = quantile_of_sorted(&sorted, n, *q);
            }
        }
    });
    for r in 0..data.rows {
        for k in 0..qs.len() {
            out[k][r] = per_row[r * qs.len() + k];
        }
    }
    out
}

pub fn row_median(data: &Array2) -> Vec<f32> {
    row_quantiles(data, &[50.0]).remove(0)
}

/// `np.nanmean(arr, axis=1)` as float32.
fn row_means(data: &Array2) -> Vec<f32> {
    let mut out = vec![0f32; data.rows];
    par_rows(&mut out, 1, |r, slot| {
        let (s, n) =
            data.row(r).iter().filter(|v| !v.is_nan()).fold((0f64, 0usize), |(s, n), &v| (s + v as f64, n + 1));
        slot[0] = if n > 0 { (s / n as f64) as f32 } else { f32::NAN };
    });
    out
}

// ---------------------------------------------------------------- baselines

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Baseline {
    Mean,
    Median,
    /// The 25th percentile (`robust`, `percentile`, `p25`).
    Robust,
}

impl Baseline {
    pub fn parse(method: &str) -> Result<Self, String> {
        match method.trim().to_lowercase().as_str() {
            "" | "mean" => Ok(Baseline::Mean),
            "median" => Ok(Baseline::Median),
            "robust" | "percentile" | "p25" => Ok(Baseline::Robust),
            _ => Err(format!("Unsupported baseline method: {method}")),
        }
    }
}

fn baseline_values(arr: &Array2, invalid: &[bool], mode: Baseline, robust_percentile: f64) -> Vec<f32> {
    let mut values = match mode {
        Baseline::Mean => row_means(arr),
        Baseline::Median => row_median(arr),
        Baseline::Robust => row_quantiles(arr, &[robust_percentile.clamp(0.0, 50.0)]).remove(0),
    };
    for (v, inv) in values.iter_mut().zip(invalid) {
        if *inv {
            *v = f32::NAN;
        }
    }
    values
}

/// `rowwise_baseline`: one value per row (NaN for invalid rows).
pub fn rowwise_baseline(data: &Array2, mode: Baseline, gap_row_mask: Option<&[bool]>) -> Vec<f32> {
    let invalid = invalid_row_mask(data, gap_row_mask);
    if invalid.iter().all(|&i| i) {
        return vec![f32::NAN; data.rows];
    }
    baseline_values(data, &invalid, mode, ROBUST_BASELINE_PERCENTILE)
}

/// Robust per-row noise scale: MAD, then IQR, then standard deviation, then 1.
fn rowwise_noise_scale_values(arr: &Array2, invalid: &[bool]) -> Vec<f32> {
    let center = row_median(arr);
    let mut deviations = arr.clone();
    for (r, c) in center.iter().enumerate() {
        for v in deviations.row_mut(r) {
            *v = (*v - c).abs();
        }
    }
    let mad = row_median(&deviations);
    let mut sigma: Vec<f32> = mad.iter().map(|m| m * ROBUST_SIGMA_SCALE).collect();
    let weak = |s: f32| !(s.is_finite() && s > MIN_NOISE_SCALE);
    if sigma.iter().any(|&s| weak(s)) {
        let q = row_quantiles(arr, &[25.0, 75.0]);
        for (r, s) in sigma.iter_mut().enumerate() {
            if weak(*s) {
                *s = (q[1][r] - q[0][r]) / IQR_TO_SIGMA;
            }
        }
        if sigma.iter().any(|&s| weak(s)) {
            for (r, s) in sigma.iter_mut().enumerate() {
                if weak(*s) {
                    let row: Vec<f64> = arr.row(r).iter().map(|&v| v as f64).collect();
                    *s = nanstd(&row).map_or(f32::NAN, |v| v as f32);
                    if weak(*s) {
                        *s = 1.0;
                    }
                }
            }
        }
    }
    for (s, inv) in sigma.iter_mut().zip(invalid) {
        if *inv {
            *s = f32::NAN;
        }
    }
    sigma
}

pub fn rowwise_noise_scale(data: &Array2, gap_row_mask: Option<&[bool]>) -> Vec<f32> {
    let invalid = invalid_row_mask(data, gap_row_mask);
    if invalid.iter().all(|&i| i) {
        return vec![f32::NAN; data.rows];
    }
    rowwise_noise_scale_values(data, &invalid)
}

#[derive(Clone, Copy, Debug)]
pub struct SubtractOptions {
    pub mode: Baseline,
    pub robust_percentile: f64,
    pub equalize_noise: bool,
    pub equalize_percentile: f64,
    pub attenuate_only: bool,
}

impl Default for SubtractOptions {
    fn default() -> Self {
        Self {
            mode: Baseline::Mean,
            robust_percentile: ROBUST_BASELINE_PERCENTILE,
            equalize_noise: false,
            equalize_percentile: NOISE_EQUALIZE_TARGET_PERCENTILE,
            attenuate_only: true,
        }
    }
}

/// `subtract_background_rows`: each row minus its baseline, optionally with
/// noisy rows attenuated to a common noise level; invalid rows become NaN.
pub fn subtract_background_rows(data: &Array2, gap_row_mask: Option<&[bool]>, o: SubtractOptions) -> Array2 {
    let invalid = invalid_row_mask(data, gap_row_mask);
    let mut out = data.clone();
    if invalid.iter().all(|&i| i) {
        for (r, inv) in invalid.iter().enumerate() {
            if *inv {
                out.row_mut(r).fill(f32::NAN);
            }
        }
        return out;
    }
    let baseline = baseline_values(data, &invalid, o.mode, o.robust_percentile);
    let cols = out.cols;
    par_rows(&mut out.data, cols, |r, row| {
        let b = baseline[r];
        for v in row.iter_mut() {
            *v -= b;
        }
    });

    if o.equalize_noise {
        let scales = rowwise_noise_scale_values(&out, &invalid);
        let valid: Vec<usize> =
            (0..out.rows).filter(|&r| !invalid[r] && scales[r].is_finite() && scales[r] > MIN_NOISE_SCALE).collect();
        if !valid.is_empty() {
            let mut vs: Vec<f64> = valid.iter().map(|&r| scales[r] as f64).collect();
            vs.sort_by(f64::total_cmp);
            if let Some(target) = percentile_sorted(&vs, o.equalize_percentile.clamp(0.0, 100.0))
                && target.is_finite()
                && target > MIN_NOISE_SCALE as f64
            {
                let mut factors = vec![1f32; out.rows];
                // numpy 2 (NEP 50) divides a Python float by float32 in float32.
                let target = target as f32;
                for &r in &valid {
                    let f = target / scales[r];
                    factors[r] = if o.attenuate_only { f.min(1.0) } else { f };
                }
                par_rows(&mut out.data, cols, |r, row| {
                    let f = factors[r];
                    for v in row.iter_mut() {
                        *v *= f;
                    }
                });
            }
        }
    }
    for (r, inv) in invalid.iter().enumerate() {
        if *inv {
            out.row_mut(r).fill(f32::NAN);
        }
    }
    out
}

// ------------------------------------------------- sidebar background methods

/// The Background Subtraction section's methods.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum BackgroundMethod {
    #[default]
    Mean,
    Median,
    Robust,
    /// Plotutil's median-dB recipe: median subtraction scaled to dB.
    PlotutilMedianDb,
}

impl BackgroundMethod {
    /// `normalize_background_method`.
    pub fn normalize(method: &str, strict: bool) -> Result<Self, String> {
        let m = method.trim().to_lowercase().replace(['-', ' '], "_");
        Ok(match m.as_str() {
            "plotutil"
            | "plotutil_median"
            | "plotutil_median_(db)"
            | "plotutil_median_db"
            | "callisto_plotutil"
            | "median_db"
            | "median_(db)" => BackgroundMethod::PlotutilMedianDb,
            "median" => BackgroundMethod::Median,
            "robust" | "percentile" | "p25" => BackgroundMethod::Robust,
            "" | "mean" => BackgroundMethod::Mean,
            _ if strict => return Err(format!("Unsupported baseline method: {method}")),
            _ => BackgroundMethod::Mean,
        })
    }

    pub fn key(self) -> &'static str {
        match self {
            BackgroundMethod::Mean => "mean",
            BackgroundMethod::Median => "median",
            BackgroundMethod::Robust => "robust",
            BackgroundMethod::PlotutilMedianDb => "plotutil_median_db",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            BackgroundMethod::Mean => "Mean",
            BackgroundMethod::Median => "Median",
            BackgroundMethod::Robust => "Robust",
            BackgroundMethod::PlotutilMedianDb => "Plotutil Median (dB)",
        }
    }

    pub fn produces_db(self) -> bool {
        self == BackgroundMethod::PlotutilMedianDb
    }
}

/// `batch_processing.subtract_background`.
pub fn subtract_background(
    data: &Array2,
    method: BackgroundMethod,
    gap_row_mask: Option<&[bool]>,
    equalize_noise: bool,
    db_scale: f64,
) -> Array2 {
    let mode = match method {
        BackgroundMethod::Mean => Baseline::Mean,
        BackgroundMethod::Median | BackgroundMethod::PlotutilMedianDb => Baseline::Median,
        BackgroundMethod::Robust => Baseline::Robust,
    };
    let mut out =
        subtract_background_rows(data, gap_row_mask, SubtractOptions { mode, equalize_noise, ..Default::default() });
    if method == BackgroundMethod::PlotutilMedianDb {
        let s = db_scale as f32;
        for v in out.data.iter_mut() {
            *v *= s;
        }
    }
    out
}

/// `(digits - cold) * scale`.
pub fn convert_digits_to_db(data: &Array2, cold_digits: f64, db_scale: f64) -> Array2 {
    let mut out = data.clone();
    let (c, s) = (cold_digits as f32, db_scale as f32);
    for v in out.data.iter_mut() {
        *v = (*v - c) * s;
    }
    out
}

// ----------------------------------------------------------------------- RFI

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RfiSettings {
    pub enabled: bool,
    pub kernel_time: usize,
    pub kernel_freq: usize,
    pub channel_z_threshold: f64,
    pub percentile_clip: f64,
}

impl Default for RfiSettings {
    fn default() -> Self {
        Self { enabled: true, kernel_time: 3, kernel_freq: 3, channel_z_threshold: 6.0, percentile_clip: 99.5 }
    }
}

#[derive(Clone, Debug)]
pub struct RfiResult {
    pub data: Array2,
    pub masked_channel_indices: Vec<usize>,
}

fn ensure_odd(v: usize) -> usize {
    let v = v.max(1);
    if v.is_multiple_of(2) { v + 1 } else { v }
}

/// `scipy.ndimage.median_filter(arr, size=(kf, kt), mode="nearest")`.
pub fn median_filter(arr: &Array2, kernel_freq: usize, kernel_time: usize) -> Array2 {
    let (kf, kt) = (ensure_odd(kernel_freq), ensure_odd(kernel_time));
    let (hf, ht) = ((kf / 2) as isize, (kt / 2) as isize);
    let (rows, cols) = (arr.rows as isize, arr.cols as isize);
    let mut out = Array2::filled(arr.rows, arr.cols, 0.0);
    if kf == 3 && kt == 3 && arr.cols >= 2 {
        median_filter_3x3(arr, &mut out);
        return out;
    }
    let mid = (kf * kt) / 2;
    par_rows(&mut out.data, arr.cols, |r, row| {
        let r = r as isize;
        let src_rows: Vec<&[f32]> = (-hf..=hf).map(|d| arr.row((r + d).clamp(0, rows - 1) as usize)).collect();
        let mut window = vec![0f32; kf * kt];
        for (c, slot) in row.iter_mut().enumerate() {
            let c = c as isize;
            let mut k = 0;
            for src in &src_rows {
                for d in -ht..=ht {
                    window[k] = src[(c + d).clamp(0, cols - 1) as usize];
                    k += 1;
                }
            }
            let (_, m, _) = window.select_nth_unstable_by(mid, f32::total_cmp);
            *slot = *m;
        }
    });
    out
}

/// `f32::total_cmp` as a plain integer order: the same key std uses.
#[inline(always)]
fn order_key(v: f32) -> i32 {
    let b = v.to_bits() as i32;
    b ^ ((((b >> 31) as u32) >> 1) as i32)
}

#[inline(always)]
fn from_order_key(k: i32) -> f32 {
    // The key transform is its own inverse.
    f32::from_bits((k ^ ((((k >> 31) as u32) >> 1) as i32)) as u32)
}

/// The 3×3 median filter with nearest edges, by the 19-exchange median-of-9
/// network on `total_cmp` keys: the same values as selecting the 5th of 9.
fn median_filter_3x3(arr: &Array2, out: &mut Array2) {
    let (rows, cols) = (arr.rows, arr.cols);
    par_rows(&mut out.data, cols, |r, row| {
        let up = arr.row(r.saturating_sub(1));
        let mid = arr.row(r);
        let down = arr.row((r + 1).min(rows - 1));
        let at = |src: &[f32], c: isize| order_key(src[c.clamp(0, cols as isize - 1) as usize]);
        for (c, slot) in row.iter_mut().enumerate() {
            let c = c as isize;
            let mut p = [
                at(up, c - 1),
                at(up, c),
                at(up, c + 1),
                at(mid, c - 1),
                at(mid, c),
                at(mid, c + 1),
                at(down, c - 1),
                at(down, c),
                at(down, c + 1),
            ];
            macro_rules! sort2 {
                ($a:expr, $b:expr) => {{
                    let (x, y) = (p[$a], p[$b]);
                    p[$a] = x.min(y);
                    p[$b] = x.max(y);
                }};
            }
            sort2!(1, 2);
            sort2!(4, 5);
            sort2!(7, 8);
            sort2!(0, 1);
            sort2!(3, 4);
            sort2!(6, 7);
            sort2!(1, 2);
            sort2!(4, 5);
            sort2!(7, 8);
            sort2!(0, 3);
            sort2!(5, 8);
            sort2!(4, 7);
            sort2!(3, 6);
            sort2!(1, 4);
            sort2!(2, 5);
            sort2!(4, 7);
            sort2!(4, 2);
            sort2!(6, 4);
            sort2!(4, 2);
            *slot = from_order_key(p[4]);
        }
    });
}

fn robust_z(values: &[f64]) -> Vec<f64> {
    let med = nanmedian(values.iter().copied()).unwrap_or(f64::NAN);
    let mad = nanmedian(values.iter().map(|v| (v - med).abs())).unwrap_or(f64::NAN);
    if !mad.is_finite() || mad <= 0.0 {
        let std = nanstd(values).unwrap_or(f64::NAN);
        if std.is_finite() && std > 0.0 {
            return values.iter().map(|v| (v - med) / std).collect();
        }
        return values.iter().map(|v| if (v - med).abs() > 0.0 { f64::INFINITY } else { 0.0 }).collect();
    }
    values.iter().map(|v| 0.6745 * (v - med) / mad).collect()
}

fn mask_hot_channels(data: &Array2, z_thresh: f64) -> Vec<usize> {
    if data.rows == 0 {
        return Vec::new();
    }
    // Per channel, in parallel: the median (row_quantiles(..., dtype=float64),
    // interpolated in f64 without the f32 cast), then the median absolute
    // deviation from it (float32 data minus a float64 median stays float64).
    let mut per_row = vec![0f64; data.rows * 2];
    par_rows(&mut per_row, 2, |r, slot| {
        let mut finite: Vec<f32> = data.row(r).iter().copied().filter(|v| !v.is_nan()).collect();
        let n = finite.len();
        if n == 0 {
            slot[0] = f64::NAN;
            slot[1] = f64::NAN;
            return;
        }
        let half = |n: usize| {
            let position = (n - 1) as f64 * 0.5;
            (position.floor() as usize, position.ceil() as usize, position - position.floor())
        };
        let (lower, upper, w) = half(n);
        let (a, b) = order_stats(&mut finite, lower, upper, f32::total_cmp);
        let med = a as f64 * (1.0 - w) + b as f64 * w;
        let mut dev: Vec<f64> = finite.iter().map(|&v| ((v as f64) - med).abs()).filter(|v| !v.is_nan()).collect();
        slot[0] = med;
        slot[1] = match dev.len() {
            0 => f64::NAN,
            m => {
                let (lower, upper, w) = half(m);
                let (a, b) = order_stats(&mut dev, lower, upper, f64::total_cmp);
                a * (1.0 - w) + b * w
            }
        };
    });
    let med: Vec<f64> = per_row.iter().step_by(2).copied().collect();
    let centered: Vec<f64> = per_row.iter().skip(1).step_by(2).copied().collect();
    let score: Vec<f64> = med.iter().zip(&centered).map(|(m, c)| m.abs() + c).collect();
    robust_z(&score).iter().enumerate().filter(|(_, z)| **z > z_thresh).map(|(i, _)| i).collect()
}

fn repair_masked_channels(cleaned: &mut Array2, masked: &[usize]) {
    let n = cleaned.rows;
    for &idx in masked {
        if idx > 0 && idx < n - 1 {
            let (a, b) = (cleaned.row(idx - 1).to_vec(), cleaned.row(idx + 1).to_vec());
            for (v, (x, y)) in cleaned.row_mut(idx).iter_mut().zip(a.iter().zip(&b)) {
                *v = 0.5 * (x + y);
            }
        } else {
            let donor = if idx == 0 {
                if n > 1 { 1 } else { 0 }
            } else if n > 1 {
                n - 2
            } else {
                0
            };
            let d = cleaned.row(donor).to_vec();
            cleaned.row_mut(idx).copy_from_slice(&d);
        }
    }
}

/// `clean_rfi`: median filter, repair of hot channels, high-side clipping.
pub fn clean_rfi(data: &Array2, s: RfiSettings) -> RfiResult {
    if !s.enabled {
        return RfiResult { data: data.clone(), masked_channel_indices: Vec::new() };
    }
    let mut filtered = median_filter(data, s.kernel_freq, s.kernel_time);
    let masked = mask_hot_channels(data, s.channel_z_threshold);
    repair_masked_channels(&mut filtered, &masked);
    if s.percentile_clip > 0.0 && s.percentile_clip < 100.0 {
        let highs = row_quantiles(&filtered, &[s.percentile_clip]).remove(0);
        let cols = filtered.cols;
        par_rows(&mut filtered.data, cols, |r, row| {
            let h = highs[r];
            for v in row.iter_mut() {
                // np.minimum propagates NaN.
                if !(v.is_nan() || h.is_nan()) {
                    *v = v.min(h);
                } else {
                    *v = f32::NAN;
                }
            }
        });
    }
    RfiResult { data: filtered, masked_channel_indices: masked }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quantiles_match_nanpercentile() {
        let a = Array2::new(2, 4, vec![1.0, 2.0, 3.0, 4.0, f32::NAN, 10.0, 20.0, 30.0]);
        let q = row_quantiles(&a, &[50.0, 25.0]);
        assert_eq!(q[0], vec![2.5, 20.0]);
        assert_eq!(q[1], vec![1.75, 15.0]);
    }

    #[test]
    fn median_3x3_matches_selection() {
        let mut seed = 12345u32;
        let mut next = || {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            (seed >> 8) as f32 / 65536.0 - 100.0
        };
        let mut data: Vec<f32> = (0..7 * 11).map(|_| next()).collect();
        data[5] = f32::NAN;
        data[20] = f32::INFINITY;
        data[33] = -0.0;
        let a = Array2::new(7, 11, data);
        let fast = median_filter(&a, 3, 3);
        // The general path: a 3×3 window by selection.
        let mut slow = Array2::filled(7, 11, 0.0);
        for r in 0..7isize {
            for c in 0..11isize {
                let mut w = Vec::new();
                for dr in -1..=1 {
                    for dc in -1..=1 {
                        w.push(a.get((r + dr).clamp(0, 6) as usize, (c + dc).clamp(0, 10) as usize));
                    }
                }
                let (_, m, _) = w.select_nth_unstable_by(4, f32::total_cmp);
                slow.row_mut(r as usize)[c as usize] = *m;
            }
        }
        assert!(fast.data.iter().zip(&slow.data).all(|(x, y)| x.to_bits() == y.to_bits()));
    }

    #[test]
    fn median_filter_nearest_edges() {
        let a = Array2::new(1, 5, vec![1.0, 9.0, 2.0, 8.0, 3.0]);
        let f = median_filter(&a, 1, 3);
        assert_eq!(f.data, vec![1.0, 2.0, 8.0, 3.0, 3.0]);
    }

    #[test]
    fn plotutil_scales_median_subtraction() {
        let a = Array2::new(1, 3, vec![10.0, 12.0, 14.0]);
        let out = subtract_background(&a, BackgroundMethod::PlotutilMedianDb, None, false, PLOTUTIL_DB_SCALE);
        assert!((out.data[2] - (2.0 * PLOTUTIL_DB_SCALE) as f32).abs() < 1e-6);
    }
}
