//! The rich text of the Type II detail labels (`QLabel` with
//! `Qt.TextFormat.RichText`, word wrap and `setMargin(2)`): blocks of
//! `<div style='margin-bottom:…'>`, `<span style='font-size:…; color:…'>`,
//! `<b>`, `<sub>`, `<sup>` and `<br>`, laid out as Qt's text engine does.
//!
//! A line is as tall as its tallest run: `ceil(max ascent + max descent)`
//! of the runs' fonts (the line break counts in the font it was written
//! in), lines follow each other without leading, and a block's bottom
//! margin separates it from the next one only. Lines wrap at the spaces;
//! a script run is set at `(pixelSize * 2) / 3`, a subscript's baseline a
//! sixth of its font's height lower, a superscript's half its height higher.

use std::rc::Rc;

use egui::{Color32, Rect, Ui, pos2};

use crate::fonts::{Weight, qt_metrics, ui_font};
use crate::ui::qlayout::{Policy, Size, WidgetSpec};

/// `QLabel.setMargin(2)`.
pub const LABEL_MARGIN: f32 = 2.0;

#[derive(Clone, Copy, Debug, PartialEq)]
struct Format {
    px: i32,
    bold: bool,
    color: Option<Color32>,
    /// -1 subscript, 1 superscript.
    script: i32,
}

impl Format {
    fn font(&self) -> egui::FontId {
        let px = if self.script != 0 { (self.px * 2) / 3 } else { self.px };
        ui_font(px as f32, if self.bold { Weight::Bold } else { Weight::Regular })
    }
}

#[derive(Clone, Debug, PartialEq)]
struct Frag {
    text: String,
    f: Format,
}

/// Hard lines (split at `<br>`), each with the format its line break was written in.
#[derive(Clone, Debug, Default, PartialEq)]
struct Block {
    lines: Vec<(Vec<Frag>, Option<Format>)>,
    margin_bottom: f32,
}

/// A parsed label text.
#[derive(Clone, Debug, PartialEq)]
pub struct Doc {
    blocks: Vec<Block>,
    base: Format,
}

fn parse_style(style: &str, f: &mut Format, margin: &mut f32) {
    for decl in style.split(';') {
        let Some((k, v)) = decl.split_once(':') else { continue };
        let (k, v) = (k.trim().to_lowercase(), v.trim());
        match k.as_str() {
            "font-size" => {
                if let Ok(px) = v.trim_end_matches("px").trim().parse::<f32>() {
                    f.px = px.round() as i32;
                }
            }
            "color" => f.color = parse_hex(v),
            "margin-bottom" => {
                if let Ok(px) = v.trim_end_matches("px").trim().parse::<f32>() {
                    *margin = px;
                }
            }
            _ => {}
        }
    }
}

fn parse_hex(v: &str) -> Option<Color32> {
    let h = v.trim().trim_start_matches('#');
    let n = u32::from_str_radix(h, 16).ok()?;
    match h.len() {
        6 => Some(Color32::from_rgb((n >> 16) as u8, (n >> 8) as u8, n as u8)),
        3 => {
            let d = |s: u32| ((n >> s) & 0xf) as u8 * 17;
            Some(Color32::from_rgb(d(8), d(4), d(0)))
        }
        _ => None,
    }
}

fn attr<'a>(tag: &'a str, name: &str) -> Option<&'a str> {
    let i = tag.find(name)?;
    let rest = tag[i + name.len()..].trim_start().strip_prefix('=')?.trim_start();
    let q = rest.chars().next()?;
    if q != '\'' && q != '"' {
        return None;
    }
    let end = rest[1..].find(q)?;
    Some(&rest[1..1 + end])
}

impl Doc {
    /// `html` in a label whose own font is `base_px` regular.
    pub fn parse(html: &str, base_px: i32) -> Doc {
        let base = Format { px: base_px, bold: false, color: None, script: 0 };
        let mut stack: Vec<(String, Format)> = Vec::new();
        let mut f = base;
        let mut blocks: Vec<Block> = vec![Block::default()];
        let mut line: Vec<Frag> = Vec::new();
        let mut after_space = true;
        let mut text_in_block = false;
        let mut rest = html;
        let push_text = |t: &str, f: Format, line: &mut Vec<Frag>, after_space: &mut bool| {
            let t = t.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", "\u{a0}").replace("&amp;", "&");
            let mut out = String::new();
            for c in t.chars() {
                if c.is_whitespace() && c != '\u{a0}' {
                    if !*after_space {
                        out.push(' ');
                    }
                    *after_space = true;
                } else {
                    out.push(c);
                    *after_space = false;
                }
            }
            if out.is_empty() {
                return;
            }
            match line.last_mut() {
                Some(fr) if fr.f == f => fr.text.push_str(&out),
                _ => line.push(Frag { text: out, f }),
            }
        };
        loop {
            let (text, tag) = match rest.find('<') {
                Some(i) => (&rest[..i], Some(i)),
                None => (rest, None),
            };
            if !text.is_empty() {
                push_text(text, f, &mut line, &mut after_space);
                text_in_block |= !line.is_empty();
            }
            let Some(i) = tag else { break };
            let Some(j) = rest[i..].find('>') else { break };
            let raw = rest[i + 1..i + j].trim();
            rest = &rest[i + j + 1..];
            let lower = raw.to_lowercase();
            let name = lower.trim_start_matches('/').split_whitespace().next().unwrap_or("").trim_end_matches('/').to_owned();
            let closing = lower.starts_with('/');
            match (name.as_str(), closing) {
                ("br", _) => {
                    let done = std::mem::take(&mut line);
                    blocks.last_mut().unwrap().lines.push((done, Some(f)));
                    after_space = true;
                }
                ("div" | "p", false) => {
                    if text_in_block || !line.is_empty() {
                        let done = std::mem::take(&mut line);
                        blocks.last_mut().unwrap().lines.push((done, None));
                        blocks.push(Block::default());
                    }
                    let mut nf = f;
                    let mut margin = 0.0;
                    if let Some(style) = attr(raw, "style") {
                        parse_style(style, &mut nf, &mut margin);
                    }
                    blocks.last_mut().unwrap().margin_bottom = margin;
                    stack.push((name.clone(), f));
                    f = nf;
                    text_in_block = false;
                    after_space = true;
                }
                ("div" | "p", true) => {
                    let done = std::mem::take(&mut line);
                    blocks.last_mut().unwrap().lines.push((done, None));
                    blocks.push(Block::default());
                    if let Some(pos) = stack.iter().rposition(|(n, _)| *n == name) {
                        f = stack[pos].1;
                        stack.truncate(pos);
                    }
                    text_in_block = false;
                    after_space = true;
                }
                (_, false) => {
                    let mut nf = f;
                    match name.as_str() {
                        "b" | "strong" => nf.bold = true,
                        "sub" => nf.script = -1,
                        "sup" => nf.script = 1,
                        "span" | "font" => {
                            let mut margin = 0.0;
                            if let Some(style) = attr(raw, "style") {
                                parse_style(style, &mut nf, &mut margin);
                            }
                        }
                        _ => {}
                    }
                    stack.push((name.clone(), f));
                    f = nf;
                }
                (_, true) => {
                    if let Some(pos) = stack.iter().rposition(|(n, _)| *n == name) {
                        f = stack[pos].1;
                        stack.truncate(pos);
                    }
                }
            }
        }
        if !line.is_empty() {
            blocks.last_mut().unwrap().lines.push((line, None));
        }
        // Blocks a closing tag left empty at the end.
        while blocks.len() > 1 && blocks.last().is_some_and(|b| b.lines.is_empty()) {
            blocks.pop();
        }
        Doc { blocks, base }
    }
}

/// A frag's advance, ascent and descent.
fn measure(ctx: &egui::Context, text: &str, f: &Format) -> (f32, f32, f32) {
    let font = f.font();
    let w = if text.is_empty() {
        0.0
    } else {
        ctx.fonts_mut(|fonts| fonts.layout_no_wrap(text.to_owned(), font.clone(), Color32::WHITE).size().x)
    };
    let (a, d) = qt_metrics(&font).map(|m| (m.ascent, m.descent)).unwrap_or((font.size * 1.06, font.size * 0.27));
    (w, a, d)
}

/// One laid-out line: its pieces (x, text, format) and metrics.
struct Line {
    pieces: Vec<(f32, String, Format)>,
    ascent: f32,
    height: f32,
}

/// Lays `doc` out at `width`; returns its lines with their tops, and the height.
fn layout(ctx: &egui::Context, doc: &Doc, width: f32) -> (Vec<(f32, Line)>, f32) {
    let mut out = Vec::new();
    let mut y = 0.0;
    for (bi, block) in doc.blocks.iter().enumerate() {
        let lines = if block.lines.is_empty() { vec![(Vec::new(), None)] } else { block.lines.clone() };
        for (frags, br) in &lines {
            for line in wrap(ctx, frags, *br, doc.base, width) {
                let h = line.height;
                out.push((y, line));
                y += h;
            }
        }
        if bi + 1 < doc.blocks.len() {
            y += block.margin_bottom;
        }
    }
    (out, y)
}

/// The words of a hard line wrapped at `width`.
fn wrap(ctx: &egui::Context, frags: &[Frag], br: Option<Format>, base: Format, width: f32) -> Vec<Line> {
    // Words: pieces up to and including a space.
    let mut words: Vec<Vec<(String, Format)>> = vec![Vec::new()];
    for fr in frags {
        let mut cur = String::new();
        for c in fr.text.chars() {
            cur.push(c);
            if c == ' ' {
                words.last_mut().unwrap().push((std::mem::take(&mut cur), fr.f));
                words.push(Vec::new());
            }
        }
        if !cur.is_empty() {
            words.last_mut().unwrap().push((cur, fr.f));
        }
    }
    words.retain(|w| !w.is_empty());
    let mut lines: Vec<Vec<(String, Format)>> = vec![Vec::new()];
    let mut x = 0.0;
    for w in words {
        let full: f32 = w.iter().map(|(t, f)| measure(ctx, t, f).0).sum();
        let trailing = w.last().filter(|(t, _)| t.ends_with(' ')).map(|(_, f)| measure(ctx, " ", f).0).unwrap_or(0.0);
        if x > 0.0 && x + full - trailing > width {
            lines.push(Vec::new());
            x = 0.0;
        }
        x += full;
        lines.last_mut().unwrap().extend(w);
    }
    let n = lines.len();
    lines
        .into_iter()
        .enumerate()
        .map(|(i, pieces)| {
            let mut x = 0.0;
            let (mut asc, mut desc) = (0.0f32, 0.0f32);
            let mut placed = Vec::new();
            for (t, f) in pieces {
                let (w, a, d) = measure(ctx, &t, &f);
                asc = asc.max(a);
                desc = desc.max(d);
                placed.push((x, t, f));
                x += w;
            }
            // The line break, in its own format, ends the last line.
            let end = if i + 1 == n { br } else { None };
            if let Some(f) = end.or_else(|| placed.is_empty().then_some(base)) {
                let (_, a, d) = measure(ctx, "", &f);
                asc = asc.max(a);
                desc = desc.max(d);
            }
            Line { pieces: placed, ascent: asc, height: (asc + desc - 1e-3).ceil() }
        })
        .collect()
}

/// The label's height at `width` (`heightForWidth`), margins included.
pub fn height_for_width(ctx: &egui::Context, doc: &Doc, width: f32) -> f32 {
    let (_, h) = layout(ctx, doc, (width - 2.0 * LABEL_MARGIN).max(1.0));
    h.ceil() + 2.0 * LABEL_MARGIN
}

/// Draws the label in `rect`, text in `color` unless a span sets one.
pub fn draw(ui: &Ui, rect: Rect, doc: &Doc, color: Color32) {
    let inner = rect.shrink(LABEL_MARGIN);
    let (lines, h) = layout(ui.ctx(), doc, inner.width().max(1.0));
    // `AlignLeft | AlignVCenter`.
    let top = inner.top() + ((inner.height() - h.ceil()) / 2.0).floor().max(0.0);
    let ppp = ui.ctx().pixels_per_point();
    let painter = ui.painter().with_clip_rect(rect.intersect(ui.clip_rect()));
    for (y, line) in lines {
        let baseline = top + y + line.ascent;
        for (x, text, f) in line.pieces {
            if text.trim().is_empty() {
                continue;
            }
            let font = f.font();
            let c = f.color.unwrap_or(color);
            let galley = ui.ctx().fonts_mut(|fonts| fonts.layout_no_wrap(text.clone(), font.clone(), c));
            let in_galley = galley.rows.first().and_then(|r| r.row.glyphs.first().map(|g| r.pos.y + g.pos.y)).unwrap_or(galley.size().y * 0.8);
            let (_, a, d) = measure(ui.ctx(), "", &f);
            let shift = match f.script {
                -1 => (a + d) / 6.0,
                1 => -(a + d) / 2.0,
                _ => 0.0,
            };
            let at = pos2(((inner.left() + x) * ppp).round() / ppp, ((baseline + shift - in_galley) * ppp).round() / ppp);
            painter.galley(at, galley, c);
        }
    }
}

/// The label's layout spec (`QLabel` with word wrap): as wide as its text
/// on one line, at least its longest word, and as tall as it lays out at
/// the width it gets.
pub fn label_spec(ui: &Ui, id: &'static str, html: &str, base_px: i32) -> WidgetSpec {
    let ctx = ui.ctx().clone();
    let doc = Doc::parse(html, base_px);
    let line_width = |lines: &[(f32, Line)]| {
        lines.iter().map(|(_, l)| l.pieces.last().map(|(x, t, f)| x + measure(&ctx, t, f).0).unwrap_or(0.0)).fold(0.0, f32::max)
    };
    let (one, h1) = layout(&ctx, &doc, f32::INFINITY);
    let natural = line_width(&one);
    let (narrow, _) = layout(&ctx, &doc, 1.0);
    let longest = line_width(&narrow);
    let m = 2 * LABEL_MARGIN as i32;
    let hfw: crate::ui::qlayout::Hfw = Rc::new(move |w: i32| height_for_width(&ctx, &doc, w as f32).round() as i32);
    WidgetSpec::new(id, Size::new(natural.ceil() as i32 + m, h1.ceil() as i32 + m), Policy::Preferred, Policy::Preferred)
        .min_hint(Size::new(longest.ceil() as i32 + m, h1.ceil() as i32 + m))
        .hfw(hfw)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_lines_and_formats() {
        let html = "<div style='margin-bottom:6px;'><span style='font-size:11px; color:#5f7693;'><b>R<sub>p,0</sub></b></span><br><span style='font-size:13px;'><b>1.3741 R<sub>s</sub></b></span></div>";
        let d = Doc::parse(html, 12);
        assert_eq!(d.blocks.len(), 1);
        let b = &d.blocks[0];
        assert_eq!(b.margin_bottom, 6.0);
        assert_eq!(b.lines.len(), 2);
        // The line break is in the label's own 12 px.
        assert_eq!(b.lines[0].1.map(|f| f.px), Some(12));
        assert_eq!(b.lines[0].0[1].text, "p,0");
        assert_eq!(b.lines[0].0[1].f.script, -1);
        assert_eq!(b.lines[1].0[0].f.px, 13);
        assert_eq!(b.lines[1].0[0].text, "1.3741 R");
        let two = Doc::parse(&format!("{html}{html}"), 12);
        assert_eq!(two.blocks.len(), 2);
        let status = Doc::parse("<span style='color:#b26a00;'><b>Analyzer input missing.</b></span><br>Run the usual Analyzer window first.", 12);
        assert_eq!(status.blocks.len(), 1);
        assert_eq!(status.blocks[0].lines.len(), 2);
        assert_eq!(status.blocks[0].lines[0].0[0].f.color, Some(Color32::from_rgb(0xb2, 0x6a, 0x00)));
    }
}
