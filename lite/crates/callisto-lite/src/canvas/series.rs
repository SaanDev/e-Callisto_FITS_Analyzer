//! What the Type II band-splitting plot adds to the canvas
//! (`TypeIIBandSplittingDialog`'s `PlotItem`): a grid from the axes' ticks
//! (`showGrid(alpha=…)`), the image and colour bar shown or hidden, its own
//! point (`ScatterPlotItem`, pixel-mode circles) and curve (`PlotCurveItem`)
//! items, and `enableAutoRange` over everything visible in the view.

use callisto_core::plot::qt_raster::Pen;

use super::PlotCanvas;

/// One plotted series, in data coordinates.
#[derive(Clone, Debug, PartialEq)]
pub enum Series {
    /// `ScatterPlotItem(size, pen, brush, pxMode=True)`.
    Scatter { x: Vec<f64>, y: Vec<f64>, size: f64, pen: Pen, brush: [u8; 4] },
    /// `PlotCurveItem(pen, antialias=…)`.
    Curve { x: Vec<f64>, y: Vec<f64>, pen: Pen, antialias: bool },
}

impl Series {
    pub(super) fn xy(&self) -> (&[f64], &[f64]) {
        match self {
            Series::Scatter { x, y, .. } | Series::Curve { x, y, .. } => (x, y),
        }
    }

    /// `pixelPadding`: what the view keeps around the item, in pixels.
    fn pixel_padding(&self) -> f64 {
        match self {
            Series::Scatter { size, pen, .. } => (size + pen.width) * 0.7072,
            Series::Curve { pen, .. } => pen.width * 0.7072,
        }
    }

    /// `dataBounds` on both axes: the finite points' extent.
    fn bounds(&self) -> Option<[f64; 4]> {
        let (xs, ys) = self.xy();
        let mut b = [f64::INFINITY, f64::NEG_INFINITY, f64::INFINITY, f64::NEG_INFINITY];
        for (&x, &y) in xs.iter().zip(ys) {
            if x.is_finite() && y.is_finite() {
                b = [b[0].min(x), b[1].max(x), b[2].min(y), b[3].max(y)];
            }
        }
        b[0].is_finite().then_some(b)
    }
}

impl PlotCanvas {
    /// `showGrid(x=True, y=True, alpha=…)`: `None` hides the grid.
    pub fn set_grid(&mut self, alpha: Option<f64>) {
        let a = alpha.map(|a| (a.clamp(0.0, 1.0) * 255.0) as u8);
        self.left.grid = a;
        self.bottom.grid = a;
    }

    /// `ColorBarItem.show()` / `hide()`: hidden, its column collapses and the
    /// 5 px gap before it stays.
    pub fn set_colorbar_visible(&mut self, visible: bool) {
        self.colorbar_visible = visible;
    }

    /// `ImageItem.show()` / `hide()`.
    pub fn set_image_visible(&mut self, visible: bool) {
        self.image_visible = visible;
    }

    /// The point and curve items over the image, in the order they were
    /// added; empty ones are hidden.
    pub fn set_series(&mut self, series: Vec<Series>) {
        if series == self.series {
            return;
        }
        self.overlays_set_series(&series);
        self.series = series;
    }

    /// `setTitle` and `setLabel` for a plot without an image.
    pub fn set_labels(&mut self, title: &str, x_label: &str, y_label: &str, colorbar_label: &str) {
        self.title = title.into();
        self.x_label = x_label.into();
        self.y_label = y_label.into();
        self.colorbar_label = colorbar_label.into();
        self.apply_labels();
    }

    /// `enableAutoRange()`: the view follows the visible items until the
    /// mouse moves it.
    pub fn enable_auto_range(&mut self) {
        self.auto_range = true;
    }

    /// `ViewBox.childrenBounds` and `updateAutoRange` for a view box of
    /// `w` × `h` px: the items' union, widened for their pixel padding, then
    /// `suggestPadding` (1/√length, between 2 % and 10 %) on each side.
    pub(super) fn apply_auto_range(&mut self, w: f64, h: f64) {
        let mut items: Vec<([f64; 4], f64)> = Vec::new();
        if self.image_visible
            && let Some(img) = &self.image
        {
            let [x, y, iw, ih] = img.rect;
            items.push(([x.min(x + iw), x.max(x + iw), y.min(y + ih), y.max(y + ih)], 0.0));
        }
        for s in &self.series {
            if let Some(b) = s.bounds() {
                items.push((b, s.pixel_padding()));
            }
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
        let padding = |l: f64| if l > 0.0 { (1.0 / l.sqrt()).clamp(0.02, 0.1) } else { 0.02 };
        let x = super::set_range_axis((r[0], r[1]), padding(w), self.range[1] - self.range[0]);
        let y = super::set_range_axis((r[2], r[3]), padding(h), self.range[3] - self.range[2]);
        if [x.0, x.1, y.0, y.1].iter().all(|v| v.is_finite()) {
            self.range = [x.0, x.1, y.0, y.1];
            self.sync_axis_ranges();
        }
    }
}
