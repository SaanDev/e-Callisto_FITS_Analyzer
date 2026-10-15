//! The spectrogram image as the full app's canvas puts it on screen.
//!
//! `AcceleratedPlotWidget.update_image` gives pyqtgraph's `ImageItem` the data
//! and a rectangle, and Qt scales the coloured image to the screen with
//! nearest-neighbour sampling. Colouring then sampling is the same as
//! sampling then colouring, so Lite works out, for each screen pixel inside
//! the plot, which data value Qt would show there and colours only those.
//! That keeps a redraw proportional to the window, not to the data.
//!
//! There are two colouring paths, as in the full app:
//!
//! * no NaN and no gap rows: pyqtgraph's own lookup (`rescaleData` in float32,
//!   then the 256-entry table);
//! * otherwise `_rgba_image_from_cmap`: matplotlib's table, transparent NaN,
//!   hatched gap rows. pyqtgraph then applies the image's levels to those
//!   RGBA bytes as well (alpha included), which shifts the colours whenever
//!   the levels are not 0–255. The full app shows that, so Lite does too.

use std::sync::Arc;

use super::Colormap;
use crate::image::{Rgba, RgbaImage};
use crate::radio::array::Array2;
use crate::radio::frequency_axis::invalid_row_mask;
use crate::util::par_rows;

/// `_GAP_FILL_RGBA` and `_GAP_STRIPE_RGBA`: `np.rint([0.70, 0.70, 0.70, 0.78] * 255)` etc.
const GAP_FILL: [u8; 4] = [178, 178, 178, 199];
const GAP_STRIPE: [u8; 4] = [107, 107, 107, 230];

/// The image handed to the canvas.
#[derive(Clone, Debug)]
pub struct ImageData {
    /// Rows are frequency channels (row 0 first in the file), columns time.
    /// Shared with the caller: a redraw does not copy the spectrogram.
    pub data: Arc<Array2>,
    /// `QRectF(x0, y0, x1 - x0, y1 - y0)` in plot coordinates: column 0
    /// starts at `x0`, row 0 at `y0`; either span may be negative.
    pub rect: [f64; 4],
    pub gap_rows: Option<Vec<bool>>,
    invalid_rows: Vec<bool>,
    has_invalid: bool,
}

impl ImageData {
    pub fn new(data: Arc<Array2>, rect: [f64; 4], gap_rows: Option<Vec<bool>>) -> Self {
        Self::reusing(data, rect, gap_rows, None)
    }

    /// [`ImageData::new`], taking the NaN and gap scan from `previous` when it
    /// was made from the same array and mask (a redraw of the same data).
    pub fn reusing(
        data: Arc<Array2>,
        rect: [f64; 4],
        gap_rows: Option<Vec<bool>>,
        previous: Option<&ImageData>,
    ) -> Self {
        let gap_rows = gap_rows.filter(|g| g.len() == data.rows);
        if let Some(prev) = previous
            && Arc::ptr_eq(&prev.data, &data)
            && prev.gap_rows == gap_rows
        {
            return Self {
                data,
                rect,
                gap_rows,
                invalid_rows: prev.invalid_rows.clone(),
                has_invalid: prev.has_invalid,
            };
        }
        let invalid_rows = invalid_row_mask(&data, gap_rows.as_deref());
        let has_invalid = invalid_rows.iter().any(|&m| m) || data.data.iter().any(|v| !v.is_finite());
        Self { data, rect, gap_rows, invalid_rows, has_invalid }
    }

    /// Whether the image takes the NaN/gap colouring path.
    pub fn has_invalid(&self) -> bool {
        self.has_invalid
    }
}

/// Where a view of the plot lands on the screen.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Placement {
    /// The view box in device pixels, relative to the output's first pixel.
    pub left: f64,
    pub top: f64,
    pub width: f64,
    pub height: f64,
    /// The plot range shown there: `[x0, x1, y0, y1]`, y upwards.
    pub view: [f64; 4],
}

/// Which data index each screen pixel centre falls in, along one axis.
///
/// Qt fills a scaled image's columns left-closed (`[left, right)`) and, the
/// plot's y axis being flipped, its rows bottom-closed (`(top, bottom]`).
/// `increasing` says whether the index grows with the pixel position, which
/// decides which end of each cell is closed.
fn indices(count: usize, n: usize, pixel_to_index: impl Fn(f64) -> f64, increasing: bool) -> Vec<i64> {
    (0..count)
        .map(|p| {
            let u = pixel_to_index(p as f64 + 0.5);
            let k = if increasing { u.floor() } else { u.ceil() - 1.0 };
            if k.is_finite() && k >= 0.0 && k < n as f64 { k as i64 } else { -1 }
        })
        .collect()
}

/// `qt_div_255`, as Qt's premultiplication and blending use it.
fn div255(x: u32) -> u32 {
    (x + 128 + ((x + 128) >> 8)) >> 8
}

/// A straight-alpha colour drawn over an opaque background.
fn over(c: [u8; 4], bg: [u8; 3]) -> Rgba {
    let a = c[3] as u32;
    let mut out = [0, 0, 0, 255];
    for i in 0..3 {
        out[i] = (div255(c[i] as u32 * a) + div255(bg[i] as u32 * (255 - a))).min(255) as u8;
    }
    Rgba(out)
}

/// pyqtgraph's `rescaleData` to uint8: float32 `(v - offset) * scale`,
/// clipped to 0..=255, truncated.
fn rescale_u8(v: f32, offset: f32, scale: f32) -> u8 {
    ((v - offset) * scale).clamp(0.0, 255.0) as u8
}

/// `makeARGB` applying 1-D levels to the bytes of an RGBA image.
fn level_table(levels: (f64, f64)) -> [u8; 256] {
    let mut table = [0u8; 256];
    let (lo, mut hi) = levels;
    if lo == 0.0 && hi == 255.0 {
        for (i, t) in table.iter_mut().enumerate() {
            *t = i as u8;
        }
        return table;
    }
    if lo == hi {
        hi = f64::from_bits(hi.to_bits() + 1);
    }
    let rng = if hi - lo == 0.0 { 1.0 } else { hi - lo };
    let (offset, scale) = (lo as f32, (255.0 / rng) as f32);
    for (i, t) in table.iter_mut().enumerate() {
        *t = rescale_u8(i as f32, offset, scale);
    }
    table
}

/// The colour table for one image: what each lookup index, NaN and the two
/// gap shades look like on the canvas background.
struct Palette {
    opaque: [Rgba; 256],
    nan: Rgba,
    pos_inf: Rgba,
    gap: [Rgba; 2],
    exact: bool,
    offset: f32,
    scale: f32,
}

impl Palette {
    fn new(img: &ImageData, levels: (f64, f64), cmap: &Colormap, bg: [u8; 3]) -> Self {
        let (vmin, vmax) = levels;
        let mut opaque = [Rgba::TRANSPARENT; 256];
        if !img.has_invalid {
            // _rescale_and_lookup_float: scale = 256 / rng, offset = vmin.
            let rng = if vmax - vmin == 0.0 { 1.0 } else { vmax - vmin };
            for (i, c) in opaque.iter_mut().enumerate() {
                *c = over(cmap.lut[i], bg);
            }
            let nan = opaque[0];
            return Self {
                opaque,
                nan,
                pos_inf: nan,
                gap: [nan; 2],
                exact: false,
                offset: vmin as f32,
                scale: (256.0 / rng) as f32,
            };
        }
        // _rgba_image_from_cmap, then makeARGB's levels on the bytes.
        let lv = level_table(levels);
        let shade = |c: [u8; 4]| over([lv[c[0] as usize], lv[c[1] as usize], lv[c[2] as usize], lv[c[3] as usize]], bg);
        for (i, c) in opaque.iter_mut().enumerate() {
            *c = shade(cmap.exact[i]);
        }
        let hidden = |c: [u8; 4]| shade([c[0], c[1], c[2], 0]);
        let scale = (vmax - vmin).max(1e-12);
        Self {
            opaque,
            nan: hidden(cmap.exact[0]),
            pos_inf: hidden(cmap.exact[255]),
            gap: [shade(GAP_FILL), shade(GAP_STRIPE)],
            exact: true,
            offset: vmin as f32,
            scale: (cmap.exact.len() as f64 / scale) as f32,
        }
    }

    fn color(&self, v: f32) -> Rgba {
        if !self.exact {
            return self.opaque[rescale_u8(v, self.offset, self.scale) as usize];
        }
        if v.is_nan() {
            return self.nan;
        }
        if v == f32::INFINITY {
            return self.pos_inf;
        }
        let i = (v - self.offset) * self.scale;
        // np.nan_to_num(nan=0, posinf=levels, neginf=0), clip, astype(uint8).
        let i = if i.is_nan() || i == f32::NEG_INFINITY { 0.0 } else { i };
        self.opaque[i.clamp(0.0, 255.0) as usize]
    }
}

/// The image as the screen shows it, `width`×`height` device pixels, over
/// the canvas `background`. Pixels the image does not cover show the background.
pub fn render(
    img: &ImageData,
    levels: (f64, f64),
    cmap: &Colormap,
    place: &Placement,
    width: usize,
    height: usize,
    background: [u8; 3],
) -> RgbaImage {
    let bg = Rgba([background[0], background[1], background[2], 255]);
    let mut pixels = vec![bg; width * height];
    let (rows, cols) = img.data.shape();
    let [x0, y0, w, h] = img.rect;
    let [vx0, vx1, vy0, vy1] = place.view;
    if rows == 0 || cols == 0 || width == 0 || height == 0 || w == 0.0 || h == 0.0 {
        return RgbaImage::new([width, height], pixels);
    }

    let col_of = |cx: f64| {
        let vx = vx0 + (cx - place.left) / place.width * (vx1 - vx0);
        (vx - x0) / w * cols as f64
    };
    let row_of = |cy: f64| {
        let vy = vy1 - (cy - place.top) / place.height * (vy1 - vy0);
        (vy - y0) / h * rows as f64
    };
    let cols_increase = (w > 0.0) == (vx1 > vx0);
    // Rows go down the screen when the image's y runs opposite to the view's.
    let rows_go_down = (h < 0.0) == (vy1 > vy0);
    let col_index = indices(width, cols, col_of, cols_increase);
    let row_index = indices(height, rows, row_of, !rows_go_down);

    let palette = Palette::new(img, levels, cmap, background);
    let gap = img.gap_rows.as_deref();
    let invalid = &img.invalid_rows;
    let data = &img.data;
    par_rows(&mut pixels, width, |py, out| {
        let r = row_index[py];
        if r < 0 {
            return;
        }
        let r = r as usize;
        let row = data.row(r);
        if palette.exact && gap.is_some_and(|g| g[r]) {
            for (px, o) in out.iter_mut().enumerate() {
                let c = col_index[px];
                if c >= 0 {
                    *o = palette.gap[usize::from((r + c as usize) % 6 < 3)];
                }
            }
            return;
        }
        let blank = palette.exact && invalid[r];
        for (px, o) in out.iter_mut().enumerate() {
            let c = col_index[px];
            if c >= 0 {
                *o = if blank { palette.nan_for(row[c as usize]) } else { palette.color(row[c as usize]) };
            }
        }
    });
    RgbaImage::new([width, height], pixels)
}

impl Palette {
    /// A row with no finite value that is not a gap: drawn fully transparent
    /// before the levels are applied.
    fn nan_for(&self, v: f32) -> Rgba {
        if v == f32::INFINITY { self.pos_inf } else { self.nan }
    }
}

/// The colour bar's gradient: `ColorBarItem`'s 1×256 pixmap of pyqtgraph's
/// table, stretched over `width`×`height` device pixels (bottom = index 0).
/// `top` and `bar_height` place the bar's ends relative to the first row.
pub fn render_colorbar(cmap: &Colormap, width: usize, height: usize, top: f64, bar_height: f64) -> RgbaImage {
    let n = cmap.lut.len();
    let mut pixels = Vec::with_capacity(width * height);
    for py in 0..height {
        let cy = py as f64 + 0.5;
        let v = (top + bar_height - cy) / bar_height * n as f64;
        let k = (v.floor().max(0.0) as usize).min(n - 1);
        let c = cmap.lut[k];
        pixels.extend(std::iter::repeat_n(Rgba([c[0], c[1], c[2], 255]), width));
    }
    RgbaImage::new([width, height], pixels)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn place(width: f64, height: f64, view: [f64; 4]) -> Placement {
        Placement { left: 0.0, top: 0.0, width, height, view }
    }

    #[test]
    fn nearest_sampling_and_orientation() {
        // Two channels, descending: row 0 (value 0) is the top one.
        let data = Array2::new(2, 2, vec![0.0, 255.0, 255.0, 0.0]);
        let img = ImageData::new(Arc::new(data), [0.0, 2.0, 2.0, -2.0], None);
        let cmap = Colormap::by_name("viridis");
        let out = render(&img, (0.0, 256.0), &cmap, &place(4.0, 4.0, [0.0, 2.0, 0.0, 2.0]), 4, 4, [255; 3]);
        let px = |x: usize, y: usize| out.pixels[y * 4 + x].0;
        let lo = cmap.lut[0];
        let hi = cmap.lut[255];
        assert_eq!(px(0, 0), lo);
        assert_eq!(px(3, 0), hi);
        assert_eq!(px(0, 3), hi);
        assert_eq!(px(3, 3), lo);
    }

    #[test]
    fn lut_index_is_float32_and_truncated() {
        let data = Array2::new(1, 3, vec![-5.0, 127.0, 300.0]);
        let img = ImageData::new(Arc::new(data), [0.0, 0.0, 3.0, 1.0], None);
        let cmap = Colormap::by_name("jet");
        let out = render(&img, (0.0, 254.0), &cmap, &place(3.0, 1.0, [0.0, 3.0, 0.0, 1.0]), 3, 1, [255; 3]);
        // 127 * 256 / 254 = 128.0
        assert_eq!(out.pixels[1].0, cmap.lut[128]);
        assert_eq!(out.pixels[0].0, cmap.lut[0]);
        assert_eq!(out.pixels[2].0, cmap.lut[255]);
    }

    #[test]
    fn gaps_hatch_and_levels_apply_to_rgba() {
        let data = Array2::new(2, 6, vec![1.0; 12]);
        let img = ImageData::new(Arc::new(data), [0.0, 0.0, 6.0, 2.0], Some(vec![false, true]));
        assert!(img.has_invalid());
        let cmap = Colormap::by_name("viridis");
        // Levels 0–255: the bytes pass through unchanged.
        let out = render(&img, (0.0, 255.0), &cmap, &place(6.0, 2.0, [0.0, 6.0, 0.0, 2.0]), 6, 2, [255; 3]);
        // Row 1 (gap) is drawn at the top with y up and an ascending rect.
        let stripes: Vec<bool> = (0..6).map(|x| out.pixels[x].0 == over(GAP_STRIPE, [255; 3]).0).collect();
        assert_eq!(stripes, vec![true, true, false, false, false, true]);
        // Wider levels shift the matplotlib colours (as pyqtgraph does).
        let shifted = render(&img, (-100.0, 155.0), &cmap, &place(6.0, 2.0, [0.0, 6.0, 0.0, 2.0]), 6, 2, [255; 3]);
        assert_ne!(shifted.pixels[6], out.pixels[6]);
    }

    #[test]
    fn level_table_matches_pyqtgraph() {
        // Measured from pyqtgraph: (10, 100, 200, 255, 0) with levels (-50, 254).
        let t = level_table((-50.0, 254.0));
        assert_eq!([t[10], t[100], t[200], t[255], t[0]], [50, 125, 209, 255, 41]);
    }
}
