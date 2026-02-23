"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from src.Backend.bug_report import (
    build_bug_report_payload,
    build_github_issue_url,
    write_bug_report_bundle,
)


def test_build_github_issue_url_encodes_title_and_body():
    url = build_github_issue_url(
        "SaanDev/e-Callisto_FITS_Analyzer",
        "Crash: export fails",
        "Line 1\nLine 2",
    )
    assert "github.com/SaanDev/e-Callisto_FITS_Analyzer/issues/new" in url
    assert "Crash%3A+export+fails" in url
    assert "Line+1%0ALine+2" in url


def test_build_bug_report_payload_excludes_raw_arrays():
    payload = build_bug_report_payload(
        {
            "summary": {"filename": "demo.fit"},
            "raw_data": [[1, 2], [3, 4]],
            "nested": {"noise_reduced_data": [1, 2, 3], "ok": True},
        }
    )
    report = payload["report"]
    assert "raw_data" not in report
    assert "noise_reduced_data" not in dict(report.get("nested") or {})
    assert dict(report.get("summary") or {}).get("filename") == "demo.fit"


def test_write_bug_report_bundle_contains_expected_files(tmp_path: Path):
    target = tmp_path / "report_bundle.zip"
    out = write_bug_report_bundle(
        str(target),
        payload=build_bug_report_payload({"summary": {"filename": "demo.fit"}}),
        provenance_payload={"generated_at": "2026-01-01T00:00:00", "app": {"name": "x"}},
        notes_md="# Notes\nhello",
    )
    assert out.endswith(".zip")
    assert Path(out).exists()

    with zipfile.ZipFile(out, "r") as zf:
        names = set(zf.namelist())
        assert "bug_report.json" in names
        assert "bug_report.md" in names
        assert "issue_notes.md" in names
        assert "provenance.json" in names
        assert "provenance.md" in names

        raw = zf.read("bug_report.json").decode("utf-8")
        data = json.loads(raw)
        assert "report" in data


def test_write_bug_report_bundle_tolerates_non_mapping_sections(tmp_path: Path):
    target = tmp_path / "report_bundle_non_mapping.zip"
    payload = {
        "generated_at": "2026-01-01T00:00:00",
        "report": {
            "summary": ["unexpected", "list"],
            "environment": "test-env",
            "session": 123,
        },
    }
    out = write_bug_report_bundle(str(target), payload=payload, provenance_payload=None, notes_md="")
    assert Path(out).exists()
    with zipfile.ZipFile(out, "r") as zf:
        assert "bug_report.md" in set(zf.namelist())
