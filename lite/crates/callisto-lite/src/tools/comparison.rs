//! Processing → Multi-Station Comparison (`MultiStationComparisonDialog`
//! in the full app's Modern view): stations side by side on one clock, a
//! hardware canvas per panel, with per-panel noise reduction, colour
//! scales, a ruler, a locked display range, view configs and exports.

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use callisto_core::analysis::measure::{Measurement, two_point};
use callisto_core::image::RgbaImage;
use callisto_core::mpl::figures::{GridOptions, comparison_grid_figure};
use callisto_core::radio::array::Array2;
use callisto_core::radio::combine::CombineType;
use callisto_core::radio::comparison::{
    Alignment, ColorScale, ComparisonDataset, DEFAULT_DB_SCALE, NoiseMethod, NoiseSettings, PanelOptions, PanelPayload,
    apply_noise, clip_in_place, comparison_colormap, datasets_from_paths, default_grid_title, load_comparison_dataset,
    panel_payloads, seconds_of_day_range_to_unwrapped, shared_extent,
};
use callisto_core::view_config::{DisplayRange, ViewConfig, VisualConfig};
use egui::{Color32, CornerRadius, Id, Pos2, Rect, Sense, Ui, UiBuilder, pos2, vec2};

use super::comparison_export::{DEFAULT_TITLE, ExportDialog, ExportOptions};
use crate::canvas::{CanvasEvent, ImageUpdate, PlotCanvas, View};
use crate::fonts::Weight;
use crate::session::{DisplayRangeRequest, Notice, NoticeKind};
use crate::theme::Palette;
use crate::ui::dialogs::{DisplayRangeDialog, DisplayRangeResult};
use crate::ui::qlayout::{BoxLayout, Container, Geometry, IRect, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

/// `_COLORMAP_NAMES`.
pub const COLORMAPS: [&str; 12] = [
    "Custom",
    "viridis",
    "plasma",
    "inferno",
    "magma",
    "cividis",
    "turbo",
    "RdYlBu",
    "jet",
    "cubehelix",
    "gray",
    "bone_r",
];
const ALIGNMENTS: [(&str, Alignment); 2] =
    [("UT clock", Alignment::Ut), ("Seconds from file start", Alignment::Seconds)];
const UNITS: [&str; 2] = ["Digits", "dB"];
const SCALES: [(&str, ColorScale); 3] = [
    ("Shared scale", ColorScale::Shared),
    ("Per-station auto scale", ColorScale::PerStation),
    ("Manual scale", ColorScale::Manual),
];
const METHODS: [(&str, NoiseMethod); 5] = [
    ("None", NoiseMethod::None),
    ("Mean background", NoiseMethod::Mean),
    ("Median background", NoiseMethod::Median),
    ("Robust background", NoiseMethod::Robust),
    ("Noise clipping", NoiseMethod::Clip),
];
const SLIDER_STEPS: i32 = 4000;
const SLIDER_MID: i32 = SLIDER_STEPS / 2;
const CLIP_MIN: f64 = -100.0;
const CLIP_MAX: f64 = 100.0;
/// `_redraw_timer`.
const REDRAW_DELAY: Duration = Duration::from_millis(35);
const EMPTY_TEXT: &str = "Add FITS files to build a comparison.";
/// `QListWidget` rows and the item text's margin.
const ROW_H: f32 = 16.0;
const ITEM_MARGIN: f32 = 3.0;
/// The panels' `setMinimumHeight(260)` and the panel layout's spacing.
const PANEL_MIN_H: f32 = 260.0;
const PANEL_SPACING: f32 = 6.0;
const BAR: f32 = 10.0;

/// `_noise_key_for_dataset`: a panel's identity.
type PanelKey = Vec<String>;

fn panel_key(d: &ComparisonDataset) -> PanelKey {
    let mut key = vec![
        "__comparison_panel__".to_owned(),
        d.combine_type.map_or("single", CombineType::key).to_owned(),
        d.label.clone(),
        d.path.clone(),
    ];
    key.extend(d.sources.iter().filter(|s| !s.trim().is_empty()).cloned());
    key
}

/// `_path_key`: absolute, for matching list rows to panels.
fn path_key(path: &str) -> String {
    std::path::absolute(path).map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| path.to_owned())
}

/// `_VisiblePanelState` with its canvas.
struct Panel {
    key: PanelKey,
    payload: PanelPayload,
}

/// `MeasurementReadout`.
#[derive(Clone, Debug)]
struct Readout {
    text: String,
    copy: bool,
    clear: bool,
}

impl Readout {
    fn empty() -> Self {
        Readout { text: "No measurement.".into(), copy: false, clear: false }
    }

    fn pending(text: &str) -> Self {
        Readout { text: text.into(), copy: false, clear: true }
    }
}

/// The visible export in progress: each panel scrolled into view and
/// captured from the screen, then stacked.
struct VisibleCapture {
    path: PathBuf,
    ext: String,
    next: usize,
    /// The panel is scrolled into view; the screenshot follows a frame later.
    scrolled: bool,
    /// The screenshot was asked for.
    asked: bool,
    images: Vec<RgbaImage>,
    scroll_before: f32,
}

pub enum ComparisonAction {
    Close,
}

pub struct ComparisonWindow {
    datasets: Vec<ComparisonDataset>,
    display: Vec<ComparisonDataset>,
    combined_mode: Option<String>,
    display_range: Option<DisplayRange>,
    user_changed_alignment: bool,
    alignment: usize,
    units: usize,
    cmap_items: Vec<String>,
    cmap: usize,
    scale: usize,
    manual_low: f64,
    manual_high: f64,
    noise_target: usize,
    noise_all: NoiseSettings,
    noise_overrides: HashMap<PanelKey, NoiseSettings>,
    clip_bounds: (f64, f64),
    low_pos: i32,
    high_pos: i32,
    noise_base: HashMap<(PanelKey, u8), Arc<Array2>>,
    panels: Vec<Panel>,
    canvases: Vec<PlotCanvas>,
    effective_alignment: Alignment,
    last_warnings: Vec<String>,
    measurements: HashMap<PanelKey, Measurement>,
    capture_key: Option<PanelKey>,
    capture_points: Vec<(f64, f64)>,
    readout: Readout,
    status: String,
    selected: BTreeSet<usize>,
    anchor: Option<usize>,
    list_first: usize,
    list_x: f32,
    controls_scroll: (f32, f32),
    plot_scroll: f32,
    left_w: f32,
    redraw_due: Option<Instant>,
    export_options: ExportOptions,
    export_dialog: Option<ExportDialog>,
    range_dialog: Option<(DisplayRangeDialog, (f64, f64))>,
    visible_capture: Option<VisibleCapture>,
    notices: Vec<Notice>,
    dark: bool,
    focus_given: bool,
}

impl ComparisonWindow {
    /// `MultiStationComparisonDialog(initial_paths=…)` with the main
    /// window's visual settings (`_apply_initial_view_config`).
    pub fn new(paths: &[String], visual: Option<&VisualConfig>, dark: bool) -> Self {
        let mut w = Self {
            datasets: Vec::new(),
            display: Vec::new(),
            combined_mode: None,
            display_range: None,
            user_changed_alignment: false,
            alignment: 0,
            units: 0,
            cmap_items: COLORMAPS.iter().map(|c| (*c).to_owned()).collect(),
            cmap: 0,
            scale: 0,
            manual_low: -10.0,
            manual_high: 10.0,
            noise_target: 0,
            noise_all: NoiseSettings::default(),
            noise_overrides: HashMap::new(),
            clip_bounds: (CLIP_MIN, CLIP_MAX),
            low_pos: SLIDER_MID,
            high_pos: SLIDER_MID,
            noise_base: HashMap::new(),
            panels: Vec::new(),
            canvases: Vec::new(),
            effective_alignment: Alignment::Seconds,
            last_warnings: Vec::new(),
            measurements: HashMap::new(),
            capture_key: None,
            capture_points: Vec::new(),
            readout: Readout::empty(),
            status: EMPTY_TEXT.into(),
            selected: BTreeSet::new(),
            anchor: None,
            list_first: 0,
            list_x: 0.0,
            controls_scroll: (0.0, 0.0),
            plot_scroll: 0.0,
            left_w: 380.0,
            redraw_due: None,
            export_options: ExportOptions::default(),
            export_dialog: None,
            range_dialog: None,
            visible_capture: None,
            notices: Vec::new(),
            dark,
            focus_given: false,
        };
        if let Some(v) = visual {
            w.apply_visual(v);
        }
        w.schedule_redraw(false);
        w.sync_noise_controls();
        if !paths.is_empty() {
            w.add_files(paths);
        }
        w
    }

    pub fn set_dark(&mut self, dark: bool) {
        if self.dark != dark {
            self.dark = dark;
            for c in &mut self.canvases {
                c.set_dark(dark);
            }
        }
    }

    // ------------------------------------------------------------ files

    /// `add_files`.
    pub fn add_files(&mut self, paths: &[String]) {
        let mut errors = Vec::new();
        let mut added = 0;
        for p in paths {
            let text = p.trim();
            if text.is_empty() || self.datasets.iter().any(|d| d.path == text) {
                continue;
            }
            match load_comparison_dataset(text) {
                Ok(d) => {
                    self.datasets.push(d);
                    added += 1;
                }
                Err(e) => errors.push(format!("{}: {e}", file_name(text))),
            }
        }
        if added > 0 {
            self.noise_base.clear();
            self.invalidate_panels();
        }
        self.refresh_display();
        self.rebuild_noise_targets();
        self.choose_default_alignment();
        self.schedule_redraw(false);
        if !errors.is_empty() {
            errors.truncate(8);
            self.notice(
                NoticeKind::Warning,
                "Add FITS Files",
                format!("Some files could not be loaded:\n{}", errors.join("\n")),
            );
        } else if added > 0 {
            self.status = format!("Added {added} FITS file(s).");
        }
    }

    /// `remove_selected_files`.
    fn remove_selected(&mut self) {
        if self.selected.is_empty() {
            return;
        }
        for &row in self.selected.iter().rev() {
            if row < self.datasets.len() {
                self.datasets.remove(row);
            }
        }
        self.selected.clear();
        self.anchor = None;
        self.noise_base.clear();
        self.invalidate_panels();
        self.display_range = None;
        self.refresh_display();
        self.rebuild_noise_targets();
        self.choose_default_alignment();
        self.schedule_redraw(false);
    }

    /// `clear_files`.
    fn clear_files(&mut self) {
        self.datasets.clear();
        self.display.clear();
        self.combined_mode = None;
        self.display_range = None;
        self.selected.clear();
        self.anchor = None;
        self.clear_measurements(true);
        self.noise_base.clear();
        self.invalidate_panels();
        self.rebuild_noise_targets();
        self.schedule_redraw(false);
    }

    /// `_refresh_display_datasets`: the files' largest valid combinations.
    fn refresh_display(&mut self) {
        self.combined_mode = None;
        self.display = self.datasets.clone();
        if self.datasets.len() < 2 {
            return;
        }
        let paths: Vec<String> = self.datasets.iter().map(|d| d.path.clone()).collect();
        if let Ok(active) = datasets_from_paths(&paths)
            && !active.is_empty()
        {
            let modes: BTreeSet<&str> = active.iter().filter_map(|d| d.combine_type.map(CombineType::key)).collect();
            self.combined_mode = match modes.len() {
                0 => None,
                1 => modes.first().map(|m| (*m).to_owned()),
                _ => Some("mixed".into()),
            };
            self.display = active;
        }
    }

    fn all_have_ut(&self) -> bool {
        !self.display.is_empty() && self.display.iter().all(|d| d.ut_start_sec.is_some())
    }

    fn alignment(&self) -> Alignment {
        ALIGNMENTS[self.alignment].1
    }

    fn set_alignment(&mut self, a: Alignment) {
        self.alignment = ALIGNMENTS.iter().position(|x| x.1 == a).unwrap_or(0);
    }

    /// `_choose_default_alignment_after_load`.
    fn choose_default_alignment(&mut self) {
        if self.user_changed_alignment {
            if self.alignment() == Alignment::Ut && !self.all_have_ut() {
                self.set_alignment(Alignment::Seconds);
                self.status = "UT alignment requires TIME-OBS in every selected file. Switched to seconds.".into();
            }
            return;
        }
        let ut = !self.datasets.is_empty() && self.all_have_ut();
        self.set_alignment(if ut { Alignment::Ut } else { Alignment::Seconds });
    }

    // ------------------------------------------------------------ noise

    fn noise_keys(&self) -> Vec<PanelKey> {
        self.display.iter().map(panel_key).collect()
    }

    /// `_current_noise_target_key`: `None` for all panels.
    fn target_key(&self) -> Option<PanelKey> {
        (self.noise_target > 0).then(|| self.noise_keys().get(self.noise_target - 1).cloned()).flatten()
    }

    fn settings_for(&self, key: Option<&PanelKey>) -> NoiseSettings {
        key.and_then(|k| self.noise_overrides.get(k)).copied().unwrap_or(self.noise_all)
    }

    fn effective_settings(&self) -> Vec<NoiseSettings> {
        self.display.iter().map(|d| self.settings_for(Some(&panel_key(d)))).collect()
    }

    /// `_set_noise_target_settings`.
    fn set_target_settings(&mut self, s: NoiseSettings) {
        let s = s.normalized();
        self.expand_clip_bounds(&[s.clip_low, s.clip_high]);
        match self.target_key() {
            None => {
                self.noise_all = s;
                self.noise_overrides.clear();
            }
            Some(k) => {
                self.noise_overrides.insert(k, s);
            }
        }
    }

    /// `_rebuild_noise_targets`.
    fn rebuild_noise_targets(&mut self) {
        let current = self.target_key();
        let keys = self.noise_keys();
        self.noise_overrides.retain(|k, _| keys.contains(k));
        self.noise_target = current.and_then(|c| keys.iter().position(|k| *k == c)).map_or(0, |i| i + 1);
        self.sync_noise_controls();
    }

    /// `_expand_noise_clip_bounds_for_values`: symmetric bounds that hold the values.
    fn expand_clip_bounds(&mut self, values: &[f64]) {
        let finite: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
        if finite.is_empty() {
            return;
        }
        let (lo, hi) = self.clip_bounds();
        let cur = lo.abs().max(hi.abs()).max(1.0);
        let need = finite.iter().map(|v| v.abs()).fold(0.0, f64::max);
        if need <= cur {
            return;
        }
        let m = (need * 1.05).max(cur);
        self.clip_bounds = (-m, m);
    }

    fn clip_bounds(&self) -> (f64, f64) {
        let (lo, hi) = self.clip_bounds;
        if lo.is_finite() && hi.is_finite() && hi > lo { (lo, hi) } else { (CLIP_MIN, CLIP_MAX) }
    }

    fn clamp_threshold(&self, v: f64) -> f64 {
        let (lo, hi) = self.clip_bounds();
        v.clamp(lo, hi)
    }

    fn log_scale(&self) -> bool {
        self.settings_for(self.target_key().as_ref()).log_scale
    }

    /// `_noise_threshold_to_slider`.
    fn threshold_to_slider(&self, value: f64) -> i32 {
        let v = self.clamp_threshold(value);
        let (lo, hi) = self.clip_bounds();
        if self.log_scale() {
            let max_abs = lo.abs().max(hi.abs());
            let unit = if v.abs() <= 1e-12 {
                0.0
            } else {
                let denom = (1.0 + max_abs).log10();
                if denom > 0.0 { ((1.0 + v.abs()).log10() / denom).copysign(v) } else { 0.0 }
            };
            return (SLIDER_MID + (unit * SLIDER_MID as f64).round_ties_even() as i32).clamp(0, SLIDER_STEPS);
        }
        (((v - lo) / (hi - lo)) * SLIDER_STEPS as f64).round_ties_even().clamp(0.0, SLIDER_STEPS as f64) as i32
    }

    /// `_noise_slider_to_threshold`.
    fn slider_to_threshold(&self, pos: i32) -> f64 {
        let raw = pos.clamp(0, SLIDER_STEPS);
        let (lo, hi) = self.clip_bounds();
        if self.log_scale() {
            let unit = ((raw - SLIDER_MID) as f64 / SLIDER_MID as f64).clamp(-1.0, 1.0);
            let max_abs = lo.abs().max(hi.abs());
            let magnitude = 10f64.powf(unit.abs() * (1.0 + max_abs).log10()) - 1.0;
            return self.clamp_threshold(magnitude.copysign(unit));
        }
        self.clamp_threshold(lo + raw as f64 / SLIDER_STEPS as f64 * (hi - lo))
    }

    /// `_sync_noise_controls_from_target`: the method, sliders and their labels.
    fn sync_noise_controls(&mut self) {
        let s = self.settings_for(self.target_key().as_ref());
        self.expand_clip_bounds(&[0.0, s.clip_low, s.clip_high]);
        self.low_pos = self.threshold_to_slider(s.clip_low);
        self.high_pos = self.threshold_to_slider(s.clip_high);
    }

    fn method_index(&self) -> usize {
        let m = self.settings_for(self.target_key().as_ref()).method;
        METHODS.iter().position(|x| x.1 == m).unwrap_or(0)
    }

    /// `_on_noise_method_changed`.
    fn on_method(&mut self, i: usize, ctx: &egui::Context) {
        let key = self.target_key();
        self.ensure_cache_for_edit(key.as_ref());
        let current = self.settings_for(key.as_ref());
        self.set_target_settings(NoiseSettings { method: METHODS[i].1, ..current });
        self.sync_noise_controls();
        self.redraw_for_target(key, ctx);
    }

    /// `_on_noise_slider_changed`: the thresholds, the lower never above the upper.
    fn on_slider(&mut self, low_moved: bool, ctx: &egui::Context) {
        let key = self.target_key();
        self.ensure_cache_for_edit(key.as_ref());
        let mut low = self.slider_to_threshold(self.low_pos);
        let mut high = self.slider_to_threshold(self.high_pos);
        if low > high {
            if low_moved {
                low = high;
                self.low_pos = self.high_pos;
            } else {
                high = low;
                self.high_pos = self.low_pos;
            }
        }
        let current = self.settings_for(key.as_ref());
        self.set_target_settings(NoiseSettings {
            method: METHODS[self.method_index()].1,
            clip_low: low,
            clip_high: high,
            log_scale: current.log_scale,
        });
        self.redraw_for_target(key, ctx);
    }

    /// `_on_noise_scale_mode_toggled`.
    fn on_log_scale(&mut self, on: bool) {
        let current = self.settings_for(self.target_key().as_ref());
        self.set_target_settings(NoiseSettings { log_scale: on, ..current });
        let s = self.settings_for(self.target_key().as_ref());
        self.low_pos = self.threshold_to_slider(s.clip_low);
        self.high_pos = self.threshold_to_slider(s.clip_high);
    }

    fn redraw_for_target(&mut self, key: Option<PanelKey>, ctx: &egui::Context) {
        if key.is_none() || !self.preview_target(ctx) {
            self.schedule_redraw(true);
            self.render_now(ctx);
        }
    }

    fn method_tag(m: NoiseMethod) -> u8 {
        m as u8
    }

    /// `_noise_base_for_dataset`: each panel's background-subtracted data, kept.
    fn noise_base(&mut self, d: &ComparisonDataset, method: NoiseMethod) -> Arc<Array2> {
        let key = (panel_key(d), Self::method_tag(method));
        if let Some(b) = self.noise_base.get(&key) {
            return b.clone();
        }
        let base =
            Arc::new(apply_noise(&d.data, NoiseSettings { method, ..Default::default() }, d.gap_row_mask.as_deref()));
        self.noise_base.insert(key, base.clone());
        base
    }

    /// `_processed_data_for_dataset`.
    fn processed(&mut self, d: &ComparisonDataset, s: NoiseSettings) -> Arc<Array2> {
        let s = s.normalized();
        match s.method {
            NoiseMethod::None => d.data.clone(),
            NoiseMethod::Clip => {
                let mut out = (*self.noise_base(d, NoiseMethod::Robust)).clone();
                clip_in_place(&mut out, s.clip_low, s.clip_high);
                Arc::new(out)
            }
            m => self.noise_base(d, m),
        }
    }

    // ----------------------------------------------------------- render

    fn panel_options(&self, alignment: Alignment) -> PanelOptions {
        PanelOptions {
            alignment,
            use_db: self.units == 1,
            color_scale: SCALES[self.scale].1,
            manual_limits: self.manual_limits(),
        }
    }

    /// `_manual_limits`.
    fn manual_limits(&self) -> Option<(f64, f64)> {
        if SCALES[self.scale].1 != ColorScale::Manual {
            return None;
        }
        let (lo, hi) = (self.manual_low, self.manual_high);
        if !(lo.is_finite() && hi.is_finite()) || (hi - lo).abs() <= 1e-9 {
            return None;
        }
        Some((lo.min(hi), lo.max(hi)))
    }

    fn cmap_name(&self) -> String {
        self.cmap_items.get(self.cmap).cloned().unwrap_or_else(|| "Custom".into())
    }

    fn invalidate_panels(&mut self) {
        self.panels.clear();
    }

    /// `schedule_redraw`: now, or after the 35 ms timer.
    fn schedule_redraw(&mut self, immediate: bool) {
        self.invalidate_panels();
        self.redraw_due = Some(if immediate { Instant::now() } else { Instant::now() + REDRAW_DELAY });
    }

    fn cache_complete(&self) -> bool {
        let keys = self.noise_keys();
        !keys.is_empty() && keys.len() == self.panels.len() && keys.iter().zip(&self.panels).all(|(k, p)| *k == p.key)
    }

    /// `_ensure_visible_cache_for_individual_edit`.
    fn ensure_cache_for_edit(&mut self, key: Option<&PanelKey>) {
        if key.is_none() || self.display.is_empty() || self.cache_complete() {
            return;
        }
        self.redraw_due = None;
        self.render_pending_now();
    }

    fn render_pending_now(&mut self) {
        // The canvases are filled on the next frame (`render_now` needs
        // nothing from egui but the time).
        self.render_panels();
    }

    fn render_now(&mut self, _ctx: &egui::Context) {
        self.redraw_due = None;
        self.render_panels();
    }

    /// `_render_now` with the hardware view (`_render_hardware`).
    fn render_panels(&mut self) {
        self.redraw_due = None;
        if self.display.is_empty() {
            self.clear_measurements(true);
            self.panels.clear();
            self.canvases.clear();
            self.status = EMPTY_TEXT.into();
            return;
        }
        let mut requested = self.alignment();
        if requested == Alignment::Ut && !self.all_have_ut() {
            self.set_alignment(Alignment::Seconds);
            requested = Alignment::Seconds;
        }
        let display = self.display.clone();
        let settings = self.effective_settings();
        let processed: Vec<Arc<Array2>> = display.iter().zip(&settings).map(|(d, s)| self.processed(d, *s)).collect();
        let cold: Vec<f64> = settings.iter().map(|s| s.clip_low).collect();
        let (payloads, effective, warnings) =
            panel_payloads(&display, &processed, &cold, &self.panel_options(requested));
        let mut all_warnings = warnings;
        for p in &payloads {
            all_warnings.extend(p.dataset.warnings.iter().cloned());
            if let Some(r) = self.display_range {
                let [x0, x1, y0, y1] = p.mpl_extent;
                let xo = x0.max(x1).min(r.time_stop_s) - x0.min(x1).max(r.time_start_s);
                let yo = y0.max(y1).min(r.freq_max_mhz) - y0.min(y1).max(r.freq_min_mhz);
                if xo <= 0.0 || yo <= 0.0 {
                    all_warnings.push(format!("{}: no data inside the locked display range.", p.dataset.label));
                }
            }
        }
        let mut seen = BTreeSet::new();
        all_warnings.retain(|w| seen.insert(w.clone()));
        self.effective_alignment = effective;
        self.panels = display.iter().zip(payloads).map(|(d, payload)| Panel { key: panel_key(d), payload }).collect();
        while self.canvases.len() < self.panels.len() {
            let mut c = PlotCanvas::new().with_id(Id::new(("cmp_panel", self.canvases.len())));
            c.set_dark(self.dark);
            self.canvases.push(c);
        }
        self.canvases.truncate(self.panels.len());
        for i in 0..self.panels.len() {
            self.update_canvas(i);
        }
        self.render_measurement_overlays();
        self.last_warnings = all_warnings;

        let mut parts =
            vec![format!("{} rendered panel(s) from {} selected file(s).", self.display.len(), self.datasets.len())];
        if let Some(mode) = &self.combined_mode {
            parts.push(format!("Combined {} view.", if mode == "time_frequency" { "time + frequency" } else { mode }));
        }
        parts.push("Hardware-accelerated.".into());
        if self.effective_settings().iter().any(|s| s.method != NoiseMethod::None) {
            parts.push("Noise reduction active.".into());
        }
        if self.display_range.is_some() {
            parts.push("Locked display range.".into());
        }
        parts.push(if effective == Alignment::Ut { "UT aligned." } else { "Seconds aligned." }.into());
        if !self.last_warnings.is_empty() {
            parts.push(format!("{} warning(s).", self.last_warnings.len()));
        }
        self.status = parts.join(" ");
    }

    fn view(&self) -> Option<View> {
        self.display_range
            .map(|r| View { xlim: (r.time_start_s, r.time_stop_s), ylim: (r.freq_min_mhz, r.freq_max_mhz) })
    }

    /// `widget.update_image(...)` and `set_time_mode` for panel `i`.
    fn update_canvas(&mut self, i: usize) {
        let view = self.view();
        let ut = self.effective_alignment == Alignment::Ut;
        let unit = if self.units == 1 { "Intensity [dB]" } else { "Intensity [Digits]" };
        let cmap = comparison_colormap(&self.cmap_name());
        let (Some(panel), Some(canvas)) = (self.panels.get(i), self.canvases.get_mut(i)) else { return };
        let p = &panel.payload;
        canvas.set_dark(self.dark);
        canvas.update_image(ImageUpdate {
            data: p.display.clone(),
            extent: p.pg_extent,
            cmap,
            gap_rows: p.dataset.gap_row_mask.as_deref(),
            levels: Some(p.levels),
            title: p.dataset.label.clone(),
            x_label: if ut { "Time [UT]" } else { "Time [s]" }.into(),
            y_label: "Frequency [MHz]".into(),
            colorbar_label: unit.into(),
            view,
            freqs: &[],
            log_freq: false,
        });
        canvas.set_time_mode(ut, Some(0.0));
    }

    /// `_render_noise_target_preview`: only the edited panel, with its own data.
    fn preview_target(&mut self, ctx: &egui::Context) -> bool {
        let Some(key) = self.target_key() else { return false };
        if self.display.is_empty() {
            return false;
        }
        if !self.cache_complete() {
            self.render_now(ctx);
            if !self.cache_complete() {
                return false;
            }
        }
        let Some(index) = self.panels.iter().position(|p| p.key == key) else { return false };
        let Some(dataset) = self.display.iter().find(|d| panel_key(d) == key).cloned() else { return false };
        let s = self.settings_for(Some(&key));
        let processed = self.processed(&dataset, s);
        let (payloads, _, _) = panel_payloads(
            std::slice::from_ref(&dataset),
            &[processed],
            &[s.clip_low],
            &self.panel_options(self.effective_alignment),
        );
        let Some(payload) = payloads.into_iter().next() else { return false };
        self.panels[index].payload = payload;
        self.update_canvas(index);
        if self.effective_settings().iter().any(|s| s.method != NoiseMethod::None)
            && !self.status.contains("Noise reduction active.")
        {
            self.status = format!("{} Noise reduction active.", self.status).trim().to_owned();
        }
        true
    }

    // ------------------------------------------------------ measurement

    fn panel_label(&self, key: &PanelKey) -> String {
        self.display.iter().find(|d| panel_key(d) == *key).map_or_else(|| "Panel".to_owned(), |d| d.label.clone())
    }

    /// `_format_measurement_time`.
    fn format_time(&self, v: f64) -> String {
        if self.effective_alignment == Alignment::Ut {
            let total = v.round_ties_even() as i64;
            let (h, m, s) = (total.div_euclid(3600).rem_euclid(24), total.rem_euclid(3600) / 60, total.rem_euclid(60));
            return format!("{h:02}:{m:02}:{s:02} UT");
        }
        format!("{v:.3} s")
    }

    fn overlay_label(m: &Measurement) -> String {
        format!("dt={:.3} s\ndf={:.3} MHz\n{:.6} MHz/s", m.duration_s, m.frequency_delta_mhz, m.slope_mhz_s)
    }

    fn points(m: &Measurement) -> [(f64, f64); 2] {
        [(m.point1.time_s, m.point1.frequency_mhz), (m.point2.time_s, m.point2.frequency_mhz)]
    }

    /// `_render_measurement_overlays`.
    fn render_measurement_overlays(&mut self) {
        let valid: Vec<PanelKey> = self.noise_keys();
        self.measurements.retain(|k, _| valid.contains(k));
        if self.capture_key.as_ref().is_some_and(|k| !valid.contains(k)) {
            self.capture_key = None;
            self.capture_points.clear();
        }
        for (canvas, panel) in self.canvases.iter_mut().zip(&self.panels) {
            if let Some(m) = self.measurements.get(&panel.key) {
                canvas.show_measurement(&Self::points(m), &Self::overlay_label(m));
            } else if self.capture_key.as_ref() == Some(&panel.key) && !self.capture_points.is_empty() {
                canvas.show_measurement(&self.capture_points, "");
            } else {
                canvas.clear_measurement();
            }
        }
    }

    /// `_set_measurement_result`.
    pub fn set_measurement(&mut self, panel: usize, a: (f64, f64), b: (f64, f64)) {
        let Some(key) = self.panels.get(panel).map(|p| p.key.clone()) else { return };
        match two_point(a, b) {
            Ok(m) => self.measurement_result(key, m),
            Err(e) => {
                self.readout = Readout::pending(&e);
                self.status = e;
            }
        }
    }

    fn measurement_result(&mut self, key: PanelKey, m: Measurement) {
        self.measurements.insert(key.clone(), m);
        self.capture_key = None;
        self.capture_points.clear();
        let label = self.panel_label(&key);
        let title = if label.trim().is_empty() { String::new() } else { format!("{}\n", label.trim()) };
        self.readout = Readout {
            text: format!(
                "{title}P1: {}, {:.3} MHz\nP2: {}, {:.3} MHz\nDuration: {:.3} s\nFrequency drift: {:.3} MHz\nSlope: {:.6} MHz/s",
                self.format_time(m.point1.time_s),
                m.point1.frequency_mhz,
                self.format_time(m.point2.time_s),
                m.point2.frequency_mhz,
                m.duration_s,
                m.frequency_delta_mhz,
                m.slope_mhz_s
            ),
            copy: true,
            clear: true,
        };
        self.render_measurement_overlays();
        self.status = format!(
            "Measurement for {label}: dt={:.3} s, df={:.3} MHz, slope={:.6} MHz/s.",
            m.duration_s, m.frequency_delta_mhz, m.slope_mhz_s
        );
    }

    /// `clear_ruler_measurements`.
    fn clear_measurements(&mut self, reset_readout: bool) {
        self.measurements.clear();
        self.capture_key = None;
        self.capture_points.clear();
        for c in &mut self.canvases {
            c.clear_measurement();
            c.stop_interaction_capture();
        }
        if reset_readout {
            self.readout = Readout::empty();
            self.status = "Ruler measurement cleared.".into();
        }
    }

    /// `start_ruler_measurement`.
    fn start_ruler(&mut self, ctx: &egui::Context) {
        if self.display.is_empty() {
            return self.notice(NoticeKind::Information, "Ruler Measurement", "Add FITS files first.");
        }
        if !self.cache_complete() {
            self.render_now(ctx);
        }
        self.capture_key = None;
        self.capture_points.clear();
        self.readout = Readout::pending("Click the first point in a comparison panel.");
        for c in &mut self.canvases {
            c.begin_measurement_capture();
        }
        self.status = "Click two points in one panel to measure duration, frequency drift, and slope.".into();
    }

    /// `_on_hardware_measurement_finished`.
    fn measurement_finished(&mut self, index: usize, points: Vec<(f64, f64)>) {
        let Some(key) = self.panels.get(index).map(|p| p.key.clone()) else { return };
        if points.len() < 2 {
            return;
        }
        for (i, c) in self.canvases.iter_mut().enumerate() {
            if i != index {
                c.stop_interaction_capture();
            }
        }
        match two_point(points[0], points[1]) {
            Ok(m) => self.measurement_result(key, m),
            Err(e) => {
                self.capture_key = None;
                self.capture_points.clear();
                if let Some(c) = self.canvases.get_mut(index) {
                    c.clear_measurement();
                }
                self.readout = Readout::pending(&e);
                self.status = e;
            }
        }
    }

    // ------------------------------------------------- visual settings

    /// `_set_colormap`: both colormap boxes, adding a name they lack.
    fn set_colormap(&mut self, name: &str) {
        let name = if name.trim().is_empty() { "Custom" } else { name.trim() };
        if !self.cmap_items.iter().any(|c| c == name) {
            self.cmap_items.push(name.to_owned());
        }
        self.cmap = self.cmap_items.iter().position(|c| c == name).unwrap_or(0);
    }

    /// `_apply_view_config_payload`'s visual part.
    fn apply_visual(&mut self, v: &VisualConfig) {
        self.units = usize::from(v.use_db);
        self.set_colormap(if v.cmap.trim().is_empty() { "Custom" } else { &v.cmap });
        self.manual_low = v.noise_clip_low;
        self.manual_high = v.noise_clip_high;
        let (lo, hi) = (self.manual_low, self.manual_high);
        if (hi - lo).abs() > 1e-9 && (hi.abs() > 1e-9 || lo.abs() > 1e-9) {
            self.scale = 2;
        }
    }

    /// `_apply_view_config_payload`.
    fn apply_view_config(&mut self, cfg: &ViewConfig, apply_range: bool, show_status: bool) {
        self.apply_visual(&cfg.visual);
        if apply_range && let Some(r) = cfg.range {
            if self.alignment() == Alignment::Seconds {
                self.display_range = Some(r);
            } else if show_status {
                self.status =
                    "View config visual settings loaded. Set UT bounds from the comparison range dialog.".into();
            }
        }
        self.schedule_redraw(false);
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
                self.apply_view_config(&cfg, true, true);
                self.status = format!("Loaded view config: {}", file_name(&path.to_string_lossy()));
            }
            Err(e) => self.notice(NoticeKind::Warning, "Load View Config", format!("Could not read view config:\n{e}")),
        }
    }

    // ---------------------------------------------------- display range

    /// `open_display_range_dialog`.
    fn open_range_dialog(&mut self) {
        let Some((xlim, ylim, effective, warnings)) = shared_extent(&self.display, self.alignment()) else {
            return self.notice(
                NoticeKind::Information,
                "Set Comparison Range",
                "Add at least one FITS file before setting the range.",
            );
        };
        if !warnings.is_empty() && effective == Alignment::Seconds && self.alignment() == Alignment::Ut {
            self.set_alignment(Alignment::Seconds);
        }
        let initial = match self.display_range {
            Some(r) => [r.time_start_s, r.time_stop_s, r.freq_min_mhz, r.freq_max_mhz],
            None => [xlim.0, xlim.1, ylim.0, ylim.1],
        };
        let ut = effective == Alignment::Ut;
        let req =
            DisplayRangeRequest { full: View { xlim, ylim }, initial, ut_start_sec: ut.then_some(0.0), ut_mode: ut };
        self.range_dialog = Some((DisplayRangeDialog::new(req), xlim));
    }

    fn finish_range_dialog(&mut self, result: DisplayRangeResult, full_x: (f64, f64)) {
        let (time, freq) = match result {
            DisplayRangeResult::Ut { seconds_of_day: (a, b), freq } => {
                match seconds_of_day_range_to_unwrapped(a as f64, b as f64, full_x) {
                    Some(t) => (t, freq),
                    None => {
                        return self.notice(
                            NoticeKind::Warning,
                            "Set Comparison Range",
                            "Enter a valid UT start and stop time for this comparison.",
                        );
                    }
                }
            }
            DisplayRangeResult::Seconds { time, freq } => (time, freq),
        };
        match DisplayRange::normalized(time.0, time.1, freq.0, freq.1) {
            Ok(r) => {
                self.display_range = Some(r);
                self.schedule_redraw(false);
            }
            Err(e) => self.notice(NoticeKind::Warning, "Set Comparison Range", e),
        }
    }

    // ------------------------------------------------------------ export

    /// `_grid_export_payloads`.
    fn grid_payloads(&mut self, ctx: &egui::Context) -> Vec<PanelPayload> {
        if self.redraw_due.is_some() || !self.cache_complete() {
            self.render_now(ctx);
        }
        self.panels.iter().map(|p| p.payload.clone()).collect()
    }

    /// `export_comparison`.
    fn export(&mut self) {
        if self.datasets.len() < 2 {
            return self.notice(
                NoticeKind::Information,
                "Export Comparison",
                "Add at least two FITS files before exporting.",
            );
        }
        let mut initial = self.export_options.clone();
        if initial.title == DEFAULT_TITLE {
            let labels: Vec<String> = self.display.iter().map(|d| d.label.clone()).collect();
            initial.title = default_grid_title(&labels);
        }
        self.export_dialog = Some(ExportDialog::new(&initial));
    }

    fn finish_export_options(&mut self, ctx: &egui::Context, options: ExportOptions) {
        self.export_options = options.clone();
        let Some(mut path) = rfd::FileDialog::new()
            .set_title("Export Comparison")
            .set_file_name("multi_station_comparison")
            .add_filter("PNG (*.png)", &["png"])
            .add_filter("PDF (*.pdf)", &["pdf"])
            .add_filter("EPS (*.eps)", &["eps"])
            .add_filter("SVG (*.svg)", &["svg"])
            .add_filter("TIFF (*.tiff)", &["tiff"])
            .save_file()
        else {
            return;
        };
        let ext = match path.extension() {
            Some(e) => e.to_string_lossy().to_lowercase(),
            None => {
                path.set_extension("png");
                "png".into()
            }
        };
        if options.grid {
            let result = self.export_grid(ctx, &path, &options);
            self.export_finished(&path, result);
        } else {
            self.start_visible_capture(path, ext);
        }
    }

    /// `_export_grid_plot`.
    pub fn export_grid(&mut self, ctx: &egui::Context, path: &Path, options: &ExportOptions) -> Result<(), String> {
        let payloads = self.grid_payloads(ctx);
        if payloads.is_empty() {
            return Err("Could not prepare comparison panels for grid export.".into());
        }
        let grid = GridOptions {
            alignment: self.effective_alignment,
            time_range: self.display_range.map(|r| (r.time_start_s, r.time_stop_s)),
            use_db: self.units == 1,
            cmap: self.cmap_name(),
            per_station: SCALES[self.scale].1 == ColorScale::PerStation,
            columns: options.columns,
            title: options.title.clone(),
        };
        let fig = comparison_grid_figure(&payloads, &grid);
        if let Some(dir) = path.parent()
            && !dir.as_os_str().is_empty()
        {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        crate::figure_export::save_figure(ctx, &fig, path, options.dpi as f64).map_err(|e| e.to_string())
    }

    fn export_finished(&mut self, path: &Path, result: Result<(), String>) {
        match result {
            Ok(()) => self.notice(
                NoticeKind::Information,
                "Export Comparison",
                format!("Comparison image saved:\n{}", path.display()),
            ),
            Err(e) => self.notice(
                NoticeKind::Critical,
                "Export Comparison Failed",
                format!("Could not export comparison image:\n{e}"),
            ),
        }
    }

    /// `_export_visible_plot`: each panel as the screen shows it, stacked.
    pub fn start_visible_capture(&mut self, path: PathBuf, ext: String) {
        if self.panels.is_empty() {
            return self.export_finished(&path, Err("Could not capture the visible comparison plot.".into()));
        }
        self.visible_capture = Some(VisibleCapture {
            path,
            ext,
            next: 0,
            scrolled: false,
            asked: false,
            images: Vec::new(),
            scroll_before: self.plot_scroll,
        });
    }

    pub fn visible_capture_running(&self) -> bool {
        self.visible_capture.is_some()
    }

    fn notice(&mut self, kind: NoticeKind, title: &str, text: impl Into<String>) {
        self.notices.push(Notice { kind, title: title.into(), text: text.into() });
    }

    /// Message boxes not yet shown, for the main window when this one closes.
    pub fn take_notices(&mut self) -> Vec<Notice> {
        std::mem::take(&mut self.notices)
    }

    // ------------------------------------------------- tests and captures

    pub fn clear(&mut self) {
        self.clear_files();
    }

    pub fn status(&self) -> &str {
        &self.status
    }

    pub fn readout_text(&self) -> &str {
        &self.readout.text
    }

    pub fn panel_labels(&self) -> Vec<String> {
        self.panels.iter().map(|p| p.payload.dataset.label.clone()).collect()
    }

    pub fn panel_extent(&self, i: usize) -> Option<[f64; 4]> {
        self.panels.get(i).map(|p| p.payload.pg_extent)
    }

    /// Renders now if a redraw is due (the timer's work, for tests).
    pub fn flush(&mut self, ctx: &egui::Context) {
        if self.redraw_due.is_some() {
            self.render_now(ctx);
        }
    }

    /// The Method box of the noise target (`noise_method_combo.setCurrentIndex`).
    pub fn set_noise_method(&mut self, ctx: &egui::Context, i: usize) {
        self.on_method(i.min(METHODS.len() - 1), ctx);
    }

    /// `noise_low_slider.setValue` / `noise_high_slider.setValue`.
    pub fn set_noise_slider(&mut self, ctx: &egui::Context, low: bool, pos: i32) {
        if low {
            self.low_pos = pos.clamp(0, SLIDER_STEPS);
        } else {
            self.high_pos = pos.clamp(0, SLIDER_STEPS);
        }
        self.on_slider(low, ctx);
    }

    pub fn set_units(&mut self, ctx: &egui::Context, db: bool) {
        self.units = usize::from(db);
        self.schedule_redraw(true);
        self.render_now(ctx);
    }

    pub fn set_colormap_name(&mut self, ctx: &egui::Context, name: &str) {
        self.set_colormap(name);
        self.schedule_redraw(true);
        self.render_now(ctx);
    }

    pub fn set_color_scale(&mut self, i: usize) {
        self.scale = i.min(SCALES.len() - 1);
        self.schedule_redraw(false);
    }

    pub fn clear_ruler(&mut self) {
        self.clear_measurements(true);
    }
}

fn file_name(path: &str) -> String {
    Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.to_owned())
}

// =============================================================== drawing

/// The left panel's contents below the files group, at its own width.
struct Controls {
    layout: Layout,
}

impl ComparisonWindow {
    fn files_layout(ui: &Ui) -> Layout {
        let buttons = BoxLayout::h([0; 4], 6)
            .widget(qt::button_spec(ui, "add", "Add Files...", false).min_height(34))
            .widget(qt::button_spec(ui, "remove", "Remove", false).min_height(34))
            .widget(qt::button_spec(ui, "clear", "Clear", false).min_height(34));
        let list = qt::view_spec("list").min_height(96).max_height(150);
        let list = WidgetSpec { vpolicy: Policy::Fixed, ..list };
        BoxLayout::v([9; 4], 8).widget(list).add(buttons.into_item()).into()
    }

    fn left_layout(ui: &Ui) -> Layout {
        BoxLayout::v([9; 4], 10)
            .add(group("files_group", Self::files_layout(ui)))
            .add_stretched(
                Item::Widget(
                    WidgetSpec::new("controls", Size::new(508, 384), Policy::Expanding, Policy::Expanding)
                        .min_hint(Size::new(54, 54)),
                ),
                1,
            )
            .into()
    }

    fn controls_layout(&self, ui: &Ui) -> Controls {
        let label = |id: &'static str, text: &str| qt::label_spec(ui, id, text);
        let combo = |id: &'static str, items: &[&str]| qt::combo_spec(ui, id, items);
        let view = BoxLayout::v([9; 4], 6)
            .widget(label("align_label", "Time alignment"))
            .widget(combo("align", &ALIGNMENTS.map(|a| a.0)));
        let manual = BoxLayout::h([0; 4], 6)
            .widget(label("low_label", "Low"))
            .widget(qt::spin_spec(ui, "manual_low", "-1000000000.000"))
            .widget(label("high_label", "High"))
            .widget(qt::spin_spec(ui, "manual_high", "-1000000000.000"));
        let cmaps: Vec<&str> = self.cmap_items.iter().map(String::as_str).collect();
        let appearance = BoxLayout::v([9; 4], 6)
            .widget(label("units_label", "Units"))
            .widget(combo("units", &UNITS))
            .widget(label("cmap_label", "Colormap"))
            .widget(combo("cmap", &cmaps))
            .widget(label("scale_label", "Color scale"))
            .widget(combo("scale", &SCALES.map(|s| s.0)))
            .add(manual.into_item());
        let targets = self.target_items();
        let target_refs: Vec<&str> = targets.iter().map(String::as_str).collect();
        let use_db = self.units == 1;
        let value_col = |a: &'static str, b: &'static str, text: &str| {
            BoxLayout::v([0; 4], 0).widget(label(a, text)).widget(label(b, "0.00 dB").hidden(!use_db)).into_item()
        };
        let s = self.settings_for(self.target_key().as_ref());
        let low_row = BoxLayout::h([0; 4], 6).widget(label("low_thr_label", "Lower threshold")).add(value_col(
            "low_value",
            "low_sub",
            &format!("{:.2} Digits", s.clip_low),
        ));
        let high_row = BoxLayout::h([0; 4], 6).widget(label("high_thr_label", "Upper threshold")).add(value_col(
            "high_value",
            "high_sub",
            &format!("{:.2} Digits", s.clip_high),
        ));
        let slider = |id: &'static str| {
            WidgetSpec::new(id, Size::new(84, 15), Policy::Expanding, Policy::Fixed).min_hint(Size::new(15, 15))
        };
        let check_w =
            widgets::text_width(ui, "Logarithmic Threshold Scale", qt::FONT_PX, Weight::Regular).ceil() as i32 + 24;
        let clip = BoxLayout::v([0; 4], 6)
            .add(low_row.into_item())
            .widget(slider("low_slider"))
            .add(high_row.into_item())
            .widget(slider("high_slider"))
            .widget(WidgetSpec::new("log_scale", Size::new(check_w, 17), Policy::Minimum, Policy::Fixed));
        let clip_panel = Item::Container(Container {
            spec: WidgetSpec::new("clip_panel", Size::new(0, 0), Policy::Preferred, Policy::Preferred)
                .hidden(s.method != NoiseMethod::Clip),
            margins: [0; 4],
            layout: Box::new(clip.into()),
        });
        let noise = BoxLayout::v([9; 4], 6)
            .widget(label("target_label", "Target"))
            .widget(combo("target", &target_refs))
            .widget(label("method_label", "Method"))
            .widget(combo("method", &METHODS.map(|m| m.0)))
            .widget(label("ncmap_label", "Colormap"))
            .widget(combo("ncmap", &cmaps))
            .add(clip_panel);
        let readout =
            BoxLayout::v([10; 4], 6).widget(qt::wrap_label_spec(ui, "readout_text", &self.readout.text, false)).add(
                BoxLayout::h([0; 4], 6)
                    .widget(qt::button_spec(ui, "copy", "Copy", false))
                    .widget(qt::button_spec(ui, "readout_clear", "Clear", false))
                    .into_item(),
            );
        let measurement = BoxLayout::v([9; 4], 6)
            .add(
                BoxLayout::h([0; 4], 6)
                    .widget(qt::button_spec(ui, "ruler", "Start Ruler", false))
                    .widget(qt::button_spec(ui, "ruler_clear", "Clear", false))
                    .into_item(),
            )
            .add(group("readout_group", readout.into()));
        let actions = BoxLayout::v([9; 4], 6)
            .add(
                BoxLayout::h([0; 4], 6)
                    .widget(qt::button_spec(ui, "set_range", "Set Range...", false))
                    .widget(qt::button_spec(ui, "reset_range", "Reset Range", false))
                    .into_item(),
            )
            .widget(qt::button_spec(ui, "load_config", "Load View Config...", false))
            .widget(qt::button_spec(ui, "export", "Export...", false))
            .widget(qt::wrap_label_spec(ui, "status", &self.status, false));
        let layout = BoxLayout::v([0; 4], 10)
            .add(group("view_group", view.into()))
            .add(group("appearance_group", appearance.into()))
            .add(group("noise_group", noise.into()))
            .add(group("measure_group", measurement.into()))
            .add(group("actions_group", actions.into()))
            .stretch(1)
            .into();
        Controls { layout }
    }

    fn target_items(&self) -> Vec<String> {
        std::iter::once("All panels".to_owned()).chain(self.display.iter().map(|d| d.label.clone())).collect()
    }

    /// The whole window's contents.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<ComparisonAction> {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Option<ComparisonAction> {
        let ctx = ui.ctx().clone();
        if self.redraw_due.is_some_and(|t| Instant::now() >= t) {
            self.render_now(&ctx);
        }
        if let Some(t) = self.redraw_due {
            ctx.request_repaint_after(t.saturating_duration_since(Instant::now()));
        }
        let modal = self.export_dialog.is_some() || self.range_dialog.is_some() || !self.notices.is_empty();
        let full = ui.max_rect();
        // `QVBoxLayout(self)`: 11 px margins around the splitter.
        let inner = Rect::from_min_max(full.min + vec2(11.0, 11.0), full.max - vec2(11.0, 11.0));
        let handle_w = 4.0;
        let left_w = self.left_w.clamp(360.0, (inner.width() - handle_w - 56.0).max(360.0)).round();
        let left = Rect::from_min_size(inner.min, vec2(left_w, inner.height()));
        let handle = Rect::from_min_max(pos2(left.right(), inner.top()), pos2(left.right() + handle_w, inner.bottom()));
        let right = Rect::from_min_max(pos2(handle.right(), inner.top()), inner.max);
        let resp = splitter_handle_vertical(ui, handle, Id::new("cmp_splitter"));
        if resp.dragged() {
            self.left_w = (self.left_w + resp.drag_delta().x).max(360.0);
        }
        self.left_panel(ui, left, p, &ctx);
        self.plot_area(ui, right, p, &ctx);
        if modal {
            crate::downloads::block_input(ui, Id::new("cmp_block"));
        }
        self.dialogs(&ctx, p);
        self.poll_visible_capture(ui, right, &ctx);
        None
    }

    fn dialogs(&mut self, ctx: &egui::Context, p: &Palette) {
        if let Some(d) = &mut self.export_dialog
            && let Some(result) = d.show(ctx, p)
        {
            self.export_dialog = None;
            if let Some(options) = result {
                self.finish_export_options(ctx, options);
            }
        }
        if let Some((d, full_x)) = &mut self.range_dialog
            && let Some(result) = d.show(ctx, p)
        {
            let full_x = *full_x;
            self.range_dialog = None;
            if let Some(r) = result {
                self.finish_range_dialog(r, full_x);
            }
        }
        if let Some(n) = self.notices.first()
            && crate::ui::dialogs::message_box(ctx, n, p)
        {
            self.notices.remove(0);
        }
    }

    fn left_panel(&mut self, ui: &mut Ui, left: Rect, p: &Palette, ctx: &egui::Context) {
        let layout = Self::left_layout(ui);
        let mut g = Geometry::default();
        layout.set_geometry(IRect::new(0, 0, left.width().round() as i32, left.height().round() as i32), &mut g);
        let r = |id: &str| g.rect(id).to_egui(left.min);
        qt::group_box(ui, r("files_group"), "Stations / Files", p);
        self.file_list(ui, r("list"), p);
        let has = !self.datasets.is_empty();
        if qt::button(ui, r("add"), Id::new("cmp_add"), "Add Files...", false, true, p).clicked()
            && let Some(paths) = rfd::FileDialog::new()
                .set_title("Add FITS Files")
                .add_filter(
                    "FITS files (*.fit *.fits *.fit.gz *.fits.gz *.FIT *.FITS *.FIT.gz *.FITS.gz)",
                    &["fit", "fits", "gz", "FIT", "FITS"],
                )
                .pick_files()
        {
            let paths: Vec<String> = paths.iter().map(|p| p.to_string_lossy().into_owned()).collect();
            self.add_files(&paths);
        }
        if qt::button(ui, r("remove"), Id::new("cmp_remove"), "Remove", false, has, p).clicked() {
            self.remove_selected();
        }
        if qt::button(ui, r("clear"), Id::new("cmp_clear"), "Clear", false, has, p).clicked() {
            self.clear_files();
        }
        self.controls(ui, r("controls"), p, ctx);
    }

    /// `QListWidget` (ExtendedSelection): one row per file, scrolled sideways
    /// when the names are wider than the view.
    fn file_list(&mut self, ui: &mut Ui, rect: Rect, p: &Palette) {
        let id = Id::new("cmp_list");
        if !self.focus_given {
            ui.memory_mut(|m| m.request_focus(id));
            self.focus_given = true;
        }
        let focused = ui.memory(|m| m.has_focus(id));
        qt::qt_box(ui, rect, 10.0, p.input_bg, if focused { p.accent } else { p.border });
        let rows: Vec<String> =
            self.datasets.iter().map(|d| format!("{}  -  {}", d.label, file_name(&d.path))).collect();
        let mut vp = Rect::from_min_max(rect.min + vec2(9.0, 7.0), rect.max - vec2(9.0, 7.0));
        let content_w =
            rows.iter().map(|t| widgets::text_width(ui, t, qt::FONT_PX, Weight::Regular)).fold(0.0, f32::max).ceil()
                + 2.0 * ITEM_MARGIN;
        let mut hbar = content_w > vp.width();
        if hbar {
            vp.max.y -= BAR;
        }
        let vbar = rows.len() as f32 * ROW_H > vp.height();
        if vbar {
            vp.max.x -= BAR;
            if !hbar && content_w > vp.width() {
                hbar = true;
                vp.max.y -= BAR;
            }
        }
        let per_page = (vp.height() / ROW_H).floor().max(1.0) as usize;
        self.list_first = self.list_first.min(rows.len().saturating_sub(per_page));
        let max_x = (content_w - vp.width()).max(0.0);
        self.list_x = self.list_x.clamp(0.0, max_x);
        let area = ui.interact(vp, id, Sense::click());
        if area.clicked() {
            area.request_focus();
            if let Some(pos) = area.interact_pointer_pos() {
                let row = self.list_first + ((pos.y - vp.top()) / ROW_H).floor() as usize;
                let mods = ui.input(|i| i.modifiers);
                if row < rows.len() {
                    if mods.shift
                        && let Some(a) = self.anchor
                    {
                        self.selected = (a.min(row)..=a.max(row)).collect();
                    } else if mods.command {
                        if !self.selected.remove(&row) {
                            self.selected.insert(row);
                        }
                        self.anchor = Some(row);
                    } else {
                        self.selected = BTreeSet::from([row]);
                        self.anchor = Some(row);
                    }
                    self.on_selection_changed();
                } else if !mods.command && !mods.shift {
                    self.selected.clear();
                }
            }
        }
        let painter = ui.painter_at(vp);
        for (k, text) in rows.iter().enumerate().skip(self.list_first).take(per_page + 1) {
            let y = vp.top() + (k - self.list_first) as f32 * ROW_H;
            let row = Rect::from_min_size(pos2(vp.left(), y), vec2(vp.width(), ROW_H));
            let selected = self.selected.contains(&k);
            if selected {
                painter.rect_filled(row, CornerRadius::ZERO, p.accent);
            }
            let color = if selected { Color32::WHITE } else { p.text };
            widgets::text_left_clipped(
                ui,
                vp,
                vp.left() + ITEM_MARGIN - self.list_x,
                row.center().y,
                text,
                qt::FONT_PX,
                Weight::Regular,
                color,
            );
            if let Some(tip) = self.datasets.get(k).filter(|d| !d.warnings.is_empty()).map(|d| d.warnings.join("\n"))
                && ui.rect_contains_pointer(row)
            {
                egui::Tooltip::always_open(
                    ui.ctx().clone(),
                    ui.layer_id(),
                    id.with(("tip", k)),
                    egui::PopupAnchor::Pointer,
                )
                .gap(12.0)
                .show(|ui| {
                    ui.label(tip);
                });
            }
        }
        if hbar {
            let bar = Rect::from_min_max(pos2(vp.left(), vp.bottom()), pos2(vp.right(), vp.bottom() + BAR));
            let mut x = self.list_x;
            qt::scroll_bar(ui, bar, id.with("hbar"), false, &mut x, content_w, vp.width(), p);
            self.list_x = x;
        }
        if vbar {
            let bar = Rect::from_min_max(pos2(vp.right(), vp.top()), pos2(vp.right() + BAR, vp.bottom()));
            let mut y = self.list_first as f32 * ROW_H;
            qt::scroll_bar(
                ui,
                bar,
                id.with("vbar"),
                true,
                &mut y,
                rows.len() as f32 * ROW_H,
                per_page as f32 * ROW_H,
                p,
            );
            self.list_first = (y / ROW_H).round() as usize;
        }
    }

    /// `_on_file_selection_changed`: the first selected file's panel becomes the noise target.
    fn on_selection_changed(&mut self) {
        let Some(&row) = self.selected.first() else { return };
        let Some(path) = self.datasets.get(row).map(|d| path_key(&d.path)) else { return };
        let keys = self.noise_keys();
        for (i, d) in self.display.iter().enumerate() {
            let mut sources: Vec<String> = if d.sources.is_empty() { vec![d.path.clone()] } else { d.sources.clone() };
            sources.push(d.path.clone());
            if sources.iter().any(|s| path_key(s) == path) {
                if keys.get(i).is_some() && self.noise_target != i + 1 {
                    self.noise_target = i + 1;
                    self.sync_noise_controls();
                }
                return;
            }
        }
    }

    /// The scrolled controls (`controls_scroll`, no frame).
    fn controls(&mut self, ui: &mut Ui, area: Rect, p: &Palette, ctx: &egui::Context) {
        let Controls { layout } = self.controls_layout(ui);
        let s = layout.summary();
        let min_w = s.min.w as f32;
        let mut vp = area;
        let mut content_w = vp.width().max(min_w);
        let mut content_h = layout.height_for_width(content_w.round() as i32) as f32;
        let mut hbar = content_w > vp.width() + 0.5;
        let mut vbar = content_h > vp.height() + 0.5;
        if hbar {
            vp.max.y -= BAR;
            vbar = content_h > vp.height() + 0.5;
        }
        if vbar {
            vp.max.x -= BAR;
            content_w = vp.width().max(min_w);
            content_h = layout.height_for_width(content_w.round() as i32) as f32;
            if !hbar && content_w > vp.width() + 0.5 {
                hbar = true;
                vp.max.y -= BAR;
            }
        }
        content_h = content_h.max(vp.height());
        // The viewport is in the palette's window colour (the full app's
        // QScrollArea keeps it), the scroll bars' column the page's.
        ui.painter().rect_filled(vp, 0.0, p.qt.window);
        if hbar && vbar {
            ui.painter().rect_filled(Rect::from_min_max(pos2(vp.right(), vp.bottom()), area.max), 0.0, p.qt.window);
        }
        let (mut sx, mut sy) = self.controls_scroll;
        qt::wheel_scroll(ui, area, &mut sy, (content_h - vp.height()).max(0.0));
        if vbar {
            let bar = Rect::from_min_max(pos2(vp.right(), vp.top()), pos2(area.right(), vp.bottom()));
            qt::scroll_bar(ui, bar, Id::new("cmp_controls_vbar"), true, &mut sy, content_h, vp.height(), p);
        }
        if hbar {
            let bar = Rect::from_min_max(pos2(vp.left(), vp.bottom()), pos2(vp.right(), area.bottom()));
            qt::scroll_bar(ui, bar, Id::new("cmp_controls_hbar"), false, &mut sx, content_w, vp.width(), p);
        }
        sx = sx.clamp(0.0, (content_w - vp.width()).max(0.0));
        sy = sy.clamp(0.0, (content_h - vp.height()).max(0.0));
        self.controls_scroll = (sx, sy);
        let mut g = Geometry::default();
        layout.set_geometry(IRect::new(0, 0, content_w.round() as i32, content_h.round() as i32), &mut g);
        let origin = pos2((vp.left() - sx).round(), (vp.top() - sy).round());
        let mut child = ui.new_child(UiBuilder::new().max_rect(vp));
        child.set_clip_rect(vp);
        self.control_widgets(&mut child, &g, origin, p, ctx);
    }

    fn control_widgets(&mut self, ui: &mut Ui, g: &Geometry, o: Pos2, p: &Palette, ctx: &egui::Context) {
        let r = |id: &str| g.rect(id).to_egui(o);
        let has = !self.datasets.is_empty();
        let text = p.text;
        for (id, title) in [
            ("view_group", "View"),
            ("appearance_group", "Appearance"),
            ("noise_group", "Noise Reduction"),
            ("measure_group", "Measurement"),
            ("actions_group", "Actions"),
        ] {
            qt::group_box(ui, r(id), title, p);
        }
        for (id, t) in [
            ("align_label", "Time alignment"),
            ("units_label", "Units"),
            ("cmap_label", "Colormap"),
            ("scale_label", "Color scale"),
            ("low_label", "Low"),
            ("high_label", "High"),
            ("target_label", "Target"),
            ("method_label", "Method"),
            ("ncmap_label", "Colormap"),
        ] {
            qt::label(ui, r(id), t, text);
        }
        let items = |v: &[&str]| v.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        if let Some(i) = qt::combo(
            ui,
            r("align"),
            Id::new("cmp_align"),
            &items(&ALIGNMENTS.map(|a| a.0)),
            Some(self.alignment),
            "",
            true,
            p,
        ) && i != self.alignment
        {
            // `_on_alignment_changed`.
            self.alignment = i;
            self.user_changed_alignment = true;
            if self.alignment() == Alignment::Ut && !self.all_have_ut() {
                self.set_alignment(Alignment::Seconds);
                self.status = "UT alignment requires TIME-OBS in every selected file. Switched to seconds.".into();
            }
            self.display_range = None;
            self.clear_measurements(true);
            self.schedule_redraw(false);
        }
        if let Some(i) = qt::combo(ui, r("units"), Id::new("cmp_units"), &items(&UNITS), Some(self.units), "", true, p)
            && i != self.units
        {
            self.units = i;
            self.schedule_redraw(true);
            self.render_now(ctx);
        }
        let cmaps = self.cmap_items.clone();
        if let Some(i) = qt::combo(ui, r("cmap"), Id::new("cmp_cmap"), &cmaps, Some(self.cmap), "", true, p)
            && i != self.cmap
        {
            self.cmap = i;
            self.schedule_redraw(true);
            self.render_now(ctx);
        }
        if let Some(i) =
            qt::combo(ui, r("scale"), Id::new("cmp_scale"), &items(&SCALES.map(|s| s.0)), Some(self.scale), "", true, p)
            && i != self.scale
        {
            self.scale = i;
            self.schedule_redraw(false);
        }
        let manual = SCALES[self.scale].1 == ColorScale::Manual;
        let range = (-1_000_000_000.0, 1_000_000_000.0);
        if qt::double_spin_box(
            ui,
            r("manual_low"),
            Id::new("cmp_manual_low"),
            &mut self.manual_low,
            range,
            1.0,
            3,
            "",
            false,
            manual,
            p,
        ) {
            self.schedule_redraw(false);
        }
        if qt::double_spin_box(
            ui,
            r("manual_high"),
            Id::new("cmp_manual_high"),
            &mut self.manual_high,
            range,
            1.0,
            3,
            "",
            false,
            manual,
            p,
        ) {
            self.schedule_redraw(false);
        }

        // Noise reduction.
        let targets = self.target_items();
        if let Some(i) =
            qt::combo(ui, r("target"), Id::new("cmp_target"), &targets, Some(self.noise_target), "", has, p)
            && i != self.noise_target
        {
            self.noise_target = i;
            self.sync_noise_controls();
        }
        let method = self.method_index();
        if let Some(i) =
            qt::combo(ui, r("method"), Id::new("cmp_method"), &items(&METHODS.map(|m| m.0)), Some(method), "", has, p)
            && i != method
        {
            self.on_method(i, ctx);
        }
        if let Some(i) = qt::combo(ui, r("ncmap"), Id::new("cmp_ncmap"), &cmaps, Some(self.cmap), "", has, p)
            && i != self.cmap
        {
            self.cmap = i;
            self.schedule_redraw(true);
            self.render_now(ctx);
        }
        let s = self.settings_for(self.target_key().as_ref());
        if s.method == NoiseMethod::Clip {
            let clip_on = has;
            qt::label(ui, r("low_thr_label"), "Lower threshold", text);
            qt::label(ui, r("high_thr_label"), "Upper threshold", text);
            qt::label(ui, r("low_value"), &format!("{:.2} Digits", s.clip_low), text);
            qt::label(ui, r("high_value"), &format!("{:.2} Digits", s.clip_high), text);
            if self.units == 1 {
                // `(threshold − cold) × DEFAULT_DB_SCALE`, the lower threshold as cold.
                qt::label(ui, r("low_sub"), &format!("{:.2} dB", 0.0), text);
                qt::label(ui, r("high_sub"), &format!("{:.2} dB", (s.clip_high - s.clip_low) * DEFAULT_DB_SCALE), text);
            }
            if let Some(v) =
                app_slider(ui, r("low_slider"), Id::new("cmp_low_slider"), self.low_pos, SLIDER_STEPS, clip_on, p)
            {
                self.low_pos = v;
                self.on_slider(true, ctx);
            }
            if let Some(v) =
                app_slider(ui, r("high_slider"), Id::new("cmp_high_slider"), self.high_pos, SLIDER_STEPS, clip_on, p)
            {
                self.high_pos = v;
                self.on_slider(false, ctx);
            }
            let mut log = s.log_scale;
            if qt::check_box(
                ui,
                r("log_scale"),
                Id::new("cmp_log_scale"),
                "Logarithmic Threshold Scale",
                &mut log,
                clip_on,
                p,
            ) {
                self.on_log_scale(log);
            }
        }

        // Measurement.
        let measuring = !self.measurements.is_empty() || !self.capture_points.is_empty();
        if qt::button(ui, r("ruler"), Id::new("cmp_ruler"), "Start Ruler", false, has, p).clicked() {
            self.start_ruler(ctx);
        }
        if qt::button(ui, r("ruler_clear"), Id::new("cmp_ruler_clear"), "Clear", false, measuring, p).clicked() {
            self.clear_measurements(true);
        }
        qt::group_box(ui, r("readout_group"), "Readout", p);
        qt::wrapped(ui, r("readout_text"), &self.readout.text.clone(), text, false);
        if qt::button(ui, r("copy"), Id::new("cmp_copy"), "Copy", false, self.readout.copy, p).clicked() {
            ui.ctx().copy_text(self.readout.text.clone());
        }
        if qt::button(ui, r("readout_clear"), Id::new("cmp_readout_clear"), "Clear", false, self.readout.clear, p)
            .clicked()
        {
            self.clear_measurements(true);
        }

        // Actions.
        if qt::button(ui, r("set_range"), Id::new("cmp_set_range"), "Set Range...", false, has, p).clicked() {
            self.open_range_dialog();
        }
        if qt::button(
            ui,
            r("reset_range"),
            Id::new("cmp_reset_range"),
            "Reset Range",
            false,
            has && self.display_range.is_some(),
            p,
        )
        .clicked()
        {
            self.display_range = None;
            self.schedule_redraw(false);
        }
        if qt::button(ui, r("load_config"), Id::new("cmp_load_config"), "Load View Config...", false, true, p).clicked()
        {
            self.load_view_config();
        }
        if qt::button(ui, r("export"), Id::new("cmp_export"), "Export...", false, self.datasets.len() >= 2, p).clicked()
        {
            self.export();
        }
        qt::wrapped(ui, r("status"), &self.status.clone(), text, false);
    }

    /// The panels' scroll area (`hardware_scroll`): stacked canvases, at
    /// least 260 px each, filling the view when they fit.
    fn plot_area(&mut self, ui: &mut Ui, area: Rect, p: &Palette, _ctx: &egui::Context) {
        let mut vp = area.shrink(1.0);
        let n = self.panels.len();
        if n == 0 {
            ui.painter().rect_filled(vp, 0.0, Color32::WHITE);
            widgets::text_center(ui, vp.center(), EMPTY_TEXT, 13.0, Weight::Regular, Color32::BLACK);
            return;
        }
        let needed = n as f32 * PANEL_MIN_H + (n as f32 - 1.0) * PANEL_SPACING;
        let vbar = needed > vp.height() + 0.5;
        if vbar {
            vp.max.x -= BAR;
        }
        ui.painter().rect_filled(vp, 0.0, p.qt.window);
        let content_h = needed.max(vp.height());
        let mut sy = self.plot_scroll;
        if self.visible_capture.is_none() {
            qt::wheel_scroll(ui, vp, &mut sy, (content_h - vp.height()).max(0.0));
        }
        if vbar {
            let bar = Rect::from_min_max(pos2(vp.right(), vp.top()), pos2(area.right() - 1.0, vp.bottom()));
            qt::scroll_bar(ui, bar, Id::new("cmp_plot_vbar"), true, &mut sy, content_h, vp.height(), p);
        }
        self.plot_scroll = sy.clamp(0.0, (content_h - vp.height()).max(0.0));
        // QBoxLayout: the spare height shared out, the first panels a pixel taller.
        let total = (content_h.round() as i32 - (n as i32 - 1) * PANEL_SPACING as i32).max(0);
        let (each, extra) = (total / n as i32, total % n as i32);
        let mut child = ui.new_child(UiBuilder::new().max_rect(vp));
        child.set_clip_rect(vp);
        let mut y = (vp.top() - self.plot_scroll).round();
        let mut events = Vec::new();
        for (i, canvas) in self.canvases.iter_mut().enumerate() {
            let h = (each + i32::from((i as i32) < extra)) as f32;
            let rect = Rect::from_min_size(pos2(vp.left(), y), vec2(vp.width(), h));
            if rect.intersects(vp) {
                canvas.show(&mut child, rect);
            }
            for e in canvas.take_events() {
                events.push((i, e));
            }
            y += h + PANEL_SPACING;
        }
        for (i, e) in events {
            if let CanvasEvent::MeasurementCaptureFinished(points) = e {
                self.measurement_finished(i, points);
            }
        }
    }

    /// The panel rectangles as last laid out, in window coordinates.
    fn panel_rects(&self, area: Rect) -> (Rect, Vec<Rect>) {
        let mut vp = area.shrink(1.0);
        let n = self.panels.len();
        let needed = n as f32 * PANEL_MIN_H + (n as f32 - 1.0).max(0.0) * PANEL_SPACING;
        if needed > vp.height() + 0.5 {
            vp.max.x -= BAR;
        }
        let content_h = needed.max(vp.height());
        let total = (content_h.round() as i32 - (n as i32 - 1).max(0) * PANEL_SPACING as i32).max(0);
        let (each, extra) = if n == 0 { (0, 0) } else { (total / n as i32, total % n as i32) };
        let mut y = (vp.top() - self.plot_scroll).round();
        let mut rects = Vec::new();
        for i in 0..n {
            let h = (each + i32::from((i as i32) < extra)) as f32;
            rects.push(Rect::from_min_size(pos2(vp.left(), y), vec2(vp.width(), h)));
            y += h + PANEL_SPACING;
        }
        (vp, rects)
    }

    /// The visible export's frames: scroll a panel into view, ask for a
    /// screenshot, crop it out, and save the stack once every panel is in.
    fn poll_visible_capture(&mut self, ui: &Ui, area: Rect, ctx: &egui::Context) {
        let Some(cap) = &mut self.visible_capture else { return };
        let shot = ctx.input(|i| {
            i.raw.events.iter().find_map(|e| match e {
                egui::Event::Screenshot { image, .. } => Some(image.clone()),
                _ => None,
            })
        });
        if cap.asked
            && let Some(image) = shot
        {
            let ppp = ctx.pixels_per_point();
            let (vp, rects) = {
                let me = &*self;
                me.panel_rects(area)
            };
            let cap = self.visible_capture.as_mut().unwrap();
            if let Some(panel) = rects.get(cap.next) {
                // `ImageExporter(plot_item)`: the plot item inside the
                // GraphicsLayout's 9 px margins, at least 1400 px wide.
                let item = panel.shrink(9.0);
                let shot = crop(&image, item.intersect(vp), ppp);
                let width = ((panel.width() * ppp).round() as u32).max(1400);
                let height = (width as f64 * item.height() as f64 / item.width().max(1.0) as f64).floor() as u32;
                cap.images.push(resize(&shot, width, height.max(1)));
            }
            cap.next += 1;
            cap.asked = false;
            cap.scrolled = false;
        }
        let n = self.panels.len();
        let cap = self.visible_capture.as_mut().unwrap();
        if cap.next >= n {
            let done = self.visible_capture.take().unwrap();
            self.plot_scroll = done.scroll_before;
            let result = save_stacked(&done.images, &done.path, &done.ext);
            self.export_finished(&done.path, result);
            return;
        }
        if cap.scrolled && !cap.asked {
            // This frame drew the panel where it was scrolled to.
            cap.asked = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::default()));
        } else if !cap.scrolled {
            // Scroll so the panel is wholly in view; it is shot once drawn there.
            let next = cap.next;
            let (vp, rects) = {
                let saved = self.plot_scroll;
                self.plot_scroll = 0.0;
                let out = self.panel_rects(area);
                self.plot_scroll = saved;
                out
            };
            if let Some(r) = rects.get(next) {
                let top = r.top() - vp.top();
                let max = (rects.last().map_or(0.0, |l| l.bottom()) - vp.bottom()).max(0.0);
                self.plot_scroll = top.min(max).max(0.0);
            }
            self.visible_capture.as_mut().unwrap().scrolled = true;
        }
        let _ = ui;
        ctx.request_repaint();
    }

    /// Shows the window (maximized when first opened, as `showEvent` does).
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<ComparisonAction> {
        let mut action = None;
        let viewport = egui::ViewportId::from_hash_of("multi_station_comparison");
        let size = crate::downloads::fit_to_screen(ctx, 1400.0, 900.0);
        let builder = egui::ViewportBuilder::default()
            .with_title("Multi-Station Comparison")
            .with_inner_size(size)
            .with_min_inner_size(vec2(442.0, 230.0))
            .with_maximized(true)
            .with_minimize_button(false);
        let bg = p.page_bg;
        ctx.show_viewport_immediate(viewport, builder, |ctx, _| {
            let close = ctx.input(|i| i.viewport().close_requested());
            egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| {
                if let Some(a) = self.frame(ui, p) {
                    action = Some(a);
                }
            });
            if close {
                action = Some(ComparisonAction::Close);
            }
        });
        action
    }
}

fn group(id: &'static str, l: Layout) -> Item {
    Item::Container(Container {
        spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred),
        margins: [13, 27, 13, 13],
        layout: Box::new(l),
    })
}

/// A `QSplitter` handle between side-by-side panes: Fusion's grips down its middle.
fn splitter_handle_vertical(ui: &Ui, rect: Rect, id: Id) -> egui::Response {
    let resp = ui.interact(rect, id, Sense::drag());
    if resp.hovered() || resp.dragged() {
        ui.ctx().set_cursor_icon(egui::CursorIcon::ResizeHorizontal);
    }
    let ppp = ui.ctx().pixels_per_point();
    let snap = |r: Rect| {
        Rect::from_min_max(
            pos2((r.min.x * ppp).round() / ppp, (r.min.y * ppp).round() / ppp),
            pos2((r.max.x * ppp).round() / ppp, (r.max.y * ppp).round() / ppp),
        )
    };
    let cx = rect.left() + ((rect.width() - 1.0) / 2.0).floor();
    let cy = rect.top() + ((rect.height() - 1.0) / 2.0).floor();
    for i in (-6..12).step_by(3) {
        let at = pos2(cx, cy + i as f32);
        qt::fill_exact(
            ui.painter(),
            snap(Rect::from_min_size(at, vec2(2.0, 2.0))),
            CornerRadius::ZERO,
            Color32::from_rgba_unmultiplied(255, 255, 255, 90),
        );
        qt::fill_exact(
            ui.painter(),
            snap(Rect::from_min_size(at, vec2(1.0, 1.0))),
            CornerRadius::ZERO,
            Color32::from_rgba_unmultiplied(0, 0, 0, 60),
        );
    }
    resp
}

/// The app's `QSlider`: a 6 px groove in the border colour, a 14 px accent
/// handle 5 px beyond it each side. Returns the new value while dragged or
/// clicked (a click pages towards the pointer).
fn app_slider(ui: &Ui, rect: Rect, id: Id, value: i32, max: i32, enabled: bool, p: &Palette) -> Option<i32> {
    const HANDLE_W: f32 = 14.0;
    let resp = ui.interact(rect, id, if enabled { Sense::click_and_drag() } else { Sense::hover() });
    let span = (rect.width() - HANDLE_W).max(1.0);
    let t = value.clamp(0, max) as f32 / max.max(1) as f32;
    let cx = rect.left() + HANDLE_W / 2.0 + t * span;
    let cy = rect.center().y;
    let painter = ui.painter_at(rect);
    painter.rect_filled(Rect::from_min_max(pos2(rect.left(), cy - 3.0), pos2(rect.right(), cy + 3.0)), 3.0, p.border);
    painter.rect_filled(Rect::from_center_size(pos2(cx, cy), vec2(HANDLE_W, 16.0)), 7.0, p.accent);
    let mut out = None;
    if enabled
        && (resp.dragged() || resp.clicked())
        && let Some(pos) = resp.interact_pointer_pos()
    {
        let v = (((pos.x - rect.left() - HANDLE_W / 2.0) / span).clamp(0.0, 1.0) * max as f32).round() as i32;
        let v = if resp.dragged() {
            v
        } else {
            // QAbstractSlider's page step (10 % of the range) towards the click.
            let page = (max / 10).max(1);
            if pos.x < cx { (value - page).max(0) } else { (value + page).min(max) }
        };
        if v != value {
            out = Some(v);
        }
    }
    if enabled && resp.hovered() {
        let wheel: f32 = ui.input(|i| {
            i.events
                .iter()
                .filter_map(|e| match e {
                    egui::Event::MouseWheel { delta, .. } => Some(delta.y),
                    _ => None,
                })
                .sum()
        });
        if wheel != 0.0 {
            let v = (value + wheel.signum() as i32 * 3).clamp(0, max);
            if v != value {
                out = Some(v);
            }
        }
    }
    out
}

/// The screen pixels of `r` (logical) from a viewport screenshot.
fn crop(image: &egui::ColorImage, r: Rect, ppp: f32) -> RgbaImage {
    let [w, h] = image.size;
    let x0 = ((r.left() * ppp).round().max(0.0) as usize).min(w);
    let y0 = ((r.top() * ppp).round().max(0.0) as usize).min(h);
    let x1 = ((r.right() * ppp).round().max(0.0) as usize).min(w);
    let y1 = ((r.bottom() * ppp).round().max(0.0) as usize).min(h);
    let mut px = Vec::with_capacity((x1 - x0) * (y1 - y0));
    for y in y0..y1 {
        for x in x0..x1 {
            let c = image.pixels[y * w + x];
            px.push(callisto_core::image::Rgba([c.r(), c.g(), c.b(), 255]));
        }
    }
    RgbaImage::new([x1 - x0, y1 - y0], px)
}

/// The capture at the exporter's size.
fn resize(im: &RgbaImage, width: u32, height: u32) -> RgbaImage {
    let src =
        image::RgbaImage::from_raw(im.size[0] as u32, im.size[1] as u32, im.as_bytes().to_vec()).unwrap_or_default();
    let out = image::imageops::resize(&src, width, height, image::imageops::FilterType::CatmullRom);
    let pixels = out.pixels().map(|p| callisto_core::image::Rgba(p.0)).collect();
    RgbaImage::new([width as usize, height as usize], pixels)
}

/// `_compose_hardware_panel_images` and the format writers of
/// `_export_visible_plot`: the panels stacked, centred on white.
fn save_stacked(images: &[RgbaImage], path: &Path, ext: &str) -> Result<(), String> {
    if images.is_empty() {
        return Err("Could not capture the visible comparison plot.".into());
    }
    let width = images.iter().map(|i| i.size[0]).max().unwrap_or(0);
    let height: usize = images.iter().map(|i| i.size[1]).sum();
    if width == 0 || height == 0 {
        return Err("Could not capture the visible comparison plot.".into());
    }
    let mut rgba = vec![255u8; width * height * 4];
    let mut y = 0;
    for im in images {
        let x = (width - im.size[0]) / 2;
        for row in 0..im.size[1] {
            for col in 0..im.size[0] {
                let c = im.pixels[row * im.size[0] + col].0;
                let at = ((y + row) * width + x + col) * 4;
                rgba[at..at + 4].copy_from_slice(&c);
            }
        }
        y += im.size[1];
    }
    crate::figure_export::save_raster(path, ext, &rgba, width as u32, height as u32).map_err(|e| e.to_string())
}
