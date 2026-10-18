//! Solar Events → Radio Bursts → Learmonth (`LearmonthDownloaderApp`):
//! a day's raw `.srs` file from the archive, its 15-minute chunks, and FIT
//! files written from them (to a folder, or straight into the main window).

use std::path::PathBuf;

use callisto_core::radio::learmonth::{
    self, LearmonthChunk, LearmonthError, learmonth_fit_filename, list_learmonth_chunks, resolve_learmonth_url,
    write_learmonth_chunk_fit,
};
use chrono::{NaiveDate, NaiveTime};
use egui::{Id, vec2};

use super::jobs::Job;
use super::{at, lay_out};
use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;
use crate::ui::itemview::{self, Check, Event, Row, Style, ViewState};
use crate::ui::qlayout::{Align, BoxLayout, Container, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::qt;

struct Loaded {
    path: PathBuf,
    url: String,
    filename: String,
    size: u64,
}

pub enum ConvertMsg {
    Text(String),
    Range(i64, i64),
    Value(i64),
}

pub enum LearmonthAction {
    Import(Vec<String>),
    Close,
}

/// The day's file and its chunks, or why not.
type LoadResult = Result<(PathBuf, Vec<LearmonthChunk>), LearmonthError>;

pub struct LearmonthDialog {
    date: NaiveDate,
    time: NaiveTime,
    cache_dir: PathBuf,
    loaded: Option<Loaded>,
    chunks: Vec<(LearmonthChunk, bool)>,
    view: ViewState,
    raw_label: String,
    status: String,
    busy: bool,
    progress: qt::Progress,
    load: Option<Job<(), LoadResult>>,
    convert: Option<Job<ConvertMsg, Result<Vec<String>, String>>>,
    import_after: bool,
    /// Converted files waiting to be imported (`import_request`).
    pending_import: Option<Vec<String>>,
    notices: Vec<Notice>,
    size: Option<egui::Vec2>,
    /// Another modal window (the import dialog) is in front.
    pub blocked: bool,
    focused: bool,
}

/// `LearmonthDownloaderApp._format_size`.
fn format_size(bytes: u64) -> String {
    let mut v = bytes as f64;
    let units = ["B", "KB", "MB", "GB"];
    let mut i = 0;
    while v >= 1024.0 && i < units.len() - 1 {
        v /= 1024.0;
        i += 1;
    }
    if i == 0 { format!("{} {}", v as u64, units[0]) } else { format!("{v:.1} {}", units[i]) }
}

impl LearmonthDialog {
    pub fn new(cache_dir: PathBuf) -> Self {
        let now = chrono::Local::now();
        LearmonthDialog {
            date: now.date_naive(),
            time: now.time().with_nanosecond(0).unwrap_or(now.time()),
            cache_dir,
            loaded: None,
            chunks: Vec::new(),
            view: ViewState::default(),
            raw_label: "Load a Learmonth date to inspect the daily .srs archive file.".into(),
            status: String::new(),
            busy: false,
            progress: qt::Progress::range(1),
            load: None,
            convert: None,
            import_after: false,
            pending_import: None,
            notices: Vec::new(),
            size: None,
            blocked: false,
            focused: false,
        }
    }

    fn set_busy(&mut self, busy: bool, indeterminate: bool) {
        self.busy = busy;
        self.progress = if busy && indeterminate { qt::Progress::busy() } else { qt::Progress::range(1) };
    }

    fn checked(&self) -> Vec<LearmonthChunk> {
        self.chunks.iter().filter(|c| c.1).map(|c| c.0.clone()).collect()
    }

    fn notice(&mut self, kind: NoticeKind, title: &str, text: impl Into<String>) {
        self.notices.push(Notice { kind, title: title.into(), text: text.into() });
    }

    fn layout(&self, ui: &egui::Ui) -> Layout {
        let query = BoxLayout::h([9; 4], 6)
            .widget(qt::label_spec(ui, "date_label", "Date:"))
            .widget(qt::date_spec(ui, "date", "9999-12-31").min_width(140))
            .widget(qt::label_spec(ui, "time_label", "Time:"))
            .widget(qt::spin_spec(ui, "time", "23:59:59 ").min_width(120))
            .stretch(1)
            .widget(qt::button_spec(ui, "show", "Show Available Data", false));
        let raw = BoxLayout::v([9; 4], 6)
            .widget(qt::wrap_label_spec(ui, "raw_label", &self.raw_label, false))
            .add_aligned(Item::Widget(qt::button_spec(ui, "download", "Download", false)), Align::LEFT);
        let mut buttons = BoxLayout::h([0; 4], 6);
        for (id, text) in BUTTONS {
            buttons = buttons.widget(qt::button_spec(ui, id, text, false));
        }
        let chunk = BoxLayout::v([9; 4], 6).widget(qt::view_spec("list")).add(buttons.into_item());
        let group = |id: &'static str, l: BoxLayout| {
            Item::Container(Container {
                spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred),
                margins: [13, 27, 13, 13],
                layout: Box::new(l.into()),
            })
        };
        BoxLayout::v([12; 4], 10)
            .add(group("query", query))
            .add(group("raw", raw))
            .add(group("chunks", chunk))
            .widget(qt::wrap_label_spec(ui, "status", &self.status, false))
            .widget(qt::progress_spec("progress").hidden(!self.busy))
            .into()
    }

    /// Draws the dialog. Returns what the main window should do.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<LearmonthAction> {
        let size = *self.size.get_or_insert_with(|| super::fit_to_screen(ctx, 960.0, 640.0));
        self.tick();
        let mut action = None;
        let modal = !self.notices.is_empty() || self.blocked;
        super::window(ctx, "learmonth_downloader", "Learmonth Solar Radio Browser", size, vec2(545.0, 424.0), p, |ui, close| {
            if close {
                action = Some(LearmonthAction::Close);
            }
            if let Some(a) = self.body(ui, ctx, p) {
                action = Some(a);
            }
            if modal {
                super::block_input(ui, Id::new("learmonth_modal"));
            }
        });
        if let Some(n) = self.notices.first()
            && crate::ui::dialogs::message_box(ctx, n, p)
        {
            self.notices.remove(0);
        }
        action
    }

    /// One frame of the dialog's contents in `ui` (its whole window).
    pub fn frame(&mut self, ui: &mut egui::Ui, p: &Palette) -> Option<LearmonthAction> {
        self.tick();
        let ctx = ui.ctx().clone();
        self.body(ui, &ctx, p)
    }

    /// Sets the observation date and time (tests, captures).
    pub fn set_when(&mut self, date: NaiveDate, time: NaiveTime) {
        self.date = date;
        self.time = time;
    }

    /// Scripted "Show Available Data" and checking a chunk (tests).
    pub fn script_show(&mut self, ctx: &egui::Context) {
        self.show_available(ctx);
    }

    pub fn script_check(&mut self, i: usize) {
        if let Some(c) = self.chunks.get_mut(i) {
            c.1 = true;
        }
    }

    pub fn is_busy(&self) -> bool {
        self.busy
    }

    pub fn chunk_count(&self) -> usize {
        self.chunks.len()
    }

    fn tick(&mut self) {
        if let Some(job) = &mut self.load
            && let (_, Some(r)) = job.poll()
        {
            self.load = None;
            self.set_busy(false, false);
            match r {
                Ok((path, chunks)) => {
                    let filename = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
                    let size = path.metadata().map_or(0, |m| m.len());
                    let url = resolve_learmonth_url(self.date);
                    self.raw_label = format!(
                        "Archive file: {filename}\nURL: {url}\nCached file: {}\nSize: {}\nAvailable chunks: {}",
                        path.display(),
                        format_size(size),
                        chunks.len()
                    );
                    self.loaded = Some(Loaded { path, url, filename, size });
                    self.chunks = chunks.into_iter().map(|c| (c, false)).collect();
                    self.view = ViewState::default();
                    // `_highlight_matching_chunk`.
                    let target = self.date.and_time(self.time);
                    match self.chunks.iter().position(|(c, _)| c.start_dt <= target && target < c.end_dt) {
                        Some(i) => {
                            self.view.current = Some(i);
                            self.status = format!("Loaded {} Learmonth chunks. Highlighted chunk #{} for the selected time.", self.chunks.len(), i + 1);
                        }
                        None => self.status = format!("Loaded {} Learmonth chunks for the selected day.", self.chunks.len()),
                    }
                }
                Err(LearmonthError::NotFound(m)) => {
                    self.raw_label = "No Learmonth raw archive file was found for the selected date.".into();
                    self.status.clear();
                    self.notice(NoticeKind::Information, "No Data Found", if m.is_empty() { "No Learmonth data were found.".into() } else { m });
                }
                Err(LearmonthError::Archive(m)) => {
                    self.raw_label = "Could not load Learmonth archive metadata.".into();
                    self.status.clear();
                    self.notice(NoticeKind::Critical, "Learmonth Error", if m.is_empty() { "Unknown Learmonth archive error.".into() } else { m });
                }
            }
        }
        if let Some(job) = &mut self.convert {
            let (msgs, done) = job.poll();
            for m in msgs {
                match m {
                    ConvertMsg::Text(t) => self.status = t,
                    ConvertMsg::Range(a, b) => {
                        self.progress.min = a;
                        self.progress.max = b.max(a);
                        self.progress.value = a;
                    }
                    ConvertMsg::Value(v) => self.progress.value = v,
                }
            }
            if let Some(r) = done {
                self.convert = None;
                self.set_busy(false, false);
                self.status.clear();
                match r {
                    Ok(paths) if self.import_after => self.pending_import = Some(paths),
                    Ok(paths) => self.notice(NoticeKind::Information, "Conversion Complete", format!("Converted {} Learmonth chunk(s) to FIT format.", paths.len())),
                    Err(m) => self.notice(NoticeKind::Critical, "Conversion Failed", m),
                }
            }
        }
    }

    fn body(&mut self, ui: &mut egui::Ui, ctx: &egui::Context, p: &Palette) -> Option<LearmonthAction> {
        if let Some(paths) = self.pending_import.take() {
            return Some(LearmonthAction::Import(paths));
        }
        let (g, o) = lay_out(&self.layout(ui), ui.max_rect());
        let r = |id: &str| at(&g, o, id);
        qt::group_box(ui, r("query"), "Observation Parameters", p);
        qt::group_box(ui, r("raw"), "Daily Raw File", p);
        qt::group_box(ui, r("chunks"), "Available 15-Minute Chunks", p);
        qt::label(ui, r("date_label"), "Date:", p.text);
        qt::label(ui, r("time_label"), "Time:", p.text);
        if !self.focused {
            // The dialog's first control takes the focus, its year selected.
            qt::focus_first_section(ui.ctx(), Id::new("lm_date"), 4);
            self.focused = true;
        }
        qt::date_edit(ui, r("date"), Id::new("lm_date"), &mut self.date, true, p);
        qt::time_edit(ui, r("time"), Id::new("lm_time"), &mut self.time, true, p);
        let busy = self.busy;
        let has_day = self.loaded.as_ref().is_some_and(|l| l.path.is_file());
        let has_chunks = !self.chunks.is_empty();
        let checked = self.chunks.iter().filter(|c| c.1).count();
        if qt::button(ui, r("show"), Id::new("lm_show"), "Show Available Data", false, !busy, p).clicked() {
            self.show_available(ctx);
        }
        qt::wrapped(ui, r("raw_label"), &self.raw_label, p.text, false);
        if qt::button(ui, r("download"), Id::new("lm_download"), "Download", false, !busy && has_day, p).clicked() {
            self.download_day();
        }
        let list = r("list");
        let style = Style::plain_list();
        let rows: Vec<Row> = self.chunks.iter().map(|(c, on)| Row::new(vec![c.label()]).checked(Check::from_bool(*on))).collect();
        let visible: Vec<usize> = (0..rows.len()).collect();
        let w = itemview::viewport(list, &style).width();
        for e in itemview::item_view(ui, list, Id::new("lm_chunks"), &[], &[w], &rows, &visible, &style, &mut self.view, true, p) {
            if let Event::Toggled(i) = e {
                self.chunks[i].1 ^= true;
            }
        }
        let enabled = [!busy && has_chunks, !busy && has_chunks, !busy && checked > 0, !busy && checked > 0];
        for ((id, text), on) in BUTTONS.iter().zip(enabled) {
            if qt::button(ui, r(id), Id::new(("lm", *id)), text, false, on, p).clicked() {
                match *id {
                    "all" | "none" => {
                        for c in &mut self.chunks {
                            c.1 = *id == "all";
                        }
                    }
                    "convert" => self.convert_selected(ctx, false),
                    _ => self.convert_selected(ctx, true),
                }
            }
        }
        qt::wrapped(ui, r("status"), &self.status, p.text, false);
        if self.busy {
            qt::progress_bar(ui, r("progress"), &self.progress, p);
        }
        None
    }

    /// `show_available_data`.
    fn show_available(&mut self, ctx: &egui::Context) {
        if self.load.is_some() {
            return self.notice(NoticeKind::Information, "Learmonth Loader", "Learmonth data are already being loaded.");
        }
        self.chunks.clear();
        self.loaded = None;
        self.raw_label = "Loading Learmonth archive metadata...".into();
        self.status = "Downloading or reusing the cached Learmonth day file...".into();
        self.set_busy(true, true);
        let (day, dir) = (self.date, self.cache_dir.clone());
        self.load = Some(Job::spawn(ctx, move |_| {
            let path = learmonth::download_learmonth_day(day, &dir)?;
            let chunks = list_learmonth_chunks(&path)?;
            Ok((path, chunks))
        }));
    }

    fn download_day(&mut self) {
        let Some(loaded) = &self.loaded else {
            return self.notice(NoticeKind::Warning, "No File", "Load a Learmonth day file before downloading.");
        };
        if !loaded.path.is_file() {
            return self.notice(NoticeKind::Warning, "No File", "Load a Learmonth day file before downloading.");
        }
        let Some(dir) = super::batch::pick_folder("Select Download Folder") else { return };
        let dest = dir.join(&loaded.filename);
        let _ = (&loaded.url, loaded.size);
        match std::fs::copy(&loaded.path, &dest) {
            Ok(_) => self.notice(NoticeKind::Information, "Download Complete", format!("Learmonth raw file saved to:\n{}", dest.display())),
            Err(e) => self.notice(NoticeKind::Critical, "Download Failed", format!("Could not copy the Learmonth file:\n{e}")),
        }
    }

    /// `convert_selected_chunks` / `convert_and_import_selected`.
    fn convert_selected(&mut self, ctx: &egui::Context, import_after: bool) {
        let chunks = self.checked();
        if chunks.is_empty() {
            return self.notice(NoticeKind::Warning, "No Selection", "Please select at least one Learmonth chunk to convert.");
        }
        let out_dir = if import_after {
            let root = self.cache_dir.join("converted");
            let dir = root.join(format!("learmonth_fit_{}", chrono::Utc::now().format("%Y%m%d%H%M%S%3f")));
            if std::fs::create_dir_all(&dir).is_err() {
                return;
            }
            dir
        } else {
            match rfd::FileDialog::new().set_title("Select Output Folder").pick_folder() {
                Some(d) => d,
                None => return,
            }
        };
        if self.convert.is_some() {
            return self.notice(NoticeKind::Information, "Learmonth Conversion", "A Learmonth conversion is already in progress.");
        }
        let Some(day) = self.loaded.as_ref().map(|l| l.path.clone()).filter(|p| p.is_file()) else {
            return self.notice(NoticeKind::Warning, "No File", "Load a Learmonth day file before converting chunks.");
        };
        self.import_after = import_after;
        self.status = "Converting selected Learmonth chunks to FIT...".into();
        self.set_busy(true, false);
        self.convert = Some(Job::spawn(ctx, move |r| {
            r.send(ConvertMsg::Text("Converting selected Learmonth chunks...".into()));
            r.send(ConvertMsg::Range(0, chunks.len() as i64));
            r.send(ConvertMsg::Value(0));
            std::fs::create_dir_all(&out_dir).map_err(|e| format!("Could not convert Learmonth chunks to FIT:\n{e}"))?;
            let mut written = Vec::new();
            for (i, c) in chunks.iter().enumerate() {
                let out = out_dir.join(learmonth_fit_filename(c));
                let path = write_learmonth_chunk_fit(&day, c, &out).map_err(|e| format!("Could not convert Learmonth chunks to FIT:\n{e}"))?;
                written.push(path.to_string_lossy().into_owned());
                r.send(ConvertMsg::Value(i as i64 + 1));
            }
            Ok(written)
        }));
    }
}

const BUTTONS: [(&str, &str); 4] = [("all", "Select All"), ("none", "Deselect All"), ("convert", "Convert to FIT"), ("convert_import", "Convert and Import")];

use chrono::Timelike;
