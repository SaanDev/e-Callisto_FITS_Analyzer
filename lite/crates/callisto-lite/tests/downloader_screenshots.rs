//! Renders Lite's download windows offscreen in the states
//! `lite/tools/capture_downloader.py` captures from the full app, with the
//! same canned network (station lists, a day listing built from the parity
//! fixtures, the files themselves, a burst catalog, a Learmonth day), and
//! compares them with `lite/reference/windows/downloads`.
//!
//! Writes `side_by_side_<state>.png` and `diff_<state>.png` to
//! `lite/reference/compare/`. Needs a GPU or software wgpu adapter:
//! `cargo test -p callisto-lite --test downloader_screenshots -- --ignored --nocapture`

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use callisto_core::radio::archive::{BASE_URL, set_mock_http};
use callisto_core::radio::burst_list::BURST_LIST_BASE_URL;
use callisto_core::radio::learmonth::BASE_URL as LEARMONTH_URL;
use callisto_lite::downloads::learmonth::LearmonthDialog;
use callisto_lite::downloads::stations::StationService;
use callisto_lite::downloads::{DateTimeState, DownloaderDialog};
use callisto_lite::fonts::SystemFonts;
use callisto_lite::gpu::GpuPreference;
use callisto_lite::theme::Palette;
use chrono::{NaiveDate, NaiveTime};
use egui_kittest::Harness;
use image::{Rgba, RgbaImage};

fn lite_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn fixtures() -> PathBuf {
    lite_dir().join("parity/fixtures")
}

const EXTRA: [&str; 5] = [
    "ALASKA-ANCHORAGE_20261007_001500_01.fit.gz",
    "Malaysia_Banting_20261007_000000_59.fit.gz",
    "BIR_20261007_103000_01.fit.gz",
    "BIR_20261007_104500_01.fit.gz",
    "BIR_20261007_101500_02.fit.gz",
];

const CATALOG: &str = "#Date\tTime\tType\tStations\n\
    20261007\t04:20-04:22\tIII\tINDIA-GAURI, GERMANY-DLR\n\
    20261007\t05:05-05:09\tII\tGERMANY-DLR, AUSTRIA-OE3FLB, (TRIEST)\tslow drift\n\
    20261007\t10:16-10:16\tIII\tBIR\n\
    20261007\t00:05-00:20\tCTM\tMEXART, SWISS-IRSOL\n";

/// The capture tool's synthetic Learmonth day: 1200 scans from midnight.
fn learmonth_day(day: NaiveDate) -> Vec<u8> {
    let mut buf = Vec::new();
    let start = day.and_hms_opt(0, 0, 0).unwrap();
    for i in 0..1200i64 {
        let t = start + chrono::Duration::seconds(3 * i);
        use chrono::{Datelike, Timelike};
        buf.extend([
            (t.year() - 2000) as u8,
            t.month() as u8,
            t.day() as u8,
            t.hour() as u8,
            t.minute() as u8,
            t.second() as u8,
            3,
            0,
        ]);
        buf.extend([0, 25, 0, 75, 0, 0, 0, 0, 0, 75, 0, 180, 0, 0, 0, 0]);
        buf.extend((0..802).map(|k| ((i * 7 + k) % 256) as u8));
    }
    buf
}

/// The canned network of `capture_downloader.py`.
fn install_mock() {
    let mut names: Vec<String> = std::fs::read_dir(fixtures())
        .unwrap()
        .filter_map(|e| e.ok()?.file_name().into_string().ok())
        .filter(|n| n.ends_with(".fit.gz"))
        .collect();
    names.extend(EXTRA.iter().map(|s| s.to_string()));
    names.sort();
    let listing = format!(
        "<html><body><table>{}</table></body></html>",
        names.iter().map(|n| format!("<tr><td><a href=\"{n}\">{n}</a></td></tr>")).collect::<String>()
    );
    set_mock_http(Some(Arc::new(move |url: &str| {
        let name = url.trim_end_matches('/').rsplit('/').next().unwrap_or("");
        if url == BASE_URL {
            return Some((200, b"<html></html>".to_vec()));
        }
        if url.starts_with(BASE_URL) {
            if url.ends_with("/2026/10/07/") {
                return Some((200, listing.clone().into_bytes()));
            }
            if let Ok(bytes) = std::fs::read(fixtures().join(name)) {
                return Some((200, bytes));
            }
            // The capture's session answers every other directory with an empty page.
            return Some(if url.ends_with('/') { (200, b"<html></html>".to_vec()) } else { (404, Vec::new()) });
        }
        if url == BURST_LIST_BASE_URL {
            return Some((200, b"<a href=\"2026/\">2026/</a>".to_vec()));
        }
        if url == format!("{BURST_LIST_BASE_URL}2026/") {
            return Some((200, b"<a href=\"e-CALLISTO_2026_10.txt\">x</a>".to_vec()));
        }
        if url.ends_with("e-CALLISTO_2026_10.txt") {
            return Some((200, CATALOG.as_bytes().to_vec()));
        }
        if url.starts_with(LEARMONTH_URL) && name == "LM261007.srs" {
            return Some((200, learmonth_day(NaiveDate::from_ymd_opt(2026, 10, 7).unwrap())));
        }
        Some((404, Vec::new()))
    })));
}

/// `compare` for a popup window: the part of `lite` at device pixel (x, y)
/// the size of the reference.
fn compare_at(name: &str, lite: &RgbaImage, x: u32, y: u32) -> Option<f64> {
    let reference = image::open(lite_dir().join(format!("reference/windows/downloads/{name}.png"))).ok()?;
    let part = image::imageops::crop_imm(lite, x, y, reference.width(), reference.height()).to_image();
    compare(name, &part)
}

fn compare(name: &str, lite: &RgbaImage) -> Option<f64> {
    let out = lite_dir().join("reference/compare");
    std::fs::create_dir_all(&out).unwrap();
    let reference = image::open(lite_dir().join(format!("reference/windows/downloads/{name}.png"))).ok()?.to_rgba8();
    let (w, h) = (reference.width().min(lite.width()), reference.height().min(lite.height()));
    let mut side = RgbaImage::from_pixel(w * 2 + 8, h, Rgba([255, 0, 255, 255]));
    image::imageops::overlay(&mut side, &reference, 0, 0);
    image::imageops::overlay(&mut side, lite, (w + 8) as i64, 0);
    side.save(out.join(format!("side_by_side_{name}.png"))).unwrap();
    let mut diff = RgbaImage::new(w, h);
    let mut differing = 0u64;
    for y in 0..h {
        for x in 0..w {
            let (a, b) = (reference.get_pixel(x, y), lite.get_pixel(x, y));
            let d = (0..3).map(|c| (a[c] as i32 - b[c] as i32).unsigned_abs()).max().unwrap();
            if d > 32 {
                differing += 1;
            }
            let v = (d * 4).min(255) as u8;
            diff.put_pixel(x, y, Rgba([v, v, v, 255]));
        }
    }
    diff.save(out.join(format!("diff_{name}.png"))).unwrap();
    Some(differing as f64 / (w * h) as f64 * 100.0)
}

struct Downloader {
    dialog: DownloaderDialog,
    svc: StationService,
    p: Palette,
    ready: bool,
}

fn setup(ctx: &egui::Context, p: &Palette) {
    egui_extras::install_image_loaders(ctx);
    SystemFonts::load().install(ctx);
    ctx.set_theme(if p.dark { egui::Theme::Dark } else { egui::Theme::Light });
    p.apply(ctx);
}

type Draw<S> = Box<dyn FnMut(&mut egui::Ui, &mut S)>;

/// A test app: `draw` gets the window's whole area, as Lite's windows do.
struct TestApp<S> {
    state: S,
    draw: Draw<S>,
}

impl<S: 'static> eframe::App for TestApp<S> {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        (self.draw)(ui, &mut self.state);
    }
}

fn harness<S: 'static>(
    size: [f32; 2],
    state: S,
    ui_fn: impl FnMut(&mut egui::Ui, &mut S) + 'static,
) -> Harness<'static, TestApp<S>> {
    let app = TestApp { state, draw: Box::new(ui_fn) };
    Harness::builder()
        .with_size(size)
        .with_pixels_per_point(1.5)
        .wgpu_setup(callisto_lite::gpu::wgpu_options(GpuPreference::from_env()).wgpu_setup)
        .with_max_steps(100_000)
        .build_eframe(move |_| app)
}

/// Steps until `done`, letting background threads finish.
fn step_until<S: 'static>(h: &mut Harness<'_, TestApp<S>>, timeout: Duration, mut done: impl FnMut(&S) -> bool) {
    let start = Instant::now();
    while start.elapsed() < timeout {
        h.step();
        if done(&h.state().state) {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    for _ in 0..4 {
        h.step();
    }
}

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn downloader_matches_full_app() {
    install_mock();
    let cache = std::env::temp_dir().join(format!("lite_dl_test_{}", std::process::id()));
    let day = NaiveDate::from_ymd_opt(2026, 10, 7).unwrap();
    for (theme, dark) in [("light", false), ("dark", true)] {
        let _ = std::fs::remove_dir_all(&cache);
        let p = Palette::new(dark, true);
        let mut svc = StationService::online();
        // The capture's local UTC+5:30 edits: 04:00 UTC shows as 09:30.
        let state = DateTimeState {
            tab: 0,
            single_date: day,
            event_start: day.and_hms_opt(9, 30, 0).unwrap(),
            event_stop: day.and_hms_opt(11, 30, 0).unwrap(),
            overview_date: day,
            burst_start: NaiveDate::from_ymd_opt(2026, 9, 7).unwrap(),
            burst_end: day,
        };
        let dialog = DownloaderDialog::new(cache.clone(), &mut svc, Some(&state));
        let st = Downloader { dialog, svc, p, ready: false };
        let mut h = harness([1540.0, 980.0], st, |ui, s| {
            if !s.ready {
                // Fonts set now apply from the next frame.
                setup(ui.ctx(), &s.p);
                s.ready = true;
                return;
            }
            ui.painter().rect_filled(ui.max_rect(), 0.0, s.p.page_bg);
            let out = s.dialog.frame(ui, &mut s.svc, &s.p);
            s.dialog.finish(ui.ctx(), out, &s.p, false);
        });
        let shot = |h: &mut Harness<'_, TestApp<Downloader>>, name: &str| {
            let img = h.render().expect("render");
            let name = format!("{name}_{theme}");
            match compare(&name, &img) {
                Some(d) => println!("{name}: {d:.2}% of pixels differ by more than 32 levels"),
                None => println!("{name}: no reference"),
            }
        };
        let script = |h: &mut Harness<'_, TestApp<Downloader>>, cmds: &[&str]| {
            for c in cmds {
                h.state_mut().state.dialog.script.push_back(c.to_string());
                h.step();
            }
        };
        step_until(&mut h, Duration::from_secs(10), |s| !s.dialog.busy());
        std::thread::sleep(Duration::from_millis(500));
        step_until(&mut h, Duration::from_secs(5), |_| false);
        shot(&mut h, "dl_single");
        // The date edit's calendar and the station list, open; each popup
        // where the capture found it (the fields at 331, 106 and 922, 108).
        for (popup, key, at) in [
            ("popup_calendar", egui::Id::new("single_date").with("calendar"), (331.0, 106.0 + 44.0)),
            ("popup_combo", egui::Id::new("single_station").with("popup"), (922.0, 108.0)),
        ] {
            h.ctx.data_mut(|d| d.insert_temp(key, true));
            h.step();
            h.step();
            let img = h.render().expect("render");
            let name = format!("{popup}_{theme}");
            let (x, y) = ((at.0 * 1.5f32).round() as u32, (at.1 * 1.5f32).round() as u32);
            match compare_at(&name, &img, x, y) {
                Some(d) => println!("{name}: {d:.2}% of pixels differ by more than 32 levels"),
                None => println!("{name}: no reference"),
            }
            h.ctx.data_mut(|d| d.insert_temp(key, false));
            h.step();
        }
        script(&mut h, &["single|station|INDIA-GAURI", "single|show"]);
        step_until(&mut h, Duration::from_secs(10), |s| !s.dialog.busy());
        script(&mut h, &["single|check|0|1"]);
        shot(&mut h, "dl_single_files");
        script(&mut h, &["tab|1"]);
        step_until(&mut h, Duration::from_secs(2), |_| false);
        shot(&mut h, "dl_event");
        script(&mut h, &["event|check|INDIA-GAURI", "event|check|GERMANY-DLR", "event|search"]);
        step_until(&mut h, Duration::from_secs(10), |s| !s.dialog.busy());
        shot(&mut h, "dl_event_results");
        script(&mut h, &["tab|2"]);
        shot(&mut h, "dl_overview");
        script(&mut h, &["overview|station|INDIA-GAURI", "overview|generate"]);
        step_until(&mut h, Duration::from_secs(60), |s| !s.dialog.busy());
        shot(&mut h, "dl_overview_result");
        script(&mut h, &["tab|3"]);
        shot(&mut h, "dl_burst");
        script(&mut h, &["burst|load"]);
        step_until(&mut h, Duration::from_secs(10), |s| !s.dialog.busy());
        script(&mut h, &["burst|select|1", "burst|find"]);
        step_until(&mut h, Duration::from_secs(10), |s| !s.dialog.busy());
        shot(&mut h, "dl_burst_loaded");
        let notices: Vec<String> = h.state().state.dialog.pending_notices().iter().map(|n| n.title.clone()).collect();
        println!("{theme}: message boxes raised: {notices:?}");
    }
    set_mock_http(None);
}

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn learmonth_matches_full_app() {
    install_mock();
    let cache = std::env::temp_dir().join(format!("lite_lm_test_{}", std::process::id()));
    for (theme, dark) in [("light", false), ("dark", true)] {
        let _ = std::fs::remove_dir_all(&cache);
        let p = Palette::new(dark, true);
        let mut dialog = LearmonthDialog::new(cache.clone());
        dialog.set_when(NaiveDate::from_ymd_opt(2026, 10, 7).unwrap(), NaiveTime::from_hms_opt(0, 20, 0).unwrap());
        let mut ready = false;
        let mut h = harness([960.0, 640.0], dialog, move |ui, d| {
            if !ready {
                setup(ui.ctx(), &p);
                ready = true;
                return;
            }
            ui.painter().rect_filled(ui.max_rect(), 0.0, p.page_bg);
            d.frame(ui, &p);
        });
        step_until(&mut h, Duration::from_secs(1), |_| false);
        let img = h.render().expect("render");
        if let Some(d) = compare(&format!("learmonth_{theme}"), &img) {
            println!("learmonth_{theme}: {d:.2}% differ");
        }
        let ctx = h.ctx.clone();
        h.state_mut().state.script_show(&ctx);
        step_until(&mut h, Duration::from_secs(10), |d| !d.is_busy() && d.chunk_count() > 0);
        h.state_mut().state.script_check(0);
        step_until(&mut h, Duration::from_millis(300), |_| false);
        let img = h.render().expect("render");
        if let Some(d) = compare(&format!("learmonth_loaded_{theme}"), &img) {
            println!("learmonth_loaded_{theme}: {d:.2}% differ");
        }
    }
    set_mock_http(None);
}

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn previews_match_full_app() {
    for (theme, dark) in [("light", false), ("dark", true)] {
        for (name, files) in [
            ("preview_single", vec!["BIR_20261007_101459_01.fit.gz"]),
            (
                "preview_multi",
                vec![
                    "INDIA-GAURI_20261007_041500_02.fit.gz",
                    "INDIA-GAURI_20261007_043000_02.fit.gz",
                    "BIR_20261007_101459_01.fit.gz",
                ],
            ),
        ] {
            let p = Palette::new(dark, true);
            let local: Vec<(String, String)> =
                files.iter().map(|f| (fixtures().join(f).to_string_lossy().into_owned(), f.to_string())).collect();
            let (panels, _) = callisto_core::radio::downloader::build_preview_panels(&local);
            let ctx = egui::Context::default();
            let window = callisto_lite::downloads::preview::PreviewWindow::new(&ctx, panels);
            let mut ready = false;
            let mut h = harness([900.0, 600.0], window, move |ui, w| {
                if !ready {
                    setup(ui.ctx(), &p);
                    ready = true;
                    return;
                }
                ui.painter().rect_filled(ui.max_rect(), 0.0, p.page_bg);
                w.frame(ui, &p);
            });
            step_until(&mut h, Duration::from_millis(500), |_| false);
            let img = h.render().expect("render");
            if let Some(d) = compare(&format!("{name}_{theme}"), &img) {
                println!("{name}_{theme} ({}): {d:.2}% differ", h.state().state.title());
            }
        }
    }
}

#[test]
#[ignore = "renders with wgpu; run explicitly"]
fn import_progress_matches_full_app() {
    for (theme, dark) in [("light", false), ("dark", true)] {
        let p = Palette::new(dark, true);
        // MainWindow._show_import_progress_dialog with three files, one done.
        let progress = callisto_lite::ui::qt::Progress { min: 0, max: 3, value: 1, format: "%p%".into() };
        let mut ready = false;
        let mut h = harness([200.0, 70.0], (), move |ui, _| {
            if !ready {
                setup(ui.ctx(), &p);
                ready = true;
                return;
            }
            ui.painter().rect_filled(ui.max_rect(), 0.0, p.page_bg);
            callisto_lite::downloads::import::body(ui, "Downloading selected FITS files...", &progress, &p);
        });
        step_until(&mut h, Duration::from_millis(200), |_| false);
        let img = h.render().expect("render");
        if let Some(d) = compare(&format!("import_progress_{theme}"), &img) {
            println!("import_progress_{theme}: {d:.2}% differ");
        }
    }
}
