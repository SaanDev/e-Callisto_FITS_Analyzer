//! Port of the combining half of `src/backend/radio/burst_processor.py`:
//! deciding whether a selection combines in time, frequency or both, and
//! doing it, with the same rules, tolerances and messages.
//!
//! Two deliberate differences, both for speed with identical results:
//! time segments are concatenated once instead of file by file, and
//! [`inspect_combination`] returns the dataset it built while validating, so
//! callers need not combine a second time.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use anyhow::{Result, anyhow, bail};
use chrono::NaiveDateTime;

use super::array::Array2;
use super::fits_io::{self, FitsLoadResult, build_combined_header, extract_ut_start_sec, preview_callisto_fits};
use super::frequency_axis::{frequency_step_mhz, orient_frequency_axis, orient_frequency_rows};
use super::naming::{basename, normalize_focus_code, parse_filename};
use super::numeric::{allclose, linspace};
use crate::fits::{Header, HeaderValue};

pub const FREQUENCY_ALIGN_ATOL_MHZ: f64 = 1e-3;
const HEADER_RANGE_TOL_FRACTION: f64 = 0.5;
const GRID_ALIGN_TOL_FRACTION: f64 = 0.25;
const HEADER_FOCUS_KEYS: [&str; 6] = ["FOCUS", "FOCUSID", "RECEIVER", "RECEIVERID", "RCVR", "RCVRID"];
const GAP_FILL_EDGE_ROWS: usize = 4;
const GAP_FILL_BACKGROUND_PERCENTILE: f64 = 25.0;
pub const MIN_CONSECUTIVE_SECONDS: f64 = 750.0;
pub const MAX_CONSECUTIVE_SECONDS: f64 = 1050.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum CombineType {
    Time,
    Frequency,
    TimeFrequency,
}

impl CombineType {
    pub fn key(self) -> &'static str {
        match self {
            CombineType::Time => "time",
            CombineType::Frequency => "frequency",
            CombineType::TimeFrequency => "time_frequency",
        }
    }
}

/// What fills the rows between two frequency bands.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum GapFill {
    /// Synthetic background interpolated across the gap.
    #[default]
    Background,
    /// Grey hatching (NaN rows).
    Hatched,
    Zero,
    /// The average of the two edge backgrounds.
    Average,
}

impl GapFill {
    pub fn key(self) -> &'static str {
        match self {
            GapFill::Background => "background",
            GapFill::Hatched => "hatched",
            GapFill::Zero => "zero",
            GapFill::Average => "average",
        }
    }

    pub fn parse(value: &str) -> Result<Self, String> {
        let t = value.trim().to_lowercase().replace(['-', ' '], "_");
        Ok(match t.as_str() {
            "background" | "interpolate" | "interpolated" | "synthetic" | "synthetic_background" => GapFill::Background,
            "hatched" | "hatch" | "blank" | "nan" | "grey_hatched" | "gray_hatched" => GapFill::Hatched,
            "zero" | "zeros" => GapFill::Zero,
            "average" | "mean" | "edge_average" => GapFill::Average,
            _ => return Err(format!("Unsupported frequency gap fill mode: {value}")),
        })
    }
}

/// Which band wins where two bands overlap.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Overlap {
    /// Lower band below the connection frequency, higher band above it.
    #[default]
    Split,
    Low,
    High,
    Reject,
}

impl Overlap {
    pub fn key(self) -> &'static str {
        match self {
            Overlap::Split => "split",
            Overlap::Low => "low",
            Overlap::High => "high",
            Overlap::Reject => "reject",
        }
    }

    pub fn parse(value: &str) -> Result<Self, String> {
        let t = value.trim().to_lowercase().replace(['-', ' '], "_");
        Ok(match t.as_str() {
            "split" | "both" | "connection" | "connect" | "midpoint" => Overlap::Split,
            "low" | "low_band" | "prefer_low" | "keep_low" | "lower" => Overlap::Low,
            "high" | "high_band" | "prefer_high" | "keep_high" | "upper" => Overlap::High,
            "reject" | "none" => Overlap::Reject,
            _ => return Err(format!("Unsupported frequency overlap policy: {value}")),
        })
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct CombineOptions {
    pub gap_fill: GapFill,
    pub overlap_policy: Overlap,
    pub overlap_connection_mhz: Option<f64>,
}

/// A combined dataset, as the full app's combine functions return it.
#[derive(Clone, Debug)]
pub struct Combined {
    pub data: Array2,
    pub freqs: Vec<f64>,
    pub time: Vec<f64>,
    pub filename: String,
    pub ut_start_sec: Option<f64>,
    pub header0: Header,
    pub sources: Vec<String>,
    pub combine_type: CombineType,
    /// Rows that are a hatched gap.
    pub gap_row_mask: Option<Vec<bool>>,
    pub frequency_step_mhz: Option<f64>,
    /// Frequency-combine settings, when frequencies were combined.
    pub options: Option<CombineOptions>,
    pub timestamp_count: Option<usize>,
    pub focus_codes: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BandRelation {
    pub low: f64,
    pub high: f64,
    pub lower_file: String,
    pub higher_file: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BandInfo {
    pub path: String,
    pub station: String,
    pub date: String,
    pub time: String,
    pub focus_code: String,
    pub freq_min: f64,
    pub freq_max: f64,
    pub frequency_step_mhz: Option<f64>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct FrequencyRelation {
    pub gaps: Vec<BandRelation>,
    pub overlaps: Vec<BandRelation>,
    pub blocks: Vec<BandInfo>,
}

impl FrequencyRelation {
    pub fn has_gap(&self) -> bool {
        !self.gaps.is_empty()
    }

    pub fn has_overlap(&self) -> bool {
        !self.overlaps.is_empty()
    }
}

#[derive(Clone, Debug, Default)]
pub struct Inspection {
    pub valid: bool,
    pub combine_type: Option<CombineType>,
    pub station: String,
    pub timestamp_groups: Vec<Vec<String>>,
    pub focus_codes: Vec<String>,
    pub frequency_relation: Option<FrequencyRelation>,
    pub observed_at: Vec<NaiveDateTime>,
    pub error: Option<String>,
    /// The dataset built while validating, with default options.
    pub combined: Option<Combined>,
}

fn invalid(error: impl Into<String>) -> Inspection {
    Inspection { valid: false, error: Some(error.into()), ..Default::default() }
}

fn stamp(t: &NaiveDateTime) -> String {
    t.format("%Y-%m-%d %H:%M:%S").to_string()
}

fn parse_observation(path: &str) -> Result<(String, NaiveDateTime, String), String> {
    let (station, date, time, focus) = parse_filename(path)?;
    let observed = NaiveDateTime::parse_from_str(&format!("{date}{time}"), "%Y%m%d%H%M%S")
        .map_err(|_| format!("Invalid CALLISTO filename format: {}", basename(path)))?;
    Ok((station, observed, focus))
}

/// `_classify_combination_structure`: the selection's shape from filenames alone.
pub fn classify_combination_structure(paths: &[String]) -> Inspection {
    let paths: Vec<&String> = paths.iter().filter(|p| !p.trim().is_empty()).collect();
    if paths.len() < 2 {
        return invalid("Select at least two FITS files to combine.");
    }
    let mut records = Vec::new();
    for path in &paths {
        match parse_observation(path) {
            Ok((station, observed, focus)) => {
                records.push((path.to_string(), station, observed, normalize_focus_code(&focus)))
            }
            Err(_) => return invalid(format!("Invalid CALLISTO filename format: {}", basename(path))),
        }
    }
    let stations: BTreeSet<&String> = records.iter().map(|r| &r.1).collect();
    if stations.len() != 1 {
        let list: Vec<&str> = stations.iter().map(|s| s.as_str()).collect();
        return invalid(format!(
            "All files in one combined dataset must come from the same station; found: {}.",
            list.join(", ")
        ));
    }
    let station = records[0].1.clone();
    let mut grouped: BTreeMap<NaiveDateTime, BTreeMap<String, String>> = BTreeMap::new();
    for (path, _, observed, focus) in &records {
        let cells = grouped.entry(*observed).or_default();
        if let Some(existing) = cells.get(focus) {
            let mut out = invalid(format!(
                "Duplicate focus code '{focus}' at {}: {} and {}.",
                stamp(observed),
                basename(existing),
                basename(path)
            ));
            out.station = station;
            return out;
        }
        cells.insert(focus.clone(), path.clone());
    }
    let ordered: Vec<NaiveDateTime> = grouped.keys().copied().collect();
    let all_focus: Vec<String> =
        grouped.values().flat_map(|c| c.keys().cloned()).collect::<BTreeSet<_>>().into_iter().collect();
    let groups: Vec<Vec<String>> = ordered.iter().map(|t| grouped[t].values().cloned().collect()).collect();
    let common = |mut ins: Inspection| {
        ins.station = station.clone();
        ins.timestamp_groups = groups.clone();
        ins.focus_codes = all_focus.clone();
        ins.observed_at = ordered.clone();
        ins
    };

    if ordered.len() == 1 {
        if all_focus.len() < 2 {
            return common(invalid("Frequency combine requires at least two distinct focus codes at one timestamp."));
        }
        return common(Inspection { valid: true, combine_type: Some(CombineType::Frequency), ..Default::default() });
    }

    let expected: BTreeSet<&String> = all_focus.iter().collect();
    let combine_type = if expected.len() > 1 {
        for t in &ordered {
            let actual: BTreeSet<&String> = grouped[t].keys().collect();
            if actual != expected {
                let missing: Vec<&str> = expected.difference(&actual).map(|s| s.as_str()).collect();
                let extra: Vec<&str> = actual.difference(&expected).map(|s| s.as_str()).collect();
                let mut parts = Vec::new();
                if !missing.is_empty() {
                    parts.push(format!("missing focus code(s): {}", missing.join(", ")));
                }
                if !extra.is_empty() {
                    parts.push(format!("unexpected focus code(s): {}", extra.join(", ")));
                }
                let mut out =
                    invalid(format!("Incomplete time/frequency grid at {} ({}).", stamp(t), parts.join("; ")));
                out.combine_type = Some(CombineType::TimeFrequency);
                return common(out);
            }
        }
        CombineType::TimeFrequency
    } else {
        CombineType::Time
    };

    for w in ordered.windows(2) {
        let delta = (w[1] - w[0]).num_milliseconds() as f64 / 1000.0;
        if !(MIN_CONSECUTIVE_SECONDS..=MAX_CONSECUTIVE_SECONDS).contains(&delta) {
            let mut out = invalid(format!(
                "Non-consecutive observation timestamps: {} to {} is {delta:.0} seconds; expected {MIN_CONSECUTIVE_SECONDS:.0}-{MAX_CONSECUTIVE_SECONDS:.0} seconds.",
                stamp(&w[0]),
                stamp(&w[1])
            ));
            out.combine_type = Some(combine_type);
            return common(out);
        }
    }
    common(Inspection { valid: true, combine_type: Some(combine_type), ..Default::default() })
}

/// Validates a selection fully, by combining it with default options.
pub fn inspect_combination(paths: &[String]) -> Inspection {
    let mut structural = classify_combination_structure(paths);
    if !structural.valid {
        return structural;
    }
    let combine_type = structural.combine_type.unwrap();
    let mut relation = None;
    let attempt = (|| -> Result<Combined> {
        match combine_type {
            CombineType::Time => combine_time(paths),
            CombineType::Frequency => {
                let group = &structural.timestamp_groups[0];
                relation = Some(describe_frequency_combination(group)?);
                combine_frequency(group, CombineOptions::default())
            }
            CombineType::TimeFrequency => {
                relation = Some(describe_frequency_combination(&structural.timestamp_groups[0])?);
                combine_time_frequency(paths, CombineOptions::default())
            }
        }
    })();
    structural.frequency_relation = relation;
    match attempt {
        Ok(combined) => {
            structural.combined = Some(combined);
            structural
        }
        Err(e) => {
            structural.valid = false;
            structural.error = Some(e.to_string());
            structural
        }
    }
}

/// Combines a valid time, frequency or time-frequency selection.
pub fn combine_compatible(paths: &[String], options: CombineOptions) -> Result<Combined> {
    let structural = classify_combination_structure(paths);
    if !structural.valid {
        bail!(structural.error.unwrap_or_else(|| "The selected FITS files cannot be combined.".into()));
    }
    match structural.combine_type.unwrap() {
        CombineType::Time => combine_time(paths),
        CombineType::Frequency => combine_frequency(paths, options),
        CombineType::TimeFrequency => combine_time_frequency(paths, options),
    }
}

// ------------------------------------------------------------------ time

fn validated_time_paths(paths: &[String]) -> Result<Vec<String>> {
    let s = classify_combination_structure(paths);
    if !s.valid || s.combine_type != Some(CombineType::Time) {
        bail!(s.error.unwrap_or_else(|| "The selected files are not time-combinable.".into()));
    }
    Ok(s.timestamp_groups.into_iter().map(|g| g[0].clone()).collect())
}

fn time_sample_step(time: &[f64], label: &str) -> Result<f64> {
    if time.len() < 2 {
        bail!("{label} must contain at least two time samples.");
    }
    let step = time[1] - time[0];
    if !step.is_finite() || step <= 0.0 {
        bail!("{label} has an invalid time sample interval.");
    }
    Ok(step)
}

struct Segment {
    data: Array2,
    freqs: Vec<f64>,
    time: Vec<f64>,
    label: String,
    header0: Option<Header>,
    gap_row_mask: Option<Vec<bool>>,
    frequency_step_mhz: Option<f64>,
}

fn stitch_time_segments(
    segments: Vec<Segment>,
    combine_type: CombineType,
    filename: String,
    sources: Vec<String>,
) -> Result<Combined> {
    if segments.len() < 2 {
        bail!("Need at least two time segments to combine.");
    }
    let first = &segments[0];
    let reference_freqs = first.freqs.clone();
    let reference_gap = first.gap_row_mask.clone();
    let reference_step = first.frequency_step_mhz;
    let header0 = first.header0.clone();
    let mut time = Vec::new();
    for (index, seg) in segments.iter().enumerate() {
        if seg.data.rows != seg.freqs.len() || seg.data.cols != seg.time.len() {
            bail!(
                "{} data shape ({}, {}) does not match its frequency/time axes ({}, {}).",
                seg.label,
                seg.data.rows,
                seg.data.cols,
                seg.freqs.len(),
                seg.time.len()
            );
        }
        let step = time_sample_step(&seg.time, &seg.label)?;
        if index == 0 {
            time.extend_from_slice(&seg.time);
            continue;
        }
        if !allclose(&seg.freqs, &reference_freqs, 0.01) {
            bail!("Combined frequency axis mismatch in {}.", seg.label);
        }
        if reference_gap.is_some() != seg.gap_row_mask.is_some() || reference_gap != seg.gap_row_mask {
            bail!("Frequency gap layout mismatch in {}.", seg.label);
        }
        if reference_step.is_some() || seg.frequency_step_mhz.is_some() {
            match (reference_step, seg.frequency_step_mhz) {
                (Some(a), Some(b)) if (a - b).abs() <= FREQUENCY_ALIGN_ATOL_MHZ => {}
                _ => bail!("Frequency channel spacing mismatch in {}.", seg.label),
            }
        }
        let shift = time.last().unwrap() + step;
        time.extend(seg.time.iter().map(|t| t + shift));
    }
    let parts: Vec<&Array2> = segments.iter().map(|s| &s.data).collect();
    let data = Array2::concat_cols(&parts);
    let header =
        build_combined_header(header0.as_ref(), combine_type.key(), &sources, data.shape(), &reference_freqs, &time);
    Ok(Combined {
        ut_start_sec: header0.as_ref().and_then(extract_ut_start_sec),
        data,
        freqs: reference_freqs,
        time,
        filename,
        header0: header,
        sources,
        combine_type,
        gap_row_mask: reference_gap,
        frequency_step_mhz: reference_step,
        options: None,
        timestamp_count: None,
        focus_codes: Vec::new(),
    })
}

/// Loads files on all cores, keeping their order.
pub fn load_many(paths: &[String]) -> Result<Vec<FitsLoadResult>> {
    let threads = std::thread::available_parallelism().map_or(4, |n| n.get()).min(paths.len().max(1));
    let per = paths.len().div_ceil(threads).max(1);
    std::thread::scope(|s| {
        let handles: Vec<_> = paths
            .chunks(per)
            .map(|chunk| {
                s.spawn(move || chunk.iter().map(|p| fits_io::load_callisto_fits(Path::new(p))).collect::<Vec<_>>())
            })
            .collect();
        let mut out = Vec::with_capacity(paths.len());
        for (h, chunk) in handles.into_iter().zip(paths.chunks(per)) {
            for (result, path) in h.join().expect("loader thread panicked").into_iter().zip(chunk) {
                out.push(result.map_err(|e| anyhow!("{}: {e:#}", basename(path)))?);
            }
        }
        Ok(out)
    })
}

pub fn combine_time(paths: &[String]) -> Result<Combined> {
    let sorted = validated_time_paths(paths)?;
    let loaded = load_many(&sorted)?;
    let segments = loaded
        .into_iter()
        .zip(&sorted)
        .map(|(r, path)| Segment {
            data: r.data,
            freqs: r.freqs,
            time: r.time,
            label: basename(path).to_string(),
            header0: Some(r.header0),
            gap_row_mask: None,
            frequency_step_mhz: None,
        })
        .collect();
    let (station, date, _, _) = parse_filename(&sorted[0]).map_err(|e| anyhow!(e))?;
    stitch_time_segments(segments, CombineType::Time, format!("{station}_{date}_combined_time"), sorted)
}

// ------------------------------------------------------------- frequency

fn header_focus_code(h: &Header) -> String {
    HEADER_FOCUS_KEYS
        .iter()
        .filter_map(|k| h.get_str(k))
        .map(|v| normalize_focus_code(&v))
        .find(|t| !t.is_empty())
        .unwrap_or_default()
}

fn header_frequency_range(h: &Header) -> Option<(f64, f64)> {
    let lo = h.get_f64("FREQMIN")?;
    let hi = h.get_f64("FREQMAX")?;
    (lo.is_finite() && hi.is_finite()).then(|| (lo.min(hi), lo.max(hi)))
}

fn preview_frequency_step(h: &Header, freqs: &[f64]) -> f64 {
    let step = frequency_step_mhz(freqs, 0.0);
    if step.is_finite() && step > 0.0 {
        return step;
    }
    h.get_f64("CDELT2").map(f64::abs).unwrap_or(0.0)
}

fn range_tol(step: f64, fraction: f64) -> f64 {
    FREQUENCY_ALIGN_ATOL_MHZ.max(step.abs() * fraction)
}

fn grid_align_tol(step: f64) -> f64 {
    range_tol(step, GRID_ALIGN_TOL_FRACTION)
}

fn finite_min_max(v: &[f64]) -> (f64, f64) {
    v.iter().filter(|x| !x.is_nan()).fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), &x| (a.min(x), b.max(x)))
}

fn resolved_frequency_range(h: &Header, freqs: &[f64]) -> (f64, f64) {
    header_frequency_range(h).unwrap_or_else(|| finite_min_max(freqs))
}

fn validate_header_frequency_range(h: &Header, freqs: &[f64], step: f64, filename: &str) -> Result<()> {
    let Some((hlo, hhi)) = header_frequency_range(h) else { return Ok(()) };
    let (amin, amax) = finite_min_max(freqs);
    let tol = range_tol(step, HEADER_RANGE_TOL_FRACTION);
    if (amin - hlo).abs() > tol || (amax - hhi).abs() > tol {
        bail!(
            "Header frequency range does not match axis values for {filename}: header={hlo:.6}-{hhi:.6} MHz, axis={amin:.6}-{amax:.6} MHz."
        );
    }
    Ok(())
}

/// Gaps and overlaps between the bands of one timestamp.
pub fn describe_frequency_combination(paths: &[String]) -> Result<FrequencyRelation> {
    if paths.len() < 2 {
        return Ok(FrequencyRelation::default());
    }
    let mut blocks = Vec::new();
    let mut steps = Vec::new();
    for fp in paths {
        let (station, date, time, focus) = parse_filename(fp).unwrap_or_default();
        let preview = preview_callisto_fits(Path::new(fp))?;
        let freqs = orient_frequency_axis(&preview.freqs, 1);
        if freqs.is_empty() {
            continue;
        }
        let step = preview_frequency_step(&preview.header0, &freqs);
        if step.is_finite() && step > 0.0 {
            steps.push(step);
        }
        let (lo, hi) = resolved_frequency_range(&preview.header0, &freqs);
        blocks.push(BandInfo {
            path: fp.clone(),
            station,
            date,
            time,
            focus_code: normalize_focus_code(&focus),
            freq_min: lo,
            freq_max: hi,
            frequency_step_mhz: step.is_finite().then_some(step),
        });
    }
    blocks.sort_by(|a, b| {
        a.freq_min.total_cmp(&b.freq_min).then(a.freq_max.total_cmp(&b.freq_max)).then(a.path.cmp(&b.path))
    });
    let step_ref = steps.iter().copied().fold(f64::INFINITY, f64::min);
    let step_ref = if step_ref.is_finite() { step_ref } else { 1.0 };
    let tol = range_tol(step_ref, GRID_ALIGN_TOL_FRACTION);
    let mut gaps = Vec::new();
    let mut overlaps = Vec::new();
    if let Some(first) = blocks.first() {
        let mut active = first.clone();
        let mut active_high = active.freq_max;
        for block in &blocks[1..] {
            if block.freq_min > active_high + tol {
                gaps.push(BandRelation {
                    low: active_high,
                    high: block.freq_min,
                    lower_file: active.path.clone(),
                    higher_file: block.path.clone(),
                });
            } else {
                let lo = active.freq_min.max(block.freq_min);
                let hi = active_high.min(block.freq_max);
                if hi >= lo - tol {
                    overlaps.push(BandRelation {
                        low: lo,
                        high: hi,
                        lower_file: active.path.clone(),
                        higher_file: block.path.clone(),
                    });
                }
            }
            if block.freq_max > active_high {
                active = block.clone();
                active_high = block.freq_max;
            }
        }
    }
    Ok(FrequencyRelation { gaps, overlaps, blocks })
}

struct PreparedBlock {
    path: String,
    freqs: Vec<f64>,
    header0: Header,
    freq_min: f64,
    freq_max: f64,
}

struct Prepared {
    blocks: Vec<PreparedBlock>,
    data: Array2,
    freqs: Vec<f64>,
    time: Vec<f64>,
    gap_row_mask: Option<Vec<bool>>,
    gap_row_count: usize,
    header0: Header,
    frequency_step_mhz: f64,
}

fn overlap_replace_mask(
    freqs: &[f64],
    policy: Overlap,
    overlap_min: f64,
    overlap_max: f64,
    connection: Option<f64>,
) -> Result<Vec<bool>> {
    Ok(match policy {
        Overlap::Low => vec![false; freqs.len()],
        Overlap::High => vec![true; freqs.len()],
        Overlap::Reject => bail!("Frequency bands overlap or interleave; selected overlap policy rejects overlap."),
        Overlap::Split => {
            let (lo, hi) = (overlap_min.min(overlap_max), overlap_min.max(overlap_max));
            let c = connection.unwrap_or(0.5 * (lo + hi)).clamp(lo, hi);
            freqs.iter().map(|f| *f > c).collect()
        }
    })
}

fn prepare_frequency_blocks(paths: &[String], options: CombineOptions) -> Result<Prepared> {
    if paths.len() < 2 {
        bail!("Need at least 2 files to combine frequencies.");
    }
    let connection = options.overlap_connection_mhz.filter(|v| v.is_finite());
    let (s_ref, d_ref, t_ref, _) =
        parse_filename(&paths[0]).map_err(|_| anyhow!("Invalid CALLISTO filename format."))?;
    let mut receivers = BTreeSet::new();
    let mut previews = Vec::new();
    let mut time_ref: Option<Vec<f64>> = None;
    let mut source_steps = Vec::new();
    for fp in paths {
        let name = basename(fp);
        let (s, d, t, rec) = parse_filename(fp).map_err(|_| anyhow!("Invalid CALLISTO filename format: {name}"))?;
        if s != s_ref || d != d_ref || t != t_ref {
            bail!("Frequency combine requires the same station, date, and timestamp.");
        }
        let rec = normalize_focus_code(&rec);
        if !receivers.insert(rec.clone()) {
            bail!("Frequency combine requires distinct focus codes.");
        }
        let preview = preview_callisto_fits(Path::new(fp))?;
        let header_focus = header_focus_code(&preview.header0);
        if !header_focus.is_empty() && header_focus != rec {
            bail!("Focus code mismatch for {name}: filename='{rec}', header='{header_focus}'.");
        }
        if preview.freq_source == "index" {
            bail!("Frequency axis metadata are missing in {name}; cannot preflight frequency combine.");
        }
        let freqs = orient_frequency_axis(&preview.freqs, 1);
        if freqs.is_empty() {
            bail!("Frequency axis cannot be empty.");
        }
        if preview.time.is_empty() {
            bail!("Time axis cannot be empty.");
        }
        match &time_ref {
            None => time_ref = Some(preview.time.clone()),
            Some(r) if !allclose(&preview.time, r, 0.01) => {
                bail!("Time arrays do not match; cannot frequency-combine.")
            }
            _ => {}
        }
        let step = preview_frequency_step(&preview.header0, &freqs);
        if !step.is_finite() || step <= 0.0 {
            bail!("Could not determine channel spacing for {name}.");
        }
        source_steps.push(step);
        validate_header_frequency_range(&preview.header0, &freqs, step, name)?;
        let (lo, hi) = resolved_frequency_range(&preview.header0, &freqs);
        previews.push(PreparedBlock { path: fp.clone(), freqs, header0: preview.header0, freq_min: lo, freq_max: hi });
    }
    // Python's sort is stable; so is Rust's.
    previews.sort_by(|a, b| a.freq_min.total_cmp(&b.freq_min).then(a.freq_max.total_cmp(&b.freq_max)));

    let grid_step_ref = source_steps.iter().copied().fold(f64::INFINITY, f64::min);
    if !grid_step_ref.is_finite() || grid_step_ref <= 0.0 {
        bail!("Could not determine a shared frequency spacing.");
    }
    let overlap_tol = range_tol(grid_step_ref, GRID_ALIGN_TOL_FRACTION);
    let mut prev_high: Option<f64> = None;
    for b in &previews {
        if let Some(ph) = prev_high
            && b.freq_min <= ph + overlap_tol
            && options.overlap_policy == Overlap::Reject
        {
            bail!("Frequency bands overlap or interleave; selected overlap policy rejects overlap.");
        }
        prev_high = Some(b.freq_max);
    }

    let overall_min = previews[0].freq_min;
    let overall_max = previews.last().unwrap().freq_max;
    let total_span = overall_max - overall_min;
    if total_span <= 0.0 {
        bail!("Combined frequency range must span more than one channel.");
    }
    // Python's round() rounds half to even.
    let grid_count = ((total_span / grid_step_ref).round_ties_even() as i64).max(1) as usize;
    let grid = linspace(overall_min, overall_max, grid_count + 1);
    let grid_step = if grid.len() > 1 { grid[1] - grid[0] } else { grid_step_ref };
    let time_ref = time_ref.unwrap();
    let ncols = time_ref.len();
    let mut combined = vec![0.0f64; grid.len() * ncols];
    let mut filled = vec![false; grid.len()];

    let loaded = load_many(&previews.iter().map(|p| p.path.clone()).collect::<Vec<_>>())?;
    for (preview, mut res) in previews.iter().zip(loaded) {
        orient_frequency_rows(&mut res.data, &mut res.freqs, 1);
        let name = basename(&preview.path);
        if !allclose(&res.time, &time_ref, 0.01) {
            bail!("Time arrays do not match; cannot frequency-combine.");
        }
        let tol = grid_align_tol(grid_step);
        if !allclose(&res.freqs, &preview.freqs, tol) {
            bail!("Frequency axis changed while loading {name}.");
        }
        let target_rows: Vec<usize> = (0..grid.len())
            .filter(|&i| grid[i] >= preview.freq_min - tol && grid[i] <= preview.freq_max + tol)
            .collect();
        if target_rows.is_empty() {
            bail!("Frequency channels in {name} are outside the combined grid.");
        }
        let positions: Vec<usize> = if res.freqs.len() == 1 {
            vec![0; target_rows.len()]
        } else {
            let mids: Vec<f64> = res.freqs.windows(2).map(|w| 0.5 * (w[0] + w[1])).collect();
            // np.searchsorted(mids, value, side="right")
            target_rows.iter().map(|&r| mids.partition_point(|&m| m <= grid[r])).collect()
        };
        let mut write = vec![true; target_rows.len()];
        let overlapped: Vec<usize> = (0..target_rows.len()).filter(|&k| filled[target_rows[k]]).collect();
        if !overlapped.is_empty() {
            if options.overlap_policy == Overlap::Reject {
                bail!("Frequency bands overlap or interleave; selected overlap policy rejects overlap.");
            }
            let ofreqs: Vec<f64> = overlapped.iter().map(|&k| grid[target_rows[k]]).collect();
            let (omin, omax) = finite_min_max(&ofreqs);
            let replace = overlap_replace_mask(
                &ofreqs,
                options.overlap_policy,
                preview.freq_min.max(omin),
                preview.freq_max.min(omax),
                connection,
            )?;
            for (k, r) in overlapped.iter().zip(replace) {
                write[*k] = r;
            }
        }
        for (k, &row) in target_rows.iter().enumerate() {
            if write[k] {
                let src = res.data.row(positions[k]);
                for (dst, v) in combined[row * ncols..(row + 1) * ncols].iter_mut().zip(src) {
                    *dst = *v as f64;
                }
                filled[row] = true;
            }
        }
    }

    let gap_count = filled.iter().filter(|f| !**f).count();
    if gap_count > 0 {
        match options.gap_fill {
            GapFill::Background => fill_gap_background(&mut combined, ncols, &filled, &grid, true),
            GapFill::Average => fill_gap_background(&mut combined, ncols, &filled, &grid, false),
            GapFill::Hatched => {
                for (r, f) in filled.iter().enumerate() {
                    if !f {
                        combined[r * ncols..(r + 1) * ncols].fill(f64::NAN);
                    }
                }
            }
            GapFill::Zero => {
                for (r, f) in filled.iter().enumerate() {
                    if !f {
                        combined[r * ncols..(r + 1) * ncols].fill(0.0);
                    }
                }
            }
        }
    }

    // Back to CALLISTO's high-frequency-first order.
    let rows = grid.len();
    let mut data = Vec::with_capacity(rows * ncols);
    for r in (0..rows).rev() {
        data.extend(combined[r * ncols..(r + 1) * ncols].iter().map(|&v| v as f32));
    }
    let freqs: Vec<f64> = grid.iter().rev().copied().collect();
    let gap_row_mask =
        (options.gap_fill == GapFill::Hatched && gap_count > 0).then(|| filled.iter().rev().map(|f| !f).collect());
    let header0 = previews[0].header0.clone();
    Ok(Prepared {
        blocks: previews,
        data: Array2::new(rows, ncols, data),
        freqs,
        time: time_ref,
        gap_row_mask,
        gap_row_count: gap_count,
        header0,
        frequency_step_mhz: grid_step,
    })
}

/// Up to `max_rows` filled rows next to `anchor`, nearest last when going down.
fn neighbor_rows(filled: &[bool], anchor: usize, down: bool, max_rows: usize) -> Vec<usize> {
    let mut rows = Vec::new();
    let mut idx = if down { anchor as isize - 1 } else { anchor as isize };
    while idx >= 0 && (idx as usize) < filled.len() && rows.len() < max_rows {
        if !filled[idx as usize] {
            break;
        }
        rows.push(idx as usize);
        idx += if down { -1 } else { 1 };
    }
    if down {
        rows.reverse();
    }
    rows
}

/// `np.nanpercentile(rows, q, axis=0)`, or the row itself when there is one.
fn edge_background_trace(data: &[f64], ncols: usize, rows: &[usize], q: f64) -> Option<Vec<f64>> {
    match rows.len() {
        0 => None,
        1 => Some(data[rows[0] * ncols..(rows[0] + 1) * ncols].to_vec()),
        _ => Some(
            (0..ncols)
                .map(|c| {
                    super::numeric::nanpercentile(rows.iter().map(|&r| data[r * ncols + c]), q).unwrap_or(f64::NAN)
                })
                .collect(),
        ),
    }
}

fn fill_gap_background(data: &mut [f64], ncols: usize, filled: &[bool], freqs: &[f64], interpolate: bool) {
    let n = filled.len();
    if n == 0 || filled.iter().all(|&f| f) {
        return;
    }
    let mut idx = 0;
    while idx < n {
        if filled[idx] {
            idx += 1;
            continue;
        }
        let start = idx;
        while idx < n && !filled[idx] {
            idx += 1;
        }
        let end = idx;
        let left = neighbor_rows(filled, start, true, GAP_FILL_EDGE_ROWS);
        let right = neighbor_rows(filled, end, false, GAP_FILL_EDGE_ROWS);
        let mut left_bg = edge_background_trace(data, ncols, &left, GAP_FILL_BACKGROUND_PERCENTILE);
        let mut right_bg = edge_background_trace(data, ncols, &right, GAP_FILL_BACKGROUND_PERCENTILE);
        if left_bg.is_none() && right_bg.is_none() {
            continue;
        }
        if left_bg.is_none() {
            left_bg = right_bg.clone();
        }
        if right_bg.is_none() {
            right_bg = left_bg.clone();
        }
        let (lb, rb) = (left_bg.unwrap(), right_bg.unwrap());
        let count = end - start;
        let alphas: Vec<f64> = if !interpolate {
            vec![0.5; count]
        } else if start > 0 && end < n {
            let (lf, rf) = (freqs[start - 1], freqs[end]);
            let span = rf - lf;
            if span.abs() > 1e-12 { (start..end).map(|i| (freqs[i] - lf) / span).collect() } else { vec![0.5; count] }
        } else {
            linspace(1.0 / (count as f64 + 1.0), count as f64 / (count as f64 + 1.0), count)
        };
        for (k, row) in (start..end).enumerate() {
            let a = alphas[k];
            for c in 0..ncols {
                data[row * ncols + c] = (1.0 - a) * lb[c] + a * rb[c];
            }
        }
    }
}

pub fn combine_frequency(paths: &[String], options: CombineOptions) -> Result<Combined> {
    if paths.len() < 2 {
        bail!("Need at least 2 files to combine frequencies.");
    }
    let p = prepare_frequency_blocks(paths, options)?;
    let (station, date, tstamp, _) = parse_filename(&p.blocks[0].path).map_err(|e| anyhow!(e))?;
    let sources: Vec<String> = p.blocks.iter().map(|b| b.path.clone()).collect();
    let step = p.frequency_step_mhz;
    let mut header = build_combined_header(Some(&p.header0), "frequency", &sources, p.data.shape(), &p.freqs, &p.time);
    let (fmin, fmax) = finite_min_max(&p.freqs);
    header.set("CRVAL2", HeaderValue::Float(p.freqs[0]), "Value on axis 2 [MHz]");
    header.set("CRPIX2", HeaderValue::Float(1.0), "Reference pixel for axis 2");
    header.set("CDELT2", HeaderValue::Float(-step), "Frequency step [MHz]");
    header.set("FREQMIN", HeaderValue::Float(fmin), "Min frequency (MHz)");
    header.set("FREQMAX", HeaderValue::Float(fmax), "Max frequency (MHz)");
    header.add_history(&format!("Regularized frequency grid step: {step:.6} MHz"));
    header.add_history(&format!("Frequency gap fill mode: {}; rows: {}", options.gap_fill.key(), p.gap_row_count));
    header.add_history(&format!("Frequency overlap policy: {}", options.overlap_policy.key()));
    let connection = options.overlap_connection_mhz.filter(|v| v.is_finite());
    if let Some(c) = connection {
        header.add_history(&format!("Overlap connection frequency: {c:.6} MHz"));
    }
    Ok(Combined {
        ut_start_sec: extract_ut_start_sec(&p.header0),
        data: p.data,
        freqs: p.freqs,
        time: p.time,
        filename: format!("{station}_{date}_{tstamp}_freq_combined"),
        header0: header,
        sources,
        combine_type: CombineType::Frequency,
        gap_row_mask: p.gap_row_mask,
        frequency_step_mhz: Some(step),
        options: Some(CombineOptions { overlap_connection_mhz: connection, ..options }),
        timestamp_count: None,
        focus_codes: Vec::new(),
    })
}

fn relations_match(a: &FrequencyRelation, b: &FrequencyRelation) -> bool {
    let same = |x: &[BandRelation], y: &[BandRelation]| {
        x.len() == y.len()
            && x.iter().zip(y).all(|(p, q)| {
                (p.low - q.low).abs() <= FREQUENCY_ALIGN_ATOL_MHZ && (p.high - q.high).abs() <= FREQUENCY_ALIGN_ATOL_MHZ
            })
    };
    same(&a.gaps, &b.gaps) && same(&a.overlaps, &b.overlaps)
}

/// Frequency-combines each timestamp of a complete grid, then stitches in time.
pub fn combine_time_frequency(paths: &[String], options: CombineOptions) -> Result<Combined> {
    let s = classify_combination_structure(paths);
    if !s.valid || s.combine_type != Some(CombineType::TimeFrequency) {
        bail!(s.error.unwrap_or_else(|| "The selected files are not time-and-frequency combinable.".into()));
    }
    let mut segments = Vec::new();
    let mut sources = Vec::new();
    let mut reference: Option<FrequencyRelation> = None;
    for group in &s.timestamp_groups {
        let relation = describe_frequency_combination(group)?;
        match &reference {
            None => reference = Some(relation),
            Some(r) if !relations_match(r, &relation) => {
                let stamp = parse_filename(&group[0]).map(|p| p.2).unwrap_or_default();
                bail!("Frequency gap/overlap layout at timestamp {stamp} does not match the first timestamp.");
            }
            _ => {}
        }
        let c = combine_frequency(group, options)?;
        sources.extend(c.sources.iter().cloned());
        segments.push(Segment {
            data: c.data,
            freqs: c.freqs,
            time: c.time,
            label: c.filename,
            header0: Some(c.header0),
            gap_row_mask: c.gap_row_mask,
            frequency_step_mhz: c.frequency_step_mhz,
        });
    }
    let first_date = s.observed_at[0].format("%Y%m%d");
    let mut result = stitch_time_segments(
        segments,
        CombineType::TimeFrequency,
        format!("{}_{first_date}_time_frequency_combined", s.station),
        sources,
    )?;
    let connection = options.overlap_connection_mhz.filter(|v| v.is_finite());
    result.options = Some(CombineOptions { overlap_connection_mhz: connection, ..options });
    result.timestamp_count = Some(s.timestamp_groups.len());
    result.focus_codes = s.focus_codes.clone();
    result.header0.set("NTIMES", HeaderValue::Int(s.timestamp_groups.len() as i64), "Number of observation timestamps");
    result.header0.set("NFOCUS", HeaderValue::Int(s.focus_codes.len() as i64), "Number of receiver focus codes");
    Ok(result)
}

// -------------------------------------------------------------- grouping

#[derive(Clone, Debug)]
pub struct Group {
    pub paths: Vec<String>,
    pub combine_type: Option<CombineType>,
    pub combined: Option<Combined>,
}

fn time_frequency_candidate_runs(paths: &[String]) -> Vec<Vec<String>> {
    let mut by_station: BTreeMap<String, BTreeMap<NaiveDateTime, BTreeMap<String, String>>> = BTreeMap::new();
    let mut rejected = BTreeSet::new();
    for path in paths {
        let Ok((station, observed, focus)) = parse_observation(path) else { continue };
        let cells = by_station.entry(station.clone()).or_default().entry(observed).or_default();
        let key = normalize_focus_code(&focus);
        if cells.contains_key(&key) {
            rejected.insert(station);
            continue;
        }
        cells.insert(key, path.clone());
    }
    let mut runs = Vec::new();
    for (station, grouped) in &by_station {
        if rejected.contains(station) {
            continue;
        }
        let flush = |stamps: &[NaiveDateTime], runs: &mut Vec<Vec<String>>| {
            if stamps.len() >= 2 {
                runs.push(stamps.iter().flat_map(|s| grouped[s].values().cloned()).collect());
            }
        };
        let mut current: Vec<NaiveDateTime> = Vec::new();
        for (stamp, cells) in grouped {
            if cells.len() < 2 {
                flush(&current, &mut runs);
                current.clear();
                continue;
            }
            if let Some(last) = current.last() {
                let delta = (*stamp - *last).num_milliseconds() as f64 / 1000.0;
                let consecutive = (MIN_CONSECUTIVE_SECONDS..=MAX_CONSECUTIVE_SECONDS).contains(&delta);
                let same_set = grouped[last].keys().eq(cells.keys());
                if !consecutive || !same_set {
                    flush(&current, &mut runs);
                    current.clear();
                }
            }
            current.push(*stamp);
        }
        flush(&current, &mut runs);
    }
    runs
}

/// Splits a selection into the largest valid combinations; leftovers stay
/// single. Groups keep the order of their earliest member.
pub fn group_combinable_paths(paths: &[String], combine: bool, options: CombineOptions) -> Vec<Group> {
    let mut unique: Vec<String> = Vec::new();
    for p in paths {
        let t = p.trim().to_string();
        if !t.is_empty() && !unique.contains(&t) {
            unique.push(t);
        }
    }
    if unique.is_empty() {
        return Vec::new();
    }
    if unique.len() > 1 {
        let ins = inspect_combination(&unique);
        if ins.valid {
            let combined = if combine { combine_compatible(&unique, options).ok() } else { None };
            return vec![Group { paths: unique, combine_type: ins.combine_type, combined }];
        }
    }
    let index_of = |p: &String| unique.iter().position(|u| u == p).unwrap();
    let mut consumed: BTreeSet<String> = BTreeSet::new();
    let mut output: Vec<(usize, Group)> = Vec::new();
    let append = |group: Vec<String>,
                  kind: CombineType,
                  payload: Option<Combined>,
                  consumed: &mut BTreeSet<String>,
                  output: &mut Vec<(usize, Group)>| {
        for p in &group {
            consumed.insert(p.clone());
        }
        let first = group.iter().map(&index_of).min().unwrap();
        output.push((first, Group { paths: group, combine_type: Some(kind), combined: payload }));
    };

    for group in time_frequency_candidate_runs(&unique) {
        if group.len() < 4 || group.iter().any(|p| consumed.contains(p)) {
            continue;
        }
        let ins = inspect_combination(&group);
        if ins.valid && ins.combine_type == Some(CombineType::TimeFrequency) {
            let payload = if combine { combine_compatible(&group, options).ok() } else { None };
            append(group, CombineType::TimeFrequency, payload, &mut consumed, &mut output);
        }
    }

    let group_by = |key: &dyn Fn(&str) -> Option<Vec<String>>, consumed: &BTreeSet<String>| {
        let mut groups: Vec<(Vec<String>, Vec<String>)> = Vec::new();
        for p in &unique {
            if consumed.contains(p) {
                continue;
            }
            let Some(k) = key(p) else { continue };
            match groups.iter_mut().find(|(gk, _)| *gk == k) {
                Some((_, members)) => members.push(p.clone()),
                None => groups.push((k, vec![p.clone()])),
            }
        }
        let mut g: Vec<Vec<String>> = groups.into_iter().map(|(_, m)| m).collect();
        g.sort_by_key(|m| m.iter().map(&index_of).min().unwrap());
        g
    };

    let by_station_focus = |p: &str| parse_filename(p).ok().map(|(s, _, _, r)| vec![s, r]);
    for group in group_by(&by_station_focus, &consumed) {
        if group.len() < 2 {
            continue;
        }
        if let Ok(c) = combine_time(&group) {
            append(group, CombineType::Time, combine.then_some(c), &mut consumed, &mut output);
        }
    }
    let by_timestamp = |p: &str| parse_filename(p).ok().map(|(s, d, t, _)| vec![s, d, t]);
    for group in group_by(&by_timestamp, &consumed) {
        if group.len() < 2 {
            continue;
        }
        if let Ok(c) = combine_frequency(&group, options) {
            append(group, CombineType::Frequency, combine.then_some(c), &mut consumed, &mut output);
        }
    }
    for p in &unique {
        if !consumed.contains(p) {
            output.push((index_of(p), Group { paths: vec![p.clone()], combine_type: None, combined: None }));
        }
    }
    output.sort_by_key(|(i, _)| *i);
    output.into_iter().map(|(_, g)| g).collect()
}

/// Source paths as `PathBuf`s.
pub fn source_paths(c: &Combined) -> Vec<PathBuf> {
    c.sources.iter().map(PathBuf::from).collect()
}
