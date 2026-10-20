//! The full app's downloader figures: the FITS preview
//! (`callisto_downloader._build_plotutil_preview_figure` +
//! `AppTheme.apply_mpl`) and the full-day spectral overview
//! (`spectral_overview.render_spectral_overview_figure`).

use std::sync::Arc;

use super::{
    AxesText, Axes, AxisSpec, FigText, Figure, Formatter, Grid, HAlign, Image, Interpolation, Label, Rgba, Side,
    Ticks, VAlign, colorbar_image, hex, subplot_with_colorbar, BLACK, WHITE,
};
use crate::plot::Colormap;
use crate::radio::array::Array2;
use crate::radio::frequency_axis::frequency_edges;
use crate::radio::processing::{BackgroundMethod, PLOTUTIL_DB_SCALE, subtract_background};
use crate::radio::spectral_overview::{DISPLAY_LIMITS, OverviewResult, SECONDS_PER_PANEL};

/// The Qt palette colours `AppTheme.apply_mpl` gives a figure.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct QtColors {
    /// `QPalette.Window`: the figure background.
    pub window: Rgba,
    /// `QPalette.Base`: the axes background.
    pub base: Rgba,
    /// `QPalette.WindowText`: ticks, labels, spines.
    pub text: Rgba,
    /// `QPalette.Mid`: the grid (alpha 0.25).
    pub mid: Rgba,
}

fn colorbar_axes(position: [f64; 4], box_aspect: Option<(f64, (f64, f64))>, image: Image, fg: Rgba, label: Label, tick_pt: f64) -> Axes {
    let (vmin, vmax) = (image.vmin, image.vmax);
    let mut y = AxisSpec::origin(fg);
    y.ticks_low = false;
    y.labels_low = false;
    y.labels_high = true;
    y.label_side = Side::High;
    y.label = Some(label);
    y.tick_label_pt = tick_pt;
    let mut x = AxisSpec::origin(fg);
    x.major = Ticks::None;
    x.minor = Ticks::None;
    x.ticks_low = false;
    x.ticks_high = false;
    x.labels_low = false;
    Axes {
        position,
        box_aspect,
        xlim: (0.0, 1.0),
        ylim: (vmin, vmax),
        facecolor: None,
        images: vec![image],
        x,
        y,
        grids: Vec::new(),
        texts: Vec::new(),
        spine_color: fg,
        spine_width_pt: 1.5,
        title: None,
        title_pad_pt: 6.0,
        scatters: Vec::new(),
        lines: Vec::new(),
        legend: None,
        axis_zorder: 1.5,
    }
}

/// `_build_plotutil_preview_figure` for a canvas of `width` × `height`
/// logical pixels.
pub fn preview_figure(data: &Array2, freqs: &[f64], times: &[f64], width: f64, height: f64, c: QtColors) -> Figure {
    let db = subtract_background(data, BackgroundMethod::PlotutilMedianDb, None, false, PLOTUTIL_DB_SCALE);
    let inferno = Colormap::by_name("inferno");
    let (vmin, vmax) = DISPLAY_LIMITS;
    let mut x = AxisSpec::origin(c.text);
    let mut y = AxisSpec::origin(c.text);
    let finite = |v: &[f64]| v.iter().copied().filter(|x| x.is_finite()).collect::<Vec<_>>();
    let (fs, ts) = (finite(freqs), finite(times));
    let (extent, origin_upper, xlim, ylim) = if freqs.len() > 1 && times.len() > 1 && !fs.is_empty() && !ts.is_empty() {
        let (fmin, fmax) = (fs.iter().copied().fold(f64::INFINITY, f64::min), fs.iter().copied().fold(f64::NEG_INFINITY, f64::max));
        let (tmin, tmax) = (ts.iter().copied().fold(f64::INFINITY, f64::min), ts.iter().copied().fold(f64::NEG_INFINITY, f64::max));
        x.label = Some(Label::new("Time [s]", 12.0, c.text));
        y.label = Some(Label::new("Frequency [MHz]", 12.0, c.text));
        ([tmin, tmax, fmin, fmax], freqs[0] > freqs[freqs.len() - 1], (tmin, tmax), (fmin, fmax))
    } else {
        // Channels, with 0 at the bottom.
        x.label = Some(Label::new("Time bin", 12.0, c.text));
        y.label = Some(Label::new("Frequency channel", 12.0, c.text));
        let (rows, cols) = (db.rows as f64, db.cols as f64);
        ([-0.5, cols - 0.5, -0.5, rows - 0.5], false, (-0.5, cols - 0.5), (0.0, rows - 1.0))
    };
    let image = Image {
        data: Arc::new(db),
        extent,
        origin_upper,
        cmap: inferno,
        vmin,
        vmax,
        interpolation: Interpolation::Auto,
    };
    let (main, cbar, aspect) = subplot_with_colorbar();
    let mut grid_color = c.mid;
    grid_color[3] = (0.25f64 * 255.0).round() as u8;
    let ax = Axes {
        position: main,
        box_aspect: None,
        xlim,
        ylim,
        facecolor: Some(c.base),
        images: vec![image],
        x,
        y,
        grids: vec![Grid { major: true, color: grid_color, width_pt: 0.8 }],
        texts: Vec::new(),
        spine_color: c.text,
        spine_width_pt: 1.5,
        title: None,
        title_pad_pt: 6.0,
        scatters: Vec::new(),
        lines: Vec::new(),
        legend: None,
        axis_zorder: 1.5,
    };
    let bar = colorbar_axes(
        cbar,
        Some(aspect),
        colorbar_image(&inferno, vmin, vmax),
        c.text,
        Label::new("Background-subtracted intensity [dB]", 12.0, c.text),
        12.0,
    );
    Figure { width, height, dpi: 100.0, facecolor: c.window, axes: vec![ax, bar], texts: Vec::new() }
}

/// `render_spectral_overview_figure` for a canvas of `width` × `height`.
pub fn overview_figure(result: &OverviewResult, width: f64, height: f64) -> Figure {
    let all: Vec<f64> = result.segments.iter().flat_map(|s| s.freqs.iter().copied()).filter(|f| f.is_finite()).collect();
    let fmin = all.iter().copied().fold(f64::INFINITY, f64::min);
    let fmax = all.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let viridis = Colormap::by_name("viridis");
    let (vmin, vmax) = DISPLAY_LIMITS;
    let (bottoms, tops, lefts, rights) = super::grid_positions([0.065, 0.070, 0.87, 0.915], 6, 1, 0.0, 0.30, &[], &[]);
    let hhmm: Arc<dyn Fn(f64) -> String + Send + Sync> =
        Arc::new(|v: f64| format!("{:02}:00", ((v / 3600.0).floor() as i64).rem_euclid(24)));
    let mut axes = Vec::new();
    for p in 0..6 {
        let (start, stop) = (p as f64 * SECONDS_PER_PANEL, (p + 1) as f64 * SECONDS_PER_PANEL);
        let mut images = Vec::new();
        for seg in &result.segments {
            let t = &seg.utc_seconds;
            if t.is_empty() {
                continue;
            }
            let tmax = t.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let tmin = t.iter().copied().fold(f64::INFINITY, f64::min);
            if tmax < start || tmin >= stop {
                continue;
            }
            let inside: Vec<usize> = (0..t.len()).filter(|&i| t[i] >= start && t[i] < stop).collect();
            if inside.is_empty() {
                continue;
            }
            let rows = seg.data_db.rows;
            let mut panel = Array2::filled(rows, inside.len(), 0.0);
            for r in 0..rows {
                let (src, dst) = (seg.data_db.row(r), panel.row_mut(r));
                for (k, &i) in inside.iter().enumerate() {
                    dst[k] = src[i];
                }
            }
            let x0 = t[inside[0]];
            let x1 = if inside.len() == 1 { stop.min(x0 + 1.0) } else { t[*inside.last().unwrap()] };
            let edges = frequency_edges(&seg.freqs, 1.0);
            images.push(Image {
                data: Arc::new(panel),
                extent: [x0, x1, edges[edges.len() - 1], edges[0]],
                origin_upper: true,
                cmap: viridis,
                vmin,
                vmax,
                interpolation: Interpolation::Nearest,
            });
        }
        let rendered = !images.is_empty();
        let mut x = AxisSpec::origin(BLACK);
        x.major = Ticks::Fixed((0..4).map(|k| start + k as f64 * 3600.0).collect());
        x.minor = Ticks::Fixed((0..16).map(|k| start + k as f64 * 900.0).collect());
        x.formatter = Formatter::Func(hhmm.clone());
        x.tick_label_pt = 8.0;
        let mut y = AxisSpec::origin(BLACK);
        y.tick_label_pt = 8.0;
        let mut texts = vec![AxesText {
            x: 1.012,
            y: 0.5,
            label: Label::new(format!("{:02}-{:02} UT", p * 4, (p + 1) * 4), 11.0, BLACK),
            ha: HAlign::Left,
            va: VAlign::Center,
        }];
        if !rendered {
            texts.push(AxesText {
                x: 0.5,
                y: 0.5,
                label: Label::new("No data", 11.0, hex(0x7a828a, 1.0)),
                ha: HAlign::Center,
                va: VAlign::Center,
            });
        }
        axes.push(Axes {
            position: [lefts[0], bottoms[p], rights[0] - lefts[0], tops[p] - bottoms[p]],
            box_aspect: None,
            xlim: (start, stop),
            ylim: (fmin, fmax),
            facecolor: Some(WHITE),
            images,
            x,
            y,
            grids: vec![
                Grid { major: true, color: hex(0x59636e, 0.55), width_pt: 0.7 },
                Grid { major: false, color: hex(0xa8b0b8, 0.45), width_pt: 0.4 },
            ],
            texts,
            spine_color: BLACK,
            spine_width_pt: 1.5,
            title: None,
            title_pad_pt: 6.0,
            scatters: Vec::new(),
            lines: Vec::new(),
            legend: None,
            axis_zorder: 1.5,
        });
    }
    axes.push(colorbar_axes(
        [0.935, 0.18, 0.014, 0.64],
        None,
        colorbar_image(&viridis, vmin, vmax),
        BLACK,
        Label::new("Background-subtracted intensity [dB]", 11.0, BLACK),
        9.0,
    ));
    let title = format!(
        "Full-day spectrum | {} | {} | Focus {}",
        result.observation_date.format("%Y-%m-%d"),
        result.station,
        result.focus_code
    );
    let texts = vec![
        FigText { x: 0.5, y: 0.975, label: Label::new(title, 14.0, BLACK), ha: HAlign::Center, va: VAlign::Top, rotation: 0.0 },
        FigText {
            x: 0.020,
            y: 0.5,
            label: Label::new("Frequency [MHz]", 12.0, BLACK),
            ha: HAlign::Left,
            va: VAlign::Center,
            rotation: 90.0,
        },
        FigText { x: 0.47, y: 0.020, label: Label::new("Time [UTC]", 12.0, BLACK), ha: HAlign::Center, va: VAlign::Baseline, rotation: 0.0 },
    ];
    Figure { width, height, dpi: 100.0, facecolor: WHITE, axes, texts }
}
