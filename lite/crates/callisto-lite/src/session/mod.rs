//! The loaded dataset and everything the main window does with it.
//!
//! A port of the data side of the full app's `MainWindow`
//! (`src/ui/app/main_window.py`): loading and combining, the processed
//! product, noise-clipping thresholds, units, the time and frequency axes,
//! undo/redo and the resets. Method names follow the Python ones so the two
//! can be read side by side. Drawing is left to `crate::canvas`, which this
//! drives as the full app drives its `AcceleratedPlotWidget`.

mod analysis;
mod display_range;
mod rfi;
mod timeline;
mod tools;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::mpsc::Receiver;
use std::time::{Duration, Instant};

use callisto_core::fits::Header;
use callisto_core::plot::Colormap;
use callisto_core::radio::array::Array2;
use callisto_core::radio::artemis;
use callisto_core::radio::combine::{self, CombineOptions, CombineType, Combined, FrequencyRelation};
use callisto_core::radio::fits_io::{self, extract_ut_start_sec};
use callisto_core::radio::frequency_axis::{
    finite_data_limits, frequency_edges, matplotlib_extent, percentile_data_limits, pyqtgraph_extent,
};
use callisto_core::radio::naming::basename;
use callisto_core::radio::processing::{self, BackgroundMethod, PLOTUTIL_DB_SCALE};
use callisto_core::radio::timeline::TimelineState;

use crate::canvas::{CanvasEvent, ImageUpdate, PlotCanvas, TextStyle, View};
pub use display_range::{DisplayRange, DisplayRangePreset, DisplayRangeRequest, delete_preset, upsert_preset};
pub use rfi::RfiConfig;
pub use timeline::ProbeResult;
pub use tools::{AnnotationMode, LightCurveRecord, RulerReadout, ToolState};
use timeline::{DatasetExtras, TimelineMsg};

/// `MainWindow.DB_SCALE`: digits to dB for CALLISTO receivers.
pub const DB_SCALE: f64 = 2500.0 / 256.0 / 25.4;
pub const NOISE_CLIP_MIN: f64 = -100.0;
pub const NOISE_CLIP_MAX: f64 = 100.0;
pub const NOISE_SLIDER_MIN: i32 = 0;
pub const NOISE_SLIDER_MAX: i32 = 4000;
pub const NOISE_SLIDER_MID: i32 = NOISE_SLIDER_MAX / 2;
pub const RAW_FITS_PRESET_NAME: &str = "Raw FITS Percentile (5-98%)";
pub const RAW_FITS_VMIN_PERCENTILE: f64 = 5.0;
pub const RAW_FITS_VMAX_PERCENTILE: f64 = 98.0;
const HW_DEFAULT_TICK_FONT_PX: u32 = 14;
const HW_DEFAULT_AXIS_FONT_PX: u32 = 16;
const HW_DEFAULT_TITLE_FONT_PX: u32 = 22;
const MAX_UNDO: usize = 30;
const MAX_HISTORY_ENTRIES: usize = 5000;
/// `noise_smooth_timer` and `noise_commit_timer`.
const NOISE_SMOOTH_DELAY: Duration = Duration::from_millis(12);

/// `MainWindow.BACKGROUND_METHODS`, in menu order.
pub const BACKGROUND_METHODS: [(BackgroundMethod, &str); 3] = [
    (BackgroundMethod::Mean, "Mean"),
    (BackgroundMethod::Median, "Median"),
    (BackgroundMethod::PlotutilMedianDb, "Median (dB)"),
];

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ClipScale {
    #[default]
    Linear,
    SignedLog,
}

/// A message box to show, as `QMessageBox.information/warning/critical`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum NoticeKind {
    Information,
    Warning,
    Critical,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Notice {
    pub kind: NoticeKind,
    pub title: String,
    pub text: String,
}

/// A selection waiting for the frequency-combine options dialog.
#[derive(Clone, Debug)]
pub struct PendingCombine {
    pub paths: Vec<String>,
    pub relation: Option<FrequencyRelation>,
}

/// The dataset behind the plot, as `_assign_dataset_arrays` describes it.
#[derive(Clone, Debug)]
pub struct Dataset {
    pub raw: Arc<Array2>,
    pub freqs: Arc<Vec<f64>>,
    pub time: Arc<Vec<f64>>,
    pub filename: String,
    pub header0: Option<Header>,
    pub headers: Vec<(String, Header)>,
    pub source_path: Option<String>,
    pub ut_start_sec: Option<f64>,
    pub combined_mode: Option<CombineType>,
    pub combined_sources: Vec<String>,
    pub combined_options: Option<CombineOptions>,
    pub gap_rows: Option<Vec<bool>>,
    pub frequency_step_mhz: Option<f64>,
}

/// Graph Properties values that change the plot.
#[derive(Clone, Debug, PartialEq)]
pub struct GraphProps {
    pub cmap: String,
    /// Font family; empty is the app font ("Default").
    pub font_family: String,
    pub title_override: String,
    pub remove_titles: bool,
    pub tick_px: u32,
    pub axis_px: u32,
    pub title_px: u32,
    pub title_bold: bool,
    pub title_italic: bool,
    pub axis_bold: bool,
    pub axis_italic: bool,
    pub ticks_bold: bool,
    pub ticks_italic: bool,
    /// `_hw_default_font_sizes_active`: the canvas uses 14/16/22 px until a
    /// font size is changed.
    pub hw_default_sizes: bool,
}

impl Default for GraphProps {
    fn default() -> Self {
        Self {
            cmap: "Custom".into(),
            font_family: String::new(),
            title_override: String::new(),
            remove_titles: false,
            tick_px: 11,
            axis_px: 12,
            title_px: 14,
            title_bold: false,
            title_italic: false,
            axis_bold: false,
            axis_italic: false,
            ticks_bold: false,
            ticks_italic: false,
            hw_default_sizes: true,
        }
    }
}

/// `_capture_state`.
#[derive(Clone, Debug)]
struct Snapshot {
    raw: Arc<Array2>,
    freqs: Arc<Vec<f64>>,
    time: Arc<Vec<f64>>,
    gap_rows: Option<Vec<bool>>,
    filename: String,
    frequency_step_mhz: Option<f64>,
    noise_reduced: Option<Arc<Array2>>,
    noise_reduced_original: Option<Arc<Array2>>,
    noise_reduced_original_plot_type: String,
    current_plot_type: String,
    noise_clip_low: f64,
    noise_clip_high: f64,
    noise_clip_scale: ClipScale,
    background_method: BackgroundMethod,
    background_applied: Option<BackgroundMethod>,
    use_db: bool,
    use_utc: bool,
    cmap: String,
    view: Option<View>,
    /// Set by `_capture_dataset_state` (timeline edits).
    extras: Option<Box<DatasetExtras>>,
}

#[derive(Clone, Debug)]
enum Entry {
    State(Box<Snapshot>),
    View(View),
}

pub struct Session {
    pub canvas: PlotCanvas,
    pub data: Option<Dataset>,
    pub noise_reduced: Option<Arc<Array2>>,
    noise_reduced_original: Option<Arc<Array2>>,
    /// Processing → RFI Cleaning settings and the last preview.
    pub rfi: RfiConfig,
    rfi_preview: Option<Arc<Array2>>,
    noise_reduced_original_plot_type: String,
    pub current_plot_type: String,
    /// What the plot shows, in display units (for the cursor readout).
    current_display_data: Option<Arc<Array2>>,
    /// What the plot was made from, before units (`_current_plot_source_data`).
    current_plot_source: Option<Arc<Array2>>,
    /// The ruler, drift, lasso, light curves and annotations.
    pub tools: ToolState,
    /// `_analysis_session`: what the analysis windows share.
    pub analysis: Option<callisto_core::analysis::session::AnalysisSession>,
    /// New data cleared the analysis: its windows close.
    pub analysis_windows_close_due: bool,

    pub noise_clip_low: f64,
    pub noise_clip_high: f64,
    pub noise_clip_scale: ClipScale,
    noise_clip_min: f64,
    noise_clip_max: f64,
    /// Slider positions, NOISE_SLIDER_MIN..=MAX.
    pub lower_slider: i32,
    pub upper_slider: i32,
    noise_slider_drag_active: bool,
    noise_update_due: Option<Instant>,
    noise_undo_pending: bool,

    pub background_method: BackgroundMethod,
    pub background_applied: Option<BackgroundMethod>,
    pub use_db: bool,
    pub use_utc: bool,
    pub freq_axis_log: bool,
    pub graph: GraphProps,
    artemis: Option<artemis::Calibration>,

    undo_stack: Vec<Entry>,
    redo_stack: Vec<Entry>,
    home_view: Option<View>,
    pub nav_locked: bool,
    pub rect_zoom_active: bool,

    pub cursor_text: String,
    /// The Timeline section's segments, when the dataset has CALLISTO sources.
    pub timeline: Option<TimelineState>,
    /// Replaces the Timeline status line: worker progress, or why it failed.
    pub timeline_message: Option<String>,
    /// The probe's answer for (next, previous), once it has one.
    pub timeline_available: Option<ProbeResult>,
    timeline_job: Option<Receiver<TimelineMsg>>,
    timeline_probe: Option<Receiver<ProbeResult>>,
    timeline_undo: Option<Box<Snapshot>>,
    /// Edit → Prefetch Adjacent Observations.
    pub prefetch_adjacent: bool,
    cache_root: Option<PathBuf>,
    online: bool,
    status: Option<(String, Option<Instant>)>,
    pub notices: Vec<Notice>,
    pub pending_combine: Option<PendingCombine>,
    /// `plot_data` runs `_plot_data_internal` on the next event-loop turn,
    /// which shows "Loaded: <file>" after whatever the caller showed.
    loaded_message_due: bool,
    /// Selections that opened, newest last, for File → Recent Files.
    pub opened: Vec<Vec<String>>,
    log: Vec<String>,
}

impl Default for Session {
    fn default() -> Self {
        Self::new()
    }
}

impl Session {
    pub fn new() -> Self {
        Self {
            canvas: PlotCanvas::new(),
            data: None,
            noise_reduced: None,
            noise_reduced_original: None,
            rfi: RfiConfig::default(),
            rfi_preview: None,
            noise_reduced_original_plot_type: "Background Subtracted".into(),
            current_plot_type: "Raw".into(),
            current_display_data: None,
            current_plot_source: None,
            analysis: None,
            analysis_windows_close_due: false,
            tools: ToolState::new(),
            noise_clip_low: 0.0,
            noise_clip_high: 0.0,
            noise_clip_scale: ClipScale::Linear,
            noise_clip_min: NOISE_CLIP_MIN,
            noise_clip_max: NOISE_CLIP_MAX,
            lower_slider: NOISE_SLIDER_MID,
            upper_slider: NOISE_SLIDER_MID,
            noise_slider_drag_active: false,
            noise_update_due: None,
            noise_undo_pending: false,
            background_method: BackgroundMethod::Mean,
            background_applied: None,
            use_db: false,
            use_utc: false,
            freq_axis_log: false,
            graph: GraphProps::default(),
            artemis: None,
            undo_stack: Vec::new(),
            redo_stack: Vec::new(),
            home_view: None,
            nav_locked: false,
            rect_zoom_active: false,
            cursor_text: String::new(),
            timeline: None,
            timeline_message: None,
            timeline_available: None,
            timeline_job: None,
            timeline_probe: None,
            timeline_undo: None,
            prefetch_adjacent: true,
            cache_root: None,
            online: false,
            status: None,
            notices: Vec::new(),
            pending_combine: None,
            loaded_message_due: false,
            opened: Vec::new(),
            log: Vec::new(),
        }
    }

    pub fn has_data(&self) -> bool {
        self.data.is_some()
    }

    pub fn filename(&self) -> &str {
        self.data.as_ref().map_or("", |d| d.filename.as_str())
    }

    // ------------------------------------------------------------ status

    /// `statusBar().showMessage(text, timeout_ms)`; 0 keeps it until replaced.
    pub fn show_message(&mut self, text: impl Into<String>, timeout_ms: u64) {
        let until = (timeout_ms > 0).then(|| Instant::now() + Duration::from_millis(timeout_ms));
        self.status = Some((text.into(), until));
    }

    /// The status message still showing, and when it goes.
    pub fn status_message(&mut self) -> Option<(&str, Option<Instant>)> {
        if let Some((_, Some(until))) = &self.status
            && Instant::now() >= *until
        {
            self.status = None;
        }
        self.status.as_ref().map(|(t, u)| (t.as_str(), *u))
    }

    fn warn(&mut self, kind: NoticeKind, title: &str, text: impl Into<String>) {
        self.notices.push(Notice { kind, title: title.into(), text: text.into() });
    }

    fn log_operation(&mut self, message: impl Into<String>) {
        self.log.push(message.into());
        if self.log.len() > 500 {
            self.log.drain(..self.log.len() - 500);
        }
    }

    // ----------------------------------------------------------- loading

    /// `open_fits_paths`: one file is loaded, several are combined.
    pub fn open_fits_paths(&mut self, paths: &[String]) -> bool {
        let paths: Vec<String> = paths.iter().filter(|p| !p.trim().is_empty()).cloned().collect();
        if paths.is_empty() {
            return false;
        }
        if paths.len() == 1 {
            let path = &paths[0];
            match fits_io::load_callisto_fits(Path::new(path)) {
                Ok(res) => {
                    let name = basename(path).to_owned();
                    let ut = extract_ut_start_sec(&res.header0);
                    self.apply_loaded_dataset(
                        Dataset {
                            raw: Arc::new(res.data),
                            freqs: Arc::new(res.freqs),
                            time: Arc::new(res.time),
                            filename: name.clone(),
                            header0: Some(res.header0),
                            headers: res.headers,
                            source_path: Some(path.clone()),
                            ut_start_sec: ut,
                            combined_mode: None,
                            combined_sources: Vec::new(),
                            combined_options: None,
                            gap_rows: None,
                            frequency_step_mhz: None,
                        },
                        "Raw",
                        format!("Loaded FITS file: {name}"),
                    );
                    self.opened.push(paths.clone());
                    true
                }
                Err(e) => {
                    self.warn(
                        NoticeKind::Critical,
                        "Open FITS Failed",
                        format!("Could not open the FITS file(s):\n{e}"),
                    );
                    false
                }
            }
        } else {
            let inspection = combine::inspect_combination(&paths);
            if !inspection.valid {
                let error = inspection.error.unwrap_or_else(|| "The selected FITS files cannot be combined.".into());
                self.warn(
                    NoticeKind::Warning,
                    "Invalid Combination Selection",
                    format!(
                        "{error}\n\nValid selections are consecutive time segments, distinct frequency bands at one \
                         timestamp, or a complete consecutive timestamp × focus-code grid."
                    ),
                );
                return false;
            }
            if matches!(inspection.combine_type, Some(CombineType::Frequency | CombineType::TimeFrequency)) {
                // FrequencyCombineOptionsDialog.choose: ask only when there is a gap or an overlap.
                let relation = match inspection.frequency_relation.clone() {
                    Some(r) => r,
                    None => match combine::describe_frequency_combination(&paths) {
                        Ok(r) => r,
                        Err(e) => {
                            self.warn(
                                NoticeKind::Critical,
                                "Frequency Combine Options",
                                format!("Could not inspect frequency ranges before combining:\n{e}"),
                            );
                            return false;
                        }
                    },
                };
                if relation.has_gap() || relation.has_overlap() {
                    self.pending_combine = Some(PendingCombine { paths, relation: Some(relation) });
                    return true;
                }
            }
            let combined = match inspection.combined {
                Some(c) => Ok(c),
                None => combine::combine_compatible(&paths, CombineOptions::default()),
            };
            self.finish_combine(&paths, combined)
        }
    }

    /// The frequency-combine options dialog was accepted (or cancelled).
    pub fn resolve_pending_combine(&mut self, options: Option<CombineOptions>) {
        let Some(pending) = self.pending_combine.take() else { return };
        let Some(options) = options else { return };
        let combined = combine::combine_compatible(&pending.paths, options);
        self.finish_combine(&pending.paths, combined);
    }

    fn finish_combine(&mut self, paths: &[String], combined: anyhow::Result<Combined>) -> bool {
        match combined {
            Ok(c) => {
                self.load_combined_into_main(c);
                self.show_message(format!("Loaded {} files (combined)", paths.len()), 5000);
                self.log_operation(format!("Loaded combined FITS set ({} files).", paths.len()));
                self.opened.push(paths.to_vec());
                true
            }
            Err(e) => {
                self.warn(
                    NoticeKind::Critical,
                    "Combine Error",
                    format!("An error occurred while combining files:\n{e}"),
                );
                false
            }
        }
    }

    /// `_load_single_import_payload`: a file the downloader prepared (no
    /// log entry, not added to Recent Files).
    pub fn load_imported_single(&mut self, filename: String, source_path: String, res: fits_io::FitsLoadResult) {
        let ut = extract_ut_start_sec(&res.header0);
        self.apply_loaded_dataset(
            Dataset {
                raw: Arc::new(res.data),
                freqs: Arc::new(res.freqs),
                time: Arc::new(res.time),
                filename: if filename.is_empty() { "Imported".into() } else { filename },
                header0: Some(res.header0),
                headers: res.headers,
                source_path: Some(source_path),
                ut_start_sec: ut,
                combined_mode: None,
                combined_sources: Vec::new(),
                combined_options: None,
                gap_rows: None,
                frequency_step_mhz: None,
            },
            "Raw",
            String::new(),
        );
    }

    /// `load_combined_into_main`.
    pub fn load_combined_into_main(&mut self, c: Combined) {
        self.apply_loaded_dataset(
            Dataset {
                raw: Arc::new(c.data),
                freqs: Arc::new(c.freqs),
                time: Arc::new(c.time),
                filename: c.filename,
                header0: Some(c.header0),
                headers: Vec::new(),
                source_path: None,
                ut_start_sec: c.ut_start_sec,
                combined_mode: Some(c.combine_type),
                combined_sources: c.sources,
                combined_options: c.options,
                gap_rows: c.gap_row_mask,
                frequency_step_mhz: c.frequency_step_mhz,
            },
            "Raw",
            "Loaded combined dataset into main window.".into(),
        );
    }

    /// `_apply_loaded_dataset`.
    fn apply_loaded_dataset(&mut self, mut d: Dataset, plot_title: &str, log_message: String) {
        if d.ut_start_sec.is_none()
            && let Some(h) = &d.header0
        {
            d.ut_start_sec = extract_ut_start_sec(h);
        }
        if let Some(g) = &d.gap_rows
            && g.len() != d.freqs.len()
        {
            d.gap_rows = None;
        }
        self.data = Some(d);
        self.refresh_instrument_profile();

        self.reset_runtime_state_for_loaded_data();
        self.reset_noise_controls_to_defaults();
        let raw = self.data.as_ref().map(|d| d.raw.clone());
        self.fit_noise_clip_bounds_to_data(raw.as_deref());
        let artemis_bg = self.prepare_artemis_display_defaults();
        let mut title = plot_title.to_owned();
        if self.noise_reduced.is_some() {
            title = self.current_plot_type.clone();
        }
        self.plot_data(&title, PlotView::Full);
        self.drop_interaction_overlays();
        self.refresh_timeline_panel();
        if !log_message.is_empty() {
            self.log_operation(log_message);
        }
        if artemis_bg {
            self.show_message("ARTEMIS-IV — opened background subtracted", 8000);
        }
    }

    /// `_refresh_instrument_profile`: ARTEMIS-IV files have their own scale.
    fn refresh_instrument_profile(&mut self) {
        self.artemis = self
            .data
            .as_ref()
            .and_then(|d| d.header0.as_ref())
            .filter(|h| artemis::is_artemis_header(h))
            .map(|h| artemis::artemis_calibration(&artemis::artemis_receiver(h)));
    }

    /// `_reset_runtime_state_for_loaded_data`.
    fn reset_runtime_state_for_loaded_data(&mut self) {
        self.noise_reduced = None;
        self.noise_reduced_original = None;
        self.noise_reduced_original_plot_type = "Background Subtracted".into();
        self.background_applied = None;
        self.current_display_data = None;
        self.current_plot_source = None;
        self.undo_stack.clear();
        self.redo_stack.clear();
        self.reset_feature_state_for_new_data();
        self.rfi = RfiConfig::default();
        self.rfi_preview = None;
    }

    /// `_prepare_artemis_display_defaults`: ARTEMIS files open background subtracted.
    fn prepare_artemis_display_defaults(&mut self) -> bool {
        if self.artemis.is_none() || !self.set_background_product(self.background_method, None) {
            return false;
        }
        let Some(base) = self.noise_reduced.clone() else { return false };
        if let Some((lo, hi)) = finite_data_limits(&base.data) {
            self.expand_noise_clip_bounds_for_values(&[lo, hi]);
        }
        let Some((low, high)) =
            artemis::suggested_display_range(&base.data, RAW_FITS_VMIN_PERCENTILE, RAW_FITS_VMAX_PERCENTILE)
        else {
            return false;
        };
        self.set_noise_clip_state(Some(low), Some(high), None);
        true
    }

    // --------------------------------------------------------- plotting

    fn current_product(&self) -> Option<Arc<Array2>> {
        self.noise_reduced.clone().or_else(|| self.data.as_ref().map(|d| d.raw.clone()))
    }

    /// `plot_data` + `_plot_data_internal` + `_refresh_accel_plot`.
    pub fn plot_data(&mut self, title: &str, view: PlotView) {
        let Some(source) = self.current_product() else { return };
        self.plot_source(source, title, view);
    }

    /// `plot_data` with explicit data (the RFI preview is not the product).
    fn plot_source(&mut self, source: Arc<Array2>, title: &str, view: PlotView) {
        let view = match view {
            PlotView::Full => None,
            PlotView::Keep => self.canvas.view(),
            PlotView::Restore(v) => v,
        };
        self.cancel_rect_zoom();
        let display = self.intensity_for_display(&source);
        self.current_display_data = Some(display.clone());
        self.current_plot_source = Some(source);
        let plot_type = normalize_plot_type(title);
        self.current_plot_type = plot_type.clone();
        self.refresh_canvas(&display, &plot_type, view);
        // _refresh_accel_plot ends with _render_light_curve_accel_overlay.
        self.render_light_curve_overlay();
        self.home_view = self.mpl_home_view();
        self.loaded_message_due = true;
    }

    /// Work the full app defers to the next event-loop turn; call once per
    /// frame. Returns whether anything changed (and needs drawing).
    pub fn flush_deferred(&mut self) -> bool {
        if self.flush_pending_isolation() {
            return true;
        }
        if std::mem::take(&mut self.loaded_message_due) && self.has_data() {
            let text = format!("Loaded: {}", self.filename());
            self.show_message(text, 5000);
            return true;
        }
        false
    }

    fn refresh_canvas(&mut self, display: &Arc<Array2>, plot_type: &str, view: Option<View>) {
        let Some(d) = &self.data else { return };
        let Some(extent) = pyqtgraph_extent(&d.freqs, &d.time, d.frequency_step_mhz) else { return };
        let remove = self.graph.remove_titles;
        let title = if remove {
            String::new()
        } else if !self.graph.title_override.trim().is_empty() {
            self.graph.title_override.clone()
        } else {
            self.default_graph_title(plot_type)
        };
        let x_label = if remove {
            String::new()
        } else if self.use_utc && d.ut_start_sec.is_some() {
            "Time [UT]".into()
        } else {
            "Time [s]".into()
        };
        let y_label = if remove { String::new() } else { "Frequency [MHz]".into() };
        let colorbar_label =
            if remove { String::new() } else { format!("Intensity [{}]", self.intensity_unit_label()) };
        let g = &self.graph;
        let (tick_px, axis_px, title_px) = if g.hw_default_sizes {
            (HW_DEFAULT_TICK_FONT_PX, HW_DEFAULT_AXIS_FONT_PX, HW_DEFAULT_TITLE_FONT_PX)
        } else {
            (g.tick_px, g.axis_px, g.title_px)
        };
        self.canvas.set_text_style(TextStyle {
            family: (!g.font_family.is_empty()).then(|| g.font_family.clone()),
            tick_px,
            axis_px,
            title_px,
            title_bold: g.title_bold,
            title_italic: g.title_italic,
            axis_bold: g.axis_bold,
            axis_italic: g.axis_italic,
            ticks_bold: g.ticks_bold,
            ticks_italic: g.ticks_italic,
        });
        let levels = self.threshold_display_levels();
        let log_freq = self.freq_axis_log && self.frequency_axis_log_usable();
        let (freqs, gap, ut) = (d.freqs.clone(), d.gap_rows.clone(), d.ut_start_sec);
        self.canvas.update_image(ImageUpdate {
            data: display.clone(),
            extent,
            cmap: Colormap::by_name(&self.graph.cmap),
            gap_rows: gap.as_deref(),
            levels,
            title,
            x_label,
            y_label,
            colorbar_label,
            view,
            freqs: &freqs,
            log_freq,
        });
        self.canvas.set_time_mode(self.use_utc, ut);
    }

    /// The view `Reset Selection` returns to: the matplotlib axes' limits
    /// after a plot (`_home_view`), which the full app keeps for both canvases.
    fn mpl_home_view(&self) -> Option<View> {
        let d = self.data.as_ref()?;
        let [x0, x1, ylo, yhi] = matplotlib_extent(&d.freqs, &d.time, d.frequency_step_mhz)?;
        let ylim = if self.freq_axis_log && self.frequency_axis_log_usable() {
            self.positive_frequency_bounds().unwrap_or((ylo, yhi))
        } else {
            (ylo, yhi)
        };
        Some(View { xlim: (x0, x1), ylim })
    }

    /// `_default_graph_title`.
    fn default_graph_title(&self, plot_type: &str) -> String {
        let base = self.filename().trim();
        let base = if base.is_empty() { "Untitled" } else { base };
        format!("{base}-{}", normalize_plot_type(plot_type))
    }

    /// `_intensity_for_display`: dB conversion when the Units say so.
    fn intensity_for_display(&self, data: &Arc<Array2>) -> Arc<Array2> {
        if !self.display_converts_to_db() {
            return data.clone();
        }
        let (cold, _) = self.db_hot_cold_digits();
        let (c, s) = (cold as f32, self.intensity_db_scale() as f32);
        let mut out = (**data).clone();
        for v in out.data.iter_mut() {
            *v = (*v - c) * s;
        }
        Arc::new(out)
    }

    fn intensity_range_for_display(&self, lo: f64, hi: f64) -> (f64, f64) {
        if !self.display_converts_to_db() {
            return (lo, hi);
        }
        let (cold, _) = self.db_hot_cold_digits();
        let s = self.intensity_db_scale();
        ((lo - cold) * s, (hi - cold) * s)
    }

    /// `_threshold_display_levels`.
    fn threshold_display_levels(&self) -> Option<(f64, f64)> {
        if !self.noise_clip_thresholds_active() {
            return None;
        }
        let (lo, hi) = sorted(self.noise_clip_low, self.noise_clip_high);
        let (lo, hi) = self.intensity_range_for_display(lo, hi);
        if !(lo.is_finite() && hi.is_finite()) {
            return None;
        }
        let hi = if hi <= lo { lo + (lo.abs() * 1e-6).max(1e-6) } else { hi };
        Some((lo, hi))
    }

    // ------------------------------------------------------------- units

    pub fn intensity_linear_unit(&self) -> &'static str {
        if self.artemis.is_some() { "Counts" } else { "Digits" }
    }

    pub fn intensity_unit_label(&self) -> &'static str {
        if self.use_db || self.background_product_is_db() { "dB" } else { self.intensity_linear_unit() }
    }

    fn intensity_db_scale(&self) -> f64 {
        self.artemis.as_ref().map_or(DB_SCALE, |c| c.db_per_count())
    }

    pub fn background_product_is_db(&self) -> bool {
        self.background_applied == Some(BackgroundMethod::PlotutilMedianDb) && self.noise_reduced.is_some()
    }

    fn display_converts_to_db(&self) -> bool {
        self.use_db && !self.background_product_is_db()
    }

    fn db_hot_cold_digits(&self) -> (f64, f64) {
        sorted(self.noise_clip_low, self.noise_clip_high)
    }

    /// `set_units_mode`.
    pub fn set_units_mode(&mut self, use_db: bool) {
        self.use_db = use_db;
        if self.has_data() {
            let title = self.current_plot_type.clone();
            self.plot_data(&title, PlotView::Keep);
        }
    }

    /// `set_axis_to_seconds` / `set_axis_to_utc`.
    pub fn set_time_axis(&mut self, use_utc: bool) {
        self.use_utc = use_utc;
        if self.has_data() {
            let title = self.current_plot_type.clone();
            self.plot_data(&title, PlotView::Keep);
        }
    }

    // ------------------------------------------------- frequency axis

    fn frequency_axis_log_usable(&self) -> bool {
        let Some(d) = &self.data else { return false };
        let pos: Vec<f64> = d.freqs.iter().copied().filter(|f| f.is_finite() && *f > 0.0).collect();
        pos.len() >= 2 && pos.iter().copied().fold(f64::MIN, f64::max) > pos.iter().copied().fold(f64::MAX, f64::min)
    }

    /// `_positive_frequency_bounds`.
    fn positive_frequency_bounds(&self) -> Option<(f64, f64)> {
        let d = self.data.as_ref()?;
        let pos: Vec<f64> = d.freqs.iter().copied().filter(|f| f.is_finite() && *f > 0.0).collect();
        if pos.len() < 2 {
            return None;
        }
        let (mut lo, mut hi) =
            (pos.iter().copied().fold(f64::MAX, f64::min), pos.iter().copied().fold(f64::MIN, f64::max));
        if hi <= lo {
            return None;
        }
        let edges = frequency_edges(&d.freqs, d.frequency_step_mhz.unwrap_or(1.0));
        let finite = edges.iter().copied().filter(|e| e.is_finite());
        let (el, eh) = finite.fold((f64::MAX, f64::MIN), |(a, b), e| (a.min(e), b.max(e)));
        if el.is_finite() && eh.is_finite() && el > 0.0 && eh > el {
            (lo, hi) = (el, eh);
        }
        Some((lo, hi))
    }

    /// `set_frequency_axis_scale`.
    pub fn set_frequency_axis_log(&mut self, log: bool) {
        if log == self.freq_axis_log {
            return;
        }
        self.freq_axis_log = log;
        if !self.has_data() {
            return;
        }
        if log && !self.frequency_axis_log_usable() {
            self.show_message("This dataset has no positive frequency range, so a log axis is not available.", 6000);
            return;
        }
        let title = self.current_plot_type.clone();
        self.plot_data(&title, PlotView::Full);
        self.log_operation(format!("Frequency axis set to {}.", if log { "log" } else { "linear" }));
    }

    // ---------------------------------------------- noise thresholds

    fn noise_clip_bounds(&self) -> (f64, f64) {
        let (lo, hi) = (self.noise_clip_min, self.noise_clip_max);
        if !lo.is_finite() || !hi.is_finite() || hi <= lo { (NOISE_CLIP_MIN, NOISE_CLIP_MAX) } else { (lo, hi) }
    }

    fn reset_noise_clip_bounds(&mut self) {
        self.noise_clip_min = NOISE_CLIP_MIN;
        self.noise_clip_max = NOISE_CLIP_MAX;
    }

    /// `_expand_noise_clip_bounds_for_values`: symmetric bounds wide enough
    /// for every value, with 5 % to spare.
    fn expand_noise_clip_bounds_for_values(&mut self, values: &[f64]) {
        let finite: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
        if finite.is_empty() {
            return;
        }
        let (lo, hi) = self.noise_clip_bounds();
        let cur = lo.abs().max(hi.abs()).max(1.0);
        let required = finite.iter().map(|v| v.abs()).fold(0.0, f64::max);
        if required <= cur {
            return;
        }
        let max_abs = (required * 1.05).max(cur);
        self.noise_clip_min = -max_abs;
        self.noise_clip_max = max_abs;
    }

    fn clamp_noise_threshold(&self, v: f64) -> f64 {
        let (lo, hi) = self.noise_clip_bounds();
        let v = if v.is_finite() { v } else { 0.0 };
        v.min(hi).max(lo)
    }

    /// `_noise_threshold_to_slider`.
    pub fn noise_threshold_to_slider(&self, value: f64) -> i32 {
        let val = self.clamp_noise_threshold(value);
        let (lo, hi) = self.noise_clip_bounds();
        let pos = if self.noise_clip_scale == ClipScale::SignedLog {
            let max_abs = lo.abs().max(hi.abs());
            let u = if val.abs() <= 1e-12 {
                0.0
            } else {
                let denom = (1.0 + max_abs).log10();
                if denom > 0.0 { ((1.0 + val.abs()).log10() / denom).copysign(val) } else { 0.0 }
            };
            NOISE_SLIDER_MID + (u * NOISE_SLIDER_MID as f64).round_ties_even() as i32
        } else {
            let fraction = (val - lo) / (hi - lo);
            NOISE_SLIDER_MIN + (fraction * (NOISE_SLIDER_MAX - NOISE_SLIDER_MIN) as f64).round_ties_even() as i32
        };
        pos.clamp(NOISE_SLIDER_MIN, NOISE_SLIDER_MAX)
    }

    /// `_noise_slider_to_threshold`.
    pub fn noise_slider_to_threshold(&self, slider: i32) -> f64 {
        let raw = slider.clamp(NOISE_SLIDER_MIN, NOISE_SLIDER_MAX);
        let (lo, hi) = self.noise_clip_bounds();
        if self.noise_clip_scale == ClipScale::SignedLog {
            let u = ((raw - NOISE_SLIDER_MID) as f64 / NOISE_SLIDER_MID as f64).clamp(-1.0, 1.0);
            let max_abs = lo.abs().max(hi.abs());
            let magnitude = 10f64.powf(u.abs() * (1.0 + max_abs).log10()) - 1.0;
            return self.clamp_noise_threshold(magnitude.copysign(u));
        }
        let fraction = (raw - NOISE_SLIDER_MIN) as f64 / (NOISE_SLIDER_MAX - NOISE_SLIDER_MIN) as f64;
        self.clamp_noise_threshold(lo + fraction * (hi - lo))
    }

    fn noise_clip_thresholds_active(&self) -> bool {
        self.noise_clip_low.abs() > 1e-9 || self.noise_clip_high.abs() > 1e-9
    }

    fn reset_noise_controls_to_defaults(&mut self) {
        self.reset_noise_clip_bounds();
        self.set_noise_clip_state(Some(0.0), Some(0.0), Some(ClipScale::Linear));
    }

    /// `_set_noise_clip_state`, which also places the sliders.
    pub fn set_noise_clip_state(&mut self, low: Option<f64>, high: Option<f64>, scale: Option<ClipScale>) {
        let wanted: Vec<f64> = [low, high].into_iter().flatten().collect();
        self.expand_noise_clip_bounds_for_values(&wanted);
        let mut lo = low.map_or(self.noise_clip_low, |v| self.clamp_noise_threshold(v));
        let mut hi = high.map_or(self.noise_clip_high, |v| self.clamp_noise_threshold(v));
        if lo > hi {
            std::mem::swap(&mut lo, &mut hi);
        }
        self.noise_clip_low = lo;
        self.noise_clip_high = hi;
        if let Some(s) = scale {
            self.noise_clip_scale = s;
        }
        self.lower_slider = self.noise_threshold_to_slider(self.noise_clip_low);
        self.upper_slider = self.noise_threshold_to_slider(self.noise_clip_high);
    }

    /// `_fit_noise_clip_bounds_to_data`.
    fn fit_noise_clip_bounds_to_data(&mut self, data: Option<&Array2>) {
        if let Some(d) = data
            && let Some((lo, hi)) = finite_data_limits(&d.data)
        {
            self.expand_noise_clip_bounds_for_values(&[lo, hi]);
        }
        self.set_noise_clip_state(Some(self.noise_clip_low), Some(self.noise_clip_high), None);
    }

    /// `_on_noise_slider_value_changed`; `lower` says which slider moved.
    pub fn on_noise_slider_value_changed(&mut self, lower: bool, position: i32) {
        if lower {
            self.lower_slider = position;
        } else {
            self.upper_slider = position;
        }
        let mut low = self.noise_slider_to_threshold(self.lower_slider);
        let mut high = self.noise_slider_to_threshold(self.upper_slider);
        if low > high {
            if lower {
                low = high;
            } else {
                high = low;
            }
        }
        self.set_noise_clip_state(Some(low), Some(high), None);
        if self.has_data() {
            self.noise_update_due = Some(Instant::now() + NOISE_SMOOTH_DELAY);
        }
    }

    pub fn on_noise_slider_pressed(&mut self) {
        self.noise_slider_drag_active = true;
    }

    /// `_on_noise_slider_released`.
    pub fn on_noise_slider_released(&mut self) {
        self.noise_slider_drag_active = false;
        if !self.has_data() {
            return;
        }
        self.noise_update_due = None;
        self.update_noise_live();
    }

    /// `_on_noise_scale_mode_toggled`.
    pub fn on_noise_scale_mode_toggled(&mut self, log: bool) {
        let scale = if log { ClipScale::SignedLog } else { ClipScale::Linear };
        if scale != self.noise_clip_scale {
            self.set_noise_clip_state(Some(self.noise_clip_low), Some(self.noise_clip_high), Some(scale));
        }
    }

    /// `update_noise_live`: the thresholds become the colour limits; the data are untouched.
    fn update_noise_live(&mut self) {
        if !self.has_data() {
            return;
        }
        // One undo step per drag: the first update records it, the release clears it.
        if !self.noise_undo_pending {
            self.push_undo_state();
            self.noise_undo_pending = true;
        }
        let title = self.current_plot_type.clone();
        self.plot_data(&title, PlotView::Keep);
        if !self.noise_slider_drag_active {
            self.noise_undo_pending = false;
        }
    }

    /// Timers that run between frames. Returns when to call again.
    pub fn tick(&mut self) -> Option<Duration> {
        self.poll_timeline();
        if self.timeline_pending() {
            return Some(Duration::from_millis(100));
        }
        if let Some(due) = self.noise_update_due {
            let now = Instant::now();
            if now >= due {
                self.noise_update_due = None;
                self.update_noise_live();
            } else {
                return Some(due - now);
            }
        }
        None
    }

    /// `_noise_clip_display_values`: the value cards' text and, in dB mode
    /// over digits, the dB line under each.
    pub fn noise_clip_labels(&self) -> [(String, Option<String>); 2] {
        let (lo, hi) = (self.noise_clip_low, self.noise_clip_high);
        let unit = self.intensity_linear_unit();
        if self.background_product_is_db() {
            return [(format!("{lo:.2} dB"), None), (format!("{hi:.2} dB"), None)];
        }
        if !self.use_db {
            return [(format!("{lo:.2} {unit}"), None), (format!("{hi:.2} {unit}"), None)];
        }
        let (cold, _) = self.db_hot_cold_digits();
        let s = self.intensity_db_scale();
        [
            (format!("{lo:.2} {unit}"), Some(format!("{:.2} dB", (lo - cold) * s))),
            (format!("{hi:.2} {unit}"), Some(format!("{:.2} dB", (hi - cold) * s))),
        ]
    }

    /// Processing → Presets → Raw FITS Percentile (5-98%).
    pub fn apply_raw_fits_percentile_preset(&mut self) {
        if !self.has_data() {
            self.warn(NoticeKind::Information, "Raw FITS Percentile Preset", "Load a FITS file first.");
            return;
        }
        let Some(source) = self.current_product() else { return };
        let Some((low, high)) =
            percentile_data_limits(&source.data, RAW_FITS_VMIN_PERCENTILE, RAW_FITS_VMAX_PERCENTILE)
        else {
            self.warn(
                NoticeKind::Warning,
                "Raw FITS Percentile Preset",
                "The loaded raw FITS data do not contain finite intensity values.",
            );
            return;
        };
        self.push_undo_state();
        self.reset_noise_clip_bounds();
        self.set_noise_clip_state(Some(low), Some(high), None);
        self.fit_noise_clip_bounds_to_data(Some(&source));
        let title = self.current_plot_type.clone();
        self.plot_data(&title, PlotView::Keep);
        self.show_message(
            format!("Applied {RAW_FITS_PRESET_NAME}: {low:.2} to {high:.2} {}.", self.intensity_linear_unit()),
            4000,
        );
    }

    // ------------------------------------------------------- background

    fn background_db_scale(&self) -> f64 {
        self.data
            .as_ref()
            .and_then(|d| d.header0.as_ref())
            .map_or(PLOTUTIL_DB_SCALE, |h| artemis::db_scale_for_header(h, PLOTUTIL_DB_SCALE))
    }

    fn background_subtracted_from_raw(&self, method: BackgroundMethod) -> Option<Array2> {
        let d = self.data.as_ref()?;
        Some(processing::subtract_background(&d.raw, method, d.gap_rows.as_deref(), false, self.background_db_scale()))
    }

    /// `_set_background_product`.
    fn set_background_product(&mut self, method: BackgroundMethod, data: Option<Array2>) -> bool {
        let Some(data) = data.or_else(|| self.background_subtracted_from_raw(method)) else { return false };
        let data = Arc::new(data);
        self.noise_reduced = Some(data.clone());
        self.noise_reduced_original = Some(data);
        self.noise_reduced_original_plot_type = "Background Subtracted".into();
        self.background_applied = Some(method);
        self.current_plot_type = "Background Subtracted".into();
        true
    }

    fn noise_threshold_space(&self) -> &'static str {
        match (self.background_applied, self.noise_reduced.is_some()) {
            (Some(BackgroundMethod::PlotutilMedianDb), true) => "db",
            (Some(_), true) => "subtracted",
            _ => "raw",
        }
    }

    pub fn set_background_method(&mut self, method: BackgroundMethod) {
        self.background_method = method;
    }

    /// `apply_background_subtraction`.
    pub fn apply_background_subtraction(&mut self) -> bool {
        if !self.has_data() {
            self.warn(NoticeKind::Information, "Background Subtraction", "Load a FITS file first.");
            return false;
        }
        let mode = self.background_method;
        let Some(data) = self.background_subtracted_from_raw(mode) else { return false };
        self.noise_update_due = None;
        self.noise_undo_pending = false;
        self.push_undo_state();
        let previous = self.noise_threshold_space();
        self.set_background_product(mode, Some(data));
        self.rfi_preview = None;
        self.rfi.applied = false;
        if self.noise_threshold_space() != previous {
            self.reset_noise_clip_bounds();
            self.set_noise_clip_state(Some(0.0), Some(0.0), None);
        }
        let product = self.noise_reduced.clone();
        self.fit_noise_clip_bounds_to_data(product.as_deref());
        self.plot_data("Background Subtracted", PlotView::Keep);
        let label = background_method_label(mode);
        self.log_operation(format!("Subtracted the background from the raw data ({label})."));
        self.show_message(format!("Background subtracted ({label})."), 3000);
        true
    }

    /// The sidebar's status line under Subtract Background.
    pub fn background_status(&self) -> String {
        if !self.has_data() {
            return "Load a FITS file to subtract its background.".into();
        }
        let plot_type = normalize_plot_type(&self.current_plot_type);
        match (self.background_applied, self.noise_reduced.is_some()) {
            (Some(m), true) => {
                let mut text = format!("Applied: {}", background_method_label(m));
                if plot_type != "Raw" && plot_type != "Background Subtracted" {
                    text.push_str(&format!(" · {plot_type}"));
                }
                text
            }
            (_, true) => format!("Not applied — showing {plot_type}."),
            _ => "Not applied — showing the raw data.".into(),
        }
    }

    // ------------------------------------------------------- timeline

    /// `_timeline_source_paths`: the files behind the dataset that still exist.
    fn timeline_source_paths(&self) -> Vec<String> {
        let Some(d) = &self.data else { return Vec::new() };
        let sources = if d.combined_sources.is_empty() {
            d.source_path.iter().cloned().collect()
        } else {
            d.combined_sources.clone()
        };
        sources.into_iter().filter(|p| !p.is_empty() && Path::new(p).exists()).collect()
    }

    /// The Timeline section's status line.
    pub fn timeline_status(&self) -> String {
        if let Some(message) = &self.timeline_message {
            return message.clone();
        }
        match &self.timeline {
            Some(state) => {
                let count = state.segments.len();
                let noun = if count == 1 { "observation" } else { "observations" };
                format!(
                    "{} · {}–{} · {count} {noun}",
                    state.station,
                    state.start().format("%H:%M"),
                    state.stop().format("%H:%M")
                )
            }
            None if !self.has_data() => "Load a CALLISTO file to step through the archive.".into(),
            None if self.artemis.is_some() => {
                "Archive stepping is e-CALLISTO only — this ARTEMIS-IV observation already covers its full time range."
                    .into()
            }
            None => "This dataset's source files are not available for stepping.".into(),
        }
    }

    /// The Timeline list's rows: `HH:MM   focus + focus`.
    pub fn timeline_rows(&self) -> Vec<String> {
        self.timeline.as_ref().map_or_else(Vec::new, |state| {
            state
                .segments
                .iter()
                .map(|seg| format!("{}   {}", seg.observed_at.format("%H:%M"), seg.focus_codes.join(" + ")))
                .collect()
        })
    }

    // ------------------------------------------------------ FITS header

    /// `open_fits_header_viewer`: the viewer's title, file name and text,
    /// or a notice when nothing is loaded.
    pub fn fits_header_view(&mut self) -> Option<(String, String, String)> {
        let Some(d) = &self.data else {
            self.warn(NoticeKind::Information, "FITS Header", "Load a FITS file first.");
            return None;
        };
        let header = d.header0.clone().unwrap_or_default();
        let name = d.filename.as_str();
        let stem = Path::new(name).file_stem().and_then(|s| s.to_str()).filter(|s| !s.is_empty()).unwrap_or("fits");
        let default_name = format!("{stem}_header.txt");
        let title = if name.is_empty() { "FITS Header".to_owned() } else { format!("FITS Header — {name}") };
        // An ARTEMIS-IV preamble, then the cards verbatim.
        let preamble = artemis::artemis_header_report(&header, &[]);
        Some((title, default_name, format!("{preamble}{}", header.to_text())))
    }

    // ------------------------------------------------- graph properties

    /// `change_cmap`.
    pub fn change_cmap(&mut self, name: &str) {
        self.graph.cmap = name.to_owned();
        if self.has_data() {
            let title = self.current_plot_type.clone();
            self.plot_data(&title, PlotView::Keep);
        }
    }

    /// `apply_graph_properties_live`: re-style the plot, keeping the view.
    pub fn apply_graph_properties_live(&mut self) {
        if self.has_data() {
            let title = self.current_plot_type.clone();
            self.plot_data(&title, PlotView::Keep);
        }
    }

    // --------------------------------------------------------- cursor

    /// `_update_cursor_label_from_xy`.
    pub fn update_cursor_label(&mut self, x: f64, y: f64, inside: bool) {
        if !inside {
            self.cursor_text = "t = 0.00   |   f = 0.00 MHz   |   I = 0.00".into();
            return;
        }
        let (Some(d), Some(display)) = (&self.data, &self.current_display_data) else { return };
        if d.time.is_empty() || d.freqs.is_empty() {
            return;
        }
        let ix = argmin_abs(&d.time, x);
        let iy = argmin_abs(&d.freqs, y);
        if ix >= display.cols || iy >= display.rows {
            return;
        }
        let t = d.time[ix];
        let f = d.freqs[iy];
        let intensity = display.get(iy, ix) as f64;
        let time_str = match (self.use_utc, d.ut_start_sec) {
            (true, Some(start)) => {
                let total = start + t;
                let hours = ((total / 3600.0).floor() as i64).rem_euclid(24);
                let minutes = (total.rem_euclid(3600.0) / 60.0).floor() as i64;
                let seconds = total.rem_euclid(60.0).floor() as i64;
                format!("{hours:02}:{minutes:02}:{seconds:02} UT")
            }
            _ => format!("{t:.2} s"),
        };
        self.cursor_text = format!(
            "t = {time_str}   |   f = {f:.2} MHz   |   I = {} {}",
            py_fixed2(intensity),
            self.intensity_unit_label()
        );
    }

    /// Canvas events: the cursor readout and the view history.
    pub fn handle_canvas_events(&mut self) {
        for event in self.canvas.take_events() {
            match event {
                CanvasEvent::MouseMoved { x, y, inside } => self.update_cursor_label(x, y, inside),
                CanvasEvent::ViewInteractionFinished { start, end } => {
                    if !start.close_to(&end, 1e-6) {
                        self.push_undo_view(start);
                    }
                }
                CanvasEvent::RectZoomFinished { start, end } => {
                    self.rect_zoom_active = false;
                    if !start.close_to(&end, 1e-6) {
                        self.push_undo_view(start);
                    }
                    self.canvas.set_navigation_locked(self.nav_locked);
                    self.show_message("Zoomed to selected region (still locked).", 2500);
                }
                other => {
                    self.handle_tool_event(&other);
                }
            }
        }
    }

    // ----------------------------------------------------- navigation

    fn cancel_rect_zoom(&mut self) {
        self.rect_zoom_active = false;
        self.canvas.cancel_rect_zoom();
        self.canvas.set_navigation_locked(self.nav_locked);
    }

    /// `lock`.
    pub fn lock(&mut self) {
        if !self.has_data() {
            self.show_message("Load a FITS file first.", 2500);
            return;
        }
        self.nav_locked = true;
        self.cancel_rect_zoom();
        self.canvas.set_navigation_locked(true);
        self.show_message("Navigation locked. Use Rectangle Zoom if needed.", 3000);
    }

    /// `unlock`.
    pub fn unlock(&mut self) {
        if !self.has_data() {
            self.show_message("Load a FITS file first.", 2500);
            return;
        }
        self.nav_locked = false;
        self.cancel_rect_zoom();
        self.canvas.set_navigation_locked(false);
        self.show_message("Navigation unlocked. Pan and scroll zoom enabled.", 3000);
    }

    /// `rectangular_zoom`.
    pub fn rectangular_zoom(&mut self) {
        if !self.has_data() {
            self.show_message("Load a FITS file first.", 2500);
            return;
        }
        if !self.nav_locked {
            self.show_message("Click Lock first to enable Rectangle Zoom.", 3500);
            return;
        }
        self.cancel_rect_zoom();
        self.rect_zoom_active = true;
        self.canvas.start_rect_zoom_once();
        self.show_message("Drag a rectangle on the plot to zoom.", 4000);
    }

    fn can_reset_view(&self) -> bool {
        match (self.canvas.view(), self.home_view) {
            (Some(cur), Some(home)) => !cur.close_to(&home, 1e-6),
            _ => false,
        }
    }

    // ------------------------------------------------------- history

    fn capture_state(&self) -> Option<Snapshot> {
        let d = self.data.as_ref()?;
        Some(Snapshot {
            raw: d.raw.clone(),
            freqs: d.freqs.clone(),
            time: d.time.clone(),
            gap_rows: d.gap_rows.clone(),
            filename: d.filename.clone(),
            frequency_step_mhz: d.frequency_step_mhz,
            noise_reduced: self.noise_reduced.clone(),
            noise_reduced_original: self.noise_reduced_original.clone(),
            noise_reduced_original_plot_type: self.noise_reduced_original_plot_type.clone(),
            current_plot_type: self.current_plot_type.clone(),
            noise_clip_low: self.noise_clip_low,
            noise_clip_high: self.noise_clip_high,
            noise_clip_scale: self.noise_clip_scale,
            background_method: self.background_method,
            background_applied: self.background_applied,
            use_db: self.use_db,
            use_utc: self.use_utc,
            cmap: self.graph.cmap.clone(),
            view: self.canvas.view(),
            extras: None,
        })
    }

    fn trim_history(&mut self) {
        let states = |s: &[Entry]| s.iter().filter(|e| matches!(e, Entry::State(_))).count();
        while states(&self.undo_stack) > MAX_UNDO {
            let Some(i) = self.undo_stack.iter().position(|e| matches!(e, Entry::State(_))) else { break };
            self.undo_stack.drain(..=i);
        }
        for stack in [&mut self.undo_stack, &mut self.redo_stack] {
            if stack.len() > MAX_HISTORY_ENTRIES {
                let extra = stack.len() - MAX_HISTORY_ENTRIES;
                stack.drain(..extra);
            }
        }
    }

    /// `_push_undo_view`.
    fn push_undo_view(&mut self, view: View) {
        if let Some(Entry::View(last)) = self.undo_stack.last()
            && last.close_to(&view, 1e-6)
        {
            return;
        }
        self.undo_stack.push(Entry::View(view));
        self.redo_stack.clear();
        self.trim_history();
    }

    /// `_push_undo_state`.
    fn push_undo_state(&mut self) {
        let Some(s) = self.capture_state() else { return };
        self.undo_stack.push(Entry::State(Box::new(s)));
        self.redo_stack.clear();
        self.trim_history();
    }

    /// `_restore_state`.
    fn restore_state(&mut self, s: Snapshot) {
        let Some(d) = self.data.as_mut() else { return };
        d.raw = s.raw;
        d.freqs = s.freqs;
        d.time = s.time;
        d.gap_rows = s.gap_rows;
        d.filename = s.filename;
        d.frequency_step_mhz = s.frequency_step_mhz;
        self.noise_reduced = s.noise_reduced;
        self.noise_reduced_original = s.noise_reduced_original;
        self.noise_reduced_original_plot_type = normalize_plot_type(&s.noise_reduced_original_plot_type);
        self.current_plot_type = s.current_plot_type;
        self.use_db = s.use_db;
        self.use_utc = s.use_utc;
        self.graph.cmap = s.cmap;
        self.expand_noise_clip_bounds_for_values(&[s.noise_clip_low, s.noise_clip_high]);
        let (mut lo, mut hi) =
            (self.clamp_noise_threshold(s.noise_clip_low), self.clamp_noise_threshold(s.noise_clip_high));
        if lo > hi {
            std::mem::swap(&mut lo, &mut hi);
        }
        self.background_method = s.background_method;
        self.background_applied = if self.noise_reduced.is_some() { s.background_applied } else { None };
        self.set_noise_clip_state(Some(lo), Some(hi), Some(s.noise_clip_scale));
        let product = self.current_product();
        self.fit_noise_clip_bounds_to_data(product.as_deref());
        if let Some(extras) = s.extras {
            self.restore_dataset_extras(*extras);
        }
        let title = self.current_plot_type.clone();
        self.plot_data(&title, PlotView::Restore(s.view));
    }

    fn step_history(&mut self, undo: bool) {
        let (label, empty) = if undo { ("Undo", "Nothing to undo") } else { ("Redo", "Nothing to redo") };
        let entry = if undo { self.undo_stack.pop() } else { self.redo_stack.pop() };
        let Some(entry) = entry else {
            self.show_message(empty, 2000);
            return;
        };
        match entry {
            Entry::View(view) => {
                if let Some(cur) = self.canvas.view() {
                    let other = if undo { &mut self.redo_stack } else { &mut self.undo_stack };
                    other.push(Entry::View(cur));
                    self.trim_history();
                }
                self.canvas.set_view(view);
            }
            Entry::State(state) => {
                if let Some(cur) = self.capture_state() {
                    let other = if undo { &mut self.redo_stack } else { &mut self.undo_stack };
                    other.push(Entry::State(Box::new(cur)));
                    self.trim_history();
                }
                self.restore_state(*state);
            }
        }
        self.show_message(label, 2000);
    }

    pub fn undo(&mut self) {
        self.step_history(true);
    }

    pub fn redo(&mut self) {
        self.step_history(false);
    }

    pub fn can_undo(&self) -> bool {
        !self.undo_stack.is_empty()
    }

    pub fn can_redo(&self) -> bool {
        !self.redo_stack.is_empty()
    }

    // --------------------------------------------------------- resets

    /// `reset_to_raw`.
    pub fn reset_to_raw(&mut self) {
        if !self.has_data() {
            self.warn(NoticeKind::Warning, "No Data", "Load a FITS file first.");
            return;
        }
        let had_processed = self.noise_reduced.is_some()
            || self.noise_reduced_original.is_some()
            || self.tools.lasso_mask.is_some()
            || self.tools.lasso_active
            || self.noise_clip_thresholds_active()
            || self.background_applied.is_some();
        if had_processed {
            self.push_undo_state();
        }
        self.noise_update_due = None;
        self.noise_undo_pending = false;
        self.noise_slider_drag_active = false;
        self.drop_interaction_overlays();
        self.noise_reduced = None;
        self.noise_reduced_original = None;
        self.noise_reduced_original_plot_type = "Background Subtracted".into();
        self.tools.lasso_mask = None;
        self.rfi_preview = None;
        self.rfi.applied = false;
        self.background_applied = None;
        self.reset_noise_clip_bounds();
        self.set_noise_clip_state(Some(0.0), Some(0.0), None);
        let raw = self.data.as_ref().map(|d| d.raw.clone());
        self.fit_noise_clip_bounds_to_data(raw.as_deref());
        self.plot_data("Raw", PlotView::Full);
        self.show_message("Reset to raw", 3000);
    }

    /// `reset_selection`: back to the processed original, or to the home
    /// view; either way the drift markers go.
    pub fn reset_selection(&mut self) {
        let had_drift_points = self.clear_drift_overlays();
        if let Some(original) = self.noise_reduced_original.clone() {
            self.push_undo_state();
            self.noise_reduced = Some(original);
            let title = normalize_plot_type(&self.noise_reduced_original_plot_type);
            self.noise_reduced_original_plot_type = title.clone();
            self.current_plot_type = title.clone();
            if title == "RFI Cleaned" {
                self.rfi.applied = true;
            } else {
                self.rfi.applied = false;
                self.rfi.masked_channel_indices.clear();
                self.rfi_preview = None;
            }
            self.plot_data(&title, PlotView::Full);
            self.tools.lasso_mask = None;
            if had_drift_points {
                self.show_message("Selection reset (drift markers cleared)", 4000);
            } else {
                self.show_message("Selection Reset", 4000);
            }
            return;
        }
        if let (Some(cur), Some(home)) = (self.canvas.view(), self.home_view)
            && !cur.close_to(&home, 1e-6)
        {
            self.push_undo_view(cur);
            self.canvas.set_view(home);
            if had_drift_points {
                self.show_message("View reset (drift markers cleared)", 2500);
            } else {
                self.show_message("View reset", 2500);
            }
        } else if had_drift_points {
            self.show_message("Drift markers reset", 2500);
        }
    }

    /// `reset_all`: back to the window as it starts. The canvas keeps its
    /// text style, as the full app's does.
    pub fn reset_all(&mut self) {
        self.timeline_job = None;
        self.timeline_probe = None;
        self.timeline_undo = None;
        self.noise_update_due = None;
        self.noise_undo_pending = false;
        self.noise_slider_drag_active = false;
        self.reset_runtime_state_for_loaded_data();
        self.nav_locked = false;
        self.cancel_rect_zoom();
        self.canvas.set_navigation_locked(false);
        self.data = None;
        self.artemis = None;
        self.current_plot_type = "Raw".into();
        self.home_view = None;
        self.log.clear();
        self.canvas.clear();
        // _reset_sidebar_controls_to_defaults
        self.reset_noise_controls_to_defaults();
        self.background_method = BackgroundMethod::Mean;
        self.use_db = false;
        self.use_utc = false;
        self.graph = GraphProps::default();
        self.cursor_text.clear();
        self.refresh_timeline_panel();
        self.show_message("All reset", 4000);
    }

    // --------------------------------------------------- enable states

    pub fn toolbar_state(&self) -> ToolbarState {
        let has_file = self.has_data();
        let has_noise = self.noise_reduced.is_some();
        ToolbarState {
            has_file,
            export: has_file && !self.filename().is_empty(),
            save_project: has_file,
            undo: self.can_undo(),
            redo: self.can_redo(),
            processed_tools: has_noise,
            rect_zoom: self.nav_locked && has_file,
            lock: !self.nav_locked && has_file,
            unlock: self.nav_locked && has_file,
            reset_selection: has_noise || self.can_reset_view(),
            reset_to_raw: has_file,
            reset_all: has_file,
        }
    }
}

/// Which view a plot keeps.
#[derive(Clone, Copy, Debug)]
pub enum PlotView {
    /// The data's full extent.
    Full,
    /// The current zoom and pan.
    Keep,
    /// A saved view (undo/redo).
    Restore(Option<View>),
}

/// `_sync_toolbar_enabled_states`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ToolbarState {
    pub has_file: bool,
    pub export: bool,
    pub save_project: bool,
    pub undo: bool,
    pub redo: bool,
    pub processed_tools: bool,
    pub rect_zoom: bool,
    pub lock: bool,
    pub unlock: bool,
    pub reset_selection: bool,
    pub reset_to_raw: bool,
    pub reset_all: bool,
}

/// `_normalize_plot_type`.
pub fn normalize_plot_type(title: &str) -> String {
    let t = title.trim();
    if t.is_empty() {
        return "Raw".into();
    }
    let lower = t.to_lowercase();
    if ["raw", "raw data", "dynamic spectrum", "combined time", "combined time plot", "raw data (combined frequency)"]
        .contains(&lower.as_str())
    {
        return "Raw".into();
    }
    if lower == "background subtracted" || lower == "background-subtracted" {
        return "Background Subtracted".into();
    }
    t.to_owned()
}

pub fn background_method_label(m: BackgroundMethod) -> &'static str {
    BACKGROUND_METHODS.iter().find(|(k, _)| *k == m).map_or("Mean", |(_, l)| l)
}

fn sorted(a: f64, b: f64) -> (f64, f64) {
    if a > b { (b, a) } else { (a, b) }
}

/// `np.argmin(np.abs(values - x))`: the first closest index.
fn argmin_abs(values: &[f64], x: f64) -> usize {
    let mut best = 0;
    let mut best_d = f64::INFINITY;
    for (i, v) in values.iter().enumerate() {
        let d = (v - x).abs();
        if d < best_d || (best_d.is_nan() && !d.is_nan()) {
            best = i;
            best_d = d;
        }
    }
    best
}

/// Python's `str(float)`: the shortest repr, always with a decimal point.
pub(crate) fn py_float(v: f64) -> String {
    if v.is_finite() && v.fract() == 0.0 && v.abs() < 1e16 { format!("{v:.1}") } else { format!("{v}") }
}

/// Python's `f"{v:.2f}"`, including `nan` and `inf`.
fn py_fixed2(v: f64) -> String {
    if v.is_nan() {
        "nan".into()
    } else if v.is_infinite() {
        if v > 0.0 { "inf".into() } else { "-inf".into() }
    } else {
        format!("{v:.2}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> String {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/fixtures").join(name).to_string_lossy().into_owned()
    }

    #[test]
    fn slider_mapping_round_trips() {
        let s = Session::new();
        assert_eq!(s.noise_threshold_to_slider(0.0), NOISE_SLIDER_MID);
        assert_eq!(s.noise_threshold_to_slider(-100.0), 0);
        assert_eq!(s.noise_slider_to_threshold(4000), 100.0);
        let mut s = Session::new();
        s.noise_clip_scale = ClipScale::SignedLog;
        let pos = s.noise_threshold_to_slider(10.0);
        assert!((s.noise_slider_to_threshold(pos) - 10.0).abs() < 0.01);
    }

    #[test]
    fn load_background_undo_reset() {
        let mut s = Session::new();
        assert!(s.open_fits_paths(&[fixture("INDIA-GAURI_20261007_041500_02.fit.gz")]));
        assert_eq!(s.current_plot_type, "Raw");
        assert_eq!(s.background_status(), "Not applied — showing the raw data.");
        // The raw data reach 254 digits, so the sliders span ±266.7.
        assert!((s.noise_clip_max - 254.0 * 1.05).abs() < 1e-9);
        let t = s.toolbar_state();
        assert!(t.lock && !t.unlock && !t.rect_zoom && !t.reset_selection && !t.undo);

        assert!(s.apply_background_subtraction());
        assert_eq!(s.background_status(), "Applied: Mean");
        assert!(s.can_undo() && s.toolbar_state().processed_tools);
        s.undo();
        assert!(s.noise_reduced.is_none() && s.can_redo());
        s.redo();
        assert_eq!(s.current_plot_type, "Background Subtracted");
        s.reset_to_raw();
        assert_eq!(s.current_plot_type, "Raw");
        s.reset_all();
        assert!(!s.has_data() && !s.can_undo());
    }

    #[test]
    fn timeline_extends_from_local_files_and_trims() {
        let mut s = Session::new();
        let cache = std::env::temp_dir().join("callisto-lite-test-cache");
        s.set_archive(Some(cache), false);
        assert!(s.open_fits_paths(&[fixture("INDIA-GAURI_20261007_043000_02.fit.gz")]));
        assert_eq!(s.timeline_rows(), vec!["04:30   02"]);
        // 04:15 is next to it in the fixtures folder, so no download is needed.
        s.extend_timeline(callisto_core::radio::timeline::Direction::Previous, 1);
        let start = Instant::now();
        while s.timeline_busy() && start.elapsed() < Duration::from_secs(30) {
            s.tick();
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(s.timeline_rows(), vec!["04:15   02", "04:30   02"]);
        assert_eq!(s.timeline_status(), "INDIA-GAURI · 04:15–04:30 · 2 observations");
        assert!(s.data.as_ref().unwrap().time.len() > 3600);
        s.trim_timeline(false);
        assert_eq!(s.timeline_rows(), vec!["04:15   02"]);
        s.undo();
        assert_eq!(s.timeline_rows(), vec!["04:15   02", "04:30   02"]);
        s.undo();
        assert_eq!(s.timeline_rows(), vec!["04:30   02"]);
    }

    #[test]
    fn rfi_preview_apply_and_undo() {
        let mut s = Session::new();
        assert!(s.open_fits_paths(&[fixture("INDIA-GAURI_20261007_041500_02.fit.gz")]));
        assert_eq!(s.rfi_masked_text(), "Masked channels: 0");
        s.preview_rfi(callisto_core::radio::processing::RfiSettings::default());
        assert_eq!(s.current_plot_type, "Raw");
        s.apply_rfi(None);
        assert_eq!(s.current_plot_type, "RFI Cleaned");
        assert!(s.rfi.applied && s.noise_reduced.is_some());
        // Reset Selection goes back to what RFI cleaning started from.
        s.reset_selection();
        assert_eq!(s.current_plot_type, "Raw");
        assert!(!s.rfi.applied);
        s.undo();
        assert_eq!(s.current_plot_type, "RFI Cleaned");
        s.undo();
        assert!(s.noise_reduced.is_none());
    }

    #[test]
    fn cursor_readout_formats() {
        let mut s = Session::new();
        assert!(s.open_fits_paths(&[fixture("INDIA-GAURI_20261007_041500_02.fit.gz")]));
        s.update_cursor_label(0.0, 0.0, false);
        assert_eq!(s.cursor_text, "t = 0.00   |   f = 0.00 MHz   |   I = 0.00");
        s.update_cursor_label(100.0, 45.0, true);
        // The nearest channel to 45 MHz is 44.94 MHz.
        assert!(s.cursor_text.starts_with("t = 100.00 s   |   f = 44.94 MHz   |   I = "), "{}", s.cursor_text);
        assert!(s.cursor_text.ends_with(" Digits"));
    }
}
