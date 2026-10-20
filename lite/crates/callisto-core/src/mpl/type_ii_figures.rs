//! The Type II band-splitting window's Save Plot figures (`origin_figure`):
//! the picked lanes and their fits over the spectrum
//! (`type_ii_spectrum_figure`), and the magnetic field against shock
//! height (`fit_graph_figure`), as OriginPro graphs.
//!
//! The full app lays these out with `layout="constrained"`; Lite places
//! the axes as `fig.add_subplot(111)` does until Phase 4 brings the saved
//! figures to matplotlib's layout.

use std::sync::Arc;

use super::analysis_figures::{FitGraph, GraphText, SUBPLOT, fit_figure, power_law_label_for};
use super::figures::colorbar_axes;
use super::legend::{Legend, Loc};
use super::{AxisSpec, Axes, BLACK, Face, Figure, Image, Interpolation, Label, Line2D, Marker, MarkerKind, Rgba, WHITE, colorbar_beside, colorbar_image};
use crate::plot::Colormap;
use crate::radio::array::Array2;

/// `SPECTRUM_FIGSIZE` and `GRAPH_FIGSIZE` at 100 dpi.
const SPECTRUM_SIZE: (f64, f64) = (800.0, 500.0);
const GRAPH_SIZE: (f64, f64) = (640.0, 480.0);

/// `SpectrumImage`: the spectrogram in display units; `extent` is
/// `(x0, x1, y_bottom, y_top)` with row 0 at `y_top`.
#[derive(Clone)]
pub struct SpectrumImage {
    pub data: Arc<Array2>,
    pub extent: [f64; 4],
    pub cmap: Colormap,
    pub levels: Option<(f64, f64)>,
    pub colorbar_label: String,
}

/// `BandTrace`: one lane's picked points and its sampled fit.
#[derive(Clone, Debug)]
pub struct BandTrace {
    pub label: String,
    pub points: Vec<(f64, f64)>,
    pub fit: Option<(Vec<f64>, Vec<f64>)>,
    pub marker_color: Rgba,
    pub line_color: Rgba,
    /// Points.
    pub marker_size: f64,
    pub line_width: f64,
}

/// `ORIGIN_MARKERS`, the shapes the two lanes take.
const MARKERS: [MarkerKind; 2] = [MarkerKind::Square, MarkerKind::Circle];

fn finite(x: &[f64], y: &[f64]) -> (Vec<f64>, Vec<f64>) {
    x.iter().zip(y).filter(|(a, b)| a.is_finite() && b.is_finite()).map(|(a, b)| (*a, *b)).unzip()
}

fn origin_axis(label: &str, text: GraphText) -> AxisSpec {
    let mut s = AxisSpec::origin(BLACK);
    s.label = Some(Label::new(label, text.label_pt, BLACK).with_face(Face::Origin));
    s.tick_label_pt = text.tick_pt;
    s.major_width_pt = 1.5;
    s.minor_width_pt = 1.0;
    s.pad_pt = 5.0;
    s.face = Face::Origin;
    s
}

/// The data's finite range, for a spectrum without display levels.
fn finite_limits(data: &[f32]) -> (f64, f64) {
    let (lo, hi) = data
        .iter()
        .filter(|v| v.is_finite())
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| (lo.min(v as f64), hi.max(v as f64)));
    if lo.is_finite() { (lo, hi) } else { (0.0, 1.0) }
}

/// `type_ii_spectrum_figure`: the spectrum (when there is one) with its
/// colour scale, each lane's points (black-edged markers) and fit.
pub fn spectrum_figure(image: Option<&SpectrumImage>, bands: &[BandTrace], title: &str, text: GraphText) -> Figure {
    let cell = [SUBPLOT[0], SUBPLOT[1], SUBPLOT[0] + SUBPLOT[2], SUBPLOT[1] + SUBPLOT[3]];
    let (main, bar) = match image {
        Some(_) => {
            let (m, b, a) = colorbar_beside(cell, 0.05, 0.02);
            (m, Some((b, a)))
        }
        None => (SUBPLOT, None),
    };
    let mut ax = Axes::new(main, origin_axis("Time (s)", text), origin_axis("Frequency (MHz)", text));
    ax.facecolor = Some(WHITE);
    for (i, band) in bands.iter().enumerate() {
        let (px, py): (Vec<f64>, Vec<f64>) = band.points.iter().copied().unzip();
        let (xs, ys) = finite(&px, &py);
        if !xs.is_empty() {
            ax.lines.push(Line2D {
                x: Arc::new(xs),
                y: Arc::new(ys),
                color: band.marker_color,
                width_pt: None,
                marker: Some(Marker {
                    kind: MARKERS[i % MARKERS.len()],
                    size_pt: band.marker_size,
                    face: band.marker_color,
                    edge: BLACK,
                    edge_width_pt: 0.8,
                }),
                label: Some(band.label.clone()),
                zorder: 4.0,
            });
        }
        if let Some((fx, fy)) = &band.fit {
            let (fx, fy) = finite(fx, fy);
            if !fx.is_empty() {
                ax.lines.push(Line2D {
                    x: Arc::new(fx),
                    y: Arc::new(fy),
                    color: band.line_color,
                    width_pt: Some(band.line_width),
                    marker: None,
                    label: Some(format!("{} fit", band.label)),
                    zorder: 5.0,
                });
            }
        }
    }
    ax.autoscale();
    let mut colorbar = None;
    if let (Some(im), Some((bar, aspect))) = (image, bar) {
        let [x0, x1, bottom, top] = im.extent;
        let (vmin, vmax) = match im.levels {
            Some((lo, hi)) if lo.is_finite() && hi.is_finite() && hi > lo => (lo, hi),
            _ => finite_limits(&im.data.data),
        };
        ax.images.push(Image { data: im.data.clone(), extent: im.extent, origin_upper: true, cmap: im.cmap, vmin, vmax, interpolation: Interpolation::Auto });
        ax.xlim = (x0.min(x1), x0.max(x1));
        ax.ylim = (bottom, top);
        // `_style_colorbar`: a thin black box, inward ticks at the right.
        let label = Label::new(im.colorbar_label.clone(), text.label_pt, BLACK).with_face(Face::Origin);
        let mut cb = colorbar_axes(bar, Some(aspect), colorbar_image(&im.cmap, vmin, vmax), BLACK, label, text.tick_pt);
        cb.spine_width_pt = 1.0;
        cb.y.major_len_pt = 5.0;
        cb.y.major_width_pt = 1.0;
        cb.y.minor_len_pt = 2.5;
        cb.y.minor_width_pt = 0.8;
        cb.y.face = Face::Origin;
        colorbar = Some(cb);
    }
    if !title.is_empty() {
        ax.title = Some(Label::new(title, text.title_pt, BLACK).with_face(Face::Origin));
    }
    if !ax.lines.is_empty() {
        ax.legend = Some(Legend::origin(Loc::Best, (text.tick_pt - 1.0).max(6.0)));
    }
    let axes = std::iter::once(ax).chain(colorbar).collect();
    Figure { width: SPECTRUM_SIZE.0, height: SPECTRUM_SIZE.1, dpi: 100.0, facecolor: WHITE, axes, texts: Vec::new() }
}

/// The B versus R view: `fit_graph_figure` of the magnetic field and its
/// power-law fit.
#[allow(clippy::too_many_arguments)]
pub fn bvr_figure(heights: &[f64], field: &[f64], fit_x: &[f64], fit_y: &[f64], a: f64, b: f64, title: &str, text: GraphText) -> Figure {
    let label = power_law_label_for(a, b, "Fit", "B", "R");
    let g = FitGraph {
        x: heights,
        y: field,
        fit_x,
        fit_y,
        data_label: "Magnetic field",
        fit_label: &label,
        title,
        x_label: "Shock Height (R\u{209b})",
        y_label: "Magnetic Field (G)",
    };
    fit_figure(&g, text, GRAPH_SIZE.0, GRAPH_SIZE.1)
}
