//! e-CALLISTO specific interpretation of FITS files.
//!
//! A typical file has a primary 8-bit image with time along axis 1 (3600 samples
//! at 0.25 s) and frequency along axis 2 (200 channels), plus a one-row binary
//! table with the `TIME` (seconds after TIME-OBS) and `FREQUENCY` (MHz) axes.

use std::path::{Path, PathBuf};

use anyhow::{Result, anyhow};
use chrono::{NaiveDate, NaiveDateTime, TimeDelta};

use crate::fits::{FitsFile, Header};

/// mV per digit divided by mV per dB for the standard CALLISTO detector
/// (2500 mV / 256 digits, 25.4 mV/dB).
pub const DB_PER_DIGIT: f64 = 2500.0 / 256.0 / 25.4;

pub struct CallistoFile {
    pub path: PathBuf,
    pub name: String,
    pub instrument: String,
    pub focus_code: Option<String>,
    pub start: NaiveDateTime,
    /// Seconds after `start`, one per column.
    pub times: Vec<f64>,
    /// MHz, ascending, one per row.
    pub freqs: Vec<f64>,
    /// `n_freq` rows of `n_time` samples, rows ordered like `freqs`.
    pub data: Vec<f32>,
    pub unit: String,
    /// (HDU label, header) for the header viewer.
    pub headers: Vec<(String, Header)>,
    /// Non-fatal problems worth telling the user about.
    pub warnings: Vec<String>,
}

impl CallistoFile {
    pub fn n_time(&self) -> usize {
        self.times.len()
    }

    pub fn n_freq(&self) -> usize {
        self.freqs.len()
    }

    pub fn load(path: &Path) -> Result<Self> {
        let fits = FitsFile::open(path)?;
        let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        Self::from_fits(&fits, path.to_path_buf(), name)
    }

    pub fn from_fits(fits: &FitsFile, path: PathBuf, name: String) -> Result<Self> {
        let primary = &fits.hdus[0].header;
        let img = fits.read_image(0)?;
        if img.width == 0 || img.height == 0 {
            anyhow::bail!("empty image ({} × {})", img.width, img.height);
        }
        let parsed_name = parse_filename(&name);

        let instrument = primary
            .get_str("INSTRUME")
            .filter(|s| !s.is_empty())
            .or_else(|| parsed_name.as_ref().map(|p| p.station.clone()))
            .unwrap_or_else(|| "unknown".to_string());

        let start = header_start(primary)
            .or_else(|| parsed_name.as_ref().map(|p| p.start))
            .ok_or_else(|| anyhow!("cannot determine observation start (DATE-OBS/TIME-OBS)"))?;

        let mut warnings = fits.warnings.clone();
        let mut table_times = None;
        let mut table_freqs = None;
        if let Some(t) = fits.find_bintable() {
            table_times = fits.read_column(t, "TIME", 0).ok();
            table_freqs = fits.read_column(t, "FREQUENCY", 0).ok();
        }

        // Normally width = time and height = frequency. If the table says otherwise,
        // the image is stored transposed.
        let (mut data, n_time, n_freq) = match (&table_times, &table_freqs) {
            (Some(t), Some(f)) if img.width != img.height && t.len() == img.height && f.len() == img.width => {
                (transpose(&img.data, img.width, img.height), img.height, img.width)
            }
            _ => (img.data, img.width, img.height),
        };

        let times = match table_times {
            Some(t) if t.len() == n_time => relative_times(t, start),
            _ => {
                warnings.push("no usable TIME column; time axis taken from CDELT1".into());
                let dt = primary.get_f64("CDELT1").filter(|d| *d > 0.0).unwrap_or(0.25);
                (0..n_time).map(|i| i as f64 * dt).collect()
            }
        };

        let mut freqs = match table_freqs {
            Some(f) if f.len() == n_freq => f,
            _ => {
                warnings.push("no usable FREQUENCY column; frequency axis taken from CRVAL2/CDELT2".into());
                let crval = primary.get_f64("CRVAL2").unwrap_or(n_freq as f64);
                let cdelt = primary.get_f64("CDELT2").unwrap_or(-1.0);
                let crpix = primary.get_f64("CRPIX2").unwrap_or(0.0);
                (0..n_freq).map(|i| crval + (i as f64 - crpix) * cdelt).collect()
            }
        };

        // Sort channels ascending in frequency (files usually store them descending).
        let mut order: Vec<usize> = (0..n_freq).collect();
        order.sort_by(|&a, &b| freqs[a].total_cmp(&freqs[b]));
        if order.iter().enumerate().any(|(i, &o)| i != o) {
            let mut sorted = Vec::with_capacity(data.len());
            for &r in &order {
                sorted.extend_from_slice(&data[r * n_time..(r + 1) * n_time]);
            }
            data = sorted;
            freqs = order.iter().map(|&r| freqs[r]).collect();
        }

        let headers =
            fits.hdus.iter().enumerate().map(|(i, h)| (format!("HDU {i}: {}", h.kind()), h.header.clone())).collect();

        Ok(Self {
            path,
            name,
            instrument,
            focus_code: parsed_name.and_then(|p| p.focus_code),
            start,
            times,
            freqs,
            data,
            unit: primary.get_str("BUNIT").filter(|s| !s.is_empty()).unwrap_or_else(|| "digits".into()),
            headers,
            warnings,
        })
    }
}

fn transpose(data: &[f32], width: usize, height: usize) -> Vec<f32> {
    let mut out = vec![0.0; data.len()];
    for r in 0..height {
        for c in 0..width {
            out[c * height + r] = data[r * width + c];
        }
    }
    out
}

/// The TIME column is relative to TIME-OBS in practice, but some writers store
/// seconds of day; detect that case by comparing with the header start.
fn relative_times(t: Vec<f64>, start: NaiveDateTime) -> Vec<f64> {
    let sod = seconds_of_day(start);
    if sod > 10.0 && (t[0] - sod).abs() < 2.0 { t.into_iter().map(|v| v - sod).collect() } else { t }
}

pub fn seconds_of_day(t: NaiveDateTime) -> f64 {
    (t - t.date().and_hms_opt(0, 0, 0).unwrap()).as_seconds_f64()
}

fn header_start(h: &Header) -> Option<NaiveDateTime> {
    let date_obs = h.get_str("DATE-OBS")?;
    // DATE-OBS may carry the time itself (ISO 8601).
    let (date_part, iso_time) = match date_obs.split_once('T') {
        Some((d, t)) => (d.to_string(), Some(t.to_string())),
        None => (date_obs, None),
    };
    let date = parse_date(&date_part)?;
    let time =
        h.get_str("TIME-OBS").or_else(|| h.get_str("TIME$_OBS")).or(iso_time).unwrap_or_else(|| "00:00:00".into());
    Some(date.and_hms_opt(0, 0, 0)? + secs(parse_time_of_day(&time)?))
}

/// Accepts `YYYY-MM-DD`, `YYYY/MM/DD` and the legacy `DD/MM/YY`.
pub fn parse_date(s: &str) -> Option<NaiveDate> {
    let parts: Vec<&str> = s.trim().split(['-', '/']).collect();
    if parts.len() != 3 {
        return None;
    }
    let n: Vec<i32> = parts.iter().map(|p| p.trim().parse().ok()).collect::<Option<_>>()?;
    if parts[0].len() == 4 {
        NaiveDate::from_ymd_opt(n[0], n[1] as u32, n[2] as u32)
    } else {
        let year = if n[2] < 50 {
            2000 + n[2]
        } else if n[2] < 100 {
            1900 + n[2]
        } else {
            n[2]
        };
        NaiveDate::from_ymd_opt(year, n[1] as u32, n[0] as u32)
    }
}

/// `HH:MM:SS.sss` to seconds. Tolerates SS = 60, which some files contain.
pub fn parse_time_of_day(s: &str) -> Option<f64> {
    let mut it = s.trim().split(':');
    let h: f64 = it.next()?.trim().parse().ok()?;
    let m: f64 = it.next().unwrap_or("0").trim().parse().ok()?;
    let sec: f64 = it.next().unwrap_or("0").trim().parse().ok()?;
    Some(h * 3600.0 + m * 60.0 + sec)
}

pub fn secs(s: f64) -> TimeDelta {
    TimeDelta::microseconds((s * 1e6).round() as i64)
}

/// Pieces of a standard archive filename: `STATION_YYYYMMDD_HHMMSS_FC.fit.gz`.
#[derive(Clone, Debug, PartialEq)]
pub struct FileName {
    pub station: String,
    pub start: NaiveDateTime,
    pub focus_code: Option<String>,
}

pub fn parse_filename(name: &str) -> Option<FileName> {
    let lower = name.to_ascii_lowercase();
    let stem_len = [".fit.gz", ".fits.gz", ".fts.gz", ".fit", ".fits", ".fts"]
        .iter()
        .find(|ext| lower.ends_with(*ext))
        .map(|ext| name.len() - ext.len())?;
    let stem = &name[..stem_len];
    // rsplitn yields from the right: [focus, time, date, station], and without a
    // focus code [time, date, station]. The station is last so it may contain '_'.
    let with_focus: Vec<&str> = stem.rsplitn(4, '_').collect();
    let without_focus: Vec<&str> = stem.rsplitn(3, '_').collect();
    let (focus, time, date, station) = match (with_focus.as_slice(), without_focus.as_slice()) {
        ([f, t, d, s], _) if t.len() == 6 && d.len() == 8 => (Some(f.to_string()), *t, *d, *s),
        (_, [t, d, s]) if t.len() == 6 && d.len() == 8 => (None, *t, *d, *s),
        _ => return None,
    };
    let start = NaiveDateTime::parse_from_str(&format!("{date}{time}"), "%Y%m%d%H%M%S").ok()?;
    Some(FileName { station: station.to_string(), start, focus_code: focus })
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use crate::fits::tests::{header_bytes, ks, kv};

    /// Builds an e-CALLISTO-like FITS file in memory: frequencies descending,
    /// data value = 10 * channel + time index (mod 200).
    pub fn synthetic_file(start: &str, n_time: usize, freqs_desc: &[f64]) -> Vec<u8> {
        let n_freq = freqs_desc.len();
        let (date, time) = start.split_once(' ').unwrap();
        let mut bytes = header_bytes(&[
            kv("SIMPLE", "T"),
            kv("BITPIX", "8"),
            kv("NAXIS", "2"),
            kv("NAXIS1", &n_time.to_string()),
            kv("NAXIS2", &n_freq.to_string()),
            kv("EXTEND", "T"),
            ks("DATE-OBS", date),
            ks("TIME-OBS", time),
            ks("INSTRUME", "TESTSTN"),
            ks("BUNIT", "digits"),
            kv("CDELT1", "0.25"),
        ]);
        let mut data = Vec::with_capacity(n_time * n_freq);
        for r in 0..n_freq {
            for c in 0..n_time {
                data.push(((10 * r + c) % 200) as u8);
            }
        }
        data.resize(data.len().div_ceil(2880) * 2880, 0);
        bytes.extend(data);
        bytes.extend(header_bytes(&[
            ks("XTENSION", "BINTABLE"),
            kv("BITPIX", "8"),
            kv("NAXIS", "2"),
            kv("NAXIS1", &((n_time + n_freq) * 8).to_string()),
            kv("NAXIS2", "1"),
            kv("PCOUNT", "0"),
            kv("GCOUNT", "1"),
            kv("TFIELDS", "2"),
            ks("TTYPE1", "TIME"),
            ks("TFORM1", &format!("{n_time}D8.3")),
            ks("TTYPE2", "FREQUENCY"),
            ks("TFORM2", &format!("{n_freq}D8.3")),
        ]));
        let mut table = Vec::new();
        for i in 0..n_time {
            table.extend((i as f64 * 0.25).to_be_bytes());
        }
        for f in freqs_desc {
            table.extend(f.to_be_bytes());
        }
        table.resize(table.len().div_ceil(2880) * 2880, 0);
        bytes.extend(table);
        bytes
    }

    #[test]
    fn loads_synthetic_file() {
        let bytes = synthetic_file("2011/06/07 06:15:00.500", 40, &[80.0, 60.0, 40.0]);
        let fits = FitsFile::from_bytes(bytes).unwrap();
        let f = CallistoFile::from_fits(&fits, "x".into(), "TESTSTN_20110607_061500_59.fit".into()).unwrap();
        assert_eq!(f.instrument, "TESTSTN");
        assert_eq!(f.focus_code.as_deref(), Some("59"));
        assert_eq!(f.freqs, vec![40.0, 60.0, 80.0]);
        assert_eq!((f.n_time(), f.n_freq()), (40, 3));
        // Lowest frequency was the last stored row (r = 2).
        assert_eq!(f.data[0], 20.0);
        assert_eq!(f.data[2 * 40 + 1], 1.0);
        assert_eq!(f.times[4], 1.0);
        assert_eq!(seconds_of_day(f.start), 6.0 * 3600.0 + 15.0 * 60.0 + 0.5);
    }

    #[test]
    fn parses_dates_and_names() {
        assert_eq!(parse_date("2011/06/07"), NaiveDate::from_ymd_opt(2011, 6, 7));
        assert_eq!(parse_date("07/06/11"), NaiveDate::from_ymd_opt(2011, 6, 7));
        assert_eq!(parse_time_of_day("06:14:60.000"), Some(6.0 * 3600.0 + 15.0 * 60.0));
        let n = parse_filename("ALASKA-ANCHORAGE_20250514_001500_01.fit.gz").unwrap();
        assert_eq!(n.station, "ALASKA-ANCHORAGE");
        assert_eq!(n.focus_code.as_deref(), Some("01"));
        assert_eq!(n.start.format("%H%M%S").to_string(), "001500");
        let n = parse_filename("BLEN7M_20110607_061500.fit").unwrap();
        assert_eq!(n.focus_code, None);
        assert!(parse_filename("notes.txt").is_none());
    }
}
