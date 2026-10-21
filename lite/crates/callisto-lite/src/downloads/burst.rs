//! The Burst List tab (`src/ui/radio/burst_list_tab.py`): browse the
//! Monstein catalog, pick a burst, find the FITS files covering it, then
//! preview, download, import or compare them.

use std::sync::atomic::Ordering;

use callisto_core::radio::burst_list::{
    self, BURST_LIST_BASE_URL, BurstEvent, BurstFits, BurstListError, BurstListResult, TYPE_FAMILIES,
};
use callisto_core::radio::downloader::{self, Candidate, PrepareError, PreviewResult};
use chrono::{NaiveDate, Utc};
use egui::{Id, Rect, Sense, Ui};

use super::batch::{DownloadBatch, pick_folder};
use super::jobs::Job;
use super::stations::{LoaderEvent, StationLoader};
use super::{Shared, at, lay_out};
use crate::session::NoticeKind;
use crate::ui::itemview::{self, Check, Event, Row, Style, ViewState};
use crate::ui::qlayout::{BoxLayout, Container, GridLayout, Item, Layout, Policy, Size, Splitter, WidgetSpec};
use crate::ui::qt;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum JobKind {
    Catalog,
    Fits,
    Preview,
    Download,
}

enum Work {
    Catalog(Job<(usize, usize, String), Result<BurstListResult, BurstListError>>),
    Fits(Job<(usize, usize, String), Result<BurstFits, BurstListError>>),
    Preview(Job<(usize, usize, String), Result<PreviewResult, PrepareError>>),
}

pub struct BurstTab {
    pub start: NaiveDate,
    pub end: NaiveDate,
    events: Vec<BurstEvent>,
    warnings: Vec<String>,
    types: Vec<(String, String)>,
    type_current: usize,
    station_filter: String,
    min_stations: i32,
    text_filter: String,
    /// Indices into `events`, as filtered.
    shown: Vec<usize>,
    catalog_view: ViewState,
    catalog_status: String,
    catalog_tip: String,
    selection_label: String,
    /// The FITS station combo: `(text, data)` items and the edited text.
    station_items: Vec<(String, String)>,
    station_text: String,
    padding: i32,
    loader: StationLoader,
    fits: Vec<(Candidate, bool)>,
    fits_view: ViewState,
    fits_status: String,
    fits_tip: String,
    activity: String,
    progress: qt::Progress,
    detail: String,
    work: Option<Work>,
    kind: Option<JobKind>,
    outcome: &'static str,
    cancel_requested: bool,
    downloads: Option<DownloadBatch>,
    split: Option<Vec<i32>>,
}

impl Default for BurstTab {
    fn default() -> Self {
        Self::new()
    }
}

fn type_items(events: &[BurstEvent]) -> Vec<(String, String)> {
    let mut names: std::collections::BTreeSet<String> = TYPE_FAMILIES.iter().map(|s| s.to_string()).collect();
    names.extend(events.iter().filter(|e| !e.burst_type.is_empty()).map(|e| e.burst_type.clone()));
    let mut out = vec![("All types".to_owned(), String::new())];
    out.extend(names.into_iter().map(|n| (n.clone(), n)));
    out
}

impl BurstTab {
    pub fn new() -> Self {
        let today = Utc::now().date_naive();
        BurstTab {
            start: today - chrono::Duration::days(30),
            end: today,
            events: Vec::new(),
            warnings: Vec::new(),
            types: type_items(&[]),
            type_current: 0,
            station_filter: String::new(),
            min_stations: 0,
            text_filter: String::new(),
            shown: Vec::new(),
            // QHeaderView starts on a descending indicator; filling the
            // table sorts it ascending (`sortItems(0, AscendingOrder)`).
            catalog_view: ViewState { sort: Some((0, false)), ..Default::default() },
            catalog_status: "Choose a UTC date range and load the event list.".into(),
            catalog_tip: String::new(),
            selection_label: "Select a burst above, then find the FITS files covering it.".into(),
            station_items: vec![("Choose a station".into(), String::new())],
            station_text: "Choose a station".into(),
            padding: 2,
            loader: StationLoader::default(),
            fits: Vec::new(),
            fits_view: ViewState::default(),
            fits_status: "Catalog times are UTC with minute precision. Files are matched by time and station.".into(),
            fits_tip: String::new(),
            activity: "Ready \u{2014} load events for the selected dates.".into(),
            progress: qt::Progress { format: "Ready".into(), ..Default::default() },
            detail: String::new(),
            work: None,
            kind: None,
            outcome: "",
            cancel_requested: false,
            downloads: None,
            split: None,
        }
    }

    pub fn is_busy(&self) -> bool {
        self.work.is_some() || self.downloads.is_some()
    }

    pub fn request_cancel(&mut self) {
        let job_cancel = match &self.work {
            Some(Work::Catalog(j)) => {
                j.request_cancel();
                true
            }
            Some(Work::Fits(j)) => {
                j.request_cancel();
                true
            }
            Some(Work::Preview(j)) => {
                j.request_cancel();
                true
            }
            None => false,
        };
        if job_cancel && !self.cancel_requested {
            self.cancel_requested = true;
            self.activity = "Cancelling \u{2014} waiting for the current operation to finish\u{2026}".into();
        }
    }

    /// `_dates_changed`.
    pub fn dates_changed(&mut self) {
        self.events.clear();
        self.warnings.clear();
        self.shown.clear();
        self.clear_fits();
        self.catalog_status = "Date range changed. Load Events to refresh the catalog.".into();
        self.reset_progress("Dates changed \u{2014} load events to refresh the catalog.");
    }

    fn clear_fits(&mut self) {
        self.fits.clear();
        self.fits_view = ViewState::default();
        self.fits_status = "Find FITS to load files for the selected burst and station.".into();
    }

    fn reset_progress(&mut self, message: &str) {
        self.progress = qt::Progress { format: "Ready".into(), ..Default::default() };
        self.detail.clear();
        self.activity = message.to_owned();
    }

    fn selected_event(&self) -> Option<&BurstEvent> {
        self.catalog_view.current.and_then(|i| self.events.get(i))
    }

    fn start_work(&mut self, kind: JobKind, work: Work) {
        self.kind = Some(kind);
        self.outcome = "";
        self.cancel_requested = false;
        self.work = Some(work);
        self.progress = qt::Progress::busy();
        self.detail = "\u{2026}".into();
        self.activity = match kind {
            JobKind::Catalog => "Loading the event catalog\u{2026}",
            JobKind::Fits => "Searching the FITS archive\u{2026}",
            _ => "Preparing spectrogram preview\u{2026}",
        }
        .into();
    }

    fn on_progress(&mut self, current: usize, total: usize, message: String) {
        self.progress.min = 0;
        self.progress.max = total as i64;
        self.progress.value = current as i64;
        self.progress.format = "%p%".into();
        self.detail =
            (100 * current).checked_div(total).map_or_else(|| "\u{2026}".into(), |v| format!("{}%", v.min(100)));
        if !self.cancel_requested {
            self.activity = message;
        }
    }

    /// `_finish_progress`.
    fn finish_progress(&mut self) {
        self.progress.min = 0;
        self.progress.max = 100;
        if self.outcome == "error" || self.outcome == "cancelled" {
            let text = if self.outcome == "cancelled" { "Cancelled" } else { "Failed" };
            self.progress.value = 0;
            self.progress.format = text.into();
            self.detail = text.into();
            self.activity = if self.kind == Some(JobKind::Catalog) {
                self.catalog_status.clone()
            } else {
                self.fits_status.clone()
            };
        } else {
            self.progress.value = 100;
            self.progress.format = "%p%".into();
            self.detail = "100%".into();
            self.activity = match self.kind {
                Some(JobKind::Catalog) => "Event catalog loaded.",
                Some(JobKind::Fits) => "FITS search complete.",
                Some(JobKind::Preview) => "Spectrogram preview ready.",
                _ => "Complete.",
            }
            .into();
        }
    }

    pub fn tick(&mut self, sh: &mut Shared) {
        if let Some(e) = self.loader.tick(&sh.ctx, sh.svc, sh.resolved) {
            self.add_archive_stations(e);
        }
        let mut finished = false;
        match &mut self.work {
            Some(Work::Catalog(job)) => {
                let (msgs, done) = job.poll();
                for (a, b, m) in msgs {
                    self.on_progress(a, b, m);
                }
                if let Some(r) = done {
                    self.display_catalog(r);
                    finished = true;
                }
            }
            Some(Work::Fits(job)) => {
                let (msgs, done) = job.poll();
                for (a, b, m) in msgs {
                    self.on_progress(a, b, m);
                }
                if let Some(r) = done {
                    self.display_fits(r);
                    finished = true;
                }
            }
            Some(Work::Preview(job)) => {
                let (msgs, done) = job.poll();
                for (a, b, m) in msgs {
                    self.on_progress(a, b, m);
                }
                if let Some(r) = done {
                    self.display_preview(r, sh);
                    finished = true;
                }
            }
            None => {}
        }
        if finished {
            self.work = None;
            self.finish_progress();
        }
        if let Some(batch) = &mut self.downloads {
            let msgs = batch.poll();
            let total = batch.total;
            let sum: f64 = batch.fractions.values().sum();
            let done = batch.done;
            let last = msgs.iter().next_back().map(|m| match m {
                super::batch::TaskMsg::Progress(_, _, msg) => msg.clone(),
                super::batch::TaskMsg::Done { filename, success, .. } => {
                    format!("{} {filename}", if *success { "Saved" } else { "Failed" })
                }
            });
            self.progress.value = (1000.0 * sum).round() as i64;
            self.detail = format!("{}%", (100.0 * sum / total as f64) as i64);
            if let Some(m) = last {
                self.activity = format!("{m} \u{b7} {done}/{total} files complete");
            }
            if batch.finished() {
                let batch = self.downloads.take().unwrap();
                self.progress = qt::Progress { min: 0, max: 100, value: 100, format: "%p%".into() };
                self.detail = "100%".into();
                let failed = batch.failures.len();
                let mut status = format!("Downloaded {} of {total} selected FITS file(s).", total - failed);
                if failed > 0 {
                    status.push_str(&format!(" {failed} failed."));
                }
                self.activity = status.clone();
                self.fits_status = status.clone();
                let lines: Vec<String> = batch.failures.iter().map(|(f, e)| format!("{f}: {e}")).collect();
                self.fits_tip = lines.join("\n");
                sh.out.cache_changed = true;
                if failed > 0 {
                    sh.out.notice(
                        NoticeKind::Warning,
                        "Download Complete",
                        format!("{status}\n\n{}", lines.iter().take(8).cloned().collect::<Vec<_>>().join("\n")),
                    );
                }
            }
        }
    }

    fn display_catalog(&mut self, r: Result<BurstListResult, BurstListError>) {
        match r {
            Err(BurstListError::Cancelled) => {
                self.outcome = "cancelled";
                self.catalog_status = "Catalog loading cancelled.".into();
            }
            Err(BurstListError::Failed(e)) => {
                self.outcome = "error";
                self.catalog_status = format!("Could not load the burst list: {e}");
            }
            Ok(res) => {
                self.events = res.events;
                self.warnings = res.warnings;
                let selected = self.types[self.type_current].1.clone();
                self.types = type_items(&self.events);
                self.type_current = self.types.iter().position(|t| t.1 == selected).unwrap_or(0);
                self.apply_filters();
            }
        }
    }

    /// `apply_filters`.
    fn apply_filters(&mut self) {
        let filtered = burst_list::filter_burst_events(
            &self.events,
            &self.types[self.type_current].1,
            &self.station_filter,
            &self.text_filter,
            None,
            None,
        )
        .unwrap_or_default();
        let min = self.min_stations.max(0) as usize;
        let keys: Vec<String> =
            filtered.iter().filter(|e| e.stations.len() >= min).map(|e| e.raw_line.clone() + &e.source_url).collect();
        self.shown = (0..self.events.len())
            .filter(|&i| keys.contains(&(self.events[i].raw_line.clone() + &self.events[i].source_url)))
            .collect();
        self.catalog_view = ViewState { sort: Some((0, true)), ..Default::default() };
        self.sort_catalog();
        let mut status = format!("{} of {} events match the filters.", self.shown.len(), self.events.len());
        if self.events.is_empty() {
            status = "No burst events were available for this date range.".into();
        }
        if !self.warnings.is_empty() {
            status.push(' ');
            status.push_str(&self.warnings.iter().take(2).cloned().collect::<Vec<_>>().join(" "));
            if self.warnings.len() > 2 {
                status.push_str(&format!(" (+{} more; hover for details.)", self.warnings.len() - 2));
            }
        }
        self.catalog_status = status;
        self.catalog_tip = self.warnings.join("\n");
        self.event_selected_changed();
    }

    fn cells(e: &BurstEvent) -> Vec<String> {
        let mut detail = e.stations.join(", ");
        if !e.remarks.is_empty() {
            if !detail.is_empty() {
                detail.push_str(" \u{2014} ");
            }
            detail.push_str(&e.remarks);
        }
        if e.frequency_min_mhz.is_some() || e.frequency_max_mhz.is_some() {
            let f = |v: Option<f64>| v.map_or("?".to_owned(), crate::session::py_float);
            detail.push_str(&format!(" [{}\u{2013}{} MHz]", f(e.frequency_min_mhz), f(e.frequency_max_mhz)));
        }
        vec![
            e.start_utc.format("%Y-%m-%d %H:%M:%S").to_string(),
            e.end_utc.format("%Y-%m-%d %H:%M:%S").to_string(),
            if e.burst_type.is_empty() { "Unknown".into() } else { e.burst_type.clone() },
            e.stations.len().to_string(),
            detail,
        ]
    }

    fn sort_catalog(&mut self) {
        let Some((col, asc)) = self.catalog_view.sort else { return };
        let events = &self.events;
        self.shown.sort_by(|&a, &b| {
            let (ea, eb) = (&events[a], &events[b]);
            let ord = if col == 3 {
                ea.stations.len().cmp(&eb.stations.len())
            } else {
                Self::cells(ea)[col].cmp(&Self::cells(eb)[col])
            };
            if asc { ord } else { ord.reverse() }
        });
    }

    /// `_event_selected`.
    fn event_selected_changed(&mut self) {
        let event = self.selected_event().cloned();
        match &event {
            None => {
                self.selection_label = "Select a burst above, then find the FITS files covering it.".into();
                self.station_items = vec![("Choose a station".into(), String::new())];
            }
            Some(e) => {
                self.selection_label = format!(
                    "{} \u{b7} {} \u{2013} {} UTC",
                    if e.burst_type.is_empty() { "Unknown type" } else { &e.burst_type },
                    e.start_utc.format("%Y-%m-%d %H:%M:%S"),
                    e.end_utc.format("%Y-%m-%d %H:%M:%S")
                );
                self.station_items = if e.stations.is_empty() {
                    vec![("Choose a station".into(), String::new())]
                } else {
                    vec![("All reported stations".into(), "__reported__".into())]
                };
                for s in &e.stations {
                    if !self.station_items.iter().any(|i| &i.0 == s) {
                        self.station_items.push((s.clone(), s.clone()));
                    }
                }
            }
        }
        self.station_text = self.station_items[0].0.clone();
        self.clear_fits();
        self.loader.cancel();
    }

    fn request_burst_stations(&mut self, svc: &super::stations::StationService) {
        let Some(e) = self.selected_event() else { return };
        let (first, last) = (e.start_utc.date(), e.end_utc.date());
        let days: Vec<NaiveDate> =
            (0..=(last - first).num_days().max(0)).map(|k| first + chrono::Duration::days(k)).collect();
        if let Some(ev) = self.loader.request(svc, &days) {
            self.add_archive_stations(ev);
        }
    }

    /// `_add_archive_stations`.
    fn add_archive_stations(&mut self, e: LoaderEvent) {
        let LoaderEvent::Loaded(stations) = e else { return };
        if self.selected_event().is_none() {
            return;
        }
        for s in stations {
            if !self.station_items.iter().any(|i| i.0.to_lowercase() == s.to_lowercase()) {
                self.station_items.push((s.clone(), s));
            }
        }
    }

    fn display_fits(&mut self, r: Result<BurstFits, BurstListError>) {
        match r {
            Err(BurstListError::Cancelled) => {
                self.outcome = "cancelled";
                self.fits_status = "FITS search cancelled.".into();
            }
            Err(BurstListError::Failed(e)) => {
                self.outcome = "error";
                self.fits_status = format!("Could not find FITS files: {e}");
            }
            Ok(res) => {
                self.fits = res.candidates.iter().map(|c| (c.clone(), true)).collect();
                self.fits_view = ViewState::default();
                let mut status = format!("Found {} FITS file(s) covering the selected window.", res.candidates.len());
                if res.candidates.is_empty() {
                    status = "No matching FITS files. Try another station or increase the padding.".into();
                }
                if !res.warnings.is_empty() {
                    status.push(' ');
                    status.push_str(&res.warnings.iter().take(2).cloned().collect::<Vec<_>>().join(" "));
                }
                self.fits_status = status;
                self.fits_tip = res.warnings.join("\n");
            }
        }
    }

    fn display_preview(&mut self, r: Result<PreviewResult, PrepareError>, sh: &mut Shared) {
        sh.out.cache_changed = true;
        if self.cancel_requested || matches!(r, Err(PrepareError::Cancelled)) {
            self.outcome = "cancelled";
            self.fits_status = "Preview preparation cancelled.".into();
            return;
        }
        match r {
            Err(PrepareError::Failed(e)) => {
                self.outcome = "error";
                self.fits_status = format!("Could not preview FITS files: {e}");
            }
            Err(PrepareError::Cancelled) => {}
            Ok(res) => {
                let (n, cached, errors) = (res.panels.len(), res.cached_count, res.errors.clone());
                sh.out.previews.push(super::preview::PreviewWindow::new(&sh.ctx, res.panels));
                let mut status =
                    format!("Preview opened \u{b7} {n} panel(s) \u{b7} {cached} file(s) reused from cache.");
                if !errors.is_empty() {
                    status.push_str(&format!(" {} warning(s); hover for details.", errors.len()));
                }
                self.fits_status = status;
                self.fits_tip = errors.join("\n");
            }
        }
    }

    fn checked_files(&self) -> Vec<Candidate> {
        self.fits.iter().filter(|f| f.1).map(|f| f.0.clone()).collect()
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let header = BoxLayout::h([0; 4], 8)
            .add_stretched(Item::Widget(qt::wrap_label_spec(ui, "intro", INTRO, false)), 1)
            .widget(qt::label_spec(ui, "source", SOURCE));
        let type_labels: Vec<&str> = self.types.iter().map(|t| t.0.as_str()).collect();
        let mut grid = GridLayout::new([6; 4], 6, 6).column_stretch(3, 1);
        for (i, (id, text)) in FILTER_LABELS.iter().enumerate() {
            grid = grid.widget(qt::label_spec(ui, id, text), 0, i);
        }
        grid = grid
            .widget(qt::date_spec(ui, "from", "9999-12-31"), 1, 0)
            .widget(qt::date_spec(ui, "through", "9999-12-31"), 1, 1)
            .widget(qt::combo_spec(ui, "type", &type_labels), 1, 2)
            .widget(qt::line_edit_spec("station_filter"), 1, 3)
            .widget(qt::spin_spec(ui, "min_stations", "200 "), 1, 4)
            .widget(qt::button_spec(ui, "load", "Load Events", true), 1, 5)
            .widget_span(qt::line_edit_spec("text_filter"), 2, 0, 1, 5)
            .widget(qt::button_spec(ui, "reset", "Reset Filters", false), 2, 5);
        let filters = super::single::section("filters", grid);
        let progress_row = BoxLayout::h([0; 4], 3)
            .add_stretched(Item::Widget(qt::progress_spec("progress").min_height(18)), 1)
            .widget(
                WidgetSpec::new(
                    "detail",
                    Size::new(qt::text_w_hint(ui, &self.detail), 16),
                    Policy::Preferred,
                    Policy::Preferred,
                )
                .min_width(45),
            )
            .widget(qt::button_spec(ui, "cancel", "Cancel", false));
        let activity = Item::Container(Container {
            spec: WidgetSpec::new("activity", Size::new(0, 0), Policy::Preferred, Policy::Preferred),
            margins: [0; 4],
            layout: Box::new(
                BoxLayout::v([4, 0, 4, 0], 3)
                    .widget(qt::wrap_label_spec(ui, "activity_label", &self.activity, false))
                    .add(progress_row.into_item())
                    .into(),
            ),
        });
        let catalog = BoxLayout::v([6; 4], 6)
            .widget(qt::view_spec("catalog").min_height(80))
            .widget(super::status_label(ui, "catalog_status", &self.catalog_status));
        let controls = BoxLayout::h([0; 4], 5)
            .widget(qt::label_spec(ui, "station_label", "Station:"))
            .add_stretched(Item::Widget(qt::combo_len_spec("station", 16)), 1)
            .widget(qt::label_spec(ui, "extra_label", "Extra time:"))
            .widget(qt::spin_spec(ui, "padding", "120 min "))
            .widget(qt::button_spec(ui, "find", "Find FITS", true));
        let mut actions = BoxLayout::h([0; 4], 5)
            .widget(qt::button_spec(ui, "all", "Select All", false))
            .widget(qt::button_spec(ui, "clear", "Clear Selection", false))
            .stretch(1);
        for (id, text) in FILE_ACTIONS {
            actions = actions.widget(qt::button_spec(ui, id, text, false));
        }
        let count = format!("{} of {} files selected", self.fits.iter().filter(|f| f.1).count(), self.fits.len());
        let status_row = BoxLayout::h([0; 4], 5)
            .add_stretched(Item::Widget(super::status_label(ui, "fits_status", &self.fits_status)), 1)
            .widget(qt::status_spec(ui, "count", &count));
        let fits = BoxLayout::v([6; 4], 5)
            .widget(qt::wrap_label_spec(ui, "selection", &self.selection_label, false))
            .add(controls.into_item())
            .add_stretched(Item::Widget(qt::view_spec("fits").min_height(80)), 1)
            .add(actions.into_item())
            .add(status_row.into_item());
        let panel = |id: &'static str, l: BoxLayout| Container {
            spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred).stretch(1, 1),
            margins: [11, 23, 11, 11],
            layout: Box::new(l.into()),
        };
        let splitter = Item::Splitter(Splitter {
            spec: WidgetSpec::new("splitter", Size::new(0, 0), Policy::Preferred, Policy::Expanding),
            horizontal: false,
            handle: 8,
            children: vec![panel("catalog_panel", catalog), panel("fits_panel", fits)],
            sizes: self.split.clone(),
        });
        BoxLayout::v([2, 10, 2, 2], 8)
            .add(header.into_item())
            .add(filters)
            .add(activity)
            .add_stretched(splitter, 1)
            .into()
    }

    pub fn show(&mut self, ui: &mut Ui, page: Rect, sh: &mut Shared) {
        let p = sh.p;
        let busy = self.is_busy();
        let (g, o) = lay_out(&self.layout(ui), page);
        if self.split.is_none() {
            // QSplitter keeps the shares it gave the panes when first shown.
            self.split = Some(vec![g.rect("catalog_panel").h, g.rect("fits_panel").h]);
        }
        let r = |id: &str| at(&g, o, id);
        qt::wrapped(ui, r("intro"), INTRO, p.text, false);
        if qt::link(ui, r("source"), Id::new("burst_source"), SOURCE, p) {
            ui.ctx().open_url(egui::OpenUrl::new_tab(BURST_LIST_BASE_URL));
        }
        qt::section_box(ui, r("filters"), "Catalog filters", p);
        qt::section_box(ui, r("catalog_panel"), "1. Choose a burst", p);
        qt::section_box(ui, r("fits_panel"), "2. Preview or import FITS", p);
        for (id, text) in FILTER_LABELS {
            qt::label(ui, r(id), text, p.text);
        }
        let mut dates = qt::date_edit(ui, r("from"), Id::new("burst_from"), &mut self.start, !busy, p);
        dates |= qt::date_edit(ui, r("through"), Id::new("burst_through"), &mut self.end, !busy, p);
        let min_date = NaiveDate::from_ymd_opt(2010, 1, 1).unwrap();
        self.start = self.start.max(min_date);
        self.end = self.end.max(min_date);
        if dates {
            self.dates_changed();
        }
        let type_labels: Vec<String> = self.types.iter().map(|t| t.0.clone()).collect();
        let mut filters_changed = false;
        if let Some(i) =
            qt::combo(ui, r("type"), Id::new("burst_type"), &type_labels, Some(self.type_current), "", !busy, p)
            && i != self.type_current
        {
            self.type_current = i;
            filters_changed = true;
        }
        filters_changed |= qt::line_edit(
            ui,
            r("station_filter"),
            Id::new("burst_station_filter"),
            &mut self.station_filter,
            "Any reporting station",
            !busy,
            p,
        );
        filters_changed |= qt::spin_box(
            ui,
            r("min_stations"),
            Id::new("burst_min"),
            &mut self.min_stations,
            (0, 200),
            "",
            "Any",
            !busy,
            p,
        );
        filters_changed |= qt::line_edit(
            ui,
            r("text_filter"),
            Id::new("burst_text_filter"),
            &mut self.text_filter,
            "Search type, stations or remarks",
            !busy,
            p,
        );
        if qt::button(ui, r("load"), Id::new("burst_load"), "Load Events", true, !busy, p).clicked() {
            self.load_events(sh);
        }
        if qt::button(ui, r("reset"), Id::new("burst_reset"), "Reset Filters", false, !busy, p).clicked() {
            self.type_current = 0;
            self.station_filter.clear();
            self.min_stations = 0;
            self.text_filter.clear();
            filters_changed = true;
        }
        if filters_changed {
            self.apply_filters();
        }

        // Activity: message, progress, detail, Cancel.
        qt::wrapped(ui, r("activity_label"), &self.activity, p.text, false);
        qt::progress_bar(ui, r("progress"), &self.progress, p);
        let detail = r("detail");
        crate::ui::widgets::text_right(
            ui,
            detail.right(),
            detail.center().y,
            &self.detail,
            qt::FONT_PX,
            crate::fonts::Weight::Regular,
            p.text,
        );
        let can_cancel = self.work.is_some() && !self.cancel_requested;
        if qt::button(ui, r("cancel"), Id::new("burst_cancel"), "Cancel", false, can_cancel, p).clicked() {
            self.request_cancel();
        }

        // Splitter handle.
        if let Some(h) = g.handles.get(&("splitter", 1)).copied() {
            let hr = h.to_egui(o);
            let resp = qt::splitter_handle(ui, hr, Id::new("burst_split"));
            if resp.dragged() {
                let (a, b) = (g.rect("catalog_panel").h, g.rect("fits_panel").h);
                let d = resp.drag_delta().y.round() as i32;
                let a2 = (a + d).max(84);
                let b2 = (a + b - a2).max(120);
                self.split = Some(vec![a + b - b2, b2]);
            }
        }

        // The catalog.
        let catalog = r("catalog");
        let style = Style::results_table(true);
        let rows: Vec<Row> = self
            .events
            .iter()
            .map(|e| {
                let mut row = Row::new(Self::cells(e));
                row.tooltip = Some(format!("{}\n\n{}", e.raw_line, e.source_url));
                row
            })
            .collect();
        let headers = ["Start (UTC)", "End (UTC)", "Type", "Stations", "Observatories / remarks"];
        let widths = self.widths(ui, catalog, &style, &headers, &rows, &self.shown, false);
        let events = itemview::item_view(
            ui,
            catalog,
            Id::new("burst_catalog"),
            &headers,
            &widths,
            &rows,
            &self.shown,
            &style,
            &mut self.catalog_view,
            !busy,
            p,
        );
        let mut selected_now = false;
        for e in events {
            match e {
                Event::Current(_) => selected_now = true,
                Event::Activated(_) => {
                    if !busy {
                        self.find_fits(sh);
                    }
                }
                Event::Sort(col) => {
                    let asc = match self.catalog_view.sort {
                        Some((c, a)) if c == col => !a,
                        _ => true,
                    };
                    self.catalog_view.sort = Some((col, asc));
                    self.sort_catalog();
                }
                _ => {}
            }
        }
        if selected_now {
            self.event_selected_changed();
            self.request_burst_stations(sh.svc);
        }
        let cs = r("catalog_status");
        qt::status(ui, cs, &self.catalog_status, p);
        if !self.catalog_tip.is_empty() {
            ui.interact(cs, Id::new("burst_catalog_tip"), Sense::hover()).on_hover_text(&self.catalog_tip);
        }

        // The files of the selected burst.
        qt::wrapped(ui, r("selection"), &self.selection_label, p.text, false);
        qt::label(ui, r("station_label"), "Station:", p.text);
        qt::label(ui, r("extra_label"), "Extra time:", p.text);
        let items: Vec<String> = self.station_items.iter().map(|i| i.0.clone()).collect();
        if qt::editable_combo(ui, r("station"), Id::new("burst_station"), &mut self.station_text, &items, !busy, p) {
            self.clear_fits();
        }
        if qt::spin_box(ui, r("padding"), Id::new("burst_padding"), &mut self.padding, (0, 120), " min", "", !busy, p) {
            self.clear_fits();
        }
        let has_event = self.selected_event().is_some();
        if qt::button(ui, r("find"), Id::new("burst_find"), "Find FITS", true, !busy && has_event, p).clicked() {
            self.find_fits(sh);
        }
        let fits_rect = r("fits");
        let fstyle = Style::results_table(false);
        let frows: Vec<Row> = self
            .fits
            .iter()
            .map(|(c, on)| {
                let mut row = Row::new(vec![
                    String::new(),
                    c.station.clone(),
                    c.utc_text(),
                    c.receiver_id.clone(),
                    c.filename.clone(),
                ])
                .checked(Check::from_bool(*on));
                row.tooltip = Some(c.url.clone());
                row
            })
            .collect();
        let fheaders = ["", "Station", "Start (UTC)", "Focus", "Filename"];
        let visible: Vec<usize> = (0..frows.len()).collect();
        let fwidths = self.widths(ui, fits_rect, &fstyle, &fheaders, &frows, &visible, true);
        for e in itemview::item_view(
            ui,
            fits_rect,
            Id::new("burst_fits"),
            &fheaders,
            &fwidths,
            &frows,
            &visible,
            &fstyle,
            &mut self.fits_view,
            !busy,
            p,
        ) {
            if let Event::Toggled(i) = e {
                self.fits[i].1 ^= true;
            }
        }
        let has_files = !self.fits.is_empty();
        let selected = self.fits.iter().filter(|f| f.1).count();
        if qt::button(ui, r("all"), Id::new("burst_all"), "Select All", false, !busy && has_files, p).clicked() {
            for f in &mut self.fits {
                f.1 = true;
            }
        }
        if qt::button(ui, r("clear"), Id::new("burst_clear"), "Clear Selection", false, !busy && has_files, p).clicked()
        {
            for f in &mut self.fits {
                f.1 = false;
            }
        }
        for (id, text) in FILE_ACTIONS {
            if qt::button(ui, r(id), Id::new(("burst", id)), text, false, !busy && selected > 0, p).clicked() {
                self.file_action(id, sh);
            }
        }
        let fs = r("fits_status");
        qt::status(ui, fs, &self.fits_status, p);
        if !self.fits_tip.is_empty() {
            ui.interact(fs, Id::new("burst_fits_tip"), Sense::hover()).on_hover_text(&self.fits_tip);
        }
        let count = format!("{selected} of {} files selected", self.fits.len());
        qt::status(ui, r("count"), &count, p);
    }

    /// Column widths: every column to its contents, the last stretched.
    #[allow(clippy::too_many_arguments)]
    fn widths(
        &self,
        ui: &Ui,
        rect: Rect,
        style: &Style,
        headers: &[&str],
        rows: &[Row],
        visible: &[usize],
        check_first: bool,
    ) -> Vec<f32> {
        let vp = itemview::viewport(rect, style);
        let total = vp.width() - if itemview::needs_scrollbar(rect, style, visible.len()) { 10.0 } else { 0.0 };
        let n = headers.len();
        let mut w: Vec<f32> = (0..n - 1)
            .map(|i| {
                let checkable = check_first && i == 0 && !rows.is_empty();
                itemview::content_width(
                    ui,
                    headers[i],
                    rows.iter().map(|r| r.cells[i].clone()),
                    checkable,
                    style.sortable,
                )
            })
            .collect();
        let used: f32 = w.iter().sum();
        let last = itemview::content_width(
            ui,
            headers[n - 1],
            rows.iter().map(|r| r.cells[n - 1].clone()),
            false,
            style.sortable,
        );
        w.push((total - used).max(last));
        w
    }

    /// Scripted steps (`DownloaderDialog::run_script`).
    pub(crate) fn script(&mut self, args: &[&str], sh: &mut Shared) {
        match args {
            ["load"] => self.load_events(sh),
            ["select", k] => {
                if let Some(&i) = k.parse::<usize>().ok().and_then(|k| self.shown.get(k)) {
                    self.catalog_view.current = Some(i);
                    self.event_selected_changed();
                    self.request_burst_stations(sh.svc);
                }
            }
            ["find"] => self.find_fits(sh),
            _ => {}
        }
    }

    fn load_events(&mut self, sh: &mut Shared) {
        if self.is_busy() {
            return;
        }
        if self.end < self.start {
            return sh.out.notice(
                NoticeKind::Warning,
                "Invalid Date Range",
                "The end date must be on or after the start date.",
            );
        }
        self.events.clear();
        self.warnings.clear();
        self.shown.clear();
        self.clear_fits();
        self.catalog_status = "Loading the source catalog\u{2026}".into();
        let (start, end) = (self.start, self.end);
        let job = Job::spawn(&sh.ctx, move |r| {
            let flag = r.cancel_flag();
            burst_list::fetch_burst_events(
                start,
                end,
                |a, b, m| r.send((a, b, m)),
                move || flag.load(Ordering::Relaxed),
            )
        });
        self.start_work(JobKind::Catalog, Work::Catalog(job));
    }

    /// `find_fits`.
    fn find_fits(&mut self, sh: &mut Shared) {
        if self.is_busy() {
            return;
        }
        let Some(event) = self.selected_event().cloned() else { return };
        let text = self.station_text.trim().to_owned();
        let value = self.station_items.iter().find(|i| i.0 == text).map_or(text.clone(), |i| i.1.clone());
        let stations: Vec<String> = if value == "__reported__" {
            event.stations.clone()
        } else if value.is_empty() {
            Vec::new()
        } else {
            vec![value]
        };
        if stations.is_empty() {
            return sh.out.notice(
                NoticeKind::Warning,
                "No Station",
                "Choose or enter a station to search for this burst.",
            );
        }
        self.clear_fits();
        self.fits_status = "Searching archive listings for files covering the burst\u{2026}".into();
        let padding = self.padding as i64;
        let job = Job::spawn(&sh.ctx, move |r| {
            let flag = r.cancel_flag();
            burst_list::find_burst_fits(
                &event,
                &stations,
                padding,
                |a, b, m| r.send((a, b, m)),
                move || flag.load(Ordering::Relaxed),
            )
        });
        self.start_work(JobKind::Fits, Work::Fits(job));
    }

    fn file_action(&mut self, id: &str, sh: &mut Shared) {
        let selected = self.checked_files();
        if selected.is_empty() || self.is_busy() {
            return;
        }
        match id {
            "preview" => {
                self.fits_status = format!("Preparing a preview of {} selected FITS file(s)\u{2026}", selected.len());
                let root = sh.cache_root.to_path_buf();
                let job = Job::spawn(&sh.ctx, move |r| {
                    let flag = r.cancel_flag();
                    downloader::prepare_preview(
                        &root,
                        &selected,
                        |a, b, m| r.send((a, b, m)),
                        move || flag.load(Ordering::Relaxed),
                    )
                });
                self.start_work(JobKind::Preview, Work::Preview(job));
            }
            "import" => sh.out.import = Some(selected.into_iter().map(|c| c.url).collect()),
            "compare" => sh.out.compare = Some(selected.into_iter().map(|c| c.url).collect()),
            "download" => {
                let Some(dir) = pick_folder("Select Download Folder") else { return };
                self.kind = Some(JobKind::Download);
                self.outcome = "";
                self.cancel_requested = false;
                self.progress =
                    qt::Progress { min: 0, max: selected.len() as i64 * 1000, value: 0, format: "%p%".into() };
                self.detail = "0%".into();
                self.activity = format!("Downloading {} FITS file(s)\u{2026}", selected.len());
                self.fits_status = "Downloading selected FITS files\u{2026}".into();
                self.downloads = Some(DownloadBatch::start(&sh.ctx, sh.cache_root, &selected, &dir));
            }
            _ => {}
        }
    }
}

const INTRO: &str = "Find a burst, choose its FITS files, then preview or import the data.";
const SOURCE: &str = "Monstein source catalog \u{2197}";
const FILTER_LABELS: [(&str, &str); 5] = [
    ("l_from", "From (UTC)"),
    ("l_through", "Through (UTC)"),
    ("l_type", "Burst type"),
    ("l_station", "Reporting station"),
    ("l_min", "Min. stations"),
];
const FILE_ACTIONS: [(&str, &str); 4] =
    [("preview", "Preview"), ("download", "Download"), ("import", "Import"), ("compare", "Compare")];
