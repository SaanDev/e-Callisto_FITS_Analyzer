//! Batch processing (`src/backend/radio/batch_processing.py` and
//! `BatchProcessWorker`): every FIT file in a folder, raw or background
//! subtracted, saved as a PNG in the full app's figure style, optionally
//! all with the same display range and visual settings.

use std::path::{Path, PathBuf};

use super::array::Array2;
use super::artemis::{db_scale_for_header, is_artemis_header};
use super::fits_io::{FitsLoadResult, extract_ut_start_sec};
use super::frequency_axis::{finite_data_limits, matplotlib_extent};
use super::processing::{BackgroundMethod, DEFAULT_DB_SCALE, PLOTUTIL_DB_SCALE, subtract_background};
use crate::view_config::ViewConfig;

const FIT_SUFFIXES: [&str; 4] = [".fit.gz", ".fits.gz", ".fit", ".fits"];
/// `PLOTUTIL_DISPLAY_LIMITS`.
pub const PLOTUTIL_DISPLAY_LIMITS: (f64, f64) = (-1.0, 8.0);

/// `_is_fit_path`.
pub fn is_fit_path(path: &Path) -> bool {
    let lower = path.to_string_lossy().trim().to_lowercase();
    FIT_SUFFIXES.iter().any(|s| lower.ends_with(s))
}

/// `_strip_fit_suffix`.
pub fn strip_fit_suffix(filename: &str) -> String {
    let name = Path::new(filename.trim()).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let lower = name.to_lowercase();
    for s in FIT_SUFFIXES {
        if lower.ends_with(s) {
            return name[..name.len() - s.len()].to_owned();
        }
    }
    match name.rfind('.') {
        Some(i) if i > 0 => name[..i].to_owned(),
        _ => name,
    }
}

/// `_sanitize_stem`: whitespace runs to one space, forbidden characters to `_`.
pub fn sanitize_stem(stem: &str) -> String {
    let s = stem.trim();
    let s = if s.is_empty() { "output" } else { s };
    let collapsed = s.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut out = String::new();
    let mut in_bad = false;
    for c in collapsed.chars() {
        if matches!(c, '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|') {
            if !in_bad {
                out.push('_');
            }
            in_bad = true;
        } else {
            out.push(c);
            in_bad = false;
        }
    }
    let out = out.trim_matches(|c| c == ' ' || c == '.').to_owned();
    if out.is_empty() { "output".into() } else { out }
}

/// `list_fit_files`: FIT files in `dir` (sub-folders too when `recursive`),
/// sorted by lower-case file name.
pub fn list_fit_files(dir: &Path, recursive: bool) -> Vec<PathBuf> {
    fn walk(dir: &Path, recursive: bool, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for e in entries.flatten() {
            let path = e.path();
            if path.is_dir() {
                if recursive {
                    walk(&path, recursive, out);
                }
            } else if path.is_file() && is_fit_path(&path) {
                out.push(path);
            }
        }
    }
    let mut files = Vec::new();
    if dir.is_dir() {
        walk(dir, recursive, &mut files);
    }
    let key = |p: &PathBuf| p.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    files.sort_by_key(key);
    files
}

/// `build_unique_output_png_path`: `<stem>.png`, else `<stem>_1.png`, ….
pub fn unique_output_png_path(output_dir: &Path, input_filename: &str) -> PathBuf {
    let stem = sanitize_stem(&strip_fit_suffix(input_filename));
    let first = output_dir.join(format!("{stem}.png"));
    if !first.exists() {
        return first;
    }
    (1..).map(|i| output_dir.join(format!("{stem}_{i}.png"))).find(|p| !p.exists()).unwrap_or(first)
}

/// `locked_view_overlaps_data`: the locked range meets the file's extent.
pub fn locked_view_overlaps_data(freqs: &[f64], time: &[f64], cfg: Option<&ViewConfig>) -> bool {
    let Some(r) = cfg.and_then(|c| c.range) else { return true };
    let Some([x0, x1, y0, y1]) = matplotlib_extent(freqs, time, None) else { return true };
    let (dx, dy) = ((x0.min(x1), x0.max(x1)), (y0.min(y1), y0.max(y1)));
    let x = dx.1.min(r.time_stop_s) - dx.0.max(r.time_start_s);
    let y = dy.1.min(r.freq_max_mhz) - dy.0.max(r.freq_min_mhz);
    x > 0.0 && y > 0.0
}

/// Raw data, or background subtracted with a method.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OutputMode {
    Raw,
    BackgroundSubtracted(BackgroundMethod),
}

impl OutputMode {
    pub fn key(self) -> &'static str {
        match self {
            OutputMode::Raw => "raw",
            OutputMode::BackgroundSubtracted(_) => "background_subtracted",
        }
    }
}

/// `background_method_label`.
pub fn background_method_label(m: BackgroundMethod) -> &'static str {
    match m {
        BackgroundMethod::Mean => "Mean",
        BackgroundMethod::Median => "Median",
        BackgroundMethod::Robust => "Robust",
        BackgroundMethod::PlotutilMedianDb => "Plotutil Median (dB)",
    }
}

/// Everything one PNG shows (`save_background_subtracted_png`'s figure).
#[derive(Clone, Debug)]
pub struct BatchFigureSpec {
    pub display: Array2,
    pub freqs: Vec<f64>,
    pub time: Vec<f64>,
    /// `matplotlib_extent`: `[x0, x1, bottom, top]`, row 0 at the top.
    pub extent: [f64; 4],
    pub levels: Option<(f64, f64)>,
    pub cmap_name: String,
    /// `None` when the titles are removed.
    pub title: Option<String>,
    pub x_label: Option<String>,
    pub y_label: Option<String>,
    pub colorbar_label: String,
    /// UT tick labels from this start (seconds of day), with seconds when
    /// the file spans five minutes or less.
    pub ut_ticks: Option<(f64, bool)>,
    pub xlim: Option<(f64, f64)>,
    pub ylim: Option<(f64, f64)>,
}

/// One file's figure, as `BatchProcessWorker.run` and
/// `save_background_subtracted_png` prepare it.
pub fn batch_figure_spec(
    res: &FitsLoadResult,
    file_name: &str,
    mode: OutputMode,
    cmap_name: &str,
    cold_digits: f64,
    cfg: Option<&ViewConfig>,
) -> Result<BatchFigureSpec, String> {
    let instrument_db_scale = db_scale_for_header(&res.header0, DEFAULT_DB_SCALE);
    let plotutil_db_scale = db_scale_for_header(&res.header0, PLOTUTIL_DB_SCALE);
    let linear_unit = if is_artemis_header(&res.header0) { "Counts" } else { "Digits" };
    let (data, title_suffix, input_is_db, db_scale, default_limits) = match mode {
        OutputMode::Raw => (res.data.clone(), "Raw".to_owned(), false, instrument_db_scale, None),
        OutputMode::BackgroundSubtracted(m) => {
            let out = subtract_background(&res.data, m, None, false, plotutil_db_scale);
            let db = m == BackgroundMethod::PlotutilMedianDb;
            (
                out,
                format!("Background Subtracted ({})", background_method_label(m)),
                db,
                if db { plotutil_db_scale } else { instrument_db_scale },
                db.then_some(PLOTUTIL_DISPLAY_LIMITS),
            )
        }
    };
    if data.rows == 0 || data.cols == 0 {
        return Err("Expected 2D data for PNG export.".into());
    }
    if res.freqs.is_empty() || res.time.is_empty() {
        return Err("Frequency/time axes cannot be empty for PNG export.".into());
    }
    let lower = file_name.to_lowercase();
    let stem = FIT_SUFFIXES
        .iter()
        .find(|s| lower.ends_with(**s))
        .map(|s| file_name[..file_name.len() - s.len()].to_owned())
        .unwrap_or_else(|| strip_fit_suffix(file_name));
    let title = format!("{stem}-{title_suffix}");
    let ut_start = extract_ut_start_sec(&res.header0).unwrap_or(0.0);

    // The visual settings apply when the config carries them.
    let apply_visual = cfg.is_some_and(|c| c.include_visual);
    let visual = cfg.filter(|_| apply_visual).map(|c| &c.visual);
    let use_db = visual.is_none_or(|v| v.use_db);
    let mut display = data;
    if input_is_db {
        if !use_db {
            for v in display.data.iter_mut() {
                *v = (*v as f64 / db_scale) as f32;
            }
        }
    } else if use_db {
        for v in display.data.iter_mut() {
            *v = ((*v as f64 - cold_digits) * db_scale) as f32;
        }
    }
    let (t0, mut t1) = (res.time[0], res.time[res.time.len() - 1]);
    if (t1 - t0).abs() < 1e-12 {
        t1 = t0 + 1.0;
    }
    let cmap_name = visual.map_or_else(|| cmap_name.to_owned(), |v| v.cmap.clone());
    let mut levels = None;
    if let Some(v) = visual
        && (v.noise_clip_low.abs() > 1e-9 || v.noise_clip_high.abs() > 1e-9)
    {
        let (mut lo, mut hi) = if v.noise_clip_low <= v.noise_clip_high { (v.noise_clip_low, v.noise_clip_high) } else { (v.noise_clip_high, v.noise_clip_low) };
        if use_db {
            lo = (lo - cold_digits) * db_scale;
            hi = (hi - cold_digits) * db_scale;
        }
        if hi > lo {
            levels = Some((lo, hi));
        }
    }
    if levels.is_none()
        && let Some((a, b)) = default_limits
    {
        let (mut lo, mut hi) = if a <= b { (a, b) } else { (b, a) };
        if input_is_db && !use_db {
            lo /= db_scale;
            hi /= db_scale;
        }
        if hi > lo {
            levels = Some((lo, hi));
        }
    }
    if levels.is_none() {
        levels = finite_data_limits(&display.data);
    }
    let colorbar_label = if use_db { "Intensity [dB]".to_owned() } else { format!("Intensity [{linear_unit}]") };
    let remove_titles = visual.is_some_and(|v| v.graph_bool("remove_titles"));
    let use_utc = visual.is_none_or(|v| v.use_utc);
    let (title, x_label, y_label) = if remove_titles {
        (None, None, None)
    } else {
        let title = if title.trim().is_empty() { "Background Subtracted".to_owned() } else { title.trim().to_owned() };
        (Some(title), Some(if use_utc { "Time [UT]" } else { "Time [s]" }.to_owned()), Some("Frequency [MHz]".to_owned()))
    };
    let extent = matplotlib_extent(&res.freqs, &res.time, None).ok_or("Frequency axis cannot be empty.")?;
    let (xlim, ylim) = match cfg.and_then(|c| c.range) {
        Some(r) => (Some((r.time_start_s, r.time_stop_s)), Some((r.freq_min_mhz, r.freq_max_mhz))),
        None => (None, None),
    };
    Ok(BatchFigureSpec {
        display,
        freqs: res.freqs.clone(),
        time: res.time.clone(),
        extent,
        levels,
        cmap_name,
        title,
        x_label,
        y_label,
        colorbar_label,
        ut_ticks: use_utc.then_some((ut_start, (t1 - t0).abs() <= 300.0)),
        xlim,
        ylim,
    })
}

/// `_fmt_ut`: a UT tick label.
pub fn format_ut_tick(x: f64, ut_start: f64, seconds: bool) -> String {
    let total = (ut_start + x).round_ties_even() as i64;
    let total = total.rem_euclid(86400);
    let (hh, mm, ss) = (total / 3600 % 24, total % 3600 / 60, total % 60);
    if seconds { format!("{hh:02}:{mm:02}:{ss:02}") } else { format!("{hh:02}:{mm:02}") }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_like_the_full_app() {
        assert_eq!(strip_fit_suffix("BIR_20261007_101459_01.fit.gz"), "BIR_20261007_101459_01");
        assert_eq!(strip_fit_suffix("a.FITS"), "a");
        assert_eq!(strip_fit_suffix("notes.txt"), "notes");
        assert_eq!(sanitize_stem("  a  b:c?*d. "), "a b_c_d");
        assert_eq!(sanitize_stem(" .. "), "output");
        assert!(is_fit_path(Path::new("X.FIT.GZ")) && !is_fit_path(Path::new("x.png")));
        assert_eq!(format_ut_tick(90.0, 15300.0, false), "04:16");
        assert_eq!(format_ut_tick(0.0, 86399.6, true), "00:00:00");
    }
}
