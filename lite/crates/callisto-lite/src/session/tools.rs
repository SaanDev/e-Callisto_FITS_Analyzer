//! The analysis tools on the main window's plot: the ruler, the drift-rate
//! estimate, isolating a burst with the lasso, light curves and annotations
//! (`MainWindow` in `src/ui/app/main_window.py`, hardware canvas paths).

use std::sync::Arc;

use callisto_core::analysis::annotations::{
    self, Annotation, AnnotationKind, DEFAULT_ANNOTATION_COLOR, DEFAULT_TEXT_FONT_SIZE, TextStyle,
};
use callisto_core::analysis::lasso;
use callisto_core::analysis::light_curve::{self, LightCurveOverlay, LightCurveSettings};
use callisto_core::analysis::measure::{self, Measurement};
use callisto_core::radio::array::Array2;
use callisto_core::radio::frequency_axis::{invalid_row_mask, matplotlib_extent};

use super::{NoticeKind, PlotView, Session, normalize_plot_type};
use crate::canvas::{CanvasEvent, Mode};

/// The sidebar's Ruler Measurement card (`MeasurementReadout`).
#[derive(Clone, Debug, PartialEq)]
pub struct RulerReadout {
    /// What Copy puts on the clipboard; the label shows it line by line.
    pub text: String,
    pub copy_enabled: bool,
    pub clear_enabled: bool,
}

impl Default for RulerReadout {
    fn default() -> Self {
        Self { text: "No measurement.".into(), copy_enabled: false, clear_enabled: false }
    }
}

impl RulerReadout {
    fn set_empty(&mut self) {
        *self = Self::default();
    }

    fn set_pending(&mut self, text: &str) {
        *self = Self { text: text.into(), copy_enabled: false, clear_enabled: true };
    }

    fn set_error(&mut self, text: &str) {
        let text = if text.is_empty() { "Measurement failed." } else { text };
        *self = Self { text: text.into(), copy_enabled: false, clear_enabled: true };
    }
}

/// A light curve's frequency: the nearest channel and what was asked for.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LightCurveRecord {
    pub frequency_mhz: f64,
    pub requested_mhz: f64,
}

/// `_annotation_style_defaults`.
#[derive(Clone, Debug, PartialEq)]
pub struct AnnotationStyleDefaults {
    pub color: String,
    pub line_width: f64,
    pub text: TextStyle,
}

impl Default for AnnotationStyleDefaults {
    fn default() -> Self {
        Self { color: DEFAULT_ANNOTATION_COLOR.into(), line_width: 1.5, text: TextStyle::default() }
    }
}

/// `_annotation_mode`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AnnotationMode {
    Polygon,
    Line,
    Text,
    MoveText,
}

/// What the tools keep between clicks.
#[derive(Clone, Debug, Default)]
pub struct ToolState {
    pub measurement: Option<Measurement>,
    pub(super) measurement_capture_points: Vec<(f64, f64)>,
    pub ruler: RulerReadout,
    pub drift_points: Vec<(f64, f64)>,
    pub lasso_active: bool,
    pub lasso_mask: Option<Vec<bool>>,
    /// The mask waits a turn of the event loop (`QTimer.singleShot(0, …)`).
    pending_isolation: Option<Vec<bool>>,
    pub light_curve_settings: LightCurveSettings,
    pub light_curves: Vec<LightCurveRecord>,
    /// Analysis → Plot Light Curves → Click on a frequency.
    pub light_curve_click_mode: bool,
    pub annotations: Vec<Annotation>,
    pub annotations_visible: bool,
    pub annotation_style: AnnotationStyleDefaults,
    pub annotation_mode: Option<AnnotationMode>,
    annotation_pending_text: String,
    annotation_pending_style: Option<TextStyle>,
    annotation_target: Option<usize>,
}

impl ToolState {
    pub fn new() -> Self {
        Self { annotations_visible: true, ..Default::default() }
    }
}

/// Python's `f"{v:.{n}f}"`, including nan and inf.
fn pyf(v: f64, n: usize) -> String {
    if v.is_nan() {
        "nan".into()
    } else if v.is_infinite() {
        if v > 0.0 { "inf".into() } else { "-inf".into() }
    } else {
        format!("{v:.n$}")
    }
}

impl Session {
    // ------------------------------------------------------------ ruler

    /// `_format_measurement_time`.
    fn format_measurement_time(&self, t: f64) -> String {
        match (self.use_utc, self.data.as_ref().and_then(|d| d.ut_start_sec)) {
            (true, Some(start)) => {
                // int(round(...)): Python rounds half to even.
                let total = (start + t).round_ties_even() as i64;
                let (h, m, s) =
                    (total.div_euclid(3600).rem_euclid(24), total.rem_euclid(3600) / 60, total.rem_euclid(60));
                format!("{h:02}:{m:02}:{s:02} UT ({} s)", pyf(t, 3))
            }
            _ => format!("{} s", pyf(t, 3)),
        }
    }

    /// `_measurement_overlay_label`.
    pub(super) fn measurement_overlay_label(m: &Measurement) -> String {
        format!(
            "dt={} s\ndf={} MHz\n{} MHz/s",
            pyf(m.duration_s, 3),
            pyf(m.frequency_delta_mhz, 3),
            pyf(m.slope_mhz_s, 6)
        )
    }

    pub(super) fn measurement_points(m: &Measurement) -> [(f64, f64); 2] {
        [(m.point1.time_s, m.point1.frequency_mhz), (m.point2.time_s, m.point2.frequency_mhz)]
    }

    /// `_render_measurement_overlay`.
    fn render_measurement_overlay(&mut self) {
        if let Some(m) = self.tools.measurement {
            self.canvas.show_measurement(&Self::measurement_points(&m), &Self::measurement_overlay_label(&m));
        } else if !self.tools.measurement_capture_points.is_empty() {
            let points = self.tools.measurement_capture_points.clone();
            self.canvas.show_measurement(&points, "");
        } else {
            self.canvas.clear_measurement();
        }
    }

    /// `_set_measurement_result`.
    fn set_measurement_result(&mut self, m: Measurement) {
        self.tools.measurement = Some(m);
        self.tools.measurement_capture_points.clear();
        let text = format!(
            "P1: {}, {} MHz\nP2: {}, {} MHz\nDuration: {} s\nFrequency drift: {} MHz\nSlope: {} MHz/s",
            self.format_measurement_time(m.point1.time_s),
            pyf(m.point1.frequency_mhz, 3),
            self.format_measurement_time(m.point2.time_s),
            pyf(m.point2.frequency_mhz, 3),
            pyf(m.duration_s, 3),
            pyf(m.frequency_delta_mhz, 3),
            pyf(m.slope_mhz_s, 6)
        );
        self.tools.ruler = RulerReadout { text, copy_enabled: true, clear_enabled: true };
        self.render_measurement_overlay();
        self.show_message(
            format!(
                "Measurement: dt={} s, df={} MHz, slope={} MHz/s",
                pyf(m.duration_s, 3),
                pyf(m.frequency_delta_mhz, 3),
                pyf(m.slope_mhz_s, 6)
            ),
            0,
        );
    }

    /// `clear_ruler_measurement`.
    pub fn clear_ruler_measurement(&mut self, reset_readout: bool) {
        self.tools.measurement = None;
        self.tools.measurement_capture_points.clear();
        self.canvas.clear_measurement();
        self.canvas.stop_interaction_capture();
        if reset_readout {
            self.tools.ruler.set_empty();
            self.show_message("Ruler measurement cleared.", 2500);
        }
    }

    /// Whether Analysis → Clear Ruler Measurement does anything.
    pub fn has_ruler_measurement(&self) -> bool {
        self.tools.measurement.is_some() || !self.tools.measurement_capture_points.is_empty()
    }

    /// `activate_ruler_tool`.
    pub fn activate_ruler_tool(&mut self) {
        if !self.has_data() {
            self.warn(NoticeKind::Information, "Ruler Measurement", "Load a FITS file first.");
            return;
        }
        self.tools.light_curve_click_mode = false;
        self.reset_annotation_mode();
        self.tools.measurement = None;
        self.tools.measurement_capture_points.clear();
        self.canvas.clear_measurement();
        self.tools.ruler.set_pending("Click the first point on the spectrum.");
        self.canvas.begin_measurement_capture();
        self.show_message("Click two points to measure duration, frequency drift, and slope.", 6000);
    }

    /// `_on_accel_measurement_capture_finished`.
    fn on_measurement_capture_finished(&mut self, points: &[(f64, f64)]) {
        if points.len() < 2 {
            return;
        }
        match measure::two_point(points[0], points[1]) {
            Ok(m) => self.set_measurement_result(m),
            Err(e) => {
                self.tools.measurement = None;
                self.tools.measurement_capture_points.clear();
                self.canvas.clear_measurement();
                self.tools.ruler.set_error(&e);
                self.show_message(e, 4000);
            }
        }
    }

    // ------------------------------------------------------------ drift

    /// `activate_drift_tool`.
    pub fn activate_drift_tool(&mut self) {
        self.tools.light_curve_click_mode = false;
        self.show_message("Click multiple points along the burst. Right-click or double-click to finish.", 8000);
        self.tools.drift_points.clear();
        self.canvas.begin_drift_capture();
        self.canvas.show_drift_points(&[], false);
    }

    /// `finish_drift_estimation`.
    fn finish_drift_estimation(&mut self) {
        self.canvas.stop_interaction_capture();
        if self.tools.drift_points.len() < 2 {
            self.show_message("Need at least two points to estimate drift.", 4000);
            return;
        }
        let Some(s) = measure::drift_summary(&self.tools.drift_points) else {
            self.show_message("Need points with different time values to estimate drift.", 4000);
            return;
        };
        self.canvas.show_drift_points(&s.points, true);
        self.show_message(
            format!(
                "Average Drift Rate: {} MHz/s, Start Frequency: {}, End Frequency: {}, Duration: {} s",
                pyf(s.avg_drift_mhz_s, 4),
                pyf(s.start_frequency_mhz, 3),
                pyf(s.end_frequency_mhz, 3),
                pyf(s.duration_s, 3)
            ),
            0,
        );
    }

    /// `_clear_drift_overlays`: whether there were drift points.
    pub(super) fn clear_drift_overlays(&mut self) -> bool {
        let had = !self.tools.drift_points.is_empty();
        self.tools.drift_points.clear();
        self.canvas.stop_interaction_capture();
        self.canvas.show_drift_points(&[], false);
        had
    }

    // ------------------------------------------------------------ lasso

    /// `activate_lasso`.
    pub fn activate_lasso(&mut self) {
        if self.noise_reduced.is_none() {
            self.warn(NoticeKind::Warning, "Error", "Please apply background substraction before isolating a burst.");
            return;
        }
        self.tools.light_curve_click_mode = false;
        self.tools.lasso_active = true;
        self.canvas.begin_lasso_capture();
        self.show_message("Press, drag around the burst, and release to isolate.", 5000);
    }

    /// `on_lasso_select`: the cells inside the lasso, isolated on the next turn.
    fn on_lasso_select(&mut self, verts: &[(f64, f64)]) {
        let (Some(nr), Some(d)) = (&self.noise_reduced, &self.data) else { return };
        if verts.len() < 3 {
            return;
        }
        let Some(extent) = matplotlib_extent(&d.freqs, &d.time, d.frequency_step_mhz) else { return };
        let mask = lasso::lasso_mask(verts, extent, nr.rows, nr.cols);
        self.tools.lasso_mask = Some(mask.clone());
        self.tools.lasso_active = false;
        self.tools.pending_isolation = Some(mask);
    }

    /// The isolation `on_lasso_select` deferred; true when it ran.
    pub(super) fn flush_pending_isolation(&mut self) -> bool {
        let Some(mask) = self.tools.pending_isolation.take() else { return false };
        self.plot_isolated_burst(&mask);
        true
    }

    /// `_plot_isolated_burst`.
    fn plot_isolated_burst(&mut self, mask: &[bool]) {
        let Some(nr) = self.noise_reduced.clone() else { return };
        self.push_undo_state();
        self.noise_reduced_original = Some(nr.clone());
        self.noise_reduced_original_plot_type = normalize_plot_type(&self.current_plot_type);
        let gap = invalid_row_mask(&nr, self.data.as_ref().and_then(|d| d.gap_rows.as_deref()));
        let burst = Arc::new(lasso::isolate(&nr, mask, &gap));
        self.plot_source(burst.clone(), "Isolated Burst", PlotView::Full);
        // _refresh_accel_plot directly: no deferred "Loaded:" message.
        self.loaded_message_due = false;
        self.noise_reduced = Some(burst);
        self.current_plot_type = "Isolated Burst".into();
        self.show_message("Burst isolated using lasso", 4000);
    }

    // ------------------------------------------------------- light curves

    fn has_light_curve_dataset(&self) -> bool {
        self.data.as_ref().is_some_and(|d| !d.freqs.is_empty() && !d.time.is_empty())
    }

    /// `_light_curve_frequency_limits`.
    pub fn light_curve_frequency_limits(&self) -> Option<(f64, f64)> {
        light_curve::frequency_limits(&self.data.as_ref()?.freqs)
    }

    /// The frequency the Enter-frequency dialog starts at.
    pub fn light_curve_initial_frequency(&self) -> Option<f64> {
        self.tools.light_curves.last().map(|r| r.frequency_mhz).filter(|f| f.is_finite())
    }

    /// `_current_light_curve_source_data`: the plotted data, before units.
    pub(super) fn current_light_curve_source(&self) -> Option<Arc<Array2>> {
        let d = self.data.as_ref()?;
        let shape = (d.freqs.len(), d.time.len());
        [self.current_plot_source.clone(), self.noise_reduced.clone(), Some(d.raw.clone())]
            .into_iter()
            .flatten()
            .find(|a| (a.rows, a.cols) == shape)
    }

    /// `_build_light_curve_overlay`.
    fn build_light_curve_overlay(&self, requested: f64, curve_index: usize) -> Result<LightCurveOverlay, String> {
        let source = self
            .current_light_curve_source()
            .ok_or_else(|| "Load a FITS file before plotting a light curve.".to_owned())?;
        let d = self.data.as_ref().ok_or_else(|| "Load a FITS file before plotting a light curve.".to_owned())?;
        let (row, actual) = light_curve::nearest_frequency(&d.freqs, requested)
            .ok_or_else(|| "No valid frequency axis is available.".to_owned())?;
        let display = self.intensity_for_display(&source);
        if row >= display.rows {
            return Err("Light-curve data does not match the frequency axis.".into());
        }
        let series: Vec<f64> = display.row(row).iter().map(|&v| v as f64).collect();
        light_curve::build_overlay(
            &d.time,
            &series,
            &d.freqs,
            requested,
            row,
            actual,
            &self.tools.light_curve_settings,
            curve_index,
        )
    }

    /// `_build_active_light_curve_overlays`.
    pub(super) fn build_active_light_curve_overlays(&self) -> (Vec<LightCurveOverlay>, Vec<LightCurveRecord>) {
        let mut overlays = Vec::new();
        let mut records = Vec::new();
        for (i, r) in self.tools.light_curves.iter().enumerate() {
            if let Ok(o) = self.build_light_curve_overlay(r.requested_mhz, i) {
                records.push(LightCurveRecord { frequency_mhz: o.frequency_mhz, requested_mhz: r.requested_mhz });
                overlays.push(o);
            }
        }
        (overlays, records)
    }

    /// `_store_light_curve_records`: one record unless the mode is multiple.
    pub(super) fn store_light_curve_records(&mut self, mut records: Vec<LightCurveRecord>) {
        records.retain(|r| r.frequency_mhz.is_finite());
        if !self.tools.light_curve_settings.multiple && records.len() > 1 {
            records.drain(..records.len() - 1);
        }
        self.tools.light_curves = records;
    }

    /// `_render_light_curve_overlay` (and the hardware-only variant after a replot).
    pub(super) fn render_light_curve_overlay(&mut self) -> bool {
        if self.tools.light_curves.is_empty() {
            self.canvas.clear_light_curve_overlay();
            return false;
        }
        let (overlays, records) = self.build_active_light_curve_overlays();
        if overlays.is_empty() {
            self.store_light_curve_records(Vec::new());
            self.canvas.clear_light_curve_overlay();
            return false;
        }
        self.store_light_curve_records(records);
        self.canvas.set_light_curve_overlays(&overlays);
        true
    }

    /// `_clear_light_curve_overlay`.
    fn clear_light_curve_overlay(&mut self, clear_frequency: bool) {
        self.canvas.clear_light_curve_overlay();
        if clear_frequency {
            self.tools.light_curves.clear();
        }
    }

    /// `clear_light_curve`.
    pub fn clear_light_curve(&mut self) {
        let had = !self.tools.light_curves.is_empty();
        self.clear_light_curve_overlay(true);
        self.show_message(if had { "Light curve(s) cleared." } else { "No light curve to clear." }, 2500);
    }

    pub fn has_light_curves(&self) -> bool {
        !self.tools.light_curves.is_empty()
    }

    /// `plot_light_curve_at_frequency`; errors go to a message box or the status bar.
    pub fn plot_light_curve_at_frequency(&mut self, frequency_mhz: f64, show_errors: bool) -> bool {
        let multiple = self.tools.light_curve_settings.multiple;
        let index = if multiple { self.tools.light_curves.len() } else { 0 };
        let overlay = match self.build_light_curve_overlay(frequency_mhz, index) {
            Ok(o) => o,
            Err(reason) => {
                let reason = if reason.is_empty() { "Could not plot the light curve.".to_owned() } else { reason };
                if show_errors {
                    self.warn(NoticeKind::Information, "Plot Light Curve", reason);
                } else {
                    self.show_message(reason, 3000);
                }
                return false;
            }
        };
        let record = LightCurveRecord { frequency_mhz: overlay.frequency_mhz, requested_mhz: overlay.requested_mhz };
        let mut records = if multiple { self.tools.light_curves.clone() } else { Vec::new() };
        records.push(record);
        self.store_light_curve_records(records);
        self.render_light_curve_overlay();
        let (requested, actual) = (overlay.requested_mhz, overlay.frequency_mhz);
        let msg = if (requested - actual).abs() > 1e-6 {
            format!("Light curve plotted at nearest frequency: {} MHz.", pyf(actual, 3))
        } else {
            format!("Light curve plotted at {} MHz.", pyf(actual, 3))
        };
        self.show_message(msg, 4000);
        true
    }

    /// `_set_light_curve_settings`: a change of mode to single keeps the newest curve.
    pub fn set_light_curve_settings(&mut self, settings: LightCurveSettings) {
        let was_multiple = self.tools.light_curve_settings.multiple;
        self.tools.light_curve_settings = settings.normalized();
        if was_multiple && !self.tools.light_curve_settings.multiple && self.tools.light_curves.len() > 1 {
            let last = self.tools.light_curves.len() - 1;
            self.tools.light_curves.drain(..last);
        }
        if !self.tools.light_curves.is_empty() {
            self.render_light_curve_overlay();
        }
    }

    /// Whether the Enter-frequency dialog can open (`open_light_curve_frequency_dialog`).
    pub fn can_plot_light_curve(&mut self) -> bool {
        if self.light_curve_frequency_limits().is_none() || !self.has_light_curve_dataset() {
            self.warn(NoticeKind::Information, "Plot Light Curve", "Load a FITS file before plotting a light curve.");
            return false;
        }
        true
    }

    /// `_on_light_curve_click_mode_toggled`.
    pub fn set_light_curve_click_mode(&mut self, checked: bool) {
        if checked && !self.has_light_curve_dataset() {
            self.tools.light_curve_click_mode = false;
            self.warn(NoticeKind::Information, "Plot Light Curve", "Load a FITS file before selecting a frequency.");
            return;
        }
        self.tools.light_curve_click_mode = checked;
        if checked {
            self.show_message("Click a frequency on the spectrogram to plot its light curve.", 5000);
        } else {
            self.show_message("Light-curve click mode disabled.", 2500);
        }
    }

    /// `_handle_light_curve_click`, from `plotClicked`.
    fn handle_light_curve_click(&mut self, y: f64) -> bool {
        if !self.tools.light_curve_click_mode {
            return false;
        }
        // _light_curve_click_blocked
        if self.tools.lasso_active || self.rect_zoom_active || self.tools.annotation_mode.is_some() {
            return false;
        }
        self.plot_light_curve_at_frequency(y, false);
        true
    }

    // -------------------------------------------------------- annotations

    /// `_text_annotation_style_defaults`.
    pub fn text_annotation_style(&self) -> TextStyle {
        self.tools.annotation_style.text.clone()
    }

    /// `_annotation_text_style`: the label's own style over the defaults.
    pub fn annotation_text_style(&self, ann: &Annotation) -> TextStyle {
        let d = self.text_annotation_style();
        TextStyle {
            color: if ann.color.is_empty() { d.color } else { ann.color.clone() },
            font_family: if ann.font_family.is_empty() { d.font_family } else { ann.font_family.clone() },
            font_size: ann.font_size.max(6),
            font_bold: ann.font_bold,
            font_italic: ann.font_italic,
        }
    }

    /// `_remember_text_annotation_style`.
    fn remember_text_annotation_style(&mut self, style: &TextStyle) {
        let t = &mut self.tools.annotation_style.text;
        if !style.color.is_empty() {
            t.color = style.color.clone();
        }
        if !style.font_family.is_empty() {
            t.font_family = style.font_family.clone();
        }
        t.font_size = style.font_size.max(6);
        t.font_bold = style.font_bold;
        t.font_italic = style.font_italic;
    }

    /// `_render_annotations` (the hardware overlay).
    pub(super) fn render_annotations(&mut self) {
        let visible: &[Annotation] = if self.tools.annotations_visible { &self.tools.annotations } else { &[] };
        let list = visible.to_vec();
        self.canvas.set_annotations(&list);
    }

    /// The font families text annotations use, for installing their faces.
    pub fn annotation_font_families(&self) -> Vec<String> {
        let mut out: Vec<String> = self
            .tools
            .annotations
            .iter()
            .filter(|a| a.kind == AnnotationKind::Text && !a.font_family.trim().is_empty())
            .map(|a| a.font_family.trim().to_owned())
            .collect();
        out.sort();
        out.dedup();
        out
    }

    fn add_annotation(&mut self, ann: Annotation) {
        let kind = ann.kind.key();
        self.tools.annotations.push(ann);
        self.render_annotations();
        self.log_operation(format!("Added annotation: {kind}"));
        self.mark_project_dirty();
    }

    /// `_reset_annotation_mode`.
    pub fn reset_annotation_mode(&mut self) {
        self.tools.annotation_mode = None;
        self.tools.annotation_pending_text.clear();
        self.tools.annotation_pending_style = None;
        self.tools.annotation_target = None;
        self.tools.lasso_active = false;
        self.canvas.stop_interaction_capture();
    }

    fn require_data_for_annotations(&mut self) -> bool {
        if !self.has_data() {
            self.warn(NoticeKind::Information, "Annotations", "Load a FITS file first.");
            return false;
        }
        true
    }

    /// `start_annotation_polygon`.
    pub fn start_annotation_polygon(&mut self) {
        if !self.require_data_for_annotations() {
            return;
        }
        self.tools.light_curve_click_mode = false;
        self.reset_annotation_mode();
        self.tools.annotation_mode = Some(AnnotationMode::Polygon);
        self.canvas.begin_annotation_capture(AnnotationKind::Polygon);
        self.show_message("Left-click to add polygon vertices. Double-click or right-click to finish.", 5000);
    }

    /// `start_annotation_line`.
    pub fn start_annotation_line(&mut self) {
        if !self.require_data_for_annotations() {
            return;
        }
        self.tools.light_curve_click_mode = false;
        self.reset_annotation_mode();
        self.tools.annotation_mode = Some(AnnotationMode::Line);
        self.canvas.begin_annotation_capture(AnnotationKind::Line);
        self.show_message("Click the start point, then the end point for the line.", 5000);
    }

    /// Whether Add Text can ask for its text (`start_annotation_text` before the dialog).
    pub fn can_start_annotation_text(&mut self) -> bool {
        self.require_data_for_annotations()
    }

    /// `start_annotation_text`, once the dialog gave the text and style.
    pub fn start_annotation_text(&mut self, text: &str, style: TextStyle) {
        self.tools.light_curve_click_mode = false;
        self.reset_annotation_mode();
        self.tools.annotation_mode = Some(AnnotationMode::Text);
        self.tools.annotation_pending_text = text.trim().to_owned();
        self.tools.annotation_pending_style = Some(style);
        self.canvas.begin_annotation_capture(AnnotationKind::Text);
        self.show_message("Click where to place the text annotation.", 5000);
    }

    /// The text labels with their indices and list labels (`_text_annotations_with_indices`).
    pub fn text_annotations(&self) -> Vec<(usize, String)> {
        self.tools
            .annotations
            .iter()
            .enumerate()
            .filter(|(_, a)| a.kind == AnnotationKind::Text)
            .map(|(i, a)| (i, annotations::text_label(i, a)))
            .collect()
    }

    pub fn annotation(&self, index: usize) -> Option<&Annotation> {
        self.tools.annotations.get(index)
    }

    /// `_replace_annotation`.
    fn replace_annotation(&mut self, index: usize, updated: Annotation, status: &str, log: String) -> bool {
        if index >= self.tools.annotations.len() {
            return false;
        }
        let Some(ann) = annotations::normalize_annotations(vec![updated]).pop() else { return false };
        self.tools.annotations[index] = ann;
        self.render_annotations();
        self.show_message(status.to_owned(), 3000);
        self.log_operation(log);
        self.mark_project_dirty();
        true
    }

    /// `edit_text_label`, once the dialog was accepted.
    pub fn edit_text_label(&mut self, index: usize, text: &str, style: &TextStyle) {
        let Some(mut ann) = self.tools.annotations.get(index).cloned() else { return };
        ann.text = text.trim().to_owned();
        ann.color = if style.color.is_empty() {
            if ann.color.is_empty() { DEFAULT_ANNOTATION_COLOR.into() } else { ann.color }
        } else {
            style.color.clone()
        };
        ann.font_family = style.font_family.clone();
        ann.font_size = if style.font_size == 0 { DEFAULT_TEXT_FONT_SIZE } else { style.font_size };
        ann.font_bold = style.font_bold;
        ann.font_italic = style.font_italic;
        self.remember_text_annotation_style(style);
        let label = annotations::text_label(index, &ann);
        self.replace_annotation(index, ann, "Updated text label style.", format!("Edited text label: {label}"));
    }

    /// `move_text_label`, once a label was chosen.
    pub fn move_text_label(&mut self, index: usize) {
        self.reset_annotation_mode();
        self.tools.annotation_mode = Some(AnnotationMode::MoveText);
        self.tools.annotation_target = Some(index);
        self.canvas.begin_annotation_capture(AnnotationKind::Text);
        self.show_message("Click the new location for the selected text label.", 5000);
    }

    /// `_move_selected_text_annotation_to`.
    fn move_selected_text_annotation_to(&mut self, point: (f64, f64)) {
        let index = self.tools.annotation_target;
        let Some(mut ann) = index.and_then(|i| self.tools.annotations.get(i)).cloned() else {
            self.reset_annotation_mode();
            return;
        };
        let index = index.unwrap();
        if ann.kind != AnnotationKind::Text {
            self.reset_annotation_mode();
            return;
        }
        ann.points = vec![[point.0, point.1]];
        let label = annotations::text_label(index, &ann);
        self.replace_annotation(index, ann, "Moved text label.", format!("Moved text label: {label}"));
        self.reset_annotation_mode();
    }

    /// `toggle_annotations_visibility`.
    pub fn toggle_annotations_visibility(&mut self) {
        self.tools.annotations_visible = !self.tools.annotations_visible;
        let list = std::mem::take(&mut self.tools.annotations);
        self.tools.annotations = annotations::toggle_all_visibility(list, self.tools.annotations_visible);
        self.render_annotations();
        let state = if self.tools.annotations_visible { "shown" } else { "hidden" };
        self.show_message(format!("Annotations {state}."), 2500);
        self.mark_project_dirty();
    }

    /// `delete_last_annotation`.
    pub fn delete_last_annotation(&mut self) {
        if self.tools.annotations.pop().is_none() {
            self.show_message("No annotations to delete.", 2500);
            return;
        }
        self.render_annotations();
        self.show_message("Deleted last annotation.", 2500);
        self.mark_project_dirty();
    }

    /// `clear_annotations`.
    pub fn clear_annotations(&mut self) {
        self.tools.annotations.clear();
        self.render_annotations();
        self.show_message("Cleared annotations.", 2500);
        self.mark_project_dirty();
    }

    fn commit_shape(&mut self, kind: AnnotationKind, points: &[(f64, f64)]) {
        let pts: Vec<[f64; 2]> = points.iter().map(|p| [p.0, p.1]).collect();
        let s = &self.tools.annotation_style;
        let ann =
            annotations::make_annotation(kind, &pts, "", &s.color, s.line_width, None, self.tools.annotations_visible);
        self.add_annotation(ann);
        self.reset_annotation_mode();
    }

    /// `_on_accel_annotation_capture_finished`.
    fn on_annotation_capture_finished(&mut self, kind: AnnotationKind, points: Vec<(f64, f64)>) {
        let mode = self.tools.annotation_mode;
        if mode == Some(AnnotationMode::MoveText) {
            match (kind, points.first()) {
                (AnnotationKind::Text, Some(&p)) => self.move_selected_text_annotation_to(p),
                _ => self.reset_annotation_mode(),
            }
            return;
        }
        let matches = matches!(
            (kind, mode),
            (AnnotationKind::Polygon, Some(AnnotationMode::Polygon))
                | (AnnotationKind::Line, Some(AnnotationMode::Line))
                | (AnnotationKind::Text, Some(AnnotationMode::Text))
        );
        if !matches {
            self.reset_annotation_mode();
            return;
        }
        match kind {
            // _on_annotation_polygon_finished
            AnnotationKind::Polygon if points.len() >= 3 => self.commit_shape(kind, &points),
            AnnotationKind::Line if points.len() >= 2 => self.commit_shape(kind, &points[..2]),
            AnnotationKind::Text if !points.is_empty() => {
                // _commit_text_annotation
                let style = self.tools.annotation_pending_style.clone().unwrap_or_else(|| self.text_annotation_style());
                self.remember_text_annotation_style(&style);
                let text = self.tools.annotation_pending_text.clone();
                let s = &self.tools.annotation_style;
                let ann = annotations::make_annotation(
                    kind,
                    &[[points[0].0, points[0].1]],
                    &text,
                    &style.color,
                    s.line_width,
                    Some(&style),
                    self.tools.annotations_visible,
                );
                self.add_annotation(ann);
                self.reset_annotation_mode();
            }
            _ => self.reset_annotation_mode(),
        }
    }

    /// `_on_accel_annotation_capture_cancelled`.
    fn on_annotation_capture_cancelled(&mut self, kind: AnnotationKind) {
        let mode = self.tools.annotation_mode;
        if mode == Some(AnnotationMode::MoveText) && kind == AnnotationKind::Text {
            self.reset_annotation_mode();
            self.show_message("Cancelled text label move.", 2500);
            return;
        }
        let matches = matches!(
            (kind, mode),
            (AnnotationKind::Polygon, Some(AnnotationMode::Polygon))
                | (AnnotationKind::Line, Some(AnnotationMode::Line))
                | (AnnotationKind::Text, Some(AnnotationMode::Text))
        );
        if !matches {
            return;
        }
        self.reset_annotation_mode();
        self.show_message(format!("Cancelled {} annotation.", kind.key()), 2500);
    }

    // ------------------------------------------------------------ events

    /// The canvas events the tools handle; false for the others.
    pub(super) fn handle_tool_event(&mut self, event: &CanvasEvent) -> bool {
        match event {
            CanvasEvent::PlotClicked { y, .. } => {
                self.handle_light_curve_click(*y);
            }
            CanvasEvent::LassoFinished(verts) => {
                // _on_accel_lasso_finished
                if self.tools.lasso_active {
                    self.on_lasso_select(verts);
                }
            }
            CanvasEvent::DriftPointAdded { x, y } => {
                self.tools.drift_points.push((*x, *y));
                let points = self.tools.drift_points.clone();
                self.canvas.show_drift_points(&points, false);
            }
            CanvasEvent::DriftCaptureFinished(points) => {
                self.tools.drift_points = points.clone();
                self.finish_drift_estimation();
            }
            CanvasEvent::MeasurementCaptureFinished(points) => self.on_measurement_capture_finished(points),
            CanvasEvent::AnnotationCaptureFinished { kind, points } => {
                self.on_annotation_capture_finished(*kind, points.clone())
            }
            CanvasEvent::AnnotationCaptureCancelled(kind) => self.on_annotation_capture_cancelled(*kind),
            _ => return false,
        }
        true
    }

    /// Whether a capture is running on the canvas.
    pub fn capture_mode(&self) -> Option<Mode> {
        self.canvas.interaction_mode()
    }

    // ------------------------------------------------------------ resets

    /// `_reset_feature_state_for_new_data`: the tools' state for the previous data.
    pub(super) fn reset_feature_state_for_new_data(&mut self) {
        self.clear_analysis_session();
        self.reset_annotation_mode();
        self.clear_ruler_measurement(true);
        self.clear_light_curve_overlay(true);
        self.tools.light_curve_click_mode = false;
        self.tools.annotations.clear();
        self.tools.annotations_visible = true;
        self.tools.annotation_style = AnnotationStyleDefaults::default();
        self.project_extras.active_preset = None;
        self.tools.lasso_mask = None;
        self.tools.pending_isolation = None;
        self.render_annotations();
    }

    /// What `_apply_loaded_dataset` and `reset_to_raw` drop after plotting.
    pub(super) fn drop_interaction_overlays(&mut self) {
        self.tools.lasso_active = false;
        self.canvas.stop_interaction_capture();
        self.canvas.clear_overlays();
        self.clear_light_curve_overlay(true);
        self.tools.light_curve_click_mode = false;
    }
}
