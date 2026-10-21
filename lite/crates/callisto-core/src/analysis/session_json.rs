//! The analysis session as projects store it (`analysis_session.py`):
//! `normalize_session` read from JSON, `to_project_payload` (the session in
//! `meta.analysis_session`, its vectors in `arrays.npz`), the pre-2.6
//! `max_intensity` payload both ways, and `validate_session_for_source`.

use std::collections::BTreeMap;

use super::analyzer::{ShockSummary, T0Mode};
use super::density::DensityModel;
use super::session::{
    AnalysisSession, AnalyzerBlock, BandFitState, BandPoints, FitParams, MaxIntensityBlock, SessionIdentity,
    SessionSource, TypeIIBlock, TypeIIInputs, TypeIIPlotStyle, UiBlock,
};
use super::type_ii::TypeIIResults;
use crate::pyjson::{Json, Object};

/// `SESSION_SCHEMA_VERSION`.
pub const SESSION_SCHEMA_VERSION: i64 = 1;

/// Vectors by `arrays.npz` key.
pub type Arrays = BTreeMap<String, Vec<f64>>;

// ------------------------------------------------------------ the helpers

fn get<'a>(o: &'a Json, key: &str) -> Option<&'a Json> {
    o.get(key).filter(|v| !v.is_null())
}

fn obj(o: Option<&Json>) -> Json {
    match o {
        Some(v @ Json::Object(_)) => v.clone(),
        _ => Json::object(),
    }
}

/// `_safe_float`.
fn safe_float(v: Option<&Json>) -> Option<f64> {
    v.and_then(Json::as_f64)
}

/// `_safe_bool(value, default)`: Python truthiness, `default` for None.
fn safe_bool(v: Option<&Json>, default: bool) -> bool {
    match v {
        None | Some(Json::Null) => default,
        Some(v) => v.truthy(),
    }
}

/// `_safe_int(value, default)`.
fn safe_int(v: Option<&Json>, default: i64) -> i64 {
    v.and_then(Json::as_i64).unwrap_or(default)
}

fn opt(v: Option<f64>) -> Json {
    v.map_or(Json::Null, Json::Float)
}

/// `_shape_from`.
fn shape_from(v: Option<&Json>) -> Option<[usize; 2]> {
    let a = v?.as_array()?;
    if a.len() < 2 {
        return None;
    }
    Some([a[0].as_i64()?.max(0) as usize, a[1].as_i64()?.max(0) as usize])
}

/// `_safe_color`: `#rgb` or `#rrggbb`, lowercased; anything else the default.
fn safe_color(v: Option<&Json>, default: &str) -> String {
    let text = v.map(Json::py_str).unwrap_or_default();
    let text = text.trim();
    if text.starts_with('#') && matches!(text.chars().count(), 4 | 7) {
        text.to_lowercase()
    } else {
        default.to_owned()
    }
}

// ------------------------------------------------------------- the blocks

/// `_normalize_fit_params`.
fn fit_params(raw: Option<&Json>) -> Option<FitParams> {
    let raw @ Json::Object(_) = raw? else { return None };
    let a = safe_float(raw.get("a"))?;
    let b = safe_float(raw.get("b"))?.abs();
    let mut std_errs = [None, None];
    if let Some(e) = raw.get("std_errs").and_then(Json::as_array)
        && e.len() >= 2
    {
        std_errs = [e[0].as_f64(), e[1].as_f64()];
    }
    Some(FitParams { a, b, std_errs, r2: safe_float(raw.get("r2")), rmse: safe_float(raw.get("rmse")) })
}

fn fit_params_json(f: &FitParams) -> Object {
    let mut o = Object::new();
    o.insert("a".into(), Json::Float(f.a));
    o.insert("b".into(), Json::Float(f.b));
    o.insert("std_errs".into(), Json::Array(f.std_errs.iter().map(|v| opt(*v)).collect()));
    o.insert("r2".into(), opt(f.r2));
    o.insert("rmse".into(), opt(f.rmse));
    o
}

const SHOCK_FLOATS: [&str; 17] = [
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
    "observed_avg_freq_mhz",
    "observed_avg_drift_mhz_s",
    "observed_start_freq_mhz",
];

fn shock_fields(s: &mut ShockSummary) -> [&mut Option<f64>; 17] {
    [
        &mut s.avg_freq_mhz,
        &mut s.avg_freq_err_mhz,
        &mut s.avg_drift_mhz_s,
        &mut s.avg_drift_err_mhz_s,
        &mut s.start_freq_mhz,
        &mut s.start_freq_err_mhz,
        &mut s.initial_shock_speed_km_s,
        &mut s.initial_shock_speed_err_km_s,
        &mut s.initial_shock_height_rs,
        &mut s.initial_shock_height_err_rs,
        &mut s.avg_shock_speed_km_s,
        &mut s.avg_shock_speed_err_km_s,
        &mut s.avg_shock_height_rs,
        &mut s.avg_shock_height_err_rs,
        &mut s.observed_avg_freq_mhz,
        &mut s.observed_avg_drift_mhz_s,
        &mut s.observed_start_freq_mhz,
    ]
}

/// `_normalize_shock_summary`.
fn shock_summary(raw: Option<&Json>, fold: i64, fundamental: bool, harmonic: bool) -> ShockSummary {
    let src = obj(raw);
    let mut s = ShockSummary::default();
    for (key, field) in SHOCK_FLOATS.iter().zip(shock_fields(&mut s)) {
        *field = safe_float(src.get(key));
    }
    s.fold = safe_int(src.get("fold").or(Some(&Json::Int(fold))), fold);
    s.fundamental = safe_bool(src.get("fundamental").or(Some(&Json::Bool(fundamental))), fundamental);
    s.harmonic = safe_bool(src.get("harmonic").or(Some(&Json::Bool(harmonic))), harmonic);
    let harmonic_default = if s.harmonic { 2 } else { 1 };
    s.harmonic_number = safe_int(src.get("harmonic_number").or(Some(&Json::Int(harmonic_default))), harmonic_default);
    s
}

fn shock_json(s: &ShockSummary) -> Object {
    let mut s = s.clone();
    let mut o = Object::new();
    o.insert("fold".into(), Json::Int(s.fold));
    o.insert("fundamental".into(), Json::Bool(s.fundamental));
    o.insert("harmonic".into(), Json::Bool(s.harmonic));
    o.insert("harmonic_number".into(), Json::Int(s.harmonic_number));
    for (key, field) in SHOCK_FLOATS.iter().zip(shock_fields(&mut s)) {
        o.insert((*key).into(), opt(*field));
    }
    o
}

/// `_to_1d_float_array` of a vector already read from the arrays.
fn vector(v: Option<&Vec<f64>>) -> Option<Vec<f64>> {
    v.cloned()
}

/// `_normalize_type_ii_points`: None for an empty or mismatched band.
fn band_points(time: Option<Vec<f64>>, freqs: Option<Vec<f64>>) -> BandPoints {
    match (time, freqs) {
        (Some(t), Some(f)) if !t.is_empty() && t.len() == f.len() => BandPoints { time_seconds: t, freqs: f },
        _ => BandPoints::default(),
    }
}

/// `_normalize_type_ii_fit`.
fn band_fit(raw: Option<&Json>) -> Option<BandFitState> {
    let fit = fit_params(raw)?;
    let n = safe_int(raw.and_then(|r| r.get("point_count")).or(Some(&Json::Int(0))), 0).max(0);
    Some(BandFitState { fit, point_count: (n > 0).then_some(n as usize) })
}

const INPUT_FLOATS: [&str; 7] = [
    "initial_shock_speed_km_s",
    "avg_shock_speed_km_s",
    "initial_shock_height_rs",
    "avg_shock_height_rs",
    "start_freq_mhz",
    "avg_drift_mhz_s",
    "avg_drift_err_mhz_s",
];

fn input_fields(i: &mut TypeIIInputs) -> [&mut Option<f64>; 7] {
    [
        &mut i.initial_shock_speed_km_s,
        &mut i.avg_shock_speed_km_s,
        &mut i.initial_shock_height_rs,
        &mut i.avg_shock_height_rs,
        &mut i.start_freq_mhz,
        &mut i.avg_drift_mhz_s,
        &mut i.avg_drift_err_mhz_s,
    ]
}

fn shock_input_values(s: &ShockSummary) -> [Option<f64>; 7] {
    [
        s.initial_shock_speed_km_s,
        s.avg_shock_speed_km_s,
        s.initial_shock_height_rs,
        s.avg_shock_height_rs,
        s.start_freq_mhz,
        s.avg_drift_mhz_s,
        s.avg_drift_err_mhz_s,
    ]
}

/// `_normalize_type_ii_speed_mode`.
fn average_speed(v: Option<&Json>) -> bool {
    let mode = v.map(Json::py_str).unwrap_or_else(|| "initial".into());
    mode.trim().to_lowercase() == "average"
}

/// `_normalize_type_ii_analysis_inputs` of typed inputs: the shock
/// summary's numbers first, then the saved ones; the shock summary's fold
/// when it has numbers, the saved fold otherwise.
pub fn normalize_type_ii_inputs(saved: &TypeIIInputs, shock: &ShockSummary) -> TypeIIInputs {
    let mut out = TypeIIInputs { average_speed: saved.average_speed, ..Default::default() };
    let shock_values = shock_input_values(shock);
    let mut saved_copy = saved.clone();
    let saved_values: Vec<Option<f64>> = input_fields(&mut saved_copy).into_iter().map(|v| *v).collect();
    for ((field, s), v) in input_fields(&mut out).into_iter().zip(shock_values).zip(saved_values) {
        *field = s.or(v);
    }
    let fold = if shock_values.iter().any(Option::is_some) { shock.fold } else { saved.fold };
    // `max(1, min(4, int(fold_value or 1)))`.
    out.fold = if fold == 0 { 1 } else { fold.clamp(1, 4) };
    out
}

fn inputs_from_json(raw: Option<&Json>, analyzer_fold: i64, shock: &ShockSummary, legacy_fold: &Json) -> TypeIIInputs {
    let src = obj(raw);
    let mut saved = TypeIIInputs::default();
    for (key, field) in INPUT_FLOATS.iter().zip(input_fields(&mut saved)) {
        *field = safe_float(src.get(key));
    }
    saved.average_speed = average_speed(src.get("speed_mode"));
    saved.fold = if shock_input_values(shock).iter().any(Option::is_some) {
        shock.fold
    } else {
        // `_safe_int(src.get("fold", legacy_fold), _safe_int(legacy_fold, analyzer_fold or 1))`.
        let default = safe_int(Some(legacy_fold), if analyzer_fold > 0 { analyzer_fold } else { 1 });
        let source = src.get("fold").cloned().unwrap_or_else(|| legacy_fold.clone());
        safe_int(Some(&source), default)
    };
    normalize_type_ii_inputs(&saved, shock)
}

fn inputs_json(i: &TypeIIInputs) -> Object {
    let mut i = i.clone();
    let mut o = Object::new();
    o.insert("fold".into(), Json::Int(i.fold));
    o.insert("speed_mode".into(), Json::Str(if i.average_speed { "average" } else { "initial" }.into()));
    for (key, field) in INPUT_FLOATS.iter().zip(input_fields(&mut i)) {
        o.insert((*key).into(), opt(*field));
    }
    o
}

const RESULT_FLOATS: [&str; 12] = [
    "start_time_s",
    "end_time_s",
    "upper_start_freq_mhz",
    "lower_start_freq_mhz",
    "avg_upper_freq_mhz",
    "avg_lower_freq_mhz",
    "bandwidth_mhz",
    "upper_avg_drift_mhz_s",
    "compression_ratio",
    "alfven_mach_number",
    "alfven_speed_km_s",
    "magnetic_field_g",
];

fn result_fields(r: &mut TypeIIResults) -> [&mut f64; 12] {
    [
        &mut r.start_time_s,
        &mut r.end_time_s,
        &mut r.upper_start_freq_mhz,
        &mut r.lower_start_freq_mhz,
        &mut r.avg_upper_freq_mhz,
        &mut r.avg_lower_freq_mhz,
        &mut r.bandwidth_mhz,
        &mut r.upper_avg_drift_mhz_s,
        &mut r.compression_ratio,
        &mut r.alfven_mach_number,
        &mut r.alfven_speed_km_s,
        &mut r.magnetic_field_g,
    ]
}

fn empty_results() -> TypeIIResults {
    TypeIIResults {
        start_time_s: f64::NAN,
        end_time_s: f64::NAN,
        upper_start_freq_mhz: f64::NAN,
        lower_start_freq_mhz: f64::NAN,
        avg_upper_freq_mhz: f64::NAN,
        avg_lower_freq_mhz: f64::NAN,
        bandwidth_mhz: f64::NAN,
        upper_avg_drift_mhz_s: f64::NAN,
        compression_ratio: f64::NAN,
        alfven_mach_number: f64::NAN,
        alfven_speed_km_s: f64::NAN,
        magnetic_field_g: f64::NAN,
        lower_extrapolated: false,
        warning: String::new(),
    }
}

/// `_normalize_type_ii_results`; None for the all-`None` record.
fn results(raw: Option<&Json>) -> Option<TypeIIResults> {
    let src = obj(raw);
    let mut r = empty_results();
    let mut any = false;
    for (key, field) in RESULT_FLOATS.iter().zip(result_fields(&mut r)) {
        if let Some(v) = safe_float(src.get(key)) {
            *field = v;
            any = true;
        }
    }
    r.lower_extrapolated = safe_bool(src.get("lower_extrapolated"), false);
    r.warning = src.get("warning").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_default().trim().to_owned();
    (any || !r.warning.is_empty()).then_some(r)
}

fn results_json(r: Option<&TypeIIResults>) -> Object {
    let mut r = r.cloned().unwrap_or_else(empty_results);
    let mut o = Object::new();
    o.insert("lower_extrapolated".into(), Json::Bool(r.lower_extrapolated));
    o.insert("warning".into(), Json::Str(r.warning.clone()));
    for (key, field) in RESULT_FLOATS.iter().zip(result_fields(&mut r)) {
        // The record keeps `None` for what was never computed.
        o.insert((*key).into(), if field.is_nan() { Json::Null } else { Json::Float(*field) });
    }
    o
}

/// `_normalize_type_ii_plot_style`.
fn plot_style(raw: Option<&Json>) -> TypeIIPlotStyle {
    let src = obj(raw);
    let d = TypeIIPlotStyle::default();
    let num = |k: &str, def: i64| safe_int(src.get(k).or(Some(&Json::Int(def))), def).max(1);
    let flag = |k: &str, def: bool| safe_bool(src.get(k).or(Some(&Json::Bool(def))), def);
    let color = |k: &str, def: &str| safe_color(src.get(k), def);
    TypeIIPlotStyle {
        font_family: src
            .get("font_family")
            .filter(|v| v.truthy())
            .map(Json::py_str)
            .unwrap_or_default()
            .trim()
            .to_owned(),
        title_font_px: num("title_font_px", d.title_font_px),
        axis_label_font_px: num("axis_label_font_px", d.axis_label_font_px),
        tick_font_px: num("tick_font_px", d.tick_font_px),
        title_bold: flag("title_bold", d.title_bold),
        title_italic: flag("title_italic", d.title_italic),
        axis_bold: flag("axis_bold", d.axis_bold),
        axis_italic: flag("axis_italic", d.axis_italic),
        ticks_bold: flag("ticks_bold", d.ticks_bold),
        ticks_italic: flag("ticks_italic", d.ticks_italic),
        upper_line_color: color("upper_line_color", &d.upper_line_color),
        upper_marker_color: color("upper_marker_color", &d.upper_marker_color),
        upper_line_width: num("upper_line_width", d.upper_line_width),
        upper_marker_size: num("upper_marker_size", d.upper_marker_size),
        lower_line_color: color("lower_line_color", &d.lower_line_color),
        lower_marker_color: color("lower_marker_color", &d.lower_marker_color),
        lower_line_width: num("lower_line_width", d.lower_line_width),
        lower_marker_size: num("lower_marker_size", d.lower_marker_size),
        bvr_line_color: color("bvr_line_color", &d.bvr_line_color),
        bvr_marker_color: color("bvr_marker_color", &d.bvr_marker_color),
        bvr_line_width: num("bvr_line_width", d.bvr_line_width),
        bvr_marker_size: num("bvr_marker_size", d.bvr_marker_size),
    }
}

fn plot_style_json(s: &TypeIIPlotStyle) -> Object {
    let mut o = Object::new();
    o.insert("font_family".into(), s.font_family.clone().into());
    for (k, v) in [
        ("title_font_px", s.title_font_px),
        ("axis_label_font_px", s.axis_label_font_px),
        ("tick_font_px", s.tick_font_px),
        ("upper_line_width", s.upper_line_width),
        ("upper_marker_size", s.upper_marker_size),
        ("lower_line_width", s.lower_line_width),
        ("lower_marker_size", s.lower_marker_size),
        ("bvr_line_width", s.bvr_line_width),
        ("bvr_marker_size", s.bvr_marker_size),
    ] {
        o.insert(k.into(), Json::Int(v));
    }
    for (k, v) in [
        ("title_bold", s.title_bold),
        ("title_italic", s.title_italic),
        ("axis_bold", s.axis_bold),
        ("axis_italic", s.axis_italic),
        ("ticks_bold", s.ticks_bold),
        ("ticks_italic", s.ticks_italic),
    ] {
        o.insert(k.into(), Json::Bool(v));
    }
    for (k, v) in [
        ("upper_line_color", &s.upper_line_color),
        ("upper_marker_color", &s.upper_marker_color),
        ("lower_line_color", &s.lower_line_color),
        ("lower_marker_color", &s.lower_marker_color),
        ("bvr_line_color", &s.bvr_line_color),
        ("bvr_marker_color", &s.bvr_marker_color),
    ] {
        o.insert(k.into(), v.clone().into());
    }
    o
}

// ------------------------------------------------------------ the session

/// `normalize_session` of a project's `analysis_session` with its vectors
/// (`analysis_*` and `type_ii_*` arrays). None when there is nothing to restore.
pub fn normalize(session: &Json, arrays: &Arrays) -> Option<AnalysisSession> {
    session.as_object()?;
    let source = obj(get(session, "source"));
    let max_raw = obj(get(session, "max_intensity"));
    let mut time_channels = vector(arrays.get("analysis_time_channels"));
    let mut time_seconds = vector(arrays.get("analysis_time_seconds"));
    let freqs = vector(arrays.get("analysis_freqs"));
    if let (Some(ts), Some(tc)) = (&time_seconds, &time_channels)
        && ts.len() != tc.len()
    {
        time_seconds = None;
    }
    if let (Some(ts), Some(f)) = (&time_seconds, &freqs)
        && ts.len() != f.len()
    {
        time_seconds = None;
    }
    let fundamental = safe_bool(max_raw.get("fundamental").or(Some(&Json::Bool(true))), true);
    let harmonic = safe_bool(max_raw.get("harmonic").or(Some(&Json::Bool(false))), false);

    let analyzer_raw = obj(get(session, "analyzer"));
    let fit = fit_params(analyzer_raw.get("fit_params"));
    let fold = safe_int(analyzer_raw.get("fold").or(Some(&Json::Int(1))), 1).clamp(1, 4);
    let shock = shock_summary(analyzer_raw.get("shock_summary"), fold, fundamental, harmonic);

    let t2 = obj(get(session, "type_ii"));
    let t2_fold = safe_int(t2.get("fold").or(Some(&Json::Int(1))), 1).clamp(1, 4);
    let legacy_fold = t2.get("fold").cloned().unwrap_or(Json::Int(t2_fold));
    let inputs = inputs_from_json(t2.get("analysis_inputs"), fold, &shock, &legacy_fold);
    let type_ii = TypeIIBlock {
        upper: band_points(vector(arrays.get("type_ii_upper_time_seconds")), vector(arrays.get("type_ii_upper_freqs"))),
        lower: band_points(vector(arrays.get("type_ii_lower_time_seconds")), vector(arrays.get("type_ii_lower_freqs"))),
        upper_fit: band_fit(t2.get("upper_fit")),
        lower_fit: band_fit(t2.get("lower_fit")),
        fold: inputs.fold,
        analysis_inputs: inputs,
        results: results(t2.get("results")),
        plot_style: plot_style(t2.get("plot_style")),
    };

    let has_max = matches!((&time_channels, &freqs), (Some(t), Some(f)) if !t.is_empty() && t.len() == f.len());
    let has_analyzer = fit.is_some() || shock.has_values();
    let has_type_ii = type_ii_has_payload(&type_ii);
    if !has_max && !has_analyzer && !has_type_ii {
        return None;
    }
    let max_intensity = match (time_channels.take(), freqs) {
        (Some(tc), Some(f)) => {
            Some(MaxIntensityBlock { time_channels: tc, time_seconds, freqs: f, fundamental, harmonic })
        }
        _ => None,
    };
    let ui = obj(get(session, "ui"));
    let analyzer = AnalyzerBlock {
        fit_params: fit,
        fold,
        shock_summary: shock,
        density_model: DensityModel::parse(&analyzer_raw.get("density_model").map(Json::py_str).unwrap_or_default()),
        t0_mode: T0Mode::parse(
            &analyzer_raw.get("t0_mode").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_default(),
        ),
        t0_s: safe_float(analyzer_raw.get("t0_s")).filter(|v| v.is_finite()).unwrap_or(0.0),
    };
    Some(AnalysisSession {
        source: SessionSource {
            filename: source.get("filename").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_default(),
            is_combined: safe_bool(source.get("is_combined"), false),
            combined_mode: source.get("combined_mode").filter(|v| !v.is_null()).map(Json::py_str),
            combined_sources: source
                .get("combined_sources")
                .and_then(Json::as_array)
                .map(|a| a.iter().map(Json::py_str).collect())
                .unwrap_or_default(),
            shape: shape_from(source.get("shape")),
        },
        max_intensity,
        analyzer,
        type_ii,
        ui: UiBlock {
            restore_max_window: safe_bool(ui.get("restore_max_window").or(Some(&Json::Bool(has_max))), has_max),
            restore_analyzer_window: safe_bool(
                ui.get("restore_analyzer_window").or(Some(&Json::Bool(has_analyzer))),
                has_analyzer,
            ),
            restore_type_ii_window: safe_bool(
                ui.get("restore_type_ii_window").or(Some(&Json::Bool(has_type_ii))),
                has_type_ii,
            ),
            auto_outlier_cleaned: safe_bool(ui.get("auto_outlier_cleaned"), false),
            auto_removed_count: safe_int(ui.get("auto_removed_count").or(Some(&Json::Int(0))), 0).max(0) as usize,
        },
        identity: SessionIdentity {
            version: safe_int(
                session.get("version").or(Some(&Json::Int(SESSION_SCHEMA_VERSION))),
                SESSION_SCHEMA_VERSION,
            ),
            run_id: session.get("analysis_run_id").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_default(),
            updated_at: session.get("updated_at").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_default(),
        }
        .ensured(),
    })
}

/// `_has_type_ii_payload` of a normalised block.
fn type_ii_has_payload(t: &TypeIIBlock) -> bool {
    !t.upper.time_seconds.is_empty()
        || !t.lower.time_seconds.is_empty()
        || t.upper_fit.is_some()
        || t.lower_fit.is_some()
        || t.results.is_some()
        || t.analysis_inputs.has_values()
        || t.plot_style != TypeIIPlotStyle::default()
}

/// `to_project_payload`: the normalised session without its vectors, and
/// the vectors by `arrays.npz` key.
pub fn to_project_payload(s: &AnalysisSession) -> (Json, Arrays) {
    let mut arrays = Arrays::new();
    let mut m = Object::new();
    let identity = s.identity.clone().ensured();
    m.insert("version".into(), Json::Int(identity.version));
    m.insert("analysis_run_id".into(), identity.run_id.into());
    m.insert("updated_at".into(), identity.updated_at.into());

    let mut source = Object::new();
    source.insert("filename".into(), s.source.filename.clone().into());
    source.insert("is_combined".into(), Json::Bool(s.source.is_combined));
    source.insert("combined_mode".into(), s.source.combined_mode.clone().into());
    source.insert("combined_sources".into(), s.source.combined_sources.clone().into());
    source.insert(
        "shape".into(),
        s.source.shape.map_or(Json::Null, |[r, c]| Json::Array(vec![Json::from(r), Json::from(c)])),
    );
    m.insert("source".into(), Json::Object(source));

    let mut max = Object::new();
    let (fundamental, harmonic) = s.max_intensity.as_ref().map_or((true, false), |b| (b.fundamental, b.harmonic));
    max.insert("fundamental".into(), Json::Bool(fundamental));
    max.insert("harmonic".into(), Json::Bool(harmonic));
    if let Some(b) = &s.max_intensity {
        max.insert("point_count".into(), Json::from(b.time_channels.len()));
        arrays.insert("analysis_time_channels".into(), b.time_channels.clone());
        if let Some(ts) = &b.time_seconds {
            arrays.insert("analysis_time_seconds".into(), ts.clone());
        }
        arrays.insert("analysis_freqs".into(), b.freqs.clone());
    }
    m.insert("max_intensity".into(), Json::Object(max));
    m.insert("analyzer".into(), Json::Object(analyzer_json(&s.analyzer)));

    let t = &s.type_ii;
    let mut t2 = Object::new();
    for (name, band) in [("upper", &t.upper), ("lower", &t.lower)] {
        let mut b = Object::new();
        if !band.time_seconds.is_empty() && band.time_seconds.len() == band.freqs.len() {
            b.insert("point_count".into(), Json::from(band.time_seconds.len()));
            arrays.insert(format!("type_ii_{name}_time_seconds"), band.time_seconds.clone());
            arrays.insert(format!("type_ii_{name}_freqs"), band.freqs.clone());
        }
        t2.insert(name.into(), Json::Object(b));
    }
    let band_fit_json = |f: &Option<BandFitState>| {
        f.map_or(Json::Null, |f| {
            let mut o = fit_params_json(&f.fit);
            o.insert("point_count".into(), f.point_count.map_or(Json::Null, Json::from));
            Json::Object(o)
        })
    };
    t2.insert("upper_fit".into(), band_fit_json(&t.upper_fit));
    t2.insert("lower_fit".into(), band_fit_json(&t.lower_fit));
    let inputs = normalize_type_ii_inputs(&t.analysis_inputs, &s.analyzer.shock_summary);
    t2.insert("fold".into(), Json::Int(inputs.fold));
    t2.insert("analysis_inputs".into(), Json::Object(inputs_json(&inputs)));
    t2.insert("results".into(), Json::Object(results_json(t.results.as_ref())));
    t2.insert("plot_style".into(), Json::Object(plot_style_json(&t.plot_style)));
    m.insert("type_ii".into(), Json::Object(t2));

    let mut ui = Object::new();
    ui.insert("restore_max_window".into(), Json::Bool(s.ui.restore_max_window));
    ui.insert("restore_analyzer_window".into(), Json::Bool(s.ui.restore_analyzer_window));
    ui.insert("restore_type_ii_window".into(), Json::Bool(s.ui.restore_type_ii_window));
    ui.insert("auto_outlier_cleaned".into(), Json::Bool(s.ui.auto_outlier_cleaned));
    ui.insert("auto_removed_count".into(), Json::from(s.ui.auto_removed_count));
    m.insert("ui".into(), Json::Object(ui));
    (Json::Object(m), arrays)
}

fn analyzer_json(a: &AnalyzerBlock) -> Object {
    let mut o = Object::new();
    o.insert("fit_params".into(), a.fit_params.as_ref().map_or(Json::Null, |f| Json::Object(fit_params_json(f))));
    o.insert("fold".into(), Json::Int(a.fold));
    o.insert("shock_summary".into(), Json::Object(shock_json(&a.shock_summary)));
    o.insert("density_model".into(), a.density_model.key().into());
    o.insert("t0_mode".into(), a.t0_mode.key().into());
    o.insert("t0_s".into(), Json::Float(a.t0_s));
    o
}

/// The pre-2.6 payload every project still carries
/// (`_session_to_legacy_max_state`): `meta.max_intensity` and the
/// `max_*` arrays. None without maximum-intensity vectors.
pub fn legacy_payload(s: &AnalysisSession) -> Option<(Json, Arrays)> {
    let b = s.max_intensity.as_ref()?;
    let mut o = Object::new();
    o.insert("present".into(), Json::Bool(true));
    o.insert("fundamental".into(), Json::Bool(b.fundamental));
    o.insert("harmonic".into(), Json::Bool(b.harmonic));
    o.insert("analyzer".into(), Json::Object(analyzer_json(&s.analyzer)));
    let mut arrays = Arrays::new();
    arrays.insert("max_time_channels".into(), b.time_channels.clone());
    if let Some(ts) = &b.time_seconds {
        arrays.insert("max_time_seconds".into(), ts.clone());
    }
    arrays.insert("max_freqs".into(), b.freqs.clone());
    Some((Json::Object(o), arrays))
}

/// `from_legacy_max_intensity`: a session from `meta.max_intensity` and the
/// `max_*` arrays of a file written before the canonical session existed.
pub fn from_legacy(meta: &Object, arrays: &Arrays) -> Option<AnalysisSession> {
    let legacy = obj(meta.get("max_intensity"));
    if !legacy.get("present").is_some_and(Json::truthy) {
        return None;
    }
    let tc = arrays.get("max_time_channels")?;
    let f = arrays.get("max_freqs")?;
    if tc.is_empty() || tc.len() != f.len() {
        return None;
    }
    let mut ts = arrays.get("max_time_seconds").cloned();
    if ts.as_ref().is_some_and(|t| t.len() != tc.len()) {
        ts = None;
    }
    let analyzer = obj(legacy.get("analyzer"));
    let fold = safe_int(analyzer.get("fold").or(Some(&Json::Int(1))), 1).clamp(1, 4);
    let fundamental =
        safe_bool(legacy.get("fundamental").or(analyzer.get("fundamental")).or(Some(&Json::Bool(true))), true);
    let harmonic = safe_bool(legacy.get("harmonic").or(analyzer.get("harmonic")).or(Some(&Json::Bool(false))), false);
    let analyzer_present = analyzer.as_object().is_some_and(|o| !o.is_empty());
    let mut session_obj = Object::new();
    let mut source = Object::new();
    source.insert(
        "filename".into(),
        meta.get("filename").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_default().into(),
    );
    source.insert("is_combined".into(), Json::Bool(safe_bool(meta.get("is_combined"), false)));
    source.insert("combined_mode".into(), meta.get("combined_mode").cloned().unwrap_or(Json::Null));
    source.insert("combined_sources".into(), meta.get("combined_sources").cloned().unwrap_or(Json::Array(Vec::new())));
    source.insert("shape".into(), meta.get("shape").cloned().unwrap_or(Json::Null));
    session_obj.insert("source".into(), Json::Object(source));
    let mut max = Object::new();
    max.insert("fundamental".into(), Json::Bool(fundamental));
    max.insert("harmonic".into(), Json::Bool(harmonic));
    session_obj.insert("max_intensity".into(), Json::Object(max));
    let mut a = Object::new();
    if let Some(f) = fit_params(analyzer.get("fit_params")) {
        a.insert("fit_params".into(), Json::Object(fit_params_json(&f)));
    }
    a.insert("fold".into(), Json::Int(fold));
    a.insert(
        "shock_summary".into(),
        Json::Object(shock_json(&shock_summary(analyzer.get("shock_summary"), fold, fundamental, harmonic))),
    );
    session_obj.insert("analyzer".into(), Json::Object(a));
    let mut ui = Object::new();
    ui.insert("restore_max_window".into(), Json::Bool(true));
    ui.insert("restore_analyzer_window".into(), Json::Bool(analyzer_present));
    ui.insert("restore_type_ii_window".into(), Json::Bool(false));
    session_obj.insert("ui".into(), Json::Object(ui));
    let mut vectors = Arrays::new();
    vectors.insert("analysis_time_channels".into(), tc.clone());
    if let Some(ts) = ts {
        vectors.insert("analysis_time_seconds".into(), ts);
    }
    vectors.insert("analysis_freqs".into(), f.clone());
    normalize(&Json::Object(session_obj), &vectors)
}

/// `validate_session_for_source`: the vectors against the dataset's shape.
pub fn validate_for_source(s: &AnalysisSession, current_shape: Option<[usize; 2]>) -> Result<(), String> {
    if let Some(b) = &s.max_intensity {
        if b.time_channels.len() != b.freqs.len() {
            return Err("Analysis vectors have mismatched lengths.".into());
        }
        if b.time_seconds.as_ref().is_some_and(|t| t.len() != b.freqs.len()) {
            return Err("Analysis time-axis values do not match max-intensity vector length.".into());
        }
        if let Some([_, cols]) = current_shape
            && cols > 0
            && b.time_channels.len() != cols
        {
            return Err("Analysis vector length does not match current dataset time-axis length.".into());
        }
    }
    if let (Some(src), Some(cur)) = (s.source.shape, current_shape)
        && src != cur
    {
        return Err("Saved analysis source shape does not match current dataset shape.".into());
    }
    Ok(())
}
