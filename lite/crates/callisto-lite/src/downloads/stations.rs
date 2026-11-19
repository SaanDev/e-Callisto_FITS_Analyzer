//! Station pickers that follow the archive (`archive_stations.py`): one
//! application-wide cache of each server's stations per UTC day, shared by
//! the downloader's tabs and kept across reopenings, and a debounced loader
//! per picker that reports the union over its days.

use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use std::sync::mpsc::{Receiver, Sender, channel};
use std::time::{Duration, Instant};

use callisto_core::radio::archive::Source;
use callisto_core::radio::downloader::{describe_days, merge_station_lists};
use chrono::{NaiveDate, Utc};

/// `LOOKUP_DELAY_MS`: coalesce the bursts of typing or stepping a date.
pub const LOOKUP_DELAY: Duration = Duration::from_millis(400);
/// `RECENT_DAY_TTL_SECONDS`: today and yesterday still receive uploads.
pub const RECENT_DAY_TTL: Duration = Duration::from_secs(600);
const MAX_THREADS: usize = 4;

pub type Fetch = Arc<dyn Fn(Source, NaiveDate) -> Result<Vec<String>, String> + Send + Sync>;

/// One server's day.
pub type DayKey = (Source, NaiveDate);

type Answer = (DayKey, u64, Result<Vec<String>, String>);

/// `_ArchiveStationService`: station lists per (server, day), with one
/// current fetch for each.
///
/// Fetches carry a generation. A loader that changes server or reloads asks
/// for a fetch at least as new as that moment, which supersedes an older one
/// still running: its late answer is dropped, even when the user switched
/// away from a server and back while it ran.
pub struct StationService {
    stations: HashMap<DayKey, (Instant, Vec<String>)>,
    errors: HashMap<DayKey, String>,
    pending: HashMap<DayKey, u64>,
    generation: u64,
    queue: VecDeque<(DayKey, u64)>,
    running: usize,
    tx: Sender<Answer>,
    rx: Receiver<Answer>,
    fetch: Fetch,
}

impl StationService {
    pub fn new(fetch: Fetch) -> Self {
        let (tx, rx) = channel();
        StationService {
            stations: HashMap::new(),
            errors: HashMap::new(),
            pending: HashMap::new(),
            generation: 0,
            queue: VecDeque::new(),
            running: 0,
            tx,
            rx,
            fetch,
        }
    }

    /// Reads the archive (`fetch_day_stations`).
    pub fn online() -> Self {
        Self::new(Arc::new(|source, day| {
            callisto_core::radio::archive::fetch_day_stations(source, day).map_err(|e| e.to_string())
        }))
    }

    fn is_recent(day: NaiveDate) -> bool {
        day >= Utc::now().date_naive() - chrono::Duration::days(1)
    }

    /// The generation of the next fetch to start.
    pub fn next_generation(&self) -> u64 {
        self.generation + 1
    }

    pub fn cached(&self, key: DayKey) -> Option<&[String]> {
        let (at, stations) = self.stations.get(&key)?;
        if Self::is_recent(key.1) && at.elapsed() > RECENT_DAY_TTL {
            return None;
        }
        Some(stations)
    }

    pub fn error(&self, key: DayKey) -> Option<&str> {
        self.errors.get(&key).map(String::as_str)
    }

    /// Starts a fetch of `key` unless one at least `min_generation` new is running.
    pub fn fetch(&mut self, ctx: &egui::Context, key: DayKey, min_generation: u64) {
        if self.pending.get(&key).is_some_and(|g| *g >= min_generation) {
            return;
        }
        self.generation += 1;
        self.pending.insert(key, self.generation);
        self.errors.remove(&key);
        self.queue.retain(|(k, _)| *k != key);
        self.queue.push_back((key, self.generation));
        self.start(ctx);
    }

    fn start(&mut self, ctx: &egui::Context) {
        while self.running < MAX_THREADS {
            let Some((key, generation)) = self.queue.pop_front() else { break };
            self.running += 1;
            let (tx, fetch, ctx) = (self.tx.clone(), self.fetch.clone(), ctx.clone());
            std::thread::spawn(move || {
                let result = fetch(key.0, key.1);
                let _ = tx.send((key, generation, result));
                ctx.request_repaint();
            });
        }
    }

    pub fn forget(&mut self, keys: &[DayKey]) {
        for k in keys {
            self.stations.remove(k);
            self.errors.remove(k);
        }
    }

    /// Stores finished lookups; returns the days resolved since last time.
    pub fn poll(&mut self, ctx: &egui::Context) -> Vec<DayKey> {
        let mut resolved = Vec::new();
        while let Ok((key, generation, result)) = self.rx.try_recv() {
            self.running -= 1;
            if self.pending.get(&key) != Some(&generation) {
                // Superseded by a newer fetch of the same day.
                continue;
            }
            self.pending.remove(&key);
            match result {
                Ok(s) => {
                    self.stations.insert(key, (Instant::now(), s));
                }
                Err(e) => {
                    self.errors.insert(key, if e.is_empty() { "Error".into() } else { e });
                }
            }
            resolved.push(key);
        }
        self.start(ctx);
        resolved
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum LoaderState {
    #[default]
    Idle,
    Loading,
    Loaded,
    Failed,
}

/// What a loader reports (`loading`, `loaded`, `failed`).
#[derive(Clone, Debug, PartialEq)]
pub enum LoaderEvent {
    Loading,
    Loaded(Vec<String>),
    Failed(String),
}

/// `ArchiveStationLoader` with its `StationAvailabilityLabel` text.
#[derive(Default)]
pub struct StationLoader {
    pub days: Vec<NaiveDate>,
    pub state: LoaderState,
    /// The server the stations are read from.
    pub source: Source,
    min_generation: u64,
    timer: Option<Instant>,
    /// The availability label: plain text, and whether it ends in "Retry".
    pub summary: String,
    pub retry: bool,
    pub tooltip: String,
}

impl StationLoader {
    fn key(&self, day: NaiveDate) -> DayKey {
        (self.source, day)
    }

    /// `set_source`: follow another server, looking up the current days on it.
    pub fn set_source(&mut self, svc: &StationService, source: Source) -> Option<LoaderEvent> {
        if source == self.source {
            return None;
        }
        self.source = source;
        self.min_generation = svc.next_generation();
        if self.days.is_empty() {
            return None;
        }
        let days = self.days.clone();
        self.request(svc, &days)
    }

    /// `request(days)`: a newer request supersedes this one.
    pub fn request(&mut self, svc: &StationService, days: &[NaiveDate]) -> Option<LoaderEvent> {
        let mut unique = Vec::new();
        for d in days {
            if !unique.contains(d) {
                unique.push(*d);
            }
        }
        self.days = unique;
        self.timer = None;
        if let Some(e) = self.finish_from_cache(svc) {
            return Some(e);
        }
        self.state = LoaderState::Loading;
        self.timer = Some(Instant::now() + LOOKUP_DELAY);
        self.show_message(&format!(
            "{}: loading the stations with data on {} (UTC)...",
            self.source.label(),
            describe_days(&self.days)
        ));
        Some(LoaderEvent::Loading)
    }

    /// `reload()`: read the days again, ignoring cached lists.
    pub fn reload(&mut self, svc: &mut StationService) -> Option<LoaderEvent> {
        let days = self.days.clone();
        svc.forget(&days.iter().map(|d| self.key(*d)).collect::<Vec<_>>());
        self.min_generation = svc.next_generation();
        self.request(svc, &days)
    }

    /// `cancel()`.
    pub fn cancel(&mut self) {
        self.timer = None;
        self.days.clear();
        self.state = LoaderState::Idle;
    }

    pub fn show_message(&mut self, text: &str) {
        self.summary = text.to_owned();
        self.retry = false;
        self.tooltip.clear();
    }

    fn finish_from_cache(&mut self, svc: &StationService) -> Option<LoaderEvent> {
        let lists: Option<Vec<&[String]>> = self.days.iter().map(|d| svc.cached(self.key(*d))).collect();
        let lists = lists?;
        self.timer = None;
        self.state = LoaderState::Loaded;
        let stations = merge_station_lists(lists);
        let days = describe_days(&self.days);
        let server = self.source.label();
        let count = stations.len();
        if count > 0 {
            let noun = if count == 1 { "station has" } else { "stations have" };
            self.show_message(&format!("{server}: {count} {noun} data on {days} (UTC)."));
        } else {
            self.show_message(&format!("{server}: no station has published data for {days} (UTC)."));
        }
        Some(LoaderEvent::Loaded(stations))
    }

    /// Runs the debounce timer and picks up resolved days.
    pub fn tick(&mut self, ctx: &egui::Context, svc: &mut StationService, resolved: &[DayKey]) -> Option<LoaderEvent> {
        if let Some(t) = self.timer {
            if Instant::now() >= t {
                self.timer = None;
                for d in self.days.clone() {
                    if svc.cached(self.key(d)).is_none() {
                        svc.fetch(ctx, self.key(d), self.min_generation);
                    }
                }
            } else {
                ctx.request_repaint_after(t - Instant::now());
            }
        }
        if self.state != LoaderState::Loading {
            return None;
        }
        for &(source, day) in resolved {
            if source != self.source || !self.days.contains(&day) {
                continue;
            }
            let key = self.key(day);
            if let Some(err) = svc.error(key)
                && svc.cached(key).is_none()
            {
                let err = err.to_owned();
                self.timer = None;
                self.state = LoaderState::Failed;
                self.summary =
                    format!("{}: could not load the stations for {}.", self.source.label(), describe_days(&self.days));
                self.retry = true;
                self.tooltip = err.clone();
                return Some(LoaderEvent::Failed(err));
            }
            if let Some(e) = self.finish_from_cache(svc) {
                return Some(e);
            }
        }
        None
    }
}

/// `StationComboBinding`: the combo's items, current station, placeholder.
/// A station the user picked stays selected on later dates that have it.
#[derive(Default)]
pub struct StationCombo {
    pub items: Vec<String>,
    pub current: Option<usize>,
    pub placeholder: String,
    preferred: String,
}

impl StationCombo {
    pub fn text(&self) -> String {
        self.current.and_then(|i| self.items.get(i)).cloned().unwrap_or_default()
    }

    /// `textActivated`: remember the user's choice.
    pub fn activate(&mut self, i: usize) {
        self.current = Some(i);
        self.preferred = self.items[i].clone();
    }

    /// `remember_current`: keep the station now shown selected on a later
    /// list that has it (across a server change).
    pub fn remember_current(&mut self) {
        let station = self.text();
        if !station.trim().is_empty() {
            self.preferred = station;
        }
    }

    /// Selects `station` if listed (used to restore a choice).
    pub fn set_text(&mut self, station: &str) {
        if let Some(i) = self.items.iter().position(|s| s.eq_ignore_ascii_case(station)) {
            self.current = Some(i);
        }
    }

    pub fn apply(&mut self, event: &LoaderEvent) {
        match event {
            LoaderEvent::Loading => self.show_empty("Loading stations..."),
            LoaderEvent::Failed(_) => self.show_empty("Stations unavailable"),
            LoaderEvent::Loaded(stations) if stations.is_empty() => self.show_empty("No stations on this date"),
            LoaderEvent::Loaded(stations) => {
                self.items = stations.clone();
                let pref = self.preferred.to_lowercase();
                self.current = Some(self.items.iter().position(|s| s.to_lowercase() == pref).unwrap_or(0));
            }
        }
    }

    fn show_empty(&mut self, placeholder: &str) {
        self.placeholder = placeholder.to_owned();
        self.items.clear();
        self.current = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    fn day() -> NaiveDate {
        NaiveDate::from_ymd_opt(2024, 1, 2).unwrap()
    }

    /// e-CALLISTO's first lookup hangs until `release` is set and then answers
    /// "STALE"; every other lookup answers at once.
    fn hanging_service(release: Arc<AtomicBool>, calls: Arc<Mutex<Vec<DayKey>>>) -> StationService {
        let first = Arc::new(AtomicUsize::new(0));
        StationService::new(Arc::new(move |source, d| {
            calls.lock().unwrap().push((source, d));
            if source == Source::Ecallisto && first.fetch_add(1, Ordering::SeqCst) == 0 {
                while !release.load(Ordering::SeqCst) {
                    std::thread::sleep(Duration::from_millis(5));
                }
                return Ok(vec!["STALE".into()]);
            }
            Ok(vec![format!("FRESH-{}", source.id())])
        }))
    }

    /// Ticks the loader until it reports something other than `Loading`.
    fn settle(loader: &mut StationLoader, ctx: &egui::Context, svc: &mut StationService) -> Option<LoaderEvent> {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            let resolved = svc.poll(ctx);
            if let Some(e) = loader.tick(ctx, svc, &resolved)
                && e != LoaderEvent::Loading
            {
                return Some(e);
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        None
    }

    #[test]
    fn a_late_answer_of_a_superseded_lookup_is_ignored() {
        // Away from e-CALLISTO and back while its first lookup hangs: only the
        // newest lookup counts.
        let ctx = egui::Context::default();
        let release = Arc::new(AtomicBool::new(false));
        let calls = Arc::new(Mutex::new(Vec::new()));
        let mut svc = hanging_service(release.clone(), calls.clone());
        let mut loader = StationLoader::default();
        assert_eq!(loader.request(&svc, &[day()]), Some(LoaderEvent::Loading));
        let deadline = Instant::now() + Duration::from_secs(5);
        while calls.lock().unwrap().is_empty() && Instant::now() < deadline {
            loader.tick(&ctx, &mut svc, &[]);
            std::thread::sleep(Duration::from_millis(10));
        }

        assert_eq!(loader.set_source(&svc, Source::Astrodoncel), Some(LoaderEvent::Loading));
        assert_eq!(settle(&mut loader, &ctx, &mut svc), Some(LoaderEvent::Loaded(vec!["FRESH-astrodoncel".into()])));
        assert!(loader.summary.starts_with("Astrodoncel: 1 station has data"), "{}", loader.summary);

        assert_eq!(loader.set_source(&svc, Source::Ecallisto), Some(LoaderEvent::Loading));
        assert_eq!(settle(&mut loader, &ctx, &mut svc), Some(LoaderEvent::Loaded(vec!["FRESH-ecallisto".into()])));
        assert_eq!(calls.lock().unwrap().iter().filter(|k| k.0 == Source::Ecallisto).count(), 2);

        release.store(true, Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(100));
        let resolved = svc.poll(&ctx);
        assert!(resolved.is_empty(), "the superseded answer must be dropped");
        assert_eq!(svc.cached((Source::Ecallisto, day())), Some(&["FRESH-ecallisto".to_owned()][..]));
        assert_eq!(loader.state, LoaderState::Loaded);
    }

    #[test]
    fn a_running_lookup_is_shared_unless_a_newer_one_is_asked_for() {
        let ctx = egui::Context::default();
        let calls = Arc::new(Mutex::new(Vec::new()));
        let seen = calls.clone();
        let mut svc = StationService::new(Arc::new(move |s, d| {
            seen.lock().unwrap().push((s, d));
            std::thread::sleep(Duration::from_millis(50));
            Ok(Vec::new())
        }));
        let key = (Source::Astrodoncel, day());
        svc.fetch(&ctx, key, 0);
        svc.fetch(&ctx, key, 0);
        std::thread::sleep(Duration::from_millis(20));
        assert_eq!(calls.lock().unwrap().len(), 1);
        let newer = svc.next_generation();
        svc.fetch(&ctx, key, newer);
        std::thread::sleep(Duration::from_millis(150));
        assert_eq!(calls.lock().unwrap().len(), 2);
        assert_eq!(svc.poll(&ctx), vec![key], "only the newest answer resolves the day");
    }

    #[test]
    fn a_failed_lookup_names_its_server_and_a_kept_station_survives() {
        let ctx = egui::Context::default();
        let mut svc = StationService::new(Arc::new(|s, _| match s {
            Source::Astrodoncel => Err("HTTP 503".into()),
            Source::Ecallisto => Ok(vec!["ALPHA".into(), "SHARED".into()]),
        }));
        let mut loader = StationLoader::default();
        let mut combo = StationCombo::default();
        loader.request(&svc, &[day()]);
        let e = settle(&mut loader, &ctx, &mut svc).unwrap();
        combo.apply(&e);
        combo.set_text("SHARED");
        combo.remember_current();
        loader.set_source(&svc, Source::Astrodoncel);
        assert!(matches!(settle(&mut loader, &ctx, &mut svc), Some(LoaderEvent::Failed(_))));
        assert_eq!(loader.summary, "Astrodoncel: could not load the stations for 2024-01-02.");
        assert!(loader.retry);
        let back = loader.set_source(&svc, Source::Ecallisto).unwrap();
        combo.apply(&back);
        assert_eq!(combo.text(), "SHARED");
    }
}
