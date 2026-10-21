//! Projects against the full app's (`parity/make_project_reference.py`):
//! Lite opens each project the full app wrote and saves it again; the file
//! must hold what the full app itself writes back after opening it
//! (`<name>.roundtrip.efaproj`): the same meta, key for key, and the same
//! arrays, dtype, shape and values. Only the save time, the log's
//! timestamps and `app_version` may differ (and a legacy file's new
//! analysis run id). The parts Lite does not have go back unchanged.
//!
//! Skipped when the references have not been generated.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use callisto_core::project::{Npy, read_project};
use callisto_core::pyjson::{Json, Object};
use callisto_lite::project::ProjectState;
use callisto_lite::session::Session;

fn refs() -> Option<PathBuf> {
    let d = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/out/projects");
    d.join("raw.efaproj").exists().then_some(d)
}

fn scratch(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("callisto_project_parity_{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d.join(name)
}

/// The differences between two JSON values, as paths.
fn diff(a: &Json, b: &Json, path: &str, out: &mut Vec<String>) {
    match (a, b) {
        (Json::Object(x), Json::Object(y)) => {
            for k in x.keys().chain(y.keys().filter(|k| !x.contains_key(*k))) {
                match (x.get(k), y.get(k)) {
                    (Some(p), Some(q)) => diff(p, q, &format!("{path}/{k}"), out),
                    (p, q) => out.push(format!("{path}/{k}: {p:?} vs {q:?}")),
                }
            }
        }
        (Json::Array(x), Json::Array(y)) if x.len() == y.len() => {
            for (i, (p, q)) in x.iter().zip(y).enumerate() {
                diff(p, q, &format!("{path}[{i}]"), out);
            }
        }
        (Json::Float(x), Json::Float(y)) if x.is_nan() && y.is_nan() => {}
        _ if a == b => {}
        _ => out.push(format!("{path}: {} vs {}", short(a), short(b))),
    }
}

fn short(v: &Json) -> String {
    let t = callisto_core::pyjson::dumps_compact(v);
    if t.len() > 160 { format!("{}…", &t[..160]) } else { t }
}

/// Drops what may differ: the save time, Lite's version, the log's times.
fn comparable(mut meta: Object, new_run_id: bool) -> Object {
    meta.remove("created_at");
    meta.remove("app_version");
    if let Some(Json::Array(log)) = meta.get_mut("processing_log") {
        for e in log.iter_mut() {
            if let Some(o) = e.as_object_mut() {
                o.remove("ts");
            }
        }
    }
    if new_run_id {
        // A legacy file has no identity: each app makes its own.
        meta.remove("project_id");
    }
    if new_run_id && let Some(Json::Object(s)) = meta.get_mut("analysis_session") {
        s.remove("analysis_run_id");
        s.remove("updated_at");
    }
    meta
}

fn array_diffs(a: &BTreeMap<String, Npy>, b: &BTreeMap<String, Npy>, out: &mut Vec<String>) {
    for k in a.keys().chain(b.keys().filter(|k| !a.contains_key(*k))) {
        match (a.get(k), b.get(k)) {
            (Some(x), Some(y)) => {
                if x.descr != y.descr || x.shape != y.shape {
                    out.push(format!("array {k}: {} {:?} vs {} {:?}", x.descr, x.shape, y.descr, y.shape));
                } else if x.data() != y.data() {
                    let (p, q) = (x.to_f64().unwrap_or_default(), y.to_f64().unwrap_or_default());
                    let n = p.iter().zip(&q).filter(|(u, v)| u != v && !(u.is_nan() && v.is_nan())).count();
                    if n > 0 {
                        out.push(format!("array {k}: {n} values differ"));
                    }
                }
            }
            (x, _) => out.push(format!("array {k}: only in {}", if x.is_some() { "Lite" } else { "the full app" })),
        }
    }
}

#[test]
fn projects_round_trip_like_the_full_app() {
    let Some(dir) = refs() else {
        eprintln!("no reference projects: run parity/make_project_reference.py");
        return;
    };
    let mut failures = Vec::new();
    for name in ["raw", "processed", "analysis", "combined", "legacy"] {
        let mut s = Session::new();
        let mut p = ProjectState::new(scratch(&format!("{name}_cache")));
        p.open(&mut s, &dir.join(format!("{name}.efaproj"))).unwrap_or_else(|e| panic!("{name}: {e}"));
        s.flush_deferred();
        let pending = s.take_pending_analysis_restore();
        let status = s.status_message().map(|(t, _)| t.to_owned()).unwrap_or_default();
        let opened: Json =
            callisto_core::pyjson::loads(&std::fs::read_to_string(dir.join(format!("{name}.open.json"))).unwrap()).unwrap();
        if opened.get("status").and_then(Json::as_str) != Some(status.as_str()) {
            failures.push(format!("{name}: status {status:?}, the full app {:?}", opened.get("status")));
        }
        let windows = pending.map(|p| (p.open_max, p.open_analyzer, p.open_type_ii)).unwrap_or_default();
        let want = |k: &str| opened.get(k).is_some_and(Json::truthy);
        if windows != (want("max_window"), want("analyzer_window"), want("type_ii_window")) {
            failures.push(format!("{name}: analysis windows {windows:?}"));
        }

        let out = scratch(&format!("{name}.lite.efaproj"));
        p.save(&mut s, &out, p.storage).unwrap();
        // `parity/check_lite_projects.py` reads these back with the full app.
        if std::env::var_os("CALLISTO_KEEP_PROJECTS").is_some() {
            let keep = dir.join("lite");
            std::fs::create_dir_all(&keep).unwrap();
            std::fs::copy(&out, keep.join(format!("{name}.efaproj"))).unwrap();
        }
        let lite = read_project(&out, None).unwrap();
        let full = read_project(&dir.join(format!("{name}.roundtrip.efaproj")), None).unwrap();
        let new_run_id = name == "legacy";
        let mut d = Vec::new();
        diff(&Json::Object(comparable(lite.meta.clone(), new_run_id)), &Json::Object(comparable(full.meta.clone(), new_run_id)), "", &mut d);
        array_diffs(&lite.arrays, &full.arrays, &mut d);
        if !d.is_empty() {
            failures.extend(d.into_iter().map(|x| format!("{name}: {x}")));
        }
    }

    // Presets: the processed state's preset, and that preset applied to the raw project.
    let full_preset = callisto_core::pyjson::loads(&std::fs::read_to_string(dir.join("processed.preset.json")).unwrap()).unwrap();
    let mut s = Session::new();
    let mut p = ProjectState::new(scratch("preset_cache"));
    p.open(&mut s, &dir.join("processed.efaproj")).unwrap();
    let lite_preset = callisto_core::presets::build("Processed look", s.preset_settings_payload()).unwrap();
    // The view config Export View Config writes for this state.
    let lite_view = callisto_core::pyjson::loads(&s.view_config_text().unwrap()).unwrap();
    let full_view = callisto_core::pyjson::loads(&std::fs::read_to_string(dir.join("processed.efaview.json")).unwrap()).unwrap();
    let strip = |mut v: Json| {
        v.as_object_mut().unwrap().remove("created_at");
        v
    };
    let mut view_diffs = Vec::new();
    diff(&strip(lite_view), &strip(full_view), "view_config", &mut view_diffs);
    failures.extend(view_diffs);
    let mut d = Vec::new();
    diff(lite_preset.get("settings").unwrap(), full_preset.get("settings").unwrap(), "preset", &mut d);
    let mut s = Session::new();
    let mut p = ProjectState::new(scratch("preset_cache2"));
    p.open(&mut s, &dir.join("raw.efaproj")).unwrap();
    s.flush_deferred();
    s.apply_preset_payload(&full_preset, true, true, true).unwrap();
    let out = scratch("raw_with_preset.lite.efaproj");
    p.save(&mut s, &out, p.storage).unwrap();
    let lite = read_project(&out, None).unwrap();
    let full = read_project(&dir.join("raw_with_preset.efaproj"), None).unwrap();
    diff(&Json::Object(comparable(lite.meta, false)), &Json::Object(comparable(full.meta, false)), "raw_with_preset", &mut d);
    array_diffs(&lite.arrays, &full.arrays, &mut d);
    failures.extend(d);

    // Export Analysis Log and Export Provenance Report after the round trip.
    for name in ["processed", "analysis"] {
        let mut s = Session::new();
        let mut p = ProjectState::new(scratch(&format!("{name}_export_cache")));
        p.open(&mut s, &dir.join(format!("{name}.efaproj"))).unwrap();
        s.flush_deferred();
        let _ = s.take_pending_analysis_restore();
        let saved = scratch(&format!("{name}.roundtrip.efaproj"));
        p.save(&mut s, &saved, p.storage).unwrap();
        let stem = scratch(&format!("{name}_export"));
        for suffix in ["_analysis_log.csv", "_analysis_log.txt"] {
            let _ = std::fs::remove_file(format!("{}{suffix}", stem.display()));
        }
        s.export_analysis_log(&stem, p.path.as_deref()).unwrap();
        s.export_provenance(&stem).unwrap();
        let read = |d: &Path, suffix: &str| std::fs::read_to_string(d.join(format!("{name}_export{suffix}"))).unwrap();
        let lite_dir = stem.parent().unwrap().to_path_buf();
        // The time, the run id and the project's folder may differ.
        let same_line = |a: &str, b: &str| {
            let base = |t: &str| t.rsplit(['\\', '/']).next().unwrap_or(t).to_owned();
            a == b
                || ["Analysis Log Entry UTC:", "Analysis Run ID:", "Generated at:", "- Name:", "- Version:", "- Platform:"].iter().any(|p| a.starts_with(p) && b.starts_with(p))
                || (a.starts_with("Project:") && base(a) == base(b))
                || (a.starts_with("- `") && b.starts_with("- `") && a.split_once("` ").map(|x| x.1) == b.split_once("` ").map(|x| x.1))
        };
        for suffix in ["_analysis_log.txt", "_provenance.md"] {
            let (l, f) = (read(&lite_dir, suffix), read(&dir, suffix));
            let (l, f): (Vec<&str>, Vec<&str>) = (l.lines().collect(), f.lines().collect());
            if l.len() != f.len() {
                failures.push(format!("{name}{suffix}: {} lines, the full app {}", l.len(), f.len()));
            }
            for (a, b) in l.iter().zip(&f) {
                if !same_line(a, b) {
                    failures.push(format!("{name}{suffix}: {a:?} vs {b:?}"));
                }
            }
        }
        let csv = |d: &Path| read(d, "_analysis_log.csv").lines().map(|l| l.to_owned()).collect::<Vec<_>>();
        let (l, f) = (csv(&lite_dir), csv(&dir));
        if l[0] != f[0] {
            failures.push(format!("{name} csv header differs"));
        }
        let cells = |row: &str| row.split(',').skip(2).map(|c| c.to_owned()).collect::<Vec<_>>();
        if cells(&l[1]) != cells(&f[1]) {
            failures.push(format!("{name} csv row: {:?} vs {:?}", cells(&l[1]), cells(&f[1])));
        }
        let strip = |text: String| {
            let mut v = callisto_core::pyjson::loads(&text).unwrap();
            let o = v.as_object_mut().unwrap();
            o.remove("generated_at");
            o.remove("app");
            if let Some(Json::Array(log)) = o.get_mut("operation_log") {
                for e in log.iter_mut() {
                    e.as_object_mut().unwrap().remove("ts");
                }
            }
            v
        };
        let mut d = Vec::new();
        diff(&strip(read(&lite_dir, "_provenance.json")), &strip(read(&dir, "_provenance.json")), &format!("{name} provenance"), &mut d);
        failures.extend(d);
    }

    // Export to FIT with the recommended BITPIX: the same bytes.
    for name in ["raw", "processed", "combined"] {
        let mut s = Session::new();
        let mut p = ProjectState::new(scratch(&format!("{name}_fit_cache")));
        p.open(&mut s, &dir.join(format!("{name}.efaproj"))).unwrap();
        s.flush_deferred();
        let data = s.fits_export_source().unwrap();
        let bitpix = callisto_core::radio::fits_export::recommend_bitpix(&data.data);
        let out = scratch(&format!("{name}_export.fit"));
        s.export_fits(&out, bitpix).unwrap();
        let (lite, full) = (std::fs::read(&out).unwrap(), std::fs::read(dir.join(format!("{name}_export.fit"))).unwrap());
        if lite != full {
            let first = lite.iter().zip(&full).position(|(a, b)| a != b).unwrap_or(lite.len().min(full.len()));
            let card = |b: &[u8]| String::from_utf8_lossy(&b[first / 80 * 80..(first / 80 * 80 + 80).min(b.len())]).into_owned();
            failures.push(format!("{name}_export.fit: {} vs {} bytes, first difference at {first}: {:?} vs {:?}", lite.len(), full.len(), card(&lite), card(&full)));
        }
    }

    // Parts Lite does not have go back as they were.
    let mut s = Session::new();
    let mut p = ProjectState::new(scratch("passthrough_cache"));
    let src = dir.join("passthrough.efaproj");
    p.open(&mut s, &src).unwrap();
    let out = scratch("passthrough.lite.efaproj");
    p.save(&mut s, &out, p.storage).unwrap();
    let a = read_project(&src, Some(&|_m: &Object| scratch("pt_a"))).unwrap();
    let b = read_project(&out, Some(&|_m: &Object| scratch("pt_b"))).unwrap();
    for key in ["solar", "geomagnetic", "parts", "storage", "files"] {
        let mut d = Vec::new();
        diff(a.meta.get(key).unwrap_or(&Json::Null), b.meta.get(key).unwrap_or(&Json::Null), key, &mut d);
        failures.extend(d.into_iter().map(|x| format!("passthrough: {x}")));
    }
    for key in ["solar_mask", "geomagnetic_h_nt"] {
        if a.arrays.get(key) != b.arrays.get(key) {
            failures.push(format!("passthrough: array {key} changed"));
        }
    }
    for (member, path) in &a.files {
        let back = b.files.get(member).map(|p| std::fs::read(p).unwrap());
        if back != Some(std::fs::read(path).unwrap()) {
            failures.push(format!("passthrough: embedded {member} changed"));
        }
    }
    let _ = std::fs::remove_dir_all(scratch("").parent().unwrap().join(format!("callisto_project_parity_{}", std::process::id())));
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}
