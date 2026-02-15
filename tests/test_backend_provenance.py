from __future__ import annotations

from src.Backend.provenance import build_provenance_payload, payload_to_markdown, write_provenance_files


def test_provenance_payload_and_markdown(tmp_path):
    payload = build_provenance_payload(
        {
            "app": {"name": "e-CALLISTO FITS Analyzer", "version": "2.1"},
            "data_source": {"filename": "x.fit", "shape": [10, 20]},
            "processing": {"plot_type": "Raw", "use_db": False},
            "rfi": {"enabled": True},
            "annotations": [{"id": "1", "kind": "text", "points": [[1, 2]], "visible": True}],
            "time_sync": {"start_utc": "2026-01-01T00:00:00+00:00"},
            "operation_log": [{"ts": "2026-01-01T00:01:00+00:00", "msg": "loaded"}],
        }
    )

    md = payload_to_markdown(payload)
    assert "Provenance" in md
    assert "x.fit" in md
    assert "loaded" in md

    json_path, md_path = write_provenance_files(str(tmp_path / "report"), payload)
    assert (tmp_path / "report_provenance.json").exists()
    assert (tmp_path / "report_provenance.md").exists()
    assert json_path.endswith("_provenance.json")
    assert md_path.endswith("_provenance.md")
