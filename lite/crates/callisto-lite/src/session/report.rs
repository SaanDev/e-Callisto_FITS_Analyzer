//! File → Generate Project Report (`_build_project_report_input` and the
//! helpers it calls): the report's tables from the provenance context, the
//! analysis session and log row, the header, and its figures, drawn as the
//! full app draws them (`REPORT_FIGURE_DPI`, trimmed to the drawing). Lite
//! has no event context, so the report tells no event story.

use callisto_core::pyjson::Json;
use callisto_core::radio::frequency_axis::format_g;
use callisto_core::report::project::{ReportFigure, ReportInput, Val};

use super::*;
use crate::analysis::type_ii_window::{Spectrum, TypeIIWindow};

/// `REPORT_FIGURE_DPI`.
pub const REPORT_FIGURE_DPI: f64 = 220.0;

/// A JSON value as the report's (Python-like) value; objects keep their
/// (sorted) key order.
fn val(j: &Json) -> Val {
    match j {
        Json::Null => Val::None,
        Json::Bool(b) => Val::Bool(*b),
        Json::Int(i) => Val::Int(*i),
        Json::Float(f) => Val::Float(*f),
        Json::Str(s) => Val::Str(s.clone()),
        Json::Array(a) => Val::List(a.iter().map(val).collect()),
        Json::Object(o) => Val::Map(o.iter().map(|(k, v)| (k.clone(), val(v))).collect()),
    }
}

/// An object's values in the order the full app builds the dict, keys it
/// does not name after them.
fn ordered(j: &Json, keys: &[&str]) -> Val {
    let Json::Object(o) = j else { return val(j) };
    let mut m: Vec<(String, Val)> = keys.iter().filter_map(|k| o.get(*k).map(|v| (k.to_string(), val(v)))).collect();
    m.extend(o.iter().filter(|(k, _)| !keys.contains(&k.as_str())).map(|(k, v)| (k.clone(), val(v))));
    Val::Map(m)
}

fn floats(v: &[f64]) -> Val {
    Val::List(v.iter().map(|x| Val::Float(*x)).collect())
}

/// `np.asarray(value, dtype=float).reshape(-1)`: None is one NaN.
fn as_floats(v: &Val) -> Vec<f64> {
    match v {
        Val::List(l) => l.iter().map(|x| x.as_f64().unwrap_or(f64::NAN)).collect(),
        Val::None => vec![f64::NAN],
        other => vec![other.as_f64().unwrap_or(f64::NAN)],
    }
}

/// `_png_is_blank_or_black`.
fn blank_or_black(rgba: &[u8]) -> bool {
    let visible: Vec<&[u8; 4]> = rgba.as_chunks::<4>().0.iter().filter(|p| p[3] > 4).collect();
    if visible.is_empty() {
        return true;
    }
    let values: Vec<f64> = visible.iter().flat_map(|p| p[..3].iter().map(|v| *v as f64)).collect();
    let n = values.len() as f64;
    let mean = values.iter().sum::<f64>() / n;
    let spread = (values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n).sqrt();
    let max = values.iter().copied().fold(0.0, f64::max);
    max <= 12.0 || (spread < 1.0 && (mean <= 12.0 || mean >= 243.0))
}

/// `_valid_report_display_levels`.
fn valid_levels(levels: Option<(f64, f64)>, display: Option<&[f32]>) -> Option<(f64, f64)> {
    let (lo, hi) = levels?;
    if !(lo.is_finite() && hi.is_finite()) || hi <= lo {
        return None;
    }
    let Some((dmin, dmax)) = display.and_then(finite_data_limits) else { return Some((lo, hi)) };
    if !(dmin.is_finite() && dmax.is_finite()) || dmax <= dmin {
        return Some((lo, hi));
    }
    if hi.min(dmax) - lo.max(dmin) <= 0.0 || (hi - lo) < ((dmax - dmin) * 0.005).max(1e-9) {
        return None;
    }
    Some((lo, hi))
}

/// `_robust_report_display_levels`.
fn robust_levels(display: &[f32]) -> Option<(f64, f64)> {
    let (dmin, dmax) = finite_data_limits(display)?;
    if dmax <= dmin {
        return None;
    }
    let span = dmax - dmin;
    for (lo_pct, hi_pct) in [(2.0, 99.5), (1.0, 99.9), (0.0, 100.0)] {
        let Some((lo, hi)) = percentile_data_limits(display, lo_pct, hi_pct) else { continue };
        if !(lo.is_finite() && hi.is_finite()) || hi <= lo {
            continue;
        }
        if (hi - lo) < (span * 0.002).max(1e-9) && hi_pct < 100.0 {
            continue;
        }
        return Some((lo, hi));
    }
    Some((dmin, dmax))
}

impl Session {
    /// `_project_report_source_paths`.
    pub fn project_report_source_paths(&self) -> Vec<String> {
        let d = self.data.as_ref();
        let sources: Vec<String> = d
            .map(|d| d.combined_sources.iter().filter(|s| !s.trim().is_empty()).cloned().collect())
            .unwrap_or_default();
        if !sources.is_empty() {
            return sources;
        }
        if let Some(p) = d.and_then(|d| d.source_path.clone()) {
            return vec![p];
        }
        if !self.filename().is_empty() { vec![self.filename().to_owned()] } else { Vec::new() }
    }

    /// `_project_report_source_label`.
    pub fn project_report_source_label(&self) -> String {
        let names: Vec<String> = self
            .project_report_source_paths()
            .iter()
            .map(|p| basename(p).to_owned())
            .filter(|n| !n.is_empty())
            .collect();
        if names.is_empty() { self.filename().to_owned() } else { names.join(", ") }
    }

    /// `_project_report_default_stem`.
    pub fn project_report_default_stem(&self, project: Option<&Path>) -> String {
        let stem_of = |p: &str| {
            let base = basename(p);
            match base.rfind('.') {
                Some(i) if i > 0 => base[..i].to_owned(),
                _ => base.to_owned(),
            }
        };
        let mut stems: Vec<String> =
            self.project_report_source_paths().iter().map(|p| stem_of(p)).filter(|s| !s.is_empty()).collect();
        if stems.is_empty()
            && let Some(p) = project
        {
            let s = stem_of(&p.to_string_lossy());
            stems = vec![if s.is_empty() { "project".into() } else { s }];
        }
        if stems.is_empty() {
            stems = vec!["project".into()];
        }
        let stem = match stems.len() {
            1 => stems[0].clone(),
            2 | 3 => stems.join("_"),
            n => format!("{}_to_{}_{n}files", stems[0], stems[n - 1]),
        };
        let mut stem = crate::app::sanitize_export_stem(&stem);
        if stem.is_empty() {
            stem = "project".into();
        }
        if stem.chars().count() > 120 {
            let cut: String = stem.chars().take(120).collect();
            let cut = cut.trim_end_matches(['.', '_', '-']).to_owned();
            stem = if cut.is_empty() { "project".into() } else { cut };
        }
        stem
    }

    /// `_project_report_default_path`.
    pub fn project_report_default_path(&self, project: Option<&Path>) -> PathBuf {
        let dir = project.and_then(|p| p.parent().map(Path::to_path_buf)).or_else(|| {
            self.data
                .as_ref()
                .and_then(|d| d.source_path.as_ref())
                .and_then(|p| Path::new(p).parent().map(Path::to_path_buf))
        });
        let name = format!("{}_project_report.pdf", self.project_report_default_stem(project));
        match dir {
            Some(d) if !d.as_os_str().is_empty() => d.join(name),
            _ => PathBuf::from(name),
        }
    }

    /// `_project_report_figure`.
    fn report_figure(
        &self,
        title: &str,
        png: Option<(Vec<u8>, u32, u32)>,
        caption: &str,
        unavailable: &str,
    ) -> ReportFigure {
        let note = if png.is_some() { String::new() } else { unavailable.to_owned() };
        ReportFigure {
            title: title.to_owned(),
            source_filename: self.project_report_source_label(),
            caption: caption.to_owned(),
            png: png.map(|(b, w, h)| (Arc::new(b), w, h)),
            availability_note: note,
        }
    }

    /// `_report_levels_for_spectrum_data`.
    fn report_levels(&self, data: &Arc<Array2>, display: &Arc<Array2>, plot_type: &str) -> Option<(f64, f64)> {
        if normalize_plot_type(plot_type) == "Isolated Burst"
            && let Some(l) = valid_levels(finite_data_limits(&display.data), Some(&display.data))
        {
            return Some(l);
        }
        // `_current_levels_for_report_data`: the canvas's, for the data it shows.
        let shown = self.current_plot_source.as_ref().or(self.current_product().as_ref()).cloned();
        let same = shown.is_some_and(|s| {
            Arc::ptr_eq(&s, data)
                || (s.rows == data.rows
                    && s.cols == data.cols
                    && s.data.iter().zip(&data.data).all(|(a, b)| a == b || (a.is_nan() && b.is_nan())))
        });
        if same {
            let current = valid_levels(self.canvas.levels(), None);
            if let Some(l) = valid_levels(current, Some(&display.data)) {
                return Some(l);
            }
        }
        robust_levels(&display.data)
    }

    /// `_render_original_dynamic_spectrum_export_png`.
    fn report_spectrum_png(
        &self,
        ctx: &egui::Context,
        data: &Arc<Array2>,
        plot_type: &str,
        light_curves: Vec<callisto_core::mpl::spectrum_figures::LightCurve>,
    ) -> Option<(Vec<u8>, u32, u32)> {
        let display = self.intensity_for_display(data);
        let levels = self.report_levels(data, &display, plot_type);
        let graph =
            self.origin_spectrum_graph(data, plot_type, self.canvas.view(), levels, light_curves, Vec::new(), None)?;
        let fig = callisto_core::mpl::spectrum_figures::dynamic_spectrum_figure(&graph);
        let (png, w, h, rgba) = crate::figure_export::png_bytes(ctx, &fig, REPORT_FIGURE_DPI, 0.08).ok()?;
        (!blank_or_black(&rgba)).then_some((png, w, h))
    }

    /// `_capture_max_intensity_fit_report_figure`.
    fn max_intensity_fit_figure(&self, ctx: &egui::Context, session: &Val) -> ReportFigure {
        use callisto_core::mpl::analysis_figures::{FitGraph, fit_graph_figure, power_law_label_digits};
        let max = session.get("max_intensity");
        let fit = session.get("analyzer").get("fit_params");
        let (times, x_label) = match max.get("time_seconds") {
            Val::None => (max.get("time_channels"), "Time channel"),
            t => (t, "Time [s]"),
        };
        let (x, y) = (as_floats(times), as_floats(max.get("freqs")));
        let n = x.len().min(y.len());
        if n == 0 {
            return self.report_figure(
                "Maximum Intensity Fit",
                None,
                "",
                "Maximum-intensity analysis is not available.",
            );
        }
        let (x, y) = (&x[..n], &y[..n]);
        let mut caption = Vec::new();
        let (mut fit_x, mut fit_y, mut label) = (Vec::new(), Vec::new(), String::new());
        if let (Some(a), Some(b)) = (fit.get("a").as_f64(), fit.get("b").as_f64().map(f64::abs)) {
            let ok: Vec<f64> = x
                .iter()
                .zip(y)
                .filter(|(t, f)| t.is_finite() && f.is_finite() && **t > 0.0 && **f > 0.0)
                .map(|(t, _)| *t)
                .collect();
            if ok.len() >= 2 {
                let lo = ok.iter().copied().fold(f64::INFINITY, f64::min);
                let hi = ok.iter().copied().fold(f64::NEG_INFINITY, f64::max);
                fit_x = callisto_core::radio::numeric::linspace(lo, hi, 400);
                fit_y = fit_x.iter().map(|t| a * t.powf(-b)).collect();
                label = power_law_label_digits(a, b, "Best fit", "f", "x", 3);
                caption.push(format!("Fit: f = {} * x^-{}", format_g(a, 5), format_g(b, 5)));
            }
        }
        let graph = FitGraph {
            x,
            y,
            fit_x: &fit_x,
            fit_y: &fit_y,
            data_label: "Maximum intensity",
            fit_label: &label,
            title: "Maximum Intensity Fit",
            x_label,
            y_label: "Frequency [MHz]",
        };
        let fig = fit_graph_figure(&graph, self.origin_graph_text());
        let png =
            crate::figure_export::png_bytes(ctx, &fig, REPORT_FIGURE_DPI, 0.08).ok().map(|(p, w, h, _)| (p, w, h));
        if let Some(r2) = fit.get("r2").as_f64() {
            caption.push(format!("R2: {}", format_g(r2, 4)));
        }
        if let Some(rmse) = fit.get("rmse").as_f64() {
            caption.push(format!("RMSE: {}", format_g(rmse, 4)));
        }
        let caption = if caption.is_empty() { "Maximum-intensity trace.".to_owned() } else { caption.join(", ") };
        self.report_figure("Maximum Intensity Fit", png, &caption, "Maximum-intensity fit could not be rendered.")
    }

    /// `_capture_type_ii_report_figure`: the open window's graph, or one
    /// drawn by a window made for it.
    fn type_ii_figure(&self, ctx: &egui::Context, session: &Val, window: Option<&TypeIIWindow>) -> ReportFigure {
        let t = session.get("type_ii");
        let has_points = |b: &Val| as_floats(b.get("time_seconds")).len().min(as_floats(b.get("freqs")).len()) > 0;
        if !(has_points(t.get("upper")) || has_points(t.get("lower"))) {
            return self.report_figure(
                "Type II Band Splitting",
                None,
                "",
                "Type II band-splitting analysis is not available.",
            );
        }
        let made;
        let window = match window {
            Some(w) => w,
            None => {
                let Some(d) = &self.data else {
                    return self.report_figure(
                        "Type II Band Splitting",
                        None,
                        "",
                        "Type II band-splitting plot data is not available.",
                    );
                };
                let Some(spectrum) = self
                    .noise_reduced_original
                    .clone()
                    .or_else(|| self.noise_reduced.clone())
                    .or_else(|| Some(d.raw.clone()))
                else {
                    return self.report_figure(
                        "Type II Band Splitting",
                        None,
                        "",
                        "Type II band-splitting plot data is not available.",
                    );
                };
                let spectrum = Spectrum {
                    display: self.intensity_for_display(&spectrum),
                    freqs: d.freqs.to_vec(),
                    time: d.time.to_vec(),
                    unit: self.intensity_unit_label().to_owned(),
                    cmap: Colormap::by_name(&self.graph.cmap),
                    frequency_step_mhz: d.frequency_step_mhz,
                    display_levels: self.threshold_display_levels(),
                };
                let candidate = self.with_analysis_context(self.analysis.as_ref());
                let normalized = candidate.is_some();
                let candidate = candidate.unwrap_or_else(|| self.empty_type_ii_session());
                let label = self.project_report_source_label();
                let name = if label.is_empty() { self.filename().to_owned() } else { label };
                made = TypeIIWindow::new(spectrum, &name, &candidate, normalized, "", Vec::new());
                &made
            }
        };
        let png = window
            .origin_figure()
            .ok()
            .and_then(|fig| crate::figure_export::png_bytes(ctx, &fig, REPORT_FIGURE_DPI, 0.08).ok());
        let png = png.filter(|(_, _, _, rgba)| !blank_or_black(rgba)).map(|(p, w, h, _)| (p, w, h));
        self.report_figure(
            "Type II Band Splitting",
            png,
            "Drawn from the Type II band-splitting view.",
            "Type II band-splitting plot could not be drawn.",
        )
    }

    /// `_build_project_report_figures` (no SWAVES, GOES or geomagnetic
    /// windows in Lite: those figures carry the full app's notes).
    fn report_figures(&self, ctx: &egui::Context, session: &Val, type_ii: Option<&TypeIIWindow>) -> Vec<ReportFigure> {
        let mut figures = Vec::new();
        let d = self.data.as_ref();
        let spectrum = |title: &str, data: Option<Arc<Array2>>, unavailable: &str, plot_type: &str, curves| {
            let png = data.and_then(|a| self.report_spectrum_png(ctx, &a, plot_type, curves));
            self.report_figure(title, png, "", unavailable)
        };
        figures.push(spectrum(
            "Raw Dynamic Spectrum",
            d.map(|d| d.raw.clone()),
            "Raw dynamic spectrum data is not available.",
            "Raw",
            Vec::new(),
        ));
        let background = self.noise_reduced_original.clone().or_else(|| self.noise_reduced.clone());
        figures.push(spectrum(
            "Background Subtracted Dynamic Spectrum",
            background,
            "Background-subtracted dynamic spectrum is not available.",
            "Background Subtracted",
            Vec::new(),
        ));
        if self.tools.light_curves.is_empty() {
            figures.push(self.report_figure(
                "Light Curves With Dynamic Spectrum",
                None,
                "",
                "Light-curve graph is not available.",
            ));
        } else {
            let curves = self.origin_light_curves();
            let source = if curves.is_empty() { None } else { self.current_light_curve_source() };
            let plot_type = self.current_plot_type.clone();
            figures.push(spectrum(
                "Light Curves With Dynamic Spectrum",
                source,
                "Light-curve graph is not available.",
                &plot_type,
                curves,
            ));
        }
        figures.push(self.max_intensity_fit_figure(ctx, session));
        figures.push(self.type_ii_figure(ctx, session, type_ii));
        for (title, note) in [
            ("GOES X-Ray Data", "Separate GOES X-Ray data is not available."),
            ("GOES SGPS Proton Flux", "GOES SGPS proton flux data is not available in the loaded app state."),
            ("DST Index", "DST index data is not available in the loaded app state."),
            ("Kp Index", "Kp index data is not available in the loaded app state."),
        ] {
            figures.push(self.report_figure(title, None, "", note));
        }
        figures
    }

    /// The analysis session as the report reads it: the normalised session
    /// with its vectors in place (`_best_available_analysis_session`).
    fn report_session(&self) -> Val {
        let Some(s) = self.with_analysis_context(self.analysis.as_ref()) else { return Val::None };
        let (payload, arrays) = callisto_core::analysis::session_json::to_project_payload(&s);
        let Val::Map(mut m) = val(&payload) else { return Val::None };
        let arr = |k: &str| arrays.get(k).map_or(Val::None, |v| floats(v));
        for (k, v) in &mut m {
            match (k.as_str(), v) {
                ("max_intensity", Val::Map(max)) => {
                    for (key, array) in [
                        ("time_channels", "analysis_time_channels"),
                        ("time_seconds", "analysis_time_seconds"),
                        ("freqs", "analysis_freqs"),
                    ] {
                        max.retain(|(k, _)| k != key);
                        max.push((key.into(), arr(array)));
                    }
                }
                ("type_ii", Val::Map(t)) => {
                    for band in ["upper", "lower"] {
                        let mut block = vec![
                            ("time_seconds".to_owned(), arr(&format!("type_ii_{band}_time_seconds"))),
                            ("freqs".to_owned(), arr(&format!("type_ii_{band}_freqs"))),
                        ];
                        if let Some((_, Val::Map(old))) = t.iter().find(|(k, _)| k == band) {
                            block.extend(old.iter().filter(|(k, _)| k != "time_seconds" && k != "freqs").cloned());
                        }
                        t.retain(|(k, _)| k != band);
                        t.push((band.into(), Val::Map(block)));
                    }
                }
                _ => {}
            }
        }
        Val::Map(m)
    }

    /// `_project_report_header_fields`: the selected cards, typed as
    /// astropy reads them, and the whole header.
    fn report_header(&self) -> (Vec<(String, Val)>, String) {
        let Some(h) = self.data.as_ref().and_then(|d| d.header0.as_ref()) else { return (Vec::new(), String::new()) };
        const KEYS: [&str; 16] = [
            "SIMPLE", "BITPIX", "NAXIS", "DATE-OBS", "TIME-OBS", "DATE", "INSTRUME", "TELESCOP", "OBSERVAT", "OBJECT",
            "ORIGIN", "CONTENT", "BZERO", "BSCALE", "DATAMIN", "DATAMAX",
        ];
        let mut selected = Vec::new();
        for key in KEYS {
            let Some(card) = h.cards.iter().find(|c| c.key == key) else { continue };
            let v = match &card.value {
                None => Val::None,
                Some(s) if card.quoted => Val::Str(s.trim_end().to_owned()),
                Some(s) => {
                    let t = s.trim();
                    match t {
                        "T" => Val::Bool(true),
                        "F" => Val::Bool(false),
                        _ => t
                            .parse::<i64>()
                            .map(Val::Int)
                            .or_else(|_| t.replace(['D', 'd'], "E").parse::<f64>().map(Val::Float))
                            .unwrap_or_else(|_| Val::Str(t.to_owned())),
                    }
                }
            };
            selected.push((key.to_owned(), v));
        }
        (selected, h.to_text())
    }

    /// `_build_project_report_input` (without the event story).
    pub fn project_report_input(
        &self,
        ctx: &egui::Context,
        project: Option<&Path>,
        type_ii: Option<&TypeIIWindow>,
    ) -> ReportInput {
        let payload = self.provenance_payload();
        let section = |k: &str| payload.get(k).cloned().unwrap_or(Json::Null);
        let app = section("app");
        let mut data_source = match val(&section("data_source")) {
            Val::Map(m) => m,
            _ => Vec::new(),
        };
        // The source label, and every source's path.
        let label = self.project_report_source_label();
        if !label.is_empty() {
            match data_source.iter_mut().find(|(k, _)| k == "filename") {
                Some((_, v)) => *v = Val::Str(label),
                None => data_source.push(("filename".into(), Val::Str(label))),
            }
        }
        let sources = Val::List(self.project_report_source_paths().into_iter().map(Val::Str).collect());
        match data_source.iter_mut().find(|(k, _)| k == "sources") {
            Some((_, v)) => *v = sources,
            None => data_source.push(("sources".into(), sources)),
        }
        let annotations = match section("annotations") {
            Json::Array(a) => a
                .iter()
                .map(|x| {
                    ordered(
                        x,
                        &[
                            "id",
                            "kind",
                            "points",
                            "text",
                            "color",
                            "line_width",
                            "font_family",
                            "font_size",
                            "font_bold",
                            "font_italic",
                            "visible",
                            "created_at",
                        ],
                    )
                })
                .collect(),
            _ => Vec::new(),
        };
        let records = Val::List(
            self.tools
                .light_curves
                .iter()
                .map(|r| {
                    Val::Map(vec![
                        ("frequency_mhz".into(), Val::Float(r.frequency_mhz)),
                        ("requested_mhz".into(), Val::Float(r.requested_mhz)),
                    ])
                })
                .collect(),
        );
        let l = &self.tools.light_curve_settings;
        let settings = Val::Map(vec![
            ("mode".into(), Val::str(if l.multiple { "multiple" } else { "single" })),
            ("line_color".into(), Val::str(l.line_color.clone())),
            ("line_width".into(), Val::Float(l.line_width)),
            ("show_frequency_label".into(), Val::Bool(l.show_frequency_label)),
            ("vertical_scale".into(), Val::Float(l.vertical_scale)),
            ("opacity".into(), Val::Float(l.opacity)),
            ("line_style".into(), Val::str(l.line_style.clone())),
        ]);
        let session = self.report_session();
        let (row, _, _) = self.analysis_log_row(project);
        let analysis_row = Val::Map(
            row.into_iter()
                .map(|(k, c)| {
                    let v = match c {
                        super::exports::Cell::Blank => Val::str(""),
                        super::exports::Cell::Num(f) => Val::Float(f),
                        super::exports::Cell::Flag(b) => Val::Bool(b),
                        super::exports::Cell::Text(s) => Val::Str(s),
                    };
                    (k.to_owned(), v)
                })
                .collect(),
        );
        let (selected_header, full_header) = self.report_header();
        let d = self.data.as_ref();
        let fits_primary = d.and_then(|d| d.source_path.clone()).unwrap_or_else(|| self.filename().to_owned());
        ReportInput {
            title: format!("{} Project Report", self.project_report_default_stem(project)),
            app_name: val(app.get("name").unwrap_or(&Json::Null)),
            app_version: val(app.get("version").unwrap_or(&Json::Null)),
            data_source,
            processing: val(&section("processing")),
            rfi: val(&section("rfi")),
            annotations,
            light_curve_settings: settings,
            light_curve_records: records,
            figures: self.report_figures(ctx, &session, type_ii),
            analysis_session: session,
            analysis_row,
            project_path: project.map(|p| p.to_string_lossy().into_owned()).unwrap_or_default(),
            fits_primary,
            station: self.analysis_station_name(),
            date_obs: self.analysis_date_obs(),
            generated_at: callisto_core::project::now_iso(),
            selected_header,
            full_header,
        }
    }
}
