//! The menu bar's contents: the full app's menus without the removed features.
//!
//! Order, labels, shortcuts and which items start disabled follow the full
//! app (`lite/reference/windows/reference_light.json`, captured from
//! `src/ui/app/main_window.py`). Removed: SWAVES, the solar-event windows,
//! GOES overlay, event context strip, Solar Image Analysis, GCS fitting,
//! Event Figure, View → Mode and Processing → Hardware Acceleration.

use std::borrow::Cow;

use crate::theme::ThemeMode;

/// Everything a menu item, toolbar button or sidebar control can ask for.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Command {
    Open,
    CombineFits,
    OpenProject,
    SaveProject,
    SaveProjectAs,
    RecoverLastSession,
    GenerateProjectReport,
    SaveAs,
    ExportFigure,
    ExportToFit,
    ExportProvenanceReport,
    ExportAnalysisLog,
    Undo,
    Redo,
    AddPreviousObservation,
    AddNextObservation,
    TogglePrefetch,
    ResetToRaw,
    ResetAll,
    LaunchDownloader,
    LaunchLearmonthDownloader,
    ViewFitsHeader,
    SetDisplayRange,
    SaveDisplayRangePreset,
    ApplyDisplayRangePreset,
    DeleteDisplayRangePreset,
    ExportViewConfig,
    ImportViewConfig,
    MultiStationComparison,
    Theme(ThemeMode),
    OpenMaximumIntensities,
    OpenTypeIIBandSplitting,
    LightCurveEnterFrequency,
    LightCurveClickFrequency,
    LightCurveSettings,
    ClearLightCurves,
    RulerMeasurement,
    ClearRulerMeasurement,
    OpenRfiPanel,
    ApplyRfi,
    ResetRfi,
    AddPolygon,
    AddLine,
    AddText,
    EditTextLabel,
    MoveTextLabel,
    ToggleAnnotations,
    DeleteLastAnnotation,
    ClearAnnotations,
    BuiltinPreset,
    SavePreset,
    ApplyPreset,
    SetDefaultPreset,
    ClearDefaultPreset,
    DeletePreset,
    ToggleAutoCleanMaxOutliers,
    OpenRestoredAnalysis,
    OpenBatchProcessor,
    CheckForUpdates,
    ReportBug,
    About,
    UserGuide,
    // Toolbar and sidebar only.
    CiteSoftware,
    EstimateDriftRate,
    IsolateBurst,
    PlotMaximumIntensities,
    RectangularZoom,
    LockNavigation,
    UnlockNavigation,
    ResetSelection,
    SubtractBackground,
    ToggleSidebar,
    /// File → Recent Files, by position (newest first).
    OpenRecentFits(usize),
    ClearRecentFiles,
    TrimTimelineStart,
    TrimTimelineEnd,
    /// A menu action in one of the analysis windows' own menu bars.
    Window(WindowAction),
}

/// The analysis windows' menu actions.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum WindowAction {
    SaveAs,
    ExportAs,
    ResetAll,
    RestoreMaxima,
    OpenAnalyzer,
    RidgeSettings,
    About,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Item {
    Action {
        label: Cow<'static, str>,
        shortcut: Option<&'static str>,
        enabled: bool,
        /// `Some` for checkable items.
        checked: Option<bool>,
        command: Command,
    },
    Submenu {
        label: Cow<'static, str>,
        items: Vec<Item>,
    },
    Separator,
}

impl Item {
    pub(crate) fn action(label: impl Into<Cow<'static, str>>, command: Command) -> Self {
        Item::Action { label: label.into(), shortcut: None, enabled: true, checked: None, command }
    }

    fn shortcut(mut self, keys: &'static str) -> Self {
        if let Item::Action { shortcut, .. } = &mut self {
            *shortcut = Some(keys);
        }
        self
    }

    pub(crate) fn enabled(mut self, on: bool) -> Self {
        if let Item::Action { enabled, .. } = &mut self {
            *enabled = on;
        }
        self
    }

    fn checked(mut self, on: bool) -> Self {
        if let Item::Action { checked, .. } = &mut self {
            *checked = Some(on);
        }
        self
    }

    fn submenu(label: impl Into<Cow<'static, str>>, items: Vec<Item>) -> Self {
        Item::Submenu { label: label.into(), items }
    }

    pub fn label(&self) -> Option<&str> {
        match self {
            Item::Action { label, .. } | Item::Submenu { label, .. } => Some(label),
            Item::Separator => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Menu {
    pub title: &'static str,
    pub items: Vec<Item>,
}

/// The application state the menus depend on.
#[derive(Clone, Debug, Default)]
pub struct MenuState {
    pub has_data: bool,
    /// File → Recent Files labels (`fits_entry_label`), newest first.
    pub recent_files: Vec<String>,
    pub can_undo: bool,
    pub can_redo: bool,
    pub theme: ThemeMode,
    pub prefetch_adjacent: bool,
    pub auto_clean_max_outliers: bool,
    pub click_light_curve: bool,
    /// Processed data (background subtracted): the analysis windows need it.
    pub has_noise: bool,
    pub has_light_curves: bool,
    pub has_ruler_measurement: bool,
}

/// `_rebuild_recent_menu`: the selections, then Clear; or a disabled placeholder.
fn recent_files_items(labels: &[String]) -> Vec<Item> {
    if labels.is_empty() {
        return vec![Item::action("No Recent Files", Command::Open).enabled(false)];
    }
    let mut items: Vec<Item> =
        labels.iter().enumerate().map(|(i, l)| Item::action(l.clone(), Command::OpenRecentFits(i))).collect();
    items.push(Item::Separator);
    items.push(Item::action("Clear Recent Files", Command::ClearRecentFiles));
    items
}

pub fn menu_bar(s: &MenuState) -> Vec<Menu> {
    use Command as C;
    let a = Item::action;
    let data = s.has_data;
    vec![
        Menu {
            title: "File",
            items: vec![
                a("Open", C::Open),
                Item::submenu("Recent Files", recent_files_items(&s.recent_files)),
                a("Combine FITS Files...", C::CombineFits),
                Item::Separator,
                a("Open Project...", C::OpenProject).shortcut("Ctrl+Shift+O"),
                Item::submenu("Recent Projects", vec![a("No Recent Projects", C::OpenProject).enabled(false)]),
                a("Save Project", C::SaveProject).shortcut("Ctrl+S").enabled(data),
                a("Save Project As...", C::SaveProjectAs).shortcut("Ctrl+Shift+S").enabled(data),
                a("Recover Last Session...", C::RecoverLastSession),
                a("Generate Project Report...", C::GenerateProjectReport).enabled(data),
                Item::Separator,
                a("Save As", C::SaveAs).enabled(data),
                Item::submenu(
                    "Export As",
                    vec![
                        a("Export Figure", C::ExportFigure),
                        a("Export to FIT", C::ExportToFit),
                        a("Export Provenance Report...", C::ExportProvenanceReport),
                        a("Export Analysis Log...", C::ExportAnalysisLog),
                    ],
                ),
            ],
        },
        Menu {
            title: "Edit",
            items: vec![
                a("Undo", C::Undo).shortcut("Ctrl+Z").enabled(s.can_undo),
                a("Redo", C::Redo).shortcut("Ctrl+Shift+Z").enabled(s.can_redo),
                Item::Separator,
                a("Add Previous Observation", C::AddPreviousObservation).shortcut("Ctrl+Shift+Left").enabled(data),
                a("Add Next Observation", C::AddNextObservation).shortcut("Ctrl+Shift+Right").enabled(data),
                a("Prefetch Adjacent Observations", C::TogglePrefetch).checked(s.prefetch_adjacent),
                Item::Separator,
                a("Reset to Raw", C::ResetToRaw).enabled(data),
                Item::Separator,
                a("Reset All", C::ResetAll),
            ],
        },
        Menu { title: "Download", items: vec![a("Launch FITS Downloader", C::LaunchDownloader)] },
        Menu {
            title: "Solar Events",
            items: vec![Item::submenu(
                "Radio Bursts",
                vec![a("e-CALLISTO", C::LaunchDownloader), a("Learmonth", C::LaunchLearmonthDownloader)],
            )],
        },
        Menu {
            title: "View",
            items: vec![
                a("View FITS Header", C::ViewFitsHeader).enabled(data),
                a("Set Display Range...", C::SetDisplayRange).enabled(data),
                Item::Separator,
                a("Save Display Range Preset...", C::SaveDisplayRangePreset).enabled(data),
                a("Apply Display Range Preset...", C::ApplyDisplayRangePreset).enabled(data),
                a("Delete Display Range Preset...", C::DeleteDisplayRangePreset).enabled(data),
                Item::Separator,
                a("Export View Config...", C::ExportViewConfig).enabled(data),
                a("Import View Config...", C::ImportViewConfig).enabled(data),
                Item::Separator,
                a("Multi-Station Comparison...", C::MultiStationComparison),
                Item::Separator,
                Item::submenu(
                    "Theme",
                    [ThemeMode::System, ThemeMode::Light, ThemeMode::Dark]
                        .into_iter()
                        .map(|m| a(m.label(), C::Theme(m)).checked(s.theme == m))
                        .collect(),
                ),
            ],
        },
        Menu {
            title: "Analysis",
            items: vec![
                Item::submenu(
                    "Maximum Intensities",
                    vec![a("Open Maximum Intensities", C::OpenMaximumIntensities).enabled(s.has_noise)],
                ),
                Item::submenu(
                    "Type II Band-splitting",
                    vec![a("Open Type II Band-splitting", C::OpenTypeIIBandSplitting).enabled(s.has_noise)],
                ),
                Item::submenu(
                    "Plot Light Curves",
                    vec![
                        a("Enter frequency", C::LightCurveEnterFrequency).enabled(data),
                        a("Click on a frequency", C::LightCurveClickFrequency)
                            .checked(s.click_light_curve)
                            .enabled(data),
                        a("Settings...", C::LightCurveSettings),
                        Item::Separator,
                        a("Clear light curve(s)", C::ClearLightCurves).enabled(data && s.has_light_curves),
                    ],
                ),
                Item::Separator,
                a("Ruler Measurement", C::RulerMeasurement).enabled(data),
                a("Clear Ruler Measurement", C::ClearRulerMeasurement).enabled(s.has_ruler_measurement),
            ],
        },
        Menu {
            title: "Processing",
            items: vec![
                Item::submenu(
                    "RFI Cleaning",
                    vec![
                        a("Open RFI Panel", C::OpenRfiPanel),
                        a("Apply RFI", C::ApplyRfi),
                        a("Reset RFI", C::ResetRfi),
                    ],
                ),
                Item::submenu(
                    "Annotations",
                    vec![
                        a("Add Polygon", C::AddPolygon),
                        a("Add Line", C::AddLine),
                        a("Add Text", C::AddText),
                        a("Edit Text Label...", C::EditTextLabel),
                        a("Move Text Label...", C::MoveTextLabel),
                        Item::Separator,
                        a("Toggle Visibility", C::ToggleAnnotations),
                        a("Delete Last", C::DeleteLastAnnotation),
                        a("Clear All", C::ClearAnnotations),
                    ],
                ),
                Item::submenu(
                    "Presets",
                    vec![
                        a("Raw FITS Percentile (5-98%)", C::BuiltinPreset).enabled(data),
                        Item::Separator,
                        a("Save Current as Preset...", C::SavePreset),
                        a("Apply Preset...", C::ApplyPreset),
                        a("Set Default Preset...", C::SetDefaultPreset),
                        a("Clear Default Preset", C::ClearDefaultPreset),
                        a("Delete Preset...", C::DeletePreset),
                    ],
                ),
                Item::submenu(
                    "Maximum Intensity",
                    vec![
                        a("Auto-Clean Isolated Burst Outliers", C::ToggleAutoCleanMaxOutliers)
                            .checked(s.auto_clean_max_outliers),
                    ],
                ),
                Item::submenu("Analysis Session", vec![a("Open Restored Analysis", C::OpenRestoredAnalysis)]),
                Item::submenu("Batch Processing", vec![a("Open Batch Processor", C::OpenBatchProcessor)]),
            ],
        },
        Menu {
            title: "About",
            items: vec![
                a("Check for Updates...", C::CheckForUpdates),
                a("Report a Bug...", C::ReportBug),
                Item::Separator,
                a("About", C::About),
            ],
        },
        Menu { title: "Help", items: vec![a("User Guide", C::UserGuide).shortcut("F1")] },
    ]
}
