//! Port of `src/backend/radio/callisto_timeline.py`: stepping a loaded
//! dataset to the previous or next 15-minute observation, from files beside
//! the current ones first and the archive otherwise.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Instant;

use anyhow::Result;
use chrono::{Duration, NaiveDate, NaiveDateTime, Utc};

use super::archive::{ListingError, Source, list_day};
use super::cache::{fetch_cached, source_for_path};
use super::combine::{MAX_CONSECUTIVE_SECONDS, MIN_CONSECUTIVE_SECONDS};
use super::naming::{basename, has_fits_suffix, normalize_focus_code, parse_archive_filename};

pub const NOMINAL_STEP_SECONDS: f64 = 900.0;
pub const TODAY_CACHE_TTL_SECONDS: f64 = 120.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Direction {
    Previous,
    Next,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Segment {
    pub observed_at: NaiveDateTime,
    pub focus_codes: Vec<String>,
    pub paths: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct TimelineState {
    pub station: String,
    pub focus_codes: Vec<String>,
    pub segments: Vec<Segment>,
    pub step_seconds: f64,
    /// The server adjacent observations are looked up on.
    pub source: Source,
}

impl TimelineState {
    pub fn start(&self) -> NaiveDateTime {
        self.segments[0].observed_at
    }

    pub fn stop(&self) -> NaiveDateTime {
        self.segments.last().unwrap().observed_at
    }

    pub fn paths(&self) -> Vec<String> {
        self.segments.iter().flat_map(|s| s.paths.iter().cloned()).collect()
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ArchiveEntry {
    pub station: String,
    pub observed_at: NaiveDateTime,
    pub focus_code: String,
    pub filename: String,
    pub url: String,
}

fn station_key(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()
}

pub fn describe_timeline(paths: &[String]) -> Result<TimelineState, String> {
    let cleaned: Vec<&String> = paths.iter().filter(|p| !p.trim().is_empty()).collect();
    if cleaned.is_empty() {
        return Err("No source files to describe.".into());
    }
    let mut stations = BTreeSet::new();
    let mut grouped: BTreeMap<NaiveDateTime, BTreeMap<String, String>> = BTreeMap::new();
    for path in &cleaned {
        let (station, observed, focus) = parse_archive_filename(basename(path))?;
        stations.insert(station_key(&station));
        grouped.entry(observed).or_default().insert(normalize_focus_code(&focus), path.to_string());
    }
    if stations.len() != 1 {
        return Err(format!(
            "A timeline must come from one station; found: {}.",
            stations.into_iter().collect::<Vec<_>>().join(", ")
        ));
    }
    let station = parse_archive_filename(basename(cleaned[0]))?.0;
    let focus_codes: Vec<String> =
        grouped.values().flat_map(|c| c.keys().cloned()).collect::<BTreeSet<_>>().into_iter().collect();
    for (t, cells) in &grouped {
        if !cells.keys().eq(focus_codes.iter()) {
            return Err(format!(
                "Every observation in a timeline must cover the same focus codes; {} does not.",
                t.format("%Y-%m-%d %H:%M:%S")
            ));
        }
    }
    let ordered: Vec<NaiveDateTime> = grouped.keys().copied().collect();
    let segments = ordered
        .iter()
        .map(|t| Segment {
            observed_at: *t,
            focus_codes: focus_codes.clone(),
            paths: focus_codes.iter().map(|f| grouped[t][f].clone()).collect(),
        })
        .collect();
    let step = if ordered.len() >= 2 { seconds(ordered[1] - ordered[0]) } else { NOMINAL_STEP_SECONDS };
    // `_source_of_paths`: the server named by a cached file, else the default.
    let source = cleaned.iter().map(|p| source_for_path(p)).find(|s| *s != Source::Ecallisto).unwrap_or_default();
    Ok(TimelineState { station, focus_codes, segments, step_seconds: step, source })
}

fn seconds(d: Duration) -> f64 {
    d.num_milliseconds() as f64 / 1000.0
}

pub fn target_timestamp(state: &TimelineState, direction: Direction) -> NaiveDateTime {
    let step = Duration::milliseconds((state.step_seconds * 1000.0) as i64);
    match direction {
        Direction::Next => state.stop() + step,
        Direction::Previous => state.start() - step,
    }
}

fn edge(state: &TimelineState, direction: Direction) -> NaiveDateTime {
    match direction {
        Direction::Next => state.stop(),
        Direction::Previous => state.start(),
    }
}

fn is_consecutive(edge: NaiveDateTime, candidate: NaiveDateTime, direction: Direction) -> bool {
    let mut delta = seconds(candidate - edge);
    if direction == Direction::Previous {
        delta = -delta;
    }
    (MIN_CONSECUTIVE_SECONDS..=MAX_CONSECUTIVE_SECONDS).contains(&delta)
}

fn pick_closest(
    edge: NaiveDateTime,
    direction: Direction,
    stamps: impl IntoIterator<Item = NaiveDateTime>,
) -> Option<NaiveDateTime> {
    let mut best: Option<(f64, NaiveDateTime)> = None;
    for s in stamps {
        if !is_consecutive(edge, s, direction) {
            continue;
        }
        let d = seconds(s - edge).abs();
        if best.is_none_or(|(b, _)| d < b) {
            best = Some((d, s));
        }
    }
    best.map(|b| b.1)
}

fn candidate_directories(state: &TimelineState) -> Vec<PathBuf> {
    let mut seen = Vec::new();
    for seg in &state.segments {
        for p in &seg.paths {
            let abs = std::path::absolute(p).unwrap_or_else(|_| PathBuf::from(p));
            if let Some(dir) = abs.parent()
                && dir.is_dir()
                && !seen.iter().any(|d: &PathBuf| d == dir)
            {
                seen.push(dir.to_path_buf());
            }
        }
    }
    seen
}

/// The adjacent observation's files from the folders already in use.
pub fn find_local_segment(state: &TimelineState, direction: Direction) -> Option<Vec<String>> {
    let e = edge(state, direction);
    let wanted: BTreeSet<&String> = state.focus_codes.iter().collect();
    let key = station_key(&state.station);
    let mut found: BTreeMap<NaiveDateTime, BTreeMap<String, String>> = BTreeMap::new();
    for dir in candidate_directories(state) {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        let mut names: Vec<String> = entries.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        for name in names {
            if !has_fits_suffix(&name) {
                continue;
            }
            let Ok((station, observed, focus)) = parse_archive_filename(&name) else { continue };
            if station_key(&station) != key || !is_consecutive(e, observed, direction) {
                continue;
            }
            let focus = normalize_focus_code(&focus);
            if !wanted.contains(&focus) {
                continue;
            }
            found
                .entry(observed)
                .or_default()
                .entry(focus)
                .or_insert_with(|| dir.join(&name).to_string_lossy().into_owned());
        }
    }
    let complete = found.iter().filter(|(_, c)| c.keys().collect::<BTreeSet<_>>() == wanted).map(|(t, _)| *t);
    let target = pick_closest(e, direction, complete.collect::<Vec<_>>())?;
    Some(state.focus_codes.iter().map(|f| found[&target][f].clone()).collect())
}

/// Finished days never change; today expires after two minutes. Keyed by
/// server and day.
type DayCache = HashMap<(Source, NaiveDate), (Instant, Vec<ArchiveEntry>)>;
static DAY_CACHE: Mutex<Option<DayCache>> = Mutex::new(None);

pub fn clear_day_cache() {
    *DAY_CACHE.lock().unwrap() = None;
}

/// One day of a server's archive, parsed.
pub fn list_archive_day(source: Source, date: NaiveDate, use_cache: bool) -> Result<Vec<ArchiveEntry>, String> {
    let key = (source, date);
    if use_cache
        && let Some((stored, entries)) = DAY_CACHE.lock().unwrap().as_ref().and_then(|c| c.get(&key)).cloned()
        && (date < Utc::now().date_naive() || stored.elapsed().as_secs_f64() < TODAY_CACHE_TTL_SECONDS)
    {
        return Ok(entries);
    }
    let label = source.label();
    let files = list_day(source, date, false).map_err(|e| match e {
        ListingError::Http(m) => format!("{label} archive: {m}"),
        ListingError::Unreachable(m) => format!("Could not reach the archive ({label}): {m}"),
    })?;
    let entries: Vec<ArchiveEntry> = files
        .into_iter()
        .filter_map(|f| {
            Some(ArchiveEntry {
                station: f.station,
                observed_at: f.observed_at?,
                focus_code: normalize_focus_code(&f.focus_code),
                url: f.download_url,
                filename: f.filename,
            })
        })
        .collect();
    DAY_CACHE.lock().unwrap().get_or_insert_with(HashMap::new).insert(key, (Instant::now(), entries.clone()));
    Ok(entries)
}

fn relevant_days(edge: NaiveDateTime, direction: Direction) -> Vec<NaiveDate> {
    let first = edge.date();
    let neighbour = match direction {
        Direction::Next => (edge + Duration::days(1)).date(),
        Direction::Previous => (edge - Duration::days(1)).date(),
    };
    if neighbour != first { vec![first, neighbour] } else { vec![first] }
}

/// The archive entries forming the adjacent observation, or the reason there is none.
pub fn find_archive_segment(state: &TimelineState, direction: Direction) -> Result<Vec<ArchiveEntry>, String> {
    let e = edge(state, direction);
    let wanted: BTreeSet<&String> = state.focus_codes.iter().collect();
    let key = station_key(&state.station);
    let mut found: BTreeMap<NaiveDateTime, BTreeMap<String, ArchiveEntry>> = BTreeMap::new();
    for day in relevant_days(e, direction) {
        for entry in list_archive_day(state.source, day, true)? {
            if station_key(&entry.station) != key || !is_consecutive(e, entry.observed_at, direction) {
                continue;
            }
            if !wanted.contains(&entry.focus_code) {
                continue;
            }
            found.entry(entry.observed_at).or_default().entry(entry.focus_code.clone()).or_insert(entry);
        }
    }
    let when = if direction == Direction::Next { "after" } else { "before" };
    if found.is_empty() {
        return Err(format!(
            "No {} data {when} {} in the {} archive.",
            state.station,
            e.format("%H:%M"),
            state.source.label()
        ));
    }
    let complete: Vec<NaiveDateTime> =
        found.iter().filter(|(_, c)| c.keys().collect::<BTreeSet<_>>() == wanted).map(|(t, _)| *t).collect();
    let Some(target) = pick_closest(e, direction, complete) else {
        if let Some(partial) = pick_closest(e, direction, found.keys().copied().collect::<Vec<_>>()) {
            let have: BTreeSet<&String> = found[&partial].keys().collect();
            let missing: Vec<&str> = wanted.difference(&have).map(|s| s.as_str()).collect();
            return Err(format!(
                "{} at {} is missing focus code(s) {}; the combined set cannot be extended.",
                state.station,
                partial.format("%H:%M"),
                missing.join(", ")
            ));
        }
        return Err(format!("No consecutive {} observation {when} {}.", state.station, e.format("%H:%M")));
    };
    Ok(state.focus_codes.iter().map(|f| found[&target][f].clone()).collect())
}

/// Local paths for the adjacent observation, downloading through the cache if needed.
pub fn resolve_segment(
    state: &TimelineState,
    direction: Direction,
    cache_root: &Path,
    mut progress: impl FnMut(f64, &str),
) -> Result<Vec<String>, String> {
    if let Some(local) = find_local_segment(state, direction) {
        progress(1.0, &format!("Using local files for {}", basename(&local[0])));
        return Ok(local);
    }
    let entries = find_archive_segment(state, direction)?;
    let mut paths = Vec::new();
    for entry in entries {
        let (path, was_cached) =
            fetch_cached(cache_root, &entry.url, &entry.filename, &mut progress).map_err(|e| e.to_string())?;
        progress(1.0, &format!("{} {}", if was_cached { "Using cached" } else { "Downloaded" }, entry.filename));
        paths.push(path.to_string_lossy().into_owned());
    }
    Ok(paths)
}

/// Whether an adjacent observation exists, and why not when it does not.
pub fn probe_segment(state: &TimelineState, direction: Direction) -> (bool, String) {
    if find_local_segment(state, direction).is_some() {
        return (true, String::new());
    }
    match find_archive_segment(state, direction) {
        Ok(_) => (true, String::new()),
        Err(e) => (false, e),
    }
}

pub fn extended_paths(state: &TimelineState, new_paths: &[String], direction: Direction) -> Vec<String> {
    match direction {
        Direction::Next => [state.paths(), new_paths.to_vec()].concat(),
        Direction::Previous => [new_paths.to_vec(), state.paths()].concat(),
    }
}

/// Drops the first or last observation; returns the remaining paths and the
/// time shift that causes (negative by one segment when trimming the start).
pub fn trimmed_paths(state: &TimelineState, trim_start: bool) -> Result<(Vec<String>, f64), String> {
    if state.segments.len() < 2 {
        return Err("A single-observation dataset cannot be trimmed further.".into());
    }
    let (remaining, shift) = if trim_start {
        let rest = &state.segments[1..];
        (rest, -seconds(rest[0].observed_at - state.start()))
    } else {
        (&state.segments[..state.segments.len() - 1], 0.0)
    };
    Ok((remaining.iter().flat_map(|s| s.paths.iter().cloned()).collect(), shift))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn describes_and_trims() {
        let paths = vec![
            "/d/GERMANY-DLR_20261007_050000_02.fit.gz".to_string(),
            "/d/GERMANY-DLR_20261007_050000_01.fit.gz".to_string(),
            "/d/GERMANY-DLR_20261007_051500_01.fit.gz".to_string(),
            "/d/GERMANY-DLR_20261007_051500_02.fit.gz".to_string(),
        ];
        let s = describe_timeline(&paths).unwrap();
        assert_eq!(s.focus_codes, vec!["01", "02"]);
        assert_eq!(s.step_seconds, 900.0);
        assert_eq!(s.segments[0].paths[0], paths[1]);
        assert_eq!(target_timestamp(&s, Direction::Next).format("%H%M%S").to_string(), "053000");
        let (rest, shift) = trimmed_paths(&s, true).unwrap();
        assert_eq!(rest.len(), 2);
        assert_eq!(shift, -900.0);
        assert!(describe_timeline(&paths[..3]).is_err());
        assert_eq!(s.source, Source::Ecallisto, "a file outside the cache keeps the default server");
    }

    use super::super::archive::test_mock::{fits_bytes, install, record};
    use super::super::archive::{ASTRODONCEL_FILES_URL, ASTRODONCEL_LISTING_URL, BASE_URL};
    use super::super::cache::cache_path_for;

    #[test]
    fn extending_an_astrodoncel_observation_stays_on_astrodoncel() {
        clear_day_cache();
        let root = std::env::temp_dir().join(format!("lite_tl_{}", std::process::id())).join("callisto_cache");
        let _ = std::fs::remove_dir_all(&root);
        let first = "BIR_20261001_234500_01.fit.gz";
        let next = "BIR_20261002_000002_01.fit.gz";
        let first_url = format!("{ASTRODONCEL_FILES_URL}2026/10/01/{first}");
        let path = cache_path_for(&root, &first_url, first).unwrap();
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, fits_bytes(true)).unwrap();
        let state = describe_timeline(&[path.to_string_lossy().into_owned()]).unwrap();
        assert_eq!(state.source, Source::Astrodoncel);

        let requests = std::sync::Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let seen = requests.clone();
        let _m = install(move |u| {
            seen.lock().unwrap().push(u.to_owned());
            let listing = |d: &str| format!("{ASTRODONCEL_LISTING_URL}?path={d}");
            Some(if u == listing("2026%2F10%2F01") {
                (200, format!("[{}]", record(first)).into_bytes())
            } else if u == listing("2026%2F10%2F02") {
                (200, format!("[{}]", record(next)).into_bytes())
            } else if u.ends_with(next) {
                (200, fits_bytes(true))
            } else {
                (404, Vec::new())
            })
        });
        let (ok, reason) = probe_segment(&state, Direction::Next);
        assert!(ok, "{reason}");
        let paths = resolve_segment(&state, Direction::Next, &root, |_, _| {}).unwrap();
        assert_eq!(paths.len(), 1);
        assert!(paths[0].contains("astrodoncel") && paths[0].ends_with(next), "{}", paths[0]);
        assert!(requests.lock().unwrap().iter().all(|u| !u.starts_with(BASE_URL)));
        let extended = describe_timeline(&extended_paths(&state, &paths, Direction::Next)).unwrap();
        assert_eq!(extended.source, Source::Astrodoncel);
        assert_eq!(extended.segments.len(), 2);
        clear_day_cache();
    }

    #[test]
    fn day_listings_are_cached_per_server_and_failures_name_it() {
        clear_day_cache();
        let date = NaiveDate::from_ymd_opt(2026, 10, 1).unwrap();
        let ecallisto_day = format!("{BASE_URL}2026/10/01/");
        let _m = install(move |u| {
            Some(if u == ecallisto_day {
                (200, b"<a href=\"ONE_20261001_000000_01.fit.gz\">x</a>".to_vec())
            } else if u.starts_with(ASTRODONCEL_LISTING_URL) {
                (200, format!("[{}]", record("TWO_20261001_000000_01.fit.gz")).into_bytes())
            } else {
                (404, Vec::new())
            })
        });
        assert_eq!(list_archive_day(Source::Ecallisto, date, true).unwrap()[0].station, "ONE");
        assert_eq!(list_archive_day(Source::Astrodoncel, date, true).unwrap()[0].station, "TWO");
        drop(_m);
        let _m = install(|u| {
            Some(if u.starts_with(ASTRODONCEL_LISTING_URL) { (200, b"<html>".to_vec()) } else { (404, Vec::new()) })
        });
        let other = NaiveDate::from_ymd_opt(2026, 10, 3).unwrap();
        let err = list_archive_day(Source::Astrodoncel, other, true).unwrap_err();
        assert!(err.starts_with("Astrodoncel archive:"), "{err}");
        drop(_m);
        let _m = install(|_| None);
        let err = list_archive_day(Source::Astrodoncel, other, true).unwrap_err();
        assert!(err.starts_with("Could not reach the archive (Astrodoncel)"), "{err}");
        clear_day_cache();
    }
}
