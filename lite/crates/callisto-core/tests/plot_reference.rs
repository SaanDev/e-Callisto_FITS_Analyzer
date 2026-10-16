//! Axis ticks against the full app's canvas.
//!
//! `lite/tools/capture_states.py` records, for each captured state, the tick
//! values and strings pyqtgraph computes for the visible view range. These
//! must come out the same from `callisto_core::plot`.

use std::path::{Path, PathBuf};

use callisto_core::plot::{tick_strings, tick_values, ut_tick_strings};
use callisto_core::radio::fits_io::{extract_ut_start_sec, load_callisto_fits};
use serde_json::Value;

fn reference_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../reference/windows")
}

fn ut_start() -> f64 {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/fixtures/INDIA-GAURI_20261007_041500_02.fit.gz");
    let loaded = load_callisto_fits(&path).unwrap();
    extract_ut_start_sec(&loaded.header0).unwrap()
}

#[test]
fn ticks_match_pyqtgraph() {
    let mut checked = 0;
    for entry in std::fs::read_dir(reference_dir()).unwrap() {
        let path = entry.unwrap().path();
        let name = path.file_name().unwrap().to_string_lossy().into_owned();
        if !name.starts_with("state_") || !name.ends_with(".json") {
            continue;
        }
        let doc: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        let plot = &doc["plot"];
        let Some(range) = plot["view_range"].as_array() else { continue };
        let range: Vec<f64> = range.iter().map(|v| v.as_f64().unwrap()).collect();
        for axis in ["left", "bottom"] {
            let a = &plot["axes"][axis];
            if a.is_null() || !a["custom_ticks"].is_null() {
                continue;
            }
            let rect: Vec<f64> = a["rect"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect();
            let (lo, hi, size) =
                if axis == "bottom" { (range[0], range[1], rect[2]) } else { (range[2], range[3], rect[3]) };
            // The empty canvas shows its axes with an SI prefix (x0.001).
            let scale = if lo.abs().max(hi.abs()) <= 1.0 { 1000.0 } else { 1.0 };
            let ut = a["label"].as_str() == Some("Time [UT]");
            let ours = tick_values(lo, hi, size, 1.0);
            let theirs = a["ticks"].as_array().unwrap();
            assert_eq!(ours.len(), theirs.len(), "{name} {axis}: number of levels");
            for ((spacing, values), level) in ours.iter().zip(theirs) {
                let want_spacing = level["spacing"].as_f64().unwrap();
                assert_eq!(*spacing, want_spacing, "{name} {axis}: spacing");
                let want: Vec<f64> = level["values"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect();
                assert_eq!(values, &want, "{name} {axis}: values at spacing {spacing}");
                let strings = if ut {
                    ut_tick_strings(values, *spacing, ut_start())
                } else {
                    tick_strings(values, scale, *spacing)
                };
                let want: Vec<&str> =
                    level["strings"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
                assert_eq!(strings, want, "{name} {axis}: strings at spacing {spacing}");
            }
            checked += 1;
        }
    }
    assert!(checked >= 10, "only {checked} axes checked");
}
