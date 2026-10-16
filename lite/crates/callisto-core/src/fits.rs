//! Minimal pure-Rust FITS reader.
//!
//! Supports what e-CALLISTO and ARTEMIS-IV files need: header parsing for every
//! HDU, primary (or IMAGE extension) arrays of any standard BITPIX, and numeric
//! columns of binary tables. Gzip-compressed input is detected from the magic
//! bytes. Where it matters for matching the full app's results, behaviour
//! follows astropy (see `read_primary_array` and `read_table_axis`).

use std::io::Read;
use std::path::Path;

use anyhow::{Context, Result, anyhow, bail};

const BLOCK: usize = 2880;
const CARD: usize = 80;

/// One 80-character header card.
#[derive(Clone, Debug)]
pub struct Card {
    pub key: String,
    /// Raw value text: strings are unquoted, everything else is trimmed.
    pub value: Option<String>,
    pub comment: String,
    /// Whether the value was a quoted string.
    pub quoted: bool,
    /// The card as it appears in the file (or as it would be written).
    pub raw: String,
}

/// A value to store in a header card.
#[derive(Clone, Debug, PartialEq)]
pub enum HeaderValue {
    Str(String),
    Bool(bool),
    Int(i64),
    Float(f64),
}

impl Card {
    /// Formats a card the way astropy writes one: the keyword padded to 8
    /// columns, `= `, the value (strings quoted and left-justified, other
    /// values right-justified in 20 columns), then ` / comment`.
    pub fn new(key: &str, value: &HeaderValue, comment: &str) -> Self {
        let key = key.to_ascii_uppercase();
        let (text, quoted) = match value {
            HeaderValue::Str(s) => (format!("'{:<8}'", s.replace('\'', "''")), true),
            HeaderValue::Bool(b) => (format!("{:>20}", if *b { "T" } else { "F" }), false),
            HeaderValue::Int(i) => (format!("{i:>20}"), false),
            HeaderValue::Float(f) => (format!("{:>20}", format_float(*f)), false),
        };
        let mut raw = format!("{key:<8}= {text:<20}");
        if !comment.is_empty() {
            raw.push_str(" / ");
            raw.push_str(comment);
        }
        raw.truncate(CARD);
        let value = match value {
            HeaderValue::Str(s) => s.clone(),
            HeaderValue::Bool(b) => if *b { "T" } else { "F" }.to_string(),
            HeaderValue::Int(i) => i.to_string(),
            HeaderValue::Float(f) => format_float(*f),
        };
        Card { key, value: Some(value), comment: comment.to_string(), quoted, raw }
    }

    /// A commentary card (HISTORY, COMMENT).
    pub fn commentary(key: &str, text: &str) -> Self {
        let mut raw = format!("{key:<8}{text}");
        raw.truncate(CARD);
        Card { key: key.to_string(), value: None, comment: text.to_string(), quoted: false, raw }
    }
}

/// astropy's float formatting for header values: always a decimal point or
/// exponent, upper-case `E`.
fn format_float(v: f64) -> String {
    if !v.is_finite() {
        return v.to_string().to_uppercase();
    }
    let mut s = format!("{v:?}");
    if s.contains('e') {
        s = s.replace('e', "E");
        if !s.contains('.') {
            let (m, e) = s.split_once('E').unwrap();
            s = format!("{m}.0E{e}");
        }
    } else if !s.contains('.') {
        s.push_str(".0");
    }
    s
}

#[derive(Clone, Debug, Default)]
pub struct Header {
    pub cards: Vec<Card>,
}

impl Header {
    /// astropy's `Header.tostring(sep="\n", endcard=True, padding=False)`:
    /// each card's 80-column image on its own line, then END.
    pub fn to_text(&self) -> String {
        let mut out = String::with_capacity((self.cards.len() + 1) * (CARD + 1));
        for card in &self.cards {
            out.push_str(&format!("{:<80}", card.raw));
            out.push('\n');
        }
        out.push_str(&format!("{:<80}", "END"));
        out
    }

    fn find(&self, key: &str) -> Option<&Card> {
        self.cards.iter().find(|c| c.key.eq_ignore_ascii_case(key))
    }

    pub fn contains(&self, key: &str) -> bool {
        self.find(key).is_some_and(|c| c.value.is_some())
    }

    pub fn get_str(&self, key: &str) -> Option<String> {
        self.find(key)?.value.as_ref().map(|v| v.trim().to_string())
    }

    pub fn get_f64(&self, key: &str) -> Option<f64> {
        let card = self.find(key)?;
        if card.quoted {
            // astropy returns a string; float() of it still works in Python.
            return card.value.as_ref()?.trim().parse::<f64>().ok();
        }
        let v = card.value.as_ref()?;
        match v.trim() {
            "T" => Some(1.0),
            "F" => Some(0.0),
            // FITS allows Fortran-style exponents like 1.0D+03.
            t => t.replace(['D', 'd'], "E").parse::<f64>().ok(),
        }
    }

    pub fn get_i64(&self, key: &str) -> Option<i64> {
        let v = self.get_f64(key)?;
        (v.fract() == 0.0).then_some(v as i64)
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        match self.find(key)?.value.as_deref()?.trim() {
            "T" => Some(true),
            "F" => Some(false),
            _ => None,
        }
    }

    /// The comment of a card, e.g. `SIMPLE`'s, which names the writer.
    pub fn comment(&self, key: &str) -> Option<&str> {
        self.find(key).map(|c| c.comment.as_str())
    }

    /// `hdr[key] = (value, comment)`: replaces the card in place, or appends it.
    pub fn set(&mut self, key: &str, value: HeaderValue, comment: &str) {
        let card = Card::new(key, &value, comment);
        match self.cards.iter_mut().find(|c| c.key.eq_ignore_ascii_case(key) && c.value.is_some()) {
            Some(existing) => *existing = card,
            None => self.cards.push(card),
        }
    }

    /// `hdr[key] = value`: like `set`, but an existing card keeps its comment.
    pub fn set_value(&mut self, key: &str, value: HeaderValue) {
        let comment = self.find(key).filter(|c| c.value.is_some()).map(|c| c.comment.clone()).unwrap_or_default();
        self.set(key, value, &comment);
    }

    pub fn add_history(&mut self, text: &str) {
        self.cards.push(Card::commentary("HISTORY", text));
    }
}

/// The bytes of one HDU: its header cards then END, padded with spaces to a
/// whole block, and its data padded with zeros.
pub fn hdu_bytes(header: &Header, data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity((header.cards.len() + 1) * CARD + data.len() + 2 * BLOCK);
    for card in header.cards.iter().map(|c| c.raw.as_str()).chain(std::iter::once("END")) {
        let mut line = card.as_bytes().to_vec();
        line.resize(CARD, b' ');
        out.extend(line);
    }
    out.resize(out.len().div_ceil(BLOCK) * BLOCK, b' ');
    out.extend_from_slice(data);
    out.resize(out.len().div_ceil(BLOCK) * BLOCK, 0);
    out
}

/// A binary table of one row whose columns are `f64` arrays (`nD`), as
/// e-CALLISTO stores its time and frequency axes. Returns its HDU bytes.
pub fn axis_table_bytes(columns: &[(&str, &[f64])]) -> Vec<u8> {
    let width: usize = columns.iter().map(|(_, v)| v.len() * 8).sum();
    let mut h = Header::default();
    h.set("XTENSION", HeaderValue::Str("BINTABLE".into()), "binary table extension");
    h.set("BITPIX", HeaderValue::Int(8), "array data type");
    h.set("NAXIS", HeaderValue::Int(2), "number of array dimensions");
    h.set("NAXIS1", HeaderValue::Int(width as i64), "length of dimension 1");
    h.set("NAXIS2", HeaderValue::Int(1), "length of dimension 2");
    h.set("PCOUNT", HeaderValue::Int(0), "number of group parameters");
    h.set("GCOUNT", HeaderValue::Int(1), "number of groups");
    h.set("TFIELDS", HeaderValue::Int(columns.len() as i64), "number of table fields");
    for (i, (name, values)) in columns.iter().enumerate() {
        h.set(&format!("TTYPE{}", i + 1), HeaderValue::Str((*name).into()), "");
        h.set(&format!("TFORM{}", i + 1), HeaderValue::Str(format!("{}D", values.len())), "");
    }
    let data: Vec<u8> = columns.iter().flat_map(|(_, v)| v.iter().flat_map(|x| x.to_be_bytes())).collect();
    hdu_bytes(&h, &data)
}

#[derive(Clone, Debug)]
pub struct Hdu {
    pub header: Header,
    data_start: usize,
    data_len: usize,
}

impl Hdu {
    /// "PRIMARY", "BINTABLE", "IMAGE", ...
    pub fn kind(&self) -> String {
        self.header.get_str("XTENSION").unwrap_or_else(|| "PRIMARY".to_string()).to_uppercase()
    }

    fn axes(&self) -> Vec<usize> {
        let naxis = self.header.get_i64("NAXIS").unwrap_or(0).max(0) as usize;
        (1..=naxis).map(|i| self.header.get_i64(&format!("NAXIS{i}")).unwrap_or(0).max(0) as usize).collect()
    }
}

pub struct FitsFile {
    bytes: Vec<u8>,
    pub hdus: Vec<Hdu>,
    /// Problems that did not prevent reading, e.g. a damaged extension.
    pub warnings: Vec<String>,
}

/// A 2-D image array, `data[row * width + col]`, FITS axis 1 = columns.
pub struct Image {
    pub width: usize,
    pub height: usize,
    pub data: Vec<f32>,
}

impl FitsFile {
    pub fn open(path: &Path) -> Result<Self> {
        let raw = std::fs::read(path).with_context(|| format!("cannot read {}", path.display()))?;
        Self::from_bytes(raw)
    }

    pub fn from_bytes(raw: Vec<u8>) -> Result<Self> {
        let bytes = if raw.starts_with(&[0x1f, 0x8b]) {
            let mut out = Vec::with_capacity(raw.len() * 4);
            flate2::read::MultiGzDecoder::new(&raw[..]).read_to_end(&mut out).context("gzip decompression failed")?;
            out
        } else {
            raw
        };

        if !bytes.starts_with(b"SIMPLE") {
            bail!("not a FITS file (missing SIMPLE keyword)");
        }

        let mut hdus = Vec::new();
        let mut warnings = Vec::new();
        let mut offset = 0;
        while offset + BLOCK <= bytes.len() {
            let (header, header_len) = match parse_header(&bytes[offset..]) {
                Ok(h) => h,
                // A damaged trailing extension should not prevent reading the primary HDU.
                Err(e) if !hdus.is_empty() => {
                    warnings.push(format!("ignored unreadable HDU at byte {offset}: {e}"));
                    break;
                }
                Err(e) => return Err(e),
            };
            let data_start = offset + header_len;
            let mut hdu = Hdu { header, data_start, data_len: 0 };
            let data_len = data_size(&hdu);
            let available = bytes.len().saturating_sub(data_start);
            if data_len > available {
                if hdus.is_empty() {
                    bail!("file truncated: primary data needs {data_len} bytes, only {available} present");
                }
                warnings.push(format!("truncated extension {} ignored", hdus.len()));
                break;
            }
            hdu.data_len = data_len;
            hdus.push(hdu);
            offset = data_start + data_len.div_ceil(BLOCK) * BLOCK;
        }

        if hdus.is_empty() {
            bail!("no HDUs found");
        }
        if offset > bytes.len() || (offset < bytes.len() && bytes.len() - offset < BLOCK) {
            warnings.push("file appears truncated".into());
        }
        Ok(Self { bytes, hdus, warnings })
    }

    fn data(&self, hdu: &Hdu) -> &[u8] {
        &self.bytes[hdu.data_start..hdu.data_start + hdu.data_len]
    }

    /// Decodes `n` values of an image HDU, applying BSCALE/BZERO. `blank_to_nan`
    /// maps the BLANK value to NaN.
    fn decode_image(&self, hdu: &Hdu, n: usize, blank_to_nan: bool) -> Result<Vec<f32>> {
        let h = &hdu.header;
        let bitpix = h.get_i64("BITPIX").ok_or_else(|| anyhow!("missing BITPIX"))?;
        let bscale = h.get_f64("BSCALE").unwrap_or(1.0);
        let bzero = h.get_f64("BZERO").unwrap_or(0.0);
        let blank = if blank_to_nan { h.get_i64("BLANK") } else { None };
        let raw = self.data(hdu);

        let scale = |v: f64| (v * bscale + bzero) as f32;
        let int = |v: i64| if Some(v) == blank { f32::NAN } else { scale(v as f64) };

        let data: Vec<f32> = match bitpix {
            8 => raw.iter().take(n).map(|&b| int(b as i64)).collect(),
            16 => raw.as_chunks().0.iter().take(n).map(|&c| int(i16::from_be_bytes(c) as i64)).collect(),
            32 => raw.as_chunks().0.iter().take(n).map(|&c| int(i32::from_be_bytes(c) as i64)).collect(),
            64 => raw.as_chunks().0.iter().take(n).map(|&c| int(i64::from_be_bytes(c))).collect(),
            -32 => raw.as_chunks().0.iter().take(n).map(|&c| scale(f32::from_be_bytes(c) as f64)).collect(),
            -64 => raw.as_chunks().0.iter().take(n).map(|&c| scale(f64::from_be_bytes(c))).collect(),
            other => bail!("unsupported BITPIX {other}"),
        };
        if data.len() != n {
            bail!("image data too short");
        }
        Ok(data)
    }

    /// Reads a 2-D image HDU, applying BSCALE/BZERO and mapping BLANK to NaN.
    pub fn read_image(&self, index: usize) -> Result<Image> {
        let hdu = self.hdus.get(index).ok_or_else(|| anyhow!("no HDU {index}"))?;
        let axes = hdu.axes();
        if axes.len() < 2 || axes[2..].iter().any(|&n| n != 1) {
            bail!("expected a 2-D image, found NAXIS = {:?}", axes);
        }
        let (width, height) = (axes[0], axes[1]);
        let data = self.decode_image(hdu, width * height, true)?;
        Ok(Image { width, height, data })
    }

    /// The primary array as astropy and numpy hand it to the full app:
    /// `np.squeeze`, then the first plane of any remaining leading axes.
    /// Returns `(rows, cols, data)` in numpy order (rows = the slower axis).
    ///
    /// astropy maps BLANK to NaN only when it scales the data to floats; an
    /// unscaled integer image (or the unsigned-integer BZERO convention) keeps
    /// the BLANK value as a number.
    pub fn read_primary_array(&self) -> Result<(usize, usize, Vec<f32>)> {
        let hdu = &self.hdus[0];
        let axes = hdu.axes();
        if axes.is_empty() || axes.iter().product::<usize>() == 0 {
            bail!("Primary HDU has no data.");
        }
        // numpy shape is the FITS axes reversed; squeeze drops length-1 axes.
        let shape: Vec<usize> = axes.iter().rev().copied().filter(|&n| n != 1).collect();
        if shape.len() < 2 {
            bail!("Expected 2D data after squeeze, got shape=({}).", shape_text(&shape));
        }
        let (rows, cols) = (shape[shape.len() - 2], shape[shape.len() - 1]);

        let h = &hdu.header;
        let bitpix = h.get_i64("BITPIX").unwrap_or(8);
        let bscale = h.get_f64("BSCALE").unwrap_or(1.0);
        let bzero = h.get_f64("BZERO").unwrap_or(0.0);
        let unsigned_convention = bitpix > 0 && bscale == 1.0 && bzero == 2f64.powi(bitpix as i32 - 1);
        let signed_byte_convention = bitpix == 8 && bscale == 1.0 && bzero == -128.0;
        let scaled = bitpix > 0 && (bscale != 1.0 || bzero != 0.0) && !unsigned_convention && !signed_byte_convention;
        let data = self.decode_image(hdu, rows * cols, scaled)?;
        Ok((rows, cols, data))
    }

    /// Index of the first binary table extension, if any.
    pub fn find_bintable(&self) -> Option<usize> {
        self.hdus.iter().position(|h| h.kind() == "BINTABLE")
    }

    /// Indexes of every binary table extension, in file order.
    pub fn bintables(&self) -> impl Iterator<Item = usize> + '_ {
        self.hdus.iter().enumerate().skip(1).filter(|(_, h)| h.kind() == "BINTABLE").map(|(i, _)| i)
    }

    /// Reads all elements of a numeric column in one row of a binary table.
    pub fn read_column(&self, index: usize, name: &str, row: usize) -> Result<Vec<f64>> {
        let (layout, code, repeat) = self.column_layout(index, name)?;
        self.read_cell(index, &layout, code, repeat, row)
    }

    /// A column as the full app's `_col_to_1d(table[name])` sees it: the
    /// first row of a vector column, or a scalar column read down all rows.
    /// The name is matched case-insensitively.
    pub fn read_table_axis(&self, index: usize, name: &str) -> Result<Vec<f64>> {
        let (layout, code, repeat) = self.column_layout(index, name)?;
        if repeat == 1 && layout.n_rows > 1 {
            (0..layout.n_rows)
                .map(|row| Ok(self.read_cell(index, &layout, code, 1, row)?[0]))
                .collect::<Result<Vec<_>>>()
        } else {
            self.read_cell(index, &layout, code, repeat, 0)
        }
    }

    /// Column names of a binary table, in order.
    pub fn column_names(&self, index: usize) -> Vec<String> {
        let Some(hdu) = self.hdus.get(index) else { return Vec::new() };
        let n = hdu.header.get_i64("TFIELDS").unwrap_or(0).max(0) as usize;
        (1..=n).map(|i| hdu.header.get_str(&format!("TTYPE{i}")).unwrap_or_default()).collect()
    }

    fn column_layout(&self, index: usize, name: &str) -> Result<(ColumnLayout, char, usize)> {
        let hdu = self.hdus.get(index).ok_or_else(|| anyhow!("no HDU {index}"))?;
        let h = &hdu.header;
        let row_len = h.get_i64("NAXIS1").unwrap_or(0) as usize;
        let n_rows = h.get_i64("NAXIS2").unwrap_or(0) as usize;
        let n_fields = h.get_i64("TFIELDS").unwrap_or(0) as usize;
        let mut offset = 0;
        for i in 1..=n_fields {
            let tform = h.get_str(&format!("TFORM{i}")).ok_or_else(|| anyhow!("missing TFORM{i}"))?;
            let (repeat, code) = parse_tform(&tform)?;
            let width = repeat * type_size(code)?;
            let ttype = h.get_str(&format!("TTYPE{i}")).unwrap_or_default();
            if ttype.eq_ignore_ascii_case(name) {
                let tscal = h.get_f64(&format!("TSCAL{i}")).unwrap_or(1.0);
                let tzero = h.get_f64(&format!("TZERO{i}")).unwrap_or(0.0);
                return Ok((ColumnLayout { row_len, n_rows, offset, width, tscal, tzero }, code, repeat));
            }
            offset += width;
        }
        bail!("column {name} not found")
    }

    fn read_cell(
        &self,
        index: usize,
        layout: &ColumnLayout,
        code: char,
        repeat: usize,
        row: usize,
    ) -> Result<Vec<f64>> {
        if row >= layout.n_rows {
            bail!("row {row} out of range ({} rows)", layout.n_rows);
        }
        let start = row * layout.row_len + layout.offset;
        let raw = self
            .data(&self.hdus[index])
            .get(start..start + layout.width)
            .ok_or_else(|| anyhow!("column extends past table data"))?;
        let values = decode_numeric(raw, code, repeat)?;
        Ok(values.into_iter().map(|v| v * layout.tscal + layout.tzero).collect())
    }
}

struct ColumnLayout {
    row_len: usize,
    n_rows: usize,
    offset: usize,
    width: usize,
    tscal: f64,
    tzero: f64,
}

fn shape_text(shape: &[usize]) -> String {
    match shape {
        [n] => format!("{n},"),
        other => other.iter().map(usize::to_string).collect::<Vec<_>>().join(", "),
    }
}

fn parse_header(bytes: &[u8]) -> Result<(Header, usize)> {
    let mut cards = Vec::new();
    for (i, chunk) in bytes.as_chunks::<CARD>().0.iter().enumerate() {
        let text = String::from_utf8_lossy(chunk);
        let key = text.get(..8).unwrap_or(&text[..]).trim_end().to_string();
        if key == "END" {
            let len = (i + 1) * CARD;
            return Ok((Header { cards }, len.div_ceil(BLOCK) * BLOCK));
        }
        cards.push(parse_card(&text, key));
    }
    bail!("header END card not found")
}

fn parse_card(text: &str, key: String) -> Card {
    let raw = text.trim_end().to_string();
    let rest = text.get(8..).unwrap_or("");
    if !rest.starts_with('=') {
        // COMMENT, HISTORY, blank, or a keyword without value indicator.
        return Card { key, value: None, comment: rest.trim().to_string(), quoted: false, raw };
    }
    let body = &rest[1..];
    let trimmed = body.trim_start();
    if let Some(s) = trimmed.strip_prefix('\'') {
        // Quoted string, with '' as an escaped quote.
        let mut value = String::new();
        let mut chars = s.char_indices().peekable();
        let mut end = s.len();
        while let Some((idx, c)) = chars.next() {
            if c == '\'' {
                if matches!(chars.peek(), Some((_, '\''))) {
                    value.push('\'');
                    chars.next();
                } else {
                    end = idx + 1;
                    break;
                }
            } else {
                value.push(c);
            }
        }
        let after = &s[end.min(s.len())..];
        let comment = after.split_once('/').map(|(_, c)| c.trim().to_string()).unwrap_or_default();
        Card { key, value: Some(value.trim_end().to_string()), comment, quoted: true, raw }
    } else {
        let (v, c) = body.split_once('/').unwrap_or((body, ""));
        Card { key, value: Some(v.trim().to_string()), comment: c.trim().to_string(), quoted: false, raw }
    }
}

fn data_size(hdu: &Hdu) -> usize {
    let h = &hdu.header;
    let axes = hdu.axes();
    if axes.is_empty() {
        return 0;
    }
    let bitpix = h.get_i64("BITPIX").unwrap_or(8).unsigned_abs() as usize;
    let pcount = h.get_i64("PCOUNT").unwrap_or(0).max(0) as usize;
    let gcount = h.get_i64("GCOUNT").unwrap_or(1).max(1) as usize;
    let product: usize = axes.iter().product();
    bitpix / 8 * gcount * (pcount + product)
}

/// Parses a TFORM value like `3600D8.3` (e-CALLISTO appends a display format) into
/// (repeat count, type code).
fn parse_tform(tform: &str) -> Result<(usize, char)> {
    let t = tform.trim();
    let digits: String = t.chars().take_while(|c| c.is_ascii_digit()).collect();
    let code = t[digits.len()..].chars().next().ok_or_else(|| anyhow!("bad TFORM '{tform}'"))?.to_ascii_uppercase();
    let repeat = if digits.is_empty() { 1 } else { digits.parse()? };
    Ok((repeat, code))
}

fn type_size(code: char) -> Result<usize> {
    Ok(match code {
        'L' | 'B' | 'A' => 1,
        'X' => 1, // approximated; bit arrays are never read here
        'I' => 2,
        'J' | 'E' => 4,
        'K' | 'D' | 'C' | 'P' => 8,
        'M' | 'Q' => 16,
        other => bail!("unsupported TFORM type '{other}'"),
    })
}

fn decode_numeric(raw: &[u8], code: char, repeat: usize) -> Result<Vec<f64>> {
    Ok(match code {
        'B' => raw.iter().take(repeat).map(|&b| b as f64).collect(),
        'I' => raw.as_chunks().0.iter().take(repeat).map(|&c| i16::from_be_bytes(c) as f64).collect(),
        'J' => raw.as_chunks().0.iter().take(repeat).map(|&c| i32::from_be_bytes(c) as f64).collect(),
        'K' => raw.as_chunks().0.iter().take(repeat).map(|&c| i64::from_be_bytes(c) as f64).collect(),
        'E' => raw.as_chunks().0.iter().take(repeat).map(|&c| f32::from_be_bytes(c) as f64).collect(),
        'D' => raw.as_chunks().0.iter().take(repeat).map(|&c| f64::from_be_bytes(c)).collect(),
        other => bail!("column type '{other}' is not numeric"),
    })
}

#[cfg(test)]
pub mod tests {
    use super::*;

    /// Formats header cards into padded FITS header blocks.
    pub fn header_bytes(cards: &[String]) -> Vec<u8> {
        let mut out = Vec::new();
        for c in cards.iter().map(String::as_str).chain(std::iter::once("END")) {
            let mut line = c.as_bytes().to_vec();
            line.resize(CARD, b' ');
            out.extend(line);
        }
        out.resize(out.len().div_ceil(BLOCK) * BLOCK, b' ');
        out
    }

    pub fn kv(key: &str, value: &str) -> String {
        format!("{key:<8}= {value:>20}")
    }

    pub fn ks(key: &str, value: &str) -> String {
        format!("{key:<8}= '{value:<8}'")
    }

    #[test]
    fn parses_cards() {
        let c = parse_card("DATE-OBS= '2011/06/07'         / Date observation starts", "DATE-OBS".into());
        assert_eq!(c.value.as_deref(), Some("2011/06/07"));
        assert_eq!(c.comment, "Date observation starts");
        assert!(c.quoted);
        let c = parse_card("CDELT1  =                 0.25 / step", "CDELT1".into());
        assert_eq!(c.value.as_deref(), Some("0.25"));
        let c = parse_card("OBJECT  = 'it''s / here'", "OBJECT".into());
        assert_eq!(c.value.as_deref(), Some("it's / here"));
        let c = parse_card("COMMENT   FITS format", "COMMENT".into());
        assert!(c.value.is_none());
    }

    #[test]
    fn formats_cards_like_astropy() {
        let c = Card::new("COMBINED", &HeaderValue::Bool(true), "Data are combined from multiple FITS files");
        assert_eq!(c.raw, "COMBINED=                    T / Data are combined from multiple FITS files");
        let c = Card::new("COMBMETH", &HeaderValue::Str("time".into()), "Combine method");
        assert_eq!(c.raw, "COMBMETH= 'time    '           / Combine method");
        let c = Card::new("FREQMIN", &HeaderValue::Float(45.0), "Min frequency (MHz)");
        assert_eq!(c.raw, "FREQMIN =                 45.0 / Min frequency (MHz)");
        let c = Card::new("NFILES", &HeaderValue::Int(4), "");
        assert_eq!(c.raw, "NFILES  =                    4");
    }

    #[test]
    fn sets_in_place_or_appends() {
        let mut h = Header::default();
        h.set("A", HeaderValue::Int(1), "");
        h.set("B", HeaderValue::Int(2), "");
        h.set("A", HeaderValue::Int(3), "x");
        assert_eq!(h.cards.iter().map(|c| c.key.as_str()).collect::<Vec<_>>(), ["A", "B"]);
        assert_eq!(h.get_i64("A"), Some(3));
        h.add_history("Combined by e-CALLISTO FITS Analyzer");
        assert_eq!(h.cards.last().unwrap().raw, "HISTORY Combined by e-CALLISTO FITS Analyzer");
    }

    #[test]
    fn parses_tform() {
        assert_eq!(parse_tform("3600D8.3").unwrap(), (3600, 'D'));
        assert_eq!(parse_tform("E").unwrap(), (1, 'E'));
    }

    #[test]
    fn reads_image_and_table() {
        let mut bytes = header_bytes(&[
            kv("SIMPLE", "T"),
            kv("BITPIX", "16"),
            kv("NAXIS", "2"),
            kv("NAXIS1", "3"),
            kv("NAXIS2", "2"),
            kv("BZERO", "10."),
            kv("EXTEND", "T"),
        ]);
        let mut data: Vec<u8> = [1i16, 2, 3, -4, 5, 6].iter().flat_map(|v| v.to_be_bytes()).collect();
        data.resize(BLOCK, 0);
        bytes.extend(data);
        bytes.extend(header_bytes(&[
            ks("XTENSION", "BINTABLE"),
            kv("BITPIX", "8"),
            kv("NAXIS", "2"),
            kv("NAXIS1", "20"),
            kv("NAXIS2", "1"),
            kv("PCOUNT", "0"),
            kv("GCOUNT", "1"),
            kv("TFIELDS", "2"),
            ks("TTYPE1", "A"),
            ks("TFORM1", "1E"),
            ks("TTYPE2", "FREQUENCY"),
            ks("TFORM2", "2D8.3"),
        ]));
        let mut table: Vec<u8> = 1.5f32.to_be_bytes().to_vec();
        table.extend(45.0f64.to_be_bytes());
        table.extend(46.5f64.to_be_bytes());
        table.resize(BLOCK, 0);
        bytes.extend(table);

        let f = FitsFile::from_bytes(bytes).unwrap();
        assert_eq!(f.hdus.len(), 2);
        let img = f.read_image(0).unwrap();
        assert_eq!((img.width, img.height), (3, 2));
        assert_eq!(img.data, vec![11.0, 12.0, 13.0, 6.0, 15.0, 16.0]);
        let t = f.find_bintable().unwrap();
        assert_eq!(f.read_column(t, "frequency", 0).unwrap(), vec![45.0, 46.5]);
        assert_eq!(f.read_table_axis(t, "frequency").unwrap(), vec![45.0, 46.5]);
        assert_eq!(f.read_table_axis(t, "a").unwrap(), vec![1.5]);
        let (rows, cols, data) = f.read_primary_array().unwrap();
        assert_eq!((rows, cols), (2, 3));
        assert_eq!(data, img.data);
    }
}
