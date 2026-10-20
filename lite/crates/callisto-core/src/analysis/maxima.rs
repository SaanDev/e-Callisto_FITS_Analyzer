//! The maximum-intensity points the analysis starts from
//! (`MainWindow._build_analysis_seed_from_current_data` and
//! `_auto_filter_isolated_maxima`): the peak frequency of every time column,
//! with columns that are too faint dropped for an isolated burst.

use crate::radio::array::Array2;
use crate::radio::numeric::nanpercentile;

#[derive(Clone, Debug, PartialEq)]
pub struct MaximaSeed {
    pub time_channels: Vec<f64>,
    pub time_seconds: Vec<f64>,
    pub freqs: Vec<f64>,
    /// Columns removed by the isolated-burst clean-up.
    pub auto_removed: usize,
    pub auto_outlier_cleaned: bool,
}

/// `_auto_filter_isolated_maxima`: keep the columns whose peak is above 15 %
/// of the 10th percentile of the positive peaks. Returns the kept indices
/// into `time_channels`, or `None` when nothing changes.
pub fn auto_filter_isolated(source: &Array2) -> Option<Vec<usize>> {
    let (rows, cols) = source.shape();
    // np.nanmax(axis=0): NaN for an all-NaN column.
    let vals: Vec<f64> = (0..cols)
        .map(|c| {
            (0..rows).map(|r| source.get(r, c) as f64).filter(|v| !v.is_nan()).fold(f64::NAN, |m, v| if m.is_nan() || v > m { v } else { m })
        })
        .collect();
    let finite: Vec<bool> = vals.iter().map(|v| v.is_finite()).collect();
    let positive: Vec<f64> = vals.iter().zip(&finite).filter(|(v, f)| **f && **v > 0.0).map(|(v, _)| *v).collect();
    let count = |m: &[bool]| m.iter().filter(|x| **x).count();
    let mut valid: Vec<bool>;
    if !positive.is_empty() {
        let base = nanpercentile(positive.iter().copied(), 10.0).unwrap_or(0.0);
        let threshold = (base * 0.15).max(1e-12);
        valid = vals.iter().zip(&finite).map(|(v, f)| *f && *v > threshold).collect();
        if count(&valid) < 3 {
            valid = vals.iter().zip(&finite).map(|(v, f)| *f && *v > 0.0).collect();
        }
    } else {
        valid = vals.iter().zip(&finite).map(|(v, f)| *f && *v > 0.0).collect();
    }
    if count(&valid) < 3 {
        return None;
    }
    Some(valid.iter().enumerate().filter(|(_, v)| **v).map(|(i, _)| i).collect())
}

/// `_build_analysis_seed_from_current_data`. `isolated_clean` is "the plot
/// is an isolated burst and auto-clean is on".
pub fn analysis_seed(data: &Array2, freqs: &[f64], time: Option<&[f64]>, isolated_clean: bool) -> Option<MaximaSeed> {
    let (rows, nx) = data.shape();
    if nx == 0 || freqs.is_empty() || rows != freqs.len() {
        // freqs[peak_indices] would fail on a mismatch; the full app has none.
        if freqs.is_empty() || nx == 0 {
            return None;
        }
    }
    let mut time_channels: Vec<f64> = (0..nx).map(|i| i as f64).collect();
    let mut time_seconds: Vec<f64> = match time {
        Some(t) if t.len() == nx => t.to_vec(),
        _ => time_channels.clone(),
    };
    let valid_cols: Vec<bool> = (0..nx).map(|c| (0..rows).any(|r| data.get(r, c).is_finite())).collect();
    if !valid_cols.iter().any(|v| *v) {
        return None;
    }
    let mut max_freqs: Vec<f64> = (0..nx)
        .map(|c| {
            let mut best = (0usize, f32::NEG_INFINITY);
            for r in 0..rows {
                let v = data.get(r, c);
                let v = if v.is_finite() { v } else { f32::NEG_INFINITY };
                if r == 0 || v > best.1 {
                    best = (r, v);
                }
            }
            freqs.get(best.0).copied().unwrap_or(f64::NAN)
        })
        .collect();
    if !valid_cols.iter().all(|v| *v) {
        let keep = |v: &Vec<f64>| v.iter().zip(&valid_cols).filter(|(_, k)| **k).map(|(x, _)| *x).collect::<Vec<_>>();
        time_channels = keep(&time_channels);
        time_seconds = keep(&time_seconds);
        max_freqs = keep(&max_freqs);
    }
    let mut auto_removed = 0;
    if isolated_clean
        && let Some(kept) = auto_filter_isolated(data)
        && time_channels.len() == nx
    {
        let before = time_channels.len();
        time_channels = kept.iter().map(|&i| time_channels[i]).collect();
        max_freqs = kept.iter().map(|&i| max_freqs[i]).collect();
        time_seconds = kept.iter().map(|&i| time_seconds[i]).collect();
        auto_removed = before - time_channels.len();
    }
    Some(MaximaSeed { time_channels, time_seconds, freqs: max_freqs, auto_removed, auto_outlier_cleaned: auto_removed > 0 })
}
