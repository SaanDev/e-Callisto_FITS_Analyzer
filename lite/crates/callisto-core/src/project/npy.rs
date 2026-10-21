//! NumPy's `.npy` format (`numpy.lib.format`) and `.npz` archives
//! (`np.savez_compressed`, `np.load`): a header with the dtype, the order
//! and the shape, then the raw elements. Arrays keep their bytes as read,
//! so the ones Lite does not use go back into a project unchanged.

use std::collections::BTreeMap;
use std::io::{Cursor, Read, Write};

use anyhow::{Context, Result, anyhow, bail};

const MAGIC: &[u8] = b"\x93NUMPY";
/// `ARRAY_ALIGN` and `GROWTH_AXIS_MAX_DIGITS`.
const ALIGN: usize = 64;
const GROWTH_DIGITS: usize = 21;

/// One `.npy` array: its header fields and the whole file's bytes.
#[derive(Clone, Debug, PartialEq)]
pub struct Npy {
    pub descr: String,
    pub fortran_order: bool,
    pub shape: Vec<usize>,
    bytes: Vec<u8>,
    data_offset: usize,
}

/// A numeric element type: kind, size in bytes, big-endian.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    Float,
    Int,
    Uint,
    Bool,
}

fn parse_descr(descr: &str) -> Option<(Kind, usize, bool)> {
    let d = descr.trim();
    let (big, rest) = match d.as_bytes().first()? {
        b'>' => (true, &d[1..]),
        b'<' | b'=' | b'|' => (false, &d[1..]),
        _ => (false, d),
    };
    let kind = match rest.as_bytes().first()? {
        b'f' => Kind::Float,
        b'i' => Kind::Int,
        b'u' => Kind::Uint,
        b'b' => Kind::Bool,
        _ => return None,
    };
    let size: usize = rest[1..].parse().ok()?;
    let ok = match kind {
        Kind::Float => matches!(size, 4 | 8),
        Kind::Bool => size == 1,
        _ => matches!(size, 1 | 2 | 4 | 8),
    };
    ok.then_some((kind, size, big))
}

/// `repr(tuple)` of a shape: `()`, `(3,)`, `(3, 4)`.
fn shape_repr(shape: &[usize]) -> String {
    match shape {
        [] => "()".into(),
        [n] => format!("({n},)"),
        _ => format!("({})", shape.iter().map(|n| n.to_string()).collect::<Vec<_>>().join(", ")),
    }
}

/// The header dict's fields from its Python literal.
fn parse_header(text: &str) -> Result<(String, bool, Vec<usize>)> {
    let find = |key: &str| -> Result<&str> {
        let at = text.find(&format!("'{key}'")).ok_or_else(|| anyhow!("npy header has no '{key}'"))?;
        let rest = &text[at + key.len() + 2..];
        let colon = rest.find(':').ok_or_else(|| anyhow!("bad npy header"))?;
        Ok(rest[colon + 1..].trim_start())
    };
    let d = find("descr")?;
    let quote = d.chars().next().filter(|c| *c == '\'' || *c == '"').ok_or_else(|| anyhow!("unsupported npy descr"))?;
    let end = d[1..].find(quote).ok_or_else(|| anyhow!("bad npy descr"))?;
    let descr = d[1..1 + end].to_owned();
    let fortran = find("fortran_order")?.starts_with("True");
    let s = find("shape")?;
    let close = s.find(')').ok_or_else(|| anyhow!("bad npy shape"))?;
    let shape = s[1..close]
        .split(',')
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .map(|t| t.trim_end_matches('L').parse::<usize>().map_err(|_| anyhow!("bad npy shape")))
        .collect::<Result<Vec<_>>>()?;
    Ok((descr, fortran, shape))
}

impl Npy {
    /// Reads a `.npy` file's bytes (format versions 1, 2 and 3).
    pub fn parse(bytes: Vec<u8>) -> Result<Self> {
        if bytes.len() < 10 || &bytes[..6] != MAGIC {
            bail!("not a .npy array");
        }
        let (len, start) = match bytes[6] {
            1 => (u16::from_le_bytes([bytes[8], bytes[9]]) as usize, 10),
            2 | 3 => {
                let b = bytes.get(8..12).ok_or_else(|| anyhow!("truncated .npy header"))?;
                (u32::from_le_bytes([b[0], b[1], b[2], b[3]]) as usize, 12)
            }
            v => bail!("unsupported .npy version {v}"),
        };
        let header = bytes.get(start..start + len).ok_or_else(|| anyhow!("truncated .npy header"))?;
        let (descr, fortran_order, shape) = parse_header(&String::from_utf8_lossy(header))?;
        Ok(Npy { descr, fortran_order, shape, bytes, data_offset: start + len })
    }

    /// The file's bytes.
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    /// The element bytes.
    pub fn data(&self) -> &[u8] {
        &self.bytes[self.data_offset..]
    }

    pub fn len(&self) -> usize {
        self.shape.iter().product()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// An array with `descr` and the elements' bytes in C order
    /// (`np.save`'s header, version 1.0 while the header fits).
    pub fn from_raw(descr: &str, shape: &[usize], data: Vec<u8>) -> Self {
        let mut header = format!("{{'descr': '{descr}', 'fortran_order': False, 'shape': {}, }}", shape_repr(shape));
        if let Some(&first) = shape.first() {
            header.push_str(&" ".repeat(GROWTH_DIGITS.saturating_sub(first.to_string().len())));
        }
        let mut out = Vec::with_capacity(data.len() + 128);
        out.extend_from_slice(MAGIC);
        let hlen = header.len() + 1;
        let (prefix, version) = if 10 + hlen + ALIGN < 65536 { (10, 1u8) } else { (12, 2u8) };
        let pad = ALIGN - ((prefix + hlen) % ALIGN);
        out.extend_from_slice(&[version, 0]);
        if version == 1 {
            out.extend_from_slice(&((hlen + pad) as u16).to_le_bytes());
        } else {
            out.extend_from_slice(&((hlen + pad) as u32).to_le_bytes());
        }
        out.extend_from_slice(header.as_bytes());
        out.extend(std::iter::repeat_n(b' ', pad));
        out.push(b'\n');
        let data_offset = out.len();
        out.extend_from_slice(&data);
        Npy { descr: descr.to_owned(), fortran_order: false, shape: shape.to_vec(), bytes: out, data_offset }
    }

    pub fn from_f64(shape: &[usize], v: &[f64]) -> Self {
        Self::from_raw("<f8", shape, v.iter().flat_map(|x| x.to_le_bytes()).collect())
    }

    pub fn from_f32(shape: &[usize], v: &[f32]) -> Self {
        Self::from_raw("<f4", shape, v.iter().flat_map(|x| x.to_le_bytes()).collect())
    }

    pub fn from_bool(shape: &[usize], v: &[bool]) -> Self {
        Self::from_raw("|b1", shape, v.iter().map(|&b| u8::from(b)).collect())
    }

    /// The elements as f64 in C order, for numeric and boolean arrays.
    pub fn to_f64(&self) -> Option<Vec<f64>> {
        let (kind, size, big) = parse_descr(&self.descr)?;
        let data = self.data();
        let n = self.len();
        if data.len() < n * size {
            return None;
        }
        let read = |b: &[u8]| -> f64 {
            let mut a = [0u8; 8];
            a[..size].copy_from_slice(b);
            if big {
                a[..size].reverse();
            }
            match (kind, size) {
                (Kind::Float, 4) => f32::from_le_bytes([a[0], a[1], a[2], a[3]]) as f64,
                (Kind::Float, _) => f64::from_le_bytes(a),
                (Kind::Bool, _) => f64::from(u8::from(a[0] != 0)),
                (Kind::Uint, _) => u64::from_le_bytes(a) as f64,
                (Kind::Int, _) => {
                    let shift = 64 - 8 * size as u32;
                    ((i64::from_le_bytes(a) << shift) >> shift) as f64
                }
            }
        };
        let values: Vec<f64> = data[..n * size].chunks_exact(size).map(read).collect();
        Some(self.c_order(values))
    }

    /// The elements as f32 (float32 arrays exactly; others converted).
    pub fn to_f32(&self) -> Option<Vec<f32>> {
        if let Some((Kind::Float, 4, big)) = parse_descr(&self.descr) {
            let n = self.len();
            let data = self.data().get(..n * 4)?;
            let values = data
                .as_chunks::<4>()
                .0
                .iter()
                .map(|&b| if big { f32::from_be_bytes(b) } else { f32::from_le_bytes(b) })
                .collect();
            return Some(self.c_order(values));
        }
        self.to_f64().map(|v| v.into_iter().map(|x| x as f32).collect())
    }

    pub fn to_bool(&self) -> Option<Vec<bool>> {
        self.to_f64().map(|v| v.into_iter().map(|x| x != 0.0).collect())
    }

    /// A Fortran-ordered 2-d array's elements in C order.
    fn c_order<T: Copy>(&self, values: Vec<T>) -> Vec<T> {
        if !self.fortran_order || self.shape.len() != 2 {
            return values;
        }
        let (rows, cols) = (self.shape[0], self.shape[1]);
        let mut out = Vec::with_capacity(values.len());
        for r in 0..rows {
            for c in 0..cols {
                out.push(values[c * rows + r]);
            }
        }
        out
    }
}

/// `np.load` of an `.npz`: each `<key>.npy` member.
pub fn read_npz(bytes: &[u8]) -> Result<BTreeMap<String, Npy>> {
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).context("Invalid arrays.npz (bad zip).")?;
    let mut out = BTreeMap::new();
    for i in 0..zip.len() {
        let mut f = zip.by_index(i)?;
        let name = f.name()?.into_owned();
        if f.is_dir() {
            continue;
        }
        let mut buf = Vec::with_capacity(f.size() as usize);
        f.read_to_end(&mut buf)?;
        let key = name.strip_suffix(".npy").unwrap_or(&name).to_owned();
        out.insert(key, Npy::parse(buf).with_context(|| format!("Invalid array {name} in arrays.npz"))?);
    }
    Ok(out)
}

/// `np.savez_compressed`: each array deflated as `<key>.npy`.
pub fn write_npz(arrays: &BTreeMap<String, Npy>) -> Result<Vec<u8>> {
    let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let opts =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated).large_file(true);
    for (key, arr) in arrays {
        zip.start_file(format!("{key}.npy"), opts)?;
        zip.write_all(arr.bytes())?;
    }
    Ok(zip.finish()?.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn headers_like_numpy() {
        // np.save of np.arange(3.0): the 128-byte header numpy 2 writes.
        let a = Npy::from_f64(&[3], &[0.0, 1.0, 2.0]);
        assert_eq!(a.data_offset % ALIGN, 0);
        let text = String::from_utf8_lossy(&a.bytes()[10..a.data_offset]).into_owned();
        assert!(text.starts_with("{'descr': '<f8', 'fortran_order': False, 'shape': (3,), }"), "{text}");
        assert!(text.ends_with('\n'));
        let back = Npy::parse(a.bytes().to_vec()).unwrap();
        assert_eq!(back.to_f64().unwrap(), vec![0.0, 1.0, 2.0]);
        assert_eq!(back.shape, vec![3]);
    }

    #[test]
    fn dtypes_and_orders() {
        let be = Npy::from_raw(">i2", &[2], vec![0xff, 0xfe, 0x00, 0x05]);
        assert_eq!(be.to_f64().unwrap(), vec![-2.0, 5.0]);
        let mut f = Npy::from_raw("<f4", &[2, 2], [1f32, 3.0, 2.0, 4.0].iter().flat_map(|x| x.to_le_bytes()).collect());
        f.fortran_order = true;
        assert_eq!(f.to_f32().unwrap(), vec![1.0, 2.0, 3.0, 4.0]);
        let b = Npy::from_bool(&[3], &[true, false, true]);
        assert_eq!(b.to_bool().unwrap(), vec![true, false, true]);
        let mut arrays = BTreeMap::new();
        arrays.insert("x".to_owned(), b.clone());
        let back = read_npz(&write_npz(&arrays).unwrap()).unwrap();
        assert_eq!(back["x"], b);
    }
}
