//! Processing → Batch Processing → Open Batch Processor
//! (`BatchProcessingDialog` and `BatchProcessWorker`): every FIT file in a
//! folder written as a PNG, raw or background subtracted, in a colormap,
//! optionally with the main window's display range and visual settings or
//! a loaded view config.

use std::path::{Path, PathBuf};

use callisto_core::radio::batch::{
    OutputMode, background_method_label, batch_figure_spec, list_fit_files, locked_view_overlaps_data,
    unique_output_png_path,
};
use callisto_core::radio::fits_io::load_callisto_fits;
use callisto_core::radio::processing::BackgroundMethod;
use callisto_core::view_config::{DisplayRange, ViewConfig, VisualConfig};
use egui::{Id, Ui, vec2};

use crate::downloads::jobs::{Job, Reporter};
use crate::downloads::{at, lay_out};
use crate::fonts::Weight;
use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;
use crate::ui::qlayout::{BoxLayout, Container, GridLayout, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

/// `_COLORMAP_OPTIONS`.
pub const COLORMAPS: [&str; 11] =
    ["Custom", "viridis", "plasma", "inferno", "magma", "cividis", "turbo", "RdYlBu", "jet", "cubehelix", "bone_r"];
const METHODS: [(&str, BackgroundMethod); 3] = [
    ("Mean", BackgroundMethod::Mean),
    ("Median", BackgroundMethod::Median),
    ("Plotutil Median (dB)", BackgroundMethod::PlotutilMedianDb),
];

/// What the main window lends the dialog (its providers): the colormap,
/// the cold digits, and what a view config from it would hold.
#[derive(Clone, Debug, Default)]
pub struct BatchEnv {
    pub cmap: String,
    pub cold_digits: f64,
    /// `_current_display_range_payload`, when there is data.
    pub display_range: Option<DisplayRange>,
    /// `_view_config_visual_payload`.
    pub visual: VisualConfig,
    pub use_utc: bool,
}

impl BatchEnv {
    /// `_build_view_config_payload(include_range, include_visual)`.
    fn view_config(&self, include_range: bool, include_visual: bool) -> Option<ViewConfig> {
        let range = if include_range { Some(self.display_range?) } else { None };
        let visual = if include_visual { self.visual.clone() } else { VisualConfig::default() };
        let mut cfg = ViewConfig::build(range, visual, self.use_utc);
        cfg.include_visual = include_visual;
        Some(cfg)
    }
}

enum Msg {
    Text(String),
    Range(i64, i64),
    Value(i64),
}

/// `BatchProcessWorker`'s `finished` payload.
#[derive(Clone, Debug, Default)]
pub struct BatchSummary {
    pub total: usize,
    pub processed: usize,
    pub succeeded: usize,
    pub failed: usize,
    pub cancelled: bool,
    pub raw: bool,
    pub method: BackgroundMethod,
    pub cmap: String,
    pub locked_axes: bool,
    pub outputs: Vec<PathBuf>,
    /// (input file, message)
    pub errors: Vec<(PathBuf, String)>,
    pub warnings: Vec<(PathBuf, String)>,
}

impl BatchSummary {
    /// The lines `_on_finished` shows.
    pub fn text(&self) -> String {
        let yes = |b: bool| if b { "Yes" } else { "No" };
        let mut lines = vec![
            format!("Total files found: {}", self.total),
            format!("Processed: {}", self.processed),
            format!("Succeeded: {}", self.succeeded),
            format!("Failed: {}", self.failed),
            format!("Cancelled: {}", yes(self.cancelled)),
            format!("Output mode: {}", if self.raw { "Raw" } else { "Background Subtracted" }),
            format!("Background method: {}", if self.raw { "N/A" } else { background_method_label(self.method) }),
            format!("Colormap: {}", self.cmap),
            format!("Locked axes: {}", yes(self.locked_axes)),
        ];
        let name = |p: &PathBuf| p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        for (title, items) in [("Warnings:", &self.warnings), ("Errors:", &self.errors)] {
            if items.is_empty() {
                continue;
            }
            lines.push(String::new());
            lines.push(title.into());
            for (path, msg) in items.iter().take(8) {
                lines.push(format!("- {}: {msg}", name(path)));
            }
            if items.len() > 8 {
                lines.push(format!("- ... and {} more", items.len() - 8));
            }
        }
        lines.join("\n")
    }
}

struct RunOptions {
    input_dir: PathBuf,
    output_dir: PathBuf,
    mode: OutputMode,
    cmap: String,
    cold_digits: f64,
    view_config: Option<ViewConfig>,
}

/// `BatchProcessWorker.run`.
fn run(ctx: &egui::Context, o: RunOptions, r: &Reporter<Msg>) -> Result<BatchSummary, String> {
    if !o.input_dir.is_dir() {
        return Err("Input folder does not exist.".into());
    }
    std::fs::create_dir_all(&o.output_dir).map_err(|e| format!("Could not create output folder:\n{e}"))?;
    let files = list_fit_files(&o.input_dir, false);
    let total = files.len();
    r.send(Msg::Range(0, total as i64));
    r.send(Msg::Value(0));
    // The visual colormap wins when the config carries visual settings.
    let cmap = match &o.view_config {
        Some(c) if c.include_visual && !c.visual.cmap.trim().is_empty() => c.visual.cmap.clone(),
        _ => o.cmap.clone(),
    };
    let (raw, method) = match o.mode {
        OutputMode::Raw => (true, BackgroundMethod::Mean),
        OutputMode::BackgroundSubtracted(m) => (false, m),
    };
    let mut s = BatchSummary {
        total,
        raw,
        method,
        cmap: cmap.clone(),
        locked_axes: o.view_config.as_ref().is_some_and(|c| c.range.is_some()),
        ..Default::default()
    };
    if total == 0 {
        r.send(Msg::Text("No FIT files found in selected input folder.".into()));
        return Ok(s);
    }
    for (i, path) in files.iter().enumerate() {
        if r.cancelled() {
            break;
        }
        s.processed = i + 1;
        let base = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        r.send(Msg::Text(format!("Processing {base} ({}/{total})...", i + 1)));
        let one = || -> Result<(PathBuf, Option<String>), String> {
            let res = load_callisto_fits(path).map_err(|e| e.to_string())?;
            let out = unique_output_png_path(&o.output_dir, &base);
            let warning = (o.view_config.as_ref().is_some_and(|c| c.range.is_some())
                && !locked_view_overlaps_data(&res.freqs, &res.time, o.view_config.as_ref()))
            .then(|| "Locked display range does not overlap this file's time/frequency extent.".to_owned());
            let spec = batch_figure_spec(&res, &base, o.mode, &cmap, o.cold_digits, o.view_config.as_ref())?;
            let fig = callisto_core::mpl::figures::batch_figure(&spec);
            crate::figure_export::save_figure(ctx, &fig, &out, 300.0).map_err(|e| e.to_string())?;
            Ok((out, warning))
        };
        match one() {
            Ok((out, warning)) => {
                if let Some(w) = warning {
                    s.warnings.push((path.clone(), w));
                }
                s.outputs.push(out);
            }
            Err(e) => s.errors.push((path.clone(), e)),
        }
        r.send(Msg::Value(i as i64 + 1));
    }
    s.succeeded = s.outputs.len();
    s.failed = s.errors.len();
    s.cancelled = r.cancelled() && s.processed < total;
    r.send(Msg::Text(if s.cancelled { "Batch processing cancelled." } else { "Batch processing complete." }.into()));
    Ok(s)
}

pub struct BatchDialog {
    input_dir: String,
    output_dir: String,
    raw: bool,
    method: usize,
    cmap: usize,
    use_range: bool,
    use_visual: bool,
    loaded_config: Option<ViewConfig>,
    loaded_label: String,
    status: String,
    progress: qt::Progress,
    job: Option<Job<Msg, Result<BatchSummary, String>>>,
    close_after_finish: bool,
    start_due: bool,
    notices: Vec<Notice>,
    size: Option<egui::Vec2>,
    pub last_summary: Option<BatchSummary>,
}

pub enum BatchAction {
    Close,
}

impl BatchDialog {
    /// `BatchProcessingDialog(cmap_name_provider=…)`: the main window's colormap preselected.
    pub fn new(env: &BatchEnv) -> Self {
        let cmap = COLORMAPS.iter().position(|c| *c == env.cmap).unwrap_or(0);
        Self {
            input_dir: String::new(),
            output_dir: String::new(),
            raw: false,
            method: 0,
            cmap,
            use_range: false,
            use_visual: false,
            loaded_config: None,
            loaded_label: "No view config loaded.".into(),
            status: "Select input/output folders, then click Start.".into(),
            progress: qt::Progress { min: 0, max: 1, value: 0, format: "%p%".into() },
            job: None,
            close_after_finish: false,
            start_due: false,
            notices: Vec::new(),
            size: None,
            last_summary: None,
        }
    }

    /// The message boxes not yet shown, for the main window once the dialog closes.
    pub fn take_notices(&mut self) -> Vec<Notice> {
        std::mem::take(&mut self.notices)
    }

    pub fn is_running(&self) -> bool {
        self.job.as_ref().is_some_and(|j| !j.is_finished())
    }

    /// Start, as if clicked, on the next frame.
    pub fn click_start(&mut self) {
        self.start_due = true;
    }

    /// Fields for tests and captures.
    pub fn set_folders(&mut self, input: &str, output: &str) {
        self.input_dir = input.into();
        self.output_dir = output.into();
    }

    pub fn set_options(&mut self, raw: bool, method: usize, cmap: &str, use_range: bool, use_visual: bool) {
        self.raw = raw;
        self.method = method.min(METHODS.len() - 1);
        self.cmap = COLORMAPS.iter().position(|c| *c == cmap).unwrap_or(self.cmap);
        self.use_range = use_range;
        self.use_visual = use_visual;
    }

    fn notice(&mut self, kind: NoticeKind, title: &str, text: impl Into<String>) {
        self.notices.push(Notice { kind, title: title.into(), text: text.into() });
    }

    /// `_load_view_config_file`.
    fn load_view_config(&mut self) {
        let Some(path) = rfd::FileDialog::new()
            .set_title("Load View Config")
            .add_filter("e-CALLISTO View Config (*.efaview.json)", &["json"])
            .add_filter("JSON Files (*.json)", &["json"])
            .pick_file()
        else {
            return;
        };
        match std::fs::read_to_string(&path).map_err(|e| e.to_string()).and_then(|t| ViewConfig::parse(&t)) {
            Ok(cfg) => {
                self.loaded_config = Some(cfg);
                self.loaded_label = format!(
                    "Loaded: {}",
                    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
                );
            }
            Err(e) => {
                self.loaded_config = None;
                self.loaded_label = "No view config loaded.".into();
                self.notice(
                    NoticeKind::Warning,
                    "Load View Config Failed",
                    format!("Could not load view config:\n{e}"),
                );
            }
        }
    }

    /// `_selected_view_config`.
    fn selected_view_config(&self, env: &BatchEnv) -> Option<ViewConfig> {
        if let Some(c) = &self.loaded_config {
            return Some(c.clone());
        }
        if !self.use_range && !self.use_visual {
            return None;
        }
        env.view_config(self.use_range, self.use_visual)
    }

    /// `_start_batch`.
    pub fn start(&mut self, ctx: &egui::Context, env: &BatchEnv) {
        if self.is_running() {
            return self.notice(NoticeKind::Information, "Batch Processing", "A batch run is already in progress.");
        }
        let (input, output) = (self.input_dir.trim().to_owned(), self.output_dir.trim().to_owned());
        if input.is_empty() {
            return self.notice(NoticeKind::Warning, "Missing Input Folder", "Please select an input folder.");
        }
        if !Path::new(&input).is_dir() {
            return self.notice(NoticeKind::Warning, "Invalid Input Folder", "Selected input folder does not exist.");
        }
        if output.is_empty() {
            return self.notice(NoticeKind::Warning, "Missing Output Folder", "Please select an output folder.");
        }
        if let Err(e) = std::fs::create_dir_all(&output) {
            return self.notice(
                NoticeKind::Critical,
                "Output Folder Error",
                format!("Could not create output folder:\n{e}"),
            );
        }
        self.last_summary = None;
        self.progress = qt::Progress { min: 0, max: 1, value: 0, format: "%p%".into() };
        self.status = "Preparing batch processing...".into();
        let view_config = self.selected_view_config(env);
        if (self.use_range || self.use_visual) && self.loaded_config.is_none() && view_config.is_none() {
            return self.notice(
                NoticeKind::Warning,
                "Batch Processing",
                "The current display/view settings are unavailable.",
            );
        }
        let opts = RunOptions {
            input_dir: input.into(),
            output_dir: output.into(),
            mode: if self.raw { OutputMode::Raw } else { OutputMode::BackgroundSubtracted(METHODS[self.method].1) },
            cmap: COLORMAPS[self.cmap].to_owned(),
            cold_digits: env.cold_digits,
            view_config,
        };
        let c = ctx.clone();
        self.job = Some(Job::spawn(ctx, move |r| run(&c, opts, r)));
    }

    /// Progress and the finished run (`_on_progress_*`, `_on_finished`, `_on_failed`).
    fn poll(&mut self) -> bool {
        let Some(job) = &mut self.job else { return false };
        let (msgs, result) = job.poll();
        for m in msgs {
            match m {
                Msg::Text(t) => self.status = t,
                Msg::Range(lo, hi) => {
                    self.progress.min = lo;
                    self.progress.max = hi.max(lo);
                    self.progress.value = self.progress.value.max(lo);
                }
                Msg::Value(v) => {
                    self.progress.max = self.progress.max.max(v);
                    self.progress.value = v;
                }
            }
        }
        let Some(result) = result else { return false };
        self.job = None;
        match result {
            Ok(s) => {
                self.progress.max = self.progress.max.max(s.total.max(s.processed) as i64);
                self.progress.value = s.processed as i64;
                self.status = "Batch processing finished.".into();
                let text = s.text();
                if s.total == 0 {
                    self.notice(NoticeKind::Information, "Batch Processing", text);
                } else if s.failed > 0 {
                    self.notice(NoticeKind::Warning, "Batch Processing Finished With Errors", text);
                } else {
                    self.notice(NoticeKind::Information, "Batch Processing Complete", text);
                }
                self.last_summary = Some(s);
            }
            Err(e) => {
                self.status = "Batch processing failed.".into();
                self.notice(NoticeKind::Critical, "Batch Processing Failed", e);
            }
        }
        std::mem::take(&mut self.close_after_finish)
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let check = |id: &'static str, text: &str| {
            WidgetSpec::new(
                id,
                Size::new(widgets::text_width(ui, text, qt::FONT_PX, Weight::Regular).ceil() as i32 + 24, 17),
                Policy::Minimum,
                Policy::Fixed,
            )
        };
        let form = GridLayout::new([0; 4], 8, 10)
            .widget(qt::label_spec(ui, "in_label", "Input Folder"), 0, 0)
            .widget(qt::line_edit_spec("input"), 0, 1)
            .widget(qt::button_spec(ui, "in_browse", "Browse...", false), 0, 2)
            .widget(qt::label_spec(ui, "out_label", "Output Folder"), 1, 0)
            .widget(qt::line_edit_spec("output"), 1, 1)
            .widget(qt::button_spec(ui, "out_browse", "Browse...", false), 1, 2);
        let output =
            BoxLayout::h([9; 4], 6).widget(check("raw", "Raw")).widget(check("bg", "Background Subtracted")).stretch(1);
        let process = GridLayout::new([9; 4], 8, 8)
            .widget(qt::label_spec(ui, "method_label", "Background Method"), 0, 0)
            .widget(qt::combo_spec(ui, "method", &METHODS.map(|m| m.0)), 0, 1)
            .widget(qt::label_spec(ui, "cmap_label", "Colormap"), 1, 0)
            .widget(qt::combo_spec(ui, "cmap", &COLORMAPS), 1, 1);
        let aligned = GridLayout::new([9; 4], 8, 8)
            .widget(check("use_range", "Use current display range"), 0, 0)
            .widget(check("use_visual", "Use current visual settings"), 0, 1)
            .widget(qt::button_spec(ui, "load_config", "Load View Config...", false), 1, 0)
            .widget(qt::wrap_label_spec(ui, "loaded", &self.loaded_label, false), 1, 1);
        let group = |id: &'static str, l: Layout| {
            Item::Container(Container {
                spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred),
                margins: [13, 27, 13, 13],
                layout: Box::new(l),
            })
        };
        let buttons = BoxLayout::h([0; 4], 10)
            .stretch(1)
            .widget(qt::button_spec(ui, "start", "Start", false))
            .widget(qt::button_spec(ui, "cancel", "Cancel", false))
            .widget(qt::button_spec(ui, "close", "Close", false));
        BoxLayout::v([14; 4], 10)
            .add(Item::Layout(Box::new(form.into())))
            .add(group("output_group", output.into()))
            .add(group("process_group", process.into()))
            .add(group("aligned_group", aligned.into()))
            .widget(qt::wrap_label_spec(ui, "status", &self.status, false))
            .widget(qt::progress_spec("progress"))
            .add(buttons.into_item())
            .into()
    }

    /// The window size: `resize(760, 360)`, bounded by the layout's minimum.
    fn window_size(&self, ui: &Ui) -> egui::Vec2 {
        let s = self.layout(ui).summary();
        vec2(760f32.max(s.min.w as f32), 360f32.max(s.min.h as f32))
    }

    pub fn frame(&mut self, ui: &mut Ui, env: &BatchEnv, p: &Palette) -> Option<BatchAction> {
        qt::with_app_style(|| self.contents(ui, env, p))
    }

    fn contents(&mut self, ui: &mut Ui, env: &BatchEnv, p: &Palette) -> Option<BatchAction> {
        let mut action = None;
        if self.poll() {
            action = Some(BatchAction::Close);
        }
        let (g, o) = lay_out(&self.layout(ui), ui.max_rect());
        let r = |id: &str| at(&g, o, id);
        let idle = !self.is_running();
        qt::group_box(ui, r("output_group"), "Output Type", p);
        qt::group_box(ui, r("process_group"), "Processing Options", p);
        qt::group_box(ui, r("aligned_group"), "Aligned View", p);
        qt::label(ui, r("in_label"), "Input Folder", p.text);
        qt::label(ui, r("out_label"), "Output Folder", p.text);
        // The first focusable widget takes the focus when the window opens.
        let opened = Id::new("batch_focus_given");
        if !ui.data(|d| d.get_temp::<bool>(opened).unwrap_or(false)) {
            ui.memory_mut(|m| m.request_focus(Id::new("batch_input")));
            ui.data_mut(|d| d.insert_temp(opened, true));
        }
        qt::line_edit(
            ui,
            r("input"),
            Id::new("batch_input"),
            &mut self.input_dir,
            "Select folder containing FIT files",
            idle,
            p,
        );
        qt::line_edit(
            ui,
            r("output"),
            Id::new("batch_output"),
            &mut self.output_dir,
            "Select folder for output PNG files",
            idle,
            p,
        );
        for (id, out, title) in
            [("in_browse", true, "Select Input Folder"), ("out_browse", false, "Select Output Folder")]
        {
            if qt::button(ui, r(id), Id::new(("batch", id)), "Browse...", false, idle, p).clicked() {
                let current = if out { &self.input_dir } else { &self.output_dir };
                let start = if current.trim().is_empty() { dirs_home() } else { PathBuf::from(current.trim()) };
                if let Some(path) = rfd::FileDialog::new().set_title(title).set_directory(start).pick_folder() {
                    let s = path.to_string_lossy().into_owned();
                    if out { self.input_dir = s } else { self.output_dir = s }
                }
            }
        }
        if crate::analysis::radio(ui, r("raw"), Id::new("batch_raw"), "Raw", self.raw, idle, p) {
            self.raw = true;
        }
        if crate::analysis::radio(ui, r("bg"), Id::new("batch_bg"), "Background Subtracted", !self.raw, idle, p) {
            self.raw = false;
        }
        // `_sync_background_method_enabled`.
        let method_on = idle && !self.raw;
        qt::label(ui, r("method_label"), "Background Method", qt::text_color(method_on, p));
        let methods: Vec<String> = METHODS.iter().map(|m| m.0.to_owned()).collect();
        if let Some(i) =
            qt::combo(ui, r("method"), Id::new("batch_method"), &methods, Some(self.method), "", method_on, p)
        {
            self.method = i;
        }
        qt::label(ui, r("cmap_label"), "Colormap", p.text);
        let cmaps: Vec<String> = COLORMAPS.iter().map(|c| (*c).to_owned()).collect();
        if let Some(i) = qt::combo(ui, r("cmap"), Id::new("batch_cmap"), &cmaps, Some(self.cmap), "", idle, p) {
            self.cmap = i;
        }
        qt::check_box(
            ui,
            r("use_range"),
            Id::new("batch_use_range"),
            "Use current display range",
            &mut self.use_range,
            idle,
            p,
        );
        qt::check_box(
            ui,
            r("use_visual"),
            Id::new("batch_use_visual"),
            "Use current visual settings",
            &mut self.use_visual,
            idle,
            p,
        );
        if qt::button(ui, r("load_config"), Id::new("batch_load_config"), "Load View Config...", false, idle, p)
            .clicked()
        {
            self.load_view_config();
        }
        qt::wrapped(ui, r("loaded"), &self.loaded_label, p.text, false);
        qt::wrapped(ui, r("status"), &self.status, p.text, false);
        qt::progress_bar(ui, r("progress"), &self.progress, p);
        let start = qt::button(ui, r("start"), Id::new("batch_start"), "Start", false, idle, p).clicked();
        if start || std::mem::take(&mut self.start_due) {
            let ctx = ui.ctx().clone();
            self.start(&ctx, env);
        }
        if qt::button(ui, r("cancel"), Id::new("batch_cancel"), "Cancel", false, true, p).clicked() {
            // `_on_cancel_clicked`.
            match &self.job {
                Some(job) if !job.is_finished() => {
                    job.request_cancel();
                    self.status = "Cancelling current batch run...".into();
                }
                _ => action = Some(BatchAction::Close),
            }
        }
        if qt::button(ui, r("close"), Id::new("batch_close"), "Close", false, idle, p).clicked() {
            action = Some(BatchAction::Close);
        }
        if let Some(n) = self.notices.first()
            && crate::ui::dialogs::message_box(ui.ctx(), n, p)
        {
            self.notices.remove(0);
        }
        if self.is_running() {
            ui.ctx().request_repaint_after(std::time::Duration::from_millis(100));
        }
        action
    }

    /// Shows the dialog; a close while running cancels and closes after (`closeEvent`).
    pub fn show(&mut self, ctx: &egui::Context, env: &BatchEnv, p: &Palette) -> Option<BatchAction> {
        let mut action = None;
        let size = self.size.unwrap_or(vec2(760.0, 626.0));
        crate::downloads::window(
            ctx,
            "batch_processing",
            "Batch FIT Processing",
            size,
            vec2(401.0, 360.0),
            p,
            |ui, close| {
                if self.size.is_none() {
                    let want = self.window_size(ui);
                    self.size = Some(want);
                    ui.ctx().send_viewport_cmd(egui::ViewportCommand::InnerSize(want));
                }
                action = self.frame(ui, env, p);
                if close {
                    match &self.job {
                        Some(job) if !job.is_finished() => {
                            self.close_after_finish = true;
                            job.request_cancel();
                        }
                        _ => action = Some(BatchAction::Close),
                    }
                }
            },
        );
        action
    }
}

fn dirs_home() -> PathBuf {
    std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).map(PathBuf::from).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn summary_like_the_full_app() {
        let s = BatchSummary {
            total: 3,
            processed: 3,
            succeeded: 3,
            method: BackgroundMethod::PlotutilMedianDb,
            cmap: "inferno".into(),
            ..Default::default()
        };
        assert_eq!(
            s.text(),
            "Total files found: 3\nProcessed: 3\nSucceeded: 3\nFailed: 0\nCancelled: No\nOutput mode: Background Subtracted\nBackground method: Plotutil Median (dB)\nColormap: inferno\nLocked axes: No"
        );
    }
}
