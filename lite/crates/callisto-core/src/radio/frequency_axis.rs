//! Port of `src/backend/radio/frequency_axis.py`: orientation, spacing and
//! edges of frequency axes, invalid rows, display limits, and the log
//! frequency axis (ticks and row resampling).

use super::array::Array2;
use super::numeric::{nanmedian, percentile_sorted};

pub const DEFAULT_FREQUENCY_DIRECTION: i32 = -1;
const STEP_EPS: f64 = 1e-9;

pub fn dominant_frequency_direction(freqs: &[f64], default: i32) -> i32 {
    if freqs.len() < 2 {
        return default;
    }
    let diffs: Vec<f64> =
        freqs.windows(2).map(|w| w[1] - w[0]).filter(|d| d.is_finite() && d.abs() > STEP_EPS).collect();
    match nanmedian(diffs) {
        None => default,
        Some(m) if m < 0.0 => -1,
        Some(_) => 1,
    }
}

fn target(direction: i32) -> i32 {
    if direction < 0 { -1 } else { 1 }
}

/// `orient_frequency_rows`: flips data rows and axis when the axis runs the
/// other way. Returns whether it flipped.
pub fn orient_frequency_rows(data: &mut Array2, freqs: &mut [f64], direction: i32) -> bool {
    let t = target(direction);
    if dominant_frequency_direction(freqs, t) == t {
        return false;
    }
    *data = data.flipped_rows();
    freqs.reverse();
    true
}

pub fn orient_frequency_axis(freqs: &[f64], direction: i32) -> Vec<f64> {
    let t = target(direction);
    let mut out = freqs.to_vec();
    if dominant_frequency_direction(freqs, t) != t {
        out.reverse();
    }
    out
}

pub fn frequency_step_mhz(freqs: &[f64], default: f64) -> f64 {
    if freqs.len() < 2 {
        return default;
    }
    let diffs: Vec<f64> = freqs
        .windows(2)
        .map(|w| w[1] - w[0])
        .filter(|d| d.is_finite())
        .map(f64::abs)
        .filter(|d| *d > STEP_EPS)
        .collect();
    nanmedian(diffs).unwrap_or(default)
}

pub fn axis_edges(values: &[f64], default_step: f64) -> Vec<f64> {
    match values.len() {
        0 => Vec::new(),
        1 => {
            let half = 0.5 * default_step;
            vec![values[0] + half, values[0] - half]
        }
        n => {
            let mut edges = vec![0.0; n + 1];
            for i in 1..n {
                edges[i] = 0.5 * (values[i - 1] + values[i]);
            }
            edges[0] = values[0] + 0.5 * (values[0] - values[1]);
            edges[n] = values[n - 1] + 0.5 * (values[n - 1] - values[n - 2]);
            edges
        }
    }
}

pub fn frequency_edges(freqs: &[f64], default_step: f64) -> Vec<f64> {
    axis_edges(freqs, default_step)
}

/// Frequency spans (lo, hi) covered by runs of masked rows.
pub fn frequency_gap_spans(freqs: &[f64], gap_row_mask: Option<&[bool]>, default_step: f64) -> Vec<(f64, f64)> {
    let Some(mask) = gap_row_mask else { return Vec::new() };
    if freqs.is_empty() || mask.len() != freqs.len() || !mask.iter().any(|&m| m) {
        return Vec::new();
    }
    let edges = frequency_edges(freqs, default_step);
    let mut spans = Vec::new();
    let mut idx = 0;
    while idx < mask.len() {
        if !mask[idx] {
            idx += 1;
            continue;
        }
        let start = idx;
        while idx < mask.len() && mask[idx] {
            idx += 1;
        }
        let (a, b) = (edges[start], edges[idx]);
        spans.push((a.min(b), a.max(b)));
    }
    spans
}

pub fn time_bounds(time: &[f64]) -> Option<(f64, f64)> {
    let (&x0, &x1) = (time.first()?, time.last()?);
    Some(if (x1 - x0).abs() < STEP_EPS { (x0, x0 + 1.0) } else { (x0, x1) })
}

/// `pyqtgraph_extent`: `[x0, x1, edge(first row), edge(last row)]`.
pub fn pyqtgraph_extent(freqs: &[f64], time: &[f64], default_step: Option<f64>) -> Option<[f64; 4]> {
    let step = default_step.unwrap_or_else(|| frequency_step_mhz(freqs, 1.0));
    let edges = frequency_edges(freqs, step);
    if edges.len() < 2 {
        return None;
    }
    let (x0, x1) = time_bounds(time)?;
    Some([x0, x1, edges[0], *edges.last().unwrap()])
}

/// `matplotlib_extent`: `[x0, x1, edge(last row), edge(first row)]`.
pub fn matplotlib_extent(freqs: &[f64], time: &[f64], default_step: Option<f64>) -> Option<[f64; 4]> {
    let [x0, x1, first, last] = pyqtgraph_extent(freqs, time, default_step)?;
    Some([x0, x1, last, first])
}

/// Rows with no finite value, or marked in `gap_row_mask`.
pub fn invalid_row_mask(data: &Array2, gap_row_mask: Option<&[bool]>) -> Vec<bool> {
    let mut mask: Vec<bool> = data.rows_iter().map(|row| !row.iter().any(|v| v.is_finite())).collect();
    if data.rows == 0 {
        mask.clear();
    }
    if let Some(gap) = gap_row_mask
        && gap.len() == mask.len()
    {
        for (m, g) in mask.iter_mut().zip(gap) {
            *m |= *g;
        }
    }
    mask
}

pub fn finite_data_limits(data: &[f32]) -> Option<(f64, f64)> {
    let mut lo = f64::INFINITY;
    let mut hi = f64::NEG_INFINITY;
    for &v in data {
        if v.is_finite() {
            lo = lo.min(v as f64);
            hi = hi.max(v as f64);
        }
    }
    if !lo.is_finite() {
        return None;
    }
    if hi <= lo {
        hi = lo + 1e-6;
    }
    Some((lo, hi))
}

pub fn percentile_data_limits(data: &[f32], lower: f64, upper: f64) -> Option<(f64, f64)> {
    let mut finite: Vec<f64> = data.iter().filter(|v| v.is_finite()).map(|&v| v as f64).collect();
    if finite.is_empty() {
        return None;
    }
    finite.sort_by(f64::total_cmp);
    let (mut lo_pct, mut hi_pct) = (lower.clamp(0.0, 100.0), upper.clamp(0.0, 100.0));
    if lo_pct > hi_pct {
        std::mem::swap(&mut lo_pct, &mut hi_pct);
    }
    let mut vmin = percentile_sorted(&finite, lo_pct)?;
    let mut vmax = percentile_sorted(&finite, hi_pct)?;
    if vmax < vmin {
        std::mem::swap(&mut vmin, &mut vmax);
    }
    Some((vmin, vmax))
}

/// Tick label for a frequency in MHz, with only the digits it needs.
pub fn format_frequency_mhz(v: f64) -> String {
    if !v.is_finite() {
        return String::new();
    }
    let strip = |s: String| s.trim_end_matches('0').trim_end_matches('.').to_string();
    let m = v.abs();
    if m >= 100.0 {
        format!("{v:.0}")
    } else if m >= 10.0 {
        strip(format!("{v:.1}"))
    } else if m >= 1.0 {
        strip(format!("{v:.2}"))
    } else if m > 0.0 {
        format_g(v, 4)
    } else {
        "0".into()
    }
}

/// Python's `f"{v:.{p}g}"`.
pub fn format_g(v: f64, precision: usize) -> String {
    if v == 0.0 {
        return if v.is_sign_negative() { "-0".into() } else { "0".into() };
    }
    if !v.is_finite() {
        return if v.is_nan() {
            "nan".into()
        } else if v > 0.0 {
            "inf".into()
        } else {
            "-inf".into()
        };
    }
    let precision = precision.max(1);
    // C's %g picks the style from the exponent of the value once rounded.
    let s = format!("{:.*e}", precision - 1, v);
    let (m, e) = s.split_once('e').unwrap();
    let exp: i32 = e.parse().unwrap();
    if exp < -4 || exp >= precision as i32 {
        let m = if m.contains('.') { m.trim_end_matches('0').trim_end_matches('.') } else { m };
        format!("{m}e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs())
    } else {
        let decimals = (precision as i32 - 1 - exp).max(0) as usize;
        let s = format!("{v:.decimals$}");
        if s.contains('.') { s.trim_end_matches('0').trim_end_matches('.').to_string() } else { s }
    }
}

// ------------------------------------------------------------ log axis

const LOG_LABEL_LADDER: &[&[f64]] = &[
    &[1.0],
    &[1.0, 3.0],
    &[1.0, 2.0, 5.0],
    &[1.0, 2.0, 3.0, 5.0, 7.0],
    &[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
];
const LOG_MINOR_MANTISSAS: &[f64] = &[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0];
const MAX_MINOR_TICKS: usize = 60;
const NICE_LINEAR_STEPS: &[f64] = &[1.0, 2.0, 2.5, 5.0];

/// Drops float noise: `float(f"{v:.10g}")`.
fn clean(v: f64) -> f64 {
    format_g(v, 10).parse().unwrap_or(v)
}

fn decade_ticks(lo: f64, hi: f64, mantissas: &[f64]) -> Vec<f64> {
    let lo_exp = lo.log10().floor() as i32;
    let hi_exp = hi.log10().ceil() as i32;
    let (low, high) = (lo * (1.0 - 1e-9), hi * (1.0 + 1e-9));
    let mut out = Vec::new();
    for e in lo_exp..=hi_exp {
        let scale = 10f64.powi(e);
        for m in mantissas {
            let v = clean(m * scale);
            if low <= v && v <= high {
                out.push(v);
            }
        }
    }
    out.sort_by(f64::total_cmp);
    out.dedup();
    out
}

fn nice_linear_ticks(lo: f64, hi: f64, max_labels: usize) -> Vec<f64> {
    let span = hi - lo;
    if span <= 0.0 {
        return Vec::new();
    }
    let base_exp = (span / (max_labels.saturating_sub(1).max(2)) as f64).log10().floor() as i32;
    for e in [base_exp, base_exp + 1, base_exp - 1, base_exp + 2] {
        for f in NICE_LINEAR_STEPS {
            let step = f * 10f64.powi(e);
            if step <= 0.0 {
                continue;
            }
            let first = (lo / step).ceil() * step;
            // np.arange(first, hi + step * 1e-6, step)
            let n = ((hi + step * 1e-6 - first) / step).ceil().max(0.0) as usize;
            let mut ticks: Vec<f64> = (0..n).map(|i| clean(first + i as f64 * step)).collect();
            ticks.retain(|t| lo - step * 1e-6 <= *t && *t <= hi + step * 1e-6);
            if (3..=max_labels).contains(&ticks.len()) {
                return ticks;
            }
        }
    }
    super::numeric::linspace(lo, hi, max_labels.min(5)).into_iter().map(clean).collect()
}

/// `(labelled, unlabelled)` tick frequencies in MHz for a log axis.
pub fn log_frequency_ticks_mhz(lo_mhz: f64, hi_mhz: f64, max_labels: usize) -> (Vec<f64>, Vec<f64>) {
    let (mut lo, mut hi) = (lo_mhz, hi_mhz);
    if lo > hi {
        std::mem::swap(&mut lo, &mut hi);
    }
    if !(lo.is_finite() && hi.is_finite()) || lo <= 0.0 || hi <= lo {
        return (Vec::new(), Vec::new());
    }
    let budget = max_labels.max(2);
    let mut labelled = Vec::new();
    for mantissas in LOG_LABEL_LADDER {
        let ticks = decade_ticks(lo, hi, mantissas);
        if ticks.len() > budget {
            break;
        }
        if ticks.len() >= 2 {
            labelled = ticks;
        }
    }
    if labelled.is_empty() {
        let decades = decade_ticks(lo, hi, &[1.0]);
        labelled = if decades.len() > budget {
            let stride = decades.len().div_ceil(budget);
            decades.into_iter().step_by(stride).collect()
        } else {
            nice_linear_ticks(lo, hi, budget)
        };
    }
    let mut minor: Vec<f64> =
        decade_ticks(lo, hi, LOG_MINOR_MANTISSAS).into_iter().filter(|v| !labelled.contains(v)).collect();
    if minor.is_empty() {
        let fine: Vec<f64> = (0..18).map(|i| 1.0 + 0.5 * i as f64).collect();
        minor = decade_ticks(lo, hi, &fine).into_iter().filter(|v| !labelled.contains(v)).collect();
    }
    if minor.len() > MAX_MINOR_TICKS {
        let stride = minor.len().div_ceil(MAX_MINOR_TICKS);
        minor = minor.into_iter().step_by(stride).collect();
    }
    (labelled, minor)
}

/// Centres of `count` equal log10 bins filling `[lo, hi]`.
pub fn log_frequency_bin_rows(lo_mhz: f64, hi_mhz: f64, count: usize, descending: bool) -> Option<Vec<f64>> {
    let (mut lo, mut hi) = (lo_mhz, hi_mhz);
    if lo > hi {
        std::mem::swap(&mut lo, &mut hi);
    }
    if !(lo.is_finite() && hi.is_finite()) || lo <= 0.0 || hi <= lo {
        return None;
    }
    let rows = count.max(2);
    let edges = super::numeric::linspace(lo.log10(), hi.log10(), rows + 1);
    let mut centres: Vec<f64> = edges.windows(2).map(|w| 0.5 * (w[0] + w[1])).collect();
    if descending {
        centres.reverse();
    }
    Some(centres)
}

/// `log_frequency_rows`: uniform log10 row centres from the lowest to the
/// highest positive frequency, in the input's order.
pub fn log_frequency_rows(freqs: &[f64], count: usize) -> Option<Vec<f64>> {
    let finite: Vec<f64> = freqs.iter().copied().filter(|f| f.is_finite() && *f > 0.0).collect();
    if finite.len() < 2 {
        return None;
    }
    let lo = finite.iter().copied().fold(f64::INFINITY, f64::min).log10();
    let hi = finite.iter().copied().fold(f64::NEG_INFINITY, f64::max).log10();
    if !lo.is_finite() || !hi.is_finite() || hi <= lo {
        return None;
    }
    let mut grid = super::numeric::linspace(lo, hi, count.max(2));
    if freqs.len() >= 2 && freqs[0] > freqs[freqs.len() - 1] {
        grid.reverse();
    }
    Some(grid)
}

/// The log rows for `freqs`: bins filling `bounds` when given, otherwise
/// centres spanning the channels (`count == 0` means one per channel).
fn log_rows_for(freqs: &[f64], count: usize, bounds: Option<(f64, f64)>) -> Option<Vec<f64>> {
    let count = if count == 0 { freqs.len() } else { count };
    match bounds {
        None => log_frequency_rows(freqs, count),
        Some((lo, hi)) => {
            let descending = freqs.len() >= 2 && freqs[0] > freqs[freqs.len() - 1];
            log_frequency_bin_rows(lo, hi, count, descending)
        }
    }
}

/// `log_frequency_row_indices`: the log rows and, for each, the nearest
/// source row, for row-indexed data such as a gap mask.
pub fn log_frequency_row_indices(
    freqs: &[f64],
    count: usize,
    bounds: Option<(f64, f64)>,
) -> Option<(Vec<f64>, Vec<usize>)> {
    let log_rows = log_rows_for(freqs, count, bounds)?;
    let mut src: Vec<(f64, usize)> =
        freqs.iter().enumerate().filter(|(_, f)| f.is_finite() && **f > 0.0).map(|(i, f)| (f.log10(), i)).collect();
    if src.len() < 2 {
        return None;
    }
    src.sort_by(|a, b| a.0.total_cmp(&b.0));
    let nearest = log_rows
        .iter()
        .map(|&v| {
            // np.searchsorted (side='left'), clipped to 1..n-1.
            let right = src.partition_point(|s| s.0 < v).clamp(1, src.len() - 1);
            let left = right - 1;
            if (v - src[left].0) > (src[right].0 - v) { src[right].1 } else { src[left].1 }
        })
        .collect();
    Some((log_rows, nearest))
}

/// `resample_row_mask_to_log_frequency`: a per-row mask carried onto the log rows.
pub fn resample_row_mask_to_log_frequency(
    mask: Option<&[bool]>,
    freqs: &[f64],
    count: usize,
    bounds: Option<(f64, f64)>,
) -> Option<Vec<bool>> {
    let mask = mask?;
    if mask.len() != freqs.len() {
        return None;
    }
    let (_, nearest) = log_frequency_row_indices(freqs, count, bounds)?;
    Some(nearest.into_iter().map(|i| mask[i]).collect())
}

/// Map a spectrogram onto `count` uniformly log-spaced rows filling
/// `bounds` (MHz). Returns `(resampled, log10 row centres)`.
pub fn resample_rows_to_log_frequency(
    data: &Array2,
    freqs: &[f64],
    count: usize,
    bounds: Option<(f64, f64)>,
) -> Option<(Array2, Vec<f64>)> {
    let log_rows = log_rows_for(freqs, count, bounds)?;
    let mut src: Vec<(f64, usize)> =
        freqs.iter().enumerate().filter(|(_, f)| f.is_finite() && **f > 0.0).map(|(i, f)| (f.log10(), i)).collect();
    if src.len() < 2 {
        return None;
    }
    src.sort_by(|a, b| a.0.total_cmp(&b.0));
    let xs: Vec<f64> = src.iter().map(|s| s.0).collect();
    let mut out = Array2::filled(log_rows.len(), data.cols, 0.0);
    for (r, &target) in log_rows.iter().enumerate() {
        // np.interp: clamp outside, linear inside.
        let k = xs.partition_point(|&x| x <= target);
        let row = out.row_mut(r);
        if k == 0 {
            row.copy_from_slice(data.row(src[0].1));
        } else if k >= xs.len() {
            row.copy_from_slice(data.row(src[xs.len() - 1].1));
        } else {
            let (x0, x1) = (xs[k - 1], xs[k]);
            let t = if x1 > x0 { (target - x0) / (x1 - x0) } else { 0.0 };
            let (a, b) = (data.row(src[k - 1].1), data.row(src[k].1));
            for c in 0..row.len() {
                let (va, vb) = (a[c] as f64, b[c] as f64);
                row[c] = (va + (vb - va) * t) as f32;
            }
        }
    }
    Some((out, log_rows))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn steps_edges_and_direction() {
        let f = [80.0, 79.0, 78.0, 77.0];
        assert_eq!(dominant_frequency_direction(&f, 1), -1);
        assert_eq!(frequency_step_mhz(&f, 1.0), 1.0);
        assert_eq!(axis_edges(&f, 1.0), vec![80.5, 79.5, 78.5, 77.5, 76.5]);
        assert_eq!(orient_frequency_axis(&f, 1), vec![77.0, 78.0, 79.0, 80.0]);
    }

    #[test]
    fn frequency_labels() {
        assert_eq!(format_frequency_mhz(200.0), "200");
        assert_eq!(format_frequency_mhz(45.0), "45");
        assert_eq!(format_frequency_mhz(45.5), "45.5");
        assert_eq!(format_frequency_mhz(1.25), "1.25");
        assert_eq!(format_g(0.0123456, 4), "0.01235");
    }

    #[test]
    fn log_ticks_for_callisto_band() {
        let (labels, _) = log_frequency_ticks_mhz(20.0, 80.0, 10);
        assert_eq!(labels, vec![20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]);
        let (labels, _) = log_frequency_ticks_mhz(45.0, 870.0, 10);
        assert_eq!(labels, vec![50.0, 70.0, 100.0, 200.0, 300.0, 500.0, 700.0]);
    }
}
