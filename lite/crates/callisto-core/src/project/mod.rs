//! The `.efaproj` project file (`src/backend/session/project_session.py`):
//! a zip holding `meta.json`, an optional `arrays.npz` and, from schema 2
//! on, embedded files (FITS frames, JPEG2000 images) other parts keep.
//!
//! Writes are atomic: the archive is written next to the target and renamed
//! over it once complete. Schema 1 files (radio only) still open.

pub mod npy;
pub mod recovery;

use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufWriter, Read, Write};
use std::path::{Path, PathBuf};

use crate::pyjson::{self, Json, Object};
pub use npy::Npy;

pub const MAGIC: &str = "e-callisto-fits-analyzer-project";
pub const SCHEMA_VERSION: i64 = 2;
pub const SUPPORTED_SCHEMA_VERSIONS: [i64; 2] = [1, 2];
pub const META_FILENAME: &str = "meta.json";
pub const ARRAYS_FILENAME: &str = "arrays.npz";
pub const STORAGE_LINK: &str = "link";
pub const STORAGE_EMBED: &str = "embed";

/// Every part a project can hold, in the order they are restored.
pub const PART_KEYS: [&str; 6] = ["radio", "solar", "gcs", "geomagnetic", "event_context", "composer"];

/// Embedded members stored without compression.
const STORED_SUFFIXES: [&str; 7] = [".fits", ".fit", ".fts", ".fz", ".gz", ".jp2", ".jpx"];
const COPY_CHUNK: usize = 1024 * 1024;

/// `ProjectFormatError` and `ProjectSaveCancelled`, with the full app's messages.
#[derive(Debug, Clone, PartialEq)]
pub enum ProjectError {
    Format(String),
    Cancelled,
    Io(String),
}

impl std::fmt::Display for ProjectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ProjectError::Format(m) | ProjectError::Io(m) => f.write_str(m),
            ProjectError::Cancelled => f.write_str("Project save cancelled."),
        }
    }
}

impl std::error::Error for ProjectError {}

impl From<std::io::Error> for ProjectError {
    fn from(e: std::io::Error) -> Self {
        ProjectError::Io(e.to_string())
    }
}

fn format_err<T>(msg: impl Into<String>) -> Result<T, ProjectError> {
    Err(ProjectError::Format(msg.into()))
}

/// `datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")`.
pub fn now_iso() -> String {
    chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%:z").to_string()
}

/// `safe_member_name`: a traversal-free leaf name for an embedded file.
pub fn safe_member_name(path: &str, default: &str) -> String {
    let unix = path.replace('\\', "/");
    let base = unix.rsplit('/').next().unwrap_or("").trim();
    let mut name = String::new();
    let mut in_bad = false;
    for c in base.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
            name.push(c);
            in_bad = false;
        } else if !in_bad {
            name.push('_');
            in_bad = true;
        }
    }
    let name = name.trim_matches('.');
    if name.is_empty() { default.to_owned() } else { name.to_owned() }
}

/// `is_safe_arcname`: relative, forward slashes, no traversal, not reserved.
pub fn is_safe_arcname(arcname: &str) -> bool {
    if arcname.is_empty() || arcname != arcname.trim() {
        return false;
    }
    if arcname.contains('\\') || arcname.contains(':') || arcname.starts_with('/') {
        return false;
    }
    if arcname == META_FILENAME || arcname == ARRAYS_FILENAME {
        return false;
    }
    arcname.split('/').all(|p| !p.is_empty() && p != "." && p != "..")
}

fn stored(arcname: &str) -> bool {
    let lower = arcname.to_lowercase();
    STORED_SUFFIXES.iter().any(|s| lower.ends_with(s))
}

/// `project_parts`: which parts a project holds (schema 1 is radio only).
pub fn project_parts(meta: &Object) -> BTreeMap<&'static str, bool> {
    let mut out: BTreeMap<&'static str, bool> = PART_KEYS.iter().map(|k| (*k, false)).collect();
    match meta.get("parts").and_then(Json::as_object) {
        Some(raw) => {
            for k in PART_KEYS {
                out.insert(k, raw.get(k).is_some_and(Json::truthy));
            }
        }
        None => {
            out.insert("radio", true);
        }
    }
    out
}

/// `project_storage`.
pub fn project_storage(meta: &Object) -> &'static str {
    match meta.get("storage").and_then(Json::as_str) {
        Some(STORAGE_EMBED) => STORAGE_EMBED,
        _ => STORAGE_LINK,
    }
}

/// What a project file holds.
#[derive(Clone, Debug, Default)]
pub struct ProjectPayload {
    pub meta: Object,
    pub arrays: BTreeMap<String, Npy>,
    /// Embedded member name → extracted path (empty unless extracted).
    pub files: BTreeMap<String, PathBuf>,
}

fn load_meta(raw: &[u8]) -> Result<Object, ProjectError> {
    let Ok(text) = std::str::from_utf8(raw) else { return format_err("Invalid meta.json (not valid UTF-8 JSON).") };
    let Ok(v) = pyjson::loads(text.trim_start_matches('\u{feff}')) else {
        return format_err("Invalid meta.json (not valid UTF-8 JSON).");
    };
    let Json::Object(meta) = v else { return format_err("Invalid meta.json (expected a JSON object).") };
    if meta.get("magic").and_then(Json::as_str) != Some(MAGIC) {
        return format_err("Unrecognized project file (magic mismatch).");
    }
    let version = meta.get("schema_version").and_then(Json::as_i64).unwrap_or(0);
    if !SUPPORTED_SCHEMA_VERSIONS.contains(&version) {
        return format_err(format!("Unsupported project schema_version={version}."));
    }
    Ok(meta)
}

fn open_zip(path: &Path) -> Result<zip::ZipArchive<File>, ProjectError> {
    let f = File::open(path)?;
    zip::ZipArchive::new(f).or_else(|_| format_err("Not a valid project file (bad zip)."))
}

fn read_member(zip: &mut zip::ZipArchive<File>, name: &str) -> Result<Option<Vec<u8>>, ProjectError> {
    match zip.by_name(name) {
        Ok(mut f) => {
            let mut buf = Vec::with_capacity(f.size() as usize);
            f.read_to_end(&mut buf)?;
            Ok(Some(buf))
        }
        Err(zip::result::ZipError::FileNotFound) => Ok(None),
        Err(_) => format_err("Not a valid project file (bad zip)."),
    }
}

/// `str(KeyError)` of `ZipFile.read` on a missing member.
fn missing(name: &str) -> String {
    format!("Missing required file in project: \"There is no item named '{name}' in the archive\"")
}

/// `read_project_meta`: only the validated `meta.json`.
pub fn read_project_meta(path: &Path) -> Result<Object, ProjectError> {
    let mut zip = open_zip(path)?;
    let Some(raw) = read_member(&mut zip, META_FILENAME)? else { return format_err(missing(META_FILENAME)) };
    load_meta(&raw)
}

/// `read_project`: the meta, the arrays, and the embedded files extracted
/// into `extract_dir(meta)` when given. Unsafe member names refuse the file.
pub fn read_project(path: &Path, extract_dir: Option<&dyn Fn(&Object) -> PathBuf>) -> Result<ProjectPayload, ProjectError> {
    let mut zip = open_zip(path)?;
    let Some(meta_raw) = read_member(&mut zip, META_FILENAME)? else { return format_err(missing(META_FILENAME)) };
    let meta = load_meta(&meta_raw)?;
    let mut names = Vec::new();
    for i in 0..zip.len() {
        let Ok(f) = zip.by_index_raw(i) else { return format_err("Not a valid project file (bad zip).") };
        names.push(f.name().map(|n| n.into_owned()).unwrap_or_default());
    }
    let arrays_raw = if names.iter().any(|n| n == ARRAYS_FILENAME) { read_member(&mut zip, ARRAYS_FILENAME)? } else { None };
    if arrays_raw.is_none() && meta.get("schema_version").and_then(Json::as_i64).unwrap_or(0) < 2 {
        return format_err(format!("Missing required file in project: '{ARRAYS_FILENAME}'"));
    }
    let members: Vec<String> =
        names.into_iter().filter(|n| n != META_FILENAME && n != ARRAYS_FILENAME && !n.ends_with('/')).collect();
    for name in &members {
        if !is_safe_arcname(name) {
            return format_err(format!("Project contains an unsafe file path: {}", py_repr_str(name)));
        }
    }
    let mut files = BTreeMap::new();
    if let Some(dir) = extract_dir
        && !members.is_empty()
    {
        files = extract_members(&mut zip, &members, &dir(&meta))?;
    }
    let arrays = match arrays_raw {
        Some(raw) if !raw.is_empty() => {
            npy::read_npz(&raw).or_else(|_| format_err("Invalid arrays.npz (cannot be read safely)."))?
        }
        _ => BTreeMap::new(),
    };
    Ok(ProjectPayload { meta, arrays, files })
}

/// Python's `repr(str)` for messages.
fn py_repr_str(s: &str) -> String {
    if s.contains('\'') && !s.contains('"') { format!("\"{s}\"") } else { format!("'{}'", s.replace('\'', "\\'")) }
}

/// `extract_members`: streams `members` into `root`, keeping their paths.
fn extract_members(zip: &mut zip::ZipArchive<File>, members: &[String], root: &Path) -> Result<BTreeMap<String, PathBuf>, ProjectError> {
    std::fs::create_dir_all(root)?;
    let mut out = BTreeMap::new();
    for name in members {
        if !is_safe_arcname(name) {
            return format_err(format!("Project contains an unsafe file path: {}", py_repr_str(name)));
        }
        let dest = name.split('/').fold(root.to_path_buf(), |p, part| p.join(part));
        if let Some(parent) = dest.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let Ok(mut src) = zip.by_name(name) else { return format_err("Not a valid project file (bad zip).") };
        let mut dst = BufWriter::new(File::create(&dest)?);
        std::io::copy(&mut src, &mut dst)?;
        dst.flush()?;
        out.insert(name.clone(), dest);
    }
    Ok(out)
}

fn zip_time(path: &Path) -> zip::DateTime {
    use chrono::{Datelike, Timelike};
    let stamp: chrono::DateTime<chrono::Local> =
        std::fs::metadata(path).and_then(|m| m.modified()).map(Into::into).unwrap_or_else(|_| chrono::Local::now());
    let year = stamp.year().max(1980);
    zip::DateTime::from_date_and_time(
        year as u16,
        if stamp.year() < 1980 { 1 } else { stamp.month() as u8 },
        if stamp.year() < 1980 { 1 } else { stamp.day() as u8 },
        stamp.hour() as u8,
        stamp.minute() as u8,
        stamp.second() as u8,
    )
    .unwrap_or_default()
}

fn now_zip_time() -> zip::DateTime {
    use chrono::{Datelike, Timelike};
    let n = chrono::Local::now();
    zip::DateTime::from_date_and_time(n.year() as u16, n.month() as u8, n.day() as u8, n.hour() as u8, n.minute() as u8, n.second() as u8)
        .unwrap_or_default()
}

/// Progress of a save: bytes done, bytes in all, a line of text.
pub type Progress<'a> = &'a mut dyn FnMut(u64, u64, &str);

/// `write_project`: atomic; `files` maps a member name to a file on disk,
/// streamed in. A `cancel` returning true aborts and leaves no file.
pub fn write_project(
    path: &Path,
    meta: &Object,
    arrays: &BTreeMap<String, Npy>,
    files: &BTreeMap<String, PathBuf>,
    mut progress: Option<Progress>,
    cancel: Option<&dyn Fn() -> bool>,
) -> Result<(), ProjectError> {
    let mut payload = meta.clone();
    payload.entry("magic".into()).or_insert_with(|| MAGIC.into());
    payload.insert("schema_version".into(), Json::Int(SCHEMA_VERSION));
    payload.entry("created_at".into()).or_insert_with(|| now_iso().into());
    for arcname in files.keys() {
        if !is_safe_arcname(arcname) {
            return format_err(format!("Refusing to embed an unsafe member name: {}", py_repr_str(arcname)));
        }
    }
    payload.insert("files".into(), Json::Array(files.keys().map(|k| Json::Str(k.clone())).collect()));
    let meta_bytes = pyjson::dumps(&Json::Object(payload)).into_bytes();
    let arrays_bytes = if arrays.is_empty() {
        Vec::new()
    } else {
        npy::write_npz(arrays).map_err(|e| ProjectError::Io(e.to_string()))?
    };
    let file_bytes: u64 = files.values().filter_map(|p| std::fs::metadata(p).ok()).map(|m| m.len()).sum();
    let total = (file_bytes + meta_bytes.len() as u64 + arrays_bytes.len() as u64).max(1);
    let mut done = 0u64;
    let check_cancel = || -> Result<(), ProjectError> {
        if cancel.is_some_and(|c| c()) { Err(ProjectError::Cancelled) } else { Ok(()) }
    };

    let tmp = PathBuf::from(format!("{}.tmp", path.display()));
    let result = (|| -> Result<(), ProjectError> {
        let mut zip = zip::ZipWriter::new(BufWriter::new(File::create(&tmp)?));
        let deflated = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated)
            .last_modified_time(now_zip_time());
        let zerr = |e: zip::result::ZipError| ProjectError::Io(e.to_string());
        zip.start_file(META_FILENAME, deflated).map_err(zerr)?;
        zip.write_all(&meta_bytes)?;
        done += meta_bytes.len() as u64;
        if !arrays_bytes.is_empty() {
            zip.start_file(ARRAYS_FILENAME, deflated).map_err(zerr)?;
            zip.write_all(&arrays_bytes)?;
            done += arrays_bytes.len() as u64;
        }
        if let Some(p) = progress.as_mut() {
            p(done, total, "Writing project\u{2026}");
        }
        for (arcname, src) in files {
            check_cancel()?;
            let Ok(mut handle) = File::open(src) else {
                let err = std::io::Error::last_os_error();
                return format_err(format!("Could not read a file to embed:\n{}\n{err}", src.display()));
            };
            let method = if stored(arcname) { zip::CompressionMethod::Stored } else { zip::CompressionMethod::Deflated };
            let opts = zip::write::SimpleFileOptions::default()
                .compression_method(method)
                .last_modified_time(zip_time(src))
                .unix_permissions(0o644)
                .large_file(true);
            zip.start_file(arcname.as_str(), opts).map_err(zerr)?;
            let mut buf = vec![0u8; COPY_CHUNK];
            loop {
                check_cancel()?;
                let n = handle.read(&mut buf)?;
                if n == 0 {
                    break;
                }
                zip.write_all(&buf[..n])?;
                done += n as u64;
            }
            if let Some(p) = progress.as_mut() {
                p(done, total, &format!("Embedded {}", safe_member_name(arcname, "file")));
            }
        }
        check_cancel()?;
        let mut inner = zip.finish().map_err(zerr)?;
        inner.flush()?;
        Ok(())
    })();
    if let Err(e) = result {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    std::fs::rename(&tmp, path).or_else(|_| {
        // `os.replace` over an existing file on Windows.
        let _ = std::fs::remove_file(path);
        std::fs::rename(&tmp, path)
    })?;
    if let Some(p) = progress.as_mut() {
        p(done, total, "Project saved.");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn member_names() {
        assert_eq!(safe_member_name("C:\\data\\my file (1).fit.gz", "file"), "my_file_1_.fit.gz");
        assert_eq!(safe_member_name("..", "file"), "file");
        assert!(is_safe_arcname("solar/frames/0001_a.fits"));
        for bad in ["", " a", "/a", "a\\b", "c:/a", "a/../b", "a//b", "meta.json", "./a"] {
            assert!(!is_safe_arcname(bad), "{bad}");
        }
    }

    #[test]
    fn round_trip_with_embedded_files() {
        let dir = std::env::temp_dir().join(format!("callisto_project_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let src = dir.join("frame.fits");
        std::fs::write(&src, b"SIMPLE  =                    T").unwrap();
        let mut meta = Object::new();
        meta.insert("filename".into(), "a.fit".into());
        meta.insert("noise_vmin".into(), Json::Float(f64::NAN));
        let mut arrays = BTreeMap::new();
        arrays.insert("freqs".to_owned(), Npy::from_f64(&[2], &[45.0, 44.5]));
        let mut files = BTreeMap::new();
        files.insert("solar/frames/0001_frame.fits".to_owned(), src.clone());
        let path = dir.join("p.efaproj");
        write_project(&path, &meta, &arrays, &files, None, None).unwrap();
        let out = dir.join("x");
        let p = read_project(&path, Some(&|_m: &Object| out.clone())).unwrap();
        assert_eq!(p.meta.get("schema_version"), Some(&Json::Int(2)));
        assert!(p.meta.get("noise_vmin").and_then(Json::as_f64).is_some_and(f64::is_nan));
        assert_eq!(p.arrays["freqs"].to_f64().unwrap(), vec![45.0, 44.5]);
        assert_eq!(std::fs::read(&p.files["solar/frames/0001_frame.fits"]).unwrap(), std::fs::read(&src).unwrap());
        // A cancelled save leaves nothing behind.
        let cancelled = dir.join("c.efaproj");
        let r = write_project(&cancelled, &meta, &arrays, &files, None, Some(&|| true));
        assert_eq!(r, Err(ProjectError::Cancelled));
        assert!(!cancelled.exists() && !dir.join("c.efaproj.tmp").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
