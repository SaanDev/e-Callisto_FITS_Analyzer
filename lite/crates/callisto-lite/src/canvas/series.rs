//! What the Type II band-splitting plot adds to the canvas
//! (`TypeIIBandSplittingDialog`'s `PlotItem`): a grid from the axes'
//! ticks (`showGrid(alpha=…)`), the colour bar shown or hidden, its own
//! scatter (`ScatterPlotItem`, pixel-mode circles) and antialiased curve
//! (`PlotCurveItem(antialias=True)`) items, and `enableAutoRange` over
//! everything in the view.

use callisto_core::plot::qt_raster::{Pen, Raster};
use egui::{Color32, Pos2, Rect, Ui, pos2};

use super::{Geometry, Pixels, PlotCanvas, cached_layer};

/// One plotted series, in data coordinates.
#[derive(Clone, Debug, PartialEq)]
pub enum Series {
    /// `ScatterPlotItem(size, pen=mkPen(pen, width=pen_width), brush)`.
    Scatter { x: Vec<f64>, y: Vec<f64>, size: f64, pen: [u8; 4], pen_width: f64, brush: [u8; 4] },
    /// `PlotCurveItem(pen=mkPen(color, width), antialias=True)`.
    Curve { x: Vec<f64>, y: Vec<f64>, color: [u8; 4], width: f64 },
}

impl Series {
    fn xy(&self) -> (&[f64], &[f64]) {
        match self {
            Series::Scatter { x, y, .. } | Series::Curve { x, y, .. } => (x, y),
        }
    }

    /// `pixelPadding`: what the view keeps around the item, in pixels.
    fn pixel_padding(&self) -> f64 {
        match self {
            Series::Scatter { size, pen_width, .. } => (size + pen_width) * 0.7072,
            Series::Curve { width, .. } => width * 0.7072,
        }
    }
}

impl PlotCanvas {
    /// `showGrid(x=True, y=True, alpha=…)`.
    pub fn set_grid(&mut self, alpha: Option<f64>) {
        let a = alpha.map(|a| (a.clamp(0.0, 1.0) * 255.0) as u8);
        self.left.grid = a;
        self.bottom.grid = a;
    }

    /// `ColorBarItem.show()` / `hide()`.
    pub fn set_colorbar_visible(&mut self, visible: bool) {
        self.colorbar_visible = visible;
    }

    /// The image shown or hidden (`ImageItem.show()` / `hide()`).
    pub fn set_image_visible(&mut self, visible: bool) {
        self.image_visible = visible;
    }

    /// The series over the image, in drawing order.
    pub fn set_series(&mut self, series: Vec<Series>) {
        if series != self.series {
            self.series = series;
            self.series_generation += 1;
        }
    }

    /// Labels for a plot without an image (`setLabel`, `setTitle`).
    pub fn set_labels(&mut self, title: &str, x_label: &str, y_label: &str, colorbar_label: &str) {
        self.title = title.into();
        self.x_label = x_label.into();
        self.y_label = y_label.into();
        self.colorbar_label = colorbar_label.into();
        self.apply_labels();
    }

    /// `enableAutoRange()`: the view follows the items until the mouse moves it.
    pub fn enable_auto_range(&mut self) {
        self.auto_range = true;
    }

    /// The plot coordinates at screen position `pos` when it is over the
    /// `PlotItem` (axes and title included), as `mapSceneToView` gives them.
    pub fn plot_coords(&self, pos: Pos2) -> Option<(f64, f64)> {
        let (px, geo) = self.last_layout?;
        let c = px.canvas(pos);
        if !geo.plot.contains(c) {
            return None;
        }
        let (x, y) = self.view_coords(c, geo.view);
        Some((x, self.data_y(y)))
    }

    /// `ViewBox.childrenBounds` + `updateAutoRange` for a view `w` × `h` px.
    pub(super) fn apply_auto_range(&mut self, w: f64, h: f64) {
        // (bounds, pixel padding) of each visible item.
        let mut items: Vec<([f64; 4], f64)> = Vec::new();
        if self.image_visible
            && let Some(img) = &self.image
        {
            let [x, y, iw, ih] = img.rect();
            items.push(([x.min(x + iw), x.max(x + iw), y.min(y + ih), y.max(y + ih)], 0.0));
        }
        for s in &self.series {
            let (xs, ys) = s.xy();
            let fin: Vec<(f64, f64)> = xs.iter().zip(ys).filter(|(a, b)| a.is_finite() && b.is_finite()).map(|(a, b)| (*a, self.axis_y(*b))).collect();
            if fin.is_empty() {
                continue;
            }
            let fold = |f: fn(f64, f64) -> f64, init: f64, pick: fn(&(f64, f64)) -> f64| fin.iter().map(pick).fold(init, f);
            let b = [
                fold(f64::min, f64::INFINITY, |p| p.0),
                fold(f64::max, f64::NEG_INFINITY, |p| p.0),
                fold(f64::min, f64::INFINITY, |p| p.1),
                fold(f64::max, f64::NEG_INFINITY, |p| p.1),
            ];
            items.push((b, s.pixel_padding()));
        }
        if items.is_empty() {
            return;
        }
        let mut r = [f64::INFINITY, f64::NEG_INFINITY, f64::INFINITY, f64::NEG_INFINITY];
        for (b, _) in &items {
            r = [r[0].min(b[0]), r[1].max(b[1]), r[2].min(b[2]), r[3].max(b[3])];
        }
        if w > 0.0 {
            let size = (r[1] - r[0]) / w;
            for (b, pad) in &items {
                if *pad != 0.0 {
                    r[0] = r[0].min(b[0] - pad * size);
                    r[1] = r[1].max(b[1] + pad * size);
                }
            }
        }
        if h > 0.0 {
            let size = (r[3] - r[2]) / h;
            for (b, pad) in &items {
                if *pad != 0.0 {
                    r[2] = r[2].min(b[2] - pad * size);
                    r[3] = r[3].max(b[3] + pad * size);
                }
            }
        }
        // `suggestPadding`: 1/√length, between 2 % and 10 %.
        let padding = |l: f64| if l > 0.0 { (50.0 * 0.02 / l.sqrt()).clamp(0.02, 0.1) } else { 0.02 };
        let (px_, py_) = ((r[1] - r[0]) * padding(w), (r[3] - r[2]) * padding(h));
        let (x0, x1, y0, y1) = (r[0] - px_, r[1] + px_, r[2] - py_, r[3] + py_);
        if [x0, x1, y0, y1].iter().all(|v| v.is_finite()) {
            let x = super::set_range_axis((x0, x1), 0.0, self.range[1] - self.range[0]);
            let y = super::set_range_axis((y0, y1), 0.0, self.range[3] - self.range[2]);
            self.range = [x.0, x.1, y.0, y.1];
            self.sync_axis_ranges();
        }
    }

    /// The series, rasterised into the view box (clipped to it) at device resolution.
    pub(super) fn draw_series(&mut self, ui: &Ui, painter: &egui::Painter, px: &Pixels, geo: &Geometry) {
        if self.series.is_empty() {
            return;
        }
        let v = geo.view;
        let (l, t) = (px.dev_x(v.x).floor(), px.dev_y(v.y).floor());
        let (r, b) = (px.dev_x(v.x + v.w).ceil(), px.dev_y(v.y + v.h).ceil());
        let (nx, ny) = ((r - l).max(0.0) as usize, (b - t).max(0.0) as usize);
        if nx == 0 || ny == 0 {
            return;
        }
        let key = format!("{}|{:?}|{l},{t},{nx},{ny}|{}", self.series_generation, self.range, px.ppp());
        let [x0, x1, y0, y1] = self.range;
        // Data to device pixels, relative to the layer.
        let to_dev = |x: f64, y: f64| {
            let cx = v.x + (x - x0) / (x1 - x0) * v.w;
            let cy = v.y + (y1 - y) / (y1 - y0) * v.h;
            (px.dev_x(cx) - l, px.dev_y(cy) - t)
        };
        let series = self.series.clone();
        let log = self.log_freq;
        let dpr = px.ppp();
        let layer = cached_layer(ui, &mut self.series_layer, "plot-series", key, || {
            let mut pix = tiny_skia::Pixmap::new(nx as u32, ny as u32).expect("series layer");
            for s in &series {
                match s {
                    Series::Curve { x, y, color, width } => {
                        let mut pb = tiny_skia::PathBuilder::new();
                        let mut started = false;
                        for (&xv, &yv) in x.iter().zip(y) {
                            let yv = if log { if yv > 0.0 { yv.log10() } else { f64::NAN } } else { yv };
                            if !(xv.is_finite() && yv.is_finite()) {
                                started = false;
                                continue;
                            }
                            let (dx, dy) = to_dev(xv, yv);
                            if started {
                                pb.line_to(dx as f32, dy as f32);
                            } else {
                                pb.move_to(dx as f32, dy as f32);
                                started = true;
                            }
                        }
                        if let Some(path) = pb.finish() {
                            let mut paint = tiny_skia::Paint::default();
                            paint.set_color_rgba8(color[0], color[1], color[2], color[3]);
                            paint.anti_alias = true;
                            // A cosmetic pen: `width` device-independent pixels, Qt's square caps and bevel joins.
                            let stroke = tiny_skia::Stroke {
                                width: (width * dpr) as f32,
                                line_cap: tiny_skia::LineCap::Square,
                                line_join: tiny_skia::LineJoin::Bevel,
                                ..Default::default()
                            };
                            pix.stroke_path(&path, &paint, &stroke, tiny_skia::Transform::identity(), None);
                        }
                    }
                    Series::Scatter { x, y, size, pen, pen_width, brush } => {
                        let centers: Vec<(f64, f64)> = x
                            .iter()
                            .zip(y)
                            .map(|(&xv, &yv)| (xv, if log { yv.log10() } else { yv }))
                            .filter(|(a, b)| a.is_finite() && b.is_finite())
                            .map(|(a, b)| to_dev(a, b))
                            .collect();
                        let mut raster = Raster::new(nx, ny, 0.0, 0.0, true);
                        raster.circles(&centers, *size, &Pen::new(*pen, *pen_width), *brush, dpr);
                        let img = raster.into_image();
                        // Over what is there (premultiplied).
                        if let Some(over) = tiny_skia::PixmapRef::from_bytes(img.as_bytes(), nx as u32, ny as u32) {
                            pix.draw_pixmap(0, 0, over, &tiny_skia::PixmapPaint::default(), tiny_skia::Transform::identity(), None);
                        }
                    }
                }
            }
            // Back to straight alpha for the texture.
            let mut out = callisto_core::image::RgbaImage::new(nx, ny);
            for (i, c) in pix.pixels().iter().enumerate() {
                let d = c.demultiply();
                out.set_index(i, [d.red(), d.green(), d.blue(), d.alpha()]);
            }
            out
        });
        let rect = Rect::from_min_max(pos2((l / dpr) as f32, (t / dpr) as f32), pos2((r / dpr) as f32, (b / dpr) as f32));
        let rect = rect.translate(px.origin().to_vec2());
        painter.with_clip_rect(px.device_rect(px.dev_x(v.x), px.dev_y(v.y), (px.dev_x(v.x + v.w) - px.dev_x(v.x)).max(0.0), (px.dev_y(v.y + v.h) - px.dev_y(v.y)).max(0.0))).image(
            layer,
            rect,
            Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)),
            Color32::WHITE,
        );
    }
}
