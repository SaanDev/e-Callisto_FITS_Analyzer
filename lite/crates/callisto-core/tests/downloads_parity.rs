//! The downloader and import code against the full app's, case by case.
//!
//! `lite/parity/make_download_reference.py` writes `parity/out/downloads.json`
//! with the full app's results (Learmonth chunks and FIT files, burst
//! catalogs and their filters, archive candidates, spectral overviews,
//! preview panels, import payloads). This test replays every case.
//!
//! Without references the test only prints a note, unless
//! `CALLISTO_PARITY_REQUIRED=1`.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use callisto_core::radio::burst_list::{self, BurstEvent};
use callisto_core::radio::downloader::{self, Candidate, ImportPayload, ImportProgress};
use callisto_core::radio::learmonth;
use callisto_core::radio::naming::parse_archive_filename;
use callisto_core::radio::spectral_overview::{self, DEFAULT_PANEL_RENDER_COLUMNS, OverviewSource};
use chrono::{NaiveDate, NaiveDateTime};
use serde_json::Value;

fn parity_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity")
}

fn read_npy(path: &Path) -> (Vec<usize>, Vec<f64>) {
    let bytes = std::fs::read(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let (len, start) = match bytes[6] {
        1 => (u16::from_le_bytes([bytes[8], bytes[9]]) as usize, 10),
        _ => (u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize, 12),
    };
    let header = std::str::from_utf8(&bytes[start..start + len]).unwrap();
    let shape_text = header.split("'shape': (").nth(1).unwrap().split(')').next().unwrap();
    let shape = shape_text.split(',').filter_map(|s| s.trim().parse().ok()).collect();
    let data = bytes[start + len..].as_chunks::<8>().0.iter().map(|c| f64::from_le_bytes(*c)).collect();
    (shape, data)
}

fn dt(v: &Value) -> NaiveDateTime {
    NaiveDateTime::parse_from_str(v.as_str().unwrap(), "%Y-%m-%d %H:%M:%S").unwrap()
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().map(|x| x.as_str().unwrap().to_owned()).collect()).unwrap_or_default()
}

fn fixture(name: &str) -> String {
    parity_dir().join("fixtures").join(name).to_string_lossy().into_owned()
}

#[derive(Default)]
struct Checker {
    failures: Vec<String>,
    checked: BTreeMap<String, usize>,
}

impl Checker {
    fn eq<T: PartialEq + std::fmt::Debug>(&mut self, case: &str, what: &str, got: T, want: T) {
        if got != want {
            self.failures.push(format!("{case}: {what}: got {got:?}, want {want:?}"));
        }
    }

    fn close(&mut self, case: &str, what: &str, got: &[f64], want: &[f64], tol: f64) {
        if got.len() != want.len() {
            return self.failures.push(format!("{case}: {what}: length {} vs {}", got.len(), want.len()));
        }
        let bad = got
            .iter()
            .zip(want)
            .enumerate()
            .find(|(_, (g, w))| if w.is_nan() { !g.is_nan() } else { let d = (*g - *w).abs(); d.is_nan() || d > tol * (1.0 + w.abs()) });
        if let Some((i, (g, w))) = bad {
            self.failures.push(format!("{case}: {what}: first difference at {i}: got {g} want {w}"));
        }
    }
}

fn candidate_json(c: &Candidate) -> Value {
    serde_json::json!({
        "station": c.station,
        "observed_at": c.observed_at.map(|t| t.format("%Y-%m-%d %H:%M:%S").to_string()),
        "filename": c.filename,
        "url": c.url,
        "receiver_id": c.receiver_id,
    })
}

fn event_json(e: &BurstEvent) -> Value {
    serde_json::json!({
        "start": e.start_utc.format("%Y-%m-%d %H:%M:%S").to_string(),
        "end": e.end_utc.format("%Y-%m-%d %H:%M:%S").to_string(),
        "type": e.burst_type,
        "fmin": e.frequency_min_mhz,
        "fmax": e.frequency_max_mhz,
        "stations": e.stations,
        "remarks": e.remarks,
        "source_url": e.source_url,
        "raw_line": e.raw_line,
    })
}

fn all_catalog_events(cases: &[Value]) -> Vec<BurstEvent> {
    let mut events = Vec::new();
    for c in cases.iter().filter(|c| c["kind"] == "catalog") {
        events.extend(burst_list::parse_burst_list(c["text"].as_str().unwrap(), c["url"].as_str().unwrap()));
    }
    events
}

fn run_case(c: &mut Checker, case: &Value, cases: &[Value], out: &Path) {
    let n = case["name"].as_str().unwrap();
    let kind = case["kind"].as_str().unwrap();
    *c.checked.entry(kind.to_owned()).or_default() += 1;
    match kind {
        "learmonth" => {
            let srs = out.join(case["srs"].as_str().unwrap());
            let chunks = learmonth::list_learmonth_chunks(&srs).unwrap();
            let got: Vec<Value> = chunks
                .iter()
                .map(|k| {
                    serde_json::json!({
                        "index": k.index,
                        "start": k.start_dt.format("%Y-%m-%d %H:%M:%S").to_string(),
                        "end": k.end_dt.format("%Y-%m-%d %H:%M:%S").to_string(),
                        "scans": k.scan_count,
                        "offset_start": k.offset_start,
                        "offset_end": k.offset_end,
                        "partial": k.is_partial,
                    })
                })
                .collect();
            c.eq(n, "chunks", Value::Array(got), case["chunks"].clone());
            let names: Vec<String> = chunks.iter().map(learmonth::learmonth_fit_filename).collect();
            c.eq(n, "fit names", names, strings(&case["fit_names"]));
            let dir = std::env::temp_dir().join(format!("lite_lm_parity_{}", std::process::id()));
            for (k, want) in chunks.iter().zip(strings(&case["fits"])) {
                let path = learmonth::write_learmonth_chunk_fit(&srs, k, &dir.join(format!("{}.fit", k.index))).unwrap();
                let got = std::fs::read(path).unwrap();
                let want = std::fs::read(out.join(&want)).unwrap();
                if got != want {
                    let at = got.iter().zip(&want).position(|(a, b)| a != b).unwrap_or(got.len().min(want.len()));
                    let line = |b: &[u8]| String::from_utf8_lossy(&b[at / 80 * 80..(at / 80 * 80 + 80).min(b.len())]).into_owned();
                    c.failures.push(format!(
                        "{n}: chunk {} FIT differs at byte {at} ({} vs {} bytes): {:?} vs {:?}",
                        k.index,
                        got.len(),
                        want.len(),
                        line(&got),
                        line(&want)
                    ));
                }
            }
            let _ = std::fs::remove_dir_all(dir);
            let day = dt(&case["chunks"][0]["start"]).date();
            c.eq(n, "url", learmonth::resolve_learmonth_url(day), case["url"].as_str().unwrap().to_owned());
        }
        "catalog" => {
            let events = burst_list::parse_burst_list(case["text"].as_str().unwrap(), case["url"].as_str().unwrap());
            for (i, (g, w)) in events.iter().map(event_json).zip(case["events"].as_array().unwrap()).enumerate() {
                c.eq(n, &format!("event {i}"), &g, w);
            }
            c.eq(n, "event count", events.len(), case["events"].as_array().unwrap().len());
        }
        "burst_filter" => {
            let events = all_catalog_events(cases);
            let got = burst_list::filter_burst_events(
                &events,
                case["burst_type"].as_str().unwrap(),
                case["station"].as_str().unwrap(),
                case["text"].as_str().unwrap(),
                case["fmin"].as_f64(),
                case["fmax"].as_f64(),
            )
            .unwrap();
            let lines: Vec<String> = got.iter().map(|e| e.raw_line.clone()).collect();
            c.eq(n, "raw lines", lines, strings(&case["raw_lines"]));
        }
        "burst_links" => {
            let links = burst_list::listing_links(case["html"].as_str().unwrap(), case["directory"].as_str().unwrap());
            c.eq(n, "links", links, strings(&case["links"]));
        }
        "source_periods" => {
            let dir = case["directory"].as_str().unwrap();
            for (name, want) in case["periods"].as_object().unwrap() {
                let got = burst_list::source_period(&format!("{dir}{name}")).map(|(a, b, t)| {
                    serde_json::json!([a.format("%Y-%m-%d").to_string(), b.format("%Y-%m-%d").to_string(), t])
                });
                c.eq(n, name, got.unwrap_or(Value::Null), want.clone());
            }
        }
        "event_filter" => {
            let found = downloader::filter_event_candidates(
                &strings(&case["hrefs"]),
                case["day_url"].as_str().unwrap(),
                &strings(&case["stations"]),
                dt(&case["start"]),
                dt(&case["stop"]),
            );
            let got: Vec<Value> = downloader::sort_event_candidates(found).iter().map(candidate_json).collect();
            c.eq(n, "candidates", Value::Array(got), case["candidates"].clone());
        }
        "overview_filter" => {
            let date = NaiveDate::parse_from_str(case["date"].as_str().unwrap(), "%Y-%m-%d").unwrap();
            let found = downloader::filter_spectral_overview_candidates(
                &strings(&case["hrefs"]),
                case["day_url"].as_str().unwrap(),
                case["station"].as_str().unwrap(),
                date,
            );
            let got: Vec<Value> = found.iter().map(candidate_json).collect();
            c.eq(n, "candidates", Value::Array(got), case["candidates"].clone());
            for (code, members) in downloader::group_by_focus_code(&found) {
                let names: Vec<String> = members.iter().map(|m| m.filename.clone()).collect();
                c.eq(n, &format!("group {code}"), names, strings(&case["groups"][&code]));
                c.eq(
                    n,
                    &format!("label {code}"),
                    downloader::focus_group_label(&code, &members),
                    case["labels"][&code].as_str().unwrap().to_owned(),
                );
            }
        }
        "burst_fits_filter" => {
            let (start, stop) = (dt(&case["start"]), dt(&case["stop"]));
            let found = burst_list::filter_burst_candidates(
                &strings(&case["hrefs"]),
                case["day_url"].as_str().unwrap(),
                &strings(&case["stations"]),
                start,
                stop,
            )
            .unwrap();
            let got: Vec<Value> = found.iter().map(candidate_json).collect();
            c.eq(n, "candidates", Value::Array(got), case["candidates"].clone());
            let dates: Vec<String> = burst_list::burst_archive_dates(start, stop)
                .unwrap()
                .iter()
                .map(|d| d.format("%Y-%m-%d").to_string())
                .collect();
            c.eq(n, "dates", dates, strings(&case["dates"]));
        }
        "overview" => {
            let sources: Vec<OverviewSource> = strings(&case["files"])
                .iter()
                .map(|f| {
                    let (station, observed_at, focus_code) = parse_archive_filename(f).unwrap();
                    OverviewSource { path: fixture(f), station, observed_at, focus_code, filename: f.clone() }
                })
                .collect();
            let r = spectral_overview::build_spectral_overview(
                &sources,
                DEFAULT_PANEL_RENDER_COLUMNS,
                &|| false,
                &mut |_| {},
            )
            .unwrap();
            c.eq(n, "total", r.total_sources, case["total"].as_u64().unwrap() as usize);
            c.eq(n, "loaded", r.loaded_sources, case["loaded"].as_u64().unwrap() as usize);
            c.close(n, "coverage", &[r.coverage_seconds], &[case["coverage"].as_f64().unwrap()], 1e-9);
            c.eq(n, "warnings", r.warnings.clone(), strings(&case["warnings"]));
            let segs = case["segments"].as_array().unwrap();
            c.eq(n, "segments", r.segments.len(), segs.len());
            for (s, w) in r.segments.iter().zip(segs) {
                let what = s.source.filename.clone();
                c.eq(n, &format!("{what} file"), what.as_str(), w["filename"].as_str().unwrap());
                let (shape, want) = read_npy(&out.join(w["data"].as_str().unwrap()));
                c.eq(n, &format!("{what} shape"), vec![s.data_db.rows, s.data_db.cols], shape);
                let got: Vec<f64> = s.data_db.data.iter().map(|&v| v as f64).collect();
                c.close(n, &format!("{what} data"), &got, &want, 1e-6);
                let (_, utc) = read_npy(&out.join(w["utc"].as_str().unwrap()));
                c.close(n, &format!("{what} utc"), &s.utc_seconds, &utc, 1e-12);
            }
        }
        "preview" => {
            let files: Vec<(String, String)> = strings(&case["files"]).iter().map(|f| (fixture(f), f.clone())).collect();
            let (panels, errors) = downloader::build_preview_panels(&files);
            c.eq(n, "errors", errors, strings(&case["errors"]));
            let want = case["panels"].as_array().unwrap();
            c.eq(n, "panels", panels.len(), want.len());
            for (i, (p, w)) in panels.iter().zip(want).enumerate() {
                c.eq(n, &format!("{i} title"), p.title.as_str(), w["title"].as_str().unwrap());
                c.eq(n, &format!("{i} subtitle"), p.subtitle.as_str(), w["subtitle"].as_str().unwrap());
                c.eq(n, &format!("{i} type"), p.combine_type.map(|t| t.key()), w["combine_type"].as_str());
                let (shape, data) = read_npy(&out.join(w["data"].as_str().unwrap()));
                c.eq(n, &format!("{i} shape"), vec![p.data.rows, p.data.cols], shape);
                let got: Vec<f64> = p.data.data.iter().map(|&v| v as f64).collect();
                c.close(n, &format!("{i} data"), &got, &data, 1e-6);
            }
        }
        "import" => {
            let sources: Vec<String> =
                strings(&case["files"]).iter().map(|f| if f.contains('/') { f.clone() } else { fixture(f) }).collect();
            let mut events = Vec::new();
            let root = std::env::temp_dir().join("lite_import_parity_cache");
            let result = downloader::prepare_import(&root, &sources, |p| events.push(p));
            let got: Vec<Value> = events
                .iter()
                .map(|e| match e {
                    ImportProgress::Text(t) => serde_json::json!(["text", t]),
                    ImportProgress::Range(a, b) => serde_json::json!(["range", a, b]),
                    ImportProgress::Value(v) => serde_json::json!(["value", v]),
                })
                .collect();
            c.eq(n, "progress", Value::Array(got), case["progress"].clone());
            match result {
                Err(e) => c.eq(n, "error", Some(e), case["error"].as_str().map(String::from)),
                Ok(ImportPayload::Single { filename, result, .. }) => {
                    c.eq(n, "kind", "single", case["payload_kind"].as_str().unwrap());
                    c.eq(n, "filename", filename.as_str(), case["filename"].as_str().unwrap());
                    let shape = vec![result.data.rows as u64, result.data.cols as u64];
                    c.eq(n, "shape", Value::from(shape), case["shape"].clone());
                }
                Ok(ImportPayload::Combined(comb)) => {
                    c.eq(n, "kind", "combined", case["payload_kind"].as_str().unwrap());
                    let shape = vec![comb.data.rows as u64, comb.data.cols as u64];
                    c.eq(n, "shape", Value::from(shape), case["shape"].clone());
                    c.eq(n, "type", comb.combine_type.key(), case["combine_type"].as_str().unwrap());
                }
                Ok(ImportPayload::FrequencyOptionsRequired { relation, combine_type, .. }) => {
                    c.eq(n, "kind", "frequency_options_required", case["payload_kind"].as_str().unwrap());
                    c.eq(n, "type", combine_type.key(), case["combine_type"].as_str().unwrap());
                    c.eq(n, "gap", relation.has_gap(), case["has_gap"].as_bool().unwrap());
                    c.eq(n, "overlap", relation.has_overlap(), case["has_overlap"].as_bool().unwrap());
                }
                Ok(ImportPayload::Invalid(e)) => {
                    c.eq(n, "kind", "invalid", case["payload_kind"].as_str().unwrap());
                    c.eq(n, "invalid error", e.as_str(), case["invalid_error"].as_str().unwrap());
                }
            }
        }
        other => c.failures.push(format!("{n}: unknown case kind {other}")),
    }
}

#[test]
fn downloads_match_full_app() {
    let out = parity_dir().join("out");
    let Ok(text) = std::fs::read_to_string(out.join("downloads.json")) else {
        assert!(
            std::env::var_os("CALLISTO_PARITY_REQUIRED").is_none(),
            "downloads.json missing; run lite/parity/make_download_reference.py"
        );
        eprintln!("note: no downloader references; run lite/parity/make_download_reference.py");
        return;
    };
    let cases: Vec<Value> = serde_json::from_str(&text).unwrap();
    let mut c = Checker::default();
    for case in &cases {
        run_case(&mut c, case, &cases, &out);
    }
    eprintln!("checked: {:?}", c.checked);
    assert!(c.failures.is_empty(), "{} mismatches:\n{}", c.failures.len(), c.failures.join("\n"));
}
