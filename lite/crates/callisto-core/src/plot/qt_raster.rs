//! The plot overlays as Qt's raster engine draws them with antialiasing off
//! (`pg.setConfigOptions(antialias=False)` in the full app's hardware canvas).
//!
//! pyqtgraph hands Qt cosmetic pens, whose width is in device pixels, and Qt
//! picks one of three line drawers by pen:
//!
//! - width ≤ 1: `QCosmeticStroker` (`src/gui/painting/qcosmeticstroker.cpp`),
//!   one pixel per step along the major axis, with its own dash counter;
//! - wider, solid and opaque: pyqtgraph draws separate segments
//!   (`PlotCurveItem._shouldUseDrawLineSegments`), which Qt rasterises as
//!   square-capped rectangles (`QRasterizer::rasterizeLine`, `qrasterizer.cpp`);
//! - anything else: the path is stroked (square caps, bevel joins, dashes of
//!   `[4, 2]` or `[1, 2]` pen widths) and the outline filled at pixel centres.
//!
//! The first two are line-by-line ports. The third uses tiny-skia's stroker and
//! scan converter, which sample the same pixel centres. A curve item with
//! `antialias=True` (the Type II band-splitting fits) skips the segmenting
//! and strokes the whole path with coverage antialiasing. Scatter symbols are
//! antialiased images (`ScatterPlotItem.renderSymbol`) placed at rounded
//! device positions, as `drawPixmapFragments` places them.
//!
//! Everything works in canvas device pixels; a [`Raster`] covers a window of
//! them (the view box, which clips its items) at an integer offset.

use tiny_skia::{
    FillRule, LineCap, LineJoin, Paint, PathBuilder, Pixmap, PixmapPaint, Shader, Stroke, StrokeDash, Transform,
};

use crate::image::{Rgba, RgbaImage};

/// `Qt::PenStyle` values pyqtgraph's overlays use.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum PenStyle {
    #[default]
    Solid,
    Dash,
    Dot,
}

impl PenStyle {
    /// `QPen::dashPattern()`, in pen widths.
    fn pattern(self) -> Option<[f64; 2]> {
        match self {
            PenStyle::Solid => None,
            PenStyle::Dash => Some([4.0, 2.0]),
            PenStyle::Dot => Some([1.0, 2.0]),
        }
    }
}

/// A cosmetic pen: straight (not premultiplied) RGBA, width in device pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pen {
    pub rgba: [u8; 4],
    pub width: f64,
    pub style: PenStyle,
}

impl Pen {
    pub fn new(rgba: [u8; 4], width: f64) -> Self {
        Self { rgba, width, style: PenStyle::Solid }
    }

    pub fn styled(mut self, style: PenStyle) -> Self {
        self.style = style;
        self
    }
}

/// `QColor(name)` for the colours the overlays use: `#rgb`, `#rrggbb`,
/// `#aarrggbb`, and the SVG names Qt knows for them. `None` when invalid.
pub fn parse_color(name: &str) -> Option<[u8; 4]> {
    let s = name.trim();
    if let Some(hex) = s.strip_prefix('#') {
        let digits: Option<Vec<u8>> = hex.chars().map(|c| c.to_digit(16).map(|d| d as u8)).collect();
        let d = digits?;
        let pair = |i: usize| d[i] * 16 + d[i + 1];
        return match d.len() {
            3 => Some([d[0] * 17, d[1] * 17, d[2] * 17, 255]),
            6 => Some([pair(0), pair(2), pair(4), 255]),
            8 => Some([pair(2), pair(4), pair(6), pair(0)]),
            _ => None,
        };
    }
    let rgb = match s.to_lowercase().as_str() {
        "white" => [255, 255, 255],
        "black" => [0, 0, 0],
        "red" => [255, 0, 0],
        "green" => [0, 128, 0],
        "lime" => [0, 255, 0],
        "blue" => [0, 0, 255],
        "yellow" => [255, 255, 0],
        "cyan" | "aqua" => [0, 255, 255],
        "magenta" | "fuchsia" => [255, 0, 255],
        "orange" => [255, 165, 0],
        "gray" | "grey" => [128, 128, 128],
        _ => return None,
    };
    Some([rgb[0], rgb[1], rgb[2], 255])
}

/// `QColor::name()`: `#rrggbb`, lower case.
pub fn color_name(c: [u8; 4]) -> String {
    format!("#{:02x}{:02x}{:02x}", c[0], c[1], c[2])
}

/// An inclusive rectangle of device pixels (`QRect`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct IRect {
    left: i32,
    top: i32,
    right: i32,
    bottom: i32,
}

/// A pen colour as `QSpanData` keeps it: `QRgba64`, premultiplied
/// (`qPremultiplyWithExtraAlpha`).
fn premultiplied64(c: [u8; 4]) -> [u32; 4] {
    let c16 = c.map(|v| v as u32 * 257);
    let a = c16[3];
    if a == 65535 {
        return c16;
    }
    let m = |v: u32| {
        let t = v as u64 * a as u64;
        ((t + (t >> 16) + 0x8000) >> 16) as u32
    };
    [m(c16[0]), m(c16[1]), m(c16[2]), a]
}

/// `QRgba64::toArgb32`: 16 to 8 bits per channel (`div_257`).
fn to_argb32(c: [u32; 4]) -> [u8; 4] {
    c.map(|x| ((x - (x >> 8) + 0x80) >> 8) as u8)
}

/// The premultiplied 8-bit colour solid fills blend with.
fn premultiply(c: [u8; 4]) -> [u8; 4] {
    to_argb32(premultiplied64(c))
}

/// `BYTE_MUL` on one channel.
fn byte_mul(v: u8, a: u32) -> u8 {
    let t = v as u32 * a;
    ((t + (t >> 8) + 0x80) >> 8) as u8
}

/// `sourceOver`: premultiplied `color` over `d`.
fn source_over(d: [u8; 4], color: [u8; 4]) -> [u8; 4] {
    let ia = 255 - color[3] as u32;
    [
        color[0].wrapping_add(byte_mul(d[0], ia)),
        color[1].wrapping_add(byte_mul(d[1], ia)),
        color[2].wrapping_add(byte_mul(d[2], ia)),
        color[3].wrapping_add(byte_mul(d[3], ia)),
    ]
}

/// The runs of finite points (`connect='finite'`).
fn finite_runs(points: &[(f64, f64)]) -> Vec<&[(f64, f64)]> {
    let finite = |p: &(f64, f64)| p.0.is_finite() && p.1.is_finite();
    let mut runs: Vec<&[(f64, f64)]> = Vec::new();
    let mut start = None;
    for (i, p) in points.iter().enumerate() {
        match (finite(p), start) {
            (true, None) => start = Some(i),
            (false, Some(s)) => {
                runs.push(&points[s..i]);
                start = None;
            }
            _ => {}
        }
    }
    if let Some(s) = start {
        runs.push(&points[s..]);
    }
    runs
}

/// A window of device pixels to draw overlays into.
pub struct Raster {
    pixmap: Pixmap,
    /// Device position of the pixmap's top-left pixel.
    ox: i32,
    oy: i32,
    /// The whole device (`deviceRectUnclipped`), for the cosmetic stroker's clipping.
    device: (i32, i32),
}

impl Raster {
    /// `width`×`height` pixels at device position (`ox`, `oy`) on a
    /// `device`-sized surface. `None` when empty.
    pub fn new(width: usize, height: usize, ox: i32, oy: i32, device: (usize, usize)) -> Option<Self> {
        let pixmap = Pixmap::new(width as u32, height as u32)?;
        Some(Self { pixmap, ox, oy, device: (device.0 as i32, device.1 as i32) })
    }

    fn clip(&self) -> IRect {
        IRect {
            left: self.ox,
            top: self.oy,
            right: self.ox + self.pixmap.width() as i32 - 1,
            bottom: self.oy + self.pixmap.height() as i32 - 1,
        }
    }

    /// Premultiplied RGBA, row-major.
    pub fn into_image(self) -> RgbaImage {
        let size = [self.pixmap.width() as usize, self.pixmap.height() as usize];
        let pixels = self.pixmap.pixels().iter().map(|p| Rgba([p.red(), p.green(), p.blue(), p.alpha()])).collect();
        RgbaImage::new(size, pixels)
    }

    pub fn is_blank(&self) -> bool {
        self.pixmap.pixels().iter().all(|p| p.alpha() == 0)
    }

    /// Blend premultiplied `color` over device pixel (x, y), if inside.
    fn blend(&mut self, x: i32, y: i32, color: [u8; 4]) {
        let c = self.clip();
        if x < c.left || x > c.right || y < c.top || y > c.bottom {
            return;
        }
        let w = self.pixmap.width() as usize;
        let i = (y - self.oy) as usize * w + (x - self.ox) as usize;
        let data = self.pixmap.data_mut();
        let d = [data[4 * i], data[4 * i + 1], data[4 * i + 2], data[4 * i + 3]];
        let out = if color[3] == 255 { color } else { source_over(d, color) };
        data[4 * i..4 * i + 4].copy_from_slice(&out);
    }

    fn span(&mut self, x: i32, len: i32, y: i32, color: [u8; 4]) {
        for xx in x..x + len {
            self.blend(xx, y, color);
        }
    }

    // ------------------------------------------------------------- curves

    /// `PlotCurveItem.paint` for device points, with non-finite points
    /// breaking the line (`connect='finite'`, as `PlotDataItem` picks).
    pub fn curve(&mut self, points: &[(f64, f64)], pen: &Pen) {
        let runs = finite_runs(points);
        let segmented = pen.width > 1.0 && pen.style == PenStyle::Solid && pen.rgba[3] == 255;
        if segmented {
            // QPainter::drawLines: each segment its own square-capped line.
            let color = premultiply(pen.rgba);
            for run in &runs {
                for w in run.windows(2) {
                    self.stroke_line_segment(w[0], w[1], pen.width, color);
                }
            }
        } else if pen.width <= 1.0 {
            CosmeticStroker::new(self, pen).draw_path(self, &runs);
        } else {
            self.stroke_generic(&runs, pen, false);
        }
    }

    /// `PlotCurveItem(antialias=True)`: `drawPath` with the Antialiasing
    /// render hint, the cosmetic pen's square caps and bevel joins.
    pub fn curve_antialiased(&mut self, points: &[(f64, f64)], pen: &Pen) {
        let runs = finite_runs(points);
        self.stroke_generic(&runs, pen, true);
    }

    /// `QRasterPaintEngine::stroke` for one line of a `LinesHint` path.
    fn stroke_line_segment(&mut self, a: (f64, f64), b: (f64, f64), width: f64, color: [u8; 4]) {
        if a == b {
            // A zero-length line with a square cap: a width-long horizontal one.
            let (p1, p2) = ((a.0 - width * 0.5, a.1), (a.0 + width * 0.5, a.1));
            let len = (p2.0 - p1.0).hypot(p2.1 - p1.1);
            self.rasterize_line(p1, p2, width / len, false, color);
            return;
        }
        let len = (b.0 - a.0).hypot(b.1 - a.1);
        self.rasterize_line(a, b, width / len, true, color);
    }

    /// `QRasterizer::rasterizeLine`, aliased. `width` is relative to |b − a|.
    fn rasterize_line(&mut self, a: (f64, f64), b: (f64, f64), mut width: f64, square_cap: bool, color: [u8; 4]) {
        let cl = self.clip();
        if a == b || width.is_nan() || width <= 0.0 || cl.right < cl.left || cl.bottom < cl.top {
            return;
        }
        let (mut pa, mut pb) = (a, b);
        if square_cap {
            let delta = (pb.0 - pa.0, pb.1 - pa.1);
            pa = (pa.0 - 0.5 * width * delta.0, pa.1 - 0.5 * width * delta.1);
            pb = (pb.0 + 0.5 * width * delta.0, pb.1 + 0.5 * width * delta.1);
        }
        let offs = ((b.1 - a.1).abs() * width * 0.5, (b.0 - a.0).abs() * width * 0.5);
        let clip = (
            cl.left as f64 - offs.0,
            cl.top as f64 - offs.1,
            (cl.right + 1) as f64 + offs.0,
            (cl.bottom + 1) as f64 + offs.1,
        );
        if clip_line(&mut pa, &mut pb, clip) {
            return;
        }
        {
            let d0 = (a.0 - b.0, a.1 - b.1);
            let w0 = d0.0 * d0.0 + d0.1 * d0.1;
            let d = (pa.0 - pb.0, pa.1 - pb.1);
            let w = d.0 * d.0 + d.1 * d.1;
            if w == 0.0 {
                return;
            }
            width *= (w0 / w).sqrt();
        }

        if same_26dot6(pa.1, pb.1) {
            let x = (pa.0 + pb.0) * 0.5;
            let dx = (pb.0 - pa.0).abs() * 0.5;
            let y = pa.1;
            let dy = width * dx;
            pa = (x, y - dy);
            pb = (x, y + dy);
            width = 1.0 / width;
        }

        if same_26dot6(pa.0, pb.0) {
            if pa.1 > pb.1 {
                std::mem::swap(&mut pa, &mut pb);
            }
            let dy = pb.1 - pa.1;
            let half = 0.5 * width * dy;
            let bound_x = |v: f64| v.clamp(cl.left as f64, (cl.right + 1) as f64);
            let bound_y = |v: f64| v.clamp(cl.top as f64, (cl.bottom + 1) as f64);
            let left = bound_x(pa.0 - half);
            let right = bound_x(pa.0 + half);
            pa.1 = bound_y(pa.1);
            pb.1 = bound_y(pb.1);
            if same_26dot6(left, right) || same_26dot6(pa.1, pb.1) {
                return;
            }
            let i_top = (pa.1 + 0.5) as i32;
            let i_bottom = if pb.1 < 0.5 { -1 } else { (pb.1 - 0.5) as i32 };
            let i_left = (left + 0.5) as i32;
            let i_right = if right < 0.5 { -1 } else { (right - 0.5) as i32 };
            let i_width = i_right - i_left + 1;
            for y in i_top..=i_bottom {
                self.span(i_left, i_width, y, color);
            }
            return;
        }

        if pa.1 > pb.1 {
            std::mem::swap(&mut pa, &mut pb);
        }
        let delta = ((pb.0 - pa.0) * 0.5 * width, (pb.1 - pa.1) * 0.5 * width);
        let perp = (delta.1, -delta.0);
        let add = |p: (f64, f64), q: (f64, f64)| (p.0 + q.0, p.1 + q.1);
        let sub = |p: (f64, f64), q: (f64, f64)| (p.0 - q.0, p.1 - q.1);
        let (top, left, right, bottom) = if pa.0 < pb.0 {
            (add(pa, perp), sub(pa, perp), add(pb, perp), sub(pb, perp))
        } else {
            (sub(pa, perp), sub(pb, perp), add(pa, perp), add(pb, perp))
        };
        let snap = |p: (f64, f64)| ((p.0 * 64.0).floor() / 64.0, (p.1 * 64.0).floor() / 64.0);
        let (top, bottom, left, right) = (snap(top), snap(bottom), snap(left), snap(right));

        let tl = sub(left, top);
        let tr = sub(right, top);
        let bl = sub(bottom, left);
        let br = sub(bottom, right);
        let top_left_slope = safe_divide(tl.0, tl.1);
        let bottom_left_slope = safe_divide(bl.0, bl.1);
        let top_right_slope = safe_divide(tr.0, tr.1);
        let bottom_right_slope = safe_divide(br.0, br.1);
        let tl_fp = to_fixed(top_left_slope);
        let tr_fp = to_fixed(top_right_slope);
        let bl_fp = to_fixed(bottom_left_slope);
        let br_fp = to_fixed(bottom_right_slope);

        let i_top = (top.1 + 0.5) as i32;
        let i_left = if left.1 < 0.5 { -1 } else { (left.1 - 0.5) as i32 };
        let i_right = if right.1 < 0.5 { -1 } else { (right.1 - 0.5) as i32 };
        let i_bottom = if bottom.1 < 0.5 { -1 } else { (bottom.1 - 0.5) as i32 };
        let i_middle = i_left.min(i_right);

        let mut left_af = to_fixed(top.0 + 0.5 + (i_top as f64 + 0.5 - top.1) * top_left_slope);
        let mut left_bf = to_fixed(left.0 + 0.5 + (i_left as f64 + 1.5 - left.1) * bottom_left_slope);
        let mut right_af = to_fixed(top.0 - 0.5 + (i_top as f64 + 0.5 - top.1) * top_right_slope);
        let mut right_bf = to_fixed(right.0 - 0.5 + (i_right as f64 + 1.5 - right.1) * bottom_right_slope);

        let mut y = i_top;
        let segment = |this: &mut Self, next: i32, li: &mut i64, ri: &mut i64, ls: i64, rs: i64, y: &mut i32| {
            let ny = (next + 1).min(cl.top);
            if *y < ny {
                *li += ls * (ny - *y) as i64;
                *ri += rs * (ny - *y) as i64;
                *y = ny;
            }
            let next = next.min(cl.bottom);
            while *y <= next {
                let x1 = ((*li >> 16) as i32).max(cl.left);
                let x2 = ((*ri >> 16) as i32).min(cl.right);
                if x2 >= x1 {
                    this.span(x1, x2 - x1 + 1, *y, color);
                }
                *li += ls;
                *ri += rs;
                *y += 1;
            }
        };
        segment(self, i_middle, &mut left_af, &mut right_af, tl_fp, tr_fp, &mut y);
        segment(self, i_right, &mut left_bf, &mut right_af, bl_fp, tr_fp, &mut y);
        segment(self, i_left, &mut left_af, &mut right_bf, tl_fp, br_fp, &mut y);
        segment(self, i_bottom, &mut left_bf, &mut right_bf, bl_fp, br_fp, &mut y);
    }

    /// `QPaintEngineEx::stroke`: the outline (square caps, bevel joins,
    /// dashes in pen widths) filled at pixel centres.
    fn stroke_generic(&mut self, runs: &[&[(f64, f64)]], pen: &Pen, antialias: bool) {
        let mut pb = PathBuilder::new();
        for run in runs {
            if run.len() < 2 {
                continue;
            }
            pb.move_to(run[0].0 as f32, run[0].1 as f32);
            let closed = run.len() > 2 && run[0] == run[run.len() - 1];
            let end = if closed { run.len() - 1 } else { run.len() };
            for p in &run[1..end] {
                pb.line_to(p.0 as f32, p.1 as f32);
            }
            if closed {
                pb.close();
            }
        }
        let Some(path) = pb.finish() else { return };
        let dash = pen.style.pattern().and_then(|p| {
            StrokeDash::new(p.iter().map(|v| (v * pen.width) as f32).collect(), 0.0)
        });
        let stroke = Stroke {
            width: pen.width as f32,
            line_cap: LineCap::Square,
            line_join: LineJoin::Bevel,
            dash,
            ..Default::default()
        };
        let [r, g, b, a] = pen.rgba;
        let paint = Paint {
            shader: Shader::SolidColor(tiny_skia::Color::from_rgba8(r, g, b, a)),
            anti_alias: antialias,
            ..Default::default()
        };
        let t = Transform::from_translate(-self.ox as f32, -self.oy as f32);
        self.pixmap.stroke_path(&path, &paint, &stroke, t, None);
    }

    // ------------------------------------------------------------- symbols

    /// `ScatterPlotItem` circles (`symbol='o'`) of `size` logical px at device
    /// `centers`, rendered at `dpr` as `renderSymbol` does.
    pub fn circles(&mut self, centers: &[(f64, f64)], size: f64, pen: &Pen, brush: [u8; 4], dpr: f64) {
        let Some(symbol) = symbol_image(size, pen, brush, dpr) else { return };
        let side = symbol.width() as f64;
        for &(cx, cy) in centers {
            if !(cx.is_finite() && cy.is_finite()) {
                continue;
            }
            // drawPixmapFragments centres the pixmap, which Qt places at a rounded position.
            let x = (cx - side / 2.0 + 0.5).floor() as i32 - self.ox;
            let y = (cy - side / 2.0 + 0.5).floor() as i32 - self.oy;
            self.pixmap.draw_pixmap(x, y, symbol.as_ref(), &PixmapPaint::default(), Transform::identity(), None);
        }
    }
}

/// `renderSymbol`: an antialiased circle, its side `ceil(dpr·(size + pen px))`.
fn symbol_image(size: f64, pen: &Pen, brush: [u8; 4], dpr: f64) -> Option<Pixmap> {
    let pen_px = pen.width.ceil().max(1.0);
    let side = (dpr * (size + pen_px)).ceil() as u32;
    let mut pix = Pixmap::new(side, side)?;
    let c = side as f32 / 2.0;
    let path = PathBuilder::from_circle(c, c, (size * dpr / 2.0) as f32)?;
    let paint = |rgba: [u8; 4]| Paint {
        shader: Shader::SolidColor(tiny_skia::Color::from_rgba8(rgba[0], rgba[1], rgba[2], rgba[3])),
        anti_alias: true,
        ..Default::default()
    };
    pix.fill_path(&path, &paint(brush), FillRule::Winding, Transform::identity(), None);
    let stroke = Stroke { width: pen.width.max(1.0) as f32, ..Default::default() };
    pix.stroke_path(&path, &paint(pen.rgba), &stroke, Transform::identity(), None);
    Some(pix)
}

/// `q26Dot6Compare`.
fn same_26dot6(p1: f64, p2: f64) -> bool {
    ((p2 - p1) * 64.0) as i32 == 0
}

/// `qSafeDivide`.
fn safe_divide(x: f64, y: f64) -> f64 {
    if y == 0.0 {
        return if x > 0.0 { 1e20 } else { -1e20 };
    }
    x / y
}

/// `qSafeFloatToQScFixed` (16.16 in 64 bits).
fn to_fixed(x: f64) -> i64 {
    let tmp = x * 65536.0;
    let limit = i32::MAX as f64 * 65536.0;
    if tmp > limit {
        i32::MAX as i64 * 65536
    } else if tmp < i32::MIN as f64 * 65536.0 {
        i32::MIN as i64 * 65536
    } else {
        tmp as i64
    }
}

/// `qClipLine` against (left, top, right, bottom); true when nothing is left.
fn clip_line(p1: &mut (f64, f64), p2: &mut (f64, f64), clip: (f64, f64, f64, f64)) -> bool {
    let (xmin, ymin, xmax, ymax) = clip;
    let (x1, y1) = (&mut p1.0, &mut p1.1);
    let (x2, y2) = (&mut p2.0, &mut p2.1);
    if !(x1.is_finite() && y1.is_finite() && x2.is_finite() && y2.is_finite()) {
        return true;
    }
    if *x1 < xmin {
        if *x2 <= xmin {
            return true;
        }
        *y1 += (*y2 - *y1) / (*x2 - *x1) * (xmin - *x1);
        *x1 = xmin;
    } else if *x1 > xmax {
        if *x2 >= xmax {
            return true;
        }
        *y1 += (*y2 - *y1) / (*x2 - *x1) * (xmax - *x1);
        *x1 = xmax;
    }
    if *x2 < xmin {
        *y2 += (*y2 - *y1) / (*x2 - *x1) * (xmin - *x2);
        *x2 = xmin;
    } else if *x2 > xmax {
        *y2 += (*y2 - *y1) / (*x2 - *x1) * (xmax - *x2);
        *x2 = xmax;
    }
    if *y1 < ymin {
        if *y2 <= ymin {
            return true;
        }
        *x1 += (*x2 - *x1) / (*y2 - *y1) * (ymin - *y1);
        *y1 = ymin;
    } else if *y1 > ymax {
        if *y2 >= ymax {
            return true;
        }
        *x1 += (*x2 - *x1) / (*y2 - *y1) * (ymax - *y1);
        *y1 = ymax;
    }
    if *y2 < ymin {
        *x2 += (*x2 - *x1) / (*y2 - *y1) * (ymin - *y2);
        *y2 = ymin;
    } else if *y2 > ymax {
        *x2 += (*x2 - *x1) / (*y2 - *y1) * (ymax - *y2);
        *y2 = ymax;
    }
    false
}

// ------------------------------------------------------- QCosmeticStroker

const CAP_BEGIN: i32 = 0x1;
const CAP_END: i32 = 0x2;
const TOP_TO_BOTTOM: i32 = 0x1;
const BOTTOM_TO_TOP: i32 = 0x2;
const LEFT_TO_RIGHT: i32 = 0x4;
const RIGHT_TO_LEFT: i32 = 0x8;
const VERTICAL_MASK: i32 = 0x3;
const HORIZONTAL_MASK: i32 = 0xc;
const NO_PIXEL: (i32, i32) = (i32::MIN, i32::MIN);

/// `toF26Dot6`.
fn f26(x: f64) -> i32 {
    (x * 64.0) as i32
}

/// `FDot16FixedDiv`.
fn fixed_div(x: i32, y: i32) -> i64 {
    (x as i64) * (1 << 16) / y as i64
}

fn swap_caps(caps: i32) -> i32 {
    ((caps & CAP_BEGIN) << 1) | ((caps & CAP_END) >> 1)
}

/// `capAdjust`: half a pixel more at a capped end.
fn cap_adjust(caps: i32, x1: &mut i32, x2: &mut i32, y: &mut i64, yinc: i64) {
    if caps & CAP_BEGIN != 0 {
        *x1 -= 32;
        *y -= yinc >> 1;
    }
    if caps & CAP_END != 0 {
        *x2 += 32;
    }
}

/// The aliased `QCosmeticStroker` with the FastDraw pixel writer.
struct CosmeticStroker {
    color: [u8; 4],
    xmin: f64,
    xmax: f64,
    ymin: f64,
    ymax: f64,
    draw_caps: bool,
    /// Cumulative dash pattern in 26.6, forwards and backwards; empty when solid.
    pattern: Vec<i32>,
    reverse_pattern: Vec<i32>,
    pattern_length: i32,
    pattern_offset: i32,
    last_dir: i32,
    last_pixel: (i32, i32),
    last_axis_aligned: bool,
}

struct Dasher {
    enabled: bool,
    reverse: bool,
    offset: i32,
    dash_index: usize,
    dash_on: usize,
}

impl Dasher {
    fn new(s: &mut CosmeticStroker, reverse: bool, start: i32, stop: i32) -> Self {
        if s.pattern.is_empty() {
            return Self { enabled: false, reverse, offset: 0, dash_index: 0, dash_on: 0 };
        }
        let delta = stop - start;
        let (mut offset, dash_on) = if reverse {
            (s.pattern_length - s.pattern_offset - delta - ((start & 63) - 32), 0)
        } else {
            (s.pattern_offset - ((start & 63) - 32), 1)
        };
        offset %= s.pattern_length;
        if offset < 0 {
            offset += s.pattern_length;
        }
        let pattern = if reverse { &s.reverse_pattern } else { &s.pattern };
        let mut dash_index = 0;
        while dash_index < pattern.len() - 1 && offset >= pattern[dash_index] {
            dash_index += 1;
        }
        s.pattern_offset += delta;
        s.pattern_offset %= s.pattern_length;
        Self { enabled: true, reverse, offset, dash_index, dash_on }
    }

    fn on(&self) -> bool {
        !self.enabled || (self.dash_index + self.dash_on) & 1 == 1
    }

    fn adjust(&mut self, s: &CosmeticStroker) {
        if !self.enabled {
            return;
        }
        let pattern = if self.reverse { &s.reverse_pattern } else { &s.pattern };
        self.offset += 64;
        if self.offset >= pattern[self.dash_index] {
            self.dash_index = (self.dash_index + 1) % pattern.len();
        }
        self.offset %= s.pattern_length;
    }
}

impl CosmeticStroker {
    /// `QCosmeticStroker::setup` for a cosmetic pen on `raster`.
    fn new(raster: &Raster, pen: &Pen) -> Self {
        let (mut pattern, mut reverse_pattern) = (Vec::new(), Vec::new());
        let mut pattern_length = 0;
        if let Some(p) = pen.style.pattern() {
            // A pen thinner than 1 dashes in whole pixels.
            for v in p {
                pattern_length += ((v * 64.0) as i32).clamp(1, 65536);
                pattern.push(pattern_length);
            }
            pattern_length = 0;
            for v in p.iter().rev() {
                pattern_length += ((v * 64.0) as i32).clamp(1, 65536);
                reverse_pattern.push(pattern_length);
            }
        }
        let opacity = if pen.width == 0.0 { 256 } else { ((256.0 * pen.width) as i32).clamp(0, 256) };
        // multiplyAlpha256 on the premultiplied 16-bit colour.
        let color = to_argb32(premultiplied64(pen.rgba).map(|v| (v * opacity as u32) >> 8));
        Self {
            color,
            xmin: -1.0,
            xmax: (raster.device.0 - 1 + 2) as f64,
            ymin: -1.0,
            ymax: (raster.device.1 - 1 + 2) as f64,
            draw_caps: true,
            pattern,
            reverse_pattern,
            pattern_length,
            pattern_offset: 0,
            last_dir: 0,
            last_pixel: NO_PIXEL,
            last_axis_aligned: false,
        }
    }

    /// `QCosmeticStroker::drawPath` for polylines (the typed-path branch).
    fn draw_path(mut self, raster: &mut Raster, runs: &[&[(f64, f64)]]) {
        for run in runs {
            if run.is_empty() {
                continue;
            }
            let mut p = run[0];
            self.pattern_offset = 0;
            self.last_pixel = NO_PIXEL;
            let n = run.len();
            let closed = run[0] == run[n - 1];
            if closed && n >= 2 {
                let (p1, p2) = (run[n.saturating_sub(2)], run[n - 1]);
                self.calculate_last_point(p1.0, p1.1, p2.0, p2.1);
            }
            let mut caps = if !closed && self.draw_caps { CAP_BEGIN } else { 0 };
            for (k, &p2) in run.iter().enumerate().skip(1) {
                if !closed && self.draw_caps && k == n - 1 {
                    caps |= CAP_END;
                }
                self.draw_line(raster, p.0, p.1, p2.0, p2.1, caps);
                p = p2;
                caps = 0;
            }
        }
    }

    /// `clipLine`: true when the whole line is clipped away.
    fn clip_line(&mut self, x1: &mut f64, y1: &mut f64, x2: &mut f64, y2: &mut f64) -> bool {
        if !(x1.is_finite() && y1.is_finite() && x2.is_finite() && y2.is_finite()) {
            return true;
        }
        let (xmin, xmax, ymin, ymax) = (self.xmin, self.xmax, self.ymin, self.ymax);
        if *x1 < xmin {
            if *x2 <= xmin {
                self.last_pixel.0 = i32::MIN;
                return true;
            }
            *y1 += (*y2 - *y1) / (*x2 - *x1) * (xmin - *x1);
            *x1 = xmin;
        } else if *x1 > xmax {
            if *x2 >= xmax {
                self.last_pixel.0 = i32::MIN;
                return true;
            }
            *y1 += (*y2 - *y1) / (*x2 - *x1) * (xmax - *x1);
            *x1 = xmax;
        }
        if *x2 < xmin {
            self.last_pixel.0 = i32::MIN;
            *y2 += (*y2 - *y1) / (*x2 - *x1) * (xmin - *x2);
            *x2 = xmin;
        } else if *x2 > xmax {
            self.last_pixel.0 = i32::MIN;
            *y2 += (*y2 - *y1) / (*x2 - *x1) * (xmax - *x2);
            *x2 = xmax;
        }
        if *y1 < ymin {
            if *y2 <= ymin {
                self.last_pixel.0 = i32::MIN;
                return true;
            }
            *x1 += (*x2 - *x1) / (*y2 - *y1) * (ymin - *y1);
            *y1 = ymin;
        } else if *y1 > ymax {
            if *y2 >= ymax {
                self.last_pixel.0 = i32::MIN;
                return true;
            }
            *x1 += (*x2 - *x1) / (*y2 - *y1) * (ymax - *y1);
            *y1 = ymax;
        }
        if *y2 < ymin {
            self.last_pixel.0 = i32::MIN;
            *x2 += (*x2 - *x1) / (*y2 - *y1) * (ymin - *y2);
            *y2 = ymin;
        } else if *y2 > ymax {
            self.last_pixel.0 = i32::MIN;
            *x2 += (*x2 - *x1) / (*y2 - *y1) * (ymax - *y2);
            *y2 = ymax;
        }
        false
    }

    /// `calculateLastPoint`: the last pixel of a closed contour's last segment.
    fn calculate_last_point(&mut self, mut rx1: f64, mut ry1: f64, mut rx2: f64, mut ry2: f64) {
        self.last_pixel = NO_PIXEL;
        if self.clip_line(&mut rx1, &mut ry1, &mut rx2, &mut ry2) {
            return;
        }
        let (mut x1, mut y1, mut x2, mut y2) = (f26(rx1), f26(ry1), f26(rx2), f26(ry2));
        let (dx, dy) = ((x2 - x1).abs(), (y2 - y1).abs());
        if dx < dy {
            let swapped = y1 > y2;
            if swapped {
                std::mem::swap(&mut y1, &mut y2);
                std::mem::swap(&mut x1, &mut x2);
            }
            let xinc = fixed_div(x2 - x1, y2 - y1);
            let mut x = (x1 as i64) << 10;
            let y = (y1 + 32) >> 6;
            let ys = (y2 + 32) >> 6;
            let round = if xinc > 0 { 32 } else { 0 };
            if y != ys {
                x += (((y << 6) + round - y1) as i64 * xinc) >> 6;
                if swapped {
                    self.last_pixel = ((x >> 16) as i32, y);
                    self.last_dir = BOTTOM_TO_TOP;
                } else {
                    self.last_pixel = (((x + (ys - y - 1) as i64 * xinc) >> 16) as i32, ys - 1);
                    self.last_dir = TOP_TO_BOTTOM;
                }
                self.last_axis_aligned = xinc.abs() < (1 << 14);
            }
        } else {
            if dx == 0 {
                return;
            }
            let swapped = x1 > x2;
            if swapped {
                std::mem::swap(&mut x1, &mut x2);
                std::mem::swap(&mut y1, &mut y2);
            }
            let yinc = fixed_div(y2 - y1, x2 - x1);
            let mut y = (y1 as i64) << 10;
            let x = (x1 + 32) >> 6;
            let xs = (x2 + 32) >> 6;
            let round = if yinc > 0 { 32 } else { 0 };
            if x != xs {
                y += (((x << 6) + round - x1) as i64 * yinc) >> 6;
                if swapped {
                    self.last_pixel = (x, (y >> 16) as i32);
                    self.last_dir = RIGHT_TO_LEFT;
                } else {
                    self.last_pixel = (xs - 1, ((y + (xs - x - 1) as i64 * yinc) >> 16) as i32);
                    self.last_dir = LEFT_TO_RIGHT;
                }
                self.last_axis_aligned = yinc.abs() < (1 << 14);
            }
        }
    }

    /// The aliased `drawLine` template, with the dasher when the pen has a pattern.
    #[allow(clippy::too_many_arguments)]
    fn draw_line(&mut self, raster: &mut Raster, mut rx1: f64, mut ry1: f64, mut rx2: f64, mut ry2: f64, mut caps: i32) {
        if self.clip_line(&mut rx1, &mut ry1, &mut rx2, &mut ry2) {
            return;
        }
        let (mut x1, mut y1, mut x2, mut y2) = (f26(rx1), f26(ry1), f26(rx2), f26(ry2));
        let (dx, dy) = ((x2 - x1).abs(), (y2 - y1).abs());
        let mut last = self.last_pixel;
        let color = self.color;

        if dx < dy {
            // vertical
            let mut dir = TOP_TO_BOTTOM;
            let swapped = y1 > y2;
            if swapped {
                std::mem::swap(&mut y1, &mut y2);
                std::mem::swap(&mut x1, &mut x2);
                caps = swap_caps(caps);
                dir = BOTTOM_TO_TOP;
            }
            let xinc = fixed_div(x2 - x1, y2 - y1);
            let mut x = (x1 as i64) << 10;
            if (self.last_dir ^ VERTICAL_MASK) == dir {
                caps |= if swapped { CAP_END } else { CAP_BEGIN };
            }
            cap_adjust(caps, &mut y1, &mut y2, &mut x, xinc);
            let mut y = (y1 + 32) >> 6;
            let mut ys = (y2 + 32) >> 6;
            let round = if xinc > 0 { 32 } else { 0 };
            if caps & CAP_BEGIN != 0 && self.last_pixel.1 == y + 1 {
                y += 1;
            }
            if y != ys {
                x += (((y << 6) + round - y1) as i64 * xinc) >> 6;
                let mut first = ((x >> 16) as i32, y);
                last = (((x + (ys - y - 1) as i64 * xinc) >> 16) as i32, ys - 1);
                if swapped {
                    std::mem::swap(&mut first, &mut last);
                }
                let axis_aligned = xinc.abs() < (1 << 14);
                let lp = self.last_pixel;
                if lp.0 > i32::MIN {
                    if first == lp {
                        // remove duplicated pixel
                        if swapped {
                            ys -= 1;
                        } else {
                            y += 1;
                            x += xinc;
                        }
                    } else if self.last_dir != dir
                        && (((axis_aligned && self.last_axis_aligned) && lp.0 != first.0 && lp.1 != first.1)
                            || ((lp.0 - first.0).abs() > 1 || (lp.1 - first.1).abs() > 1))
                    {
                        // have a missing pixel, insert it
                        if swapped {
                            ys += 1;
                        } else {
                            y -= 1;
                            x -= xinc;
                        }
                    } else if self.last_dir == dir && ((lp.0 - first.0).abs() <= 1 && (lp.1 - first.1).abs() > 1) {
                        x += xinc >> 1;
                        last.0 = if swapped {
                            (x >> 16) as i32
                        } else {
                            ((x + (ys - y - 1) as i64 * xinc) >> 16) as i32
                        };
                    }
                }
                self.last_dir = dir;
                self.last_axis_aligned = axis_aligned;
                let mut dasher = Dasher::new(self, swapped, y << 6, ys << 6);
                loop {
                    if dasher.on() {
                        raster.blend((x >> 16) as i32, y, color);
                    }
                    dasher.adjust(self);
                    x += xinc;
                    y += 1;
                    if y >= ys {
                        break;
                    }
                }
            }
        } else {
            // horizontal
            if dx == 0 {
                return;
            }
            let mut dir = LEFT_TO_RIGHT;
            let swapped = x1 > x2;
            if swapped {
                std::mem::swap(&mut x1, &mut x2);
                std::mem::swap(&mut y1, &mut y2);
                caps = swap_caps(caps);
                dir = RIGHT_TO_LEFT;
            }
            let yinc = fixed_div(y2 - y1, x2 - x1);
            let mut y = (y1 as i64) << 10;
            if (self.last_dir ^ HORIZONTAL_MASK) == dir {
                caps |= if swapped { CAP_END } else { CAP_BEGIN };
            }
            cap_adjust(caps, &mut x1, &mut x2, &mut y, yinc);
            let mut x = (x1 + 32) >> 6;
            let mut xs = (x2 + 32) >> 6;
            let round = if yinc > 0 { 32 } else { 0 };
            if caps & CAP_BEGIN != 0 && self.last_pixel.0 == x + 1 {
                x += 1;
            }
            if x != xs {
                y += (((x << 6) + round - x1) as i64 * yinc) >> 6;
                let mut first = (x, (y >> 16) as i32);
                last = (xs - 1, ((y + (xs - x - 1) as i64 * yinc) >> 16) as i32);
                if swapped {
                    std::mem::swap(&mut first, &mut last);
                }
                let axis_aligned = yinc.abs() < (1 << 14);
                let lp = self.last_pixel;
                if lp.0 > i32::MIN {
                    if first == lp {
                        if swapped {
                            xs -= 1;
                        } else {
                            x += 1;
                            y += yinc;
                        }
                    } else if self.last_dir != dir
                        && (((axis_aligned && self.last_axis_aligned) && lp.0 != first.0 && lp.1 != first.1)
                            || ((lp.0 - first.0).abs() > 1 || (lp.1 - first.1).abs() > 1))
                    {
                        if swapped {
                            xs += 1;
                        } else {
                            x -= 1;
                            y -= yinc;
                        }
                    } else if self.last_dir == dir && ((lp.0 - first.0).abs() <= 1 && (lp.1 - first.1).abs() > 1) {
                        y += yinc >> 1;
                        last.1 = if swapped {
                            (y >> 16) as i32
                        } else {
                            ((y + (xs - x - 1) as i64 * yinc) >> 16) as i32
                        };
                    }
                }
                self.last_dir = dir;
                self.last_axis_aligned = axis_aligned;
                let mut dasher = Dasher::new(self, swapped, x << 6, xs << 6);
                loop {
                    if dasher.on() {
                        raster.blend(x, (y >> 16) as i32, color);
                    }
                    dasher.adjust(self);
                    y += yinc;
                    x += 1;
                    if x >= xs {
                        break;
                    }
                }
            }
        }
        self.last_pixel = last;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn coverage(r: &Raster) -> Vec<(i32, i32)> {
        let w = r.pixmap.width() as i32;
        r.pixmap
            .pixels()
            .iter()
            .enumerate()
            .filter(|(_, p)| p.alpha() > 0)
            .map(|(i, _)| (i as i32 % w + r.ox, i as i32 / w + r.oy))
            .collect()
    }

    #[test]
    fn thin_horizontal_line_covers_its_columns() {
        // Measured from Qt: (10.3, 20.4)→(200.7, 20.4) logical at 1.5× is
        // row 30, columns 15..=301.
        let mut r = Raster::new(600, 300, 0, 0, (600, 300)).unwrap();
        r.curve(&[(15.45, 30.6), (301.05, 30.6)], &Pen::new([255, 0, 0, 255], 1.0));
        let px = coverage(&r);
        assert!(px.iter().all(|p| p.1 == 30));
        assert_eq!(px.first().map(|p| p.0), Some(15));
        assert_eq!(px.last().map(|p| p.0), Some(301));
    }

    #[test]
    fn thin_dots_and_dashes_count_whole_pixels() {
        let mut r = Raster::new(600, 300, 0, 0, (600, 300)).unwrap();
        r.curve(&[(15.45, 30.6), (301.05, 30.6)], &Pen::new([255, 0, 0, 255], 1.0).styled(PenStyle::Dot));
        let cols: Vec<i32> = coverage(&r).iter().map(|p| p.0).collect();
        assert_eq!(&cols[..4], &[15, 18, 21, 24]);
    }

    #[test]
    fn wide_line_is_two_rows() {
        let mut r = Raster::new(600, 300, 0, 0, (600, 300)).unwrap();
        r.curve(&[(15.45, 30.6), (301.05, 30.6)], &Pen::new([255, 0, 0, 255], 2.0));
        let px = coverage(&r);
        let mut rows: Vec<i32> = px.iter().map(|p| p.1).collect();
        rows.dedup();
        assert_eq!(rows, vec![30, 31]);
    }
}
