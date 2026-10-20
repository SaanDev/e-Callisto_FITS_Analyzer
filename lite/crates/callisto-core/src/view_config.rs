//! View configurations (`src/backend/session/view_config.py`): a display
//! range and the visual settings (units, time axis, noise clipping,
//! colormap, Graph Properties), saved as `.efaview.json` and used by batch
//! processing and the multi-station comparison to show files alike.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// `VIEW_CONFIG_SCHEMA_VERSION`.
pub const VIEW_CONFIG_SCHEMA_VERSION: i64 = 1;
/// `VIEW_CONFIG_APP_NAME`.
pub const VIEW_CONFIG_APP_NAME: &str = "e-CALLISTO FITS Analyzer";

/// `datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")`.
pub fn now_iso() -> String {
    chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%:z").to_string()
}

/// Python's `repr` of a float, for messages.
fn py_repr(v: f64) -> String {
    if v.is_nan() {
        "nan".into()
    } else if v.is_infinite() {
        if v > 0.0 { "inf".into() } else { "-inf".into() }
    } else if v.fract() == 0.0 && v.abs() < 1e16 {
        format!("{v:.1}")
    } else {
        format!("{v}")
    }
}

/// `normalize_display_range`'s output.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct DisplayRange {
    pub time_start_s: f64,
    pub time_stop_s: f64,
    pub freq_min_mhz: f64,
    pub freq_max_mhz: f64,
}

impl DisplayRange {
    /// `normalize_display_range`: stop after start, distinct frequencies, sorted.
    pub fn normalized(time_start: f64, time_stop: f64, freq_a: f64, freq_b: f64) -> Result<Self, String> {
        for v in [time_start, time_stop, freq_a, freq_b] {
            if !v.is_finite() {
                return Err(format!("Invalid finite numeric value: {}", py_repr(v)));
            }
        }
        if time_stop <= time_start {
            return Err("Display range stop time must be later than start time.".into());
        }
        if (freq_b - freq_a).abs() <= 1e-9 {
            return Err("Display range frequency bounds must be different.".into());
        }
        let (freq_min_mhz, freq_max_mhz) = if freq_a <= freq_b { (freq_a, freq_b) } else { (freq_b, freq_a) };
        Ok(Self { time_start_s: time_start, time_stop_s: time_stop, freq_min_mhz, freq_max_mhz })
    }

    /// `normalize_display_range` of a JSON object (`freq_start_mhz` and
    /// `freq_stop_mhz` are accepted for the frequencies).
    pub fn from_json(v: &Value) -> Result<Self, String> {
        let num = |keys: &[&str]| -> Result<f64, String> {
            let raw = keys.iter().find_map(|k| v.get(*k).filter(|x| !x.is_null()));
            let Some(raw) = raw else { return Err("Invalid numeric value: None".into()) };
            json_float(raw).ok_or_else(|| format!("Invalid numeric value: {raw}"))
        };
        Self::normalized(num(&["time_start_s"])?, num(&["time_stop_s"])?, num(&["freq_min_mhz", "freq_start_mhz"])?, num(&["freq_max_mhz", "freq_stop_mhz"])?)
    }

    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "time_start_s": self.time_start_s,
            "time_stop_s": self.time_stop_s,
            "freq_min_mhz": self.freq_min_mhz,
            "freq_max_mhz": self.freq_max_mhz,
        })
    }
}

/// `float(value)` for JSON numbers, booleans and numeric strings.
fn json_float(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// `bool(value)` as Python reads JSON values.
fn json_truthy(v: Option<&Value>, default: bool) -> bool {
    match v {
        None => default,
        Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|x| x != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `normalize_visual_config`'s output.
#[derive(Clone, Debug, PartialEq)]
pub struct VisualConfig {
    pub use_db: bool,
    pub use_utc: bool,
    pub noise_clip_low: f64,
    pub noise_clip_high: f64,
    /// `"linear"` or `"signed_log"`.
    pub noise_clip_scale: String,
    pub cmap: String,
    /// Graph Properties as the full app keeps them (`remove_titles`,
    /// `font_family`, `title_font_px`, …), passed through.
    pub graph: Map<String, Value>,
}

impl Default for VisualConfig {
    fn default() -> Self {
        Self {
            use_db: false,
            use_utc: false,
            noise_clip_low: 0.0,
            noise_clip_high: 0.0,
            noise_clip_scale: "linear".into(),
            cmap: "Custom".into(),
            graph: Map::new(),
        }
    }
}

impl VisualConfig {
    /// `normalize_visual_config`.
    pub fn from_json(v: &Value) -> Self {
        let f = |k: &str| v.get(k).and_then(json_float).filter(|x| x.is_finite()).unwrap_or(0.0);
        let scale = v.get("noise_clip_scale").and_then(Value::as_str).unwrap_or("").trim().to_lowercase();
        let cmap = match v.get("cmap") {
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            Some(other) if json_truthy(Some(other), false) && !other.is_string() => other.to_string(),
            _ => "Custom".into(),
        };
        Self {
            use_db: json_truthy(v.get("use_db"), false),
            use_utc: json_truthy(v.get("use_utc"), false),
            noise_clip_low: f("noise_clip_low"),
            noise_clip_high: f("noise_clip_high"),
            noise_clip_scale: if scale == "signed_log" { "signed_log".into() } else { "linear".into() },
            cmap,
            graph: v.get("graph").and_then(Value::as_object).cloned().unwrap_or_default(),
        }
    }

    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "use_db": self.use_db,
            "use_utc": self.use_utc,
            "noise_clip_low": self.noise_clip_low,
            "noise_clip_high": self.noise_clip_high,
            "noise_clip_scale": self.noise_clip_scale,
            "cmap": self.cmap,
            "graph": Value::Object(self.graph.clone()),
        })
    }

    /// A Graph Properties value, if set.
    pub fn graph_bool(&self, key: &str) -> bool {
        json_truthy(self.graph.get(key), false)
    }
}

/// `normalize_view_config`'s output.
#[derive(Clone, Debug, PartialEq)]
pub struct ViewConfig {
    pub version: i64,
    pub created_at: String,
    pub app: String,
    pub range: Option<DisplayRange>,
    /// `"ut"` or `"seconds"`.
    pub time_mode: String,
    pub visual: VisualConfig,
    /// `_include_visual`: whether the visual settings apply (batch runs
    /// built from "Use current display range" alone leave them out).
    pub include_visual: bool,
}

impl ViewConfig {
    /// `build_view_config`.
    pub fn build(range: Option<DisplayRange>, visual: VisualConfig, ut: bool) -> Self {
        Self {
            version: VIEW_CONFIG_SCHEMA_VERSION,
            created_at: now_iso(),
            app: VIEW_CONFIG_APP_NAME.into(),
            range,
            time_mode: if ut { "ut".into() } else { "seconds".into() },
            visual,
            include_visual: true,
        }
    }

    /// `normalize_view_config`.
    pub fn from_json(v: &Value) -> Result<Self, String> {
        let version = match v.get("version") {
            None | Some(Value::Null) => VIEW_CONFIG_SCHEMA_VERSION,
            Some(x) => json_float(x).map_or(VIEW_CONFIG_SCHEMA_VERSION, |f| f as i64),
        };
        if version != VIEW_CONFIG_SCHEMA_VERSION {
            return Err(format!("Unsupported view config version: {version}"));
        }
        let range = match v.get("range") {
            Some(r @ Value::Object(_)) => Some(DisplayRange::from_json(r)?),
            _ => None,
        };
        let text = |k: &str, default: &str| match v.get(k) {
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            _ => default.to_owned(),
        };
        let time_mode = text("time_mode", "").trim().to_lowercase();
        Ok(Self {
            version,
            created_at: text("created_at", &now_iso()),
            app: text("app", VIEW_CONFIG_APP_NAME),
            range,
            time_mode: if time_mode == "ut" { "ut".into() } else { "seconds".into() },
            visual: VisualConfig::from_json(v.get("visual").unwrap_or(&Value::Null)),
            include_visual: json_truthy(v.get("_include_visual"), true),
        })
    }

    /// `parse_view_config_json`.
    pub fn parse(text: &str) -> Result<Self, String> {
        if text.is_empty() {
            return Err("View config JSON is empty.".into());
        }
        let v: Value = serde_json::from_str(text).map_err(|_| "View config JSON is invalid.".to_owned())?;
        if !v.is_object() {
            return Err("View config JSON must contain an object.".into());
        }
        Self::from_json(&v)
    }

    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "version": self.version,
            "created_at": self.created_at,
            "app": self.app,
            "range": self.range.map_or(Value::Null, |r| r.to_json()),
            "time_mode": self.time_mode,
            "visual": self.visual.to_json(),
        })
    }

    /// `dump_view_config_json`: sorted keys, two-space indents.
    pub fn dump(&self) -> String {
        serde_json::to_string_pretty(&self.to_json()).unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_like_the_full_app() {
        let range = DisplayRange::normalized(10.0, 200.0, 80.0, 45.0).unwrap();
        assert_eq!((range.freq_min_mhz, range.freq_max_mhz), (45.0, 80.0));
        let mut visual = VisualConfig { use_db: true, cmap: "inferno".into(), ..Default::default() };
        visual.graph.insert("remove_titles".into(), Value::Bool(true));
        let cfg = ViewConfig::build(Some(range), visual, true);
        let text = cfg.dump();
        assert!(text.starts_with("{\n  \"app\": \"e-CALLISTO FITS Analyzer\""));
        let back = ViewConfig::parse(&text).unwrap();
        assert_eq!(back.range, Some(range));
        assert!(back.visual.use_db && back.visual.graph_bool("remove_titles"));
        assert_eq!(back.time_mode, "ut");
        assert!(ViewConfig::parse("[]").is_err());
        assert_eq!(ViewConfig::parse("{\"version\": 2}").unwrap_err(), "Unsupported view config version: 2");
        // Legacy frequency keys and the default visual block.
        let v = ViewConfig::parse("{\"range\": {\"time_start_s\": 0, \"time_stop_s\": 5, \"freq_start_mhz\": 10, \"freq_stop_mhz\": 20}}").unwrap();
        assert_eq!(v.range.unwrap().freq_max_mhz, 20.0);
        assert_eq!(v.visual.cmap, "Custom");
    }
}
