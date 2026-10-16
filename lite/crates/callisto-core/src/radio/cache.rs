//! Port of `src/backend/radio/callisto_cache.py`: the on-disk cache of
//! archive downloads, `<root>/<STATION>/<YYYY-MM-DD>/<file>`, so a file is
//! fetched at most once. The caller chooses the root (Lite keeps its own).

use std::path::{Path, PathBuf};
use std::time::SystemTime;

use anyhow::{Context, Result, anyhow};

use super::archive::{DOWNLOAD_TIMEOUT_S, agent, get_with_retries};
use super::naming::parse_archive_filename;

pub const CACHE_DIR_NAME: &str = "callisto_cache";
const UNSORTED_DIR_NAME: &str = "_unsorted";
pub const DEFAULT_MAX_CACHE_BYTES: u64 = 2 * 1024 * 1024 * 1024;

pub fn filename_from_url(url: &str) -> String {
    let path = url.split(['?', '#']).next().unwrap_or("");
    let name = path.rsplit('/').next().unwrap_or("");
    percent_decode(name).trim().to_string()
}

pub(crate) fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%'
            && i + 2 < bytes.len()
            && let Ok(v) = u8::from_str_radix(&s[i + 1..i + 3], 16)
        {
            out.push(v);
            i += 3;
            continue;
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Where a file lives in the cache rooted at `root`.
pub fn cache_path_for(root: &Path, url: &str, filename: &str) -> Result<PathBuf> {
    let name = if filename.trim().is_empty() { filename_from_url(url) } else { filename.trim().to_string() };
    if name.is_empty() {
        return Err(anyhow!("Cannot derive a cache filename from: {url:?}"));
    }
    let sub = match parse_archive_filename(&name) {
        Ok((station, observed, _)) => PathBuf::from(station).join(observed.format("%Y-%m-%d").to_string()),
        Err(_) => PathBuf::from(UNSORTED_DIR_NAME),
    };
    Ok(root.join(sub).join(name))
}

/// The cached copy when a usable one exists (its modification time is
/// refreshed, so eviction keeps recently used files).
pub fn cached_file(root: &Path, url: &str, filename: &str) -> Option<PathBuf> {
    let target = cache_path_for(root, url, filename).ok()?;
    let meta = std::fs::metadata(&target).ok()?;
    if meta.is_file() && meta.len() > 0 {
        let _ = std::fs::File::options().write(true).open(&target).and_then(|f| f.set_modified(SystemTime::now()));
        return Some(target);
    }
    None
}

/// `(local path, was cached)`; downloads on a miss, writing to `.part` first
/// so an interrupted download never looks complete. `progress` receives a
/// 0–1 fraction (0 when the size is unknown) and a message.
pub fn fetch_cached(
    root: &Path,
    url: &str,
    filename: &str,
    mut progress: impl FnMut(f64, &str),
) -> Result<(PathBuf, bool)> {
    let target = cache_path_for(root, url, filename)?;
    let name = target.file_name().unwrap().to_string_lossy().into_owned();
    if let Some(hit) = cached_file(root, url, filename) {
        progress(1.0, &format!("Using cached {name}"));
        return Ok((hit, true));
    }
    let dir = target.parent().unwrap();
    std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    let part = target.with_file_name(format!("{name}.part"));
    let result = (|| -> Result<()> {
        let (status, resp) = get_with_retries(&agent(DOWNLOAD_TIMEOUT_S), url)?;
        if status >= 400 {
            return Err(anyhow!("{status} Client Error for url: {url}"));
        }
        let total: u64 = resp.headers().get("Content-Length").and_then(|v| v.to_str().ok()?.parse().ok()).unwrap_or(0);
        let mut reader = resp.into_body().into_reader();
        let mut out = std::fs::File::create(&part)?;
        let mut buf = vec![0u8; 128 * 1024];
        let mut written = 0u64;
        loop {
            let n = std::io::Read::read(&mut reader, &mut buf)?;
            if n == 0 {
                break;
            }
            std::io::Write::write_all(&mut out, &buf[..n])?;
            written += n as u64;
            let fraction = if total > 0 { (written as f64 / total as f64).min(1.0) } else { 0.0 };
            progress(fraction, &format!("Downloading {name}"));
        }
        drop(out);
        std::fs::rename(&part, &target)?;
        Ok(())
    })();
    if let Err(e) = result {
        let _ = std::fs::remove_file(&part);
        return Err(e);
    }
    progress(1.0, &format!("Downloaded {name}"));
    Ok((target, false))
}

fn cached_files(root: &Path) -> Vec<PathBuf> {
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out);
            } else if !p.to_string_lossy().ends_with(".part") {
                out.push(p);
            }
        }
    }
    let mut out = Vec::new();
    walk(root, &mut out);
    out
}

pub fn cache_size_bytes(root: &Path) -> u64 {
    cached_files(root).iter().filter_map(|p| p.metadata().ok()).map(|m| m.len()).sum()
}

/// Evicts least recently used files until the cache fits; returns bytes freed.
pub fn enforce_cache_limit(root: &Path, max_bytes: u64) -> u64 {
    let mut entries: Vec<(SystemTime, u64, PathBuf)> = cached_files(root)
        .into_iter()
        .filter_map(|p| {
            let m = p.metadata().ok()?;
            Some((m.modified().ok()?, m.len(), p))
        })
        .collect();
    let total: u64 = entries.iter().map(|e| e.1).sum();
    if total <= max_bytes {
        return 0;
    }
    entries.sort_by_key(|e| e.0);
    let mut freed = 0;
    for (_, size, path) in entries {
        if total - freed <= max_bytes {
            break;
        }
        if std::fs::remove_file(&path).is_ok() {
            freed += size;
        }
    }
    prune_empty_dirs(root);
    freed
}

/// `_prune_empty_dirs`: removes emptied station and date folders.
fn prune_empty_dirs(root: &Path) {
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out);
                out.push(p);
            }
        }
    }
    let mut dirs = Vec::new();
    walk(root, &mut dirs);
    dirs.sort_by_key(|d| std::cmp::Reverse(d.components().count()));
    for d in dirs {
        let _ = std::fs::remove_dir(d);
    }
}

/// `clear_cache`: deletes every cached file; returns how many entries
/// could not be removed (files in use).
pub fn clear_cache(root: &Path) -> usize {
    let mut errors = 0;
    if let Ok(entries) = std::fs::read_dir(root) {
        for e in entries.flatten() {
            let p = e.path();
            let ok = if p.is_dir() {
                let _ = std::fs::remove_dir_all(&p);
                true
            } else {
                std::fs::remove_file(&p).is_ok() || !p.exists()
            };
            if !ok {
                errors += 1;
            }
        }
    }
    let _ = std::fs::create_dir_all(root);
    errors
}

/// Python's `format_size`.
pub fn format_size(num_bytes: u64) -> String {
    let mut size = num_bytes as f64;
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        if size < 1024.0 || unit == "TB" {
            return if unit == "B" { format!("{size:.0} {unit}") } else { format!("{size:.1} {unit}") };
        }
        size /= 1024.0;
    }
    format!("{size:.1} TB")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cache_layout() {
        let root = Path::new("/c");
        let p = cache_path_for(root, "https://x/2026/10/07/BIR_20261007_101459_01.fit.gz", "").unwrap();
        assert_eq!(p, root.join("BIR").join("2026-10-07").join("BIR_20261007_101459_01.fit.gz"));
        let p = cache_path_for(root, "https://x/a%20b.txt", "").unwrap();
        assert_eq!(p, root.join("_unsorted").join("a b.txt"));
        assert_eq!(format_size(1536), "1.5 KB");
    }
}
