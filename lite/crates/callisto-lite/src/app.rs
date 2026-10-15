//! Application state and the eframe entry point.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::fonts::SystemFonts;
use crate::menus::{Command, MenuState};
use crate::session::{DisplayRangePreset, Notice, NoticeKind, Session};
use crate::theme::{Palette, ThemeMode};
use crate::ui::{self, dialogs, menubar::MenuBarState, sidebar::SidebarState};
use callisto_core::radio::timeline::Direction;

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

/// What an open input dialog is asking for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputPurpose {
    SaveRangePreset,
    ApplyRangePreset,
    DeleteRangePreset,
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
        session.prefetch_adjacent = settings.prefetch_adjacent;
        // Lite keeps its own download cache, next to its settings.
        let cache = eframe::storage_dir(crate::APP_NAME).map(|d| d.join("callisto_cache"));
        let online = std::env::var_os("CALLISTO_LITE_OFFLINE").is_none();
        session.set_archive(cache, online);
        Self {
            settings,
            os_dark_override: None,
            menubar: MenuBarState::default(),
            sidebar,
            session,
            fonts,
            installed_graph_family: String::new(),
            update_status: "Updates: not checked".into(),
            notice: None,
            combine_dialog: None,
            header_viewer: None,
            rfi_dialog: None,
            display_range_dialog: None,
            combine_fits: None,
            input: None,
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
            click_light_curve: false,
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
        }
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
            other => s.show_message(format!("{other:?} is not available yet in this build."), 4000),
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
        if self.session.graph.font_family != self.installed_graph_family {
            let family = self.session.graph.font_family.clone();
            self.fonts.install_with(&ctx, (!family.is_empty()).then_some(family.as_str()));
            self.installed_graph_family = family;
        }
        if self.notice.is_none()
            && self.combine_dialog.is_none()
            && self.header_viewer.is_none()
            && self.display_range_dialog.is_none()
            && self.input.is_none()
        {
            self.shortcuts(&ctx);
            self.dropped_files(&ctx);
        }
        if let Some(wait) = self.session.tick() {
            ctx.request_repaint_after(wait);
        }

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
        if let Some(dialog) = &mut self.combine_dialog
            && let Some(result) = dialog.show(&ctx, &palette)
        {
            self.combine_dialog = None;
            let before = self.session.opened.len();
            self.session.resolve_pending_combine(result);
            self.after_open(before);
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
