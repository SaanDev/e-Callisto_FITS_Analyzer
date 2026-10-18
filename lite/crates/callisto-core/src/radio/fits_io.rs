//! Port of `src/backend/radio/fits_io.py`: loading CALLISTO-like FITS files
//! exactly as the full app does.
//!
//! The data keep the file's row order (CALLISTO stores high frequencies
//! first) and the time axis is taken as written; only ARTEMIS-IV files are
//! rebased (see `artemis`). Axes come from a binary table when one has them,
//! then from the header WCS, then `FREQMIN`/`FREQMAX`, then indices.

use std::path::Path;

use anyhow::Result;

use super::array::Array2;
use super::artemis;
use crate::fits::{FitsFile, Header, HeaderValue};

#[derive(Clone, Debug)]
pub struct FitsLoadResult {
    /// `(frequency, time)`.
    pub data: Array2,
    pub freqs: Vec<f64>,
    pub time: Vec<f64>,
    pub header0: Header,
    /// Headers of every HDU, for the FITS header viewer.
    pub headers: Vec<(String, Header)>,
}

#[derive(Clone, Debug)]
pub struct FitsPreviewResult {
    pub freqs: Vec<f64>,
    pub time: Vec<f64>,
    pub header0: Header,
    pub data_shape: (usize, usize),
    /// "table", "header", "header-range", "index" or (time only) "artemis".
    pub freq_source: &'static str,
    pub time_source: &'static str,
}

/// `(freqs, time)` from the first binary tables holding them.
fn table_axes(fits: &FitsFile) -> (Option<Vec<f64>>, Option<Vec<f64>>) {
    let mut freqs = None;
    let mut time = None;
    for index in fits.bintables() {
        let names = fits.column_names(index);
        let find =
            |wanted: &[&str]| wanted.iter().find_map(|w| names.iter().find(|n| n.eq_ignore_ascii_case(w)).cloned());
        if freqs.is_none()
            && let Some(name) = find(&["frequency", "freq"])
        {
            freqs = fits.read_table_axis(index, &name).ok();
        }
        if time.is_none()
            && let Some(name) = find(&["time", "times"])
        {
            time = fits.read_table_axis(index, &name).ok();
        }
        if freqs.is_some() && time.is_some() {
            break;
        }
    }
    (freqs, time)
}

/// `CRVAL + (i - CRPIX) * CDELT` for 1-based pixel `i`.
fn axis_from_header(h: &Header, axis: usize, length: usize) -> Option<Vec<f64>> {
    let crval = h.get_f64(&format!("CRVAL{axis}"))?;
    let cdelt = h.get_f64(&format!("CDELT{axis}"))?;
    let crpix = h.get_f64(&format!("CRPIX{axis}")).unwrap_or(1.0);
    Some((0..length).map(|i| crval + ((i as f64 + 1.0) - crpix) * cdelt).collect())
}

/// `FREQMIN`/`FREQMAX`, high frequency first as CALLISTO stores rows.
fn frequency_axis_from_range(h: &Header, length: usize) -> Option<Vec<f64>> {
    if length == 0 {
        return None;
    }
    let lo = h.get_f64("FREQMIN")?;
    let hi = h.get_f64("FREQMAX")?;
    if !lo.is_finite() || !hi.is_finite() {
        return None;
    }
    if length == 1 {
        return Some(vec![lo.max(hi)]);
    }
    Some(super::numeric::linspace(lo.max(hi), lo.min(hi), length))
}

fn header_list(fits: &FitsFile) -> Vec<(String, Header)> {
    fits.hdus.iter().enumerate().map(|(i, h)| (format!("HDU {i}: {}", h.kind()), h.header.clone())).collect()
}

pub fn load_callisto_fits(path: &Path) -> Result<FitsLoadResult> {
    let fits = FitsFile::open(path)?;
    load_from(&fits)
}

pub fn load_from(fits: &FitsFile) -> Result<FitsLoadResult> {
    let header0 = fits.hdus[0].header.clone();
    let (rows, cols, values) = fits.read_primary_array()?;
    let mut data = Array2::new(rows, cols, values);

    let (table_freqs, table_time) = table_axes(fits);
    let mut freqs = table_freqs
        .or_else(|| axis_from_header(&header0, 2, data.rows))
        .or_else(|| frequency_axis_from_range(&header0, data.rows));
    let mut time = table_time.or_else(|| axis_from_header(&header0, 1, data.cols));
    let mut f = freqs.take().unwrap_or_else(|| (0..data.rows).map(|i| i as f64).collect());
    let mut t = time.take().unwrap_or_else(|| (0..data.cols).map(|i| i as f64).collect());

    // Desired: data.shape == (len(freqs), len(time)).
    let matches = (data.rows, data.cols) == (f.len(), t.len());
    if matches {
        // Already oriented.
    } else if (data.rows, data.cols) == (t.len(), f.len()) {
        data = data.transposed();
    } else if f.len() == data.cols && t.len() == data.cols {
        // Ambiguous: both probably came from the same axis.
        f = (0..data.rows).map(|i| i as f64).collect();
        t = (0..data.cols).map(|i| i as f64).collect();
    } else {
        if f.len() != data.rows && f.len() == data.cols {
            data = data.transposed();
        }
        if f.len() != data.rows {
            f = (0..data.rows).map(|i| i as f64).collect();
        }
        if t.len() != data.cols {
            t = (0..data.cols).map(|i| i as f64).collect();
        }
    }

    // ARTEMIS-IV stores UT hours on the time axis; rebase once, here.
    let (t, _) = artemis::normalize_artemis_axes(&header0, t, data.cols);

    Ok(FitsLoadResult { data, freqs: f, time: t, header0, headers: header_list(fits) })
}

/// Axes and shape without decoding the image.
pub fn preview_callisto_fits(path: &Path) -> Result<FitsPreviewResult> {
    let fits = FitsFile::open(path)?;
    let header0 = fits.hdus[0].header.clone();
    let mut nx = header0.get_i64("NAXIS1").unwrap_or(0).max(0) as usize;
    let mut ny = header0.get_i64("NAXIS2").unwrap_or(0).max(0) as usize;

    let (table_freqs, table_time) = table_axes(&fits);
    let mut freq_source = "missing";
    let mut time_source = "missing";
    let mut freqs = table_freqs.inspect(|_| freq_source = "table");
    let mut time = table_time.inspect(|_| time_source = "table");

    if ny == 0
        && let Some(f) = &freqs
    {
        ny = f.len();
    }
    if nx == 0
        && let Some(t) = &time
    {
        nx = t.len();
    }
    if freqs.is_none() && ny > 0 {
        freqs = axis_from_header(&header0, 2, ny).inspect(|_| freq_source = "header");
    }
    if freqs.is_none() && ny > 0 {
        freqs = frequency_axis_from_range(&header0, ny).inspect(|_| freq_source = "header-range");
    }
    if time.is_none() && nx > 0 {
        time = axis_from_header(&header0, 1, nx).inspect(|_| time_source = "header");
    }
    let freqs = freqs.unwrap_or_else(|| {
        freq_source = "index";
        (0..ny).map(|i| i as f64).collect()
    });
    let mut time = time.unwrap_or_else(|| {
        time_source = "index";
        (0..nx).map(|i| i as f64).collect()
    });
    if ny == 0 {
        ny = freqs.len();
    }
    if nx == 0 {
        nx = time.len();
    }
    if artemis::is_artemis_header(&header0) {
        time = artemis::normalize_artemis_axes(&header0, time, nx).0;
        time_source = "artemis";
    }
    Ok(FitsPreviewResult { freqs, time, header0, data_shape: (ny, nx), freq_source, time_source })
}

/// Seconds of UT day at the start: ARTEMIS's own axis, else `TIME-OBS`.
pub fn extract_ut_start_sec(h: &Header) -> Option<f64> {
    if artemis::is_artemis_header(h)
        && let Some(start) = artemis::artemis_ut_start_sec(h)
    {
        return Some(start);
    }
    let t = h.get_str("TIME-OBS")?;
    let parts: Vec<&str> = t.trim().split(':').collect();
    if parts.len() != 3 {
        return None;
    }
    let hh: i64 = parts[0].trim().parse().ok()?;
    let mm: i64 = parts[1].trim().parse().ok()?;
    let ss: f64 = parts[2].trim().parse().ok()?;
    Some(hh as f64 * 3600.0 + mm as f64 * 60.0 + ss)
}

/// The primary header of a combined dataset: the base header plus
/// combination provenance and updated ranges.
pub fn build_combined_header(
    base: Option<&Header>,
    mode: &str,
    sources: &[String],
    data_shape: (usize, usize),
    freqs: &[f64],
    time: &[f64],
) -> Header {
    let mut h = base.cloned().unwrap_or_default();
    let mode = if mode.trim().is_empty() { "combined".to_string() } else { mode.trim().to_lowercase() };
    h.set("COMBINED", HeaderValue::Bool(true), "Data are combined from multiple FITS files");
    h.set("COMBMETH", HeaderValue::Str(mode.clone()), "Combine method (time|frequency|time_frequency)");
    h.set("NFILES", HeaderValue::Int(sources.len() as i64), "Number of source FITS files used");
    let finite = |v: &[f64]| {
        let f: Vec<f64> = v.iter().copied().filter(|x| !x.is_nan()).collect();
        (!f.is_empty()).then(|| f.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), &x| (a.min(x), b.max(x))))
    };
    if let Some((lo, hi)) = finite(freqs) {
        h.set("FREQMIN", HeaderValue::Float(lo), "Min frequency (MHz)");
        h.set("FREQMAX", HeaderValue::Float(hi), "Max frequency (MHz)");
    }
    if let Some((lo, hi)) = finite(time) {
        h.set("TMIN", HeaderValue::Float(lo), "Min time (s)");
        h.set("TMAX", HeaderValue::Float(hi), "Max time (s)");
    }
    h.set_value("NAXIS", HeaderValue::Int(2));
    h.set_value("NAXIS1", HeaderValue::Int(data_shape.1 as i64));
    h.set_value("NAXIS2", HeaderValue::Int(data_shape.0 as i64));
    h.add_history("Combined by e-CALLISTO FITS Analyzer");
    h.add_history(&format!("Combine method: {mode}"));
    if let (Some(first), Some(last)) = (sources.first(), sources.last()) {
        h.add_history(&format!("First source: {}", super::naming::basename(first)));
        h.add_history(&format!("Last source: {}", super::naming::basename(last)));
    }
    h
}
