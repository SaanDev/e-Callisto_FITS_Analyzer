//! Port of `src/backend/radio/callisto_archive.py`: the e-CALLISTO archive at
//! soleil.i4ds.ch, its day listings and the HTTP policy (timeouts, retries).

use std::sync::OnceLock;
use std::time::Duration;

use anyhow::{Result, anyhow, bail};
use chrono::{Datelike, NaiveDate};

use super::naming::{has_fits_suffix, parse_archive_filename};

pub const BASE_URL: &str = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/";
pub const REQUEST_TIMEOUT_S: u64 = 15;
pub const DOWNLOAD_TIMEOUT_S: u64 = 30;
const RETRY_STATUS: [u16; 5] = [429, 500, 502, 503, 504];
const RETRIES: u32 = 3;
const BACKOFF_S: f64 = 0.5;

static USER_AGENT: OnceLock<String> = OnceLock::new();

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

/// The directory URL holding one UTC day.
pub fn day_url(date: NaiveDate) -> String {
    format!("{BASE_URL}{}/{:02}/{:02}/", date.year(), date.month(), date.day())
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

/// The FITS links of one day's listing; a missing day (404) is an empty day.
pub fn fetch_day_links(date: NaiveDate) -> Result<Option<Vec<String>>> {
    let url = day_url(date);
    let (status, mut resp) = get_with_retries(&agent(REQUEST_TIMEOUT_S), &url)?;
    if status == 404 {
        return Ok(None);
    }
    if status >= 400 {
        bail!("HTTP {status} for {url}");
    }
    let html = resp.body_mut().with_config().limit(64 * 1024 * 1024).read_to_string()?;
    Ok(Some(extract_fits_links(&html)))
}

#[cfg(test)]
mod tests {
    use super::*;

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
