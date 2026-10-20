//! The interface font: the same system family the full app uses.
//!
//! The full app keeps Qt's default font, which `src/ui/common/font_utils.py`
//! resolves to Segoe UI on Windows, Helvetica Neue on macOS and DejaVu Sans
//! (or a close substitute) on Linux. Lite loads that family's regular,
//! semibold and bold faces from the system, so text has the same shapes and
//! widths. egui's own fonts remain as fallbacks for symbols.

use std::collections::HashMap;
use std::sync::RwLock;

use callisto_core::mpl::Face;
use callisto_core::mpl::mathtext::GlyphMetrics;
use egui::{FontData, FontDefinitions, FontFamily, FontId};

/// Font weights the full app's stylesheets use. 500 renders as regular in
/// Qt (Segoe UI has no medium face), so it is not a separate entry.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Weight {
    Regular,
    Semibold,
    Bold,
}

impl Weight {
    fn family_name(self) -> &'static str {
        match self {
            Weight::Regular => "ui-regular",
            Weight::Semibold => "ui-semibold",
            Weight::Bold => "ui-bold",
        }
    }
}

/// A font as the full app's stylesheets specify one: pixel size and weight.
pub fn ui_font(px: f32, weight: Weight) -> FontId {
    match weight {
        Weight::Regular => FontId::proportional(px),
        w => FontId::new(px, FontFamily::Name(w.family_name().into())),
    }
}

/// A plot text font, as the canvas builds one (`_build_font`): pixel size,
/// bold, italic. Bold is Qt's 700 weight.
pub fn plot_font(px: f32, bold: bool, italic: bool) -> FontId {
    match (bold, italic) {
        (false, false) => FontId::proportional(px),
        (true, false) => ui_font(px, Weight::Bold),
        (false, true) => FontId::new(px, FontFamily::Name(ITALIC.into())),
        (true, true) => FontId::new(px, FontFamily::Name(BOLD_ITALIC.into())),
    }
}

/// [`plot_font`] in the Graph Properties font family, once
/// [`SystemFonts::install`] has installed it.
pub fn graph_font(px: f32, bold: bool, italic: bool) -> FontId {
    let name = match (bold, italic) {
        (false, false) => GRAPH[0],
        (true, false) => GRAPH[1],
        (false, true) => GRAPH[2],
        (true, true) => GRAPH[3],
    };
    FontId::new(px, FontFamily::Name(name.into()))
}

/// A text annotation's font in `family` (`QFontComboBox`), once
/// [`SystemFonts::install_with`] has installed that family; the app font
/// otherwise, as Qt falls back.
pub fn annotation_font(family: &str, px: f32, bold: bool, italic: bool) -> FontId {
    let installed = EXTRA_FAMILIES.read().ok().is_some_and(|g| g.iter().any(|f| f.eq_ignore_ascii_case(family)));
    if !installed {
        return plot_font(px, bold, italic);
    }
    FontId::new(px, FontFamily::Name(extra_family_name(family, bold, italic).into()))
}

fn extra_family_name(family: &str, bold: bool, italic: bool) -> String {
    format!("extra:{}:{}{}", family.to_lowercase(), if bold { "b" } else { "" }, if italic { "i" } else { "" })
}

/// Families installed for annotations.
static EXTRA_FAMILIES: RwLock<Vec<String>> = RwLock::new(Vec::new());

/// egui family names for the Graph Properties family: regular, bold, italic, bold italic.
const GRAPH: [&str; 4] = ["graph-regular", "graph-bold", "graph-italic", "graph-bold-italic"];

const ITALIC: &str = "ui-italic";
const BOLD_ITALIC: &str = "ui-bold-italic";

/// A font's vertical metrics as Qt computes them: design units scaled to
/// the pixel size and rounded to 1/64 px (`QFixed`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct QtMetrics {
    pub ascent: f32,
    pub descent: f32,
}

impl QtMetrics {
    /// `QFontMetricsF::height()`: ascent + descent.
    pub fn height(&self) -> f32 {
        self.ascent + self.descent
    }
}

/// Ascent and descent per pixel of size, by egui family.
static METRICS: RwLock<Option<HashMap<FontFamily, (f32, f32)>>> = RwLock::new(None);

/// A face's file and index in it, once installed.
type FaceSlot = RwLock<Option<(std::sync::Arc<Vec<u8>>, u32)>>;

/// The regular UI face's file, for glyph ink boxes ([`MplMeasure`]).
static UI_FACE: FaceSlot = RwLock::new(None);

/// The UI family's semibold and bold faces, for Qt's design-metric widths.
static UI_SEMIBOLD_FACE: RwLock<Option<(std::sync::Arc<Vec<u8>>, u32)>> = RwLock::new(None);
static UI_BOLD_FACE: RwLock<Option<(std::sync::Arc<Vec<u8>>, u32)>> = RwLock::new(None);

/// `QFontMetricsF::horizontalAdvance` as Qt measures for elision: the
/// face's design advances at `px` (unrounded), in the UI family's weight.
pub fn design_width(text: &str, px: f32, weight: Weight) -> Option<f32> {
    let slot = match weight {
        Weight::Regular => &UI_FACE,
        Weight::Semibold => &UI_SEMIBOLD_FACE,
        Weight::Bold => &UI_BOLD_FACE,
    };
    let (data, index) = slot.read().ok()?.clone().or_else(|| UI_FACE.read().ok()?.clone())?;
    let face = ttf_parser::Face::parse(&data, index).ok()?;
    let scale = px / face.units_per_em() as f32;
    Some(text.chars().map(|c| face.glyph_index(c).and_then(|g| face.glyph_hor_advance(g)).unwrap_or(0) as f32 * scale).sum())
}

/// The Origin graphs' face (Arial or a metric-compatible stand-in), for
/// glyph metrics and outlines.
static ORIGIN_FACE: RwLock<Option<(std::sync::Arc<Vec<u8>>, u32)>> = RwLock::new(None);

/// egui's family for matplotlib text in the Origin graphs' face.
const ORIGIN: &str = "mpl-origin";

/// Origin's Arial and the stand-ins `origin_font_families` accepts.
const ORIGIN_CANDIDATES: [&str; 3] = ["Arial", "Liberation Sans", "Arimo"];

/// Runs `f` with the regular UI face (for glyph outlines), once installed.
pub fn with_ui_face<R>(f: impl FnOnce(&ttf_parser::Face) -> R) -> Option<R> {
    with_mpl_face(Face::Ui, f)
}

/// `normalize_font_family` without a family list: "" for an empty name,
/// "Default" or a hidden family (a leading dot); a trailing point size
/// (`Arial 12`) dropped.
pub fn normalize_font_family(value: &str) -> String {
    let text = value.trim();
    if text.is_empty() || text.eq_ignore_ascii_case("default") || text.starts_with('.') {
        return String::new();
    }
    // `^(?P<name>.+?)\s+\d+(?:\.\d+)?$`.
    let bytes = text.as_bytes();
    let mut i = bytes.len();
    while i > 0 && (bytes[i - 1].is_ascii_digit() || bytes[i - 1] == b'.') {
        i -= 1;
    }
    let number = &text[i..];
    let valid_number = !number.is_empty()
        && !number.starts_with('.')
        && !number.ends_with('.')
        && number.matches('.').count() <= 1;
    let head = text[..i].trim_end();
    if valid_number && head.len() < text[..i].len() && !head.is_empty() {
        return head.to_owned();
    }
    text.to_owned()
}

/// The file of the face matplotlib text in `face` is drawn in: the Origin
/// face or the UI family's weight when installed, the regular UI face otherwise.
fn mpl_face_data(face: Face) -> Option<(std::sync::Arc<Vec<u8>>, u32)> {
    let slot = match face {
        Face::Ui => None,
        Face::UiSemibold => Some(&UI_SEMIBOLD_FACE),
        Face::UiBold => Some(&UI_BOLD_FACE),
        Face::Origin => Some(&ORIGIN_FACE),
    };
    slot.and_then(|s| s.read().ok().and_then(|g| g.clone())).or_else(|| UI_FACE.read().ok()?.clone())
}

/// Runs `f` with the face matplotlib text in `face` is drawn in.
pub fn with_mpl_face<R>(face: Face, f: impl FnOnce(&ttf_parser::Face) -> R) -> Option<R> {
    let (data, index) = mpl_face_data(face)?;
    let parsed = ttf_parser::Face::parse(&data, index).ok()?;
    Some(f(&parsed))
}

/// The egui font matplotlib text in `face` is laid out with.
pub fn mpl_font(face: Face, px: f32) -> FontId {
    let installed = |s: &FaceSlot| s.read().ok().is_some_and(|g| g.is_some());
    match face {
        Face::Origin if installed(&ORIGIN_FACE) => FontId::new(px, FontFamily::Name(ORIGIN.into())),
        Face::UiSemibold if installed(&UI_SEMIBOLD_FACE) => ui_font(px, Weight::Semibold),
        Face::UiBold if installed(&UI_BOLD_FACE) => ui_font(px, Weight::Bold),
        _ => FontId::proportional(px),
    }
}

/// matplotlib's text extents (`RendererAgg.get_text_width_height_descent`)
/// for text in the interface or the Origin face: the advance egui lays the
/// text out with, and the inked glyphs' height and descent; and the glyph
/// metrics mathtext lays math labels out with.
pub struct MplMeasure<'a> {
    pub ctx: &'a egui::Context,
}

/// `FT_Get_Kerning(KERNING_DEFAULT)` at matplotlib's 8× horizontal
/// hinting factor: the `kern` table's value rounded to an eighth of a pixel.
fn kern_px(face: &ttf_parser::Face, a: char, b: char, size_px: f64) -> f64 {
    let (Some(l), Some(r)) = (face.glyph_index(a), face.glyph_index(b)) else { return 0.0 };
    let Some(kern) = face.tables().kern else { return 0.0 };
    let v = kern.subtables.into_iter().filter(|t| t.horizontal && !t.variable).find_map(|t| t.glyphs_kerning(l, r));
    let scale = size_px / face.units_per_em() as f64;
    v.map(|v| (v as f64 * scale * 8.0).round() / 8.0).unwrap_or(0.0)
}

impl callisto_core::mpl::TextMeasure for MplMeasure<'_> {
    fn glyph(&self, c: char, size_px: f64, face: Face) -> Option<GlyphMetrics> {
        with_mpl_face(face, |f| {
            let id = f.glyph_index(c)?;
            let scale = size_px / f.units_per_em() as f64;
            let advance = f.glyph_hor_advance(id)? as f64 * scale;
            let Some(bb) = f.glyph_bounding_box(id) else {
                return Some(GlyphMetrics { advance, ..Default::default() });
            };
            let (xmin, xmax) = (bb.x_min as f64 * scale, bb.x_max as f64 * scale);
            let (ymin, ymax) = (bb.y_min as f64 * scale, bb.y_max as f64 * scale);
            // FreeType's hinted metrics: the vertical extent grid-fitted.
            let (top, bottom) = (ymax.ceil(), ymin.floor());
            Some(GlyphMetrics { advance, width: xmax - xmin, height: top - bottom, xmin, xmax, ymin, ymax, iceberg: top })
        })
        .flatten()
    }

    fn kerning(&self, a: char, b: char, size_px: f64, face: Face) -> f64 {
        with_mpl_face(face, |f| kern_px(f, a, b, size_px)).unwrap_or(0.0)
    }

    fn x_height(&self, size_px: f64, face: Face) -> f64 {
        // `get_xheight`: PCLT's xHeight / 64 × (size / 12 pt) × (dpi / 100).
        let pclt = with_mpl_face(face, |f| {
            let t = f.raw_face().table(ttf_parser::Tag::from_bytes(b"PCLT"))?;
            Some(u16::from_be_bytes([*t.get(10)?, *t.get(11)?]) as f64)
        })
        .flatten();
        match pclt {
            Some(x) => x / 64.0 * size_px * 0.06,
            None => self.glyph('x', size_px, face).map(|g| g.iceberg).unwrap_or(0.5 * size_px),
        }
    }

    fn measure(&self, text: &str, size_px: f64, mpl_face: Face) -> callisto_core::mpl::TextExtent {
        let galley = self.ctx.fonts_mut(|f| {
            f.layout_no_wrap(text.to_owned(), mpl_font(mpl_face, size_px as f32), egui::Color32::WHITE)
        });
        let data = mpl_face_data(mpl_face);
        let face = data.as_ref().and_then(|(d, i)| ttf_parser::Face::parse(d, *i).ok());
        let Some(face) = face else {
            let w = galley.size().x as f64;
            return callisto_core::mpl::TextExtent { width: w, height: size_px, descent: size_px * 0.22 };
        };
        let scale = size_px / face.units_per_em() as f64;
        let (mut y0, mut y1) = (f64::INFINITY, f64::NEG_INFINITY);
        let glyphs = galley.rows.first().map(|r| r.row.glyphs.as_slice()).unwrap_or(&[]);
        for g in glyphs {
            let Some(bb) = face.glyph_index(g.chr).and_then(|id| face.glyph_bounding_box(id)) else { continue };
            y0 = y0.min(bb.y_min as f64 * scale);
            y1 = y1.max(bb.y_max as f64 * scale);
        }
        let width = galley.size().x as f64;
        if !y0.is_finite() {
            return callisto_core::mpl::TextExtent { width, height: 0.0, descent: 0.0 };
        }
        // The full app's matplotlib (3.10) gives the pen's advance as the
        // width (FT2Font::get_width_height), not the ink's.
        callisto_core::mpl::TextExtent { width, height: y1 - y0, descent: -y0 }
    }
}

fn fixed64(v: f32) -> f32 {
    (v * 64.0).round() / 64.0
}

/// Qt's metrics for `font`, when its face was installed from the system.
pub fn qt_metrics(font: &FontId) -> Option<QtMetrics> {
    let guard = METRICS.read().ok()?;
    let (a, d) = *guard.as_ref()?.get(&font.family)?;
    Some(QtMetrics { ascent: fixed64(a * font.size), descent: fixed64(d * font.size) })
}

/// Ascent and descent per pixel, as the platform's text engine reads them:
/// DirectWrite on Windows uses the OS/2 "win" metrics, other systems hhea.
fn face_metrics(data: &[u8], index: u32) -> Option<(f32, f32)> {
    let face = ttf_parser::Face::parse(data, index).ok()?;
    let upem = face.units_per_em() as f32;
    let (asc, desc) = match face.tables().os2 {
        Some(os2) if cfg!(windows) => (os2.windows_ascender() as f32, os2.windows_descender() as f32),
        _ => (face.ascender() as f32, -(face.descender() as f32)),
    };
    Some((asc / upem, desc.abs() / upem))
}

fn candidates() -> &'static [&'static str] {
    if cfg!(target_os = "windows") {
        &["Segoe UI", "Arial", "Tahoma"]
    } else if cfg!(target_os = "macos") {
        &["Helvetica Neue", "Helvetica", "Arial", "Arial Rounded MT Bold"]
    } else {
        &["DejaVu Sans", "Liberation Sans", "Nimbus Sans", "Arial"]
    }
}

/// Symbol fonts for glyphs the UI font lacks (◀ ▶ ✔), as Qt falls back.
/// On Windows Qt takes Segoe UI's font-link list, which starts with
/// MS UI Gothic (the timeline's ◀ Prev / Next ▶ come from it).
fn symbol_candidates() -> &'static [&'static str] {
    if cfg!(target_os = "windows") {
        &["MS UI Gothic", "Segoe UI Symbol", "Segoe UI Emoji"]
    } else if cfg!(target_os = "macos") {
        &["Apple Symbols", "Menlo"]
    } else {
        &["DejaVu Sans", "Noto Sans Symbols2", "Noto Sans Symbols"]
    }
}

pub struct SystemFonts {
    db: fontdb::Database,
}

impl Default for SystemFonts {
    fn default() -> Self {
        Self::load()
    }
}

impl SystemFonts {
    pub fn load() -> Self {
        let mut db = fontdb::Database::new();
        db.load_system_fonts();
        Self { db }
    }

    /// Installed family names, sorted, for the Graph Properties font picker.
    pub fn families(&self) -> Vec<String> {
        let mut names: Vec<String> =
            self.db.faces().filter_map(|f| f.families.first().map(|(n, _)| n.clone())).collect();
        names.sort();
        names.dedup();
        names
    }

    fn face(&self, family: &str, weight: u16) -> Option<FontData> {
        self.styled_face(family, weight, false)
    }

    fn styled_face(&self, family: &str, weight: u16, italic: bool) -> Option<FontData> {
        let query = fontdb::Query {
            families: &[fontdb::Family::Name(family)],
            weight: fontdb::Weight(weight),
            stretch: fontdb::Stretch::Normal,
            style: if italic { fontdb::Style::Italic } else { fontdb::Style::Normal },
        };
        let id = self.db.query(&query)?;
        // fontdb falls back to the nearest family; make sure it is this one.
        let face = self.db.face(id)?;
        if !face.families.iter().any(|(n, _)| n.eq_ignore_ascii_case(family)) {
            return None;
        }
        self.db.with_face_data(id, |data, index| {
            let mut font = FontData::from_owned(data.to_vec());
            font.index = index;
            font
        })
    }

    fn metrics_of(&self, family: &str, weight: u16, italic: bool) -> Option<(f32, f32)> {
        let query = fontdb::Query {
            families: &[fontdb::Family::Name(family)],
            weight: fontdb::Weight(weight),
            stretch: fontdb::Stretch::Normal,
            style: if italic { fontdb::Style::Italic } else { fontdb::Style::Normal },
        };
        let id = self.db.query(&query)?;
        self.db.with_face_data(id, face_metrics)?
    }

    /// The UI family that will be used, as the full app picks it.
    pub fn ui_family(&self) -> Option<&'static str> {
        candidates().iter().copied().find(|f| self.face(f, 400).is_some())
    }

    /// Install the UI faces into egui. Returns the family used, if any.
    pub fn install(&self, ctx: &egui::Context) -> Option<String> {
        self.install_with(ctx, None, &[])
    }

    /// [`Self::install`], plus `graph_family`'s faces for the plot text and
    /// the `extra` families text annotations use.
    pub fn install_with(&self, ctx: &egui::Context, graph_family: Option<&str>, extra: &[String]) -> Option<String> {
        let mut defs = FontDefinitions::default();
        let egui_fallbacks = defs.families.get(&FontFamily::Proportional).cloned().unwrap_or_default();

        let symbols: Vec<String> = symbol_candidates()
            .iter()
            .filter_map(|name| {
                let font = self.face(name, 400)?;
                let key = format!("sym-{name}");
                defs.font_data.insert(key.clone(), font.into());
                Some(key)
            })
            .collect();

        let family = self.ui_family();
        for (weight, value) in [(Weight::Regular, 400u16), (Weight::Semibold, 600), (Weight::Bold, 700)] {
            let mut chain = Vec::new();
            if let Some(name) = family {
                // A missing semibold face falls back to bold, then regular.
                let fallbacks: &[u16] = match value {
                    600 => &[600, 700, 400],
                    700 => &[700, 600, 400],
                    _ => &[400],
                };
                if let Some(font) = fallbacks.iter().find_map(|w| self.face(name, *w)) {
                    let slot = match value {
                        400 => Some(&UI_FACE),
                        600 => Some(&UI_SEMIBOLD_FACE),
                        700 => Some(&UI_BOLD_FACE),
                        _ => None,
                    };
                    if let Some(slot) = slot
                        && let Ok(mut g) = slot.write()
                    {
                        *g = Some((std::sync::Arc::new(font.font.to_vec()), font.index));
                    }
                    let key = format!("{name}-{value}");
                    defs.font_data.insert(key.clone(), font.into());
                    chain.push(key);
                }
            }
            chain.extend(symbols.iter().cloned());
            chain.extend(egui_fallbacks.iter().cloned());
            match weight {
                Weight::Regular => {
                    defs.families.insert(FontFamily::Proportional, chain);
                }
                w => {
                    defs.families.insert(FontFamily::Name(w.family_name().into()), chain);
                }
            }
        }
        // Italic faces for the plot text; a family without them falls back to upright.
        for (name, weight) in [(ITALIC, 400u16), (BOLD_ITALIC, 700)] {
            let mut chain = Vec::new();
            if let Some(family) = family {
                let face = self.styled_face(family, weight, true).or_else(|| self.face(family, weight));
                if let Some(font) = face {
                    let key = format!("{family}-{weight}-italic");
                    defs.font_data.insert(key.clone(), font.into());
                    chain.push(key);
                }
            }
            chain.extend(symbols.iter().cloned());
            chain.extend(egui_fallbacks.iter().cloned());
            defs.families.insert(FontFamily::Name(name.into()), chain);
        }
        // The Graph Properties family, falling back to the UI family's faces.
        let mut graph_metrics = Vec::new();
        for (i, (weight, italic)) in [(400u16, false), (700, false), (400, true), (700, true)].into_iter().enumerate() {
            let ui_chain = match (weight, italic) {
                (400, false) => defs.families.get(&FontFamily::Proportional),
                (700, false) => defs.families.get(&FontFamily::Name(Weight::Bold.family_name().into())),
                (400, true) => defs.families.get(&FontFamily::Name(ITALIC.into())),
                _ => defs.families.get(&FontFamily::Name(BOLD_ITALIC.into())),
            }
            .cloned()
            .unwrap_or_default();
            let mut chain = Vec::new();
            if let Some(name) = graph_family {
                let face = self
                    .styled_face(name, weight, italic)
                    .or_else(|| self.styled_face(name, 400, italic))
                    .or_else(|| self.face(name, weight))
                    .or_else(|| self.face(name, 400));
                if let Some(font) = face {
                    let key = format!("graph:{name}-{weight}-{italic}");
                    defs.font_data.insert(key.clone(), font.into());
                    chain.push(key);
                    if let Some(m) = self.metrics_of(name, weight, italic).or_else(|| self.metrics_of(name, 400, false))
                    {
                        graph_metrics.push((FontFamily::Name(GRAPH[i].into()), m));
                    }
                }
            }
            chain.extend(ui_chain);
            defs.families.insert(FontFamily::Name(GRAPH[i].into()), chain);
        }
        // matplotlib text in the Origin graphs' face, falling back to the UI chain.
        let origin = ORIGIN_CANDIDATES.iter().find_map(|name| self.face(name, 400));
        let mut chain = Vec::new();
        if let Some(font) = origin {
            if let Ok(mut g) = ORIGIN_FACE.write() {
                *g = Some((std::sync::Arc::new(font.font.to_vec()), font.index));
            }
            defs.font_data.insert("mpl-origin-face".into(), font.into());
            chain.push("mpl-origin-face".to_owned());
        }
        chain.extend(defs.families.get(&FontFamily::Proportional).cloned().unwrap_or_default());
        defs.families.insert(FontFamily::Name(ORIGIN.into()), chain);
        // Annotation families, each falling back to the UI family's faces.
        let mut installed_extra = Vec::new();
        for name in extra {
            if self.face(name, 400).is_none() && self.styled_face(name, 400, true).is_none() {
                continue;
            }
            for (bold, italic) in [(false, false), (true, false), (false, true), (true, true)] {
                let weight = if bold { 700 } else { 400 };
                let ui_chain = defs
                    .families
                    .get(&plot_font(1.0, bold, italic).family)
                    .cloned()
                    .unwrap_or_default();
                let mut chain = Vec::new();
                let face = self
                    .styled_face(name, weight, italic)
                    .or_else(|| self.styled_face(name, 400, italic))
                    .or_else(|| self.face(name, weight))
                    .or_else(|| self.face(name, 400));
                let family_name = extra_family_name(name, bold, italic);
                if let Some(font) = face {
                    let key = format!("{family_name}-face");
                    defs.font_data.insert(key.clone(), font.into());
                    chain.push(key);
                    if let Some(m) = self.metrics_of(name, weight, italic).or_else(|| self.metrics_of(name, 400, false)) {
                        graph_metrics.push((FontFamily::Name(family_name.clone().into()), m));
                    }
                }
                chain.extend(ui_chain);
                defs.families.insert(FontFamily::Name(family_name.into()), chain);
            }
            installed_extra.push(name.clone());
        }
        ctx.set_fonts(defs);
        if let Ok(mut g) = EXTRA_FAMILIES.write() {
            *g = installed_extra;
        }
        if let Some(name) = family {
            let mut map = HashMap::new();
            map.extend(graph_metrics);
            for (fam, weight, italic) in [
                (FontFamily::Proportional, 400u16, false),
                (FontFamily::Name(Weight::Semibold.family_name().into()), 600, false),
                (FontFamily::Name(Weight::Bold.family_name().into()), 700, false),
                (FontFamily::Name(ITALIC.into()), 400, true),
                (FontFamily::Name(BOLD_ITALIC.into()), 700, true),
            ] {
                if let Some(m) = self.metrics_of(name, weight, italic).or_else(|| self.metrics_of(name, 400, false)) {
                    map.entry(fam).or_insert(m);
                }
            }
            for (i, ui_family) in [
                FontFamily::Proportional,
                FontFamily::Name(Weight::Bold.family_name().into()),
                FontFamily::Name(ITALIC.into()),
                FontFamily::Name(BOLD_ITALIC.into()),
            ]
            .into_iter()
            .enumerate()
            {
                if let Some(m) = map.get(&ui_family).copied() {
                    map.entry(FontFamily::Name(GRAPH[i].into())).or_insert(m);
                }
            }
            if let Ok(mut guard) = METRICS.write() {
                *guard = Some(map);
            }
        }
        family.map(str::to_owned)
    }
}
