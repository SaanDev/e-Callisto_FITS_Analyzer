//! The project report (`project_report.py`): the title page, the data
//! source, header, processing, RFI and annotation tables, the analysis and
//! Type II results, the figures and the FITS header appendix, as
//! `generate_project_report_pdf` builds them. Lite has no event context,
//! so there is no Event Story section (nor an "Event" row on the title page).

use std::sync::Arc;

use super::pdf;
use super::platypus::{self, Align, Doc, Flow, FontId, INCH, ImageFlow, Laid, PairTable, Para, Pre, Style, escape};
use crate::analysis::density::DensityModel;
use crate::radio::frequency_axis::format_g;

/// A value as the report formats it (`_fmt_value`): Python's types, maps
/// in their insertion order.
#[derive(Clone, Debug, Default, PartialEq)]
pub enum Val {
    #[default]
    None,
    Str(String),
    Bool(bool),
    Int(i64),
    Float(f64),
    Map(Vec<(String, Val)>),
    List(Vec<Val>),
}

impl Val {
    pub fn str(s: impl Into<String>) -> Val {
        Val::Str(s.into())
    }

    /// `mapping.get(key)` (None for a missing key or a non-map).
    pub fn get(&self, key: &str) -> &Val {
        match self {
            Val::Map(m) => m.iter().find(|(k, _)| k == key).map(|(_, v)| v).unwrap_or(&Val::None),
            _ => &Val::None,
        }
    }

    /// `mapping.get(key, default)`: the default only when the key is absent.
    pub fn get_or<'a>(&'a self, key: &str, default: &'a Val) -> &'a Val {
        match self {
            Val::Map(m) => m.iter().find(|(k, _)| k == key).map(|(_, v)| v).unwrap_or(default),
            _ => default,
        }
    }

    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Val::Float(f) => Some(*f),
            Val::Int(i) => Some(*i as f64),
            Val::Bool(b) => Some(*b as i64 as f64),
            Val::Str(s) => s.trim().parse().ok(),
            _ => None,
        }
    }
}

/// `_fmt_number`: `%.5g`, "Not available" for nothing or a non-finite number.
pub fn fmt_number(v: &Val) -> String {
    match v {
        Val::None => "Not available".into(),
        Val::Str(s) if s.is_empty() => "Not available".into(),
        _ => match v.as_f64() {
            Some(f) if f.is_finite() => format_g(f, 5),
            Some(_) => "Not available".into(),
            None => fmt_value(v),
        },
    }
}

/// `_fmt_value`.
pub fn fmt_value(v: &Val) -> String {
    match v {
        Val::None => "Not available".into(),
        Val::Str(s) if s.is_empty() => "Not available".into(),
        Val::Str(s) => s.clone(),
        Val::Bool(b) => if *b { "Yes" } else { "No" }.into(),
        Val::Int(i) => i.to_string(),
        Val::Float(_) => fmt_number(v),
        Val::Map(m) if m.is_empty() => "Not available".into(),
        Val::Map(m) => m.iter().map(|(k, v)| format!("{k}: {}", fmt_value(v))).collect::<Vec<_>>().join("; "),
        Val::List(l) if l.is_empty() => "Not available".into(),
        Val::List(l) => {
            let mut s = l.iter().take(8).map(fmt_value).collect::<Vec<_>>().join(", ");
            if l.len() > 8 {
                s.push_str(&format!(", ... ({} total)", l.len()));
            }
            s
        }
    }
}

/// `_safe_para_text`.
fn safe(v: &Val) -> String {
    escape(&fmt_value(v)).replace('\n', "<br/>")
}

/// `ProjectReportFigure`.
#[derive(Clone, Debug, Default)]
pub struct ReportFigure {
    pub title: String,
    pub source_filename: String,
    pub caption: String,
    /// The PNG and its size in pixels.
    pub png: Option<(Arc<Vec<u8>>, u32, u32)>,
    pub availability_note: String,
}

/// `ProjectReportInput` (without the event story).
#[derive(Clone, Debug, Default)]
pub struct ReportInput {
    pub title: String,
    pub app_name: Val,
    pub app_version: Val,
    pub data_source: Vec<(String, Val)>,
    pub processing: Val,
    pub rfi: Val,
    pub annotations: Vec<Val>,
    pub light_curve_settings: Val,
    pub light_curve_records: Val,
    pub analysis_session: Val,
    pub analysis_row: Val,
    pub project_path: String,
    pub fits_primary: String,
    pub station: String,
    pub date_obs: String,
    pub generated_at: String,
    pub selected_header: Vec<(String, Val)>,
    pub full_header: String,
    pub figures: Vec<ReportFigure>,
}

/// The report's paragraph styles (`report_styles`): ReportLab's sample
/// sheet as the app changes it, in DejaVu Sans.
pub struct Styles {
    pub normal: Arc<Style>,
    pub heading2: Arc<Style>,
    pub heading3: Arc<Style>,
    pub title: Arc<Style>,
    pub subtitle: Arc<Style>,
    pub small: Arc<Style>,
    pub key: Arc<Style>,
    pub caption: Arc<Style>,
}

fn hexc(v: u32) -> [u8; 3] {
    [(v >> 16) as u8, (v >> 8) as u8, v as u8]
}

impl Styles {
    pub fn new() -> Styles {
        let st = |name, font, size, leading, before, after, align, cjk, color: u32| {
            Arc::new(Style {
                name,
                font,
                size,
                leading,
                space_before: before,
                space_after: after,
                align,
                cjk,
                color: hexc(color),
            })
        };
        Styles {
            normal: st("Normal", FontId::Regular, 10.0, 12.0, 0.0, 0.0, Align::Left, true, 0x000000),
            heading2: st("Heading2", FontId::Bold, 14.0, 18.0, 10.0, 7.0, Align::Left, false, 0x1f2937),
            heading3: st("Heading3", FontId::Bold, 12.0, 14.0, 12.0, 6.0, Align::Left, false, 0x000000),
            title: st("ReportTitle", FontId::Bold, 22.0, 27.0, 0.0, 16.0, Align::Center, false, 0x111827),
            subtitle: st("ReportSubtitle", FontId::Regular, 10.0, 14.0, 0.0, 8.0, Align::Center, false, 0x4b5563),
            small: st("SmallText", FontId::Regular, 8.5, 11.0, 0.0, 0.0, Align::Left, true, 0x374151),
            key: st("TableKey", FontId::Bold, 8.5, 11.0, 0.0, 0.0, Align::Left, true, 0x111827),
            caption: st("Caption", FontId::Regular, 8.0, 10.0, 0.0, 8.0, Align::Left, true, 0x6b7280),
        }
    }
}

impl Default for Styles {
    fn default() -> Self {
        Self::new()
    }
}

/// `SimpleDocTemplate(pagesize=A4, ...)` with the report's margins.
pub fn report_doc() -> Doc {
    Doc { page: platypus::a4(), left: 0.55 * INCH, right: 0.55 * INCH, top: 0.68 * INCH, bottom: 0.72 * INCH }
}

/// `_pair_table`.
fn pair_table(s: &Styles, pairs: &[(String, Val)], width: f64) -> Flow {
    let mut rows: Vec<[Para; 2]> = pairs
        .iter()
        .map(|(k, v)| [Para::new(safe(&Val::str(k.clone())), &s.key), Para::new(safe(v), &s.small)])
        .collect();
    if rows.is_empty() {
        rows.push([Para::new("Status", &s.key), Para::new("Not available", &s.small)]);
    }
    Flow::Table(PairTable::new(rows, [width * 0.32, width * 0.68]))
}

/// `_fit_equation_text`.
fn fit_equation_text(fit: &Val, t0: &Val) -> Val {
    let (Some(a), Some(b)) = (fit.get("a").as_f64(), fit.get("b").as_f64().map(f64::abs)) else { return Val::None };
    let t0 = t0.as_f64().unwrap_or(0.0);
    let n = |v: f64| fmt_number(&Val::Float(v));
    if t0 != 0.0 {
        Val::Str(format!("f(x) = {} * (x - {})^-{}", n(a), n(t0), n(b)))
    } else {
        Val::Str(format!("f(x) = {} * x^-{}", n(a), n(b)))
    }
}

/// `_analysis_pairs`.
fn analysis_pairs(r: &ReportInput) -> Vec<(String, Val)> {
    let row = &r.analysis_row;
    let sess = &r.analysis_session;
    let analyzer = sess.get("analyzer");
    let fit = analyzer.get("fit_params");
    let shock = analyzer.get("shock_summary");
    let max = sess.get("max_intensity");
    let density = match analyzer.get("density_model") {
        Val::Str(s) => DensityModel::parse(s),
        _ => DensityModel::parse(""),
    };
    let p = |k: &str, v: Val| (k.to_owned(), v);
    vec![
        p("Analysis run ID", sess.get("analysis_run_id").clone()),
        p("Fit equation", fit_equation_text(fit, analyzer.get("t0_s"))),
        p("R2", fit.get_or("r2", row.get("fit_r2")).clone()),
        p("RMSE", fit.get_or("rmse", row.get("fit_rmse")).clone()),
        p("Density model", Val::str(density.label())),
        p("Fold", analyzer.get_or("fold", row.get("fold")).clone()),
        p("Fundamental", max.get_or("fundamental", row.get("fundamental")).clone()),
        p("Harmonic", max.get_or("harmonic", row.get("harmonic")).clone()),
        p("Average frequency (MHz)", shock.get_or("avg_freq_mhz", row.get("avg_freq_mhz")).clone()),
        p("Average drift (MHz/s)", shock.get_or("avg_drift_mhz_s", row.get("avg_drift_mhz_s")).clone()),
        p(
            "Initial shock speed (km/s)",
            shock.get_or("initial_shock_speed_km_s", row.get("initial_shock_speed_km_s")).clone(),
        ),
        p("Average shock speed (km/s)", shock.get_or("avg_shock_speed_km_s", row.get("avg_shock_speed_km_s")).clone()),
        p("Average shock height (Rs)", shock.get_or("avg_shock_height_rs", row.get("avg_shock_height_rs")).clone()),
    ]
}

/// `_type_ii_pairs`.
fn type_ii_pairs(session: &Val) -> Vec<(String, Val)> {
    let results = session.get("type_ii").get("results");
    [
        ("Start time (s)", "start_time_s"),
        ("End time (s)", "end_time_s"),
        ("Bandwidth (MHz)", "bandwidth_mhz"),
        ("Compression ratio", "compression_ratio"),
        ("Alfven Mach number", "alfven_mach_number"),
        ("Alfven speed (km/s)", "alfven_speed_km_s"),
        ("Magnetic field (G)", "magnetic_field_g"),
        ("Warning", "warning"),
    ]
    .into_iter()
    .map(|(label, key)| (label.to_owned(), results.get(key).clone()))
    .collect()
}

/// `_fit_image`: the PNG at its pixel size in points, scaled down to fit.
fn fit_image(png: &Arc<Vec<u8>>, w: u32, h: u32, max_w: f64, max_h: f64) -> Flow {
    let (w, h) = (w.max(1) as f64, h.max(1) as f64);
    let scale = (max_w / w).min(max_h / h).min(1.0);
    Flow::Image(ImageFlow { png: png.clone(), width: w * scale, height: h * scale })
}

/// The report's story (`generate_project_report_pdf` up to `doc.build`).
pub fn story(r: &ReportInput, s: &Styles, doc: &Doc) -> (Vec<Flow>, usize) {
    let width = doc.width();
    let mut f: Vec<Flow> = Vec::new();
    let title = if r.title.is_empty() { "e-CALLISTO Project Report".to_owned() } else { r.title.clone() };
    let mut data_source = r.data_source.clone();
    for (k, v) in &mut data_source {
        if k == "combined_mode" && *v == Val::str("time_frequency") {
            *v = Val::str("time + frequency");
        }
    }
    let ds = Val::Map(data_source);

    f.push(Flow::Spacer(0.6 * INCH));
    f.push(Flow::Para(Para::new(escape(&title), &s.title)));
    let mut subtitle = vec![
        format!("Generated at: {}", escape(&r.generated_at)),
        format!("Application: {} {}", escape(&fmt_value(&r.app_name)), escape(&fmt_value(&r.app_version))),
    ];
    if !r.project_path.is_empty() {
        subtitle.push(format!("Project: {}", escape(&r.project_path)));
    }
    if !r.fits_primary.is_empty() {
        subtitle.push(format!("FITS primary: {}", escape(&r.fits_primary)));
    }
    f.push(Flow::Para(Para::new(subtitle.join("<br/>"), &s.subtitle)));
    f.push(Flow::Spacer(0.25 * INCH));
    let p = |k: &str, v: Val| (k.to_owned(), v);
    f.push(pair_table(
        s,
        &[
            p("Station", Val::str(r.station.clone())),
            p("Observation date", Val::str(r.date_obs.clone())),
            p("Current plot", r.processing.get("plot_type").clone()),
            p("Source file", ds.get("filename").clone()),
        ],
        width,
    ));
    f.push(Flow::PageBreak);

    let section = |f: &mut Vec<Flow>, t: &str| f.push(Flow::Para(Para::new(escape(t), &s.heading2)));
    let available = |m: &Val, keys: &[(&str, &str)]| -> Vec<(String, Val)> {
        keys.iter().map(|(l, k)| (l.to_string(), m.get(k).clone())).collect()
    };

    section(&mut f, "Data Source");
    let mut pairs = available(
        &ds,
        &[
            ("Filename", "filename"),
            ("Combined", "is_combined"),
            ("Combined mode", "combined_mode"),
            ("Array shape", "shape"),
            ("Frequency range (MHz)", "freq_range_mhz"),
            ("Time range (s)", "time_range_s"),
            ("Sources", "sources"),
        ],
    );
    pairs.push(p("Project path", Val::str(r.project_path.clone())));
    pairs.push(p("FITS primary", Val::str(r.fits_primary.clone())));
    f.push(pair_table(s, &pairs, width));
    f.push(Flow::Spacer(0.12 * INCH));

    section(&mut f, "Selected FITS Header");
    f.push(pair_table(s, &r.selected_header, width));
    f.push(Flow::Spacer(0.12 * INCH));

    section(&mut f, "Processing");
    let processing = available(
        &r.processing,
        &[
            ("Plot type", "plot_type"),
            ("Units in dB", "use_db"),
            ("Use UT axis", "use_utc"),
            ("Colormap", "cmap"),
            ("Noise clip low", "noise_clip_low"),
            ("Noise clip high", "noise_clip_high"),
            ("Noise clip scale", "noise_clip_scale"),
            ("Active preset", "active_preset"),
        ],
    );
    f.push(pair_table(s, &processing, width));
    f.push(Flow::Spacer(0.12 * INCH));

    section(&mut f, "RFI, Annotations, And Light Curves");
    f.push(pair_table(
        s,
        &[
            p("RFI settings", r.rfi.clone()),
            p("Annotation count", Val::Int(r.annotations.len() as i64)),
            p("Annotations", Val::List(r.annotations.clone())),
            p("Light curve settings", r.light_curve_settings.clone()),
            p("Light curve records", r.light_curve_records.clone()),
        ],
        width,
    ));
    f.push(Flow::PageBreak);

    section(&mut f, "Analysis Summary");
    f.push(pair_table(s, &analysis_pairs(r), width));
    f.push(Flow::Spacer(0.12 * INCH));
    section(&mut f, "Type II Results");
    f.push(pair_table(s, &type_ii_pairs(&r.analysis_session), width));
    f.push(Flow::Spacer(0.12 * INCH));

    let mut written = 0;
    if r.figures.is_empty() {
        section(&mut f, "Figures");
        f.push(Flow::Para(Para::new("Not available", &s.normal)));
    }
    for (i, fig) in r.figures.iter().enumerate() {
        let mut block = Vec::new();
        if i == 0 {
            block.push(Flow::Para(Para::new("Figures", &s.heading2)));
        }
        let t = if fig.title.is_empty() { format!("Figure {}", i + 1) } else { fig.title.clone() };
        block.push(Flow::Para(Para::new(escape(&t), &s.heading3)));
        match &fig.png {
            Some((png, w, h)) => {
                block.push(fit_image(png, *w, *h, width, 4.65 * INCH));
                written += 1;
            }
            None => {
                let note = if fig.availability_note.is_empty() { "Not available" } else { &fig.availability_note };
                block.push(Flow::Para(Para::new(escape(note), &s.small)));
            }
        }
        if !fig.source_filename.is_empty() {
            block.push(Flow::Para(Para::new(format!("Source: {}", escape(&fig.source_filename)), &s.caption)));
        }
        if !fig.caption.is_empty() {
            block.push(Flow::Para(Para::new(escape(&fig.caption), &s.caption)));
        }
        block.push(Flow::Spacer(0.1 * INCH));
        f.push(Flow::KeepTogether(block));
    }

    f.push(Flow::PageBreak);
    section(&mut f, "Full FITS Header Appendix");
    let header = r.full_header.trim();
    if !header.is_empty() {
        let cut: String = header.chars().take(18000).collect();
        f.push(Flow::Pre(Pre::new(&cut, &s.small)));
        if header.chars().count() > 18000 {
            f.push(Flow::Para(Para::new("Header appendix was truncated for PDF readability.", &s.caption)));
        }
    } else {
        f.push(Flow::Para(Para::new("Not available", &s.normal)));
    }
    (f, written)
}

/// `_draw_header_footer`, on every page.
fn header_footer(laid: &mut Laid, doc: &Doc, title: &str) {
    let grey = hexc(0xd7dce3);
    let ink = hexc(0x6b7280);
    let (pw, ph) = doc.page;
    let short: String = title.chars().take(90).collect();
    for (i, page) in laid.pages.iter_mut().enumerate() {
        let n = i + 1;
        let mut deco = vec![platypus::Draw::Line {
            x0: doc.left,
            y0: 0.55 * INCH,
            x1: pw - doc.right,
            y1: 0.55 * INCH,
            width: 0.4,
            color: grey,
        }];
        if n > 1 {
            deco.push(platypus::Draw::Text {
                x: doc.left,
                y: ph - 0.45 * INCH,
                text: short.clone(),
                font: FontId::Helvetica,
                size: 8.0,
                color: ink,
            });
        }
        let label = format!("Page {n}");
        let w = platypus::string_width(&label, FontId::Helvetica, 8.0);
        deco.push(platypus::Draw::Text {
            x: pw - doc.right - w,
            y: 0.35 * INCH,
            text: label,
            font: FontId::Helvetica,
            size: 8.0,
            color: ink,
        });
        // Drawn when the page begins, under its content.
        deco.append(page);
        *page = deco;
    }
}

/// The report laid out (pages and the record of where things went), and
/// how many figures it embeds.
pub fn lay_out(r: &ReportInput) -> (Laid, usize) {
    let doc = report_doc();
    let styles = Styles::new();
    let (story, written) = story(r, &styles, &doc);
    let mut laid = platypus::build(&doc, story);
    let title = if r.title.is_empty() { "e-CALLISTO Project Report" } else { &r.title };
    header_footer(&mut laid, &doc, title);
    (laid, written)
}

/// `ReportGenerationCancelled`.
#[derive(Debug)]
pub struct Cancelled;

impl std::fmt::Display for Cancelled {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Project report generation was cancelled.")
    }
}

impl std::error::Error for Cancelled {}

/// `ProjectReportResult`.
#[derive(Clone, Debug)]
pub struct ReportResult {
    pub path: std::path::PathBuf,
    pub file_size: u64,
    pub figures_written: usize,
}

/// `generate_project_report_pdf(output_path, report, progress_cb=…)`: the
/// report written to `path` (with a `.pdf` suffix), its progress reported
/// as the full app reports it; `progress` returning false cancels.
pub fn generate_project_report_pdf(
    path: &std::path::Path,
    r: &ReportInput,
    progress: &mut dyn FnMut(i32, &str) -> bool,
) -> anyhow::Result<ReportResult> {
    let mut emit = |v: i32, t: &str| if progress(v, t) { Ok(()) } else { Err(anyhow::Error::new(Cancelled)) };
    let mut path = path.to_path_buf();
    if !path.extension().is_some_and(|e| e.eq_ignore_ascii_case("pdf")) {
        path.set_extension("pdf");
    }
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::create_dir_all(dir)?;
    }
    emit(5, "Preparing report layout...")?;
    emit(18, "Adding data and processing sections...")?;
    emit(34, "Adding analysis sections...")?;
    let n = r.figures.len().max(1);
    for (i, f) in r.figures.iter().enumerate() {
        emit(40 + (35 * (i + 1) / n) as i32, &format!("Adding figure: {}", f.title))?;
    }
    emit(80, "Adding appendix...")?;
    emit(92, "Writing PDF...")?;
    let (bytes, written) = report_pdf(r)?;
    std::fs::write(&path, &bytes)?;
    emit(100, "Project report complete.")?;
    Ok(ReportResult { file_size: bytes.len() as u64, path, figures_written: written })
}

/// The report as PDF bytes, and how many figures it embeds.
pub fn report_pdf(r: &ReportInput) -> anyhow::Result<(Vec<u8>, usize)> {
    let (laid, written) = lay_out(r);
    let title = if r.title.is_empty() { "e-CALLISTO Project Report".to_owned() } else { r.title.clone() };
    let author = match &r.app_name {
        Val::Str(s) if !s.is_empty() => s.clone(),
        _ => "e-CALLISTO FITS Analyzer".into(),
    };
    let bytes = pdf::write(&laid.pages, report_doc().page, &title, &author)?;
    Ok((bytes, written))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn values_like_the_full_app() {
        assert_eq!(fmt_value(&Val::Float(899.75)), "899.75");
        assert_eq!(fmt_value(&Val::Float(-2.686712)), "-2.6867");
        assert_eq!(fmt_value(&Val::List(vec![Val::Int(200), Val::Int(3600)])), "200, 3600");
        assert_eq!(
            fmt_value(&Val::Map(vec![("a".into(), Val::Bool(true)), ("b".into(), Val::None)])),
            "a: Yes; b: Not available"
        );
        let many = Val::List((0..10).map(Val::Int).collect());
        assert_eq!(fmt_value(&many), "0, 1, 2, 3, 4, 5, 6, 7, ... (10 total)");
    }
}
