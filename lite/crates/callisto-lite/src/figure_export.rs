//! Saving a matplotlib-style figure as the full app's
//! `savefig(path, dpi, bbox_inches="tight", facecolor="white")` does:
//! cropped to the drawn content plus 0.1 inch, at the requested dpi.
//! PNG and TIFF are rasterised; SVG is written as vector paths (text as
//! glyph outlines, as matplotlib's default `svg.fonttype = 'path'`); PDF
//! and EPS are vector files too ([`crate::vector_export`]).

use std::io::Write;
use std::path::Path;

use anyhow::{Context, Result, bail};
use callisto_core::mpl::{Face, Figure, GlyphRun, MarkerKind, Prim, PxRect, image_pixels, rasterize_image, rasterize_image_mesh, render};
use tiny_skia::{FillRule, Paint, PathBuilder, Pixmap, Stroke, Transform};

use crate::fonts::{MplMeasure, mpl_font, with_mpl_face};

thread_local! {
    /// `pad_inches` of the export in progress.
    static PAD_INCHES: std::cell::Cell<f64> = const { std::cell::Cell::new(0.1) };
}

/// The union of everything drawn, plus `pad` (`bbox_inches='tight'`).
pub(crate) fn tight_bbox(fig: &Figure, list: &callisto_core::mpl::DrawList) -> PxRect {
    let mut b = PxRect { x0: f64::INFINITY, y0: f64::INFINITY, x1: f64::NEG_INFINITY, y1: f64::NEG_INFINITY };
    let mut add = |r: &PxRect| {
        b.x0 = b.x0.min(r.x0.min(r.x1));
        b.y0 = b.y0.min(r.y0.min(r.y1));
        b.x1 = b.x1.max(r.x0.max(r.x1));
        b.y1 = b.y1.max(r.y0.max(r.y1));
    };
    for r in &list.axes_rects {
        add(r);
    }
    for p in &list.prims {
        if let Prim::Text { bbox, .. } = p {
            add(bbox);
        }
    }
    if !b.x0.is_finite() {
        return PxRect { x0: 0.0, y0: 0.0, x1: fig.width, y1: fig.height };
    }
    let pad = PAD_INCHES.with(|p| p.get()) * fig.dpi;
    PxRect { x0: b.x0 - pad, y0: b.y0 - pad, x1: b.x1 + pad, y1: b.y1 + pad }
}

fn paint(c: [u8; 4]) -> Paint<'static> {
    let mut p = Paint::default();
    p.set_color_rgba8(c[0], c[1], c[2], c[3]);
    p.anti_alias = true;
    p
}

/// Glyph outlines of `text` at `size_px` in `face`, pen at the origin, y
/// down; a math label's glyphs where mathtext placed them.
pub(crate) fn text_path(ctx: &egui::Context, text: &str, size_px: f64, face: Face, runs: &[GlyphRun]) -> Option<tiny_skia::Path> {
    if runs.is_empty() && matches!(face, Face::Styled { .. }) {
        // A graph font list: each glyph from the font that has it.
        let mut pb = PathBuilder::new();
        crate::fonts::styled_layout(face, text, size_px, |ft, gid, x| {
            let scale = (size_px / ft.units_per_em() as f64) as f32;
            ft.outline_glyph(gid, &mut Outline { pb: &mut pb, scale, dx: x as f32, dy: 0.0 });
        })?;
        return pb.finish();
    }
    // (character, x, y, size)
    let glyphs: Vec<(char, f32, f32, f64)> = if runs.is_empty() {
        let galley = ctx.fonts_mut(|f| f.layout_no_wrap(text.to_owned(), mpl_font(face, size_px as f32), egui::Color32::WHITE));
        galley.rows.first().map(|r| r.row.glyphs.iter().map(|g| (g.chr, g.pos.x, 0.0, size_px)).collect()).unwrap_or_default()
    } else {
        runs.iter().map(|g| (g.c, g.dx as f32, g.dy as f32, g.size_px)).collect()
    };
    let mut pb = PathBuilder::new();
    let mut missing = Vec::new();
    with_mpl_face(face, |f| {
        for g @ (ch, x, y, size) in &glyphs {
            let Some(id) = f.glyph_index(*ch) else {
                missing.push(*g);
                continue;
            };
            let scale = (size / f.units_per_em() as f64) as f32;
            f.outline_glyph(id, &mut Outline { pb: &mut pb, scale, dx: *x, dy: *y });
        }
    });
    // matplotlib falls back glyph by glyph (the Origin face's "ₛ", "☉").
    if !missing.is_empty() && face != Face::Ui {
        with_mpl_face(Face::Ui, |f| {
            for (ch, x, y, size) in &missing {
                let Some(id) = f.glyph_index(*ch) else { continue };
                let scale = (size / f.units_per_em() as f64) as f32;
                f.outline_glyph(id, &mut Outline { pb: &mut pb, scale, dx: *x, dy: *y });
            }
        });
    }
    pb.finish()
}

struct Outline<'a> {
    pb: &'a mut PathBuilder,
    scale: f32,
    dx: f32,
    dy: f32,
}

impl ttf_parser::OutlineBuilder for Outline<'_> {
    fn move_to(&mut self, x: f32, y: f32) {
        self.pb.move_to(self.dx + x * self.scale, self.dy - y * self.scale);
    }
    fn line_to(&mut self, x: f32, y: f32) {
        self.pb.line_to(self.dx + x * self.scale, self.dy - y * self.scale);
    }
    fn quad_to(&mut self, x1: f32, y1: f32, x: f32, y: f32) {
        let (s, dx, dy) = (self.scale, self.dx, self.dy);
        self.pb.quad_to(dx + x1 * s, dy - y1 * s, dx + x * s, dy - y * s);
    }
    fn curve_to(&mut self, x1: f32, y1: f32, x2: f32, y2: f32, x: f32, y: f32) {
        let (s, dx, dy) = (self.scale, self.dx, self.dy);
        self.pb.cubic_to(dx + x1 * s, dy - y1 * s, dx + x2 * s, dy - y2 * s, dx + x * s, dy - y * s);
    }
    fn close(&mut self) {
        self.pb.close();
    }
}

/// The figure as pixels at `scale` device pixels per logical pixel,
/// cropped to its tight bounding box. Like `savefig(dpi=…)`, the figure is
/// laid out again at that dpi (text is hinted at its own size), and Agg's
/// canvas is the box truncated to whole pixels, anchored at its
/// bottom-left corner.
pub fn rasterize(ctx: &egui::Context, fig: &Figure, scale: f64) -> Result<Pixmap> {
    let mut at_dpi = fig.clone();
    at_dpi.width *= scale;
    at_dpi.height *= scale;
    at_dpi.dpi *= scale;
    // `savefig(bbox_inches="tight")` lays the figure out once, for the
    // tight box, then draws with the layout engine off.
    let fig = &at_dpi;
    let mut list = fig.layout(&MplMeasure { ctx });
    let bbox = tight_bbox(fig, &list);
    let w = (bbox.x1 - bbox.x0).floor().max(1.0) as u32;
    let h = (bbox.y1 - bbox.y0).floor().max(1.0) as u32;
    list.translate(-bbox.x0, h as f64 - bbox.y1);
    let mut pix = Pixmap::new(w, h).context("figure too large to export")?;
    pix.fill(tiny_skia::Color::WHITE);
    let to = Transform::identity();
    for p in &list.prims {
        match p {
            Prim::Fill { rect, color } => {
                if let Some(r) = tiny_skia::Rect::from_ltrb(rect.x0 as f32, rect.y0 as f32, rect.x1 as f32, rect.y1 as f32) {
                    pix.fill_rect(r, &paint(*color), to, None);
                }
            }
            Prim::Line { a, b, color, width, square_caps } => {
                let mut pb = PathBuilder::new();
                pb.move_to(a.0 as f32, a.1 as f32);
                pb.line_to(b.0 as f32, b.1 as f32);
                if let Some(path) = pb.finish()
                    && *width > 0.0
                {
                    let stroke = Stroke {
                        width: *width as f32,
                        line_cap: if *square_caps { tiny_skia::LineCap::Square } else { tiny_skia::LineCap::Butt },
                        ..Stroke::default()
                    };
                    pix.stroke_path(&path, &paint(*color), &stroke, to, None);
                }
            }
            Prim::Image { axes, image, dest, clip, log_y } => {
                let Some(im) = fig.axes.get(*axes).and_then(|a| a.images.get(*image)) else { continue };
                let map = |x: f64, y: f64| (x, y);
                let (dx0, dy0) = map(dest.x0.min(dest.x1), dest.y0.min(dest.y1));
                let (dx1, dy1) = map(dest.x0.max(dest.x1), dest.y0.max(dest.y1));
                let (cx0, cy0) = map(clip.x0, clip.y0);
                let (cx1, cy1) = map(clip.x1, clip.y1);
                let Some((out, iw, ih, px)) = image_pixels(im, [dx0, dy0, dx1, dy1], [cx0, cy0, cx1, cy1], *log_y) else {
                    continue;
                };
                let Some(mut sub) = Pixmap::new(iw as u32, ih as u32) else { continue };
                for (i, c) in px.as_chunks::<4>().0.iter().enumerate() {
                    let a = c[3] as u32;
                    let pm = |v: u8| ((v as u32 * a + 127) / 255) as u8;
                    let d = &mut sub.data_mut()[i * 4..i * 4 + 4];
                    d.copy_from_slice(&[pm(c[0]), pm(c[1]), pm(c[2]), c[3]]);
                }
                pix.draw_pixmap(out[0] as i32, out[1] as i32, sub.as_ref(), &tiny_skia::PixmapPaint::default(), Transform::identity(), None);
            }
            Prim::Markers { .. } | Prim::Path { .. } | Prim::Frame { .. } | Prim::Hatch { .. } => {
                render::draw(p, &mut pix.as_mut(), 1.0, (0.0, 0.0));
            }
            Prim::Text { pos, text, size_px, color, rotation, face, glyphs, .. } => {
                if let Some(path) = text_path(ctx, text, *size_px, *face, glyphs) {
                    let (dx, dy) = callisto_core::mpl::agg_rotated_text_offset(*rotation);
                    let t = Transform::from_rotate(-*rotation as f32).post_translate(pos.0 as f32, pos.1 as f32).post_concat(to).post_translate(dx as f32, dy as f32);
                    pix.fill_path(&path, &paint(*color), FillRule::Winding, t, None);
                }
            }
        }
    }
    Ok(pix)
}

/// `savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")`, the
/// format from the extension.
pub fn save_figure(ctx: &egui::Context, fig: &Figure, path: &Path, dpi: f64) -> Result<()> {
    save_figure_padded(ctx, fig, path, dpi, 0.1)
}

/// `figure_png_bytes(fig, dpi=…, tight=True)`: the figure as PNG bytes,
/// with its size and RGBA rows.
pub fn png_bytes(ctx: &egui::Context, fig: &Figure, dpi: f64, pad_inches: f64) -> Result<(Vec<u8>, u32, u32, Vec<u8>)> {
    PAD_INCHES.with(|p| p.set(pad_inches));
    let pix = rasterize(ctx, fig, dpi / fig.dpi)?;
    let (w, h) = (pix.width(), pix.height());
    let rgba = pix.data().to_vec();
    let mut png = Vec::new();
    image::write_buffer_with_format(&mut std::io::Cursor::new(&mut png), &rgba, w, h, image::ExtendedColorType::Rgba8, image::ImageFormat::Png)?;
    Ok((png, w, h, rgba))
}

/// [`save_figure`] with `pad_inches` around the drawing (the full app's
/// `save_figure(tight=True)` pads 0.08 inch); JPEG at quality 95.
pub fn save_figure_padded(ctx: &egui::Context, fig: &Figure, path: &Path, dpi: f64, pad_inches: f64) -> Result<()> {
    PAD_INCHES.with(|p| p.set(pad_inches));
    let ext = path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_else(|| "png".into());
    let scale = dpi / fig.dpi;
    let vector = match ext.as_str() {
        "svg" => Some(svg(ctx, fig)?.into_bytes()),
        "pdf" => Some(crate::vector_export::pdf(ctx, fig, dpi)?),
        "eps" | "ps" => Some(crate::vector_export::eps(ctx, fig, dpi)?),
        _ => None,
    };
    if let Some(bytes) = vector {
        return std::fs::write(path, bytes).with_context(|| format!("writing {}", path.display()));
    }
    let pix = rasterize(ctx, fig, scale)?;
    let (w, h) = (pix.width(), pix.height());
    // Unpremultiply onto opaque white (the background is opaque).
    let rgba: Vec<u8> = pix.data().to_vec();
    let rgb: Vec<u8> = rgba.as_chunks::<4>().0.iter().flat_map(|c| [c[0], c[1], c[2]]).collect();
    match ext.as_str() {
        "png" => image::save_buffer_with_format(path, &rgba, w, h, image::ExtendedColorType::Rgba8, image::ImageFormat::Png)?,
        "tif" | "tiff" => image::save_buffer_with_format(path, &rgba, w, h, image::ExtendedColorType::Rgba8, image::ImageFormat::Tiff)?,
        "jpg" | "jpeg" => {
            let file = std::fs::File::create(path)?;
            let mut enc = image::codecs::jpeg::JpegEncoder::new_with_quality(std::io::BufWriter::new(file), 95);
            enc.encode(&rgb, w, h, image::ExtendedColorType::Rgb8)?;
        }
        other => bail!("Format '{other}' is not supported."),
    }
    Ok(())
}

/// The comparison's visible export (`_export_visible_plot`): raster
/// formats as captured, PDF on an A4 page with the image scaled to fit and
/// centred (`QPdfWriter` at 300 dpi), EPS at 300 dpi, SVG with the PNG embedded.
pub fn save_raster(path: &Path, ext: &str, rgba: &[u8], w: u32, h: u32) -> Result<()> {
    let rgb: Vec<u8> = rgba.as_chunks::<4>().0.iter().flat_map(|c| [c[0], c[1], c[2]]).collect();
    match ext {
        "png" => image::save_buffer_with_format(path, rgba, w, h, image::ExtendedColorType::Rgba8, image::ImageFormat::Png)?,
        "tif" | "tiff" => image::save_buffer_with_format(path, rgba, w, h, image::ExtendedColorType::Rgba8, image::ImageFormat::Tiff)?,
        "jpg" | "jpeg" => {
            let file = std::fs::File::create(path)?;
            let mut enc = image::codecs::jpeg::JpegEncoder::new_with_quality(std::io::BufWriter::new(file), 95);
            enc.encode(&rgb, w, h, image::ExtendedColorType::Rgb8)?;
        }
        "pdf" => {
            let (pw, ph) = (595.276, 841.89);
            let k = (pw / w as f64).min(ph / h as f64);
            let (dw, dh) = (w as f64 * k, h as f64 * k);
            std::fs::write(path, pdf_on_page(&rgb, w, h, (pw, ph), [(pw - dw) / 2.0, (ph - dh) / 2.0, dw, dh])?)?;
        }
        "eps" => std::fs::write(path, eps(&rgb, w, h, 300.0))?,
        "svg" => {
            let mut png = Vec::new();
            image::write_buffer_with_format(&mut std::io::Cursor::new(&mut png), rgba, w, h, image::ExtendedColorType::Rgba8, image::ImageFormat::Png)?;
            let svg = format!(
                "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\" viewBox=\"0 0 {w} {h}\">
<image width=\"{w}\" height=\"{h}\" href=\"data:image/png;base64,{}\"/>
</svg>
",
                base64(&png)
            );
            std::fs::write(path, svg)?;
        }
        other => bail!("Unsupported export format: {other}."),
    }
    Ok(())
}

/// A one-page PDF of `page` points with the image in `[x, y, width, height]`.
fn pdf_on_page(rgb: &[u8], w: u32, h: u32, page: (f64, f64), place: [f64; 4]) -> Result<Vec<u8>> {
    let mut z = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    z.write_all(rgb)?;
    let data = z.finish()?;
    let (pw, ph) = page;
    let [px, py, dw, dh] = place;
    let content = format!("q {dw:.3} 0 0 {dh:.3} {px:.3} {py:.3} cm /Im0 Do Q");
    let mut out: Vec<u8> = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n".to_vec();
    let mut offsets = Vec::new();
    let mut obj = |out: &mut Vec<u8>, body: Vec<u8>| {
        offsets.push(out.len());
        out.extend(format!("{} 0 obj\n", offsets.len()).as_bytes());
        out.extend(body);
        out.extend(b"\nendobj\n");
    };
    obj(&mut out, b"<< /Type /Catalog /Pages 2 0 R >>".to_vec());
    obj(&mut out, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec());
    obj(&mut out, format!("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.3} {ph:.3}] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>").into_bytes());
    obj(&mut out, format!("<< /Length {} >>\nstream\n{content}\nendstream", content.len()).into_bytes());
    let mut img = format!("<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {} >>\nstream\n", data.len()).into_bytes();
    img.extend(&data);
    img.extend(b"\nendstream");
    obj(&mut out, img);
    let xref = out.len();
    out.extend(format!("xref\n0 {}\n0000000000 65535 f \n", offsets.len() + 1).as_bytes());
    for o in &offsets {
        out.extend(format!("{o:010} 00000 n \n").as_bytes());
    }
    out.extend(format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n", offsets.len() + 1).as_bytes());
    Ok(out)
}

/// An EPS image of the figure at its physical size.
fn eps(rgb: &[u8], w: u32, h: u32, dpi: f64) -> Vec<u8> {
    let (pw, ph) = (w as f64 * 72.0 / dpi, h as f64 * 72.0 / dpi);
    let mut s = format!(
        "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 {} {}\n%%HiResBoundingBox: 0 0 {pw:.3} {ph:.3}\n%%Creator: e-CALLISTO FITS Analyzer Lite\n%%EndComments\n\
         gsave\n{pw:.3} {ph:.3} scale\n/line {} string def\n{w} {h} 8 [{w} 0 0 -{h} 0 {h}]\n{{currentfile line readhexstring pop}} false 3 colorimage\n",
        pw.ceil() as i64,
        ph.ceil() as i64,
        w * 3
    );
    for row in rgb.chunks(w as usize * 3) {
        for (i, b) in row.iter().enumerate() {
            s.push_str(&format!("{b:02x}"));
            if i % 39 == 38 {
                s.push('\n');
            }
        }
        s.push('\n');
    }
    s.push_str("grestore\nshowpage\n%%EOF\n");
    s.into_bytes()
}

fn base64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        for k in 0..4 {
            if k <= c.len() {
                out.push(T[(n >> (18 - 6 * k) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

fn svg_color(c: [u8; 4]) -> String {
    format!("#{:02x}{:02x}{:02x}\" fill-opacity=\"{:.3}", c[0], c[1], c[2], c[3] as f64 / 255.0)
}

fn svg_stroke(c: [u8; 4]) -> String {
    format!("#{:02x}{:02x}{:02x}\" stroke-opacity=\"{:.3}", c[0], c[1], c[2], c[3] as f64 / 255.0)
}

/// A marker, curve or frame primitive as SVG elements.
fn svg_shape(p: &Prim) -> String {
    let mut s = String::new();
    match p {
        Prim::Markers { kind, centers, size, face, edge, edge_width, .. } => {
            let h = size / 2.0;
            let style = format!("fill=\"{}\" stroke=\"{}\" stroke-width=\"{edge_width:.3}\"", svg_color(*face), svg_stroke(*edge));
            for &(x, y) in centers.iter() {
                s.push_str(&match kind {
                    MarkerKind::Square => format!("<rect x=\"{:.3}\" y=\"{:.3}\" width=\"{size:.3}\" height=\"{size:.3}\" {style}/>\n", x - h, y - h),
                    MarkerKind::Circle => format!("<circle cx=\"{x:.3}\" cy=\"{y:.3}\" r=\"{h:.3}\" {style}/>\n"),
                    MarkerKind::Star => {
                        let pts: Vec<String> = (0..10)
                            .map(|k| {
                                let t = std::f64::consts::PI / 5.0 * k as f64 + std::f64::consts::FRAC_PI_2;
                                let r = if k % 2 == 1 { 0.381966 } else { 1.0 } * h;
                                format!("{:.3},{:.3}", x + r * t.cos(), y - r * t.sin())
                            })
                            .collect();
                        format!("<polygon points=\"{}\" {style} stroke-linejoin=\"bevel\"/>\n", pts.join(" "))
                    }
                });
            }
        }
        Prim::Path { points, color, width, dash, .. } => {
            let pts: Vec<String> = points.iter().map(|(x, y)| format!("{x:.3},{y:.3}")).collect();
            let (cap, dashes) = match dash {
                Some((on, off)) => ("butt", format!(" stroke-dasharray=\"{on:.3},{off:.3}\"")),
                None => ("square", String::new()),
            };
            s.push_str(&format!(
                "<polyline points=\"{}\" fill=\"none\" stroke=\"{}\" stroke-width=\"{width:.3}\" stroke-linecap=\"{cap}\" stroke-linejoin=\"round\"{dashes}/>\n",
                pts.join(" "),
                svg_stroke(*color)
            ));
        }
        Prim::Hatch { rect, face, hatch, hatch_width, spacing, .. } => {
            let (x, y, w, h) = (rect.x0, rect.y0, rect.x1 - rect.x0, rect.y1 - rect.y0);
            s.push_str(&format!("<rect x=\"{x:.3}\" y=\"{y:.3}\" width=\"{w:.3}\" height=\"{h:.3}\" fill=\"{}\"/>\n", svg_color(*face)));
            // The hatch lines, clipped to the band.
            let id = format!("hatch{}", (x * 1000.0 + y * 7.0) as i64);
            s.push_str(&format!(
                "<clipPath id=\"{id}\"><rect x=\"{x:.3}\" y=\"{y:.3}\" width=\"{w:.3}\" height=\"{h:.3}\"/></clipPath>\n<g clip-path=\"url(#{id})\" stroke=\"{}\" stroke-width=\"{hatch_width:.3}\">\n",
                svg_stroke(*hatch)
            ));
            let (k0, k1) = (((rect.x0 + rect.y0) / spacing).floor() as i64 - 1, ((rect.x1 + rect.y1) / spacing).ceil() as i64 + 1);
            for k in k0..=k1 {
                let c = k as f64 * spacing;
                s.push_str(&format!("<line x1=\"{:.3}\" y1=\"{:.3}\" x2=\"{:.3}\" y2=\"{:.3}\"/>\n", c - rect.y0, rect.y0, c - rect.y1, rect.y1));
            }
            s.push_str("</g>\n");
        }
        Prim::Frame { rect, face, edge, width } => s.push_str(&format!(
            "<rect x=\"{:.3}\" y=\"{:.3}\" width=\"{:.3}\" height=\"{:.3}\" fill=\"{}\" stroke=\"{}\" stroke-width=\"{width:.3}\"/>\n",
            rect.x0,
            rect.y0,
            rect.x1 - rect.x0,
            rect.y1 - rect.y0,
            svg_color(*face),
            svg_stroke(*edge)
        )),
        _ => {}
    }
    s
}

/// The figure as SVG: vector shapes, images as embedded PNG, text as paths.
fn svg(ctx: &egui::Context, fig: &Figure) -> Result<String> {
    let list = fig.layout(&MplMeasure { ctx });
    let b = tight_bbox(fig, &list);
    // matplotlib writes SVG in points (72 per inch).
    let k = 72.0 / fig.dpi;
    let (w, h) = ((b.x1 - b.x0) * k, (b.y1 - b.y0) * k);
    let mut s = format!(
        "<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"no\"?>\n<svg xmlns=\"http://www.w3.org/2000/svg\" xmlns:xlink=\"http://www.w3.org/1999/xlink\" width=\"{w:.3}pt\" height=\"{h:.3}pt\" viewBox=\"0 0 {w:.3} {h:.3}\" version=\"1.1\">\n<rect width=\"100%\" height=\"100%\" fill=\"#ffffff\"/>\n<g transform=\"scale({k}) translate({:.4} {:.4})\">\n",
        -b.x0, -b.y0
    );
    for p in &list.prims {
        match p {
            Prim::Fill { rect, color } => s.push_str(&format!(
                "<rect x=\"{:.3}\" y=\"{:.3}\" width=\"{:.3}\" height=\"{:.3}\" fill=\"{}\"/>\n",
                rect.x0, rect.y0, rect.x1 - rect.x0, rect.y1 - rect.y0, svg_color(*color)
            )),
            Prim::Line { a, b, color, width, square_caps } => {
                if *width <= 0.0 {
                    continue;
                }
                s.push_str(&format!(
                    "<line x1=\"{:.3}\" y1=\"{:.3}\" x2=\"{:.3}\" y2=\"{:.3}\" stroke=\"#{:02x}{:02x}{:02x}\" stroke-opacity=\"{:.3}\" stroke-width=\"{:.3}\" stroke-linecap=\"{}\"/>\n",
                    a.0, a.1, b.0, b.1, color[0], color[1], color[2], color[3] as f64 / 255.0, width, if *square_caps { "square" } else { "butt" }
                ));
            }
            Prim::Image { axes, image, dest, clip, log_y } => {
                let Some(im) = fig.axes.get(*axes).and_then(|a| a.images.get(*image)) else { continue };
                // Rasterised at 3× (matplotlib's image.composite_image at 72 dpi is coarser).
                let sc = 3.0;
                let (x0, x1) = (dest.x0.min(dest.x1).max(clip.x0), dest.x0.max(dest.x1).min(clip.x1));
                let (y0, y1) = (dest.y0.min(dest.y1).max(clip.y0), dest.y0.max(dest.y1).min(clip.y1));
                if x1 <= x0 || y1 <= y0 {
                    continue;
                }
                let d = [dest.x0.min(dest.x1) * sc, dest.y0.min(dest.y1) * sc, dest.x0.max(dest.x1) * sc, dest.y0.max(dest.y1) * sc];
                let out = [(x0 * sc).floor() as i64, (y0 * sc).floor() as i64, (x1 * sc).ceil() as i64, (y1 * sc).ceil() as i64];
                let c = [clip.x0 * sc, clip.y0 * sc, clip.x1 * sc, clip.y1 * sc];
                let (out, iw, ih, px) = match log_y {
                    Some(log) => match rasterize_image_mesh(im, d, c, *log) {
                        Some(r) => r,
                        None => continue,
                    },
                    None => {
                        let (iw, ih, px) = rasterize_image(im, d, out);
                        (out, iw, ih, px)
                    }
                };
                let mut png = Vec::new();
                image::write_buffer_with_format(&mut std::io::Cursor::new(&mut png), &px, iw as u32, ih as u32, image::ExtendedColorType::Rgba8, image::ImageFormat::Png)?;
                s.push_str(&format!(
                    "<image x=\"{:.3}\" y=\"{:.3}\" width=\"{:.3}\" height=\"{:.3}\" preserveAspectRatio=\"none\" xlink:href=\"data:image/png;base64,{}\"/>\n",
                    out[0] as f64 / sc, out[1] as f64 / sc, iw as f64 / sc, ih as f64 / sc, base64(&png)
                ));
            }
            Prim::Markers { .. } | Prim::Path { .. } | Prim::Frame { .. } | Prim::Hatch { .. } => s.push_str(&svg_shape(p)),
            Prim::Text { pos, text, size_px, color, rotation, face, glyphs, .. } => {
                let Some(path) = text_path(ctx, text, *size_px, *face, glyphs) else { continue };
                let mut d = String::new();
                for seg in path.segments() {
                    use tiny_skia::PathSegment as P;
                    match seg {
                        P::MoveTo(p) => d.push_str(&format!("M{:.2} {:.2}", p.x, p.y)),
                        P::LineTo(p) => d.push_str(&format!("L{:.2} {:.2}", p.x, p.y)),
                        P::QuadTo(a, p) => d.push_str(&format!("Q{:.2} {:.2} {:.2} {:.2}", a.x, a.y, p.x, p.y)),
                        P::CubicTo(a, b2, p) => d.push_str(&format!("C{:.2} {:.2} {:.2} {:.2} {:.2} {:.2}", a.x, a.y, b2.x, b2.y, p.x, p.y)),
                        P::Close => d.push('Z'),
                    }
                }
                s.push_str(&format!(
                    "<path transform=\"translate({:.3} {:.3}) rotate({:.3})\" d=\"{d}\" fill=\"{}\"/>\n",
                    pos.0, pos.1, -rotation, svg_color(*color)
                ));
            }
        }
    }
    s.push_str("</g>\n</svg>\n");
    Ok(s)
}
