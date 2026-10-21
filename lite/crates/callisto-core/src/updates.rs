//! About → Check for Updates (`update_checker.py`, `UpdateDownloadWorker`):
//! the newest stable GitHub release for this platform, and its installer
//! downloaded. Lite is released from the full app's repository under
//! `lite-v…` tags with its own version numbers, so it looks at those
//! releases only (the full app skips them).

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result, bail};
use serde_json::Value;

pub const GITHUB_REPO: &str = "SaanDev/e-Callisto_FITS_Analyzer";

fn releases_url() -> String {
    format!("https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=50")
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Platform {
    Windows,
    MacOs,
    Linux,
    Unknown,
}

impl Platform {
    /// `_platform_family()` for the running system.
    pub fn current() -> Self {
        match std::env::consts::OS {
            "windows" => Platform::Windows,
            "macos" => Platform::MacOs,
            "linux" => Platform::Linux,
            _ => Platform::Unknown,
        }
    }

    /// `_asset_extension_priority`.
    fn extensions(self) -> &'static [&'static str] {
        match self {
            Platform::Windows => &[".exe", ".msi", ".zip"],
            Platform::MacOs => &[".dmg", ".pkg", ".zip"],
            Platform::Linux => &[".deb", ".appimage", ".tar.gz", ".zip"],
            Platform::Unknown => &[".exe", ".dmg", ".deb", ".appimage", ".pkg", ".zip", ".tar.gz"],
        }
    }
}

/// `UpdateCheckResult`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct UpdateCheck {
    pub update_available: bool,
    pub current_version: String,
    pub latest_version: String,
    pub release_name: String,
    pub release_url: String,
    pub download_url: String,
    pub published_at: String,
    pub notes: String,
}

/// `_extract_version_text`: the first dotted number, `v` prefixes dropped.
pub fn version_text(raw: &str) -> Option<String> {
    let text = raw.trim().trim_start_matches(['v', 'V']);
    let start = text.find(|c: char| c.is_ascii_digit())?;
    let rest = &text[start..];
    let mut end = 0;
    let bytes = rest.as_bytes();
    while end < bytes.len() {
        let digit = bytes[end].is_ascii_digit();
        let dot = bytes[end] == b'.' && bytes.get(end + 1).is_some_and(u8::is_ascii_digit);
        if !(digit || dot) {
            break;
        }
        end += 1;
    }
    Some(rest[..end].to_owned())
}

/// `normalize_version`.
pub fn normalize_version(v: &str) -> Vec<u64> {
    version_text(v).map(|t| t.split('.').filter_map(|p| p.parse().ok()).collect()).unwrap_or_default()
}

/// `_is_prerelease_version`: a pre-release label straight after the number.
fn is_prerelease(v: &str) -> bool {
    let text = v.trim().trim_start_matches(['v', 'V']);
    let Some(num) = version_text(text) else { return false };
    let Some(pos) = text.find(&num) else { return false };
    let after = text[pos + num.len()..].to_lowercase();
    let mut s = after.as_str();
    // `(?:[-_.\s]?(?:alpha|beta|preview|pre|rc|dev)|[-_.]?[ab])\d*(?![a-z])`
    let word = |s: &str| -> Option<usize> {
        ["alpha", "beta", "preview", "pre", "rc", "dev"].iter().find(|w| s.starts_with(**w)).map(|w| w.len())
    };
    // `\d*(?![a-z])` backtracks: only a letter straight after the label fails.
    let tail_ok = |s: &str| !s.starts_with(|c: char| c.is_ascii_lowercase());
    let sep = s.starts_with(['-', '_', '.', ' ', '\t']);
    let body = if sep { &s[1..] } else { s };
    if let Some(n) = word(body)
        && tail_ok(&body[n..])
    {
        return true;
    }
    if s.starts_with(['-', '_', '.']) {
        s = &s[1..];
    }
    if s.starts_with(['a', 'b']) && tail_ok(&s[1..]) {
        return true;
    }
    false
}

/// `is_newer_version`.
pub fn is_newer_version(current: &str, latest: &str) -> bool {
    let (mut c, mut l) = (normalize_version(current), normalize_version(latest));
    if c.is_empty() || l.is_empty() {
        return false;
    }
    let n = c.len().max(l.len());
    c.resize(n, 0);
    l.resize(n, 0);
    if l != c {
        return l > c;
    }
    // Same numbers: a pre-release is older than its final release.
    is_prerelease(current) && !is_prerelease(latest)
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").trim().to_owned()
}

/// `select_download_url`: the platform's preferred installer.
pub fn select_download_url(assets: &[Value], platform: Platform) -> Option<String> {
    let named: Vec<(String, String)> = assets
        .iter()
        .map(|a| (s(a, "name").to_lowercase(), s(a, "browser_download_url")))
        .filter(|(n, u)| !n.is_empty() && !u.is_empty())
        .collect();
    for ext in platform.extensions() {
        if let Some((_, u)) = named.iter().find(|(n, _)| n.ends_with(ext)) {
            return Some(u.clone());
        }
    }
    if platform != Platform::Unknown {
        return None;
    }
    named.first().map(|(_, u)| u.clone())
}

/// A Lite release: tagged `lite-…`, neither a draft nor a pre-release, with a version.
fn is_lite_stable(r: &Value) -> bool {
    if r.get("draft").and_then(Value::as_bool).unwrap_or(false)
        || r.get("prerelease").and_then(Value::as_bool).unwrap_or(false)
    {
        return false;
    }
    let tag = s(r, "tag_name").to_lowercase();
    tag.starts_with("lite-")
        && (version_text(tag.trim_start_matches("lite-")).is_some() || version_text(&s(r, "name")).is_some())
}

/// A release's version: from its tag (past `lite-`), else its name.
fn release_version(r: &Value) -> Option<String> {
    let tag = s(r, "tag_name");
    let tag = tag.get(5..).unwrap_or("");
    version_text(tag).or_else(|| version_text(&s(r, "name")))
}

/// `_explicit_platform_markers`.
fn platform_markers(text: &str) -> Vec<Platform> {
    let words: Vec<&str> = text.split(|c: char| !c.is_ascii_alphanumeric()).filter(|w| !w.is_empty()).collect();
    let has = |w: &str| words.contains(&w);
    let mut m = Vec::new();
    if has("windows") {
        m.push(Platform::Windows);
    }
    if has("macos") || has("darwin") || has("osx") || words.windows(2).any(|p| p == ["mac", "os"]) {
        m.push(Platform::MacOs);
    }
    if has("linux") || has("ubuntu") || has("debian") {
        m.push(Platform::Linux);
    }
    m
}

/// `_release_platform_score`.
fn platform_score(r: &Value, platform: Platform) -> (i32, i32) {
    let text = format!("{} {}", s(r, "tag_name").to_lowercase(), s(r, "name").to_lowercase());
    let markers = platform_markers(text.trim());
    let tag_score = if platform == Platform::Unknown {
        0
    } else if markers.contains(&platform) {
        2
    } else if !markers.is_empty() {
        -2
    } else {
        0
    };
    let assets = r.get("assets").and_then(Value::as_array).cloned().unwrap_or_default();
    (tag_score, select_download_url(&assets, platform).is_some() as i32)
}

/// `_pick_latest_stable_release_for_platform` over a release list.
pub fn pick_release(releases: &Value, platform: Platform) -> Result<Value> {
    let Some(list) = releases.as_array() else { bail!("Unexpected release list payload from GitHub API.") };
    let stable: Vec<&Value> = list.iter().filter(|r| is_lite_stable(r)).collect();
    if stable.is_empty() {
        bail!("No stable release found in GitHub release list.");
    }
    let mut scored: Vec<(Vec<u64>, i32, i32, String, &Value)> = stable
        .into_iter()
        .filter_map(|r| {
            let mut v = normalize_version(&release_version(r)?);
            if v.is_empty() {
                return None;
            }
            if v.len() < 5 {
                v.resize(5, 0);
            }
            let (t, a) = platform_score(r, platform);
            Some((v, t, a, s(r, "published_at"), r))
        })
        .collect();
    if scored.is_empty() {
        bail!("No parseable stable release version found.");
    }
    if platform != Platform::Unknown {
        scored.retain(|x| x.1 > 0 || x.2 > 0);
        if scored.is_empty() {
            let name = match platform {
                Platform::Windows => "windows",
                Platform::MacOs => "macos",
                Platform::Linux => "linux",
                Platform::Unknown => "unknown",
            };
            bail!("No release found for platform '{name}'.");
        }
    }
    scored.sort_by(|a, b| (&b.0, b.1, b.2, &b.3).cmp(&(&a.0, a.1, a.2, &a.3)));
    Ok(scored[0].4.clone())
}

/// `check_for_updates` on a release list (for tests and the worker).
pub fn evaluate(releases: &Value, current_version: &str, platform: Platform) -> Result<UpdateCheck> {
    let r = pick_release(releases, platform)?;
    let latest = release_version(&r).context("Could not parse release version from GitHub response.")?;
    let release_url = s(&r, "html_url");
    let assets = r.get("assets").and_then(Value::as_array).cloned().unwrap_or_default();
    let download_url = select_download_url(&assets, platform).unwrap_or_else(|| release_url.clone());
    let name = s(&r, "name");
    Ok(UpdateCheck {
        update_available: is_newer_version(current_version, &latest),
        current_version: current_version.to_owned(),
        release_name: if name.is_empty() { format!("v{latest}") } else { name },
        latest_version: latest,
        release_url,
        download_url,
        published_at: s(&r, "published_at"),
        notes: s(&r, "body"),
    })
}

fn agent(timeout: Duration) -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(timeout))
        .user_agent("e-callisto-fits-analyzer-lite-update-check")
        .build()
        .into()
}

/// `check_for_updates`: asks GitHub (8 s timeout).
pub fn check_for_updates(current_version: &str) -> Result<UpdateCheck> {
    let mut resp = agent(Duration::from_secs(8))
        .get(&releases_url())
        .header("Accept", "application/vnd.github+json")
        .header("X-GitHub-Api-Version", "2022-11-28")
        .call()
        .map_err(|e| anyhow::anyhow!("{e}"))?;
    let text = resp.body_mut().with_config().limit(32 * 1024 * 1024).read_to_string()?;
    let releases: Value = serde_json::from_str(&text)?;
    evaluate(&releases, current_version, Platform::current())
}

/// `_extract_whats_new_section`: the "What's New" section of release
/// notes, else all of them.
fn whats_new(markdown: &str) -> String {
    let text = markdown.replace("\r\n", "\n").replace('\r', "\n");
    let text = text.trim();
    let lines: Vec<&str> = text.split('\n').collect();
    let heading = |l: &str| -> Option<(usize, String)> {
        let t = l.trim_start();
        let level = t.chars().take_while(|c| *c == '#').count();
        (1..=6).contains(&level).then(|| (level, t[level..].trim().to_owned()))
    };
    let simplify = |h: &str| {
        let h: String =
            h.to_lowercase().chars().map(|c| if c.is_ascii_alphanumeric() || c == ' ' { c } else { ' ' }).collect();
        h.split_whitespace().collect::<Vec<_>>().join(" ")
    };
    let Some((start, level)) = lines.iter().enumerate().find_map(|(i, l)| {
        let (lv, h) = heading(l)?;
        let h = simplify(&h);
        (h.contains("what s new") || h.contains("whats new")).then_some((i, lv))
    }) else {
        return text.to_owned();
    };
    let end = (start + 1..lines.len())
        .find(|&j| {
            let t = lines[j].trim_start();
            let lv = t.chars().take_while(|c| *c == '#').count();
            (1..=6).contains(&lv) && t[lv..].starts_with(char::is_whitespace) && lv <= level
        })
        .unwrap_or(lines.len());
    lines[start..end].join("\n").trim().to_owned()
}

/// `_markdown_to_plain_text`.
fn markdown_to_plain(md: &str) -> String {
    let mut text = md.replace("\r\n", "\n").replace('\r', "\n");
    // Fenced code blocks.
    while let Some(a) = text.find("```") {
        let Some(b) = text[a + 3..].find("```") else { break };
        text.replace_range(a..a + 3 + b + 3, "");
    }
    let mut out = Vec::new();
    for line in text.split('\n') {
        let mut l = line.to_owned();
        // Headings, block quotes, rules, bullets.
        let t = l.trim_start();
        let hashes = t.chars().take_while(|c| *c == '#').count();
        if (1..=6).contains(&hashes) {
            l = t[hashes..].trim_start().to_owned();
        }
        if let Some(rest) = l.trim_start().strip_prefix('>') {
            l = rest.strip_prefix(' ').unwrap_or(rest).to_owned();
        }
        let tt = l.trim();
        if tt.len() >= 3
            && (tt.chars().all(|c| c == '-') || tt.chars().all(|c| c == '*') || tt.chars().all(|c| c == '_'))
        {
            l = String::new();
        }
        let tl = l.trim_start();
        if let Some(rest) = tl.strip_prefix("- ").or_else(|| tl.strip_prefix("* ")).or_else(|| tl.strip_prefix("+ ")) {
            l = format!("- {}", rest.trim_start());
        }
        out.push(inline_plain(&l));
    }
    let mut text = out.join("\n");
    while text.contains("\n\n\n") {
        text = text.replace("\n\n\n", "\n\n");
    }
    text.trim().to_owned()
}

/// Inline markdown to text: code, images, links, emphasis.
fn inline_plain(line: &str) -> String {
    let mut s = line.replace('`', "");
    // Images, then links: keep the text of `[text](url)`.
    while let Some(a) = s.find('[') {
        let Some(b) = s[a..].find("](").map(|i| a + i) else { break };
        let Some(c) = s[b..].find(')').map(|i| b + i) else { break };
        let image = a > 0 && s.as_bytes()[a - 1] == b'!';
        let label = s[a + 1..b].to_owned();
        let from = if image { a - 1 } else { a };
        s.replace_range(from..=c, if image { "" } else { &label });
    }
    for mark in ["**", "__", "*", "_"] {
        while let Some(a) = s.find(mark) {
            let Some(b) = s[a + mark.len()..].find(mark).map(|i| a + mark.len() + i) else { break };
            let inner = s[a + mark.len()..b].to_owned();
            s.replace_range(a..b + mark.len(), &inner);
        }
    }
    s
}

/// `_release_notes_preview`: the What's New section as text, at most `limit` characters.
pub fn release_notes_preview(notes: &str, limit: usize) -> String {
    let raw = notes.replace("\r\n", "\n").replace('\r', "\n");
    let raw = raw.trim();
    if raw.is_empty() {
        return String::new();
    }
    let clean = markdown_to_plain(&whats_new(raw));
    if clean.chars().count() <= limit {
        return clean;
    }
    let cut: String = clean.chars().take(limit).collect();
    format!("{}...", cut.trim_end())
}

/// `_format_bytes`.
pub fn format_bytes(n: u64) -> String {
    let units = ["B", "KB", "MB", "GB", "TB"];
    let mut v = n as f64;
    let mut i = 0;
    while v >= 1024.0 && i < units.len() - 1 {
        v /= 1024.0;
        i += 1;
    }
    if i == 0 { format!("{} {}", v as u64, units[0]) } else { format!("{v:.1} {}", units[i]) }
}

/// `_suggest_update_filename`: the URL's file name, else one for this platform.
pub fn suggest_filename(url: &str, latest_version: &str, current_version: &str) -> String {
    let path = url.split(['?', '#']).next().unwrap_or("");
    let base = path.rsplit('/').next().unwrap_or("");
    let base = percent_decode(base);
    if !base.is_empty() && base != "." && path.contains('/') && !path.ends_with('/') {
        return base;
    }
    let v = if latest_version.trim().is_empty() { current_version } else { latest_version.trim() };
    let ext = match Platform::current() {
        Platform::Windows => ".exe",
        Platform::MacOs => ".dmg",
        Platform::Linux => ".deb",
        Platform::Unknown => ".bin",
    };
    format!("e-CALLISTO_FITS_Analyzer_Lite_v{v}{ext}")
}

fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%'
            && i + 2 < b.len()
            && let Ok(v) = u8::from_str_radix(&s[i + 1..i + 3], 16)
        {
            out.push(v);
            i += 3;
            continue;
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// How a download ended.
pub enum Download {
    Done(PathBuf),
    Cancelled,
}

/// `UpdateDownloadWorker.run`: `url` streamed to `<dest>.part`, then
/// renamed; `progress(downloaded, total)` (total 0 when unknown);
/// `cancelled()` stops it and removes the partial file.
pub fn download(
    url: &str,
    dest: &Path,
    progress: &mut dyn FnMut(u64, u64),
    cancelled: &dyn Fn() -> bool,
) -> Result<Download> {
    if url.trim().is_empty() {
        bail!("Missing update download URL.");
    }
    if dest.as_os_str().is_empty() {
        bail!("Missing destination path for update download.");
    }
    let part = PathBuf::from(format!("{}.part", dest.display()));
    if let Some(dir) = dest.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::create_dir_all(dir)?;
    }
    let result = (|| -> Result<Download> {
        let resp = agent(Duration::from_secs(3600)).get(url).call().map_err(|e| anyhow::anyhow!("{e}"))?;
        let total: u64 = resp
            .headers()
            .get("Content-Length")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.trim().parse().ok())
            .unwrap_or(0);
        let mut reader = resp.into_body().into_reader();
        let mut f = std::fs::File::create(&part)?;
        let mut done = 0u64;
        progress(done, total);
        let mut buf = vec![0u8; 128 * 1024];
        loop {
            if cancelled() {
                drop(f);
                let _ = std::fs::remove_file(&part);
                return Ok(Download::Cancelled);
            }
            let n = reader.read(&mut buf)?;
            if n == 0 {
                break;
            }
            f.write_all(&buf[..n])?;
            done += n as u64;
            progress(done, total);
        }
        f.flush()?;
        drop(f);
        if dest.exists() {
            std::fs::remove_file(dest)?;
        }
        std::fs::rename(&part, dest)?;
        Ok(Download::Done(dest.to_path_buf()))
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&part);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn release(tag: &str, assets: &[&str], published: &str) -> Value {
        json!({
            "tag_name": tag, "name": tag, "draft": false, "prerelease": false, "published_at": published,
            "html_url": format!("https://github.com/x/releases/{tag}"), "body": "## What's New\n- **Faster** loading\n\n## Other\nx",
            "assets": assets.iter().map(|a| json!({"name": a, "browser_download_url": format!("https://dl/{a}")})).collect::<Vec<_>>(),
        })
    }

    #[test]
    fn versions_like_the_full_app() {
        assert_eq!(version_text("lite-v1.2.3"), Some("1.2.3".into()));
        assert!(is_newer_version("1.0.0", "1.0.1"));
        assert!(!is_newer_version("1.0.1", "1.0.1"));
        assert!(is_newer_version("1.0.0-beta", "1.0.0"));
        assert!(!is_newer_version("1.0.0", "1.0.0-rc1"));
        // As `update_checker.py` answers them.
        for (v, pre) in [
            ("1.0.0", false),
            ("v1.0.0-beta", true),
            ("1.0.0b1", true),
            ("1.0.0 Beta", true),
            ("1.0.0rc2", true),
            ("1.0.0-alpha3x", true),
            ("1.0.0a", true),
            ("1.0.0 a release", false),
            ("1.0.0.dev", true),
            ("1.0.0-pre", true),
            ("1.0.0_rc1", true),
            ("2", false),
            ("1.0.0-final", false),
            ("1.0.0beta", true),
            ("lite-v1.2", false),
        ] {
            assert_eq!(is_prerelease(v), pre, "{v}");
        }
        for (a, b, newer) in [
            ("1.0.0", "1.0", false),
            ("1.0", "1.0.1", true),
            ("1.0.0b1", "1.0.0", true),
            ("1.0.0", "1.0.0b1", false),
            ("1.0.0-beta", "1.0.0-rc1", false),
            ("2.0", "10.0", true),
            ("x", "1.0", false),
        ] {
            assert_eq!(is_newer_version(a, b), newer, "{a} {b}");
        }
    }

    #[test]
    fn only_lite_releases_count() {
        let list = json!([
            release("v3.2.0", &["full.exe"], "2026-10-01"),
            release("lite-v1.1.0", &["lite.exe", "lite.dmg"], "2026-10-02"),
            release("lite-v1.0.0", &["lite.exe"], "2026-09-01"),
        ]);
        let r = evaluate(&list, "1.0.0", Platform::Windows).unwrap();
        assert!(r.update_available);
        assert_eq!(r.latest_version, "1.1.0");
        assert_eq!(r.download_url, "https://dl/lite.exe");
        let mac = evaluate(&list, "1.1.0", Platform::MacOs).unwrap();
        assert!(!mac.update_available);
        assert!(evaluate(&list, "1.0.0", Platform::Linux).is_err());
        assert_eq!(release_notes_preview(&r.notes, 1400), "What's New\n- Faster loading");
    }

    #[test]
    fn sizes_and_names() {
        assert_eq!(format_bytes(512), "512 B");
        assert_eq!(format_bytes(1536), "1.5 KB");
        assert_eq!(suggest_filename("https://dl/a%20b.exe?x=1", "1.1.0", "1.0.0"), "a b.exe");
    }
}
