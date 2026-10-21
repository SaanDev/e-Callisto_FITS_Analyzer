//! File → Export As → Export to FIT (`MainWindow.export_to_fits`): the data
//! on show cast to BITPIX 8, 16 or 32, under the source file's header (its
//! structural keywords dropped), with the source's extensions copied and
//! its axis table rewritten for the current time and frequency axes — as
//! astropy writes the file.

use std::path::Path;

use anyhow::{Context, Result, bail};

use crate::fits::{Card, FitsFile, Header, HeaderValue, hdu_bytes};
use crate::radio::array::Array2;

/// `_recommend_bitpix_for_export`: the smallest integer type the data fit.
pub fn recommend_bitpix(data: &[f32]) -> i32 {
    let finite: Vec<f32> = data.iter().copied().filter(|v| v.is_finite()).collect();
    if finite.is_empty() {
        return 16;
    }
    // `np.nanmin` / `np.nanmax`: infinities count.
    let lo = data.iter().copied().filter(|v| !v.is_nan()).fold(f32::INFINITY, f32::min) as f64;
    let hi = data.iter().copied().filter(|v| !v.is_nan()).fold(f32::NEG_INFINITY, f32::max) as f64;
    if lo >= 0.0 && hi <= 255.0 {
        8
    } else if lo >= -32768.0 && hi <= 32767.0 {
        16
    } else {
        32
    }
}

/// `_cast_data_for_bitpix`: non-finite values to 0, rounded half to even,
/// clipped to the type; big-endian bytes, and the data's min and max.
pub fn cast_for_bitpix(data: &[f32], bitpix: i32) -> Result<(Vec<u8>, f64, f64)> {
    let (lo, hi) = match bitpix {
        8 => (0.0, 255.0),
        16 => (i16::MIN as f64, i16::MAX as f64),
        32 => (i32::MIN as f64, i32::MAX as f64),
        _ => bail!("Unsupported BITPIX: {bitpix}"),
    };
    let values: Vec<f64> =
        data.iter().map(|&v| if v.is_finite() { (v as f64).round_ties_even().clamp(lo, hi) } else { 0.0 }).collect();
    let bytes = match bitpix {
        8 => values.iter().map(|&v| v as u8).collect(),
        16 => values.iter().flat_map(|&v| (v as i16).to_be_bytes()).collect(),
        _ => values.iter().flat_map(|&v| (v as i32).to_be_bytes()).collect(),
    };
    let min = values.iter().copied().fold(f64::INFINITY, f64::min);
    let max = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    Ok((bytes, min, max))
}

const COMMENTARY: [&str; 3] = ["HISTORY", "COMMENT", ""];

fn is_commentary(c: &Card) -> bool {
    c.value.is_none() && COMMENTARY.contains(&c.key.trim())
}

/// `header[key] = value` in astropy: an existing card takes the new value
/// and keeps its comment; a new one goes after the last non-commentary card.
pub fn header_update(h: &mut Header, key: &str, value: HeaderValue) {
    if let Some(existing) = h.cards.iter_mut().find(|c| c.key.eq_ignore_ascii_case(key) && c.value.is_some()) {
        let comment = existing.comment.clone();
        *existing = Card::new(key, &value, &comment);
        return;
    }
    let at = h.cards.iter().rposition(|c| !is_commentary(c)).map_or(0, |i| i + 1);
    h.cards.insert(at, Card::new(key, &value, ""));
}

/// `header["HISTORY"] = text`: appended after the last card.
pub fn header_history(h: &mut Header, text: &str) {
    h.add_history(text);
}

/// What the export needs from the window.
pub struct FitsExport<'a> {
    pub data: &'a Array2,
    pub freqs: &'a [f64],
    pub time: &'a [f64],
    pub header0: Option<&'a Header>,
    pub plot_type: &'a str,
    pub unit_label: &'a str,
    pub combined_mode: Option<&'a str>,
    pub combined_sources: &'a [String],
    pub source_path: Option<&'a str>,
    pub bitpix: i32,
}

/// `_axis_kind_from_name`.
fn axis_kind(name: &str) -> Option<&'static str> {
    let n = name.trim().to_lowercase();
    if ["freq", "frequency", "freqs", "frequency_mhz", "freq_mhz"].contains(&n.as_str()) || n.contains("freq") {
        return Some("freq");
    }
    if ["time", "times", "time_s", "time_sec", "seconds", "sec"].contains(&n.as_str()) || n.starts_with("time") {
        return Some("time");
    }
    None
}

/// A binary table column: name, repeat count, type code.
struct Column {
    name: String,
    repeat: usize,
    code: char,
}

fn columns(h: &Header) -> Vec<Column> {
    let n = h.get_i64("TFIELDS").unwrap_or(0).max(0) as usize;
    (1..=n)
        .filter_map(|i| {
            let name = h.get_str(&format!("TTYPE{i}"))?;
            let form = h.get_str(&format!("TFORM{i}"))?;
            let digits: String = form.chars().take_while(char::is_ascii_digit).collect();
            let code = form[digits.len()..].chars().next()?;
            Some(Column { name, repeat: digits.parse().unwrap_or(1), code })
        })
        .collect()
}

fn type_size(code: char) -> Option<usize> {
    Some(match code {
        'L' | 'B' | 'A' => 1,
        'I' => 2,
        'J' | 'E' => 4,
        'K' | 'D' => 8,
        _ => return None,
    })
}

/// `_update_axis_table_hdu` for the one-row vector tables e-CALLISTO
/// writes: each axis column takes the axis (as float32 values, stored in
/// the column's type), the header is rebuilt as astropy writes a table.
/// None when the table has no axis column or another layout.
fn updated_axis_table(h: &Header, data: &[u8], freqs: &[f32], times: &[f32]) -> Option<Vec<u8>> {
    let cols = columns(h);
    let rows = h.get_i64("NAXIS2").unwrap_or(0).max(0) as usize;
    if cols.is_empty() || rows != 1 || !cols.iter().all(|c| matches!(c.code, 'D' | 'E') || type_size(c.code).is_some())
    {
        return None;
    }
    let axes: Vec<Option<&[f32]>> = cols
        .iter()
        .map(|c| match axis_kind(&c.name) {
            Some("freq") => Some(freqs),
            Some("time") => Some(times),
            _ => None,
        })
        .collect();
    if axes.iter().all(Option::is_none) || cols.iter().zip(&axes).any(|(c, a)| a.is_some() && c.repeat == 1) {
        return None;
    }
    let mut out = Vec::new();
    let mut offset = 0;
    let mut new_cols = Vec::new();
    for (c, axis) in cols.iter().zip(&axes) {
        let size = type_size(c.code)?;
        let old = data.get(offset..offset + size * c.repeat)?;
        offset += size * c.repeat;
        match axis {
            Some(values) => {
                for &v in *values {
                    match c.code {
                        'D' => out.extend((v as f64).to_be_bytes()),
                        'E' => out.extend(v.to_be_bytes()),
                        _ => return None,
                    }
                }
                new_cols.push((c.name.clone(), values.len(), c.code));
            }
            None => {
                out.extend_from_slice(old);
                new_cols.push((c.name.clone(), c.repeat, c.code));
            }
        }
    }
    let mut t = Header::default();
    t.set("XTENSION", HeaderValue::Str("BINTABLE".into()), "binary table extension");
    t.set("BITPIX", HeaderValue::Int(8), "array data type");
    t.set("NAXIS", HeaderValue::Int(2), "number of array dimensions");
    t.set("NAXIS1", HeaderValue::Int(out.len() as i64), "length of dimension 1");
    t.set("NAXIS2", HeaderValue::Int(1), "length of dimension 2");
    t.set("PCOUNT", HeaderValue::Int(0), "number of group parameters");
    t.set("GCOUNT", HeaderValue::Int(1), "number of groups");
    t.set("TFIELDS", HeaderValue::Int(new_cols.len() as i64), "number of table fields");
    for (i, (name, n, code)) in new_cols.iter().enumerate() {
        t.set(&format!("TTYPE{}", i + 1), HeaderValue::Str(name.clone()), "");
        t.set(&format!("TFORM{}", i + 1), HeaderValue::Str(format!("{n}{code}")), "");
        if *n > 1 {
            t.set(&format!("TDIM{}", i + 1), HeaderValue::Str(format!("({n})")), "");
        }
    }
    Some(hdu_bytes(&t, &out))
}

/// `export_to_fits`: the file's bytes.
pub fn export_fits_bytes(e: &FitsExport) -> Result<Vec<u8>> {
    let (data, min, max) = cast_for_bitpix(&e.data.data, e.bitpix)?;
    let mut hdr = e.header0.cloned().unwrap_or_default();
    // `_sanitize_primary_header_for_export`.
    let structural = ["SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "EXTEND", "BSCALE", "BZERO", "BLANK"];
    hdr.cards.retain(|c| c.value.is_none() || !structural.iter().any(|k| c.key.eq_ignore_ascii_case(k)));
    header_history(&mut hdr, "Exported by e-CALLISTO FITS Analyzer");
    header_history(&mut hdr, &format!("Export plot type: {}", e.plot_type));
    header_history(&mut hdr, &format!("Units shown: {}", e.unit_label));
    if e.combined_mode.is_some() {
        header_update(&mut hdr, "COMBINED", HeaderValue::Bool(true));
        if let Some(m) = e.combined_mode.filter(|m| !m.is_empty()) {
            header_update(&mut hdr, "COMBMETH", HeaderValue::Str(m.to_owned()));
        }
        header_update(&mut hdr, "NFILES", HeaderValue::Int(e.combined_sources.len() as i64));
        if let (Some(first), Some(last)) = (e.combined_sources.first(), e.combined_sources.last()) {
            header_history(&mut hdr, &format!("First source: {}", crate::radio::naming::basename(first)));
            header_history(&mut hdr, &format!("Last source: {}", crate::radio::naming::basename(last)));
        }
    } else {
        header_update(&mut hdr, "COMBINED", HeaderValue::Bool(false));
        if let Some(p) = e.source_path.filter(|p| !p.is_empty()) {
            header_history(&mut hdr, &format!("Source: {}", crate::radio::naming::basename(p)));
        }
    }
    header_update(&mut hdr, "BUNIT", HeaderValue::Str(e.unit_label.to_owned()));

    // `PrimaryHDU(data, header)`: astropy's structural cards first.
    let (rows, cols) = e.data.shape();
    let mut primary = Header::default();
    primary.set("SIMPLE", HeaderValue::Bool(true), "conforms to FITS standard");
    primary.set("BITPIX", HeaderValue::Int(e.bitpix as i64), "array data type");
    primary.set("NAXIS", HeaderValue::Int(2), "number of array dimensions");
    primary.set("NAXIS1", HeaderValue::Int(cols as i64), "");
    primary.set("NAXIS2", HeaderValue::Int(rows as i64), "");
    primary.set("EXTEND", HeaderValue::Bool(true), "");
    primary.cards.extend(hdr.cards);
    header_update(&mut primary, "BSCALE", HeaderValue::Int(1));
    header_update(&mut primary, "BZERO", HeaderValue::Int(0));
    header_update(&mut primary, "DATAMIN", HeaderValue::Float(min));
    header_update(&mut primary, "DATAMAX", HeaderValue::Float(max));

    let freqs: Vec<f32> = e.freqs.iter().map(|&v| v as f32).collect();
    let times: Vec<f32> = e.time.iter().map(|&v| v as f32).collect();
    let template = e.source_path.filter(|p| !p.is_empty()).or_else(|| e.combined_sources.first().map(String::as_str));
    let mut extensions: Option<Vec<Vec<u8>>> = None;
    let mut updated_any = false;
    if let Some(path) = template.filter(|p| Path::new(p).exists())
        && let Ok(t) = FitsFile::open(Path::new(path))
    {
        let mut ext = Vec::new();
        for i in 1..t.hdus.len() {
            let h = &t.hdus[i].header;
            let raw = t.hdu_data(i);
            let table = (t.hdus[i].kind() == "BINTABLE").then(|| updated_axis_table(h, raw, &freqs, &times)).flatten();
            match table {
                Some(bytes) => {
                    updated_any = true;
                    ext.push(bytes);
                }
                None => ext.push(hdu_bytes(h, raw)),
            }
        }
        extensions = Some(ext);
    }
    if extensions.is_some() && e.combined_mode.is_some() && !updated_any {
        extensions = None;
    }
    let extensions = extensions.unwrap_or_else(|| {
        // `BinTableHDU.from_columns`: FREQUENCY and TIME as float32 vectors, EXTNAME AXIS.
        let mut t = Header::default();
        let width = 4 * (freqs.len() + times.len());
        t.set("XTENSION", HeaderValue::Str("BINTABLE".into()), "binary table extension");
        t.set("BITPIX", HeaderValue::Int(8), "array data type");
        t.set("NAXIS", HeaderValue::Int(2), "number of array dimensions");
        t.set("NAXIS1", HeaderValue::Int(width as i64), "length of dimension 1");
        t.set("NAXIS2", HeaderValue::Int(1), "length of dimension 2");
        t.set("PCOUNT", HeaderValue::Int(0), "number of group parameters");
        t.set("GCOUNT", HeaderValue::Int(1), "number of groups");
        t.set("TFIELDS", HeaderValue::Int(2), "number of table fields");
        t.set("TTYPE1", HeaderValue::Str("FREQUENCY".into()), "");
        t.set("TFORM1", HeaderValue::Str(format!("{}E", freqs.len())), "");
        t.set("TTYPE2", HeaderValue::Str("TIME".into()), "");
        t.set("TFORM2", HeaderValue::Str(format!("{}E", times.len())), "");
        t.set("EXTNAME", HeaderValue::Str("AXIS".into()), "extension name");
        let bytes: Vec<u8> = freqs.iter().chain(&times).flat_map(|v| v.to_be_bytes()).collect();
        vec![hdu_bytes(&t, &bytes)]
    });
    header_update(&mut primary, "EXTEND", HeaderValue::Bool(!extensions.is_empty()));
    let mut out = hdu_bytes(&primary, &data);
    for e in extensions {
        out.extend(e);
    }
    Ok(out)
}

/// Writes the export to `path`, gzipped for a `.gz` name.
pub fn export_fits(path: &Path, e: &FitsExport) -> Result<()> {
    let mut bytes = export_fits_bytes(e)?;
    // astropy compresses a `.gz` name.
    if path.to_string_lossy().to_lowercase().ends_with(".gz") {
        use std::io::Write;
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        gz.write_all(&bytes)?;
        bytes = gz.finish()?;
    }
    std::fs::write(path, bytes).with_context(|| format!("writing {}", path.display()))
}
