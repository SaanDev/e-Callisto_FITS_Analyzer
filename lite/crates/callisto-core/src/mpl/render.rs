//! Rasterising the graphs' marker, line and frame primitives as Agg draws
//! them: line markers snapped and stamped at whole device pixels
//! (`draw_markers`), scatter circles at their exact positions (a path
//! collection), lines with projecting caps and round joins, rectangle
//! patches snapped to the pixel grid (`PathSnapper`), all antialiased and
//! clipped to the axes' rounded clip box.

use tiny_skia::{FillRule, LineCap, LineJoin, Mask, Paint, PathBuilder, PixmapMut, Stroke, StrokeDash, Transform};

use super::{MarkerKind, Prim, PxRect, Rgba};

fn paint(c: Rgba) -> Paint<'static> {
    let mut p = Paint::default();
    p.set_color_rgba8(c[0], c[1], c[2], c[3]);
    p.anti_alias = true;
    p
}

/// `PathSnapper`'s offset: a half pixel for odd stroke widths.
fn snap_value(width_px: f64) -> f64 {
    if (width_px.round() as i64) % 2 == 1 { 0.5 } else { 0.0 }
}

/// Agg's clip box for a clip rectangle (`set_clipbox`): rounded to whole
/// device pixels.
fn clip_box(clip: &PxRect, scale: f64) -> [i64; 4] {
    let r = |v: f64| (v * scale + 0.5).floor() as i64;
    [r(clip.x0), r(clip.y0), r(clip.x1), r(clip.y1)]
}

/// The device pixels (figure coordinates, `[x0, y0, x1, y1)`) a marker,
/// line or frame primitive can touch, within its clip box.
pub fn device_bounds(prim: &Prim, scale: f64) -> Option<[i64; 4]> {
    let (mut x0, mut y0, mut x1, mut y1) = (f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY);
    let mut add = |x: f64, y: f64, r: f64| {
        x0 = x0.min(x - r);
        y0 = y0.min(y - r);
        x1 = x1.max(x + r);
        y1 = y1.max(y + r);
    };
    let clip = match prim {
        Prim::Markers { centers, size, edge_width, clip, .. } => {
            let r = (size / 2.0 + edge_width) * scale + 2.0;
            for &(x, y) in centers.iter() {
                add(x * scale, y * scale, r);
            }
            clip
        }
        Prim::Path { points, width, clip, .. } => {
            let r = width * scale + 2.0;
            for &(x, y) in points.iter() {
                add(x * scale, y * scale, r);
            }
            clip
        }
        Prim::Frame { rect, width, .. } => {
            add(rect.x0 * scale, rect.y0 * scale, width * scale + 2.0);
            add(rect.x1 * scale, rect.y1 * scale, width * scale + 2.0);
            &None
        }
        Prim::Hatch { rect, clip, .. } => {
            add(rect.x0 * scale, rect.y0 * scale, 1.0);
            add(rect.x1 * scale, rect.y1 * scale, 1.0);
            clip
        }
        _ => return None,
    };
    if !x0.is_finite() {
        return None;
    }
    let mut b = [x0.floor() as i64, y0.floor() as i64, x1.ceil() as i64, y1.ceil() as i64];
    if let Some(c) = clip {
        let c = clip_box(c, scale);
        b = [b[0].max(c[0]), b[1].max(c[1]), b[2].min(c[2]), b[3].min(c[3])];
    }
    (b[2] > b[0] && b[3] > b[1]).then_some(b)
}

/// `Path.unit_regular_star(5, 0.381966)` scaled to `radius`, pointing up.
fn star(cx: f32, cy: f32, radius: f32) -> Option<tiny_skia::Path> {
    let mut pb = PathBuilder::new();
    for k in 0..=10 {
        let theta = std::f32::consts::PI * 2.0 / 10.0 * k as f32 + std::f32::consts::FRAC_PI_2;
        let r = if k % 2 == 1 { 0.381966 } else { 1.0 } * radius;
        let (x, y) = (cx + r * theta.cos(), cy - r * theta.sin());
        if k == 0 {
            pb.move_to(x, y);
        } else {
            pb.line_to(x, y);
        }
    }
    pb.close();
    pb.finish()
}

/// Draws a marker, line or frame primitive into `pix`, whose pixel (0, 0)
/// is the figure's device pixel `origin`, at `scale` device pixels per
/// logical pixel. Other primitives are left to the caller.
pub fn draw(prim: &Prim, pix: &mut PixmapMut, scale: f64, origin: (f64, f64)) {
    let (ox, oy) = origin;
    let clip_mask = |clip: &Option<PxRect>, pix: &PixmapMut| -> Option<Mask> {
        let c = clip_box(clip.as_ref()?, scale);
        let (w, h) = (pix.width() as f64, pix.height() as f64);
        let r = [c[0] as f64 - ox, c[1] as f64 - oy, c[2] as f64 - ox, c[3] as f64 - oy];
        if r[0] <= 0.0 && r[1] <= 0.0 && r[2] >= w && r[3] >= h {
            return None;
        }
        let mut mask = Mask::new(pix.width(), pix.height())?;
        let rect = tiny_skia::Rect::from_ltrb(r[0] as f32, r[1] as f32, r[2] as f32, r[3] as f32)?;
        mask.fill_path(&PathBuilder::from_rect(rect), FillRule::Winding, false, Transform::identity());
        Some(mask)
    };
    match prim {
        Prim::Markers { kind, centers, size, face, edge, edge_width, snapped, clip } => {
            let mask = clip_mask(clip, pix);
            let ew = edge_width * scale;
            let join = if *kind == MarkerKind::Star { LineJoin::Bevel } else { LineJoin::Miter };
            let stroke = Stroke { width: ew as f32, line_join: join, ..Stroke::default() };
            let (fp, ep) = (paint(*face), paint(*edge));
            let half = size * scale / 2.0;
            for &(x, y) in centers.iter() {
                let (xd, yd) = (x * scale, y * scale);
                let path = match (kind, snapped) {
                    (MarkerKind::Square, true) => {
                        // The snapped unit square, stamped at the rounded centre.
                        let sv = snap_value(ew);
                        let (lo, hi) = ((-half + 0.5).floor() + sv, (half + 0.5).floor() + sv);
                        let (cx, cy) = ((xd + 0.5).floor() - ox, (yd + 0.5).floor() - oy);
                        tiny_skia::Rect::from_ltrb((cx + lo) as f32, (cy + lo) as f32, (cx + hi) as f32, (cy + hi) as f32).map(PathBuilder::from_rect)
                    }
                    (MarkerKind::Square, false) => {
                        tiny_skia::Rect::from_ltrb((xd - half - ox) as f32, (yd - half - oy) as f32, (xd + half - ox) as f32, (yd + half - oy) as f32)
                            .map(PathBuilder::from_rect)
                    }
                    (MarkerKind::Circle, true) => {
                        // Not snappable: centred on the stamped pixel's centre.
                        PathBuilder::from_circle(((xd + 0.5).floor() + 0.5 - ox) as f32, ((yd + 0.5).floor() + 0.5 - oy) as f32, half as f32)
                    }
                    (MarkerKind::Circle, false) => PathBuilder::from_circle((xd - ox) as f32, (yd - oy) as f32, half as f32),
                    (MarkerKind::Star, _) => {
                        let (cx, cy) = if *snapped { ((xd + 0.5).floor() + 0.5, (yd + 0.5).floor() + 0.5) } else { (xd, yd) };
                        star((cx - ox) as f32, (cy - oy) as f32, half as f32)
                    }
                };
                let Some(path) = path else { continue };
                if face[3] > 0 {
                    pix.fill_path(&path, &fp, FillRule::Winding, Transform::identity(), mask.as_ref());
                }
                if edge[3] > 0 && ew > 0.0 {
                    pix.stroke_path(&path, &ep, &stroke, Transform::identity(), mask.as_ref());
                }
            }
        }
        Prim::Path { points, color, width, clip, dash } => {
            let mask = clip_mask(clip, pix);
            let mut pb = PathBuilder::new();
            for (i, &(x, y)) in points.iter().enumerate() {
                let (xd, yd) = ((x * scale - ox) as f32, (y * scale - oy) as f32);
                if i == 0 {
                    pb.move_to(xd, yd);
                } else {
                    pb.line_to(xd, yd);
                }
            }
            if let Some(path) = pb.finish() {
                // `lines.solid_capstyle` projecting, `lines.dash_capstyle` butt.
                let (cap, dash) = match dash {
                    Some((on, off)) => (LineCap::Butt, StrokeDash::new(vec![(on * scale) as f32, (off * scale) as f32], 0.0)),
                    None => (LineCap::Square, None),
                };
                let stroke = Stroke { width: (width * scale) as f32, line_cap: cap, line_join: LineJoin::Round, dash, ..Stroke::default() };
                pix.stroke_path(&path, &paint(*color), &stroke, Transform::identity(), mask.as_ref());
            }
        }
        Prim::Hatch { rect, face, hatch, hatch_width, spacing, clip } => {
            // `PathSnapper` without a stroke rounds the corners to whole
            // pixels; the clip box is whole pixels too, so their overlap is.
            let snap = |v: f64| (v * scale + 0.5).floor();
            let mut b = [snap(rect.x0), snap(rect.y0), snap(rect.x1), snap(rect.y1)];
            if let Some(c) = clip {
                let c = clip_box(c, scale);
                b = [b[0].max(c[0] as f64), b[1].max(c[1] as f64), b[2].min(c[2] as f64), b[3].min(c[3] as f64)];
            }
            if b[2] <= b[0] || b[3] <= b[1] {
                return;
            }
            let Some(r) = tiny_skia::Rect::from_ltrb((b[0] - ox) as f32, (b[1] - oy) as f32, (b[2] - ox) as f32, (b[3] - oy) as f32) else { return };
            pix.fill_path(&PathBuilder::from_rect(r), &paint(*face), FillRule::Winding, Transform::identity(), None);
            let Some(mut mask) = Mask::new(pix.width(), pix.height()) else { return };
            mask.fill_path(&PathBuilder::from_rect(r), FillRule::Winding, false, Transform::identity());
            // The tile's lines, `x + y = k · spacing` from the canvas corner.
            let sp = spacing * scale;
            if sp <= 0.0 || *hatch_width <= 0.0 {
                return;
            }
            let w = hatch_width * scale;
            let mut pb = PathBuilder::new();
            let (k0, k1) = (((b[0] + b[1] - w) / sp).floor() as i64, ((b[2] + b[3] + w) / sp).ceil() as i64);
            for k in k0..=k1 {
                let c = k as f64 * sp;
                let (ya, yb) = (b[1] - w, b[3] + w);
                pb.move_to((c - ya - ox) as f32, (ya - oy) as f32);
                pb.line_to((c - yb - ox) as f32, (yb - oy) as f32);
            }
            if let Some(path) = pb.finish() {
                let stroke = Stroke { width: w as f32, line_cap: LineCap::Butt, ..Stroke::default() };
                pix.stroke_path(&path, &paint(*hatch), &stroke, Transform::identity(), Some(&mask));
            }
        }
        Prim::Frame { rect, face, edge, width } => {
            let w = width * scale;
            let sv = snap_value(w);
            let s = |v: f64, o: f64| ((v * scale + 0.5).floor() + sv - o) as f32;
            let Some(r) = tiny_skia::Rect::from_ltrb(s(rect.x0, ox), s(rect.y0, oy), s(rect.x1, ox), s(rect.y1, oy)) else { return };
            let path = PathBuilder::from_rect(r);
            pix.fill_path(&path, &paint(*face), FillRule::Winding, Transform::identity(), None);
            let stroke = Stroke { width: w as f32, line_join: LineJoin::Miter, ..Stroke::default() };
            pix.stroke_path(&path, &paint(*edge), &stroke, Transform::identity(), None);
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::*;

    #[test]
    fn line_markers_stamp_at_whole_pixels() {
        // A 6 pt square at 150 dpi: 12.5 px, snapped to 12, edged 2 px.
        let prim = Prim::Markers {
            kind: MarkerKind::Square,
            centers: Arc::new(vec![(10.2, 10.2)]),
            size: 6.0 * 100.0 / 72.0,
            face: [0, 0, 0, 255],
            edge: [0, 0, 0, 255],
            edge_width: 100.0 / 72.0,
            snapped: true,
            clip: None,
        };
        let mut pix = tiny_skia::Pixmap::new(40, 40).unwrap();
        draw(&prim, &mut pix.as_mut(), 1.5, (0.0, 0.0));
        let alpha = |x: u32, y: u32| pix.pixel(x, y).unwrap().alpha();
        // Centre 15.3 rounds to 15: ink from 15 − 6 − 1 to 15 + 6 + 1.
        assert_eq!(alpha(8, 15), 255);
        assert_eq!(alpha(21, 15), 255);
        assert!(alpha(7, 15) < 20 && alpha(22, 15) < 20);
    }
}
