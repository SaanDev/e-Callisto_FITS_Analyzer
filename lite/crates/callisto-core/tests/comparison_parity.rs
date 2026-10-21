//! The comparison's panels against the full app's
//! (`lite/tools/capture_tools.py`, `reference/windows/tools/window_comparison_*`):
//! the combinations, labels, aligned extents and colour levels of each state.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use callisto_core::radio::comparison::{
    Alignment, ColorScale, NoiseMethod, NoiseSettings, PanelOptions, apply_noise, datasets_from_paths, panel_payloads,
};
use serde_json::Value;

fn lite_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn fixture(name: &str) -> String {
    lite_dir().join("parity/fixtures").join(name).to_string_lossy().into_owned()
}

fn reference(state: &str) -> Option<Value> {
    let path = lite_dir().join(format!("reference/windows/tools/window_comparison_{state}_light.json"));
    Some(serde_json::from_str(&std::fs::read_to_string(path).ok()?).unwrap())
}

#[test]
fn panels_match_full_app() {
    let four = [
        "GERMANY-DLR_20261007_050000_01.fit.gz",
        "TRIEST_20261007_050000_57.fit.gz",
        "INDIA-GAURI_20261007_050000_02.fit.gz",
        "AUSTRIA-OE3FLB_20261007_051500_55.fit.gz",
    ];
    let mut six: Vec<String> = four.iter().map(|f| fixture(f)).collect();
    six.push(fixture("GERMANY-DLR_20261007_050000_02.fit.gz"));
    six.push(fixture("TRIEST_20261007_050000_59.fit.gz"));
    let clip = NoiseSettings { method: NoiseMethod::Clip, clip_low: -10.0, clip_high: 30.0, log_scale: false };
    let after_clip = NoiseSettings { method: NoiseMethod::None, ..clip };
    let digits =
        PanelOptions { alignment: Alignment::Ut, use_db: false, color_scale: ColorScale::Shared, manual_limits: None };
    let states: Vec<(&str, Vec<String>, NoiseSettings, PanelOptions)> = vec![
        ("one", vec![fixture("INDIA-GAURI_20261007_041500_02.fit.gz")], NoiseSettings::default(), digits.clone()),
        ("four", four.iter().map(|f| fixture(f)).collect(), NoiseSettings::default(), digits.clone()),
        ("combined", six.clone(), NoiseSettings::default(), digits.clone()),
        ("clip", six.clone(), clip, digits.clone()),
        (
            "per_station",
            six.clone(),
            after_clip,
            PanelOptions { use_db: true, color_scale: ColorScale::PerStation, ..digits.clone() },
        ),
    ];
    let mut failures = Vec::new();
    let mut checked = 0;
    for (state, paths, noise, options) in states {
        let Some(r) = reference(state) else { continue };
        let datasets = datasets_from_paths(&paths).unwrap();
        let processed: Vec<_> =
            datasets.iter().map(|d| Arc::new(apply_noise(&d.data, noise, d.gap_row_mask.as_deref()))).collect();
        let cold: Vec<f64> = datasets.iter().map(|_| noise.clip_low).collect();
        let (payloads, mode, _) = panel_payloads(&datasets, &processed, &cold, &options);
        assert_eq!(mode, Alignment::Ut, "{state}");
        let want = r["panels"].as_array().unwrap();
        if want.len() != payloads.len() {
            failures.push(format!("{state}: {} panels, full app {}", payloads.len(), want.len()));
            continue;
        }
        for (i, (p, w)) in payloads.iter().zip(want).enumerate() {
            if p.dataset.label != w["label"].as_str().unwrap() {
                failures.push(format!("{state} {i}: label {:?}, full app {}", p.dataset.label, w["label"]));
            }
            let near = |a: f64, b: &Value, tol: f64| (a - b.as_f64().unwrap()).abs() <= tol * (1.0 + a.abs());
            let ext = &w["pg_extent"];
            if !(0..4).all(|k| near(p.pg_extent[k], &ext[k], 1e-9)) {
                failures.push(format!("{state} {i}: extent {:?}, full app {ext}", p.pg_extent));
            }
            // float32 arithmetic on both sides; the levels come back as Python floats.
            let lv = &w["levels"];
            if !(near(p.levels.0, &lv[0], 1e-6) && near(p.levels.1, &lv[1], 1e-6)) {
                failures.push(format!("{state} {i}: levels {:?}, full app {lv}", p.levels));
            }
        }
        checked += 1;
    }
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
    eprintln!("{checked} comparison states checked");
}
