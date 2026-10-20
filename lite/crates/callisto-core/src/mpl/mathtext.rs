//! The part of matplotlib's mathtext (3.10, `_mathtext.py`) the full app's
//! labels use: text with `$…$` math in the text's own font — superscripts
//! and subscripts (`$\mathregular{^{-0.41}}$`), groups and plain
//! characters — laid out as `Parser` builds its boxes and `ship` places the
//! glyphs, with the extents Agg's raster output reports.
//!
//! The glyph metrics come from the caller ([`TextMeasure::glyph`]), as
//! `TruetypeFonts._get_info` reads them from FreeType for a font and size.

use super::{Face, TextMeasure};

pub const SHRINK_FACTOR: f64 = 0.7;
const NUM_SIZE_LEVELS: u32 = 6;

// `FontConstantsBase`: the constants for fonts mathtext has no table for
// (Arial and the UI fonts among them).
const SCRIPT_SPACE: f64 = 0.05;
const SUP1: f64 = 0.7;
const SUB1: f64 = 0.3;
const SUB2: f64 = 0.5;
const DELTA: f64 = 0.025;

/// `FontMetrics`: a glyph's advance, ink size and ink box (y up from the
/// baseline), and `iceberg` (the bearing above the baseline), in pixels.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct GlyphMetrics {
    pub advance: f64,
    pub width: f64,
    pub height: f64,
    pub xmin: f64,
    pub xmax: f64,
    pub ymin: f64,
    pub ymax: f64,
    pub iceberg: f64,
}

/// `cbook.is_math_text`: an even, non-zero number of unescaped `$`.
pub fn is_math_text(s: &str) -> bool {
    let count = s.matches('$').count() - s.matches("\\$").count();
    count > 0 && count.is_multiple_of(2)
}

/// A glyph `ship` placed: its character and size, its pen position (x
/// right, y down from the top of the box, on the glyph's baseline), and
/// its metrics at that size.
#[derive(Clone, Debug, PartialEq)]
pub struct PlacedGlyph {
    pub c: char,
    pub size_px: f64,
    pub x: f64,
    pub y: f64,
    pub metrics: GlyphMetrics,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MathLayout {
    pub glyphs: Vec<PlacedGlyph>,
    /// The outer box's natural width, height and depth.
    pub box_width: f64,
    pub box_height: f64,
    pub box_depth: f64,
    /// `RendererAgg.get_text_width_height_descent(ismath=True)`.
    pub width: f64,
    pub height: f64,
    pub descent: f64,
    /// The raster's left and top edges in box coordinates (`xmin`, `ymin`
    /// of `Output.to_raster`).
    pub raster_x0: f64,
    pub raster_y0: f64,
}

#[derive(Clone, Debug)]
struct CharNode {
    c: char,
    size_px: f64,
    metrics: GlyphMetrics,
    width: f64,
    height: f64,
    depth: f64,
    level: u32,
}

#[derive(Clone, Debug)]
struct HList {
    children: Vec<Node>,
    width: f64,
    height: f64,
    depth: f64,
    shift: f64,
    level: u32,
}

#[derive(Clone, Debug)]
enum Node {
    Char(CharNode),
    Kern { width: f64, level: u32 },
    List(HList),
    /// `Hbox(width)`: an empty box.
    Box { width: f64, level: u32 },
}

/// `get_unicode_index`: mathtext draws `-` as the minus sign.
fn glyph_char(c: char) -> char {
    if c == '-' { '\u{2212}' } else { c }
}

struct Ctx<'a> {
    m: &'a dyn TextMeasure,
    face: Face,
    size_px: f64,
}

impl Ctx<'_> {
    fn metrics(&self, c: char, size_px: f64) -> GlyphMetrics {
        self.m.glyph(glyph_char(c), size_px, self.face).unwrap_or_default()
    }

    /// `Char.__init__`.
    fn char(&self, c: char) -> Node {
        let c = glyph_char(c);
        let metrics = self.metrics(c, self.size_px);
        let width = if c == ' ' { metrics.advance } else { metrics.width };
        Node::Char(CharNode {
            c,
            size_px: self.size_px,
            metrics,
            width,
            height: metrics.iceberg,
            depth: -(metrics.iceberg - metrics.height),
            level: 0,
        })
    }

    /// `Hlist.__init__`: kerned (`Hlist.kern`), then packed.
    fn hlist(&self, children: Vec<Node>, do_kern: bool) -> HList {
        let children = if do_kern { self.kern(children) } else { children };
        let mut h = HList { children, width: 0.0, height: 0.0, depth: 0.0, shift: 0.0, level: 0 };
        h.hpack();
        h
    }

    /// `Hlist.kern` with `Char.get_kerning`: after each character, its
    /// advance less its ink width, plus the pair kerning with the next
    /// character of the same size.
    fn kern(&self, children: Vec<Node>) -> Vec<Node> {
        let mut out = Vec::with_capacity(children.len() * 2);
        for i in 0..children.len() {
            let distance = match &children[i] {
                Node::Char(a) => {
                    let mut k = a.metrics.advance - a.width;
                    if let Some(Node::Char(b)) = children.get(i + 1)
                        && b.size_px == a.size_px
                    {
                        k += self.m.kerning(a.c, b.c, a.size_px, self.face);
                    }
                    k
                }
                _ => 0.0,
            };
            out.push(children[i].clone());
            if distance != 0.0 {
                out.push(Node::Kern { width: distance, level: 0 });
            }
        }
        out
    }
}

impl Node {
    fn shrink(&mut self) {
        match self {
            Node::Char(c) => {
                c.level += 1;
                if c.level < NUM_SIZE_LEVELS {
                    c.size_px *= SHRINK_FACTOR;
                    c.width *= SHRINK_FACTOR;
                    c.height *= SHRINK_FACTOR;
                    c.depth *= SHRINK_FACTOR;
                }
            }
            Node::Kern { width, level } | Node::Box { width, level } => {
                *level += 1;
                if *level < NUM_SIZE_LEVELS {
                    *width *= SHRINK_FACTOR;
                }
            }
            Node::List(l) => l.shrink(),
        }
    }
}

impl HList {
    /// `Hlist.hpack()` at the natural width (no glue here).
    fn hpack(&mut self) {
        let (mut x, mut h, mut d) = (0.0f64, 0.0f64, 0.0f64);
        for p in &self.children {
            match p {
                Node::Char(c) => {
                    x += c.width;
                    h = h.max(c.height);
                    d = d.max(c.depth);
                }
                Node::List(l) => {
                    x += l.width;
                    h = h.max(l.height - l.shift);
                    d = d.max(l.depth + l.shift);
                }
                Node::Box { width, .. } => x += width,
                Node::Kern { width, .. } => x += width,
            }
        }
        self.width = x;
        self.height = h;
        self.depth = d;
    }

    /// `List.shrink`.
    fn shrink(&mut self) {
        for c in &mut self.children {
            c.shrink();
        }
        self.level += 1;
        if self.level < NUM_SIZE_LEVELS {
            self.width *= SHRINK_FACTOR;
            self.height *= SHRINK_FACTOR;
            self.depth *= SHRINK_FACTOR;
            self.shift *= SHRINK_FACTOR;
        }
    }
}

// ------------------------------------------------------------------ parser

struct Parser<'a, 'm> {
    ctx: &'a Ctx<'m>,
    chars: Vec<char>,
    pos: usize,
}

/// Symbols mathtext knows by name that the full app's labels use.
fn named_symbol(name: &str) -> Option<char> {
    Some(match name {
        "times" => '×',
        "cdot" => '·',
        "pm" => '±',
        "odot" => '⊙',
        "circ" => '∘',
        "degree" => '°',
        "alpha" => 'α',
        "beta" => 'β',
        "mu" => 'μ',
        "nu" => 'ν',
        "lambda" => 'λ',
        "Delta" => 'Δ',
        "minus" => '\u{2212}',
        _ => return None,
    })
}

impl Parser<'_, '_> {
    fn peek(&self) -> Option<char> {
        self.chars.get(self.pos).copied()
    }

    fn skip_space(&mut self) {
        while self.peek().is_some_and(char::is_whitespace) {
            self.pos += 1;
        }
    }

    fn command(&mut self) -> String {
        let mut name = String::new();
        while let Some(c) = self.peek().filter(char::is_ascii_alphabetic) {
            name.push(c);
            self.pos += 1;
        }
        name
    }

    /// Tokens up to a closing `}` (consumed) or the end.
    fn tokens(&mut self) -> Vec<Node> {
        let mut out = Vec::new();
        loop {
            self.skip_space();
            match self.peek() {
                None => break,
                Some('}') => {
                    self.pos += 1;
                    break;
                }
                Some('^' | '_') => {
                    let node = self.subsuper(Node::Box { width: 0.0, level: 0 });
                    out.push(node);
                }
                Some(_) => {
                    let Some(nucleus) = self.placeable() else { continue };
                    self.skip_space();
                    if matches!(self.peek(), Some('^' | '_')) {
                        out.push(self.subsuper(nucleus));
                    } else {
                        out.push(nucleus);
                    }
                }
            }
        }
        out
    }

    /// A group (`{…}`, `\mathregular{…}` and the other font groups), a
    /// named symbol or a character.
    fn placeable(&mut self) -> Option<Node> {
        match self.peek()? {
            '{' => {
                self.pos += 1;
                let children = self.tokens();
                Some(Node::List(self.ctx.hlist(children, true)))
            }
            '\\' => {
                self.pos += 1;
                let name = self.command();
                if name.is_empty() {
                    // `\,` `\ ` `\$` and the like: the character itself.
                    let c = self.peek()?;
                    self.pos += 1;
                    return Some(self.ctx.char(if c == ',' { ' ' } else { c }));
                }
                if name.starts_with("math") || name == "text" {
                    self.skip_space();
                    if self.peek() == Some('{') {
                        return self.placeable();
                    }
                    return None;
                }
                match named_symbol(&name) {
                    Some(c) => Some(self.ctx.char(c)),
                    None => Some(self.ctx.char('?')),
                }
            }
            c => {
                self.pos += 1;
                Some(self.ctx.char(c))
            }
        }
    }

    /// `Parser.subsuper` for a nucleus followed by `^` and/or `_`.
    fn subsuper(&mut self, nucleus: Node) -> Node {
        let ctx = self.ctx;
        let (mut sup, mut sub) = (None, None);
        loop {
            self.skip_space();
            let op = match self.peek() {
                Some(c @ ('^' | '_')) => c,
                _ => break,
            };
            self.pos += 1;
            self.skip_space();
            let Some(arg) = self.placeable() else { break };
            if op == '^' {
                sup = Some(arg);
            } else {
                sub = Some(arg);
            }
        }
        let x_height = ctx.m.x_height(ctx.size_px, ctx.face);
        let rule_thickness = 0.75 / 12.0 * ctx.size_px;
        // The nucleus without its trailing kern, its last character as wide
        // as its advance.
        let nucleus = match nucleus {
            Node::List(mut l) => {
                let n = l.children.len();
                if n >= 2 && matches!(l.children[n - 1], Node::Kern { .. }) && matches!(l.children[n - 2], Node::Char(_)) {
                    l.children.pop();
                }
                if let Some(Node::Char(c)) = l.children.last_mut() {
                    c.width = c.metrics.advance;
                }
                ctx.hlist(l.children, false)
            }
            Node::Char(mut c) => {
                c.width = c.metrics.advance;
                ctx.hlist(vec![Node::Char(c)], false)
            }
            other => ctx.hlist(vec![other], false),
        };
        let superkern = DELTA * x_height;
        let subkern = DELTA * x_height;
        let wrap = |kern: f64, arg: Node| {
            let mut h = ctx.hlist(vec![Node::Kern { width: kern, level: 0 }, arg], true);
            h.shrink();
            h
        };
        let mut x = match (sup, sub) {
            (None, Some(sub)) => {
                let mut x = wrap(subkern, sub);
                x.shift = SUB1 * x_height;
                x
            }
            (Some(sup), None) => {
                let mut x = wrap(superkern, sup);
                x.shift = -(SUP1 * x_height);
                x
            }
            (Some(sup), Some(sub)) => {
                // A Vlist of both, which ship places as two shifted lists.
                let x = wrap(superkern, sup);
                let y = wrap(subkern, sub);
                let mut shift_up = SUP1 * x_height;
                let shift_down = SUB2 * x_height;
                let clr = 2.0 * rule_thickness - ((shift_up - x.depth) - (y.height - shift_down));
                if clr > 0.0 {
                    shift_up += clr;
                }
                let mut sx = x;
                sx.shift = -shift_up;
                let mut sy = y;
                sy.shift = shift_down;
                // Side by side at the same x: pack as a list whose second
                // member is pulled back by the first's width.
                let back = -sx.width;
                let width = sx.width.max(sy.width);
                let mut l = ctx.hlist(vec![Node::List(sx), Node::Kern { width: back, level: 0 }, Node::List(sy)], false);
                l.width = width;
                l
            }
            (None, None) => return Node::List(nucleus),
        };
        x.width += SCRIPT_SPACE * x_height;
        Node::List(ctx.hlist(vec![Node::List(nucleus), Node::List(x)], true))
    }
}

/// Lays out `text` (with `$…$` math) at `size_px` in `face`.
pub fn layout(text: &str, size_px: f64, face: Face, m: &dyn TextMeasure) -> MathLayout {
    let ctx = Ctx { m, face, size_px };
    // Split at unescaped dollars: even pieces are text, odd ones math.
    let mut pieces: Vec<String> = vec![String::new()];
    let mut prev = '\0';
    for c in text.chars() {
        if c == '$' && prev != '\\' {
            pieces.push(String::new());
        } else {
            pieces.last_mut().unwrap().push(c);
        }
        prev = c;
    }
    let mut top = Vec::new();
    for (i, piece) in pieces.iter().enumerate() {
        if i % 2 == 0 {
            let s = piece.replace("\\$", "$");
            let chars: Vec<Node> = s.chars().map(|c| ctx.char(c)).collect();
            top.push(Node::List(ctx.hlist(chars, true)));
        } else {
            let mut p = Parser { ctx: &ctx, chars: piece.chars().collect(), pos: 0 };
            let children = p.tokens();
            top.push(Node::List(ctx.hlist(children, true)));
        }
    }
    let root = ctx.hlist(top, true);
    let mut glyphs = Vec::new();
    ship(&root, 0.0, root.height, &mut glyphs, &ctx);
    // Output.to_raster.
    let fold = |init: f64, f: &dyn Fn(&PlacedGlyph) -> f64, max: bool| {
        glyphs.iter().map(f).fold(init, |a, b| if max { a.max(b) } else { a.min(b) })
    };
    let xmin = fold(0.0, &|g| g.x + g.metrics.xmin, false) - 1.0;
    let ymin = fold(0.0, &|g| g.y - g.metrics.ymax, false) - 1.0;
    let xmax = fold(0.0, &|g| g.x + g.metrics.xmax, true) + 1.0;
    let ymax = fold(0.0, &|g| g.y - g.metrics.ymin, true) + 1.0;
    let w = xmax - xmin;
    let h = ymax - ymin - root.depth;
    let d = ymax - ymin - root.height;
    MathLayout {
        glyphs,
        box_width: root.width,
        box_height: root.height,
        box_depth: root.depth,
        width: w,
        height: h + d,
        descent: d,
        raster_x0: xmin,
        raster_y0: ymin,
    }
}

/// `ship`'s `hlist_out`: glyph pen positions, y down from the top.
fn ship(list: &HList, x0: f64, baseline: f64, out: &mut Vec<PlacedGlyph>, ctx: &Ctx) {
    let mut x = x0;
    for p in &list.children {
        match p {
            Node::Char(c) => {
                let metrics = ctx.metrics(c.c, c.size_px);
                out.push(PlacedGlyph { c: c.c, size_px: c.size_px, x, y: baseline, metrics });
                x += c.width;
            }
            Node::Kern { width, .. } | Node::Box { width, .. } => x += width,
            Node::List(l) => {
                if !l.children.is_empty() {
                    ship(l, x, baseline + l.shift, out, ctx);
                }
                x += l.width;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mpl::TextExtent;

    /// Arial-like metrics: every glyph 0.5 em wide with a 0.05 em bearing.
    struct Fixed;

    impl TextMeasure for Fixed {
        fn measure(&self, _text: &str, size_px: f64, _face: Face) -> TextExtent {
            TextExtent { width: size_px, height: size_px, descent: 0.0 }
        }
        fn glyph(&self, c: char, size_px: f64, _face: Face) -> Option<GlyphMetrics> {
            if c == ' ' {
                return Some(GlyphMetrics { advance: 0.25 * size_px, ..Default::default() });
            }
            let h = 0.7 * size_px;
            Some(GlyphMetrics {
                advance: 0.5 * size_px,
                width: 0.4 * size_px,
                height: h,
                xmin: 0.05 * size_px,
                xmax: 0.45 * size_px,
                ymin: 0.0,
                ymax: h,
                iceberg: h,
            })
        }
        fn x_height(&self, size_px: f64, _face: Face) -> f64 {
            0.5 * size_px
        }
    }

    #[test]
    fn detects_math() {
        assert!(is_math_text("a$^{2}$"));
        assert!(!is_math_text("costs \\$5"));
        assert!(!is_math_text("one $ sign"));
    }

    #[test]
    fn superscript_is_raised_and_shrunk() {
        let l = layout("ab$\\mathregular{^{-1}}$", 10.0, Face::Ui, &Fixed);
        let chars: Vec<char> = l.glyphs.iter().map(|g| g.c).collect();
        assert_eq!(chars, vec!['a', 'b', '\u{2212}', '1']);
        // Text advances 5 px a glyph; the script follows after a kern of
        // delta·xHeight·0.7, its glyphs 0.7 the size and spacing.
        assert!((l.glyphs[1].x - 5.0).abs() < 1e-9);
        let kern = DELTA * 5.0 * SHRINK_FACTOR;
        assert!((l.glyphs[2].x - (10.0 + kern)).abs() < 1e-9);
        assert!((l.glyphs[3].x - l.glyphs[2].x - 3.5).abs() < 1e-9);
        assert!((l.glyphs[2].size_px - 7.0).abs() < 1e-9);
        // Raised by sup1·xHeight above the text's baseline.
        assert!((l.glyphs[0].y - l.glyphs[2].y - SUP1 * 5.0).abs() < 1e-9);
        // The box is as tall as the raised script.
        assert!((l.box_height - (0.7 * 7.0 + SUP1 * 5.0)).abs() < 1e-9);
    }
}
