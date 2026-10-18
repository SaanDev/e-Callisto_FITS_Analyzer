//! Small shared helpers: parallel row processing, statistics and time formatting.

/// Runs `f(row_index, row)` over the rows of `out` on all available cores.
pub fn par_rows<T, F>(out: &mut [T], row_len: usize, f: F)
where
    T: Send,
    F: Fn(usize, &mut [T]) + Sync,
{
    if row_len == 0 || out.is_empty() {
        return;
    }
    let n_rows = out.len().div_ceil(row_len);
    let threads = std::thread::available_parallelism().map_or(4, |n| n.get()).min(n_rows).max(1);
    let rows_per = n_rows.div_ceil(threads);
    std::thread::scope(|s| {
        for (ci, chunk) in out.chunks_mut(rows_per * row_len).enumerate() {
            let f = &f;
            s.spawn(move || {
                for (k, row) in chunk.chunks_mut(row_len).enumerate() {
                    f(ci * rows_per + k, row);
                }
            });
        }
    });
}

/// Quantile (0..=1) of the finite values in `values`; reorders the slice.
pub fn quantile_in_place(values: &mut [f32], q: f64) -> Option<f32> {
    if values.is_empty() {
        return None;
    }
    let k = ((values.len() - 1) as f64 * q.clamp(0.0, 1.0)).round() as usize;
    let (_, v, _) = values.select_nth_unstable_by(k, f32::total_cmp);
    Some(*v)
}

/// Median of finite values, subsampled to at most `max_samples` for speed.
pub fn median_subsampled(values: &[f32], max_samples: usize) -> Option<f32> {
    let stride = (values.len() / max_samples.max(1)).max(1);
    let mut buf: Vec<f32> = values.iter().step_by(stride).copied().filter(|v| v.is_finite()).collect();
    quantile_in_place(&mut buf, 0.5)
}

pub fn mean_finite(values: &[f32]) -> Option<f32> {
    let (sum, n) = values.iter().filter(|v| v.is_finite()).fold((0.0f64, 0usize), |(s, n), &v| (s + v as f64, n + 1));
    (n > 0).then(|| (sum / n as f64) as f32)
}

/// Formats seconds after the reference midnight as `HH:MM:SS[.fff]`, with a
/// `+Nd` suffix past midnight.
pub fn fmt_time(t: f64, decimals: usize) -> String {
    let day = (t / 86400.0).floor();
    let sod = t - day * 86400.0;
    let scale = 10f64.powi(decimals as i32);
    let total = (sod * scale).round() / scale;
    let h = (total / 3600.0).floor();
    let m = ((total - h * 3600.0) / 60.0).floor();
    let s = total - h * 3600.0 - m * 60.0;
    let width = if decimals > 0 { decimals + 3 } else { 2 };
    let mut out = format!("{:02}:{:02}:{:0width$.decimals$}", h as i64, m as i64, s.max(0.0));
    if day != 0.0 {
        out.push_str(&format!(" {:+}d", day as i64));
    }
    out
}

/// Parses `HH:MM[:SS.fff]` into seconds of day.
pub fn parse_hms(s: &str) -> Option<f64> {
    crate::callisto::parse_time_of_day(s).filter(|v| v.is_finite())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_time() {
        assert_eq!(fmt_time(3661.25, 2), "01:01:01.25");
        assert_eq!(fmt_time(59.999, 0), "00:01:00");
        assert_eq!(fmt_time(86400.0 + 60.0, 0), "00:01:00 +1d");
    }

    #[test]
    fn quantiles() {
        let mut v = vec![5.0, 1.0, 3.0, 2.0, 4.0];
        assert_eq!(quantile_in_place(&mut v, 0.5), Some(3.0));
        assert_eq!(median_subsampled(&[1.0, f32::NAN, 9.0, 5.0], 100), Some(5.0));
    }

    #[test]
    fn par_rows_visits_all() {
        let mut v = vec![0usize; 1000];
        par_rows(&mut v, 10, |r, row| row.iter_mut().for_each(|x| *x = r));
        assert!(v.chunks(10).enumerate().all(|(r, row)| row.iter().all(|&x| x == r)));
    }
}
