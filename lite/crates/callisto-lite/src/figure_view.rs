//! Draws a matplotlib-style [`Figure`] (`callisto_core::mpl`) the way the
//! full app's `FigureCanvasQTAgg` shows it: laid out at the widget's size,
//! rendered at device resolution, straight lines snapped to pixels as Agg
//! snaps them, images resampled into device pixels, markers, curves and
//! frames rasterised as Agg draws them (`mpl::render`).

use egui::{Color32, Pos2, Rect, Stroke, TextureHandle, TextureOptions, Ui, pos2, vec2};

use callisto_core::mpl::{DrawList, Figure, Prim, PxRect, agg_rotated_text_offset, image_pixels, render};

use crate::fonts::{MplMeasure, mpl_font};

struct Cache {
    size: (u32, u32),
    ppp: f32,
    list: DrawList,
    /// One texture per image, marker, curve or frame primitive, with its
    /// logical rectangle.
    images: Vec<Option<(Rect, TextureHandle)>>,
    /// Where the figure was last drawn (its top left, device-rounded).
    origin: Pos2,
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

    /// Axes `axes`' rectangle on screen, as last drawn.
    pub fn axes_rect(&self, axes: usize) -> Option<Rect> {
        let c = self.cache.as_ref()?;
        let r = c.list.axes_rects.get(axes)?;
        Some(Rect::from_min_max(
            pos2(c.origin.x + r.x0 as f32, c.origin.y + r.y0 as f32),
            pos2(c.origin.x + r.x1 as f32, c.origin.y + r.y1 as f32),
        ))
    }

    /// The data coordinates of screen position `pos` in axes `axes`
    /// (`event.xdata`, `event.ydata`), wherever it is.
    pub fn data_at(&self, pos: Pos2, axes: usize) -> Option<(f64, f64)> {
        let r = self.axes_rect(axes)?;
        let ax = self.figure.as_ref()?.axes.get(axes)?;
        let fx = (pos.x - r.left()) as f64 / r.width() as f64;
        let fy = (r.bottom() - pos.y) as f64 / r.height() as f64;
        Some((ax.xlim.0 + fx * (ax.xlim.1 - ax.xlim.0), ax.ylim.0 + fy * (ax.ylim.1 - ax.ylim.0)))
    }

    /// The screen position of data point `(x, y)` in axes `axes`.
    pub fn screen_at(&self, x: f64, y: f64, axes: usize) -> Option<Pos2> {
        let r = self.axes_rect(axes)?;
        let ax = self.figure.as_ref()?.axes.get(axes)?;
        let fx = (x - ax.xlim.0) / (ax.xlim.1 - ax.xlim.0);
        let fy = (y - ax.ylim.0) / (ax.ylim.1 - ax.ylim.0);
        Some(pos2(r.left() + fx as f32 * r.width(), r.bottom() - fy as f32 * r.height()))
    }

    fn rebuild(&mut self, ui: &Ui, size: (u32, u32), ppp: f32) {
        let Some(fig) = &mut self.figure else { return };
        fig.width = size.0 as f64;
        fig.height = size.1 as f64;
        let list = fig.layout(&MplMeasure { ctx: ui.ctx() });
        let mut images = Vec::new();
        for (i, p) in list.prims.iter().enumerate() {
            if let Some(b) = render::device_bounds(p, ppp as f64) {
                // Rasterised into the device pixels it touches.
                let (w, h) = ((b[2] - b[0]) as u32, (b[3] - b[1]) as u32);
                let Some(mut pix) = tiny_skia::Pixmap::new(w, h) else {
                    images.push(None);
                    continue;
                };
                render::draw(p, &mut pix.as_mut(), ppp as f64, (b[0] as f64, b[1] as f64));
                let img = egui::ColorImage::from_rgba_premultiplied([w as usize, h as usize], pix.data());
                let tex = ui.ctx().load_texture(format!("figure-prim-{i}"), img, TextureOptions::NEAREST);
                let r = Rect::from_min_max(
                    pos2(b[0] as f32 / ppp, b[1] as f32 / ppp),
                    pos2(b[2] as f32 / ppp, b[3] as f32 / ppp),
                );
                images.push(Some((r, tex)));
                continue;
            }
            let Prim::Image { axes, image, dest, clip, log_y } = p else {
                images.push(None);
                continue;
            };
            let Some(im) = fig.axes.get(*axes).and_then(|a| a.images.get(*image)) else {
                images.push(None);
                continue;
            };
            let s = ppp as f64;
            let d = [
                dest.x0.min(dest.x1) * s,
                dest.y0.min(dest.y1) * s,
                dest.x0.max(dest.x1) * s,
                dest.y0.max(dest.y1) * s,
            ];
            let c = [clip.x0 * s, clip.y0 * s, clip.x1 * s, clip.y1 * s];
            let Some((out, w, h, px)) = image_pixels(im, d, c, *log_y) else {
                images.push(None);
                continue;
            };
            let img = egui::ColorImage::from_rgba_unmultiplied([w, h], &px);
            let tex = ui.ctx().load_texture(format!("figure-image-{i}"), img, TextureOptions::NEAREST);
            let r = Rect::from_min_max(
                pos2(out[0] as f32 / ppp, out[1] as f32 / ppp),
                pos2(out[2] as f32 / ppp, out[3] as f32 / ppp),
            );
            images.push(Some((r, tex)));
        }
        self.cache = Some(Cache { size, ppp, list, images, origin: Pos2::ZERO });
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
        // The canvas renders the figure into its own pixel buffer and Qt
        // blits it at the widget's device position, rounded (`qRound`).
        let origin = pos2((rect.min.x * ppp).round() / ppp, (rect.min.y * ppp).round() / ppp);
        if let Some(c) = &mut self.cache {
            c.origin = origin;
        }
        let Some(cache) = &self.cache else { return };
        let painter = ui.painter_at(rect);
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
                Prim::Image { .. }
                | Prim::Markers { .. }
                | Prim::Path { .. }
                | Prim::Frame { .. }
                | Prim::Hatch { .. } => {
                    if let Some(Some((r, tex))) = cache.images.get(i) {
                        let r = r.translate(origin.to_vec2());
                        painter.image(tex.id(), r, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
                    }
                }
                Prim::Text { pos, glyphs, color: c, face, .. } if !glyphs.is_empty() => {
                    // A math label: each glyph where mathtext placed it.
                    let pen = at(pos.0, pos.1);
                    for g in glyphs {
                        let galley = ui.ctx().fonts_mut(|f| {
                            f.layout_no_wrap(g.c.to_string(), mpl_font(*face, g.size_px as f32), color(*c))
                        });
                        let baseline = galley
                            .rows
                            .first()
                            .and_then(|r| r.row.glyphs.first().map(|gl| r.pos.y + gl.pos.y))
                            .unwrap_or(galley.size().y * 0.8);
                        // Agg blits the label as one bitmap whose left column is
                        // the raster's `xmin` (a pixel before the ink) and whose
                        // rows are counted from the descent: on screen its glyphs
                        // land a device pixel right and two up of their pens.
                        let x = ((pen.x + g.dx as f32) * ppp).round() / ppp + 1.0 / ppp;
                        let y = ((pen.y + g.dy as f32 - baseline) * ppp).round() / ppp - 2.0 / ppp;
                        painter.add(egui::epaint::TextShape::new(pos2(x, y), galley, color(*c)));
                    }
                }
                Prim::Text { pos, text, size_px, color: c, rotation, face, .. } => {
                    let galley = ui
                        .ctx()
                        .fonts_mut(|f| f.layout_no_wrap(text.clone(), mpl_font(*face, *size_px as f32), color(*c)));
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
                        let top_left =
                            pen - vec2(baseline * rad.sin(), baseline * rad.cos()) + vec2(dx as f32, dy as f32) / ppp;
                        egui::epaint::TextShape::new(top_left, galley, color(*c)).with_angle(-rad)
                    };
                    painter.add(shape);
                }
            }
        }
    }
}

fn px_rect(origin: Pos2, r: &PxRect) -> Rect {
    Rect::from_min_max(
        pos2(origin.x + r.x0 as f32, origin.y + r.y0 as f32),
        pos2(origin.x + r.x1 as f32, origin.y + r.y1 as f32),
    )
}
