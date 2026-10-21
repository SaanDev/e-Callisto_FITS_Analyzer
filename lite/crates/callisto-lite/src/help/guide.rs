//! Help → User Guide (`UserGuideDialog`): Home, Back, a find field with
//! Find Next, the guide in a `QTextBrowser`, and Close.
//!
//! The guide is `assets/user_guide.html`, written from the full app's guide
//! by `lite/tools/make_user_guide.py`. It keeps to the rich-text subset the
//! full app's guide uses (headings, paragraphs, lists, tables, bold, code,
//! `<a name>` anchors and `<a href="#…">` links), which is laid out here as
//! `QTextDocument` lays it out with `build_default_stylesheet`'s colours:
//! the default block margins (collapsed between neighbours), a 4 px
//! document margin, lines as tall as their tallest font, list items 40 px
//! in, and tables sized column by column to their content.

use std::collections::HashMap;
use std::sync::Arc;

use egui::{Color32, CursorIcon, FontId, Id, Pos2, Rect, Sense, Ui, pos2, vec2};

use crate::fonts::{Weight, mono_font, qt_metrics, ui_font};
use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;
use crate::ui::qlayout::{BoxLayout, Item, Layout, Size};
use crate::ui::{dialogs, qt};

const HTML: &str = include_str!("../../assets/user_guide.html");

/// `QTextDocument::documentMargin()`.
const DOC_MARGIN: f32 = 4.0;
/// `QTextBlockFormat` indent of a list item (one level).
const LIST_INDENT: f32 = 40.0;
/// The stylesheet's `td`/`th` padding: 4 px above and below, 12 px right.
const CELL_PAD_Y: f32 = 4.0;
const CELL_PAD_RIGHT: f32 = 12.0;
/// `QTextEdit`'s scroll bar single step.
const SCROLL_STEP: f32 = 20.0;

/// The guide's colours (`_LIGHT_COLORS`, `_DARK_COLORS`).
#[derive(Clone, Copy)]
struct Colors {
    text: Color32,
    muted: Color32,
    heading: Color32,
    accent: Color32,
    border: Color32,
    code: Color32,
}

impl Colors {
    fn of(dark: bool) -> Colors {
        let c = |v: u32| Color32::from_rgb((v >> 16) as u8, (v >> 8) as u8, v as u8);
        if dark {
            Colors {
                text: c(0xe8eef8),
                muted: c(0x9db0c9),
                heading: c(0xffffff),
                accent: c(0x4ea3ff),
                border: c(0x314055),
                code: c(0xc9e2ff),
            }
        } else {
            Colors {
                text: c(0x202a36),
                muted: c(0x61758f),
                heading: c(0x0f2338),
                accent: c(0x146fda),
                border: c(0xd3dcea),
                code: c(0x0b4da2),
            }
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Role {
    Text,
    Muted,
    Heading,
}

#[derive(Clone, Debug, PartialEq)]
struct Run {
    text: String,
    bold: bool,
    code: bool,
    link: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Marker {
    Disc,
    Number(usize),
}

#[derive(Clone, Debug)]
struct Block {
    runs: Vec<Run>,
    px: f32,
    bold: bool,
    role: Role,
    margin_top: f32,
    margin_bottom: f32,
    indent: f32,
    marker: Option<Marker>,
    rule: bool,
    anchors: Vec<String>,
}

impl Block {
    fn new(px: f32, role: Role, margins: (f32, f32)) -> Block {
        Block {
            runs: Vec::new(),
            px,
            bold: false,
            role,
            margin_top: margins.0,
            margin_bottom: margins.1,
            indent: 0.0,
            marker: None,
            rule: false,
            anchors: Vec::new(),
        }
    }
}

#[derive(Clone, Debug)]
struct Cell {
    block: Block,
}

#[derive(Clone, Debug)]
enum Node {
    Block(Block),
    Table(Vec<Vec<Cell>>),
}

// ------------------------------------------------------------------ parsing

fn decode_entities(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(i) = rest.find('&') {
        out.push_str(&rest[..i]);
        let tail = &rest[i..];
        let Some(end) = tail.find(';').filter(|&e| e <= 10) else {
            out.push('&');
            rest = &tail[1..];
            continue;
        };
        let name = &tail[1..end];
        let ch = match name {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            "nbsp" => Some('\u{a0}'),
            _ if name.starts_with("#x") || name.starts_with("#X") => {
                u32::from_str_radix(&name[2..], 16).ok().and_then(char::from_u32)
            }
            _ if name.starts_with('#') => name[1..].parse().ok().and_then(char::from_u32),
            _ => None,
        };
        match ch {
            Some(c) => {
                out.push(c);
                rest = &tail[end + 1..];
            }
            None => {
                out.push('&');
                rest = &tail[1..];
            }
        }
    }
    out.push_str(rest);
    out
}

fn attr(tag: &str, name: &str) -> Option<String> {
    let lower = tag.to_ascii_lowercase();
    let key = format!("{name}=");
    let i = lower.find(&key)? + key.len();
    let rest = &tag[i..];
    let (quote, rest) = match rest.chars().next()? {
        q @ ('"' | '\'') => (q, &rest[1..]),
        _ => (' ', rest),
    };
    let end = rest.find(quote).unwrap_or(rest.len());
    Some(decode_entities(&rest[..end]))
}

/// The document's blocks, as `QTextHtmlImporter` makes them.
fn parse(html: &str) -> Vec<Node> {
    struct List {
        ordered: bool,
        count: usize,
        first: Option<usize>,
    }
    let mut nodes: Vec<Node> = Vec::new();
    let mut cur: Option<Block> = None;
    let mut table: Option<Vec<Vec<Cell>>> = None;
    let mut lists: Vec<List> = Vec::new();
    let mut pending_anchors: Vec<String> = Vec::new();
    let (mut bold, mut code) = (0i32, 0i32);
    let mut link: Option<String> = None;
    let mut class: Option<String> = None;

    // Closes the open block into the table cell or the document.
    fn flush(cur: &mut Option<Block>, table: &mut Option<Vec<Vec<Cell>>>, nodes: &mut Vec<Node>) {
        let Some(b) = cur.take() else { return };
        match table {
            Some(rows) => {
                if rows.is_empty() {
                    rows.push(Vec::new());
                }
                rows.last_mut().unwrap().push(Cell { block: b });
            }
            None => nodes.push(Node::Block(b)),
        }
    }

    let open = |b: Block, cur: &mut Option<Block>, pending: &mut Vec<String>| {
        let mut b = b;
        b.anchors.append(pending);
        *cur = Some(b);
    };

    let mut rest = html;
    while !rest.is_empty() {
        let (text, tag) = match rest.find('<') {
            Some(i) => {
                let end = rest[i..].find('>').map(|j| i + j).unwrap_or(rest.len() - 1);
                let t = (&rest[..i], Some(&rest[i + 1..end]));
                rest = &rest[(end + 1).min(rest.len())..];
                t
            }
            None => {
                let t = (rest, None);
                rest = "";
                t
            }
        };
        if !text.is_empty() {
            let decoded = decode_entities(text);
            if cur.is_none() && !decoded.trim_matches(|c: char| c.is_ascii_whitespace()).is_empty() {
                // Text outside a block starts an anonymous one.
                open(Block::new(12.0, Role::Text, (0.0, 0.0)), &mut cur, &mut pending_anchors);
            }
            if let Some(b) = cur.as_mut() {
                b.runs.push(Run { text: decoded, bold: bold > 0, code: code > 0, link: link.clone() });
            }
        }
        let Some(tag) = tag else { continue };
        let closing = tag.starts_with('/');
        let name: String = tag
            .trim_start_matches('/')
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric())
            .collect::<String>()
            .to_ascii_lowercase();
        match (name.as_str(), closing) {
            ("b" | "strong", false) => bold += 1,
            ("b" | "strong", true) => bold -= 1,
            ("code" | "kbd" | "tt", false) => code += 1,
            ("code" | "kbd" | "tt", true) => code -= 1,
            ("a", false) => {
                if let Some(n) = attr(tag, "name") {
                    match cur.as_mut() {
                        Some(b) => b.anchors.push(n),
                        None => pending_anchors.push(n),
                    }
                }
                link = attr(tag, "href");
            }
            ("a", true) => link = None,
            ("br", _) => {
                if cur.is_none() {
                    open(Block::new(12.0, Role::Text, (0.0, 0.0)), &mut cur, &mut pending_anchors);
                }
                if let Some(b) = cur.as_mut() {
                    b.runs.push(Run { text: "\u{2028}".into(), bold: false, code: false, link: None });
                }
            }
            ("p", false) => {
                flush(&mut cur, &mut table, &mut nodes);
                class = attr(tag, "class");
                let (px, role) = match class.as_deref() {
                    Some("lead") => (11.0 * 96.0 / 72.0, Role::Muted),
                    Some("muted" | "note") => (12.0, Role::Muted),
                    _ => (12.0, Role::Text),
                };
                open(Block::new(px, role, (12.0, 12.0)), &mut cur, &mut pending_anchors);
            }
            ("h1" | "h2" | "h3" | "h4", false) => {
                flush(&mut cur, &mut table, &mut nodes);
                let (px, margins) = match name.as_str() {
                    "h1" => (24.0, (18.0, 12.0)),
                    "h2" => (18.0, (16.0, 12.0)),
                    "h3" => (14.4, (14.0, 12.0)),
                    _ => (12.0, (12.0, 12.0)),
                };
                let mut b = Block::new(px, Role::Heading, margins);
                b.bold = true;
                open(b, &mut cur, &mut pending_anchors);
            }
            ("p" | "h1" | "h2" | "h3" | "h4" | "li", true) => {
                flush(&mut cur, &mut table, &mut nodes);
                class = None;
            }
            ("ul" | "ol", false) => {
                flush(&mut cur, &mut table, &mut nodes);
                lists.push(List { ordered: name == "ol", count: 0, first: None });
            }
            ("ul" | "ol", true) => {
                flush(&mut cur, &mut table, &mut nodes);
                if lists.pop().is_some_and(|l| l.first.is_some())
                    && let Some(Node::Block(b)) = nodes.last_mut()
                {
                    b.margin_bottom = 12.0;
                }
            }
            ("li", false) => {
                flush(&mut cur, &mut table, &mut nodes);
                let depth = lists.len().max(1);
                let mut b = Block::new(12.0, Role::Text, (0.0, 0.0));
                b.indent = LIST_INDENT * depth as f32;
                if let Some(l) = lists.last_mut() {
                    l.count += 1;
                    b.marker = Some(if l.ordered { Marker::Number(l.count) } else { Marker::Disc });
                    if l.first.is_none() {
                        l.first = Some(nodes.len());
                        b.margin_top = 12.0;
                    }
                }
                open(b, &mut cur, &mut pending_anchors);
            }
            ("hr", _) => {
                flush(&mut cur, &mut table, &mut nodes);
                let mut b = Block::new(12.0, Role::Text, (0.0, 0.0));
                b.rule = true;
                b.anchors.append(&mut pending_anchors);
                nodes.push(Node::Block(b));
            }
            ("table", false) => {
                flush(&mut cur, &mut table, &mut nodes);
                table = Some(Vec::new());
            }
            ("table", true) => {
                flush(&mut cur, &mut table, &mut nodes);
                if let Some(rows) = table.take() {
                    nodes.push(Node::Table(rows.into_iter().filter(|r| !r.is_empty()).collect()));
                }
            }
            ("tr", false) => {
                flush(&mut cur, &mut table, &mut nodes);
                if let Some(rows) = table.as_mut() {
                    rows.push(Vec::new());
                }
            }
            ("td" | "th", false) => {
                flush(&mut cur, &mut table, &mut nodes);
                let header = name == "th";
                let mut b = Block::new(12.0, if header { Role::Heading } else { Role::Text }, (0.0, 0.0));
                b.bold = header;
                open(b, &mut cur, &mut pending_anchors);
            }
            ("td" | "th" | "tr", true) => flush(&mut cur, &mut table, &mut nodes),
            ("div", _) => flush(&mut cur, &mut table, &mut nodes),
            _ => {}
        }
    }
    flush(&mut cur, &mut table, &mut nodes);
    let _ = class;
    for node in &mut nodes {
        match node {
            Node::Block(b) => collapse_spaces(b),
            Node::Table(rows) => rows.iter_mut().flatten().for_each(|c| collapse_spaces(&mut c.block)),
        }
    }
    nodes
}

/// HTML white space: runs of spaces (and line ends) become one space,
/// none at the start of a block or a line, none at its end.
fn collapse_spaces(b: &mut Block) {
    let mut after_space = true;
    for r in &mut b.runs {
        let mut t = String::with_capacity(r.text.len());
        for c in r.text.chars() {
            if c == '\u{2028}' {
                while t.ends_with(' ') {
                    t.pop();
                }
                t.push(c);
                after_space = true;
            } else if c == ' ' || c == '\n' || c == '\t' || c == '\r' {
                if !after_space {
                    t.push(' ');
                }
                after_space = true;
            } else {
                t.push(c);
                after_space = false;
            }
        }
        r.text = t;
    }
    // Spaces left before a line break or the end, across runs.
    let mut i = b.runs.len();
    let mut trim = true;
    while i > 0 {
        i -= 1;
        let r = &mut b.runs[i];
        if r.text.starts_with('\u{2028}') {
            trim = true;
            continue;
        }
        if trim {
            let kept = r.text.trim_end_matches(' ').len();
            r.text.truncate(kept);
            if !r.text.is_empty() {
                trim = false;
            }
        }
    }
    b.runs.retain(|r| !r.text.is_empty());
}

// ------------------------------------------------------------------- layout

/// One piece of a line in one font.
#[derive(Clone, Debug)]
struct Frag {
    x: f32,
    width: f32,
    text: String,
    font: FontId,
    color: Color32,
    link: Option<String>,
    /// Index of its first character in [`Laid::text`].
    start: usize,
}

#[derive(Clone, Debug)]
struct Line {
    top: f32,
    height: f32,
    ascent: f32,
    left: f32,
    frags: Vec<Frag>,
    /// The characters of [`Laid::text`] it shows.
    start: usize,
    end: usize,
}

#[derive(Clone, Debug)]
enum Deco {
    Disc { center: Pos2, radius: f32, color: Color32 },
    Number { right: f32, baseline: f32, text: String, font: FontId, color: Color32 },
    Rule { left: f32, right: f32, y: f32, color: Color32 },
    Border { rect: Rect, color: Color32 },
}

/// The laid-out document, in document coordinates (origin at the
/// viewport's top left before scrolling).
#[derive(Clone, Debug, Default)]
struct Laid {
    lines: Vec<Line>,
    decos: Vec<Deco>,
    height: f32,
    anchors: HashMap<String, f32>,
    /// The plain text: each block's characters, then a block separator.
    text: Vec<char>,
    /// Each block's rectangle and line count, as `blockBoundingRect` gives them.
    blocks: Vec<BlockBox>,
}

/// A block's place, for comparing with the full app's layout.
#[derive(Clone, Debug)]
pub struct BlockBox {
    pub text: String,
    pub rect: Rect,
    pub lines: usize,
}

/// A piece of a run between break opportunities.
struct Piece {
    text: String,
    font: FontId,
    color: Color32,
    link: Option<String>,
    width: f32,
    /// Width without trailing spaces.
    ink: f32,
    /// A line may break after it.
    breakable: bool,
    /// It ends a line (`<br>`).
    hard: bool,
}

fn text_width(ctx: &egui::Context, text: &str, font: &FontId) -> f32 {
    if text.is_empty() {
        return 0.0;
    }
    ctx.fonts_mut(|f| f.layout_no_wrap(text.to_owned(), font.clone(), Color32::WHITE).size().x)
}

fn metrics(ctx: &egui::Context, font: &FontId) -> (f32, f32) {
    if let Some(m) = qt_metrics(font) {
        return (m.ascent, m.descent);
    }
    let g = ctx.fonts_mut(|f| f.layout_no_wrap("x".into(), font.clone(), Color32::WHITE));
    let (h, a) = g
        .rows
        .first()
        .and_then(|r| r.row.glyphs.first())
        .map(|g| (g.font_height, g.font_ascent))
        .unwrap_or((font.size * 1.3, font.size));
    (a, h - a)
}

/// Where a line may break after `chars[i]` (UAX #14 as far as the guide
/// needs it: after spaces, after hyphens and slashes not followed by a
/// digit, either side of an em dash).
pub(crate) fn break_after(chars: &[char], i: usize) -> bool {
    let c = chars[i];
    let next = chars.get(i + 1).copied();
    match c {
        ' ' => next != Some(' '),
        '-' | '/' | '\u{2013}' => {
            let prev_alnum = i > 0 && chars[i - 1].is_alphanumeric();
            prev_alnum && next.is_some_and(|n| n.is_alphabetic())
        }
        '\u{2014}' => true,
        _ => next == Some('\u{2014}'),
    }
}

fn pieces(ctx: &egui::Context, b: &Block, colors: &Colors) -> Vec<Piece> {
    let base = match b.role {
        Role::Text => colors.text,
        Role::Muted => colors.muted,
        Role::Heading => colors.heading,
    };
    let mut out = Vec::new();
    for r in &b.runs {
        let font = if r.code {
            mono_font(b.px)
        } else {
            ui_font(b.px, if r.bold || b.bold { Weight::Bold } else { Weight::Regular })
        };
        let color = if r.link.is_some() {
            colors.accent
        } else if r.code {
            colors.code
        } else {
            base
        };
        let chars: Vec<char> = r.text.chars().collect();
        let mut start = 0;
        for i in 0..chars.len() {
            let hard = chars[i] == '\u{2028}';
            let soft = !hard && break_after(&chars, i);
            if hard || soft || i + 1 == chars.len() {
                let end = if hard { i } else { i + 1 };
                let text: String = chars[start..end].iter().collect();
                if !text.is_empty() {
                    let width = text_width(ctx, &text, &font);
                    let ink = text_width(ctx, text.trim_end_matches(' '), &font);
                    out.push(Piece {
                        text,
                        font: font.clone(),
                        color,
                        link: r.link.clone(),
                        width,
                        ink,
                        breakable: soft,
                        hard: false,
                    });
                }
                if hard {
                    out.push(Piece {
                        text: String::new(),
                        font: font.clone(),
                        color,
                        link: None,
                        width: 0.0,
                        ink: 0.0,
                        breakable: true,
                        hard: true,
                    });
                }
                start = i + 1;
            }
        }
    }
    out
}

/// The widest unbreakable stretch and the widest unwrapped line.
fn min_max_width(ps: &[Piece]) -> (f32, f32) {
    let (mut min, mut max) = (0.0f32, 0.0f32);
    let (mut word, mut line) = (0.0f32, 0.0f32);
    for p in ps {
        if p.hard {
            max = max.max(line);
            min = min.max(word);
            line = 0.0;
            word = 0.0;
            continue;
        }
        word += p.ink;
        min = min.max(word);
        word += p.width - p.ink;
        line += p.width;
        max = max.max(line - (p.width - p.ink));
        if p.breakable {
            word = 0.0;
        }
    }
    (min, max)
}

struct Layouter<'a> {
    ctx: &'a egui::Context,
    colors: Colors,
    laid: Laid,
}

impl Layouter<'_> {
    /// Lays out `b`'s lines at `left` and `top`, `width` wide. Returns its height.
    fn block(&mut self, b: &Block, left: f32, top: f32, width: f32) -> f32 {
        for a in &b.anchors {
            self.laid.anchors.entry(a.clone()).or_insert(top);
        }
        let base_font = ui_font(b.px, if b.bold { Weight::Bold } else { Weight::Regular });
        let (base_asc, base_desc) = metrics(self.ctx, &base_font);
        let text_left = left + b.indent;
        let avail = width - b.indent;
        let start_char = self.laid.text.len();
        if b.rule {
            let h = (base_asc + base_desc).ceil();
            self.laid.decos.push(Deco::Rule { left, right: left + width, y: top + h / 2.0, color: self.colors.text });
            self.laid.lines.push(Line {
                top,
                height: h,
                ascent: base_asc,
                left,
                frags: Vec::new(),
                start: start_char,
                end: start_char,
            });
            self.laid.text.push('\n');
            self.laid.blocks.push(BlockBox {
                text: String::new(),
                rect: Rect::from_min_size(pos2(left, top), vec2(width, h)),
                lines: 1,
            });
            return h;
        }
        let ps = pieces(self.ctx, b, &self.colors);
        // Greedy line filling.
        let mut lines: Vec<Vec<usize>> = vec![Vec::new()];
        let mut x = 0.0f32;
        let mut pending: Vec<usize> = Vec::new();
        let mut pending_w = 0.0f32;
        let mut pending_ink = 0.0f32;
        for (i, p) in ps.iter().enumerate() {
            if p.hard {
                lines.last_mut().unwrap().append(&mut pending);
                lines.last_mut().unwrap().push(i);
                lines.push(Vec::new());
                x = 0.0;
                pending_w = 0.0;
                pending_ink = 0.0;
                continue;
            }
            pending_ink = pending_w + p.ink;
            pending_w += p.width;
            pending.push(i);
            if !p.breakable && i + 1 < ps.len() && !ps[i + 1].hard {
                continue;
            }
            // `pending` is a word: it goes on this line if it fits.
            let line = lines.last_mut().unwrap();
            if !line.is_empty() && x + pending_ink > avail {
                lines.push(Vec::new());
                x = 0.0;
            }
            lines.last_mut().unwrap().append(&mut pending);
            x += pending_w;
            pending_w = 0.0;
            pending_ink = 0.0;
        }
        lines.last_mut().unwrap().append(&mut pending);
        let _ = pending_ink;

        let mut y = top;
        let mut ch = start_char;
        for (li, idx) in lines.iter().enumerate() {
            let hard_end = idx.last().is_some_and(|&i| ps[i].hard);
            let (mut asc, mut desc) = (base_asc, base_desc);
            let visible: Vec<&Piece> = idx.iter().map(|&i| &ps[i]).filter(|p| !p.hard).collect();
            if !visible.is_empty() {
                asc = 0.0;
                desc = 0.0;
                for p in &visible {
                    let (a, d) = metrics(self.ctx, &p.font);
                    asc = asc.max(a);
                    desc = desc.max(d);
                }
            }
            let height = (asc + desc).ceil();
            let mut frags = Vec::new();
            let mut fx = text_left;
            let line_start = ch;
            for p in visible {
                let n = p.text.chars().count();
                frags.push(Frag {
                    x: fx,
                    width: p.width,
                    text: p.text.clone(),
                    font: p.font.clone(),
                    color: p.color,
                    link: p.link.clone(),
                    start: ch,
                });
                self.laid.text.extend(p.text.chars());
                ch += n;
                fx += p.width;
            }
            if li == 0
                && let Some(m) = b.marker
            {
                let color = match b.role {
                    Role::Muted => self.colors.muted,
                    Role::Heading => self.colors.heading,
                    Role::Text => self.colors.text,
                };
                let font = ui_font(b.px, Weight::Regular);
                let space = text_width(self.ctx, " ", &font);
                let (a, d) = metrics(self.ctx, &font);
                match m {
                    Marker::Disc => {
                        let size = (a + d) / 3.0;
                        let center = pos2(text_left - space - size / 2.0, y + (a + d) / 2.0);
                        self.laid.decos.push(Deco::Disc { center, radius: size / 2.0, color });
                    }
                    Marker::Number(n) => {
                        self.laid.decos.push(Deco::Number {
                            right: text_left - space,
                            baseline: y + a,
                            text: format!("{n}."),
                            font,
                            color,
                        });
                    }
                }
            }
            let end = ch;
            if hard_end {
                self.laid.text.push('\u{2028}');
                ch += 1;
            }
            self.laid.lines.push(Line { top: y, height, ascent: asc, left: text_left, frags, start: line_start, end });
            y += height;
        }
        self.laid.text.push('\n');
        let text: String = self.laid.text[start_char..self.laid.text.len() - 1].iter().collect();
        self.laid.blocks.push(BlockBox {
            text,
            rect: Rect::from_min_size(pos2(left, top), vec2(width, y - top)),
            lines: lines.len(),
        });
        y - top
    }

    fn table(&mut self, rows: &[Vec<Cell>], left: f32, top: f32, width: f32) -> f32 {
        let cols = rows.iter().map(Vec::len).max().unwrap_or(0);
        if cols == 0 {
            return 0.0;
        }
        let pad = CELL_PAD_RIGHT;
        let mut min_w = vec![0.0f32; cols];
        let mut max_w = vec![0.0f32; cols];
        for row in rows {
            for (c, cell) in row.iter().enumerate() {
                let ps = pieces(self.ctx, &cell.block, &self.colors);
                let (mn, mx) = min_max_width(&ps);
                min_w[c] = min_w[c].max(mn + pad);
                max_w[c] = max_w[c].max(mx + pad);
            }
        }
        // QTextDocumentLayoutPrivate::layoutTable: each column starts at its
        // minimum; the rest is shared out, no column past its maximum.
        let mut widths = min_w.clone();
        let mut remaining = width - widths.iter().sum::<f32>();
        let mut last = remaining;
        while remaining > 0.0 {
            for c in 0..cols {
                let left_cols = (cols - c) as f32;
                let w = (max_w[c] - widths[c]).min(remaining / left_cols).max(0.0);
                widths[c] += w;
                remaining -= w;
            }
            if remaining == last {
                break;
            }
            last = remaining;
        }
        let mut y = top;
        for (r, row) in rows.iter().enumerate() {
            let content_top = y + CELL_PAD_Y + if r == 1 { 0.5 } else { 0.0 };
            let mut x = left;
            let mut row_h = 0.0f32;
            for (c, cell) in row.iter().enumerate() {
                let h = self.block(&cell.block, x, content_top, widths[c] - pad);
                row_h = row_h.max(h);
                x += widths[c];
            }
            let border_y = content_top + row_h + CELL_PAD_Y - 0.5;
            let right = left + widths.iter().sum::<f32>();
            self.laid.decos.push(Deco::Border {
                rect: Rect::from_min_max(pos2(left, border_y), pos2(right, border_y + 1.0)),
                color: self.colors.border,
            });
            y = content_top + row_h + CELL_PAD_Y + 1.0;
        }
        y + 0.5 - top
    }
}

fn layout(ctx: &egui::Context, nodes: &[Node], width: f32, colors: Colors) -> Laid {
    let mut l = Layouter { ctx, colors, laid: Laid::default() };
    let inner = width - 2.0 * DOC_MARGIN;
    let mut y = DOC_MARGIN;
    // The bottom margin still owed by the block above.
    let mut owed = 0.0f32;
    for (i, node) in nodes.iter().enumerate() {
        match node {
            Node::Block(b) => {
                if i > 0 {
                    y += owed.max(b.margin_top);
                }
                y += l.block(b, DOC_MARGIN, y, inner);
                owed = b.margin_bottom;
            }
            Node::Table(rows) => {
                y += l.table(rows, DOC_MARGIN, y, inner);
                owed = 0.0;
            }
        }
    }
    // The last block's bottom margin is not part of the document.
    let _ = owed;
    l.laid.height = y + DOC_MARGIN;
    l.laid
}

// ------------------------------------------------------------------- window

/// A position in the text: a character index into [`Laid::text`].
type Pos = usize;

pub struct UserGuide {
    nodes: Vec<Node>,
    laid: Option<(f32, bool, Arc<Laid>)>,
    scroll: f32,
    history: Vec<f32>,
    find: String,
    /// The selection (anchor, cursor), as a find or a drag leaves it.
    selection: Option<(Pos, Pos)>,
    dragging: bool,
    not_found: Option<Notice>,
    /// Scroll to an anchor once laid out.
    goto: Option<String>,
    /// Scroll the selection into view once laid out.
    reveal: bool,
    /// Find Next, pressed from outside ([`Self::find_text`]).
    pending_find: bool,
}

impl Default for UserGuide {
    fn default() -> Self {
        Self::new()
    }
}

impl UserGuide {
    pub fn new() -> Self {
        let html = HTML.replace("{{VERSION}}", crate::APP_VERSION);
        UserGuide {
            nodes: parse(&html),
            laid: None,
            scroll: 0.0,
            history: Vec::new(),
            find: String::new(),
            selection: None,
            dragging: false,
            not_found: None,
            goto: None,
            reveal: false,
            pending_find: false,
        }
    }

    pub fn title() -> String {
        format!("{} User Guide", crate::APP_NAME)
    }

    fn laid(&mut self, ctx: &egui::Context, width: f32, dark: bool) -> Arc<Laid> {
        if let Some((w, d, l)) = &self.laid
            && *w == width
            && *d == dark
        {
            return l.clone();
        }
        let l = Arc::new(layout(ctx, &self.nodes, width, Colors::of(dark)));
        self.laid = Some((width, dark, l.clone()));
        l
    }

    fn layout_spec(ui: &Ui) -> Layout {
        let toolbar = BoxLayout::h([0; 4], 8)
            .widget(qt::button_spec(ui, "home", "Home", false))
            .widget(qt::button_spec(ui, "back", "Back", false))
            .stretch(1)
            .add_stretched(Item::Widget(qt::line_edit_spec("find")), 1)
            .widget(qt::button_spec(ui, "find_next", "Find Next", false));
        let buttons = BoxLayout::h([0; 4], 8).stretch(1).widget(qt::button_spec(ui, "close", "Close", false));
        BoxLayout::v([12; 4], 8)
            .add(toolbar.into_item())
            .add_stretched(Item::Widget(qt::view_spec("browser")), 1)
            .add(buttons.into_item())
            .into()
    }

    /// `scrollToAnchor`: the anchor's block at the top.
    fn scroll_to_anchor(&mut self, laid: &Laid, anchor: &str, view_h: f32) {
        if let Some(&y) = laid.anchors.get(anchor) {
            self.scroll = y.round().clamp(0.0, (laid.height - view_h).max(0.0));
        }
    }

    /// `QTextBrowser::find`: the next match after the selection, case
    /// insensitive, within a block. Returns whether one was found.
    fn find_from(&mut self, laid: &Laid, needle: &str, from: Pos) -> bool {
        let needle: Vec<char> = needle.chars().flat_map(char::to_lowercase).collect();
        if needle.is_empty() {
            return false;
        }
        let hay: Vec<char> = laid.text.iter().map(|c| c.to_lowercase().next().unwrap_or(*c)).collect();
        let n = needle.len();
        let mut i = from;
        while i + n <= hay.len() {
            if hay[i..i + n] == needle[..] && !hay[i..i + n].contains(&'\n') {
                self.selection = Some((i, i + n));
                self.reveal = true;
                return true;
            }
            i += 1;
        }
        false
    }

    fn find_next(&mut self, laid: &Laid) {
        let text = self.find.trim().to_owned();
        if text.is_empty() {
            return;
        }
        let from = self.selection.map(|(a, b)| a.max(b)).unwrap_or(0);
        if !self.find_from(laid, &text, from) && !self.find_from(laid, &text, 0) {
            self.selection = None;
            self.not_found = Some(Notice {
                kind: NoticeKind::Information,
                title: "Find".into(),
                text: format!("\"{text}\" was not found."),
            });
        }
    }

    /// `ensureCursorVisible`: the cursor's line scrolled just into view.
    fn reveal_selection(&mut self, laid: &Laid, view_h: f32) {
        let Some((_, cursor)) = self.selection else { return };
        let Some(line) = laid.lines.iter().find(|l| cursor >= l.start && cursor <= l.end && !l.frags.is_empty()) else {
            return;
        };
        let (top, bottom) = (line.top, line.top + line.height);
        if bottom > self.scroll + view_h {
            self.scroll = bottom - view_h;
        }
        if top < self.scroll {
            self.scroll = top;
        }
        self.scroll = self.scroll.round().clamp(0.0, (laid.height - view_h).max(0.0));
    }

    fn hit(&self, ctx: &egui::Context, laid: &Laid, p: Pos2) -> Option<(Pos, Option<String>)> {
        let line = laid.lines.iter().filter(|l| p.y >= l.top && p.y < l.top + l.height && !l.frags.is_empty()).min_by(
            |a, b| {
                let da = if p.x < a.left {
                    a.left - p.x
                } else {
                    (p.x - a.frags.last().map(|f| f.x + f.width).unwrap_or(a.left)).max(0.0)
                };
                let db = if p.x < b.left {
                    b.left - p.x
                } else {
                    (p.x - b.frags.last().map(|f| f.x + f.width).unwrap_or(b.left)).max(0.0)
                };
                da.total_cmp(&db)
            },
        )?;
        for f in &line.frags {
            if p.x < f.x + f.width {
                let chars: Vec<char> = f.text.chars().collect();
                let mut x = f.x;
                for (i, c) in chars.iter().enumerate() {
                    let w = text_width(ctx, &c.to_string(), &f.font);
                    if p.x < x + w / 2.0 {
                        return Some((f.start + i, f.link.clone().filter(|_| p.x >= f.x)));
                    }
                    x += w;
                }
                return Some((f.start + chars.len(), f.link.clone()));
            }
        }
        Some((line.end, None))
    }

    fn selected_text(&self, laid: &Laid) -> Option<String> {
        let (a, b) = self.selection?;
        let (a, b) = (a.min(b), a.max(b));
        (a < b).then(|| {
            laid.text[a..b.min(laid.text.len())].iter().map(|&c| if c == '\u{2028}' { '\n' } else { c }).collect()
        })
    }

    /// Draws the window; returns false once it is closed.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> bool {
        let builder = egui::ViewportBuilder::default()
            .with_title(Self::title())
            .with_inner_size(vec2(880.0, 720.0))
            .with_min_inner_size(vec2(369.0, 324.0));
        let bg = p.page_bg;
        let mut open = true;
        ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("user_guide"), builder, |ctx, _| {
            if ctx.input(|i| i.viewport().close_requested()) {
                open = false;
            }
            egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| {
                if !self.frame(ui, p) {
                    open = false;
                }
            });
        });
        if let Some(n) = &self.not_found
            && dialogs::message_box(ctx, n, p)
        {
            self.not_found = None;
        }
        open
    }

    /// The window's contents; false once Close (or Escape) closes it.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> bool {
        qt::with_app_style(|| self.contents(ui, p))
    }

    /// Types `text` into the find field and presses Find Next.
    pub fn find_text(&mut self, text: &str) {
        self.find = text.to_owned();
        self.pending_find = true;
    }

    /// The find box, while one is up.
    pub fn not_found(&self) -> Option<&Notice> {
        self.not_found.as_ref()
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> bool {
        let r = ui.max_rect();
        let g = Self::layout_spec(ui).layout_window(Size::new(r.width().round() as i32, r.height().round() as i32));
        let rect = |id: &str| g.rect(id).to_egui(r.min);
        let browser = rect("browser");
        // The frame's 9 × 7 px insets, the 10 px scroll bar inside them.
        let inner = Rect::from_min_max(browser.min + vec2(9.0, 7.0), browser.max - vec2(9.0, 7.0));
        let full_w = inner.width();
        let mut laid = self.laid(ui.ctx(), full_w, p.dark);
        let mut bar = false;
        if laid.height > inner.height() {
            bar = true;
            laid = self.laid(ui.ctx(), full_w - 10.0, p.dark);
        }
        let view = Rect::from_min_size(inner.min, vec2(if bar { full_w - 10.0 } else { full_w }, inner.height()));
        let view_h = view.height();
        let max_scroll = (laid.height - view_h).max(0.0);

        if let Some(a) = self.goto.take() {
            self.scroll_to_anchor(&laid, &a, view_h);
        }

        // Buttons.
        if qt::button(ui, rect("home"), Id::new("guide_home"), "Home", false, true, p).clicked() {
            self.scroll_to_anchor(&laid, "top", view_h);
        }
        if qt::button(ui, rect("back"), Id::new("guide_back"), "Back", false, true, p).clicked()
            && let Some(y) = self.history.pop()
        {
            self.scroll = y.clamp(0.0, max_scroll);
        }
        let find_rect = rect("find");
        let find_id = Id::new("guide_find");
        let had_focus = ui.memory(|m| m.has_focus(find_id));
        qt::line_edit(ui, find_rect, find_id, &mut self.find, "Find in guide...", true, p);
        // `returnPressed`; the field keeps the focus.
        let enter = had_focus && ui.input(|i| i.key_pressed(egui::Key::Enter));
        if enter {
            ui.memory_mut(|m| m.request_focus(find_id));
        }
        if !self.find.is_empty() && clear_button(ui, find_rect, p) {
            self.find.clear();
        }
        if qt::button(ui, rect("find_next"), Id::new("guide_find_next"), "Find Next", false, true, p).clicked()
            || enter
            || std::mem::take(&mut self.pending_find)
        {
            self.find_next(&laid);
        }
        let close = qt::button(ui, rect("close"), Id::new("guide_close"), "Close", false, true, p).clicked();

        // The browser frame.
        qt::qt_box(ui, browser, 10.0, p.input_bg, p.border);
        let resp = ui.interact(view, Id::new("guide_view"), Sense::click_and_drag());
        qt::wheel_scroll(ui, view, &mut self.scroll, max_scroll);
        if resp.has_focus() || resp.hovered() {
            let (up, down, pg_up, pg_down, home, end) = ui.input(|i| {
                (
                    i.key_pressed(egui::Key::ArrowUp),
                    i.key_pressed(egui::Key::ArrowDown),
                    i.key_pressed(egui::Key::PageUp),
                    i.key_pressed(egui::Key::PageDown),
                    i.modifiers.command && i.key_pressed(egui::Key::Home),
                    i.modifiers.command && i.key_pressed(egui::Key::End),
                )
            });
            if resp.has_focus() {
                let mut s = self.scroll;
                if up {
                    s -= SCROLL_STEP;
                }
                if down {
                    s += SCROLL_STEP;
                }
                if pg_up {
                    s -= view_h;
                }
                if pg_down {
                    s += view_h;
                }
                if home {
                    s = 0.0;
                }
                if end {
                    s = max_scroll;
                }
                self.scroll = s.clamp(0.0, max_scroll);
            }
        }
        if std::mem::take(&mut self.reveal) {
            self.reveal_selection(&laid, view_h);
        }
        if bar {
            let bar_rect = Rect::from_min_max(pos2(view.right(), inner.top()), pos2(inner.right(), inner.bottom()));
            qt::scroll_bar(ui, bar_rect, Id::new("guide_vbar"), true, &mut self.scroll, laid.height, view_h, p);
        }
        self.scroll = self.scroll.clamp(0.0, max_scroll);

        // Pointer: links and selection.
        let origin = pos2(view.left(), view.top() - self.scroll);
        let to_doc = |q: Pos2| pos2(q.x - origin.x, q.y - origin.y);
        let hover = resp.hover_pos().and_then(|q| self.hit(ui.ctx(), &laid, to_doc(q)));
        if hover.as_ref().is_some_and(|(_, l)| l.is_some()) {
            ui.ctx().set_cursor_icon(CursorIcon::PointingHand);
        } else if resp.hovered() {
            ui.ctx().set_cursor_icon(CursorIcon::Text);
        }
        if resp.drag_started()
            && let Some(q) = resp.interact_pointer_pos()
            && let Some((at, _)) = self.hit(ui.ctx(), &laid, to_doc(q))
        {
            self.selection = Some((at, at));
            self.dragging = true;
        }
        if self.dragging
            && resp.dragged()
            && let Some(q) = resp.interact_pointer_pos()
        {
            if let Some((at, _)) = self.hit(ui.ctx(), &laid, to_doc(q))
                && let Some(sel) = self.selection.as_mut()
            {
                sel.1 = at;
            }
            // Dragging past the edge scrolls.
            if q.y < view.top() {
                self.scroll = (self.scroll - SCROLL_STEP).max(0.0);
            } else if q.y > view.bottom() {
                self.scroll = (self.scroll + SCROLL_STEP).min(max_scroll);
            }
        }
        if resp.drag_stopped() {
            self.dragging = false;
        }
        if resp.clicked() {
            resp.request_focus();
            match resp.interact_pointer_pos().and_then(|q| self.hit(ui.ctx(), &laid, to_doc(q))) {
                Some((_, Some(link))) => {
                    self.selection = None;
                    if let Some(anchor) = link.strip_prefix('#') {
                        self.history.push(self.scroll);
                        self.scroll_to_anchor(&laid, anchor, view_h);
                    } else {
                        ui.ctx().open_url(egui::OpenUrl::new_tab(link));
                    }
                }
                Some((at, None)) => self.selection = Some((at, at)),
                None => self.selection = None,
            }
        }
        if resp.has_focus() && ui.input(|i| i.modifiers.command && i.key_pressed(egui::Key::A)) {
            self.selection = Some((0, laid.text.len().saturating_sub(1)));
        }
        if let Some(text) = self.selected_text(&laid)
            && ui.input(|i| i.events.iter().any(|e| matches!(e, egui::Event::Copy)))
        {
            ui.ctx().copy_text(text);
        }

        let origin = pos2(view.left(), view.top() - self.scroll);
        paint(ui, &laid, origin, view, self.selection, p);
        let escape = ui.input(|i| i.key_pressed(egui::Key::Escape)) && self.not_found.is_none();
        !(close || escape)
    }

    /// The blocks as laid out `width` wide (the viewport's width).
    pub fn block_boxes(&mut self, ctx: &egui::Context, width: f32, dark: bool) -> Vec<BlockBox> {
        self.laid(ctx, width, dark).blocks.clone()
    }

    /// The document's height at `width`.
    pub fn document_height(&mut self, ctx: &egui::Context, width: f32) -> f32 {
        self.laid(ctx, width, false).height
    }

    /// Scroll to `anchor` (`show_anchor`).
    pub fn show_anchor(&mut self, anchor: &str) {
        self.goto = Some(anchor.to_owned());
    }
}

/// `QLineEdit`'s clear button: a filled circle with a cross, at the right.
fn clear_button(ui: &Ui, field: Rect, p: &Palette) -> bool {
    let center = pos2(field.right() - 15.0, field.center().y);
    let hit = Rect::from_center_size(center, vec2(16.0, 16.0));
    let resp = ui.interact(hit, Id::new("guide_find_clear"), Sense::click());
    let color = if resp.hovered() { p.text } else { p.text.gamma_multiply(0.85) };
    ui.painter().circle_filled(center, 7.0, color);
    let d = 2.8;
    let stroke = egui::Stroke::new(1.6, p.input_bg);
    ui.painter().line_segment([center + vec2(-d, -d), center + vec2(d, d)], stroke);
    ui.painter().line_segment([center + vec2(-d, d), center + vec2(d, -d)], stroke);
    resp.clicked()
}

fn paint(ui: &Ui, laid: &Laid, origin: Pos2, view: Rect, selection: Option<(Pos, Pos)>, p: &Palette) {
    let painter = ui.painter().with_clip_rect(view.intersect(ui.clip_rect()));
    let ppp = ui.ctx().pixels_per_point();
    let snap = |v: f32| (v * ppp).round() / ppp;
    let visible = |top: f32, h: f32| origin.y + top + h >= view.top() && origin.y + top <= view.bottom();
    for d in &laid.decos {
        match d {
            Deco::Disc { center, radius, color } => {
                if visible(center.y - radius, 2.0 * radius) {
                    painter.circle_filled(pos2(origin.x + center.x, origin.y + center.y), *radius, *color);
                }
            }
            Deco::Number { right, baseline, text, font, color } => {
                if visible(baseline - font.size, font.size * 1.5) {
                    let g = ui.ctx().fonts_mut(|f| f.layout_no_wrap(text.clone(), font.clone(), *color));
                    let base = galley_baseline(&g);
                    let top = (snap(origin.y + baseline) * ppp - (base * ppp).round()) / ppp;
                    painter.galley(pos2(origin.x + right - g.size().x, top), g, *color);
                }
            }
            Deco::Rule { left, right, y, color } => {
                if visible(*y - 1.0, 2.0) {
                    let yy = snap(origin.y + y);
                    painter.rect_filled(
                        Rect::from_min_max(pos2(origin.x + left, yy), pos2(origin.x + right, yy + 1.0 / ppp)),
                        0.0,
                        *color,
                    );
                }
            }
            Deco::Border { rect, color } => {
                if visible(rect.top(), rect.height()) {
                    painter.rect_filled(rect.translate(origin.to_vec2()), 0.0, *color);
                }
            }
        }
    }
    let sel = selection.map(|(a, b)| (a.min(b), a.max(b))).filter(|(a, b)| a < b);
    for line in &laid.lines {
        if !visible(line.top, line.height) {
            continue;
        }
        let baseline = snap(origin.y + line.top + line.ascent);
        for f in &line.frags {
            let n = f.text.chars().count();
            let (s0, s1) = match sel {
                Some((a, b)) if a < f.start + n && b > f.start => {
                    (a.max(f.start) - f.start, b.min(f.start + n) - f.start)
                }
                _ => (0, 0),
            };
            let chars: Vec<char> = f.text.chars().collect();
            let parts = [(0, s0, false), (s0, s1, true), (s1, n, false)];
            let mut x = origin.x + f.x;
            for (a, b, selected) in parts {
                if a >= b {
                    continue;
                }
                let text: String = chars[a..b].iter().collect();
                let color = if selected { Color32::WHITE } else { f.color };
                let g = ui.ctx().fonts_mut(|fo| fo.layout_no_wrap(text, f.font.clone(), color));
                let w = g.size().x;
                if selected {
                    let top = origin.y + line.top;
                    painter.rect_filled(
                        Rect::from_min_max(pos2(x, top), pos2(x + w, top + line.height)),
                        0.0,
                        p.accent,
                    );
                }
                let base = galley_baseline(&g);
                let top = (baseline * ppp - (base * ppp).round()) / ppp;
                painter.galley(pos2(x, top), g, color);
                x += w;
            }
        }
    }
}

fn galley_baseline(g: &egui::Galley) -> f32 {
    g.rows.first().and_then(|r| r.row.glyphs.first().map(|gl| r.pos.y + gl.pos.y)).unwrap_or(g.size().y * 0.8)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_guide_into_blocks() {
        let nodes = parse(&HTML.replace("{{VERSION}}", "1.0.0"));
        let Node::Block(first) = &nodes[0] else { panic!("the guide starts with a heading") };
        assert_eq!(first.px, 24.0);
        assert!(first.anchors.contains(&"top".to_owned()));
        assert_eq!(first.runs[0].text, "e-CALLISTO FITS Analyzer Lite");
        let tables = nodes.iter().filter(|n| matches!(n, Node::Table(_))).count();
        assert_eq!(tables, 4);
        let anchors: Vec<&String> = nodes
            .iter()
            .filter_map(|n| if let Node::Block(b) = n { Some(&b.anchors) } else { None })
            .flatten()
            .collect();
        for a in ["quick-start", "menu-file", "fits-downloader", "shortcuts", "tips"] {
            assert!(anchors.iter().any(|x| *x == a), "{a}");
        }
    }

    #[test]
    fn collapses_white_space_like_html() {
        let nodes = parse("<p>One\n  <b>two</b>  three <br>\n four </p>");
        let Node::Block(b) = &nodes[0] else { panic!() };
        let text: String = b.runs.iter().map(|r| r.text.as_str()).collect();
        assert_eq!(text, "One two three\u{2028}four");
    }

    #[test]
    fn breaks_after_spaces_hyphens_and_slashes() {
        let c: Vec<char> = "a b-c d/e 5-6".chars().collect();
        let at: Vec<usize> = (0..c.len()).filter(|&i| break_after(&c, i)).collect();
        assert_eq!(at, vec![1, 3, 5, 7, 9]);
    }
}
