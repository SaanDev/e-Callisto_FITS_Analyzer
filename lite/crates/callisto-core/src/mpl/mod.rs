//! The matplotlib figures the full app shows, laid out as matplotlib 3.10
//! lays them out: subplot and gridspec geometry, colour bars made with
//! `make_axes_gridspec`, tick locators and formatters, text alignment with
//! matplotlib's "lp" line metrics, and image resampling (nearest, and the
//! Agg Hanning filter `interpolation='auto'` picks when downsampling); and
//! for the analysis graphs, titles, scatter plots and lines with markers
//! autoscaled with matplotlib's margins, legends ([`legend`]) and labels
//! with superscripts ([`mathtext`]), drawn in matplotlib's z-order.
//!
//! A [`Figure`] is laid out into a [`DrawList`] in logical pixels (y down);
//! the GUI paints it, and exports rasterise it. Text extents come from the
//! caller ([`TextMeasure`]), because only the GUI knows the fonts.

pub mod analysis_figures;
pub mod figures;
pub mod legend;
pub mod mathtext;
pub mod render;
pub mod ticker;

use std::sync::Arc;

use crate::plot::Colormap;
use crate::radio::array::Array2;

pub type Rgba = [u8; 4];

pub const BLACK: Rgba = [0, 0, 0, 255];
pub const WHITE: Rgba = [255, 255, 255, 255];

/// `matplotlib.colors.to_rgba` for `#rrggbb`, with an alpha.
pub fn hex(rgb: u32, alpha: f64) -> Rgba {
    [(rgb >> 16) as u8, (rgb >> 8) as u8, rgb as u8, (alpha * 255.0).round() as u8]
}

/// One line of text at a size as matplotlib 3.10's Agg renderer measures
/// it (`get_text_width_height_descent`): the pen's advance across the line,
/// the height of the inked glyphs and their descent below the baseline.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct TextExtent {
    pub width: f64,
    pub height: f64,
    pub descent: f64,
}

/// The family a text is drawn in: the interface family (the full app's
/// `apply_origin_style` font) or the Origin graphs' family (Arial).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub enum Face {
    #[default]
    Ui,
    Origin,
}

pub trait TextMeasure {
    fn measure(&self, text: &str, size_px: f64, face: Face) -> TextExtent;
    /// A glyph's metrics, for [`mathtext`].
    fn glyph(&self, _c: char, _size_px: f64, _face: Face) -> Option<mathtext::GlyphMetrics> {
        None
    }
    /// Pair kerning in pixels, as FreeType's `KERNING_DEFAULT` gives it.
    fn kerning(&self, _a: char, _b: char, _size_px: f64, _face: Face) -> f64 {
        0.0
    }
    /// mathtext's x-height (`TruetypeFonts.get_xheight`, from the font's
    /// PCLT table).
    fn x_height(&self, size_px: f64, _face: Face) -> f64 {
        0.5 * size_px
    }
}

/// A text's extent as `Text._get_layout` measures it: mathtext's for math
/// strings, with their layout.
fn text_extent(m: &dyn TextMeasure, text: &str, size_px: f64, face: Face) -> (TextExtent, Option<mathtext::MathLayout>) {
    if mathtext::is_math_text(text) {
        let l = mathtext::layout(text, size_px, face, m);
        (TextExtent { width: l.width, height: l.height, descent: l.descent }, Some(l))
    } else {
        (m.measure(text, size_px, face), None)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HAlign {
    Left,
    Center,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VAlign {
    Top,
    Center,
    Baseline,
    CenterBaseline,
    Bottom,
}

/// A text artist: position in some coordinate system, alignment, rotation.
#[derive(Clone, Debug)]
pub struct Label {
    pub text: String,
    pub size_pt: f64,
    pub color: Rgba,
    pub face: Face,
}

impl Label {
    pub fn new(text: impl Into<String>, size_pt: f64, color: Rgba) -> Self {
        Label { text: text.into(), size_pt, color, face: Face::Ui }
    }

    pub fn with_face(mut self, face: Face) -> Self {
        self.face = face;
        self
    }
}

/// How an image is resampled to screen pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Interpolation {
    Nearest,
    /// `'auto'`: nearest when zoomed in more than 3×, else Hanning.
    Auto,
}

/// `imshow`: data mapped through a colour map, NaN transparent.
#[derive(Clone)]
pub struct Image {
    pub data: Arc<Array2>,
    /// `[left, right, bottom, top]` in data units.
    pub extent: [f64; 4],
    /// `origin='upper'`: row 0 at the top of the extent.
    pub origin_upper: bool,
    pub cmap: Colormap,
    pub vmin: f64,
    pub vmax: f64,
    pub interpolation: Interpolation,
}

#[derive(Clone, Debug)]
pub enum Ticks {
    /// `AutoLocator`.
    Auto,
    Fixed(Vec<f64>),
    None,
}

#[derive(Clone)]
pub enum Formatter {
    Scalar,
    Func(Arc<dyn Fn(f64) -> String + Send + Sync>),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    /// Bottom for x, left for y.
    Low,
    /// Top for x, right for y.
    High,
}

#[derive(Clone)]
pub struct AxisSpec {
    pub major: Ticks,
    /// `AutoMinorLocator` when `Ticks::Auto`.
    pub minor: Ticks,
    pub formatter: Formatter,
    pub label: Option<Label>,
    pub label_side: Side,
    pub tick_label_pt: f64,
    pub tick_color: Rgba,
    pub label_color: Rgba,
    /// Tick marks on the low and high side.
    pub ticks_low: bool,
    pub ticks_high: bool,
    pub labels_low: bool,
    pub labels_high: bool,
    pub inward: bool,
    pub major_len_pt: f64,
    pub minor_len_pt: f64,
    pub major_width_pt: f64,
    pub minor_width_pt: f64,
    pub pad_pt: f64,
    pub labelpad_pt: f64,
    /// The tick labels' family.
    pub face: Face,
}

impl AxisSpec {
    /// The full app's style (`apply_origin_style`): ticks inward on both
    /// sides, minor ticks on, 12 pt labels.
    pub fn origin(color: Rgba) -> Self {
        AxisSpec {
            major: Ticks::Auto,
            minor: Ticks::Auto,
            formatter: Formatter::Scalar,
            label: None,
            label_side: Side::Low,
            tick_label_pt: 12.0,
            tick_color: color,
            label_color: color,
            ticks_low: true,
            ticks_high: true,
            labels_low: true,
            labels_high: false,
            inward: true,
            major_len_pt: 6.0,
            minor_len_pt: 3.0,
            major_width_pt: 0.8,
            minor_width_pt: 0.6,
            pad_pt: 3.5,
            labelpad_pt: 4.0,
            face: Face::Ui,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Grid {
    pub major: bool,
    pub color: Rgba,
    pub width_pt: f64,
}

/// Text placed in axes fractions (`ax.text(..., transform=ax.transAxes)`).
#[derive(Clone, Debug)]
pub struct AxesText {
    pub x: f64,
    pub y: f64,
    pub label: Label,
    pub ha: HAlign,
    pub va: VAlign,
}

/// A marker's shape.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MarkerKind {
    /// `'s'`.
    Square,
    /// `'o'`.
    Circle,
    /// `'*'`: a five-pointed star.
    Star,
}

/// A line's markers: shape, size in points, colours and edge width.
#[derive(Clone, Debug)]
pub struct Marker {
    pub kind: MarkerKind,
    pub size_pt: f64,
    pub face: Rgba,
    pub edge: Rgba,
    pub edge_width_pt: f64,
}

/// `ax.scatter` with one size and colour: circles `sqrt(s)` points across,
/// edged in the face colour.
#[derive(Clone, Debug)]
pub struct Scatter {
    pub x: Arc<Vec<f64>>,
    pub y: Arc<Vec<f64>>,
    /// `s`, in points².
    pub size_pt2: f64,
    pub color: Rgba,
    pub edge_width_pt: f64,
    pub zorder: f64,
}

impl Scatter {
    pub fn new(x: Vec<f64>, y: Vec<f64>, size_pt2: f64, color: Rgba) -> Self {
        Scatter { x: Arc::new(x), y: Arc::new(y), size_pt2, color, edge_width_pt: 1.0, zorder: 1.0 }
    }
}

/// `ax.plot`: a solid line (or none) with optional markers.
#[derive(Clone, Debug)]
pub struct Line2D {
    pub x: Arc<Vec<f64>>,
    pub y: Arc<Vec<f64>>,
    pub color: Rgba,
    /// A solid line of this width in points; `None` for `linestyle='none'`.
    pub width_pt: Option<f64>,
    pub marker: Option<Marker>,
    pub label: Option<String>,
    pub zorder: f64,
}

#[derive(Clone)]
pub struct Axes {
    /// `[left, bottom, width, height]` in figure fractions.
    pub position: [f64; 4],
    /// `set_box_aspect` and `set_anchor`.
    pub box_aspect: Option<(f64, (f64, f64))>,
    pub xlim: (f64, f64),
    pub ylim: (f64, f64),
    pub facecolor: Option<Rgba>,
    pub images: Vec<Image>,
    pub x: AxisSpec,
    pub y: AxisSpec,
    pub grids: Vec<Grid>,
    pub texts: Vec<AxesText>,
    pub spine_color: Rgba,
    pub spine_width_pt: f64,
    /// `ax.set_title` (centred, `axes.titlepad` above the axes).
    pub title: Option<Label>,
    pub title_pad_pt: f64,
    pub scatters: Vec<Scatter>,
    pub lines: Vec<Line2D>,
    pub legend: Option<legend::Legend>,
    /// The axis artists' z-order (grid, ticks and labels): 1.5 for
    /// `axes.axisbelow = 'line'`.
    pub axis_zorder: f64,
}

impl Axes {
    /// An empty axes at `position` with the given axis styles.
    pub fn new(position: [f64; 4], x: AxisSpec, y: AxisSpec) -> Self {
        Axes {
            position,
            box_aspect: None,
            xlim: (0.0, 1.0),
            ylim: (0.0, 1.0),
            facecolor: None,
            images: Vec::new(),
            x,
            y,
            grids: Vec::new(),
            texts: Vec::new(),
            spine_color: BLACK,
            spine_width_pt: 1.5,
            title: None,
            title_pad_pt: 6.0,
            scatters: Vec::new(),
            lines: Vec::new(),
            legend: None,
            axis_zorder: 1.5,
        }
    }

    /// `autoscale_view` after plotting: the data's extent (scatter offsets
    /// and line vertices) with `axes.xmargin`/`ymargin` (0.05) on each side.
    pub fn autoscale(&mut self) {
        let (mut x0, mut x1, mut y0, mut y1) = (f64::INFINITY, f64::NEG_INFINITY, f64::INFINITY, f64::NEG_INFINITY);
        let pairs = self
            .scatters
            .iter()
            .map(|s| (s.x.clone(), s.y.clone()))
            .chain(self.lines.iter().map(|l| (l.x.clone(), l.y.clone())));
        for (xs, ys) in pairs {
            for (&x, &y) in xs.iter().zip(ys.iter()) {
                if x.is_finite() && y.is_finite() {
                    x0 = x0.min(x);
                    x1 = x1.max(x);
                    y0 = y0.min(y);
                    y1 = y1.max(y);
                }
            }
        }
        if x0.is_finite() {
            self.xlim = auto_limits(x0, x1);
            self.ylim = auto_limits(y0, y1);
        }
    }
}

/// One axis of `autoscale_view`: the locator's `nonsingular`, the margin,
/// then `view_limits` (`axes.autolimit_mode = 'data'`).
fn auto_limits(lo: f64, hi: f64) -> (f64, f64) {
    let (lo, hi) = ticker::nonsingular(lo, hi, 0.05, 1e-15);
    let delta = (hi - lo) * 0.05;
    ticker::nonsingular(lo - delta, hi + delta, 0.05, 1e-15)
}

/// `fig.text`.
#[derive(Clone, Debug)]
pub struct FigText {
    pub x: f64,
    pub y: f64,
    pub label: Label,
    pub ha: HAlign,
    pub va: VAlign,
    pub rotation: f64,
}

#[derive(Clone)]
pub struct Figure {
    /// Logical pixels; `dpi` is matplotlib's at a device ratio of 1.
    pub width: f64,
    pub height: f64,
    pub dpi: f64,
    pub facecolor: Rgba,
    pub axes: Vec<Axes>,
    pub texts: Vec<FigText>,
}

// ------------------------------------------------------------------ draw list

#[derive(Clone, Debug, PartialEq)]
pub struct PxRect {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
}

/// One glyph of a math label: its offset from the label's pen (y down,
/// from the baseline) and size.
#[derive(Clone, Debug, PartialEq)]
pub struct GlyphRun {
    pub c: char,
    pub dx: f64,
    pub dy: f64,
    pub size_px: f64,
}

#[derive(Clone)]
pub enum Prim {
    Fill { rect: PxRect, color: Rgba },
    Line { a: (f64, f64), b: (f64, f64), color: Rgba, width: f64, square_caps: bool },
    /// An image in its extent's pixel rectangle, clipped to `clip`.
    Image { axes: usize, image: usize, dest: PxRect, clip: PxRect },
    /// Text with its pen origin on the baseline at `pos`, rotated
    /// counter-clockwise by `rotation` degrees about that point; `bbox` is
    /// its window extent as matplotlib computes it. A math label carries
    /// its glyphs as mathtext placed them (plain text has none).
    Text { pos: (f64, f64), text: String, size_px: f64, color: Rgba, rotation: f64, bbox: PxRect, face: Face, glyphs: Vec<GlyphRun> },
    /// Markers centred at `centers`, `size` across (a square's side, a
    /// circle's diameter), as Agg draws them: `draw_markers` (`snapped`:
    /// each marker stamped at a whole device pixel, its path snapped when
    /// it can be) or a path collection (exact positions).
    Markers { kind: MarkerKind, centers: Arc<Vec<(f64, f64)>>, size: f64, face: Rgba, edge: Rgba, edge_width: f64, snapped: bool, clip: Option<PxRect> },
    /// A solid polyline: projecting caps, round joins.
    Path { points: Arc<Vec<(f64, f64)>>, color: Rgba, width: f64, clip: Option<PxRect> },
    /// A rectangle patch, filled and edged, snapped as Agg snaps it.
    Frame { rect: PxRect, face: Rgba, edge: Rgba, width: f64 },
}

pub struct DrawList {
    pub width: f64,
    pub height: f64,
    pub prims: Vec<Prim>,
    /// The axes rectangles, for images and hit testing.
    pub axes_rects: Vec<PxRect>,
}

fn pt(f: &Figure, points: f64) -> f64 {
    points * f.dpi / 72.0
}

/// The line metrics matplotlib aligns text with: the larger of the text's
/// ink and that of "lp".
fn line_metrics(m: &dyn TextMeasure, text: &str, size_px: f64, face: Face) -> (TextExtent, f64, f64, Option<mathtext::MathLayout>) {
    let lp = m.measure("lp", size_px, face);
    let (e, math) = if text.is_empty() { (TextExtent::default(), None) } else { text_extent(m, text, size_px, face) };
    (e, e.height.max(lp.height), e.descent.max(lp.descent), math)
}

/// Where Agg's `draw_text_image` puts rotated text relative to the exact
/// spot, in device pixels (y down) for a rotation in degrees: its bitmap
/// carries a pixel of padding under the ink and is pasted a pixel low, so
/// the ink lands a pixel back along the text and a pixel toward the
/// ascent (at 90°: one left, one down). Unrotated text is blitted exactly.
pub fn agg_rotated_text_offset(rotation: f64) -> (f64, f64) {
    if rotation.abs() < 1e-9 {
        return (0.0, 0.0);
    }
    let (s, c) = rotation.to_radians().sin_cos();
    (-c - s, s - c)
}

/// Places a one-line text like `Text._get_layout` + `draw`. Returns the
/// primitive and the text's window extent (unrotated or rotated by 90°).
#[allow(clippy::too_many_arguments)]
fn place_text(
    m: &dyn TextMeasure,
    anchor: (f64, f64),
    label: &Label,
    size_px: f64,
    ha: HAlign,
    va: VAlign,
    rotation: f64,
    rotation_anchor: bool,
) -> (Prim, PxRect) {
    let (e, h, d, math) = line_metrics(m, &label.text, size_px, label.face);
    let w = e.width;
    // The layout box in text coordinates (y up), baseline of the line at
    // y = -(h - d) from the box top at 0.
    let (xmin, ymin, height) = (0.0, -h, h);
    let baseline = h - d;
    let rad = rotation.to_radians();
    let (c, s) = (rad.cos(), rad.sin());
    let rot = |x: f64, y: f64| (x * c - y * s, x * s + y * c);
    let offx = |w: f64| match ha {
        HAlign::Left => xmin,
        HAlign::Center => xmin + w / 2.0,
        HAlign::Right => xmin + w,
    };
    let offy = |ymin: f64, height: f64| match va {
        VAlign::Center => ymin + height / 2.0,
        VAlign::Top => ymin + height,
        VAlign::Baseline => ymin + height - baseline,
        VAlign::CenterBaseline => ymin + height - baseline / 2.0,
        VAlign::Bottom => ymin,
    };
    // Corners of the unrotated box, rotated.
    let corners = [rot(0.0, 0.0), rot(w, 0.0), rot(w, -h), rot(0.0, -h)];
    let (ox, oy) = if rotation_anchor {
        // Align the unrotated box, then rotate about the anchor.
        rot(offx(w), offy(ymin, height))
    } else {
        let bx0 = corners.iter().map(|p| p.0).fold(f64::INFINITY, f64::min);
        let bx1 = corners.iter().map(|p| p.0).fold(f64::NEG_INFINITY, f64::max);
        let by0 = corners.iter().map(|p| p.1).fold(f64::INFINITY, f64::min);
        let by1 = corners.iter().map(|p| p.1).fold(f64::NEG_INFINITY, f64::max);
        let x = match ha {
            HAlign::Left => bx0,
            HAlign::Center => (bx0 + bx1) / 2.0,
            HAlign::Right => bx1,
        };
        let y = match va {
            VAlign::Center => (by0 + by1) / 2.0,
            VAlign::Top => by1,
            VAlign::Baseline => by1 - baseline,
            VAlign::CenterBaseline => by1 - baseline / 2.0,
            VAlign::Bottom => by0,
        };
        (x, y)
    };
    // Screen (y down): text-space point p lands at anchor + (p.x - ox, -(p.y - oy)).
    let to_screen = |p: (f64, f64)| (anchor.0 + p.0 - ox, anchor.1 - (p.1 - oy));
    // The pen starts at the box's left edge on the baseline; Agg puts each
    // glyph's ink at its own bearing from there.
    let pos = to_screen(rot(0.0, -baseline));
    let pts: Vec<(f64, f64)> = corners.iter().map(|&p| to_screen(p)).collect();
    let bbox = PxRect {
        x0: pts.iter().map(|p| p.0).fold(f64::INFINITY, f64::min),
        y0: pts.iter().map(|p| p.1).fold(f64::INFINITY, f64::min),
        x1: pts.iter().map(|p| p.0).fold(f64::NEG_INFINITY, f64::max),
        y1: pts.iter().map(|p| p.1).fold(f64::NEG_INFINITY, f64::max),
    };
    let glyphs = math
        .map(|l| {
            l.glyphs
                .iter()
                .filter(|g| g.c != ' ')
                .map(|g| GlyphRun { c: g.c, dx: g.x, dy: g.y - l.box_height, size_px: g.size_px })
                .collect()
        })
        .unwrap_or_default();
    let prim = Prim::Text { pos, text: label.text.clone(), size_px, color: label.color, rotation, bbox: bbox.clone(), face: label.face, glyphs };
    (prim, bbox)
}

/// `Bbox.shrunk_to_aspect` + `anchored`, in figure fractions.
fn apply_box_aspect(pos: [f64; 4], aspect: f64, anchor: (f64, f64), fig_aspect: f64) -> [f64; 4] {
    let [l, b, w, h] = pos;
    let mut hh = w * aspect / fig_aspect;
    let ww = if hh <= h {
        w
    } else {
        hh = h;
        h * fig_aspect / aspect
    };
    [l + anchor.0 * (w - ww), b + anchor.1 * (h - hh), ww, hh]
}

/// `GridSpec.get_grid_positions`: `(bottoms, tops, lefts, rights)` of each
/// row and column within `[left, bottom, right, top]`.
pub fn grid_positions(
    bounds: [f64; 4],
    nrows: usize,
    ncols: usize,
    wspace: f64,
    hspace: f64,
    width_ratios: &[f64],
    height_ratios: &[f64],
) -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>) {
    let [left, bottom, right, top] = bounds;
    let (tot_w, tot_h) = (right - left, top - bottom);
    let cell_h = tot_h / (nrows as f64 + hspace * (nrows as f64 - 1.0));
    let sep_h = hspace * cell_h;
    let hr: Vec<f64> = if height_ratios.is_empty() { vec![1.0; nrows] } else { height_ratios.to_vec() };
    let norm_h = cell_h * nrows as f64 / hr.iter().sum::<f64>();
    let cell_w = tot_w / (ncols as f64 + wspace * (ncols as f64 - 1.0));
    let sep_w = wspace * cell_w;
    let wr: Vec<f64> = if width_ratios.is_empty() { vec![1.0; ncols] } else { width_ratios.to_vec() };
    let norm_w = cell_w * ncols as f64 / wr.iter().sum::<f64>();
    let (mut bottoms, mut tops, mut lefts, mut rights) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let mut acc = 0.0;
    for (i, r) in hr.iter().enumerate() {
        acc += if i == 0 { 0.0 } else { sep_h };
        tops.push(top - acc);
        acc += r * norm_h;
        bottoms.push(top - acc);
    }
    let mut acc = 0.0;
    for (i, r) in wr.iter().enumerate() {
        acc += if i == 0 { 0.0 } else { sep_w };
        lefts.push(left + acc);
        acc += r * norm_w;
        rights.push(left + acc);
    }
    (bottoms, tops, lefts, rights)
}

/// `fig.add_subplot(111)` + `fig.colorbar(im, ax=ax)`: the main axes and
/// the colour bar's axes (with its box aspect), in figure fractions.
/// A colour bar's box aspect and anchor (`make_axes_gridspec`).
pub type BarAspect = (f64, (f64, f64));

pub fn subplot_with_colorbar() -> ([f64; 4], [f64; 4], BarAspect) {
    let (left, bottom, right, top) = (0.125, 0.11, 0.9, 0.88);
    let (fraction, pad, shrink, aspect) = (0.15, 0.05, 1.0, 20.0);
    let wh_space = 2.0 * pad / (1.0 - pad);
    let (_, _, lefts, rights) =
        grid_positions([left, bottom, right, top], 1, 2, wh_space, 0.0, &[1.0 - fraction - pad, fraction], &[]);
    let pad_s = (1.0 - shrink) * 0.5;
    let (bottoms, tops, _, _) =
        grid_positions([lefts[1], bottom, rights[1], top], 3, 1, 0.0, 0.0, &[], &[pad_s, shrink, pad_s]);
    let main = [lefts[0], bottom, rights[0] - lefts[0], top - bottom];
    let cbar = [lefts[1], bottoms[1], rights[1] - lefts[1], tops[1] - bottoms[1]];
    (main, cbar, (aspect, (0.0, 0.5)))
}

struct AxisTicks {
    major: Vec<(f64, String)>,
    minor: Vec<f64>,
}

fn axis_ticks(spec: &AxisSpec, lim: (f64, f64), length_pt: f64, x_axis: bool) -> AxisTicks {
    let (lo, hi) = if lim.0 <= lim.1 { lim } else { (lim.1, lim.0) };
    let majors: Vec<f64> = match &spec.major {
        Ticks::Auto => {
            let nbins = ticker::auto_bins(length_pt, spec.tick_label_pt, x_axis);
            ticker::max_n_ticks(lo, hi, nbins, &ticker::AUTO_STEPS)
        }
        Ticks::Fixed(v) => v.clone(),
        Ticks::None => Vec::new(),
    };
    let labels: Vec<String> = match &spec.formatter {
        Formatter::Scalar => ticker::scalar_format(&majors, lim).0,
        Formatter::Func(f) => majors.iter().map(|&v| f(v)).collect(),
    };
    let minor = match &spec.minor {
        Ticks::Auto => ticker::auto_minor_ticks(&majors, lim),
        Ticks::Fixed(v) => {
            let tol = (hi - lo) * 1e-5;
            v.iter().copied().filter(|m| !majors.iter().any(|j| (m - j).abs() <= tol)).collect()
        }
        Ticks::None => Vec::new(),
    };
    let visible = |v: f64| ticker::interval_contains_close(lo, hi, v);
    AxisTicks {
        major: majors.into_iter().zip(labels).filter(|(v, _)| visible(*v)).collect(),
        minor: minor.into_iter().filter(|v| visible(*v)).collect(),
    }
}

impl Figure {
    /// Lays the figure out in logical pixels.
    pub fn layout(&self, m: &dyn TextMeasure) -> DrawList {
        let mut prims = vec![Prim::Fill {
            rect: PxRect { x0: 0.0, y0: 0.0, x1: self.width, y1: self.height },
            color: self.facecolor,
        }];
        let mut axes_rects = Vec::new();
        let fig_aspect = self.height / self.width;
        for (ai, ax) in self.axes.iter().enumerate() {
            let pos = match ax.box_aspect {
                Some((aspect, anchor)) => apply_box_aspect(ax.position, aspect, anchor, fig_aspect),
                None => ax.position,
            };
            let r = PxRect {
                x0: pos[0] * self.width,
                x1: (pos[0] + pos[2]) * self.width,
                y0: self.height - (pos[1] + pos[3]) * self.height,
                y1: self.height - pos[1] * self.height,
            };
            axes_rects.push(r.clone());
            self.layout_axes(ai, ax, &r, m, &mut prims);
        }
        for t in &self.texts {
            let anchor = (t.x * self.width, self.height - t.y * self.height);
            let (prim, _) =
                place_text(m, anchor, &t.label, pt(self, t.label.size_pt), t.ha, t.va, t.rotation, false);
            prims.push(prim);
        }
        DrawList { width: self.width, height: self.height, prims, axes_rects }
    }

    /// Lays out one axes: its patch, then its artists in z-order as
    /// `Axes.draw` sorts them (stable, children before spines, axes,
    /// title and legend at equal z-order).
    fn layout_axes(&self, ai: usize, ax: &Axes, r: &PxRect, m: &dyn TextMeasure, out: &mut Vec<Prim>) {
        let to_x = |v: f64| r.x0 + (v - ax.xlim.0) / (ax.xlim.1 - ax.xlim.0) * (r.x1 - r.x0);
        let to_y = |v: f64| r.y1 - (v - ax.ylim.0) / (ax.ylim.1 - ax.ylim.0) * (r.y1 - r.y0);
        if let Some(face) = ax.facecolor {
            out.push(Prim::Fill { rect: r.clone(), color: face });
        }
        let mut groups: Vec<(f64, Vec<Prim>)> = Vec::new();
        for (ii, im) in ax.images.iter().enumerate() {
            let [l, rt, b, t] = im.extent;
            let dest = PxRect { x0: to_x(l), x1: to_x(rt), y0: to_y(t), y1: to_y(b) };
            groups.push((0.0, vec![Prim::Image { axes: ai, image: ii, dest, clip: r.clone() }]));
        }
        // What a "best" legend avoids: line vertices and scatter offsets.
        let mut avoid = legend::Avoid::default();
        for s in &ax.scatters {
            let centers: Vec<(f64, f64)> = s
                .x
                .iter()
                .zip(s.y.iter())
                .filter(|(x, y)| x.is_finite() && y.is_finite())
                .map(|(&x, &y)| (to_x(x), to_y(y)))
                .collect();
            avoid.offsets.extend(centers.iter().copied());
            let prim = Prim::Markers {
                kind: MarkerKind::Circle,
                centers: Arc::new(centers),
                size: pt(self, s.size_pt2.sqrt()),
                face: s.color,
                edge: s.color,
                edge_width: pt(self, s.edge_width_pt),
                // One path in one colour: `Collection.draw` hands it to
                // `draw_markers` (`do_single_path_optimization`).
                snapped: true,
                clip: Some(r.clone()),
            };
            groups.push((s.zorder, vec![prim]));
        }
        for l in &ax.lines {
            let points: Vec<(f64, f64)> = l
                .x
                .iter()
                .zip(l.y.iter())
                .filter(|(x, y)| x.is_finite() && y.is_finite())
                .map(|(&x, &y)| (to_x(x), to_y(y)))
                .collect();
            let points = Arc::new(points);
            avoid.lines.push(points.clone());
            let mut g = Vec::new();
            if let Some(w) = l.width_pt {
                g.push(Prim::Path { points: points.clone(), color: l.color, width: pt(self, w), clip: Some(r.clone()) });
            }
            if let Some(mk) = &l.marker {
                g.push(self.markers(mk, points, Some(r.clone())));
            }
            groups.push((l.zorder, g));
        }
        let mut texts = Vec::new();
        for t in &ax.texts {
            let anchor = (r.x0 + t.x * (r.x1 - r.x0), r.y1 - t.y * (r.y1 - r.y0));
            let (p, bb) = place_text(m, anchor, &t.label, pt(self, t.label.size_pt), t.ha, t.va, 0.0, false);
            avoid.bboxes.push(bb);
            texts.push(p);
        }
        groups.push((3.0, texts));
        let mut axis = Vec::new();
        let mut spines = Vec::new();
        let x_high = self.layout_axis(ax, r, m, &mut axis, &mut spines);
        groups.push((2.5, spines));
        groups.push((ax.axis_zorder, axis));
        if let Some(title) = &ax.title {
            groups.push((3.0, vec![self.title(ax, title, r, x_high, m)]));
        }
        if let Some(leg) = &ax.legend {
            groups.push((5.0, legend::layout(self, ax, r, leg, m, &avoid)));
        }
        groups.sort_by(|a, b| a.0.total_cmp(&b.0));
        out.extend(groups.into_iter().flat_map(|g| g.1));
    }

    /// A line's markers as `draw_markers` draws them.
    fn markers(&self, mk: &Marker, centers: Arc<Vec<(f64, f64)>>, clip: Option<PxRect>) -> Prim {
        Prim::Markers {
            kind: mk.kind,
            centers,
            size: pt(self, mk.size_pt),
            face: mk.face,
            edge: mk.edge,
            edge_width: pt(self, mk.edge_width_pt),
            snapped: true,
            clip,
        }
    }

    /// The title, `_update_title_position`: `title_pad` above the axes, or
    /// above what sits on top of it (top tick labels), raised further when
    /// its descent would still reach below that.
    fn title(&self, ax: &Axes, title: &Label, r: &PxRect, top: f64, m: &dyn TextMeasure) -> Prim {
        let size = pt(self, title.size_pt);
        let pad = pt(self, ax.title_pad_pt);
        let place = |y: f64| place_text(m, ((r.x0 + r.x1) / 2.0, y), title, size, HAlign::Center, VAlign::Baseline, 0.0, false);
        let (mut p, bb) = place(r.y0 - pad);
        if bb.y1 > top {
            let (p2, bb2) = place(top - pad);
            p = p2;
            if bb2.y1 > top {
                p = place(top - pad - (bb2.y1 - top)).0;
            }
        }
        p
    }

    /// The axis artists (grid, ticks, tick and axis labels) and spines.
    /// Returns the top of what sits above the axes, for the title.
    fn layout_axis(&self, ax: &Axes, r: &PxRect, m: &dyn TextMeasure, prims: &mut Vec<Prim>, spines: &mut Vec<Prim>) -> f64 {
        let to_x = |v: f64| r.x0 + (v - ax.xlim.0) / (ax.xlim.1 - ax.xlim.0) * (r.x1 - r.x0);
        let to_y = |v: f64| r.y1 - (v - ax.ylim.0) / (ax.ylim.1 - ax.ylim.0) * (r.y1 - r.y0);
        let (wpx, hpx) = (r.x1 - r.x0, r.y1 - r.y0);
        let xt = axis_ticks(&ax.x, ax.xlim, wpx * 72.0 / self.dpi, true);
        let yt = axis_ticks(&ax.y, ax.ylim, hpx * 72.0 / self.dpi, false);

        for g in &ax.grids {
            let w = pt(self, g.width_pt);
            let xs: Vec<f64> = if g.major { xt.major.iter().map(|t| t.0).collect() } else { xt.minor.clone() };
            let ys: Vec<f64> = if g.major { yt.major.iter().map(|t| t.0).collect() } else { yt.minor.clone() };
            for v in xs {
                let x = to_x(v);
                prims.push(Prim::Line { a: (x, r.y0), b: (x, r.y1), color: g.color, width: w, square_caps: false });
            }
            for v in ys {
                let y = to_y(v);
                prims.push(Prim::Line { a: (r.x0, y), b: (r.x1, y), color: g.color, width: w, square_caps: false });
            }
        }

        // Tick marks.
        let tick = |spec: &AxisSpec, at: f64, major: bool, x_axis: bool, prims: &mut Vec<Prim>| {
            let len = pt(self, if major { spec.major_len_pt } else { spec.minor_len_pt });
            let width = pt(self, if major { spec.major_width_pt } else { spec.minor_width_pt });
            let sign = if spec.inward { 1.0 } else { -1.0 };
            if x_axis {
                let x = to_x(at);
                if spec.ticks_low {
                    prims.push(Prim::Line { a: (x, r.y1), b: (x, r.y1 - sign * len), color: spec.tick_color, width, square_caps: false });
                }
                if spec.ticks_high {
                    prims.push(Prim::Line { a: (x, r.y0), b: (x, r.y0 + sign * len), color: spec.tick_color, width, square_caps: false });
                }
            } else {
                let y = to_y(at);
                if spec.ticks_low {
                    prims.push(Prim::Line { a: (r.x0, y), b: (r.x0 + sign * len, y), color: spec.tick_color, width, square_caps: false });
                }
                if spec.ticks_high {
                    prims.push(Prim::Line { a: (r.x1, y), b: (r.x1 - sign * len, y), color: spec.tick_color, width, square_caps: false });
                }
            }
        };
        for &v in &xt.minor {
            tick(&ax.x, v, false, true, prims);
        }
        for (v, _) in &xt.major {
            tick(&ax.x, *v, true, true, prims);
        }
        for &v in &yt.minor {
            tick(&ax.y, v, false, false, prims);
        }
        for (v, _) in &yt.major {
            tick(&ax.y, *v, true, false, prims);
        }

        // Spines (square caps, centred on the axes edges).
        let sw = pt(self, ax.spine_width_pt);
        for (a, b) in [((r.x0, r.y1), (r.x1, r.y1)), ((r.x0, r.y0), (r.x1, r.y0)), ((r.x0, r.y0), (r.x0, r.y1)), ((r.x1, r.y0), (r.x1, r.y1))] {
            spines.push(Prim::Line { a, b, color: ax.spine_color, width: sw, square_caps: true });
        }

        // Tick labels, and the extents the axis labels are placed beyond.
        let pad = |spec: &AxisSpec| pt(self, spec.pad_pt + if spec.inward { 0.0 } else { spec.major_len_pt });
        let (mut x_low, mut x_high) = (r.y1, r.y0);
        if ax.x.labels_low || ax.x.labels_high {
            for (v, text) in &xt.major {
                let label = Label::new(text.clone(), ax.x.tick_label_pt, ax.x.label_color).with_face(ax.x.face);
                let size = pt(self, label.size_pt);
                if ax.x.labels_low {
                    let (p, bb) = place_text(m, (to_x(*v), r.y1 + pad(&ax.x)), &label, size, HAlign::Center, VAlign::Top, 0.0, false);
                    x_low = x_low.max(bb.y1);
                    prims.push(p);
                }
                if ax.x.labels_high {
                    let (p, bb) = place_text(m, (to_x(*v), r.y0 - pad(&ax.x)), &label, size, HAlign::Center, VAlign::Bottom, 0.0, false);
                    x_high = x_high.min(bb.y0);
                    prims.push(p);
                }
            }
        }
        let (mut y_low, mut y_high) = (r.x0, r.x1);
        if ax.y.labels_low || ax.y.labels_high {
            for (v, text) in &yt.major {
                let label = Label::new(text.clone(), ax.y.tick_label_pt, ax.y.label_color).with_face(ax.y.face);
                let size = pt(self, label.size_pt);
                if ax.y.labels_low {
                    let (p, bb) = place_text(m, (r.x0 - pad(&ax.y), to_y(*v)), &label, size, HAlign::Right, VAlign::CenterBaseline, 0.0, false);
                    y_low = y_low.min(bb.x0);
                    prims.push(p);
                }
                if ax.y.labels_high {
                    let (p, bb) = place_text(m, (r.x1 + pad(&ax.y), to_y(*v)), &label, size, HAlign::Left, VAlign::CenterBaseline, 0.0, false);
                    y_high = y_high.max(bb.x1);
                    prims.push(p);
                }
            }
        }
        let mut x_top = if ax.x.labels_high { x_high } else { r.y0 };
        if let Some(label) = &ax.x.label {
            let size = pt(self, label.size_pt);
            let labelpad = pt(self, ax.x.labelpad_pt);
            let (p, bb) = match ax.x.label_side {
                Side::Low => place_text(m, ((r.x0 + r.x1) / 2.0, x_low + labelpad), label, size, HAlign::Center, VAlign::Top, 0.0, false),
                Side::High => place_text(m, ((r.x0 + r.x1) / 2.0, x_high - labelpad), label, size, HAlign::Center, VAlign::Bottom, 0.0, false),
            };
            if ax.x.label_side == Side::High {
                x_top = x_top.min(bb.y0);
            }
            prims.push(p);
        }
        if let Some(label) = &ax.y.label {
            let size = pt(self, label.size_pt);
            let labelpad = pt(self, ax.y.labelpad_pt);
            let cy = (r.y0 + r.y1) / 2.0;
            let (p, _) = match ax.y.label_side {
                Side::Low => place_text(m, (y_low - labelpad, cy), label, size, HAlign::Center, VAlign::Bottom, 90.0, true),
                Side::High => place_text(m, (y_high + labelpad, cy), label, size, HAlign::Center, VAlign::Top, 90.0, true),
            };
            prims.push(p);
        }
        x_top
    }
}

// ------------------------------------------------------------------ images

/// Agg's Hanning filter (radius 1).
fn hanning(x: f64) -> f64 {
    if x.abs() >= 1.0 { 0.0 } else { 0.5 + 0.5 * (std::f64::consts::PI * x).cos() }
}

/// For each output pixel, the source indices and normalised weights.
fn filter_weights(n_src: usize, n_out: usize, src_per_out: f64, src_offset: f64, hann: bool) -> Vec<Vec<(usize, f32)>> {
    let scale = src_per_out.abs().max(1.0);
    (0..n_out)
        .map(|o| {
            // Centre of output pixel `o` in source coordinates.
            let u = src_offset + (o as f64 + 0.5) * src_per_out;
            if !hann {
                let j = u.floor();
                return if j >= 0.0 && (j as usize) < n_src { vec![(j as usize, 1.0)] } else { Vec::new() };
            }
            let radius = scale;
            let lo = ((u - radius - 0.5).floor().max(0.0)) as usize;
            let hi = ((u + radius + 0.5).ceil().min(n_src as f64)) as usize;
            let mut w: Vec<(usize, f32)> = (lo..hi)
                .filter_map(|j| {
                    let x = (j as f64 + 0.5 - u) / scale;
                    let v = hanning(x);
                    (v > 0.0).then_some((j, v as f32))
                })
                .collect();
            let sum: f32 = w.iter().map(|p| p.1).sum();
            if sum > 0.0 {
                for p in &mut w {
                    p.1 /= sum;
                }
            }
            w
        })
        .collect()
}

/// An image's pixels for a device-pixel rectangle: `dest` is where the
/// image's extent lands (device pixels, possibly beyond the window) and
/// `out` the region to fill, both `[x0, y0, x1, y1]`. Returns RGBA8 rows.
pub fn rasterize_image(im: &Image, dest: [f64; 4], out: [i64; 4]) -> (usize, usize, Vec<u8>) {
    let (ow, oh) = ((out[2] - out[0]).max(0) as usize, (out[3] - out[1]).max(0) as usize);
    let mut px = vec![0u8; ow * oh * 4];
    let data = &im.data;
    if ow == 0 || oh == 0 || data.cols == 0 || data.rows == 0 {
        return (ow, oh, px);
    }
    let (dw, dh) = (dest[2] - dest[0], dest[3] - dest[1]);
    // Source columns/rows per device pixel; x runs left→right with the
    // extent, rows top→bottom for origin='upper' when the extent is
    // top > bottom (dest is already in screen orientation).
    let cpp = data.cols as f64 / dw;
    let rpp = data.rows as f64 / dh;
    let hann = match im.interpolation {
        Interpolation::Nearest => false,
        Interpolation::Auto => {
            let near = |d: f64, n: f64| d > 3.0 * n || d == n || d == 2.0 * n;
            !(near(dw.abs(), data.cols as f64) && near(dh.abs(), data.rows as f64))
        }
    };
    let cols = filter_weights(data.cols, ow, cpp, (out[0] as f64 - dest[0]) * cpp, hann);
    let rows = filter_weights(data.rows, oh, rpp, (out[1] as f64 - dest[1]) * rpp, hann);
    // Colour-map the data once (the RGBA interpolation stage), as
    // `Normalize` and `Colormap.__call__` do it in float32.
    let lut = im.cmap.exact;
    let n = lut.len() as f32;
    let (vmin, span) = (im.vmin as f32, (im.vmax - im.vmin) as f32);
    let rgba: Vec<[f32; 4]> = data
        .data
        .iter()
        .map(|&v| {
            if !v.is_finite() {
                return [0.0; 4];
            }
            let mut xa = if span != 0.0 { (v - vmin) / span * n } else { 0.0 };
            if xa == n {
                xa = n - 1.0;
            }
            let i = if xa < 0.0 { 0 } else if xa >= n { lut.len() - 1 } else { xa as usize };
            let c = lut[i];
            [c[0] as f32, c[1] as f32, c[2] as f32, c[3] as f32]
        })
        .collect();
    let row_idx = |r: usize| if im.origin_upper { r } else { data.rows - 1 - r };
    for (oy, rw) in rows.iter().enumerate() {
        // Horizontal pass for the rows this output row needs.
        let mut acc_rows: Vec<[f32; 4]> = vec![[0.0; 4]; ow];
        for &(r, wr) in rw {
            let src = &rgba[row_idx(r) * data.cols..(row_idx(r) + 1) * data.cols];
            for (ox, cw) in cols.iter().enumerate() {
                let mut c = [0f32; 4];
                for &(j, wc) in cw {
                    let p = src[j];
                    for k in 0..4 {
                        c[k] += p[k] * wc;
                    }
                }
                for k in 0..4 {
                    acc_rows[ox][k] += c[k] * wr;
                }
            }
        }
        // `to_rgba(bytes=True)` truncates the resampled floats.
        for (ox, c) in acc_rows.iter().enumerate() {
            let o = (oy * ow + ox) * 4;
            for k in 0..4 {
                px[o + k] = (c[k] + 1e-3).floor().clamp(0.0, 255.0) as u8;
            }
        }
    }
    (ow, oh, px)
}

/// Where Agg puts an image (`_ImageBase._make_image` with
/// `round_to_pixel_border`, then `RendererAgg.draw_image`), in device
/// pixels with y down: `dest` is the image's extent, `clip` the axes box.
/// The output is as many whole pixels as the clipped box needs, pasted at
/// the truncated corner and clipped to the rounded axes box. Returns that
/// pixel rectangle and the extent as the resampling sees it.
pub fn image_placement(dest: [f64; 4], clip: [f64; 4]) -> Option<([i64; 4], [f64; 4])> {
    let (cx0, cx1) = (dest[0].max(clip[0]), dest[2].min(clip[2]));
    let (cy0, cy1) = (dest[1].max(clip[1]), dest[3].min(clip[3]));
    if cx1 <= cx0 || cy1 <= cy0 {
        return None;
    }
    let (cw, ch) = (cx1 - cx0, cy1 - cy0);
    let (out_w, out_h) = if cw.fract() != 0.0 || ch.fract() != 0.0 { (cw.ceil(), ch.ceil()) } else { (cw, ch) };
    let (sx, sy) = (out_w / cw, out_h / ch);
    // Where the full app's Qt canvas shows it: the corner rounded (measured
    // against the captured previews: truncating lands a pixel up and left).
    let draw_x = cx0.round();
    let draw_y = (cy1 - out_h).round();
    let vx = |x: f64| draw_x + (x - cx0) * sx;
    let vy = |y: f64| draw_y + out_h - (cy1 - y) * sy;
    let virtual_dest = [vx(dest[0]), vy(dest[1]), vx(dest[2]), vy(dest[3])];
    // set_clipbox rounds the clip rectangle to whole pixels.
    let round = |v: f64| (v + 0.5).floor();
    let out = [
        draw_x.max(round(clip[0])) as i64,
        draw_y.max(round(clip[1])) as i64,
        (draw_x + out_w).min(round(clip[2])) as i64,
        (draw_y + out_h).min(round(clip[3])) as i64,
    ];
    (out[2] > out[0] && out[3] > out[1]).then_some((out, virtual_dest))
}

/// A colour bar's colours, bottom (low) to top, as a 256 × 1 image.
pub fn colorbar_image(cmap: &Colormap, vmin: f64, vmax: f64) -> Image {
    let n = cmap.lut.len();
    let values: Vec<f32> = (0..n).rev().map(|i| (vmin + (i as f64 + 0.5) / n as f64 * (vmax - vmin)) as f32).collect();
    Image {
        data: Arc::new(Array2::new(n, 1, values)),
        extent: [0.0, 1.0, vmin, vmax],
        origin_upper: true,
        cmap: *cmap,
        vmin,
        vmax,
        interpolation: Interpolation::Nearest,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixed;
    impl TextMeasure for Fixed {
        fn measure(&self, text: &str, size: f64, _face: Face) -> TextExtent {
            TextExtent { width: text.chars().count() as f64 * size * 0.5, height: size, descent: size * 0.2 }
        }
    }

    #[test]
    fn images_land_where_agg_puts_them() {
        // The BIR preview at 1.5×: axes from x 163.875, y 86.4 to 976.69, 640.8.
        let (out, v) = image_placement([163.875, 86.4, 976.6875, 640.8], [163.875, 86.4, 976.6875, 640.8]).unwrap();
        assert_eq!(out, [164, 86, 977, 641]);
        assert!((v[0] - 164.0).abs() < 1e-9 && (v[2] - 977.0).abs() < 1e-9, "{v:?}");
        assert!((v[1] - 86.0).abs() < 1e-9 && (v[3] - 641.0).abs() < 1e-9, "{v:?}");
    }

    #[test]
    fn colorbar_geometry_like_matplotlib() {
        let (main, cbar, (aspect, anchor)) = subplot_with_colorbar();
        assert!((main[0] - 0.125).abs() < 1e-12);
        assert!((main[2] - 0.62).abs() < 1e-3, "{main:?}");
        assert!((cbar[0] - 0.78375).abs() < 1e-3, "{cbar:?}");
        assert!((cbar[2] - 0.11625).abs() < 1e-3);
        // 874 × 480 canvas: the bar is 18.48 px wide, full height.
        let fig_aspect = 480.0 / 874.0;
        let b = apply_box_aspect(cbar, aspect, anchor, fig_aspect);
        assert!((b[2] * 874.0 - 0.77 * 480.0 / 20.0).abs() < 1e-6);
        assert!((b[0] - cbar[0]).abs() < 1e-12);
    }

    #[test]
    fn ylabel_sits_left_of_tick_labels() {
        let (main, _, _) = subplot_with_colorbar();
        let mut ax = Axes { xlim: (0.0, 1797.0), ylim: (10.0, 110.0), ..Axes::new(main, AxisSpec::origin(BLACK), AxisSpec::origin(BLACK)) };
        ax.y.label = Some(Label::new("Frequency [MHz]", 12.0, BLACK));
        let fig = Figure { width: 874.0, height: 480.0, dpi: 100.0, facecolor: WHITE, axes: vec![ax], texts: Vec::new() };
        let list = fig.layout(&Fixed);
        let texts: Vec<_> = list
            .prims
            .iter()
            .filter_map(|p| if let Prim::Text { pos, text, rotation, .. } = p { Some((pos.0, text.clone(), *rotation)) } else { None })
            .collect();
        let label_x = texts.iter().find(|t| t.1 == "Frequency [MHz]").unwrap().0;
        let tick_x = texts.iter().filter(|t| t.2 == 0.0 && t.1 == "100").map(|t| t.0).next().unwrap();
        assert!(label_x < tick_x);
        // 0..1600 s and 20..100 MHz.
        assert_eq!(texts.iter().filter(|t| t.2 == 0.0).count(), 9 + 5);
    }
}
