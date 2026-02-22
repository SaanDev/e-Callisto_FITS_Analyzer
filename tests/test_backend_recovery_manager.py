"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import time

import numpy as np

from src.Backend.recovery_manager import (
    latest_snapshot_path,
    list_snapshots,
    prune_snapshots,
    save_recovery_snapshot,
    load_recovery_snapshot,
)


def test_recovery_snapshot_round_trip(tmp_path):
    meta = {"filename": "a.fit"}
    arrays = {"raw_data": np.arange(12, dtype=float).reshape(3, 4)}

    path = save_recovery_snapshot(
        meta=meta,
        arrays=arrays,
        source_project_path="/tmp/project.efaproj",
        reason="timer",
        max_snapshots=10,
        base_dir=tmp_path,
    )

    payload = load_recovery_snapshot(path)
    assert payload.meta["recovery_snapshot"] is True
    assert payload.meta["recovery_reason"] == "timer"
    assert payload.meta["recovery_source_project_path"] == "/tmp/project.efaproj"
    assert np.array_equal(payload.arrays["raw_data"], arrays["raw_data"])


def test_recovery_prune_keeps_latest(tmp_path):
    meta = {"filename": "x"}
    arrays = {"raw_data": np.ones((2, 2), dtype=float)}

    for _ in range(4):
        save_recovery_snapshot(
            meta=meta,
            arrays=arrays,
            source_project_path=None,
            reason="timer",
            max_snapshots=100,
            base_dir=tmp_path,
        )
        time.sleep(0.01)

    assert len(list_snapshots(tmp_path)) == 4
    removed = prune_snapshots(max_snapshots=2, base_dir=tmp_path)
    assert removed == 2
    assert len(list_snapshots(tmp_path)) == 2
    assert latest_snapshot_path(tmp_path) is not None
