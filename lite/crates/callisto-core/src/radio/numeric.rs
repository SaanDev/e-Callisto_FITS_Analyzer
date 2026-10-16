//! numpy's reductions, reproduced closely enough that Lite's numbers match
//! the full app's: percentiles with numpy's linear interpolation, NaN-aware
//! medians, means and standard deviations, and `np.allclose`.

/// numpy's `_lerp(a, b, t)`, the interpolation `np.percentile` uses: it
/// interpolates from whichever end is nearer, which changes the last bits.
pub fn numpy_lerp(a: f64, b: f64, t: f64) -> f64 {
    let diff = b - a;
    if t >= 0.5 { b - diff * (1.0 - t) } else { a + diff * t }
}

/// `np.percentile(values, q)` (method "linear") of already-sorted, NaN-free
/// values. `None` when empty.
pub fn percentile_sorted(sorted: &[f64], q: f64) -> Option<f64> {
    let n = sorted.len();
    if n == 0 {
        return None;
    }
    let position = (n - 1) as f64 * (q / 100.0);
    let lower = position.floor().clamp(0.0, (n - 1) as f64) as usize;
    let upper = (lower + 1).min(n - 1);
    let t = position - lower as f64;
    Some(numpy_lerp(sorted[lower], sorted[upper], t))
}

/// `np.nanpercentile(values, q)`: NaNs dropped, infinities kept in order.
pub fn nanpercentile(values: impl IntoIterator<Item = f64>, q: f64) -> Option<f64> {
    let mut v: Vec<f64> = values.into_iter().filter(|x| !x.is_nan()).collect();
    v.sort_by(f64::total_cmp);
    percentile_sorted(&v, q)
}

/// `np.nanmedian(values)`.
pub fn nanmedian(values: impl IntoIterator<Item = f64>) -> Option<f64> {
    let mut v: Vec<f64> = values.into_iter().filter(|x| !x.is_nan()).collect();
    if v.is_empty() {
        return None;
    }
    v.sort_by(f64::total_cmp);
    let n = v.len();
    Some(if n % 2 == 1 { v[n / 2] } else { (v[n / 2 - 1] + v[n / 2]) / 2.0 })
}

/// `np.nanmean(values)`, accumulated in f64.
pub fn nanmean(values: impl IntoIterator<Item = f64>) -> Option<f64> {
    let (sum, n) = values.into_iter().filter(|x| !x.is_nan()).fold((0.0, 0usize), |(s, n), v| (s + v, n + 1));
    (n > 0).then(|| sum / n as f64)
}

/// `np.nanstd(values)` (population, ddof = 0).
pub fn nanstd(values: &[f64]) -> Option<f64> {
    let mean = nanmean(values.iter().copied())?;
    let (ss, n) =
        values.iter().filter(|x| !x.is_nan()).fold((0.0, 0usize), |(s, n), v| (s + (v - mean) * (v - mean), n + 1));
    Some((ss / n as f64).sqrt())
}

/// `np.allclose(a, b, atol=atol, rtol=0)` for equal-length slices.
pub fn allclose(a: &[f64], b: &[f64], atol: f64) -> bool {
    // numpy's default equal_nan=False: NaN is never close to anything.
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| (x - y).abs() <= atol)
}

/// `np.linspace(start, stop, num)`, endpoint included, as numpy computes it.
pub fn linspace(start: f64, stop: f64, num: usize) -> Vec<f64> {
    match num {
        0 => Vec::new(),
        1 => vec![start],
        _ => {
            let div = (num - 1) as f64;
            let step = (stop - start) / div;
            let mut out: Vec<f64> = (0..num).map(|i| start + i as f64 * step).collect();
            out[num - 1] = stop;
            out
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentiles_follow_numpy() {
        let v = [1.0, 2.0, 3.0, 4.0];
        assert_eq!(percentile_sorted(&v, 50.0), Some(2.5));
        assert_eq!(percentile_sorted(&v, 25.0), Some(1.75));
        assert_eq!(percentile_sorted(&v, 100.0), Some(4.0));
        assert_eq!(nanpercentile([3.0, f64::NAN, 1.0, 2.0], 50.0), Some(2.0));
        assert_eq!(nanmedian([4.0, 1.0, 3.0, 2.0]), Some(2.5));
        assert_eq!(linspace(0.0, 1.0, 5), vec![0.0, 0.25, 0.5, 0.75, 1.0]);
    }
}
