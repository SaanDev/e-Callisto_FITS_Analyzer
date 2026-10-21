//! Processing presets (`src/backend/session/presets.py`): named snapshots of
//! the thresholds, units, time axis, colormap, background, Graph
//! Properties, RFI settings and annotation style, kept as a JSON list.

use crate::pyjson::{self, Json, Object};

pub const PRESET_SCHEMA_VERSION: i64 = 1;
const CLIP_MIN: f64 = -1_000_000.0;
const CLIP_MAX: f64 = 1_000_000.0;

/// `normalize_name`: whitespace collapsed.
pub fn normalize_name(name: &str) -> String {
    name.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `name.casefold()` for matching.
pub fn name_key(name: &str) -> String {
    normalize_name(name).to_lowercase()
}

fn clamp(v: Option<&Json>, default: f64) -> f64 {
    v.and_then(Json::as_f64).unwrap_or(default).clamp(CLIP_MIN, CLIP_MAX)
}

fn dict(v: Option<&Json>) -> Json {
    match v {
        Some(o @ Json::Object(_)) => o.clone(),
        _ => Json::object(),
    }
}

/// `normalize_preset`; an error for a nameless one.
pub fn normalize(raw: &Json) -> Result<Json, String> {
    let name = normalize_name(&raw.get("name").map(Json::py_str).unwrap_or_default());
    if name.is_empty() {
        return Err("Preset name is required".into());
    }
    let settings = dict(raw.get("settings"));
    let legacy_low = clamp(settings.get("lower_slider"), 0.0);
    let legacy_high = clamp(settings.get("upper_slider"), 0.0);
    let mut low = clamp(settings.get("noise_clip_low"), legacy_low);
    let mut high = clamp(settings.get("noise_clip_high"), legacy_high);
    if low > high {
        std::mem::swap(&mut low, &mut high);
    }
    let mut s = Object::new();
    s.insert("lower_slider".into(), Json::Int(low.round_ties_even() as i64));
    s.insert("upper_slider".into(), Json::Int(high.round_ties_even() as i64));
    s.insert("noise_clip_low".into(), Json::Float(low));
    s.insert("noise_clip_high".into(), Json::Float(high));
    let scale = settings.get("noise_clip_scale").map(Json::py_str).unwrap_or_default();
    s.insert(
        "noise_clip_scale".into(),
        Json::Str(if scale.trim().to_lowercase() == "signed_log" { "signed_log" } else { "linear" }.into()),
    );
    s.insert("use_db".into(), Json::Bool(settings.get("use_db").is_some_and(Json::truthy)));
    s.insert("use_utc".into(), Json::Bool(settings.get("use_utc").is_some_and(Json::truthy)));
    s.insert(
        "cmap".into(),
        Json::Str(settings.get("cmap").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(|| "Custom".into())),
    );
    for key in ["graph", "rfi", "annotation_style_defaults"] {
        s.insert(key.into(), dict(settings.get(key)));
    }
    if let Some(m) = settings.get("background_method") {
        let text = if m.truthy() { m.py_str() } else { String::new() };
        s.insert("background_method".into(), Json::Str(text.trim().to_lowercase()));
    }
    if let Some(b) = settings.get("background_subtracted") {
        s.insert("background_subtracted".into(), Json::Bool(b.truthy()));
    }
    let mut out = Object::new();
    out.insert("name".into(), Json::Str(name));
    out.insert("version".into(), Json::Int(raw.get("version").and_then(Json::as_i64).unwrap_or(PRESET_SCHEMA_VERSION)));
    out.insert(
        "created_at".into(),
        Json::Str(
            raw.get("created_at").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(crate::project::now_iso),
        ),
    );
    out.insert("settings".into(), Json::Object(s));
    Ok(Json::Object(out))
}

/// `build_preset`.
pub fn build(name: &str, settings: Json) -> Result<Json, String> {
    let mut raw = Object::new();
    raw.insert("name".into(), Json::Str(name.to_owned()));
    raw.insert("version".into(), Json::Int(PRESET_SCHEMA_VERSION));
    raw.insert("created_at".into(), Json::Str(crate::project::now_iso()));
    raw.insert("settings".into(), settings);
    normalize(&Json::Object(raw))
}

/// `parse_presets_json`: the valid presets of a JSON list.
pub fn parse(text: &str) -> Vec<Json> {
    match pyjson::loads(text) {
        Ok(Json::Array(items)) => {
            items.iter().filter(|i| i.as_object().is_some()).filter_map(|i| normalize(i).ok()).collect()
        }
        _ => Vec::new(),
    }
}

/// `dump_presets_json`.
pub fn dump(items: &[Json]) -> String {
    pyjson::dumps(&Json::Array(items.iter().filter_map(|i| normalize(i).ok()).collect()))
}

fn name_of(p: &Json) -> String {
    p.get("name").map(Json::py_str).unwrap_or_default()
}

/// `upsert_preset`: replaced by name, else added; sorted by name.
pub fn upsert(items: &[Json], preset: &Json) -> Result<(Vec<Json>, bool), String> {
    let new = normalize(preset)?;
    let key = name_key(&name_of(&new));
    let mut replaced = false;
    let mut out: Vec<Json> = items
        .iter()
        .filter_map(|i| normalize(i).ok())
        .map(|cur| {
            if name_key(&name_of(&cur)) == key {
                replaced = true;
                new.clone()
            } else {
                cur
            }
        })
        .collect();
    if !replaced {
        out.push(new);
    }
    out.sort_by_key(|p| name_key(&name_of(p)));
    Ok((out, replaced))
}

/// `delete_preset`.
pub fn delete(items: &[Json], name: &str) -> (Vec<Json>, bool) {
    let key = name_key(name);
    if key.is_empty() {
        return (items.to_vec(), false);
    }
    let mut removed = false;
    let out: Vec<Json> = items
        .iter()
        .filter_map(|i| normalize(i).ok())
        .filter(|p| {
            let hit = name_key(&name_of(p)) == key;
            removed |= hit;
            !hit
        })
        .collect();
    (out, removed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn presets_like_the_full_app() {
        let settings =
            pyjson::loads(r#"{"noise_clip_low": 5.5, "noise_clip_high": -2.5, "use_db": 1, "cmap": ""}"#).unwrap();
        let p = build("  My   preset ", settings).unwrap();
        assert_eq!(name_of(&p), "My preset");
        let s = p.get("settings").unwrap();
        assert_eq!(s.get("noise_clip_low"), Some(&Json::Float(-2.5)));
        assert_eq!(s.get("lower_slider"), Some(&Json::Int(-2)));
        assert_eq!(s.get("upper_slider"), Some(&Json::Int(6)));
        assert_eq!(s.get("cmap"), Some(&Json::Str("Custom".into())));
        assert_eq!(s.get("use_db"), Some(&Json::Bool(true)));
        assert!(s.get("background_method").is_none());
        let (list, replaced) = upsert(&[], &p).unwrap();
        assert!(!replaced);
        let (list, replaced) = upsert(&list, &build("my PRESET", Json::object()).unwrap()).unwrap();
        assert!(replaced && list.len() == 1);
        assert_eq!(parse(&dump(&list)).len(), 1);
        assert!(delete(&list, "MY preset").1);
    }
}
