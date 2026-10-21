//! `matplotlib.legend.Legend` (3.10) as the full app's graphs use it: a
//! handle (`HandlerLine2D`) beside each labelled line's label, packed by
//! `offsetbox`'s `HPacker` and `VPacker`, placed at a fixed location or the
//! "best" one, in a square frame.

use std::sync::Arc;

use super::{Axes, Face, Figure, HAlign, Label, Prim, PxRect, Rgba, TextMeasure, VAlign, line_metrics, place_text};

/// `loc`, in the order of `Legend.codes` (best is 0).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Loc {
    Best,
    UpperRight,
    UpperLeft,
    LowerLeft,
    LowerRight,
    Right,
    CenterLeft,
    CenterRight,
    LowerCenter,
    UpperCenter,
    Center,
}

const FIXED: [Loc; 10] = [
    Loc::UpperRight,
    Loc::UpperLeft,
    Loc::LowerLeft,
    Loc::LowerRight,
    Loc::Right,
    Loc::CenterLeft,
    Loc::CenterRight,
    Loc::LowerCenter,
    Loc::UpperCenter,
    Loc::Center,
];

impl Loc {
    /// The anchor of `offsetbox._get_anchored_bbox` (`Bbox.coefs`).
    fn coefs(self) -> (f64, f64) {
        match self {
            Loc::UpperRight => (1.0, 1.0),
            Loc::UpperLeft => (0.0, 1.0),
            Loc::LowerLeft => (0.0, 0.0),
            Loc::LowerRight => (1.0, 0.0),
            Loc::Right | Loc::CenterRight => (1.0, 0.5),
            Loc::CenterLeft => (0.0, 0.5),
            Loc::LowerCenter => (0.5, 0.0),
            Loc::UpperCenter => (0.5, 1.0),
            Loc::Center | Loc::Best => (0.5, 0.5),
        }
    }
}

/// A legend's placement, text and frame (sizes in font-size units, as
/// the `legend.*` rcParams give them).
#[derive(Clone, Debug)]
pub struct Legend {
    pub loc: Loc,
    pub fontsize_pt: f64,
    pub face: Face,
    pub text_color: Rgba,
    pub borderpad: f64,
    pub labelspacing: f64,
    pub handlelength: f64,
    pub handleheight: f64,
    pub handletextpad: f64,
    pub borderaxespad: f64,
    pub frame_face: Rgba,
    pub frame_edge: Rgba,
    pub frame_width_pt: f64,
}

impl Legend {
    /// `origin_legend(ax, loc=…)` under `origin_rc`: a square white frame
    /// with a 1 pt black edge, `legend.borderpad` 0.5.
    pub fn origin(loc: Loc, fontsize_pt: f64) -> Self {
        Legend {
            loc,
            fontsize_pt,
            face: Face::Origin,
            text_color: super::BLACK,
            borderpad: 0.5,
            labelspacing: 0.5,
            handlelength: 2.0,
            handleheight: 0.7,
            handletextpad: 0.8,
            borderaxespad: 0.5,
            frame_face: super::WHITE,
            frame_edge: super::BLACK,
            frame_width_pt: 1.0,
        }
    }
}

/// What a "best" legend avoids (`_auto_legend_data`), in display pixels
/// (y down): each line's vertices, the scatter offsets, text boxes.
#[derive(Default)]
pub struct Avoid {
    pub lines: Vec<Arc<Vec<(f64, f64)>>>,
    pub offsets: Vec<(f64, f64)>,
    pub bboxes: Vec<PxRect>,
}

/// `path_intersects_rectangle` (`_path.h`, unfilled).
fn path_intersects_rect(points: &[(f64, f64)], r: &PxRect) -> bool {
    let Some(&first) = points.first() else { return false };
    let (cx, cy) = ((r.x0 + r.x1) * 0.5, (r.y0 + r.y1) * 0.5);
    let (w, h) = ((r.x0 - r.x1).abs(), (r.y0 - r.y1).abs());
    if 2.0 * (first.0 - cx).abs() <= w && 2.0 * (first.1 - cy).abs() <= h {
        return true;
    }
    let (mut x1, mut y1) = first;
    for &(x2, y2) in &points[1..] {
        if x1 == x2 && y1 == y2 {
            continue;
        }
        if (x1 + x2 - 2.0 * cx).abs() < (x1 - x2).abs() + w
            && (y1 + y2 - 2.0 * cy).abs() < (y1 - y2).abs() + h
            && 2.0 * ((x1 - cx) * (y1 - y2) - (y1 - cy) * (x1 - x2)).abs() < w * (y1 - y2).abs() + h * (x1 - x2).abs()
        {
            return true;
        }
        x1 = x2;
        y1 = y2;
    }
    false
}

/// `Bbox.count_contains`: points strictly inside.
fn count_contains(r: &PxRect, points: &[(f64, f64)]) -> usize {
    points.iter().filter(|p| r.x0 < p.0 && p.0 < r.x1 && r.y0 < p.1 && p.1 < r.y1).count()
}

/// `count_bboxes_overlapping_bbox`.
fn count_overlaps(r: &PxRect, boxes: &[PxRect]) -> usize {
    boxes.iter().filter(|b| b.x1 > r.x0 && b.y1 > r.y0 && b.x0 < r.x1 && b.y0 < r.y1).count()
}

struct Item {
    label: Label,
    /// The item's box (y up from its baseline origin).
    y0: f64,
    y1: f64,
    handle: usize,
}

/// The legend's frame, handles and texts, in drawing order.
pub(super) fn layout(
    fig: &Figure,
    ax: &Axes,
    r: &PxRect,
    leg: &Legend,
    m: &dyn TextMeasure,
    avoid: &Avoid,
) -> Vec<Prim> {
    let entries: Vec<usize> = ax
        .lines
        .iter()
        .enumerate()
        .filter(|(_, l)| l.label.as_deref().is_some_and(|s| !s.is_empty() && !s.starts_with('_')))
        .map(|(i, _)| i)
        .collect();
    if entries.is_empty() {
        return Vec::new();
    }
    let dpi_cor = fig.dpi / 72.0;
    let fs = leg.fontsize_pt;
    let fs_px = fs * dpi_cor;
    // The handle's DrawingArea, in points (descent 0 for handleheight 0.7).
    let descent = 0.35 * fs * (leg.handleheight - 0.7);
    let da_w = leg.handlelength * fs;
    let da_h = fs * leg.handleheight - descent;
    let text_sep = leg.handletextpad * fs_px;
    let lp = m.measure("lp", fs_px, leg.face);
    let items: Vec<Item> = entries
        .iter()
        .map(|&i| {
            let label =
                Label::new(ax.lines[i].label.clone().unwrap_or_default(), fs, leg.text_color).with_face(leg.face);
            // TextArea.get_bbox for one line.
            let (_, h, d, _) = line_metrics(m, &label.text, fs_px, leg.face);
            let text_h = (lp.height - lp.descent).max(h - d) + d;
            let (ty0, ty1) = (-d, text_h - d);
            let (dy0, dy1) = (-descent * dpi_cor, (da_h - descent) * dpi_cor);
            Item { label, y0: ty0.min(dy0), y1: ty1.max(dy1), handle: i }
        })
        .collect();
    let widths: Vec<f64> = items
        .iter()
        .map(|it| da_w * dpi_cor + text_sep + line_metrics(m, &it.label.text, fs_px, leg.face).0.width)
        .collect();
    // The column (VPacker, baseline-aligned), then the padded legend box.
    let sep = leg.labelspacing * fs_px;
    let mut packed = Vec::new();
    let mut acc = 0.0;
    for it in &items {
        packed.push(acc);
        acc += it.y1 - it.y0 + sep;
    }
    let height = acc - sep;
    let mut yoff: Vec<f64> = items.iter().zip(&packed).map(|(it, o)| height - (o + it.y1)).collect();
    let ydesc = yoff[0];
    for v in &mut yoff {
        *v -= ydesc;
    }
    let width = widths.iter().copied().fold(0.0, f64::max);
    let pad = leg.borderpad * fs_px;
    let (bw, bh) = (width + 2.0 * pad, height + 2.0 * pad);
    let (xdescent, ydescent) = (pad, ydesc + pad);

    // Placement in display coordinates (y up), as matplotlib computes it.
    let hfig = fig.height;
    let parent = (r.x0, hfig - r.y1, r.x1, hfig - r.y0);
    let border = leg.borderaxespad * fs_px;
    let anchored = |loc: Loc| {
        let (l, b, w, h) = (
            parent.0 + border,
            parent.1 + border,
            parent.2 - parent.0 - 2.0 * border,
            parent.3 - parent.1 - 2.0 * border,
        );
        let (cx, cy) = loc.coefs();
        (l + cx * (w - bw), b + cy * (h - bh))
    };
    let (lx, ly) = if leg.loc == Loc::Best {
        // The candidates in display pixels, y down as the data are.
        let mut best: Option<(usize, usize, (f64, f64))> = None;
        for (idx, &loc) in FIXED.iter().enumerate() {
            let (l, b) = anchored(loc);
            let rect = PxRect { x0: l, x1: l + bw, y0: hfig - (b + bh), y1: hfig - b };
            let badness = avoid.lines.iter().map(|p| count_contains(&rect, p)).sum::<usize>()
                + count_contains(&rect, &avoid.offsets)
                + count_overlaps(&rect, &avoid.bboxes)
                + avoid.lines.iter().filter(|p| path_intersects_rect(p, &rect)).count();
            if best.is_none_or(|b| (badness, idx) < (b.0, b.1)) {
                best = Some((badness, idx, (l, b)));
            }
            if badness == 0 {
                break;
            }
        }
        best.map(|b| b.2).unwrap_or_else(|| anchored(Loc::UpperRight))
    } else {
        anchored(leg.loc)
    };
    let (px, py) = (lx + xdescent, ly + ydescent);

    let mut out = vec![Prim::Frame {
        rect: PxRect { x0: lx, x1: lx + bw, y0: hfig - (ly + bh), y1: hfig - ly },
        face: leg.frame_face,
        edge: leg.frame_edge,
        width: leg.frame_width_pt * dpi_cor,
    }];
    for (it, dy) in items.iter().zip(&yoff) {
        let (ox, oy) = (px, py + dy);
        // HandlerLine2D with one point: a three-point line, the marker on
        // the middle one, half the DrawingArea's height above its origin.
        let line = &ax.lines[it.handle];
        let hy = hfig - (oy + (da_h - descent) / 2.0 * dpi_cor);
        let xs = [0.0, da_w / 2.0, da_w].map(|v| ox + v * dpi_cor);
        if let Some(w) = line.width_pt {
            let points = Arc::new(xs.iter().map(|&x| (x, hy)).collect());
            out.push(Prim::Path { points, color: line.color, width: w * dpi_cor, clip: None, dash: None });
        }
        if let Some(mk) = &line.marker {
            out.push(fig.markers(mk, Arc::new(vec![(xs[1], hy)]), None));
        }
        let anchor = (ox + da_w * dpi_cor + text_sep, hfig - oy);
        out.push(place_text(m, anchor, &it.label, fs_px, HAlign::Left, VAlign::Baseline, 0.0, false).0);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn segment_through_rectangle_intersects() {
        let r = PxRect { x0: 10.0, y0: 10.0, x1: 20.0, y1: 20.0 };
        assert!(path_intersects_rect(&[(0.0, 15.0), (30.0, 15.0)], &r));
        assert!(!path_intersects_rect(&[(0.0, 0.0), (30.0, 5.0)], &r));
        assert!(path_intersects_rect(&[(12.0, 12.0)], &r));
        assert_eq!(count_contains(&r, &[(10.0, 15.0), (15.0, 15.0)]), 1);
    }
}
