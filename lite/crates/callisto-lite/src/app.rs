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
use crate::ui::{self, dialogs, menubar::MenuBarState, sidebar::SidebarState};
use callisto_core::analysis::light_curve::LightCurveSettings;
use callisto_core::radio::timeline::Direction;
use crate::ui::tool_dialogs::{FrequencyDialog, LightCurveSettingsDialog, SettingsAction, TextAnnotationDialog, TextPurpose};

const SETTINGS_KEY: &str = "settings";
/// `recent_files.MAX_RECENT_ENTRIES`.
const MAX_RECENT_ENTRIES: usize = 10;
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
        }
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
    SaveRangePreset,
    ApplyRangePreset,
    DeleteRangePreset,
    EditTextLabel,
    MoveTextLabel,
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
    pub update_status: String,
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
    /// The frequency-combine options dialog is finishing an import.
    import_combine: Option<ImportSource>,
    /// Lite's own download caches.
    pub cache_root: PathBuf,
    pub learmonth_cache: PathBuf,
    /// The Maximum Intensities, Analyzer and Type II windows.
    pub analysis: crate::analysis::AnalysisWindows,
    commands: Vec<Command>,
    applied_theme: Option<(bool, bool)>,
    frames: u64,
}

impl LiteApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let settings = cc.storage.and_then(|s| eframe::get_value(s, SETTINGS_KEY)).unwrap_or_default();
        Self::with_settings(&cc.egui_ctx, settings)
    }

    pub fn with_settings(ctx: &egui::Context, settings: Settings) -> Self {
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
        let data_dir = eframe::storage_dir(crate::APP_NAME).unwrap_or_else(|| std::env::temp_dir().join(crate::APP_NAME));
        let cache_root = data_dir.join("callisto_cache");
        let learmonth_cache = data_dir.join("learmonth_cache");
        let online = std::env::var_os("CALLISTO_LITE_OFFLINE").is_none();
        session.set_archive(Some(cache_root.clone()), online);
        let stations = if online {
            StationService::online()
        } else {
            StationService::new(std::sync::Arc::new(|_| Err("The archive is offline (CALLISTO_LITE_OFFLINE).".to_owned())))
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
            update_status: "Updates: not checked".into(),
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
            importing: None,
            import_combine: None,
            cache_root,
            learmonth_cache,
            analysis: Default::default(),
            commands: Vec::new(),
            applied_theme: None,
            frames: 0,
        }
    }

    fn menu_state(&self) -> MenuState {
        MenuState {
            has_data: self.session.has_data(),
            recent_files: self.settings.recent_fits.iter().map(|e| fits_entry_label(e)).collect(),
            can_undo: self.session.can_undo(),
            can_redo: self.session.can_redo(),
            theme: self.settings.theme_mode,
            prefetch_adjacent: self.settings.prefetch_adjacent,
            auto_clean_max_outliers: self.settings.auto_clean_max_outliers,
            click_light_curve: self.session.tools.light_curve_click_mode,
            has_noise: self.session.noise_reduced.is_some(),
            has_light_curves: self.session.has_light_curves(),
            has_ruler_measurement: self.session.has_ruler_measurement(),
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

    /// A name or choice came back from an input dialog.
    fn finish_input(&mut self, purpose: InputPurpose, value: String) {
        if matches!(purpose, InputPurpose::EditTextLabel | InputPurpose::MoveTextLabel) {
            if let Some((index, _)) = self.session.text_annotations().into_iter().find(|(_, label)| *label == value) {
                self.text_label_chosen(purpose, index);
            }
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
            InputPurpose::DeleteRangePreset => {
                if crate::session::delete_preset(presets, &value) {
                    s.show_message(format!("Deleted display range preset '{value}'."), 2500);
                }
            }
            InputPurpose::EditTextLabel | InputPurpose::MoveTextLabel => {}
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
        self.open_paths(&paths);
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
            Command::ToggleAutoCleanMaxOutliers => {
                self.settings.auto_clean_max_outliers = !self.settings.auto_clean_max_outliers
            }
            Command::ToggleSidebar => self.settings.sidebar_collapsed = !self.settings.sidebar_collapsed,
            Command::Open => self.load_file(),
            Command::OpenRecentFits(i) => self.open_recent_fits(i),
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
                    self.downloader = Some(DownloaderDialog::new(self.cache_root.clone(), &mut self.stations, state.as_ref()));
                }
            }
            Command::LaunchLearmonthDownloader => {
                if self.learmonth.is_none() {
                    self.learmonth = Some(LearmonthDialog::new(self.learmonth_cache.clone()));
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

    fn notify(&mut self, kind: NoticeKind, title: &str, text: impl Into<String>) {
        self.session.notices.push(Notice { kind, title: title.into(), text: text.into() });
    }

    /// `process_imported_files`.
    fn process_imported_files(&mut self, ctx: &egui::Context, sources: Vec<String>, from: ImportSource) {
        if sources.is_empty() {
            return self.notify(NoticeKind::Warning, "No Files", "No files were received from the downloader.");
        }
        if self.importing.is_some() {
            return self.notify(NoticeKind::Information, "Import In Progress", "Another FITS import is already running.");
        }
        self.importing = Some((ImportDialog::import(ctx, self.cache_root.clone(), sources), Some(from)));
    }

    /// `process_comparison_sources`.
    fn process_comparison_sources(&mut self, ctx: &egui::Context, sources: Vec<String>) {
        if sources.is_empty() {
            return self.notify(NoticeKind::Warning, "No Files", "No files were received for comparison.");
        }
        if self.importing.is_some() {
            return self.notify(NoticeKind::Information, "Comparison In Progress", "Another comparison preparation is already running.");
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
                self.notify(NoticeKind::Critical, "Import Failed", if m.is_empty() { "Import failed.".to_owned() } else { m });
            }
            Outcome::Compare(Ok(paths)) if paths.is_empty() => {
                self.notify(NoticeKind::Warning, "Comparison", "No FITS files were prepared for comparison.");
            }
            Outcome::Compare(Ok(paths)) => {
                self.notify(
                    NoticeKind::Information,
                    "Multi-Station Comparison",
                    format!(
                        "{} file(s) are ready in the download cache. The multi-station comparison window is not available in this build yet.",
                        paths.len()
                    ),
                );
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
                Some(DownloaderAction::Import(urls)) => self.process_imported_files(ctx, urls, ImportSource::Downloader),
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
                Some(LearmonthAction::Import(paths)) => self.process_imported_files(ctx, paths, ImportSource::Learmonth),
                Some(LearmonthAction::Close) => self.learmonth = None,
                None => {}
            }
        }
        if let Some((dialog, _)) = &mut self.importing
            && let Some(outcome) = dialog.show(ctx, palette)
        {
            let from = self.importing.take().and_then(|(_, f)| f);
            self.finish_import(outcome, from);
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
        if !dropped.is_empty() && dropped.iter().all(|p| is_fits_path(p)) {
            self.open_paths(&dropped);
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
        let families = self.session.annotation_font_families();
        if self.session.graph.font_family != self.installed_graph_family || families != self.installed_extra_families {
            let family = self.session.graph.font_family.clone();
            self.fonts.install_with(&ctx, (!family.is_empty()).then_some(family.as_str()), &families);
            self.installed_graph_family = family;
            self.installed_extra_families = families;
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
            update_status: &self.update_status,
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
        for command in commands {
            let before = self.session.opened.len();
            self.handle(command);
            self.after_open(before);
        }

        if self.session.flush_deferred() {
            ctx.request_repaint();
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
                        Err(e) => self.notify(NoticeKind::Critical, "Import Failed", format!("Could not import downloaded files:\n{e}")),
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
