//! The parts of ReportLab's Platypus (4.5) the project report uses, laid
//! out as ReportLab lays them out: paragraphs wrapped by words or, in the
//! `wordWrap='CJK'` styles, by characters (`dumbSplit`, `cjkFragSplit`),
//! two-column tables of paragraphs that split between rows, images,
//! preformatted text, spacers, `KeepTogether` and page breaks, flowed
//! through one frame per page (`SimpleDocTemplate`, `Frame._add`,
//! `handle_flowable`). Text is measured with the TrueType advance widths
//! ReportLab reads (`charWidths`, 1/1000 em).

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, LazyLock};

/// `rl_config._FUZZ`.
pub const FUZZ: f64 = 1e-6;
/// `reportlab.lib.units.inch`.
pub const INCH: f64 = 72.0;

/// `reportlab.lib.pagesizes.A4`, as ReportLab computes it.
pub fn a4() -> (f64, f64) {
    let cm = INCH / 2.54;
    let mm = cm * 0.1;
    (210.0 * mm, 297.0 * mm)
}

// --------------------------------------------------------------------- fonts

/// The report's faces: DejaVu Sans (as `register_report_font` registers
/// it) and the header and footer's standard Helvetica.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum FontId {
    Regular,
    Bold,
    Helvetica,
}

pub static DEJAVU_SANS: &[u8] = include_bytes!("../../assets/fonts/DejaVuSans.ttf");
pub static DEJAVU_SANS_BOLD: &[u8] = include_bytes!("../../assets/fonts/DejaVuSans-Bold.ttf");

/// A TrueType face's widths as `TTFontFile.extractInfo` reads them.
pub struct TtMetrics {
    pub data: &'static [u8],
    /// 1/1000 em, by character.
    widths: HashMap<char, f64>,
    /// Glyph 0's width, for characters the font lacks.
    default: f64,
    pub units_per_em: u16,
    pub ascent: f64,
    pub descent: f64,
    pub cap_height: f64,
    pub bbox: [f64; 4],
    pub italic_angle: f64,
    pub name: String,
}

impl TtMetrics {
    fn load(data: &'static [u8]) -> TtMetrics {
        let face = ttf_parser::Face::parse(data, 0).expect("bundled font");
        let upem = face.units_per_em();
        let scale = 1000.0 / upem as f64;
        let mut widths = HashMap::new();
        if let Some(cmap) = face.tables().cmap {
            for sub in cmap.subtables {
                if !sub.is_unicode() {
                    continue;
                }
                sub.codepoints(|cp| {
                    if let (Some(c), Some(g)) = (char::from_u32(cp), sub.glyph_index(cp)) {
                        widths.entry(c).or_insert_with(|| face.glyph_hor_advance(g).unwrap_or(0) as f64 * scale);
                    }
                });
            }
        }
        // `charWidths[0xa0] = charWidths[0x20]`.
        if let Some(&w) = widths.get(&' ') {
            widths.insert('\u{a0}', w);
        }
        let default = face.glyph_hor_advance(ttf_parser::GlyphId(0)).unwrap_or(0) as f64 * scale;
        let bb = face.global_bounding_box();
        let name = face
            .names()
            .into_iter()
            .find(|n| n.name_id == ttf_parser::name_id::POST_SCRIPT_NAME && n.is_unicode())
            .and_then(|n| n.to_string())
            .unwrap_or_else(|| "DejaVuSans".into());
        TtMetrics {
            data,
            widths,
            default,
            units_per_em: upem,
            ascent: face.ascender() as f64 * scale,
            descent: face.descender() as f64 * scale,
            cap_height: face.capital_height().unwrap_or(face.ascender()) as f64 * scale,
            bbox: [bb.x_min as f64 * scale, bb.y_min as f64 * scale, bb.x_max as f64 * scale, bb.y_max as f64 * scale],
            italic_angle: face.italic_angle() as f64,
            name,
        }
    }

    /// A character's width in 1/1000 em.
    pub fn width(&self, c: char) -> f64 {
        *self.widths.get(&c).unwrap_or(&self.default)
    }
}

pub static REGULAR: LazyLock<TtMetrics> = LazyLock::new(|| TtMetrics::load(DEJAVU_SANS));
pub static BOLD: LazyLock<TtMetrics> = LazyLock::new(|| TtMetrics::load(DEJAVU_SANS_BOLD));

/// Helvetica's AFM widths for the characters the footer right-aligns.
fn helvetica_width(c: char) -> f64 {
    match c {
        ' ' => 278.0,
        'P' => 667.0,
        'a' | 'e' | 'g' | '0'..='9' => 556.0,
        _ => 556.0,
    }
}

/// `pdfmetrics.stringWidth`: `0.001 * size * Σ widths`.
pub fn string_width(text: &str, font: FontId, size: f64) -> f64 {
    let sum: f64 = match font {
        FontId::Regular => text.chars().map(|c| REGULAR.width(c)).sum(),
        FontId::Bold => text.chars().map(|c| BOLD.width(c)).sum(),
        FontId::Helvetica => text.chars().map(helvetica_width).sum(),
    };
    0.001 * size * sum
}

// -------------------------------------------------------------------- styles

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Align {
    Left,
    Center,
}

/// A `ParagraphStyle`'s values the report reads.
#[derive(Clone, Debug)]
pub struct Style {
    pub name: &'static str,
    pub font: FontId,
    pub size: f64,
    pub leading: f64,
    pub space_before: f64,
    pub space_after: f64,
    pub align: Align,
    /// `wordWrap='CJK'`: lines break between any two characters.
    pub cjk: bool,
    pub color: [u8; 3],
}

// ------------------------------------------------------------------- markup

/// `cleanBlockQuotedText`: each line stripped and its whitespace runs
/// collapsed, empty lines dropped, the rest joined by spaces.
pub fn clean_block_quoted(text: &str) -> String {
    text.split('\n')
        .map(|l| l.split(|c: char| WSC.contains(c)).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" "))
        .filter(|l| !l.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// `paragraph._wsc`: what ReportLab strips and splits on.
const WSC: &str = "\u{9}\u{a}\u{b}\u{c}\u{d}\u{1c}\u{1d}\u{1e}\u{1f}\u{20}\u{85}\u{1680}\u{2000}\u{2001}\u{2002}\u{2003}\u{2004}\u{2005}\u{2006}\u{2007}\u{2008}\u{2009}\u{200a}\u{2028}\u{2029}\u{202f}\u{205f}\u{3000}";

/// `html.escape`, as the report escapes its values.
pub fn escape(text: &str) -> String {
    let mut s = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => s.push_str("&amp;"),
            '<' => s.push_str("&lt;"),
            '>' => s.push_str("&gt;"),
            '"' => s.push_str("&quot;"),
            '\'' => s.push_str("&#x27;"),
            _ => s.push(c),
        }
    }
    s
}

fn unescape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(i) = rest.find('&') {
        out.push_str(&rest[..i]);
        rest = &rest[i..];
        let Some(end) = rest.find(';') else { break };
        let entity = &rest[1..end];
        let c = match entity {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            "nbsp" => Some('\u{a0}'),
            e if e.starts_with("#x") || e.starts_with("#X") => u32::from_str_radix(&e[2..], 16).ok().and_then(char::from_u32),
            e if e.starts_with('#') => e[1..].parse().ok().and_then(char::from_u32),
            _ => None,
        };
        match c {
            Some(c) => {
                out.push(c);
                rest = &rest[end + 1..];
            }
            None => {
                out.push('&');
                rest = &rest[1..];
            }
        }
    }
    out.push_str(rest);
    out
}

/// The paragraph markup the report writes (escaped text and `<br/>`),
/// cleaned and parsed into the text runs between line breaks.
fn parse(markup: &str) -> Vec<String> {
    let cleaned = clean_block_quoted(markup);
    let mut segs = vec![String::new()];
    let mut rest = cleaned.as_str();
    while let Some(i) = rest.find('<') {
        segs.last_mut().unwrap().push_str(&unescape(&rest[..i]));
        let Some(end) = rest[i..].find('>') else {
            rest = &rest[i..];
            break;
        };
        let tag = rest[i + 1..i + end].trim().trim_end_matches('/').trim().to_lowercase();
        if tag == "br" {
            segs.push(String::new());
        }
        rest = &rest[i + end + 1..];
    }
    segs.last_mut().unwrap().push_str(&unescape(rest));
    segs
}

// ------------------------------------------------------------------ wrapping

/// `ALL_CANNOT_START` (`reportlab.lib.textsplit`).
const ALL_CANNOT_START: &str = "!',.:;?!\")]\u{3001}\u{3002}\u{300d}\u{300f}\u{3011}\u{3015}\u{ff3d}\u{3011}\u{ff09}\u{3005}\u{2015}\u{3041}\u{3043}\u{3045}\u{3047}\u{3049}\u{3063}\u{3083}\u{3085}\u{3087}\u{308e}\u{30a1}\u{30a3}\u{30a5}\u{30a7}\u{30a9}\u{30c3}\u{30e3}\u{30e5}\u{30e7}\u{30ee}\u{30fc}\u{30f5}\u{30f6}\u{309b}\u{309c}\u{30fb}\u{30fd}\u{30fe}\u{309d}\u{309e}\u{2015}\u{2010}\u{b0}\u{2032}\u{2033}\u{2103}\u{ffe0}\u{ff05}\u{2030}";

/// `unicodedata.category(c) == 'Zs'`.
fn is_space_separator(c: char) -> bool {
    matches!(c, ' ' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

/// One laid-out line: its text and the width left over.
#[derive(Clone, Debug)]
pub struct Line {
    pub text: String,
    pub extra: f64,
}

/// `textsplit.dumbSplit` of a one-run CJK paragraph.
fn dumb_split(word: &[char], widths: &[f64], max_width: f64) -> Vec<Line> {
    let mut lines = Vec::new();
    let (mut i, mut used, mut start) = (0usize, 0.0f64, 0usize);
    let n = word.len();
    while i < n {
        let mut w = widths[i];
        let mut c = word[i];
        used += w;
        i += 1;
        if used > max_width + FUZZ && used > 0.0 {
            let mut extra = max_width - used;
            if (c as u32) < 0x3000 {
                let limit = (start + i) >> 1;
                let mut j = i - 1;
                while j > limit {
                    let cj = word[j];
                    if is_space_separator(cj) || cj as u32 >= 0x3000 {
                        let k = j + 1;
                        if k < i {
                            let j2 = k + 1;
                            extra += widths[j2.min(i)..i].iter().sum::<f64>();
                            w = widths[k];
                            c = word[k];
                            i = j2;
                            break;
                        }
                    }
                    j -= 1;
                }
            }
            if !ALL_CANNOT_START.contains(c) && i > start + 1 {
                i -= 1;
                extra += w;
            }
            let text: String = word[start..i].iter().collect();
            lines.push(Line { text: text.trim().to_owned(), extra });
            start = i;
            used = 0.0;
        }
    }
    if used > 0.0 {
        lines.push(Line { text: word[start..].iter().collect(), extra: max_width - used });
    }
    lines
}

/// `cjkFragSplit` of a CJK paragraph with line breaks: `None` is a `<br/>`.
fn cjk_frag_split(units: &[(Option<char>, f64)], max_width: f64) -> Vec<Line> {
    let mut lines = Vec::new();
    let (mut i, mut used, mut start) = (0usize, 0.0f64, 0usize);
    let n = units.len();
    let text = |a: usize, b: usize| units[a..b].iter().filter_map(|u| u.0).collect::<String>();
    while i < n {
        let mut u = units[i];
        i += 1;
        let mut w = u.1;
        used += w;
        let line_break = u.0.is_none();
        if (used > max_width + FUZZ && used > 0.0) || line_break {
            let mut extra = max_width - used;
            if !line_break {
                if (u.0.unwrap() as u32) < 0x3000 {
                    let limit = (start + i) >> 1;
                    let mut j = i - 1;
                    while j > limit {
                        if let Some(cj) = units[j].0
                            && (is_space_separator(cj) || cj as u32 >= 0x3000)
                        {
                            let k = j + 1;
                            if k < i {
                                let j2 = k + 1;
                                extra += units[j2.min(i)..i].iter().map(|u| u.1).sum::<f64>();
                                w = units[k].1;
                                u = units[k];
                                i = j2;
                                break;
                            }
                        }
                        j -= 1;
                    }
                }
                if !u.0.is_some_and(|c| ALL_CANNOT_START.contains(c)) && i > start + 1 {
                    i -= 1;
                    extra += w;
                }
            }
            lines.push(Line { text: text(start, i), extra });
            start = i;
            used = 0.0;
        }
    }
    if used > 0.0 {
        lines.push(Line { text: text(start, n), extra: max_width - used });
    }
    lines
}

/// `breakLines` for one run of text in one font (kind 0): greedy by words,
/// a word wider than the line split by characters (`_splitWord`).
fn break_words(text: &str, font: FontId, size: f64, max_width: f64) -> Vec<Line> {
    let text = text.trim_matches(|c: char| WSC.contains(c));
    if text.is_empty() {
        return Vec::new();
    }
    let space = string_width(" ", font, size);
    let shrink = 0.05 * space;
    let mut words: VecDeque<(String, bool)> = text.split_whitespace().map(|w| (w.to_owned(), false)).collect();
    let mut lines = Vec::new();
    let mut line: Vec<String> = Vec::new();
    let mut current = -space;
    let mut forced = false;
    while let Some((word, split_piece)) = words.pop_front() {
        let ww = string_width(&word, font, size);
        let new = current + space + ww;
        let lim = max_width + shrink * line.len() as f64;
        if new > lim && !forced && !split_piece && ww > max_width {
            // `_splitWord`: as many characters as fit on each line.
            let mut pieces = Vec::new();
            let mut line_width = current + space;
            let mut piece = String::new();
            for c in word.chars() {
                let cw = string_width(&c.to_string(), font, size);
                let nlw = line_width + cw;
                if nlw > max_width && (!piece.is_empty() || cw <= max_width) {
                    pieces.push(std::mem::take(&mut piece));
                    line_width = cw;
                } else {
                    line_width = nlw;
                }
                piece.push(c);
            }
            pieces.push(piece);
            // `_SplitWord`s (the last a `_SplitWordEnd`): never split again.
            for p in pieces.into_iter().rev() {
                words.push_front((p, true));
            }
            forced = true;
            continue;
        }
        if new <= lim || line.is_empty() || forced {
            line.push(word);
            if forced {
                forced = false;
                lines.push(Line { text: line.join(" "), extra: max_width - new });
                line.clear();
                current = -space;
            } else {
                current = new;
            }
        } else {
            lines.push(Line { text: line.join(" "), extra: max_width - current });
            line = vec![word];
            current = ww;
        }
    }
    if !line.is_empty() {
        lines.push(Line { text: line.join(" "), extra: max_width - current });
    }
    lines
}

// ----------------------------------------------------------------- flowables

/// A `Paragraph`.
#[derive(Clone, Debug)]
pub struct Para {
    pub markup: String,
    pub style: Arc<Style>,
    segs: Vec<String>,
    lines: Vec<Line>,
    width: f64,
    height: f64,
}

impl Para {
    pub fn new(markup: impl Into<String>, style: &Arc<Style>) -> Para {
        let markup = markup.into();
        let segs = parse(&markup);
        Para { markup, style: style.clone(), segs, lines: Vec::new(), width: 0.0, height: 0.0 }
    }

    fn wrap(&mut self, aw: f64) -> (f64, f64) {
        let st = &self.style;
        self.width = aw;
        self.lines = if st.cjk {
            if self.segs.len() == 1 {
                let chars: Vec<char> = self.segs[0].chars().collect();
                let widths: Vec<f64> = chars.iter().map(|c| string_width(&c.to_string(), st.font, st.size)).collect();
                dumb_split(&chars, &widths, aw)
            } else {
                let mut units = Vec::new();
                for (i, s) in self.segs.iter().enumerate() {
                    if i > 0 {
                        units.push((None, 0.0));
                    }
                    units.extend(s.chars().map(|c| (Some(c), string_width(&c.to_string(), st.font, st.size))));
                }
                cjk_frag_split(&units, aw)
            }
        } else {
            self.segs.iter().flat_map(|s| break_words(s, st.font, st.size, aw)).collect()
        };
        self.height = self.lines.len() as f64 * st.leading;
        (self.width, self.height)
    }

    /// `Paragraph.split`: whole lines, never leaving one alone at the bottom.
    fn split(&mut self, aw: f64, ah: f64) -> Option<(Para, Para)> {
        self.wrap(aw);
        let s = (ah / self.style.leading) as usize;
        if s <= 1 || self.lines.len() <= s {
            return None;
        }
        let mut first = self.clone();
        first.lines.truncate(s);
        first.height = s as f64 * self.style.leading;
        let mut rest = self.clone();
        rest.lines.drain(..s);
        rest.height = rest.lines.len() as f64 * self.style.leading;
        Some((first, rest))
    }
}

/// A two-column table of paragraphs (`_pair_table`): fixed widths, a grid,
/// the key column shaded, cells padded and aligned at the top.
#[derive(Clone, Debug)]
pub struct PairTable {
    pub rows: Vec<[Para; 2]>,
    pub col_widths: [f64; 2],
    pub grid: [u8; 3],
    pub grid_width: f64,
    pub key_background: [u8; 3],
    pub pad: [f64; 4],
    heights: Vec<f64>,
}

impl PairTable {
    pub fn new(rows: Vec<[Para; 2]>, col_widths: [f64; 2]) -> PairTable {
        PairTable {
            rows,
            col_widths,
            grid: [0xd7, 0xdc, 0xe3],
            grid_width: 0.25,
            key_background: [0xf3, 0xf6, 0xfa],
            // left, right, top, bottom
            pad: [5.0, 5.0, 4.0, 4.0],
            heights: Vec::new(),
        }
    }

    /// `_calc_height`: each row as tall as its tallest cell plus padding.
    fn wrap(&mut self) -> (f64, f64) {
        let pad = self.pad;
        let cw = self.col_widths;
        self.heights = self
            .rows
            .iter_mut()
            .map(|r| {
                let a = r[0].wrap(cw[0] - pad[0] - pad[1]).1;
                let b = r[1].wrap(cw[1] - pad[0] - pad[1]).1;
                a.max(b) + pad[3] + pad[2]
            })
            .collect();
        (cw[0] + cw[1], self.height())
    }

    /// The rows' total, summed bottom up as `_calc_height` sums them.
    fn height(&self) -> f64 {
        let (mut height, mut c) = (0.0f64, 0.0f64);
        for h in self.heights.iter().rev() {
            let y = h - c;
            let t = height + y;
            c = (t - height) - y;
            height = t;
        }
        height
    }

    /// `_splitRows` between rows: as many as fit, at least one.
    fn split(&mut self, ah: f64) -> Option<(PairTable, PairTable)> {
        self.wrap();
        let mut h = 0.0;
        let mut n = 0;
        for rh in &self.heights {
            if h + rh > ah {
                break;
            }
            h += rh;
            n += 1;
        }
        if n == 0 || n == self.rows.len() {
            return None;
        }
        let mut first = self.clone();
        first.rows.truncate(n);
        let mut rest = self.clone();
        rest.rows.drain(..n);
        Some((first, rest))
    }
}

/// An `Image`: a PNG drawn at a size in points, centred in the frame.
#[derive(Clone, Debug)]
pub struct ImageFlow {
    pub png: Arc<Vec<u8>>,
    pub width: f64,
    pub height: f64,
}

/// A `Preformatted` block: lines drawn as they are.
#[derive(Clone, Debug)]
pub struct Pre {
    pub lines: Vec<String>,
    pub style: Arc<Style>,
}

impl Pre {
    /// `_dedenter`: the text's lines, without leading or trailing blank ones.
    pub fn new(text: &str, style: &Arc<Style>) -> Pre {
        let mut lines: Vec<String> = text.split('\n').map(str::to_owned).collect();
        while lines.first().is_some_and(|l| l.trim().is_empty()) {
            lines.remove(0);
        }
        while lines.last().is_some_and(|l| l.trim().is_empty()) {
            lines.pop();
        }
        Pre { lines, style: style.clone() }
    }
}

#[derive(Clone, Debug)]
pub enum Flow {
    Spacer(f64),
    Para(Para),
    Table(PairTable),
    Image(ImageFlow),
    Pre(Pre),
    KeepTogether(Vec<Flow>),
    PageBreak,
    /// What `KeepTogether.split` inserts to start a new frame.
    FrameBreak,
    /// `NullActionFlowable`: hands the pieces back to flow one by one.
    Null,
}

impl Flow {
    fn space_before(&self) -> f64 {
        match self {
            Flow::Para(p) => p.style.space_before,
            Flow::Pre(p) => p.style.space_before,
            Flow::KeepTogether(c) => c.first().map_or(0.0, |f| f.space_before()),
            _ => 0.0,
        }
    }

    fn space_after(&self) -> f64 {
        match self {
            Flow::Para(p) => p.style.space_after,
            Flow::Pre(p) => p.style.space_after,
            Flow::KeepTogether(c) => c.last().map_or(0.0, |f| f.space_after()),
            _ => 0.0,
        }
    }

    /// `wrap(availWidth, availHeight)`.
    fn wrap(&mut self, aw: f64) -> (f64, f64) {
        match self {
            Flow::Spacer(h) => (1.0, *h),
            Flow::Para(p) => p.wrap(aw),
            Flow::Table(t) => t.wrap(),
            Flow::Image(i) => (i.width, i.height),
            Flow::Pre(p) => (aw, p.style.leading * p.lines.len() as f64),
            Flow::KeepTogether(_) => (aw, 0xffffff as f64),
            Flow::PageBreak | Flow::FrameBreak | Flow::Null => (0.0, 0.0),
        }
    }
}

/// `_listWrapOn`: the height of flowables stacked with their spaces merged.
fn list_height(items: &mut [Flow], aw: f64) -> (f64, f64) {
    let (mut h_total, mut ps, mut at_top, mut first) = (0.0, 0.0, true, 0.0);
    for (i, f) in items.iter_mut().enumerate() {
        let (_, h) = f.wrap(aw);
        if i == 0 {
            first = h;
        }
        if h <= FUZZ {
            continue;
        }
        h_total += h;
        if !at_top {
            h_total += (f.space_before() - ps).max(0.0);
        } else {
            at_top = false;
        }
        ps = f.space_after();
        h_total += ps;
    }
    (h_total - ps, first)
}

// --------------------------------------------------------------- the output

/// What a page shows, in points from its bottom-left corner.
#[derive(Clone, Debug)]
pub enum Draw {
    /// Text with its baseline's left end at (`x`, `y`).
    Text { x: f64, y: f64, text: String, font: FontId, size: f64, color: [u8; 3] },
    Rect { x: f64, y: f64, w: f64, h: f64, color: [u8; 3] },
    Line { x0: f64, y0: f64, x1: f64, y1: f64, width: f64, color: [u8; 3] },
    Image { x: f64, y: f64, w: f64, h: f64, png: Arc<Vec<u8>> },
}

/// Where a flowable was drawn, as `make_report_reference.py` records it.
#[derive(Clone, Debug, PartialEq)]
pub struct Placed {
    pub page: usize,
    pub kind: &'static str,
    pub style: &'static str,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub lines: Vec<String>,
    pub col_widths: Vec<f64>,
    pub row_heights: Vec<f64>,
}

/// The pages' drawing and the record of what went where.
#[derive(Default)]
pub struct Laid {
    pub pages: Vec<Vec<Draw>>,
    pub placed: Vec<Placed>,
}

/// `SimpleDocTemplate`'s page: margins in points, a header and footer.
pub struct Doc {
    pub page: (f64, f64),
    pub left: f64,
    pub right: f64,
    pub top: f64,
    pub bottom: f64,
}

impl Doc {
    /// `doc.width`.
    pub fn width(&self) -> f64 {
        self.page.0 - self.left - self.right
    }
}

/// The frame being filled: `Frame` with 6 pt padding on every side.
struct Frame {
    x: f64,
    y: f64,
    bottom: f64,
    aw: f64,
    at_top: bool,
    prev_space: f64,
}

struct Builder<'a> {
    doc: &'a Doc,
    out: Laid,
    frame: Frame,
}

const FRAME_PAD: f64 = 6.0;

impl Builder<'_> {
    fn new_frame(doc: &Doc) -> Frame {
        let (x1, y1) = (doc.left, doc.bottom);
        let (w, h) = (doc.width(), doc.page.1 - doc.top - doc.bottom);
        Frame { x: x1 + FRAME_PAD, y: y1 + h - FRAME_PAD, bottom: y1 + FRAME_PAD, aw: w - 2.0 * FRAME_PAD, at_top: true, prev_space: 0.0 }
    }

    fn page(&mut self) -> &mut Vec<Draw> {
        self.out.pages.last_mut().unwrap()
    }

    fn end_page(&mut self) {
        self.out.pages.push(Vec::new());
        self.frame = Self::new_frame(self.doc);
    }

    /// `Frame._add`: draws `f` if it fits below the current point.
    fn add(&mut self, f: &mut Flow) -> bool {
        let fr = &self.frame;
        let mut s = 0.0;
        if !fr.at_top {
            s = (f.space_before() - fr.prev_space).max(0.0);
        }
        let avail = fr.y - fr.bottom - s;
        if avail <= 0.0 {
            return false;
        }
        let (w, h) = f.wrap(fr.aw);
        let h = h + s;
        let y = fr.y - h;
        if y < fr.bottom - FUZZ {
            return false;
        }
        let x = fr.x;
        let aw = fr.aw;
        self.draw(f, x, y, aw - w);
        let sa = f.space_after();
        let y = y - sa;
        self.frame.prev_space = sa;
        if y != self.frame.y {
            self.frame.at_top = false;
        }
        self.frame.y = y;
        true
    }

    /// `Frame.split`.
    fn split(&mut self, f: &mut Flow) -> Option<Vec<Flow>> {
        let fr = &self.frame;
        let mut s = 0.0;
        if !fr.at_top {
            s = (f.space_before() - fr.prev_space).max(0.0);
        }
        let ah = fr.y - fr.bottom - s;
        if ah <= 0.0 {
            return None;
        }
        let aw = fr.aw;
        let at_top = fr.at_top;
        match f {
            Flow::Para(p) => p.split(aw, ah).map(|(a, b)| vec![Flow::Para(a), Flow::Para(b)]),
            Flow::Table(t) => t.split(ah).map(|(a, b)| vec![Flow::Table(a), Flow::Table(b)]),
            Flow::Pre(p) => {
                if ah < p.style.leading {
                    return None;
                }
                let n = (ah / p.style.leading) as usize;
                let rest = Pre { lines: p.lines[n.min(p.lines.len())..].to_vec(), style: p.style.clone() };
                let first = Pre { lines: p.lines[..n.min(p.lines.len())].to_vec(), style: p.style.clone() };
                Some(vec![Flow::Pre(first), Flow::Pre(rest)])
            }
            Flow::KeepTogether(items) => {
                let (h, h0) = list_height(items, aw);
                let c0 = h > ah;
                let c1 = h0 > ah || (c0 && at_top);
                let mut out = items.clone();
                // The next frame is this one's size: break unless already at
                // the top, where the pieces just flow on.
                if c0 && !at_top || (!c0 && c1) {
                    out.insert(0, Flow::FrameBreak);
                } else if c0 {
                    out.insert(0, Flow::Null);
                }
                Some(out)
            }
            _ => None,
        }
    }

    fn place(&mut self, kind: &'static str, style: &'static str, x: f64, y: f64, w: f64, h: f64) -> &mut Placed {
        let page = self.out.pages.len();
        self.out.placed.push(Placed { page, kind, style, x, y, width: w, height: h, lines: Vec::new(), col_widths: Vec::new(), row_heights: Vec::new() });
        self.out.placed.last_mut().unwrap()
    }

    /// `drawOn` at (`x`, `y`) with `slack` to spare across the frame.
    fn draw(&mut self, f: &Flow, x: f64, y: f64, slack: f64) {
        match f {
            Flow::Spacer(h) => {
                self.place("Spacer", "", x, y, 1.0, *h);
            }
            Flow::Para(p) => self.draw_para(p, x, y),
            Flow::Table(t) => self.draw_table(t, x, y),
            Flow::Image(i) => {
                // `hAlign='CENTER'`.
                let x = x + 0.5 * slack;
                self.place("Image", "", x, y, i.width, i.height);
                self.page().push(Draw::Image { x, y, w: i.width, h: i.height, png: i.png.clone() });
            }
            Flow::Pre(p) => {
                let h = p.style.leading * p.lines.len() as f64;
                let aw = self.frame.aw;
                self.place("Preformatted", "", x, y, aw, h).lines = p.lines.clone();
                let st = &p.style;
                for (i, line) in p.lines.iter().enumerate() {
                    let by = y + h - st.size - i as f64 * st.leading;
                    self.page().push(Draw::Text { x, y: by, text: line.clone(), font: st.font, size: st.size, color: st.color });
                }
            }
            _ => {}
        }
    }

    fn draw_para(&mut self, p: &Para, x: f64, y: f64) {
        let st = p.style.clone();
        let lines: Vec<String> = p.lines.iter().map(|l| l.text.clone()).collect();
        self.place("Paragraph", st.name, x, y, p.width, p.height).lines = lines;
        for (i, l) in p.lines.iter().enumerate() {
            let by = y + p.height - st.size - i as f64 * st.leading;
            let lx = match st.align {
                Align::Left => x,
                Align::Center => x + 0.5 * l.extra,
            };
            self.page().push(Draw::Text { x: lx, y: by, text: l.text.trim_end().to_owned(), font: st.font, size: st.size, color: st.color });
        }
    }

    fn draw_table(&mut self, t: &PairTable, x: f64, y: f64) {
        let total = t.height();
        let placed = self.place("Table", "", x, y, 0.0, 0.0);
        placed.col_widths = t.col_widths.to_vec();
        placed.row_heights = t.heights.clone();
        let (w0, w1) = (t.col_widths[0], t.col_widths[1]);
        // `BACKGROUND` of the key column, then the cells, then `GRID`.
        self.page().push(Draw::Rect { x, y, w: w0, h: total, color: t.key_background });
        let mut top = y + total;
        for (row, h) in t.rows.iter().zip(&t.heights) {
            for (k, cell) in row.iter().enumerate() {
                let cx = x + if k == 0 { 0.0 } else { w0 } + t.pad[0];
                let ch = cell.height;
                self.draw_para(cell, cx, top - t.pad[2] - ch);
            }
            top -= h;
        }
        let gw = t.grid_width;
        let mut ys = vec![y + total];
        let mut acc = y + total;
        for h in &t.heights {
            acc -= h;
            ys.push(acc);
        }
        for &yy in &ys {
            self.page().push(Draw::Line { x0: x, y0: yy, x1: x + w0 + w1, y1: yy, width: gw, color: t.grid });
        }
        for xx in [x, x + w0, x + w0 + w1] {
            self.page().push(Draw::Line { x0: xx, y0: y, x1: xx, y1: y + total, width: gw, color: t.grid });
        }
    }

    /// `handle_flowable`, until the story is told.
    fn build(&mut self, story: Vec<Flow>) {
        let mut queue: VecDeque<(Flow, bool)> = story.into_iter().map(|f| (f, false)).collect();
        while let Some((mut f, postponed)) = queue.pop_front() {
            match f {
                Flow::PageBreak | Flow::FrameBreak => {
                    self.end_page();
                    continue;
                }
                Flow::Null => continue,
                _ => {}
            }
            if self.add(&mut f) {
                continue;
            }
            match self.split(&mut f) {
                Some(parts) if !parts.is_empty() => {
                    let mut parts: VecDeque<Flow> = parts.into();
                    let head_is_action = matches!(parts.front(), Some(Flow::FrameBreak | Flow::PageBreak | Flow::Null));
                    if !head_is_action {
                        let mut head = parts.pop_front().unwrap();
                        if !self.add(&mut head) {
                            // ReportLab raises a LayoutError; draw it on a page of its own.
                            self.end_page();
                            self.add(&mut head);
                        }
                    }
                    for p in parts.into_iter().rev() {
                        queue.push_front((p, false));
                    }
                }
                _ => {
                    if postponed {
                        // Too large for any frame: ReportLab gives up; keep going.
                        continue;
                    }
                    queue.push_front((f, true));
                    self.end_page();
                }
            }
        }
    }
}

/// Lays `story` out over pages of `doc`.
pub fn build(doc: &Doc, story: Vec<Flow>) -> Laid {
    let mut b = Builder { doc, out: Laid { pages: vec![Vec::new()], placed: Vec::new() }, frame: Builder::new_frame(doc) };
    b.build(story);
    b.out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn small() -> Arc<Style> {
        Arc::new(Style { name: "SmallText", font: FontId::Regular, size: 8.5, leading: 11.0, space_before: 0.0, space_after: 0.0, align: Align::Left, cjk: true, color: [0, 0, 0] })
    }

    #[test]
    fn widths_like_reportlab() {
        // `stringWidth(' ', 'DejaVuSans', 8.5)`.
        assert_eq!(string_width(" ", FontId::Regular, 8.5), 2.701904296875);
        assert!((string_width("Page 10", FontId::Helvetica, 8.0) - 29.8).abs() < 1e-9);
    }

    #[test]
    fn character_wrap_breaks_after_spaces() {
        let mut p = Para::new("aaaa bbbb cccc", &small());
        let w = string_width("aaaa bbbb ", FontId::Regular, 8.5);
        p.wrap(w + 0.5);
        let lines: Vec<&str> = p.lines.iter().map(|l| l.text.as_str()).collect();
        assert_eq!(lines, ["aaaa bbbb", "cccc"]);
    }
}
