//! The Single Station tab: a station's whole UTC day, grouped by focus
//! code, to download, preview, compare or import
//! (`CallistoDownloaderApp._build_single_station_tab` and its handlers).

use callisto_core::radio::archive::{ListingError, Source};
use callisto_core::radio::cache;
use callisto_core::radio::downloader::{self, Candidate, PreviewResult};
use chrono::NaiveDate;
use egui::{Id, Rect, Ui};

use super::batch::{DownloadBatch, pick_folder};
use super::jobs::Job;
use super::stations::{StationCombo, StationLoader, StationService};
use super::{Retry, Shared, at, lay_out};
use crate::session::NoticeKind;
use crate::ui::itemview::{self, Check, Event, Row, Style, ViewState};
use crate::ui::qlayout::{BoxLayout, Container, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::qt;

/// A `#DownloaderSection` group box around `layout`.
pub fn section(id: &'static str, layout: impl Into<Layout>) -> Item {
    Item::Container(Container {
        spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred),
        margins: [11, 23, 11, 11],
        layout: Box::new(layout.into()),
    })
}

struct Group {
    label: String,
    expanded: bool,
    children: Vec<(Candidate, bool)>,
}

impl Group {
    fn state(&self) -> Check {
        let n = self.children.iter().filter(|c| c.1).count();
        if n == 0 {
            Check::Unchecked
        } else if n == self.children.len() {
            Check::Checked
        } else {
            Check::Partial
        }
    }
}

type FetchJob = Job<(usize, usize), Result<Vec<(String, String)>, ListingError>>;
type PreviewJob = Job<Progress, Result<PreviewResult, String>>;

/// The single-station preview worker's signals.
pub enum Progress {
    Max(usize),
    Step(usize),
    Text(String),
}

pub struct SingleTab {
    pub date: NaiveDate,
    loader: StationLoader,
    combo: StationCombo,
    groups: Vec<Group>,
    view: ViewState,
    fetch: Option<FetchJob>,
    preview: Option<PreviewJob>,
    downloads: Option<DownloadBatch>,
    progress: Option<qt::Progress>,
    status: String,
}

impl Default for SingleTab {
    fn default() -> Self {
        Self::new()
    }
}

impl SingleTab {
    pub fn new() -> Self {
        SingleTab {
            date: chrono::Local::now().date_naive(),
            loader: StationLoader::default(),
            combo: StationCombo::default(),
            groups: Vec::new(),
            view: ViewState::default(),
            fetch: None,
            preview: None,
            downloads: None,
            progress: None,
            status: String::new(),
        }
    }

    pub fn is_busy(&self) -> bool {
        self.fetch.is_some() || self.preview.is_some() || self.downloads.is_some()
    }

    pub fn request_stations(&mut self, svc: &StationService) {
        if let Some(e) = self.loader.request(svc, &[self.date]) {
            self.combo.apply(&e);
        }
    }

    /// The server the tab starts on, before its first station lookup.
    pub(crate) fn start_on(&mut self, source: Source) {
        self.loader.source = source;
    }

    /// The selected server changed: the files of the previous one go, the
    /// station list reloads, and a listed station stays selected.
    pub(crate) fn set_source(&mut self, svc: &StationService, source: Source) {
        self.combo.remember_current();
        self.groups.clear();
        self.view = ViewState::default();
        self.status.clear();
        if let Some(e) = self.loader.set_source(svc, source) {
            self.combo.apply(&e);
        }
    }

    /// The tree's rows (groups and their files) and what each row is.
    fn rows(&self) -> (Vec<Row>, Vec<(usize, Option<usize>)>) {
        let mut rows = Vec::new();
        let mut map = Vec::new();
        for (gi, g) in self.groups.iter().enumerate() {
            let mut r = Row::new(vec![g.label.clone(), String::new()]).checked(g.state());
            r.expanded = Some(g.expanded);
            r.selectable = false;
            rows.push(r);
            map.push((gi, None));
            if g.expanded {
                for (ci, (c, checked)) in g.children.iter().enumerate() {
                    let time =
                        c.observed_at.map_or_else(|| "\u{2014}".to_owned(), |t| t.format("%H:%M:%S").to_string());
                    let mut r = Row::new(vec![c.filename.clone(), time]).checked(Check::from_bool(*checked));
                    r.depth = 1;
                    rows.push(r);
                    map.push((gi, Some(ci)));
                }
            }
        }
        (rows, map)
    }

    fn checked(&self) -> Vec<Candidate> {
        self.groups.iter().flat_map(|g| g.children.iter().filter(|c| c.1).map(|c| c.0.clone())).collect()
    }

    /// `display_fetched_files`.
    fn display(&mut self, result: Result<Vec<(String, String)>, ListingError>, sh: &mut Shared) {
        self.groups.clear();
        self.view = ViewState::default();
        self.progress = None;
        self.status.clear();
        let server = self.loader.source.label();
        let files = match result {
            Err(ListingError::Unreachable(m)) => {
                sh.out.server_failed(format!("{server} FITS server is not responding.\n{m}"), Retry::SingleShow);
                return sh.out.notice(
                    NoticeKind::Critical,
                    "Server Error",
                    format!("{server} FITS server is not responding.\n\nDetails:\n{m}"),
                );
            }
            Err(ListingError::Http(m)) => {
                sh.out.server_failed(
                    format!("Could not load the {server} listing for the selected date.\n{m}"),
                    Retry::SingleShow,
                );
                return sh.out.notice(
                    NoticeKind::Critical,
                    "Fetch Error",
                    format!("Could not load the {server} listing for the selected date.\n\nDetails:\n{m}"),
                );
            }
            Ok(f) => f,
        };
        let valid: Vec<(String, String)> = files.into_iter().filter(|(n, u)| !n.is_empty() && !u.is_empty()).collect();
        if valid.is_empty() {
            return sh.out.notice(
                NoticeKind::Information,
                "No Data Found",
                format!("No FITS files were found on {server} for the selected station on that date."),
            );
        }
        self.status = format!("Found {} FITS file(s) on {server}.", valid.len());
        let mut parsed = Vec::new();
        let mut unparsed = Vec::new();
        for (name, url) in valid {
            match Candidate::parse(&name, url.clone()) {
                Some(c) => parsed.push(c),
                None => unparsed.push(Candidate {
                    station: String::new(),
                    observed_at: None,
                    filename: name,
                    url,
                    receiver_id: String::new(),
                }),
            }
        }
        for (code, members) in downloader::group_by_focus_code(&parsed) {
            let label = downloader::focus_group_label(&code, &members);
            self.groups.push(Group {
                label,
                expanded: true,
                children: members.into_iter().map(|c| (c, false)).collect(),
            });
        }
        if !unparsed.is_empty() {
            unparsed.sort_by_key(|c| c.filename.to_lowercase());
            let n = unparsed.len();
            let label = format!("Unparsed ({n} {})", if n == 1 { "file" } else { "files" });
            self.groups.push(Group {
                label,
                expanded: true,
                children: unparsed.into_iter().map(|c| (c, false)).collect(),
            });
        }
    }

    pub fn tick(&mut self, sh: &mut Shared) {
        if let Some(e) = self.loader.tick(&sh.ctx, sh.svc, sh.resolved) {
            self.combo.apply(&e);
        }
        if let Some(job) = &mut self.fetch {
            let (steps, done) = job.poll();
            for (value, max) in steps {
                if let Some(p) = &mut self.progress {
                    p.max = max as i64;
                    p.value = value as i64;
                }
            }
            if let Some(r) = done {
                self.fetch = None;
                self.display(r, sh);
            }
        }
        if let Some(job) = &mut self.preview {
            let (msgs, done) = job.poll();
            for m in msgs {
                match m {
                    Progress::Max(n) => {
                        if let Some(p) = &mut self.progress {
                            p.max = n as i64;
                        }
                    }
                    Progress::Step(i) => {
                        if let Some(p) = &mut self.progress {
                            p.value = i as i64;
                        }
                    }
                    Progress::Text(t) => self.status = t,
                }
            }
            if let Some(r) = done {
                self.preview = None;
                // `_finish_preview_run`.
                self.progress = None;
                self.status.clear();
                sh.out.cache_changed = true;
                match r {
                    Ok(res) => open_preview(res, self.loader.source, sh),
                    Err(m) => {
                        sh.out.server_failed(
                            format!("{}: {}", self.loader.source.label(), m.trim()),
                            Retry::SinglePreview,
                        );
                        sh.out.notice(NoticeKind::Critical, "Preview Error", m)
                    }
                }
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
                self.status.clear();
                cache::enforce_cache_limit(sh.cache_root, cache::DEFAULT_MAX_CACHE_BYTES);
                sh.out.cache_changed = true;
                // `show_download_complete_message`.
                let failed = batch.failures.len();
                let server = self.loader.source.label();
                let mut details = vec![
                    format!("Server: {server}"),
                    format!("Files requested: {}", batch.total),
                    format!("Saved: {} ({} reused from cache)", batch.total.saturating_sub(failed), batch.cached),
                    format!("Failed: {failed}"),
                ];
                if failed > 0 {
                    details.push(String::new());
                    details.push("Failures:".into());
                    details.extend(
                        batch
                            .failures
                            .iter()
                            .take(8)
                            .map(|(_, e)| if e.is_empty() { "Unknown error".to_owned() } else { e.clone() }),
                    );
                    if failed > 8 {
                        details.push("...".into());
                    }
                    let reasons: Vec<String> = batch.failures.iter().take(8).map(|(_, e)| e.clone()).collect();
                    sh.out.server_failed(
                        format!("{failed} of {} downloads from {server} failed.\n{}", batch.total, reasons.join("\n")),
                        Retry::SingleDownload,
                    );
                    sh.out.notice(NoticeKind::Warning, "Download Finished", details.join("\n"));
                } else {
                    sh.out.notice(NoticeKind::Information, "Download Complete", details.join("\n"));
                }
            }
        }
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let p_row = BoxLayout::h([0; 4], 8)
            .widget(qt::label_spec(ui, "date_label", "Date:"))
            .widget(qt::date_spec(ui, "date", "9999-12-31").min_width(140))
            .widget(qt::label_spec(ui, "station_label", "Station:"))
            .widget(qt::combo_len_spec("station", 22))
            .widget(qt::button_spec(ui, "show", "Show Available FITS", true));
        let params = BoxLayout::v([9; 4], 4).add(p_row.into_item()).widget(super::status_label(
            ui,
            "stations_status",
            &self.loader.summary,
        ));
        let files = BoxLayout::v([9; 4], 6).widget(qt::view_spec("tree"));
        let mut actions = BoxLayout::h([9; 4], 8);
        for (id, t) in ACTIONS {
            actions = actions.widget(qt::button_spec(ui, id, t, false));
        }
        BoxLayout::v([2, 10, 2, 2], 8)
            .add(section("params", params))
            .add(section("files", files))
            .add(section("actions", actions))
            .widget(qt::progress_spec("progress").hidden(self.progress.is_none()))
            .widget(super::status_label(ui, "status", &self.status))
            .into()
    }

    pub fn show(&mut self, ui: &mut Ui, page: Rect, sh: &mut Shared) {
        let p = sh.p;
        let (g, o) = lay_out(&self.layout(ui), page);
        qt::section_box(ui, at(&g, o, "params"), "Observation Parameters", p);
        qt::section_box(ui, at(&g, o, "files"), "Available FITS Files (Whole Day, grouped by focus code)", p);
        qt::section_box(ui, at(&g, o, "actions"), "Actions", p);
        qt::label(ui, at(&g, o, "date_label"), "Date:", p.text);
        qt::label(ui, at(&g, o, "station_label"), "Station:", p.text);
        if qt::date_edit(ui, at(&g, o, "date"), Id::new("single_date"), &mut self.date, true, p) {
            self.request_stations(sh.svc);
        }
        if let Some(i) = qt::combo(
            ui,
            at(&g, o, "station"),
            Id::new("single_station"),
            &self.combo.items,
            self.combo.current,
            &self.combo.placeholder,
            true,
            p,
        ) {
            self.combo.activate(i);
        }
        let status_rect = at(&g, o, "stations_status");
        if self.loader.retry {
            if qt::status_with_link(ui, status_rect, Id::new("single_retry"), &self.loader.summary, "Retry", p)
                && let Some(e) = self.loader.reload(sh.svc)
            {
                self.combo.apply(&e);
            }
        } else {
            qt::status(ui, status_rect, &self.loader.summary, p);
        }
        if qt::button(ui, at(&g, o, "show"), Id::new("single_show"), "Show Available FITS", true, true, p).clicked() {
            self.show_available(sh);
        }

        // The file tree.
        let (rows, map) = self.rows();
        let tree = at(&g, o, "tree");
        let style = Style::tree();
        let vp = itemview::viewport(tree, &style);
        let scroll = itemview::needs_scrollbar(tree, &style, rows.len());
        let width = vp.width() - if scroll { 10.0 } else { 0.0 };
        let half = (width / 2.0).floor();
        let visible: Vec<usize> = (0..rows.len()).collect();
        let events = itemview::item_view(
            ui,
            tree,
            Id::new("single_tree"),
            &["File", "UTC Time"],
            &[half, width - half],
            &rows,
            &visible,
            &style,
            &mut self.view,
            true,
            p,
        );
        for e in events {
            match e {
                Event::Toggled(r) => match map[r] {
                    (gi, None) => {
                        let on = self.groups[gi].state() != Check::Checked;
                        for c in &mut self.groups[gi].children {
                            c.1 = on;
                        }
                    }
                    (gi, Some(ci)) => self.groups[gi].children[ci].1 ^= true,
                },
                Event::Expand(r, open) => {
                    if let (gi, None) = map[r] {
                        self.groups[gi].expanded = open;
                    }
                }
                _ => {}
            }
        }

        let preview_idle = self.preview.is_none();
        let enabled = |id: &str| id != "preview" || preview_idle;
        for (id, text) in ACTIONS {
            if qt::button(ui, at(&g, o, id), Id::new(("single", id)), text, false, enabled(id), p).clicked() {
                self.action(id, sh);
            }
        }
        if let Some(prog) = &self.progress {
            qt::progress_bar(ui, at(&g, o, "progress"), prog, p);
        }
        qt::status(ui, at(&g, o, "status"), &self.status, p);
    }

    /// Scripted steps (`DownloaderDialog::run_script`).
    pub(crate) fn script(&mut self, args: &[&str], sh: &mut Shared) {
        match args {
            ["station", name] => self.combo.set_text(name),
            ["show"] => self.show_available(sh),
            ["check", g, c] => {
                if let (Ok(g), Ok(c)) = (g.parse::<usize>(), c.parse::<usize>())
                    && let Some(child) = self.groups.get_mut(g).and_then(|x| x.children.get_mut(c))
                {
                    child.1 ^= true;
                }
            }
            _ => {}
        }
    }

    /// `show_available_fits`.
    pub(crate) fn show_available(&mut self, sh: &mut Shared) {
        let station = self.combo.text().trim().to_owned();
        if station.is_empty() {
            return sh.out.notice(
                NoticeKind::Information,
                "No Station",
                format!("No station is selected. {}", self.loader.summary).trim().to_owned(),
            );
        }
        sh.out.clear_server_failure = true;
        self.groups.clear();
        self.progress = Some(qt::Progress::busy());
        let (date, source) = (self.date, self.loader.source);
        self.status = format!("Searching {}...", source.label());
        self.fetch = Some(Job::spawn(&sh.ctx, move |r| {
            downloader::fetch_station_day(source, date, &station, |v, m| r.send((v, m)))
        }));
    }

    pub(crate) fn action(&mut self, id: &str, sh: &mut Shared) {
        match id {
            "select_all" | "deselect_all" => {
                let on = id == "select_all";
                for g in &mut self.groups {
                    for c in &mut g.children {
                        c.1 = on;
                    }
                }
            }
            "download" => {
                let selected = self.checked();
                if selected.is_empty() {
                    return sh.out.notice(NoticeKind::Warning, "No Selection", "Please select files to download.");
                }
                let Some(dir) = pick_folder("Select Download Folder") else { return };
                sh.out.clear_server_failure = true;
                self.progress = Some(qt::Progress::range(selected.len() as i64));
                self.status =
                    format!("Downloading {} FITS file(s) from {}...", selected.len(), self.loader.source.label());
                self.downloads = Some(DownloadBatch::start(&sh.ctx, sh.cache_root, &selected, &dir));
            }
            "preview" => {
                let selected: Vec<Candidate> = self.checked().into_iter().filter(|c| !c.url.is_empty()).collect();
                if selected.is_empty() {
                    return sh.out.notice(NoticeKind::Warning, "No Selection", "Please select files to preview.");
                }
                if self.preview.is_some() {
                    return sh.out.notice(
                        NoticeKind::Information,
                        "Preview In Progress",
                        "Another preview is already being prepared.",
                    );
                }
                sh.out.clear_server_failure = true;
                self.progress = Some(qt::Progress::range(selected.len() as i64));
                let root = sh.cache_root.to_path_buf();
                self.preview = Some(Job::spawn(&sh.ctx, move |r| prepare_preview(&root, &selected, r)));
            }
            "compare" | "import" => {
                let urls: Vec<String> =
                    self.checked().into_iter().filter(|c| !c.url.is_empty()).map(|c| c.url).collect();
                if urls.is_empty() {
                    return sh.out.notice(NoticeKind::Warning, "No Selection", "Please select at least one FITS file.");
                }
                if id == "import" {
                    sh.out.import = Some(urls);
                } else {
                    sh.out.compare = Some(urls);
                }
            }
            _ => {}
        }
    }
}

const ACTIONS: [(&str, &str); 6] = [
    ("select_all", "Select All"),
    ("deselect_all", "Deselect All"),
    ("download", "Download Selected"),
    ("preview", "Preview Selected"),
    ("compare", "Compare"),
    ("import", "Import"),
];

/// `PreviewPrepareWorker.run`.
fn prepare_preview(
    root: &std::path::Path,
    candidates: &[Candidate],
    r: &super::jobs::Reporter<Progress>,
) -> Result<PreviewResult, String> {
    let n = candidates.len();
    let mut local = Vec::new();
    let mut errors = Vec::new();
    let mut cached_count = 0;
    r.send(Progress::Max(n));
    for (i, c) in candidates.iter().enumerate() {
        r.send(Progress::Text(format!("Preparing {} ({}/{n})...", c.filename, i + 1)));
        match cache::fetch_cached(root, &c.url, &c.filename, |_, _| {}) {
            Ok((path, was_cached)) => {
                cached_count += usize::from(was_cached);
                local.push((path.to_string_lossy().into_owned(), c.filename.clone()));
                r.send(Progress::Step(i + 1));
            }
            Err(e) => errors.push(format!("{}: {e}", c.filename)),
        }
    }
    let first8 = |e: &[String]| e.iter().take(8).cloned().collect::<Vec<_>>().join("\n");
    if local.is_empty() {
        return Err(format!("None of the selected files could be prepared.\n\n{}", first8(&errors)));
    }
    r.send(Progress::Text("Building preview...".into()));
    let (panels, panel_errors) = downloader::build_preview_panels(&local);
    errors.extend(panel_errors);
    if panels.is_empty() {
        return Err(format!("No preview could be produced.\n\n{}", first8(&errors)));
    }
    cache::enforce_cache_limit(root, cache::DEFAULT_MAX_CACHE_BYTES);
    Ok(PreviewResult { panels, errors, cached_count, total: n })
}

/// `_on_preview_ready`: the preview window, naming the server its files came
/// from, and a warning for files it lacks.
pub fn open_preview(res: PreviewResult, source: Source, sh: &mut Shared) {
    if res.panels.is_empty() {
        let text = if res.errors.is_empty() {
            "No preview available.".to_owned()
        } else {
            res.errors.iter().take(8).cloned().collect::<Vec<_>>().join("\n")
        };
        return sh.out.notice(NoticeKind::Critical, "Preview Error", text);
    }
    let errors = res.errors.clone();
    sh.out.previews.push(super::preview::PreviewWindow::new(&sh.ctx, res.panels).with_source(source.label()));
    if !errors.is_empty() {
        sh.out.notice(
            NoticeKind::Warning,
            "Preview Warnings",
            format!(
                "Some files could not be previewed:\n\n{}",
                errors.iter().take(8).cloned().collect::<Vec<_>>().join("\n")
            ),
        );
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
    use std::time::{Duration, Instant};

    use callisto_core::radio::archive::{ASTRODONCEL_FILES_URL, ASTRODONCEL_LISTING_URL, Source, set_mock_http};
    use egui_kittest::Harness;

    use super::super::DownloaderDialog;
    use super::super::stations::{LoaderState, StationService};
    use crate::theme::Palette;

    struct Rig {
        d: DownloaderDialog,
        svc: StationService,
        p: Palette,
        ready: bool,
    }

    fn harness(source: Source) -> Harness<'static, Rig> {
        // Every server lists BIR; each also has a station of its own.
        let mut svc = StationService::new(Arc::new(|s, _| Ok(vec!["BIR".into(), format!("ONLY-{}", s.id())])));
        let cache = std::env::temp_dir().join(format!("lite_server_{}_{}", source.id(), std::process::id()));
        let mut d = DownloaderDialog::new(cache, &mut svc, None, source);
        d.single.date = chrono::NaiveDate::from_ymd_opt(2024, 1, 2).unwrap();
        d.single.request_stations(&svc);
        let rig = Rig { d, svc, p: Palette::new(false, true), ready: false };
        Harness::builder().with_size([1540.0, 980.0]).build_ui_state(
            |ui, r: &mut Rig| {
                if !r.ready {
                    egui_extras::install_image_loaders(ui.ctx());
                    crate::fonts::SystemFonts::load().install(ui.ctx());
                    r.p.apply(ui.ctx());
                    r.ready = true;
                    return;
                }
                let out = r.d.frame(ui, &mut r.svc, &r.p);
                r.d.finish(ui.ctx(), out, &r.p, false);
            },
            rig,
        )
    }

    fn step_until(h: &mut Harness<'static, Rig>, what: &str, done: impl Fn(&Rig) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while !done(h.state()) {
            assert!(Instant::now() < deadline, "timed out: {what}");
            h.step();
            std::thread::sleep(Duration::from_millis(20));
        }
        h.step();
    }

    fn script(h: &mut Harness<'static, Rig>, cmd: &str) {
        h.state_mut().d.script.push_back(cmd.into());
        h.step();
    }

    #[test]
    fn the_server_selector_switches_locks_and_retries() {
        // The Astrodoncel listing of 2024-01-02 waits for `gate`; its answer
        // is broken (0), lists one BIR file (1).
        let gate = Arc::new(AtomicBool::new(true));
        let answer = Arc::new(AtomicU8::new(0));
        let (g, a) = (gate.clone(), answer.clone());
        let listing = format!("{ASTRODONCEL_LISTING_URL}?path=2024%2F01%2F02");
        set_mock_http(Some(Arc::new(move |url: &str| {
            if url != listing {
                return Some((404, Vec::new()));
            }
            while !g.load(Ordering::SeqCst) {
                std::thread::sleep(Duration::from_millis(5));
            }
            Some(match a.load(Ordering::SeqCst) {
                0 => (200, b"<html>maintenance</html>".to_vec()),
                _ => (200, br#"[{"name":"BIR_20240102_000000_01.fit.gz","isDirectory":false,"size":10}]"#.to_vec()),
            })
        })));

        // The remembered server applies before the first station lookup.
        let mut h = harness(Source::Astrodoncel);
        step_until(&mut h, "stations", |r| r.d.single.loader.state == LoaderState::Loaded);
        assert_eq!(h.state().d.source(), Source::Astrodoncel);
        assert_eq!(h.state().d.single.loader.summary, "Astrodoncel: 2 stations have data on 2024-01-02 (UTC).");

        // A search locks the selector until it ends.
        gate.store(false, Ordering::SeqCst);
        script(&mut h, "single|station|BIR");
        script(&mut h, "single|show");
        assert!(h.state().d.busy());
        assert_eq!(h.state().d.single.status, "Searching Astrodoncel...");
        let rig = h.state_mut();
        assert!(!rig.d.set_source(&mut rig.svc, Source::Ecallisto), "refused while searching");
        assert_eq!(rig.d.source(), Source::Astrodoncel);

        // A broken answer is an error naming the server, with Retry; the
        // server stays selected.
        gate.store(true, Ordering::SeqCst);
        step_until(&mut h, "failed search", |r| !r.d.busy());
        let failure = h.state().d.server_failure().unwrap_or_default().to_owned();
        assert!(failure.starts_with("Could not load the Astrodoncel listing for the selected date."), "{failure}");
        assert!(h.state().d.pending_notices().iter().any(|n| n.title == "Fetch Error"));
        assert_eq!(h.state().d.source(), Source::Astrodoncel);

        // Retry repeats the search.
        answer.store(1, Ordering::SeqCst);
        script(&mut h, "retry");
        step_until(&mut h, "retried search", |r| !r.d.busy());
        assert!(h.state().d.server_failure().is_none());
        let single = &h.state().d.single;
        assert_eq!(single.status, "Found 1 FITS file(s) on Astrodoncel.");
        assert_eq!(single.groups.len(), 1);
        assert_eq!(
            single.groups[0].children[0].0.url,
            format!("{ASTRODONCEL_FILES_URL}2024/01/02/BIR_20240102_000000_01.fit.gz")
        );
        assert_eq!(single.groups[0].children[0].0.source(), Source::Astrodoncel);

        // Switching clears the results, keeps the date and a station both
        // servers list, and searches nothing.
        let rig = h.state_mut();
        rig.d.single.groups[0].children[0].1 = true;
        assert!(rig.d.set_source(&mut rig.svc, Source::Ecallisto));
        assert!(rig.d.single.groups.is_empty());
        assert!(rig.d.single.status.is_empty());
        assert_eq!(rig.d.single.date, chrono::NaiveDate::from_ymd_opt(2024, 1, 2).unwrap());
        step_until(&mut h, "e-CALLISTO stations", |r| r.d.single.loader.state == LoaderState::Loaded);
        assert!(!h.state().d.busy());
        assert_eq!(h.state().d.single.combo.text(), "BIR");
        assert!(h.state().d.single.loader.summary.starts_with("e-CALLISTO: 2 stations"));

        // A station the new server lacks falls back to the first listed.
        script(&mut h, "single|station|ONLY-ecallisto");
        script(&mut h, "server|astrodoncel");
        step_until(&mut h, "Astrodoncel stations", |r| r.d.single.loader.state == LoaderState::Loaded);
        assert_eq!(h.state().d.single.combo.text(), "BIR");
        set_mock_http(None);
    }

    #[test]
    fn a_preview_window_names_its_server() {
        let w =
            super::super::preview::PreviewWindow::new(&egui::Context::default(), Vec::new()).with_source("Astrodoncel");
        assert!(w.title().ends_with("(Astrodoncel)"), "{}", w.title());
    }
}
