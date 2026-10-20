//! The plot: the full app's hardware canvas (`AcceleratedPlotWidget` in
//! `src/ui/widgets/accelerated_plot_widget.py`, drawn by pyqtgraph 0.14.0).
//!
//! The layout, axes, colour bar, title and mouse handling follow pyqtgraph's
//! `GraphicsLayout`, `PlotItem`, `AxisItem`, `ColorBarItem` and `ViewBox`, so
//! the canvas looks and behaves like the full app's. Tick logic and image
//! colouring live in `callisto_core::plot`; this module lays them out and
//! draws them, snapping lines to physical pixels as Qt's raster engine does.

mod overlays;

use std::time::{Duration, Instant};

use callisto_core::image::RgbaImage;
use callisto_core::plot::image::{ImageData, Placement, render, render_colorbar};
use callisto_core::plot::{Axis, AxisDraw, Colormap, Line, Orientation, TextAlign, TickText};
use callisto_core::radio::array::Array2;
use callisto_core::radio::frequency_axis::{
    axis_edges, finite_data_limits, format_frequency_mhz, log_frequency_ticks_mhz, resample_row_mask_to_log_frequency,
    resample_rows_to_log_frequency,
};
use egui::epaint::TextShape;
use egui::{
    Color32, ColorImage, CornerRadius, FontId, Galley, Id, Pos2, Rect, Sense, Shape, Stroke, StrokeKind, TextureHandle,
    TextureOptions, Ui, pos2, vec2,
};
use std::sync::Arc;

use crate::fonts::{graph_font, plot_font};
use callisto_core::analysis::annotations::AnnotationKind;
pub use overlays::Mode;

/// `GraphicsLayout` contents margins and the `PlotItem`'s own.
const LAYOUT_MARGIN: f64 = 9.0;
const PLOT_MARGIN: f64 = 1.0;
/// `PlotItem.setTitle`: the title row is 30 px whenever a title is set.
const TITLE_H: f64 = 30.0;
/// `ColorBarItem.setImageItem(insert_in=…)`: column 4 is fixed at 5 px.
const COLORBAR_GAP: f64 = 5.0;
/// `ColorBarItem(width=25)`, its axis `setWidth(45)`, and its 1 px margins.
const BAR_W: f64 = 25.0;
const BAR_AXIS_W: f64 = 45.0;
const COLORBAR_W: f64 = 1.0 + BAR_W + BAR_AXIS_W + 1.0;
/// `QGraphicsTextItem`'s document margin around label and title text.
const DOC_MARGIN: f64 = 4.0;
/// pyqtgraph's default foreground, which the colour bar's frame keeps.
const FRAME_GREY: Color32 = Color32::from_rgb(150, 150, 150);
/// `AcceleratedPlotWidget._interaction_timer`.
const INTERACTION_DELAY: Duration = Duration::from_millis(140);
/// `ViewBox` `wheelScaleFactor` and Qt's angle delta per wheel notch.
const WHEEL_SCALE: f64 = -1.0 / 8.0;
const WHEEL_NOTCH: f64 = 120.0;

/// A visible range in data units (MHz for y, also on a log axis).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct View {
    pub xlim: (f64, f64),
    pub ylim: (f64, f64),
}

impl View {
    /// `_views_close`.
    pub fn close_to(&self, other: &View, tol: f64) -> bool {
        (self.xlim.0 - other.xlim.0).abs() <= tol
            && (self.xlim.1 - other.xlim.1).abs() <= tol
            && (self.ylim.0 - other.ylim.0).abs() <= tol
            && (self.ylim.1 - other.ylim.1).abs() <= tol
    }
}

/// `set_text_style`: plot fonts in pixels, with bold and italic flags.
#[derive(Clone, Debug, PartialEq)]
pub struct TextStyle {
    /// Graph Properties → Font family; `None` is the app font.
    pub family: Option<String>,
    pub tick_px: u32,
    pub axis_px: u32,
    pub title_px: u32,
    pub title_bold: bool,
    pub title_italic: bool,
    pub axis_bold: bool,
    pub axis_italic: bool,
    pub ticks_bold: bool,
    pub ticks_italic: bool,
}

impl Default for TextStyle {
    /// The widget's own defaults, before the main window styles it.
    fn default() -> Self {
        Self {
            family: None,
            tick_px: 11,
            axis_px: 12,
            title_px: 14,
            title_bold: false,
            title_italic: false,
            axis_bold: false,
            axis_italic: false,
            ticks_bold: false,
            ticks_italic: false,
        }
    }
}

impl TextStyle {
    fn font(&self, px: u32, bold: bool, italic: bool) -> FontId {
        let px = px.max(1) as f32;
        if self.family.is_some() { graph_font(px, bold, italic) } else { plot_font(px, bold, italic) }
    }

    fn tick_font(&self) -> FontId {
        self.font(self.tick_px, self.ticks_bold, self.ticks_italic)
    }

    fn axis_font(&self) -> FontId {
        self.font(self.axis_px, self.axis_bold, self.axis_italic)
    }

    fn title_font(&self) -> FontId {
        self.font(self.title_px, self.title_bold, self.title_italic)
    }
}

/// Everything `update_image` takes.
pub struct ImageUpdate<'a> {
    pub data: Arc<Array2>,
    /// `pyqtgraph_extent`: `[x0, x1, y of row 0's outer edge, y of the last row's]`.
    pub extent: [f64; 4],
    pub cmap: Colormap,
    pub gap_rows: Option<&'a [bool]>,
    pub levels: Option<(f64, f64)>,
    pub title: String,
    pub x_label: String,
    pub y_label: String,
    pub colorbar_label: String,
    pub view: Option<View>,
    pub freqs: &'a [f64],
    pub log_freq: bool,
}

/// What the canvas reports to the window.
#[derive(Clone, Debug, PartialEq)]
pub enum CanvasEvent {
    /// `mousePositionChanged`: plot coordinates under the pointer (MHz for y).
    MouseMoved { x: f64, y: f64, inside: bool },
    /// `viewInteractionFinished`, 140 ms after a pan or zoom stops.
    ViewInteractionFinished { start: View, end: View },
    /// `rectZoomFinished`.
    RectZoomFinished { start: View, end: View },
    /// `plotClicked`: a left click with no capture running.
    PlotClicked { x: f64, y: f64 },
    /// `lassoFinished`: the lasso's vertices (at least three).
    LassoFinished(Vec<(f64, f64)>),
    /// `driftPointAdded`.
    DriftPointAdded { x: f64, y: f64 },
    /// `driftCaptureFinished`: every point clicked.
    DriftCaptureFinished(Vec<(f64, f64)>),
    /// `measurementCaptureFinished`: the two points.
    MeasurementCaptureFinished(Vec<(f64, f64)>),
    /// `annotationCaptureFinished`: the polygon's vertices, the line's ends
    /// or the text's position.
    AnnotationCaptureFinished { kind: AnnotationKind, points: Vec<(f64, f64)> },
    /// `annotationCaptureCancelled`.
    AnnotationCaptureCancelled(AnnotationKind),
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum DragKind {
    Pan,
    Scale,
    RectZoom,
}

#[derive(Clone, Copy, Debug)]
struct Drag {
    kind: DragKind,
    /// `None` for the view box, `Some(0)` for the x axis, `Some(1)` for y.
    axis: Option<usize>,
    /// Press position in canvas coordinates.
    start: (f64, f64),
    last: (f64, f64),
}

/// Rectangles in canvas coordinates (logical pixels from the canvas' top-left).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct R {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

impl R {
    fn new(x: f64, y: f64, w: f64, h: f64) -> Self {
        Self { x, y, w, h }
    }

    fn contains(&self, p: (f64, f64)) -> bool {
        p.0 >= self.x && p.0 <= self.x + self.w && p.1 >= self.y && p.1 <= self.y + self.h
    }
}

/// The `PlotItem` grid, as Qt's layout resolves it.
#[derive(Clone, Copy, Debug, Default)]
struct Geometry {
    /// The whole canvas (the graphics view), logical pixels.
    size: (f64, f64),
    plot: R,
    title: R,
    left: R,
    bottom: R,
    view: R,
    bar: R,
    bar_axis: R,
}

fn layout(w: f64, h: f64, left_w: f64, bottom_h: f64) -> Geometry {
    let plot =
        R::new(LAYOUT_MARGIN, LAYOUT_MARGIN, (w - 2.0 * LAYOUT_MARGIN).max(0.0), (h - 2.0 * LAYOUT_MARGIN).max(0.0));
    let inner_x = plot.x + PLOT_MARGIN;
    let inner_y = plot.y + PLOT_MARGIN;
    let inner_w = (plot.w - 2.0 * PLOT_MARGIN).max(0.0);
    let inner_h = (plot.h - 2.0 * PLOT_MARGIN).max(0.0);
    let main_h = (inner_h - TITLE_H - bottom_h).max(0.0);
    let view_w = (inner_w - left_w - COLORBAR_GAP - COLORBAR_W).max(0.0);
    let main_y = inner_y + TITLE_H;
    let view = R::new(inner_x + left_w, main_y, view_w, main_h);
    let colorbar = R::new(view.x + view_w + COLORBAR_GAP, main_y, COLORBAR_W, main_h);
    let bar_h = (main_h - 2.0).max(0.0);
    Geometry {
        size: (w, h),
        plot,
        title: R::new(view.x, inner_y, view_w, TITLE_H),
        left: R::new(inner_x, main_y, left_w, main_h),
        bottom: R::new(view.x, main_y + main_h, view_w, bottom_h),
        view,
        bar: R::new(colorbar.x + 1.0, main_y + 1.0, BAR_W, bar_h),
        bar_axis: R::new(colorbar.x + 1.0 + BAR_W, main_y + 1.0, BAR_AXIS_W, bar_h),
    }
}

/// The cached screen image of the spectrogram or the colour bar.
struct Layer {
    texture: TextureHandle,
    key: String,
}

pub struct PlotCanvas {
    left: Axis,
    bottom: Axis,
    bar_axis: Axis,
    image: Option<ImageData>,
    image_generation: u64,
    levels: (f64, f64),
    cmap: Colormap,
    /// Plot-coordinate range `[x0, x1, y0, y1]` (log10 MHz on a log axis).
    range: [f64; 4],
    full_view: Option<View>,
    axis_ylim: Option<(f64, f64)>,
    log_freq: bool,
    title: String,
    x_label: String,
    y_label: String,
    colorbar_label: String,
    text: TextStyle,
    dark: bool,
    navigation_locked: bool,
    rect_zoom_once: bool,
    drag: Option<Drag>,
    interaction_start: Option<View>,
    last_range_change: Option<Instant>,
    events: Vec<CanvasEvent>,
    image_layer: Option<Layer>,
    bar_layer: Option<Layer>,
    overlays: overlays::Overlays,
    capture: overlays::Capture,
    overlay_cache: overlays::LayerCache,
    /// How the canvas was last laid out on screen.
    last_layout: Option<(Pixels, Geometry)>,
    /// The widget id for the mouse (several canvases can share a window).
    id: Id,
}

impl Default for PlotCanvas {
    fn default() -> Self {
        Self::new()
    }
}

impl PlotCanvas {
    /// `DEFAULT_X_LABEL` and `DEFAULT_Y_LABEL`.
    pub const DEFAULT_X_LABEL: &'static str = "Time [s]";
    pub const DEFAULT_Y_LABEL: &'static str = "Frequency [MHz]";

    pub fn new() -> Self {
        let mut bar_axis = Axis::new(Orientation::Right);
        bar_axis.fixed_width = Some(BAR_AXIS_W);
        let mut canvas = Self {
            left: Axis::new(Orientation::Left),
            bottom: Axis::new(Orientation::Bottom),
            bar_axis,
            image: None,
            image_generation: 0,
            levels: (0.0, 1.0),
            cmap: Colormap::by_name("viridis"),
            // An empty ViewBox auto-ranges its (0, 1) start to ±0.5 around zero.
            range: [-0.5, 0.5, -0.5, 0.5],
            full_view: None,
            axis_ylim: None,
            log_freq: false,
            title: String::new(),
            x_label: Self::DEFAULT_X_LABEL.into(),
            y_label: Self::DEFAULT_Y_LABEL.into(),
            colorbar_label: String::new(),
            text: TextStyle::default(),
            dark: false,
            navigation_locked: false,
            rect_zoom_once: false,
            drag: None,
            interaction_start: None,
            last_range_change: None,
            events: Vec::new(),
            image_layer: None,
            bar_layer: None,
            overlays: Default::default(),
            capture: Default::default(),
            overlay_cache: Default::default(),
            last_layout: None,
            id: Id::new("plot_canvas"),
        };
        canvas.apply_labels();
        canvas
    }

    /// A canvas with its own widget id, for windows that show several.
    pub fn with_id(mut self, id: Id) -> Self {
        self.id = id;
        self
    }

    /// Events since the last call.
    pub fn take_events(&mut self) -> Vec<CanvasEvent> {
        std::mem::take(&mut self.events)
    }

    pub fn has_image(&self) -> bool {
        self.image.is_some()
    }

    pub fn set_dark(&mut self, dark: bool) {
        self.dark = dark;
    }

    fn background(&self) -> Color32 {
        if self.dark { Color32::from_rgb(0x11, 0x11, 0x11) } else { Color32::WHITE }
    }

    fn foreground(&self) -> Color32 {
        if self.dark { Color32::from_rgb(0xf2, 0xf2, 0xf2) } else { Color32::from_rgb(0x10, 0x10, 0x10) }
    }

    pub fn set_text_style(&mut self, style: TextStyle) {
        self.text = style;
    }

    pub fn text_style(&self) -> &TextStyle {
        &self.text
    }

    /// `set_time_mode`: UT tick labels once there is a start time.
    pub fn set_time_mode(&mut self, use_utc: bool, ut_start_sec: Option<f64>) {
        self.bottom.text = match (use_utc, ut_start_sec) {
            (true, Some(start)) => TickText::Ut(start),
            _ => TickText::Numbers,
        };
    }

    fn apply_labels(&mut self) {
        self.left.label_text = self.y_label.clone();
        self.bottom.label_text = self.x_label.clone();
        self.bar_axis.label_text = self.colorbar_label.clone();
        self.sync_axis_ranges();
    }

    fn sync_axis_ranges(&mut self) {
        self.bottom.set_range(self.range[0], self.range[1]);
        self.left.set_range(self.range[2], self.range[3]);
        self.bar_axis.set_range(self.levels.0, self.levels.1);
    }

    // ------------------------------------------------------- coordinates

    fn data_y(&self, y: f64) -> f64 {
        if self.log_freq { 10f64.powf(y) } else { y }
    }

    fn axis_y(&self, y: f64) -> f64 {
        if !self.log_freq {
            y
        } else if y > 0.0 {
            y.log10()
        } else {
            f64::NAN
        }
    }

    /// Where plot coordinates (seconds, MHz) were on screen when last drawn.
    pub fn screen_pos(&self, x: f64, y: f64) -> Option<Pos2> {
        let (px, geo) = self.last_layout?;
        let v = geo.view;
        let [x0, x1, y0, y1] = self.range;
        let cx = v.x + (x - x0) / (x1 - x0) * v.w;
        let cy = v.y + (y1 - self.axis_y(y)) / (y1 - y0) * v.h;
        Some(px.point(cx, cy))
    }

    /// `get_view`.
    pub fn view(&self) -> Option<View> {
        self.image.as_ref()?;
        Some(View {
            xlim: (self.range[0], self.range[1]),
            ylim: (self.data_y(self.range[2]), self.data_y(self.range[3])),
        })
    }

    pub fn full_view(&self) -> Option<View> {
        self.full_view
    }

    /// `set_view`: a programmatic change, which does not count as an interaction.
    pub fn set_view(&mut self, view: View) {
        let mut lo = self.axis_y(view.ylim.0);
        let mut hi = self.axis_y(view.ylim.1);
        if !(lo.is_finite() && hi.is_finite()) {
            let Some((a, b)) = self.axis_ylim else { return };
            (lo, hi) = (a, b);
        }
        let x = set_range_axis(view.xlim, 0.0, self.range[1] - self.range[0]);
        let y = set_range_axis((lo, hi), 0.0, self.range[3] - self.range[2]);
        self.range = [x.0, x.1, y.0, y.1];
        self.sync_axis_ranges();
    }

    /// A range change from the mouse: starts or extends the interaction timer.
    fn set_range_interactive(&mut self, range: [f64; 4]) {
        if self.interaction_start.is_none() {
            self.interaction_start = self.view();
        }
        self.range = range;
        self.sync_axis_ranges();
        self.last_range_change = Some(Instant::now());
    }

    // ----------------------------------------------------------- content

    /// `update_image`.
    pub fn update_image(&mut self, u: ImageUpdate<'_>) {
        if u.data.rows == 0 || u.data.cols == 0 {
            return;
        }
        let [x0, x1, mut y0, mut y1] = u.extent;
        let mut data: Option<Array2> = None;
        let mut gap: Option<Vec<bool>> = u.gap_rows.map(<[bool]>::to_vec);

        self.log_freq = false;
        if u.log_freq && !u.freqs.is_empty() {
            let (edge_lo, edge_hi) = (y0.min(y1), y0.max(y1));
            let bounds = (edge_lo > 0.0 && edge_hi > edge_lo).then_some((edge_lo, edge_hi));
            if let Some((resampled, log_rows)) = resample_rows_to_log_frequency(&u.data, u.freqs, 0, bounds) {
                match bounds {
                    None => {
                        let edges = axis_edges(&log_rows, 1.0);
                        (y0, y1) = (edges[0], *edges.last().unwrap());
                    }
                    Some((lo, hi)) => {
                        let (low, high) = (lo.log10(), hi.log10());
                        (y0, y1) = if log_rows[0] > *log_rows.last().unwrap() { (high, low) } else { (low, high) };
                    }
                }
                gap = resample_row_mask_to_log_frequency(gap.as_deref(), u.freqs, 0, bounds);
                data = Some(resampled);
                self.log_freq = true;
            }
        }
        let data = data.map_or_else(|| u.data.clone(), Arc::new);

        self.axis_ylim = Some((y0.min(y1), y0.max(y1)));
        self.full_view =
            Some(View { xlim: (x0.min(x1), x0.max(x1)), ylim: (self.data_y(y0.min(y1)), self.data_y(y0.max(y1))) });
        self.apply_log_frequency_ticks();

        // Thresholds win over the data's own range, which is then not needed.
        let limits = match u.levels {
            Some((lo, hi)) if lo.is_finite() && hi.is_finite() && hi > lo => Some((lo, hi)),
            _ => finite_data_limits(&data.data),
        };
        match limits {
            None => self.image = None,
            Some(levels) => {
                let previous = self.image.take();
                self.image = Some(ImageData::reusing(data, [x0, y0, x1 - x0, y1 - y0], gap, previous.as_ref()));
                self.levels = levels;
            }
        }
        self.image_generation += 1;
        self.cmap = u.cmap;

        self.title = u.title;
        self.x_label = u.x_label;
        self.y_label = u.y_label;
        self.colorbar_label = u.colorbar_label;
        self.apply_labels();

        if let Some(view) = u.view {
            self.set_view(view);
        } else if let Some(full) = self.full_view {
            self.set_view(full);
        }
    }

    /// `_apply_log_frequency_ticks`.
    fn apply_log_frequency_ticks(&mut self) {
        self.left.explicit_ticks = None;
        let (true, Some((lo, hi))) = (self.log_freq, self.axis_ylim) else { return };
        let (labelled, minor) = log_frequency_ticks_mhz(10f64.powf(lo), 10f64.powf(hi), 10);
        if labelled.is_empty() {
            return;
        }
        self.left.explicit_ticks = Some(vec![
            labelled.iter().map(|f| (f.log10(), format_frequency_mhz(*f))).collect(),
            minor.iter().map(|f| (f.log10(), String::new())).collect(),
        ]);
    }

    /// `clear`: back to the empty plot. The text style stays as it was.
    pub fn clear(&mut self) {
        self.image = None;
        self.image_generation += 1;
        self.title.clear();
        self.x_label = Self::DEFAULT_X_LABEL.into();
        self.y_label = Self::DEFAULT_Y_LABEL.into();
        self.colorbar_label.clear();
        self.full_view = None;
        self.axis_ylim = None;
        self.log_freq = false;
        self.left.explicit_ticks = None;
        self.levels = (0.0, 1.0);
        self.cmap = Colormap::by_name("viridis");
        self.range = [-0.5, 0.5, -0.5, 0.5];
        self.drag = None;
        self.interaction_start = None;
        self.last_range_change = None;
        self.reset_overlays();
        self.apply_labels();
    }

    // -------------------------------------------------------- navigation

    /// The spectrogram as the screen would show it at `width`×`height` device
    /// pixels for the current view (benchmarks and tests).
    pub fn render_view(&self, width: usize, height: usize) -> Option<RgbaImage> {
        let img = self.image.as_ref()?;
        let place = Placement { left: 0.0, top: 0.0, width: width as f64, height: height as f64, view: self.range };
        let bg = self.background();
        Some(render(img, self.levels, &self.cmap, &place, width, height, [bg.r(), bg.g(), bg.b()]))
    }

    /// `set_navigation_locked`.
    pub fn set_navigation_locked(&mut self, locked: bool) {
        self.navigation_locked = locked;
    }

    fn mouse_enabled(&self) -> bool {
        !self.capture.mouse_suspended && (!self.navigation_locked || self.rect_zoom_once)
    }

    /// `start_rect_zoom_once`: the next left drag zooms to a rectangle.
    pub fn start_rect_zoom_once(&mut self) {
        self.rect_zoom_once = true;
        self.interaction_start = self.view();
    }

    /// `cancel_rect_zoom`.
    pub fn cancel_rect_zoom(&mut self) {
        self.rect_zoom_once = false;
        self.interaction_start = None;
        self.last_range_change = None;
        self.drag = None;
    }

    pub fn rect_zoom_active(&self) -> bool {
        self.rect_zoom_once
    }

    /// `_emit_interaction_finished`, once the 140 ms timer has run out.
    fn poll_interaction_timer(&mut self, ui: &Ui) {
        let Some(changed) = self.last_range_change else { return };
        let elapsed = changed.elapsed();
        if elapsed < INTERACTION_DELAY {
            ui.ctx().request_repaint_after(INTERACTION_DELAY - elapsed);
            return;
        }
        self.last_range_change = None;
        let start = self.interaction_start.take();
        let end = self.view();
        let (Some(start), Some(end)) = (start, end) else { return };
        if start.close_to(&end, 1e-6) {
            return;
        }
        if self.rect_zoom_once {
            self.rect_zoom_once = false;
            self.events.push(CanvasEvent::RectZoomFinished { start, end });
        } else {
            self.events.push(CanvasEvent::ViewInteractionFinished { start, end });
        }
    }

    // ------------------------------------------------------------ drawing

    /// Draw the canvas into `rect` and handle the mouse over it.
    pub fn show(&mut self, ui: &mut Ui, rect: Rect) {
        let ppp = ui.ctx().pixels_per_point() as f64;
        let px = Pixels::new(rect, ppp);
        let (w, h) = (rect.width() as f64, rect.height() as f64);
        let painter = ui.painter_at(rect);
        painter.rect_filled(rect, CornerRadius::ZERO, self.background());

        let fg = self.foreground();
        let tick_font = self.text.tick_font();
        let axis_font = self.text.axis_font();
        let measure_tick = |s: &str| {
            let m = metrics(&galley(ui, s, &tick_font, fg), &tick_font);
            (m.width, m.line_height)
        };
        let label_box = |axis: &Axis| -> (f64, f64) {
            if !axis.label_visible() {
                return (8.0, 24.0);
            }
            let m = metrics(&galley(ui, &axis.label_string(), &axis_font, fg), &axis_font);
            (m.width + 2.0 * DOC_MARGIN, document_height(m.line_height))
        };

        // Lay out, draw the axes' specs, and lay out again while the text
        // space they keep changes (pyqtgraph does this over repaints).
        let mut geo = Geometry::default();
        let mut left_draw = AxisDraw::default();
        let mut bottom_draw = AxisDraw::default();
        for _ in 0..4 {
            let left_w = self.left.width(label_box(&self.left).1);
            let bottom_h = self.bottom.height(label_box(&self.bottom).1);
            geo = layout(w, h, left_w, bottom_h);
            left_draw = self.left.generate(geo.left.w, geo.left.h, &measure_tick);
            bottom_draw = self.bottom.generate(geo.bottom.w, geo.bottom.h, &measure_tick);
            let stable = self.left.width(label_box(&self.left).1) == left_w
                && self.bottom.height(label_box(&self.bottom).1) == bottom_h;
            if stable {
                break;
            }
        }
        let bar_draw = self.bar_axis.generate(geo.bar_axis.w, geo.bar_axis.h, &measure_tick);
        self.last_layout = Some((px, geo));

        self.handle_mouse(ui, rect, &px, &geo);
        self.poll_interaction_timer(ui);

        // The spectrogram, clipped to the view box.
        if self.image.is_some() {
            self.draw_image(ui, &painter, &px, &geo);
        }
        self.draw_overlays(ui, &painter, &px, &geo);

        draw_axis(ui, &painter, &px, geo.left, &left_draw, &tick_font, fg, fg);
        draw_axis(ui, &painter, &px, geo.bottom, &bottom_draw, &tick_font, fg, fg);
        self.draw_axis_label(ui, &painter, &px, &self.left, geo.left, &axis_font, fg);
        self.draw_axis_label(ui, &painter, &px, &self.bottom, geo.bottom, &axis_font, fg);
        self.draw_title(ui, &painter, &px, geo.title, fg);

        self.draw_colorbar(ui, &painter, &px, &geo);
        // The colour bar's frame: its left, top and bottom axes, lines only.
        let bar = geo.bar;
        let frame = |line: Line| draw_line(&painter, &px, bar.x, bar.y, line, FRAME_GREY);
        frame(Line { x1: -1.0, y1: -1.0, x2: -1.0, y2: bar.h + 1.0, alpha: 255 });
        frame(Line { x1: -1.0, y1: -1.0, x2: bar.w + 1.0, y2: -1.0, alpha: 255 });
        frame(Line { x1: -1.0, y1: bar.h + 1.0, x2: bar.w + 1.0, y2: bar.h + 1.0, alpha: 255 });
        draw_axis(ui, &painter, &px, geo.bar_axis, &bar_draw, &tick_font, fg, fg);
        self.draw_axis_label(ui, &painter, &px, &self.bar_axis, geo.bar_axis, &axis_font, fg);

        // The rectangle-zoom rubber band (`ViewBox.rbScaleBox`).
        if let Some(d) = self.drag
            && d.kind == DragKind::RectZoom
        {
            let (x0, y0) = (d.start.0.min(d.last.0), d.start.1.min(d.last.1));
            let (x1, y1) = (d.start.0.max(d.last.0), d.start.1.max(d.last.1));
            let r = Rect::from_min_max(px.point(x0, y0), px.point(x1, y1));
            painter.rect(
                r,
                CornerRadius::ZERO,
                Color32::from_rgba_unmultiplied(255, 255, 0, 100),
                Stroke::new(1.0 / ppp as f32, Color32::from_rgb(255, 255, 100)),
                StrokeKind::Inside,
            );
        }
    }

    fn draw_image(&mut self, ui: &Ui, painter: &egui::Painter, px: &Pixels, geo: &Geometry) {
        let Some(img) = &self.image else { return };
        let v = geo.view;
        // Device pixels whose centres fall inside the view box: [left, right) across, (top, bottom] down.
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
        let bg = self.background();
        let key = format!(
            "{}|{:?}|{}|{:?}|{first_x},{first_y},{nx},{ny},{l},{t},{r},{b}|{:?}",
            self.image_generation, self.levels, self.cmap.name, self.range, bg
        );
        let place = Placement { left: l - first_x, top: t - first_y, width: r - l, height: b - t, view: self.range };
        let layer = cached_layer(ui, &mut self.image_layer, "plot-image", key, || {
            render(img, self.levels, &self.cmap, &place, nx, ny, [bg.r(), bg.g(), bg.b()])
        });
        let rect = px.device_rect(first_x, first_y, nx as f64, ny as f64);
        painter.image(layer, rect, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
    }

    fn draw_colorbar(&mut self, ui: &Ui, painter: &egui::Painter, px: &Pixels, geo: &Geometry) {
        let bar = geo.bar;
        // The bar pixmap fills pixel centres in (left, right] and (top, bottom].
        let (l, r) = (px.dev_x(bar.x), px.dev_x(bar.x + bar.w));
        let (t, b) = (px.dev_y(bar.y), px.dev_y(bar.y + bar.h));
        let first_x = (l - 0.5).floor() + 1.0;
        let end_x = (r - 0.5).floor() + 1.0;
        let first_y = (t - 0.5).floor() + 1.0;
        let end_y = (b - 0.5).floor() + 1.0;
        let (nx, ny) = ((end_x - first_x).max(0.0) as usize, (end_y - first_y).max(0.0) as usize);
        if nx == 0 || ny == 0 {
            return;
        }
        let key = format!("{}|{first_x},{first_y},{nx},{ny},{t},{b}", self.cmap.name);
        let cmap = self.cmap;
        let layer = cached_layer(ui, &mut self.bar_layer, "colorbar", key, || {
            render_colorbar(&cmap, nx, ny, t - first_y, b - t)
        });
        let rect = px.device_rect(first_x, first_y, nx as f64, ny as f64);
        painter.image(layer, rect, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
    }

    /// The axis label: a `QGraphicsTextItem`, rotated -90° on vertical axes.
    #[allow(clippy::too_many_arguments)]
    fn draw_axis_label(
        &self,
        ui: &Ui,
        painter: &egui::Painter,
        px: &Pixels,
        axis: &Axis,
        r: R,
        font: &FontId,
        color: Color32,
    ) {
        if !axis.label_visible() || !axis.visible {
            return;
        }
        let g = galley(ui, &axis.label_string(), font, color);
        let m = metrics(&g, font);
        let (lw, lh) = (m.width + 2.0 * DOC_MARGIN, document_height(m.line_height));
        let (lx, ly) = axis.label_pos(r.w, r.h, lw, lh);
        match axis.orientation {
            Orientation::Left | Orientation::Right => {
                // Rotated: the text runs upwards from (lx, ly), its top towards +x.
                let pos = px.rotated_text_origin(r.x + lx + DOC_MARGIN, r.y + ly - DOC_MARGIN, &m);
                let shape = TextShape::new(pos, g, color).with_angle(-std::f32::consts::FRAC_PI_2);
                painter.add(Shape::Text(shape));
            }
            Orientation::Top | Orientation::Bottom => {
                painter.galley(px.text_origin(r.x + lx + DOC_MARGIN, r.y + ly + DOC_MARGIN, &m), g, color);
            }
        }
    }

    /// `PlotItem.titleLabel`: centred in its 30 px row over the view box.
    fn draw_title(&self, ui: &Ui, painter: &egui::Painter, px: &Pixels, r: R, color: Color32) {
        if self.title.is_empty() {
            return;
        }
        let title_font = self.text.title_font();
        let g = galley(ui, &self.title, &title_font, color);
        let m = metrics(&g, &title_font);
        let (iw, ih) = (m.width + 2.0 * DOC_MARGIN, document_height(m.line_height));
        let x = r.x + (r.w - iw) / 2.0;
        let y = r.y + (r.h - ih) / 2.0;
        painter.galley(px.text_origin(x + DOC_MARGIN, y + DOC_MARGIN, &m), g, color);
    }

    // -------------------------------------------------------------- mouse

    fn handle_mouse(&mut self, ui: &mut Ui, rect: Rect, px: &Pixels, geo: &Geometry) {
        let resp = ui.interact(rect, self.id, Sense::click_and_drag());
        let pointer = ui.input(|i| i.pointer.latest_pos());
        let to_canvas = |p: Pos2| px.canvas(p);
        let move_consumed = self.handle_capture_input(ui, &resp, px, geo);

        // Cursor readout: anywhere over the PlotItem, extrapolating outside the view box.
        if resp.hovered()
            && !move_consumed
            && let Some(p) = pointer
            && ui.input(|i| i.pointer.delta() != egui::Vec2::ZERO || i.pointer.is_moving())
        {
            let c = to_canvas(p);
            if geo.plot.contains(c) && self.image.is_some() {
                let (x, y) = self.view_coords(c, geo.view);
                self.events.push(CanvasEvent::MouseMoved { x, y: self.data_y(y), inside: true });
            } else {
                self.events.push(CanvasEvent::MouseMoved { x: 0.0, y: 0.0, inside: false });
            }
        }

        // Wheel: zoom about the pointer, on one axis when over that axis.
        if resp.hovered()
            && self.mouse_enabled()
            && let Some(p) = pointer
        {
            let delta = ui.input(|i| {
                i.events
                    .iter()
                    .filter_map(|e| match e {
                        egui::Event::MouseWheel { unit, delta, .. } => Some(match unit {
                            egui::MouseWheelUnit::Line => delta.y as f64 * WHEEL_NOTCH,
                            egui::MouseWheelUnit::Page => delta.y as f64 * WHEEL_NOTCH * 3.0,
                            // Qt reports roughly 120 per 50 px of smooth scrolling.
                            egui::MouseWheelUnit::Point => delta.y as f64 * WHEEL_NOTCH / 50.0,
                        }),
                        _ => None,
                    })
                    .sum::<f64>()
            });
            let c = to_canvas(p);
            if delta != 0.0 {
                let axis = self.hit_axis(c, geo);
                if axis.is_some() || geo.view.contains(c) {
                    let s = 1.02f64.powf(delta * WHEEL_SCALE);
                    let center = self.view_coords(c, geo.view);
                    let mask = axis_mask(axis);
                    self.scale_by((mask[0].then_some(s), mask[1].then_some(s)), center);
                }
            }
        }

        // Drags.
        let primary = ui.input(|i| i.pointer.primary_down() || i.pointer.middle_down());
        let secondary = ui.input(|i| i.pointer.secondary_down());
        if resp.drag_started()
            && let Some(origin) = ui.input(|i| i.pointer.press_origin())
        {
            let c = to_canvas(origin);
            let axis = self.hit_axis(c, geo);
            let in_view = geo.view.contains(c);
            if self.mouse_enabled() && (axis.is_some() || in_view) {
                let kind = if secondary {
                    Some(DragKind::Scale)
                } else if primary {
                    Some(if self.rect_zoom_once && axis.is_none() { DragKind::RectZoom } else { DragKind::Pan })
                } else {
                    None
                };
                if let Some(kind) = kind {
                    self.drag = Some(Drag { kind, axis, start: c, last: c });
                }
            }
        }
        if let Some(mut d) = self.drag {
            if let Some(p) = pointer {
                let c = to_canvas(p);
                let (dx, dy) = (c.0 - d.last.0, c.1 - d.last.1);
                match d.kind {
                    DragKind::Pan if dx != 0.0 || dy != 0.0 => self.pan_by(dx, dy, d.axis, geo.view),
                    DragKind::Scale if dx != 0.0 || dy != 0.0 => {
                        // ViewBox: s = 1.02 ** (-dx, dy), about the press point.
                        let mask = axis_mask(d.axis);
                        let sx = 1.02f64.powf(-dx);
                        let sy = 1.02f64.powf(dy);
                        let center = self.view_coords(d.start, geo.view);
                        self.scale_by((mask[0].then_some(sx), mask[1].then_some(sy)), center);
                    }
                    _ => {}
                }
                d.last = c;
            }
            if resp.drag_stopped() || !(primary || secondary) {
                if d.kind == DragKind::RectZoom {
                    self.finish_rect_zoom(d, geo.view);
                }
                self.drag = None;
            } else {
                self.drag = Some(d);
            }
        }
    }

    /// Which axis (0 = x, 1 = y) a canvas point is over, if any.
    fn hit_axis(&self, c: (f64, f64), geo: &Geometry) -> Option<usize> {
        if geo.bottom.contains(c) && !geo.view.contains(c) {
            Some(0)
        } else if geo.left.contains(c) && !geo.view.contains(c) {
            Some(1)
        } else {
            None
        }
    }

    /// Canvas coordinates to plot coordinates through the view box.
    fn view_coords(&self, c: (f64, f64), v: R) -> (f64, f64) {
        let [x0, x1, y0, y1] = self.range;
        let x = x0 + (c.0 - v.x) / v.w.max(1e-12) * (x1 - x0);
        let y = y1 - (c.1 - v.y) / v.h.max(1e-12) * (y1 - y0);
        (x, y)
    }

    /// `ViewBox.mouseDragEvent` with the left button: the view follows the mouse.
    fn pan_by(&mut self, dx: f64, dy: f64, axis: Option<usize>, v: R) {
        let [x0, x1, y0, y1] = self.range;
        let mask = axis_mask(axis);
        let tx = -dx * (x1 - x0) / v.w.max(1e-12);
        let ty = dy * (y1 - y0) / v.h.max(1e-12);
        let mut range = self.range;
        if mask[0] {
            range[0] = x0 + tx;
            range[1] = x1 + tx;
        }
        if mask[1] {
            range[2] = y0 + ty;
            range[3] = y1 + ty;
        }
        self.set_range_interactive(range);
    }

    /// `ViewBox.scaleBy`.
    fn scale_by(&mut self, s: (Option<f64>, Option<f64>), center: (f64, f64)) {
        let [x0, x1, y0, y1] = self.range;
        let mut range = self.range;
        if let Some(sx) = s.0 {
            let (a, b) = (center.0 + (x0 - center.0) * sx, center.0 + (x1 - center.0) * sx);
            let (a, b) = set_range_axis((a, b), 0.0, x1 - x0);
            range[0] = a;
            range[1] = b;
        }
        if let Some(sy) = s.1 {
            let (a, b) = (center.1 + (y0 - center.1) * sy, center.1 + (y1 - center.1) * sy);
            let (a, b) = set_range_axis((a, b), 0.0, y1 - y0);
            range[2] = a;
            range[3] = b;
        }
        if range != self.range {
            self.set_range_interactive(range);
        }
    }

    /// `ViewBox.showAxRect`: the dragged rectangle, with `suggestPadding`.
    fn finish_rect_zoom(&mut self, d: Drag, v: R) {
        let a = self.view_coords(d.start, v);
        let b = self.view_coords(d.last, v);
        let pad = |len: f64| (50.0 * 0.02 / len.sqrt()).clamp(0.02, 0.1);
        let x = set_range_axis((a.0, b.0), if v.w > 0.0 { pad(v.w) } else { 0.02 }, self.range[1] - self.range[0]);
        let y = set_range_axis((a.1, b.1), if v.h > 0.0 { pad(v.h) } else { 0.02 }, self.range[3] - self.range[2]);
        self.set_range_interactive([x.0, x.1, y.0, y.1]);
    }
}

/// `ViewBox.setRange` for one axis: sorted, a zero span widened to the
/// current one, the quantisation guard, then `padding` on each side.
fn set_range_axis(range: (f64, f64), padding: f64, current_span: f64) -> (f64, f64) {
    let (mut mn, mut mx) = (range.0.min(range.1), range.0.max(range.1));
    let mut preserve = false;
    if mn == mx {
        preserve = true;
        let dy = if current_span == 0.0 { 1.0 } else { current_span };
        mn -= dy * 0.5;
        mx += dy * 0.5;
    }
    let q = (mn + mx) * 1.5e-15;
    if mx - mn < 2.0 * q {
        mn -= q;
        mx += q;
    }
    if !preserve {
        let p = (mx - mn) * padding;
        mn -= p;
        mx += p;
    }
    (mn, mx)
}

fn axis_mask(axis: Option<usize>) -> [bool; 2] {
    match axis {
        None => [true, true],
        Some(0) => [true, false],
        Some(_) => [false, true],
    }
}

/// `QTextDocument` height for one line of text: the line height rounded up,
/// plus the document margin on both sides.
fn document_height(row_height: f64) -> f64 {
    (row_height - 1e-3).ceil() + 2.0 * DOC_MARGIN
}

fn galley(ui: &Ui, text: &str, font: &FontId, color: Color32) -> Arc<Galley> {
    ui.ctx().fonts_mut(|f| f.layout_no_wrap(text.to_owned(), font.clone(), color))
}

fn cached_layer(
    ui: &Ui,
    slot: &mut Option<Layer>,
    name: &str,
    key: String,
    make: impl FnOnce() -> RgbaImage,
) -> egui::TextureId {
    let fresh = slot.as_ref().is_none_or(|l| l.key != key);
    if fresh {
        let img = make();
        let color = ColorImage::from_rgba_unmultiplied(img.size, img.as_bytes());
        match slot {
            Some(layer) => {
                layer.texture.set(color, TextureOptions::NEAREST);
                layer.key = key;
            }
            None => {
                let texture = ui.ctx().load_texture(name, color, TextureOptions::NEAREST);
                *slot = Some(Layer { texture, key });
            }
        }
    }
    slot.as_ref().unwrap().texture.id()
}

/// Maps canvas coordinates to device pixels, with the canvas origin snapped
/// to a whole pixel as Qt places a widget.
#[derive(Clone, Copy, Debug)]
struct Pixels {
    origin: (f64, f64),
    ppp: f64,
}

impl Pixels {
    fn new(rect: Rect, ppp: f64) -> Self {
        Self { origin: ((rect.min.x as f64 * ppp).round(), (rect.min.y as f64 * ppp).round()), ppp }
    }

    fn dev_x(&self, x: f64) -> f64 {
        self.origin.0 + x * self.ppp
    }

    fn dev_y(&self, y: f64) -> f64 {
        self.origin.1 + y * self.ppp
    }

    fn point(&self, x: f64, y: f64) -> Pos2 {
        pos2((self.dev_x(x) / self.ppp) as f32, (self.dev_y(y) / self.ppp) as f32)
    }

    /// A rectangle of whole device pixels, in points.
    fn device_rect(&self, x: f64, y: f64, w: f64, h: f64) -> Rect {
        let s = self.ppp as f32;
        Rect::from_min_size(pos2(x as f32 / s, y as f32 / s), vec2(w as f32 / s, h as f32 / s))
    }

    fn canvas(&self, p: Pos2) -> (f64, f64) {
        ((p.x as f64 * self.ppp - self.origin.0) / self.ppp, (p.y as f64 * self.ppp - self.origin.1) / self.ppp)
    }

    /// Where to put `galley` so its glyphs land where Qt draws them: Qt
    /// rounds the exact baseline (`top` + the font's ascent) to a device
    /// pixel; egui rounds the galley origin and keeps the baseline a whole
    /// number of pixels below it.
    fn text_origin(&self, x: f64, top: f64, m: &TextMetrics) -> Pos2 {
        let target = (self.dev_y(top) + m.ascent * self.ppp).round();
        let top = target - (m.baseline_in_galley * self.ppp).round();
        pos2((self.dev_x(x) / self.ppp) as f32, (top / self.ppp) as f32)
    }

    /// The same for text rotated -90°, whose top edge is the vertical line `left`.
    fn rotated_text_origin(&self, left: f64, y: f64, m: &TextMetrics) -> Pos2 {
        let target = (self.dev_x(left) + m.ascent * self.ppp).round();
        let left = target - (m.baseline_in_galley * self.ppp).round();
        pos2((left / self.ppp) as f32, (self.dev_y(y) / self.ppp) as f32)
    }
}

/// A line of text as Qt measures it: advance width and the font's own
/// (unrounded) line height and ascent; plus where egui put the baseline.
#[derive(Clone, Copy, Debug)]
struct TextMetrics {
    width: f64,
    line_height: f64,
    ascent: f64,
    baseline_in_galley: f64,
}

fn metrics(g: &Galley, font: &FontId) -> TextMetrics {
    let mut m = galley_metrics(g);
    if let Some(q) = crate::fonts::qt_metrics(font) {
        m.line_height = q.height() as f64;
        m.ascent = q.ascent as f64;
    }
    m
}

fn galley_metrics(g: &Galley) -> TextMetrics {
    let Some(row) = g.rows.first() else {
        return TextMetrics { width: 0.0, line_height: 0.0, ascent: 0.0, baseline_in_galley: 0.0 };
    };
    let glyphs = &row.row.glyphs;
    let width = glyphs.iter().map(|g| g.advance_width as f64).sum();
    match glyphs.first() {
        Some(first) => TextMetrics {
            width,
            line_height: first.font_height as f64,
            ascent: first.font_ascent as f64,
            baseline_in_galley: (row.pos.y + first.pos.y) as f64,
        },
        None => TextMetrics { width, line_height: 0.0, ascent: 0.0, baseline_in_galley: 0.0 },
    }
}

/// A 1 px cosmetic, unantialiased line: the device pixels it crosses.
fn draw_line(painter: &egui::Painter, px: &Pixels, ox: f64, oy: f64, line: Line, color: Color32) {
    let (x1, x2) = (px.dev_x(ox + line.x1), px.dev_x(ox + line.x2));
    let (y1, y2) = (px.dev_y(oy + line.y1), px.dev_y(oy + line.y2));
    let (c0, c1) = (x1.min(x2).floor(), x1.max(x2).floor());
    let (r0, r1) = (y1.min(y2).floor(), y1.max(y2).floor());
    let color = if line.alpha == 255 { color } else { color.gamma_multiply_u8(line.alpha) };
    painter.rect_filled(px.device_rect(c0, r0, c1 - c0 + 1.0, r1 - r0 + 1.0), CornerRadius::ZERO, color);
}

/// An axis' line, ticks and tick text, at `r` in canvas coordinates.
#[allow(clippy::too_many_arguments)]
fn draw_axis(
    ui: &Ui,
    painter: &egui::Painter,
    px: &Pixels,
    r: R,
    draw: &AxisDraw,
    font: &FontId,
    pen: Color32,
    text: Color32,
) {
    if let Some(line) = draw.line {
        draw_line(painter, px, r.x, r.y, line, pen);
    }
    for tick in &draw.ticks {
        draw_line(painter, px, r.x, r.y, *tick, pen);
    }
    for t in &draw.texts {
        let g = galley(ui, &t.text, font, text);
        let m = metrics(&g, font);
        let (gw, gh) = (m.width, m.line_height);
        let rr = t.rect;
        let (x, y) = match t.align {
            TextAlign::RightVCenter => (rr.right() - gw, rr.y + (rr.h - gh) / 2.0),
            TextAlign::LeftVCenter => (rr.x, rr.y + (rr.h - gh) / 2.0),
            TextAlign::HCenterTop => (rr.x + (rr.w - gw) / 2.0, rr.y),
            TextAlign::HCenterBottom => (rr.x + (rr.w - gw) / 2.0, rr.bottom() - gh),
        };
        painter.galley(px.text_origin(r.x + x, r.y + y, &m), g, text);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_matches_pyqtgraph() {
        // The single-file reference: canvas 1270×863, left axis 44.09375, bottom 44.
        let g = layout(1270.0, 863.0, 44.09375, 44.0);
        assert_eq!((g.view.x, g.view.y, g.view.w, g.view.h), (54.09375, 40.0, 1128.90625, 769.0));
        assert_eq!((g.bar.x, g.bar.y, g.bar.w, g.bar.h), (1189.0, 41.0, 25.0, 767.0));
        assert_eq!((g.bar_axis.x, g.bar_axis.w), (1214.0, 45.0));
        assert_eq!((g.bottom.y, g.title.y, g.title.h), (809.0, 10.0, 30.0));
    }

    #[test]
    fn set_range_rules() {
        assert_eq!(set_range_axis((3.0, 1.0), 0.0, 1.0), (1.0, 3.0));
        // A zero span takes half the current span on each side.
        assert_eq!(set_range_axis((0.0, 0.0), 0.0, 1.0), (-0.5, 0.5));
        let (a, b) = set_range_axis((0.0, 100.0), 0.05, 1.0);
        assert!((a + 5.0).abs() < 1e-12 && (b - 105.0).abs() < 1e-12);
    }

    #[test]
    fn empty_canvas_has_prefixed_labels() {
        let c = PlotCanvas::new();
        assert_eq!(c.left.label_string(), "Frequency [MHz] (x0.001)");
        assert_eq!(c.bottom.label_string(), "Time [s] (x0.001)");
        assert!(c.view().is_none());
    }
}
