"""
Bug report dialog.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from src.Backend.bug_report import (
    build_bug_report_payload,
    build_github_issue_url,
    default_bug_report_filename,
    write_bug_report_bundle,
)
from src.UI.utils.url_opener import open_url_robust


class BugReportDialog(QDialog):
    def __init__(
        self,
        *,
        repo: str,
        context_provider: Callable[[], dict[str, Any]] | None = None,
        provenance_provider: Callable[[], dict[str, Any]] | None = None,
        default_dir_provider: Callable[[], str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Report a Bug")
        self.resize(760, 460)

        self._repo = str(repo or "").strip()
        self._context_provider = context_provider
        self._provenance_provider = provenance_provider
        self._default_dir_provider = default_dir_provider
        self._bundle_path = ""

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Bug Title"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Short summary (e.g., Crash when exporting PNG)")
        title_row.addWidget(self.title_edit, 1)
        root.addLayout(title_row)

        root.addWidget(QLabel("What happened / Steps to reproduce / Expected behavior"))
        self.details_edit = QPlainTextEdit()
        self.details_edit.setPlaceholderText(
            "1. What you did\n2. What happened\n3. What you expected\n4. Any extra notes"
        )
        root.addWidget(self.details_edit, 1)

        self.diag_label = QLabel("Diagnostics bundle: not generated yet.")
        self.diag_label.setWordWrap(True)
        root.addWidget(self.diag_label)

        buttons = QHBoxLayout()
        self.generate_btn = QPushButton("Generate Diagnostics...")
        self.copy_btn = QPushButton("Copy Issue Text")
        self.open_btn = QPushButton("Open GitHub Issue")
        self.close_btn = QPushButton("Close")

        self.generate_btn.clicked.connect(self.generate_diagnostics_bundle)
        self.copy_btn.clicked.connect(self.copy_issue_text)
        self.open_btn.clicked.connect(self.open_github_issue)
        self.close_btn.clicked.connect(self.close)

        buttons.addWidget(self.generate_btn)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(self.open_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)
        root.addLayout(buttons)

    def _default_output_dir(self) -> str:
        provider = self._default_dir_provider
        if callable(provider):
            try:
                p = str(provider() or "").strip()
                if p:
                    return p
            except Exception:
                pass
        return os.path.expanduser("~")

    def _build_context(self) -> dict[str, Any]:
        provider = self._context_provider
        if callable(provider):
            try:
                out = provider()
                if isinstance(out, Mapping):
                    return dict(out)
            except Exception:
                pass
        return {}

    def _build_provenance(self) -> dict[str, Any] | None:
        provider = self._provenance_provider
        if callable(provider):
            try:
                out = provider()
                if isinstance(out, Mapping):
                    return dict(out)
            except Exception:
                pass
        return None

    def _issue_title(self) -> str:
        text = self.title_edit.text().strip()
        return text or "Bug Report"

    def _issue_body(self) -> str:
        details = self.details_edit.toPlainText().strip()
        ctx = self._build_context()
        summary = dict(ctx.get("summary") or {})
        environment = dict(ctx.get("environment") or {})
        session = dict(ctx.get("session") or {})

        lines = [
            "## Description",
            details or "(Please describe the issue.)",
            "",
            "## Environment",
        ]

        if environment:
            for key, value in environment.items():
                lines.append(f"- {key}: {value}")
        else:
            lines.append("- (not available)")

        lines.append("")
        lines.append("## Current Session")
        if summary:
            for key, value in summary.items():
                lines.append(f"- {key}: {value}")
        else:
            lines.append("- (not available)")

        if session:
            for key, value in session.items():
                lines.append(f"- {key}: {value}")

        lines.append("")
        lines.append("## Diagnostics")
        if self._bundle_path:
            lines.append(f"- Generated bundle: `{self._bundle_path}`")
            lines.append("- Please attach this `.zip` file to the GitHub issue.")
        else:
            lines.append("- No diagnostics bundle generated yet.")
            lines.append("- Use `Generate Diagnostics...` and attach the created `.zip` file.")
        lines.append("")

        return "\n".join(lines)

    def _update_diag_label(self):
        if self._bundle_path:
            self.diag_label.setText(f"Diagnostics bundle: {self._bundle_path}")
        else:
            self.diag_label.setText("Diagnostics bundle: not generated yet.")

    def generate_diagnostics_bundle(self):
        default_name = default_bug_report_filename()
        start = os.path.join(self._default_output_dir(), default_name)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Diagnostics Bundle",
            start,
            "Zip Archive (*.zip)",
        )
        if not path:
            return

        context = self._build_context()
        payload = build_bug_report_payload(context)
        provenance_payload = self._build_provenance()
        notes = self._issue_body()

        try:
            out = write_bug_report_bundle(path, payload, provenance_payload=provenance_payload, notes_md=notes)
        except Exception as e:
            QMessageBox.critical(self, "Diagnostics Failed", f"Could not generate diagnostics bundle:\n{e}")
            return

        self._bundle_path = out
        self._update_diag_label()
        QMessageBox.information(self, "Diagnostics Ready", f"Diagnostics bundle created:\n{out}")

    def copy_issue_text(self):
        text = self._issue_body()
        cb = QGuiApplication.clipboard()
        if cb is None:
            QMessageBox.warning(self, "Clipboard", "Clipboard is not available on this system.")
            return
        cb.setText(text)
        QMessageBox.information(self, "Copied", "Issue text copied to clipboard.")

    def open_github_issue(self):
        title = self._issue_title()
        body = self._issue_body()
        try:
            url = build_github_issue_url(self._repo, title, body)
        except Exception as e:
            QMessageBox.critical(self, "Report a Bug", f"Could not build issue URL:\n{e}")
            return

        result = open_url_robust(url)
        if result.opened:
            return

        QMessageBox.warning(
            self,
            "Open URL Failed",
            f"Could not open GitHub issue page automatically.\n\nURL:\n{url}\n\n{result.error}",
        )
