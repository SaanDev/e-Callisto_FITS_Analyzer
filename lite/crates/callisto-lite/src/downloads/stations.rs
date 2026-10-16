//! Station pickers that follow the archive (`archive_stations.py`): one
//! application-wide cache of each UTC day's stations, shared by the
//! downloader's tabs and kept across reopenings, and a debounced loader per
//! picker that reports the union over its days.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use std::sync::mpsc::{Receiver, Sender, channel};
use std::time::{Duration, Instant};

use callisto_core::radio::downloader::{describe_days, merge_station_lists};
use chrono::{NaiveDate, Utc};

/// `LOOKUP_DELAY_MS`: coalesce the bursts of typing or stepping a date.
pub const LOOKUP_DELAY: Duration = Duration::from_millis(400);
/// `RECENT_DAY_TTL_SECONDS`: today and yesterday still receive uploads.
pub const RECENT_DAY_TTL: Duration = Duration::from_secs(600);
const MAX_THREADS: usize = 4;

pub type Fetch = Arc<dyn Fn(NaiveDate) -> Result<Vec<String>, String> + Send + Sync>;

/// `_ArchiveStationService`.
pub struct StationService {
    stations: HashMap<NaiveDate, (Instant, Vec<String>)>,
    errors: HashMap<NaiveDate, String>,
    pending: HashSet<NaiveDate>,
    queue: VecDeque<NaiveDate>,
    running: usize,
    tx: Sender<(NaiveDate, Result<Vec<String>, String>)>,
    rx: Receiver<(NaiveDate, Result<Vec<String>, String>)>,
    fetch: Fetch,
}

impl StationService {
    pub fn new(fetch: Fetch) -> Self {
        let (tx, rx) = channel();
        StationService {
            stations: HashMap::new(),
            errors: HashMap::new(),
            pending: HashSet::new(),
            queue: VecDeque::new(),
            running: 0,
            tx,
            rx,
            fetch,
        }
    }

    /// Reads the archive (`fetch_day_stations`).
    pub fn online() -> Self {
        Self::new(Arc::new(|day| callisto_core::radio::archive::fetch_day_stations(day).map_err(|e| e.to_string())))
    }

    fn is_recent(day: NaiveDate) -> bool {
        day >= Utc::now().date_naive() - chrono::Duration::days(1)
    }

    pub fn cached(&self, day: NaiveDate) -> Option<&[String]> {
        let (at, stations) = self.stations.get(&day)?;
        if Self::is_recent(day) && at.elapsed() > RECENT_DAY_TTL {
            return None;
        }
        Some(stations)
    }

    pub fn error(&self, day: NaiveDate) -> Option<&str> {
        self.errors.get(&day).map(String::as_str)
    }

    pub fn fetch(&mut self, ctx: &egui::Context, day: NaiveDate) {
        if !self.pending.insert(day) {
            return;
        }
        self.errors.remove(&day);
        self.queue.push_back(day);
        self.start(ctx);
    }

    fn start(&mut self, ctx: &egui::Context) {
        while self.running < MAX_THREADS {
            let Some(day) = self.queue.pop_front() else { break };
            self.running += 1;
            let (tx, fetch, ctx) = (self.tx.clone(), self.fetch.clone(), ctx.clone());
            std::thread::spawn(move || {
                let result = fetch(day);
                let _ = tx.send((day, result));
                ctx.request_repaint();
            });
        }
    }

    pub fn forget(&mut self, days: &[NaiveDate]) {
        for d in days {
            self.stations.remove(d);
            self.errors.remove(d);
        }
    }

    /// Stores finished lookups; returns the days resolved since last time.
    pub fn poll(&mut self, ctx: &egui::Context) -> Vec<NaiveDate> {
        let mut resolved = Vec::new();
        while let Ok((day, result)) = self.rx.try_recv() {
            self.running -= 1;
            self.pending.remove(&day);
            match result {
                Ok(s) => {
                    self.stations.insert(day, (Instant::now(), s));
                }
                Err(e) => {
                    self.errors.insert(day, if e.is_empty() { "Error".into() } else { e });
                }
            }
            resolved.push(day);
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
    timer: Option<Instant>,
    /// The availability label: plain text, and whether it ends in "Retry".
    pub summary: String,
    pub retry: bool,
    pub tooltip: String,
}

impl StationLoader {
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
        self.show_message(&format!("Loading the stations with data on {} (UTC)...", describe_days(&self.days)));
        Some(LoaderEvent::Loading)
    }

    /// `reload()`: read the days again, ignoring cached lists.
    pub fn reload(&mut self, svc: &mut StationService) -> Option<LoaderEvent> {
        let days = self.days.clone();
        svc.forget(&days);
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
        let lists: Option<Vec<&[String]>> = self.days.iter().map(|d| svc.cached(*d)).collect();
        let lists = lists?;
        self.timer = None;
        self.state = LoaderState::Loaded;
        let stations = merge_station_lists(lists);
        let days = describe_days(&self.days);
        let count = stations.len();
        if count > 0 {
            let noun = if count == 1 { "station has" } else { "stations have" };
            self.show_message(&format!("{count} {noun} data on {days} (UTC)."));
        } else {
            self.show_message(&format!("No station has published data for {days} (UTC)."));
        }
        Some(LoaderEvent::Loaded(stations))
    }

    /// Runs the debounce timer and picks up resolved days.
    pub fn tick(&mut self, ctx: &egui::Context, svc: &mut StationService, resolved: &[NaiveDate]) -> Option<LoaderEvent> {
        if let Some(t) = self.timer {
            if Instant::now() >= t {
                self.timer = None;
                for d in self.days.clone() {
                    if svc.cached(d).is_none() {
                        svc.fetch(ctx, d);
                    }
                }
            } else {
                ctx.request_repaint_after(t - Instant::now());
            }
        }
        if self.state != LoaderState::Loading {
            return None;
        }
        for day in resolved {
            if !self.days.contains(day) {
                continue;
            }
            if let Some(err) = svc.error(*day)
                && svc.cached(*day).is_none()
            {
                let err = err.to_owned();
                self.timer = None;
                self.state = LoaderState::Failed;
                self.summary = format!("Could not load the stations for {}.", describe_days(&self.days));
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
