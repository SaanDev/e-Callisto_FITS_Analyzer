//! Draws a matplotlib-style [`Figure`] (`callisto_core::mpl`) the way the
//! full app's `FigureCanvasQTAgg` shows it: laid out at the widget's size,
//! rendered at device resolution, straight lines snapped to pixels as Agg
//! snaps them, images resampled into device pixels.

use egui::{Color32, Pos2, Rect, Stroke, TextureHandle, TextureOptions, Ui, pos2, vec2};

use callisto_core::mpl::{DrawList, Figure, Prim, PxRect, agg_rotated_text_offset, image_placement, rasterize_image};

use crate::fonts::MplMeasure;

struct Cache {
    size: (u32, u32),
    ppp: f32,
    list: DrawList,
    /// One texture per image primitive, with its logical rectangle.
    images: Vec<Option<(Rect, TextureHandle)>>,
}

#[derive(Default)]
pub struct FigureView {
    figure: Option<Figure>,
    cache: Option<Cache>,
}

fn color(c: [u8; 4]) -> Color32 {
    Color32::from_rgba_unmultiplied(c[0], c[1], c[2], c[3])
}

impl FigureView {
    pub fn new(figure: Figure) -> Self {
        FigureView { figure: Some(figure), cache: None }
    }

    pub fn set(&mut self, figure: Figure) {
        self.figure = Some(figure);
        self.cache = None;
    }

    pub fn clear(&mut self) {
        self.figure = None;
        self.cache = None;
    }

    pub fn figure(&self) -> Option<&Figure> {
        self.figure.as_ref()
    }

    pub fn has_figure(&self) -> bool {
        self.figure.is_some()
    }

    fn rebuild(&mut self, ui: &Ui, size: (u32, u32), ppp: f32) {
        let Some(fig) = &mut self.figure else { return };
        fig.width = size.0 as f64;
        fig.height = size.1 as f64;
        let list = fig.layout(&MplMeasure { ctx: ui.ctx() });
        let mut images = Vec::new();
        for (i, p) in list.prims.iter().enumerate() {
            let Prim::Image { axes, image, dest, clip } = p else {
                images.push(None);
                continue;
            };
            let Some(im) = fig.axes.get(*axes).and_then(|a| a.images.get(*image)) else {
                images.push(None);
                continue;
            };
            let s = ppp as f64;
            let d = [dest.x0.min(dest.x1) * s, dest.y0.min(dest.y1) * s, dest.x0.max(dest.x1) * s, dest.y0.max(dest.y1) * s];
            let c = [clip.x0 * s, clip.y0 * s, clip.x1 * s, clip.y1 * s];
            let Some((out, virtual_dest)) = image_placement(d, c) else {
                images.push(None);
                continue;
            };
            let (w, h, px) = rasterize_image(im, virtual_dest, out);
            let img = egui::ColorImage::from_rgba_unmultiplied([w, h], &px);
            let tex = ui.ctx().load_texture(format!("figure-image-{i}"), img, TextureOptions::NEAREST);
            let r = Rect::from_min_max(
                pos2(out[0] as f32 / ppp, out[1] as f32 / ppp),
                pos2(out[2] as f32 / ppp, out[3] as f32 / ppp),
            );
            images.push(Some((r, tex)));
        }
        self.cache = Some(Cache { size, ppp, list, images });
    }

    /// Draws the figure filling `rect` (whole logical pixels, as Qt sizes
    /// the canvas widget).
    pub fn show(&mut self, ui: &Ui, rect: Rect) {
        if self.figure.is_none() {
            return;
        }
        let ppp = ui.ctx().pixels_per_point();
        let size = (rect.width().round().max(1.0) as u32, rect.height().round().max(1.0) as u32);
        if self.cache.as_ref().is_none_or(|c| c.size != size || c.ppp != ppp) {
            self.rebuild(ui, size, ppp);
        }
        let Some(cache) = &self.cache else { return };
        let painter = ui.painter_at(rect);
        // The canvas renders the figure into its own pixel buffer and Qt
        // blits it at the widget's device position, rounded (`qRound`).
        let origin = pos2((rect.min.x * ppp).round() / ppp, (rect.min.y * ppp).round() / ppp);
        let snap = |v: f32, width_px: f32| {
            // PathSnapper: floor(x + 0.5) + 0.5 for odd stroke widths.
            let odd = (width_px.round() as i64) % 2 == 1;
            (v * ppp + 0.5).floor() / ppp + if odd { 0.5 / ppp } else { 0.0 }
        };
        let at = |x: f64, y: f64| -> Pos2 { pos2(origin.x + x as f32, origin.y + y as f32) };
        for (i, p) in cache.list.prims.iter().enumerate() {
            match p {
                Prim::Fill { rect: r, color: c } => {
                    painter.rect_filled(px_rect(origin, r), 0.0, color(*c));
                }
                Prim::Line { a, b, color: c, width, square_caps } => {
                    let w = *width as f32;
                    let (mut p0, mut p1) = (at(a.0, a.1), at(b.0, b.1));
                    let wpx = w * ppp;
                    if (p0.x - p1.x).abs() < 1e-6 {
                        let x = snap(p0.x, wpx);
                        p0.x = x;
                        p1.x = x;
                    }
                    if (p0.y - p1.y).abs() < 1e-6 {
                        let y = snap(p0.y, wpx);
                        p0.y = y;
                        p1.y = y;
                    }
                    if *square_caps {
                        let dir = (p1 - p0).normalized() * (w / 2.0);
                        p0 -= dir;
                        p1 += dir;
                    }
                    painter.line_segment([p0, p1], Stroke::new(w, color(*c)));
                }
                Prim::Image { .. } => {
                    if let Some(Some((r, tex))) = cache.images.get(i) {
                        let r = r.translate(origin.to_vec2());
                        painter.image(tex.id(), r, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
                    }
                }
                Prim::Text { pos, text, size_px, color: c, rotation, .. } => {
                    let galley = ui.ctx().fonts_mut(|f| {
                        f.layout_no_wrap(text.clone(), egui::FontId::proportional(*size_px as f32), color(*c))
                    });
                    let baseline = galley
                        .rows
                        .first()
                        .and_then(|r| r.row.glyphs.first().map(|g| r.pos.y + g.pos.y))
                        .unwrap_or(galley.size().y * 0.8);
                    let pen = at(pos.0, pos.1);
                    let shape = if rotation.abs() < 1e-9 {
                        let top = pos2(pen.x, ((pen.y - baseline) * ppp).round() / ppp);
                        egui::epaint::TextShape::new(pos2((top.x * ppp).round() / ppp, top.y), galley, color(*c))
                    } else {
                        // Counter-clockwise on screen. egui turns clockwise
                        // about the galley's top-left corner, from which the
                        // pen sits (0, baseline) down the unrotated text.
                        let rad = rotation.to_radians() as f32;
                        let (dx, dy) = agg_rotated_text_offset(*rotation);
                        let top_left = pen - vec2(baseline * rad.sin(), baseline * rad.cos()) + vec2(dx as f32, dy as f32) / ppp;
                        egui::epaint::TextShape::new(top_left, galley, color(*c)).with_angle(-rad)
                    };
                    painter.add(shape);
                }
            }
        }
    }
}

fn px_rect(origin: Pos2, r: &PxRect) -> Rect {
    Rect::from_min_max(pos2(origin.x + r.x0 as f32, origin.y + r.y0 as f32), pos2(origin.x + r.x1 as f32, origin.y + r.y1 as f32))
}
