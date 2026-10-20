//! The rich text `QLabel`s of the analysis windows show: plain text with
//! `<b>` runs and `<sup>` superscripts, laid out as Qt's text engine lays
//! them out (a superscript at two thirds of the pixel size, its baseline
//! raised by half that font's height).

use egui::{Color32, Pos2, Ui, pos2};

use crate::fonts::{Weight, ui_font};

/// One run of text and how it is set.
#[derive(Clone, Debug, PartialEq)]
pub struct Run {
    pub text: String,
    pub bold: bool,
    pub sup: bool,
}

/// The runs of `html` (`<b>`, `<sup>` and entities `&lt;` `&gt;` `&amp;`),
/// white space collapsed as HTML collapses it: a run of spaces is one
/// space, across tags too, and none leads the text.
pub fn parse(html: &str) -> Vec<Run> {
    let mut runs = parse_raw(html);
    let mut after_space = true;
    for r in &mut runs {
        let mut t = String::with_capacity(r.text.len());
        for c in r.text.chars() {
            if c == ' ' || c == '\t' || c == '\n' || c == '\r' {
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
    runs.retain(|r| !r.text.is_empty());
    runs
}

fn parse_raw(html: &str) -> Vec<Run> {
    let mut runs: Vec<Run> = Vec::new();
    let (mut bold, mut sup) = (0, 0);
    let mut rest = html;
    let push = |text: &str, bold: bool, sup: bool, runs: &mut Vec<Run>| {
        if text.is_empty() {
            return;
        }
        let text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", "\u{a0}").replace("&amp;", "&");
        match runs.last_mut() {
            Some(r) if r.bold == bold && r.sup == sup => r.text.push_str(&text),
            _ => runs.push(Run { text, bold, sup }),
        }
    };
    while let Some(i) = rest.find('<') {
        push(&rest[..i], bold > 0, sup > 0, &mut runs);
        let Some(j) = rest[i..].find('>') else {
            push(&rest[i..], bold > 0, sup > 0, &mut runs);
            rest = "";
            break;
        };
        let tag = rest[i + 1..i + j].trim().to_lowercase();
        match tag.as_str() {
            "b" | "strong" => bold += 1,
            "/b" | "/strong" => bold = (bold - 1).max(0),
            "sup" => sup += 1,
            "/sup" => sup = (sup - 1).max(0),
            _ => {}
        }
        rest = &rest[i + j + 1..];
    }
    push(rest, bold > 0, sup > 0, &mut runs);
    runs
}

/// The plain text of `html`, as a `QLabel` holding it would give `text()` without tags.
pub fn plain(html: &str) -> String {
    parse(html).into_iter().map(|r| r.text).collect()
}

fn font(px: f32, bold: bool, sup: bool) -> egui::FontId {
    // `(pixelSize * 2) / 3` for a superscript.
    let px = if sup { ((px as i32 * 2) / 3) as f32 } else { px };
    ui_font(px, if bold { Weight::Bold } else { Weight::Regular })
}

/// Draws `html` with its baseline at `baseline` from `x`; returns its width.
pub fn draw(ui: &Ui, x: f32, baseline: f32, html: &str, px: f32, color: Color32) -> f32 {
    let mut pen = x;
    for run in parse(html) {
        let f = font(px, run.bold, run.sup);
        let galley = ui.ctx().fonts_mut(|fonts| fonts.layout_no_wrap(run.text.clone(), f.clone(), color));
        let ascent = galley.rows.first().and_then(|r| r.row.glyphs.first().map(|g| r.pos.y + g.pos.y)).unwrap_or(galley.size().y * 0.8);
        let mut base = baseline;
        if run.sup {
            // itemBaseLine -= (ascent + descent) / 2 of the superscript's font.
            let height = crate::fonts::qt_metrics(&f).map(|m| m.height()).unwrap_or(galley.size().y);
            base -= height / 2.0;
        }
        let ppp = ui.ctx().pixels_per_point();
        let top = pos2((pen * ppp).round() / ppp, ((base - ascent) * ppp).round() / ppp);
        let w = galley.size().x;
        ui.painter().galley(top, galley, color);
        pen += w;
    }
    pen - x
}

/// The width `html` takes.
pub fn width(ui: &Ui, html: &str, px: f32) -> f32 {
    parse(html)
        .into_iter()
        .map(|run| ui.ctx().fonts_mut(|f| f.layout_no_wrap(run.text, font(px, run.bold, run.sup), Color32::WHITE).size().x))
        .sum()
}

/// A one-line label in `rect`, vertically centred like `QLabel`'s
/// `AlignLeft | AlignVCenter`, at `px` (the label's font).
pub fn label(ui: &Ui, rect: egui::Rect, html: &str, px: f32, color: Color32) {
    let m = crate::fonts::qt_metrics(&ui_font(px, Weight::Regular));
    let (asc, h) = m.map(|m| (m.ascent, m.height())).unwrap_or((px * 1.06, px * 1.33));
    let top = rect.top() + ((rect.height() - h) / 2.0).floor().max(0.0);
    draw(ui, rect.left(), top + asc, html, px, color);
}

/// `Pos2` of the pen after `html` drawn at `at` (for following text).
pub fn end(ui: &Ui, at: Pos2, html: &str, px: f32) -> Pos2 {
    pos2(at.x + width(ui, html, px), at.y)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runs_follow_the_tags() {
        let r = parse("Average Frequency: <b>52.90 ± 0.36</b> MHz");
        assert_eq!(r.len(), 3);
        assert!(r[1].bold && !r[0].bold && r[2].text == " MHz");
        let r = parse("<b>f(x) = 514.62 · x<sup>-0.41</sup></b>");
        assert_eq!(r[1], Run { text: "-0.41".into(), bold: true, sup: true });
        assert_eq!(plain("<b>Fit Metrics:</b>"), "Fit Metrics:");
        assert_eq!(plain("Average Frequency: <b> \u{b1} </b> MHz"), "Average Frequency: \u{b1} MHz");
    }
}
