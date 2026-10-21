//! Report a Bug's diagnostics (`src/backend/services/bug_report.py` and
//! `BugReportDialog._issue_body`): the issue text, the prefilled GitHub
//! issue URL and the diagnostics bundle (`bug_report.json`, `.md`, the
//! issue notes and the provenance report, zipped).

use std::io::Write as _;
use std::path::{Path, PathBuf};

use anyhow::{Result, bail};

use crate::pyjson::{self, Json, Object};

/// The context `_build_bug_report_context` gathers, each section in the
/// order the full app writes it.
#[derive(Clone, Debug)]
pub struct BugContext {
    pub summary: Vec<(String, Json)>,
    pub environment: Vec<(String, Json)>,
    pub session: Vec<(String, Json)>,
    /// `_processing_log`: (timestamp, message).
    pub operation_log: Vec<(String, String)>,
    pub processing: Json,
    pub rfi: Json,
}

impl Default for BugContext {
    fn default() -> Self {
        BugContext {
            summary: Vec::new(),
            environment: Vec::new(),
            session: Vec::new(),
            operation_log: Vec::new(),
            processing: Json::object(),
            rfi: Json::object(),
        }
    }
}

/// `default_bug_report_filename`.
pub fn default_filename() -> String {
    format!("bug_report_{}.zip", chrono::Local::now().format("%Y%m%d_%H%M%S"))
}

/// `BugReportDialog._issue_body`.
pub fn issue_body(details: &str, ctx: &BugContext, bundle: Option<&str>) -> String {
    let details = details.trim();
    let mut lines = vec![
        "## Description".to_owned(),
        if details.is_empty() { "(Please describe the issue.)".to_owned() } else { details.to_owned() },
        String::new(),
        "## Environment".to_owned(),
    ];
    let items = |lines: &mut Vec<String>, items: &[(String, Json)]| {
        for (k, v) in items {
            lines.push(format!("- {k}: {}", v.py_str()));
        }
    };
    if ctx.environment.is_empty() {
        lines.push("- (not available)".into());
    } else {
        items(&mut lines, &ctx.environment);
    }
    lines.push(String::new());
    lines.push("## Current Session".into());
    if ctx.summary.is_empty() {
        lines.push("- (not available)".into());
    } else {
        items(&mut lines, &ctx.summary);
    }
    items(&mut lines, &ctx.session);
    lines.push(String::new());
    lines.push("## Diagnostics".into());
    match bundle {
        Some(path) => {
            lines.push(format!("- Generated bundle: `{path}`"));
            lines.push("- Please attach this `.zip` file to the GitHub issue.".into());
        }
        None => {
            lines.push("- No diagnostics bundle generated yet.".into());
            lines.push("- Use `Generate Diagnostics...` and attach the created `.zip` file.".into());
        }
    }
    lines.push(String::new());
    lines.join("\n")
}

/// `urllib.parse.quote_plus`.
pub fn quote_plus(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(b as char),
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// `build_github_issue_url`.
pub fn issue_url(repo: &str, title: &str, body: &str) -> Result<String> {
    let repo = repo.trim().trim_matches('/');
    if repo.is_empty() {
        bail!("Repository is required for GitHub issue URL.");
    }
    let title = match title.trim() {
        "" => "Bug Report",
        t => t,
    };
    Ok(format!("https://github.com/{repo}/issues/new?title={}&body={}", quote_plus(title), quote_plus(body.trim())))
}

fn object(items: &[(String, Json)]) -> Json {
    Json::Object(items.iter().cloned().collect::<Object>())
}

/// `build_bug_report_payload`: `generated_at` and the report.
pub fn payload(ctx: &BugContext, generated_at: &str) -> Json {
    let mut report = Object::new();
    report.insert("summary".into(), object(&ctx.summary));
    report.insert("environment".into(), object(&ctx.environment));
    report.insert("session".into(), object(&ctx.session));
    report.insert(
        "operation_log".into(),
        Json::Array(
            ctx.operation_log
                .iter()
                .map(|(ts, msg)| {
                    let mut o = Object::new();
                    o.insert("ts".into(), ts.clone().into());
                    o.insert("msg".into(), msg.clone().into());
                    Json::Object(o)
                })
                .collect(),
        ),
    );
    report.insert("processing".into(), ctx.processing.clone());
    report.insert("rfi".into(), ctx.rfi.clone());
    let mut p = Object::new();
    p.insert("generated_at".into(), generated_at.into());
    p.insert("report".into(), Json::Object(report));
    Json::Object(p)
}

/// `_payload_to_markdown`.
pub fn payload_markdown(ctx: &BugContext, generated_at: &str) -> String {
    let mut lines =
        vec!["# Bug Report".to_owned(), String::new(), format!("Generated at: `{generated_at}`"), String::new()];
    for (title, items) in [("Summary", &ctx.summary), ("Environment", &ctx.environment), ("Session", &ctx.session)] {
        if items.is_empty() {
            continue;
        }
        lines.push(format!("## {title}"));
        for (k, v) in items {
            lines.push(format!("- {k}: `{}`", v.py_str()));
        }
        lines.push(String::new());
    }
    lines.push("## Operation Log".into());
    if ctx.operation_log.is_empty() {
        lines.push("- (none)".into());
    }
    for (ts, msg) in &ctx.operation_log {
        lines.push(format!("- `{ts}` {msg}"));
    }
    lines.push(String::new());
    lines.join("\n")
}

/// `write_bug_report_bundle`: the zip at `path` (`.zip` added when
/// missing), with the provenance report's JSON and Markdown if given.
pub fn write_bundle(path: &Path, ctx: &BugContext, provenance: Option<(&Json, &str)>, notes: &str) -> Result<PathBuf> {
    let text = path.to_string_lossy().trim().to_owned();
    if text.is_empty() {
        bail!("Output path is required for bug report bundle.");
    }
    let path =
        if text.to_lowercase().ends_with(".zip") { PathBuf::from(text) } else { PathBuf::from(format!("{text}.zip")) };
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::create_dir_all(dir)?;
    }
    let generated_at = crate::project::now_iso();
    let file = std::fs::File::create(&path)?;
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    let mut put = |name: &str, data: &str| -> Result<()> {
        zip.start_file(name, options)?;
        zip.write_all(data.as_bytes())?;
        Ok(())
    };
    put("bug_report.json", &pyjson::dumps(&payload(ctx, &generated_at)))?;
    put("bug_report.md", &payload_markdown(ctx, &generated_at))?;
    let notes = notes.trim();
    if !notes.is_empty() {
        put("issue_notes.md", notes)?;
    }
    if let Some((json, md)) = provenance {
        put("provenance.json", &pyjson::dumps(json))?;
        put("provenance.md", md)?;
    }
    zip.finish()?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx() -> BugContext {
        BugContext {
            summary: vec![("filename".into(), "a.fit".into()), ("use_db".into(), Json::Bool(false))],
            environment: vec![("app_name".into(), "Lite".into())],
            session: vec![("combined_mode".into(), Json::Null)],
            operation_log: vec![("2026-10-21T10:00:00".into(), "Loaded".into())],
            processing: Json::object(),
            rfi: Json::object(),
        }
    }

    #[test]
    fn issue_body_as_the_full_app_writes_it() {
        let body = issue_body("  It broke  ", &ctx(), None);
        assert_eq!(
            body,
            "## Description\nIt broke\n\n## Environment\n- app_name: Lite\n\n## Current Session\n- filename: a.fit\n- use_db: False\n- combined_mode: None\n\n## Diagnostics\n- No diagnostics bundle generated yet.\n- Use `Generate Diagnostics...` and attach the created `.zip` file.\n"
        );
        let empty = issue_body("", &BugContext::default(), Some("C:\\x.zip"));
        assert!(empty.starts_with("## Description\n(Please describe the issue.)\n\n## Environment\n- (not available)\n\n## Current Session\n- (not available)\n\n## Diagnostics\n- Generated bundle: `C:\\x.zip`"));
    }

    #[test]
    fn issue_url_quotes_like_quote_plus() {
        // urllib.parse.quote_plus("a b&c/d~e*") == "a+b%26c%2Fd~e%2A"
        assert_eq!(quote_plus("a b&c/d~e*"), "a+b%26c%2Fd~e%2A");
        assert_eq!(quote_plus("é"), "%C3%A9");
        let url = issue_url("/SaanDev/repo/", " ", "x\ny").unwrap();
        assert_eq!(url, "https://github.com/SaanDev/repo/issues/new?title=Bug+Report&body=x%0Ay");
        assert!(issue_url(" ", "t", "b").is_err());
    }

    #[test]
    fn bundle_holds_the_report_files() {
        let dir = std::env::temp_dir().join(format!("callisto_bug_{}", std::process::id()));
        let prov = Json::object();
        let out = write_bundle(&dir.join("report"), &ctx(), Some((&prov, "# P")), "notes").unwrap();
        assert!(out.to_string_lossy().ends_with("report.zip"));
        let mut z = zip::ZipArchive::new(std::fs::File::open(&out).unwrap()).unwrap();
        let names: Vec<String> = (0..z.len()).map(|i| z.by_index(i).unwrap().name().unwrap().into_owned()).collect();
        assert_eq!(names, ["bug_report.json", "bug_report.md", "issue_notes.md", "provenance.json", "provenance.md"]);
        let _ = std::fs::remove_dir_all(dir);
    }
}
