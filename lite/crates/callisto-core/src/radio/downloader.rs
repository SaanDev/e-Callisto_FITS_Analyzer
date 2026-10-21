//! The non-GUI parts of the e-CALLISTO downloader: archive candidates and
//! their filtering (`src/ui/downloads/callisto_downloader.py`), the station
//! list helpers (`archive_stations.py`), preview panels, and preparing a
//! selection for import or comparison (`gui_workers.py`).

use std::collections::BTreeMap;
use std::path::Path;

use chrono::{NaiveDate, NaiveDateTime};

use super::archive::{ListingError, check_archive_server, day_url, extract_fits_links, fetch_listing, join_url};
use super::array::Array2;
use super::cache::{self, DEFAULT_MAX_CACHE_BYTES, fetch_cached};
use super::combine::{self, CombineOptions, CombineType, Combined, FrequencyRelation, group_combinable_paths};
use super::fits_io::{FitsLoadResult, load_callisto_fits};
use super::naming::{basename, parse_archive_filename};

/// `MAX_EVENT_STATION_DAYS`: listing the stations of a long event window
/// reads one archive day per UTC day.
pub const MAX_EVENT_STATION_DAYS: usize = 7;

/// `CallistoEventCandidate`: one archive file. `observed_at` is `None` only
/// for names the archive parser does not recognise.
#[derive(Clone, Debug, PartialEq)]
pub struct Candidate {
    pub station: String,
    pub observed_at: Option<NaiveDateTime>,
    pub filename: String,
    pub url: String,
    pub receiver_id: String,
}

impl Candidate {
    /// A parsed archive file name, or `None`.
    pub fn parse(filename: &str, url: String) -> Option<Self> {
        let (station, observed, receiver_id) = parse_archive_filename(filename).ok()?;
        Some(Candidate { station, observed_at: Some(observed), filename: filename.to_owned(), url, receiver_id })
    }

    pub fn utc_text(&self) -> String {
        self.observed_at.map_or_else(|| "\u{2014}".to_owned(), |t| t.format("%Y-%m-%d %H:%M:%S").to_string())
    }
}

/// `_station_key`: whitespace collapsed, lower case.
pub fn station_key(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()
}

/// `_candidate_matches_station`.
pub fn candidate_matches_station(parsed_station: &str, filename: &str, selected_station: &str) -> bool {
    let selected = station_key(selected_station);
    if selected.is_empty() {
        return false;
    }
    if station_key(parsed_station) == selected {
        return true;
    }
    basename(filename).to_lowercase().starts_with(&format!("{}_", selected_station.trim().to_lowercase()))
}

/// `utc_archive_dates_for_window`: every UTC day the window touches.
pub fn utc_archive_dates_for_window(start: NaiveDateTime, stop: NaiveDateTime) -> Result<Vec<NaiveDate>, String> {
    if stop < start {
        return Err("End time must be after start time.".into());
    }
    let mut days = Vec::new();
    let mut day = start.date();
    while day <= stop.date() {
        days.push(day);
        day = day.succ_opt().unwrap();
    }
    Ok(days)
}

/// `filter_event_candidates`: the selected stations' files that start
/// inside the window.
pub fn filter_event_candidates(
    hrefs: &[String],
    day_url: &str,
    selected_stations: &[String],
    start: NaiveDateTime,
    stop: NaiveDateTime,
) -> Vec<Candidate> {
    let selected: Vec<&str> = selected_stations.iter().map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
    let mut out = Vec::new();
    for href in hrefs {
        let filename = basename(href);
        let Some(c) = Candidate::parse(filename, join_url(day_url, href)) else { continue };
        let observed = c.observed_at.unwrap();
        if observed < start || observed > stop {
            continue;
        }
        if !selected.iter().any(|s| candidate_matches_station(&c.station, filename, s)) {
            continue;
        }
        out.push(c);
    }
    out
}

/// `sort_event_candidates`: unique by URL, by station, time and name.
pub fn sort_event_candidates(candidates: Vec<Candidate>) -> Vec<Candidate> {
    let mut seen = std::collections::HashSet::new();
    let mut unique: Vec<Candidate> = candidates
        .into_iter()
        .filter(|c| seen.insert(if c.url.is_empty() { c.filename.clone() } else { c.url.clone() }))
        .collect();
    unique.sort_by_cached_key(|c| (station_key(&c.station), c.observed_at, c.filename.to_lowercase()));
    unique
}

/// `filter_spectral_overview_candidates`: one station's files of one UTC day.
pub fn filter_spectral_overview_candidates(
    hrefs: &[String],
    day_url: &str,
    station: &str,
    date: NaiveDate,
) -> Vec<Candidate> {
    let wanted = station_key(station);
    let out = hrefs
        .iter()
        .filter_map(|href| Candidate::parse(basename(href), join_url(day_url, href)))
        .filter(|c| station_key(&c.station) == wanted && c.observed_at.unwrap().date() == date)
        .collect();
    sort_event_candidates(out)
}

/// `group_spectral_overview_candidates`: by focus code, in code order.
pub fn group_by_focus_code(candidates: &[Candidate]) -> BTreeMap<String, Vec<Candidate>> {
    let mut groups: BTreeMap<String, Vec<Candidate>> = BTreeMap::new();
    for c in candidates {
        let code = c.receiver_id.trim();
        if !code.is_empty() {
            groups.entry(code.to_owned()).or_default().push(c.clone());
        }
    }
    groups.into_iter().map(|(k, v)| (k, sort_event_candidates(v))).collect()
}

/// `CallistoDownloaderApp._focus_group_label`.
pub fn focus_group_label(focus_code: &str, candidates: &[Candidate]) -> String {
    let count = candidates.len();
    let suffix = if count == 1 { "file" } else { "files" };
    let hm = |c: Option<&Candidate>| c.and_then(|c| c.observed_at).map(|t| t.format("%H:%M").to_string());
    let first = hm(candidates.first()).unwrap_or_default();
    let last = hm(candidates.last()).unwrap_or_default();
    let span = if first == last { first } else { format!("{first}\u{2013}{last}") };
    format!("Focus {focus_code} \u{2014} {count} {suffix} ({span})")
}

/// `merge_station_lists`: the union, case-insensitively, in listing order.
pub fn merge_station_lists<'a>(lists: impl IntoIterator<Item = &'a [String]>) -> Vec<String> {
    let mut by_key: BTreeMap<String, String> = BTreeMap::new();
    for list in lists {
        for s in list {
            by_key.entry(s.to_lowercase()).or_insert_with(|| s.clone());
        }
    }
    by_key.into_values().collect()
}

/// `describe_days`.
pub fn describe_days(days: &[NaiveDate]) -> String {
    match days {
        [] => "the selected dates".into(),
        [d] => d.format("%Y-%m-%d").to_string(),
        [first, .., last] => format!("{} to {}", first.format("%Y-%m-%d"), last.format("%Y-%m-%d")),
    }
}

// ------------------------------------------------------------- workers

/// `FetchWorker`'s result: the whole day's files whose name contains the
/// station, as `(filename, url)`.
pub fn fetch_station_day(
    date: NaiveDate,
    station: &str,
    mut progress: impl FnMut(usize, usize),
) -> Result<Vec<(String, String)>, ListingError> {
    let (ok, message) = check_archive_server();
    if !ok {
        return Err(ListingError::Unreachable(message));
    }
    let url = day_url(date);
    let html = fetch_listing(&url)?;
    let hrefs = extract_fits_links(&html);
    let key = station.trim().to_lowercase();
    let mut out = Vec::new();
    for (i, href) in hrefs.iter().enumerate() {
        let filename = basename(href);
        if filename.to_lowercase().contains(&key) {
            out.push((filename.to_owned(), join_url(&url, href)));
        }
        progress(i + 1, hrefs.len());
    }
    Ok(out)
}

/// What an event search found (`EventFetchWorker`).
#[derive(Clone, Debug, Default)]
pub struct EventSearch {
    pub candidates: Vec<Candidate>,
    pub warnings: Vec<String>,
    pub missing_stations: Vec<String>,
}

/// `EventFetchWorker.run`.
pub fn search_event(
    start: NaiveDateTime,
    stop: NaiveDateTime,
    stations: &[String],
    mut progress: impl FnMut(usize, usize),
) -> Result<EventSearch, String> {
    let dates = utc_archive_dates_for_window(start, stop)?;
    let stations: Vec<String> = stations.iter().map(|s| s.trim().to_owned()).filter(|s| !s.is_empty()).collect();
    let (ok, message) = check_archive_server();
    if !ok {
        return Err(format!("FITS server is not responding: {message}"));
    }
    let mut found = Vec::new();
    let mut warnings = Vec::new();
    for (i, date) in dates.iter().enumerate() {
        let url = day_url(*date);
        match fetch_listing(&url) {
            Ok(html) => found.extend(filter_event_candidates(&extract_fits_links(&html), &url, &stations, start, stop)),
            Err(e) => warnings.push(format!("{}: {e}", date.format("%Y-%m-%d"))),
        }
        progress(i + 1, dates.len());
    }
    let candidates = sort_event_candidates(found);
    let missing_stations = stations
        .iter()
        .filter(|s| !candidates.iter().any(|c| candidate_matches_station(&c.station, &c.filename, s)))
        .cloned()
        .collect();
    Ok(EventSearch { candidates, warnings, missing_stations })
}

/// `deliver_archive_file`: the file through the cache, copied to `out_path`.
/// Returns whether it came from the cache.
pub fn deliver_archive_file(
    cache_root: &Path,
    url: &str,
    out_path: &Path,
    filename: &str,
    progress: impl FnMut(f64, &str),
) -> anyhow::Result<bool> {
    let (cached, was_cached) = fetch_cached(cache_root, url, filename, progress)?;
    let same = match (std::fs::canonicalize(&cached), std::fs::canonicalize(out_path)) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    };
    if !same {
        std::fs::copy(&cached, out_path)?;
    }
    Ok(was_cached)
}

// ------------------------------------------------------------- previews

/// One spectrogram of a preview window (`build_preview_panels`).
#[derive(Clone, Debug)]
pub struct PreviewPanel {
    pub title: String,
    pub subtitle: String,
    pub data: Array2,
    pub freqs: Vec<f64>,
    pub time: Vec<f64>,
    pub combine_type: Option<CombineType>,
}

/// `_COMBINE_TAB_LABELS`.
fn combine_tab_label(t: Option<CombineType>) -> &'static str {
    match t {
        Some(CombineType::Time) => "Combined (time)",
        Some(CombineType::Frequency) => "Combined (frequency)",
        Some(CombineType::TimeFrequency) => "Combined (time + frequency)",
        None => "Combined",
    }
}

/// `build_preview_panels`: the downloaded files in the largest valid
/// combinations (backend defaults; Import offers the options), the rest one
/// panel each. `local_files` holds `(path, display name)`.
pub fn build_preview_panels(local_files: &[(String, String)]) -> (Vec<PreviewPanel>, Vec<String>) {
    let name_of = |path: &str| {
        local_files.iter().find(|(p, _)| p == path).map_or_else(|| basename(path).to_owned(), |(_, n)| n.clone())
    };
    let paths: Vec<String> = local_files.iter().map(|(p, _)| p.clone()).collect();
    let mut panels = Vec::new();
    let mut errors = Vec::new();
    for group in group_combinable_paths(&paths, true, CombineOptions::default()) {
        if let Some(c) = group.combined {
            let sources: Vec<String> = group.paths.iter().map(|p| name_of(p)).collect();
            let mut notes = Vec::new();
            if let (Some(options), Some(mask)) = (c.options, &c.gap_row_mask)
                && mask.iter().any(|&g| g)
            {
                notes.push(format!("frequency gap filled ({})", options.gap_fill.key()));
            }
            if let Some(options) = c.options
                && options.overlap_connection_mhz.is_some()
            {
                notes.push(format!("overlap resolved ({})", options.overlap_policy.key()));
            }
            let mut subtitle = format!("{} files: {}", sources.len(), sources.join(", "));
            if !notes.is_empty() {
                subtitle.push_str(&format!("  \u{2014}  {} (use Import for full control)", notes.join("; ")));
            }
            panels.push(PreviewPanel {
                title: combine_tab_label(group.combine_type).into(),
                subtitle,
                data: c.data,
                freqs: c.freqs,
                time: c.time,
                combine_type: Some(c.combine_type),
            });
            continue;
        }
        for path in &group.paths {
            let name = name_of(path);
            let res = match load_callisto_fits(Path::new(path)) {
                Ok(r) => r,
                Err(e) => {
                    errors.push(format!("{name}: {e}"));
                    continue;
                }
            };
            let title = match parse_archive_filename(&name) {
                Ok((_, at, focus)) => format!("Focus {focus} \u{b7} {}", at.format("%H:%M:%S")),
                Err(_) => name.clone(),
            };
            panels.push(PreviewPanel {
                title,
                subtitle: name,
                data: res.data,
                freqs: res.freqs,
                time: res.time,
                combine_type: None,
            });
        }
    }
    (panels, errors)
}

/// A finished preview preparation (`PreviewPrepareWorker`, `BurstPreviewWorker`).
#[derive(Clone, Debug)]
pub struct PreviewResult {
    pub panels: Vec<PreviewPanel>,
    pub errors: Vec<String>,
    pub cached_count: usize,
    pub total: usize,
}

/// `PreviewPrepareWorker.run` (and the Burst List's worker, which reports
/// finer progress): download or reuse each file, then build the panels.
/// `progress` gets `(value, maximum, text)`; returning `false` from
/// `cancelled` stops the work.
pub fn prepare_preview(
    cache_root: &Path,
    candidates: &[Candidate],
    mut progress: impl FnMut(usize, usize, String),
    cancelled: impl Fn() -> bool,
) -> Result<PreviewResult, PrepareError> {
    let count = candidates.len();
    if count == 0 {
        return Err(PrepareError::Failed("Select at least one FITS file to preview.".into()));
    }
    let total = count * 1000;
    let mut local = Vec::new();
    let mut errors = Vec::new();
    let mut cached_count = 0;
    progress(0, total, "Preparing selected FITS files...".into());
    for (i, c) in candidates.iter().enumerate() {
        if cancelled() {
            return Err(PrepareError::Cancelled);
        }
        progress(i * 900, total, format!("Preparing {} ({}/{count})...", c.filename, i + 1));
        let result = fetch_cached(cache_root, &c.url, &c.filename, |fraction, message| {
            let step = i * 900 + (fraction.clamp(0.0, 1.0) * 900.0) as usize;
            progress(step, total, format!("{message} ({}/{count})", i + 1));
        });
        if cancelled() {
            return Err(PrepareError::Cancelled);
        }
        match result {
            Ok((path, was_cached)) => {
                cached_count += usize::from(was_cached);
                local.push((path.to_string_lossy().into_owned(), c.filename.clone()));
            }
            Err(e) => errors.push(format!("{}: {e}", c.filename)),
        }
        progress((i + 1) * 900, total, format!("Prepared {} of {count} FITS files.", i + 1));
    }
    if local.is_empty() {
        return Err(PrepareError::Failed(format!(
            "None of the selected files could be prepared.\n\n{}",
            errors.iter().take(8).cloned().collect::<Vec<_>>().join("\n")
        )));
    }
    progress(count * 900, total, "Building preview...".into());
    let (panels, panel_errors) = build_preview_panels(&local);
    if cancelled() {
        return Err(PrepareError::Cancelled);
    }
    errors.extend(panel_errors);
    if panels.is_empty() {
        return Err(PrepareError::Failed(format!(
            "No preview could be produced.\n\n{}",
            errors.iter().take(8).cloned().collect::<Vec<_>>().join("\n")
        )));
    }
    cache::enforce_cache_limit(cache_root, DEFAULT_MAX_CACHE_BYTES);
    progress(total, total, "Preview ready.".into());
    Ok(PreviewResult { panels, errors, cached_count, total: count })
}

#[derive(Clone, Debug, PartialEq)]
pub enum PrepareError {
    Cancelled,
    Failed(String),
}

// ------------------------------------------------------------- import

/// Progress of an import or comparison preparation, as the full app's
/// workers signal it to the "Importing FITS Files" dialog.
#[derive(Clone, Debug, PartialEq)]
pub enum ImportProgress {
    Text(String),
    Range(i64, i64),
    Value(i64),
}

/// `_is_remote_source`.
pub fn is_remote_source(value: &str) -> bool {
    let v = value.trim().to_lowercase();
    v.starts_with("http://") || v.starts_with("https://")
}

/// The label the progress dialog opens with (`process_imported_files`).
pub fn import_label(sources: &[String]) -> &'static str {
    let remote = sources.iter().filter(|s| is_remote_source(s)).count();
    if remote == sources.len() {
        "Downloading selected FITS files..."
    } else if remote == 0 {
        "Preparing selected FITS files..."
    } else {
        "Downloading and preparing selected FITS files..."
    }
}

/// The label of a comparison preparation (`process_comparison_sources`).
pub fn comparison_label(sources: &[String]) -> &'static str {
    if sources.iter().any(|s| is_remote_source(s)) {
        "Preparing selected FITS files for comparison..."
    } else {
        "Opening selected FITS files for comparison..."
    }
}

/// Every source as a local file: archive URLs through the cache, local
/// paths checked. Returns the files and how many came from the cache.
fn local_sources(
    cache_root: &Path,
    sources: &[String],
    progress: &mut impl FnMut(ImportProgress),
) -> anyhow::Result<(Vec<String>, usize)> {
    let mut files = Vec::new();
    let mut cached_count = 0;
    for (i, source) in sources.iter().enumerate() {
        let path = if is_remote_source(source) {
            let (path, was_cached) = fetch_cached(cache_root, source.trim(), "", |_, _| {})?;
            if was_cached {
                cached_count += 1;
                progress(ImportProgress::Text(format!(
                    "Using cached {}...",
                    path.file_name().unwrap_or_default().to_string_lossy()
                )));
            }
            path.to_string_lossy().into_owned()
        } else {
            let p = std::path::absolute(source.trim()).unwrap_or_else(|_| source.trim().into());
            if !p.is_file() {
                anyhow::bail!("Local FITS file does not exist: {}", p.display());
            }
            p.to_string_lossy().into_owned()
        };
        files.push(path);
        progress(ImportProgress::Value(i as i64 + 1));
    }
    Ok((files, cached_count))
}

/// What an import prepared (`DownloaderImportWorker`'s payload kinds).
#[derive(Clone, Debug)]
pub enum ImportPayload {
    Single { filename: String, source_path: String, result: Box<FitsLoadResult> },
    Combined(Box<Combined>),
    FrequencyOptionsRequired { files: Vec<String>, relation: FrequencyRelation, combine_type: CombineType },
    Invalid(String),
}

/// `DownloaderImportWorker.run`.
pub fn prepare_import(
    cache_root: &Path,
    sources: &[String],
    mut progress: impl FnMut(ImportProgress),
) -> Result<ImportPayload, String> {
    if sources.is_empty() {
        return Err("No files were received from the downloader.".into());
    }
    progress(ImportProgress::Text(import_label(sources).into()));
    progress(ImportProgress::Range(0, sources.len() as i64));
    progress(ImportProgress::Value(0));
    let (files, cached_count) = local_sources(cache_root, sources, &mut progress)
        .map_err(|e| format!("Failed to prepare one or more FITS files:\n{e}"))?;
    if cached_count > 0 {
        progress(ImportProgress::Text(format!("Prepared {} file(s), {cached_count} from cache.", files.len())));
    }
    cache::enforce_cache_limit(cache_root, DEFAULT_MAX_CACHE_BYTES);

    if files.len() == 1 {
        let path = files[0].clone();
        return load_callisto_fits(Path::new(&path))
            .map(|result| ImportPayload::Single {
                filename: basename(&path).to_owned(),
                source_path: path.clone(),
                result: Box::new(result),
            })
            .map_err(|e| format!("Could not load FITS file:\n{e}"));
    }

    progress(ImportProgress::Text("Checking file compatibility...".into()));
    let inspection = combine::inspect_combination(&files);
    if !inspection.valid {
        return Ok(ImportPayload::Invalid(inspection.error.unwrap_or_default()));
    }
    let combine_type = inspection.combine_type;
    if let Some(t @ (CombineType::Frequency | CombineType::TimeFrequency)) = combine_type {
        let relation = match inspection.frequency_relation.clone() {
            Some(r) => r,
            None => combine::describe_frequency_combination(&files)
                .map_err(|e| format!("An error occurred while combining files:\n{e}"))?,
        };
        if relation.has_gap() || relation.has_overlap() {
            return Ok(ImportPayload::FrequencyOptionsRequired { files, relation, combine_type: t });
        }
    }
    let mode = match combine_type {
        Some(CombineType::TimeFrequency) => "time + frequency",
        Some(t) => t.key(),
        None => "",
    };
    progress(ImportProgress::Text(format!("Combining files ({mode} mode)...")));
    let combined = match inspection.combined {
        Some(c) => Ok(c),
        None => combine::combine_compatible(&files, CombineOptions::default()),
    };
    combined
        .map(|c| ImportPayload::Combined(Box::new(c)))
        .map_err(|e| format!("An error occurred while combining files:\n{e}"))
}

/// `DownloaderComparisonWorker.run`: the sources as local files.
pub fn prepare_comparison(
    cache_root: &Path,
    sources: &[String],
    mut progress: impl FnMut(ImportProgress),
) -> Result<Vec<String>, String> {
    if sources.is_empty() {
        return Err("No files were received for comparison.".into());
    }
    progress(ImportProgress::Text(comparison_label(sources).into()));
    progress(ImportProgress::Range(0, sources.len() as i64));
    progress(ImportProgress::Value(0));
    let (files, cached_count) = local_sources(cache_root, sources, &mut progress)
        .map_err(|e| format!("Failed to prepare selected FITS files for comparison:\n{e}"))?;
    if cached_count > 0 {
        progress(ImportProgress::Text(format!("Prepared {} file(s), {cached_count} from cache.", files.len())));
    }
    cache::enforce_cache_limit(cache_root, DEFAULT_MAX_CACHE_BYTES);
    Ok(files)
}

/// The invalid-selection message of `_on_downloader_import_finished`.
pub fn invalid_selection_text(detail: &str) -> String {
    let mut text = String::new();
    if !detail.is_empty() {
        text.push_str(detail);
        text.push_str("\n\n");
    }
    text.push_str(
        "Select consecutive time segments, matching frequency bands, or a complete timestamp \u{d7} focus-code grid.",
    );
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dt(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S").unwrap()
    }

    fn hrefs() -> Vec<String> {
        [
            "BIR_20261007_101459_01.fit.gz",
            "BIR_20261007_103000_01.fit.gz",
            "BIR_20261007_103000_02.fit.gz",
            "Malaysia_Banting_20261007_101500_59.fit.gz",
            "GLASGOW_20261007_120000_59.fit",
            "readme.fit",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }

    #[test]
    fn filters_event_candidates_like_the_full_app() {
        let day = "https://x/2026/10/07/";
        let found = filter_event_candidates(
            &hrefs(),
            day,
            &["bir".into(), "malaysia_banting ".into()],
            dt("2026-10-07 10:15:00"),
            dt("2026-10-07 10:30:00"),
        );
        let names: Vec<&str> = found.iter().map(|c| c.filename.as_str()).collect();
        assert_eq!(
            names,
            [
                "BIR_20261007_103000_01.fit.gz",
                "BIR_20261007_103000_02.fit.gz",
                "Malaysia_Banting_20261007_101500_59.fit.gz"
            ]
        );
        assert_eq!(found[0].url, "https://x/2026/10/07/BIR_20261007_103000_01.fit.gz");
        let sorted = sort_event_candidates([found.clone(), found].concat());
        assert_eq!(sorted.len(), 3);
        assert_eq!(sorted[0].station, "BIR");
        assert_eq!(sorted[2].station, "Malaysia_Banting");
    }

    #[test]
    fn groups_and_labels_focus_codes() {
        let all = filter_spectral_overview_candidates(&hrefs(), "https://x/", "BIR", dt("2026-10-07 00:00:00").date());
        let groups = group_by_focus_code(&all);
        assert_eq!(groups.keys().collect::<Vec<_>>(), ["01", "02"]);
        assert_eq!(focus_group_label("01", &groups["01"]), "Focus 01 \u{2014} 2 files (10:14\u{2013}10:30)");
        assert_eq!(focus_group_label("02", &groups["02"]), "Focus 02 \u{2014} 1 file (10:30)");
    }

    #[test]
    fn station_helpers() {
        assert_eq!(station_key("  Mexico   Lance-A "), "mexico lance-a");
        assert!(candidate_matches_station("X", "bir_20261007_000000_01.fit", "BIR"));
        assert!(!candidate_matches_station("BIRR", "BIRR_1.fit", "BIR"));
        let a = vec!["BIR".to_string(), "alaska".to_string()];
        let b = vec!["Alaska".to_string(), "ZZ".to_string()];
        assert_eq!(merge_station_lists([a.as_slice(), b.as_slice()]), ["alaska", "BIR", "ZZ"]);
        let d = |s| NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap();
        assert_eq!(describe_days(&[d("2026-10-07")]), "2026-10-07");
        assert_eq!(describe_days(&[d("2026-10-06"), d("2026-10-07")]), "2026-10-06 to 2026-10-07");
        assert_eq!(
            utc_archive_dates_for_window(dt("2026-10-06 23:00:00"), dt("2026-10-07 01:00:00")).unwrap().len(),
            2
        );
        assert!(utc_archive_dates_for_window(dt("2026-10-07 01:00:00"), dt("2026-10-06 23:00:00")).is_err());
        assert_eq!(import_label(&["https://a".into()]), "Downloading selected FITS files...");
        assert_eq!(import_label(&["C:/a.fit".into()]), "Preparing selected FITS files...");
    }
}
