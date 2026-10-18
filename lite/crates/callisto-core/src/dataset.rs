//! A dataset is one or more loaded files arranged into segments.
//!
//! Files with the same instrument, focus code and frequency axis are joined in
//! time into one segment. Files with a different setup become separate segments,
//! drawn on the same time/frequency axes (so two receivers covering different
//! bands appear stacked in frequency).

use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Result, bail};
use chrono::{NaiveDate, NaiveDateTime};

use crate::callisto::{CallistoFile, DB_PER_DIGIT, secs};
use crate::fits::Header;
use crate::util::{mean_finite, median_subsampled, par_rows, quantile_in_place};

pub struct FileInfo {
    pub path: PathBuf,
    pub name: String,
    pub start: NaiveDateTime,
    pub end: NaiveDateTime,
    pub n_time: usize,
    pub n_freq: usize,
    pub headers: Vec<(String, Header)>,
    pub segment: usize,
}

#[derive(Clone)]
pub struct Segment {
    pub label: String,
    /// Seconds after the dataset's reference midnight, ascending.
    pub times: Arc<Vec<f64>>,
    /// MHz, ascending.
    pub freqs: Arc<Vec<f64>>,
    /// Frequency interval covered by each channel, used for lookups.
    pub f_lo: Arc<Vec<f64>>,
    pub f_hi: Arc<Vec<f64>>,
    /// `n_freq` rows × `n_time` columns, as read from the files.
    pub raw: Arc<Vec<f32>>,
    /// Same layout after background subtraction / unit conversion.
    pub data: Arc<Vec<f32>>,
    /// Typical sample spacing in seconds.
    pub dt: f64,
    pub visible: bool,
}

impl Segment {
    pub fn n_time(&self) -> usize {
        self.times.len()
    }

    pub fn n_freq(&self) -> usize {
        self.freqs.len()
    }

    pub fn t_range(&self) -> (f64, f64) {
        (self.times[0], *self.times.last().unwrap())
    }

    pub fn f_range(&self) -> (f64, f64) {
        (self.f_lo[0], *self.f_hi.last().unwrap())
    }

    #[inline]
    pub fn value(&self, row: usize, col: usize) -> f32 {
        self.data[row * self.n_time() + col]
    }

    /// Channel whose frequency interval contains `f`.
    #[inline]
    pub fn chan_at(&self, f: f64) -> Option<usize> {
        let i = self.f_hi.partition_point(|&h| h <= f);
        (i < self.f_hi.len() && f >= self.f_lo[i]).then_some(i)
    }

    /// Nearest sample to `t`, unless `t` falls in a gap in the data.
    #[inline]
    pub fn time_index(&self, t: f64) -> Option<usize> {
        let i = self.times.partition_point(|&x| x < t);
        let best = match (i.checked_sub(1), (i < self.times.len()).then_some(i)) {
            (Some(a), Some(b)) => {
                if t - self.times[a] <= self.times[b] - t {
                    a
                } else {
                    b
                }
            }
            (Some(a), None) => a,
            (None, Some(b)) => b,
            (None, None) => return None,
        };
        ((self.times[best] - t).abs() <= self.dt).then_some(best)
    }

    /// Index range of samples with `t0 <= t < t1`.
    #[inline]
    pub fn time_range_idx(&self, t0: f64, t1: f64) -> (usize, usize) {
        (self.times.partition_point(|&x| x < t0), self.times.partition_point(|&x| x < t1))
    }

    fn process(&self, p: &Processing) -> Arc<Vec<f32>> {
        if p.is_identity() {
            return self.raw.clone();
        }
        let nt = self.n_time();
        let nf = self.n_freq();
        let scale = if p.to_db { DB_PER_DIGIT as f32 } else { 1.0 };
        let (a, b) = match p.interval {
            Some((t0, t1)) if p.background != Background::None => {
                let (a, b) = self.time_range_idx(t0.min(t1), t0.max(t1));
                if b - a >= 2 { (a, b) } else { (0, nt) }
            }
            _ => (0, nt),
        };

        let mut out = vec![0.0f32; nt * nf];
        par_rows(&mut out, nt, |r, row| {
            let src = &self.raw[r * nt..(r + 1) * nt];
            let bg = match p.background {
                Background::None => 0.0,
                Background::Mean => mean_finite(&src[a..b]).unwrap_or(0.0),
                Background::Median => median_subsampled(&src[a..b], 20_000).unwrap_or(0.0),
            };
            for (o, &v) in row.iter_mut().zip(src) {
                *o = (v - bg) * scale;
            }
        });

        if p.subtract_time_median && nf > 1 {
            // Median over all channels per time sample removes broadband stripes.
            let mut medians = vec![0.0f32; nt];
            par_rows(&mut medians, 1024.min(nt), |chunk, m| {
                let mut col = Vec::with_capacity(nf);
                for (k, slot) in m.iter_mut().enumerate() {
                    let c = chunk * 1024.min(nt) + k;
                    col.clear();
                    col.extend((0..nf).map(|r| out[r * nt + c]).filter(|v| v.is_finite()));
                    *slot = quantile_in_place(&mut col, 0.5).unwrap_or(0.0);
                }
            });
            par_rows(&mut out, nt, |_, row| {
                for (o, m) in row.iter_mut().zip(&medians) {
                    *o -= m;
                }
            });
        }
        Arc::new(out)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Background {
    None,
    Mean,
    Median,
}

impl Background {
    pub const ALL: [Background; 3] = [Background::None, Background::Mean, Background::Median];

    pub fn label(self) -> &'static str {
        match self {
            Background::None => "None (raw)",
            Background::Mean => "Mean per channel",
            Background::Median => "Median per channel",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Processing {
    pub background: Background,
    /// Quiet interval (seconds after reference midnight) used to estimate the
    /// background; the whole time range when `None`.
    pub interval: Option<(f64, f64)>,
    pub subtract_time_median: bool,
    pub to_db: bool,
}

impl Default for Processing {
    fn default() -> Self {
        Self { background: Background::None, interval: None, subtract_time_median: false, to_db: false }
    }
}

impl Processing {
    fn is_identity(&self) -> bool {
        self.background == Background::None && !self.subtract_time_median && !self.to_db
    }
}

/// A cross-section line, split into pieces at data gaps. Points are (x, value).
pub struct Curve {
    pub label: String,
    pub pieces: Vec<Vec<[f64; 2]>>,
}

#[derive(Clone)]
pub struct Dataset {
    pub ref_date: NaiveDate,
    pub files: Vec<Arc<FileInfo>>,
    pub segments: Vec<Segment>,
    pub unit: String,
}

impl Dataset {
    pub fn build(mut files: Vec<CallistoFile>) -> Result<Self> {
        if files.is_empty() {
            bail!("no files to show");
        }
        files.sort_by_key(|f| f.start);
        let ref_date = files[0].start.date();
        let midnight = ref_date.and_hms_opt(0, 0, 0).unwrap();

        let mut groups: Vec<Vec<usize>> = Vec::new();
        for (i, f) in files.iter().enumerate() {
            match groups.iter_mut().find(|g| compatible(&files[g[0]], f)) {
                Some(g) => g.push(i),
                None => groups.push(vec![i]),
            }
        }

        let mut segments = Vec::new();
        let mut infos: Vec<(usize, FileInfo)> = Vec::new();
        for (si, group) in groups.iter().enumerate() {
            let first = &files[group[0]];
            let nf = first.n_freq();
            // Column ranges to take from each file, dropping samples that overlap
            // the previous file.
            let mut parts = Vec::new();
            let mut last_t = f64::NEG_INFINITY;
            for &i in group {
                let f = &files[i];
                let offset = (f.start - midnight).as_seconds_f64();
                let c0 = f.times.partition_point(|&t| offset + t <= last_t + 1e-6);
                if c0 < f.n_time() {
                    parts.push((i, c0, offset));
                    last_t = offset + f.times[f.n_time() - 1];
                }
            }
            let total: usize = parts.iter().map(|&(i, c0, _)| files[i].n_time() - c0).sum();
            let mut times = Vec::with_capacity(total);
            for &(i, c0, offset) in &parts {
                times.extend(files[i].times[c0..].iter().map(|t| t + offset));
            }
            let mut raw = vec![0.0f32; nf * total];
            par_rows(&mut raw, total, |r, row| {
                let mut pos = 0;
                for &(i, c0, _) in &parts {
                    let f = &files[i];
                    let nt = f.n_time();
                    let src = &f.data[r * nt + c0..(r + 1) * nt];
                    row[pos..pos + src.len()].copy_from_slice(src);
                    pos += src.len();
                }
            });
            sort_columns(&mut times, &mut raw, nf);

            let label = match &first.focus_code {
                Some(fc) => format!("{} ({fc})", first.instrument),
                None => first.instrument.clone(),
            };
            let (f_lo, f_hi) = channel_edges(&first.freqs);
            let raw = Arc::new(raw);
            segments.push(Segment {
                label,
                dt: median_spacing(&times).unwrap_or(0.25),
                times: Arc::new(times),
                freqs: Arc::new(first.freqs.clone()),
                f_lo: Arc::new(f_lo),
                f_hi: Arc::new(f_hi),
                data: raw.clone(),
                raw,
                visible: true,
            });
            for &i in group {
                let f = &files[i];
                infos.push((
                    i,
                    FileInfo {
                        path: f.path.clone(),
                        name: f.name.clone(),
                        start: f.start,
                        end: f.start + secs(f.times.last().copied().unwrap_or(0.0)),
                        n_time: f.n_time(),
                        n_freq: f.n_freq(),
                        headers: f.headers.clone(),
                        segment: si,
                    },
                ));
            }
        }
        infos.sort_by_key(|(i, _)| *i);

        Ok(Self {
            ref_date,
            files: infos.into_iter().map(|(_, f)| Arc::new(f)).collect(),
            segments,
            unit: files[0].unit.clone(),
        })
    }

    pub fn apply_processing(&mut self, p: &Processing) {
        for s in &mut self.segments {
            s.data = s.process(p);
        }
    }

    pub fn visible(&self) -> impl Iterator<Item = (usize, &Segment)> {
        self.segments.iter().enumerate().filter(|(_, s)| s.visible)
    }

    fn union<F: Fn(&Segment) -> (f64, f64)>(&self, f: F) -> Option<(f64, f64)> {
        self.visible().map(|(_, s)| f(s)).reduce(|a, b| (a.0.min(b.0), a.1.max(b.1)))
    }

    pub fn time_range(&self) -> Option<(f64, f64)> {
        self.union(Segment::t_range)
    }

    pub fn freq_range(&self) -> Option<(f64, f64)> {
        self.union(Segment::f_range)
    }

    pub fn datetime(&self, t: f64) -> NaiveDateTime {
        self.ref_date.and_hms_opt(0, 0, 0).unwrap() + secs(t)
    }

    /// (segment, row, col, value) of the sample under a plot position.
    pub fn sample_at(&self, t: f64, f: f64) -> Option<(usize, usize, usize, f32)> {
        self.visible().find_map(|(si, s)| {
            let c = s.time_index(t)?;
            let r = s.chan_at(f)?;
            Some((si, r, c, s.value(r, c)))
        })
    }

    /// Intensity vs time at frequency `f` within `[t0, t1]`, min/max decimated to
    /// roughly `max_points` per segment.
    pub fn light_curves(&self, f: f64, t0: f64, t1: f64, max_points: usize) -> Vec<Curve> {
        let mut out = Vec::new();
        for (_, s) in self.visible() {
            let Some(r) = s.chan_at(f) else { continue };
            let (mut a, mut b) = s.time_range_idx(t0, t1);
            a = a.saturating_sub(1);
            b = (b + 1).min(s.n_time());
            if b <= a {
                continue;
            }
            let nt = s.n_time();
            let row = &s.data[r * nt..(r + 1) * nt];
            let bucket = ((b - a) / max_points.max(1)).max(1);
            let mut pieces = vec![Vec::new()];
            let mut prev_t = f64::NAN;
            let mut start = a;
            while start < b {
                let end = (start + bucket).min(b);
                if s.times[start] - prev_t > 5.0 * s.dt.max(bucket as f64 * s.dt) {
                    pieces.push(Vec::new());
                }
                let piece = pieces.last_mut().unwrap();
                if bucket == 1 {
                    if row[start].is_finite() {
                        piece.push([s.times[start], row[start] as f64]);
                    }
                } else {
                    let (mut lo, mut hi) = ((f32::INFINITY, start), (f32::NEG_INFINITY, start));
                    for (c, &v) in row.iter().enumerate().take(end).skip(start) {
                        if v < lo.0 {
                            lo = (v, c);
                        }
                        if v > hi.0 {
                            hi = (v, c);
                        }
                    }
                    if lo.0.is_finite() {
                        let (p, q) = if lo.1 <= hi.1 { (lo, hi) } else { (hi, lo) };
                        piece.push([s.times[p.1], p.0 as f64]);
                        piece.push([s.times[q.1], q.0 as f64]);
                    }
                }
                prev_t = s.times[end - 1];
                start = end;
            }
            pieces.retain(|p| !p.is_empty());
            out.push(Curve { label: format!("{} @ {:.2} MHz", s.label, s.freqs[r]), pieces });
        }
        out
    }

    /// Intensity vs frequency at time `t`; points are (frequency, value).
    pub fn spectra(&self, t: f64) -> Vec<Curve> {
        let mut out = Vec::new();
        for (_, s) in self.visible() {
            let Some(c) = s.time_index(t) else { continue };
            let mut pieces = vec![Vec::new()];
            for r in 0..s.n_freq() {
                if r > 0 && s.f_lo[r] > s.f_hi[r - 1] + 1e-9 {
                    pieces.push(Vec::new());
                }
                let v = s.value(r, c);
                if v.is_finite() {
                    pieces.last_mut().unwrap().push([s.freqs[r], v as f64]);
                }
            }
            pieces.retain(|p| !p.is_empty());
            out.push(Curve { label: s.label.clone(), pieces });
        }
        out
    }

    /// Low/high quantiles of the processed data over all visible segments.
    pub fn percentiles(&self, lo: f64, hi: f64) -> Option<(f32, f32)> {
        let total: usize = self.visible().map(|(_, s)| s.data.len()).sum();
        let stride = (total / 400_000).max(1);
        let mut sample: Vec<f32> = self
            .visible()
            .flat_map(|(_, s)| s.data.iter().step_by(stride).copied())
            .filter(|v| v.is_finite())
            .collect();
        let a = quantile_in_place(&mut sample, lo)?;
        let b = quantile_in_place(&mut sample, hi)?;
        Some((a, if b > a { b } else { a + 1.0 }))
    }

    pub fn title(&self) -> String {
        let mut names: Vec<&str> = self.visible().map(|(_, s)| s.label.as_str()).collect();
        names.dedup();
        let (t0, t1) = self.time_range().unwrap_or((0.0, 0.0));
        format!(
            "{}   {}   {} – {} UT",
            names.join(" + "),
            self.ref_date.format("%Y-%m-%d"),
            crate::util::fmt_time(t0, 0),
            crate::util::fmt_time(t1, 0)
        )
    }
}

fn compatible(a: &CallistoFile, b: &CallistoFile) -> bool {
    a.instrument == b.instrument
        && a.focus_code == b.focus_code
        && a.n_freq() == b.n_freq()
        && a.freqs.iter().zip(&b.freqs).all(|(x, y)| (x - y).abs() < 0.05)
}

fn median_spacing(v: &[f64]) -> Option<f64> {
    let stride = (v.len() / 5000).max(1);
    let mut d: Vec<f32> = v.windows(2).step_by(stride).map(|w| (w[1] - w[0]) as f32).filter(|d| *d > 0.0).collect();
    quantile_in_place(&mut d, 0.5).map(|m| m as f64)
}

/// Lower/upper edge of each channel: halfway to the neighbours, except across
/// large gaps in the frequency list, where a channel only covers ±spacing/2.
fn channel_edges(freqs: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let n = freqs.len();
    let spacing = median_spacing(freqs).unwrap_or(1.0);
    let gap = 4.0 * spacing;
    let half = |d: f64| if d > gap { spacing / 2.0 } else { d / 2.0 };
    let lo = (0..n)
        .map(|i| if i == 0 { freqs[0] - spacing / 2.0 } else { freqs[i] - half(freqs[i] - freqs[i - 1]) })
        .collect();
    let hi = (0..n)
        .map(|i| if i + 1 == n { freqs[i] + spacing / 2.0 } else { freqs[i] + half(freqs[i + 1] - freqs[i]) })
        .collect();
    (lo, hi)
}

/// Makes the time axis monotonic (it almost always already is).
fn sort_columns(times: &mut Vec<f64>, data: &mut Vec<f32>, nf: usize) {
    if times.windows(2).all(|w| w[0] <= w[1]) {
        return;
    }
    let nt = times.len();
    let mut order: Vec<usize> = (0..nt).collect();
    order.sort_by(|&a, &b| times[a].total_cmp(&times[b]));
    *times = order.iter().map(|&c| times[c]).collect();
    let mut out = vec![0.0f32; data.len()];
    for r in 0..nf {
        for (k, &c) in order.iter().enumerate() {
            out[r * nt + k] = data[r * nt + c];
        }
    }
    *data = out;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::callisto::tests::synthetic_file;
    use crate::fits::FitsFile;

    fn file(start: &str, name: &str, n_time: usize, freqs: &[f64]) -> CallistoFile {
        let fits = FitsFile::from_bytes(synthetic_file(start, n_time, freqs)).unwrap();
        CallistoFile::from_fits(&fits, name.into(), name.into()).unwrap()
    }

    #[test]
    fn joins_consecutive_files() {
        let freqs = [80.0, 60.0, 40.0];
        // 40 samples at 0.25 s = 10 s per file; the second file starts 10 s later.
        let a = file("2020/01/01 10:00:00", "T_20200101_100000_01.fit", 40, &freqs);
        let b = file("2020/01/01 10:00:10", "T_20200101_100010_01.fit", 40, &freqs);
        let ds = Dataset::build(vec![b, a]).unwrap();
        assert_eq!(ds.segments.len(), 1);
        let s = &ds.segments[0];
        assert_eq!(s.n_time(), 80);
        assert_eq!(s.t_range(), (36000.0, 36019.75));
        assert_eq!(ds.files[0].segment, 0);
        // Sample 45 is sample 5 of the second file; channel 0 (40 MHz) = row 2 in file.
        assert_eq!(s.value(0, 45), 25.0);
        assert_eq!(ds.sample_at(36011.25, 40.2).unwrap().3, 25.0);
        assert!(ds.sample_at(36011.25, 100.0).is_none());
    }

    #[test]
    fn different_setups_become_segments() {
        let a = file("2020/01/01 10:00:00", "T_20200101_100000_01.fit", 40, &[80.0, 60.0, 40.0]);
        let b = file("2020/01/01 10:00:00", "T_20200101_100000_02.fit", 40, &[300.0, 200.0]);
        let ds = Dataset::build(vec![a, b]).unwrap();
        assert_eq!(ds.segments.len(), 2);
        assert_eq!(ds.freq_range(), Some((30.0, 350.0)));
        assert_eq!(ds.spectra(36001.0).len(), 2);
    }

    #[test]
    fn background_subtraction() {
        let mut ds = Dataset::build(vec![file("2020/01/01 10:00:00", "x.fit", 40, &[80.0, 60.0, 40.0])]).unwrap();
        ds.apply_processing(&Processing { background: Background::Mean, ..Default::default() });
        for r in 0..3 {
            let row = &ds.segments[0].data[r * 40..(r + 1) * 40];
            assert!(mean_finite(row).unwrap().abs() < 1e-4);
        }
        ds.apply_processing(&Processing::default());
        assert!(Arc::ptr_eq(&ds.segments[0].data, &ds.segments[0].raw));
    }

    #[test]
    fn channel_edges_handle_gaps() {
        let (lo, hi) = channel_edges(&[10.0, 11.0, 12.0, 50.0, 51.0]);
        assert_eq!(hi[2], 12.5);
        assert_eq!(lo[3], 49.5);
        assert_eq!(lo[1], 10.5);
    }

    #[test]
    fn light_curve_decimates() {
        let ds = Dataset::build(vec![file("2020/01/01 10:00:00", "x.fit", 4000, &[80.0, 60.0])]).unwrap();
        let lc = ds.light_curves(60.0, 0.0, 1e6, 100);
        assert_eq!(lc.len(), 1);
        let n: usize = lc[0].pieces.iter().map(Vec::len).sum();
        assert!((100..=2 * 101).contains(&n), "{n}");
    }
}
