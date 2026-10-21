//! Application state and the eframe entry point.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::downloads::import::{ImportDialog, Outcome};
use crate::downloads::learmonth::{LearmonthAction, LearmonthDialog};
use crate::downloads::stations::StationService;
use crate::downloads::{DateTimeState, DownloaderAction, DownloaderDialog};
use crate::fonts::SystemFonts;
use crate::menus::{Command, MenuState};
use crate::session::{DisplayRangePreset, Notice, NoticeKind, Session};
use crate::theme::{Palette, ThemeMode};
use crate::ui::tool_dialogs::{
    FrequencyDialog, LightCurveSettingsDialog, SettingsAction, TextAnnotationDialog, TextPurpose,
};
use crate::ui::{self, dialogs, menubar::MenuBarState, sidebar::SidebarState};
use callisto_core::analysis::light_curve::LightCurveSettings;
use callisto_core::radio::timeline::Direction;

const SETTINGS_KEY: &str = "settings";
/// `recent_files.MAX_RECENT_ENTRIES`.
pub const MAX_RECENT_ENTRIES: usize = 10;
/// `recent_files.FITS_SUFFIXES`.
const FITS_SUFFIXES: [&str; 4] = [".fit", ".fits", ".fit.gz", ".fits.gz"];

/// What Lite remembers between runs. Separate from the full app's settings.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub theme_mode: ThemeMode,
    /// Expanded state of each sidebar section, by title.
    pub sidebar_sections: BTreeMap<String, bool>,
    pub sidebar_collapsed: bool,
    pub prefetch_adjacent: bool,
    pub auto_clean_max_outliers: bool,
    /// File → Recent Files: each entry is a selection opened together, newest first.
    pub recent_fits: Vec<Vec<String>>,
    /// Axis → Frequency scale, which the full app also keeps between runs.
    pub frequency_axis_log: bool,
    /// View → display range presets, as the full app stores them.
    pub display_range_presets: Vec<DisplayRangePreset>,
    /// Analysis → Plot Light Curves → Settings (`analysis/light_curve_settings_json`).
    pub light_curve: LightCurvePrefs,
    /// File → Recent Projects, newest first.
    pub recent_projects: Vec<String>,
    /// `runtime/clean_exit`: false while Lite runs, true once it closed.
    pub clean_exit: bool,
    /// `processing/presets_json`: the presets as `dump_presets_json` writes them.
    pub presets_json: String,
    /// `processing/default_preset_name`.
    pub default_preset_name: String,
}

/// The light-curve settings with the full app's keys.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct LightCurvePrefs {
    /// "single" or "multiple".
    pub mode: String,
    pub line_color: String,
    pub line_width: f64,
    pub show_frequency_label: bool,
    pub vertical_scale: f64,
    pub opacity: f64,
    pub line_style: String,
}

impl Default for LightCurvePrefs {
    fn default() -> Self {
        Self::from(&LightCurveSettings::default())
    }
}

impl From<&LightCurveSettings> for LightCurvePrefs {
    fn from(s: &LightCurveSettings) -> Self {
        Self {
            mode: if s.multiple { "multiple" } else { "single" }.into(),
            line_color: s.line_color.clone(),
            line_width: s.line_width,
            show_frequency_label: s.show_frequency_label,
            vertical_scale: s.vertical_scale,
            opacity: s.opacity,
            line_style: s.line_style.clone(),
        }
    }
}

impl LightCurvePrefs {
    /// `_normalize_light_curve_settings`.
    pub fn settings(&self) -> LightCurveSettings {
        let color = callisto_core::plot::qt_raster::parse_color(&self.line_color)
            .map_or_else(|| "#00e5ff".to_owned(), callisto_core::plot::qt_raster::color_name);
        LightCurveSettings {
            multiple: self.mode.eq_ignore_ascii_case("multiple"),
            line_color: color,
            line_width: self.line_width,
            show_frequency_label: self.show_frequency_label,
            vertical_scale: self.vertical_scale,
            opacity: self.opacity,
            line_style: self.line_style.clone(),
        }
        .normalized()
    }
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            theme_mode: ThemeMode::System,
            sidebar_sections: BTreeMap::new(),
            sidebar_collapsed: false,
            prefetch_adjacent: true,
            auto_clean_max_outliers: true,
            recent_fits: Vec::new(),
            frequency_axis_log: false,
            display_range_presets: Vec::new(),
            light_curve: LightCurvePrefs::default(),
            recent_projects: Vec::new(),
            clean_exit: true,
            presets_json: String::new(),
            default_preset_name: String::new(),
        }
    }
}

/// `_sanitize_export_stem`: one space per run of blanks, `_` per run of
/// characters Windows refuses, no edge spaces or dots.
pub fn sanitize_export_stem(text: &str) -> String {
    let mut out = String::new();
    let (mut space, mut bad) = (false, false);
    for c in text.trim().chars() {
        if c.is_whitespace() {
            if !space {
                out.push(' ');
            }
            (space, bad) = (true, false);
        } else if "\\/:*?\"<>|".contains(c) {
            if !bad {
                out.push('_');
            }
            (space, bad) = (false, true);
        } else {
            out.push(c);
            (space, bad) = (false, false);
        }
    }
    let out = out.trim_matches([' ', '.']).to_owned();
    if out.is_empty() { "export".into() } else { out }
}

/// `_view_config_path_with_extension`.
fn view_config_path_with_extension(path: &str) -> String {
    let text = path.trim();
    let lower = text.to_lowercase();
    if text.is_empty() || lower.ends_with(".efaview.json") || lower.ends_with(".json") {
        text.to_owned()
    } else {
        format!("{text}.efaview.json")
    }
}

pub fn is_fits_path(path: &str) -> bool {
    let lower = path.to_lowercase();
    FITS_SUFFIXES.iter().any(|s| lower.ends_with(s))
}

fn normalized(path: &str) -> String {
    let p = Path::new(path);
    let abs = if p.is_absolute() { p.to_path_buf() } else { std::env::current_dir().unwrap_or_default().join(p) };
    abs.components().collect::<PathBuf>().to_string_lossy().into_owned()
}

/// `_selection_key`: order and (on Windows) case do not matter.
fn selection_key(paths: &[String]) -> Vec<String> {
    let mut keys: Vec<String> = paths
        .iter()
        .map(|p| {
            let n = normalized(p);
            if cfg!(windows) { n.to_lowercase() } else { n }
        })
        .collect();
    keys.sort();
    keys
}

impl Settings {
    /// `add_recent_fits`.
    pub fn add_recent_fits(&mut self, paths: &[String]) {
        let selection: Vec<String> = paths.iter().filter(|p| !p.trim().is_empty()).map(|p| normalized(p)).collect();
        if selection.is_empty() {
            return;
        }
        let wanted = selection_key(&selection);
        self.recent_fits.retain(|e| selection_key(e) != wanted);
        self.recent_fits.insert(0, selection);
        self.recent_fits.truncate(MAX_RECENT_ENTRIES);
    }

    /// `add_recent_project`.
    pub fn add_recent_project(&mut self, path: &str) {
        if path.trim().is_empty() {
            return;
        }
        let project = normalized(path);
        let key = selection_key(std::slice::from_ref(&project));
        self.recent_projects.retain(|e| selection_key(std::slice::from_ref(e)) != key);
        self.recent_projects.insert(0, project);
        self.recent_projects.truncate(MAX_RECENT_ENTRIES);
    }

    /// `remove_recent_project`.
    pub fn remove_recent_project(&mut self, path: &str) {
        let key = selection_key(&[path.to_owned()]);
        self.recent_projects.retain(|e| selection_key(std::slice::from_ref(e)) != key);
    }

    /// `remove_recent_fits`.
    pub fn remove_recent_fits(&mut self, paths: &[String]) {
        let unwanted = selection_key(paths);
        self.recent_fits.retain(|e| selection_key(e) != unwanted);
    }
}

/// `fits_entry_label`: the first file, plus how many more.
pub fn fits_entry_label(paths: &[String]) -> String {
    let names: Vec<&str> = paths.iter().map(|p| callisto_core::radio::naming::basename(p)).collect();
    match names.len() {
        0 => String::new(),
        1 => names[0].to_owned(),
        n => format!("{} (+{} more)", names[0], n - 1),
    }
}

/// Which window an import came from: it closes when the import succeeds
/// (`import_success`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ImportSource {
    Downloader,
    Learmonth,
}

/// What an open input dialog is asking for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputPurpose {
    ExportFitsBitpix,
    SavePreset,
    ApplyPreset,
    SetDefaultPreset,
    DeletePreset,
    SaveRangePreset,
    ApplyRangePreset,
    DeleteRangePreset,
    EditTextLabel,
    MoveTextLabel,
}

/// What waits for the answer to "Unsaved Project".
#[derive(Clone, Debug)]
enum Deferred {
    LoadFile,
    OpenFits(Vec<String>),
    OpenProjectDialog,
    OpenProject(PathBuf),
    Close,
}

pub struct LiteApp {
    pub settings: Settings,
    /// Overrides the operating system's dark/light scheme (tests, screenshots).
    pub os_dark_override: Option<bool>,
    pub menubar: MenuBarState,
    pub sidebar: SidebarState,
    pub session: Session,
    fonts: SystemFonts,
    /// The Graph Properties family installed in egui ("" for none).
    installed_graph_family: String,
    /// The text annotations' font families installed in egui.
    installed_extra_families: Vec<String>,
    /// The update check, its status label and its windows.
    pub updates: crate::help::updates::Updates,
    /// Help → User Guide, Cite this Software, Report a Bug, About.
    pub guide: Option<crate::help::guide::UserGuide>,
    pub cite: Option<crate::help::cite::CitationDialog>,
    pub bug_report: Option<crate::help::bug_report::BugReportDialog>,
    pub about: Option<crate::ui::message_box::MessageBox>,
    /// A help window to bring to the front (its viewport's id).
    focus_help: Option<&'static str>,
    /// The startup splash's step while it shows (the window starts as the splash).
    pub splash: Option<usize>,
    /// The message box showing, if any.
    pub notice: Option<Notice>,
    pub combine_dialog: Option<dialogs::CombineOptionsDialog>,
    pub header_viewer: Option<dialogs::HeaderViewer>,
    pub rfi_dialog: Option<dialogs::RfiDialog>,
    pub display_range_dialog: Option<dialogs::DisplayRangeDialog>,
    pub combine_fits: Option<crate::ui::combine_dialog::CombineFitsDialog>,
    /// A name or choice being asked for, and what it is for.
    pub input: Option<(dialogs::InputDialog, InputPurpose)>,
    /// Analysis → Plot Light Curves → Enter frequency.
    pub light_curve_frequency: Option<FrequencyDialog>,
    /// Analysis → Plot Light Curves → Settings... (not modal).
    pub light_curve_settings: Option<LightCurveSettingsDialog>,
    /// Add Text and Edit Text Label.
    pub text_annotation: Option<TextAnnotationDialog>,
    /// Download → Launch FITS Downloader, and what it remembers when closed.
    pub downloader: Option<DownloaderDialog>,
    downloader_state: Option<DateTimeState>,
    /// The archive's stations per day, shared by every downloader tab.
    pub stations: StationService,
    pub learmonth: Option<LearmonthDialog>,
    /// The "Importing FITS Files" dialog and where the import came from.
    pub importing: Option<(ImportDialog, Option<ImportSource>)>,
    /// File → Generate Project Report, while it is being written.
    report: Option<crate::report_dialog::ReportDialog>,
    /// The frequency-combine options dialog is finishing an import.
    import_combine: Option<ImportSource>,
    /// Lite's own download caches.
    pub cache_root: PathBuf,
    pub learmonth_cache: PathBuf,
    /// The Maximum Intensities, Analyzer and Type II windows.
    pub analysis: crate::analysis::AnalysisWindows,
    /// The `.efaproj` behind the window.
    pub project: crate::project::ProjectState,
    /// What waits for the "Unsaved Project" question.
    unsaved_prompt: Option<Deferred>,
    /// The window may close: the question was answered.
    allow_close: bool,
    /// Where autosave snapshots go (`recovery_dir`).
    pub recovery_root: PathBuf,
    last_autosave: std::time::Instant,
    /// The last run did not close cleanly: offer its newest snapshot.
    recovery_prompt: Option<PathBuf>,
    /// Processing → Batch Processing → Open Batch Processor.
    pub batch: Option<crate::tools::batch::BatchDialog>,
    /// Multi-Station Comparison (also the downloader's Compare).
    pub comparison: Option<crate::tools::comparison::ComparisonWindow>,
    commands: Vec<Command>,
    applied_theme: Option<(bool, bool)>,
    frames: u64,
}

impl LiteApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        // First start: the full app's settings, if it has any here.
        let settings = cc.storage.and_then(|s| eframe::get_value(s, SETTINGS_KEY)).unwrap_or_else(|| {
            crate::settings_import::read_full_app().map(|v| crate::settings_import::import(&v)).unwrap_or_default()
        });
        let mut app = Self::with_settings(&cc.egui_ctx, settings);
        app.updates.schedule_startup();
        if crate::splash_enabled() {
            app.splash = Some(0);
        }
        app
    }

    pub fn with_settings(ctx: &egui::Context, mut settings: Settings) -> Self {
        egui_extras::install_image_loaders(ctx);
        let fonts = SystemFonts::load();
        fonts.install(ctx);
        let mut sidebar = SidebarState::default();
        sidebar.font_families.extend(fonts.families());
        let mut session = Session::new();
        session.freq_axis_log = settings.frequency_axis_log;
        session.tools.light_curve_settings = settings.light_curve.settings();
        session.prefetch_adjacent = settings.prefetch_adjacent;
        // Lite keeps its own download caches, next to its settings.
        let data_dir =
            eframe::storage_dir(crate::APP_NAME).unwrap_or_else(|| std::env::temp_dir().join(crate::APP_NAME));
        let cache_root = data_dir.join("callisto_cache");
        let learmonth_cache = data_dir.join("learmonth_cache");
        let online = std::env::var_os("CALLISTO_LITE_OFFLINE").is_none();
        let recovery_root = match std::env::var("CALLISTO_LITE_RECOVERY_DIR") {
            Ok(v) if !v.trim().is_empty() => PathBuf::from(v.trim()),
            _ => data_dir.join("recovery"),
        };
        // `_prompt_recovery_if_needed`.
        let prompt_disabled = std::env::var("E_CALLISTO_DISABLE_RECOVERY_PROMPT").is_ok_and(|v| v.trim() == "1");
        let recovery_prompt = if settings.clean_exit || prompt_disabled {
            None
        } else {
            callisto_core::project::recovery::latest_snapshot(&recovery_root)
        };
        settings.clean_exit = false;
        session.set_archive(Some(cache_root.clone()), online);
        let stations = if online {
            StationService::online()
        } else {
            StationService::new(std::sync::Arc::new(|_| {
                Err("The archive is offline (CALLISTO_LITE_OFFLINE).".to_owned())
            }))
        };
        Self {
            settings,
            os_dark_override: None,
            menubar: MenuBarState::default(),
            sidebar,
            session,
            fonts,
            installed_graph_family: String::new(),
            installed_extra_families: Vec::new(),
            updates: crate::help::updates::Updates::new(),
            guide: None,
            cite: None,
            bug_report: None,
            about: None,
            focus_help: None,
            splash: None,
            notice: None,
            combine_dialog: None,
            header_viewer: None,
            rfi_dialog: None,
            display_range_dialog: None,
            combine_fits: None,
            input: None,
            light_curve_frequency: None,
            light_curve_settings: None,
            text_annotation: None,
            downloader: None,
            downloader_state: None,
            stations,
            learmonth: None,
            batch: None,
            comparison: None,
            importing: None,
            report: None,
            import_combine: None,
            cache_root,
            learmonth_cache,
            analysis: Default::default(),
            project: crate::project::ProjectState::new(crate::project::ProjectState::default_cache_root(&data_dir)),
            unsaved_prompt: None,
            allow_close: false,
            recovery_root,
            last_autosave: std::time::Instant::now(),
            recovery_prompt,
            commands: Vec::new(),
            applied_theme: None,
            frames: 0,
        }
    }

    fn menu_state(&self) -> MenuState {
        MenuState {
            has_data: self.session.has_data(),
            recent_files: self.settings.recent_fits.iter().map(|e| fits_entry_label(e)).collect(),
            recent_projects: self
                .settings
                .recent_projects
                .iter()
                .map(|p| callisto_core::radio::naming::basename(p).to_owned())
                .collect(),
            has_project: self.project.has_content(&self.session),
            can_undo: self.session.can_undo(),
            can_redo: self.session.can_redo(),
            theme: self.settings.theme_mode,
            prefetch_adjacent: self.settings.prefetch_adjacent,
            auto_clean_max_outliers: self.settings.auto_clean_max_outliers,
            click_light_curve: self.session.tools.light_curve_click_mode,
            has_noise: self.session.noise_reduced.is_some(),
            has_light_curves: self.session.has_light_curves(),
            has_ruler_measurement: self.session.has_ruler_measurement(),
            checking_updates: self.updates.checking(),
        }
    }

    pub fn palette(&self, ctx: &egui::Context) -> Palette {
        let os_dark = self.os_dark_override.unwrap_or_else(|| ctx.system_theme() == Some(egui::Theme::Dark));
        Palette::new(self.settings.theme_mode.is_dark(os_dark), os_dark)
    }

    /// `CALLISTO_LITE_TRACE_STARTUP=<file>`: write how long the first
    /// complete frame (fonts and icons loaded) took after start, in ms.
    fn trace_startup(&mut self) {
        self.frames += 1;
        if self.frames != 3 {
            return;
        }
        if let (Some(path), Some(start)) = (std::env::var_os("CALLISTO_LITE_TRACE_STARTUP"), crate::STARTED.get()) {
            let _ = std::fs::write(
                path,
                format!(
                    "{:.0}
",
                    start.elapsed().as_secs_f64() * 1000.0
                ),
            );
        }
    }

    /// `open_fits_paths`, remembering what opened.
    pub fn open_paths(&mut self, paths: &[String]) -> bool {
        let before = self.session.opened.len();
        let ok = self.session.open_fits_paths(paths);
        self.after_open(before);
        ok
    }

    fn after_open(&mut self, before: usize) {
        let opened: Vec<Vec<String>> = self.session.opened.drain(before.min(self.session.opened.len())..).collect();
        for selection in opened {
            self.settings.add_recent_fits(&selection);
        }
        if let Some(pending) = &self.session.pending_combine {
            self.combine_dialog = Some(dialogs::CombineOptionsDialog::new(pending));
        }
    }

    /// File → Open: the platform's file dialog, several files to combine.
    fn load_file(&mut self) {
        let mut dialog = rfd::FileDialog::new()
            .set_title("Open")
            .add_filter("FITS files (*.fit *.fits *.fit.gz *.fits.gz)", &["fit", "fits", "gz", "FIT", "FITS"]);
        if let Some(source) = self.session.data.as_ref().and_then(|d| d.source_path.clone())
            && let Some(dir) = Path::new(&source).parent()
        {
            dialog = dialog.set_directory(dir);
        }
        if let Some(files) = dialog.pick_files() {
            let paths: Vec<String> = files.iter().map(|p| p.to_string_lossy().into_owned()).collect();
            self.open_paths(&paths);
        }
    }

    /// The rest of `open_display_range_dialog`, once the dialog is accepted.
    fn finish_display_range(&mut self, result: Option<dialogs::DisplayRangeResult>) {
        let Some(result) = result else { return };
        let s = &mut self.session;
        let Some(req) = s.display_range_request() else { return };
        let (time, freq) = match result {
            dialogs::DisplayRangeResult::Seconds { time, freq } => (time, freq),
            dialogs::DisplayRangeResult::Ut { seconds_of_day, freq } => {
                match s.ut_range_to_relative(seconds_of_day.0 as f64, seconds_of_day.1 as f64, req.full.xlim) {
                    Some(t) => (t, freq),
                    None => {
                        self.notice = Some(Notice {
                            kind: NoticeKind::Warning,
                            title: "Set Display Range".into(),
                            text: "Enter a valid UT start and stop time for this data range.".into(),
                        });
                        return;
                    }
                }
            }
        };
        s.apply_display_range(time.0, time.1, freq.0, freq.1, true);
    }

    /// `save_current_preset`, `apply_saved_preset`, `set_default_preset` and
    /// `delete_saved_preset`, once a name is given.
    fn finish_preset(&mut self, purpose: InputPurpose, value: String) {
        use callisto_core::presets;
        let list = presets::parse(&self.settings.presets_json);
        let find =
            |name: &str| list.iter().find(|p| p.get("name").map(|n| n.py_str()).as_deref() == Some(name)).cloned();
        match purpose {
            InputPurpose::SavePreset => {
                if value.trim().is_empty() {
                    return;
                }
                let Ok(preset) = presets::build(value.trim(), self.session.preset_settings_payload()) else { return };
                let Ok((merged, replaced)) = presets::upsert(&list, &preset) else { return };
                self.settings.presets_json = presets::dump(&merged);
                let name = preset.get("name").map(|n| n.py_str()).unwrap_or_default();
                self.session.set_active_preset(preset);
                self.session.mark_project_dirty();
                let verb = if replaced { "Updated" } else { "Saved" };
                self.session.show_message(format!("{verb} preset '{name}'."), 3000);
            }
            InputPurpose::ApplyPreset => {
                if let Some(preset) = find(&value)
                    && let Err(e) = self.session.apply_preset_payload(&preset, true, true, true)
                {
                    self.notice =
                        Some(Notice { kind: NoticeKind::Warning, title: "Preset Version Unsupported".into(), text: e });
                }
            }
            InputPurpose::SetDefaultPreset => {
                if value.is_empty() {
                    return;
                }
                self.settings.default_preset_name = presets::normalize_name(&value);
                self.session.show_message(format!("Default FITS-load preset set to '{value}'."), 3000);
            }
            InputPurpose::DeletePreset => {
                let (updated, removed) = presets::delete(&list, &value);
                if removed {
                    self.settings.presets_json = presets::dump(&updated);
                    if presets::name_key(&self.settings.default_preset_name) == presets::name_key(&value) {
                        self.settings.default_preset_name.clear();
                    }
                    self.session.show_message(format!("Deleted preset '{value}'."), 2500);
                }
            }
            _ => {}
        }
    }

    /// `_default_preset_payload`: the default preset, forgotten once deleted.
    fn sync_default_preset(&mut self) {
        let name = self.settings.default_preset_name.trim().to_owned();
        if name.is_empty() {
            self.session.default_preset = None;
            return;
        }
        let key = callisto_core::presets::name_key(&name);
        let found = callisto_core::presets::parse(&self.settings.presets_json)
            .into_iter()
            .find(|p| callisto_core::presets::name_key(&p.get("name").map(|n| n.py_str()).unwrap_or_default()) == key);
        if found.is_none() {
            self.settings.default_preset_name.clear();
        }
        self.session.default_preset = found;
    }

    /// A name or choice came back from an input dialog.
    fn finish_input(&mut self, purpose: InputPurpose, value: String) {
        if matches!(purpose, InputPurpose::EditTextLabel | InputPurpose::MoveTextLabel) {
            if let Some((index, _)) = self.session.text_annotations().into_iter().find(|(_, label)| *label == value) {
                self.text_label_chosen(purpose, index);
            }
            return;
        }
        if purpose == InputPurpose::ExportFitsBitpix {
            self.finish_export_fits(&value);
            return;
        }
        if matches!(
            purpose,
            InputPurpose::SavePreset
                | InputPurpose::ApplyPreset
                | InputPurpose::SetDefaultPreset
                | InputPurpose::DeletePreset
        ) {
            self.finish_preset(purpose, value);
            return;
        }
        let s = &mut self.session;
        let presets = &mut self.settings.display_range_presets;
        match purpose {
            InputPurpose::SaveRangePreset => {
                if value.trim().is_empty() {
                    return;
                }
                let Some(range) = s.current_display_range() else { return };
                match DisplayRangePreset::new(value.trim(), range, s.use_utc) {
                    Ok(preset) => {
                        let name = preset.name.clone();
                        let verb = if crate::session::upsert_preset(presets, preset) { "Updated" } else { "Saved" };
                        s.show_message(format!("{verb} display range preset '{name}'."), 3000);
                    }
                    Err(e) => {
                        self.notice = Some(Notice {
                            kind: NoticeKind::Warning,
                            title: "Save Display Range Preset".into(),
                            text: e,
                        })
                    }
                }
            }
            InputPurpose::ApplyRangePreset => {
                if let Some(preset) = presets.iter().find(|p| p.name == value).cloned() {
                    s.apply_display_range_preset(&preset);
                }
            }
            InputPurpose::DeleteRangePreset if crate::session::delete_preset(presets, &value) => {
                s.show_message(format!("Deleted display range preset '{value}'."), 2500);
            }
            _ => {}
        }
    }

    /// `_choose_text_annotation_index`: one label is taken directly, several are asked for.
    fn choose_text_label(&mut self, purpose: InputPurpose) {
        let title = if purpose == InputPurpose::EditTextLabel { "Edit Text Label" } else { "Move Text Label" };
        let rows = self.session.text_annotations();
        match rows.len() {
            0 => {
                self.notice = Some(Notice {
                    kind: NoticeKind::Information,
                    title: title.into(),
                    text: "There are no text labels available.".into(),
                })
            }
            1 => self.text_label_chosen(purpose, rows[0].0),
            _ => {
                let labels = rows.into_iter().map(|(_, l)| l).collect();
                self.input = Some((dialogs::InputDialog::item(title, "Choose label:", labels), purpose));
            }
        }
    }

    fn text_label_chosen(&mut self, purpose: InputPurpose, index: usize) {
        if purpose == InputPurpose::MoveTextLabel {
            self.session.move_text_label(index);
            return;
        }
        let Some(ann) = self.session.annotation(index).cloned() else { return };
        let style = self.session.annotation_text_style(&ann);
        self.text_annotation = Some(self.text_dialog("Edit Text Label", TextPurpose::Edit(index), &ann.text, &style));
    }

    fn text_dialog(
        &self,
        title: &str,
        purpose: TextPurpose,
        text: &str,
        style: &callisto_core::analysis::annotations::TextStyle,
    ) -> TextAnnotationDialog {
        let ui_family = self.fonts.ui_family().unwrap_or("Segoe UI");
        TextAnnotationDialog::new(title, purpose, text, style, ui_family, self.fonts.families())
    }

    /// `FitsHeaderViewerDialog.save_as_txt`.
    /// The help windows and the update check, each frame.
    fn help_windows(&mut self, ctx: &egui::Context, p: &Palette) {
        if let Some(id) = self.focus_help.take() {
            ctx.send_viewport_cmd_to(egui::ViewportId::from_hash_of(id), egui::ViewportCommand::Focus);
        }
        for (text, ms) in self.updates.show(ctx, p) {
            self.session.show_message(text, ms);
        }
        if let Some(g) = &mut self.guide
            && !g.show(ctx, p)
        {
            self.guide = None;
        }
        if let Some(c) = &mut self.cite
            && !c.show(ctx, p)
        {
            self.cite = None;
        }
        if let Some(b) = &mut self.bug_report {
            let (session, project_path) = (&self.session, self.project.path.clone());
            let provider = || bug_context(session, project_path.as_deref());
            if !b.show(ctx, p, &provider) {
                self.bug_report = None;
            }
        }
        if let Some(a) = &mut self.about
            && a.show(ctx, "about", p).is_some()
        {
            self.about = None;
        }
    }

    fn save_header_text(&mut self, default_name: &str, text: &str) {
        let Some(path) = rfd::FileDialog::new()
            .set_title("Save FITS Header")
            .set_file_name(default_name)
            .add_filter("Text files (*.txt)", &["txt"])
            .save_file()
        else {
            return;
        };
        let mut path = path.to_string_lossy().into_owned();
        if !path.to_lowercase().ends_with(".txt") {
            path.push_str(".txt");
        }
        let written =
            Path::new(&path).parent().map_or(Ok(()), std::fs::create_dir_all).and_then(|_| std::fs::write(&path, text));
        self.notice = Some(match written {
            Ok(()) => {
                Notice { kind: NoticeKind::Information, title: "Saved".into(), text: format!("Header saved:\n{path}") }
            }
            Err(e) => Notice {
                kind: NoticeKind::Critical,
                title: "Save Failed".into(),
                text: format!("Could not save header:\n{e}"),
            },
        });
    }

    /// `export_figure`: the main plot as an OriginPro graph (`save_figure`
    /// at 300 dpi, trimmed to the drawing); the screen keeps its own look.
    fn export_figure(&mut self, ctx: &egui::Context) {
        if !self.session.has_data() {
            self.notice = Some(Notice {
                kind: NoticeKind::Warning,
                title: "No File Loaded".into(),
                text: "Load a FITS file before exporting.".into(),
            });
            return;
        }
        let Some(path) =
            crate::analysis::max_window::export_dialog(&sanitize_export_stem(&self.session.graph_title_for_export()))
        else {
            return;
        };
        let shown = path.to_string_lossy().into_owned();
        if cfg!(windows) && shown.to_lowercase().starts_with("c:\\program files") {
            self.notice = Some(Notice {
                kind: NoticeKind::Warning,
                title: "Permission Denied".into(),
                text: "Windows does not allow saving files inside Program Files.
Please choose another folder such as Documents or Desktop."
                    .into(),
            });
            return;
        }
        let result = match self.session.origin_export_graph() {
            None => Err("There is no dynamic spectrum to export.".to_owned()),
            Some(graph) => {
                let fig = callisto_core::mpl::spectrum_figures::dynamic_spectrum_figure(&graph);
                crate::figure_export::save_figure_padded(ctx, &fig, &path, 300.0, 0.08).map_err(|e| e.to_string())
            }
        };
        self.notice = Some(match result {
            Ok(()) => Notice {
                kind: NoticeKind::Information,
                title: "Export Complete".into(),
                text: format!(
                    "Figure saved:
{shown}"
                ),
            },
            Err(e) => Notice {
                kind: NoticeKind::Critical,
                title: "Export Failed".into(),
                text: format!(
                    "An error occurred:
{e}"
                ),
            },
        });
    }

    /// `generate_project_report`: the report's data gathered here, then
    /// written by a worker behind the progress dialog.
    fn generate_project_report(&mut self, ctx: &egui::Context) {
        let info = |text: &str| {
            Some(Notice { kind: NoticeKind::Information, title: "Generate Project Report".into(), text: text.into() })
        };
        if !self.session.has_data() {
            self.notice = info("Load a FITS file first.");
            return;
        }
        if self.report.is_some() {
            self.notice = info("A project report is already being generated.");
            return;
        }
        let project = self.project.path.clone();
        let default = self.session.project_report_default_path(project.as_deref());
        let mut dialog =
            rfd::FileDialog::new().set_title("Generate Project Report").add_filter("PDF (*.pdf)", &["pdf"]);
        if let Some(name) = default.file_name() {
            dialog = dialog.set_file_name(name.to_string_lossy());
        }
        if let Some(dir) = default.parent().filter(|d| !d.as_os_str().is_empty()) {
            dialog = dialog.set_directory(dir);
        }
        let Some(mut path) = dialog.save_file() else { return };
        // `_pick_project_report_path`: a PDF, whatever the extension typed.
        if !path.extension().is_some_and(|e| e.eq_ignore_ascii_case("pdf")) {
            path.set_extension("pdf");
        }
        if cfg!(windows) && path.to_string_lossy().to_lowercase().starts_with("c:\\program files") {
            self.notice = Some(Notice {
                kind: NoticeKind::Warning,
                title: "Permission Denied".into(),
                text: "Windows does not allow saving files inside Program Files.\nPlease choose another folder such as Documents or Desktop.".into(),
            });
            return;
        }
        let input = self.session.project_report_input(ctx, project.as_deref(), self.analysis.type_ii.as_ref());
        self.report = Some(crate::report_dialog::ReportDialog::start(ctx, path, input));
    }

    /// `export_to_fits` once the BITPIX is chosen.
    fn finish_export_fits(&mut self, choice: &str) {
        let Some(data) = self.session.fits_export_source() else { return };
        let rec = callisto_core::radio::fits_export::recommend_bitpix(&data.data);
        let t = choice.trim();
        let bitpix = if t.starts_with('8') {
            8
        } else if t.starts_with("16") {
            16
        } else if t.starts_with("32") {
            32
        } else {
            rec
        };
        let name = format!("{}.fit", sanitize_export_stem(&self.session.graph_title_for_export()));
        let Some(path) = rfd::FileDialog::new()
            .set_title("Export to FITS")
            .set_file_name(name)
            .add_filter("FITS files (*.fit *.fits *.fit.gz *.fits.gz)", &["fit", "fits", "gz"])
            .save_file()
        else {
            return;
        };
        let mut path = path.to_string_lossy().into_owned();
        let lower = path.to_lowercase();
        if ![".fit", ".fits", ".fit.gz", ".fits.gz"].iter().any(|s| lower.ends_with(s)) {
            path.push_str(".fit");
        }
        if let Err(e) = self.session.export_fits(Path::new(&path), bitpix) {
            self.notice = Some(Notice {
                kind: NoticeKind::Critical,
                title: "Export Failed".into(),
                text: format!("Could not write FITS file:\n{e}"),
            });
        }
    }

    /// `export_analysis_log`.
    fn export_analysis_log(&mut self) {
        if !self.session.has_data() {
            self.notice = Some(Notice {
                kind: NoticeKind::Information,
                title: "Export Analysis Log".into(),
                text: "Load a FITS file first.".into(),
            });
            return;
        }
        let mut dialog = rfd::FileDialog::new()
            .set_title("Export Analysis Log")
            .set_file_name(sanitize_export_stem(&self.session.graph_title_for_export()))
            .add_filter("CSV (*.csv)", &["csv"])
            .add_filter("Text (*.txt)", &["txt"])
            .add_filter("All Files (*)", &["*"]);
        if let Some(dir) = self.project_start_dir() {
            dialog = dialog.set_directory(dir);
        }
        let Some(path) = dialog.save_file() else { return };
        let stem = path.with_extension("");
        let project = self.project.path.clone();
        if let Err(e) = self.session.export_analysis_log(&stem, project.as_deref()) {
            self.notice = Some(Notice {
                kind: NoticeKind::Critical,
                title: "Export Analysis Log Failed".into(),
                text: e.to_string(),
            });
        }
    }

    /// `export_provenance_report`.
    fn export_provenance_report(&mut self) {
        if !self.session.has_data() {
            self.notice = Some(Notice {
                kind: NoticeKind::Information,
                title: "Export Provenance".into(),
                text: "Load a FITS file first.".into(),
            });
            return;
        }
        let Some(path) = rfd::FileDialog::new()
            .set_title("Export Provenance Report")
            .set_file_name(format!("{}_provenance", sanitize_export_stem(&self.session.graph_title_for_export())))
            .add_filter("Provenance JSON (*.json)", &["json"])
            .add_filter("Markdown (*.md)", &["md"])
            .add_filter("All Files (*)", &["*"])
            .save_file()
        else {
            return;
        };
        if let Err(e) = self.session.export_provenance(&path.with_extension("")) {
            self.notice = Some(Notice {
                kind: NoticeKind::Critical,
                title: "Export Provenance Failed".into(),
                text: e.to_string(),
            });
        }
    }

    /// `export_view_config`.
    fn export_view_config(&mut self) {
        let Some(text) = self.session.view_config_text() else {
            self.notice = Some(Notice {
                kind: NoticeKind::Information,
                title: "Export View Config".into(),
                text: "Load a FITS file before exporting a view config.".into(),
            });
            return;
        };
        let stem = sanitize_export_stem(if self.session.filename().is_empty() {
            "view_config"
        } else {
            self.session.filename()
        });
        let Some(path) = rfd::FileDialog::new()
            .set_title("Export View Config")
            .set_file_name(view_config_path_with_extension(&stem))
            .add_filter("e-CALLISTO View Config (*.efaview.json)", &["json"])
            .add_filter("JSON Files (*.json)", &["json"])
            .save_file()
        else {
            return;
        };
        let path = view_config_path_with_extension(&path.to_string_lossy());
        if let Err(e) = std::fs::write(&path, text) {
            self.notice = Some(Notice {
                kind: NoticeKind::Critical,
                title: "Export View Config Failed".into(),
                text: format!("Could not write view config:\n{e}"),
            });
            return;
        }
        self.notice = Some(Notice {
            kind: NoticeKind::Information,
            title: "Export View Config".into(),
            text: format!("View config saved:\n{path}"),
        });
    }

    /// `import_view_config`.
    fn import_view_config(&mut self) {
        let Some(path) = rfd::FileDialog::new()
            .set_title("Import View Config")
            .add_filter("e-CALLISTO View Config (*.efaview.json)", &["json"])
            .add_filter("JSON Files (*.json)", &["json"])
            .pick_file()
        else {
            return;
        };
        let cfg = std::fs::read_to_string(&path)
            .map_err(|e| e.to_string())
            .and_then(|t| callisto_core::view_config::ViewConfig::parse(&t));
        match cfg {
            Err(e) => {
                self.notice = Some(Notice {
                    kind: NoticeKind::Warning,
                    title: "Import View Config Failed".into(),
                    text: format!("Could not read view config:\n{e}"),
                })
            }
            Ok(cfg) => {
                if self.session.apply_view_config(&cfg) {
                    self.notice = Some(Notice {
                        kind: NoticeKind::Information,
                        title: "Import View Config".into(),
                        text: "View config applied.".into(),
                    });
                }
            }
        }
    }

    /// `_maybe_prompt_save_dirty`: `then` now, or once the question is answered.
    fn after_unsaved_question(&mut self, then: Deferred) {
        let skip = std::env::var("E_CALLISTO_DISABLE_SAVE_PROMPT").is_ok_and(|v| v.trim() == "1");
        if skip || !self.project.is_dirty(&self.session) {
            self.run_deferred_now(then);
        } else {
            self.unsaved_prompt = Some(then);
        }
    }

    fn run_deferred(&mut self, ctx: &egui::Context, then: Deferred) {
        if matches!(then, Deferred::Close) {
            self.allow_close = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        } else {
            self.run_deferred_now(then);
        }
    }

    fn run_deferred_now(&mut self, then: Deferred) {
        let before = self.session.opened.len();
        match then {
            Deferred::LoadFile => self.load_file(),
            Deferred::OpenFits(paths) => {
                self.open_paths(&paths);
            }
            Deferred::OpenProjectDialog => self.open_project_dialog(),
            Deferred::OpenProject(path) => {
                self.open_project_path(&path);
            }
            Deferred::Close => self.allow_close = true,
        }
        self.after_open(before);
    }

    /// Where the project dialogs start: the project's folder, else the FITS file's.
    fn project_start_dir(&self) -> Option<PathBuf> {
        self.project.path.as_ref().and_then(|p| p.parent().map(Path::to_path_buf)).or_else(|| {
            self.session
                .data
                .as_ref()
                .and_then(|d| d.source_path.as_ref())
                .and_then(|s| Path::new(s).parent().map(Path::to_path_buf))
        })
    }

    /// `open_project`'s file dialog.
    fn open_project_dialog(&mut self) {
        let mut dialog =
            rfd::FileDialog::new().set_title("Open Project").add_filter("e-CALLISTO Project (*.efaproj)", &["efaproj"]);
        if let Some(dir) = self.project_start_dir() {
            dialog = dialog.set_directory(dir);
        }
        if let Some(path) = dialog.pick_file() {
            self.open_project_path(&path);
        }
    }

    /// `_load_snapshot_path`.
    fn recover_snapshot(&mut self, path: &Path) {
        if let Err(e) = self.project.recover(&mut self.session, path) {
            self.notice = Some(Notice {
                kind: NoticeKind::Warning,
                title: "Recovery Failed".into(),
                text: format!("Could not open recovery snapshot:\n{e}"),
            });
        }
    }

    /// `open_project_path`.
    pub fn open_project_path(&mut self, path: &Path) -> bool {
        match self.project.open(&mut self.session, path) {
            Ok(_) => {
                self.settings.add_recent_project(&path.to_string_lossy());
                true
            }
            Err(e) => {
                self.notice = Some(Notice {
                    kind: NoticeKind::Critical,
                    title: "Open Project Failed".into(),
                    text: crate::project::open_error_text(&e),
                });
                false
            }
        }
    }

    /// `open_recent_project`.
    fn open_recent_project(&mut self, index: usize) {
        let Some(path) = self.settings.recent_projects.get(index).cloned() else { return };
        if !Path::new(&path).is_file() {
            self.settings.remove_recent_project(&path);
            self.notice = Some(Notice {
                kind: NoticeKind::Warning,
                title: "Open Recent".into(),
                text: format!("This project is no longer available and was removed from the recent list:\n{path}"),
            });
            return;
        }
        self.after_unsaved_question(Deferred::OpenProject(PathBuf::from(path)));
    }

    fn nothing_to_save(&mut self) -> bool {
        if self.project.has_content(&self.session) {
            return false;
        }
        self.notice = Some(Notice {
            kind: NoticeKind::Information,
            title: "Save Project".into(),
            text: "There is nothing to save yet: load a FITS file, solar images or a GCS event first.".into(),
        });
        true
    }

    /// `save_project`.
    pub fn save_project(&mut self) -> bool {
        if self.nothing_to_save() {
            return false;
        }
        match self.project.path.clone() {
            Some(path) => self.write_project_to(&path, self.project.storage),
            None => self.save_project_as(),
        }
    }

    /// `save_project_as`. The parts Lite only keeps stay in the storage they
    /// were opened with: Lite can write their images back, not relink them.
    pub fn save_project_as(&mut self) -> bool {
        if self.nothing_to_save() {
            return false;
        }
        let mut dialog = rfd::FileDialog::new()
            .set_title("Save Project As")
            .set_file_name(crate::project::ProjectState::default_filename(&self.session))
            .add_filter("e-CALLISTO Project (*.efaproj)", &["efaproj"]);
        if let Some(dir) = self.project_start_dir() {
            dialog = dialog.set_directory(dir);
        }
        let Some(mut path) = dialog.save_file() else { return false };
        if !path.to_string_lossy().to_lowercase().ends_with(".efaproj") {
            path = PathBuf::from(format!("{}.efaproj", path.display()));
        }
        let key = |p: &Path| selection_key(&[p.to_string_lossy().into_owned()]);
        if self.project.path.as_deref().map(key) != Some(key(&path)) {
            // A copy gets its own identity, so the two never share an unpack folder.
            self.project.new_identity();
        }
        self.write_project_to(&path, self.project.storage)
    }

    /// `_write_project_to`.
    fn write_project_to(&mut self, path: &Path, storage: &'static str) -> bool {
        match self.project.save(&mut self.session, path, storage) {
            Ok(()) => {
                self.settings.add_recent_project(&path.to_string_lossy());
                true
            }
            Err(e) => {
                self.notice = Some(Notice {
                    kind: NoticeKind::Critical,
                    title: "Save Project Failed".into(),
                    text: format!("Could not save project:\n{e}"),
                });
                false
            }
        }
    }

    /// `open_recent_fits`.
    fn open_recent_fits(&mut self, index: usize) {
        let Some(paths) = self.settings.recent_fits.get(index).cloned() else { return };
        let missing: Vec<&String> = paths.iter().filter(|p| !Path::new(p).is_file()).collect();
        if !missing.is_empty() {
            let list = missing.iter().map(|s| s.as_str()).collect::<Vec<_>>().join("\n");
            self.settings.remove_recent_fits(&paths);
            self.notice = Some(Notice {
                kind: crate::session::NoticeKind::Warning,
                title: "Open Recent".into(),
                text: format!("These files are no longer available and were removed from the recent list:\n{list}"),
            });
            return;
        }
        self.after_unsaved_question(Deferred::OpenFits(paths));
    }

    fn handle(&mut self, command: Command) {
        let s = &mut self.session;
        match command {
            Command::Theme(mode) => self.settings.theme_mode = mode,
            Command::TogglePrefetch => {
                self.settings.prefetch_adjacent = !self.settings.prefetch_adjacent;
                s.prefetch_adjacent = self.settings.prefetch_adjacent;
            }
            Command::PlotMaximumIntensities | Command::OpenMaximumIntensities => {
                s.stop_main_lasso();
                let auto_clean = self.settings.auto_clean_max_outliers;
                self.analysis.open_or_focus_max(&mut self.session, None, false, true, auto_clean);
            }
            Command::OpenTypeIIBandSplitting => {
                let ui_family = self.fonts.ui_family().unwrap_or("Segoe UI");
                self.analysis.open_or_focus_type_ii(&mut self.session, None, ui_family, self.fonts.families());
            }
            Command::ToggleAutoCleanMaxOutliers => {
                self.settings.auto_clean_max_outliers = !self.settings.auto_clean_max_outliers
            }
            Command::ToggleSidebar => self.settings.sidebar_collapsed = !self.settings.sidebar_collapsed,
            Command::Open => self.after_unsaved_question(Deferred::LoadFile),
            Command::OpenRecentFits(i) => self.open_recent_fits(i),
            Command::OpenProject => self.after_unsaved_question(Deferred::OpenProjectDialog),
            Command::OpenRecentProject(i) => self.open_recent_project(i),
            Command::ClearRecentProjects => self.settings.recent_projects.clear(),
            Command::OpenRestoredAnalysis => {
                // `open_restored_analysis_windows`: the windows the session asks for.
                match self.session.with_analysis_context(self.session.analysis.as_ref()) {
                    Some(session) => {
                        self.session.pending_analysis_restore = Some(crate::session::project::PendingAnalysisRestore {
                            open_max: session.ui.restore_max_window,
                            open_analyzer: session.ui.restore_analyzer_window,
                            open_type_ii: session.ui.restore_type_ii_window,
                            warning: String::new(),
                        });
                    }
                    None => self.session.show_message("No saved analysis session is available.", 4000),
                }
            }
            Command::ExportViewConfig => self.export_view_config(),
            Command::ExportAnalysisLog => self.export_analysis_log(),
            Command::ExportToFit => match self.session.fits_export_source() {
                None => {
                    self.notice = Some(Notice {
                        kind: NoticeKind::Warning,
                        title: "No Data".into(),
                        text: "Load a FITS file before exporting.".into(),
                    })
                }
                Some(data) => {
                    let rec = callisto_core::radio::fits_export::recommend_bitpix(&data.data);
                    let items = vec![
                        format!("Auto (Recommended: {rec})"),
                        "8 (unsigned byte)".to_owned(),
                        "16 (signed int16)".to_owned(),
                        "32 (signed int32)".to_owned(),
                    ];
                    self.input = Some((
                        dialogs::InputDialog::item(
                            "Export FITS - BITPIX",
                            "Choose BITPIX for the exported FITS:",
                            items,
                        ),
                        InputPurpose::ExportFitsBitpix,
                    ));
                }
            },
            Command::ExportProvenanceReport => self.export_provenance_report(),
            Command::ImportViewConfig => self.import_view_config(),
            Command::SavePreset => {
                self.input =
                    Some((dialogs::InputDialog::text("Save Preset", "Preset name:"), InputPurpose::SavePreset));
            }
            Command::ApplyPreset | Command::DeletePreset | Command::SetDefaultPreset => {
                let presets = callisto_core::presets::parse(&self.settings.presets_json);
                let (title, empty, label, purpose) = match command {
                    Command::ApplyPreset => {
                        ("Apply Preset", "No presets have been saved yet.", "Choose preset:", InputPurpose::ApplyPreset)
                    }
                    Command::DeletePreset => {
                        ("Delete Preset", "No presets are available.", "Choose preset:", InputPurpose::DeletePreset)
                    }
                    _ => (
                        "Set Default Preset",
                        "No presets have been saved yet.",
                        "Apply on FITS load:",
                        InputPurpose::SetDefaultPreset,
                    ),
                };
                if presets.is_empty() {
                    self.notice =
                        Some(Notice { kind: NoticeKind::Information, title: title.into(), text: empty.into() });
                } else {
                    let names: Vec<String> =
                        presets.iter().map(|p| p.get("name").map(|n| n.py_str()).unwrap_or_default()).collect();
                    let mut dialog = dialogs::InputDialog::item(title, label, names.clone());
                    if purpose == InputPurpose::SetDefaultPreset {
                        let key = callisto_core::presets::name_key(&self.settings.default_preset_name);
                        dialog.choice =
                            names.iter().position(|n| callisto_core::presets::name_key(n) == key).unwrap_or(0);
                    }
                    self.input = Some((dialog, purpose));
                }
            }
            Command::ClearDefaultPreset => {
                let old = std::mem::take(&mut self.settings.default_preset_name);
                if old.trim().is_empty() {
                    self.notice = Some(Notice {
                        kind: NoticeKind::Information,
                        title: "Clear Default Preset".into(),
                        text: "No default preset is set.".into(),
                    });
                } else {
                    self.session.show_message("Default FITS-load preset cleared.", 2500);
                }
            }

            Command::RecoverLastSession => match callisto_core::project::recovery::latest_snapshot(&self.recovery_root)
            {
                Some(path) => self.recover_snapshot(&path),
                None => {
                    self.notice = Some(Notice {
                        kind: NoticeKind::Information,
                        title: "Recover Last Session".into(),
                        text: "No recovery snapshot was found.".into(),
                    })
                }
            },
            Command::SaveProject => {
                self.save_project();
            }
            Command::SaveProjectAs => {
                self.save_project_as();
            }
            Command::ClearRecentFiles => self.settings.recent_fits.clear(),
            Command::Undo => s.undo(),
            Command::Redo => s.redo(),
            Command::ResetToRaw => s.reset_to_raw(),
            Command::ResetAll => {
                s.reset_all();
                self.sidebar.graph_title.clear();
                self.sidebar.timeline_step = 1;
            }
            Command::ResetSelection => s.reset_selection(),
            Command::LockNavigation => s.lock(),
            Command::UnlockNavigation => s.unlock(),
            Command::RectangularZoom => s.rectangular_zoom(),
            Command::SubtractBackground => {
                s.apply_background_subtraction();
            }
            Command::BuiltinPreset => s.apply_raw_fits_percentile_preset(),
            Command::OpenRfiPanel => {
                if s.can_open_rfi_panel() && self.rfi_dialog.is_none() {
                    self.rfi_dialog = Some(dialogs::RfiDialog::new(&s.rfi.settings));
                }
            }
            Command::ApplyRfi => s.apply_rfi(None),
            Command::ResetRfi => {
                s.reset_rfi();
                if let Some(d) = &mut self.rfi_dialog {
                    d.set(&s.rfi.settings);
                }
            }
            Command::CombineFits => {
                if self.combine_fits.is_none() {
                    self.combine_fits = Some(crate::ui::combine_dialog::CombineFitsDialog::new());
                }
            }
            Command::SetDisplayRange => {
                if let Some(req) = s.display_range_request() {
                    self.display_range_dialog = Some(dialogs::DisplayRangeDialog::new(req));
                }
            }
            Command::SaveDisplayRangePreset => {
                if s.current_display_range().is_some() {
                    self.input = Some((
                        dialogs::InputDialog::text("Save Display Range Preset", "Preset name:"),
                        InputPurpose::SaveRangePreset,
                    ));
                }
            }
            Command::ApplyDisplayRangePreset | Command::DeleteDisplayRangePreset => {
                let apply = command == Command::ApplyDisplayRangePreset;
                let (title, empty) = if apply {
                    ("Apply Display Range Preset", "No display range presets have been saved yet.")
                } else {
                    ("Delete Display Range Preset", "No display range presets are available.")
                };
                if self.settings.display_range_presets.is_empty() {
                    self.notice =
                        Some(Notice { kind: NoticeKind::Information, title: title.into(), text: empty.into() });
                } else {
                    let names = self.settings.display_range_presets.iter().map(|p| p.name.clone()).collect();
                    let purpose = if apply { InputPurpose::ApplyRangePreset } else { InputPurpose::DeleteRangePreset };
                    self.input = Some((dialogs::InputDialog::item(title, "Choose preset:", names), purpose));
                }
            }
            Command::UserGuide => {
                if self.guide.is_none() {
                    self.guide = Some(crate::help::guide::UserGuide::new());
                }
                self.focus_help = Some("user_guide");
            }
            Command::CiteSoftware => {
                if self.cite.is_none() {
                    self.cite = Some(crate::help::cite::CitationDialog::new());
                }
                self.focus_help = Some("cite_software");
            }
            Command::ReportBug => {
                if self.bug_report.is_none() {
                    self.bug_report =
                        Some(crate::help::bug_report::BugReportDialog::new(callisto_core::updates::GITHUB_REPO));
                }
                self.focus_help = Some("report_bug");
            }
            Command::About => self.about = Some(crate::help::about_box()),
            Command::ViewFitsHeader => {
                if let Some((title, default_name, text)) = s.fits_header_view() {
                    self.header_viewer = Some(dialogs::HeaderViewer { title, default_name, text });
                }
            }
            Command::AddPreviousObservation => s.extend_timeline(Direction::Previous, self.sidebar.timeline_step),
            Command::AddNextObservation => s.extend_timeline(Direction::Next, self.sidebar.timeline_step),
            Command::TrimTimelineStart => s.trim_timeline(true),
            Command::TrimTimelineEnd => s.trim_timeline(false),
            Command::LaunchDownloader => {
                if self.downloader.is_none() {
                    let state = self.downloader_state.clone();
                    self.downloader =
                        Some(DownloaderDialog::new(self.cache_root.clone(), &mut self.stations, state.as_ref()));
                }
            }
            Command::LaunchLearmonthDownloader => {
                if self.learmonth.is_none() {
                    self.learmonth = Some(LearmonthDialog::new(self.learmonth_cache.clone()));
                }
            }
            Command::MultiStationComparison => {
                self.open_comparison(Vec::new());
            }
            Command::OpenBatchProcessor => {
                if self.batch.is_none() {
                    self.batch = Some(crate::tools::batch::BatchDialog::new(&s.batch_env()));
                }
            }
            Command::RulerMeasurement => s.activate_ruler_tool(),
            Command::ClearRulerMeasurement => s.clear_ruler_measurement(true),
            Command::EstimateDriftRate => s.activate_drift_tool(),
            Command::IsolateBurst => s.activate_lasso(),
            Command::LightCurveClickFrequency => {
                let on = !s.tools.light_curve_click_mode;
                s.set_light_curve_click_mode(on);
            }
            Command::ClearLightCurves => s.clear_light_curve(),
            Command::LightCurveEnterFrequency => {
                if s.can_plot_light_curve()
                    && let Some((lo, hi)) = s.light_curve_frequency_limits()
                {
                    self.light_curve_frequency = Some(FrequencyDialog::new(lo, hi, s.light_curve_initial_frequency()));
                }
            }
            Command::LightCurveSettings => match &mut self.light_curve_settings {
                Some(d) => d.load(&s.tools.light_curve_settings),
                None => self.light_curve_settings = Some(LightCurveSettingsDialog::new(&s.tools.light_curve_settings)),
            },
            Command::AddText => {
                if s.can_start_annotation_text() {
                    let style = s.text_annotation_style();
                    self.text_annotation = Some(self.text_dialog("Add Text Annotation", TextPurpose::Add, "", &style));
                }
            }
            Command::EditTextLabel => self.choose_text_label(InputPurpose::EditTextLabel),
            Command::MoveTextLabel => self.choose_text_label(InputPurpose::MoveTextLabel),
            Command::AddPolygon => s.start_annotation_polygon(),
            Command::AddLine => s.start_annotation_line(),
            Command::ToggleAnnotations => s.toggle_annotations_visibility(),
            Command::DeleteLastAnnotation => s.delete_last_annotation(),
            Command::ClearAnnotations => s.clear_annotations(),
            other => s.show_message(format!("{other:?} is not available yet in this build."), 4000),
        }
    }

    /// `open_multi_station_comparison_dialog`: the window with `paths`, or
    /// the main window's file (or its combined sources) when none are given;
    /// an open window gets the paths added.
    fn open_comparison(&mut self, paths: Vec<String>) {
        let exists = |p: &String| !p.trim().is_empty() && std::path::Path::new(p).exists();
        let requested: Vec<String> = paths.into_iter().filter(exists).collect();
        if let Some(w) = &mut self.comparison {
            if !requested.is_empty() {
                w.add_files(&requested);
            }
            return;
        }
        let mut initial = requested;
        if initial.is_empty()
            && let Some(d) = &self.session.data
        {
            match &d.source_path {
                Some(p) if exists(p) => initial.push(p.clone()),
                _ => initial.extend(d.combined_sources.iter().filter(|p| exists(p)).cloned()),
            }
        }
        let visual = self.session.batch_env().visual;
        // The window takes the theme from its first frame on.
        let dark = self.applied_theme.is_some_and(|t| t.0);
        self.comparison = Some(crate::tools::comparison::ComparisonWindow::new(&initial, Some(&visual), dark));
    }

    fn notify(&mut self, kind: NoticeKind, title: &str, text: impl Into<String>) {
        self.session.notices.push(Notice { kind, title: title.into(), text: text.into() });
    }

    /// `process_imported_files`.
    fn process_imported_files(&mut self, ctx: &egui::Context, sources: Vec<String>, from: ImportSource) {
        if sources.is_empty() {
            return self.notify(NoticeKind::Warning, "No Files", "No files were received from the downloader.");
        }
        if self.importing.is_some() {
            return self.notify(
                NoticeKind::Information,
                "Import In Progress",
                "Another FITS import is already running.",
            );
        }
        self.importing = Some((ImportDialog::import(ctx, self.cache_root.clone(), sources), Some(from)));
    }

    /// `process_comparison_sources`.
    fn process_comparison_sources(&mut self, ctx: &egui::Context, sources: Vec<String>) {
        if sources.is_empty() {
            return self.notify(NoticeKind::Warning, "No Files", "No files were received for comparison.");
        }
        if self.importing.is_some() {
            return self.notify(
                NoticeKind::Information,
                "Comparison In Progress",
                "Another comparison preparation is already running.",
            );
        }
        self.importing = Some((ImportDialog::compare(ctx, self.cache_root.clone(), sources), None));
    }

    /// `_emit_downloader_import_success`: the source window closes.
    fn import_success(&mut self, from: Option<ImportSource>) {
        match from {
            Some(ImportSource::Downloader) => {
                if let Some(d) = self.downloader.take() {
                    self.downloader_state = Some(d.date_time_state());
                }
            }
            Some(ImportSource::Learmonth) => self.learmonth = None,
            None => {}
        }
    }

    /// `_on_downloader_import_finished` and the comparison's counterpart.
    fn finish_import(&mut self, outcome: Outcome, from: Option<ImportSource>) {
        use callisto_core::radio::downloader::{ImportPayload, invalid_selection_text};
        match outcome {
            Outcome::Import(Ok(ImportPayload::Single { filename, source_path, result })) => {
                self.session.load_imported_single(filename, source_path, *result);
                self.import_success(from);
            }
            Outcome::Import(Ok(ImportPayload::Combined(c))) => {
                self.session.load_combined_into_main(*c);
                self.import_success(from);
            }
            Outcome::Import(Ok(ImportPayload::FrequencyOptionsRequired { files, relation, .. })) => {
                let pending = crate::session::PendingCombine { paths: files, relation: Some(relation) };
                self.combine_dialog = Some(dialogs::CombineOptionsDialog::new(&pending));
                self.session.pending_combine = Some(pending);
                self.import_combine = from.or(Some(ImportSource::Downloader));
            }
            Outcome::Import(Ok(ImportPayload::Invalid(detail))) => {
                self.notify(NoticeKind::Warning, "Invalid Selection", invalid_selection_text(&detail));
            }
            Outcome::Import(Err(m)) => {
                self.notify(
                    NoticeKind::Critical,
                    "Import Failed",
                    if m.is_empty() { "Import failed.".to_owned() } else { m },
                );
            }
            Outcome::Compare(Ok(paths)) if paths.is_empty() => {
                self.notify(NoticeKind::Warning, "Comparison", "No FITS files were prepared for comparison.");
            }
            Outcome::Compare(Ok(paths)) => {
                self.open_comparison(paths);
            }
            Outcome::Compare(Err(m)) => {
                let text = if m.is_empty() { "Could not prepare FITS files for comparison.".to_owned() } else { m };
                self.notify(NoticeKind::Critical, "Comparison Failed", text);
            }
        }
    }

    /// The download windows, the import dialog and their results.
    fn download_windows(&mut self, ctx: &egui::Context, palette: &Palette) {
        let importing = self.importing.is_some();
        if let Some(d) = &mut self.downloader {
            d.blocked = importing;
            match d.show(ctx, &mut self.stations, palette) {
                Some(DownloaderAction::Import(urls)) => {
                    self.process_imported_files(ctx, urls, ImportSource::Downloader)
                }
                Some(DownloaderAction::Compare(urls)) => {
                    if let Some(d) = self.downloader.take() {
                        self.downloader_state = Some(d.date_time_state());
                    }
                    self.process_comparison_sources(ctx, urls);
                }
                Some(DownloaderAction::Close) => {
                    if let Some(d) = self.downloader.take() {
                        self.downloader_state = Some(d.date_time_state());
                    }
                }
                None => {}
            }
        }
        if let Some(l) = &mut self.learmonth {
            l.blocked = importing;
            match l.show(ctx, palette) {
                Some(LearmonthAction::Import(paths)) => {
                    self.process_imported_files(ctx, paths, ImportSource::Learmonth)
                }
                Some(LearmonthAction::Close) => self.learmonth = None,
                None => {}
            }
        }
        if let Some(w) = &mut self.comparison {
            w.set_dark(palette.dark);
            if let Some(crate::tools::comparison::ComparisonAction::Close) = w.show(ctx, palette)
                && let Some(mut w) = self.comparison.take()
            {
                for n in w.take_notices() {
                    self.notify(n.kind, &n.title, n.text);
                }
            }
        }
        if let Some(b) = &mut self.batch {
            let env = self.session.batch_env();
            if let Some(crate::tools::batch::BatchAction::Close) = b.show(ctx, &env, palette) {
                // Message boxes still waiting move to the main window.
                if let Some(mut b) = self.batch.take() {
                    for n in b.take_notices() {
                        self.notify(n.kind, &n.title, n.text);
                    }
                }
            }
        }
        if let Some((dialog, _)) = &mut self.importing
            && let Some(outcome) = dialog.show(ctx, palette)
        {
            let from = self.importing.take().and_then(|(_, f)| f);
            self.finish_import(outcome, from);
        }
        if let Some(d) = &mut self.report {
            use crate::report_dialog::{Event, Outcome};
            for event in d.show(ctx, palette) {
                match event {
                    // `_on_project_report_progress`.
                    Event::Progress(text) => self.session.show_message(text, 2500),
                    Event::Cancel => self.session.show_message("Cancelling project report generation...", 3000),
                    Event::Done(outcome) => {
                        self.report = None;
                        match outcome {
                            Outcome::Finished(r) => {
                                let shown = r.path.to_string_lossy().into_owned();
                                let name = callisto_core::radio::naming::basename(&shown).to_owned();
                                self.session.show_message(format!("Project report generated: {name}"), 5000);
                                self.session.log_operation(format!("Generated project report: {name}"));
                                self.notice = Some(Notice {
                                    kind: NoticeKind::Information,
                                    title: "Project Report Complete".into(),
                                    text: format!("Report saved:\n{shown}"),
                                });
                            }
                            Outcome::Failed(m) => {
                                let text = if m.is_empty() { "Unknown error.".to_owned() } else { m };
                                self.notice = Some(Notice {
                                    kind: NoticeKind::Critical,
                                    title: "Generate Project Report Failed".into(),
                                    text,
                                });
                            }
                            Outcome::Cancelled => {
                                self.session.show_message("Project report generation cancelled.", 4000)
                            }
                        }
                    }
                }
            }
        }
    }

    /// Main-window shortcuts (the menus show them; Qt makes them work anywhere).
    fn shortcuts(&mut self, ctx: &egui::Context) {
        use egui::{Key, KeyboardShortcut, Modifiers};
        let fire = |m: Modifiers, k: Key, c: Command, commands: &mut Vec<Command>| {
            if ctx.input_mut(|i| i.consume_shortcut(&KeyboardShortcut::new(m, k))) {
                commands.push(c);
            }
        };
        let cs = Modifiers::COMMAND | Modifiers::SHIFT;
        fire(cs, Key::Z, Command::Redo, &mut self.commands);
        fire(cs, Key::ArrowLeft, Command::AddPreviousObservation, &mut self.commands);
        fire(cs, Key::ArrowRight, Command::AddNextObservation, &mut self.commands);
        fire(Modifiers::COMMAND, Key::Z, Command::Undo, &mut self.commands);
        fire(Modifiers::COMMAND, Key::O, Command::Open, &mut self.commands);
        fire(Modifiers::COMMAND, Key::E, Command::ExportFigure, &mut self.commands);
        fire(Modifiers::COMMAND, Key::F, Command::ExportToFit, &mut self.commands);
        fire(Modifiers::NONE, Key::F1, Command::UserGuide, &mut self.commands);
    }

    /// Files dropped on the window: FITS files only (projects come later).
    fn dropped_files(&mut self, ctx: &egui::Context) {
        let dropped: Vec<String> = ctx.input(|i| {
            i.raw
                .dropped_files
                .iter()
                .map(|f| f.path().to_string_lossy().into_owned())
                .filter(|p| !p.is_empty())
                .collect()
        });
        if dropped.is_empty() {
            return;
        }
        // `classify_dropped_paths`: one project, or FITS files.
        if dropped.len() == 1 && dropped[0].to_lowercase().ends_with(".efaproj") {
            let path = PathBuf::from(&dropped[0]);
            self.after_unsaved_question(Deferred::OpenProject(path));
        } else if dropped.iter().all(|p| is_fits_path(p)) {
            self.after_unsaved_question(Deferred::OpenFits(dropped));
        }
    }
}

impl eframe::App for LiteApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        let palette = self.palette(&ctx);
        let key = (palette.dark, palette.qt_window == Palette::new(false, true).qt_window);
        if self.applied_theme != Some(key) {
            ctx.set_theme(if palette.dark { egui::Theme::Dark } else { egui::Theme::Light });
            palette.apply(&ctx);
            self.applied_theme = Some(key);
        }
        let mut families = self.session.annotation_font_families();
        // The Type II graph settings' font family, while the window is open.
        if let Some(f) = self.analysis.type_ii_font_family()
            && !families.iter().any(|x| x.eq_ignore_ascii_case(&f))
        {
            families.push(f);
        }
        if self.session.graph.font_family != self.installed_graph_family || families != self.installed_extra_families {
            let family = self.session.graph.font_family.clone();
            self.fonts.install_with(&ctx, (!family.is_empty()).then_some(family.as_str()), &families);
            self.installed_graph_family = family;
            self.installed_extra_families = families;
        }
        // The startup splash, a step a frame, before the window becomes the main window.
        if let Some(step) = self.splash {
            ui.painter().rect_filled(ui.max_rect(), 0.0, egui::Color32::from_rgb(0x07, 0x13, 0x1f));
            let (value, text) = crate::ui::splash::STEPS[step.min(crate::ui::splash::STEPS.len() - 1)];
            crate::ui::splash::draw(ui, value, text);
            if step + 1 < crate::ui::splash::STEPS.len() {
                self.splash = Some(step + 1);
            } else {
                self.splash = None;
                // `window.showMaximized()`, then the splash closes.
                ctx.send_viewport_cmd(egui::ViewportCommand::Decorations(true));
                ctx.send_viewport_cmd(egui::ViewportCommand::Resizable(true));
                ctx.send_viewport_cmd(egui::ViewportCommand::MinInnerSize(egui::vec2(900.0, 600.0)));
                ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(egui::vec2(1600.0, 1000.0)));
                ctx.send_viewport_cmd(egui::ViewportCommand::Maximized(true));
                ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
            }
            ctx.request_repaint();
            return;
        }
        // The downloaders and the import dialog are modal (`exec()`).
        let modal = self.downloader.is_some()
            || self.learmonth.is_some()
            || self.importing.is_some()
            || self.light_curve_frequency.is_some()
            || self.text_annotation.is_some();
        if self.notice.is_none()
            && self.combine_dialog.is_none()
            && self.header_viewer.is_none()
            && self.display_range_dialog.is_none()
            && self.input.is_none()
            && !modal
        {
            self.shortcuts(&ctx);
            self.dropped_files(&ctx);
        }
        if let Some(wait) = self.session.tick() {
            ctx.request_repaint_after(wait);
        }

        self.sidebar.analysis_summary = self.session.analysis_summary_text();
        let state = ui::WindowState {
            menu: self.menu_state(),
            menubar: &mut self.menubar,
            sidebar: &mut self.sidebar,
            sections: &mut self.settings.sidebar_sections,
            sidebar_collapsed: self.settings.sidebar_collapsed,
            session: &mut self.session,
            update_status: &self.updates.status,
            commands: &mut self.commands,
        };
        ui::main_window(ui, state, &palette);
        if modal {
            crate::downloads::block_input(ui, egui::Id::new("main_window_modal"));
        }
        self.settings.frequency_axis_log = self.session.freq_axis_log;

        let commands = std::mem::take(&mut self.commands);
        if !commands.is_empty() {
            // Commands run after the window was drawn: draw their result.
            ctx.request_repaint();
        }
        self.sync_default_preset();
        for command in commands {
            if matches!(command, Command::ExportFigure) {
                self.export_figure(&ctx);
                continue;
            }
            if matches!(command, Command::GenerateProjectReport) {
                self.generate_project_report(&ctx);
                continue;
            }
            if matches!(command, Command::CheckForUpdates) {
                for (text, ms) in self.updates.check(&ctx, true) {
                    self.session.show_message(text, ms);
                }
                continue;
            }
            let before = self.session.opened.len();
            self.handle(command);
            self.after_open(before);
        }
        if std::mem::take(&mut self.session.project_detached) {
            self.project.path = None;
        }

        if self.session.flush_deferred() {
            ctx.request_repaint();
        }
        // `_apply_pending_analysis_restore`, once an opened project's plot is up.
        if let Some(p) = self.session.take_pending_analysis_restore() {
            let auto_clean = self.settings.auto_clean_max_outliers;
            let session = self.session.analysis.clone();
            if p.open_max {
                self.analysis.open_or_focus_max(&mut self.session, session.clone(), p.open_analyzer, false, auto_clean);
            } else if p.open_analyzer {
                self.analysis.open_or_focus_analyzer(&mut self.session, session.clone(), auto_clean);
            }
            if p.open_type_ii {
                let ui_family = self.fonts.ui_family().unwrap_or("Segoe UI");
                self.analysis.open_or_focus_type_ii(&mut self.session, session, ui_family, self.fonts.families());
            }
        }
        // `closeEvent` asks about unsaved changes first.
        if ctx.input(|i| i.viewport().close_requested()) {
            if !self.allow_close && self.project.is_dirty(&self.session) {
                ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
                self.unsaved_prompt = Some(Deferred::Close);
            } else {
                self.settings.clean_exit = true;
            }
        }
        // The autosave timer: every three minutes, while there are unsaved changes.
        if self.last_autosave.elapsed() >= std::time::Duration::from_secs(180) {
            self.last_autosave = std::time::Instant::now();
            let root = self.recovery_root.clone();
            self.project.autosave(&mut self.session, &root);
        }
        ctx.request_repaint_after(std::time::Duration::from_secs(180).saturating_sub(self.last_autosave.elapsed()));
        if let Some(path) = self.recovery_prompt.clone() {
            match dialogs::question_box(
                &ctx,
                "Recover Last Session",
                "An unclean exit was detected. Recover the latest autosave snapshot?",
                &["Yes", "No"],
                0,
                &palette,
            ) {
                Some(0) => {
                    self.recovery_prompt = None;
                    self.recover_snapshot(&path);
                }
                Some(_) => self.recovery_prompt = None,
                None => {}
            }
        }
        if let Some(then) = self.unsaved_prompt.take() {
            match dialogs::question_box(
                &ctx,
                "Unsaved Project",
                "You have unsaved project changes.\n\nSave before continuing?",
                &["Save", "Discard", "Cancel"],
                0,
                &palette,
            ) {
                Some(0) => {
                    if self.save_project() {
                        self.run_deferred(&ctx, then);
                    }
                }
                Some(1) => self.run_deferred(&ctx, then),
                Some(_) => {}
                None => self.unsaved_prompt = Some(then),
            }
        }
        self.analysis.show(&ctx, &palette, &mut self.session, self.settings.auto_clean_max_outliers);
        if self.notice.is_none() && !self.session.notices.is_empty() {
            self.notice = Some(self.session.notices.remove(0));
        }
        if let Some(notice) = &self.notice
            && dialogs::message_box(&ctx, notice, &palette)
        {
            self.notice = None;
        }
        if let Some(dialog) = &mut self.combine_fits {
            use crate::ui::combine_dialog::CombineAction;
            match dialog.show(&ctx, &palette) {
                Some(CombineAction::Import(combined)) => {
                    self.combine_fits = None;
                    self.session.load_combined_into_main(*combined);
                }
                Some(CombineAction::Notice(n)) => self.notice = Some(n),
                Some(CombineAction::Close) => self.combine_fits = None,
                None => {}
            }
        }
        if let Some(dialog) = &mut self.display_range_dialog
            && let Some(result) = dialog.show(&ctx, &palette)
        {
            self.display_range_dialog = None;
            self.finish_display_range(result);
        }
        if let Some((dialog, purpose)) = &mut self.input
            && let Some(result) = dialog.show(&ctx, &palette)
        {
            let purpose = *purpose;
            self.input = None;
            if let Some(value) = result {
                self.finish_input(purpose, value);
            }
        }
        if let Some(dialog) = &mut self.light_curve_frequency
            && let Some(result) = dialog.show(&ctx, &palette)
        {
            self.light_curve_frequency = None;
            if let Some(f) = result {
                self.session.plot_light_curve_at_frequency(f, true);
            }
        }
        if let Some(dialog) = &mut self.light_curve_settings {
            match dialog.show(&ctx, &palette) {
                Some(SettingsAction::Changed(settings)) => {
                    self.settings.light_curve = LightCurvePrefs::from(&settings);
                    self.session.set_light_curve_settings(settings);
                }
                Some(SettingsAction::Close) => self.light_curve_settings = None,
                None => {}
            }
        }
        if let Some(dialog) = &mut self.text_annotation
            && let Some(result) = dialog.show(&ctx, &palette)
        {
            let purpose = dialog.purpose;
            self.text_annotation = None;
            if let Some((text, style)) = result {
                match purpose {
                    TextPurpose::Add => self.session.start_annotation_text(&text, style),
                    TextPurpose::Edit(index) => self.session.edit_text_label(index, &text, &style),
                }
            }
        }
        if let Some(dialog) = &mut self.rfi_dialog {
            let masked = self.session.rfi_masked_text();
            match dialog.show(&ctx, &masked, &palette) {
                Some(dialogs::RfiAction::Preview(cfg)) => self.session.preview_rfi(cfg),
                Some(dialogs::RfiAction::Apply(cfg)) => self.session.apply_rfi(Some(cfg)),
                Some(dialogs::RfiAction::Reset) => {
                    self.session.reset_rfi();
                    dialog.set(&self.session.rfi.settings);
                }
                Some(dialogs::RfiAction::Close) => self.rfi_dialog = None,
                None => {}
            }
        }
        self.trace_startup();
        if let Some(viewer) = &mut self.header_viewer {
            match viewer.show(&ctx, &palette) {
                Some(dialogs::HeaderViewerAction::Close) => self.header_viewer = None,
                Some(dialogs::HeaderViewerAction::Save) => {
                    let (name, text) = (viewer.default_name.clone(), viewer.text.clone());
                    self.save_header_text(&name, &text);
                }
                None => {}
            }
        }
        self.download_windows(&ctx, &palette);
        self.help_windows(&ctx, &palette);
        if let Some(dialog) = &mut self.combine_dialog
            && let Some(result) = dialog.show(&ctx, &palette)
        {
            self.combine_dialog = None;
            if let Some(from) = self.import_combine.take() {
                // `_on_downloader_import_finished`, frequency options chosen.
                let pending = self.session.pending_combine.take();
                if let (Some(pending), Some(options)) = (pending, result) {
                    match callisto_core::radio::combine::combine_compatible(&pending.paths, options) {
                        Ok(c) => {
                            self.session.load_combined_into_main(c);
                            self.import_success(Some(from));
                        }
                        Err(e) => self.notify(
                            NoticeKind::Critical,
                            "Import Failed",
                            format!("Could not import downloaded files:\n{e}"),
                        ),
                    }
                }
            } else {
                let before = self.session.opened.len();
                self.session.resolve_pending_combine(result);
                self.after_open(before);
            }
        }
    }

    fn save(&mut self, storage: &mut dyn eframe::Storage) {
        eframe::set_value(storage, SETTINGS_KEY, &self.settings);
    }
}

/// `_build_bug_report_context`, with the provenance report Generate
/// Diagnostics... puts in the bundle.
fn bug_context(
    s: &Session,
    project_path: Option<&std::path::Path>,
) -> (callisto_core::bug_report::BugContext, callisto_core::pyjson::Json, String) {
    use callisto_core::pyjson::Json;
    let d = s.data.as_ref();
    let s_ = |v: &str| Json::from(v);
    let environment = vec![
        ("app_name".to_owned(), s_(crate::APP_NAME)),
        ("app_version".to_owned(), s_(crate::APP_VERSION)),
        ("platform".to_owned(), Json::from(format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH))),
        ("gui".to_owned(), s_("egui")),
    ];
    let summary = vec![
        ("filename".to_owned(), s_(s.filename())),
        ("plot_type".to_owned(), Json::from(s.current_plot_type.clone())),
        ("cmap".to_owned(), Json::from(s.graph.cmap.clone())),
        ("use_db".to_owned(), Json::Bool(s.use_db)),
        ("use_utc".to_owned(), Json::Bool(s.use_utc)),
    ];
    let session = vec![
        ("is_combined".to_owned(), Json::Bool(d.is_some_and(|d| d.combined_mode.is_some()))),
        ("combined_mode".to_owned(), d.and_then(|d| d.combined_mode).map_or(Json::Null, |m| m.key().into())),
        ("source_path".to_owned(), Json::from(d.and_then(|d| d.source_path.clone()).unwrap_or_default())),
        ("project_path".to_owned(), Json::from(project_path.map(|p| p.display().to_string()).unwrap_or_default())),
    ];
    let provenance = s.provenance_payload();
    let markdown = Session::provenance_markdown(&provenance);
    let ctx = callisto_core::bug_report::BugContext {
        summary,
        environment,
        session,
        operation_log: s.log.clone(),
        processing: provenance.get("processing").cloned().unwrap_or_else(Json::object),
        rfi: provenance.get("rfi").cloned().unwrap_or_else(Json::object),
    };
    (ctx, provenance, markdown)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recent_fits_dedupes_and_caps() {
        let mut s = Settings::default();
        for i in 0..12 {
            s.add_recent_fits(&[format!("C:/data/f{i}.fit")]);
        }
        assert_eq!(s.recent_fits.len(), MAX_RECENT_ENTRIES);
        s.add_recent_fits(&["C:/data/b.fit".into(), "C:/data/a.fit".into()]);
        s.add_recent_fits(&["C:/data/a.fit".into(), "C:/data/b.fit".into()]);
        assert_eq!(s.recent_fits.iter().filter(|e| e.len() == 2).count(), 1);
        assert_eq!(fits_entry_label(&s.recent_fits[0]), "a.fit (+1 more)");
        assert!(is_fits_path("x.FIT.GZ") && !is_fits_path("x.txt"));
    }
}
