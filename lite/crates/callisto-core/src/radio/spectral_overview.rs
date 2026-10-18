//! Port of `src/backend/radio/spectral_overview.py`: a station's whole UTC
//! day for one focus code, background-subtracted against the day-wide row
//! median (Plotutil median dB) and reduced to a few columns per minute.
//!
//! The full app gathers the day in a memory-mapped file to take each row's
//! median. Lite takes the same medians from per-row histograms of the 8-bit
//! samples (values that are not whole numbers in 0–255 are kept as they are),
//! so a full day never has to be held at once.

use std::path::Path;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use chrono::{NaiveDate, NaiveDateTime, Timelike};

use super::array::Array2;
use super::fits_io::{load_callisto_fits, preview_callisto_fits};
use super::frequency_axis::{orient_frequency_axis, orient_frequency_rows};
use super::naming::basename;
use super::numeric::allclose;
use super::processing::PLOTUTIL_DB_SCALE;

pub const FREQUENCY_MATCH_ATOL_MHZ: f64 = 1e-3;
pub const DEFAULT_PANEL_RENDER_COLUMNS: usize = 1440;
pub const SECONDS_PER_DAY: f64 = 86400.0;
pub const SECONDS_PER_PANEL: f64 = 4.0 * 3600.0;
/// `SPECTRAL_OVERVIEW_FIGURE_SIZE`, inches.
pub const FIGURE_SIZE: (f64, f64) = (24.0, 9.0);
/// `PLOTUTIL_DISPLAY_LIMITS`.
pub const DISPLAY_LIMITS: (f64, f64) = (-1.0, 8.0);

#[derive(Clone, Debug, PartialEq)]
pub struct OverviewSource {
    pub path: String,
    pub station: String,
    pub observed_at: NaiveDateTime,
    pub focus_code: String,
    pub filename: String,
}

impl OverviewSource {
    fn name(&self) -> String {
        if self.filename.is_empty() { basename(&self.path).to_owned() } else { self.filename.clone() }
    }
}

#[derive(Clone, Debug)]
pub struct OverviewSegment {
    pub source: OverviewSource,
    /// dB above the day's row median, `(frequency, time)`.
    pub data_db: Array2,
    pub freqs: Vec<f64>,
    pub utc_seconds: Vec<f64>,
    pub frequency_group: usize,
}

#[derive(Clone, Debug)]
pub struct OverviewResult {
    pub station: String,
    pub observation_date: NaiveDate,
    pub focus_code: String,
    pub segments: Vec<OverviewSegment>,
    pub total_sources: usize,
    pub loaded_sources: usize,
    pub coverage_seconds: f64,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum OverviewError {
    Cancelled,
    Failed(String),
}

impl std::fmt::Display for OverviewError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OverviewError::Cancelled => f.write_str("Spectral overview generation was cancelled."),
            OverviewError::Failed(m) => f.write_str(m),
        }
    }
}

fn seconds_of_day(t: NaiveDateTime) -> f64 {
    t.num_seconds_from_midnight() as f64 + t.nanosecond() as f64 / 1e9
}

/// One frequency configuration of the day and its files.
struct Group {
    freqs: Vec<f64>,
    members: Vec<(OverviewSource, usize)>,
}

/// Each row's values: a histogram of whole numbers 0–255 plus the rest.
struct RowValues {
    hist: Vec<[u32; 256]>,
    other: Vec<Vec<f32>>,
}

impl RowValues {
    fn new(rows: usize) -> Self {
        RowValues { hist: vec![[0; 256]; rows], other: vec![Vec::new(); rows] }
    }

    fn add(&mut self, row: usize, values: &[f32]) {
        let (hist, other) = (&mut self.hist[row], &mut self.other[row]);
        for &v in values {
            if !v.is_finite() {
                continue;
            }
            if (0.0..=255.0).contains(&v) && v.fract() == 0.0 {
                hist[v as usize] += 1;
            } else {
                other.push(v);
            }
        }
    }

    fn merge(&mut self, o: RowValues) {
        for (a, b) in self.hist.iter_mut().zip(o.hist) {
            for (x, y) in a.iter_mut().zip(b) {
                *x += y;
            }
        }
        for (a, b) in self.other.iter_mut().zip(o.other) {
            a.extend(b);
        }
    }

    /// `np.nanmedian` of a float32 row, as float32.
    fn median(&mut self, row: usize) -> f32 {
        let other = &mut self.other[row];
        other.sort_by(f32::total_cmp);
        let hist = &self.hist[row];
        let n = hist.iter().map(|&c| c as usize).sum::<usize>() + other.len();
        if n == 0 {
            return f32::NAN;
        }
        // The k-th smallest of the histogram merged with the sorted rest.
        let kth = |k: usize| -> f32 {
            let (mut seen, mut j) = (0usize, 0usize);
            for (value, &count) in hist.iter().enumerate() {
                let v = value as f32;
                while j < other.len() && other[j] < v {
                    if seen == k {
                        return other[j];
                    }
                    seen += 1;
                    j += 1;
                }
                if k < seen + count as usize {
                    return v;
                }
                seen += count as usize;
            }
            other[j + (k - seen)]
        };
        if n % 2 == 1 { kth(n / 2) } else { (kth(n / 2 - 1) + kth(n / 2)) / 2.0 }
    }
}

/// Runs `job` for every item on all cores; results come back in order.
fn parallel<T: Sync, R: Send>(items: &[T], job: impl Fn(&T) -> R + Sync) -> Vec<R> {
    let threads = std::thread::available_parallelism().map_or(4, |n| n.get()).min(items.len()).max(1);
    let next = AtomicUsize::new(0);
    let out: Mutex<Vec<Option<R>>> = Mutex::new((0..items.len()).map(|_| None).collect());
    std::thread::scope(|s| {
        for _ in 0..threads {
            s.spawn(|| {
                loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    if i >= items.len() {
                        break;
                    }
                    let r = job(&items[i]);
                    out.lock().unwrap()[i] = Some(r);
                }
            });
        }
    });
    out.into_inner().unwrap().into_iter().map(|r| r.unwrap()).collect()
}

/// `_peak_preserving_downsample`: each block's maximum, at its mean time.
pub fn peak_preserving_downsample(data: &Array2, times: &[f64], max_columns: usize) -> (Array2, Vec<f64>) {
    let cols = data.cols;
    if cols <= max_columns {
        return (data.clone(), times.to_vec());
    }
    let count = max_columns.max(1);
    let step = cols as f64 / count as f64;
    let mut bounds: Vec<usize> = (0..=count).map(|i| (i as f64 * step).floor() as usize).collect();
    bounds[count] = cols;
    let mut out = Array2::filled(data.rows, count, f32::NAN);
    for r in 0..data.rows {
        let row = data.row(r);
        let dst = out.row_mut(r);
        for i in 0..count {
            let (a, b) = (bounds[i], bounds[i + 1].max(bounds[i] + 1).min(cols));
            let m = row[a..b].iter().copied().filter(|v| v.is_finite()).fold(f32::NEG_INFINITY, f32::max);
            dst[i] = if m.is_finite() { m } else { f32::NAN };
        }
    }
    let out_times = (0..count)
        .map(|i| {
            let (a, b) = (bounds[i], bounds[i + 1].max(bounds[i] + 1).min(times.len()));
            let seg: Vec<f64> = times.get(a..b).unwrap_or(&[]).iter().copied().filter(|t| !t.is_nan()).collect();
            if seg.is_empty() { f64::NAN } else { seg.iter().sum::<f64>() / seg.len() as f64 }
        })
        .collect();
    (out, out_times)
}

fn median_f64(mut v: Vec<f64>) -> Option<f64> {
    v.retain(|x| !x.is_nan());
    if v.is_empty() {
        return None;
    }
    v.sort_by(f64::total_cmp);
    let n = v.len();
    Some(if n % 2 == 1 { v[n / 2] } else { (v[n / 2 - 1] + v[n / 2]) / 2.0 })
}

/// `_coverage_seconds`: the day covered by segments, overlaps merged.
pub fn coverage_seconds(segments: &[OverviewSegment]) -> f64 {
    let mut intervals: Vec<(f64, f64)> = Vec::new();
    for s in segments {
        let t: Vec<f64> = s.utc_seconds.iter().copied().filter(|v| !v.is_nan()).collect();
        if s.utc_seconds.is_empty() || t.is_empty() {
            continue;
        }
        let start = t.iter().copied().fold(f64::INFINITY, f64::min);
        let mut stop = t.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if s.utc_seconds.len() > 1 {
            let diffs: Vec<f64> =
                s.utc_seconds.windows(2).map(|w| w[1] - w[0]).filter(|d| d.is_finite() && *d > 0.0).collect();
            if let Some(m) = median_f64(diffs) {
                stop += m;
            }
        }
        intervals.push((start.max(0.0), stop.min(SECONDS_PER_DAY)));
    }
    intervals.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
    let mut merged: Vec<(f64, f64)> = Vec::new();
    for (start, stop) in intervals {
        if stop <= start {
            continue;
        }
        match merged.last_mut() {
            Some(last) if start <= last.1 => last.1 = last.1.max(stop),
            _ => merged.push((start, stop)),
        }
    }
    merged.iter().map(|(a, b)| b - a).sum()
}

/// `build_spectral_overview`.
pub fn build_spectral_overview(
    sources: &[OverviewSource],
    panel_render_columns: usize,
    cancelled: &(dyn Fn() -> bool + Sync),
    progress: &mut dyn FnMut(&str),
) -> Result<OverviewResult, OverviewError> {
    let check = || if cancelled() { Err(OverviewError::Cancelled) } else { Ok(()) };
    let mut list = sources.to_vec();
    list.sort_by(|a, b| {
        let ka = if a.filename.is_empty() { &a.path } else { &a.filename };
        let kb = if b.filename.is_empty() { &b.path } else { &b.filename };
        (a.observed_at, ka).cmp(&(b.observed_at, kb))
    });
    let Some(first) = list.first().cloned() else {
        return Err(OverviewError::Failed("At least one FITS source is required.".into()));
    };
    let date = first.observed_at.date();
    for s in &list[1..] {
        if s.station.trim().to_lowercase() != first.station.trim().to_lowercase()
            || s.observed_at.date() != date
            || s.focus_code.trim() != first.focus_code.trim()
        {
            return Err(OverviewError::Failed(
                "Spectral overview sources must share one station, UTC date, and focus code.".into(),
            ));
        }
    }

    let mut warnings = Vec::new();
    progress("Inspecting FITS frequency grids...");
    let previews = parallel(&list, |s| {
        if cancelled() {
            return Err(String::new());
        }
        let p = preview_callisto_fits(Path::new(&s.path)).map_err(|e| e.to_string())?;
        let freqs = orient_frequency_axis(&p.freqs, -1);
        if p.freq_source == "index" {
            return Err("missing frequency metadata".into());
        }
        if freqs.is_empty() || p.data_shape.1 == 0 {
            return Err("missing frequency or time axis".into());
        }
        Ok((freqs, p.data_shape.1))
    });
    check()?;
    let mut groups: Vec<Group> = Vec::new();
    for (s, p) in list.iter().zip(previews) {
        match p {
            Ok((freqs, cols)) => {
                match groups
                    .iter_mut()
                    .find(|g| g.freqs.len() == freqs.len() && allclose(&g.freqs, &freqs, FREQUENCY_MATCH_ATOL_MHZ))
                {
                    Some(g) => g.members.push((s.clone(), cols)),
                    None => groups.push(Group { freqs, members: vec![(s.clone(), cols)] }),
                }
            }
            Err(e) => warnings.push(format!("{}: {e}", s.name())),
        }
    }
    if groups.is_empty() {
        return Err(OverviewError::Failed("No readable FITS sources were available for the overview.".into()));
    }

    let mut segments = Vec::new();
    let mut loaded = std::collections::BTreeSet::new();
    let n_groups = groups.len();
    for (gi, group) in groups.iter().enumerate() {
        check()?;
        let total_columns: usize = group.members.iter().map(|m| m.1).sum();
        if total_columns == 0 {
            continue;
        }
        progress(&format!("Building day-wide median for frequency configuration {}/{n_groups}...", gi + 1));
        let rows = group.freqs.len();
        let load = |source: &OverviewSource| -> Result<(Array2, Vec<f64>, Vec<f64>), String> {
            let r = load_callisto_fits(Path::new(&source.path)).map_err(|e| e.to_string())?;
            let (mut data, mut freqs) = (r.data, r.freqs);
            orient_frequency_rows(&mut data, &mut freqs, -1);
            if data.rows != rows || !allclose(&freqs, &group.freqs, FREQUENCY_MATCH_ATOL_MHZ) {
                return Err("frequency grid changed while loading".into());
            }
            Ok((data, freqs, r.time))
        };
        // First pass: every member's samples into the row medians.
        let first_pass = parallel(&group.members, |(source, expected)| {
            if cancelled() {
                return Err(String::new());
            }
            let (data, _, _) = load(source)?;
            let count = (*expected).min(data.cols);
            if count == 0 {
                return Err("FITS file contains no time samples".into());
            }
            let mut values = RowValues::new(rows);
            for r in 0..rows {
                values.add(r, &data.row(r)[..count]);
            }
            Ok(values)
        });
        check()?;
        let mut all = RowValues::new(rows);
        let mut valid = Vec::new();
        for ((source, _), result) in group.members.iter().zip(first_pass) {
            match result {
                Ok(values) => {
                    all.merge(values);
                    valid.push(source.clone());
                    loaded.insert(source.path.clone());
                }
                Err(e) => warnings.push(format!("{}: {e}", source.name())),
            }
        }
        let baseline: Vec<f32> = (0..rows).map(|r| all.median(r)).collect();
        check()?;

        // Second pass: dB against the baseline, inside the day, reduced.
        let scale = PLOTUTIL_DB_SCALE as f32;
        let second_pass = parallel(&valid, |source| -> Result<OverviewSegment, String> {
            if cancelled() {
                return Err(String::new());
            }
            let (data, freqs, time) = load(source)?;
            let count = data.cols.min(time.len());
            let base = seconds_of_day(source.observed_at);
            let utc: Vec<f64> = time[..count].iter().map(|t| base + t).collect();
            let inside: Vec<usize> =
                (0..count).filter(|&i| utc[i].is_finite() && utc[i] >= 0.0 && utc[i] < SECONDS_PER_DAY).collect();
            if inside.is_empty() {
                return Err("FITS samples fall outside the selected UTC day".into());
            }
            let mut db = Array2::filled(rows, inside.len(), 0.0);
            for (r, &b) in baseline.iter().enumerate().take(rows) {
                let (src, dst) = (data.row(r), db.row_mut(r));
                for (k, &i) in inside.iter().enumerate() {
                    dst[k] = (src[i] - b) * scale;
                }
            }
            let utc: Vec<f64> = inside.iter().map(|&i| utc[i]).collect();
            let lo = utc.iter().copied().fold(f64::INFINITY, f64::min);
            let hi = utc.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let duration = (hi - lo).max(1.0);
            let max_columns = ((panel_render_columns as f64 * duration / SECONDS_PER_PANEL).ceil() as usize).max(1);
            let (data_db, utc_seconds) = peak_preserving_downsample(&db, &utc, max_columns);
            Ok(OverviewSegment { source: source.clone(), data_db, freqs, utc_seconds, frequency_group: gi })
        });
        check()?;
        for (source, result) in valid.iter().zip(second_pass) {
            match result {
                Ok(segment) => segments.push(segment),
                Err(e) => warnings.push(format!("{}: {e}", source.name())),
            }
        }
    }
    if segments.is_empty() {
        return Err(OverviewError::Failed("No valid FITS data remained after processing.".into()));
    }
    segments.sort_by(|a, b| {
        a.utc_seconds[0]
            .total_cmp(&b.utc_seconds[0])
            .then(a.frequency_group.cmp(&b.frequency_group))
            .then(a.source.filename.cmp(&b.source.filename))
    });
    let coverage = coverage_seconds(&segments);
    Ok(OverviewResult {
        station: first.station,
        observation_date: date,
        focus_code: first.focus_code,
        segments,
        total_sources: list.len(),
        loaded_sources: loaded.len(),
        coverage_seconds: coverage,
        warnings,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn histogram_median_matches_sorting() {
        let mut rv = RowValues::new(1);
        let values = [3.0f32, 250.0, 7.5, 3.0, f32::NAN, -2.0, 100.0, 7.0];
        rv.add(0, &values);
        let mut sorted: Vec<f32> = values.iter().copied().filter(|v| v.is_finite()).collect();
        sorted.sort_by(f32::total_cmp);
        assert_eq!(sorted.len(), 7);
        assert_eq!(rv.median(0), sorted[3]);
        rv.add(0, &[300.0]);
        assert_eq!(rv.median(0), (sorted[3] + sorted[4]) / 2.0);
        let mut rv = RowValues::new(1);
        rv.add(0, &[1.0, 2.0, 300.5]);
        assert_eq!(rv.median(0), 2.0);
    }

    #[test]
    fn downsample_keeps_peaks() {
        let data = Array2::new(1, 10, vec![1.0, 5.0, 2.0, f32::NAN, f32::NAN, 0.0, 9.0, 1.0, 1.0, 3.0]);
        let times: Vec<f64> = (0..10).map(f64::from).collect();
        let (out, t) = peak_preserving_downsample(&data, &times, 4);
        // linspace(0, 10, 5) = 0, 2.5, 5, 7.5, 10 -> 0, 2, 5, 7, 10.
        assert_eq!(out.row(0)[..2], [5.0, 2.0]);
        assert_eq!(out.row(0)[2..], [9.0, 3.0]);
        assert_eq!(t, [0.5, 3.0, 5.5, 8.0]);
    }
}
