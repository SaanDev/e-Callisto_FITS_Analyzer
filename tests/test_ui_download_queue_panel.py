"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Tests for the rich download progress panel (src/UI/download_queue_panel.py).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from src.Backend.download_manager import AggregateProgress, FileProgress, STATUS_DOWNLOADING, STATUS_DONE
from src.UI.download_queue_panel import DownloadProgressPanel


def _app():
    return QApplication.instance() or QApplication([])


def test_panel_aggregate_only_updates_stats_not_table():
    _app()
    panel = DownloadProgressPanel()
    agg = AggregateProgress(
        files_total=12,
        files_done=3,
        bytes_done=45 * 1024**2,
        bytes_total=150 * 1024**2,
        speed_bps=6 * 1024**2,
        eta_seconds=18.0,
    )
    panel.update_aggregate(agg, drive_bar=True)

    assert panel.bar.value() == 30  # 45/150
    text = panel.stats_label.text()
    assert "Frame 4 of 12" in text
    assert "MB" in text
    assert "/s" in panel.eta_label.text()
    assert "ETA" in panel.eta_label.text()
    # No per-file detail -> table stays hidden.
    assert panel.file_table.isHidden() is True
    assert panel.details_btn.isHidden() is True


def test_panel_host_owns_bar_when_drive_bar_false():
    _app()
    panel = DownloadProgressPanel()
    panel.bar.setValue(40)
    agg = AggregateProgress(files_total=2, files_done=0, bytes_done=10, bytes_total=100)
    panel.update_aggregate(agg, drive_bar=False)
    # Bar untouched (host drives it); only the stats line updates.
    assert panel.bar.value() == 40
    assert panel.stats_label.text() != ""


def test_panel_populates_per_file_table():
    _app()
    panel = DownloadProgressPanel()
    per_file = [
        FileProgress(name="aia_193_0001.fits", status=STATUS_DONE, bytes_done=4096, bytes_total=4096),
        FileProgress(name="aia_193_0002.fits", status=STATUS_DOWNLOADING, bytes_done=2048, bytes_total=8192,
                     speed_bps=1024),
    ]
    agg = AggregateProgress(
        files_total=2, files_done=1, bytes_done=6144, bytes_total=12288, per_file=per_file
    )
    panel.update_aggregate(agg, drive_bar=False)

    assert panel.details_btn.isHidden() is False
    assert panel.file_table.rowCount() == 2
    assert panel.file_table.item(0, 0).text() == "aia_193_0001.fits"
    assert panel.file_table.item(0, 3).text() == "Done"
    assert panel.file_table.item(1, 3).text() == "Downloading"
    # Toggling the button reveals the table.
    assert panel.file_table.isHidden() is True
    panel.details_btn.setChecked(True)
    assert panel.file_table.isHidden() is False


def test_panel_reset_clears_state():
    _app()
    panel = DownloadProgressPanel()
    per_file = [FileProgress(name="x.fits", status=STATUS_DONE, bytes_done=10, bytes_total=10)]
    panel.update_aggregate(AggregateProgress(files_total=1, files_done=1, per_file=per_file), drive_bar=False)
    panel.reset()
    assert panel.bar.value() == 0
    assert panel.stats_label.text() == ""
    assert panel.details_btn.isHidden() is True
    assert panel.file_table.rowCount() == 0


def test_panel_indeterminate_mode():
    _app()
    panel = DownloadProgressPanel()
    panel.set_indeterminate("Preparing download session...")
    assert panel.bar.maximum() == 0
    assert "Preparing" in panel.stats_label.text()
