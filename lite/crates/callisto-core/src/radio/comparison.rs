//! The multi-station comparison's data side
//! (`src/backend/radio/multi_station_comparison.py`): datasets and their
//! labels, the station combinations, time alignment on the UT clock or from
//! each file's start, noise reduction per panel, colour limits, and the
//! panels the window and the grid export draw.

use std::sync::Arc;

use crate::fits::Header;
use crate::plot::Colormap;
use crate::radio::array::Array2;
use crate::radio::batch::{format_ut_tick, strip_fit_suffix};
use crate::radio::combine::{CombineOptions, CombineType, Combined, group_combinable_paths};
use crate::radio::fits_io::{extract_ut_start_sec, load_callisto_fits};
use crate::radio::frequency_axis::{finite_data_limits, matplotlib_extent, pyqtgraph_extent};
use crate::radio::processing::{Baseline, SubtractOptions, subtract_background_rows};

/// `DEFAULT_DB_SCALE`: digits to dB for the comparison's dB units.
pub const DEFAULT_DB_SCALE: f64 = 2500.0 / 256.0 / 25.4;

/// `TIME_ALIGNMENT_*`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Alignment {
    /// Each panel from its own file start.
    Seconds,
    /// Seconds of day, unwrapped across midnight.
    #[default]
    Ut,
}

/// `COLOR_SCALE_*`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ColorScale {
    #[default]
    Shared,
    PerStation,
    Manual,
}

/// `NOISE_METHOD_*`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum NoiseMethod {
    #[default]
    None,
    Mean,
    Median,
    Robust,
    /// The robust background, then clipped to the thresholds.
    Clip,
}

/// `ComparisonNoiseSettings`.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct NoiseSettings {
    pub method: NoiseMethod,
    pub clip_low: f64,
    pub clip_high: f64,
    /// `clip_scale == "signed_log"`: the sliders move on a signed log scale.
    pub log_scale: bool,
}

impl NoiseSettings {
    /// `normalize_comparison_noise_settings`: finite thresholds, low first.
    pub fn normalized(self) -> Self {
        let (mut low, mut high) = (self.clip_low, self.clip_high);
        if !(low.is_finite() && high.is_finite()) {
            (low, high) = (0.0, 0.0);
        }
        if low > high {
            (low, high) = (high, low);
        }
        NoiseSettings { clip_low: low, clip_high: high, ..self }
    }
}

/// `ComparisonDataset`.
#[derive(Clone, Debug)]
pub struct ComparisonDataset {
    pub path: String,
    pub label: String,
    pub data: Arc<Array2>,
    pub freqs: Vec<f64>,
    pub time: Vec<f64>,
    pub ut_start_sec: Option<f64>,
    pub gap_row_mask: Option<Vec<bool>>,
    pub frequency_step_mhz: Option<f64>,
    pub sources: Vec<String>,
    pub combine_type: Option<CombineType>,
    pub warnings: Vec<String>,
}

/// `_date_label_from_path`: the first `YYYYMMDD` part of the name.
fn date_label_from_path(path: &str) -> String {
    let stem = strip_fit_suffix(path);
    let is_date = |s: &str| s.len() == 8 && s.bytes().all(|b| b.is_ascii_digit());
    let fmt = |p: &str| format!("{}-{}-{}", &p[0..4], &p[4..6], &p[6..8]);
    if let Some(part) = stem.split('_').find(|p| is_date(p)) {
        return fmt(part);
    }
    // `(?<!\d)(\d{8})(?!\d)`: eight digits not inside a longer run.
    let b = stem.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i].is_ascii_digit() {
            let start = i;
            while i < b.len() && b[i].is_ascii_digit() {
                i += 1;
            }
            if i - start == 8 {
                return fmt(&stem[start..i]);
            }
        } else {
            i += 1;
        }
    }
    String::new()
}

/// `_date_label_from_sources`: one date, or the first to the last.
fn date_label_from_sources(sources: &[String]) -> String {
    let mut dates: Vec<String> = sources.iter().map(|s| date_label_from_path(s)).filter(|d| !d.is_empty()).collect();
    dates.sort();
    dates.dedup();
    match dates.len() {
        0 => String::new(),
        1 => dates.remove(0),
        n => format!("{} to {}", dates[0], dates[n - 1]),
    }
}

/// `station_label_from_header`: STATION, OBSERVAT, INSTRUME or TELESCOP,
/// else the name's first part.
pub fn station_label_from_header(path: &str, header: Option<&Header>) -> String {
    for key in ["STATION", "OBSERVAT", "INSTRUME", "TELESCOP"] {
        let text = header
            .and_then(|h| h.get_str(key))
            .map(|v| v.split_whitespace().collect::<Vec<_>>().join(" "))
            .unwrap_or_default();
        if !text.is_empty() {
            return text;
        }
    }
    let stem = strip_fit_suffix(path);
    if let Some((first, _)) = stem.split_once('_')
        && !first.trim().is_empty()
    {
        return first.trim().to_owned();
    }
    if stem.is_empty() { "Station".into() } else { stem }
}

/// `station_date_label`: "STATION - YYYY-MM-DD".
pub fn station_date_label(path: &str, header: Option<&Header>, sources: &[String]) -> String {
    let station = station_label_from_header(path, header);
    let own = [path.to_owned()];
    let date = date_label_from_sources(if sources.is_empty() { &own } else { sources });
    if date.is_empty() { station } else { format!("{station} - {date}") }
}

/// `load_comparison_dataset`.
pub fn load_comparison_dataset(path: &str) -> Result<ComparisonDataset, String> {
    let r = load_callisto_fits(std::path::Path::new(path)).map_err(|e| e.to_string())?;
    if r.freqs.is_empty() || r.time.is_empty() {
        return Err("Frequency/time axes cannot be empty.".into());
    }
    if (r.data.rows, r.data.cols) != (r.freqs.len(), r.time.len()) {
        return Err(format!(
            "Data shape ({}, {}) does not match axes ({}, {}).",
            r.data.rows,
            r.data.cols,
            r.freqs.len(),
            r.time.len()
        ));
    }
    let ut_start_sec = extract_ut_start_sec(&r.header0);
    let mut warnings = Vec::new();
    if ut_start_sec.is_none() {
        warnings.push("Missing TIME-OBS; UT alignment unavailable for this file.".into());
    }
    Ok(ComparisonDataset {
        path: path.to_owned(),
        label: station_date_label(path, Some(&r.header0), &[]),
        data: Arc::new(r.data),
        freqs: r.freqs,
        time: r.time,
        ut_start_sec,
        gap_row_mask: None,
        frequency_step_mhz: None,
        sources: vec![path.to_owned()],
        combine_type: None,
        warnings,
    })
}

/// `comparison_dataset_from_combined`.
pub fn dataset_from_combined(c: Combined) -> ComparisonDataset {
    let label = station_date_label(&c.filename, Some(&c.header0), &c.sources);
    let mut warnings = Vec::new();
    if matches!(c.combine_type, CombineType::Frequency | CombineType::TimeFrequency)
        && let Some(mask) = &c.gap_row_mask
    {
        let gaps = mask.iter().filter(|g| **g).count();
        if gaps > 0 {
            warnings.push(format!("Frequency-combined view contains {gaps} gap row(s)."));
        }
    }
    ComparisonDataset {
        path: c.filename,
        label,
        data: Arc::new(c.data),
        freqs: c.freqs,
        time: c.time,
        ut_start_sec: c.ut_start_sec,
        gap_row_mask: c.gap_row_mask,
        frequency_step_mhz: c.frequency_step_mhz,
        sources: c.sources,
        combine_type: Some(c.combine_type),
        warnings,
    }
}

/// `combined_comparison_datasets_from_paths`: the largest valid combinations
/// as single panels, the other files as their own.
pub fn datasets_from_paths(paths: &[String]) -> Result<Vec<ComparisonDataset>, String> {
    let mut out = Vec::new();
    for group in group_combinable_paths(paths, true, CombineOptions::default()) {
        match group.combined {
            Some(c) => out.push(dataset_from_combined(c)),
            None => {
                for p in &group.paths {
                    out.push(load_comparison_dataset(p)?);
                }
            }
        }
    }
    Ok(out)
}

/// `_unwrap_seconds_of_day`: start times on one clock, cut at the widest
/// gap around the day so a midnight crossing stays continuous.
pub fn unwrap_seconds_of_day(values: &[f64]) -> Vec<f64> {
    let raw: Vec<f64> = values.iter().map(|v| v.rem_euclid(86400.0)).collect();
    if raw.len() <= 1 {
        return raw;
    }
    let mut indexed: Vec<(usize, f64)> = raw.iter().copied().enumerate().collect();
    // Python's sort is stable, as is this one.
    indexed.sort_by(|a, b| a.1.total_cmp(&b.1));
    let vals: Vec<f64> = indexed.iter().map(|x| x.1).collect();
    let mut gaps: Vec<(f64, usize)> = (0..vals.len() - 1).map(|i| (vals[i + 1] - vals[i], i)).collect();
    gaps.push(((vals[0] + 86400.0) - vals[vals.len() - 1], vals.len() - 1));
    // `max` keeps the first of equal gaps; so does a strict comparison.
    let mut cut = gaps[0];
    for g in &gaps[1..] {
        if g.0 > cut.0 {
            cut = *g;
        }
    }
    let cut_idx = cut.1;
    let ordered: Vec<(usize, f64)> = indexed[cut_idx + 1..].iter().chain(&indexed[..cut_idx + 1]).copied().collect();
    let mut unwrapped = vec![0.0; raw.len()];
    let mut day_offset = 0.0;
    let mut previous: Option<f64> = None;
    for (idx, value) in ordered {
        if let Some(prev) = previous
            && value + day_offset < prev - 1e-9
        {
            day_offset += 86400.0;
        }
        let current = value + day_offset;
        unwrapped[idx] = current;
        previous = Some(current);
    }
    unwrapped
}

/// `aligned_time_axes`: each panel's time axis, the mode that applied, and
/// why UT was not possible.
pub fn aligned_time_axes(datasets: &[ComparisonDataset], mode: Alignment) -> (Vec<Vec<f64>>, Alignment, Vec<String>) {
    let plain = || datasets.iter().map(|d| d.time.clone()).collect::<Vec<_>>();
    if mode != Alignment::Ut {
        return (plain(), Alignment::Seconds, Vec::new());
    }
    if datasets.iter().any(|d| d.ut_start_sec.is_none()) {
        return (
            plain(),
            Alignment::Seconds,
            vec!["UT alignment unavailable because one or more files are missing TIME-OBS.".into()],
        );
    }
    let starts = unwrap_seconds_of_day(&datasets.iter().map(|d| d.ut_start_sec.unwrap_or(0.0)).collect::<Vec<_>>());
    let axes = datasets.iter().zip(starts).map(|(d, s)| d.time.iter().map(|t| t + s).collect()).collect();
    (axes, Alignment::Ut, Vec::new())
}

/// A candidate UT placement's score (outside the data, duration deviation,
/// start distance) and its start and stop.
type Placement = ((f64, f64, f64), (f64, f64));

/// `shared_extent`'s result: the time and frequency spans, the alignment
/// that applied and why UT was not possible.
pub type SharedExtent = ((f64, f64), (f64, f64), Alignment, Vec<String>);

/// `seconds_of_day_range_to_unwrapped`: a UT start and stop on the
/// comparison's unwrapped clock, the placement that fits `full` best.
pub fn seconds_of_day_range_to_unwrapped(start_sod: f64, stop_sod: f64, full: (f64, f64)) -> Option<(f64, f64)> {
    let (start_sod, stop_sod) = (start_sod.rem_euclid(86400.0), stop_sod.rem_euclid(86400.0));
    let (lo, hi) = (full.0.min(full.1), full.0.max(full.1));
    if ![start_sod, stop_sod, lo, hi].iter().all(|v| v.is_finite()) || hi <= lo || (stop_sod - start_sod).abs() <= 1e-9
    {
        return None;
    }
    let nominal = if stop_sod > start_sod { stop_sod - start_sod } else { stop_sod + 86400.0 - start_sod };
    let first_day = (lo / 86400.0).floor() as i64 - 1;
    let last_day = (hi / 86400.0).ceil() as i64 + 2;
    let mut best: Option<Placement> = None;
    for start_day in first_day..=last_day {
        let start = start_sod + start_day as f64 * 86400.0;
        for stop_day in start_day..=last_day + 1 {
            let stop = stop_sod + stop_day as f64 * 86400.0;
            if stop <= start {
                continue;
            }
            let overlap = hi.min(stop) - lo.max(start);
            if overlap <= 0.0 {
                continue;
            }
            let score =
                ((lo - start).max(0.0) + (stop - hi).max(0.0), ((stop - start) - nominal).abs(), (start - lo).abs());
            let better = match &best {
                None => true,
                Some((b, _)) => {
                    score.0 < b.0 || (score.0 == b.0 && (score.1 < b.1 || (score.1 == b.1 && score.2 < b.2)))
                }
            };
            if better {
                best = Some((score, (start, stop)));
            }
        }
    }
    best.map(|b| b.1)
}

/// `dataset_extent`: `(x0, x1, y0, y1)`, each pair ascending.
pub fn dataset_extent(d: &ComparisonDataset, time_axis: &[f64]) -> [f64; 4] {
    let Some([x0, x1, y0, y1]) = matplotlib_extent(&d.freqs, time_axis, d.frequency_step_mhz) else {
        return [0.0, 1.0, 0.0, 1.0];
    };
    [x0.min(x1), x0.max(x1), y0.min(y1), y0.max(y1)]
}

/// `shared_extent`: the time and frequency span of every panel.
pub fn shared_extent(datasets: &[ComparisonDataset], mode: Alignment) -> Option<SharedExtent> {
    if datasets.is_empty() {
        return None;
    }
    let (axes, effective, warnings) = aligned_time_axes(datasets, mode);
    let (mut x, mut y) = ((f64::INFINITY, f64::NEG_INFINITY), (f64::INFINITY, f64::NEG_INFINITY));
    for (d, axis) in datasets.iter().zip(&axes) {
        let [x0, x1, y0, y1] = dataset_extent(d, axis);
        x = (x.0.min(x0), x.1.max(x1));
        y = (y.0.min(y0), y.1.max(y1));
    }
    Some((x, y, effective, warnings))
}

/// `comparison_cmap`: "Custom" runs blue → red → yellow; an unknown name is viridis.
pub fn comparison_colormap(name: &str) -> Colormap {
    let name = name.trim();
    if name.eq_ignore_ascii_case("custom") {
        return Colormap::by_name("Custom");
    }
    let c = Colormap::by_name(name);
    if c.name == name { c } else { Colormap::by_name("viridis") }
}

/// `apply_comparison_noise`: each row minus its background (the robust one
/// for clipping, then clipped to the thresholds).
pub fn apply_noise(data: &Array2, settings: NoiseSettings, gap_row_mask: Option<&[bool]>) -> Array2 {
    let s = settings.normalized();
    let mode = match s.method {
        NoiseMethod::None => return data.clone(),
        NoiseMethod::Mean => Baseline::Mean,
        NoiseMethod::Median => Baseline::Median,
        NoiseMethod::Robust | NoiseMethod::Clip => Baseline::Robust,
    };
    let mut out = subtract_background_rows(
        data,
        gap_row_mask,
        SubtractOptions { mode, equalize_noise: false, ..Default::default() },
    );
    if s.method == NoiseMethod::Clip {
        clip_in_place(&mut out, s.clip_low, s.clip_high);
    }
    out
}

/// `np.clip` in float32 (NaN stays NaN).
pub fn clip_in_place(a: &mut Array2, low: f64, high: f64) {
    let (lo, hi) = (low as f32, high as f32);
    for v in &mut a.data {
        if *v < lo {
            *v = lo;
        } else if *v > hi {
            *v = hi;
        }
    }
}

/// `display_data_for_visual`: digits, or `(v − cold) × DEFAULT_DB_SCALE` dB
/// (float32, as numpy computes it).
pub fn display_data(data: &Array2, use_db: bool, cold_digits: f64) -> Array2 {
    if !use_db {
        return data.clone();
    }
    let (cold, scale) = (cold_digits as f32, DEFAULT_DB_SCALE as f32);
    Array2::new(data.rows, data.cols, data.data.iter().map(|v| (v - cold) * scale).collect())
}

/// `_compute_color_limits_for_arrays`: manual limits, each panel's own
/// finite range, or the range shared by all.
pub fn color_limits(displayed: &[&Array2], mode: ColorScale, manual: Option<(f64, f64)>) -> Vec<(f64, f64)> {
    if displayed.is_empty() {
        return Vec::new();
    }
    if mode == ColorScale::Manual
        && let Some((a, b)) = manual
    {
        let (lo, hi) = (a.min(b), a.max(b));
        if lo.is_finite() && hi.is_finite() && hi > lo {
            return vec![(lo, hi); displayed.len()];
        }
    }
    let per: Vec<(f64, f64)> = displayed.iter().map(|d| finite_data_limits(&d.data).unwrap_or((0.0, 1.0))).collect();
    if mode == ColorScale::PerStation {
        return per;
    }
    let lo = per.iter().map(|l| l.0).fold(f64::INFINITY, f64::min);
    let mut hi = per.iter().map(|l| l.1).fold(f64::NEG_INFINITY, f64::max);
    if hi <= lo {
        hi = lo + 1e-6;
    }
    vec![(lo, hi); displayed.len()]
}

/// `ComparisonPanelPayload`: one panel as drawn.
#[derive(Clone, Debug)]
pub struct PanelPayload {
    pub dataset: ComparisonDataset,
    pub time_axis: Vec<f64>,
    pub display: Arc<Array2>,
    pub mpl_extent: [f64; 4],
    pub pg_extent: [f64; 4],
    pub levels: (f64, f64),
}

/// The settings every panel is drawn with (`_visual_payload` and the scale).
#[derive(Clone, Debug, PartialEq)]
pub struct PanelOptions {
    pub alignment: Alignment,
    pub use_db: bool,
    pub color_scale: ColorScale,
    pub manual_limits: Option<(f64, f64)>,
}

/// `comparison_panel_payloads` for data already noise-reduced (`processed`
/// is each dataset's data after its panel's method): the aligned axes, the
/// display units with each panel's cold digits, the colour limits.
pub fn panel_payloads(
    datasets: &[ComparisonDataset],
    processed: &[Arc<Array2>],
    cold_digits: &[f64],
    o: &PanelOptions,
) -> (Vec<PanelPayload>, Alignment, Vec<String>) {
    if datasets.is_empty() {
        return (Vec::new(), Alignment::Seconds, Vec::new());
    }
    let (axes, effective, warnings) = aligned_time_axes(datasets, o.alignment);
    let display: Vec<Arc<Array2>> = processed
        .iter()
        .enumerate()
        .map(|(i, d)| {
            if o.use_db {
                Arc::new(display_data(d, true, cold_digits.get(i).copied().unwrap_or(0.0)))
            } else {
                d.clone()
            }
        })
        .collect();
    let refs: Vec<&Array2> = display.iter().map(|d| d.as_ref()).collect();
    let limits = color_limits(&refs, o.color_scale, o.manual_limits);
    let payloads = datasets
        .iter()
        .zip(axes)
        .zip(display)
        .zip(limits)
        .map(|(((d, axis), display), levels)| PanelPayload {
            mpl_extent: matplotlib_extent(&d.freqs, &axis, d.frequency_step_mhz).unwrap_or([0.0, 1.0, 0.0, 1.0]),
            pg_extent: pyqtgraph_extent(&d.freqs, &axis, d.frequency_step_mhz).unwrap_or([0.0, 1.0, 0.0, 1.0]),
            dataset: d.clone(),
            time_axis: axis,
            display,
            levels,
        })
        .collect();
    (payloads, effective, warnings)
}

/// `comparison_grid_dimensions`: (rows, columns).
pub fn grid_dimensions(panels: usize, columns: Option<usize>) -> (usize, usize) {
    let count = panels.max(1);
    let cols = match columns {
        None if count <= 2 => count,
        None if count <= 6 => 3,
        None => 4.min((count as f64).sqrt().ceil() as usize),
        Some(c) => c.clamp(1, count),
    };
    (count.div_ceil(cols), cols)
}

/// `_default_grid_export_title`: the title with the panels' dates.
pub fn default_grid_title(labels: &[String]) -> String {
    let mut dates: Vec<String> = Vec::new();
    for label in labels {
        // `\b\d{4}-\d{2}-\d{2}\b`.
        let b = label.as_bytes();
        for i in 0..b.len().saturating_sub(9) {
            let s = &b[i..i + 10];
            let digits = |r: std::ops::Range<usize>| r.into_iter().all(|k| s[k].is_ascii_digit());
            let word = |c: Option<&u8>| c.is_some_and(|c| c.is_ascii_alphanumeric() || *c == b'_');
            if digits(0..4)
                && s[4] == b'-'
                && digits(5..7)
                && s[7] == b'-'
                && digits(8..10)
                && !word(i.checked_sub(1).and_then(|j| b.get(j)))
                && !word(b.get(i + 10))
            {
                dates.push(label[i..i + 10].to_owned());
            }
        }
    }
    dates.sort();
    dates.dedup();
    let slash = |d: &str| d.replace('-', "/");
    match dates.len() {
        0 => "e-CALLISTO Multi-Station Comparison".into(),
        1 => format!("e-CALLISTO Multi-Station Comparison - {}", slash(&dates[0])),
        n => format!("e-CALLISTO Multi-Station Comparison - {} to {}", slash(&dates[0]), slash(&dates[n - 1])),
    }
}

/// `_format_ut_tick`: seconds of day as HH:MM (or HH:MM:SS).
pub fn ut_tick(value: f64, show_seconds: bool) -> String {
    format_ut_tick(value, 0.0, show_seconds)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_and_dates() {
        assert_eq!(date_label_from_path("GERMANY-DLR_20261007_050000_01.fit.gz"), "2026-10-07");
        assert_eq!(station_date_label("TRIEST_20261007_050000_57.fit.gz", None, &[]), "TRIEST - 2026-10-07");
        let s = ["A_20261007_000000_01.fit".to_owned(), "A_20261008_000000_01.fit".to_owned()];
        assert_eq!(date_label_from_sources(&s), "2026-10-07 to 2026-10-08");
        assert_eq!(
            default_grid_title(&["GERMANY-DLR - 2026-10-07".into(), "TRIEST - 2026-10-07".into()]),
            "e-CALLISTO Multi-Station Comparison - 2026/10/07"
        );
    }

    #[test]
    fn midnight_unwraps() {
        assert_eq!(unwrap_seconds_of_day(&[86000.0, 100.0]), vec![86000.0, 86500.0]);
        assert_eq!(unwrap_seconds_of_day(&[18000.0, 19000.0]), vec![18000.0, 19000.0]);
        assert_eq!(seconds_of_day_range_to_unwrapped(18100.0, 18200.0, (18000.0, 19800.0)), Some((18100.0, 18200.0)));
        assert_eq!(seconds_of_day_range_to_unwrapped(86300.0, 200.0, (86000.0, 86900.0)), Some((86300.0, 86600.0)));
    }

    #[test]
    fn grid_shapes() {
        assert_eq!(grid_dimensions(2, None), (1, 2));
        assert_eq!(grid_dimensions(5, None), (2, 3));
        assert_eq!(grid_dimensions(9, None), (3, 3));
        assert_eq!(grid_dimensions(5, Some(4)), (2, 4));
    }
}
