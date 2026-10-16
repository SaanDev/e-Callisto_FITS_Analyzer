//! The Multi-Station Event tab: several stations' files that start inside
//! a UTC window (`CallistoDownloaderApp._build_event_tab` and handlers).

use callisto_core::radio::downloader::{self, Candidate, EventSearch, MAX_EVENT_STATION_DAYS, station_key};
use chrono::{NaiveDateTime, Utc};
use egui::{Id, Rect, Ui};

use super::batch::{DownloadBatch, pick_folder};
use super::jobs::Job;
use super::single::section;
use super::stations::{LoaderEvent, StationLoader, StationService};
use super::{Shared, at, lay_out};
use crate::session::NoticeKind;
use crate::ui::itemview::{self, Check, Event, Row, Style, ViewState};
use crate::ui::qlayout::{BoxLayout, Container, GridLayout, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::qt;

type SearchJob = Job<(usize, usize), Result<EventSearch, String>>;

pub struct EventTab {
    pub start: NaiveDateTime,
    pub stop: NaiveDateTime,
    filter: String,
    loader: StationLoader,
    station_days: Option<Vec<chrono::NaiveDate>>,
    /// Station names and whether each is checked.
    stations: Vec<(String, bool)>,
    checked_keys: std::collections::HashSet<String>,
    station_view: ViewState,
    results: Vec<(Candidate, bool)>,
    results_view: ViewState,
    search: Option<SearchJob>,
    downloads: Option<DownloadBatch>,
    progress: Option<qt::Progress>,
    status: String,
}

impl Default for EventTab {
    fn default() -> Self {
        Self::new()
    }
}

impl EventTab {
    pub fn new() -> Self {
        // The window ends now (UTC), an hour long.
        let now = Utc::now().naive_utc();
        let now = now.with_nanosecond(0).unwrap_or(now);
        EventTab {
            start: now - chrono::Duration::hours(1),
            stop: now,
            filter: String::new(),
            loader: StationLoader::default(),
            station_days: None,
            stations: Vec::new(),
            checked_keys: Default::default(),
            station_view: ViewState::default(),
            results: Vec::new(),
            results_view: ViewState::default(),
            search: None,
            downloads: None,
            progress: None,
            status: "Select stations and a UTC event time window.".into(),
        }
    }

    pub fn is_busy(&self) -> bool {
        self.search.is_some() || self.downloads.is_some()
    }

    /// `_request_event_stations`.
    pub fn request_stations(&mut self, svc: &StationService) {
        let days = match downloader::utc_archive_dates_for_window(self.start, self.stop) {
            Ok(d) => d,
            Err(_) => return self.clear_days("Stop time is before start time."),
        };
        if days.len() > MAX_EVENT_STATION_DAYS {
            return self.clear_days(&format!(
                "The window spans {} UTC days. Narrow it to {MAX_EVENT_STATION_DAYS} days or fewer to list its stations.",
                days.len()
            ));
        }
        if self.station_days.as_ref() != Some(&days) {
            self.station_days = Some(days.clone());
            if let Some(e) = self.loader.request(svc, &days) {
                self.apply(e);
            }
        }
    }

    fn clear_days(&mut self, message: &str) {
        self.station_days = None;
        self.loader.cancel();
        self.fill(&[]);
        self.loader.show_message(message);
    }

    fn apply(&mut self, e: LoaderEvent) {
        match e {
            LoaderEvent::Loading => self.fill(&[]),
            LoaderEvent::Loaded(s) => self.fill(&s),
            LoaderEvent::Failed(_) => {}
        }
    }

    /// `_fill_event_station_list`: checked stations stay checked.
    fn fill(&mut self, stations: &[String]) {
        self.stations = stations.iter().map(|s| (s.clone(), self.checked_keys.contains(&station_key(s)))).collect();
        self.station_view = ViewState::default();
    }

    fn visible_stations(&self) -> Vec<usize> {
        let q = self.filter.trim().to_lowercase();
        (0..self.stations.len()).filter(|&i| q.is_empty() || self.stations[i].0.to_lowercase().contains(&q)).collect()
    }

    pub fn tick(&mut self, sh: &mut Shared) {
        if let Some(e) = self.loader.tick(&sh.ctx, sh.svc, sh.resolved) {
            self.apply(e);
        }
        if let Some(job) = &mut self.search {
            let (steps, done) = job.poll();
            for (v, m) in steps {
                if let Some(p) = &mut self.progress {
                    p.max = m as i64;
                    p.value = v as i64;
                }
            }
            if let Some(r) = done {
                self.search = None;
                self.display(r, sh);
            }
        }
        if let Some(batch) = &mut self.downloads {
            batch.poll();
            if let Some(p) = &mut self.progress {
                p.value = batch.done as i64;
            }
            if batch.finished() {
                let batch = self.downloads.take().unwrap();
                self.progress = None;
                sh.out.cache_changed = true;
                // `finish_event_download`.
                let ok = batch.success_paths.len();
                let failed = batch.failures.len();
                let mut status = format!("Downloaded {ok} of {} selected FITS file(s).", batch.total);
                if failed > 0 {
                    status.push_str(&format!(" {failed} failed."));
                }
                self.status = status.clone();
                let mut details = vec![status];
                if failed > 0 {
                    details.push(String::new());
                    details.push("Failures:".into());
                    details.extend(batch.failures.iter().take(8).map(|(f, e)| format!("{f}: {}", if e.is_empty() { "download failed" } else { e })));
                    if failed > 8 {
                        details.push("...".into());
                    }
                }
                sh.out.notice(NoticeKind::Information, "Download Complete", details.join("\n"));
            }
        }
    }

    /// `display_event_search_results`.
    fn display(&mut self, r: Result<EventSearch, String>, sh: &mut Shared) {
        self.progress = None;
        let res = match r {
            Ok(r) => r,
            Err(e) => return sh.out.notice(NoticeKind::Critical, "Search Error", e),
        };
        self.results = res.candidates.iter().map(|c| (c.clone(), true)).collect();
        self.results_view = ViewState::default();
        let mut status = if res.candidates.is_empty() {
            "No matching FITS files were found for the selected stations and time window.".to_owned()
        } else {
            format!("Found {} matching FITS file(s).", res.candidates.len())
        };
        if !res.missing_stations.is_empty() {
            let shown = res.missing_stations.iter().take(8).cloned().collect::<Vec<_>>().join(", ");
            let more = if res.missing_stations.len() > 8 { "..." } else { "" };
            status.push_str(&format!(" No matches for: {shown}{more}."));
        }
        if !res.warnings.is_empty() {
            status.push_str(&format!(" Warnings: {}", res.warnings.iter().take(3).cloned().collect::<Vec<_>>().join("; ")));
        }
        self.status = status;
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let stations = BoxLayout::v([9; 4], 8)
            .widget(qt::line_edit_spec("filter"))
            .widget(super::status_label(ui, "stations_status", &self.loader.summary))
            .widget(qt::view_spec("station_list").min_height(260))
            .add(BoxLayout::h([0; 4], 8).widget(qt::button_spec(ui, "st_all", "Select All", false)).widget(qt::button_spec(ui, "st_clear", "Clear", false)).into_item());
        let station_group = Item::Container(Container {
            spec: WidgetSpec::new("stations", Size::new(0, 0), Policy::Preferred, Policy::Preferred).min_width(280).max_width(360),
            margins: [11, 23, 11, 11],
            layout: Box::new(stations.into()),
        });
        let start_label = qt::label_spec(ui, "start_label", "Start (UTC):");
        let stop_label = qt::label_spec(ui, "stop_label", "Stop (UTC):");
        let window = GridLayout::new([9; 4], 8, 8)
            .column_stretch(1, 1)
            .widget(start_label, 0, 0)
            .widget(qt::date_spec(ui, "start", "9999-12-31 23:59:59").min_width(190), 0, 1)
            .widget(stop_label, 1, 0)
            .widget(qt::date_spec(ui, "stop", "9999-12-31 23:59:59").min_width(190), 1, 1)
            .widget_span(qt::button_spec(ui, "search", "Search Matching FITS", true), 0, 2, 2, 1);
        let results = BoxLayout::v([10, 12, 10, 10], 6).widget(qt::view_spec("table"));
        let mut actions = BoxLayout::h([9; 4], 8);
        for (id, text, primary) in ACTIONS {
            actions = actions.widget(qt::button_spec(ui, id, text, primary));
        }
        let actions = actions.stretch(1);
        let right = BoxLayout::v([0; 4], 8)
            .add(section("window", window))
            .add_stretched(section("results", results), 1)
            .add(section("actions", actions))
            .widget(qt::progress_spec("progress").hidden(self.progress.is_none()))
            .widget(super::status_label(ui, "status", &self.status));
        let right_panel = Item::Container(Container {
            spec: WidgetSpec::new("right", Size::new(0, 0), Policy::Preferred, Policy::Preferred),
            margins: [0; 4],
            layout: Box::new(right.into()),
        });
        let row = BoxLayout::h([0; 4], 10).add(station_group).add_stretched(right_panel, 1);
        BoxLayout::v([2, 10, 2, 2], 8).add_stretched(row.into_item(), 1).into()
    }

    pub fn show(&mut self, ui: &mut Ui, page: Rect, sh: &mut Shared) {
        let p = sh.p;
        let (g, o) = lay_out(&self.layout(ui), page);
        qt::section_box(ui, at(&g, o, "stations"), "Stations", p);
        qt::section_box(ui, at(&g, o, "window"), "Event Window", p);
        qt::section_box(ui, at(&g, o, "results"), "Matching FITS Files", p);
        qt::section_box(ui, at(&g, o, "actions"), "Actions", p);

        qt::line_edit(ui, at(&g, o, "filter"), Id::new("event_filter"), &mut self.filter, "Filter stations", true, p);
        let status_rect = at(&g, o, "stations_status");
        if self.loader.retry {
            if qt::status_with_link(ui, status_rect, Id::new("event_retry"), &self.loader.summary, "Retry", p)
                && let Some(e) = self.loader.reload(sh.svc)
            {
                self.apply(e);
            }
        } else {
            qt::status(ui, status_rect, &self.loader.summary, p);
        }
        let rows: Vec<Row> = self.stations.iter().map(|(s, c)| Row::new(vec![s.clone()]).checked(Check::from_bool(*c))).collect();
        let visible = self.visible_stations();
        let list = at(&g, o, "station_list");
        let style = Style::station_list();
        let w = itemview::viewport(list, &style).width();
        for e in itemview::item_view(ui, list, Id::new("event_stations"), &[], &[w], &rows, &visible, &style, &mut self.station_view, true, p) {
            if let Event::Toggled(i) = e {
                self.stations[i].1 ^= true;
                let key = station_key(&self.stations[i].0);
                if self.stations[i].1 {
                    self.checked_keys.insert(key);
                } else {
                    self.checked_keys.remove(&key);
                }
            }
        }
        if qt::button(ui, at(&g, o, "st_all"), Id::new("event_st_all"), "Select All", false, true, p).clicked() {
            for i in self.visible_stations() {
                self.stations[i].1 = true;
                self.checked_keys.insert(station_key(&self.stations[i].0));
            }
        }
        if qt::button(ui, at(&g, o, "st_clear"), Id::new("event_st_clear"), "Clear", false, true, p).clicked() {
            for s in &mut self.stations {
                s.1 = false;
            }
            self.checked_keys.clear();
        }

        qt::label(ui, at(&g, o, "start_label"), "Start (UTC):", p.text);
        qt::label(ui, at(&g, o, "stop_label"), "Stop (UTC):", p.text);
        let mut changed = qt::datetime_edit(ui, at(&g, o, "start"), Id::new("event_start"), &mut self.start, "yyyy-MM-dd HH:mm:ss", true, p);
        changed |= qt::datetime_edit(ui, at(&g, o, "stop"), Id::new("event_stop"), &mut self.stop, "yyyy-MM-dd HH:mm:ss", true, p);
        if changed {
            self.request_stations(sh.svc);
        }
        if qt::button(ui, at(&g, o, "search"), Id::new("event_search"), "Search Matching FITS", true, self.search.is_none() && self.downloads.is_none(), p).clicked() {
            self.search_fits(sh);
        }

        // Results: check, station, time, filename (stretch), receiver.
        let table = at(&g, o, "table");
        let style = Style::results_table(false);
        let rows: Vec<Row> = self
            .results
            .iter()
            .map(|(c, on)| {
                let mut r = Row::new(vec![String::new(), c.station.clone(), c.utc_text(), c.filename.clone(), c.receiver_id.clone()]).checked(Check::from_bool(*on));
                r.tooltip = None;
                r
            })
            .collect();
        let headers = ["", "Station", "UTC Time", "Filename", "Receiver"];
        let col = |i: usize, check: bool| itemview::content_width(ui, headers[i], rows.iter().map(|r| r.cells[i].clone()), check, false);
        let vp = itemview::viewport(table, &style);
        let width = vp.width() - if itemview::needs_scrollbar(table, &style, rows.len()) { 10.0 } else { 0.0 };
        let (w0, w1, w2, w4) = (col(0, !rows.is_empty()), col(1, false), col(2, false), col(4, false));
        let w3 = (width - w0 - w1 - w2 - w4).max(0.0);
        let visible: Vec<usize> = (0..rows.len()).collect();
        for e in itemview::item_view(ui, table, Id::new("event_results"), &headers, &[w0, w1, w2, w3, w4], &rows, &visible, &style, &mut self.results_view, true, p) {
            if let Event::Toggled(i) = e {
                self.results[i].1 ^= true;
            }
        }
        let has = !self.results.is_empty();
        for (id, text, primary) in ACTIONS {
            let enabled = has && (id != "download" || self.downloads.is_none());
            if qt::button(ui, at(&g, o, id), Id::new(("event", id)), text, primary, enabled, p).clicked() {
                self.action(id, sh);
            }
        }
        if let Some(prog) = &self.progress {
            qt::progress_bar(ui, at(&g, o, "progress"), prog, p);
        }
        qt::status(ui, at(&g, o, "status"), &self.status, p);
    }

    fn checked_results(&self) -> Vec<Candidate> {
        self.results.iter().filter(|r| r.1).map(|r| r.0.clone()).collect()
    }

    /// Scripted steps (`DownloaderDialog::run_script`).
    pub(crate) fn script(&mut self, args: &[&str], sh: &mut Shared) {
        match args {
            ["check", name] => {
                for s in &mut self.stations {
                    if s.0 == *name {
                        s.1 = true;
                        self.checked_keys.insert(station_key(&s.0));
                    }
                }
            }
            ["search"] => self.search_fits(sh),
            _ => {}
        }
    }

    /// `search_event_fits`.
    fn search_fits(&mut self, sh: &mut Shared) {
        let stations: Vec<String> = self.stations.iter().filter(|s| s.1).map(|s| s.0.clone()).collect();
        if stations.is_empty() {
            return sh.out.notice(NoticeKind::Warning, "No Stations", "Please select at least one station.");
        }
        if self.stop < self.start {
            return sh.out.notice(NoticeKind::Warning, "Invalid Time Window", "Stop time must be after start time.");
        }
        if self.search.is_some() {
            return sh.out.notice(NoticeKind::Information, "Search In Progress", "An event search is already running.");
        }
        self.results.clear();
        self.status = "Searching archive listings...".into();
        self.progress = Some(qt::Progress::busy());
        let (start, stop) = (self.start, self.stop);
        self.search = Some(Job::spawn(&sh.ctx, move |r| downloader::search_event(start, stop, &stations, |v, m| r.send((v, m)))));
    }

    fn action(&mut self, id: &str, sh: &mut Shared) {
        match id {
            "res_all" | "res_clear" => {
                for r in &mut self.results {
                    r.1 = id == "res_all";
                }
            }
            "compare" => {
                let sel = self.checked_results();
                if sel.is_empty() {
                    return sh.out.notice(NoticeKind::Warning, "No Selection", "Please select files to compare.");
                }
                sh.out.compare = Some(sel.into_iter().map(|c| c.url).collect());
            }
            "import" => {
                let sel = self.checked_results();
                if sel.is_empty() {
                    return sh.out.notice(NoticeKind::Warning, "No Selection", "Please select files to import.");
                }
                sh.out.import = Some(sel.into_iter().map(|c| c.url).collect());
            }
            "download" => {
                let sel = self.checked_results();
                if sel.is_empty() {
                    return sh.out.notice(NoticeKind::Warning, "No Selection", "Please select files to download.");
                }
                let Some(dir) = pick_folder("Select Download Folder") else { return };
                self.progress = Some(qt::Progress::range(sel.len() as i64));
                self.status = "Downloading selected event FITS files...".into();
                self.downloads = Some(DownloadBatch::start(&sh.ctx, sh.cache_root, &sel, &dir));
            }
            _ => {}
        }
    }
}

const ACTIONS: [(&str, &str, bool); 5] = [
    ("res_all", "Select All", false),
    ("res_clear", "Clear Selection", false),
    ("download", "Download Selected", true),
    ("import", "Import", false),
    ("compare", "Compare", false),
];

use chrono::Timelike;
