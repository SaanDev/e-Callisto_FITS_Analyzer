//! Lite's aliased overlay lines against Qt's (`parity/make_raster_reference.py`).
//!
//! Each case is a polyline drawn with a cosmetic pen into a clipped image; the
//! port must cover the same pixels with the same alpha. Skips when the
//! reference has not been generated.

use std::path::{Path, PathBuf};

use callisto_core::plot::qt_raster::{Pen, PenStyle, Raster};
use serde_json::Value;

fn out_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../parity/out/raster")
}

#[test]
fn overlay_lines_match_qt() {
    let dir = out_dir();
    let Ok(text) = std::fs::read_to_string(dir.join("cases.json")) else {
        eprintln!("no raster reference in {}; run parity/make_raster_reference.py", dir.display());
        return;
    };
    let spec: Value = serde_json::from_str(&text).unwrap();
    let (w, h) = (spec["size"][0].as_u64().unwrap() as usize, spec["size"][1].as_u64().unwrap() as usize);
    let mut failures = Vec::new();
    let mut by_kind: std::collections::BTreeMap<String, (usize, usize)> = Default::default();
    for case in spec["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let points: Vec<(f64, f64)> = case["points"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| (p[0].as_f64().unwrap(), p[1].as_f64().unwrap()))
            .collect();
        let width = case["width"].as_f64().unwrap();
        let style = match case["style"].as_str().unwrap() {
            "dash" => PenStyle::Dash,
            "dot" => PenStyle::Dot,
            _ => PenStyle::Solid,
        };
        let c: Vec<u8> = case["rgba"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap() as u8).collect();
        let clip: Vec<i32> = case["clip"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i32).collect();
        let pen = Pen::new([c[0], c[1], c[2], c[3]], width).styled(style);

        let mut r = Raster::new(clip[2] as usize, clip[3] as usize, clip[0], clip[1], (w, h)).unwrap();
        r.curve(&points, &pen);
        let ours = r.into_image();

        let reference = image::open(dir.join(format!("{name}.png"))).unwrap().to_rgba8();
        let mut bad = 0usize;
        let mut covered = 0usize;
        for y in 0..h {
            for x in 0..w {
                let inside = x as i32 >= clip[0]
                    && (x as i32) < clip[0] + clip[2]
                    && y as i32 >= clip[1]
                    && (y as i32) < clip[1] + clip[3];
                // The PNG is straight alpha, ours premultiplied: compare premultiplied.
                let t = reference.get_pixel(x as u32, y as u32).0;
                let theirs = [0, 1, 2].map(|i| (t[i] as f64 * t[3] as f64 / 255.0).round() as i32);
                let mine = if inside {
                    ours.pixels[(y - clip[1] as usize) * ours.width() + (x - clip[0] as usize)].0
                } else {
                    [0, 0, 0, 0]
                };
                if t[3] > 0 {
                    covered += 1;
                }
                let alpha_off = (t[3] as i32 - mine[3] as i32).abs();
                let color_off = (0..3).map(|i| (theirs[i] - mine[i] as i32).abs()).max().unwrap();
                if alpha_off > 1 || color_off > 2 {
                    bad += 1;
                }
            }
        }
        // Thin lines and wide opaque segments are ports of Qt's own code and
        // must match exactly; other wide lines go through tiny-skia's stroker,
        // which differs at some joins and dash ends (up to 7 % of a thin
        // dashed line's pixels).
        let ported = width <= 1.0 || (style == PenStyle::Solid && c[3] == 255);
        let allowed = if ported { 0 } else { (covered / 10).max(3) };
        let kind = format!("w{width} {:?} a{}", style, c[3]);
        let e = by_kind.entry(kind).or_default();
        e.0 += 1;
        if bad > 0 {
            e.1 += 1;
        }
        if bad > allowed {
            failures.push(format!("{name} (width {width}, {style:?}, alpha {}): {bad} of {covered} pixels differ", c[3]));
        }
    }
    for (k, (n, f)) in &by_kind {
        println!("{k}: {f} of {n} cases not pixel-exact");
    }
    for f in &failures {
        println!("{f}");
    }
    assert!(failures.is_empty(), "{} of the cases differ from Qt", failures.len());
}
