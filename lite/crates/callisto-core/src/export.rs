//! PNG and CSV export.

use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

use crate::image::RgbaImage;
use anyhow::{Context, Result, bail};

use crate::dataset::{Curve, Dataset};

const ISO: &str = "%Y-%m-%dT%H:%M:%S%.3f";

pub fn save_png(path: &Path, img: &RgbaImage) -> Result<()> {
    let [w, h] = img.size;
    image::save_buffer(path, img.as_bytes(), w as u32, h as u32, image::ExtendedColorType::Rgba8)
        .with_context(|| format!("writing {}", path.display()))
}

fn create(path: &Path) -> Result<BufWriter<File>> {
    Ok(BufWriter::new(File::create(path).with_context(|| format!("creating {}", path.display()))?))
}

fn fmt_value(v: f32) -> String {
    if v.is_finite() { v.to_string() } else { String::new() }
}

/// Writes the processed data matrix (rows = time samples, columns = channels) of
/// every visible segment within the given ranges. With several segments, one
/// file per segment is written, suffixed with the segment label.
pub fn write_matrix_csv(
    ds: &Dataset,
    path: &Path,
    t_range: Option<(f64, f64)>,
    f_range: Option<(f64, f64)>,
    description: &str,
) -> Result<Vec<PathBuf>> {
    let visible: Vec<_> = ds.visible().collect();
    if visible.is_empty() {
        bail!("no visible data");
    }
    let mut written = Vec::new();
    for &(_, s) in &visible {
        let out_path = if visible.len() == 1 {
            path.to_path_buf()
        } else {
            let label: String =
                s.label.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '_' }).collect();
            let stem = path.file_stem().unwrap_or_default().to_string_lossy();
            path.with_file_name(format!("{stem}_{label}.csv"))
        };
        let (a, b) = match t_range {
            Some((t0, t1)) => s.time_range_idx(t0, t1),
            None => (0, s.n_time()),
        };
        let rows: Vec<usize> =
            (0..s.n_freq()).filter(|&r| f_range.is_none_or(|(f0, f1)| s.freqs[r] >= f0 && s.freqs[r] <= f1)).collect();
        if b <= a || rows.is_empty() {
            continue;
        }

        let mut w = create(&out_path)?;
        writeln!(w, "# e-CALLISTO data export: {}", s.label)?;
        writeln!(w, "# {description}")?;
        writeln!(w, "# rows: time samples (UTC); columns: frequency channels in MHz")?;
        write!(w, "time_utc")?;
        for &r in &rows {
            write!(w, ",{:.3}", s.freqs[r])?;
        }
        writeln!(w)?;
        let nt = s.n_time();
        for c in a..b {
            write!(w, "{}", ds.datetime(s.times[c]).format(ISO))?;
            for &r in &rows {
                write!(w, ",{}", fmt_value(s.data[r * nt + c]))?;
            }
            writeln!(w)?;
        }
        w.flush()?;
        written.push(out_path);
    }
    if written.is_empty() {
        bail!("no samples in the selected range");
    }
    Ok(written)
}

pub fn write_light_curve_csv(ds: &Dataset, curves: &[Curve], path: &Path, description: &str) -> Result<()> {
    let mut w = create(path)?;
    writeln!(w, "# e-CALLISTO light curve: {description}")?;
    writeln!(w, "time_utc,seconds_after_midnight,value,series")?;
    for c in curves {
        for &[t, v] in c.pieces.iter().flatten() {
            writeln!(w, "{},{t:.3},{v},{}", ds.datetime(t).format(ISO), c.label)?;
        }
    }
    w.flush()?;
    Ok(())
}

pub fn write_spectrum_csv(curves: &[Curve], path: &Path, description: &str) -> Result<()> {
    let mut w = create(path)?;
    writeln!(w, "# e-CALLISTO spectrum: {description}")?;
    writeln!(w, "frequency_mhz,value,series")?;
    for c in curves {
        for &[f, v] in c.pieces.iter().flatten() {
            writeln!(w, "{f:.3},{v},{}", c.label)?;
        }
    }
    w.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::callisto::CallistoFile;
    use crate::callisto::tests::synthetic_file;
    use crate::fits::FitsFile;
    use crate::image::Rgba;

    fn dataset() -> Dataset {
        let fits = FitsFile::from_bytes(synthetic_file("2020/01/01 10:00:00", 20, &[60.0, 40.0])).unwrap();
        Dataset::build(vec![CallistoFile::from_fits(&fits, "x".into(), "x.fit".into()).unwrap()]).unwrap()
    }

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("callisto_viewer_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir.join(name)
    }

    #[test]
    fn writes_matrix_csv() {
        let ds = dataset();
        let path = temp("m.csv");
        // 10:00:01 to 10:00:02 → samples 4..8, only the 40 MHz channel.
        let written = write_matrix_csv(&ds, &path, Some((36001.0, 36002.0)), Some((30.0, 50.0)), "test").unwrap();
        assert_eq!(written, vec![path.clone()]);
        let text = std::fs::read_to_string(&path).unwrap();
        let lines: Vec<&str> = text.lines().filter(|l| !l.starts_with('#')).collect();
        assert_eq!(lines[0], "time_utc,40.000");
        assert_eq!(lines.len(), 1 + 4);
        assert_eq!(lines[1], "2020-01-01T10:00:01.000,14");
    }

    #[test]
    fn writes_cross_sections_and_png() {
        let ds = dataset();
        let lc = ds.light_curves(40.0, 0.0, 1e9, usize::MAX);
        let p = temp("lc.csv");
        write_light_curve_csv(&ds, &lc, &p, "test").unwrap();
        let text = std::fs::read_to_string(&p).unwrap();
        assert_eq!(text.lines().count(), 2 + 20);
        assert!(text.contains("2020-01-01T10:00:00.250,36000.250,11,"));

        let p = temp("sp.csv");
        write_spectrum_csv(&ds.spectra(36000.0), &p, "test").unwrap();
        let text = std::fs::read_to_string(&p).unwrap();
        assert!(text.contains("40.000,10,") && text.contains("60.000,0,"));

        let img = RgbaImage::new([3, 2], vec![Rgba::rgb(255, 0, 0); 6]);
        let p = temp("x.png");
        save_png(&p, &img).unwrap();
        let back = image::open(&p).unwrap().to_rgba8();
        assert_eq!((back.width(), back.height()), (3, 2));
        assert_eq!(back.get_pixel(0, 0).0, [255, 0, 0, 255]);
    }
}
