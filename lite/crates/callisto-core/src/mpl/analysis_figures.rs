//! The analysis windows' matplotlib graphs, built as the full app builds
//! them: the Maximum Intensities window's scatter (`_redraw_points`), the
//! Analyzer's maxima and shock plots (`plot_max`, `plot_extra`) in the
//! interface style of `apply_origin_style` + `style_axes`, and its best fit
//! in the OriginPro style (`origin_graph_style` + `draw_fit_graph`).

use std::sync::Arc;

use super::legend::{Legend, Loc};
use super::{AxisSpec, Axes, BLACK, Face, Figure, Grid, Label, Line2D, Marker, MarkerKind, Rgba, Scatter, WHITE, hex};

/// `fig.add_subplot(111)` with matplotlib's default subplot parameters.
pub const SUBPLOT: [f64; 4] = [0.125, 0.11, 0.775, 0.77];

pub const RED: Rgba = [255, 0, 0, 255];
pub const BLUE: Rgba = [0, 0, 255, 255];
pub const GREEN: Rgba = [0, 128, 0, 255];
pub const PURPLE: Rgba = [128, 0, 128, 255];
/// matplotlib's `C0`, the ridge seed's star.
pub const TAB_BLUE: Rgba = [0x1f, 0x77, 0xb4, 255];

/// `axes.titlesize: 'large'` at `font.size` 12.
const UI_TITLE_PT: f64 = 14.4;

/// A white page with one axes in the interface style: inward ticks on all
/// sides, minor ticks, 12 pt labels, a 1.5 pt frame. `origin_ticks` keeps
/// the tick widths `style_origin_axes` gave an axes the best fit was drawn
/// on (`Axes.clear` leaves them).
fn ui_axes(title: &str, x_label: &str, y_label: &str, origin_ticks: bool) -> Axes {
    let spec = |label: &str| {
        let mut s = AxisSpec::origin(BLACK);
        s.label = Some(Label::new(label, 12.0, BLACK));
        if origin_ticks {
            s.major_width_pt = 1.5;
            s.minor_width_pt = 1.0;
        }
        s
    };
    let mut ax = Axes::new(SUBPLOT, spec(x_label), spec(y_label));
    ax.facecolor = Some(WHITE);
    ax.title = Some(Label::new(title, UI_TITLE_PT, BLACK));
    ax
}

fn page(ax: Axes, width: f64, height: f64) -> Figure {
    Figure { width, height, dpi: 100.0, facecolor: WHITE, axes: vec![ax], texts: Vec::new(), constrained: None }
}

/// The Analyzer's canvas before anything is plotted: an empty axes from
/// 0 to 1 with `style_axes`' ticks.
pub fn empty_axes_figure(width: f64, height: f64) -> Figure {
    let mut ax = Axes::new(SUBPLOT, AxisSpec::origin(BLACK), AxisSpec::origin(BLACK));
    ax.facecolor = Some(WHITE);
    page(ax, width, height)
}

/// The Maximum Intensities window's graph: the maxima as small red dots,
/// and the ridge's start when one was picked.
pub fn max_intensity_figure(x: &[f64], y: &[f64], title: &str, x_label: &str, seed: Option<(f64, f64)>, width: f64, height: f64) -> Figure {
    let mut ax = ui_axes(title, x_label, "Frequency (MHz)", false);
    ax.scatters.push(Scatter::new(x.to_vec(), y.to_vec(), 5.0, RED));
    if let Some((sx, sy)) = seed {
        // `ax.plot` of the star: the view takes it in too.
        ax.lines.push(Line2D {
            x: Arc::new(vec![sx]),
            y: Arc::new(vec![sy]),
            color: TAB_BLUE,
            width_pt: None,
            marker: Some(Marker { kind: MarkerKind::Star, size_pt: 14.0, face: TAB_BLUE, edge: BLACK, edge_width_pt: 1.0 }),
            label: None,
            zorder: 5.0,
            dash: None,
        });
    }
    ax.autoscale();
    page(ax, width, height)
}

/// The Analyzer's scatter graphs (`plot_max`, `plot_extra`): 10 pt² dots
/// on a major grid.
#[allow(clippy::too_many_arguments)]
pub fn analyzer_scatter_figure(
    x: &[f64],
    y: &[f64],
    color: Rgba,
    title: &str,
    x_label: &str,
    y_label: &str,
    origin_ticks: bool,
    width: f64,
    height: f64,
) -> Figure {
    let mut ax = ui_axes(title, x_label, y_label, origin_ticks);
    ax.scatters.push(Scatter::new(x.to_vec(), y.to_vec(), 10.0, color));
    ax.grids.push(Grid { major: true, color: hex(0xb0b0b0, 1.0), width_pt: 0.8 });
    ax.autoscale();
    page(ax, width, height)
}

/// `GraphText`: the font, sizes in points and emphasis of a graph's titles,
/// labels and ticks. The defaults: Origin's Arial, 11 pt ticks, 12 pt labels
/// and title, nothing bold or italic.
#[derive(Clone, Copy, Debug)]
pub struct GraphText {
    pub tick_pt: f64,
    pub label_pt: f64,
    pub title_pt: f64,
    /// The family the user picked, ahead of Arial ([`super::family_chain_id`]
    /// of it); 0 for Origin's Arial alone.
    pub family: u16,
    pub title_bold: bool,
    pub title_italic: bool,
    pub axis_bold: bool,
    pub axis_italic: bool,
    pub ticks_bold: bool,
    pub ticks_italic: bool,
}

impl Default for GraphText {
    fn default() -> Self {
        GraphText {
            tick_pt: 11.0,
            label_pt: 12.0,
            title_pt: 12.0,
            family: 0,
            title_bold: false,
            title_italic: false,
            axis_bold: false,
            axis_italic: false,
            ticks_bold: false,
            ticks_italic: false,
        }
    }
}

impl GraphText {
    /// The family id for a `font_family` ("" keeps Origin's Arial).
    pub fn family_id(font_family: &str) -> u16 {
        super::family_chain_id(&[font_family.to_owned()])
    }

    /// `families()` at the normal weight: the legend and the overlay texts.
    pub fn face(&self) -> Face {
        Face::graph(self.family, false, false)
    }

    /// `title_kw()`.
    pub fn title_face(&self) -> Face {
        Face::graph(self.family, self.title_bold, self.title_italic)
    }

    /// `label_kw()`.
    pub fn label_face(&self) -> Face {
        Face::graph(self.family, self.axis_bold, self.axis_italic)
    }

    /// `_emphasise_ticks`.
    pub fn tick_face(&self) -> Face {
        Face::graph(self.family, self.ticks_bold, self.ticks_italic)
    }
}

/// The data and fit of an OriginPro-style graph.
pub struct FitGraph<'a> {
    pub x: &'a [f64],
    pub y: &'a [f64],
    pub fit_x: &'a [f64],
    pub fit_y: &'a [f64],
    pub data_label: &'a str,
    pub fit_label: &'a str,
    pub title: &'a str,
    pub x_label: &'a str,
    pub y_label: &'a str,
}

/// `draw_fit_graph` under `origin_graph_style`: filled black squares, the
/// fitted curve in red, a closed frame with inward ticks, a boxed legend
/// at the best place.
pub fn fit_figure(g: &FitGraph, text: GraphText, width: f64, height: f64) -> Figure {
    let spec = |label: &str| {
        let mut s = AxisSpec::origin(BLACK);
        s.label = Some(Label::new(label, text.label_pt, BLACK).with_face(text.label_face()));
        s.tick_label_pt = text.tick_pt;
        s.major_width_pt = 1.5;
        s.minor_width_pt = 1.0;
        s.pad_pt = 5.0;
        s.face = text.tick_face();
        s
    };
    let mut ax = Axes::new(SUBPLOT, spec(g.x_label), spec(g.y_label));
    if g.x_label.is_empty() {
        ax.x.label = None;
    }
    if g.y_label.is_empty() {
        ax.y.label = None;
    }
    ax.facecolor = Some(WHITE);
    let finite = |x: &[f64], y: &[f64]| -> (Vec<f64>, Vec<f64>) {
        x.iter().zip(y).filter(|(a, b)| a.is_finite() && b.is_finite()).map(|(a, b)| (*a, *b)).unzip()
    };
    let (xs, ys) = finite(g.x, g.y);
    if !xs.is_empty() {
        ax.lines.push(Line2D {
            x: Arc::new(xs),
            y: Arc::new(ys),
            color: BLACK,
            width_pt: None,
            marker: Some(Marker { kind: MarkerKind::Square, size_pt: 6.0, face: BLACK, edge: BLACK, edge_width_pt: 1.0 }),
            label: Some(g.data_label.to_owned()),
            zorder: 3.0,
            dash: None,
        });
    }
    let (fx, fy) = finite(g.fit_x, g.fit_y);
    if !fx.is_empty() {
        ax.lines.push(Line2D {
            x: Arc::new(fx),
            y: Arc::new(fy),
            color: RED,
            width_pt: Some(1.8),
            marker: None,
            label: Some(g.fit_label.to_owned()),
            zorder: 4.0,
            dash: None,
        });
    }
    if !g.title.is_empty() {
        ax.title = Some(Label::new(g.title, text.title_pt, BLACK).with_face(text.title_face()));
    }
    // `get_legend_handles_labels`: only lines with a label.
    if ax.lines.iter().any(|l| l.label.as_deref().is_some_and(|s| !s.is_empty() && !s.starts_with('_'))) {
        ax.legend = Some(Legend { face: text.face(), ..Legend::origin(Loc::Best, (text.tick_pt - 1.0).max(6.0)) });
    }
    ax.autoscale();
    page(ax, width, height)
}

/// `fit_graph_figure`: [`fit_figure`] on its own 6.4 × 4.8 in page,
/// laid out by matplotlib's constrained layout (Save Graph, Save Plot).
pub fn fit_graph_figure(g: &FitGraph, text: GraphText) -> Figure {
    let mut fig = fit_figure(g, text, 640.0, 480.0);
    fig.constrained = Some(super::Constrained { axes: 0, colorbar: None });
    fig
}

/// `power_law_label`: `prefix: f = a x⁻ᵇ`, the exponent a mathtext
/// superscript in the text's font.
pub fn power_law_label(a: f64, b: f64, prefix: &str, variable: &str) -> String {
    power_law_label_for(a, b, prefix, "f", variable)
}

/// `power_law_label(…, symbol=…)`: `prefix: B = a R⁻ᵇ` and the like.
pub fn power_law_label_for(a: f64, b: f64, prefix: &str, symbol: &str, variable: &str) -> String {
    power_law_label_digits(a, b, prefix, symbol, variable, 2)
}

/// `power_law_label(…, digits=…)`.
pub fn power_law_label_digits(a: f64, b: f64, prefix: &str, symbol: &str, variable: &str, digits: usize) -> String {
    let head = if prefix.is_empty() { String::new() } else { format!("{prefix}: ") };
    format!("{head}{symbol} = {a:.digits$} {variable}$\\mathregular{{^{{{:.digits$}}}}}$", -b)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn power_law_label_like_the_full_app() {
        assert_eq!(power_law_label(514.6213, 0.4106, "Best Fit", "x"), "Best Fit: f = 514.62 x$\\mathregular{^{-0.41}}$");
    }
}
