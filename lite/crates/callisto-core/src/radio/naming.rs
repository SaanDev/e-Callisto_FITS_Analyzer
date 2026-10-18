//! e-CALLISTO filenames: `STATION_YYYYMMDD_HHMMSS[_HHMMSS]_FOCUS.fit[.gz]`.
//!
//! Two parsers, as in the full app:
//! * [`parse_filename`] — `burst_processor.parse_filename`, a plain split used
//!   by the combine logic (the station is the first field);
//! * [`parse_archive_filename`] — `callisto_naming.parse_callisto_archive_filename`,
//!   which allows underscores in station names (`Malaysia_Banting`).

use chrono::NaiveDateTime;

pub const FITS_SUFFIXES: [&str; 4] = [".fit.gz", ".fits.gz", ".fit", ".fits"];

pub fn has_fits_suffix(name: &str) -> bool {
    let lower = name.to_lowercase();
    FITS_SUFFIXES.iter().any(|s| lower.ends_with(s))
}

pub fn basename(path: &str) -> &str {
    path.rsplit(['/', '\\']).next().unwrap_or(path)
}

/// `name` without a FITS suffix (case-insensitive).
pub fn strip_fits_suffix(name: &str) -> &str {
    let lower = name.to_lowercase();
    for s in FITS_SUFFIXES {
        if lower.ends_with(s) {
            return &name[..name.len() - s.len()];
        }
    }
    name
}

/// `(station, date, time, receiver_id)` from a plain split on `_`.
pub fn parse_filename(path: &str) -> Result<(String, String, String, String), String> {
    let base = basename(path);
    let stem = strip_fits_suffix(base);
    let parts: Vec<&str> = stem.split('_').collect();
    if parts.len() < 4 {
        return Err(format!("Invalid CALLISTO filename format: {base}"));
    }
    Ok((parts[0].into(), parts[1].into(), parts[2].into(), parts[parts.len() - 1].into()))
}

/// `(station, observed_at, receiver_id)` for an archive filename.
pub fn parse_archive_filename(name: &str) -> Result<(String, NaiveDateTime, String), String> {
    let base = basename(name.trim()).trim();
    let stem = strip_fits_suffix(base);
    let parts: Vec<&str> = stem.split('_').collect();
    let is_date = |s: &str| s.len() == 8 && s.bytes().all(|b| b.is_ascii_digit());
    let is_time = |s: &str| s.len() == 6 && s.bytes().all(|b| b.is_ascii_digit());
    for idx in 1..parts.len().saturating_sub(2) {
        if is_date(parts[idx]) && idx + 1 < parts.len() && is_time(parts[idx + 1]) {
            let station = parts[..idx].join("_").trim().to_string();
            if station.is_empty() {
                break;
            }
            let stamp = format!("{}{}", parts[idx], parts[idx + 1]);
            let observed = NaiveDateTime::parse_from_str(&stamp, "%Y%m%d%H%M%S")
                .map_err(|_| format!("Invalid CALLISTO timestamp in filename: {base}"))?;
            let receiver = parts[parts.len() - 1].trim().to_string();
            if receiver.is_empty() {
                return Err(format!("Missing receiver id in CALLISTO filename: {base}"));
            }
            return Ok((station, observed, receiver));
        }
    }
    Err(format!("Invalid CALLISTO filename format: {base}"))
}

/// `burst_processor._normalize_focus_code`: the last alphanumeric token that
/// is not a word like FOCUS or RECEIVER, upper-cased.
pub fn normalize_focus_code(value: &str) -> String {
    let text = value.trim();
    if text.is_empty() {
        return String::new();
    }
    let tokens: Vec<&str> = text.split(|c: char| !c.is_ascii_alphanumeric()).filter(|t| !t.is_empty()).collect();
    if tokens.is_empty() {
        return text.to_uppercase();
    }
    const IGNORE: [&str; 7] = ["FOCUS", "FOCUSCODE", "FOCUSID", "RECEIVER", "RECEIVERID", "RCVR", "RCVRID"];
    let filtered: Vec<&&str> = tokens.iter().filter(|t| !IGNORE.contains(&t.to_uppercase().as_str())).collect();
    let chosen = filtered.last().map(|t| **t).unwrap_or(tokens[tokens.len() - 1]);
    chosen.trim().to_uppercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_names() {
        let (s, d, t, r) = parse_filename("C:/x/BIR_20261007_101459_01.fit.gz").unwrap();
        assert_eq!((s.as_str(), d.as_str(), t.as_str(), r.as_str()), ("BIR", "20261007", "101459", "01"));
        let (s, at, r) = parse_archive_filename("Malaysia_Banting_20240101_000000_59.fit.gz").unwrap();
        assert_eq!(s, "Malaysia_Banting");
        assert_eq!(at.format("%H%M%S").to_string(), "000000");
        assert_eq!(r, "59");
        assert!(parse_archive_filename("nonsense.fit").is_err());
        assert_eq!(normalize_focus_code(" focus 01 "), "01");
        assert_eq!(normalize_focus_code("59"), "59");
    }
}
