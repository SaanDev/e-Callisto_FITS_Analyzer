//! Following a burst's emission ridge through a dynamic spectrum
//! (`src/backend/radio/ridge_tracking.py`): from the brightest sample (or a
//! picked one) column by column within a few channels, stopping after
//! several columns in the noise; peaks refined with a three-point parabola.

use crate::radio::array::Array2;
use crate::radio::numeric::nanmedian;

/// Consecutive hits needed to resume tracking after a gap.
const CONFIRM_RUN: usize = 2;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RidgeSettings {
    /// How far the ridge may move between consecutive columns.
    pub search_channels: usize,
    /// A peak must stand this many robust σ above the background.
    pub threshold_sigma: f64,
    /// Columns below threshold in a row before tracking stops.
    pub max_gap: usize,
    /// Only follow drift to lower frequency.
    pub falling_only: bool,
}

impl Default for RidgeSettings {
    fn default() -> Self {
        Self { search_channels: 3, threshold_sigma: 3.0, max_gap: 8, falling_only: false }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct RidgeResult {
    pub time_indices: Vec<usize>,
    pub freqs_mhz: Vec<f64>,
    pub peak_values: Vec<f64>,
    pub seed_index: (usize, usize),
    pub threshold: f64,
    pub warnings: Vec<String>,
}

/// `_background_threshold`.
fn background_threshold(values: &[f64], threshold_sigma: f64) -> f64 {
    let finite: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
    if finite.is_empty() {
        return f64::INFINITY;
    }
    let baseline = median(finite.clone());
    let sigma = 1.4826 * median(finite.iter().map(|v| (v - baseline).abs()).collect());
    if !sigma.is_finite() || sigma <= 0.0 {
        return next_up(baseline);
    }
    baseline + threshold_sigma * sigma
}

fn median(mut v: Vec<f64>) -> f64 {
    v.sort_by(f64::total_cmp);
    let n = v.len();
    if n % 2 == 1 { v[n / 2] } else { (v[n / 2 - 1] + v[n / 2]) / 2.0 }
}

fn next_up(x: f64) -> f64 {
    if x.is_nan() || x == f64::INFINITY {
        return x;
    }
    if x == 0.0 {
        return f64::from_bits(1);
    }
    let bits = x.to_bits();
    f64::from_bits(if x > 0.0 { bits + 1 } else { bits - 1 })
}

/// `_channel_frequency`: linear between channels.
fn channel_frequency(freqs: &[f64], position: f64) -> f64 {
    let lower = position.floor() as usize;
    let upper = (lower + 1).min(freqs.len() - 1);
    let weight = position - lower as f64;
    freqs[lower] + (freqs[upper] - freqs[lower]) * weight
}

/// `_refined_peak`.
fn refined_peak(column: &[f64], row: usize) -> f64 {
    if row == 0 || row >= column.len() - 1 {
        return row as f64;
    }
    let (left, centre, right) = (column[row - 1], column[row], column[row + 1]);
    if !(left.is_finite() && centre.is_finite() && right.is_finite()) {
        return row as f64;
    }
    let curvature = left - 2.0 * centre + right;
    if curvature >= 0.0 {
        return row as f64;
    }
    let offset = 0.5 * (left - right) / curvature;
    row as f64 + offset.clamp(-0.5, 0.5)
}

/// `np.argmax` over a slice with non-finite values as -inf: the first maximum.
fn argmax_finite(values: impl Iterator<Item = f64>) -> (usize, f64) {
    let mut best = (0usize, f64::NEG_INFINITY);
    let mut first = true;
    for (i, v) in values.enumerate() {
        let v = if v.is_finite() { v } else { f64::NEG_INFINITY };
        if first || v > best.1 {
            best = (i, v);
            first = false;
        }
    }
    best
}

#[allow(clippy::too_many_arguments)]
fn follow(
    data: &[f64],
    rows: usize,
    cols: usize,
    start_row: usize,
    start_col: usize,
    step: isize,
    settings: &RidgeSettings,
    threshold: f64,
    freq_descending: bool,
) -> Vec<(usize, usize)> {
    let window = settings.search_channels.max(1) as isize;
    let mut points = Vec::new();
    let mut pending: Vec<(usize, usize)> = Vec::new();
    let mut row = start_row as isize;
    let mut probe = start_row as isize;
    let mut misses = 0usize;
    let mut col = start_col as isize + step;
    while col >= 0 && (col as usize) < cols && misses <= settings.max_gap {
        let (mut lo, mut hi) = (probe - window, probe + window);
        if settings.falling_only {
            let later_is_higher_row = freq_descending;
            if (step > 0) == later_is_higher_row {
                lo = probe;
            } else {
                hi = probe;
            }
        }
        let (lo, hi) = (lo.max(0), hi.min(rows as isize - 1));
        let c = col as usize;
        let (best, value) = argmax_finite((lo..=hi).map(|r| data[r as usize * cols + c]));
        if value.is_finite() && value > threshold {
            probe = lo + best as isize;
            if misses == 0 {
                points.push((probe as usize, c));
                row = probe;
            } else {
                pending.push((probe as usize, c));
                if pending.len() >= CONFIRM_RUN {
                    points.append(&mut pending);
                    row = probe;
                    misses = 0;
                }
            }
        } else {
            if !pending.is_empty() {
                pending.clear();
                probe = row;
            }
            misses += 1;
        }
        col += step;
    }
    points
}

/// `track_ridge`. `seed` is an optional (row, column) to start from.
pub fn track_ridge(data: &Array2, freqs: &[f64], seed: Option<(usize, usize)>, settings: &RidgeSettings) -> Result<RidgeResult, String> {
    let (rows, cols) = data.shape();
    if rows != freqs.len() {
        return Err("Ridge tracking needs (frequency, time) data matching the frequency axis.".into());
    }
    if rows < 1 || cols < 1 {
        return Err("The spectrum is empty.".into());
    }
    // Each channel's excess over its own median level.
    let mut arr: Vec<f64> = data.data.iter().map(|v| *v as f64).collect();
    for r in 0..rows {
        let row = &mut arr[r * cols..(r + 1) * cols];
        let level = if row.iter().any(|v| v.is_finite()) { nanmedian(row.iter().copied()).unwrap_or(0.0) } else { 0.0 };
        for v in row.iter_mut() {
            *v -= level;
        }
    }
    let threshold = background_threshold(&arr, settings.threshold_sigma);
    let start = match seed {
        None => {
            let (flat, value) = argmax_finite(arr.iter().copied());
            if !value.is_finite() {
                return Err("The spectrum has no finite samples to track.".into());
            }
            (flat / cols, flat % cols)
        }
        Some((r, c)) => (r.min(rows - 1), c.min(cols - 1)),
    };
    let (mut row, col) = start;
    if seed.is_some() {
        let window = settings.search_channels.max(1);
        let (lo, hi) = (row.saturating_sub(window), (row + window).min(rows - 1));
        let (best, _) = argmax_finite((lo..=hi).map(|r| arr[r * cols + col]));
        row = lo + best;
    }
    let v = arr[row * cols + col];
    if !(v.is_finite() && v > threshold) {
        return Err("The starting point is not above the background; start on the burst itself.".into());
    }
    let freq_descending = freqs.len() >= 2 && freqs[0] > freqs[freqs.len() - 1];
    let backward = follow(&arr, rows, cols, row, col, -1, settings, threshold, freq_descending);
    let forward = follow(&arr, rows, cols, row, col, 1, settings, threshold, freq_descending);
    let path: Vec<(usize, usize)> = backward.into_iter().rev().chain(std::iter::once((row, col))).chain(forward).collect();

    let column = |c: usize| -> Vec<f64> { (0..rows).map(|r| arr[r * cols + c]).collect() };
    let time_indices: Vec<usize> = path.iter().map(|&(_, c)| c).collect();
    let freqs_mhz: Vec<f64> = path.iter().map(|&(r, c)| channel_frequency(freqs, refined_peak(&column(c), r))).collect();
    let peak_values: Vec<f64> = path.iter().map(|&(r, c)| arr[r * cols + c]).collect();
    let mut warnings = Vec::new();
    if time_indices.len() < 3 {
        warnings.push(
            "The ridge could only be followed for a few columns; try a larger search window or a lower threshold.".to_owned(),
        );
    }
    Ok(RidgeResult { time_indices, freqs_mhz, peak_values, seed_index: (row, col), threshold, warnings })
}
