//! What the analysis tools draw over the spectrogram, and the mouse modes
//! that capture their points: `AcceleratedPlotWidget`'s lasso, drift,
//! measurement and annotation captures, light curves and annotations
//! (`src/ui/widgets/accelerated_plot_widget.py`).
//!
//! Items keep pyqtgraph's stacking: by z value, then by when the item was
//! added. Lines and symbols are rasterised as Qt draws them with
//! antialiasing off (`callisto_core::plot::qt_raster`) into layers between
//! the text items, which egui draws.

use std::sync::Arc;

use callisto_core::analysis::annotations::{Annotation, AnnotationKind};
use callisto_core::analysis::light_curve::LightCurveOverlay;
use callisto_core::plot::qt_raster::{Pen, PenStyle, Raster, parse_color};
use egui::{Color32, ColorImage, FontId, Pos2, Rect, Response, TextureHandle, TextureOptions, Ui, pos2};

use super::{CanvasEvent, Geometry, Pixels, PlotCanvas, galley, metrics};
use crate::fonts::{annotation_font, plot_font};

/// `QGraphicsTextItem`'s document margin.
const DOC_MARGIN: f64 = 4.0;
/// The app font pyqtgraph's `TextItem`s use: Segoe UI 9 pt, 12 px.
const TEXT_ITEM_PX: f32 = 12.0;
/// A line annotation turns into a drag after 4 px (`(dx² + dy²) < 16`).
const LINE_DRAG_DISTANCE_SQ: f64 = 16.0;

/// A capture in progress (`_interaction_mode`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Lasso,
    Drift,
    Measurement,
    Annotation(AnnotationKind),
}

/// Which overlay item a slot holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Role {
    Lasso,
    DriftScatter,
    DriftLine,
    MeasureScatter,
    MeasureLine,
    MeasureText,
    CaptureLine,
    CaptureVertices,
    LightCurve(usize),
    LightCurveLabel(usize),
    Annotation(usize),
    /// `set_series`: the Type II plot's own items.
    Series(usize),
}

impl Role {
    fn is_light_curve(self) -> bool {
        matches!(self, Role::LightCurve(_) | Role::LightCurveLabel(_))
    }
}

/// A text item's font: the app font unless a family is named.
#[derive(Clone, Debug, PartialEq)]
struct TextFont {
    family: String,
    px: f32,
    bold: bool,
    italic: bool,
}

impl TextFont {
    fn app() -> Self {
        Self { family: String::new(), px: TEXT_ITEM_PX, bold: false, italic: false }
    }

    fn font_id(&self) -> FontId {
        if self.family.is_empty() {
            plot_font(self.px, self.bold, self.italic)
        } else {
            annotation_font(&self.family, self.px, self.bold, self.italic)
        }
    }
}

/// pyqtgraph's `PlotDataItem`, `ScatterPlotItem` (circles) and `TextItem`
/// (anchor (0, 1)). `y` is in MHz, also on a log axis.
#[derive(Clone, Debug, PartialEq)]
enum Item {
    Curve { x: Vec<f64>, y: Vec<f64>, pen: Pen, antialias: bool },
    Scatter { x: Vec<f64>, y: Vec<f64>, size: f64, pen: Pen, brush: [u8; 4] },
    Text { x: f64, y: f64, text: String, color: Color32, font: TextFont },
}

#[derive(Clone, Debug)]
struct Entry {
    role: Role,
    z: f64,
    seq: u64,
    item: Item,
}

/// The overlay items, in pyqtgraph's stacking order.
#[derive(Default)]
pub(super) struct Overlays {
    entries: Vec<Entry>,
    next_seq: u64,
    /// Bumped on every change, for the layer cache.
    generation: u64,
}

impl Overlays {
    /// `setData` on an existing item, or `addItem` of a new one.
    fn set(&mut self, role: Role, z: f64, item: Item) {
        self.generation += 1;
        if let Some(e) = self.entries.iter_mut().find(|e| e.role == role) {
            e.item = item;
            e.z = z;
            return;
        }
        self.next_seq += 1;
        self.entries.push(Entry { role, z, seq: self.next_seq, item });
    }

    fn has(&self, role: Role) -> bool {
        self.entries.iter().any(|e| e.role == role)
    }

    /// `removeItem`.
    fn remove(&mut self, keep: impl Fn(Role) -> bool) {
        let before = self.entries.len();
        self.entries.retain(|e| keep(e.role));
        if self.entries.len() != before {
            self.generation += 1;
        }
    }

    fn sorted(&self) -> Vec<&Entry> {
        let mut v: Vec<&Entry> = self.entries.iter().collect();
        v.sort_by(|a, b| a.z.total_cmp(&b.z).then(a.seq.cmp(&b.seq)));
        v
    }
}

/// The press that may become a line annotation drag.
#[derive(Clone, Copy, Debug)]
struct LinePress {
    /// Canvas (scene) position of the press.
    at: (f64, f64),
    xy: (f64, f64),
    last: (f64, f64),
    active: bool,
}

/// The capture state the widget keeps between events.
#[derive(Default)]
pub(super) struct Capture {
    pub(super) mode: Option<Mode>,
    lasso: Vec<(f64, f64)>,
    lasso_drag: bool,
    drift: Vec<(f64, f64)>,
    measurement: Vec<(f64, f64)>,
    annotation: Vec<(f64, f64)>,
    line_press: Option<LinePress>,
    suppress_next_click: bool,
    /// `setMouseEnabled(False)` while capturing.
    pub(super) mouse_suspended: bool,
    /// The event filter took the last press, so Qt sends no click for it.
    press_consumed: bool,
}

/// The overlay layers' textures, for the view and items they were drawn for.
#[derive(Default)]
pub(super) struct LayerCache {
    key: String,
    textures: Vec<Option<TextureHandle>>,
}

fn rgba(c: [u8; 4]) -> [u8; 4] {
    c
}

fn color_or(name: &str, fallback: [u8; 4]) -> [u8; 4] {
    parse_color(name).unwrap_or(fallback)
}

fn points_close(a: (f64, f64), b: (f64, f64)) -> bool {
    (a.0 - b.0).powi(2) + (a.1 - b.1).powi(2) <= 1e-6
}

fn split(points: &[(f64, f64)]) -> (Vec<f64>, Vec<f64>) {
    (points.iter().map(|p| p.0).collect(), points.iter().map(|p| p.1).collect())
}

impl PlotCanvas {
    /// The series as overlay items, z 0, in the order given; items without
    /// a finite point are hidden (`hide()`), so not drawn.
    pub(super) fn overlays_set_series(&mut self, series: &[super::Series]) {
        self.overlays.remove(|r| !matches!(r, Role::Series(_)));
        for (i, s) in series.iter().enumerate() {
            let (xs, ys) = s.xy();
            if !xs.iter().zip(ys).any(|(a, b)| a.is_finite() && b.is_finite()) {
                continue;
            }
            let item = match s {
                super::Series::Scatter { x, y, size, pen, brush } => {
                    Item::Scatter { x: x.clone(), y: y.clone(), size: *size, pen: *pen, brush: *brush }
                }
                super::Series::Curve { x, y, pen, antialias } => {
                    Item::Curve { x: x.clone(), y: y.clone(), pen: *pen, antialias: *antialias }
                }
            };
            self.overlays.set(Role::Series(i), 0.0, item);
        }
    }

    // ------------------------------------------------------- capture API

    /// The capture in progress.
    pub fn interaction_mode(&self) -> Option<Mode> {
        self.capture.mode
    }

    /// `_apply_navigation_state`: the mouse pans and zooms again unless locked.
    fn apply_navigation_state(&mut self) {
        self.capture.mouse_suspended = false;
    }

    /// `begin_lasso_capture`.
    pub fn begin_lasso_capture(&mut self) {
        self.capture.mode = Some(Mode::Lasso);
        self.clear_lasso_overlay();
        self.clear_annotation_capture_overlay();
        // Avoid panning while drawing freehand lasso
        self.capture.mouse_suspended = true;
    }

    /// `begin_drift_capture`: the view still pans.
    pub fn begin_drift_capture(&mut self) {
        self.capture.mode = Some(Mode::Drift);
        self.clear_drift_overlay();
        self.clear_annotation_capture_overlay();
    }

    /// `begin_measurement_capture`.
    pub fn begin_measurement_capture(&mut self) {
        self.capture.mode = Some(Mode::Measurement);
        self.clear_measurement();
        self.clear_lasso_overlay();
        self.clear_annotation_capture_overlay();
        self.capture.mouse_suspended = true;
    }

    /// `begin_annotation_capture`.
    pub fn begin_annotation_capture(&mut self, kind: AnnotationKind) {
        self.capture.mode = Some(Mode::Annotation(kind));
        self.capture.suppress_next_click = false;
        self.clear_lasso_overlay();
        self.clear_annotation_capture_overlay();
        self.capture.mouse_suspended = true;
    }

    /// `stop_interaction_capture`.
    pub fn stop_interaction_capture(&mut self) {
        let was_measurement = self.capture.mode == Some(Mode::Measurement);
        self.capture.mode = None;
        self.capture.suppress_next_click = false;
        self.clear_lasso_overlay();
        self.clear_annotation_capture_overlay();
        if was_measurement {
            self.clear_measurement();
        }
        self.apply_navigation_state();
    }

    /// `clear_overlays`: the captures' items (not light curves or annotations).
    pub fn clear_overlays(&mut self) {
        self.clear_lasso_overlay();
        self.clear_drift_overlay();
        self.clear_measurement();
        self.clear_annotation_capture_overlay();
    }

    /// Everything, for `clear`.
    pub(super) fn reset_overlays(&mut self) {
        self.capture = Capture::default();
        self.overlays.remove(|_| false);
    }

    fn clear_lasso_overlay(&mut self) {
        self.capture.lasso.clear();
        self.capture.lasso_drag = false;
        self.overlays.remove(|r| r != Role::Lasso);
    }

    fn clear_drift_overlay(&mut self) {
        self.capture.drift.clear();
        self.overlays.remove(|r| !matches!(r, Role::DriftScatter | Role::DriftLine));
    }

    /// `clear_measurement`.
    pub fn clear_measurement(&mut self) {
        self.capture.measurement.clear();
        self.overlays.remove(|r| !matches!(r, Role::MeasureScatter | Role::MeasureLine | Role::MeasureText));
    }

    fn clear_annotation_capture_overlay(&mut self) {
        self.capture.annotation.clear();
        self.capture.line_press = None;
        self.overlays.remove(|r| !matches!(r, Role::CaptureLine | Role::CaptureVertices));
    }

    /// `show_drift_points`.
    pub fn show_drift_points(&mut self, points: &[(f64, f64)], with_segments: bool) {
        if points.is_empty() {
            self.clear_drift_overlay();
            return;
        }
        let (x, y) = split(points);
        let pen = Pen::new(rgba([40, 40, 40, 240]), 1.0);
        self.overlays.set(
            Role::DriftScatter,
            0.0,
            Item::Scatter { x: x.clone(), y: y.clone(), size: 10.0, pen, brush: [255, 255, 255, 230] },
        );
        let line = Pen::new([40, 255, 120, 255], 2.0);
        if with_segments {
            self.overlays.set(Role::DriftLine, 0.0, Item::Curve { x, y, pen: line, antialias: false });
        } else if self.overlays.has(Role::DriftLine) {
            self.overlays.set(
                Role::DriftLine,
                0.0,
                Item::Curve { x: Vec::new(), y: Vec::new(), pen: line, antialias: false },
            );
        }
    }

    /// `show_measurement`: the points, their line and the readout beside the last.
    pub fn show_measurement(&mut self, points: &[(f64, f64)], label: &str) {
        let pts: Vec<(f64, f64)> = points.iter().copied().filter(|p| p.0.is_finite() && p.1.is_finite()).collect();
        if pts.is_empty() {
            self.clear_measurement();
            return;
        }
        self.capture.measurement = pts.iter().take(2).copied().collect();
        let (x, y) = split(&pts);
        self.overlays.set(
            Role::MeasureScatter,
            35.0,
            Item::Scatter {
                x: x.clone(),
                y: y.clone(),
                size: 11.0,
                pen: Pen::new([24, 180, 255, 250], 2.0),
                brush: [255, 255, 255, 235],
            },
        );
        let line = Pen::new([24, 180, 255, 255], 2.0);
        if pts.len() >= 2 {
            self.overlays.set(
                Role::MeasureLine,
                34.0,
                Item::Curve { x: x[..2].to_vec(), y: y[..2].to_vec(), pen: line, antialias: false },
            );
        } else if self.overlays.has(Role::MeasureLine) {
            self.overlays.set(
                Role::MeasureLine,
                34.0,
                Item::Curve { x: Vec::new(), y: Vec::new(), pen: line, antialias: false },
            );
        }
        let text = label.trim();
        let last = *pts.last().unwrap();
        if !text.is_empty() && pts.len() >= 2 {
            self.overlays.set(
                Role::MeasureText,
                36.0,
                Item::Text {
                    x: last.0,
                    y: last.1,
                    text: text.to_owned(),
                    color: Color32::WHITE,
                    font: TextFont::app(),
                },
            );
        } else if self.overlays.has(Role::MeasureText) {
            self.overlays.set(
                Role::MeasureText,
                36.0,
                Item::Text { x: last.0, y: last.1, text: String::new(), color: Color32::WHITE, font: TextFont::app() },
            );
        }
    }

    /// `clear_light_curve_overlay`.
    pub fn clear_light_curve_overlay(&mut self) {
        self.overlays.remove(|r| !r.is_light_curve());
    }

    /// `set_light_curve_overlays`: one curve per frequency, with its label.
    pub fn set_light_curve_overlays(&mut self, curves: &[LightCurveOverlay]) {
        self.clear_light_curve_overlay();
        for (i, c) in curves.iter().enumerate() {
            if c.time.is_empty() || c.time.len() != c.y.len() {
                continue;
            }
            let mut color = color_or(&c.color, [0x00, 0xe5, 0xff, 255]);
            color[3] = (c.opacity.clamp(0.0, 1.0) * 255.0).round() as u8;
            let style = match c.line_style.to_lowercase().as_str() {
                "dashed" => PenStyle::Dash,
                "dotted" => PenStyle::Dot,
                _ => PenStyle::Solid,
            };
            let pen = Pen::new(color, c.line_width.max(0.5)).styled(style);
            self.overlays.set(
                Role::LightCurve(i),
                19.0,
                Item::Curve { x: c.time.clone(), y: c.y.clone(), pen, antialias: false },
            );
            let label = c.label.trim();
            if c.show_label && !label.is_empty() {
                let [r, g, b, _] = color_or(&c.color, [0x00, 0xe5, 0xff, 255]);
                self.overlays.set(
                    Role::LightCurveLabel(i),
                    20.0,
                    Item::Text {
                        x: c.label_x,
                        y: c.label_y,
                        text: label.to_owned(),
                        color: Color32::from_rgb(r, g, b),
                        font: TextFont::app(),
                    },
                );
            }
        }
    }

    /// `set_annotations`: the visible ones, as lines, closed polygons and labels.
    pub fn set_annotations(&mut self, annotations: &[Annotation]) {
        self.overlays.remove(|r| !matches!(r, Role::Annotation(_)));
        for (i, ann) in annotations.iter().enumerate() {
            if !ann.visible {
                continue;
            }
            let color = color_or(&ann.color, [0x00, 0xd4, 0xff, 255]);
            match ann.kind {
                AnnotationKind::Polygon | AnnotationKind::Line => {
                    if ann.points.len() < 2 {
                        continue;
                    }
                    let mut x: Vec<f64> = ann.points.iter().map(|p| p[0]).collect();
                    let mut y: Vec<f64> = ann.points.iter().map(|p| p[1]).collect();
                    if ann.kind == AnnotationKind::Polygon {
                        x.push(x[0]);
                        y.push(y[0]);
                    }
                    let pen = Pen::new(color, ann.line_width.max(1.0));
                    self.overlays.set(Role::Annotation(i), 20.0, Item::Curve { x, y, pen, antialias: false });
                }
                AnnotationKind::Text => {
                    let Some(p) = ann.points.first() else { continue };
                    let font = TextFont {
                        family: ann.font_family.trim().to_owned(),
                        px: ann.font_size.max(6) as f32,
                        bold: ann.font_bold,
                        italic: ann.font_italic,
                    };
                    let color = Color32::from_rgb(color[0], color[1], color[2]);
                    self.overlays.set(
                        Role::Annotation(i),
                        20.0,
                        Item::Text { x: p[0], y: p[1], text: ann.text.clone(), color, font },
                    );
                }
            }
        }
    }

    fn update_lasso_curve(&mut self) {
        let (x, y) = split(&self.capture.lasso);
        self.overlays.set(
            Role::Lasso,
            0.0,
            Item::Curve { x, y, pen: Pen::new([0, 212, 255, 255], 2.0), antialias: false },
        );
    }

    /// `_update_annotation_capture_overlay`.
    fn update_annotation_capture_overlay(&mut self, cursor: Option<(f64, f64)>) {
        let Some(Mode::Annotation(kind)) = self.capture.mode else { return };
        let points = self.capture.annotation.clone();
        let mut line: Vec<(f64, f64)> = Vec::new();
        let mut vertices = points.clone();
        match kind {
            AnnotationKind::Polygon => {
                line = points.clone();
                if let (Some(c), false) = (cursor, points.is_empty()) {
                    line.push(c);
                    if points.len() >= 2 {
                        line.push(points[0]);
                    }
                }
            }
            AnnotationKind::Line => {
                if !points.is_empty() {
                    if let Some(c) = cursor {
                        line = vec![points[0], c];
                    }
                    vertices = points[..1].to_vec();
                }
            }
            AnnotationKind::Text => vertices = cursor.into_iter().collect(),
        }
        let (lx, ly) = split(&line);
        self.overlays.set(
            Role::CaptureLine,
            0.0,
            Item::Curve { x: lx, y: ly, pen: Pen::new([0, 212, 255, 220], 2.0), antialias: false },
        );
        let (vx, vy) = split(&vertices);
        self.overlays.set(
            Role::CaptureVertices,
            0.0,
            Item::Scatter {
                x: vx,
                y: vy,
                size: 8.0,
                pen: Pen::new([255, 255, 255, 220], 1.0),
                brush: [0, 212, 255, 230],
            },
        );
    }

    fn finish_annotation_capture(&mut self, kind: AnnotationKind, points: Vec<(f64, f64)>) {
        self.capture.mode = None;
        self.clear_annotation_capture_overlay();
        self.apply_navigation_state();
        self.events.push(CanvasEvent::AnnotationCaptureFinished { kind, points });
    }

    fn cancel_annotation_capture(&mut self, kind: AnnotationKind) {
        self.capture.mode = None;
        self.clear_annotation_capture_overlay();
        self.apply_navigation_state();
        self.events.push(CanvasEvent::AnnotationCaptureCancelled(kind));
    }

    fn finish_lasso(&mut self) {
        if self.capture.lasso.len() >= 3 {
            self.events.push(CanvasEvent::LassoFinished(self.capture.lasso.clone()));
        }
        self.capture.mode = None;
        self.clear_lasso_overlay();
        self.apply_navigation_state();
    }

    fn finish_drift(&mut self) {
        self.events.push(CanvasEvent::DriftCaptureFinished(self.capture.drift.clone()));
        self.capture.mode = None;
    }

    fn finish_measurement(&mut self) {
        let points = self.capture.measurement.iter().take(2).copied().collect();
        self.events.push(CanvasEvent::MeasurementCaptureFinished(points));
        self.capture.mode = None;
        self.apply_navigation_state();
    }

    // ----------------------------------------------------------- mouse

    /// `_scene_to_plot_xy`: plot coordinates (MHz for y) of a canvas point
    /// over the PlotItem, extrapolated outside the view box.
    fn scene_xy(&self, c: (f64, f64), geo: &Geometry) -> Option<(f64, f64)> {
        if !geo.plot.contains(c) {
            return None;
        }
        let (x, y) = self.view_coords(c, geo.view);
        Some((x, self.data_y(y)))
    }

    /// The scene events the capture modes take: lasso presses and drags, line
    /// annotation drags, clicks and pointer moves. Returns whether the
    /// pointer move was consumed (no `mousePositionChanged` for it).
    pub(super) fn handle_capture_input(&mut self, ui: &Ui, resp: &Response, px: &Pixels, geo: &Geometry) -> bool {
        use egui::PointerButton::{Primary, Secondary};
        let (pressed_l, pressed_r, released_l, pointer, moved) = ui.input(|i| {
            (
                i.pointer.button_pressed(Primary),
                i.pointer.button_pressed(Secondary),
                i.pointer.button_released(Primary),
                i.pointer.latest_pos(),
                i.pointer.delta() != egui::Vec2::ZERO,
            )
        });
        let over = resp.hovered() || resp.contains_pointer();
        let c = pointer.map(|p| px.canvas(p));
        let mut move_consumed = false;
        if (pressed_l || pressed_r) && over {
            self.capture.press_consumed = false;
        }

        match self.capture.mode {
            Some(Mode::Lasso) => {
                if pressed_l
                    && over
                    && let Some(xy) = c.and_then(|c| self.scene_xy(c, geo))
                {
                    self.capture.lasso_drag = true;
                    self.capture.lasso = vec![xy];
                    self.update_lasso_curve();
                    self.capture.press_consumed = true;
                } else if pressed_r && over {
                    self.capture.mode = None;
                    self.clear_lasso_overlay();
                    self.apply_navigation_state();
                    self.capture.press_consumed = true;
                    return false;
                }
                if self.capture.lasso_drag {
                    move_consumed = true;
                    if moved
                        && !pressed_l
                        && let Some(xy) = c.and_then(|c| self.scene_xy(c, geo))
                        && self.capture.lasso.last().is_none_or(|l| !points_close(*l, xy))
                    {
                        self.capture.lasso.push(xy);
                        self.update_lasso_curve();
                    }
                    if released_l {
                        self.capture.lasso_drag = false;
                        if let Some(xy) = c.and_then(|c| self.scene_xy(c, geo)) {
                            self.capture.lasso.push(xy);
                            self.update_lasso_curve();
                        }
                        self.finish_lasso();
                    }
                }
            }
            Some(Mode::Annotation(AnnotationKind::Line)) => {
                if pressed_r && over {
                    self.capture.suppress_next_click = true;
                    self.cancel_annotation_capture(AnnotationKind::Line);
                    self.capture.press_consumed = true;
                    return false;
                }
                if pressed_l
                    && over
                    && let Some(c) = c
                    && let Some(xy) = self.scene_xy(c, geo)
                {
                    self.capture.line_press = Some(LinePress { at: c, xy, last: xy, active: false });
                } else if let (Some(mut press), Some(c)) = (self.capture.line_press, c) {
                    if moved {
                        let d = (c.0 - press.at.0).powi(2) + (c.1 - press.at.1).powi(2);
                        if press.active || d >= LINE_DRAG_DISTANCE_SQ {
                            press.active = true;
                            move_consumed = true;
                            if let Some(xy) = self.scene_xy(c, geo) {
                                press.last = xy;
                                self.capture.annotation = vec![press.xy];
                                self.capture.line_press = Some(press);
                                self.update_annotation_capture_overlay(Some(xy));
                            }
                            self.capture.line_press = Some(press);
                        }
                    }
                    if released_l {
                        let origin = press.xy;
                        let drag_point = self.scene_xy(c, geo).unwrap_or(press.last);
                        self.capture.line_press = None;
                        if press.active {
                            self.capture.suppress_next_click = true;
                            if points_close(origin, drag_point) {
                                self.clear_annotation_capture_overlay();
                            } else {
                                self.finish_annotation_capture(AnnotationKind::Line, vec![origin, drag_point]);
                            }
                            return true;
                        }
                    }
                }
            }
            _ => {}
        }

        // Clicks (`sigMouseClicked`): not for presses the filter took.
        let left = resp.clicked();
        let right = resp.secondary_clicked();
        if (left || right) && self.capture.press_consumed {
            self.capture.press_consumed = false;
        } else if left || right {
            let at = resp.interact_pointer_pos().or(pointer).map(|p| px.canvas(p));
            self.scene_click(at, geo, left, resp.double_clicked());
        }

        // `_on_scene_mouse_moved` refreshes the capture's rubber band.
        if !move_consumed
            && moved
            && over
            && matches!(self.capture.mode, Some(Mode::Annotation(_)))
            && let Some(xy) = c.and_then(|c| self.scene_xy(c, geo))
        {
            self.update_annotation_capture_overlay(Some(xy));
        }
        move_consumed
    }

    /// `_on_scene_mouse_clicked`.
    fn scene_click(&mut self, at: Option<(f64, f64)>, geo: &Geometry, left: bool, double: bool) {
        if self.capture.suppress_next_click {
            self.capture.suppress_next_click = false;
            return;
        }
        let Some((x, y)) = at.and_then(|c| self.scene_xy(c, geo)) else { return };
        match self.capture.mode {
            Some(Mode::Annotation(kind)) => {
                if !left {
                    if kind == AnnotationKind::Polygon && self.capture.annotation.len() >= 3 {
                        let points = self.capture.annotation.clone();
                        self.finish_annotation_capture(kind, points);
                    } else {
                        self.cancel_annotation_capture(kind);
                    }
                    return;
                }
                if kind == AnnotationKind::Text {
                    self.finish_annotation_capture(kind, vec![(x, y)]);
                    return;
                }
                if self.capture.annotation.last().is_none_or(|l| !points_close(*l, (x, y))) {
                    self.capture.annotation.push((x, y));
                }
                self.update_annotation_capture_overlay(Some((x, y)));
                let n = self.capture.annotation.len();
                if kind == AnnotationKind::Line && n >= 2 {
                    let points = self.capture.annotation[..2].to_vec();
                    self.finish_annotation_capture(kind, points);
                } else if kind == AnnotationKind::Polygon && double && n >= 3 {
                    let points = self.capture.annotation.clone();
                    self.finish_annotation_capture(kind, points);
                }
            }
            Some(Mode::Drift) => {
                if left {
                    self.capture.drift.push((x, y));
                    let points = self.capture.drift.clone();
                    self.show_drift_points(&points, false);
                    self.capture.drift = points;
                    self.events.push(CanvasEvent::DriftPointAdded { x, y });
                    if double && self.capture.drift.len() >= 2 {
                        self.finish_drift();
                    }
                } else {
                    self.finish_drift();
                }
            }
            Some(Mode::Measurement) => {
                if !left {
                    self.capture.mode = None;
                    self.clear_measurement();
                    self.apply_navigation_state();
                    return;
                }
                self.capture.measurement.push((x, y));
                let points = self.capture.measurement.clone();
                self.show_measurement(&points, "");
                if self.capture.measurement.len() >= 2 {
                    self.finish_measurement();
                }
            }
            Some(Mode::Lasso) => {}
            None => {
                if left {
                    self.events.push(CanvasEvent::PlotClicked { x, y });
                }
            }
        }
    }

    // --------------------------------------------------------- drawing

    /// The overlay items over the spectrogram, clipped to the view box.
    pub(super) fn draw_overlays(&mut self, ui: &Ui, painter: &egui::Painter, px: &Pixels, geo: &Geometry) {
        if self.overlays.entries.is_empty() {
            return;
        }
        let v = geo.view;
        // The view box's pixels, as the image fills them (and Qt clips).
        let (l, r) = (px.dev_x(v.x), px.dev_x(v.x + v.w));
        let (t, b) = (px.dev_y(v.y), px.dev_y(v.y + v.h));
        let first_x = (l - 0.5).ceil();
        let end_x = (r - 0.5).ceil();
        let first_y = (t - 0.5).floor() + 1.0;
        let end_y = (b - 0.5).floor() + 1.0;
        let (nx, ny) = ((end_x - first_x).max(0.0) as usize, (end_y - first_y).max(0.0) as usize);
        if nx == 0 || ny == 0 {
            return;
        }
        let ppp = px.ppp;
        let (ox, oy) = px.origin;
        // Canvas-local device pixels, as Qt draws into the canvas' own image.
        let device = ((geo.size.0 * ppp).round() as usize, (geo.size.1 * ppp).round() as usize);
        let [x0, x1, y0, y1] = self.range;
        let (sx, sy) = (v.w / (x1 - x0), v.h / (y1 - y0));
        let log = self.log_freq;
        let to_scene = move |x: f64, y_data: f64| -> (f64, f64) {
            let ay = if !log {
                y_data
            } else if y_data > 0.0 {
                y_data.log10()
            } else {
                f64::NAN
            };
            (v.x + (x - x0) * sx, v.y + (y1 - ay) * sy)
        };
        let to_device = |x: f64, y_data: f64| -> (f64, f64) {
            let (a, b) = to_scene(x, y_data);
            (a * ppp, b * ppp)
        };

        // Lines and symbols between text items share one layer.
        enum Batch<'a> {
            Raster(Vec<&'a Entry>),
            Text(&'a Entry),
        }
        let sorted = self.overlays.sorted();
        let mut batches: Vec<Batch<'_>> = Vec::new();
        for e in sorted {
            match &e.item {
                Item::Text { .. } => batches.push(Batch::Text(e)),
                _ => match batches.last_mut() {
                    Some(Batch::Raster(list)) => list.push(e),
                    _ => batches.push(Batch::Raster(vec![e])),
                },
            }
        }

        let key = format!(
            "{}|{:?}|{first_x},{first_y},{nx},{ny}|{ox},{oy}|{ppp}|{:?}",
            self.overlays.generation, self.range, geo.size
        );
        let fresh = self.overlay_cache.key != key;
        if fresh {
            self.overlay_cache.key = key;
            self.overlay_cache.textures.resize_with(batches.len(), || None);
        }
        let local_x = first_x - ox;
        let local_y = first_y - oy;
        let rect = px.device_rect(first_x, first_y, nx as f64, ny as f64);
        let clipped = painter.with_clip_rect(rect);
        for (i, batch) in batches.iter().enumerate() {
            match batch {
                Batch::Raster(list) => {
                    if fresh {
                        let image =
                            Raster::new(nx, ny, local_x as i32, local_y as i32, device).and_then(|mut raster| {
                                for e in list {
                                    draw_item(&mut raster, &e.item, &to_device, ppp);
                                }
                                (!raster.is_blank()).then(|| raster.into_image())
                            });
                        let slot = &mut self.overlay_cache.textures[i];
                        match image {
                            Some(img) => {
                                let color = ColorImage::from_rgba_premultiplied(img.size, img.as_bytes());
                                match slot {
                                    Some(t) => t.set(color, TextureOptions::NEAREST),
                                    None => {
                                        *slot = Some(ui.ctx().load_texture(
                                            format!("plot-overlay-{i}"),
                                            color,
                                            TextureOptions::NEAREST,
                                        ))
                                    }
                                }
                            }
                            None => *slot = None,
                        }
                    }
                    if let Some(Some(t)) = self.overlay_cache.textures.get(i) {
                        clipped.image(t.id(), rect, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
                    }
                }
                Batch::Text(e) => {
                    if let Item::Text { x, y, text, color, font } = &e.item {
                        let (a, b) = to_scene(*x, *y);
                        draw_text_item(ui, &clipped, px, (a, b), text, *color, &font.font_id());
                    }
                }
            }
        }
    }
}

/// One line or symbol item into `raster`.
fn draw_item(raster: &mut Raster, item: &Item, to_device: &dyn Fn(f64, f64) -> (f64, f64), dpr: f64) {
    match item {
        Item::Curve { x, y, pen, antialias } => {
            if x.is_empty() {
                return;
            }
            let pts: Vec<(f64, f64)> = x.iter().zip(y).map(|(&a, &b)| to_device(a, b)).collect();
            if *antialias {
                raster.curve_antialiased(&pts, pen);
            } else {
                raster.curve(&pts, pen);
            }
        }
        Item::Scatter { x, y, size, pen, brush } => {
            let pts: Vec<(f64, f64)> = x.iter().zip(y).map(|(&a, &b)| to_device(a, b)).collect();
            raster.circles(&pts, *size, pen, *brush, dpr);
        }
        Item::Text { .. } => {}
    }
}

/// A `TextItem` with anchor (0, 1): the text document's bottom-left corner
/// at `at` (canvas coordinates), each line a font height below the last.
fn draw_text_item(
    ui: &Ui,
    painter: &egui::Painter,
    px: &Pixels,
    at: (f64, f64),
    text: &str,
    color: Color32,
    font: &FontId,
) {
    if text.is_empty() || !(at.0.is_finite() && at.1.is_finite()) {
        return;
    }
    let lines: Vec<&str> = text.split('\n').collect();
    let line_h = crate::fonts::qt_metrics(font)
        .map_or_else(|| ui.ctx().fonts_mut(|f| f.row_height(font)) as f64, |m| m.height() as f64);
    let doc_h = (lines.len() as f64 * line_h - 1e-3).ceil() + 2.0 * DOC_MARGIN;
    let top = at.1 - doc_h;
    for (i, line) in lines.iter().enumerate() {
        if line.is_empty() {
            continue;
        }
        let g: Arc<egui::Galley> = galley(ui, line, font, color);
        let m = metrics(&g, font);
        let pos: Pos2 = px.text_origin(at.0 + DOC_MARGIN, top + DOC_MARGIN + i as f64 * line_h, &m);
        painter.galley(pos, g, color);
    }
}
