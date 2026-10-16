//! The axis logic of pyqtgraph 0.14.0 (`pyqtgraph/graphicsItems/AxisItem.py`),
//! which the full app's hardware canvas draws its axes with.
//!
//! Lite draws the same canvas, so tick positions, tick text, which labels are
//! dropped for crowding, the automatic SI prefix and the way an axis grows to
//! fit its text are reproduced here operation for operation. Drawing itself
//! is left to the caller: [`Axis::generate`] returns the lines and text boxes
//! in the axis' own coordinates, as `generateDrawSpecs` does.

use crate::radio::frequency_axis::format_g;

/// `fn.siScale`: the factor and prefix that bring `x` into 1..1000.
pub fn si_scale(x: f64) -> (f64, String) {
    const PREFIXES: [&str; 17] = ["y", "z", "a", "f", "p", "n", "µ", "m", "", "k", "M", "G", "T", "P", "E", "Z", "Y"];
    if !x.is_finite() {
        return (1.0, String::new());
    }
    let m = if x.abs() < 1e-25 { 0 } else { ((x.abs().ln() / 1000f64.ln()).floor()).clamp(-9.0, 9.0) as i32 };
    let prefix = if m == 0 {
        String::new()
    } else if !(-8..=8).contains(&m) {
        format!("e{}", m * 3)
    } else {
        PREFIXES[(m + 8) as usize].to_owned()
    };
    (10f64.powf((-3 * m) as f64), prefix)
}

/// `math.frexp` for positive finite values: `x = m * 2**e`, `0.5 <= m < 1`.
fn frexp_exponent(x: f64) -> i32 {
    if x == 0.0 || !x.is_finite() {
        return 0;
    }
    let bits = x.abs().to_bits();
    let exp = ((bits >> 52) & 0x7ff) as i32;
    if exp == 0 {
        // Subnormal: scale into the normal range first.
        return frexp_exponent(x * 2f64.powi(64)) - 64;
    }
    exp - 1022
}

/// `AxisItem.tickSpacing`: `(spacing, offset)` for each tick level.
// pyqtgraph divides by this rounded log2(10), so Lite does too.
#[allow(clippy::approx_constant)]
pub fn tick_spacing(min: f64, max: f64, size: f64, density: f64, max_tick_level: usize) -> Vec<(f64, f64)> {
    let dif = (max - min).abs();
    if dif == 0.0 {
        return Vec::new();
    }
    let ref_size = 300.0;
    let min_intervals = f64::max(2.25, 2.25 * density * (size / ref_size).sqrt());
    let major_max_spacing = dif / min_intervals;

    let exp2 = frexp_exponent(major_max_spacing);
    let mut p10unit = 10f64.powf((((exp2 - 1) as f64) / 3.32192809488736).floor() - 1.0);
    let mut major_factor = 10.0;
    if 100.0 * p10unit <= major_max_spacing {
        major_factor = 10.0;
        p10unit *= 10.0;
    } else {
        for f in [50.0, 20.0, 10.0] {
            major_factor = f;
            if f * p10unit <= major_max_spacing {
                break;
            }
        }
    }
    let major = major_factor * p10unit;
    let mut levels = vec![(major, 0.0)];

    let minor_min_spacing = 2.0 * dif / size;
    let mut minor = major;
    if max_tick_level >= 1 {
        let trials: &[f64] = if major_factor == 10.0 { &[5.0, 10.0] } else { &[10.0, 20.0, 50.0] };
        for f in trials {
            minor = f * p10unit;
            if minor >= minor_min_spacing {
                break;
            }
        }
        levels.push((minor, 0.0));
    }
    if max_tick_level >= 2 {
        let trials: &[f64] = if major_factor == 10.0 {
            &[1.0, 2.0, 5.0, 10.0]
        } else if major_factor == 20.0 {
            &[2.0, 5.0, 10.0, 20.0]
        } else {
            &[5.0, 10.0, 50.0]
        };
        let mut extra = minor;
        for f in trials {
            extra = f * p10unit;
            if extra >= minor_min_spacing || extra == minor {
                break;
            }
        }
        if extra < minor {
            levels.push((extra, 0.0));
        }
    }
    levels
}

/// `AxisItem.tickValues`: `(spacing, values)` for each level, without the
/// values an earlier (longer) level already has.
#[allow(clippy::identity_op)]
pub fn tick_values(min: f64, max: f64, size: f64, scale: f64) -> Vec<(f64, Vec<f64>)> {
    let (lo, hi) = if min <= max { (min, max) } else { (max, min) };
    let (lo, hi) = (lo * scale, hi * scale);
    let mut all: Vec<f64> = Vec::new();
    let mut ticks = Vec::new();
    for (spacing, offset) in tick_spacing(lo, hi, size, 1.0, 2) {
        // `math.ceil` returns an int, so there is no negative zero.
        let start = ((lo - offset) / spacing).ceil() * spacing + offset + 0.0;
        let num = ((hi - start) / spacing).trunc() as i64 + 1;
        let atol = spacing / scale * 0.01;
        let values: Vec<f64> = (0..num.max(0))
            .map(|i| (i as f64 * spacing + start) / scale)
            .filter(|v| !all.iter().any(|a| (a - v).abs() <= atol))
            .collect();
        all.extend(&values);
        ticks.push((spacing / scale, values));
    }
    ticks
}

/// `AxisItem.tickStrings`.
pub fn tick_strings(values: &[f64], scale: f64, spacing: f64) -> Vec<String> {
    let places = (-(spacing * scale).log10()).ceil().max(0.0) as usize;
    values
        .iter()
        .map(|v| {
            let vs = v * scale;
            if vs.abs() < 0.001 || vs.abs() >= 10000.0 { format_g(vs, 6) } else { format!("{vs:.places$}") }
        })
        .collect()
}

/// `_TimeAxisItem.tickStrings` in UT mode: `HH:MM`, or `HH:MM:SS` once the
/// ticks are 30 s apart or closer.
pub fn ut_tick_strings(values: &[f64], spacing: f64, ut_start_sec: f64) -> Vec<String> {
    let show_seconds = spacing <= 30.0;
    values
        .iter()
        .map(|v| {
            let total = (ut_start_sec + v).round_ties_even() as i64;
            let hours = total.div_euclid(3600).rem_euclid(24);
            let minutes = total.rem_euclid(3600).div_euclid(60);
            let seconds = total.rem_euclid(60);
            if show_seconds {
                format!("{hours:02}:{minutes:02}:{seconds:02}")
            } else {
                format!("{hours:02}:{minutes:02}")
            }
        })
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Orientation {
    Left,
    Right,
    Top,
    Bottom,
}

impl Orientation {
    fn vertical(self) -> bool {
        matches!(self, Orientation::Left | Orientation::Right)
    }
}

/// How the tick text of an axis is produced.
#[derive(Clone, Debug, PartialEq)]
pub enum TickText {
    /// Numbers, as `AxisItem.tickStrings` writes them.
    Numbers,
    /// `_TimeAxisItem` in UT mode: the start of the observation in seconds of the day.
    Ut(f64),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Rect {
    pub fn new(x: f64, y: f64, w: f64, h: f64) -> Self {
        Self { x, y, w, h }
    }

    pub fn right(&self) -> f64 {
        self.x + self.w
    }

    pub fn bottom(&self) -> f64 {
        self.y + self.h
    }

    /// `br & rect == rect` with `QRectF`'s fuzzy comparison.
    fn contains_rect(&self, r: &Rect) -> bool {
        let fuzzy_le = |a: f64, b: f64| a <= b || (a - b).abs() <= 1e-12 * a.abs().max(b.abs()).max(1.0);
        fuzzy_le(self.x, r.x)
            && fuzzy_le(self.y, r.y)
            && fuzzy_le(r.right(), self.right())
            && fuzzy_le(r.bottom(), self.bottom())
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Line {
    pub x1: f64,
    pub y1: f64,
    pub x2: f64,
    pub y2: f64,
    /// Pen alpha: 255 for the axis line and major ticks, less for minor ones.
    pub alpha: u8,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TextAlign {
    /// Right edge at the rect's right, centred vertically (left axis).
    RightVCenter,
    /// Left edge at the rect's left, centred vertically (right axis).
    LeftVCenter,
    /// Centred horizontally, bottom at the rect's bottom (top axis).
    HCenterBottom,
    /// Centred horizontally, top at the rect's top (bottom axis).
    HCenterTop,
}

#[derive(Clone, Debug, PartialEq)]
pub struct TextSpec {
    pub rect: Rect,
    pub align: TextAlign,
    pub text: String,
}

/// What [`Axis::generate`] produces: everything an axis draws, in its own
/// coordinates (origin at its top-left).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct AxisDraw {
    pub line: Option<Line>,
    pub ticks: Vec<Line>,
    pub texts: Vec<TextSpec>,
}

/// One pyqtgraph `AxisItem`, with the state that persists between paints.
#[derive(Clone, Debug)]
pub struct Axis {
    pub orientation: Orientation,
    pub visible: bool,
    pub show_values: bool,
    pub tick_length: f64,
    pub tick_text_offset: [f64; 2],
    pub text: TickText,
    /// `setTicks`: explicit levels of `(value, text)`. `Some(vec![])` draws no ticks.
    pub explicit_ticks: Option<Vec<Vec<(f64, String)>>>,
    pub fixed_width: Option<f64>,
    pub fixed_height: Option<f64>,
    /// Space kept for tick text, which follows the text drawn last.
    pub text_width: f64,
    pub text_height: f64,
    pub label_text: String,
    pub auto_si_prefix: bool,
    pub range: (f64, f64),
    si_prefix_scale: f64,
}

impl Axis {
    pub fn new(orientation: Orientation) -> Self {
        Self {
            orientation,
            visible: true,
            show_values: true,
            tick_length: -5.0,
            tick_text_offset: [5.0, 2.0],
            text: TickText::Numbers,
            explicit_ticks: None,
            fixed_width: None,
            fixed_height: None,
            text_width: 30.0,
            text_height: 18.0,
            label_text: String::new(),
            auto_si_prefix: true,
            range: (0.0, 1.0),
            si_prefix_scale: 1.0,
        }
    }

    pub fn label_visible(&self) -> bool {
        !self.label_text.is_empty()
    }

    /// `setRange`, including `updateAutoSIPrefix`.
    pub fn set_range(&mut self, lo: f64, hi: f64) {
        if !lo.is_finite() || !hi.is_finite() {
            return;
        }
        self.range = (lo, hi);
        let mut scale = 1.0;
        if self.auto_si_prefix && self.label_visible() {
            let value = lo.abs().max(hi.abs());
            // Without units the prefix only applies below 1 and from 1e9.
            if (0.0..=1.0).contains(&value) || value >= 1e9 {
                scale = si_scale(value).0;
            }
        }
        self.si_prefix_scale = if self.auto_si_prefix { scale } else { 1.0 };
    }

    pub fn si_prefix_scale(&self) -> f64 {
        self.si_prefix_scale
    }

    /// `labelString` without the HTML: the label text and any `(x…)` scale.
    /// pyqtgraph always puts a space after the text, which counts towards
    /// the label's width.
    pub fn label_string(&self) -> String {
        let units = if !self.auto_si_prefix || self.si_prefix_scale == 1.0 {
            String::new()
        } else {
            format!("(x{})", format_g(1.0 / self.si_prefix_scale, 6))
        };
        format!("{} {}", self.label_text, units)
    }

    /// `_updateWidth`: the width a vertical axis takes in the layout.
    /// `label_height` is the label's `boundingRect().height()`.
    pub fn width(&self, label_height: f64) -> f64 {
        if !self.visible {
            return 0.0;
        }
        if let Some(w) = self.fixed_width {
            return w;
        }
        let mut w = if self.show_values { self.text_width + self.tick_text_offset[0] } else { 0.0 };
        w += self.tick_length.max(0.0);
        if self.label_visible() {
            w += label_height * 0.8;
        }
        w
    }

    /// `_updateHeight`: the height a horizontal axis takes in the layout.
    pub fn height(&self, label_height: f64) -> f64 {
        if !self.visible {
            return 0.0;
        }
        if let Some(h) = self.fixed_height {
            return h;
        }
        let mut h = if self.show_values { self.text_height + self.tick_text_offset[1] } else { 0.0 };
        h += self.tick_length.max(0.0);
        if self.label_visible() {
            h += label_height * 0.8;
        }
        h
    }

    /// `resizeEvent`: the label's position in axis coordinates. For a
    /// vertical axis the label is rotated -90°, so this is its bottom-left.
    pub fn label_pos(&self, w: f64, h: f64, label_w: f64, label_h: f64) -> (f64, f64) {
        let nudge = 5.0;
        let trunc = |v: f64| v.trunc();
        match self.orientation {
            Orientation::Left => (-nudge, trunc(h / 2.0 + label_w / 2.0)),
            Orientation::Right => (trunc(w - label_h + nudge), trunc(h / 2.0 + label_w / 2.0)),
            Orientation::Top => (trunc(w / 2.0 - label_w / 2.0), -nudge),
            Orientation::Bottom => (trunc(w / 2.0 - label_w / 2.0), trunc(h - label_h + nudge)),
        }
    }

    /// `boundingRect` in axis coordinates.
    fn bounding_rect(&self, w: f64, h: f64) -> Rect {
        let m = if self.orientation.vertical() { 15.0 } else { 0.0 };
        let tl = self.tick_length.min(0.0);
        match self.orientation {
            Orientation::Left => Rect::new(0.0, -m, w - tl, h + 2.0 * m),
            Orientation::Right => Rect::new(tl, -m, w - tl, h + 2.0 * m),
            Orientation::Top => Rect::new(-m, 0.0, w + 2.0 * m, h - tl),
            Orientation::Bottom => Rect::new(-m, tl, w + 2.0 * m, h - tl),
        }
    }

    fn strings(&self, values: &[f64], spacing: f64) -> Vec<String> {
        match self.text {
            TickText::Ut(start) => ut_tick_strings(values, spacing, start),
            TickText::Numbers => tick_strings(values, self.si_prefix_scale, spacing),
        }
    }

    /// `generateDrawSpecs` for an axis of size `w`×`h`.
    ///
    /// `measure` returns a tick string's `(width, height)` as
    /// `QPainter.boundingRect` does: its advance and the font's line height.
    /// Like pyqtgraph, this updates the space kept for tick text, so call
    /// [`Axis::width`]/[`Axis::height`] again afterwards and lay out anew
    /// when they changed.
    #[allow(clippy::type_complexity)]
    pub fn generate(&mut self, w: f64, h: f64, measure: &dyn Fn(&str) -> (f64, f64)) -> AxisDraw {
        let mut out = AxisDraw::default();
        let vertical = self.orientation.vertical();
        let (span0, span1, tick_start, tick_stop, tick_dir) = match self.orientation {
            Orientation::Left => ((w - 1.0, -1.0), (w - 1.0, h + 1.0), w, w, -1.0),
            Orientation::Right => ((1.0, -1.0), (1.0, h + 1.0), 0.0, 0.0, 1.0),
            Orientation::Top => ((-1.0, h - 1.0), (w + 1.0, h - 1.0), h, h, -1.0),
            Orientation::Bottom => ((-1.0, 1.0), (w + 1.0, 1.0), 0.0, 0.0, 1.0),
        };
        let length = (span1.0 - span0.0).hypot(span1.1 - span0.1);
        if length == 0.0 {
            return out;
        }

        let (levels, given_strings): (Vec<(f64, Vec<f64>)>, Option<Vec<Vec<String>>>) = match &self.explicit_ticks {
            None => (tick_values(self.range.0, self.range.1, length, 1.0), None),
            Some(explicit) => (
                explicit.iter().map(|level| (f64::NAN, level.iter().map(|(v, _)| *v).collect())).collect(),
                Some(explicit.iter().map(|level| level.iter().map(|(_, s)| s.clone()).collect()).collect()),
            ),
        };

        let dif = self.range.1 - self.range.0;
        let (x_scale, offset) = if dif == 0.0 {
            (1.0, 0.0)
        } else if vertical {
            let s = -h / dif;
            (s, self.range.0 * s - h)
        } else {
            let s = w / dif;
            (s, self.range.0 * s)
        };
        let xr0 = self.range.0 * x_scale - offset;
        let xr1 = self.range.1 * x_scale - offset;
        let (x_min, x_max) = (xr0.min(xr1), xr0.max(xr1));

        let mut positions: Vec<Vec<Option<f64>>> = Vec::new();
        for (i, (_, values)) in levels.iter().enumerate() {
            let tick_length = self.tick_length / (i as f64 * 0.5 + 1.0);
            let alpha = (255.0 / (i as f64 + 1.0)) as u8;
            let mut row = Vec::with_capacity(values.len());
            for v in values {
                let x = v * x_scale - offset;
                if x < x_min || x > x_max {
                    row.push(None);
                    continue;
                }
                row.push(Some(x));
                let end = tick_stop + tick_length * tick_dir;
                out.ticks.push(if vertical {
                    Line { x1: tick_start, y1: x, x2: end, y2: x, alpha }
                } else {
                    Line { x1: x, y1: tick_start, x2: x, y2: end, alpha }
                });
            }
            positions.push(row);
        }
        out.line = Some(Line { x1: span0.0, y1: span0.1, x2: span1.0, y2: span1.1, alpha: 255 });

        if !self.show_values {
            return out;
        }

        let axis = if vertical { 0 } else { 1 };
        let text_offset = self.tick_text_offset[axis];
        let br = self.bounding_rect(w, h);
        let fill_limits = [(0usize, 0.8), (2, 0.6), (4, 0.4), (6, 0.2)];
        let mut all_rects: Vec<(f64, f64)> = Vec::new();
        let mut last_size2 = 0.0;
        for i in 0..levels.len().min(3) {
            let mut strings: Vec<Option<String>> = match &given_strings {
                Some(given) => given[i].iter().cloned().map(Some).collect(),
                None => self.strings(&levels[i].1, levels[i].0).into_iter().map(Some).collect(),
            };
            if strings.is_empty() {
                continue;
            }
            for (j, s) in strings.iter_mut().enumerate() {
                if positions[i].get(j).copied().flatten().is_none() {
                    *s = None;
                }
            }
            let rects: Vec<Option<(f64, f64)>> = strings
                .iter()
                .map(|s| {
                    s.as_ref().map(|s| {
                        let (tw, th) = measure(s);
                        (tw, th * 0.8)
                    })
                })
                .collect();
            all_rects.extend(rects.iter().flatten());
            let (text_size, text_size2) = if all_rects.is_empty() {
                (0.0, 0.0)
            } else if vertical {
                (all_rects.iter().map(|r| r.1).sum::<f64>(), all_rects.iter().map(|r| r.0).fold(f64::MIN, f64::max))
            } else {
                (all_rects.iter().map(|r| r.0).sum::<f64>(), all_rects.iter().map(|r| r.1).fold(f64::MIN, f64::max))
            };
            if i > 0 {
                let ratio = text_size / length;
                if fill_limits.iter().any(|(n, limit)| out.texts.len() >= *n && ratio >= *limit) {
                    break;
                }
            }
            last_size2 = text_size2;

            for (j, s) in strings.into_iter().enumerate() {
                let (Some(s), Some((tw, th))) = (s, rects[j]) else { continue };
                let x = positions[i][j].unwrap();
                let off = self.tick_length.max(0.0) + text_offset;
                let (rect, align) = match self.orientation {
                    Orientation::Left => {
                        (Rect::new(tick_stop - off - tw, x - th / 2.0, tw, th), TextAlign::RightVCenter)
                    }
                    Orientation::Right => (Rect::new(tick_stop + off, x - th / 2.0, tw, th), TextAlign::LeftVCenter),
                    Orientation::Top => {
                        (Rect::new(x - tw / 2.0, tick_stop - off - th, tw, th), TextAlign::HCenterBottom)
                    }
                    Orientation::Bottom => (Rect::new(x - tw / 2.0, tick_stop + off, tw, th), TextAlign::HCenterTop),
                };
                if !br.contains_rect(&rect) {
                    continue;
                }
                out.texts.push(TextSpec { rect, align, text: s });
            }
        }
        self.update_max_text_size(last_size2);
        out
    }

    /// `_updateMaxTextSize` with `autoReduceTextSpace`.
    fn update_max_text_size(&mut self, x: f64) {
        let current = if self.orientation.vertical() { &mut self.text_width } else { &mut self.text_height };
        if x > *current || x < *current - 10.0 {
            *current = x;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spacing_matches_pyqtgraph() {
        // The single-file reference: 30–90.09 MHz over 771 px, 0–899.75 s over 1130.9 px.
        let left = tick_values(30.0, 90.0945053100586, 769.0, 1.0);
        assert_eq!(left.iter().map(|l| l.0).collect::<Vec<_>>(), vec![10.0, 5.0, 1.0]);
        assert_eq!(left[0].1, vec![30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]);
        assert_eq!(left[1].1, vec![35.0, 45.0, 55.0, 65.0, 75.0, 85.0]);
        let bottom = tick_values(0.0, 899.75, 1128.90625, 1.0);
        assert_eq!(bottom.iter().map(|l| l.0).collect::<Vec<_>>(), vec![200.0, 100.0, 20.0]);
        assert_eq!(tick_strings(&bottom[0].1, 1.0, 200.0), vec!["0", "200", "400", "600", "800"]);
    }

    #[test]
    fn strings_and_prefix() {
        assert_eq!(tick_strings(&[-0.4, 0.0, 0.2], 1000.0, 0.2), vec!["-400", "0", "200"]);
        assert_eq!(tick_strings(&[0.5, 1.0], 1.0, 0.1), vec!["0.5", "1.0"]);
        assert_eq!(tick_strings(&[0.0005, 20000.0], 1.0, 0.1), vec!["0.0005", "20000"]);
        let mut axis = Axis::new(Orientation::Left);
        axis.label_text = "Frequency [MHz]".into();
        axis.set_range(-0.5, 0.5);
        assert_eq!(axis.label_string(), "Frequency [MHz] (x0.001)");
        axis.set_range(30.0, 90.0);
        assert_eq!(axis.label_string(), "Frequency [MHz] ");
        assert_eq!(si_scale(0.5), (1000.0, "m".into()));
        assert_eq!(si_scale(2e9).1, "G");
    }

    #[test]
    fn ut_strings() {
        // 04:15:00 UT start.
        assert_eq!(ut_tick_strings(&[0.0, 200.0], 200.0, 15300.0), vec!["04:15", "04:18"]);
        assert_eq!(ut_tick_strings(&[20.0], 20.0, 15300.0), vec!["04:15:20"]);
    }

    #[test]
    fn widths_follow_drawn_text() {
        let mut axis = Axis::new(Orientation::Left);
        axis.label_text = "Frequency [MHz]".into();
        axis.set_range(30.0, 90.0945053100586);
        // Segoe UI digits at 14 px are 7.546875 px wide; line height 19.
        let measure = |s: &str| (s.chars().count() as f64 * 7.546875, 19.0);
        let draw = axis.generate(44.09375, 769.0, &measure);
        assert_eq!(axis.text_width, 15.09375);
        assert_eq!(axis.width(30.0), 44.09375);
        assert_eq!(draw.texts.first().unwrap().text, "30");
        assert!(draw.texts.iter().all(|t| t.text.len() == 2));
        // Major ticks run 5 px into the plot, minor ones less.
        assert_eq!(draw.ticks[0].x2 - draw.ticks[0].x1, 5.0);
        assert_eq!(draw.ticks[0].alpha, 255);
        assert!(draw.ticks.iter().any(|t| t.alpha == 127));
    }
}
