//! Writing laid-out report pages as PDF: DejaVu Sans embedded whole as CID
//! TrueType fonts (Identity-H, with the widths and a ToUnicode map of the
//! glyphs used, so text can be copied), the standard Helvetica for the
//! header and footer, and PNG figures as image XObjects.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::io::Write as _;

use anyhow::{Context, Result};

use super::platypus::{BOLD, Draw, FontId, REGULAR, TtMetrics};

fn deflate(data: &[u8]) -> Result<Vec<u8>> {
    let mut z = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    z.write_all(data)?;
    Ok(z.finish()?)
}

/// A PDF text string: ASCII as `(...)`, anything else as UTF-16BE hex.
fn text_string(s: &str) -> String {
    if s.is_ascii() {
        let mut out = String::from("(");
        for c in s.chars() {
            if matches!(c, '(' | ')' | '\\') {
                out.push('\\');
            }
            out.push(c);
        }
        out.push(')');
        out
    } else {
        let mut out = String::from("<FEFF");
        for u in s.encode_utf16() {
            let _ = write!(out, "{u:04X}");
        }
        out.push('>');
        out
    }
}

fn rgb(c: [u8; 3]) -> String {
    format!("{:.4} {:.4} {:.4}", c[0] as f64 / 255.0, c[1] as f64 / 255.0, c[2] as f64 / 255.0)
}

/// An image's RGB rows, its alpha when not opaque, and its size.
type PageImage = (Vec<u8>, Option<Vec<u8>>, u32, u32);

/// The glyphs a TrueType font draws, with the characters they stand for.
#[derive(Default)]
struct Used {
    glyphs: BTreeMap<u16, char>,
}

/// A WinAnsi string for the standard font: Latin-1 as is, the rest as `?`.
fn win_ansi(s: &str) -> String {
    let mut out = String::from("(");
    for c in s.chars() {
        let b = if (c as u32) < 256 { c as u32 as u8 } else { b'?' };
        match b {
            b'(' | b')' | b'\\' => {
                out.push('\\');
                out.push(b as char);
            }
            0x20..=0x7e => out.push(b as char),
            _ => {
                let _ = write!(out, "\\{b:03o}");
            }
        }
    }
    out.push(')');
    out
}

/// Writes `pages` (drawn in points, `page` in size) as a PDF document.
pub fn write(pages: &[Vec<Draw>], page: (f64, f64), title: &str, author: &str) -> Result<Vec<u8>> {
    let faces = [
        ttf_parser::Face::parse(REGULAR.data, 0).context("bundled font")?,
        ttf_parser::Face::parse(BOLD.data, 0).context("bundled font")?,
    ];
    let mut used = [Used::default(), Used::default()];
    let mut images: Vec<PageImage> = Vec::new();
    let mut contents = Vec::new();
    for draws in pages {
        let mut s = String::new();
        let mut page_images = Vec::new();
        for d in draws {
            match d {
                Draw::Rect { x, y, w, h, color } => {
                    let _ = writeln!(s, "{} rg {x:.3} {y:.3} {w:.3} {h:.3} re f", rgb(*color));
                }
                Draw::Line { x0, y0, x1, y1, width, color } => {
                    let _ = writeln!(s, "{} RG {width:.3} w {x0:.3} {y0:.3} m {x1:.3} {y1:.3} l S", rgb(*color));
                }
                Draw::Text { x, y, text, font, size, color } => {
                    let (name, encoded) = match font {
                        FontId::Helvetica => ("F0", win_ansi(text)),
                        f => {
                            let k = if *f == FontId::Regular { 0 } else { 1 };
                            let mut hex = String::from("<");
                            for c in text.chars() {
                                let g = faces[k].glyph_index(c).map_or(0, |g| g.0);
                                used[k].glyphs.entry(g).or_insert(c);
                                let _ = write!(hex, "{g:04X}");
                            }
                            hex.push('>');
                            (if k == 0 { "F1" } else { "F2" }, hex)
                        }
                    };
                    let _ = writeln!(s, "BT /{name} {size} Tf {} rg {x:.3} {y:.3} Td {encoded} Tj ET", rgb(*color));
                }
                Draw::Image { x, y, w, h, png } => {
                    let img = image::load_from_memory(png).context("figure image")?.to_rgba8();
                    let (iw, ih) = img.dimensions();
                    let mut rgb_bytes = Vec::with_capacity((iw * ih * 3) as usize);
                    let mut alpha = Vec::with_capacity((iw * ih) as usize);
                    for p in img.pixels() {
                        rgb_bytes.extend_from_slice(&p.0[..3]);
                        alpha.push(p.0[3]);
                    }
                    let mask = alpha.iter().any(|a| *a != 255).then_some(alpha);
                    let i = images.len();
                    images.push((rgb_bytes, mask, iw, ih));
                    page_images.push(i);
                    let _ = writeln!(s, "q {w:.3} 0 0 {h:.3} {x:.3} {y:.3} cm /Im{i} Do Q");
                }
            }
        }
        contents.push((s, page_images));
    }

    // Objects: 1 catalog, 2 pages, 3 info, 4 Helvetica, 5–8 and 9–12 the
    // two embedded fonts (font, CID font, descriptor, file, then ToUnicode),
    // then images (each with its mask), then pages with their contents.
    let mut objects: Vec<Vec<u8>> = Vec::new();
    let add = |objects: &mut Vec<Vec<u8>>, body: Vec<u8>| -> usize {
        objects.push(body);
        objects.len()
    };
    let stream = |dict: String, data: &[u8]| -> Vec<u8> {
        let mut v = format!("<< {dict} /Length {} >>\nstream\n", data.len()).into_bytes();
        v.extend_from_slice(data);
        v.extend_from_slice(b"\nendstream");
        v
    };
    add(&mut objects, b"<< /Type /Catalog /Pages 2 0 R >>".to_vec());
    add(&mut objects, Vec::new()); // the page tree, once the pages are numbered
    add(&mut objects, format!("<< /Title {} /Author {} /Creator (e-CALLISTO FITS Analyzer Lite) /Producer (e-CALLISTO FITS Analyzer Lite) >>", text_string(title), text_string(author)).into_bytes());
    add(&mut objects, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec());
    let mut font_ids = [0usize; 2];
    for (k, (m, face)) in [(&*REGULAR, &faces[0]), (&*BOLD, &faces[1])].into_iter().enumerate() {
        font_ids[k] = embed_font(&mut objects, m, face, &used[k], &stream)?;
    }
    let mut image_ids = Vec::new();
    for (rgb_bytes, mask, w, h) in &images {
        let mask_id = match mask {
            Some(a) => Some(add(
                &mut objects,
                stream(
                    format!(
                        "/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode"
                    ),
                    &deflate(a)?,
                ),
            )),
            None => None,
        };
        let smask = mask_id.map(|m| format!(" /SMask {m} 0 R")).unwrap_or_default();
        image_ids.push(add(
            &mut objects,
            stream(format!("/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode{smask}"), &deflate(rgb_bytes)?),
        ));
    }
    let mut kids = Vec::new();
    for (s, page_images) in &contents {
        let content = add(&mut objects, stream("/Filter /FlateDecode".into(), &deflate(s.as_bytes())?));
        let xobjects: String = page_images.iter().map(|i| format!(" /Im{i} {} 0 R", image_ids[*i])).collect();
        let page_id = add(
            &mut objects,
            format!(
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {:.4} {:.4}] /Resources << /Font << /F0 4 0 R /F1 {} 0 R /F2 {} 0 R >> /XObject <<{xobjects} >> >> /Contents {content} 0 R >>",
                page.0, page.1, font_ids[0], font_ids[1]
            )
            .into_bytes(),
        );
        kids.push(format!("{page_id} 0 R"));
    }
    objects[1] = format!("<< /Type /Pages /Kids [{}] /Count {} >>", kids.join(" "), kids.len()).into_bytes();

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
        format!("trailer\n<< /Size {} /Root 1 0 R /Info 3 0 R >>\nstartxref\n{xref}\n%%EOF\n", offsets.len() + 1)
            .as_bytes(),
    );
    Ok(out)
}

/// A TrueType font as a Type0 font over a CIDFontType2 (glyph ids as CIDs):
/// the file embedded, the used glyphs' widths, a ToUnicode map. Returns the
/// Type0 font's object number.
fn embed_font(
    objects: &mut Vec<Vec<u8>>,
    m: &TtMetrics,
    face: &ttf_parser::Face,
    used: &Used,
    stream: &dyn Fn(String, &[u8]) -> Vec<u8>,
) -> Result<usize> {
    let scale = 1000.0 / m.units_per_em as f64;
    let file = deflate(m.data)?;
    objects.push(stream(format!("/Length1 {} /Filter /FlateDecode", m.data.len()), &file));
    let file_id = objects.len();
    let flags = 32; // Nonsymbolic
    objects.push(
        format!(
            "<< /Type /FontDescriptor /FontName /{} /Flags {flags} /FontBBox [{:.0} {:.0} {:.0} {:.0}] /ItalicAngle {:.0} /Ascent {:.0} /Descent {:.0} /CapHeight {:.0} /StemV 80 /FontFile2 {file_id} 0 R >>",
            m.name, m.bbox[0], m.bbox[1], m.bbox[2], m.bbox[3], m.italic_angle, m.ascent, m.descent, m.cap_height
        )
        .into_bytes(),
    );
    let descriptor = objects.len();
    let widths: String = used
        .glyphs
        .keys()
        .map(|g| format!("{g} [{:.3}]", face.glyph_hor_advance(ttf_parser::GlyphId(*g)).unwrap_or(0) as f64 * scale))
        .collect::<Vec<_>>()
        .join(" ");
    objects.push(
        format!(
            "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{} /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor {descriptor} 0 R /CIDToGIDMap /Identity /W [{widths}] >>",
            m.name
        )
        .into_bytes(),
    );
    let cid = objects.len();
    let mut cmap = String::from(
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n",
    );
    let entries: Vec<(&u16, &char)> = used.glyphs.iter().collect();
    for chunk in entries.chunks(100) {
        let _ = writeln!(cmap, "{} beginbfchar", chunk.len());
        for (g, c) in chunk {
            let mut hex = String::new();
            for u in c.encode_utf16(&mut [0; 2]).iter() {
                let _ = write!(hex, "{u:04X}");
            }
            let _ = writeln!(cmap, "<{g:04X}> <{hex}>");
        }
        cmap.push_str("endbfchar\n");
    }
    cmap.push_str("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n");
    objects.push(stream("/Filter /FlateDecode".into(), &deflate(cmap.as_bytes())?));
    let to_unicode = objects.len();
    objects.push(
        format!("<< /Type /Font /Subtype /Type0 /BaseFont /{} /Encoding /Identity-H /DescendantFonts [{cid} 0 R] /ToUnicode {to_unicode} 0 R >>", m.name).into_bytes(),
    );
    Ok(objects.len())
}
