//! Port of `src/backend/radio/callisto_archive.py`: the servers publishing
//! e-CALLISTO FITS files (the e-CALLISTO archive at soleil.i4ds.ch and
//! Astrodoncel), their day listings and the HTTP policy (timeouts, retries).

use std::sync::OnceLock;
use std::time::Duration;

use anyhow::{Result, anyhow};
use chrono::{Datelike, NaiveDate, NaiveDateTime};

use super::naming::{has_fits_suffix, parse_archive_filename};

pub const BASE_URL: &str = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/";
pub const ASTRODONCEL_FILES_URL: &str = "https://astrodoncel.uah.es/ecallistodata/";
pub const ASTRODONCEL_LISTING_URL: &str = "https://astrodoncel.uah.es/dashboard/datos/getitems.php";

/// A server publishing FITS files under `<files_url>YYYY/MM/DD/` (`ARCHIVE_SOURCES`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub enum Source {
    #[default]
    Ecallisto,
    Astrodoncel,
}

impl Source {
    pub const ALL: [Source; 2] = [Source::Ecallisto, Source::Astrodoncel];

    /// The stable id kept in settings (`callisto/archive_source`).
    pub fn id(self) -> &'static str {
        match self {
            Source::Ecallisto => "ecallisto",
            Source::Astrodoncel => "astrodoncel",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Source::Ecallisto => "e-CALLISTO",
            Source::Astrodoncel => "Astrodoncel",
        }
    }

    pub fn files_url(self) -> &'static str {
        match self {
            Source::Ecallisto => BASE_URL,
            Source::Astrodoncel => ASTRODONCEL_FILES_URL,
        }
    }

    /// The JSON endpoint listing a day. Without one the day directories are
    /// Apache indexes; with one those indexes may be refused (Astrodoncel: 403).
    pub fn listing_url(self) -> Option<&'static str> {
        match self {
            Source::Ecallisto => None,
            Source::Astrodoncel => Some(ASTRODONCEL_LISTING_URL),
        }
    }

    /// `normalize_source_id`: a stored id when still valid, else the default.
    pub fn from_id(id: &str) -> Source {
        Source::ALL.into_iter().find(|s| s.id() == id).unwrap_or_default()
    }

    /// `source_id_for_url`: the server a file URL belongs to; any other URL
    /// keeps the default server.
    pub fn for_url(url: &str) -> Source {
        Source::ALL.into_iter().find(|s| *s != Source::Ecallisto && url.starts_with(s.files_url())).unwrap_or_default()
    }

    /// The URL prefix of one UTC day's files.
    pub fn day_url(self, date: NaiveDate) -> String {
        format!("{}{}/{:02}/{:02}/", self.files_url(), date.year(), date.month(), date.day())
    }
}

/// `ArchiveFile`: one FITS file a server lists for a UTC day. `station`,
/// `observed_at` and `focus_code` are empty for a name the naming rules
/// cannot read (see `list_day`'s `include_unparsed`).
#[derive(Clone, Debug, PartialEq)]
pub struct ArchiveFile {
    pub source: Source,
    pub filename: String,
    pub station: String,
    pub observed_at: Option<NaiveDateTime>,
    pub focus_code: String,
    pub download_url: String,
    pub size_bytes: Option<u64>,
}

impl ArchiveFile {
    fn new(source: Source, filename: String, download_url: String, size_bytes: Option<u64>) -> Self {
        let (station, observed_at, focus_code) = match parse_archive_filename(&filename) {
            Ok((s, t, f)) => (s, Some(t), f),
            Err(_) => (String::new(), None, String::new()),
        };
        ArchiveFile { source, filename, station, observed_at, focus_code, download_url, size_bytes }
    }
}
pub const REQUEST_TIMEOUT_S: u64 = 15;
pub const DOWNLOAD_TIMEOUT_S: u64 = 30;
const RETRY_STATUS: [u16; 5] = [429, 500, 502, 503, 504];
const RETRIES: u32 = 3;
const BACKOFF_S: f64 = 0.5;

static USER_AGENT: OnceLock<String> = OnceLock::new();

/// Answers HTTP GETs instead of the network: `Some((status, body))`, or
/// `None` for a connection failure. For tests and reference captures.
pub type MockHttp = dyn Fn(&str) -> Option<(u16, Vec<u8>)> + Send + Sync;

static MOCK: std::sync::RwLock<Option<std::sync::Arc<MockHttp>>> = std::sync::RwLock::new(None);

/// Routes every archive, catalog and Learmonth request through `mock`
/// (or back to the network with `None`).
pub fn set_mock_http(mock: Option<std::sync::Arc<MockHttp>>) {
    if let Ok(mut m) = MOCK.write() {
        *m = mock;
    }
}

/// The mocked answer for `url`, when a mock is installed.
pub fn mock_get(url: &str) -> Option<Result<(u16, ureq::http::Response<ureq::Body>)>> {
    let mock = MOCK.read().ok()?.clone()?;
    Some(match mock(url) {
        Some((status, body)) => {
            let resp = ureq::http::Response::builder()
                .status(status)
                .body(ureq::Body::builder().data(body))
                .map_err(|e| anyhow!(e.to_string()));
            resp.map(|r| (status, r))
        }
        None => Err(anyhow!("Connection refused (mock): {url}")),
    })
}

/// Sets the `User-Agent` sent to the archive (`"<app name>/<version>"`).
pub fn set_user_agent(agent: &str) {
    let _ = USER_AGENT.set(agent.to_string());
}

fn user_agent() -> &'static str {
    USER_AGENT.get().map(String::as_str).unwrap_or(concat!("e-CALLISTO FITS Analyzer Lite/", env!("CARGO_PKG_VERSION")))
}

pub fn agent(timeout_s: u64) -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(timeout_s)))
        .http_status_as_error(false)
        .user_agent(user_agent())
        .build()
        .into()
}

/// GET with the full app's retry policy (3 retries on 429/5xx and connection
/// errors, exponential back-off from 0.5 s). Returns the final status and body.
pub fn get_with_retries(agent: &ureq::Agent, url: &str) -> Result<(u16, ureq::http::Response<ureq::Body>)> {
    if let Some(mocked) = mock_get(url) {
        return mocked;
    }
    let mut attempt = 0;
    loop {
        match agent.get(url).call() {
            Ok(resp) => {
                let status = resp.status().as_u16();
                if RETRY_STATUS.contains(&status) && attempt < RETRIES {
                    std::thread::sleep(Duration::from_secs_f64(BACKOFF_S * 2f64.powi(attempt as i32)));
                    attempt += 1;
                    continue;
                }
                return Ok((status, resp));
            }
            Err(e) if attempt < RETRIES => {
                let _ = e;
                std::thread::sleep(Duration::from_secs_f64(BACKOFF_S * 2f64.powi(attempt as i32)));
                attempt += 1;
            }
            Err(e) => return Err(anyhow!(e)),
        }
    }
}

/// The e-CALLISTO directory URL holding one UTC day.
pub fn day_url(date: NaiveDate) -> String {
    Source::Ecallisto.day_url(date)
}

/// `urllib.parse.quote(s, safe="")`: everything but unreserved characters encoded.
pub fn percent_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn html_unescape(s: &str) -> String {
    s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#39;", "'")
}

/// FITS links in an Apache directory listing, in order, without duplicates.
/// Matches the full app's `<a ... href=...>` regex, case-insensitively.
pub fn extract_fits_links(html: &str) -> Vec<String> {
    let lower = html.to_ascii_lowercase();
    let bytes = lower.as_bytes();
    let mut links: Vec<String> = Vec::new();
    let mut pos = 0;
    while let Some(found) = lower[pos..].find("<a") {
        let start = pos + found;
        pos = start + 2;
        // `<a\b`: the next character must not continue a word.
        if bytes.get(pos).is_some_and(|b| b.is_ascii_alphanumeric() || *b == b'_') {
            continue;
        }
        let tag_end = lower[pos..].find('>').map_or(lower.len(), |e| pos + e);
        let tag = &lower[pos..tag_end];
        let mut search = 0;
        while let Some(h) = tag[search..].find("href") {
            let at = search + h;
            search = at + 4;
            let word_start =
                at == 0 || !tag.as_bytes()[at - 1].is_ascii_alphanumeric() && tag.as_bytes()[at - 1] != b'_';
            if !word_start {
                continue;
            }
            let rest = tag[at + 4..].trim_start();
            let Some(rest) = rest.strip_prefix('=') else { continue };
            let rest = rest.trim_start();
            let offset = tag_end - tag.len() + (tag.len() - rest.len());
            let original = &html[offset..tag_end];
            let (quote, body) = match original.chars().next() {
                Some(q @ ('"' | '\'')) => (Some(q), &original[1..]),
                _ => (None, original),
            };
            let end = body.find(['"', '\'', ' ', '>']).unwrap_or(body.len());
            let value = &body[..end];
            if value.is_empty() || (quote.is_some() && !body[end..].starts_with(quote.unwrap())) {
                continue;
            }
            let href = html_unescape(value);
            let href = href.split('#').next().unwrap_or("").split('?').next().unwrap_or("").trim().to_string();
            if has_fits_suffix(&href) && !links.contains(&href) {
                links.push(href);
            }
            break;
        }
        pos = tag_end.max(pos);
    }
    links
}

/// Distinct stations named by archive FITS links, sorted case-insensitively;
/// spellings that differ only in case are one station (first one wins).
pub fn stations_in_listing(hrefs: &[String]) -> Vec<String> {
    let mut seen: Vec<(String, String)> = Vec::new();
    for href in hrefs {
        if let Ok((station, _, _)) = parse_archive_filename(href) {
            let key = station.to_lowercase();
            if !seen.iter().any(|(k, _)| *k == key) {
                seen.push((key, station));
            }
        }
    }
    seen.sort_by(|a, b| a.0.cmp(&b.0));
    seen.into_iter().map(|(_, s)| s).collect()
}

/// `urljoin(base, href)` for the hrefs a directory listing holds.
pub fn join_url(base: &str, href: &str) -> String {
    if href.starts_with("http://") || href.starts_with("https://") {
        href.to_string()
    } else if let Some(path) = href.strip_prefix('/') {
        let origin_end =
            base.find("://").map(|i| i + 3).and_then(|i| base[i..].find('/').map(|j| i + j)).unwrap_or(base.len());
        format!("{}/{path}", &base[..origin_end])
    } else {
        let dir = &base[..base.rfind('/').map_or(base.len(), |i| i + 1)];
        format!("{dir}{href}")
    }
}

/// Why a listing could not be read: the server was not reached
/// (`requests.RequestException`), or it answered with an error or with
/// something other than a listing (`ArchiveListingError`).
#[derive(Clone, Debug, PartialEq)]
pub enum ListingError {
    Unreachable(String),
    Http(String),
}

impl std::fmt::Display for ListingError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ListingError::Unreachable(m) | ListingError::Http(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for ListingError {}

/// The status and body of a listing request.
fn get_listing(url: &str) -> Result<(u16, String), ListingError> {
    let (status, mut resp) =
        get_with_retries(&agent(REQUEST_TIMEOUT_S), url).map_err(|e| ListingError::Unreachable(e.to_string()))?;
    if status >= 400 {
        return Ok((status, String::new()));
    }
    let body = resp
        .body_mut()
        .with_config()
        .limit(64 * 1024 * 1024)
        .read_to_string()
        .map_err(|e| ListingError::Unreachable(e.to_string()))?;
    Ok((status, body))
}

/// The body of a directory listing; any HTTP error is `HTTP <code> for <url>`.
pub fn fetch_listing(url: &str) -> Result<String, ListingError> {
    let (status, body) = get_listing(url)?;
    if status >= 400 {
        return Err(ListingError::Http(format!("HTTP {status} for {url}")));
    }
    Ok(body)
}

/// `check_archive_server`: a HEAD of the archive root (8 s), then a GET. A
/// server that refuses its directory indexes is not probed; the day listing
/// that follows reports any failure itself.
pub fn check_archive_server(source: Source) -> (bool, String) {
    if source.listing_url().is_some() {
        return (true, String::new());
    }
    let root = source.files_url();
    if let Some(mocked) = mock_get(root) {
        return match mocked {
            Ok((status, _)) if status >= 400 => (false, format!("HTTP {status}")),
            Ok(_) => (true, String::new()),
            Err(e) => (false, e.to_string()),
        };
    }
    if let Ok(resp) = agent(8).head(root).call()
        && resp.status().as_u16() < 400
    {
        return (true, String::new());
    }
    match get_with_retries(&agent(REQUEST_TIMEOUT_S), root) {
        Ok((status, _)) if status >= 400 => (false, format!("HTTP {status}")),
        Ok(_) => (true, String::new()),
        Err(e) => (false, e.to_string()),
    }
}

/// The request that lists one day on a server with a JSON listing
/// (`params={"path": "YYYY/MM/DD"}`, encoded as `requests` does).
pub fn listing_request_url(source: Source, date: NaiveDate) -> String {
    match source.listing_url() {
        Some(endpoint) => format!("{endpoint}?path={}", percent_encode(&date.format("%Y/%m/%d").to_string())),
        None => source.day_url(date),
    }
}

/// `_is_safe_basename`: no separators, drive colons or other path syntax.
fn is_safe_basename(name: &str) -> bool {
    let mut chars = name.chars();
    let first_ok = chars.next().is_some_and(char::is_alphanumeric);
    first_ok && chars.all(|c| c.is_alphanumeric() || matches!(c, '_' | ' ' | '.' | '+' | '-')) && !name.contains("..")
}

/// `_list_directory_day`: an Apache day directory. The archive has no
/// directory for a day nobody has published yet (including future days),
/// which is an empty day rather than an error.
fn list_directory_day(source: Source, date: NaiveDate) -> Result<Vec<ArchiveFile>, ListingError> {
    let url = source.day_url(date);
    let (status, html) = get_listing(&url)?;
    if status == 404 {
        return Ok(Vec::new());
    }
    if status >= 400 {
        return Err(ListingError::Http(format!("HTTP {status} for {url}")));
    }
    Ok(extract_fits_links(&html)
        .iter()
        .map(|href| ArchiveFile::new(source, super::naming::basename(href).to_owned(), join_url(&url, href), None))
        .collect())
}

/// `_list_json_day`: a JSON day listing, which answers `[]` for a day
/// without files (and for any path it does not know).
fn list_json_day(source: Source, date: NaiveDate) -> Result<Vec<ArchiveFile>, ListingError> {
    let where_ = format!("{} listing for {}", source.label(), date.format("%Y-%m-%d"));
    let (status, text) = get_listing(&listing_request_url(source, date))?;
    if status >= 400 {
        return Err(ListingError::Http(format!("HTTP {status} for the {where_}")));
    }
    // Served as text/html despite being JSON, so only the body can decide.
    let records: serde_json::Value = serde_json::from_str(text.trim_start_matches('\u{feff}'))
        .map_err(|_| ListingError::Http(format!("The {where_} is not valid JSON.")))?;
    let Some(records) = records.as_array() else {
        return Err(ListingError::Http(format!("The {where_} is not a list of files.")));
    };
    let prefix = source.day_url(date);
    let mut files = Vec::new();
    for record in records {
        let (Some(name), Some(directory)) =
            (record.get("name").and_then(|v| v.as_str()), record.get("isDirectory").and_then(|v| v.as_bool()))
        else {
            return Err(ListingError::Http(format!("The {where_} has an unexpected record.")));
        };
        if directory || !has_fits_suffix(name) || !is_safe_basename(name) {
            continue;
        }
        let size = record.get("size").and_then(|v| v.as_u64());
        let entry = ArchiveFile::new(source, name.to_owned(), format!("{prefix}{}", percent_encode(name)), size);
        if entry.observed_at.is_some_and(|t| t.date() == date) {
            files.push(entry);
        }
    }
    Ok(files)
}

/// `list_day`: the FITS files a server publishes for one UTC day.
///
/// An error answer or a body that is not a listing is an error, never an
/// empty day. A directory listing can name files the naming rules cannot
/// read; those are left out unless `include_unparsed` is set.
pub fn list_day(source: Source, date: NaiveDate, include_unparsed: bool) -> Result<Vec<ArchiveFile>, ListingError> {
    if source.listing_url().is_some() {
        return list_json_day(source, date);
    }
    let files = list_directory_day(source, date)?;
    Ok(if include_unparsed { files } else { files.into_iter().filter(|f| f.observed_at.is_some()).collect() })
}

/// `fetch_day_stations`: the stations that published files on one UTC day.
pub fn fetch_day_stations(source: Source, date: NaiveDate) -> Result<Vec<String>> {
    let files = list_day(source, date, false)?;
    Ok(stations_in_listing(&files.into_iter().map(|f| f.filename).collect::<Vec<_>>()))
}

/// Serializes the tests that install the process-wide HTTP mock, and
/// removes it again when the guard drops.
#[cfg(test)]
pub(crate) mod test_mock {
    use std::sync::{Arc, Mutex, MutexGuard};

    static LOCK: Mutex<()> = Mutex::new(());

    pub struct Mocked(#[allow(dead_code)] MutexGuard<'static, ()>);

    impl Drop for Mocked {
        fn drop(&mut self) {
            super::set_mock_http(None);
        }
    }

    pub fn install(f: impl Fn(&str) -> Option<(u16, Vec<u8>)> + Send + Sync + 'static) -> Mocked {
        let guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        super::set_mock_http(Some(Arc::new(f)));
        Mocked(guard)
    }

    /// No mock: the real servers, while no other test mocks them.
    pub fn network() -> Mocked {
        let guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        super::set_mock_http(None);
        Mocked(guard)
    }

    /// One Astrodoncel listing record.
    pub fn record(name: &str) -> String {
        format!(r#"{{"name":"{name}","isDirectory":false,"dateModified":"2026-10-01T00:00:00","size":1234}}"#)
    }

    /// A minimal FITS file (an empty primary HDU), gzip-compressed when asked.
    pub fn fits_bytes(gz: bool) -> Vec<u8> {
        let mut header = String::new();
        for card in [
            "SIMPLE  =                    T",
            "BITPIX  =                    8",
            "NAXIS   =                    0",
            "END",
        ] {
            header.push_str(&format!("{card:<80}"));
        }
        let mut raw = format!("{header:<2880}").into_bytes();
        raw.truncate(2880);
        if !gz {
            return raw;
        }
        use std::io::Write;
        let mut enc = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        enc.write_all(&raw).unwrap();
        enc.finish().unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::test_mock::{install, record};
    use super::*;

    fn day() -> NaiveDate {
        NaiveDate::from_ymd_opt(2026, 10, 1).unwrap()
    }

    const ANCHORAGE: &str = "ALASKA-ANCHORAGE_20261001_000000_01.fit.gz";

    fn listing_url() -> String {
        format!("{ASTRODONCEL_LISTING_URL}?path=2026%2F10%2F01")
    }

    /// Serves `body` as the Astrodoncel listing of 2026-10-01.
    fn astrodoncel(status: u16, body: &str) -> test_mock::Mocked {
        let (url, body) = (listing_url(), body.to_owned());
        install(move |u| Some(if u == url { (status, body.clone().into_bytes()) } else { (404, Vec::new()) }))
    }

    #[test]
    fn the_two_servers_and_their_stable_ids() {
        assert_eq!(
            Source::ALL.map(|s| (s.id(), s.label())),
            [("ecallisto", "e-CALLISTO"), ("astrodoncel", "Astrodoncel")]
        );
        assert_eq!(Source::from_id("astrodoncel"), Source::Astrodoncel);
        for bad in ["", "Astrodoncel", "some-old-server"] {
            assert_eq!(Source::from_id(bad), Source::Ecallisto);
        }
        assert_eq!(Source::Astrodoncel.day_url(day()), format!("{ASTRODONCEL_FILES_URL}2026/10/01/"));
        assert_eq!(day_url(day()), format!("{BASE_URL}2026/10/01/"));
        assert_eq!(Source::for_url(&format!("{ASTRODONCEL_FILES_URL}2026/10/01/{ANCHORAGE}")), Source::Astrodoncel);
        assert_eq!(Source::for_url(&format!("{BASE_URL}2026/10/01/{ANCHORAGE}")), Source::Ecallisto);
        assert_eq!(Source::for_url("https://astrodoncel.uah.es/other/x.fit.gz"), Source::Ecallisto);
        assert_eq!(listing_request_url(Source::Astrodoncel, day()), listing_url());
        assert_eq!(percent_encode("NEW STATION+1_x.fit.gz"), "NEW%20STATION%2B1_x.fit.gz");
    }

    #[test]
    fn astrodoncel_lists_a_day_through_its_json_endpoint() {
        let _m = astrodoncel(200, &format!("[{}]", record(ANCHORAGE)));
        let files = list_day(Source::Astrodoncel, day(), false).unwrap();
        assert_eq!(files.len(), 1);
        let f = &files[0];
        assert_eq!(f.source, Source::Astrodoncel);
        assert_eq!(f.station, "ALASKA-ANCHORAGE");
        assert_eq!(f.focus_code, "01");
        assert_eq!(f.size_bytes, Some(1234));
        assert_eq!(f.observed_at, Some(day().and_hms_opt(0, 0, 0).unwrap()));
        assert_eq!(f.download_url, format!("{ASTRODONCEL_FILES_URL}2026/10/01/{ANCHORAGE}"));
        assert_eq!(fetch_day_stations(Source::Astrodoncel, day()).unwrap(), ["ALASKA-ANCHORAGE"]);
    }

    #[test]
    fn astrodoncel_keeps_spellings_seconds_and_focus_codes_but_drops_the_rest() {
        let names = [
            "SRI-Lanka_20261001_000229_059.fit.gz",
            "readme.txt",
            "notes.fit.gz",
            "BIR_20260930_234500_01.fit.gz",
            "../BIR_20261001_000000_01.fit.gz",
            "C:BIR_20261001_000000_01.fit.gz",
            ".BIR_20261001_000000_01.fit.gz",
            "NEW STATION+1_20261001_001500_01.fit.gz",
        ];
        let mut records: Vec<String> = names.iter().map(|n| record(n)).collect();
        records.push(r#"{"name":"BIR_20261001_000000_01.fit.gz","isDirectory":true}"#.into());
        let _m = astrodoncel(200, &format!("[{}]", records.join(",")));
        let files = list_day(Source::Astrodoncel, day(), false).unwrap();
        let got: Vec<(&str, &str)> = files.iter().map(|f| (f.station.as_str(), f.focus_code.as_str())).collect();
        assert_eq!(got, [("SRI-Lanka", "059"), ("NEW STATION+1", "01")]);
        assert_eq!(files[0].observed_at.unwrap().format("%H:%M:%S").to_string(), "00:02:29");
        assert!(files[1].download_url.ends_with("NEW%20STATION%2B1_20261001_001500_01.fit.gz"));
    }

    #[test]
    fn astrodoncel_empty_array_is_an_empty_day() {
        let _m = astrodoncel(200, "[]");
        assert!(list_day(Source::Astrodoncel, day(), false).unwrap().is_empty());
    }

    #[test]
    fn astrodoncel_unexpected_answers_are_errors_not_empty_days() {
        for body in
            ["<html>Service unavailable</html>", "", r#"{"error":"x"}"#, "null", "[1]", r#"[{"name":"x.fit.gz"}]"#]
        {
            let _m = astrodoncel(200, body);
            match list_day(Source::Astrodoncel, day(), false) {
                Err(ListingError::Http(m)) => {
                    assert!(m.contains("Astrodoncel listing for 2026-10-01"), "{body:?}: {m}")
                }
                other => panic!("{body:?}: {other:?}"),
            }
        }
        for status in [403, 404, 500] {
            let _m = astrodoncel(status, "");
            match list_day(Source::Astrodoncel, day(), false) {
                Err(ListingError::Http(m)) => assert!(m.starts_with(&format!("HTTP {status} for the Astrodoncel"))),
                other => panic!("{status}: {other:?}"),
            }
        }
        let _m = install(|_| None);
        assert!(matches!(list_day(Source::Astrodoncel, day(), false), Err(ListingError::Unreachable(_))));
    }

    #[test]
    fn ecallisto_missing_day_is_empty_and_unparsed_names_are_kept_on_request() {
        let html = format!("<a href=\"{ANCHORAGE}\">a</a><a href=\"notes_about_the_day.fit\">b</a>");
        let url = day_url(day());
        let _m = install(move |u| Some(if u == url { (200, html.clone().into_bytes()) } else { (404, Vec::new()) }));
        assert_eq!(list_day(Source::Ecallisto, day(), false).unwrap().len(), 1);
        let all = list_day(Source::Ecallisto, day(), true).unwrap();
        assert_eq!(all.len(), 2);
        assert!(all[1].observed_at.is_none());
        assert_eq!(all[0].download_url, format!("{BASE_URL}2026/10/01/{ANCHORAGE}"));
        let next = day().succ_opt().unwrap();
        assert!(list_day(Source::Ecallisto, next, false).unwrap().is_empty());
    }

    #[test]
    fn only_the_directory_server_gets_a_root_probe() {
        let _m = install(|_| panic!("no request expected"));
        assert_eq!(check_archive_server(Source::Astrodoncel), (true, String::new()));
    }

    #[test]
    #[ignore = "reads the real servers; run explicitly"]
    fn live_anchorage_file_on_both_servers() {
        let _m = test_mock::network();
        for source in Source::ALL {
            let files = list_day(source, day(), false).unwrap();
            assert!(files.iter().any(|f| f.filename == ANCHORAGE), "{source:?}");
        }
        let entry =
            list_day(Source::Astrodoncel, day(), false).unwrap().into_iter().find(|f| f.filename == ANCHORAGE).unwrap();
        let root = std::env::temp_dir().join(format!("lite_live_{}", std::process::id())).join("callisto_cache");
        let _ = std::fs::remove_dir_all(&root);
        let (path, cached) =
            super::super::cache::fetch_cached(&root, &entry.download_url, &entry.filename, |_, _| {}).unwrap();
        assert!(!cached);
        assert_eq!(Some(std::fs::metadata(&path).unwrap().len()), entry.size_bytes);
        assert_eq!(super::super::cache::source_for_path(&path.to_string_lossy()), Source::Astrodoncel);
        assert!(super::super::fits_io::load_callisto_fits(&path).is_ok());
        let future = NaiveDate::from_ymd_opt(2099, 1, 1).unwrap();
        assert!(list_day(Source::Astrodoncel, future, false).unwrap().is_empty());
        assert!(list_day(Source::Ecallisto, future, false).unwrap().is_empty());
        let _ = std::fs::remove_dir_all(root.parent().unwrap());
    }

    #[test]
    fn extracts_links_like_the_full_app() {
        let html = r#"<tr><td><a href="BIR_20261007_101459_01.fit.gz">BIR_20261007_101459_01.fit.gz</a></td></tr>
<tr><td><A HREF='MRO_20261007_000000_01.fit.gz'>x</A></td></tr>
<tr><td><a href="?C=N;O=D">Name</a><a href="BIR_20261007_101459_01.fit.gz#x">dup</a></td></tr>
<tr><td><a class="x" href=GLASGOW_20261007_000000_59.fit>y</a><a href="readme.txt">z</a></td></tr>"#;
        assert_eq!(
            extract_fits_links(html),
            vec!["BIR_20261007_101459_01.fit.gz", "MRO_20261007_000000_01.fit.gz", "GLASGOW_20261007_000000_59.fit"]
        );
        assert_eq!(day_url(NaiveDate::from_ymd_opt(2026, 10, 7).unwrap()), format!("{BASE_URL}2026/10/07/"));
        assert_eq!(join_url("https://a.b/x/y/", "f.fit"), "https://a.b/x/y/f.fit");
    }
}
