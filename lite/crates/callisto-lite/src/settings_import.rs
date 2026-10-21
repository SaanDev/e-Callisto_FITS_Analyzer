//! First start: the full app's settings, read from its `QSettings` store
//! (`SaanDev` / `e-CALLISTO FITS Analyzer`: the registry on Windows, a
//! plist on macOS, an INI file elsewhere) and taken over into Lite's own:
//! the theme, sidebar sections, frequency axis scale, the processing and
//! display range presets, the light-curve settings, the auto-clean switch
//! and the recent files and projects. The full app's store is only read.

use std::collections::{BTreeMap, HashMap};

use crate::app::{LightCurvePrefs, Settings};
use crate::session::DisplayRangePreset;
use crate::theme::ThemeMode;

pub const ORGANIZATION: &str = "SaanDev";
pub const APPLICATION: &str = "e-CALLISTO FITS Analyzer";

/// The full app's settings as `key/path` → value text (booleans as
/// `true` / `false`), or `None` when it has none here.
pub fn read_full_app() -> Option<HashMap<String, String>> {
    let values = platform::read()?;
    (!values.is_empty()).then_some(values)
}

/// Lite's settings from the full app's values; unknown or broken values
/// keep Lite's defaults.
pub fn import(values: &HashMap<String, String>) -> Settings {
    let mut s = Settings::default();
    let get = |k: &str| values.get(k).map(|v| v.trim()).filter(|v| !v.is_empty());
    if let Some(mode) = get("ui/theme_mode") {
        s.theme_mode = match mode.to_lowercase().as_str() {
            "light" => ThemeMode::Light,
            "dark" => ThemeMode::Dark,
            _ => ThemeMode::System,
        };
    }
    if let Some(Ok(serde_json::Value::Object(o))) =
        get("ui/sidebar_sections_main").map(serde_json::from_str::<serde_json::Value>)
    {
        s.sidebar_sections = o.into_iter().filter_map(|(k, v)| Some((k, v.as_bool()?))).collect::<BTreeMap<_, _>>();
    }
    if let Some(scale) = get("ui/frequency_axis_scale") {
        s.frequency_axis_log = scale.eq_ignore_ascii_case("log");
    }
    if let Some(v) = get("processing/max_auto_clean_isolated") {
        s.auto_clean_max_outliers = !matches!(v.to_lowercase().as_str(), "false" | "0");
    }
    if let Some(v) = get("processing/presets_json")
        && serde_json::from_str::<serde_json::Value>(v).is_ok()
    {
        s.presets_json = v.to_owned();
    }
    if let Some(v) = get("processing/default_preset_name") {
        s.default_preset_name = v.split_whitespace().collect::<Vec<_>>().join(" ");
    }
    if let Some(Ok(serde_json::Value::Array(items))) =
        get("view/display_range_presets_json").map(serde_json::from_str::<serde_json::Value>)
    {
        s.display_range_presets =
            items.into_iter().filter_map(|i| serde_json::from_value::<DisplayRangePreset>(i).ok()).collect();
    }
    if let Some(Ok(lc)) = get("analysis/light_curve_settings_json").map(serde_json::from_str::<LightCurvePrefs>) {
        s.light_curve = lc;
    }
    if let Some(Ok(serde_json::Value::Array(items))) = get("recent/fits").map(serde_json::from_str::<serde_json::Value>)
    {
        s.recent_fits = items
            .into_iter()
            .filter_map(|e| {
                let paths: Vec<String> = match e {
                    serde_json::Value::Array(a) => {
                        a.into_iter().filter_map(|p| p.as_str().map(str::to_owned)).collect()
                    }
                    serde_json::Value::String(p) => vec![p],
                    _ => Vec::new(),
                };
                let paths: Vec<String> = paths.into_iter().filter(|p| !p.trim().is_empty()).collect();
                (!paths.is_empty()).then_some(paths)
            })
            .take(crate::app::MAX_RECENT_ENTRIES)
            .collect();
    }
    if let Some(Ok(serde_json::Value::Array(items))) =
        get("recent/projects").map(serde_json::from_str::<serde_json::Value>)
    {
        s.recent_projects = items
            .into_iter()
            .filter_map(|p| p.as_str().map(str::to_owned))
            .filter(|p| !p.trim().is_empty())
            .take(crate::app::MAX_RECENT_ENTRIES)
            .collect();
    }
    s
}

/// One value of a Qt INI file (`QSettingsPrivate::iniUnescapedStringList`):
/// quoted parts, backslash escapes, and commas outside quotes separating
/// the items of a list (joined back with commas here).
pub fn ini_value(raw: &str) -> String {
    let mut items: Vec<String> = vec![String::new()];
    let mut quoted = false;
    let mut chars = raw.trim().chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '"' => quoted = !quoted,
            ',' if !quoted => {
                items.push(String::new());
                while chars.peek() == Some(&' ') {
                    chars.next();
                }
            }
            '\\' => {
                let Some(e) = chars.next() else { break };
                let out = match e {
                    'a' => '\u{7}',
                    'b' => '\u{8}',
                    'f' => '\u{c}',
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    'v' => '\u{b}',
                    'x' => {
                        let mut hex = String::new();
                        while hex.len() < 4 && chars.peek().is_some_and(|h| h.is_ascii_hexdigit()) {
                            hex.push(chars.next().unwrap());
                        }
                        u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32).unwrap_or('?')
                    }
                    other => other,
                };
                items.last_mut().unwrap().push(out);
            }
            _ => items.last_mut().unwrap().push(c),
        }
    }
    let joined = items.join(",");
    // `@` starts a variant (`@Variant(...)`); a literal one is written `@@`.
    joined.strip_prefix("@@").map(|r| format!("@{r}")).unwrap_or(joined)
}

/// `QSettings`' INI key escapes (`%XX` and `%UXXXX`).
fn ini_key(raw: &str) -> String {
    let mut out = String::new();
    let mut chars = raw.trim().chars().peekable();
    while let Some(c) = chars.next() {
        if c != '%' {
            out.push(c);
            continue;
        }
        let n = if chars.peek() == Some(&'U') {
            chars.next();
            4
        } else {
            2
        };
        let hex: String = (0..n).filter_map(|_| chars.next()).collect();
        match u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32) {
            Some(ch) => out.push(ch),
            None => {
                out.push('%');
                out.push_str(&hex);
            }
        }
    }
    out
}

/// A Qt INI file as `group/key` → value (`[General]` keys at the top level).
pub fn parse_ini(text: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let mut group = String::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with(';') || line.starts_with('#') {
            continue;
        }
        if let Some(name) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
            let name = ini_key(name);
            group = if name.eq_ignore_ascii_case("general") { String::new() } else { name };
            continue;
        }
        let Some((k, v)) = line.split_once('=') else { continue };
        let key = ini_key(k).replace('\\', "/");
        let full = if group.is_empty() { key } else { format!("{group}/{key}") };
        out.insert(full, ini_value(v));
    }
    out
}

#[cfg(windows)]
mod platform {
    use std::collections::HashMap;

    use winreg::RegKey;
    use winreg::enums::HKEY_CURRENT_USER;

    fn walk(key: &RegKey, prefix: &str, out: &mut HashMap<String, String>) {
        for (name, value) in key.enum_values().flatten() {
            let text = match value.vtype {
                winreg::enums::REG_DWORD => key.get_value::<u32, _>(&name).map(|v| v.to_string()).ok(),
                winreg::enums::REG_QWORD => key.get_value::<u64, _>(&name).map(|v| v.to_string()).ok(),
                _ => key.get_value::<String, _>(&name).ok(),
            };
            if let Some(t) = text {
                out.insert(format!("{prefix}{name}"), t);
            }
        }
        for sub in key.enum_keys().flatten() {
            if let Ok(k) = key.open_subkey(&sub) {
                walk(&k, &format!("{prefix}{sub}/"), out);
            }
        }
    }

    /// `HKEY_CURRENT_USER\Software\<organization>\<application>`.
    pub fn read() -> Option<HashMap<String, String>> {
        let path = format!("Software\\{}\\{}", super::ORGANIZATION, super::APPLICATION);
        let key = RegKey::predef(HKEY_CURRENT_USER).open_subkey(path).ok()?;
        let mut out = HashMap::new();
        walk(&key, "", &mut out);
        Some(out)
    }
}

#[cfg(target_os = "macos")]
mod platform {
    use std::collections::HashMap;

    /// `~/Library/Preferences/com.<organization>.<application>.plist`,
    /// keys with `/` written as `.`.
    pub fn read() -> Option<HashMap<String, String>> {
        let home = std::env::var_os("HOME")?;
        let file = std::path::Path::new(&home).join("Library/Preferences").join(format!(
            "com.{}.{}.plist",
            super::ORGANIZATION.to_lowercase(),
            super::APPLICATION
        ));
        let value = plist::Value::from_file(file).ok()?;
        let dict = value.as_dictionary()?;
        let mut out = HashMap::new();
        for (k, v) in dict {
            let text = match v {
                plist::Value::String(s) => s.clone(),
                plist::Value::Boolean(b) => b.to_string(),
                plist::Value::Integer(i) => i.to_string(),
                plist::Value::Real(r) => r.to_string(),
                _ => continue,
            };
            out.insert(k.replace('.', "/"), text);
        }
        Some(out)
    }
}

#[cfg(not(any(windows, target_os = "macos")))]
mod platform {
    use std::collections::HashMap;

    /// `$XDG_CONFIG_HOME/<organization>/<application>.conf`.
    pub fn read() -> Option<HashMap<String, String>> {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .map(std::path::PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| std::path::Path::new(&h).join(".config")))?;
        let file = base.join(super::ORGANIZATION).join(format!("{}.conf", super::APPLICATION));
        Some(super::parse_ini(&std::fs::read_to_string(file).ok()?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_a_qt_ini_file() {
        let text = "[General]\nfoo=bar\n\n[ui]\ntheme_mode=dark\nsidebar_sections_main=\"{\\\"Units\\\": false, \\\"Graph Properties\\\": true}\"\nfrequency_axis_scale=log\n\n[recent]\nprojects=\"[\\\"C:\\\\\\\\data\\\\\\\\a.efaproj\\\"]\"\n\n[processing]\nmax_auto_clean_isolated=false\ndefault_preset_name=  My   preset\n";
        let v = parse_ini(text);
        assert_eq!(v["foo"], "bar");
        assert_eq!(v["ui/theme_mode"], "dark");
        assert_eq!(v["ui/sidebar_sections_main"], "{\"Units\": false, \"Graph Properties\": true}");
        assert_eq!(v["recent/projects"], "[\"C:\\\\data\\\\a.efaproj\"]");
        let s = import(&v);
        assert_eq!(s.theme_mode, ThemeMode::Dark);
        assert_eq!(s.sidebar_sections.get("Units"), Some(&false));
        assert_eq!(s.sidebar_sections.get("Graph Properties"), Some(&true));
        assert!(s.frequency_axis_log);
        assert!(!s.auto_clean_max_outliers);
        assert_eq!(s.default_preset_name, "My preset");
        assert_eq!(s.recent_projects, vec!["C:\\data\\a.efaproj".to_owned()]);
    }

    /// The INI file Qt wrote (`lite/parity/make_settings_reference.py`).
    #[test]
    fn reads_the_ini_qt_writes() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/settings");
        let (Ok(ini), Ok(expected)) =
            (std::fs::read_to_string(dir.join("full_app.ini")), std::fs::read_to_string(dir.join("expected.json")))
        else {
            eprintln!("no reference: run lite/parity/make_settings_reference.py");
            return;
        };
        let values = parse_ini(&ini);
        let expected: HashMap<String, String> = serde_json::from_str(&expected).unwrap();
        for (k, v) in &expected {
            assert_eq!(values.get(k), Some(v), "{k}");
        }
        let s = import(&values);
        assert_eq!(s.theme_mode, ThemeMode::Dark);
        assert_eq!(s.default_preset_name, "Wide = clip; \u{e9}");
        assert_eq!(s.display_range_presets[0].name, "Burst, morning");
        assert_eq!(s.light_curve.line_style, "dashed");
        assert_eq!(s.recent_fits[0], vec!["C:\\data\\a b.fit".to_owned(), "C:\\data\\c,d.fit.gz".to_owned()]);
        assert_eq!(s.recent_projects, vec!["C:\\data\\p.efaproj".to_owned()]);
    }

    #[test]
    fn unescapes_values_and_keys() {
        assert_eq!(ini_value("a, b"), "a,b");
        assert_eq!(ini_value("\"a, b\""), "a, b");
        assert_eq!(ini_value("\\x00e9t\\xe9"), "\u{e9}t\u{e9}");
        assert_eq!(ini_value("@@at"), "@at");
        assert_eq!(ini_key("a%20b%U00e9"), "a b\u{e9}");
    }

    #[test]
    fn imports_presets_and_recent_files() {
        let mut v = HashMap::new();
        v.insert("recent/fits".to_owned(), "[[\"a.fit\", \"b.fit\"], \"c.fit\", [], 5]".to_owned());
        v.insert(
            "view/display_range_presets_json".to_owned(),
            "[{\"name\": \"Burst\", \"version\": 1, \"created_at\": \"2026-10-01T10:00:00+05:30\", \"range\": {\"time_start_s\": 10.0, \"time_stop_s\": 20.0, \"freq_min_mhz\": 45.0, \"freq_max_mhz\": 80.0}, \"time_mode\": \"ut\"}, {\"bad\": 1}]".to_owned(),
        );
        v.insert("processing/presets_json".to_owned(), "not json".to_owned());
        v.insert("ui/theme_mode".to_owned(), "LIGHT".to_owned());
        let s = import(&v);
        assert_eq!(s.recent_fits, vec![vec!["a.fit".to_owned(), "b.fit".to_owned()], vec!["c.fit".to_owned()]]);
        assert_eq!(s.display_range_presets.len(), 1);
        assert_eq!(s.display_range_presets[0].name, "Burst");
        assert_eq!(s.presets_json, Settings::default().presets_json);
        assert_eq!(s.theme_mode, ThemeMode::Light);
    }
}
