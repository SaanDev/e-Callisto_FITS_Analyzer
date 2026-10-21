//! The radio part of an `.efaproj` (`MainWindow._capture_project_payload`
//! and `_apply_project_payload`): the dataset and its processing, the
//! sidebar's settings, the tools' results and the analysis session, as the
//! top-level keys of `meta.json` and the arrays of `arrays.npz`.

use std::collections::BTreeMap;

use callisto_core::analysis::annotations::{
    Annotation, AnnotationKind, DEFAULT_ANNOTATION_COLOR, DEFAULT_TEXT_FONT_SIZE, TextStyle as AnnotationTextStyle, new_id,
    now_iso,
};
use callisto_core::analysis::light_curve::LightCurveSettings;
use callisto_core::analysis::session_json;
use callisto_core::project::Npy;
use callisto_core::pyjson::{Json, Object};
use callisto_core::radio::combine::{GapFill, Overlap};
use callisto_core::radio::processing::RfiSettings;

use super::tools::AnnotationStyleDefaults;
use super::*;

/// What the radio part held that Lite keeps without using.
#[derive(Clone, Debug, Default)]
pub struct RadioExtras {
    /// `active_preset`: the preset snapshot last applied.
    pub active_preset: Option<Json>,
    /// `time_sync`: the full app's last solar / geomagnetic time sync.
    pub time_sync: Option<Json>,
    /// `swaves` and the `swaves_*` arrays: Lite has no SWAVES panel, so the
    /// one a project held stays as it was, as the full app keeps it across
    /// new data until Reset All.
    pub swaves: Option<(Json, BTreeMap<String, Npy>)>,
}

/// `_pending_analysis_restore`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PendingAnalysisRestore {
    pub open_max: bool,
    pub open_analyzer: bool,
    pub open_type_ii: bool,
    pub warning: String,
}

/// The arrays the radio part writes; every other array belongs to another part.
pub fn is_radio_array(key: &str) -> bool {
    matches!(
        key,
        "raw_data"
            | "noise_reduced_data"
            | "noise_reduced_original"
            | "lasso_mask"
            | "gap_row_mask"
            | "freqs"
            | "time"
            | "analysis_time_channels"
            | "analysis_time_seconds"
            | "analysis_freqs"
            | "type_ii_upper_time_seconds"
            | "type_ii_upper_freqs"
            | "type_ii_lower_time_seconds"
            | "type_ii_lower_freqs"
            | "max_time_channels"
            | "max_time_seconds"
            | "max_freqs"
    ) || key.starts_with("swaves_")
}

fn f(v: f64) -> Json {
    Json::Float(v)
}

fn of(v: Option<f64>) -> Json {
    v.map_or(Json::Null, Json::Float)
}

fn s(v: &str) -> Json {
    Json::Str(v.to_owned())
}

/// `int(round(x))`: ties to even.
fn py_round(v: f64) -> i64 {
    if v.is_finite() { v.round_ties_even() as i64 } else { 0 }
}

fn clip_scale_key(c: ClipScale) -> &'static str {
    match c {
        ClipScale::Linear => "linear",
        ClipScale::SignedLog => "signed_log",
    }
}

/// `_normalize_noise_clip_scale`.
fn clip_scale(v: Option<&Json>) -> ClipScale {
    if v.map(Json::py_str).unwrap_or_default().trim().to_lowercase() == "signed_log" {
        ClipScale::SignedLog
    } else {
        ClipScale::Linear
    }
}

fn combine_type(key: &str) -> Option<CombineType> {
    match key.trim() {
        "time" => Some(CombineType::Time),
        "frequency" => Some(CombineType::Frequency),
        "time_frequency" => Some(CombineType::TimeFrequency),
        _ => None,
    }
}

fn str_of(v: Option<&Json>) -> String {
    v.filter(|v| v.truthy()).map(Json::py_str).unwrap_or_default()
}

fn bool_of(v: Option<&Json>, default: bool) -> bool {
    v.map_or(default, Json::truthy)
}

// ----------------------------------------------------- annotations

pub(super) fn annotation_json(a: &Annotation) -> Json {
    let mut o = Object::new();
    o.insert("id".into(), s(&a.id));
    o.insert("kind".into(), s(a.kind.key()));
    o.insert("points".into(), Json::Array(a.points.iter().map(|[x, y]| Json::Array(vec![f(*x), f(*y)])).collect()));
    o.insert("text".into(), s(&a.text));
    o.insert("color".into(), s(&a.color));
    o.insert("line_width".into(), f(a.line_width));
    o.insert("font_family".into(), s(&a.font_family));
    o.insert("font_size".into(), Json::Int(a.font_size));
    o.insert("font_bold".into(), Json::Bool(a.font_bold));
    o.insert("font_italic".into(), Json::Bool(a.font_italic));
    o.insert("visible".into(), Json::Bool(a.visible));
    o.insert("created_at".into(), s(&a.created_at));
    Json::Object(o)
}

/// `normalize_annotations` of a project's list.
fn annotations_from(v: Option<&Json>) -> Vec<Annotation> {
    let mut out = Vec::new();
    for raw in v.and_then(Json::as_array).into_iter().flatten() {
        if raw.as_object().is_none() {
            continue;
        }
        let Some(kind) = AnnotationKind::parse(&raw.get("kind").map(Json::py_str).unwrap_or_default()) else { continue };
        let points: Vec<[f64; 2]> = raw
            .get("points")
            .and_then(Json::as_array)
            .into_iter()
            .flatten()
            .filter_map(|p| {
                let p = p.as_array()?;
                (p.len() == 2).then_some([p[0].as_f64()?, p[1].as_f64()?])
            })
            .collect();
        let enough = match kind {
            AnnotationKind::Polygon | AnnotationKind::Line => points.len() >= 2,
            AnnotationKind::Text => !points.is_empty(),
        };
        if !enough {
            continue;
        }
        out.push(Annotation {
            id: raw.get("id").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(new_id),
            kind,
            points,
            text: raw.get("text").map(Json::py_str).unwrap_or_default(),
            color: raw.get("color").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(|| DEFAULT_ANNOTATION_COLOR.into()),
            line_width: raw.get("line_width").and_then(Json::as_f64).unwrap_or(1.5),
            font_family: str_of(raw.get("font_family")),
            font_size: raw.get("font_size").and_then(Json::as_i64).map_or(DEFAULT_TEXT_FONT_SIZE, |v| v.max(1)),
            font_bold: bool_of(raw.get("font_bold"), false),
            font_italic: bool_of(raw.get("font_italic"), false),
            visible: bool_of(raw.get("visible"), true),
            created_at: raw.get("created_at").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(now_iso),
        });
    }
    out
}

pub(super) fn style_defaults_json(d: &AnnotationStyleDefaults) -> Json {
    let mut o = Object::new();
    o.insert("color".into(), s(&d.color));
    o.insert("line_width".into(), f(d.line_width));
    o.insert("text_color".into(), s(&d.text.color));
    o.insert("text_font_family".into(), s(&d.text.font_family));
    o.insert("text_font_size".into(), Json::Int(d.text.font_size));
    o.insert("text_bold".into(), Json::Bool(d.text.font_bold));
    o.insert("text_italic".into(), Json::Bool(d.text.font_italic));
    Json::Object(o)
}

/// `_merge_annotation_style_defaults`.
pub(super) fn style_defaults_from(v: Option<&Json>) -> AnnotationStyleDefaults {
    let d = AnnotationStyleDefaults::default();
    let raw = v.cloned().unwrap_or_else(Json::object);
    let color = raw.get("color").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(|| d.color.clone());
    AnnotationStyleDefaults {
        line_width: raw.get("line_width").map_or(Some(d.line_width), Json::as_f64).map_or(d.line_width, |w| w.max(0.5)),
        text: AnnotationTextStyle {
            color: raw.get("text_color").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(|| color.clone()),
            font_family: raw.get("text_font_family").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(|| d.text.font_family.clone()),
            font_size: raw.get("text_font_size").map_or(Some(d.text.font_size), Json::as_i64).map_or(d.text.font_size, |v| v.max(6)),
            font_bold: bool_of(raw.get("text_bold"), d.text.font_bold),
            font_italic: bool_of(raw.get("text_italic"), d.text.font_italic),
        },
        color,
    }
}

// ---------------------------------------------------- light curves

fn light_curve_settings_json(l: &LightCurveSettings) -> Json {
    let mut o = Object::new();
    o.insert("mode".into(), s(if l.multiple { "multiple" } else { "single" }));
    o.insert("line_color".into(), s(&l.line_color));
    o.insert("line_width".into(), f(l.line_width));
    o.insert("vertical_scale".into(), f(l.vertical_scale));
    o.insert("opacity".into(), f(l.opacity));
    o.insert("line_style".into(), s(&l.line_style));
    o.insert("show_frequency_label".into(), Json::Bool(l.show_frequency_label));
    Json::Object(o)
}

/// `QColor(text).name()` for the spellings settings use, else None.
fn qcolor_name(text: &str) -> Option<String> {
    let t = text.trim();
    let hex = t.strip_prefix('#')?;
    if !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    match hex.len() {
        3 => Some(format!("#{}", hex.chars().flat_map(|c| [c, c]).collect::<String>().to_lowercase())),
        6 => Some(format!("#{}", hex.to_lowercase())),
        8 => Some(format!("#{}", hex[2..].to_lowercase())),
        _ => None,
    }
}

/// `_normalize_light_curve_settings`.
fn light_curve_settings_from(v: Option<&Json>, current: &LightCurveSettings) -> LightCurveSettings {
    let Some(raw) = v.filter(|v| v.as_object().is_some()) else { return current.clone() };
    let d = LightCurveSettings::default();
    let num = |k: &str, def: f64| raw.get(k).map_or(Some(def), Json::as_f64).unwrap_or(def);
    LightCurveSettings {
        multiple: raw.get("mode").map(Json::py_str).unwrap_or_default().to_lowercase() == "multiple",
        line_color: qcolor_name(&raw.get("line_color").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_default())
            .unwrap_or_else(|| "#00e5ff".into()),
        line_width: num("line_width", d.line_width),
        vertical_scale: num("vertical_scale", d.vertical_scale),
        opacity: num("opacity", d.opacity),
        line_style: raw.get("line_style").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(|| "solid".into()),
        show_frequency_label: bool_of(raw.get("show_frequency_label"), true),
    }
    .normalized()
}

/// `_normalize_light_curve_record`.
fn light_curve_record(v: &Json) -> Option<LightCurveRecord> {
    let (freq, req) = match v {
        Json::Object(o) => {
            let freq = o.get("frequency_mhz").or_else(|| o.get("frequency")).cloned().unwrap_or(Json::Null);
            let req = o.get("requested_mhz").cloned().unwrap_or_else(|| freq.clone());
            (freq, req)
        }
        other => (other.clone(), other.clone()),
    };
    let frequency_mhz = freq.as_f64().filter(|v| v.is_finite())?;
    let requested_mhz = req.as_f64().filter(|v| v.is_finite()).unwrap_or(frequency_mhz);
    Some(LightCurveRecord { frequency_mhz, requested_mhz })
}

// ------------------------------------------------------------ RFI

pub(super) fn rfi_json(r: &RfiConfig) -> Json {
    let mut o = Object::new();
    o.insert("enabled".into(), Json::Bool(r.settings.enabled));
    o.insert("kernel_time".into(), Json::from(r.settings.kernel_time));
    o.insert("kernel_freq".into(), Json::from(r.settings.kernel_freq));
    o.insert("channel_z_threshold".into(), f(r.settings.channel_z_threshold));
    o.insert("percentile_clip".into(), f(r.settings.percentile_clip));
    o.insert("masked_channel_indices".into(), Json::Array(r.masked_channel_indices.iter().map(|i| Json::from(*i)).collect()));
    o.insert("applied".into(), Json::Bool(r.applied));
    Json::Object(o)
}

pub(super) fn rfi_from(v: &Json) -> RfiConfig {
    let d = RfiSettings::default();
    let uint = |k: &str, def: usize| v.get(k).and_then(Json::as_i64).map_or(def, |x| x.max(0) as usize);
    let num = |k: &str, def: f64| v.get(k).and_then(Json::as_f64).unwrap_or(def);
    RfiConfig {
        settings: RfiSettings {
            enabled: bool_of(v.get("enabled"), d.enabled),
            kernel_time: uint("kernel_time", d.kernel_time),
            kernel_freq: uint("kernel_freq", d.kernel_freq),
            channel_z_threshold: num("channel_z_threshold", d.channel_z_threshold),
            percentile_clip: num("percentile_clip", d.percentile_clip),
        },
        masked_channel_indices: v
            .get("masked_channel_indices")
            .and_then(Json::as_array)
            .into_iter()
            .flatten()
            .filter_map(Json::as_i64)
            .filter(|i| *i >= 0)
            .map(|i| i as usize)
            .collect(),
        applied: bool_of(v.get("applied"), false),
    }
}

// --------------------------------------------------------- arrays

fn arr2(npy: Option<&Npy>) -> Option<Array2> {
    let n = npy?;
    let (rows, cols) = match n.shape.as_slice() {
        [r, c] => (*r, *c),
        [c] => (1, *c),
        _ => return None,
    };
    Some(Array2::new(rows, cols, n.to_f32()?))
}

fn vec_f64(npy: Option<&Npy>) -> Option<Vec<f64>> {
    npy?.to_f64()
}

impl Session {
    /// `_capture_project_payload`: the radio part's meta keys and arrays;
    /// None without data.
    pub fn project_capture(&self) -> Option<(Object, BTreeMap<String, Npy>)> {
        let d = self.data.as_ref()?;
        let mut arrays = BTreeMap::new();
        let (rows, cols) = d.raw.shape();
        arrays.insert("raw_data".into(), Npy::from_f64(&[rows, cols], &d.raw.data.iter().map(|v| *v as f64).collect::<Vec<_>>()));
        for (key, a) in [("noise_reduced_data", &self.noise_reduced), ("noise_reduced_original", &self.noise_reduced_original)] {
            if let Some(a) = a {
                let (r, c) = a.shape();
                arrays.insert(key.into(), Npy::from_f32(&[r, c], &a.data));
            }
        }
        if let Some(m) = &self.tools.lasso_mask
            && let Some(n) = &self.noise_reduced
        {
            let (r, c) = n.shape();
            if m.len() == r * c {
                arrays.insert("lasso_mask".into(), Npy::from_bool(&[r, c], m));
            }
        }
        if let Some(g) = &d.gap_rows {
            arrays.insert("gap_row_mask".into(), Npy::from_bool(&[g.len()], g));
        }
        arrays.insert("freqs".into(), Npy::from_f64(&[d.freqs.len()], &d.freqs));
        arrays.insert("time".into(), Npy::from_f64(&[d.time.len()], &d.time));

        let mut meta = Object::new();
        let mut swaves = Json::Null;
        if let Some((m, a)) = &self.project_extras.swaves {
            swaves = m.clone();
            arrays.extend(a.iter().map(|(k, v)| (k.clone(), v.clone())));
        }
        let g = &self.graph;
        let mut graph = Object::new();
        for (k, v) in [
            ("remove_titles", g.remove_titles),
            ("title_bold", g.title_bold),
            ("title_italic", g.title_italic),
            ("axis_bold", g.axis_bold),
            ("axis_italic", g.axis_italic),
            ("ticks_bold", g.ticks_bold),
            ("ticks_italic", g.ticks_italic),
        ] {
            graph.insert(k.into(), Json::Bool(v));
        }
        graph.insert("title_override".into(), s(&g.title_override));
        graph.insert("font_family".into(), s(&g.font_family));
        graph.insert("tick_font_px".into(), Json::Int(g.tick_px as i64));
        graph.insert("axis_label_font_px".into(), Json::Int(g.axis_px as i64));
        graph.insert("title_font_px".into(), Json::Int(g.title_px as i64));

        let clamp = |v: f64| py_round(self.clamp_noise_threshold(v));
        meta.insert("filename".into(), s(&d.filename));
        meta.insert("current_plot_type".into(), s(&self.current_plot_type));
        meta.insert("lower_slider".into(), Json::Int(clamp(self.noise_clip_low)));
        meta.insert("upper_slider".into(), Json::Int(clamp(self.noise_clip_high)));
        meta.insert("noise_clip_low".into(), f(self.noise_clip_low));
        meta.insert("noise_clip_high".into(), f(self.noise_clip_high));
        meta.insert("noise_clip_scale".into(), s(clip_scale_key(self.noise_clip_scale)));
        meta.insert("background_method".into(), s(self.background_method.key()));
        meta.insert("background_applied_method".into(), self.background_applied.map_or(Json::Null, |m| s(m.key())));
        meta.insert("use_db".into(), Json::Bool(self.use_db));
        meta.insert("use_utc".into(), Json::Bool(self.use_utc));
        meta.insert("ut_start_sec".into(), of(d.ut_start_sec));
        meta.insert("cmap".into(), s(&self.graph.cmap));
        meta.insert("frequency_step_mhz".into(), of(d.frequency_step_mhz));
        meta.insert(
            "view".into(),
            self.canvas.view().map_or(Json::Null, |v| {
                let mut o = Object::new();
                o.insert("xlim".into(), Json::Array(vec![f(v.xlim.0), f(v.xlim.1)]));
                o.insert("ylim".into(), Json::Array(vec![f(v.ylim.0), f(v.ylim.1)]));
                Json::Object(o)
            }),
        );
        meta.insert("noise_vmin".into(), of(self.noise_vmin));
        meta.insert("noise_vmax".into(), of(self.noise_vmax));
        meta.insert("noise_reduced_original_plot_type".into(), s(&self.noise_reduced_original_plot_type));
        meta.insert("fits_header".into(), d.header0.as_ref().map_or(Json::Null, |h| s(&h.to_text())));
        meta.insert("fits_source_path".into(), d.source_path.clone().into());
        meta.insert("is_combined".into(), Json::Bool(d.combined_mode.is_some()));
        meta.insert("combined_mode".into(), d.combined_mode.map_or(Json::Null, |m| s(m.key())));
        meta.insert("combined_sources".into(), d.combined_sources.clone().into());
        let mut options = Object::new();
        if let Some(o) = &d.combined_options {
            options.insert("gap_fill".into(), s(o.gap_fill.key()));
            options.insert("overlap_policy".into(), s(o.overlap_policy.key()));
            if let Some(c) = o.overlap_connection_mhz {
                options.insert("overlap_connection_mhz".into(), f(c));
            }
        }
        meta.insert("combined_options".into(), Json::Object(options));
        meta.insert("graph".into(), Json::Object(graph));
        meta.insert("rfi".into(), rfi_json(&self.rfi));
        meta.insert("annotations".into(), Json::Array(self.tools.annotations.iter().map(annotation_json).collect()));
        meta.insert("annotation_style_defaults".into(), style_defaults_json(&self.tools.annotation_style));
        let records: Vec<Json> = self
            .tools
            .light_curves
            .iter()
            .map(|r| {
                let mut o = Object::new();
                o.insert("frequency_mhz".into(), f(r.frequency_mhz));
                o.insert("requested_mhz".into(), f(r.requested_mhz));
                Json::Object(o)
            })
            .collect();
        let mut lc = Object::new();
        lc.insert("frequency_mhz".into(), of(self.tools.light_curves.last().map(|r| r.frequency_mhz)));
        lc.insert("records".into(), Json::Array(records));
        lc.insert("settings".into(), light_curve_settings_json(&self.tools.light_curve_settings));
        lc.insert("click_mode".into(), Json::Bool(self.tools.light_curve_click_mode));
        meta.insert("light_curve".into(), Json::Object(lc));
        meta.insert("active_preset".into(), self.project_extras.active_preset.clone().unwrap_or_else(Json::object));
        meta.insert(
            "processing_log".into(),
            Json::Array(
                self.log
                    .iter()
                    .map(|(ts, msg)| {
                        let mut o = Object::new();
                        o.insert("ts".into(), s(ts));
                        o.insert("msg".into(), s(msg));
                        Json::Object(o)
                    })
                    .collect(),
            ),
        );
        meta.insert("time_sync".into(), self.project_extras.time_sync.clone().unwrap_or_else(Json::object));
        meta.insert("swaves".into(), swaves);

        // The canonical analysis session (v2.6) and the legacy payload.
        if let Some(session) = self.with_analysis_context(self.analysis.as_ref()) {
            let (session_meta, vectors) = session_json::to_project_payload(&session);
            meta.insert("analysis_session".into(), session_meta);
            for (k, v) in vectors {
                arrays.insert(k, Npy::from_f64(&[v.len()], &v));
            }
            if let Some((legacy, vectors)) = session_json::legacy_payload(&session) {
                meta.insert("max_intensity".into(), legacy);
                for (k, v) in vectors {
                    arrays.insert(k, Npy::from_f64(&[v.len()], &v));
                }
            }
        }
        Some((meta, arrays))
    }

    /// `_apply_project_payload`: the radio part from a project.
    pub fn project_apply(&mut self, meta: &Object, arrays: &BTreeMap<String, Npy>) {
        let get = |k: &str| meta.get(k).filter(|v| !v.is_null());
        self.undo_stack.clear();
        self.redo_stack.clear();
        self.analysis_windows_close_due = true;
        self.timeline_job = None;
        self.timeline_probe = None;
        self.timeline_undo = None;

        let raw = arr2(arrays.get("raw_data"));
        self.noise_reduced = arr2(arrays.get("noise_reduced_data")).map(Arc::new);
        self.noise_reduced_original = arr2(arrays.get("noise_reduced_original")).map(Arc::new);
        self.noise_reduced_original_plot_type =
            normalize_plot_type(&str_of(meta.get("noise_reduced_original_plot_type")).if_empty("Background Subtracted"));
        self.tools.lasso_mask = arrays.get("lasso_mask").and_then(Npy::to_bool);
        let gap_rows = arrays.get("gap_row_mask").and_then(Npy::to_bool);
        let freqs = vec_f64(arrays.get("freqs"));
        let time = vec_f64(arrays.get("time"));

        // `_restore_swaves_from_project`.
        self.project_extras.swaves = get("swaves").filter(|v| v.as_object().is_some()).and_then(|m| {
            let a: BTreeMap<String, Npy> = arrays.iter().filter(|(k, _)| k.starts_with("swaves_")).map(|(k, v)| (k.clone(), v.clone())).collect();
            let complete = ["swaves_x_seconds", "swaves_log_freq", "swaves_intensity"].iter().all(|k| a.get(*k).is_some_and(|n| !n.is_empty()));
            complete.then(|| (m.clone(), a))
        });

        let filename = str_of(meta.get("filename"));
        self.current_plot_type = normalize_plot_type(&str_of(meta.get("current_plot_type")).if_empty("Raw"));
        self.use_db = bool_of(get("use_db"), false);
        self.use_utc = bool_of(get("use_utc"), false);
        let ut_start_sec = get("ut_start_sec").and_then(Json::as_f64);
        self.graph.cmap = str_of(meta.get("cmap")).if_empty("Custom");
        let frequency_step_mhz = get("frequency_step_mhz").and_then(Json::as_f64);
        self.noise_vmin = get("noise_vmin").and_then(Json::as_f64);
        self.noise_vmax = get("noise_vmax").and_then(Json::as_f64);

        // `_restore_background_state`.
        if let Some(m) = get("background_method") {
            self.background_method = BackgroundMethod::normalize(&m.py_str(), true).unwrap_or_default();
        }
        self.background_applied = match get("background_applied_method") {
            Some(m) if self.noise_reduced.is_some() => Some(BackgroundMethod::normalize(&m.py_str(), true).unwrap_or_default()),
            _ => None,
        };

        let header0 = get("fits_header").map(|t| Header::from_text(&t.py_str()));
        let combined_mode = get("combined_mode").and_then(|m| combine_type(&m.py_str()));
        let opts = get("combined_options").and_then(Json::as_object).filter(|o| !o.is_empty());
        let combined_options = opts.map(|o| CombineOptions {
            gap_fill: o.get("gap_fill").and_then(|v| GapFill::parse(&v.py_str()).ok()).unwrap_or_default(),
            overlap_policy: o.get("overlap_policy").and_then(|v| Overlap::parse(&v.py_str()).ok()).unwrap_or_default(),
            overlap_connection_mhz: o.get("overlap_connection_mhz").and_then(Json::as_f64),
        });
        self.data = match (raw, freqs, time) {
            (Some(raw), Some(freqs), Some(time)) => Some(Dataset {
                raw: Arc::new(raw),
                gap_rows: gap_rows.filter(|g| g.len() == freqs.len()),
                freqs: Arc::new(freqs),
                time: Arc::new(time),
                filename,
                header0,
                headers: Vec::new(),
                source_path: get("fits_source_path").map(Json::py_str),
                ut_start_sec,
                combined_mode,
                combined_sources: get("combined_sources").and_then(Json::as_array).map(|a| a.iter().map(Json::py_str).collect()).unwrap_or_default(),
                combined_options,
                frequency_step_mhz,
            }),
            _ => None,
        };

        // `_noise_thresholds_from_mapping`, then the bounds over the product.
        let low = get("noise_clip_low").or(get("lower_slider")).and_then(Json::as_f64).unwrap_or(self.noise_clip_low);
        let high = get("noise_clip_high").or(get("upper_slider")).and_then(Json::as_f64).unwrap_or(self.noise_clip_high);
        let scale = if meta.contains_key("noise_clip_scale") { clip_scale(meta.get("noise_clip_scale")) } else { self.noise_clip_scale };
        self.expand_noise_clip_bounds_for_values(&[low, high]);
        let (mut lo, mut hi) = (self.clamp_noise_threshold(low), self.clamp_noise_threshold(high));
        if lo > hi {
            std::mem::swap(&mut lo, &mut hi);
        }
        self.set_noise_clip_state(Some(lo), Some(hi), Some(scale));
        if self.data.is_some() {
            let product = self.current_product();
            self.fit_noise_clip_bounds_to_data(product.as_deref());
        }

        if let Some(r) = get("rfi").filter(|r| r.truthy()) {
            self.rfi = rfi_from(r);
        }
        self.rfi_preview = None;
        self.tools.annotations = annotations_from(meta.get("annotations"));
        self.tools.annotation_style = style_defaults_from(get("annotation_style_defaults"));
        let lc = get("light_curve").cloned().unwrap_or_else(Json::object);
        let settings = light_curve_settings_from(lc.get("settings").filter(|v| !v.is_null()), &self.tools.light_curve_settings);
        self.tools.light_curve_settings = settings;
        let mut records: Vec<LightCurveRecord> = match lc.get("records") {
            Some(Json::Array(a)) => a.iter().filter_map(light_curve_record).collect(),
            Some(o @ Json::Object(_)) => light_curve_record(o).into_iter().collect(),
            _ => Vec::new(),
        };
        if records.is_empty()
            && let Some(legacy) = lc.get("frequency_mhz").filter(|v| !v.is_null())
        {
            records.extend(light_curve_record(legacy));
        }
        self.store_light_curve_records(records);
        self.tools.light_curve_click_mode = bool_of(lc.get("click_mode"), false);
        self.project_extras.active_preset = Some(get("active_preset").filter(|v| v.truthy()).cloned().unwrap_or_else(Json::object));
        self.log = get("processing_log")
            .and_then(Json::as_array)
            .into_iter()
            .flatten()
            .filter_map(|e| Some((e.get("ts").map(Json::py_str).unwrap_or_default(), e.get("msg")?.py_str())))
            .collect();
        self.project_extras.time_sync = Some(get("time_sync").filter(|v| v.truthy()).cloned().unwrap_or_else(Json::object));
        self.refresh_instrument_profile();

        // Graph Properties: the file's sizes, not the hardware defaults.
        let graph = get("graph").cloned().unwrap_or_else(Json::object);
        let g = &mut self.graph;
        g.hw_default_sizes = false;
        g.remove_titles = bool_of(graph.get("remove_titles"), false);
        g.title_bold = bool_of(graph.get("title_bold"), false);
        g.title_italic = bool_of(graph.get("title_italic"), false);
        g.axis_bold = bool_of(graph.get("axis_bold"), false);
        g.axis_italic = bool_of(graph.get("axis_italic"), false);
        g.ticks_bold = bool_of(graph.get("ticks_bold"), false);
        g.ticks_italic = bool_of(graph.get("ticks_italic"), false);
        g.title_override = str_of(graph.get("title_override"));
        g.font_family = crate::fonts::normalize_font_family(&graph.get("font_family").map(Json::py_str).unwrap_or_default());
        let size = |k: &str, current: u32| graph.get(k).and_then(Json::as_i64).map_or(current, |v| v.clamp(1, 200) as u32);
        g.tick_px = size("tick_font_px", g.tick_px);
        g.axis_px = size("axis_label_font_px", g.axis_px);
        g.title_px = size("title_font_px", g.title_px);

        // The analysis session (canonical, else the legacy keys).
        self.analysis = None;
        let vectors: session_json::Arrays = arrays
            .iter()
            .filter(|(k, _)| k.starts_with("analysis_") || k.starts_with("type_ii_") || k.starts_with("max_"))
            .filter_map(|(k, v)| Some((k.clone(), v.to_f64()?)))
            .collect();
        let mut loaded = get("analysis_session").filter(|v| v.as_object().is_some()).and_then(|s| session_json::normalize(s, &vectors));
        if loaded.is_none() {
            loaded = session_json::from_legacy(meta, &vectors);
        }
        let mut pending = PendingAnalysisRestore::default();
        if let Some(session) = loaded {
            let shape = self.data.as_ref().map(|d| [d.raw.shape().0, d.raw.shape().1]);
            let check = session_json::validate_for_source(&session, shape);
            self.analysis = self.with_analysis_context(Some(&session));
            match check {
                Ok(()) => {
                    let ui = self.analysis.as_ref().map(|s| s.ui.clone()).unwrap_or_default();
                    pending.open_max = ui.restore_max_window;
                    pending.open_analyzer = ui.restore_analyzer_window;
                    pending.open_type_ii = ui.restore_type_ii_window;
                }
                Err(reason) => pending.warning = format!("Analysis restore loaded with warning: {reason}"),
            }
        }

        if self.data.is_some() {
            let title = self.current_plot_type.clone();
            let view = get("view").and_then(|v| {
                let pair = |k: &str| {
                    let a = v.get(k)?.as_array()?;
                    Some((a.first()?.as_f64()?, a.get(1)?.as_f64()?))
                };
                Some(View { xlim: pair("xlim")?, ylim: pair("ylim")? })
            });
            self.plot_data(&title, PlotView::Restore(view));
            self.render_annotations();
            self.pending_analysis_restore = Some(pending);
        } else {
            self.pending_analysis_restore = None;
            self.canvas.clear();
        }
    }

    /// The analysis windows an opened project asked for, once its plot is up;
    /// a validation warning goes to the status bar instead.
    pub fn take_pending_analysis_restore(&mut self) -> Option<PendingAnalysisRestore> {
        let p = self.pending_analysis_restore.take()?;
        if !p.warning.is_empty() {
            self.show_message(p.warning.clone(), 5500);
        }
        Some(p)
    }
}

trait IfEmpty {
    fn if_empty(self, default: &str) -> String;
}

impl IfEmpty for String {
    fn if_empty(self, default: &str) -> String {
        if self.is_empty() { default.to_owned() } else { self }
    }
}
