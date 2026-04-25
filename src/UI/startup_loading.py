"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget


class StartupLoadingScreen(QWidget):
    def __init__(
        self,
        app_name: str,
        version: str,
        logo_path: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("StartupLoadingScreen")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(540, 280)

        self._status_text = "Starting application..."
        self._build_ui(app_name=app_name, version=version, logo_path=logo_path or "")

    def _build_ui(self, app_name: str, version: str, logo_path: str) -> None:
        self.setStyleSheet(
            """
            QWidget#StartupLoadingScreen {
                background-color: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #07131f,
                    stop: 0.55 #123049,
                    stop: 1 #21607a
                );
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 24px;
            }
            QLabel#StartupEyebrow {
                color: rgba(225, 241, 255, 0.72);
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#StartupTitle {
                color: #f5fbff;
                font-size: 30px;
                font-weight: 700;
            }
            QLabel#StartupVersion {
                color: #cde7f5;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#StartupSummary {
                color: rgba(233, 244, 251, 0.88);
                font-size: 13px;
            }
            QLabel#StartupStatus {
                color: #f5fbff;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#StartupLogo {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 20px;
                color: #f5fbff;
            }
            QProgressBar {
                background: rgba(255, 255, 255, 0.14);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 7px;
            }
            QProgressBar::chunk {
                background-color: #8ee4ff;
                border-radius: 7px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(18)

        self._logo_label = QLabel("")
        self._logo_label.setObjectName("StartupLogo")
        self._logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo_label.setFixedSize(108, 108)
        self._logo_label.setScaledContents(False)
        self._apply_logo(logo_path)
        header.addWidget(self._logo_label, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(6)

        eyebrow = QLabel("Solar Radio Burst Toolkit")
        eyebrow.setObjectName("StartupEyebrow")

        title = QLabel(app_name)
        title.setObjectName("StartupTitle")
        title.setWordWrap(True)

        version_label = QLabel(f"Version {version}")
        version_label.setObjectName("StartupVersion")

        summary = QLabel(
            "Preparing plotting tools, project recovery, and analysis controls for the main workspace."
        )
        summary.setObjectName("StartupSummary")
        summary.setWordWrap(True)

        text_col.addWidget(eyebrow)
        text_col.addWidget(title)
        text_col.addWidget(version_label)
        text_col.addSpacing(6)
        text_col.addWidget(summary)
        text_col.addStretch(1)
        header.addLayout(text_col, 1)

        root.addLayout(header)
        root.addStretch(1)

        self._status_label = QLabel(self._status_text)
        self._status_label.setObjectName("StartupStatus")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(14)
        root.addWidget(self._progress_bar)

        title_font = QFont(self.font())
        title_font.setPointSize(22)
        title_font.setBold(True)
        title.setFont(title_font)

        logo_font = QFont(self.font())
        logo_font.setPointSize(30)
        logo_font.setBold(True)
        self._logo_label.setFont(logo_font)

    def _apply_logo(self, logo_path: str) -> None:
        if logo_path and os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    78,
                    78,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._logo_label.setPixmap(scaled)
                return
        self._logo_label.setText("eC")

    def _center_on_primary_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self.move(
            geometry.center().x() - self.width() // 2,
            geometry.center().y() - self.height() // 2,
        )

    def present(self) -> None:
        self._center_on_primary_screen()
        self.show()
        self.raise_()
        QApplication.processEvents()

    def set_progress(self, value: int, text: str | None = None) -> None:
        clamped = max(0, min(100, int(value)))
        if text is not None:
            self._status_text = str(text)
            self._status_label.setText(self._status_text)
        self._progress_bar.setValue(clamped)
        QApplication.processEvents()

    def progress_value(self) -> int:
        return int(self._progress_bar.value())

    def status_text(self) -> str:
        return str(self._status_text)
