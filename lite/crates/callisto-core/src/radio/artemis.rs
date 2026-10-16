//! Port of `src/backend/radio/artemis.py`: ARTEMIS-IV (Thermopylae, Greece)
//! files written by ARTLOOK.
//!
//! They store the spectrogram as a plain image with a WCS description and a
//! time axis in **hours of UT**, which is rebased here to seconds from the
//! first sample, with the UT start kept separately. Counts are converted to dB
//! above background with the published receiver chain (12-bit ADC over a
//! 70 dB log detector: 58.51 counts/dB). See the Python module for references.

use chrono::NaiveDateTime;

use crate::fits::Header;

pub const ORIGIN_TOKEN: &str = "ARTEMIS";
pub const STATION: &str = "Thermopylae, Greece";
pub const OBSERVATORY: &str = "ARTEMIS-IV";
pub const RECEIVER_ASG: &str = "ASG";
pub const RECEIVER_SAO: &str = "SAO";
pub const ADC_FULL_SCALE_COUNTS: f64 = 4096.0;
pub const ASG_DYNAMIC_RANGE_DB: f64 = 70.0;
const UT_CROSS_CHECK_TOLERANCE_S: f64 = 60.0;
const SECONDS_PER_DAY: f64 = 86400.0;

const RECEIVER_ALIASES: [(&str, &str); 4] =
    [("ASG", RECEIVER_ASG), ("SWEEP", RECEIVER_ASG), ("SAO", RECEIVER_SAO), ("AOS", RECEIVER_SAO)];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeUnit {
    Hours,
    Minutes,
    Seconds,
}

impl TimeUnit {
    fn scale(self) -> f64 {
        match self {
            TimeUnit::Hours => 3600.0,
            TimeUnit::Minutes => 60.0,
            TimeUnit::Seconds => 1.0,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            TimeUnit::Hours => "hours",
            TimeUnit::Minutes => "minutes",
            TimeUnit::Seconds => "seconds",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Calibration {
    pub receiver: String,
    pub adc_full_scale_counts: f64,
    pub dynamic_range_db: f64,
    pub counts_per_db: f64,
    pub dynamic_range_is_assumed: bool,
    pub reference: String,
}

impl Calibration {
    pub fn db_per_count(&self) -> f64 {
        if self.counts_per_db != 0.0 { 1.0 / self.counts_per_db } else { 0.0 }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct TimeAxis {
    pub seconds: Vec<f64>,
    pub ut_start_sec: Option<f64>,
    pub cadence_s: Option<f64>,
    pub unit: TimeUnit,
    /// "cunit", "ut-cross-check" or "convention".
    pub unit_source: &'static str,
}

fn text(h: &Header, key: &str) -> String {
    h.get_str(key).unwrap_or_default().trim().to_string()
}

fn float(h: &Header, key: &str) -> Option<f64> {
    h.get_f64(key).filter(|v| v.is_finite())
}

pub fn is_artemis_header(h: &Header) -> bool {
    text(h, "ORIGIN").to_uppercase().contains(ORIGIN_TOKEN)
        || text(h, "OBSERVAT").to_uppercase().contains(ORIGIN_TOKEN)
        || h.comment("SIMPLE").unwrap_or("").to_uppercase().contains("ARTLOOK")
}

pub fn artemis_receiver(h: &Header) -> String {
    let token = text(h, "INSTRUME").to_uppercase();
    RECEIVER_ALIASES.iter().find(|(alias, _)| token.contains(alias)).map(|(_, r)| r.to_string()).unwrap_or_default()
}

pub fn artemis_calibration(receiver: &str) -> Calibration {
    let name = RECEIVER_ALIASES
        .iter()
        .find(|(alias, _)| *alias == receiver.trim().to_uppercase())
        .map(|(_, r)| r.to_string())
        .unwrap_or_default();
    // The 70 dB figure is published for the ASG; the SAO borrows it, flagged.
    let assumed = name != RECEIVER_ASG;
    let reference = if assumed {
        "12-bit ADC full scale over the ASG dynamic range (this receiver's own figure is unpublished)"
    } else {
        "12-bit ADC full scale over the receiver's log-detector dynamic range"
    };
    Calibration {
        receiver: name,
        adc_full_scale_counts: ADC_FULL_SCALE_COUNTS,
        dynamic_range_db: ASG_DYNAMIC_RANGE_DB,
        counts_per_db: ADC_FULL_SCALE_COUNTS / ASG_DYNAMIC_RANGE_DB,
        dynamic_range_is_assumed: assumed,
        reference: reference.into(),
    }
}

fn parse_ut_time(text: &str) -> Option<f64> {
    let parts: Vec<&str> = text.trim().split(':').collect();
    if parts.len() != 3 {
        return None;
    }
    let h: i64 = parts[0].trim().parse().ok()?;
    let m: i64 = parts[1].trim().parse().ok()?;
    let s: f64 = parts[2].trim().parse().ok()?;
    let total = h as f64 * 3600.0 + m as f64 * 60.0 + s;
    (0.0..SECONDS_PER_DAY * 1.5).contains(&total).then_some(total)
}

/// Seconds of UT day at the start, from `TIME-OBS` (or the time part of `DATE-OBS`).
pub fn ut_seconds_from_header(h: &Header) -> Option<f64> {
    parse_ut_time(&text(h, "TIME-OBS")).or_else(|| {
        let stamp = text(h, "DATE-OBS");
        stamp.split_once('T').and_then(|(_, t)| parse_ut_time(t))
    })
}

fn cunit_time_unit(h: &Header) -> Option<TimeUnit> {
    match text(h, "CUNIT1").to_lowercase().as_str() {
        "h" | "hr" | "hrs" | "hour" | "hours" => Some(TimeUnit::Hours),
        "min" | "mins" | "minute" | "minutes" => Some(TimeUnit::Minutes),
        "s" | "sec" | "secs" | "second" | "seconds" => Some(TimeUnit::Seconds),
        _ => None,
    }
}

pub fn infer_time_unit(h: &Header) -> (TimeUnit, &'static str) {
    if let Some(unit) = cunit_time_unit(h) {
        return (unit, "cunit");
    }
    if let (Some(crval), Some(reference)) = (float(h, "CRVAL1"), ut_seconds_from_header(h)) {
        for unit in [TimeUnit::Hours, TimeUnit::Minutes, TimeUnit::Seconds] {
            if (crval * unit.scale() - reference).abs() <= UT_CROSS_CHECK_TOLERANCE_S {
                return (unit, "ut-cross-check");
            }
        }
    }
    (TimeUnit::Hours, "convention")
}

pub fn artemis_time_axis(h: &Header, length: usize) -> TimeAxis {
    let (unit, unit_source) = infer_time_unit(h);
    let scale = unit.scale();
    let cdelt = float(h, "CDELT1");
    let crval = float(h, "CRVAL1");
    let crpix = float(h, "CRPIX1").unwrap_or(1.0);
    let mut cadence = cdelt.map(|d| d.abs() * scale).filter(|c| c.is_finite());
    let seconds: Vec<f64> = match cdelt {
        _ if length == 0 => Vec::new(),
        None => {
            cadence = Some(1.0);
            (0..length).map(|i| i as f64).collect()
        }
        Some(d) => (0..length).map(|i| i as f64 * (d * scale)).collect(),
    };
    let mut ut_start = crval.map(|v| (v + (1.0 - crpix) * cdelt.unwrap_or(0.0)) * scale);
    if !ut_start.is_some_and(|u| u.is_finite() && (0.0..SECONDS_PER_DAY * 1.5).contains(&u)) {
        ut_start = ut_seconds_from_header(h);
    }
    TimeAxis { seconds, ut_start_sec: ut_start, cadence_s: cadence, unit, unit_source }
}

/// The time axis as seconds from the first sample, plus the UT start;
/// non-ARTEMIS headers get their inputs back.
pub fn normalize_artemis_axes(h: &Header, time: Vec<f64>, sample_count: usize) -> (Vec<f64>, Option<f64>) {
    if !is_artemis_header(h) {
        return (time, None);
    }
    let axis = artemis_time_axis(h, sample_count);
    let seconds = if axis.seconds.len() == sample_count {
        axis.seconds
    } else {
        let c = axis.cadence_s.unwrap_or(1.0);
        (0..sample_count).map(|i| i as f64 * c).collect()
    };
    (seconds, axis.ut_start_sec)
}

pub fn artemis_ut_start_sec(h: &Header) -> Option<f64> {
    if !is_artemis_header(h) {
        return None;
    }
    artemis_time_axis(h, h.get_i64("NAXIS1").unwrap_or(0).max(0) as usize).ut_start_sec
}

/// A FITS timestamp, tolerating ARTLOOK's `YYYY-DD-MM` creation dates.
/// Returns the value and whether day and month had to be swapped.
pub fn parse_fits_datetime(text: &str) -> (Option<NaiveDateTime>, bool) {
    let stamp = text.trim();
    if stamp.is_empty() {
        return (None, false);
    }
    let parse = |s: &str| {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
            .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S"))
            .ok()
            .or_else(|| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok().and_then(|d| d.and_hms_opt(0, 0, 0)))
    };
    if let Some(v) = parse(stamp) {
        return (Some(v), false);
    }
    let parts: Vec<&str> = stamp.splitn(3, '-').collect();
    if parts.len() == 3 && parts[2].len() >= 2 && parts[2].is_char_boundary(2) {
        let swapped = format!("{}-{}-{}{}", parts[0], &parts[2][..2], parts[1], &parts[2][2..]);
        if let Some(v) = parse(&swapped) {
            return (Some(v), true);
        }
    }
    (None, false)
}

/// The unit names the Units sidebar shows: (linear, logarithmic).
pub fn display_units(h: &Header) -> (&'static str, &'static str) {
    if is_artemis_header(h) { ("Counts", "dB") } else { ("Digits", "dB") }
}

/// dB per raw intensity unit for whatever instrument wrote `h`.
pub fn db_scale_for_header(h: &Header, default: f64) -> f64 {
    if is_artemis_header(h) { artemis_calibration(&artemis_receiver(h)).db_per_count() } else { default }
}

/// A first-look clipping range: the 5th and 98th percentiles.
pub fn suggested_display_range(data: &[f32], low: f64, high: f64) -> Option<(f64, f64)> {
    let (lo, hi) = super::frequency_axis::percentile_data_limits(data, low, high)?;
    (lo.is_finite() && hi.is_finite() && hi > lo).then_some((lo, hi))
}

fn receiver_label(receiver: &str) -> String {
    match receiver {
        RECEIVER_ASG => "ASG (sweep-frequency analyser)".into(),
        RECEIVER_SAO => "SAO (acousto-optical analyser)".into(),
        "" => "unknown receiver".into(),
        other => other.into(),
    }
}

fn format_cadence(seconds: f64) -> String {
    if seconds >= 1.0 {
        format!("{} s cadence", super::frequency_axis::format_g(seconds, 6))
    } else {
        let rate = if seconds > 0.0 { 1.0 / seconds } else { 0.0 };
        format!("{} samples/s", super::frequency_axis::format_g(rate, 6))
    }
}

/// Header oddities worth surfacing, phrased for the header viewer.
pub fn artemis_header_notes(h: &Header, axis: &TimeAxis) -> Vec<String> {
    let mut notes = Vec::new();
    if axis.unit != TimeUnit::Seconds {
        let detail = match axis.unit_source {
            "cunit" => "declared by CUNIT1",
            "ut-cross-check" => "confirmed against TIME-OBS",
            _ => "assumed from the ARTLOOK convention; TIME-OBS was unavailable to confirm it",
        };
        notes.push(format!(
            "Time axis stored as UT in {} ({detail}); converted to seconds from the first sample.",
            axis.unit.name()
        ));
    }
    if parse_fits_datetime(&text(h, "DATE")).1 {
        notes.push(format!(
            "DATE (file creation) is written as YYYY-DD-MM: {}. DATE-OBS is well formed and is what the analyzer uses.",
            text(h, "DATE")
        ));
    }
    if float(h, "CDELT2").is_some_and(|d| d < 0.0) {
        notes.push("Frequency axis descends from CRVAL2; rows are stored high frequency first.".into());
    }
    if let Some(blank) = h.get_str("BLANK") {
        notes.push(format!("BLANK={blank} marks deleted samples; those become NaN and are excluded from statistics."));
    }
    let bunit = text(h, "BUNIT");
    if !bunit.is_empty() {
        notes.push(format!(
            "BUNIT is '{bunit}': raw ADC counts. ARTEMIS-IV has no absolute flux calibration, so intensities are relative — dB above the per-channel background."
        ));
    }
    notes
}

/// The readable preamble the FITS header viewer shows above ARTEMIS cards,
/// or "" for other files.
pub fn artemis_header_report(h: &Header, freqs: &[f64]) -> String {
    if !is_artemis_header(h) {
        return String::new();
    }
    let receiver = artemis_receiver(h);
    let channel_count =
        if h.get_i64("NAXIS2").unwrap_or(0) > 0 { h.get_i64("NAXIS2").unwrap() as usize } else { freqs.len() };
    let sample_count = h.get_i64("NAXIS1").unwrap_or(0).max(0) as usize;
    let finite: Vec<f64> = freqs.iter().copied().filter(|f| f.is_finite()).collect();
    let (lo, hi) = finite.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), &f| (a.min(f), b.max(f)));
    let width = {
        let steps: Vec<f64> = finite.windows(2).map(|w| (w[1] - w[0]).abs()).filter(|s| *s > 0.0).collect();
        super::numeric::nanmedian(steps)
    };
    let axis = artemis_time_axis(h, sample_count);
    let duration = axis.cadence_s.filter(|_| sample_count > 0).map(|c| c * sample_count as f64);

    let mut summary = vec![format!("{OBSERVATORY} · {}", receiver_label(&receiver)), STATION.to_string()];
    if !finite.is_empty() {
        summary.push(format!("{lo:.0}–{hi:.0} MHz"));
    }
    if channel_count > 0 {
        summary.push(format!("{channel_count} channels"));
    }
    if let Some(c) = axis.cadence_s.filter(|c| *c > 0.0) {
        summary.push(format_cadence(c));
    }
    let mut lines = vec![summary.join(" · "), String::new()];
    if let (Some(observed), _) = parse_fits_datetime(&text(h, "DATE-OBS")) {
        lines.push(format!("Observation start : {} UT", observed.format("%Y-%m-%d %H:%M:%S")));
    }
    if let Some(d) = duration.filter(|d| *d > 0.0) {
        lines.push(format!("Duration          : {:.2} h ({d:.0} s)", d / 3600.0));
    }
    if let Some(w) = width.filter(|w| *w > 0.0) {
        lines.push(format!("Channel spacing   : {w:.4} MHz"));
    }
    let telescope = text(h, "TELESCOP");
    if !telescope.is_empty() {
        lines.push(format!("Telescope         : {telescope}"));
    }
    let cal = artemis_calibration(&receiver);
    lines.push(String::new());
    lines.push("Calibration".into());
    for line in [
        format!("Receiver: {}", receiver_label(&receiver)),
        format!("ADC full scale: {} counts (12-bit)", super::frequency_axis::format_g(cal.adc_full_scale_counts, 6)),
        format!(
            "Dynamic range: {} dB{}",
            super::frequency_axis::format_g(cal.dynamic_range_db, 6),
            if cal.dynamic_range_is_assumed { " (assumed — see notes)" } else { "" }
        ),
        format!("Scale: {:.3} counts/dB ({:.5} dB/count)", cal.counts_per_db, cal.db_per_count()),
        "Reference level: per-channel background — values are dB above background, not sfu.".into(),
    ] {
        lines.push(format!("  {line}"));
    }
    let notes = artemis_header_notes(h, &axis);
    if !notes.is_empty() {
        lines.push(String::new());
        lines.push("Header notes".into());
        for note in notes {
            lines.push(format!("  - {note}"));
        }
    }
    lines.push(String::new());
    lines.push("-".repeat(72));
    lines.push(String::new());
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fits::HeaderValue;

    fn header(cards: &[(&str, HeaderValue)]) -> Header {
        let mut h = Header::default();
        for (k, v) in cards {
            h.set(k, v.clone(), "");
        }
        h
    }

    #[test]
    fn rebases_hours_to_seconds() {
        let h = header(&[
            ("ORIGIN", HeaderValue::Str("ARTEMIS-IV".into())),
            ("TIME-OBS", HeaderValue::Str("06:00:00".into())),
            ("CRVAL1", HeaderValue::Float(6.0)),
            ("CDELT1", HeaderValue::Float(0.1 / 3600.0)),
            ("NAXIS1", HeaderValue::Int(4)),
        ]);
        assert!(is_artemis_header(&h));
        let axis = artemis_time_axis(&h, 4);
        assert_eq!(axis.unit, TimeUnit::Hours);
        assert_eq!(axis.unit_source, "ut-cross-check");
        assert!((axis.seconds[3] - 0.3).abs() < 1e-9);
        assert_eq!(axis.ut_start_sec, Some(21600.0));
    }

    #[test]
    fn tolerates_swapped_dates() {
        let (v, swapped) = parse_fits_datetime("2015-22-06");
        assert!(swapped);
        assert_eq!(v.unwrap().format("%Y-%m-%d").to_string(), "2015-06-22");
    }
}
