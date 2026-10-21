//! Plot annotations (`src/backend/session/annotations.py`): polygons, lines
//! and text labels in plot coordinates (seconds, MHz), as projects store them.

use std::sync::atomic::{AtomicU64, Ordering};

pub const DEFAULT_ANNOTATION_COLOR: &str = "#00d4ff";
pub const DEFAULT_TEXT_FONT_SIZE: i64 = 12;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum AnnotationKind {
    Polygon,
    Line,
    Text,
}

impl AnnotationKind {
    pub fn key(self) -> &'static str {
        match self {
            AnnotationKind::Polygon => "polygon",
            AnnotationKind::Line => "line",
            AnnotationKind::Text => "text",
        }
    }

    /// `ALLOWED_KINDS`, after `str(kind).strip().lower()`.
    pub fn parse(text: &str) -> Option<Self> {
        match text.trim().to_lowercase().as_str() {
            "polygon" => Some(AnnotationKind::Polygon),
            "line" => Some(AnnotationKind::Line),
            "text" => Some(AnnotationKind::Text),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Annotation {
    pub id: String,
    pub kind: AnnotationKind,
    pub points: Vec<[f64; 2]>,
    pub text: String,
    pub color: String,
    pub line_width: f64,
    pub font_family: String,
    pub font_size: i64,
    pub font_bold: bool,
    pub font_italic: bool,
    pub visible: bool,
    pub created_at: String,
}

/// A text label's style (`_text_annotation_style_defaults` and the dialog's payload).
#[derive(Clone, Debug, PartialEq)]
pub struct TextStyle {
    pub color: String,
    pub font_family: String,
    pub font_size: i64,
    pub font_bold: bool,
    pub font_italic: bool,
}

impl Default for TextStyle {
    fn default() -> Self {
        Self {
            color: DEFAULT_ANNOTATION_COLOR.into(),
            font_family: String::new(),
            font_size: DEFAULT_TEXT_FONT_SIZE,
            font_bold: false,
            font_italic: false,
        }
    }
}

/// `_now_iso`: local time with its UTC offset, to the second.
pub fn now_iso() -> String {
    chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%:z").to_string()
}

/// `uuid4().hex`: 32 random hex digits (version 4 bits set).
pub fn new_id() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let seed =
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0)
            ^ COUNTER.fetch_add(0x9e37_79b9_7f4a_7c15, Ordering::Relaxed)
            ^ (std::process::id() as u64) << 32;
    // splitmix64
    let mut z = seed;
    let mut next = || {
        z = z.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut x = z;
        x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        x ^ (x >> 31)
    };
    let (hi, lo) = (next(), next());
    let hi = (hi & !0xf000) | 0x4000; // version 4
    let lo = (lo & !(0xc << 60)) | (0x8 << 60); // RFC 4122 variant
    format!("{hi:016x}{lo:016x}")
}

/// `_norm_int`: at least 1.
fn norm_int(v: i64) -> i64 {
    v.max(1)
}

/// `make_annotation`.
#[allow(clippy::too_many_arguments)]
pub fn make_annotation(
    kind: AnnotationKind,
    points: &[[f64; 2]],
    text: &str,
    color: &str,
    line_width: f64,
    style: Option<&TextStyle>,
    visible: bool,
) -> Annotation {
    let style = style.cloned().unwrap_or_default();
    Annotation {
        id: new_id(),
        kind,
        points: points.to_vec(),
        text: text.to_owned(),
        color: if color.is_empty() { DEFAULT_ANNOTATION_COLOR.into() } else { color.to_owned() },
        line_width,
        font_family: style.font_family,
        font_size: norm_int(style.font_size),
        font_bold: style.font_bold,
        font_italic: style.font_italic,
        visible,
        created_at: now_iso(),
    }
}

/// `normalize_annotations`: drops shapes without enough points.
pub fn normalize_annotations(items: Vec<Annotation>) -> Vec<Annotation> {
    items
        .into_iter()
        .filter(|a| match a.kind {
            AnnotationKind::Polygon | AnnotationKind::Line => a.points.len() >= 2,
            AnnotationKind::Text => !a.points.is_empty(),
        })
        .map(|mut a| {
            if a.id.is_empty() {
                a.id = new_id();
            }
            if a.color.is_empty() {
                a.color = DEFAULT_ANNOTATION_COLOR.into();
            }
            a.font_size = norm_int(a.font_size);
            if a.created_at.is_empty() {
                a.created_at = now_iso();
            }
            a
        })
        .collect()
}

/// `toggle_all_visibility`.
pub fn toggle_all_visibility(items: Vec<Annotation>, visible: bool) -> Vec<Annotation> {
    normalize_annotations(items)
        .into_iter()
        .map(|mut a| {
            a.visible = visible;
            a
        })
        .collect()
}

/// `_text_annotation_label`: how a label is listed for Edit / Move.
pub fn text_label(index: usize, ann: &Annotation) -> String {
    let [x, y] = ann.points.first().copied().unwrap_or([0.0, 0.0]);
    let mut text = ann.text.replace('\n', " ").trim().to_owned();
    if text.chars().count() > 32 {
        text = format!("{}...", text.chars().take(29).collect::<String>());
    }
    if text.is_empty() {
        text = "<blank>".into();
    }
    format!("#{} {text} @ ({x:.2}, {y:.2})", index + 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_uuid_hex() {
        let (a, b) = (new_id(), new_id());
        assert_eq!(a.len(), 32);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
        assert_eq!(&a[12..13], "4");
    }

    #[test]
    fn labels_are_shortened() {
        let ann = make_annotation(
            AnnotationKind::Text,
            &[[1.234, 56.0]],
            "a very long label that goes on and on",
            "",
            1.5,
            None,
            true,
        );
        assert_eq!(text_label(0, &ann), "#1 a very long label that goes o... @ (1.23, 56.00)");
        assert_eq!(ann.color, DEFAULT_ANNOTATION_COLOR);
    }

    #[test]
    fn short_shapes_are_dropped() {
        let line = make_annotation(AnnotationKind::Line, &[[0.0, 1.0]], "", "#fff", 1.5, None, true);
        assert!(normalize_annotations(vec![line]).is_empty());
    }
}
