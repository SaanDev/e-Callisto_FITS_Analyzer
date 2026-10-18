//! Access to the public e-CALLISTO data archive, which serves one Apache
//! directory listing per day: `<base>/YYYY/MM/DD/STATION_YYYYMMDD_HHMMSS_FC.fit.gz`.

use std::path::{Path, PathBuf};
use std::sync::mpsc::Sender;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use chrono::{NaiveDate, NaiveDateTime};

use crate::callisto::parse_filename;

pub const DEFAULT_BASE_URL: &str = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/";

#[derive(Clone, Debug)]
pub struct RemoteFile {
    pub name: String,
    pub url: String,
    pub station: String,
    pub start: NaiveDateTime,
    pub focus_code: String,
    pub size: String,
}

pub enum Msg {
    Listing(Result<Vec<RemoteFile>, String>),
    Progress { done: usize, total: usize, current: String },
    Downloaded { paths: Vec<PathBuf>, errors: Vec<String> },
}

fn agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(120)))
        .user_agent(concat!("callisto_viewer/", env!("CARGO_PKG_VERSION")))
        .build()
        .into()
}

pub fn day_url(base: &str, date: NaiveDate) -> String {
    format!("{}/{}/", base.trim_end_matches('/'), date.format("%Y/%m/%d"))
}

pub fn list_day(base: &str, date: NaiveDate) -> Result<Vec<RemoteFile>> {
    let url = day_url(base, date);
    let mut resp = agent().get(&url).call().map_err(|e| match e {
        ureq::Error::StatusCode(404) => anyhow::anyhow!("no data in the archive for {date} (404)"),
        e => anyhow::anyhow!("{url}: {e}"),
    })?;
    let html =
        resp.body_mut().with_config().limit(64 * 1024 * 1024).read_to_string().context("reading directory listing")?;
    Ok(parse_listing(&html, &url))
}

/// Extracts FITS links and their sizes from an Apache index page.
pub fn parse_listing(html: &str, dir_url: &str) -> Vec<RemoteFile> {
    let mut files = Vec::new();
    for chunk in html.split("href=\"").skip(1) {
        let Some((name, rest)) = chunk.split_once('"') else { continue };
        if name.contains('/') || name.starts_with('?') {
            continue;
        }
        let Some(parsed) = parse_filename(name) else { continue };
        // Apache puts the size in the last right-aligned cell of the row:
        // ...<td align="right">189K</td>. Other servers may not list sizes.
        let row = rest.split(['\n']).next().unwrap_or("").split("</tr>").next().unwrap_or("");
        let size = row
            .rsplit_once("<td align=\"right\">")
            .and_then(|(_, s)| s.split('<').next())
            .map(|s| s.trim().to_string())
            .unwrap_or_default();
        files.push(RemoteFile {
            name: name.to_string(),
            url: format!("{dir_url}{name}"),
            station: parsed.station,
            start: parsed.start,
            focus_code: parsed.focus_code.unwrap_or_default(),
            size,
        });
    }
    files.sort_by(|a, b| (&a.station, &a.focus_code, a.start).cmp(&(&b.station, &b.focus_code, b.start)));
    files.dedup_by(|a, b| a.name == b.name);
    files
}

/// Downloads `files` into `dir`, skipping files that already exist. `wake` is
/// called after each message, so a GUI can repaint.
pub fn download_all(files: Vec<RemoteFile>, dir: PathBuf, tx: Sender<Msg>, wake: impl Fn()) {
    let agent = agent();
    let total = files.len();
    let mut paths = Vec::new();
    let mut errors = Vec::new();
    for (i, f) in files.into_iter().enumerate() {
        let _ = tx.send(Msg::Progress { done: i, total, current: f.name.clone() });
        wake();
        let day_dir = dir.join(f.start.format("%Y/%m/%d").to_string());
        let dest = day_dir.join(&f.name);
        match download_one(&agent, &f.url, &day_dir, &dest) {
            Ok(()) => paths.push(dest),
            Err(e) => errors.push(format!("{}: {e:#}", f.name)),
        }
    }
    let _ = tx.send(Msg::Downloaded { paths, errors });
    wake();
}

fn download_one(agent: &ureq::Agent, url: &str, dir: &Path, dest: &Path) -> Result<()> {
    if dest.exists() {
        return Ok(());
    }
    std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    let resp = agent.get(url).call()?;
    if resp.status() != 200 {
        bail!("HTTP {}", resp.status());
    }
    // Write to a temporary name first so an interrupted download never looks complete.
    let tmp = dest.with_extension("part");
    let mut out = std::fs::File::create(&tmp)?;
    std::io::copy(&mut resp.into_body().into_reader(), &mut out)?;
    drop(out);
    std::fs::rename(&tmp, dest)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_apache_listing() {
        let html = r#"<tr><td><a href="/solarradio/data/2002-20yy_Callisto/2025/05/">Parent Directory</a></td></tr>
<tr><td valign="top"><img src="/icons/compressed.gif" alt="[   ]"></td><td><a href="ALASKA-ANCHORAGE_20250514_001500_01.fit.gz">ALASKA-ANCHORAGE_20250514_001500_01.fit.gz</a></td><td align="right">2025-05-14 02:35  </td><td align="right">187K</td><td>&nbsp;</td></tr>
<tr><td valign="top"><img src="/icons/compressed.gif" alt="[   ]"></td><td><a href="ALASKA-ANCHORAGE_20250514_000000_01.fit.gz">ALASKA-ANCHORAGE_20250514_000000_01.fit.gz</a></td><td align="right">2025-05-14 02:20  </td><td align="right">189K</td><td>&nbsp;</td></tr>
<tr><td><a href="?C=N;O=D">Name</a></td></tr>"#;
        let files = parse_listing(html, "https://x/2025/05/14/");
        assert_eq!(files.len(), 2);
        assert_eq!(files[0].name, "ALASKA-ANCHORAGE_20250514_000000_01.fit.gz");
        assert_eq!(files[0].size, "189K");
        assert_eq!(files[0].focus_code, "01");
        assert_eq!(files[1].url, "https://x/2025/05/14/ALASKA-ANCHORAGE_20250514_001500_01.fit.gz");
    }
}
