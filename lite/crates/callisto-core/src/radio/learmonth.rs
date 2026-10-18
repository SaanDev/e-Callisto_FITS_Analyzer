//! Port of `src/backend/radio/learmonth.py`: the Learmonth solar radio
//! spectrograph's daily `.srs` files, their 15-minute chunks, and the FIT
//! files the full app writes for a chunk.
//!
//! A day file starts with an 8-byte scan header (year since 2000, month,
//! day, hour, minute, second, nominal step) and two 8-byte channel headers
//! (start and end frequency, big-endian), then alternates 802 one-byte
//! samples with the next scan's 24 header bytes.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::Duration as StdDuration;

use chrono::{Duration, NaiveDate, NaiveDateTime, Timelike};

use super::frequency_axis::{DEFAULT_FREQUENCY_DIRECTION, dominant_frequency_direction};
use crate::fits::{Header, HeaderValue, axis_table_bytes, hdu_bytes};

pub const BASE_URL: &str = "https://downloads.sws.bom.gov.au/wdc/wdc_spec/data/learmonth/raw/";
pub const DEFAULT_RECEIVER_ID: &str = "01";
const HEADER_SIZE: usize = 8;
const CHANNEL_HEADER_SIZE: usize = 8;
const SAMPLE_COUNT: usize = 802;
pub const FULL_CHUNK_SCANS: usize = 300;
const OBS_LATITUDE: f64 = -22.24111;
const OBS_LONGITUDE: f64 = 114.0806;
const OBS_ALTITUDE_M: f64 = 594.0;
const DOWNLOAD_TIMEOUT_S: u64 = 45;

#[derive(Clone, Debug, PartialEq)]
pub enum LearmonthError {
    /// `LearmonthNotFoundError`: no file for that day.
    NotFound(String),
    Archive(String),
}

impl std::fmt::Display for LearmonthError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LearmonthError::NotFound(m) | LearmonthError::Archive(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for LearmonthError {}

type Result<T> = std::result::Result<T, LearmonthError>;

fn archive_error(m: impl Into<String>) -> LearmonthError {
    LearmonthError::Archive(m.into())
}

#[derive(Clone, Debug, PartialEq)]
pub struct LearmonthChunk {
    pub index: usize,
    pub start_dt: NaiveDateTime,
    pub end_dt: NaiveDateTime,
    pub scan_count: usize,
    pub offset_start: u64,
    pub offset_end: u64,
    pub is_partial: bool,
}

impl LearmonthChunk {
    /// `LearmonthDownloaderApp._chunk_label`.
    pub fn label(&self) -> String {
        let suffix = if self.is_partial { " [partial]" } else { "" };
        format!(
            "{}  ->  {}  |  {} scans{suffix}",
            self.start_dt.format("%Y-%m-%d %H:%M:%S"),
            self.end_dt.format("%Y-%m-%d %H:%M:%S"),
            self.scan_count
        )
    }
}

pub fn build_learmonth_filename(day: NaiveDate) -> String {
    format!("LM{}.srs", day.format("%y%m%d"))
}

pub fn resolve_learmonth_url(day: NaiveDate) -> String {
    format!("{BASE_URL}{}/{}", day.format("%y"), build_learmonth_filename(day))
}

pub fn resolve_learmonth_cache_path(day: NaiveDate, cache_dir: &Path) -> PathBuf {
    cache_dir.join(day.format("%y").to_string()).join(build_learmonth_filename(day))
}

pub fn learmonth_fit_filename(chunk: &LearmonthChunk) -> String {
    format!("LEARMONTH_{}_{DEFAULT_RECEIVER_ID}.fit", chunk.start_dt.format("%Y%m%d_%H%M%S"))
}

/// `download_learmonth_day`: the day file in the cache, downloaded once.
pub fn download_learmonth_day(day: NaiveDate, cache_dir: &Path) -> Result<PathBuf> {
    let destination = resolve_learmonth_cache_path(day, cache_dir);
    let parent = destination.parent().unwrap();
    std::fs::create_dir_all(parent)
        .map_err(|e| archive_error(format!("Could not download Learmonth archive data: {e}")))?;
    if destination.metadata().is_ok_and(|m| m.len() > 0) {
        return Ok(destination);
    }
    let url = resolve_learmonth_url(day);
    let part = destination.with_file_name(format!("{}.part", build_learmonth_filename(day)));
    let result = (|| -> Result<()> {
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(StdDuration::from_secs(DOWNLOAD_TIMEOUT_S)))
            .http_status_as_error(false)
            .build()
            .into();
        let resp = match super::archive::mock_get(&url) {
            Some(mocked) => mocked.map(|(_, r)| r),
            None => agent.get(&url).call().map_err(anyhow::Error::from),
        }
        .map_err(|e| archive_error(format!("Could not download Learmonth archive data: {e}")))?;
        let status = resp.status().as_u16();
        if status == 404 {
            return Err(LearmonthError::NotFound(format!(
                "No Learmonth archive data were found for {}.",
                day.format("%Y-%m-%d")
            )));
        }
        if status >= 400 {
            return Err(archive_error(format!("Learmonth archive request failed with HTTP {status}.")));
        }
        let mut reader = resp.into_body().into_reader();
        let mut out =
            File::create(&part).map_err(|e| archive_error(format!("Could not download Learmonth archive data: {e}")))?;
        std::io::copy(&mut reader, &mut out)
            .map_err(|e| archive_error(format!("Could not download Learmonth archive data: {e}")))?;
        Ok(())
    })();
    if let Err(e) = result {
        let _ = std::fs::remove_file(&part);
        return Err(e);
    }
    if !part.metadata().is_ok_and(|m| m.len() > 0) {
        return Err(archive_error("Learmonth archive download completed without writing data."));
    }
    std::fs::rename(&part, &destination)
        .map_err(|e| archive_error(format!("Could not download Learmonth archive data: {e}")))?;
    Ok(destination)
}

/// Reads up to `count` bytes (fewer only at the end of the file).
fn read_up_to(reader: &mut impl Read, count: usize) -> std::io::Result<Vec<u8>> {
    let mut buf = Vec::with_capacity(count);
    reader.take(count as u64).read_to_end(&mut buf)?;
    Ok(buf)
}

fn read_exact(reader: &mut impl Read, count: usize, label: &str) -> Result<Vec<u8>> {
    let buf = read_up_to(reader, count).map_err(|e| archive_error(e.to_string()))?;
    if buf.len() != count {
        return Err(archive_error(format!(
            "{label} could not be read completely from the Learmonth archive file."
        )));
    }
    Ok(buf)
}

fn header_to_datetime(header: &[u8]) -> Result<NaiveDateTime> {
    if header.len() < 6 {
        return Err(archive_error("Learmonth scan header is too short to decode a timestamp."));
    }
    let v: Vec<u32> = header[..6].iter().map(|&b| b as u32).collect();
    NaiveDate::from_ymd_opt(2000 + v[0] as i32, v[1], v[2])
        .and_then(|d| d.and_hms_opt(v[3], v[4], v[5]))
        .ok_or_else(|| {
            let list = v.iter().map(u32::to_string).collect::<Vec<_>>().join(", ");
            archive_error(format!("Invalid Learmonth timestamp in archive header: [{list}]"))
        })
}

fn nominal_step_seconds(header: &[u8]) -> f64 {
    match header.get(6) {
        Some(&v) if v > 0 => v as f64,
        _ => 3.0,
    }
}

struct Prelude {
    initial_header: Vec<u8>,
    start_freq1: u16,
    end_freq2: u16,
}

fn read_prelude(reader: &mut impl Read) -> Result<Prelude> {
    let initial_header = read_exact(reader, HEADER_SIZE, "Learmonth initial header")?;
    let u16be = |b: Vec<u8>| u16::from_be_bytes([b[0], b[1]]);
    let start_freq1 = u16be(read_exact(reader, 2, "Learmonth channel-1 start frequency")?);
    read_exact(reader, 2, "Learmonth channel-1 end frequency")?;
    read_exact(reader, 4, "Learmonth channel-1 trailer")?;
    read_exact(reader, 2, "Learmonth channel-2 start frequency")?;
    let end_freq2 = u16be(read_exact(reader, 2, "Learmonth channel-2 end frequency")?);
    read_exact(reader, 4, "Learmonth channel-2 trailer")?;
    Ok(Prelude { initial_header, start_freq1, end_freq2 })
}

fn open(path: &Path) -> Result<std::io::BufReader<File>> {
    if !path.exists() {
        return Err(archive_error(format!("Learmonth archive file does not exist: {}", path.display())));
    }
    File::open(path).map(std::io::BufReader::new).map_err(|e| archive_error(e.to_string()))
}

/// `list_learmonth_chunks`: the file split every 300 scans.
pub fn list_learmonth_chunks(day_path: &Path) -> Result<Vec<LearmonthChunk>> {
    let mut reader = open(day_path)?;
    let prelude = read_prelude(&mut reader)?;
    let nominal = nominal_step_seconds(&prelude.initial_header);
    let mut pos: u64 = (HEADER_SIZE + 2 * CHANNEL_HEADER_SIZE) as u64;
    let mut current_dt = header_to_datetime(&prelude.initial_header)?;
    let mut chunk_start = current_dt;
    let mut chunk_offset_start = pos;
    let mut chunk_last_sample_end;
    let mut index = 0;
    let mut count = 0;
    let mut chunks = Vec::new();
    loop {
        let sample = read_up_to(&mut reader, SAMPLE_COUNT).map_err(|e| archive_error(e.to_string()))?;
        if sample.is_empty() {
            break;
        }
        if sample.len() != SAMPLE_COUNT {
            return Err(archive_error("Learmonth archive ended unexpectedly while reading scan samples."));
        }
        pos += SAMPLE_COUNT as u64;
        count += 1;
        chunk_last_sample_end = pos;
        let next_header = read_up_to(&mut reader, HEADER_SIZE).map_err(|e| archive_error(e.to_string()))?;
        if next_header.is_empty() {
            // `_infer_chunk_end`: the nominal step is always positive.
            let end_dt = current_dt + Duration::milliseconds((nominal * 1000.0).round() as i64);
            chunks.push(LearmonthChunk {
                index,
                start_dt: chunk_start,
                end_dt,
                scan_count: count,
                offset_start: chunk_offset_start,
                offset_end: chunk_last_sample_end,
                is_partial: count < FULL_CHUNK_SCANS,
            });
            break;
        }
        if next_header.len() != HEADER_SIZE {
            return Err(archive_error("Learmonth archive ended unexpectedly while reading the next scan header."));
        }
        read_exact(&mut reader, 2 * CHANNEL_HEADER_SIZE, "Learmonth channel headers")?;
        pos += (HEADER_SIZE + 2 * CHANNEL_HEADER_SIZE) as u64;
        let next_dt = header_to_datetime(&next_header)?;
        current_dt = next_dt;
        if count >= FULL_CHUNK_SCANS {
            chunks.push(LearmonthChunk {
                index,
                start_dt: chunk_start,
                end_dt: next_dt,
                scan_count: count,
                offset_start: chunk_offset_start,
                offset_end: chunk_last_sample_end,
                is_partial: false,
            });
            index += 1;
            chunk_start = next_dt;
            chunk_offset_start = pos;
            count = 0;
        }
    }
    Ok(chunks)
}

/// `_read_chunk_data`: scan times and `(802, scans)` samples.
fn read_chunk_data(path: &Path, chunk: &LearmonthChunk) -> Result<(Vec<NaiveDateTime>, Vec<Vec<u8>>)> {
    let mut reader = open(path)?;
    reader.seek(SeekFrom::Start(chunk.offset_start)).map_err(|e| archive_error(e.to_string()))?;
    let mut current = chunk.start_dt;
    let mut times = Vec::with_capacity(chunk.scan_count);
    let mut rows = Vec::with_capacity(chunk.scan_count);
    for i in 0..chunk.scan_count {
        rows.push(read_exact(&mut reader, SAMPLE_COUNT, "Learmonth scan samples")?);
        times.push(current);
        if i + 1 >= chunk.scan_count {
            continue;
        }
        let next = read_exact(&mut reader, HEADER_SIZE, "Learmonth scan header")?;
        read_exact(&mut reader, 2 * CHANNEL_HEADER_SIZE, "Learmonth channel headers")?;
        current = header_to_datetime(&next)?;
    }
    Ok((times, rows))
}

fn median(mut values: Vec<f64>) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(f64::total_cmp);
    let n = values.len();
    Some(if n % 2 == 1 { values[n / 2] } else { (values[n / 2 - 1] + values[n / 2]) / 2.0 })
}

fn seconds_of_day(t: NaiveDateTime) -> f64 {
    t.num_seconds_from_midnight() as f64 + t.nanosecond() as f64 / 1e9
}

/// `write_learmonth_chunk_fit`: one chunk as an e-CALLISTO-like FIT file
/// (uint8 image, high frequencies first, axes in a binary table).
pub fn write_learmonth_chunk_fit(day_path: &Path, chunk: &LearmonthChunk, out_path: &Path) -> Result<PathBuf> {
    let mut reader = open(day_path)?;
    let prelude = read_prelude(&mut reader)?;
    drop(reader);
    let nominal = nominal_step_seconds(&prelude.initial_header);
    let (scan_times, rows) = read_chunk_data(day_path, chunk)?;
    if scan_times.is_empty() || rows.is_empty() {
        return Err(archive_error("Learmonth chunk did not contain any scan samples."));
    }
    let start = scan_times[0];
    let times: Vec<f64> = scan_times.iter().map(|t| (*t - start).num_microseconds().unwrap() as f64 / 1e6).collect();
    let step = (prelude.end_freq2 as f64 - prelude.start_freq1 as f64) / SAMPLE_COUNT as f64;
    let mut freqs: Vec<f64> = (0..SAMPLE_COUNT).map(|i| prelude.start_freq1 as f64 + i as f64 * step).collect();
    // `(scans, 802)` stacked and transposed: row f is frequency f.
    let n = rows.len();
    let mut data: Vec<Vec<u8>> = (0..SAMPLE_COUNT).map(|f| rows.iter().map(|r| r[f]).collect()).collect();
    if dominant_frequency_direction(&freqs, DEFAULT_FREQUENCY_DIRECTION) != DEFAULT_FREQUENCY_DIRECTION {
        data.reverse();
        freqs.reverse();
    }
    let max = data.iter().flatten().copied().max().unwrap_or(0);
    let min = data.iter().flatten().copied().min().unwrap_or(0);
    let time_step = median(times.windows(2).map(|w| w[1] - w[0]).filter(|d| d.is_finite() && *d > 0.0).collect())
        .unwrap_or(if nominal != 0.0 { nominal } else { 3.0 });
    let freq_step = median(freqs.windows(2).map(|w| w[1] - w[0]).filter(|d| d.is_finite()).collect()).unwrap_or(0.0);
    let fmin = freqs.iter().copied().fold(f64::INFINITY, f64::min);
    let fmax = freqs.iter().copied().fold(f64::NEG_INFINITY, f64::max);

    let mut h = Header::default();
    let s = |v: &str| HeaderValue::Str(v.into());
    let i = |v: i64| HeaderValue::Int(v);
    let f = HeaderValue::Float;
    h.set("SIMPLE", HeaderValue::Bool(true), "conforms to FITS standard");
    h.set("BITPIX", i(8), "array data type");
    h.set("NAXIS", i(2), "number of array dimensions");
    h.set("NAXIS1", i(n as i64), "");
    h.set("NAXIS2", i(SAMPLE_COUNT as i64), "");
    h.set("EXTEND", HeaderValue::Bool(true), "");
    let (begin, end) = (chunk.start_dt, chunk.end_dt);
    h.set("DATE", s(&begin.format("%Y-%m-%d").to_string()), "Date of observation");
    h.set("CONTENT", s("Learmonth radio flux density"), "Observation title");
    h.set("INSTRUME", s("LEARMONTH"), "Name of the instrument");
    h.set("OBJECT", s("Sun"), "Observed object");
    h.set("DATE-OBS", s(&begin.format("%Y/%m/%d").to_string()), "Date observation starts");
    h.set("TIME-OBS", s(&begin.format("%H:%M:%S").to_string()), "Time observation starts");
    h.set("DATE-END", s(&end.format("%Y/%m/%d").to_string()), "Date observation ends");
    h.set("TIME-END", s(&end.format("%H:%M:%S").to_string()), "Time observation ends");
    h.set("ORIGIN", s("e-CALLISTO FITS Analyzer"), "File creator");
    // astropy drops the default BZERO = 0.0 and BSCALE = 1.0 when writing.
    h.set("BUNIT", s("sfu"), "Z-axis units");
    h.set("DATAMAX", i(max as i64), "Maximum data value");
    h.set("DATAMIN", i(min as i64), "Minimum data value");
    h.set("CRVAL1", f(seconds_of_day(begin)), "Value on axis 1 [sec of day]");
    h.set("CRPIX1", i(1), "Reference pixel of axis 1");
    h.set("CTYPE1", s("TIME [UT]"), "Title of axis 1");
    h.set("CDELT1", f(time_step), "Median step in time axis");
    h.set("CRVAL2", f(freqs[0]), "Value on axis 2 [MHz]");
    h.set("CRPIX2", i(1), "Reference pixel of axis 2");
    h.set("CTYPE2", s("Frequency [MHz]"), "Title of axis 2");
    h.set("CDELT2", f(freq_step), "Median step in frequency axis");
    h.set("FREQMIN", f(fmin), "Minimum frequency [MHz]");
    h.set("FREQMAX", f(fmax), "Maximum frequency [MHz]");
    h.set("OBS_LAT", f(OBS_LATITUDE), "Observatory latitude in degree");
    h.set("OBS_LAC", s("S"), "Observatory latitude code {N, S}");
    h.set("OBS_LON", f(OBS_LONGITUDE), "Observatory longitude in degree");
    h.set("OBS_LOC", s("E"), "Observatory longitude code {E, W}");
    h.set("OBS_ALT", f(OBS_ALTITUDE_M), "Observatory altitude in meter");
    let raw_name = day_path.file_name().unwrap_or_default().to_string_lossy();
    h.set("RAWFILE", s(&raw_name), "Learmonth source archive file");
    h.set("CHUNKIDX", i(chunk.index as i64), "Learmonth chunk index");
    h.set("PARTIAL", HeaderValue::Bool(chunk.is_partial), "Chunk ended before 300 scans");

    let image: Vec<u8> = data.concat();
    let mut bytes = hdu_bytes(&h, &image);
    bytes.extend(axis_table_bytes(&[("Time", &times), ("Frequency", &freqs)]));
    if let Some(parent) = out_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| archive_error(e.to_string()))?;
    }
    std::fs::write(out_path, bytes).map_err(|e| archive_error(e.to_string()))?;
    Ok(out_path.to_path_buf())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A synthetic day: 320 scans 3 s apart from 23:50, 25–180 MHz.
    pub(crate) fn synthetic_day(dir: &Path) -> PathBuf {
        let mut buf = Vec::new();
        let t0 = NaiveDate::from_ymd_opt(2024, 5, 1).unwrap().and_hms_opt(23, 50, 0).unwrap();
        let mut seed: u32 = 7;
        for i in 0..320 {
            let t = t0 + Duration::seconds(3 * i);
            buf.extend([
                (t.date().format("%y").to_string().parse::<u8>().unwrap()),
                t.date().format("%m").to_string().parse().unwrap(),
                t.date().format("%d").to_string().parse().unwrap(),
                t.hour() as u8,
                t.minute() as u8,
                t.second() as u8,
                3,
                0,
            ]);
            buf.extend([0, 25, 0, 75, 0, 0, 0, 0, 0, 75, 0, 180, 0, 0, 0, 0]);
            for _ in 0..SAMPLE_COUNT {
                seed = seed.wrapping_mul(1_103_515_245).wrapping_add(12345);
                buf.push((seed >> 16) as u8);
            }
        }
        let path = dir.join("LM240501.srs");
        std::fs::write(&path, buf).unwrap();
        path
    }

    #[test]
    fn chunks_and_fit_files() {
        let dir = std::env::temp_dir().join(format!("lm_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let day = synthetic_day(&dir);
        let chunks = list_learmonth_chunks(&day).unwrap();
        assert_eq!(chunks.len(), 2);
        assert_eq!((chunks[0].scan_count, chunks[0].offset_start, chunks[0].offset_end), (300, 24, 247800));
        assert_eq!(chunks[0].end_dt.format("%H:%M:%S").to_string(), "00:05:00");
        assert_eq!((chunks[1].scan_count, chunks[1].offset_start, chunks[1].is_partial), (20, 247824, true));
        assert_eq!(chunks[1].end_dt.format("%H:%M:%S").to_string(), "00:06:00");
        assert_eq!(learmonth_fit_filename(&chunks[1]), "LEARMONTH_20240502_000500_01.fit");
        assert_eq!(
            chunks[1].label(),
            "2024-05-02 00:05:00  ->  2024-05-02 00:06:00  |  20 scans [partial]"
        );
        let out = write_learmonth_chunk_fit(&day, &chunks[1], &dir.join(learmonth_fit_filename(&chunks[1]))).unwrap();
        let res = crate::radio::fits_io::load_callisto_fits(&out).unwrap();
        assert_eq!(res.data.shape(), (802, 20));
        assert_eq!(res.time[1], 3.0);
        assert!((res.freqs[0] - 179.8067331670823).abs() < 1e-12);
        let text = res.header0.to_text();
        assert!(text.contains("CDELT2  = -0.19326683291770763 / Median step in frequency axis"));
        assert!(text.contains("OBJECT  = 'Sun     '           / Observed object"));
        assert_eq!(resolve_learmonth_url(NaiveDate::from_ymd_opt(2024, 5, 1).unwrap()), format!("{BASE_URL}24/LM240501.srs"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
