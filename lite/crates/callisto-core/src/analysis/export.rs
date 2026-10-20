//! What the Analyzer's Save Data writes (`AnalyzeDialog.save_data`): the
//! station and date read from the file name, and each shock parameter's
//! value and error taken back out of its label's text.

/// The columns of a new workbook.
pub const ANALYZER_HEADERS: [&str; 22] = [
    "Date",
    "Station",
    "Best_fit",
    "R_sq",
    "RMSE",
    "avg_freq",
    "avg_freq_err",
    "Avg_drift",
    "avg_drift_err",
    "start_freq",
    "start_freq_err",
    "initial_shock_speed",
    "initial_shock_speed_err",
    "initial_shock_height",
    "initial_shock_height_err",
    "avg_shock_speed",
    "avg_shock_speed_err",
    "avg_shock_height",
    "avg_shock_height_err",
    "avg_drift_abs",
    "density_model",
    "t0_s",
];

/// The e-Callisto stations `save_data` knows, in its order.
const STATIONS: [&str; 79] = [
    "ALASKA-ANCHORAGE",
    "ALASKA-COHOE",
    "ALASKA-HAARP",
    "ALGERIA-CRAAG",
    "ALMATY",
    "Arecibo-observatory",
    "AUSTRIA-Krumbach",
    "AUSTRIA-MICHELBACH",
    "AUSTRIA-OE3FLB",
    "AUSTRIA-UNIGRAZ",
    "Australia-ASSA",
    "BRAZIL",
    "BIR",
    "Croatia-Visnjan",
    "DENMARK",
    "EGYPT-Alexandria",
    "EGYPT-SpaceAgency",
    "ETHIOPIA",
    "FINLAND-Siuntio",
    "FINLAND-Kempele",
    "GERMANY-ESSEN",
    "GERMANY-DLR",
    "GLASGOW",
    "GREENLAND",
    "HUMAIN",
    "HURBANOVO",
    "INDIA-GAURI",
    "INDIA-Nashik",
    "INDIA-OOTY",
    "INDIA-UDAIPUR",
    "INDONESIA",
    "ITALY-Strassolt",
    "JAPAN-IBARAKI",
    "KASI",
    "KRIM",
    "MEXART",
    "MEXICO-ENSENADA-UNAM",
    "MEXICO-FCFM-UANL",
    "MEXICO-FCFM-UNACH",
    "MEXICO-LANCE-A",
    "MEXICO-LANCE-B",
    "MEXICO-UANL-INFIERNILLO",
    "MONGOLIA-UB",
    "MRO",
    "MRT1",
    "MRT3",
    "Malaysia_Banting",
    "NASA-GSFC",
    "NORWAY-EGERSUND",
    "NORWAY-NY-AALESUND",
    "NORWAY-RANDABERG",
    "PARAGUAY",
    "POLAND-BALDY",
    "POLAND-Grotniki",
    "ROMANIA",
    "ROSWELL-NM",
    "RWANDA",
    "SOUTHAFRICA-SANSA",
    "SPAIN-ALCALA",
    "SPAIN-PERALEJOS",
    "SPAIN-SIGUENZA",
    "SRI-Lanka",
    "SSRT",
    "SWISS-CalU",
    "SWISS-FM",
    "SWISS-HB9SCT",
    "SWISS-HEITERSWIL",
    "SWISS-IRSOL",
    "SWISS-Landschlacht",
    "SWISS-MUHEN",
    "TAIWAN-NCU",
    "THAILAND-Pathumthani",
    "TRIEST",
    "TURKEY",
    "UNAM",
    "URUGUAY",
    "USA-ARIZONA-ERAU",
    "USA-BOSTON",
    "UZBEKISTAN",
];

/// The first known station the file name starts with (case-insensitive).
pub fn station_from_filename(filename: &str) -> String {
    let lower = filename.to_lowercase();
    STATIONS.iter().find(|s| lower.starts_with(&s.to_lowercase())).map_or_else(|| "UNKNOWN".to_owned(), |s| (*s).to_owned())
}

/// `re.search(r'_(\d{4})(\d{2})(\d{2})_', filename)` as `YYYY-MM-DD`.
pub fn date_from_filename(filename: &str) -> String {
    let b = filename.as_bytes();
    for i in 0..b.len() {
        if b[i] != b'_' || i + 10 > b.len() {
            continue;
        }
        let digits = &b[i + 1..i + 9];
        if digits.iter().all(u8::is_ascii_digit) && b[i + 9] == b'_' {
            let d = &filename[i + 1..i + 9];
            return format!("{}-{}-{}", &d[..4], &d[4..6], &d[6..8]);
        }
    }
    "UNKNOWN".into()
}

/// `extract_val_err`: the label's text without tags, units and every `s`
/// and `/` removed (`re.sub(r'(MHz|km/s|Rₛ|s|/)', '', …)`), then the value
/// and the error either side of `±` after the last colon.
pub fn value_and_error(plain: &str) -> (String, String) {
    let mut cleaned = String::new();
    let mut rest = plain;
    'scan: while let Some(c) = rest.chars().next() {
        for pat in ["MHz", "km/s", "R\u{209b}", "s", "/"] {
            if let Some(after) = rest.strip_prefix(pat) {
                rest = after;
                continue 'scan;
            }
        }
        cleaned.push(c);
        rest = &rest[c.len_utf8()..];
    }
    let value_text = cleaned.trim().rsplit(':').next().unwrap_or("").trim();
    match value_text.split_once('\u{b1}') {
        Some((v, e)) => (v.trim().to_owned(), e.trim().to_owned()),
        None => (value_text.to_owned(), String::new()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn like_save_data() {
        assert_eq!(station_from_filename("INDIA-GAURI_20261007_041500_02"), "INDIA-GAURI");
        assert_eq!(station_from_filename("nowhere_20261007"), "UNKNOWN");
        assert_eq!(date_from_filename("INDIA-GAURI_20261007_041500_02"), "2026-10-07");
        assert_eq!(value_and_error("Average Drift Rate: -0.0920 \u{b1} 0.0033 MHz/s"), ("-0.0920".into(), "0.0033".into()));
        assert_eq!(value_and_error("Initial Shock Height: 1.374 \u{b1} 0.061 R\u{209b}"), ("1.374".into(), "0.061".into()));
        assert_eq!(value_and_error("Average Frequency:  \u{b1}  MHz"), (String::new(), String::new()));
    }
}
