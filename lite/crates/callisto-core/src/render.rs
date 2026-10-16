//! Rasterises the visible part of a dataset into an image.
//!
//! Instead of uploading the whole spectrogram as one texture, the view is
//! re-rendered at screen resolution whenever it changes. That keeps zoomed-in
//! views sharp, handles gaps and multiple segments, and never runs into GPU
//! texture size limits for long time series.

use crate::image::{Rgba, RgbaImage};

use crate::dataset::{Dataset, Segment};
use crate::util::par_rows;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Pool {
    Mean,
    Max,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct View {
    /// Time range (seconds after reference midnight).
    pub x: (f64, f64),
    /// Frequency range in MHz, min < max.
    pub y: (f64, f64),
    /// Image size in pixels.
    pub width: usize,
    pub height: usize,
}

pub struct Style<'a> {
    pub vmin: f32,
    pub vmax: f32,
    pub lut: &'a [Rgba; 256],
    pub pool: Pool,
    pub no_data: Rgba,
    /// When true, image row 0 is the lowest frequency (low frequencies on top).
    pub low_freq_on_top: bool,
}

#[derive(Clone, Copy)]
enum Col {
    None,
    One(usize),
    Span(usize, usize),
}

/// At most this many samples are read per pixel when zoomed far out.
const MAX_POOL: usize = 32;

pub fn render(ds: &Dataset, view: &View, style: &Style) -> RgbaImage {
    let (w, h) = (view.width.max(1), view.height.max(1));
    let segs: Vec<&Segment> = ds.visible().map(|(_, s)| s).collect();
    let dx = (view.x.1 - view.x.0) / w as f64;
    let dy = (view.y.1 - view.y.0) / h as f64;

    let cols: Vec<Vec<Col>> = segs
        .iter()
        .map(|s| {
            (0..w)
                .map(|px| {
                    let ta = view.x.0 + px as f64 * dx;
                    let (a, b) = s.time_range_idx(ta, ta + dx);
                    match b - a {
                        0 => s.time_index(ta + 0.5 * dx).map_or(Col::None, Col::One),
                        1 => Col::One(a),
                        _ => Col::Span(a, b),
                    }
                })
                .collect()
        })
        .collect();

    let rows: Vec<Vec<Option<usize>>> = segs
        .iter()
        .map(|s| {
            (0..h)
                .map(|py| {
                    let k = if style.low_freq_on_top { py } else { h - 1 - py };
                    s.chan_at(view.y.0 + (k as f64 + 0.5) * dy)
                })
                .collect()
        })
        .collect();

    let scale = 256.0 / (style.vmax - style.vmin).max(f32::EPSILON);
    let color = |v: f32| {
        if v.is_finite() { style.lut[((v - style.vmin) * scale).clamp(0.0, 255.0) as usize] } else { style.no_data }
    };

    let mut pixels = vec![style.no_data; w * h];
    par_rows(&mut pixels, w, |py, out| {
        for (k, s) in segs.iter().enumerate() {
            let Some(r) = rows[k][py] else { continue };
            let nt = s.n_time();
            let row = &s.data[r * nt..(r + 1) * nt];
            for (px, pixel) in out.iter_mut().enumerate() {
                // Earlier segments take precedence where segments overlap.
                if *pixel != style.no_data {
                    continue;
                }
                let v = match cols[k][px] {
                    Col::None => continue,
                    Col::One(c) => row[c],
                    Col::Span(a, b) => pool(&row[a..b], style.pool),
                };
                *pixel = color(v);
            }
        }
    });

    RgbaImage::new([w, h], pixels)
}

fn pool(values: &[f32], mode: Pool) -> f32 {
    let stride = values.len().div_ceil(MAX_POOL).max(1);
    let it = values.iter().step_by(stride).copied().filter(|v| v.is_finite());
    match mode {
        Pool::Max => it.fold(f32::NAN, f32::max),
        Pool::Mean => {
            let (sum, n) = it.fold((0.0f32, 0u32), |(s, n), v| (s + v, n + 1));
            if n == 0 { f32::NAN } else { sum / n as f32 }
        }
    }
}

/// View covering the whole dataset at roughly one pixel per sample/channel.
pub fn full_resolution_view(ds: &Dataset) -> Option<View> {
    let (t0, t1) = ds.time_range()?;
    let (f0, f1) = ds.freq_range()?;
    let dt = ds.visible().map(|(_, s)| s.dt).fold(f64::INFINITY, f64::min);
    let n_freq = ds.visible().map(|(_, s)| s.n_freq()).max().unwrap_or(1);
    let spacing =
        ds.visible().map(|(_, s)| (s.f_range().1 - s.f_range().0) / s.n_freq() as f64).fold(f64::INFINITY, f64::min);
    let width = (((t1 - t0) / dt).round() as usize + 1).clamp(1, 60_000);
    let height = (((f1 - f0) / spacing).round() as usize).clamp(n_freq, 4096);
    Some(View { x: (t0 - dt / 2.0, t1 + dt / 2.0), y: (f0, f1), width, height })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::callisto::CallistoFile;
    use crate::callisto::tests::synthetic_file;
    use crate::fits::FitsFile;

    #[test]
    fn renders_expected_pixels() {
        let fits = FitsFile::from_bytes(synthetic_file("2020/01/01 00:00:00", 8, &[30.0, 20.0, 10.0])).unwrap();
        let f = CallistoFile::from_fits(&fits, "x".into(), "x.fit".into()).unwrap();
        let ds = Dataset::build(vec![f]).unwrap();
        let lut: [Rgba; 256] = std::array::from_fn(|i| Rgba::gray(i as u8));
        let style = Style {
            vmin: 0.0,
            vmax: 256.0,
            lut: &lut,
            pool: Pool::Mean,
            no_data: Rgba::TRANSPARENT,
            low_freq_on_top: true,
        };
        let view = full_resolution_view(&ds).unwrap();
        assert_eq!((view.width, view.height), (8, 3));
        let img = render(&ds, &view, &style);
        // Row 0 = 10 MHz, which was stored last (file row 2): value 20 + col.
        assert_eq!(img.pixels[0], Rgba::gray(20));
        assert_eq!(img.pixels[3], Rgba::gray(23));
        assert_eq!(img.pixels[2 * 8], Rgba::gray(0));

        // Outside the data → no-data colour.
        let off = View { x: (1000.0, 1001.0), ..view };
        assert!(render(&ds, &off, &style).pixels.iter().all(|&p| p == Rgba::TRANSPARENT));
    }
}

#[cfg(test)]
mod bench {
    use super::*;
    use crate::callisto::{CallistoFile, secs};
    use crate::dataset::{Background, Processing};
    use std::time::Instant;

    /// `cargo test --release bench_full_day -- --ignored --nocapture`
    #[test]
    #[ignore = "benchmark"]
    fn bench_full_day() {
        let freqs: Vec<f64> = (0..200).map(|i| 45.0 + i as f64 * 4.0).collect();
        let day = chrono::NaiveDate::from_ymd_opt(2025, 5, 14).unwrap().and_hms_opt(0, 0, 0).unwrap();
        let files: Vec<CallistoFile> = (0..96)
            .map(|k| CallistoFile {
                path: format!("f{k}.fit").into(),
                name: format!("S_20250514_000000_{k:02}.fit"),
                instrument: "S".into(),
                focus_code: None,
                start: day + secs(k as f64 * 900.0),
                times: (0..3600).map(|i| i as f64 * 0.25).collect(),
                freqs: freqs.clone(),
                data: (0..3600 * 200).map(|i| ((i * 7 + k) % 251) as f32).collect(),
                unit: "digits".into(),
                headers: Vec::new(),
                warnings: Vec::new(),
            })
            .collect();
        let t = Instant::now();
        let mut ds = Dataset::build(files).unwrap();
        eprintln!("build: {:?} ({} samples)", t.elapsed(), ds.segments[0].raw.len());
        let t = Instant::now();
        ds.apply_processing(&Processing { background: Background::Median, ..Default::default() });
        eprintln!("median background: {:?}", t.elapsed());
        let t = Instant::now();
        let _ = ds.percentiles(0.01, 0.995);
        eprintln!("percentiles: {:?}", t.elapsed());

        let lut = crate::colormap::Colormap::Jet.lut(false);
        let style = Style {
            vmin: -50.0,
            vmax: 50.0,
            lut: &lut,
            pool: Pool::Max,
            no_data: Rgba::TRANSPARENT,
            low_freq_on_top: true,
        };
        for (label, x) in
            [("full day", (0.0, 86400.0)), ("1 hour", (36000.0, 39600.0)), ("1 minute", (36000.0, 36060.0))]
        {
            let view = View { x, y: (45.0, 845.0), width: 2000, height: 1250 };
            let t = Instant::now();
            let n = 5;
            for _ in 0..n {
                std::hint::black_box(render(&ds, &view, &style));
            }
            eprintln!("render {label} 2000x1250: {:?}/frame", t.elapsed() / n);
        }
    }
}
