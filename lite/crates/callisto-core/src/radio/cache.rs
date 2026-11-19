//! Port of `src/backend/radio/callisto_cache.py`: the on-disk cache of
//! archive downloads, `<root>/<STATION>/<YYYY-MM-DD>/<file>`, so a file is
//! fetched at most once. The caller chooses the root (Lite keeps its own).
//!
//! The URL says which server a file came from. e-CALLISTO files live
//! directly under the root; those of any other server under
//! `sources/<source id>/`, so a file of the same name on two servers stays
//! two entries and a cached path still says where it came from.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime};

use anyhow::{Context, Result, anyhow};

use super::archive::{DOWNLOAD_TIMEOUT_S, Source, agent, get_with_retries};
use super::naming::parse_archive_filename;

pub const CACHE_DIR_NAME: &str = "callisto_cache";
pub const SOURCES_DIR_NAME: &str = "sources";
const UNSORTED_DIR_NAME: &str = "_unsorted";
pub const DEFAULT_MAX_CACHE_BYTES: u64 = 2 * 1024 * 1024 * 1024;
/// A `.part` file this old was left by a crash, not by a running download.
const STALE_PART: Duration = Duration::from_secs(24 * 3600);

/// A server's response is not the complete, readable FITS file asked for.
#[derive(Debug)]
pub struct InvalidArchiveFile(pub String);

impl std::fmt::Display for InvalidArchiveFile {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for InvalidArchiveFile {}

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

/// Where a file lives in the cache rooted at `root`; files of a server other
/// than e-CALLISTO under `sources/<source id>/`.
pub fn cache_path_for(root: &Path, url: &str, filename: &str) -> Result<PathBuf> {
    let name = if filename.trim().is_empty() { filename_from_url(url) } else { filename.trim().to_string() };
    if name.is_empty() {
        return Err(anyhow!("Cannot derive a cache filename from: {url:?}"));
    }
    let mut sub = match parse_archive_filename(&name) {
        Ok((station, observed, _)) => PathBuf::from(station).join(observed.format("%Y-%m-%d").to_string()),
        Err(_) => PathBuf::from(UNSORTED_DIR_NAME),
    };
    let source = Source::for_url(url);
    if source != Source::Ecallisto {
        sub = Path::new(SOURCES_DIR_NAME).join(source.id()).join(sub);
    }
    Ok(root.join(sub).join(name))
}

/// `source_id_for_path`: the server a local FITS file came from. Only the
/// cache records that; any other file has no source and keeps the default.
pub fn source_for_path(path: &str) -> Source {
    let parts: Vec<String> =
        Path::new(path).components().map(|c| c.as_os_str().to_string_lossy().to_lowercase()).collect();
    for i in 0..parts.len().saturating_sub(2) {
        if parts[i] == CACHE_DIR_NAME && parts[i + 1] == SOURCES_DIR_NAME {
            return Source::ALL.into_iter().find(|s| s.id() == parts[i + 2]).unwrap_or_default();
        }
    }
    Source::Ecallisto
}

/// `validate_fits_download`: whether `path` holds a readable FITS file.
///
/// Reading the whole gzip stream checks its CRC, which catches a truncated
/// transfer; the FITS check rejects what a server sends in place of a file,
/// such as an HTML error page answered with status 200.
pub fn validate_fits_download(path: &Path) -> Result<(), InvalidArchiveFile> {
    let invalid = |m: String| InvalidArchiveFile(m);
    let mut raw = std::fs::read(path).map_err(|e| invalid(e.to_string()))?;
    if raw.starts_with(&[0x1f, 0x8b]) {
        let mut plain = Vec::new();
        flate2::read::MultiGzDecoder::new(raw.as_slice())
            .read_to_end(&mut plain)
            .map_err(|e| invalid(format!("the gzip data is damaged or incomplete ({e})")))?;
        raw = plain;
    }
    if !raw.starts_with(b"SIMPLE  =") {
        return Err(invalid("the server did not send a FITS file".into()));
    }
    let has_end =
        raw.as_chunks::<80>().0.iter().any(|card| card.starts_with(b"END") && card[3..].iter().all(|b| *b == b' '));
    if !has_end {
        return Err(invalid("the FITS structure cannot be read (the header has no END card)".into()));
    }
    Ok(())
}

/// A `.part` name no other transfer of the same file uses.
fn part_path(target: &Path, name: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map_or(0, |d| d.subsec_nanos());
    let unique = format!("{:x}{:x}{:x}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed), nanos);
    target.with_file_name(format!("{name}.{unique}.part"))
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

/// `(local path, was cached)`; downloads on a miss, writing to a `.part`
/// file of its own first, so an interrupted download never looks complete
/// and concurrent requests for one file cannot corrupt each other. A new
/// transfer becomes a cache entry only once `validate_fits_download` passes.
/// `progress` receives a 0–1 fraction (0 when the size is unknown) and a message.
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
    let part = part_path(&target, &name);
    let unusable = |reason: String| {
        anyhow!(InvalidArchiveFile(format!(
            "{} sent an unusable copy of {name}: {reason}",
            Source::for_url(url).label()
        )))
    };
    let result = (|| -> Result<()> {
        let (status, resp) = get_with_retries(&agent(DOWNLOAD_TIMEOUT_S), url)?;
        if status >= 400 {
            return Err(anyhow!("{status} Client Error for url: {url}"));
        }
        let total: u64 = resp.headers().get("Content-Length").and_then(|v| v.to_str().ok()?.parse().ok()).unwrap_or(0);
        // With a transfer encoding, Content-Length counts the encoded bytes.
        let encoded = resp.headers().contains_key("Content-Encoding");
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
        if total > 0 && written != total && !encoded {
            return Err(unusable(format!("received {written} of {total} bytes")));
        }
        validate_fits_download(&part).map_err(|e| unusable(e.0))?;
        if let Err(e) = std::fs::rename(&part, &target) {
            // Windows refuses to replace a file that is open: a concurrent
            // request for this file finished first and its copy is being read.
            if !target.is_file() {
                return Err(e.into());
            }
            let _ = std::fs::remove_file(&part);
        }
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

/// Removes `.part` files abandoned by a crash; running transfers are newer.
fn remove_stale_parts(root: &Path) {
    fn walk(dir: &Path) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                walk(&p);
            } else if p.to_string_lossy().ends_with(".part")
                && p.metadata().and_then(|m| m.modified()).is_ok_and(|t| t.elapsed().is_ok_and(|age| age > STALE_PART))
            {
                let _ = std::fs::remove_file(&p);
            }
        }
    }
    walk(root);
}

/// Evicts least recently used files until the cache fits; returns bytes freed.
pub fn enforce_cache_limit(root: &Path, max_bytes: u64) -> u64 {
    remove_stale_parts(root);
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

    use super::super::archive::test_mock::{fits_bytes, install};
    use super::super::archive::{ASTRODONCEL_FILES_URL, BASE_URL};

    const NAME: &str = "ALASKA-ANCHORAGE_20261001_000000_01.fit.gz";

    fn urls() -> (String, String) {
        (format!("{BASE_URL}2026/10/01/{NAME}"), format!("{ASTRODONCEL_FILES_URL}2026/10/01/{NAME}"))
    }

    fn temp_root(tag: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("lite_cache_{tag}_{}", std::process::id())).join(CACHE_DIR_NAME);
        let _ = std::fs::remove_dir_all(&root);
        root
    }

    fn parts_under(root: &Path) -> usize {
        fn walk(d: &Path, n: &mut usize) {
            for e in std::fs::read_dir(d).into_iter().flatten().flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk(&p, n);
                } else if p.to_string_lossy().ends_with(".part") {
                    *n += 1;
                }
            }
        }
        let mut n = 0;
        walk(root, &mut n);
        n
    }

    #[test]
    fn each_server_has_its_own_namespace_and_a_path_says_which() {
        let root = Path::new("/c/callisto_cache");
        let (ecallisto, astrodoncel) = urls();
        let legacy = cache_path_for(root, &ecallisto, "").unwrap();
        let other = cache_path_for(root, &astrodoncel, "").unwrap();
        assert_eq!(legacy, root.join("ALASKA-ANCHORAGE").join("2026-10-01").join(NAME));
        assert_eq!(
            other,
            root.join("sources").join("astrodoncel").join("ALASKA-ANCHORAGE").join("2026-10-01").join(NAME)
        );
        assert_eq!(source_for_path(&other.to_string_lossy()), Source::Astrodoncel);
        assert_eq!(source_for_path(&legacy.to_string_lossy()), Source::Ecallisto);
        assert_eq!(source_for_path(&format!("C:/Users/me/fits/{NAME}")), Source::Ecallisto);
        assert_eq!(source_for_path(&format!("/x/callisto_cache/sources/unknown/{NAME}")), Source::Ecallisto);
    }

    #[test]
    fn validation_accepts_fits_and_rejects_what_is_not() {
        let dir = std::env::temp_dir().join(format!("lite_validate_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let good = fits_bytes(true);
        let cases: [(&str, Vec<u8>, bool); 5] = [
            ("gz", good.clone(), true),
            ("plain", fits_bytes(false), true),
            ("html", b"<html><body>Not found</body></html>".to_vec(), false),
            ("truncated", good[..good.len() - 20].to_vec(), false),
            ("no_end", format!("{:<2880}", "SIMPLE  =                    T").into_bytes(), false),
        ];
        for (name, bytes, ok) in cases {
            let path = dir.join(name);
            std::fs::write(&path, bytes).unwrap();
            assert_eq!(validate_fits_download(&path).is_ok(), ok, "{name}");
        }
    }

    #[test]
    fn a_legacy_entry_is_not_a_hit_for_another_server() {
        let root = temp_root("namespaces");
        let (ecallisto, astrodoncel) = urls();
        let payload = fits_bytes(true);
        let requests = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let seen = requests.clone();
        let _m = install(move |u| {
            seen.lock().unwrap().push(u.to_owned());
            Some((200, payload.clone()))
        });
        let (legacy, cached) = fetch_cached(&root, &ecallisto, NAME, |_, _| {}).unwrap();
        assert!(!cached);
        let (other, cached) = fetch_cached(&root, &astrodoncel, NAME, |_, _| {}).unwrap();
        assert!(!cached, "the e-CALLISTO copy must not answer for Astrodoncel");
        assert_ne!(legacy, other);
        assert_eq!(requests.lock().unwrap().len(), 2);
        assert!(other.starts_with(root.join("sources").join("astrodoncel")));
        assert_eq!(std::fs::read(&other).unwrap(), fits_bytes(true), "kept compressed");
        assert_eq!(cache_size_bytes(&root), 2 * fits_bytes(true).len() as u64);
        assert_eq!(parts_under(&root), 0);
        assert_eq!(clear_cache(&root), 0);
        assert_eq!(cache_size_bytes(&root), 0);
    }

    #[test]
    fn an_unusable_transfer_never_becomes_an_entry() {
        let root = temp_root("invalid");
        let (_, astrodoncel) = urls();
        let _m = install(|_| Some((200, b"<html>Error</html>".to_vec())));
        let err = fetch_cached(&root, &astrodoncel, NAME, |_, _| {}).unwrap_err();
        assert!(err.to_string().starts_with(&format!("Astrodoncel sent an unusable copy of {NAME}")), "{err}");
        assert!(cached_file(&root, &astrodoncel, NAME).is_none());
        assert_eq!(parts_under(&root), 0);
    }

    #[test]
    fn only_abandoned_part_files_are_removed() {
        let root = temp_root("parts");
        let dir = root.join("BIR").join("2026-10-01");
        std::fs::create_dir_all(&dir).unwrap();
        let (running, abandoned) = (dir.join("a.fit.gz.1.part"), dir.join("b.fit.gz.2.part"));
        std::fs::write(&running, b"x").unwrap();
        std::fs::write(&abandoned, b"x").unwrap();
        let old = SystemTime::now() - STALE_PART - Duration::from_secs(60);
        std::fs::File::options().write(true).open(&abandoned).unwrap().set_modified(old).unwrap();
        enforce_cache_limit(&root, DEFAULT_MAX_CACHE_BYTES);
        assert!(running.exists() && !abandoned.exists());
    }
}
