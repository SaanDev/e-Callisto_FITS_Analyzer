//! File → Export As → Export Analysis Log and Export Provenance Report
//! (`analysis_log.py`, `provenance.py` and the main window's slots): one
//! analysis row appended to a CSV and a text summary; the data source,
//! processing and operation log as JSON and Markdown.

use callisto_core::pyjson::{self, Json, Object};

use super::project::rfi_json;
use super::*;

/// `CSV_COLUMNS`.
pub const CSV_COLUMNS: [&str; 28] = [
    "timestamp_utc",
    "project_path",
    "fits_primary",
    "fits_sources",
    "combined_mode",
    "station",
    "date_obs",
    "fit_a",
    "fit_b",
    "fit_r2",
    "fit_rmse",
    "fold",
    "fundamental",
    "harmonic",
    "avg_freq_mhz",
    "avg_freq_err_mhz",
    "avg_drift_mhz_s",
    "avg_drift_err_mhz_s",
    "start_freq_mhz",
    "start_freq_err_mhz",
    "initial_shock_speed_km_s",
    "initial_shock_speed_err_km_s",
    "initial_shock_height_rs",
    "initial_shock_height_err_rs",
    "avg_shock_speed_km_s",
    "avg_shock_speed_err_km_s",
    "avg_shock_height_rs",
    "avg_shock_height_err_rs",
];

/// A log row's cell: blank, a number, a flag or text.
#[derive(Clone, Debug, PartialEq)]
pub enum Cell {
    Blank,
    Num(f64),
    Flag(bool),
    Text(String),
}

impl Cell {
    /// `str(value)`, as `csv` writes it.
    fn text(&self) -> String {
        match self {
            Cell::Blank => String::new(),
            Cell::Num(v) => pyjson::float_repr(*v),
            Cell::Flag(b) => if *b { "True" } else { "False" }.into(),
            Cell::Text(t) => t.clone(),
        }
    }

    /// `_fmt_number(value, digits=4)`.
    fn fmt4(&self) -> String {
        match self {
            Cell::Num(v) => py_fixed(*v, 4),
            Cell::Flag(b) => py_fixed(f64::from(u8::from(*b)), 4),
            Cell::Text(t) => pyjson::py_float(t).map(|v| py_fixed(v, 4)).unwrap_or_default(),
            Cell::Blank => String::new(),
        }
    }
}

fn py_fixed(v: f64, digits: usize) -> String {
    if v.is_nan() {
        "nan".into()
    } else if v.is_infinite() {
        if v > 0.0 { "inf".into() } else { "-inf".into() }
    } else {
        format!("{v:.digits$}")
    }
}

fn num(v: Option<f64>) -> Cell {
    v.map_or(Cell::Blank, Cell::Num)
}

/// `csv` with the excel dialect: quoted when a field holds `,`, `"` or a line break.
fn csv_field(text: &str) -> String {
    if text.contains([',', '"', '\n', '\r']) { format!("\"{}\"", text.replace('"', "\"\"")) } else { text.to_owned() }
}

/// Python's `repr` of a value, as f-strings print lists and `None`.
pub fn py_repr(v: &Json) -> String {
    match v {
        Json::Null => "None".into(),
        Json::Bool(b) => if *b { "True" } else { "False" }.into(),
        Json::Int(i) => i.to_string(),
        Json::Float(f) => pyjson::float_repr(*f),
        Json::Str(s) => py_repr_str(s),
        Json::Array(a) => format!("[{}]", a.iter().map(py_repr).collect::<Vec<_>>().join(", ")),
        Json::Object(o) => format!(
            "{{{}}}",
            o.iter().map(|(k, v)| format!("{}: {}", py_repr_str(k), py_repr(v))).collect::<Vec<_>>().join(", ")
        ),
    }
}

/// `str(value)`: strings bare, everything else as `repr`.
fn py_str(v: &Json) -> String {
    match v {
        Json::Str(s) => s.clone(),
        other => py_repr(other),
    }
}

fn py_repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

impl Session {
    /// `_current_graph_title_for_export`: the title on show, else the override, else the default.
    pub fn graph_title_for_export(&self) -> String {
        let o = self.graph.title_override.trim();
        if o.is_empty() { self.default_graph_title(&self.current_plot_type) } else { o.to_owned() }
    }

    /// `_analysis_station_name`: the file name's first `_` field.
    pub(super) fn analysis_station_name(&self) -> String {
        let name = self.filename();
        if name.is_empty() {
            return String::new();
        }
        basename(name).split('_').next().unwrap_or("").trim().to_owned()
    }

    /// `_analysis_date_obs`: the header's DATE-OBS as a date.
    pub(super) fn analysis_date_obs(&self) -> String {
        let h = self.data.as_ref().and_then(|d| d.header0.as_ref());
        for key in ["DATE-OBS", "DATEOBS"] {
            let raw = h.and_then(|h| h.get_str(key)).unwrap_or_default();
            let raw = raw.trim();
            if raw.is_empty() {
                continue;
            }
            let text = raw.replace('Z', "+00:00");
            if let Ok(d) = chrono::DateTime::parse_from_rfc3339(&text) {
                return d.date_naive().to_string();
            }
            for fmt in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"] {
                if let Ok(d) = chrono::NaiveDateTime::parse_from_str(&text, fmt) {
                    return d.date().to_string();
                }
                if let Ok(d) = chrono::NaiveDate::parse_from_str(&text, fmt) {
                    return d.to_string();
                }
            }
            if let Some(d) = raw.get(..10).and_then(|t| chrono::NaiveDate::parse_from_str(t, "%Y-%m-%d").ok()) {
                return d.to_string();
            }
        }
        // `re.search(r"(\d{8})", filename)`: the first eight digits in a row.
        let name: Vec<char> = self.filename().chars().collect();
        for w in name.windows(8) {
            if w.iter().all(char::is_ascii_digit) {
                let digits: String = w.iter().collect();
                return chrono::NaiveDate::parse_from_str(&digits, "%Y%m%d").map(|d| d.to_string()).unwrap_or_default();
            }
        }
        String::new()
    }

    /// `build_log_row`: one analysis, with `project_path` and the run id.
    pub fn analysis_log_row(&self, project_path: Option<&Path>) -> (Vec<(&'static str, Cell)>, String, bool) {
        let session = self.with_analysis_context(self.analysis.as_ref());
        let d = self.data.as_ref();
        let mut sources: Vec<String> = d.map(|d| d.combined_sources.clone()).unwrap_or_default();
        if sources.is_empty()
            && let Some(p) = d.and_then(|d| d.source_path.clone())
        {
            sources.push(p);
        }
        let primary = d.and_then(|d| d.source_path.clone()).unwrap_or_else(|| self.filename().to_owned());
        let a = session.as_ref().map(|s| &s.analyzer);
        let fit = a.and_then(|a| a.fit_params);
        let shock = a.map(|a| a.shock_summary.clone()).unwrap_or_default();
        let flag = |v: Option<bool>| v.map_or(Cell::Blank, Cell::Flag);
        let max = session.as_ref().and_then(|s| s.max_intensity.as_ref());
        let row = vec![
            ("timestamp_utc", Cell::Text(chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string())),
            ("project_path", Cell::Text(project_path.map(|p| p.to_string_lossy().into_owned()).unwrap_or_default())),
            ("fits_primary", Cell::Text(primary)),
            (
                "fits_sources",
                Cell::Text(sources.into_iter().filter(|s| !s.trim().is_empty()).collect::<Vec<_>>().join("|")),
            ),
            (
                "combined_mode",
                Cell::Text(d.and_then(|d| d.combined_mode).map(|m| m.key().to_owned()).unwrap_or_default()),
            ),
            ("station", Cell::Text(self.analysis_station_name())),
            ("date_obs", Cell::Text(self.analysis_date_obs())),
            ("fit_a", num(fit.map(|f| f.a))),
            ("fit_b", num(fit.map(|f| f.b.abs()))),
            ("fit_r2", num(fit.and_then(|f| f.r2))),
            ("fit_rmse", num(fit.and_then(|f| f.rmse))),
            ("fold", num(a.map(|a| a.fold as f64))),
            ("fundamental", flag(session.as_ref().map(|_| max.is_none_or(|m| m.fundamental)))),
            ("harmonic", flag(session.as_ref().map(|_| max.is_some_and(|m| m.harmonic)))),
            ("avg_freq_mhz", num(shock.avg_freq_mhz)),
            ("avg_freq_err_mhz", num(shock.avg_freq_err_mhz)),
            ("avg_drift_mhz_s", num(shock.avg_drift_mhz_s)),
            ("avg_drift_err_mhz_s", num(shock.avg_drift_err_mhz_s)),
            ("start_freq_mhz", num(shock.start_freq_mhz)),
            ("start_freq_err_mhz", num(shock.start_freq_err_mhz)),
            ("initial_shock_speed_km_s", num(shock.initial_shock_speed_km_s)),
            ("initial_shock_speed_err_km_s", num(shock.initial_shock_speed_err_km_s)),
            ("initial_shock_height_rs", num(shock.initial_shock_height_rs)),
            ("initial_shock_height_err_rs", num(shock.initial_shock_height_err_rs)),
            ("avg_shock_speed_km_s", num(shock.avg_shock_speed_km_s)),
            ("avg_shock_speed_err_km_s", num(shock.avg_shock_speed_err_km_s)),
            ("avg_shock_height_rs", num(shock.avg_shock_height_rs)),
            ("avg_shock_height_err_rs", num(shock.avg_shock_height_err_rs)),
        ];
        let run_id = session.as_ref().map(|s| s.identity.run_id.clone()).unwrap_or_default();
        (row, run_id, fit.is_some())
    }

    /// `append_csv_log` and `append_txt_summary` for `stem` (`<stem>_analysis_log.csv/.txt`).
    pub fn export_analysis_log(
        &mut self,
        stem: &Path,
        project_path: Option<&Path>,
    ) -> std::io::Result<(PathBuf, PathBuf)> {
        let (row, run_id, has_fit) = self.analysis_log_row(project_path);
        let csv_path = PathBuf::from(format!("{}_analysis_log.csv", stem.display()));
        let txt_path = PathBuf::from(format!("{}_analysis_log.txt", stem.display()));
        if let Some(dir) = csv_path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        use std::io::Write;
        let header = std::fs::metadata(&csv_path).map_or(true, |m| m.len() == 0);
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&csv_path)?;
        if header {
            write!(f, "{}\r\n", CSV_COLUMNS.join(","))?;
        }
        let cells: Vec<String> = row.iter().map(|(_, c)| csv_field(&c.text())).collect();
        write!(f, "{}\r\n", cells.join(","))?;
        let get = |k: &str| row.iter().find(|(c, _)| *c == k).map(|(_, v)| v.clone()).unwrap_or(Cell::Blank);
        let fit_line = match (get("fit_a"), get("fit_b")) {
            (Cell::Blank, _) | (_, Cell::Blank) => String::new(),
            (a, b) => format!("f(x) = {} * x^-{}", a.fmt4(), b.fmt4()),
        };
        let pm = |label: &str, v: &str, e: &str| format!("  {label}: {} +/- {}", get(v).fmt4(), get(e).fmt4());
        let mode = get("combined_mode").text();
        let lines = [
            "=".repeat(88),
            format!("Analysis Log Entry UTC: {}", get("timestamp_utc").text()),
            format!("Analysis Run ID: {run_id}"),
            format!("Project: {}", get("project_path").text()),
            format!("FITS primary: {}", get("fits_primary").text()),
            format!("FITS sources: {}", get("fits_sources").text()),
            format!("Combined mode: {}", if mode == "time_frequency" { "time + frequency".to_owned() } else { mode }),
            format!("Station: {}", get("station").text()),
            format!("Date obs: {}", get("date_obs").text()),
            String::new(),
            "Fit:".into(),
            format!("  Equation: {fit_line}"),
            format!("  R2: {}", get("fit_r2").fmt4()),
            format!("  RMSE: {}", get("fit_rmse").fmt4()),
            format!("  Fold: {}", get("fold").text()),
            format!("  Fundamental: {}", get("fundamental").text()),
            format!("  Harmonic: {}", get("harmonic").text()),
            String::new(),
            "Shock parameters:".into(),
            pm("Avg frequency (MHz)", "avg_freq_mhz", "avg_freq_err_mhz"),
            pm("Avg drift (MHz/s)", "avg_drift_mhz_s", "avg_drift_err_mhz_s"),
            pm("Start frequency (MHz)", "start_freq_mhz", "start_freq_err_mhz"),
            pm("Initial shock speed (km/s)", "initial_shock_speed_km_s", "initial_shock_speed_err_km_s"),
            pm("Initial shock height (Rs)", "initial_shock_height_rs", "initial_shock_height_err_rs"),
            pm("Avg shock speed (km/s)", "avg_shock_speed_km_s", "avg_shock_speed_err_km_s"),
            pm("Avg shock height (Rs)", "avg_shock_height_rs", "avg_shock_height_err_rs"),
            String::new(),
        ];
        let mut t = std::fs::OpenOptions::new().create(true).append(true).open(&txt_path)?;
        t.write_all(lines.join("\n").as_bytes())?;
        if has_fit {
            self.show_message(
                format!(
                    "Analysis log exported: {}, {}",
                    basename(&csv_path.to_string_lossy()),
                    basename(&txt_path.to_string_lossy())
                ),
                5000,
            );
        } else {
            self.show_message(
                "Analysis log exported, but analyzer fit state was missing (blank fields written).",
                5000,
            );
        }
        self.log_operation("Exported analysis log (CSV + TXT).");
        Ok((csv_path, txt_path))
    }

    /// `_build_provenance_context` + `build_provenance_payload`.
    pub fn provenance_payload(&self) -> Json {
        let d = self.data.as_ref();
        let range = |v: &[f64]| {
            let finite: Vec<f64> = v.iter().copied().filter(|x| !x.is_nan()).collect();
            if v.is_empty() {
                Json::Null
            } else {
                let lo = finite.iter().copied().fold(f64::INFINITY, f64::min);
                let hi = finite.iter().copied().fold(f64::NEG_INFINITY, f64::max);
                Json::Array(vec![
                    Json::Float(if finite.is_empty() { f64::NAN } else { lo }),
                    Json::Float(if finite.is_empty() { f64::NAN } else { hi }),
                ])
            }
        };
        let mut app = Object::new();
        app.insert("name".into(), crate::APP_NAME.into());
        app.insert("version".into(), env!("CARGO_PKG_VERSION").into());
        app.insert("platform".into(), format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH).into());
        let mut source = Object::new();
        source.insert("filename".into(), self.filename().into());
        source.insert("is_combined".into(), Json::Bool(d.is_some_and(|d| d.combined_mode.is_some())));
        source.insert("combined_mode".into(), d.and_then(|d| d.combined_mode).map_or(Json::Null, |m| m.key().into()));
        source.insert(
            "shape".into(),
            d.map_or(Json::Null, |d| Json::Array(vec![Json::from(d.raw.shape().0), Json::from(d.raw.shape().1)])),
        );
        source.insert("freq_range_mhz".into(), d.map_or(Json::Null, |d| range(&d.freqs)));
        source.insert("time_range_s".into(), d.map_or(Json::Null, |d| range(&d.time)));
        let sources: Vec<String> = match d {
            Some(d) if !d.combined_sources.is_empty() => d.combined_sources.clone(),
            Some(d) => d.source_path.clone().into_iter().collect(),
            None => Vec::new(),
        };
        source.insert("sources".into(), sources.into());
        let preset = self.preset_settings_payload();
        let clamp = |v: f64| Json::Int(self.clamp_noise_threshold(v).round_ties_even() as i64);
        let mut proc = Object::new();
        proc.insert("plot_type".into(), self.current_plot_type.clone().into());
        proc.insert("use_db".into(), Json::Bool(self.use_db));
        proc.insert("use_utc".into(), Json::Bool(self.use_utc));
        proc.insert("slider_low".into(), clamp(self.noise_clip_low));
        proc.insert("slider_high".into(), clamp(self.noise_clip_high));
        proc.insert("noise_clip_low".into(), Json::Float(self.noise_clip_low));
        proc.insert("noise_clip_high".into(), Json::Float(self.noise_clip_high));
        proc.insert("noise_clip_scale".into(), preset.get("noise_clip_scale").cloned().unwrap_or(Json::Null));
        proc.insert("background_method".into(), self.background_applied.map_or(Json::Null, |m| m.key().into()));
        proc.insert("cmap".into(), self.graph.cmap.clone().into());
        proc.insert("graph".into(), preset.get("graph").cloned().unwrap_or_else(Json::object));
        proc.insert("active_preset".into(), self.project_extras.active_preset.clone().unwrap_or_else(Json::object));
        // `_max_intensity_state`: the legacy maxima with the normalised analyzer.
        let max = self.with_analysis_context(self.analysis.as_ref()).and_then(|s| {
            let (legacy, vectors) = callisto_core::analysis::session_json::legacy_payload(&s)?;
            let mut o = Object::new();
            for (k, key) in
                [("max_time_channels", "time_channels"), ("max_time_seconds", "time_seconds"), ("max_freqs", "freqs")]
            {
                o.insert(
                    key.into(),
                    vectors.get(k).map_or(Json::Null, |v| Json::Array(v.iter().map(|x| Json::Float(*x)).collect())),
                );
            }
            for k in ["fundamental", "harmonic", "analyzer"] {
                o.insert(k.into(), legacy.get(k).cloned().unwrap_or(Json::Null));
            }
            o.insert(
                "source_filename".into(),
                (if s.source.filename.is_empty() { self.filename().to_owned() } else { s.source.filename.clone() })
                    .into(),
            );
            Some(Json::Object(o))
        });
        let mut p = Object::new();
        p.insert("generated_at".into(), callisto_core::project::now_iso().into());
        p.insert("app".into(), Json::Object(app));
        p.insert("data_source".into(), Json::Object(source));
        p.insert("processing".into(), Json::Object(proc));
        p.insert("rfi".into(), rfi_json(&self.rfi));
        p.insert(
            "annotations".into(),
            Json::Array(self.tools.annotations.iter().map(super::project::annotation_json).collect()),
        );
        p.insert("max_intensity".into(), max.unwrap_or(Json::Null));
        p.insert("time_sync".into(), self.project_extras.time_sync.clone().unwrap_or_else(Json::object));
        p.insert(
            "operation_log".into(),
            Json::Array(
                self.log
                    .iter()
                    .map(|(ts, msg)| {
                        let mut o = Object::new();
                        o.insert("ts".into(), ts.clone().into());
                        o.insert("msg".into(), msg.clone().into());
                        Json::Object(o)
                    })
                    .collect(),
            ),
        );
        Json::Object(p)
    }

    /// `payload_to_markdown`.
    pub fn provenance_markdown(payload: &Json) -> String {
        let section = |k: &str| payload.get(k).cloned().unwrap_or_else(Json::object);
        let (app, source, proc, rfi, sync) =
            (section("app"), section("data_source"), section("processing"), section("rfi"), section("time_sync"));
        let ann = payload.get("annotations").and_then(Json::as_array).cloned().unwrap_or_default();
        let ops = payload.get("operation_log").and_then(Json::as_array).cloned().unwrap_or_default();
        let field = |o: &Json, k: &str| o.get(k).map_or("None".to_owned(), py_str);
        let mut lines = vec![
            "# e-CALLISTO FITS Analyzer Provenance".to_owned(),
            String::new(),
            format!("Generated at: `{}`", payload.get("generated_at").map(py_str).unwrap_or_default()),
            String::new(),
            "## App".into(),
            format!("- Name: `{}`", app.get("name").map(py_str).unwrap_or_default()),
            format!("- Version: `{}`", app.get("version").map(py_str).unwrap_or_default()),
            format!("- Platform: `{}`", app.get("platform").map(py_str).unwrap_or_default()),
            String::new(),
            "## Data Source".into(),
        ];
        for key in ["filename", "is_combined", "combined_mode", "shape", "freq_range_mhz", "time_range_s", "sources"] {
            let mut v = field(&source, key);
            if key == "combined_mode" && v == "time_frequency" {
                v = "time + frequency".into();
            }
            lines.push(format!("- {key}: `{v}`"));
        }
        lines.push(String::new());
        lines.push("## Processing".into());
        for key in ["plot_type", "use_db", "use_utc", "slider_low", "slider_high", "cmap"] {
            lines.push(format!("- {key}: `{}`", field(&proc, key)));
        }
        lines.push(String::new());
        lines.push("## RFI".into());
        for key in ["enabled", "applied", "kernel_time", "kernel_freq", "channel_z_threshold", "percentile_clip"] {
            lines.push(format!("- {key}: `{}`", field(&rfi, key)));
        }
        lines.push(String::new());
        lines.push("## Annotations".into());
        lines.push(format!("- count: `{}`", ann.len()));
        for item in &ann {
            let points = item.get("points").and_then(Json::as_array).map_or(0, Vec::len);
            lines.push(format!(
                "- [{}] id={} visible={} points={points}",
                field(item, "kind"),
                field(item, "id"),
                field(item, "visible")
            ));
        }
        lines.push(String::new());
        lines.push("## Time Sync".into());
        for (k, v) in sync.as_object().into_iter().flatten() {
            lines.push(format!("- {k}: `{}`", py_str(v)));
        }
        lines.push(String::new());
        lines.push("## Operation Log".into());
        if ops.is_empty() {
            lines.push("- (none)".into());
        }
        for item in &ops {
            match item {
                Json::Object(_) => lines.push(format!(
                    "- `{}` {}",
                    item.get("ts").map(py_str).unwrap_or_default(),
                    item.get("msg").map(py_str).unwrap_or_default()
                )),
                other => lines.push(format!("- `` {}", py_str(other))),
            }
        }
        lines.push(String::new());
        lines.join("\n")
    }

    /// `write_provenance_files`: `<stem>_provenance.json` and `.md`.
    pub fn export_provenance(&mut self, stem: &Path) -> std::io::Result<(PathBuf, PathBuf)> {
        let payload = self.provenance_payload();
        let json_path = PathBuf::from(format!("{}_provenance.json", stem.display()));
        let md_path = PathBuf::from(format!("{}_provenance.md", stem.display()));
        std::fs::write(&json_path, pyjson::dumps(&payload))?;
        std::fs::write(&md_path, Self::provenance_markdown(&payload))?;
        self.show_message(
            format!(
                "Provenance exported: {}, {}",
                basename(&json_path.to_string_lossy()),
                basename(&md_path.to_string_lossy())
            ),
            5000,
        );
        self.log_operation("Exported provenance report (JSON + Markdown).");
        Ok((json_path, md_path))
    }

    /// What Export to FIT writes: the data on show, else the product.
    pub fn fits_export_source(&self) -> Option<Arc<Array2>> {
        self.has_data().then(|| self.current_display_data.clone().or_else(|| self.current_product())).flatten()
    }

    /// `export_to_fits` once the BITPIX and the path are chosen.
    pub fn export_fits(&mut self, path: &Path, bitpix: i32) -> anyhow::Result<()> {
        use callisto_core::radio::fits_export::{FitsExport, export_fits};
        let Some(data) = self.fits_export_source() else { anyhow::bail!("Load a FITS file before exporting.") };
        let d = self.data.as_ref().expect("data");
        let mode = d.combined_mode.map(|m| m.key().to_owned());
        export_fits(
            path,
            &FitsExport {
                data: &data,
                freqs: &d.freqs,
                time: &d.time,
                header0: d.header0.as_ref(),
                plot_type: &self.current_plot_type,
                unit_label: self.intensity_unit_label(),
                combined_mode: mode.as_deref(),
                combined_sources: &d.combined_sources,
                source_path: d.source_path.as_deref(),
                bitpix,
            },
        )?;
        self.show_message(format!("Exported FITS (BITPIX={bitpix}): {}", basename(&path.to_string_lossy())), 5000);
        Ok(())
    }
}

impl Session {
    /// `_origin_graph_text`: the Graph Properties font, sizes (pixels read
    /// as points) and emphasis.
    pub fn origin_graph_text(&self) -> callisto_core::mpl::analysis_figures::GraphText {
        use callisto_core::mpl::analysis_figures::GraphText;
        let g = &self.graph;
        GraphText {
            tick_pt: g.tick_px as f64,
            label_pt: g.axis_px as f64,
            title_pt: g.title_px as f64,
            family: GraphText::family_id(&crate::fonts::normalize_font_family(&g.font_family)),
            title_bold: g.title_bold,
            title_italic: g.title_italic,
            axis_bold: g.axis_bold,
            axis_italic: g.axis_italic,
            ticks_bold: g.ticks_bold,
            ticks_italic: g.ticks_italic,
        }
    }

    /// `_origin_spectrum_graph`: `source` (before units) as the OriginPro
    /// figure draws it, titled for `plot_type`, at `view` (unless it misses
    /// the spectrum) and `levels` (unless they miss it: then the clipping
    /// thresholds or the data's range), with the overlays given.
    #[allow(clippy::too_many_arguments)]
    pub fn origin_spectrum_graph(
        &self,
        source: &Arc<Array2>,
        plot_type: &str,
        view: Option<View>,
        levels: Option<(f64, f64)>,
        light_curves: Vec<callisto_core::mpl::spectrum_figures::LightCurve>,
        annotations: Vec<callisto_core::mpl::spectrum_figures::SpectrumAnnotation>,
        measurement: Option<callisto_core::mpl::spectrum_figures::Measurement>,
    ) -> Option<callisto_core::mpl::spectrum_figures::SpectrumGraph> {
        use callisto_core::mpl::spectrum_figures::SpectrumGraph;
        use callisto_core::mpl::type_ii_figures::SpectrumImage;
        use callisto_core::radio::frequency_axis::frequency_gap_spans;

        let d = self.data.as_ref()?;
        if source.rows == 0 || source.cols == 0 {
            return None;
        }
        let display = self.intensity_for_display(source);
        let extent = matplotlib_extent(&d.freqs, &d.time, d.frequency_step_mhz)?;

        let data_limits = finite_data_limits(&display.data);
        let mut levels = levels;
        if let (Some((lo, hi)), Some((dlo, dhi))) = (levels, data_limits)
            && hi.min(dhi) < lo.max(dlo)
        {
            // Left over from another spectrum: they miss this one entirely.
            levels = None;
        }
        let levels = levels.or_else(|| self.threshold_display_levels()).or(data_limits);

        let utc = self.use_utc && d.ut_start_sec.is_some();
        let (title, x_label, y_label, colorbar_label) = if self.graph.remove_titles {
            Default::default()
        } else {
            let title = if self.graph.title_override.is_empty() {
                self.default_graph_title(&normalize_plot_type(plot_type))
            } else {
                self.graph.title_override.clone()
            };
            let x = if utc { "Time [UT]" } else { "Time [s]" };
            (title, x.to_owned(), "Frequency [MHz]".to_owned(), format!("Intensity [{}]", self.intensity_unit_label()))
        };

        let step = d.frequency_step_mhz.filter(|s| *s != 0.0).unwrap_or(1.0);
        let gap_spans = match &d.gap_rows {
            Some(mask) if mask.len() == d.freqs.len() => frequency_gap_spans(&d.freqs, Some(mask), step),
            _ => Vec::new(),
        };
        let log_frequency_bounds = (self.freq_axis_log && self.frequency_axis_log_usable())
            .then(|| self.positive_frequency_bounds())
            .flatten();

        // `_dynamic_spectrum_view_for_extent`: a view missing the spectrum is a stale default.
        let view = view.and_then(|v| {
            let span = |a: f64, b: f64| (a.min(b), a.max(b));
            let (fx, fy) = (span(extent[0], extent[1]), span(extent[2], extent[3]));
            let (vx, vy) = (span(v.xlim.0, v.xlim.1), span(v.ylim.0, v.ylim.1));
            let overlap = |a: (f64, f64), b: (f64, f64)| a.1.min(b.1) - a.0.max(b.0);
            (overlap(vx, fx) > 0.0 && overlap(vy, fy) > 0.0).then_some((v.xlim, v.ylim))
        });

        Some(SpectrumGraph {
            image: SpectrumImage {
                data: display,
                extent,
                cmap: Colormap::by_name(&self.graph.cmap),
                levels,
                colorbar_label,
            },
            title,
            x_label,
            y_label,
            ut_start_sec: if utc { d.ut_start_sec } else { None },
            view,
            log_frequency_bounds,
            gap_spans,
            light_curves,
            annotations,
            measurement,
            text: self.origin_graph_text(),
        })
    }

    /// `_build_active_light_curve_overlays` as the figures draw them.
    pub(super) fn origin_light_curves(&self) -> Vec<callisto_core::mpl::spectrum_figures::LightCurve> {
        use callisto_core::mpl::spectrum_figures::LightCurve;
        if self.tools.light_curves.is_empty() {
            return Vec::new();
        }
        self.build_active_light_curve_overlays()
            .0
            .into_iter()
            .map(|o| LightCurve {
                label: (o.show_label && !o.label.trim().is_empty()).then(|| (o.label.clone(), o.label_x, o.label_y)),
                time: o.time,
                y: o.y,
                color: o.color,
                line_width: o.line_width,
                opacity: o.opacity,
                line_style: o.line_style,
            })
            .collect()
    }

    /// `_build_origin_export_figure`: what the main plot shows, overlays
    /// and zoom included, as Export Figure saves it. None without data.
    pub fn origin_export_graph(&self) -> Option<callisto_core::mpl::spectrum_figures::SpectrumGraph> {
        use callisto_core::analysis::annotations::AnnotationKind;
        use callisto_core::mpl::spectrum_figures::{Measurement, SpectrumAnnotation};

        // `_current_dynamic_spectrum_source_data`.
        let source = self.current_plot_source.clone().or_else(|| self.current_product())?;
        // `_current_dynamic_spectrum_levels`.
        let levels = self.canvas.levels().filter(|(lo, hi)| lo.is_finite() && hi.is_finite() && hi > lo);

        // `_origin_annotations`.
        let mut annotations = Vec::new();
        if self.tools.annotations_visible {
            for a in self.tools.annotations.iter().filter(|a| a.visible) {
                let points: Vec<(f64, f64)> = a.points.iter().map(|p| (p[0], p[1])).collect();
                match a.kind {
                    AnnotationKind::Text => {
                        let Some(&(x, y)) = points.first() else { continue };
                        let style = self.annotation_text_style(a);
                        annotations.push(SpectrumAnnotation::Text {
                            x,
                            y,
                            text: a.text.clone(),
                            color: style.color,
                            font_size: style.font_size as f64,
                            bold: style.font_bold,
                            italic: style.font_italic,
                            font_family: style.font_family,
                        });
                    }
                    kind => annotations.push(SpectrumAnnotation::Path {
                        points,
                        closed: kind == AnnotationKind::Polygon,
                        color: a.color.clone(),
                        line_width: a.line_width,
                    }),
                }
            }
        }

        // `_origin_measurement`.
        let measurement = match &self.tools.measurement {
            Some(m) => Some(Measurement {
                points: Self::measurement_points(m).to_vec(),
                label: Self::measurement_overlay_label(m),
            }),
            None if !self.tools.measurement_capture_points.is_empty() => {
                Some(Measurement { points: self.tools.measurement_capture_points.clone(), label: String::new() })
            }
            None => None,
        };
        let plot_type = self.current_plot_type.clone();
        self.origin_spectrum_graph(
            &source,
            &plot_type,
            self.canvas.view(),
            levels,
            self.origin_light_curves(),
            annotations,
            measurement,
        )
    }
}
