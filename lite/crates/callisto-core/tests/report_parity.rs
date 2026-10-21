//! The project report laid out as ReportLab lays it out.
//!
//! `lite/parity/make_report_reference.py` generates the full app's report
//! for four projects and records the report's input (`<name>_report.json`)
//! and where ReportLab put every flowable (`<name>_report_layout.json`):
//! page, position, size, each paragraph's lines, each table's columns and
//! rows. Here Lite lays the same input out; every placement must match.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use callisto_core::report::project::{ReportFigure, ReportInput, Val, lay_out};

fn refs() -> Option<PathBuf> {
    let d = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/out/projects");
    d.join("raw_report_layout.json").exists().then_some(d)
}

/// JSON (as `json.dumps` writes it, NaN included) into ordered values.
struct Reader<'a> {
    s: &'a [u8],
    i: usize,
}

impl Reader<'_> {
    fn ws(&mut self) {
        while self.i < self.s.len() && self.s[self.i].is_ascii_whitespace() {
            self.i += 1;
        }
    }

    fn eat(&mut self, lit: &str) -> bool {
        if self.s[self.i..].starts_with(lit.as_bytes()) {
            self.i += lit.len();
            true
        } else {
            false
        }
    }

    fn string(&mut self) -> String {
        assert_eq!(self.s[self.i], b'"');
        self.i += 1;
        let mut out: Vec<u16> = Vec::new();
        let mut bytes = Vec::new();
        loop {
            let c = self.s[self.i];
            self.i += 1;
            match c {
                b'"' => break,
                b'\\' => {
                    let e = self.s[self.i];
                    self.i += 1;
                    let ch = match e {
                        b'n' => "\n".to_owned(),
                        b't' => "\t".to_owned(),
                        b'r' => "\r".to_owned(),
                        b'b' => "\u{8}".to_owned(),
                        b'f' => "\u{c}".to_owned(),
                        b'u' => {
                            let h = std::str::from_utf8(&self.s[self.i..self.i + 4]).unwrap();
                            self.i += 4;
                            out.push(u16::from_str_radix(h, 16).unwrap());
                            continue;
                        }
                        other => (other as char).to_string(),
                    };
                    bytes.extend_from_slice(&String::from_utf16_lossy(&std::mem::take(&mut out)).into_bytes());
                    bytes.extend_from_slice(ch.as_bytes());
                }
                _ => {
                    if !out.is_empty() {
                        bytes.extend_from_slice(&String::from_utf16_lossy(&std::mem::take(&mut out)).into_bytes());
                    }
                    bytes.push(c);
                }
            }
        }
        if !out.is_empty() {
            bytes.extend_from_slice(&String::from_utf16_lossy(&out).into_bytes());
        }
        String::from_utf8(bytes).unwrap()
    }

    fn value(&mut self) -> Val {
        self.ws();
        match self.s[self.i] {
            b'{' => {
                self.i += 1;
                let mut m = Vec::new();
                loop {
                    self.ws();
                    if self.eat("}") {
                        break;
                    }
                    let k = self.string();
                    self.ws();
                    assert!(self.eat(":"));
                    let v = self.value();
                    m.push((k, v));
                    self.ws();
                    self.eat(",");
                }
                Val::Map(m)
            }
            b'[' => {
                self.i += 1;
                let mut l = Vec::new();
                loop {
                    self.ws();
                    if self.eat("]") {
                        break;
                    }
                    l.push(self.value());
                    self.ws();
                    self.eat(",");
                }
                Val::List(l)
            }
            b'"' => Val::Str(self.string()),
            _ => {
                for (lit, v) in [
                    ("null", Val::None),
                    ("true", Val::Bool(true)),
                    ("false", Val::Bool(false)),
                    ("NaN", Val::Float(f64::NAN)),
                    ("Infinity", Val::Float(f64::INFINITY)),
                    ("-Infinity", Val::Float(f64::NEG_INFINITY)),
                ] {
                    if self.eat(lit) {
                        return v;
                    }
                }
                let start = self.i;
                while self.i < self.s.len() && matches!(self.s[self.i], b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9')
                {
                    self.i += 1;
                }
                let t = std::str::from_utf8(&self.s[start..self.i]).unwrap();
                if t.contains(['.', 'e', 'E']) { Val::Float(t.parse().unwrap()) } else { Val::Int(t.parse().unwrap()) }
            }
        }
    }
}

fn read(path: &Path) -> Val {
    let text = std::fs::read(path).unwrap();
    Reader { s: &text, i: 0 }.value()
}

fn pairs(v: &Val) -> Vec<(String, Val)> {
    match v {
        Val::Map(m) => m.clone(),
        _ => Vec::new(),
    }
}

fn s(v: &Val) -> String {
    match v {
        Val::Str(s) => s.clone(),
        _ => String::new(),
    }
}

/// The report input the full app built, as Lite's.
fn input(dir: &Path, name: &str, j: &Val) -> ReportInput {
    let figures = match j.get("figures") {
        Val::List(l) => l
            .iter()
            .enumerate()
            .map(|(i, f)| ReportFigure {
                title: s(f.get("title")),
                source_filename: s(f.get("source_filename")),
                caption: s(f.get("caption")),
                availability_note: s(f.get("availability_note")),
                png: match f.get("png_size") {
                    Val::List(size) => {
                        let bytes = std::fs::read(dir.join(format!("{name}_report_figure{i}.png"))).unwrap();
                        Some((Arc::new(bytes), size[0].as_f64().unwrap() as u32, size[1].as_f64().unwrap() as u32))
                    }
                    _ => None,
                },
            })
            .collect(),
        _ => Vec::new(),
    };
    ReportInput {
        title: s(j.get("title")),
        app_name: j.get("app").get("name").clone(),
        app_version: j.get("app").get("version").clone(),
        data_source: pairs(j.get("data_source")),
        processing: j.get("processing").clone(),
        rfi: j.get("rfi").clone(),
        annotations: match j.get("annotations") {
            Val::List(l) => l.clone(),
            _ => Vec::new(),
        },
        light_curve_settings: j.get("light_curve").get("settings").clone(),
        light_curve_records: j.get("light_curve").get("records").clone(),
        analysis_session: j.get("analysis_session").clone(),
        analysis_row: j.get("analysis_row").clone(),
        project_path: s(j.get("project_path")),
        fits_primary: s(j.get("fits_primary")),
        station: s(j.get("station")),
        date_obs: s(j.get("date_obs")),
        generated_at: "2026-10-21T09:00:00+05:30".into(),
        selected_header: pairs(j.get("selected_header")),
        full_header: s(j.get("full_header")),
        figures,
    }
}

#[test]
fn report_layout_matches_reportlab() {
    let Some(dir) = refs() else {
        eprintln!("note: no report references; run lite/parity/make_report_reference.py");
        return;
    };
    let mut failures = Vec::new();
    for name in ["raw", "processed", "analysis", "combined"] {
        let j = read(&dir.join(format!("{name}_report.json")));
        let r = input(&dir, name, &j);
        let (laid, written) = lay_out(&r);
        if written as f64 != j.get("figures_written").as_f64().unwrap() {
            failures.push(format!("{name}: {written} figures, the full app {:?}", j.get("figures_written")));
        }
        let Val::List(want) = read(&dir.join(format!("{name}_report_layout.json"))) else { panic!() };
        eprintln!("{name}: {} placements, {} pages, ReportLab {}", laid.placed.len(), laid.pages.len(), want.len());
        if want.len() != laid.placed.len() {
            failures.push(format!("{name}: {} flowables drawn, ReportLab {}", laid.placed.len(), want.len()));
        }
        let near = |a: f64, b: f64| (a - b).abs() < 1e-6;
        for (i, (got, w)) in laid.placed.iter().zip(&want).enumerate() {
            let f = |k: &str| w.get(k).as_f64().unwrap_or(0.0);
            let mut bad = Vec::new();
            if got.page as f64 != f("page") || got.kind != s(w.get("kind")) {
                bad.push(format!("page/kind {} {} vs {} {}", got.page, got.kind, f("page"), s(w.get("kind"))));
            }
            if !(near(got.x, f("x"))
                && near(got.y, f("y"))
                && near(got.width, f("width"))
                && near(got.height, f("height")))
            {
                bad.push(format!(
                    "at {:.4} {:.4} {:.4}x{:.4} vs {:.4} {:.4} {:.4}x{:.4}",
                    got.x,
                    got.y,
                    got.width,
                    got.height,
                    f("x"),
                    f("y"),
                    f("width"),
                    f("height")
                ));
            }
            if got.kind == "Paragraph" || got.kind == "Preformatted" {
                let lines: Vec<String> = match w.get("lines") {
                    Val::List(l) => l.iter().map(s).collect(),
                    _ => Vec::new(),
                };
                if lines != got.lines {
                    bad.push(format!("lines {:?} vs {:?}", got.lines, lines));
                }
                if got.kind == "Paragraph" && got.style != s(w.get("style")) {
                    bad.push(format!("style {} vs {}", got.style, s(w.get("style"))));
                }
            }
            if got.kind == "Table" {
                let nums = |k: &str| -> Vec<f64> {
                    match w.get(k) {
                        Val::List(l) => l.iter().map(|v| v.as_f64().unwrap()).collect(),
                        _ => Vec::new(),
                    }
                };
                let same = |a: &[f64], b: &[f64]| a.len() == b.len() && a.iter().zip(b).all(|(x, y)| near(*x, *y));
                if !same(&got.col_widths, &nums("colWidths")) || !same(&got.row_heights, &nums("rowHeights")) {
                    bad.push(format!(
                        "table {:?} {:?} vs {:?} {:?}",
                        got.col_widths,
                        got.row_heights,
                        nums("colWidths"),
                        nums("rowHeights")
                    ));
                }
            }
            if !bad.is_empty() {
                failures.push(format!("{name} #{i} ({} {}): {}", got.kind, got.style, bad.join("; ")));
                if failures.len() > 40 {
                    break;
                }
            }
        }
    }
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}
