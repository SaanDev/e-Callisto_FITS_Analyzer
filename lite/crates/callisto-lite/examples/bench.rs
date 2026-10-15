//! Times the plan's speed targets (`lite/PLAN.md`, section 4) on a folder of
//! e-CALLISTO files: `cargo run --release -p callisto-lite --example bench -- <folder>`.
//!
//! Uses INDIA-GAURI focus code 02 (96 files for 2026-10-07 in `New Data`).

use std::time::Instant;

use callisto_lite::session::{NOISE_SLIDER_MAX, Session};

/// The plot area of the 1600×1000 window at 1.5×, in device pixels.
const VIEW: (usize, usize) = (1693, 1154);

fn ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1000.0
}

fn main() {
    let dir = std::env::args().nth(1).unwrap_or_else(|| "C:/Users/CALLISTO/Desktop/New Data".into());
    let mut day: Vec<String> = std::fs::read_dir(&dir)
        .expect("data folder")
        .filter_map(|e| e.ok())
        .map(|e| e.path().to_string_lossy().into_owned())
        .filter(|p| p.contains("INDIA-GAURI_") && p.ends_with("_02.fit.gz"))
        .collect();
    day.sort();
    println!("{} files", day.len());

    let mut s = Session::new();
    s.set_archive(None, false);
    let t = Instant::now();
    assert!(s.open_fits_paths(&day));
    let open = ms(t);
    let t = Instant::now();
    let img = s.canvas.render_view(VIEW.0, VIEW.1).expect("image");
    let first_draw = ms(t);
    let d = s.data.as_ref().unwrap();
    println!("open a full day ({}x{}): {open:.0} ms, first image {first_draw:.1} ms", d.raw.rows, d.raw.cols);
    assert_eq!(img.size, [VIEW.0, VIEW.1]);

    // A threshold change: the slider moves, the plot is rebuilt and drawn.
    let mut redraws = Vec::new();
    for k in 0..10 {
        let t = Instant::now();
        s.on_noise_slider_value_changed(false, NOISE_SLIDER_MAX * 3 / 4 + k * 10);
        s.on_noise_slider_released();
        s.canvas.render_view(VIEW.0, VIEW.1).expect("image");
        redraws.push(ms(t));
    }
    redraws.sort_by(f64::total_cmp);
    println!("redraw after a threshold change: median {:.1} ms (min {:.1})", redraws[5], redraws[0]);

    let t = Instant::now();
    s.preview_rfi(Default::default());
    println!("RFI clean, full day: {:.0} ms", ms(t));

    let event = &day[16..20];
    let mut s = Session::new();
    s.set_archive(None, false);
    let mut times = Vec::new();
    for _ in 0..5 {
        let t = Instant::now();
        assert!(s.open_fits_paths(event));
        times.push(ms(t));
    }
    times.sort_by(f64::total_cmp);
    println!("open a 4-file event: median {:.1} ms (min {:.1})", times[2], times[0]);
}
