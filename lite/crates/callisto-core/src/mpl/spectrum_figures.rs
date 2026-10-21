//! The main window's Export Figure (`dynamic_spectrum_figure` with
//! `draw_callisto_panel`): the dynamic spectrum as an OriginPro graph, with
//! its colour scale, the missing channels hatched, a UT or seconds time
//! axis, a linear or log frequency axis, and what the plot shows over it
//! (light curves, annotations, the ruler), at the on-screen zoom.
//!
//! Laid out by matplotlib's constrained layout, as the full app's is.

use std::sync::Arc;

use super::analysis_figures::{GraphText, SUBPLOT};
use super::type_ii_figures::{SpectrumImage, finite_limits, origin_axis, origin_colorbar};
use super::{
    Axes, BLACK, ColorbarSlot, Constrained, DataText, Face, Figure, Formatter, HAlign, HSpan, Image, Interpolation,
    Label, Line2D, Marker, MarkerKind, Rgba, TextBox, Ticks, VAlign, WHITE, family_chain_id, hex, line_dash,
    make_axes_right,
};
use crate::radio::frequency_axis::{format_frequency_mhz, log_frequency_ticks_mhz};

/// `SPECTRUM_FIGSIZE` at 100 dpi.
const SPECTRUM_SIZE: (f64, f64) = (800.0, 500.0);

/// `MEASUREMENT_COLOR`, the ruler's colour.
const MEASUREMENT: u32 = 0x18b4ff;

/// `_UT_STEPS`: UT tick spacings in seconds, from one second to six hours.
const UT_STEPS: [f64; 19] = [
    1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0, 120.0, 180.0, 300.0, 600.0, 900.0, 1200.0, 1800.0, 3600.0, 7200.0,
    10800.0, 21600.0,
];

/// A light curve over the spectrum, as `_build_light_curve_overlay` gives it.
#[derive(Clone, Debug)]
pub struct LightCurve {
    pub time: Vec<f64>,
    pub y: Vec<f64>,
    pub color: String,
    pub line_width: f64,
    pub opacity: f64,
    pub line_style: String,
    /// The label and where it goes, when `show_label` and it has text.
    pub label: Option<(String, f64, f64)>,
}

/// An annotation as `_origin_annotations` hands it over (text labels with
/// their resolved style).
#[derive(Clone, Debug)]
pub enum SpectrumAnnotation {
    /// A line, or a polygon (closed when drawn).
    Path {
        points: Vec<(f64, f64)>,
        closed: bool,
        color: String,
        line_width: f64,
    },
    Text {
        x: f64,
        y: f64,
        text: String,
        color: String,
        font_size: f64,
        bold: bool,
        italic: bool,
        font_family: String,
    },
}

/// `Measurement`: the ruler's points and the label at the last one.
#[derive(Clone, Debug, Default)]
pub struct Measurement {
    pub points: Vec<(f64, f64)>,
    pub label: String,
}

/// `SpectrumGraph`: everything the figure draws.
#[derive(Clone)]
pub struct SpectrumGraph {
    pub image: SpectrumImage,
    pub title: String,
    pub x_label: String,
    pub y_label: String,
    /// UT of time zero in seconds of the day; None labels the axis in seconds.
    pub ut_start_sec: Option<f64>,
    /// The zoom to reproduce: `(xlim, ylim)`.
    pub view: Option<((f64, f64), (f64, f64))>,
    /// A log frequency axis over these bounds (MHz).
    pub log_frequency_bounds: Option<(f64, f64)>,
    pub gap_spans: Vec<(f64, f64)>,
    pub light_curves: Vec<LightCurve>,
    pub annotations: Vec<SpectrumAnnotation>,
    pub measurement: Option<Measurement>,
    pub text: GraphText,
}

/// `matplotlib.colors.to_rgba` for the colours the overlays carry
/// (`#rgb`, `#rrggbb`, `#rrggbbaa`, the basic names), at `alpha`.
pub fn mpl_color(name: &str, alpha: Option<f64>) -> Option<Rgba> {
    let s = name.trim();
    let mut c = match s.strip_prefix('#') {
        Some(h) if h.len() == 8 => {
            let v = u32::from_str_radix(h, 16).ok()?;
            [(v >> 24) as u8, (v >> 16) as u8, (v >> 8) as u8, v as u8]
        }
        _ => crate::plot::qt_raster::parse_color(s)?,
    };
    if let Some(a) = alpha {
        c[3] = (a.clamp(0.0, 1.0) * 255.0).round() as u8;
    }
    Some(c)
}

/// `_UtLocator.tick_values`: ticks on whole UT steps, at most `max_ticks`.
pub fn ut_ticks(vmin: f64, vmax: f64, ut_start: f64, max_ticks: usize) -> Vec<f64> {
    let (lo, hi) = if vmin <= vmax { (vmin, vmax) } else { (vmax, vmin) };
    let span = hi - lo;
    if !(lo.is_finite() && hi.is_finite()) || span <= 0.0 {
        return Vec::new();
    }
    let max = max_ticks as f64;
    let last = UT_STEPS[UT_STEPS.len() - 1];
    let step =
        UT_STEPS.iter().copied().find(|s| span / s <= max).unwrap_or_else(|| last * (span / (last * max)).ceil());
    let first = ((lo + ut_start) / step).ceil() * step - ut_start;
    // `np.arange(first, hi + step * 1e-9, step)`.
    let stop = hi + step * 1e-9;
    let n = ((stop - first) / step).ceil().max(0.0) as usize;
    (0..n).map(|i| first + i as f64 * step).collect()
}

/// `ut_time_formatter`: `HH:MM`, or `HH:MM:SS` over five minutes or less.
pub fn ut_label(value: f64, ut_start: f64, xlim: (f64, f64)) -> String {
    let show_seconds = (xlim.1 - xlim.0).abs() <= 300.0;
    let total = (ut_start + value).round_ties_even() as i64;
    let (h, m, s) = (total.div_euclid(3600).rem_euclid(24), total.rem_euclid(3600) / 60, total.rem_euclid(60));
    if show_seconds { format!("{h:02}:{m:02}:{s:02}") } else { format!("{h:02}:{m:02}") }
}

/// Splits a line at its non-finite points, as matplotlib breaks a path there.
fn finite_runs(x: &[f64], y: &[f64]) -> Vec<(Vec<f64>, Vec<f64>)> {
    let mut runs = Vec::new();
    let (mut xs, mut ys) = (Vec::new(), Vec::new());
    for (a, b) in x.iter().zip(y) {
        if a.is_finite() && b.is_finite() {
            xs.push(*a);
            ys.push(*b);
        } else if !xs.is_empty() {
            runs.push((std::mem::take(&mut xs), std::mem::take(&mut ys)));
        }
    }
    if !xs.is_empty() {
        runs.push((xs, ys));
    }
    runs
}

fn line(x: Vec<f64>, y: Vec<f64>, color: Rgba, width: f64, zorder: f64, dash: Option<(f64, f64)>) -> Line2D {
    Line2D { x: Arc::new(x), y: Arc::new(y), color, width_pt: Some(width), marker: None, label: None, zorder, dash }
}

/// `dynamic_spectrum_figure` for a spectrum (no SWAVES panel or event strip
/// in Lite).
pub fn dynamic_spectrum_figure(g: &SpectrumGraph) -> Figure {
    let text = g.text;
    // `fig.colorbar(im, ax=ax, pad=0.02, fraction=0.05)` under constrained layout.
    let (main, bar) = make_axes_right(SUBPLOT, 0.05, 0.02, 1.0);
    let mut ax = Axes::new(main, origin_axis(&g.x_label, text), origin_axis(&g.y_label, text));
    for spec in [&mut ax.x, &mut ax.y] {
        if spec.label.as_ref().is_some_and(|l| l.text.is_empty()) {
            spec.label = None;
        }
    }
    ax.facecolor = Some(WHITE);

    // `_show_image`.
    let im = &g.image;
    let [x0, x1, bottom, top] = im.extent;
    let (vmin, vmax) = match im.levels {
        Some((lo, hi)) if lo.is_finite() && hi.is_finite() && hi > lo => (lo, hi),
        _ => finite_limits(&im.data.data),
    };
    ax.images.push(Image {
        data: im.data.clone(),
        extent: im.extent,
        origin_upper: true,
        cmap: im.cmap,
        vmin,
        vmax,
        interpolation: Interpolation::Auto,
    });
    for &(lo, hi) in &g.gap_spans {
        ax.spans.push(HSpan {
            y0: lo,
            y1: hi,
            face: hex(0xb8b8b8, 0.35),
            hatch: hex(0x555555, 0.35),
            hatch_count: 3,
            hatch_width_pt: 1.0,
            zorder: 3.0,
        });
    }
    ax.xlim = (x0.min(x1), x0.max(x1));
    ax.ylim = (bottom, top);
    if let Some((a, b)) = g.log_frequency_bounds {
        // `_log_frequency_axis`.
        let (lo, hi) = if a <= b { (a, b) } else { (b, a) };
        if lo.is_finite() && hi.is_finite() && lo > 0.0 && hi > lo {
            ax.y_log = true;
            ax.ylim = (lo, hi);
            let (labelled, minor) = log_frequency_ticks_mhz(lo, hi, 10);
            ax.y.major = Ticks::Fixed(labelled);
            ax.y.minor = Ticks::Fixed(minor);
            ax.y.formatter = Formatter::Func(Arc::new(format_frequency_mhz));
        }
    }

    // `_draw_light_curves`.
    for c in &g.light_curves {
        let color =
            mpl_color(&c.color, Some(c.opacity)).or_else(|| mpl_color("#00e5ff", Some(c.opacity))).unwrap_or(BLACK);
        for (x, y) in finite_runs(&c.time, &c.y) {
            ax.lines.push(line(x, y, color, c.line_width, 8.0, line_dash(&c.line_style, c.line_width)));
        }
        if let Some((label, lx, ly)) = &c.label
            && !label.trim().is_empty()
        {
            let solid = mpl_color(&c.color, None).unwrap_or(BLACK);
            ax.data_texts.push(DataText {
                x: *lx,
                y: *ly,
                label: Label::new(label.trim(), (text.tick_pt - 2.0).max(6.0), solid).with_face(text.face()),
                ha: HAlign::Left,
                va: VAlign::Bottom,
                zorder: 9.0,
                bbox: None,
            });
        }
    }
    // `_draw_annotations`.
    for a in &g.annotations {
        match a {
            SpectrumAnnotation::Path { points, closed, color, line_width } if points.len() >= 2 => {
                let mut pts = points.clone();
                if *closed {
                    pts.push(points[0]);
                }
                let c = mpl_color(color, Some(0.95)).or_else(|| mpl_color("#00d4ff", Some(0.95))).unwrap_or(BLACK);
                for (x, y) in finite_runs(
                    &pts.iter().map(|p| p.0).collect::<Vec<_>>(),
                    &pts.iter().map(|p| p.1).collect::<Vec<_>>(),
                ) {
                    ax.lines.push(line(x, y, c, *line_width, 10.0, None));
                }
            }
            SpectrumAnnotation::Text { x, y, text: s, color, font_size, bold, italic, font_family } => {
                let family = font_family.trim();
                // `fontfamily=[family] + text.families()`.
                let id = if family.is_empty() {
                    text.family
                } else {
                    let mut chain = vec![family.to_owned()];
                    chain.extend(super::family_chain(text.family));
                    family_chain_id(&chain)
                };
                let c = mpl_color(color, None).or_else(|| mpl_color("#00d4ff", None)).unwrap_or(BLACK);
                ax.data_texts.push(DataText {
                    x: *x,
                    y: *y,
                    label: Label::new(s.clone(), *font_size, c).with_face(Face::graph(id, *bold, *italic)),
                    ha: HAlign::Left,
                    va: VAlign::Bottom,
                    zorder: 11.0,
                    bbox: None,
                });
            }
            _ => {}
        }
    }
    // `_draw_measurement`.
    if let Some(m) = &g.measurement
        && !m.points.is_empty()
    {
        let (xs, ys): (Vec<f64>, Vec<f64>) = m.points.iter().copied().unzip();
        let edge = hex(MEASUREMENT, 1.0);
        // `ax.scatter(..., s=46, facecolors="white", edgecolors=…, linewidths=1.5)`.
        ax.lines.push(Line2D {
            x: Arc::new(xs.clone()),
            y: Arc::new(ys.clone()),
            color: edge,
            width_pt: None,
            marker: Some(Marker {
                kind: MarkerKind::Circle,
                size_pt: 46f64.sqrt(),
                face: WHITE,
                edge,
                edge_width_pt: 1.5,
            }),
            label: None,
            zorder: 20.0,
            dash: None,
        });
        if m.points.len() >= 2 {
            ax.lines.push(line(xs[..2].to_vec(), ys[..2].to_vec(), edge, 1.8, 19.0, None));
            if !m.label.is_empty() {
                ax.data_texts.push(DataText {
                    x: xs[xs.len() - 1],
                    y: ys[ys.len() - 1],
                    label: Label::new(m.label.clone(), (text.tick_pt - 2.0).max(6.0), WHITE).with_face(text.face()),
                    ha: HAlign::Left,
                    va: VAlign::Bottom,
                    zorder: 21.0,
                    bbox: Some(TextBox {
                        pad: 0.25,
                        face: hex(0x0f172a, 0.85),
                        edge: hex(MEASUREMENT, 0.85),
                        width_pt: 1.0,
                    }),
                });
            }
        }
    }

    // `_set_time_range`, then `_apply_view`.
    if let Some((xl, yl)) = g.view {
        if xl.0.is_finite() && xl.1.is_finite() && xl.0 != xl.1 {
            ax.xlim = xl;
        }
        if yl.0.is_finite() && yl.1.is_finite() && yl.0 != yl.1 && !(ax.y_log && yl.0.min(yl.1) <= 0.0) {
            ax.ylim = yl;
        }
    }
    if let Some(start) = g.ut_start_sec {
        // `ut_time_axis`: located and labelled from the final view.
        let xlim = ax.xlim;
        ax.x.major = Ticks::Fixed(ut_ticks(xlim.0, xlim.1, start, 8));
        ax.x.formatter = Formatter::Func(Arc::new(move |v| ut_label(v, start, xlim)));
    }
    if !g.title.is_empty() {
        ax.title = Some(Label::new(g.title.clone(), text.title_pt, BLACK).with_face(text.title_face()));
    }

    let cb = origin_colorbar(bar, &im.cmap, vmin, vmax, &im.colorbar_label, text);
    Figure {
        width: SPECTRUM_SIZE.0,
        height: SPECTRUM_SIZE.1,
        dpi: 100.0,
        facecolor: WHITE,
        axes: vec![ax, cb],
        texts: Vec::new(),
        constrained: Some(Constrained {
            axes: 0,
            colorbar: Some(ColorbarSlot { axes: 1, fraction: 0.05, pad: 0.02, shrink: 1.0, aspect: 20.0 }),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ut_ticks_fall_on_whole_ut_steps() {
        // 04:15:00 UT start, 900 s shown: two-minute steps from 04:16.
        let t = ut_ticks(0.0, 900.0, 15300.0, 8);
        assert_eq!(t.first().copied(), Some(60.0));
        assert!((t[1] - 180.0).abs() < 1e-9, "{t:?}");
        assert_eq!(ut_label(180.0, 15300.0, (0.0, 900.0)), "04:18");
        assert_eq!(ut_label(10.0, 15300.0, (0.0, 200.0)), "04:15:10");
    }

    #[test]
    fn colours_like_matplotlib() {
        assert_eq!(mpl_color("#00e5ff", Some(0.95)), Some([0, 0xe5, 0xff, 242]));
        assert_eq!(mpl_color("#11223344", None), Some([0x11, 0x22, 0x33, 0x44]));
    }
}
