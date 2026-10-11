"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import List

import requests
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QSplitter,
    QTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from src.ui.common.gui_shared import fit_window_to_screen
from src.ui.cme.cme_launcher import LaunchResult, launch_cme_helper
from src.ui.cme.cme_helper_client import CMEHelperClient, HelperOpenResult
from src.ui.common.url_opener import OpenResult, open_url_robust
from src.version import APP_VERSION

# The CDAW catalog itself (URLs, the table parser and the month fetch) lives in
# the backend so the event-context strip can use it too; the names stay
# importable from here for this window and its tests.
from src.backend.space_weather.event_catalogs import (  # noqa: E402
    BASE_URL,
    CDAW_ROOT,
    FETCH_MAX_ATTEMPTS,
    FETCH_TIMEOUT,
    STATUS_FETCH_ERROR,
    STATUS_MONTH_UNPUBLISHED,
    STATUS_NO_DATA,
    STATUS_OK,
    CMECatalogRow,
    FetchOutcome,
    build_fallback_movie_url,
    build_month_catalog_url,
    parse_catalog_rows,
    resolve_catalog_movie_url,
)
from src.backend.space_weather.event_catalogs import fetch_catalog_outcome as _fetch_catalog_outcome  # noqa: E402


@dataclass(frozen=True)
class MovieLaunchOutcome:
    opened: bool
    method: str
    message: str
    helper_result: object | None = None
    fallback_result: OpenResult | None = None


def _cme_logger() -> logging.Logger:
    logger = logging.getLogger("callisto.cme_viewer")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_root = os.path.expanduser("~/.local/share/e-callisto-fits-analyzer")
    try:
        from PySide6.QtCore import QStandardPaths

        app_data = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
        if app_data:
            log_root = app_data
    except Exception:
        pass

    try:
        log_dir = os.path.join(log_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(log_dir, "cme_viewer.log"),
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())

    return logger


def fetch_catalog_outcome(
    year: str,
    month: str,
    day: str,
    timeout: tuple[int, int] = FETCH_TIMEOUT,
    max_attempts: int = FETCH_MAX_ATTEMPTS,
) -> FetchOutcome:
    """One day of the CDAW catalog, logged to this window's rotating log file."""
    return _fetch_catalog_outcome(year, month, day, timeout, max_attempts, logger=_cme_logger())


def _use_legacy_helper_mode() -> bool:
    value = str(os.environ.get("CALLISTO_CME_HELPER_MODE", "") or "").strip().lower()
    if value in {"legacy", "oneoff", "one-off"}:
        return True
    legacy_flag = str(os.environ.get("CALLISTO_CME_HELPER_LEGACY", "") or "").strip().lower()
    return legacy_flag in {"1", "true", "yes", "on"}


def _describe_helper_error(helper_result: object | None) -> str:
    if helper_result is None:
        return "unknown"

    if isinstance(helper_result, HelperOpenResult):
        return helper_result.error or "unknown"

    if isinstance(helper_result, LaunchResult):
        return helper_result.error or "unknown"

    return "unknown"


def launch_movie_with_fallback(
    movie_url: str,
    movie_title: str,
    direct_movie_url: str = "",
    helper_client: CMEHelperClient | None = None,
) -> MovieLaunchOutcome:
    logger = _cme_logger()
    text_url = str(movie_url or "").strip()
    raw_url = str(direct_movie_url or "").strip()
    if not text_url:
        return MovieLaunchOutcome(
            opened=False,
            method="failed",
            message="Could not open CME movie because interactive URL is empty.",
        )

    helper_result: object | None = None
    helper_error = ""

    if helper_client is not None and not _use_legacy_helper_mode():
        ipc_result = helper_client.open_movie(
            text_url,
            raw_url=raw_url,
            title=str(movie_title or "").strip(),
        )
        helper_result = ipc_result
        if ipc_result.ok:
            method = "helper_ipc_restarted" if ipc_result.restart_attempted else "helper_ipc"
            logger.info(
                "event=movie_launch_success method=%s helper_pid=%s socket=%s restart=%s",
                method,
                helper_client.helper_pid(),
                helper_client.ipc_name,
                ipc_result.restart_attempted,
            )
            message = "Opened in persistent isolated viewer."
            if ipc_result.restart_attempted:
                message = "Opened in persistent isolated viewer after helper restart."
            return MovieLaunchOutcome(
                opened=True,
                method=method,
                message=message,
                helper_result=ipc_result,
            )

        helper_error = ipc_result.error or "Unknown helper IPC error."
        logger.error(
            "event=movie_launch_failed method=helper_ipc error=%s helper_pid=%s socket=%s restart_attempted=%s",
            helper_error,
            helper_client.helper_pid(),
            helper_client.ipc_name,
            ipc_result.restart_attempted,
        )
    else:
        helper_result = launch_cme_helper(
            text_url,
            movie_title,
            direct_movie_url=raw_url,
        )
        if helper_result.launched:
            logger.info(
                "event=movie_launch_success method=legacy_helper pid=%s command=%s",
                helper_result.pid,
                " ".join(shlex.quote(part) for part in helper_result.command),
            )
            return MovieLaunchOutcome(
                opened=True,
                method="legacy_helper",
                message=f"Opened in isolated viewer (PID {helper_result.pid}).",
                helper_result=helper_result,
            )

        helper_error = helper_result.error or "Unknown helper launch error."
        logger.error(
            "event=movie_launch_failed method=legacy_helper error=%s exit_code=%s command=%s",
            helper_result.error,
            helper_result.exit_code,
            " ".join(shlex.quote(part) for part in helper_result.command),
        )

    fallback_result = open_url_robust(text_url)
    if fallback_result.opened:
        logger.info(
            "event=movie_fallback_success method=%s helper_error=%s",
            fallback_result.method,
            helper_error,
        )
        return MovieLaunchOutcome(
            opened=True,
            method=f"browser:{fallback_result.method}",
            message=(
                "Helper could not open the movie. "
                f"Opened in browser using '{fallback_result.method}'."
            ),
            helper_result=helper_result,
            fallback_result=fallback_result,
        )

    logger.error(
        "event=movie_fallback_failed helper_error=%s fallback_error=%s",
        _describe_helper_error(helper_result),
        fallback_result.error,
    )
    return MovieLaunchOutcome(
        opened=False,
        method="failed",
        message=(
            "Could not open CME movie. "
            f"Helper error: {_describe_helper_error(helper_result)}. "
            f"Fallback error: {fallback_result.error or 'unknown'}."
        ),
        helper_result=helper_result,
        fallback_result=fallback_result,
    )


class FetchThread(QThread):
    progress = Signal(int)
    result = Signal(object)

    def __init__(self, year: str, month: str, day: str):
        super().__init__()
        self.year = str(year)
        self.month = str(month)
        self.day = str(day)

    def run(self) -> None:
        try:
            self.progress.emit(10)
            outcome = fetch_catalog_outcome(self.year, self.month, self.day)
            self.progress.emit(100)
            self.result.emit(outcome)
        except Exception as exc:
            self.result.emit(
                FetchOutcome(
                    status=STATUS_FETCH_ERROR,
                    rows=[],
                    message=f"Unexpected error while loading CME data: {exc}",
                    month_url=build_month_catalog_url(self.year, self.month),
                )
            )


class CMEViewer(QMainWindow):
    def __init__(self, parent=None, helper_client: CMEHelperClient | None = None):
        super().__init__(parent)

        self.setWindowTitle("SOHO/LASCO CME Catalog Tool")
        fit_window_to_screen(self, 1400, 900)

        self._logger = _cme_logger()
        self._rows: List[CMECatalogRow] = []
        self._selected_row: int | None = None
        self._pending_target_dt: datetime | None = None
        self.fetch_thread: FetchThread | None = None
        self._helper_client = helper_client

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        date_layout = QHBoxLayout()
        self.year_combo = QComboBox()
        self.month_combo = QComboBox()
        self.day_combo = QComboBox()

        for y in range(1996, datetime.now().year + 1):
            self.year_combo.addItem(str(y))
        self.year_combo.setCurrentText(str(datetime.now().year))

        for m in range(1, 13):
            self.month_combo.addItem(f"{m:02d}")
        self.month_combo.setCurrentText(f"{datetime.now().month:02d}")

        for d in range(1, 32):
            self.day_combo.addItem(f"{d:02d}")
        self.day_combo.setCurrentText(f"{datetime.now().day:02d}")

        date_layout.addWidget(QLabel("Year:"))
        date_layout.addWidget(self.year_combo)
        date_layout.addWidget(QLabel("Month:"))
        date_layout.addWidget(self.month_combo)
        date_layout.addWidget(QLabel("Day:"))
        date_layout.addWidget(self.day_combo)

        self.search_btn = QPushButton("Search")
        date_layout.addWidget(self.search_btn)
        layout.addLayout(date_layout)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        outer_splitter = QSplitter(Qt.Vertical)
        layout.addWidget(outer_splitter)

        top_splitter = QSplitter(Qt.Horizontal)
        outer_splitter.addWidget(top_splitter)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            [
                "Date and Time",
                "Central PA",
                "Angular Width",
                "Linear Speed",
                "Accel",
                "Mass",
                "Kinetic Energy",
                "MPA",
                "Remarks",
            ]
        )
        top_splitter.addWidget(self.table)

        self.details_text = QTextEdit()
        self.details_text.setReadOnly(True)
        top_splitter.addWidget(self.details_text)

        action_panel = QWidget()
        action_layout = QVBoxLayout(action_panel)

        self.movie_status = QTextEdit()
        self.movie_status.setReadOnly(True)
        self.movie_status.setMinimumHeight(120)
        action_layout.addWidget(self.movie_status)

        action_buttons = QHBoxLayout()
        self.play_btn = QPushButton("Play Movie")
        self.open_browser_btn = QPushButton("Open in Browser")
        self.copy_url_btn = QPushButton("Copy URL")
        action_buttons.addWidget(self.play_btn)
        action_buttons.addWidget(self.open_browser_btn)
        action_buttons.addWidget(self.copy_url_btn)
        action_layout.addLayout(action_buttons)

        outer_splitter.addWidget(action_panel)
        outer_splitter.setStretchFactor(0, 3)
        outer_splitter.setStretchFactor(1, 2)

        self.search_btn.clicked.connect(self.search_cmes)
        self.table.cellClicked.connect(self.show_cme_details)
        self.table.itemDoubleClicked.connect(self.play_cme_movie)
        self.play_btn.clicked.connect(self.play_cme_movie)
        self.open_browser_btn.clicked.connect(self.open_current_in_browser)
        self.copy_url_btn.clicked.connect(self.copy_current_url)

        self._set_action_buttons_enabled(False)
        self._set_movie_status("Select a CME entry and click Play Movie.")

    def set_target_datetime(
        self,
        target_dt: datetime,
        *,
        auto_search: bool = True,
        auto_select_nearest: bool = True,
    ) -> None:
        """Programmatically set the target CME date/time and optionally search/select."""
        try:
            year = f"{int(target_dt.year)}"
            month = f"{int(target_dt.month):02d}"
            day = f"{int(target_dt.day):02d}"
            self.year_combo.setCurrentText(year)
            self.month_combo.setCurrentText(month)
            self.day_combo.setCurrentText(day)
            self._pending_target_dt = target_dt if auto_select_nearest else None
            self._set_movie_status(
                f"Synced target CME time: {target_dt:%Y-%m-%d %H:%M:%S} UTC"
            )
            if auto_search:
                self.search_cmes()
        except Exception as e:
            self._set_movie_status(f"Failed to sync CME target time: {e}")

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        state = bool(enabled)
        self.play_btn.setEnabled(state)
        self.open_browser_btn.setEnabled(state)
        self.copy_url_btn.setEnabled(state)

    def _set_movie_status(self, message: str) -> None:
        self.movie_status.setPlainText(str(message or "").strip())

    def _current_row_obj(self) -> CMECatalogRow | None:
        if self._selected_row is None:
            return None
        if self._selected_row < 0 or self._selected_row >= len(self._rows):
            return None
        return self._rows[self._selected_row]

    def _movie_url_for_row(self, row_obj: CMECatalogRow | None) -> str:
        if row_obj is None:
            return ""
        return str(row_obj.interactive_movie_url or "").strip()

    def search_cmes(self) -> None:
        year = self.year_combo.currentText()
        month = self.month_combo.currentText()
        day = self.day_combo.currentText()

        self.progress.setValue(0)
        self.details_text.clear()
        self.table.setRowCount(0)
        self._rows = []
        self._selected_row = None
        self._set_action_buttons_enabled(False)
        self._set_movie_status("Loading CME catalog...")

        self.fetch_thread = FetchThread(year, month, day)
        self.fetch_thread.progress.connect(self.progress.setValue)
        self.fetch_thread.result.connect(self.populate_table)
        self.fetch_thread.start()

    def populate_table(self, outcome_obj: object) -> None:
        self.table.setRowCount(0)
        self._rows = []
        self._selected_row = None
        self._set_action_buttons_enabled(False)

        if not isinstance(outcome_obj, FetchOutcome):
            self.details_text.setText("Unexpected CME fetch result.")
            self._set_movie_status("Unexpected CME fetch result.")
            self._pending_target_dt = None
            return

        outcome = outcome_obj
        self._logger.info(
            "event=populate_table status=%s rows=%s month_url=%s",
            outcome.status,
            len(outcome.rows),
            outcome.month_url,
        )

        if outcome.status == STATUS_MONTH_UNPUBLISHED:
            message = outcome.message or "Catalog for selected month is not published yet."
            self.details_text.setText(message)
            self._set_movie_status(message)
            self._pending_target_dt = None
            return

        if outcome.status == STATUS_FETCH_ERROR:
            message = outcome.message or "Failed to load CME catalog."
            self.details_text.setText(message)
            self._set_movie_status(message)
            self._pending_target_dt = None
            return

        if outcome.status == STATUS_NO_DATA or not outcome.rows:
            message = outcome.message or "No CME data available for selected date."
            self.details_text.setText(message)
            self._set_movie_status(message)
            self._pending_target_dt = None
            return

        self._rows = list(outcome.rows)
        for row_obj in self._rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for index, value in enumerate(row_obj.values):
                self.table.setItem(row, index, QTableWidgetItem(value))

        self._set_movie_status(
            "CME data loaded. Select a row and click Play Movie to open isolated playback."
        )

        if self._pending_target_dt and self._rows:
            target_dt = self._pending_target_dt
            self._pending_target_dt = None
            try:
                nearest_idx = min(
                    range(len(self._rows)),
                    key=lambda i: abs((self._rows[i].timestamp - target_dt).total_seconds()),
                )
                self.table.selectRow(nearest_idx)
                self.show_cme_details(nearest_idx, 0)
                self._set_movie_status(
                    f"Synced CME selection to nearest event at {self._rows[nearest_idx].timestamp:%H:%M:%S} UTC."
                )
            except Exception:
                pass

    def show_cme_details(self, row: int, _: int) -> None:
        if row < 0 or row >= len(self._rows):
            self._selected_row = None
            self._set_action_buttons_enabled(False)
            return

        self._selected_row = row
        row_obj = self._rows[row]

        headers = [
            self.table.horizontalHeaderItem(index).text()
            for index in range(self.table.columnCount())
        ]
        details = [
            f"{headers[index]}: {row_obj.values[index]}"
            for index in range(self.table.columnCount())
        ]
        details.append("")
        details.append(f"Interactive Movie URL: {row_obj.interactive_movie_url}")
        details.append(f"Catalog Raw Movie URL: {row_obj.catalog_movie_url or 'N/A'}")
        details.append(f"Generated Interactive URL: {row_obj.fallback_movie_url}")
        self.details_text.setText("\n".join(details))

        self._set_action_buttons_enabled(bool(row_obj.interactive_movie_url))
        self._set_movie_status(
            "Ready to launch movie. Double-click row or use Play Movie."
        )

    def play_cme_movie(self, item=None) -> None:
        if item is not None and hasattr(item, "row"):
            try:
                self._selected_row = int(item.row())
            except Exception:
                pass

        row_obj = self._current_row_obj()
        movie_url = self._movie_url_for_row(row_obj)
        if not movie_url:
            self._set_movie_status("No movie URL is available for the selected CME row.")
            self._set_action_buttons_enabled(False)
            return

        title = f"CME Viewer - {row_obj.values[0] if row_obj else ''}".strip()
        self._logger.info(
            "event=play_requested url=%s selected_row=%s",
            movie_url,
            self._selected_row,
        )

        outcome = launch_movie_with_fallback(
            movie_url,
            title,
            direct_movie_url=(row_obj.catalog_movie_url if row_obj else ""),
            helper_client=self._helper_client,
        )
        self._set_movie_status(outcome.message)

    def open_current_in_browser(self) -> None:
        row_obj = self._current_row_obj()
        movie_url = self._movie_url_for_row(row_obj)
        if not movie_url:
            self._set_movie_status("No movie URL is available for the selected CME row.")
            return

        result = open_url_robust(movie_url)
        if result.opened:
            self._logger.info("event=open_in_browser_success method=%s url=%s", result.method, movie_url)
            self._set_movie_status(f"Opened movie using method '{result.method}'.")
            return

        self._logger.error("event=open_in_browser_failed error=%s url=%s", result.error, movie_url)
        self._set_movie_status(f"Could not open movie URL: {result.error}")

    def copy_current_url(self) -> None:
        row_obj = self._current_row_obj()
        movie_url = self._movie_url_for_row(row_obj)
        if not movie_url:
            self._set_movie_status("No movie URL is available for the selected CME row.")
            return

        QApplication.clipboard().setText(movie_url)
        self._logger.info("event=copy_url url=%s", movie_url)
        self._set_movie_status("Movie URL copied to clipboard.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = CMEViewer()
    win.show()
    sys.exit(app.exec())
