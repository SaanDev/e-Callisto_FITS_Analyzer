"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Rich download-progress widget for the SDO Data Analysis window.

Replaces the old single, timer-animated QProgressBar. Driven by
:class:`src.Backend.download_manager.AggregateProgress`, it shows the true
fraction transferred, a live stats line (MB done / MB total, MB/s, ETA, frame
count), and, when the active source reports per-file detail (JSOC / Helioviewer
direct URLs), an expandable table of individual files. For the VSO path, which
only yields an aggregate byte estimate, the per-file table stays hidden and the
bar plus stats line carry the honest numbers.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.Backend.download_manager import (
    STATUS_CACHED,
    STATUS_DONE,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    format_bytes,
    format_eta,
    format_speed,
)

_STATUS_LABELS = {
    "queued": "Queued",
    STATUS_DOWNLOADING: "Downloading",
    STATUS_DONE: "Done",
    STATUS_CACHED: "Cached",
    STATUS_FAILED: "Failed",
    "cancelled": "Cancelled",
    "paused": "Paused",
}


class DownloadProgressPanel(QWidget):
    """Aggregate progress bar + stats line + optional per-file table."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._has_per_file = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)

        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(2, 0, 2, 0)
        self.stats_label = QLabel("", self)
        self.stats_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.eta_label = QLabel("", self)
        self.eta_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        stats_row.addWidget(self.stats_label, 1)
        stats_row.addWidget(self.eta_label, 0)
        layout.addLayout(stats_row)

        self.details_btn = QPushButton("Show file details", self)
        self.details_btn.setCheckable(True)
        self.details_btn.setFlat(True)
        self.details_btn.setCursor(Qt.PointingHandCursor)
        self.details_btn.toggled.connect(self._on_details_toggled)
        self.details_btn.setVisible(False)
        layout.addWidget(self.details_btn, 0, Qt.AlignLeft)

        self.file_table = QTableWidget(0, 4, self)
        self.file_table.setHorizontalHeaderLabels(["File", "Progress", "Speed", "Status"])
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.file_table.setSelectionMode(QTableWidget.NoSelection)
        self.file_table.setFocusPolicy(Qt.NoFocus)
        self.file_table.setMaximumHeight(150)
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.file_table.setVisible(False)
        layout.addWidget(self.file_table)

    # -- public API --------------------------------------------------------
    def reset(self) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("%p%")
        self.stats_label.setText("")
        self.eta_label.setText("")
        self._has_per_file = False
        self.details_btn.setVisible(False)
        self.details_btn.setChecked(False)
        self.file_table.setVisible(False)
        self.file_table.setRowCount(0)

    def set_indeterminate(self, message: str = "") -> None:
        self.bar.setRange(0, 0)
        if message:
            self.stats_label.setText(message)

    def set_status_text(self, text: str) -> None:
        if text:
            self.stats_label.setText(str(text))

    def update_aggregate(self, agg: Any, *, drive_bar: bool = True) -> None:
        """Render an :class:`AggregateProgress` snapshot.

        When ``drive_bar`` is False the host owns the progress bar (because it
        spans more phases than the byte transfer alone) and only the stats line
        and per-file table are updated here.
        """
        if agg is None:
            return

        if drive_bar:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 100)
            percent = _safe_percent(agg)
            if percent is not None:
                self.bar.setValue(percent)

        self.stats_label.setText(_format_stats_line(agg))
        eta = getattr(agg, "eta_seconds", None)
        speed = getattr(agg, "speed_bps", 0.0)
        eta_text = ""
        if speed and speed > 0:
            eta_text = f"{format_speed(speed)}"
            if eta is not None:
                eta_text += f"  ·  ETA {format_eta(eta)}"
        self.eta_label.setText(eta_text)

        per_file = list(getattr(agg, "per_file", []) or [])
        if per_file:
            self._ensure_per_file_visible(len(per_file))
            self._populate_file_table(per_file)

    # -- internals ---------------------------------------------------------
    def _ensure_per_file_visible(self, count: int) -> None:
        if self._has_per_file:
            return
        self._has_per_file = True
        self.details_btn.setVisible(True)
        self.details_btn.setText(f"Show file details ({count})")

    def _on_details_toggled(self, checked: bool) -> None:
        self.file_table.setVisible(checked)
        self.details_btn.setText("Hide file details" if checked else "Show file details")

    def _populate_file_table(self, per_file: list[Any]) -> None:
        table = self.file_table
        if table.rowCount() != len(per_file):
            table.setRowCount(len(per_file))
        for row, fp in enumerate(per_file):
            name = getattr(fp, "name", "") or ""
            status = getattr(fp, "status", "") or ""
            done = getattr(fp, "bytes_done", 0) or 0
            total = getattr(fp, "bytes_total", None)
            speed = getattr(fp, "speed_bps", 0.0)

            if total:
                progress_text = f"{format_bytes(done)} / {format_bytes(total)}"
            else:
                progress_text = format_bytes(done)
            speed_text = format_speed(speed) if status == STATUS_DOWNLOADING else ""
            status_text = _STATUS_LABELS.get(status, status.title() if status else "")

            self._set_cell(table, row, 0, name)
            self._set_cell(table, row, 1, progress_text)
            self._set_cell(table, row, 2, speed_text)
            self._set_cell(table, row, 3, status_text)

    @staticmethod
    def _set_cell(table: QTableWidget, row: int, col: int, text: str) -> None:
        item = table.item(row, col)
        if item is None:
            item = QTableWidgetItem(text)
            if col != 0:
                item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, col, item)
        elif item.text() != text:
            item.setText(text)


def _safe_percent(agg: Any) -> int | None:
    try:
        return int(agg.percent())
    except Exception:
        frac = getattr(agg, "fraction", None)
        if frac is None:
            return None
        return int(round(float(frac) * 100))


def _format_stats_line(agg: Any) -> str:
    parts: list[str] = []
    files_total = int(getattr(agg, "files_total", 0) or 0)
    files_done = int(getattr(agg, "files_done", 0) or 0)
    if files_total > 0:
        parts.append(f"Frame {min(files_done + 1, files_total)} of {files_total}"
                     if files_done < files_total else f"{files_total} of {files_total} frames")

    bytes_done = int(getattr(agg, "bytes_done", 0) or 0)
    bytes_total = getattr(agg, "bytes_total", None)
    if bytes_done > 0 or bytes_total:
        if bytes_total:
            parts.append(f"{format_bytes(bytes_done)} / {format_bytes(bytes_total)}")
        else:
            parts.append(format_bytes(bytes_done))
    return "   ·   ".join(parts)
