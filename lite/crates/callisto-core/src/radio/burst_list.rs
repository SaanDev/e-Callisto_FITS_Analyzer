//! Port of `src/backend/radio/burst_list.py` (the published Monstein /
//! e-CALLISTO burst lists) and `src/ui/radio/burst_fits.py` (the archive
//! files covering a catalog burst).
//!
//! The archive holds monthly e-CALLISTO tables, older BLEN/SGD tables with
//! times in tenths of a minute, and a November 2014 CELESTINA detection list.
//! Reported values and qualifications are kept; nothing is guessed to repair
//! source typos.

use std::collections::{BTreeMap, HashSet};

use chrono::{Datelike, Duration, NaiveDate, NaiveDateTime, NaiveTime};

use super::archive::{REQUEST_TIMEOUT_S, agent, day_url, extract_fits_links, get_with_retries, join_url};
use super::cache::percent_decode;
use super::downloader::{Candidate, sort_event_candidates};
use super::naming::{basename, parse_archive_filename, strip_fits_suffix};

pub const BURST_LIST_BASE_URL: &str = "https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/";
/// `burst_fits.NOMINAL_FITS_DURATION`.
pub const NOMINAL_FITS_SECONDS: i64 = 900;
/// The burst types every catalog offers in the type filter.
pub const TYPE_FAMILIES: [&str; 9] = ["I", "II", "III", "IV", "V", "CTM", "U", "J", "RFI"];

/// One catalog event; times are naive UTC, as in the FITS downloader.
#[derive(Clone, Debug, PartialEq)]
pub struct BurstEvent {
    pub start_utc: NaiveDateTime,
    pub end_utc: NaiveDateTime,
    pub burst_type: String,
    pub frequency_min_mhz: Option<f64>,
    pub frequency_max_mhz: Option<f64>,
    pub stations: Vec<String>,
    pub remarks: String,
    pub source_url: String,
    pub raw_line: String,
}

impl BurstEvent {
    /// Every field, so equal events are one (the dataclass is frozen and hashed).
    fn key(&self) -> String {
        format!(
            "{}|{}|{}|{:?}|{:?}|{}|{}|{}|{}",
            self.start_utc,
            self.end_utc,
            self.burst_type,
            self.frequency_min_mhz.map(f64::to_bits),
            self.frequency_max_mhz.map(f64::to_bits),
            self.stations.join("\u{1f}"),
            self.remarks,
            self.source_url,
            self.raw_line
        )
    }
}

#[derive(Clone, Debug, Default)]
pub struct BurstListResult {
    pub events: Vec<BurstEvent>,
    pub warnings: Vec<String>,
    /// Complete headers, legends and notices, keyed by source URL.
    pub source_notes: BTreeMap<String, String>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum BurstListError {
    Cancelled,
    Failed(String),
}

impl std::fmt::Display for BurstListError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BurstListError::Cancelled => f.write_str("Burst list retrieval cancelled."),
            BurstListError::Failed(m) => f.write_str(m),
        }
    }
}

#[derive(Default)]
struct Parsed {
    events: Vec<BurstEvent>,
    notes: Vec<String>,
    invalid: Vec<usize>,
    notices: Vec<(NaiveDate, String)>,
    recognized: bool,
}

// ------------------------------------------------------------- text helpers

/// Python's `str.splitlines()`.
fn splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        let is_break = matches!(
            c,
            '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
        );
        if !is_break {
            continue;
        }
        out.push(&text[start..i]);
        let mut next = i + c.len_utf8();
        if c == '\r'
            && let Some(&(j, '\n')) = chars.peek()
        {
            chars.next();
            next = j + 1;
        }
        start = next;
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

fn is_space(c: char) -> bool {
    c.is_whitespace()
}

/// Leading ASCII digits of `s`.
fn digits(s: &str) -> usize {
    s.bytes().take_while(u8::is_ascii_digit).count()
}

/// `\d{1,2}:\d{2}(?::\d{2})?` at the start of `s`; the matched length.
fn clock_len(s: &str) -> Option<usize> {
    let b = s.as_bytes();
    let h = digits(s).min(2);
    if h == 0 || b.get(h) != Some(&b':') {
        return None;
    }
    let m = digits(&s[h + 1..]);
    if m < 2 {
        return None;
    }
    let mut len = h + 3;
    if b.get(len) == Some(&b':') && digits(&s[len + 1..]) >= 2 {
        len += 3;
    }
    Some(len)
}

struct ModernRow<'a> {
    date: &'a str,
    start: &'a str,
    end: &'a str,
    rest: &'a str,
}

/// `_MODERN_ROW.fullmatch(line)`.
fn modern_row(line: &str) -> Option<ModernRow<'_>> {
    if digits(line) < 8 {
        return None;
    }
    let date = &line[..8];
    let after = &line[8..];
    let ws = after.len() - after.trim_start_matches(is_space).len();
    if ws == 0 {
        return None;
    }
    let s = &after[ws..];
    let start_len = clock_len(s)?;
    let start = &s[..start_len];
    let s = s[start_len..].trim_start_matches(is_space);
    let s = s.strip_prefix('-').or_else(|| s.strip_prefix('\u{2013}'))?.trim_start_matches(is_space);
    let end_len = clock_len(s)?;
    let end = &s[..end_len];
    let after = &s[end_len..];
    let rest = after.trim_start_matches(is_space);
    if rest.len() == after.len() || rest.is_empty() || rest.contains(['\n', '\r']) {
        return None;
    }
    Some(ModernRow { date, start, end, rest })
}

/// `strptime(value, "%Y%m%d")` for eight digits.
fn ymd(value: &str) -> Option<NaiveDate> {
    if value.len() != 8 || digits(value) != 8 {
        return None;
    }
    NaiveDate::from_ymd_opt(value[..4].parse().ok()?, value[4..6].parse().ok()?, value[6..].parse().ok()?)
}

/// `strptime(value, "%y%m%d")` for six digits.
fn short_ymd(value: &str) -> Option<NaiveDate> {
    if value.len() != 6 || digits(value) != 6 {
        return None;
    }
    let y: i32 = value[..2].parse().ok()?;
    let year = if y < 69 { 2000 + y } else { 1900 + y };
    NaiveDate::from_ymd_opt(year, value[2..4].parse().ok()?, value[4..].parse().ok()?)
}

/// `_clock`.
fn clock(day: NaiveDate, value: &str, legacy: bool) -> Option<NaiveDateTime> {
    let (hour, minute, second) = if legacy {
        // `_LEGACY_CLOCK`: HHMM with an optional tenth of a minute.
        let (main, tenth) = match value.split_once('.') {
            Some((m, t)) if t.len() == 1 && digits(t) == 1 => (m, t.parse::<u32>().ok()?),
            Some(_) => return None,
            None => (value, 0),
        };
        if main.len() != 4 || digits(main) != 4 {
            return None;
        }
        (main[..2].parse::<u32>().ok()?, main[2..].parse::<u32>().ok()?, tenth * 6)
    } else {
        let parts: Vec<u32> = value.split(':').map(|p| p.parse().ok()).collect::<Option<_>>()?;
        (parts[0], parts[1], parts.get(2).copied().unwrap_or(0))
    };
    if hour == 24 && minute == 0 && second == 0 {
        return Some(day.succ_opt()?.and_time(NaiveTime::MIN));
    }
    Some(day.and_time(NaiveTime::from_hms_opt(hour, minute, second)?))
}

/// `_stations`: the reported stations and notes on qualified ones.
fn stations(value: &str) -> (Vec<String>, Vec<String>) {
    let mut out: Vec<String> = Vec::new();
    let mut notes = Vec::new();
    for raw in value.split([',', ';']) {
        let raw = raw.trim();
        let station = raw.trim_matches(|c| "()[]? ".contains(c));
        if station.is_empty() || station == "---" || station == "-" {
            continue;
        }
        if raw.contains(['(', ')', '[', ']', '?']) {
            notes.push(format!("Station qualification: {raw}"));
        }
        if !out.iter().any(|s| s.to_lowercase() == station.to_lowercase()) {
            out.push(station.to_owned());
        }
    }
    (out, notes)
}

/// Python's `str.split(None, 1)`.
fn split_once_ws(s: &str) -> (&str, &str) {
    let s = s.trim_start_matches(is_space);
    match s.find(is_space) {
        Some(i) => (&s[..i], s[i..].trim_start_matches(is_space)),
        None => (s, ""),
    }
}

fn parse_modern(m: &ModernRow, source_url: &str, raw_line: &str, untyped: bool) -> Option<Option<BurstEvent>> {
    let day = ymd(m.date)?;
    let start = clock(day, m.start, false)?;
    let mut end = clock(day, m.end, false)?;
    if end < start {
        end += Duration::days(1);
    }
    let rest = m.rest.trim();
    let (mut first, tail) = match rest.split_once('\t') {
        Some((f, t)) => (f, t),
        None => split_once_ws(rest),
    };
    first = first.trim();
    if first.to_uppercase() == "REM" {
        return Some(None);
    }
    let (burst_type, station_text) = if untyped { ("---", rest) } else { (first, tail) };
    let (station_text, comment) = station_text.split_once('\t').unwrap_or((station_text, ""));
    let (station_text, inline) = match station_text.split_once('#') {
        Some((s, c)) => (s, Some(c)),
        None => (station_text, None),
    };
    let (stations, mut notes) = stations(station_text);
    if burst_type == "---" {
        notes.push("No burst classification supplied by the catalog".into());
    }
    if !comment.trim().is_empty() {
        notes.push(comment.trim().into());
    }
    if let Some(c) = inline
        && !c.trim().is_empty()
    {
        notes.push(c.trim().into());
    }
    Some(Some(BurstEvent {
        start_utc: start,
        end_utc: end,
        burst_type: burst_type.into(),
        frequency_min_mhz: None,
        frequency_max_mhz: None,
        stations,
        remarks: notes.join("; "),
        source_url: source_url.into(),
        raw_line: raw_line.into(),
    }))
}

/// `_NUMBER`: `[<>]?\d+(\.\d+)?[Xx?]?`, the number.
fn sgd_number(s: &str) -> Option<f64> {
    let s = s.strip_prefix(['<', '>']).unwrap_or(s);
    let s = s.strip_suffix(['X', 'x', '?']).unwrap_or(s);
    let int = digits(s);
    if int == 0 {
        return None;
    }
    let frac = &s[int..];
    if !frac.is_empty() && !(frac.starts_with('.') && frac.len() > 1 && digits(&frac[1..]) == frac.len() - 1) {
        return None;
    }
    s.parse().ok()
}

fn parse_legacy(line: &str, source_url: &str) -> Option<Option<BurstEvent>> {
    let mut fields: Vec<&str> = line.split_whitespace().collect();
    let day = short_ymd(fields.remove(0))?;
    let four = |s: &str| s.len() == 4 && digits(s) == 4;
    if fields.len() >= 2 && four(fields[0]) && four(fields[1]) {
        fields.drain(..2);
    }
    if fields.is_empty() {
        return None;
    }
    let station = fields.remove(0);
    if fields.is_empty() {
        return Some(None);
    }
    if fields.len() < 5 {
        return None;
    }
    let start = clock(day, fields[0], true)?;
    let mut end = clock(day, fields[1], true)?;
    if end < start {
        end += Duration::days(1);
    }
    let burst_type = fields[2];
    let details = &fields[3..];
    let low = sgd_number(details[details.len() - 2])?;
    let high = sgd_number(details[details.len() - 1])?;
    let mut notes = vec!["SGD/BLEN record; times reported in tenths of a minute".to_string()];
    if details.len() > 2 {
        notes.push(format!("SGD subtype/intensity: {}", details[..details.len() - 2].join(" ")));
    }
    notes.push(format!("Reported frequency bounds (MHz): {}", details[details.len() - 2..].join(" ")));
    Some(Some(BurstEvent {
        start_utc: start,
        end_utc: end,
        burst_type: burst_type.into(),
        frequency_min_mhz: Some(low.min(high)),
        frequency_max_mhz: Some(low.max(high)),
        stations: vec![station.into()],
        remarks: notes.join("; "),
        source_url: source_url.into(),
        raw_line: line.into(),
    }))
}

/// `^\d{8}\s+[#:-]+(?:\s|$)`: a dated notice row.
fn is_notice_row(line: &str) -> bool {
    if digits(line) < 8 {
        return false;
    }
    let after = &line[8..];
    let s = after.trim_start_matches(is_space);
    if s.len() == after.len() {
        return false;
    }
    let marks = s.len() - s.trim_start_matches(['#', ':', '-']).len();
    marks > 0 && s[marks..].chars().next().is_none_or(is_space)
}

/// `^\d{n}\s` for `n` in `range` (a greedy digit run).
fn starts_with_digits_then_space(line: &str, min: usize, max: usize) -> bool {
    let n = digits(line);
    (min..=max).any(|k| k <= n && line[k..].chars().next().is_some_and(is_space))
}

fn file_name(url: &str) -> String {
    let path = url::Url::parse(url).map(|u| u.path().to_owned()).unwrap_or_else(|_| url.to_owned());
    path.rsplit('/').next().unwrap_or("").to_owned()
}

/// `_RANGE_NAME`: `YYYYMMDD-YYYYMMDD_nhitN.txt`; `(start, end, threshold)`.
fn range_name(name: &str) -> Option<(&str, &str, &str)> {
    let lower = name.to_lowercase();
    let stem = name.get(..lower.strip_suffix(".txt")?.len())?;
    let (dates, n) = stem.split_once('_')?;
    let n = n.get(4..).filter(|_| n.get(..4).is_some_and(|p| p.eq_ignore_ascii_case("nhit")))?;
    let (a, b) = dates.split_once('-')?;
    (a.len() == 8 && digits(a) == 8 && b.len() == 8 && digits(b) == 8 && !n.is_empty() && digits(n) == n.len())
        .then_some((a, b, n))
}

/// `_MONTHLY_NAME`: `e-CALLISTO_YYYY_MM.txt` or `SGD_BLEN_YYYY_MM.txt`.
fn monthly_name(name: &str) -> Option<(i32, u32)> {
    let lower = name.to_lowercase();
    let rest = lower.strip_prefix("e-callisto_").or_else(|| lower.strip_prefix("sgd_blen_"))?;
    let (y, m) = rest.strip_suffix(".txt")?.split_once('_')?;
    if !(y.len() == 4 && digits(y) == 4 && m.len() == 2 && digits(m) == 2) {
        return None;
    }
    Some((y.parse().ok()?, m.parse().ok()?))
}

fn parse_content(text: &str, source_url: &str) -> Parsed {
    let mut parsed = Parsed::default();
    let mut untyped = range_name(&file_name(source_url)).is_some();
    let mut seen = HashSet::new();
    for (index, raw_line) in splitlines(text).into_iter().enumerate() {
        let number = index + 1;
        let line = raw_line.trim().trim_start_matches('\u{feff}');
        if line.is_empty() {
            continue;
        }
        let lower = line.to_lowercase();
        if line.starts_with('#') || lower.starts_with("product:") {
            parsed.notes.push(raw_line.into());
            if lower.contains("date") && lower.contains("time") && lower.contains("stations") {
                parsed.recognized = true;
                untyped = !lower.contains("type");
            }
            continue;
        }
        if is_notice_row(line) {
            parsed.recognized = true;
            parsed.notes.push(raw_line.into());
            continue;
        }
        let modern = modern_row(line);
        let legacy = starts_with_digits_then_space(line, 6, 6);
        if modern.is_some() || legacy {
            let event = match &modern {
                Some(m) => parse_modern(m, source_url, raw_line, untyped),
                None => parse_legacy(raw_line, source_url),
            };
            match event {
                Some(event) => {
                    parsed.recognized = true;
                    match event {
                        None => {
                            parsed.notes.push(raw_line.into());
                            if let Some(m) = &modern
                                && let Some(day) = ymd(m.date)
                            {
                                parsed.notices.push((day, m.rest.trim().into()));
                            }
                        }
                        Some(e) => {
                            if seen.insert(e.key()) {
                                parsed.events.push(e);
                            }
                        }
                    }
                }
                None => {
                    parsed.invalid.push(number);
                    parsed.notes.push(raw_line.into());
                }
            }
        } else if starts_with_digits_then_space(line, 6, 8) {
            parsed.invalid.push(number);
            parsed.notes.push(raw_line.into());
        } else {
            parsed.notes.push(raw_line.into());
            if lower.contains("no bursts") {
                parsed.recognized = true;
            }
        }
    }
    parsed.events.sort_by(|a, b| (a.start_utc, a.end_utc, &a.burst_type).cmp(&(b.start_utc, b.end_utc, &b.burst_type)));
    parsed
}

/// `parse_burst_list`: the events of one catalog table.
pub fn parse_burst_list(text: &str, source_url: &str) -> Vec<BurstEvent> {
    parse_content(text, source_url).events
}

/// `_matches_type`: Roman numerals match as families (II is not III).
fn matches_type(value: &str, requested: &str) -> bool {
    let value = value.to_uppercase();
    let requested = requested.to_uppercase();
    if ["UNCLASSIFIED", "UNKNOWN", "---"].contains(&requested.as_str()) {
        return ["---", "?", "???", "UNKNOWN"].contains(&value.as_str());
    }
    if requested.contains('/') {
        return value == requested;
    }
    let families: Vec<&str> =
        value.split(['+', ',']).map(|p| p.split('/').next().unwrap_or("").trim_matches(|c| c == '?' || c == ' ')).collect();
    if requested == "III" {
        return families.iter().any(|f| ["III", "IIIG", "IIIGG"].contains(f));
    }
    families.contains(&requested.as_str())
}

/// `filter_burst_events`: type family, reporting-station substring, text,
/// and frequency overlap (records without frequencies drop out).
pub fn filter_burst_events(
    events: &[BurstEvent],
    burst_type: &str,
    station: &str,
    text: &str,
    frequency_min_mhz: Option<f64>,
    frequency_max_mhz: Option<f64>,
) -> Result<Vec<BurstEvent>, String> {
    if let (Some(lo), Some(hi)) = (frequency_min_mhz, frequency_max_mhz)
        && lo > hi
    {
        return Err("Minimum frequency must not exceed maximum frequency.".into());
    }
    let burst_type = burst_type.trim();
    let station = station.trim().to_lowercase();
    let text = text.trim().to_lowercase();
    Ok(events
        .iter()
        .filter(|e| burst_type.is_empty() || matches_type(&e.burst_type, burst_type))
        .filter(|e| station.is_empty() || e.stations.iter().any(|s| s.to_lowercase().contains(&station)))
        .filter(|e| {
            if text.is_empty() {
                return true;
            }
            let mut hay = vec![e.raw_line.as_str(), e.remarks.as_str(), e.burst_type.as_str()];
            hay.extend(e.stations.iter().map(String::as_str));
            hay.join(" ").to_lowercase().contains(&text)
        })
        .filter(|e| frequency_min_mhz.is_none_or(|lo| e.frequency_max_mhz.is_some_and(|hi| hi >= lo)))
        .filter(|e| frequency_max_mhz.is_none_or(|hi| e.frequency_min_mhz.is_some_and(|lo| lo <= hi)))
        .cloned()
        .collect())
}

// ------------------------------------------------------------- fetching

/// `href` values of `<a>` tags (`html.parser` unescapes them).
fn anchor_hrefs(body: &str) -> Vec<String> {
    let lower = body.to_ascii_lowercase();
    let mut out = Vec::new();
    let mut pos = 0;
    while let Some(found) = lower[pos..].find("<a") {
        let start = pos + found + 2;
        pos = start;
        if !lower[start..].starts_with(|c: char| c.is_ascii_whitespace() || c == '>' || c == '/') {
            continue;
        }
        let end = lower[start..].find('>').map_or(lower.len(), |e| start + e);
        let tag = &body[start..end];
        let tag_lower = &lower[start..end];
        let mut search = 0;
        while let Some(i) = tag_lower[search..].find("href") {
            let at = search + i;
            search = at + 4;
            if at > 0 && !tag_lower.as_bytes()[at - 1].is_ascii_whitespace() {
                continue;
            }
            let rest = tag[at + 4..].trim_start();
            let Some(rest) = rest.strip_prefix('=') else { continue };
            let rest = rest.trim_start();
            let value = match rest.chars().next() {
                Some(q @ ('"' | '\'')) => rest[1..].split(q).next().unwrap_or(""),
                _ => rest.split(|c: char| c.is_ascii_whitespace()).next().unwrap_or(""),
            };
            if !value.is_empty() {
                out.push(
                    value
                        .replace("&amp;", "&")
                        .replace("&lt;", "<")
                        .replace("&gt;", ">")
                        .replace("&quot;", "\"")
                        .replace("&#39;", "'"),
                );
            }
            break;
        }
        pos = end;
    }
    out
}

/// `_listing_links`: children of `directory`, without queries or fragments.
pub fn listing_links(body: &str, directory: &str) -> Vec<String> {
    let Ok(base) = url::Url::parse(directory) else { return Vec::new() };
    let mut links: Vec<String> = Vec::new();
    for href in anchor_hrefs(body) {
        let Ok(joined) = base.join(&href) else { continue };
        if joined.query().is_some() || joined.fragment().is_some() {
            continue;
        }
        let s = joined.to_string();
        if s.starts_with(directory) && !links.contains(&s) {
            links.push(s);
        }
    }
    links
}

/// `_source_period`: `(first day, last day, coincidence threshold)`.
pub fn source_period(url: &str) -> Option<(NaiveDate, NaiveDate, Option<u32>)> {
    let name = percent_decode(&file_name(url));
    if let Some((year, month)) = monthly_name(&name) {
        let first = NaiveDate::from_ymd_opt(year, month, 1)?;
        let next = if month == 12 { NaiveDate::from_ymd_opt(year + 1, 1, 1) } else { first.with_month(month + 1) }?;
        return Some((first, next.pred_opt()?, None));
    }
    let (a, b, n) = range_name(&name)?;
    Some((ymd(a)?, ymd(b)?, n.parse().ok()))
}

fn last_day_of_month(year: i32, month: u32) -> NaiveDate {
    let next = if month == 12 { (year + 1, 1) } else { (year, month + 1) };
    NaiveDate::from_ymd_opt(next.0, next.1, 1).unwrap().pred_opt().unwrap()
}

/// `fetch_burst_events`: the published tables covering an inclusive range
/// of UTC start dates. Missing years, months and 404/410 tables become
/// warnings; network and other HTTP errors fail. `progress` gets
/// `(done, total, message)` (total 0 while discovering).
pub fn fetch_burst_events(
    start_date: NaiveDate,
    end_date: NaiveDate,
    mut progress: impl FnMut(usize, usize, String),
    cancelled: impl Fn() -> bool,
) -> Result<BurstListResult, BurstListError> {
    if start_date > end_date {
        return Err(BurstListError::Failed("Start date must not be after end date.".into()));
    }
    let check = || if cancelled() { Err(BurstListError::Cancelled) } else { Ok(()) };
    let agent = agent(REQUEST_TIMEOUT_S);
    let get = |url: &str, missing_ok: bool| -> Result<Option<String>, BurstListError> {
        check()?;
        let (status, resp) = get_with_retries(&agent, url)
            .map_err(|e| BurstListError::Failed(format!("Could not reach the burst catalog: {e}")))?;
        check()?;
        if missing_ok && (status == 404 || status == 410) {
            return Ok(None);
        }
        if status != 200 {
            return Err(BurstListError::Failed(format!(
                "Could not retrieve burst catalog (HTTP {status}): {url}"
            )));
        }
        let bytes = resp
            .into_body()
            .with_config()
            .limit(64 * 1024 * 1024)
            .read_to_vec()
            .map_err(|e| BurstListError::Failed(format!("Could not reach the burst catalog: {e}")))?;
        // Historical files use Latin-1; newer ones may use UTF-8.
        Ok(Some(match std::str::from_utf8(&bytes) {
            Ok(s) => s.trim_start_matches('\u{feff}').to_owned(),
            Err(_) => bytes.iter().map(|&b| b as char).collect(),
        }))
    };
    let mut report = |done: usize, total: usize, message: String| -> Result<(), BurstListError> {
        check()?;
        progress(done, total, message);
        Ok(())
    };

    let mut result = BurstListResult::default();
    check()?;
    report(0, 0, "Reading published burst catalog years\u{2026}".into())?;
    let root = get(BURST_LIST_BASE_URL, false)?.unwrap_or_default();
    let mut years: BTreeMap<i32, String> = BTreeMap::new();
    for url in listing_links(&root, BURST_LIST_BASE_URL) {
        let tail = &url[BURST_LIST_BASE_URL.len()..];
        if tail.len() == 5 && tail.ends_with('/') && digits(tail) == 4 {
            years.insert(tail[..4].parse().unwrap(), url.clone());
        }
    }
    if years.is_empty() {
        return Err(BurstListError::Failed(
            "The burst catalog index contains no recognized year directories.".into(),
        ));
    }
    type Period = (NaiveDate, NaiveDate, Option<u32>);
    let mut sources: Vec<(String, Period)> = Vec::new();
    let mut missing_years = Vec::new();
    for year in start_date.year()..=end_date.year() {
        check()?;
        let Some(year_url) = years.get(&year) else {
            missing_years.push(year);
            continue;
        };
        report(0, 0, format!("Reading {year} burst catalog index\u{2026}"))?;
        let Some(listing) = get(year_url, true)? else {
            missing_years.push(year);
            continue;
        };
        let available: Vec<(String, Period)> = listing_links(&listing, year_url)
            .into_iter()
            .filter_map(|url| {
                let p = source_period(&url)?;
                (p.0 <= end_date && p.1 >= start_date).then_some((url, p))
            })
            .collect();
        // CELESTINA nhit3 overlaps nhit2: keep the lowest threshold per period.
        let mut thresholds: BTreeMap<(NaiveDate, NaiveDate), u32> = BTreeMap::new();
        for (_, (first, last, threshold)) in &available {
            if let Some(t) = threshold {
                let entry = thresholds.entry((*first, *last)).or_insert(*t);
                *entry = (*entry).min(*t);
            }
        }
        for (url, period) in &available {
            if period.2.is_none() || period.2 == thresholds.get(&(period.0, period.1)).copied() {
                sources.push((url.clone(), *period));
            }
        }
        let first_month = if year == start_date.year() { start_date.month() } else { 1 };
        let last_month = if year == end_date.year() { end_date.month() } else { 12 };
        let missing_months: Vec<String> = (first_month..=last_month)
            .filter(|&m| {
                let (mf, ml) = (NaiveDate::from_ymd_opt(year, m, 1).unwrap(), last_day_of_month(year, m));
                !available.iter().any(|(_, (f, l, _))| *f <= ml && *l >= mf)
            })
            .map(|m| format!("{m:02}"))
            .collect();
        if !missing_months.is_empty() {
            result
                .warnings
                .push(format!("No published burst catalog for {year} month(s): {}.", missing_months.join(", ")));
        }
    }
    if !missing_years.is_empty() {
        let list: Vec<String> = missing_years.iter().map(i32::to_string).collect();
        result.warnings.push(format!("No published burst catalog for year(s): {}.", list.join(", ")));
    }
    if start_date.year() <= 2019 && end_date.year() >= 2012 {
        result.warnings.push(
            "The source reports no regular burst lists for 2012\u{2013}2019; November 2014 has a separate \
             unclassified CELESTINA detection list."
                .into(),
        );
    }
    sources.sort_by(|a, b| (a.1.0, &a.0).cmp(&(b.1.0, &b.0)));
    let total = sources.len();
    for (index, (url, (period_start, period_end, threshold))) in sources.iter().enumerate() {
        let filename = url.rsplit('/').next().unwrap_or("").to_owned();
        report(index, total, format!("Loading {filename}\u{2026}"))?;
        let Some(body) = get(url, true)? else {
            result.warnings.push(format!("Published catalog is unavailable: {url}"));
            continue;
        };
        let parsed = parse_content(&body, url);
        check()?;
        if !parsed.recognized {
            return Err(BurstListError::Failed(format!("Unrecognized burst catalog format: {url}")));
        }
        result.source_notes.insert(url.clone(), parsed.notes.join("\n"));
        if !parsed.invalid.is_empty() {
            let lines: Vec<String> = parsed.invalid.iter().take(10).map(usize::to_string).collect();
            result.warnings.push(format!(
                "{filename}: skipped {} invalid record(s), at line(s) {}.",
                parsed.invalid.len(),
                lines.join(", ")
            ));
        }
        let outside =
            parsed.events.iter().filter(|e| !(*period_start..=*period_end).contains(&e.start_utc.date())).count();
        if outside > 0 {
            result.warnings.push(format!(
                "{filename}: {outside} record(s) have dates outside the file's stated period; source dates were preserved."
            ));
        }
        for (day, notice) in &parsed.notices {
            if (start_date..=end_date).contains(day) {
                result.warnings.push(format!("{} \u{2014} {notice}", day.format("%Y-%m-%d")));
            }
        }
        if let Some(t) = threshold {
            result.warnings.push(format!(
                "{filename}: unclassified CELESTINA detections with at least {t} coincident stations; overlapping \
                 higher-threshold lists are omitted."
            ));
        }
        result.events.extend(parsed.events.into_iter().filter(|e| (start_date..=end_date).contains(&e.start_utc.date())));
        report(index + 1, total, format!("Loaded {filename}"))?;
    }
    result
        .events
        .sort_by(|a, b| (a.start_utc, a.end_utc, &a.burst_type).cmp(&(b.start_utc, b.end_utc, &b.burst_type)));
    check()?;
    Ok(result)
}

// ------------------------------------------------------------- FITS files

/// `_STATION_ALIASES`: documented spellings only, so a site prefix never
/// selects unrelated stations.
fn station_aliases(key: &str) -> &'static [&'static str] {
    match key {
        "mexico-lance" => &["mexico-lance-a", "mexico-lance-b"],
        "uk-glasgow" => &["glasgow"],
        "glasgow" => &["uk-glasgow"],
        "glsagow" => &["glasgow", "uk-glasgow"],
        "alaska-haar" => &["alaska-haarp"],
        "malaysia_banting" => &["malaysia-banting"],
        "malaysia-banting" => &["malaysia_banting"],
        _ => &[],
    }
}

fn burst_station_key(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()
}

/// `station_matches_burst`: the station itself or a documented alias.
pub fn station_matches_burst(archive_station: &str, selected_station: &str) -> bool {
    let archive = burst_station_key(archive_station);
    let selected = burst_station_key(selected_station);
    !selected.is_empty() && (archive == selected || station_aliases(&selected).contains(&archive.as_str()))
}

/// `_catalog_window`: catalog times have minute precision, so the final
/// minute is included (also for a burst whose start and end are equal).
fn catalog_window(start: NaiveDateTime, stop: NaiveDateTime) -> Result<(NaiveDateTime, NaiveDateTime), String> {
    if stop < start {
        return Err("The burst end time must be at or after its start time.".into());
    }
    Ok((start, stop + Duration::minutes(1)))
}

/// `burst_archive_dates`: the UTC directories to read, including the day
/// before (a file with an explicit stop can cross midnight).
pub fn burst_archive_dates(start: NaiveDateTime, stop: NaiveDateTime) -> Result<Vec<NaiveDate>, String> {
    let (start, stop) = catalog_window(start, stop)?;
    let mut day = start.date().pred_opt().unwrap();
    let last = (stop - Duration::microseconds(1)).date();
    let mut out = Vec::new();
    while day <= last {
        out.push(day);
        day = day.succ_opt().unwrap();
    }
    Ok(out)
}

/// `_observation_stop`: an explicit stop in the name, else 900 s after the start.
fn observation_stop(filename: &str, start: NaiveDateTime) -> Option<NaiveDateTime> {
    let stem = strip_fits_suffix(filename);
    let explicit = (stem.len() != filename.len())
        .then(|| {
            let parts: Vec<&str> = stem.split('_').collect();
            let n = parts.len();
            let ok = |s: &str, len| s.len() == len && digits(s) == len;
            (n >= 5 && ok(parts[n - 4], 8) && ok(parts[n - 3], 6) && ok(parts[n - 2], 6) && !parts[n - 1].is_empty())
                .then_some(parts[n - 2])
        })
        .flatten();
    let Some(stop) = explicit else { return Some(start + Duration::seconds(NOMINAL_FITS_SECONDS)) };
    let time = NaiveTime::parse_from_str(stop, "%H%M%S").ok()?;
    let mut end = start.date().and_time(time);
    if end < start {
        end += Duration::days(1);
    }
    Some(end)
}

/// `filter_burst_candidates`: files whose observation overlaps the
/// catalog minutes, from the selected stations.
pub fn filter_burst_candidates(
    hrefs: &[String],
    day_url: &str,
    selected_stations: &[String],
    start: NaiveDateTime,
    stop: NaiveDateTime,
) -> Result<Vec<Candidate>, String> {
    let (start, stop) = catalog_window(start, stop)?;
    let mut out = Vec::new();
    for href in hrefs {
        let path = href.split(['?', '#']).next().unwrap_or("");
        let filename = percent_decode(basename(path));
        let Ok((station, observed, receiver_id)) = parse_archive_filename(&filename) else { continue };
        let Some(observed_stop) = observation_stop(&filename, observed) else { continue };
        if observed >= stop || observed_stop <= start {
            continue;
        }
        if !selected_stations.iter().any(|s| station_matches_burst(&station, s)) {
            continue;
        }
        out.push(Candidate {
            station,
            observed_at: Some(observed),
            filename,
            url: join_url(day_url, href),
            receiver_id,
        });
    }
    Ok(sort_event_candidates(out))
}

/// What a burst's FITS search found (`BurstFitsWorker`).
#[derive(Clone, Debug, Default)]
pub struct BurstFits {
    pub candidates: Vec<Candidate>,
    pub warnings: Vec<String>,
}

/// `BurstFitsWorker.run`: read the archive days around the (padded) burst.
pub fn find_burst_fits(
    event: &BurstEvent,
    stations: &[String],
    padding_minutes: i64,
    mut progress: impl FnMut(usize, usize, String),
    cancelled: impl Fn() -> bool,
) -> Result<BurstFits, BurstListError> {
    let mut stations_unique: Vec<String> = Vec::new();
    for s in stations.iter().map(|s| s.trim()).filter(|s| !s.is_empty()) {
        if !stations_unique.iter().any(|u| u == s) {
            stations_unique.push(s.to_owned());
        }
    }
    let check = || if cancelled() { Err(BurstListError::Cancelled) } else { Ok(()) };
    check()?;
    if stations_unique.is_empty() {
        return Err(BurstListError::Failed("Select at least one station to find FITS files.".into()));
    }
    if padding_minutes < 0 {
        return Err(BurstListError::Failed("Burst padding must not be negative.".into()));
    }
    catalog_window(event.start_utc, event.end_utc).map_err(BurstListError::Failed)?;
    let padding = Duration::minutes(padding_minutes);
    let (start, stop) = (event.start_utc - padding, event.end_utc + padding);
    let dates = burst_archive_dates(start, stop).map_err(BurstListError::Failed)?;
    let mut candidates = Vec::new();
    let mut warnings = Vec::new();
    let mut successful = 0;
    let agent = agent(REQUEST_TIMEOUT_S);
    for (index, date) in dates.iter().enumerate() {
        check()?;
        let url = day_url(*date);
        progress(index, dates.len(), format!("Reading FITS archive for {}...", date.format("%Y-%m-%d")));
        check()?;
        let listing = (|| -> anyhow::Result<Vec<Candidate>> {
            let (status, mut resp) = get_with_retries(&agent, &url)?;
            if status >= 400 {
                anyhow::bail!("HTTP {status} for {url}");
            }
            let html = resp.body_mut().with_config().limit(64 * 1024 * 1024).read_to_string()?;
            filter_burst_candidates(&extract_fits_links(&html), &url, &stations_unique, start, stop)
                .map_err(anyhow::Error::msg)
        })();
        check()?;
        match listing {
            Ok(found) => {
                candidates.extend(found);
                successful += 1;
            }
            Err(e) => warnings.push(format!("{}: {e}", date.format("%Y-%m-%d"))),
        }
        progress(index + 1, dates.len(), format!("Checked {} of {} archive days.", index + 1, dates.len()));
    }
    check()?;
    let candidates = sort_event_candidates(candidates);
    if successful == 0 {
        return Err(BurstListError::Failed("Unable to read the FITS archive for the selected burst.".into()));
    }
    let missing: Vec<&str> = stations_unique
        .iter()
        .filter(|s| !candidates.iter().any(|c| station_matches_burst(&c.station, s)))
        .map(String::as_str)
        .collect();
    if !missing.is_empty() {
        warnings.push(format!("No overlapping archive files found for: {}.", missing.join(", ")));
    }
    Ok(BurstFits { candidates, warnings })
}

#[cfg(test)]
mod tests {
    use super::*;

    const MODERN: &str = "#Date\tTime\tType\tStations\n\
        20230105\t10:15-10:17\tIII\tBIR, (MRO), ALMATY?\tweak\n\
        20230105\t11:00-11:05\tREM\tno data\n\
        20230105 23:59-00:02 II  GLASGOW # drifting\n\
        20230106\t25:00-25:10\tIII\tBIR\n\
        20230107   ---\n";

    fn dt(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S").unwrap()
    }

    #[test]
    fn parses_modern_tables() {
        let p = parse_content(MODERN, "https://x/2023/e-CALLISTO_2023_01.txt");
        assert!(p.recognized);
        assert_eq!(p.events.len(), 2);
        let e = &p.events[0];
        assert_eq!(e.burst_type, "III");
        assert_eq!(e.stations, ["BIR", "MRO", "ALMATY"]);
        assert_eq!(e.remarks, "Station qualification: (MRO); Station qualification: ALMATY?; weak");
        let e = &p.events[1];
        assert_eq!(e.end_utc, dt("2023-01-06 00:02:00"));
        assert_eq!(e.stations, ["GLASGOW"]);
        assert_eq!(e.remarks, "drifting");
        assert_eq!(p.invalid, [5]);
        assert_eq!(p.notices.len(), 1);
    }

    #[test]
    fn parses_legacy_and_untyped_tables() {
        let text = "110607 0700 1600 BLEN 0815 0815.5 IIIG 2 45 870X\n110608 BLEN\n";
        let p = parse_content(text, "https://x/2011/SGD_BLEN_2011_06.txt");
        assert_eq!(p.events.len(), 1);
        let e = &p.events[0];
        assert_eq!(e.start_utc, dt("2011-06-07 08:15:00"));
        assert_eq!(e.end_utc, dt("2011-06-07 08:15:30"));
        assert_eq!((e.frequency_min_mhz, e.frequency_max_mhz), (Some(45.0), Some(870.0)));
        assert!(e.remarks.contains("SGD subtype/intensity: 2"));
        let p = parse_content("20141102 10:00-10:01 BIR, MRO\n", "https://x/2014/20141101-20141130_nhit2.txt");
        assert_eq!(p.events[0].burst_type, "---");
        assert_eq!(p.events[0].stations, ["BIR", "MRO"]);
    }

    #[test]
    fn filters_type_families() {
        let p = parse_content(MODERN, "u");
        let ev = |t: &str| filter_burst_events(&p.events, t, "", "", None, None).unwrap().len();
        assert_eq!(ev("III"), 1);
        assert_eq!(ev("II"), 1);
        assert_eq!(ev("IV"), 0);
        assert!(matches_type("IIIG+V", "III") && matches_type("II/IV", "II/IV") && !matches_type("III", "II"));
        assert_eq!(filter_burst_events(&p.events, "", "glas", "", None, None).unwrap().len(), 1);
        assert_eq!(filter_burst_events(&p.events, "", "", "drift", None, None).unwrap().len(), 1);
    }

    #[test]
    fn helpers() {
        assert_eq!(splitlines("a\r\nb\rc\n"), ["a", "b", "c"]);
        assert_eq!(monthly_name("e-CALLISTO_2023_01.txt"), Some((2023, 1)));
        assert_eq!(range_name("20141101-20141130_nhit2.txt"), Some(("20141101", "20141130", "2")));
        let base = "https://h/BurstLists/2010-yyyy_Monstein/";
        let links = listing_links(
            r#"<a href="?C=N;O=D">Name</a> <a href="/BurstLists/">Parent</a> <a href="2023/">2023/</a> <A HREF='2024/'>"#,
            base,
        );
        assert_eq!(links, [format!("{base}2023/"), format!("{base}2024/")]);
        let (first, last, t) = source_period(&format!("{base}2024/e-CALLISTO_2024_02.txt")).unwrap();
        assert_eq!((first.day(), last.day(), t), (1, 29, None));
        assert!(station_matches_burst("MEXICO-LANCE-A", "Mexico-LANCE"));
        assert!(!station_matches_burst("MEXICO-LANCE-B", "MEXICO-LANCE-A"));
        // The day before, and the burst's day (its last minute ends at midnight).
        let dates = burst_archive_dates(dt("2023-01-05 23:58:00"), dt("2023-01-05 23:59:00")).unwrap();
        assert_eq!(dates.len(), 2);
        let start = dt("2023-01-05 23:50:00");
        assert_eq!(observation_stop("A_20230105_235000_001500_01.fit.gz", start), Some(dt("2023-01-06 00:15:00")));
        assert_eq!(observation_stop("A_20230105_235000_01.fit.gz", start), Some(dt("2023-01-06 00:05:00")));
        let hrefs = vec!["BIR_20230105_101000_01.fit.gz".to_string(), "BIR_20230105_103000_01.fit.gz".to_string()];
        let found = filter_burst_candidates(&hrefs, "https://a/", &["BIR".into()], dt("2023-01-05 10:15:00"), dt("2023-01-05 10:17:00")).unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].filename, "BIR_20230105_101000_01.fit.gz");
    }
}
