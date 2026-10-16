//! The Spectral Overview tab: a station's full UTC day per focus code, as
//! Plotutil median-dB panels of four hours each
//! (`CallistoDownloaderApp._build_spectral_overview_tab`,
//! `SpectralOverviewWorker`, `SpectralOverviewScrollArea`).

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use callisto_core::mpl::figures::overview_figure;
use callisto_core::mpl::{AxesText, Axes, AxisSpec, Figure, HAlign, Label, Ticks, VAlign, WHITE, hex};
use callisto_core::radio::archive::{DOWNLOAD_TIMEOUT_S, agent, check_archive_server, day_url, extract_fits_links, fetch_listing, get_with_retries};
use callisto_core::radio::downloader::{self, Candidate};
use callisto_core::radio::spectral_overview::{
    DEFAULT_PANEL_RENDER_COLUMNS, FIGURE_SIZE, OverviewError, OverviewResult, OverviewSource, build_spectral_overview,
};
use chrono::NaiveDate;
use egui::{Id, Rect, Sense, Ui, pos2, vec2};

use super::jobs::{Job, Reporter};
use super::single::section;
use super::stations::{StationCombo, StationLoader, StationService};
use super::{Shared, at, lay_out};
use crate::figure_view::FigureView;
use crate::session::NoticeKind;
use crate::ui::qlayout::{BoxLayout, GridLayout, Layout, Policy, Size, WidgetSpec};
use crate::ui::qt;

/// `SpectralOverviewScrollArea._MINIMUM_PREVIEW_HEIGHT`.
const MIN_PREVIEW_HEIGHT: f32 = 650.0;

pub enum Msg {
    Range(i64, i64),
    Value(i64),
    Text(String),
    FocusCodes(Vec<String>),
}

type OverviewOutcome = Result<(Vec<OverviewResult>, Vec<String>), OverviewError>;

struct Preview {
    title: String,
    result: Option<OverviewResult>,
    view: FigureView,
    scroll: f32,
}

pub struct OverviewTab {
    pub date: NaiveDate,
    loader: StationLoader,
    combo: StationCombo,
    /// `(label, focus code)`; the first is "all".
    focus: Vec<(String, String)>,
    focus_current: usize,
    job: Option<Job<Msg, OverviewOutcome>>,
    progress: qt::Progress,
    status: String,
    tooltip: String,
    previews: Vec<Preview>,
    current: usize,
    cancelling: bool,
}

impl Default for OverviewTab {
    fn default() -> Self {
        Self::new()
    }
}

fn placeholder() -> Preview {
    // `Figure(figsize=(12, 8))`, axis off, a grey note in the middle.
    let mut x = AxisSpec::origin(hex(0x000000, 1.0));
    x.major = Ticks::None;
    x.minor = Ticks::None;
    x.ticks_low = false;
    x.ticks_high = false;
    x.labels_low = false;
    let y = x.clone();
    let ax = Axes {
        position: [0.125, 0.11, 0.775, 0.77],
        box_aspect: None,
        xlim: (0.0, 1.0),
        ylim: (0.0, 1.0),
        facecolor: None,
        images: Vec::new(),
        x,
        y,
        grids: Vec::new(),
        texts: vec![AxesText {
            x: 0.5,
            y: 0.5,
            label: Label::new("Generated spectral overview will appear here.", 12.0, hex(0x737b84, 1.0)),
            ha: HAlign::Center,
            va: VAlign::Center,
        }],
        spine_color: WHITE,
        spine_width_pt: 0.0,
    };
    let fig = Figure { width: 1200.0, height: 800.0, dpi: 100.0, facecolor: WHITE, axes: vec![ax], texts: Vec::new() };
    Preview { title: "Preview".into(), result: None, view: FigureView::new(fig), scroll: 0.0 }
}

impl OverviewTab {
    pub fn new() -> Self {
        OverviewTab {
            date: chrono::Local::now().date_naive(),
            loader: StationLoader::default(),
            combo: StationCombo::default(),
            focus: vec![("All available focus codes".into(), String::new())],
            focus_current: 0,
            job: None,
            progress: qt::Progress::busy(),
            status: "Select a station and UTC date, then generate full-day Plotutil median-dB overviews for every available focus code.".into(),
            tooltip: String::new(),
            previews: vec![placeholder()],
            current: 0,
            cancelling: false,
        }
    }

    pub fn is_running(&self) -> bool {
        self.job.is_some()
    }

    pub fn request_cancel(&mut self) {
        if let Some(j) = &self.job {
            j.request_cancel();
        }
    }

    pub fn request_stations(&mut self, svc: &StationService) {
        if let Some(e) = self.loader.request(svc, &[self.date]) {
            self.combo.apply(&e);
        }
    }

    fn reset_focus(&mut self) {
        self.focus = vec![("All available focus codes".into(), String::new())];
        self.focus_current = 0;
    }

    pub fn tick(&mut self, sh: &mut Shared) {
        if let Some(e) = self.loader.tick(&sh.ctx, sh.svc, sh.resolved) {
            self.combo.apply(&e);
            // currentTextChanged resets the focus selector.
            self.reset_focus();
        }
        let Some(job) = &mut self.job else { return };
        let (msgs, done) = job.poll();
        for m in msgs {
            match m {
                Msg::Range(a, b) => {
                    self.progress.min = a;
                    self.progress.max = b;
                }
                Msg::Value(v) => self.progress.value = v,
                Msg::Text(t) => {
                    if !self.cancelling {
                        self.status = t;
                    }
                }
                Msg::FocusCodes(codes) => {
                    // `_update_overview_focus_codes`.
                    let requested = self.focus[self.focus_current].1.clone();
                    self.focus = vec![(format!("All available focus codes ({})", codes.len()), String::new())];
                    self.focus.extend(codes.iter().map(|c| (c.clone(), c.clone())));
                    self.focus_current = self.focus.iter().position(|f| !requested.is_empty() && f.1 == requested).unwrap_or(0);
                }
            }
        }
        let Some(outcome) = done else { return };
        self.job = None;
        self.cancelling = false;
        match outcome {
            Err(OverviewError::Cancelled) => self.status = "Spectral overview generation cancelled.".into(),
            Err(OverviewError::Failed(m)) => {
                self.status = "Spectral overview generation failed.".into();
                sh.out.notice(NoticeKind::Critical, "Spectral Overview Failed", if m.is_empty() { "Could not generate the spectral overview.".to_owned() } else { m });
            }
            Ok((results, errors)) => {
                let total: usize = results.iter().map(|r| r.total_sources).sum();
                let loaded: usize = results.iter().map(|r| r.loaded_sources).sum();
                let warnings: usize = results.iter().map(|r| r.warnings.len()).sum::<usize>() + errors.len();
                let codes = results.iter().map(|r| r.focus_code.clone()).collect::<Vec<_>>().join(", ");
                let mut status = format!("Generated {} focus-code overview(s): {codes}. Loaded {loaded}/{total} FITS file(s).", results.len());
                let mut details = errors.clone();
                details.extend(results.iter().flat_map(|r| r.warnings.iter().map(move |w| format!("Focus {}: {w}", r.focus_code))));
                if warnings > 0 {
                    status.push_str(&format!(" {warnings} warning(s); hover for details."));
                    self.tooltip = details.join("\n");
                } else {
                    self.tooltip.clear();
                }
                self.status = status;
                self.previews = results
                    .into_iter()
                    .map(|r| {
                        let fig = overview_figure(&r, FIGURE_SIZE.0 * 100.0, FIGURE_SIZE.1 * 100.0);
                        Preview { title: format!("Focus {}", r.focus_code), result: Some(r), view: FigureView::new(fig), scroll: 0.0 }
                    })
                    .collect();
                self.current = 0;
            }
        }
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let running = self.job.is_some();
        let focus_labels: Vec<&str> = self.focus.iter().map(|f| f.0.as_str()).collect();
        let grid = GridLayout::new([9; 4], 8, 8)
            .column_stretch(3, 1)
            .widget(qt::label_spec(ui, "date_label", "Date (UTC):"), 0, 0)
            .widget(qt::date_spec(ui, "date", "9999-12-31").min_width(128), 0, 1)
            .widget(qt::label_spec(ui, "station_label", "Station:"), 0, 2)
            .widget_span(qt::combo_len_spec("station", 22).min_width(180), 0, 3, 1, 3)
            .widget_span(super::status_label(ui, "stations_status", &self.loader.summary), 1, 0, 1, 6)
            .widget(qt::label_spec(ui, "focus_label", "Focus code:"), 2, 0)
            .widget(qt::combo_spec(ui, "focus", &focus_labels).min_width(180), 2, 1)
            .widget_span(qt::button_spec(ui, "generate", "Generate Overview", true), 2, 2, 1, 2)
            .widget(qt::button_spec(ui, "cancel", "Cancel", false), 2, 4)
            .widget(qt::button_spec(ui, "export", "Export...", false), 2, 5)
            .widget_span(qt::progress_spec("progress").hidden(!running), 3, 0, 1, 6)
            .widget_span(super::status_label(ui, "status", &self.status), 4, 0, 1, 6);
        let plot = BoxLayout::v([9; 4], 6).widget(WidgetSpec::new("preview_tabs", Size::new(620, 484), Policy::Expanding, Policy::Expanding));
        BoxLayout::v([2, 10, 2, 2], 8).add(section("controls", grid)).add_stretched(section("plot", plot), 1).into()
    }

    pub fn show(&mut self, ui: &mut Ui, page: Rect, sh: &mut Shared) {
        let p = sh.p;
        let running = self.job.is_some();
        let (g, o) = lay_out(&self.layout(ui), page);
        qt::section_box(ui, at(&g, o, "controls"), "Full-Day Spectral Overview", p);
        qt::section_box(ui, at(&g, o, "plot"), "Overview Preview", p);
        for (id, text) in [("date_label", "Date (UTC):"), ("station_label", "Station:"), ("focus_label", "Focus code:")] {
            qt::label(ui, at(&g, o, id), text, p.text);
        }
        if qt::date_edit(ui, at(&g, o, "date"), Id::new("overview_date"), &mut self.date, !running, p) {
            self.request_stations(sh.svc);
            self.reset_focus();
        }
        if let Some(i) = qt::combo(ui, at(&g, o, "station"), Id::new("overview_station"), &self.combo.items, self.combo.current, &self.combo.placeholder, !running, p) {
            if self.combo.current != Some(i) {
                self.reset_focus();
            }
            self.combo.activate(i);
        }
        let status_rect = at(&g, o, "stations_status");
        if self.loader.retry {
            if qt::status_with_link(ui, status_rect, Id::new("overview_retry"), &self.loader.summary, "Retry", p)
                && let Some(e) = self.loader.reload(sh.svc)
            {
                self.combo.apply(&e);
            }
        } else {
            qt::status(ui, status_rect, &self.loader.summary, p);
        }
        let labels: Vec<String> = self.focus.iter().map(|f| f.0.clone()).collect();
        if let Some(i) = qt::combo(ui, at(&g, o, "focus"), Id::new("overview_focus"), &labels, Some(self.focus_current), "", !running, p) {
            self.focus_current = i;
        }
        if qt::button(ui, at(&g, o, "generate"), Id::new("overview_generate"), "Generate Overview", true, !running, p).clicked() {
            self.generate(sh);
        }
        if qt::button(ui, at(&g, o, "cancel"), Id::new("overview_cancel"), "Cancel", false, running && !self.cancelling, p).clicked() {
            self.status = "Cancelling spectral overview generation...".into();
            self.cancelling = true;
            self.request_cancel();
        }
        let can_export = !running && self.previews.get(self.current).is_some_and(|x| x.result.is_some());
        if qt::button(ui, at(&g, o, "export"), Id::new("overview_export"), "Export...", false, can_export, p).clicked() {
            self.export(sh);
        }
        if running {
            qt::progress_bar(ui, at(&g, o, "progress"), &self.progress, p);
        }
        let status_rect = at(&g, o, "status");
        qt::status(ui, status_rect, &self.status, p);
        if !self.tooltip.is_empty() {
            ui.interact(status_rect, Id::new("overview_status_tip"), Sense::hover()).on_hover_text(&self.tooltip);
        }
        self.preview_area(ui, at(&g, o, "preview_tabs"), p);
    }

    /// The inner tab widget: `#DownloaderTabs`-styled tabs over a Fusion
    /// pane holding the scrolling canvas.
    fn preview_area(&mut self, ui: &mut Ui, rect: Rect, p: &crate::theme::Palette) {
        let titles: Vec<&str> = self.previews.iter().map(|x| x.title.as_str()).collect();
        let enabled = vec![true; titles.len()];
        if let Some(i) = qt::tab_bar(ui, rect.min, Id::new("overview_tabs"), &titles, self.current, &enabled, p) {
            self.current = i;
        }
        let pane = Rect::from_min_max(pos2(rect.left(), rect.top() + 42.0), rect.max);
        ui.painter().rect_filled(pane, 0.0, p.qt.tab_frame());
        let area = pane.shrink(2.0);
        // QScrollArea: 1 px frame, the canvas as wide as the viewport.
        ui.painter().rect_stroke(area, 0.0, egui::Stroke::new(1.0, crate::theme::darken(p.qt.window, 1.4)), egui::StrokeKind::Inside);
        let vp = area.shrink(1.0);
        let Some(preview) = self.previews.get_mut(self.current) else { return };
        let mut canvas_h = |w: f32| MIN_PREVIEW_HEIGHT.max((w * (FIGURE_SIZE.1 / FIGURE_SIZE.0) as f32).round());
        let mut width = vp.width();
        let scroll = canvas_h(width) > vp.height();
        if scroll {
            width -= 10.0;
        }
        let height = canvas_h(width);
        let max_scroll = (height - vp.height()).max(0.0);
        if ui.rect_contains_pointer(vp) {
            let dy = ui.input(|i| i.smooth_scroll_delta.y);
            preview.scroll = (preview.scroll - dy).clamp(0.0, max_scroll);
        }
        preview.scroll = preview.scroll.clamp(0.0, max_scroll);
        let canvas = Rect::from_min_size(pos2(vp.left(), vp.top() - preview.scroll.round()), vec2(width, height));
        let clip = ui.new_child(egui::UiBuilder::new().max_rect(vp));
        let mut clipped = clip;
        clipped.set_clip_rect(vp);
        preview.view.show(&clipped, canvas);
        if scroll {
            let track = Rect::from_min_max(pos2(vp.right() - 10.0, vp.top() + 4.0), pos2(vp.right(), vp.bottom() - 4.0));
            let handle_h = (track.height() * vp.height() / height).max(24.0);
            let y = track.top() + (track.height() - handle_h) * if max_scroll > 0.0 { preview.scroll / max_scroll } else { 0.0 };
            let resp = ui.interact(track, Id::new(("overview_vbar", self.current)), Sense::drag());
            if resp.dragged() {
                preview.scroll = (preview.scroll + resp.drag_delta().y * height / track.height()).clamp(0.0, max_scroll);
            }
            ui.painter().rect_filled(Rect::from_min_size(pos2(track.left(), y), vec2(10.0, handle_h)), 5.0, p.border);
        }
        let _ = &mut canvas_h;
    }

    /// Scripted steps (`DownloaderDialog::run_script`).
    pub(crate) fn script(&mut self, args: &[&str], sh: &mut Shared) {
        match args {
            ["station", name] => {
                self.combo.set_text(name);
                self.reset_focus();
            }
            ["generate"] => self.generate(sh),
            _ => {}
        }
    }

    /// `generate_spectral_overview`.
    fn generate(&mut self, sh: &mut Shared) {
        if self.job.is_some() {
            return sh.out.notice(NoticeKind::Information, "Overview In Progress", "A spectral overview is already being generated.");
        }
        let station = self.combo.text().trim().to_owned();
        if station.is_empty() {
            return sh.out.notice(NoticeKind::Information, "No Station", format!("No station is selected. {}", self.loader.summary).trim().to_owned());
        }
        let focus = self.focus[self.focus_current].1.clone();
        self.progress = qt::Progress::busy();
        self.status = "Preparing full-day spectral overview...".into();
        self.tooltip.clear();
        let date = self.date;
        self.job = Some(Job::spawn(&sh.ctx, move |r| run(date, &station, &focus, r)));
    }

    /// `export_spectral_overview`.
    fn export(&mut self, sh: &mut Shared) {
        let Some(preview) = self.previews.get(self.current) else { return };
        let (Some(result), Some(fig)) = (&preview.result, preview.view.figure()) else {
            return sh.out.notice(NoticeKind::Information, "Export Spectral Overview", "Generate a spectral overview before exporting.");
        };
        let name = format!("{}_{}_{}_spectral_overview", result.station, result.observation_date.format("%Y%m%d"), result.focus_code);
        let Some(path) = rfd::FileDialog::new()
            .set_title("Export Spectral Overview")
            .set_file_name(&name)
            .add_filter("PNG (*.png)", &["png"])
            .add_filter("PDF (*.pdf)", &["pdf"])
            .add_filter("EPS (*.eps)", &["eps"])
            .add_filter("SVG (*.svg)", &["svg"])
            .add_filter("TIFF (*.tiff)", &["tiff", "tif"])
            .save_file()
        else {
            return;
        };
        let mut path: PathBuf = path;
        if path.extension().is_none() {
            path.set_extension("png");
        }
        match crate::figure_export::save_figure(&sh.ctx, fig, &path, 300.0) {
            Ok(()) => sh.out.notice(NoticeKind::Information, "Export Spectral Overview", format!("Spectral overview saved:\n{}", path.display())),
            Err(e) => sh.out.notice(NoticeKind::Critical, "Export Spectral Overview Failed", format!("Could not export spectral overview:\n{e}")),
        }
    }
}

/// `SpectralOverviewWorker.run`.
fn run(date: NaiveDate, station: &str, focus_code: &str, r: &Reporter<Msg>) -> OverviewOutcome {
    let flag = r.cancel_flag();
    let cancelled = move || flag.load(Ordering::Relaxed);
    let check = |c: &dyn Fn() -> bool| if c() { Err(OverviewError::Cancelled) } else { Ok(()) };
    let fail = |m: String| OverviewError::Failed(m);
    check(&cancelled)?;
    r.send(Msg::Text("Reading the selected archive day...".into()));
    let (ok, message) = check_archive_server();
    if !ok {
        return Err(fail(format!("FITS server is not responding: {message}")));
    }
    let url = day_url(date);
    let html = fetch_listing(&url).map_err(|e| fail(e.to_string()))?;
    let candidates = downloader::filter_spectral_overview_candidates(&extract_fits_links(&html), &url, station, date);
    if candidates.is_empty() {
        return Err(fail("No FITS files were found for the selected station on that UTC date.".into()));
    }
    let groups: BTreeMap<String, Vec<Candidate>> = downloader::group_by_focus_code(&candidates);
    let codes: Vec<String> = groups.keys().cloned().collect();
    if codes.is_empty() {
        return Err(fail("No focus codes were found for the selected station and date.".into()));
    }
    let selected_codes: Vec<String> = if focus_code.is_empty() {
        codes.clone()
    } else if groups.contains_key(focus_code) {
        vec![focus_code.to_owned()]
    } else {
        return Err(fail(format!("Focus code '{focus_code}' is not available for the selected station and date.")));
    };
    let selected: Vec<&Candidate> = selected_codes.iter().flat_map(|c| groups[c].iter()).collect();
    r.send(Msg::FocusCodes(codes));
    r.send(Msg::Range(0, selected.len() as i64));
    r.send(Msg::Value(0));

    let temp = std::env::temp_dir().join(format!("callisto_spectral_overview_{}_{}", std::process::id(), chrono::Utc::now().timestamp_millis()));
    std::fs::create_dir_all(&temp).map_err(|e| fail(e.to_string()))?;
    struct Cleanup(PathBuf);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    let _cleanup = Cleanup(temp.clone());
    let mut warnings: BTreeMap<String, Vec<String>> = selected_codes.iter().map(|c| (c.clone(), Vec::new())).collect();
    let mut sources: BTreeMap<String, Vec<OverviewSource>> = selected_codes.iter().map(|c| (c.clone(), Vec::new())).collect();
    let agent = agent(DOWNLOAD_TIMEOUT_S);
    for (i, c) in selected.iter().enumerate() {
        check(&cancelled)?;
        r.send(Msg::Text(format!("Downloading {} ({}/{})...", c.filename, i + 1, selected.len())));
        let out = temp.join(&c.filename);
        let got = (|| -> Result<(), String> {
            let (status, resp) = get_with_retries(&agent, &c.url).map_err(|e| e.to_string())?;
            if status >= 400 {
                return Err(format!("{status} Client Error for url: {}", c.url));
            }
            let mut reader = resp.into_body().into_reader();
            let mut file = std::fs::File::create(&out).map_err(|e| e.to_string())?;
            let mut buf = vec![0u8; 128 * 1024];
            loop {
                if cancelled() {
                    return Err("cancelled".into());
                }
                let n = std::io::Read::read(&mut reader, &mut buf).map_err(|e| e.to_string())?;
                if n == 0 {
                    break;
                }
                std::io::Write::write_all(&mut file, &buf[..n]).map_err(|e| e.to_string())?;
            }
            Ok(())
        })();
        check(&cancelled)?;
        match got {
            Ok(()) => sources.get_mut(&c.receiver_id).unwrap().push(OverviewSource {
                path: out.to_string_lossy().into_owned(),
                station: c.station.clone(),
                observed_at: c.observed_at.unwrap(),
                focus_code: c.receiver_id.clone(),
                filename: c.filename.clone(),
            }),
            Err(e) => warnings.get_mut(&c.receiver_id).unwrap().push(format!("{}: {e}", c.filename)),
        }
        r.send(Msg::Value(i as i64 + 1));
    }
    if sources.values().all(Vec::is_empty) {
        return Err(fail("None of the selected focus-code FITS files could be downloaded.".into()));
    }
    r.send(Msg::Range(0, 0));
    let mut results = Vec::new();
    let mut errors = Vec::new();
    for (k, code) in selected_codes.iter().enumerate() {
        check(&cancelled)?;
        let src = &sources[code];
        if src.is_empty() {
            errors.push(format!("Focus code {code}: no FITS files could be downloaded."));
            continue;
        }
        r.send(Msg::Text(format!("Building focus code {code} ({}/{})...", k + 1, selected_codes.len())));
        let flag = r.cancel_flag();
        let is_cancelled = move || flag.load(Ordering::Relaxed);
        match build_spectral_overview(src, DEFAULT_PANEL_RENDER_COLUMNS, &is_cancelled, &mut |m| r.send(Msg::Text(m.to_owned()))) {
            Ok(mut result) => {
                result.total_sources = groups[code].len();
                let mut w = warnings[code].clone();
                w.extend(result.warnings);
                result.warnings = w;
                results.push(result);
            }
            Err(OverviewError::Cancelled) => return Err(OverviewError::Cancelled),
            Err(OverviewError::Failed(e)) => errors.push(format!("Focus code {code}: {e}")),
        }
    }
    if results.is_empty() {
        let text = if errors.is_empty() { "No focus-code overview could be generated.".to_owned() } else { errors.join("\n") };
        return Err(fail(text));
    }
    check(&cancelled)?;
    Ok((results, errors))
}
