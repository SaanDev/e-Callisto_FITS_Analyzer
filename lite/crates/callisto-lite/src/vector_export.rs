//! PDF and EPS figures as vector files, as matplotlib's PDF and PostScript
//! backends write them for `savefig(bbox_inches="tight")`: the page is the
//! figure's tight box in points, shapes are paths, images are embedded at
//! the export's dpi, and text is drawn as glyph outlines (the look of
//! matplotlib's embedded Type 42 fonts, though not selectable).
//!
//! PostScript has no transparency: as matplotlib's backend does, partially
//! transparent shapes are drawn opaque and images lose their alpha.

use std::fmt::Write as _;
use std::io::Write as _;

use anyhow::Result;
use callisto_core::mpl::{Figure, MarkerKind, Prim, PxRect, image_pixels};

use crate::figure_export::{text_path, tight_bbox};
use crate::fonts::MplMeasure;

/// Bézier control-point factor for a quarter circle.
const KAPPA: f64 = 0.552_284_749_830_793_4;

#[derive(Clone, Copy, PartialEq)]
enum Format {
    Pdf,
    Eps,
}

/// An image on the page: pixel size, RGB and alpha rows.
struct PageImage {
    w: u32,
    h: u32,
    rgb: Vec<u8>,
    alpha: Vec<u8>,
}

/// The page's content in PDF or PostScript operators, in points with y up.
struct Painter {
    fmt: Format,
    out: String,
    /// Figure pixels → points: scale, then the tight box's left and bottom.
    k: f64,
    x0: f64,
    y1: f64,
    /// PDF graphics states by fill and stroke alpha (`/ca`, `/CA`).
    alphas: Vec<(u8, u8)>,
    images: Vec<PageImage>,
}

impl Painter {
    fn x(&self, x: f64) -> f64 {
        (x - self.x0) * self.k
    }

    fn y(&self, y: f64) -> f64 {
        (self.y1 - y) * self.k
    }

    fn op(&mut self, pdf: &str, ps: &str) {
        self.out.push_str(if self.fmt == Format::Pdf { pdf } else { ps });
        self.out.push('\n');
    }

    fn save(&mut self) {
        self.op("q", "gsave");
    }

    fn restore(&mut self) {
        self.op("Q", "grestore");
    }

    fn move_to(&mut self, x: f64, y: f64) {
        let (x, y) = (self.x(x), self.y(y));
        let _ = writeln!(self.out, "{x:.3} {y:.3} {}", if self.fmt == Format::Pdf { "m" } else { "moveto" });
    }

    fn line_to(&mut self, x: f64, y: f64) {
        let (x, y) = (self.x(x), self.y(y));
        let _ = writeln!(self.out, "{x:.3} {y:.3} {}", if self.fmt == Format::Pdf { "l" } else { "lineto" });
    }

    fn curve_to(&mut self, a: (f64, f64), b: (f64, f64), c: (f64, f64)) {
        let p = [self.x(a.0), self.y(a.1), self.x(b.0), self.y(b.1), self.x(c.0), self.y(c.1)];
        let _ = writeln!(
            self.out,
            "{:.3} {:.3} {:.3} {:.3} {:.3} {:.3} {}",
            p[0],
            p[1],
            p[2],
            p[3],
            p[4],
            p[5],
            if self.fmt == Format::Pdf { "c" } else { "curveto" }
        );
    }

    fn close(&mut self) {
        self.op("h", "closepath");
    }

    fn rect(&mut self, r: &PxRect) {
        self.move_to(r.x0, r.y0);
        self.line_to(r.x1, r.y0);
        self.line_to(r.x1, r.y1);
        self.line_to(r.x0, r.y1);
        self.close();
    }

    fn circle(&mut self, cx: f64, cy: f64, r: f64) {
        let k = KAPPA * r;
        self.move_to(cx + r, cy);
        self.curve_to((cx + r, cy + k), (cx + k, cy + r), (cx, cy + r));
        self.curve_to((cx - k, cy + r), (cx - r, cy + k), (cx - r, cy));
        self.curve_to((cx - r, cy - k), (cx - k, cy - r), (cx, cy - r));
        self.curve_to((cx + k, cy - r), (cx + r, cy - k), (cx + r, cy));
        self.close();
    }

    /// The colour for the next fill or stroke.
    fn color(&mut self, c: [u8; 4], stroke: bool) {
        let (r, g, b) = (c[0] as f64 / 255.0, c[1] as f64 / 255.0, c[2] as f64 / 255.0);
        let op = match (self.fmt, stroke) {
            (Format::Pdf, true) => "RG",
            (Format::Pdf, false) => "rg",
            (Format::Eps, _) => "setrgbcolor",
        };
        let _ = writeln!(self.out, "{r:.4} {g:.4} {b:.4} {op}");
    }

    /// PDF's fill and stroke alpha for the next shape (`/ca`, `/CA`).
    fn alpha(&mut self, fill: u8, stroke: u8) {
        if self.fmt != Format::Pdf || (fill == 255 && stroke == 255) {
            return;
        }
        let key = (fill, stroke);
        let i = self.alphas.iter().position(|a| *a == key).unwrap_or_else(|| {
            self.alphas.push(key);
            self.alphas.len() - 1
        });
        let _ = writeln!(self.out, "/A{i} gs");
    }

    fn stroke_style(&mut self, width_px: f64, cap: u8, join: u8, dash: Option<(f64, f64)>) {
        let w = width_px * self.k;
        let dash = match dash {
            Some((on, off)) => format!("[{:.3} {:.3}] 0", on * self.k, off * self.k),
            None => "[] 0".to_owned(),
        };
        match self.fmt {
            Format::Pdf => {
                let _ = writeln!(self.out, "{w:.3} w {cap} J {join} j {dash} d");
            }
            Format::Eps => {
                let _ = writeln!(self.out, "{w:.3} setlinewidth {cap} setlinecap {join} setlinejoin {dash} setdash");
            }
        }
    }

    fn clip_rect(&mut self, r: &PxRect) {
        self.rect(r);
        self.op("W n", "clip newpath");
    }
}

/// Builds a shape's path with `build`, then fills and/or strokes it: PDF
/// needs the colours set before the path, PostScript fills inside a
/// `gsave` to stroke the same path after.
/// A stroke: colour, width in pixels, cap and join (PDF numbering), dashes.
type Edge = ([u8; 4], f64, u8, u8, Option<(f64, f64)>);

fn paint(p: &mut Painter, face: Option<[u8; 4]>, edge: Option<Edge>, build: impl Fn(&mut Painter)) {
    let face = face.filter(|c| c[3] > 0);
    let edge = edge.filter(|e| e.0[3] > 0 && e.1 > 0.0);
    if face.is_none() && edge.is_none() {
        return;
    }
    p.save();
    match p.fmt {
        Format::Pdf => {
            p.alpha(face.map_or(255, |c| c[3]), edge.map_or(255, |e| e.0[3]));
            if let Some(c) = face {
                p.color(c, false);
            }
            if let Some((c, w, cap, join, dash)) = edge {
                p.color(c, true);
                p.stroke_style(w, cap, join, dash);
            }
            build(p);
            p.op(
                match (face.is_some(), edge.is_some()) {
                    (true, true) => "B",
                    (true, false) => "f",
                    _ => "S",
                },
                "",
            );
        }
        Format::Eps => {
            if let Some(c) = face {
                build(p);
                p.color(c, false);
                if edge.is_some() {
                    p.op("", "gsave fill grestore");
                } else {
                    p.op("", "fill");
                }
            }
            if let Some((c, w, cap, join, dash)) = edge {
                if face.is_none() {
                    build(p);
                }
                p.color(c, true);
                p.stroke_style(w, cap, join, dash);
                p.op("", "stroke");
            }
        }
    }
    p.restore();
}

fn lay_out(ctx: &egui::Context, fig: &Figure, fmt: Format, dpi: f64) -> Result<(Painter, f64, f64)> {
    let list = fig.layout(&MplMeasure { ctx });
    let b = tight_bbox(fig, &list);
    let k = 72.0 / fig.dpi;
    let (pw, ph) = ((b.x1 - b.x0) * k, (b.y1 - b.y0) * k);
    let mut p = Painter { fmt, out: String::new(), k, x0: b.x0, y1: b.y1, alphas: Vec::new(), images: Vec::new() };
    // `savefig(facecolor="white")`.
    paint(&mut p, Some([255, 255, 255, 255]), None, |p| p.rect(&b));
    // Images are composited at the export's resolution.
    let sc = dpi / fig.dpi;
    for prim in &list.prims {
        match prim {
            Prim::Fill { rect, color } => paint(&mut p, Some(*color), None, |p| p.rect(rect)),
            Prim::Line { a, b, color, width, square_caps } => {
                let (a, b) = (*a, *b);
                paint(&mut p, None, Some((*color, *width, if *square_caps { 2 } else { 0 }, 0, None)), move |p| {
                    p.move_to(a.0, a.1);
                    p.line_to(b.0, b.1);
                });
            }
            Prim::Image { axes, image, dest, clip, log_y } => {
                let Some(im) = fig.axes.get(*axes).and_then(|a| a.images.get(*image)) else { continue };
                let d = [
                    dest.x0.min(dest.x1) * sc,
                    dest.y0.min(dest.y1) * sc,
                    dest.x0.max(dest.x1) * sc,
                    dest.y0.max(dest.y1) * sc,
                ];
                let c = [clip.x0 * sc, clip.y0 * sc, clip.x1 * sc, clip.y1 * sc];
                let Some((out, w, h, px)) = image_pixels(im, d, c, *log_y) else { continue };
                let (mut rgb, mut alpha) = (Vec::with_capacity(w * h * 3), Vec::with_capacity(w * h));
                for c in px.as_chunks::<4>().0 {
                    rgb.extend_from_slice(&c[..3]);
                    alpha.push(c[3]);
                }
                let r = [
                    p.x(out[0] as f64 / sc),
                    p.y(out[3] as f64 / sc),
                    (out[2] - out[0]) as f64 / sc * k,
                    (out[3] - out[1]) as f64 / sc * k,
                ];
                let i = p.images.len();
                p.images.push(PageImage { w: w as u32, h: h as u32, rgb, alpha });
                match fmt {
                    Format::Pdf => {
                        let _ = writeln!(p.out, "q {:.3} 0 0 {:.3} {:.3} {:.3} cm /Im{i} Do Q", r[2], r[3], r[0], r[1]);
                    }
                    Format::Eps => {
                        let img = &p.images[i];
                        let mut s = format!(
                            "gsave {:.3} {:.3} translate {:.3} {:.3} scale\n{} {} 8 [{} 0 0 -{} 0 {}]\ncurrentfile /ASCIIHexDecode filter false 3 colorimage\n",
                            r[0], r[1], r[2], r[3], img.w, img.h, img.w, img.h, img.h
                        );
                        for (n, b) in img.rgb.iter().enumerate() {
                            let _ = write!(s, "{b:02x}");
                            if n % 40 == 39 {
                                s.push('\n');
                            }
                        }
                        s.push_str(">\ngrestore\n");
                        p.out.push_str(&s);
                    }
                }
            }
            Prim::Markers { kind, centers, size, face, edge, edge_width, clip, .. } => {
                p.save();
                if let Some(c) = clip {
                    p.clip_rect(c);
                }
                let half = size / 2.0;
                let join = if *kind == MarkerKind::Star { 2 } else { 0 };
                for &(x, y) in centers.iter() {
                    let kind = *kind;
                    paint(&mut p, Some(*face), Some((*edge, *edge_width, 0, join, None)), move |p| match kind {
                        MarkerKind::Circle => p.circle(x, y, half),
                        MarkerKind::Square => {
                            p.rect(&PxRect { x0: x - half, y0: y - half, x1: x + half, y1: y + half })
                        }
                        MarkerKind::Star => {
                            for i in 0..10 {
                                let t = std::f64::consts::PI / 5.0 * i as f64 + std::f64::consts::FRAC_PI_2;
                                let r = if i % 2 == 1 { 0.381966 } else { 1.0 } * half;
                                let (px, py) = (x + r * t.cos(), y - r * t.sin());
                                if i == 0 { p.move_to(px, py) } else { p.line_to(px, py) }
                            }
                            p.close();
                        }
                    });
                }
                p.restore();
            }
            Prim::Path { points, color, width, clip, dash } => {
                if points.len() < 2 {
                    continue;
                }
                p.save();
                if let Some(c) = clip {
                    p.clip_rect(c);
                }
                // Solid lines project their caps, dashes are butt-ended; joins are round.
                let cap = if dash.is_some() { 0 } else { 2 };
                let pts = points.clone();
                paint(&mut p, None, Some((*color, *width, cap, 1, *dash)), move |p| {
                    p.move_to(pts[0].0, pts[0].1);
                    for &(x, y) in &pts[1..] {
                        p.line_to(x, y);
                    }
                });
                p.restore();
            }
            Prim::Frame { rect, face, edge, width } => {
                paint(&mut p, Some(*face), Some((*edge, *width, 0, 0, None)), |p| p.rect(rect));
            }
            Prim::Hatch { rect, face, hatch, hatch_width, spacing, clip } => {
                p.save();
                if let Some(c) = clip {
                    p.clip_rect(c);
                }
                p.clip_rect(rect);
                paint(&mut p, Some(*face), None, |p| p.rect(rect));
                let (k0, k1) = (
                    ((rect.x0 + rect.y0) / spacing).floor() as i64 - 1,
                    ((rect.x1 + rect.y1) / spacing).ceil() as i64 + 1,
                );
                let (r, sp) = (rect.clone(), *spacing);
                paint(&mut p, None, Some((*hatch, *hatch_width, 0, 0, None)), move |p| {
                    for k in k0..=k1 {
                        let c = k as f64 * sp;
                        p.move_to(c - r.y0, r.y0);
                        p.line_to(c - r.y1, r.y1);
                    }
                });
                p.restore();
            }
            Prim::Text { pos, text, size_px, color, rotation, face, glyphs, .. } => {
                let Some(path) = text_path(ctx, text, *size_px, *face, glyphs) else { continue };
                let (s, c) = (-rotation.to_radians()).sin_cos();
                let (ox, oy) = *pos;
                let at = move |q: tiny_skia::Point| {
                    (ox + q.x as f64 * c - q.y as f64 * s, oy + q.x as f64 * s + q.y as f64 * c)
                };
                paint(&mut p, Some(*color), None, move |p| {
                    let mut last = (0.0, 0.0);
                    for seg in path.segments() {
                        use tiny_skia::PathSegment as S;
                        match seg {
                            S::MoveTo(q) => {
                                last = at(q);
                                p.move_to(last.0, last.1);
                            }
                            S::LineTo(q) => {
                                last = at(q);
                                p.line_to(last.0, last.1);
                            }
                            S::QuadTo(a, q) => {
                                let (a, e) = (at(a), at(q));
                                let c1 = (last.0 + 2.0 / 3.0 * (a.0 - last.0), last.1 + 2.0 / 3.0 * (a.1 - last.1));
                                let c2 = (e.0 + 2.0 / 3.0 * (a.0 - e.0), e.1 + 2.0 / 3.0 * (a.1 - e.1));
                                p.curve_to(c1, c2, e);
                                last = e;
                            }
                            S::CubicTo(a, b, q) => {
                                last = at(q);
                                p.curve_to(at(a), at(b), last);
                            }
                            S::Close => p.close(),
                        }
                    }
                });
            }
        }
    }
    Ok((p, pw, ph))
}

/// The figure as a one-page vector PDF.
pub fn pdf(ctx: &egui::Context, fig: &Figure, dpi: f64) -> Result<Vec<u8>> {
    let (p, pw, ph) = lay_out(ctx, fig, Format::Pdf, dpi)?;
    let deflate = |data: &[u8]| -> Result<Vec<u8>> {
        let mut z = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
        z.write_all(data)?;
        Ok(z.finish()?)
    };
    let mut objects: Vec<Vec<u8>> = Vec::new();
    // 1 catalog, 2 pages, 3 page, 4 content, then images (and masks).
    let mut xobjects = String::new();
    let mut image_objects = Vec::new();
    for (i, im) in p.images.iter().enumerate() {
        let n = 5 + image_objects.len();
        let opaque = im.alpha.iter().all(|a| *a == 255);
        let data = deflate(&im.rgb)?;
        let smask = if opaque { String::new() } else { format!(" /SMask {} 0 R", n + 1) };
        let mut obj = format!(
            "<< /Type /XObject /Subtype /Image /Width {} /Height {} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode{smask} /Length {} >>\nstream\n",
            im.w,
            im.h,
            data.len()
        )
        .into_bytes();
        obj.extend(&data);
        obj.extend(b"\nendstream");
        image_objects.push(obj);
        if !opaque {
            let data = deflate(&im.alpha)?;
            let mut obj = format!(
                "<< /Type /XObject /Subtype /Image /Width {} /Height {} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length {} >>\nstream\n",
                im.w,
                im.h,
                data.len()
            )
            .into_bytes();
            obj.extend(&data);
            obj.extend(b"\nendstream");
            image_objects.push(obj);
        }
        let _ = write!(xobjects, " /Im{i} {n} 0 R");
    }
    let states: String = p
        .alphas
        .iter()
        .enumerate()
        .map(|(i, (fa, sa))| {
            format!(" /A{i} << /Type /ExtGState /ca {:.4} /CA {:.4} >>", *fa as f64 / 255.0, *sa as f64 / 255.0)
        })
        .collect();
    objects.push(b"<< /Type /Catalog /Pages 2 0 R >>".to_vec());
    objects.push(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec());
    objects.push(
        format!("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.3} {ph:.3}] /Resources << /XObject <<{xobjects} >> /ExtGState <<{states} >> >> /Contents 4 0 R >>")
            .into_bytes(),
    );
    let content = deflate(p.out.as_bytes())?;
    let mut obj = format!("<< /Filter /FlateDecode /Length {} >>\nstream\n", content.len()).into_bytes();
    obj.extend(&content);
    obj.extend(b"\nendstream");
    objects.push(obj);
    objects.extend(image_objects);
    let mut out: Vec<u8> = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n".to_vec();
    let mut offsets = Vec::new();
    for (i, body) in objects.iter().enumerate() {
        offsets.push(out.len());
        out.extend(format!("{} 0 obj\n", i + 1).as_bytes());
        out.extend(body);
        out.extend(b"\nendobj\n");
    }
    let xref = out.len();
    out.extend(format!("xref\n0 {}\n0000000000 65535 f \n", offsets.len() + 1).as_bytes());
    for o in &offsets {
        out.extend(format!("{o:010} 00000 n \n").as_bytes());
    }
    out.extend(
        format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n", offsets.len() + 1).as_bytes(),
    );
    Ok(out)
}

/// The figure as vector Encapsulated PostScript.
pub fn eps(ctx: &egui::Context, fig: &Figure, dpi: f64) -> Result<Vec<u8>> {
    let (p, pw, ph) = lay_out(ctx, fig, Format::Eps, dpi)?;
    let mut s = format!(
        "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 {} {}\n%%HiResBoundingBox: 0 0 {pw:.3} {ph:.3}\n%%Creator: e-CALLISTO FITS Analyzer Lite\n%%LanguageLevel: 2\n%%EndComments\n",
        pw.ceil() as i64,
        ph.ceil() as i64
    );
    s.push_str(&p.out);
    s.push_str("showpage\n%%EOF\n");
    Ok(s.into_bytes())
}
