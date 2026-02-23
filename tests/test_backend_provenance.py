"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np

from src.Backend.provenance import build_provenance_payload, dump_json, payload_to_markdown, write_provenance_files


def test_provenance_payload_and_markdown(tmp_path):
    payload = build_provenance_payload(
        {
            "app": {"name": "e-CALLISTO FITS Analyzer", "version": "2.2-dev"},
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


def test_provenance_payload_serializes_numpy_values(tmp_path):
    payload = build_provenance_payload(
        {
            "app": {"name": "e-CALLISTO FITS Analyzer", "version": "2.2-dev"},
            "data_source": {"filename": "x.fit", "shape": np.array([10, 20], dtype=np.int32)},
            "processing": {"plot_type": "Raw", "use_db": np.bool_(False)},
            "max_intensity": {
                "time_channels": np.array([0.0, 1.0, 2.0], dtype=np.float32),
                "freqs": np.array([90.0, 80.0, 70.0], dtype=np.float64),
                "analyzer": {"fit_params": {"a": np.float32(12.5), "b": np.float64(-0.3)}},
            },
        }
    )

    json_text = dump_json(payload)
    assert "\"time_channels\": [" in json_text
    assert "\"shape\": [" in json_text

    json_path, md_path = write_provenance_files(str(tmp_path / "report_numpy"), payload)
    assert (tmp_path / "report_numpy_provenance.json").exists()
    assert (tmp_path / "report_numpy_provenance.md").exists()
    assert json_path.endswith("_provenance.json")
    assert md_path.endswith("_provenance.md")
