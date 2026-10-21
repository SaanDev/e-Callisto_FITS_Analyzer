//! Just enough of Office Open XML spreadsheets for the Analyzer's Save Data
//! (`openpyxl`'s `Workbook().append(...)`, and `load_workbook(path).active
//! .append(row)` on an existing file): a new one-sheet workbook from rows,
//! and a row appended to the active sheet of an existing workbook, leaving
//! everything else in it as it was. Strings are written inline.

use std::io::{Read, Write};
use std::path::Path;

use anyhow::{Context, Result, anyhow, bail};

/// A cell's value.
#[derive(Clone, Debug, PartialEq)]
pub enum Cell {
    Str(String),
    Num(f64),
}

// ------------------------------------------------------------------ zip

fn crc32(data: &[u8]) -> u32 {
    let mut table = [0u32; 256];
    for (i, t) in table.iter_mut().enumerate() {
        let mut c = i as u32;
        for _ in 0..8 {
            c = if c & 1 != 0 { 0xedb8_8320 ^ (c >> 1) } else { c >> 1 };
        }
        *t = c;
    }
    let mut crc = 0xffff_ffffu32;
    for &b in data {
        crc = table[((crc ^ b as u32) & 0xff) as usize] ^ (crc >> 8);
    }
    !crc
}

fn u16_at(b: &[u8], i: usize) -> Result<u16> {
    Ok(u16::from_le_bytes(b.get(i..i + 2).ok_or_else(|| anyhow!("truncated zip"))?.try_into()?))
}

fn u32_at(b: &[u8], i: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(b.get(i..i + 4).ok_or_else(|| anyhow!("truncated zip"))?.try_into()?))
}

/// The entries of a zip archive, in order, decompressed.
fn read_zip(bytes: &[u8]) -> Result<Vec<(String, Vec<u8>)>> {
    let eocd = (0..bytes.len().saturating_sub(21))
        .rev()
        .find(|&i| bytes[i..].starts_with(&[0x50, 0x4b, 0x05, 0x06]))
        .context("not a zip archive")?;
    let count = u16_at(bytes, eocd + 10)? as usize;
    let mut at = u32_at(bytes, eocd + 16)? as usize;
    let mut out = Vec::with_capacity(count);
    for _ in 0..count {
        if u32_at(bytes, at)? != 0x0201_4b50 {
            bail!("bad zip directory");
        }
        let method = u16_at(bytes, at + 10)?;
        let csize = u32_at(bytes, at + 20)? as usize;
        let name_len = u16_at(bytes, at + 28)? as usize;
        let extra_len = u16_at(bytes, at + 30)? as usize;
        let comment_len = u16_at(bytes, at + 32)? as usize;
        let local = u32_at(bytes, at + 42)? as usize;
        if csize == 0xffff_ffff || local == 0xffff_ffff {
            bail!("ZIP64 workbooks are not supported");
        }
        let name =
            String::from_utf8_lossy(bytes.get(at + 46..at + 46 + name_len).context("truncated zip")?).into_owned();
        let data_at = local + 30 + u16_at(bytes, local + 26)? as usize + u16_at(bytes, local + 28)? as usize;
        let raw = bytes.get(data_at..data_at + csize).context("truncated zip entry")?;
        let data = match method {
            0 => raw.to_vec(),
            8 => {
                let mut d = Vec::new();
                flate2::read::DeflateDecoder::new(raw).read_to_end(&mut d)?;
                d
            }
            m => bail!("unsupported zip compression {m}"),
        };
        out.push((name, data));
        at += 46 + name_len + extra_len + comment_len;
    }
    Ok(out)
}

/// A zip archive of `entries`, deflated.
fn write_zip(entries: &[(String, Vec<u8>)]) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    let mut central = Vec::new();
    // 1980-01-01 00:00, as DOS time.
    let (time, date) = (0u16, (1 << 5) | 1u16);
    for (name, data) in entries {
        let mut enc = flate2::write::DeflateEncoder::new(Vec::new(), flate2::Compression::default());
        enc.write_all(data)?;
        let comp = enc.finish()?;
        let crc = crc32(data);
        let offset = out.len() as u32;
        let head = |sig: u32, central: bool| {
            let mut h = Vec::new();
            h.extend(sig.to_le_bytes());
            if central {
                h.extend(20u16.to_le_bytes());
            }
            h.extend(20u16.to_le_bytes());
            h.extend(0u16.to_le_bytes());
            h.extend(8u16.to_le_bytes());
            h.extend(time.to_le_bytes());
            h.extend(date.to_le_bytes());
            h.extend(crc.to_le_bytes());
            h.extend((comp.len() as u32).to_le_bytes());
            h.extend((data.len() as u32).to_le_bytes());
            h.extend((name.len() as u16).to_le_bytes());
            h.extend(0u16.to_le_bytes());
            h
        };
        out.extend(head(0x0403_4b50, false));
        out.extend(name.as_bytes());
        out.extend(&comp);
        let mut c = head(0x0201_4b50, true);
        c.extend(0u16.to_le_bytes()); // comment
        c.extend(0u16.to_le_bytes()); // disk
        c.extend(0u16.to_le_bytes()); // internal attributes
        c.extend(0u32.to_le_bytes()); // external attributes
        c.extend(offset.to_le_bytes());
        c.extend(name.as_bytes());
        central.extend(c);
    }
    let cd_at = out.len() as u32;
    out.extend(&central);
    out.extend(0x0605_4b50u32.to_le_bytes());
    out.extend(0u16.to_le_bytes());
    out.extend(0u16.to_le_bytes());
    out.extend((entries.len() as u16).to_le_bytes());
    out.extend((entries.len() as u16).to_le_bytes());
    out.extend((central.len() as u32).to_le_bytes());
    out.extend(cd_at.to_le_bytes());
    out.extend(0u16.to_le_bytes());
    Ok(out)
}

// ------------------------------------------------------------------ sheets

fn escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

/// `A`, `B`, …, `Z`, `AA`, … for a 0-based column.
fn column_name(mut c: usize) -> String {
    let mut s = Vec::new();
    loop {
        s.push(b'A' + (c % 26) as u8);
        if c < 26 {
            break;
        }
        c = c / 26 - 1;
    }
    s.reverse();
    String::from_utf8(s).unwrap_or_default()
}

/// One `<row>` of cells (row numbers 1-based).
fn row_xml(r: usize, cells: &[Cell]) -> String {
    let mut s = format!("<row r=\"{r}\">");
    for (c, cell) in cells.iter().enumerate() {
        let at = format!("{}{r}", column_name(c));
        match cell {
            Cell::Num(v) if v.is_finite() => s.push_str(&format!("<c r=\"{at}\" t=\"n\"><v>{v}</v></c>")),
            Cell::Num(v) => s.push_str(&format!("<c r=\"{at}\" t=\"inlineStr\"><is><t>{v}</t></is></c>")),
            Cell::Str(t) => s.push_str(&format!(
                "<c r=\"{at}\" t=\"inlineStr\"><is><t xml:space=\"preserve\">{}</t></is></c>",
                escape(t)
            )),
        }
    }
    s.push_str("</row>");
    s
}

/// Writes a new workbook whose one sheet ("Sheet") holds `rows`.
pub fn write_rows(path: &Path, rows: &[Vec<Cell>]) -> Result<()> {
    let ncols = rows.iter().map(Vec::len).max().unwrap_or(1).max(1);
    let dim = format!("A1:{}{}", column_name(ncols - 1), rows.len().max(1));
    let body: String = rows.iter().enumerate().map(|(i, r)| row_xml(i + 1, r)).collect();
    let sheet = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><dimension ref=\"{dim}\"/><sheetData>{body}</sheetData></worksheet>"
    );
    let entries = vec![
        (
            "[Content_Types].xml".to_owned(),
            b"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/><Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/></Types>".to_vec(),
        ),
        (
            "_rels/.rels".to_owned(),
            b"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>".to_vec(),
        ),
        (
            "xl/workbook.xml".to_owned(),
            b"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets><sheet name=\"Sheet\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>".to_vec(),
        ),
        (
            "xl/_rels/workbook.xml.rels".to_owned(),
            b"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/></Relationships>".to_vec(),
        ),
        ("xl/worksheets/sheet1.xml".to_owned(), sheet.into_bytes()),
    ];
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(path, write_zip(&entries)?).with_context(|| format!("writing {}", path.display()))
}

/// The value of attribute `name` in the first `<tag …>` of `xml` after `from`.
fn attr<'a>(xml: &'a str, tag_start: usize, name: &str) -> Option<&'a str> {
    let end = xml[tag_start..].find('>')? + tag_start;
    let tag = &xml[tag_start..end];
    let key = format!(" {name}=\"");
    let i = tag.find(&key)? + key.len();
    let j = tag[i..].find('"')? + i;
    Some(&tag[i..j])
}

/// The active sheet's part name (`xl/worksheets/…`).
fn active_sheet(entries: &[(String, Vec<u8>)]) -> Result<String> {
    let text =
        |name: &str| entries.iter().find(|(n, _)| n == name).map(|(_, d)| String::from_utf8_lossy(d).into_owned());
    let wb = text("xl/workbook.xml").context("no workbook part")?;
    let active: usize =
        wb.find("<workbookView").and_then(|i| attr(&wb, i, "activeTab")).and_then(|v| v.parse().ok()).unwrap_or(0);
    let sheets: Vec<usize> = wb.match_indices("<sheet ").map(|(i, _)| i).collect();
    let at = *sheets.get(active).or(sheets.first()).context("the workbook has no sheet")?;
    let rid = attr(&wb, at, "r:id").context("sheet without a relationship")?.to_owned();
    let rels = text("xl/_rels/workbook.xml.rels").context("no workbook relationships")?;
    let rel = rels
        .match_indices("<Relationship ")
        .map(|(i, _)| i)
        .find(|&i| attr(&rels, i, "Id") == Some(rid.as_str()))
        .context("missing sheet relationship")?;
    let target = attr(&rels, rel, "Target").context("relationship without target")?;
    Ok(match target.strip_prefix('/') {
        Some(abs) => abs.to_owned(),
        None => format!("xl/{target}"),
    })
}

/// Appends `cells` as a new last row of the active sheet of the workbook
/// at `path`, keeping its other parts.
pub fn append_row(path: &Path, cells: &[Cell]) -> Result<()> {
    let bytes = std::fs::read(path).with_context(|| format!("reading {}", path.display()))?;
    let mut entries = read_zip(&bytes)?;
    let sheet_name = active_sheet(&entries)?;
    let entry = entries.iter_mut().find(|(n, _)| *n == sheet_name).context("missing sheet part")?;
    let mut xml = String::from_utf8(std::mem::take(&mut entry.1)).context("sheet is not UTF-8")?;
    // The last row: the largest row number in use.
    let mut last = 0usize;
    for (i, _) in xml.match_indices("<row") {
        if xml[i + 4..].starts_with([' ', '>']) {
            last = attr(&xml, i, "r").and_then(|v| v.parse().ok()).unwrap_or(last + 1).max(last);
        }
    }
    let r = last + 1;
    let row = row_xml(r, cells);
    if let Some(i) = xml.find("</sheetData>") {
        xml.insert_str(i, &row);
    } else if let Some(i) = xml.find("<sheetData/>") {
        xml.replace_range(i..i + "<sheetData/>".len(), &format!("<sheetData>{row}</sheetData>"));
    } else {
        bail!("the sheet has no data section");
    }
    if let Some(i) = xml.find("<dimension ")
        && let Some(old) = attr(&xml, i, "ref")
    {
        let start = old.split(':').next().unwrap_or("A1");
        let cols = old
            .split(':')
            .nth(1)
            .map(|e| e.trim_end_matches(|c: char| c.is_ascii_digit()).to_owned())
            .unwrap_or_default();
        let width = cols.bytes().fold(0usize, |a, b| a * 26 + (b - b'A' + 1) as usize).max(cells.len());
        let new = format!("{start}:{}{r}", column_name(width.max(1) - 1));
        let from = xml[i..].find(old).map(|k| k + i);
        if let Some(k) = from {
            xml.replace_range(k..k + old.len(), &new);
        }
    }
    entry.1 = xml.into_bytes();
    std::fs::write(path, write_zip(&entries)?).with_context(|| format!("writing {}", path.display()))
}

/// The cells of the active sheet's rows (inline and shared strings, numbers), for checks.
pub fn read_rows(path: &Path) -> Result<Vec<Vec<String>>> {
    let entries = read_zip(&std::fs::read(path)?)?;
    let sheet = active_sheet(&entries)?;
    let xml =
        String::from_utf8_lossy(&entries.iter().find(|(n, _)| *n == sheet).context("missing sheet")?.1).into_owned();
    let shared: Vec<String> = entries
        .iter()
        .find(|(n, _)| n == "xl/sharedStrings.xml")
        .map(|(_, d)| {
            let s = String::from_utf8_lossy(d);
            s.split("<si>")
                .skip(1)
                .map(|si| {
                    si.split("<t")
                        .skip(1)
                        .filter_map(|t| t.split_once('>').map(|(_, r)| r.split("</t>").next().unwrap_or("").to_owned()))
                        .collect()
                })
                .collect()
        })
        .unwrap_or_default();
    let mut rows = Vec::new();
    for row in xml.split("<row").skip(1) {
        let row = row.split("</row>").next().unwrap_or("");
        let mut cells = Vec::new();
        for c in row.split("<c ").skip(1) {
            let shared_ref = c.contains("t=\"s\"");
            let value = if let Some(t) = c.split("<t").nth(1) {
                t.split_once('>').map(|(_, r)| r.split("</t>").next().unwrap_or("").to_owned()).unwrap_or_default()
            } else if let Some(v) = c.split("<v>").nth(1) {
                let v = v.split("</v>").next().unwrap_or("").to_owned();
                if shared_ref {
                    v.parse::<usize>().ok().and_then(|i| shared.get(i).cloned()).unwrap_or_default()
                } else {
                    v
                }
            } else {
                String::new()
            };
            cells.push(value.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&amp;", "&"));
        }
        rows.push(cells);
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc_of_known_text() {
        assert_eq!(crc32(b"123456789"), 0xcbf4_3926);
    }

    #[test]
    fn columns_are_letters() {
        assert_eq!(column_name(0), "A");
        assert_eq!(column_name(21), "V");
        assert_eq!(column_name(26), "AA");
    }

    #[test]
    fn write_then_append() {
        let dir = std::env::temp_dir().join(format!("callisto_xlsx_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("data.xlsx");
        write_rows(
            &path,
            &[
                vec![Cell::Str("Date".into()), Cell::Str("t0_s".into())],
                vec![Cell::Str("2026-10-07".into()), Cell::Num(0.0)],
            ],
        )
        .unwrap();
        append_row(&path, &[Cell::Str("a & b".into()), Cell::Num(1.5)]).unwrap();
        let rows = read_rows(&path).unwrap();
        assert_eq!(
            rows,
            vec![
                vec!["Date".to_owned(), "t0_s".into()],
                vec!["2026-10-07".into(), "0".into()],
                vec!["a & b".into(), "1.5".into()]
            ]
        );
        let _ = std::fs::remove_dir_all(dir);
    }
}
