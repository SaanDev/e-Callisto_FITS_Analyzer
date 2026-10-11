"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The Geomagnetic Response window (src/ui/space_weather/geomagnetic_response_window.py).

Stacked panels on one shared UTC axis — Dst with SYM-H, Kp coloured by G
level, the IMF, the plasma, Ey/β/Mach and AE/AL/AU — from one day before the
event to seven days after (the span is editable). The event start is a dashed
line, the radio data's interval is shaded and, when the event context is
loaded, DONKI shock arrivals and storms are marked.

The window fetches nothing itself: it shows what the main window's
:class:`GeomagneticController` holds, so the sidebar card and this window
never disagree. The panel checklist is remembered between sessions. Figures
are exported in the OriginPro style (PNG, PDF, SVG, EPS, …); the CSV carries a
provenance column saying which archive or spacecraft each value came from.
"""

from __future__ import annotations

import csv
import json
from datetime import timedelta
from typing import Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.backend.common.figure_export import FIGURE_EXPORT_FILTERS, save_figure
from src.backend.space_weather.geomagnetic_context import context_csv_rows
from src.backend.space_weather.geomagnetic_figures import (
    DEFAULT_GEO_PANELS,
    GEO_PANELS,
    draw_geomagnetic_panels,
    geomagnetic_figure,
    normalize_panels,
    total_rows,
)
from src.ui.space_weather.geomagnetic_controller import GeomagneticController
from src.version import APP_NAME, APP_ORG

PANELS_SETTINGS_KEY = "geomagnetic/panels"
ROW_HEIGHT_PX = 150


def _make_settings() -> QSettings:
    """Persistent settings (a module-level factory so tests can redirect it)."""
    return QSettings(APP_ORG, APP_NAME)


def _get_theme():
    app = QApplication.instance()
    return app.property("theme_manager") if app else None


def load_panel_choice() -> list[str]:
    try:
        raw = _make_settings().value(PANELS_SETTINGS_KEY, "")
        panels = json.loads(str(raw)) if raw else list(DEFAULT_GEO_PANELS)
    except (TypeError, ValueError):
        panels = list(DEFAULT_GEO_PANELS)
    return normalize_panels(panels) or list(DEFAULT_GEO_PANELS)


def save_panel_choice(panels: list[str]) -> None:
    try:
        settings = _make_settings()
        settings.setValue(PANELS_SETTINGS_KEY, json.dumps(list(panels)))
        settings.sync()
    except Exception:
        pass


class GeomagneticResponseWindow(QMainWindow):
    """Every panel of the response, bound to the main window's controller."""

    def __init__(self, controller: GeomagneticController, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Geomagnetic Response")
        self.controller = controller
        self.panels = load_panel_choice()
        self._build_ui()
        controller.contextChanged.connect(lambda _c: self.render())
        controller.eventChanged.connect(self._sync_event)
        controller.statusChanged.connect(self.statusBar().showMessage)
        self._sync_event()
        self.render()
        try:
            from src.ui.common.gui_shared import fit_window_to_screen

            fit_window_to_screen(self, 1100, 900)
        except Exception:
            self.resize(1100, 900)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        top = QHBoxLayout()
        self.event_label = QLabel("")
        self.event_label.setWordWrap(True)
        top.addWidget(self.event_label, 1)
        top.addWidget(QLabel("Before"))
        self.before_spin = QDoubleSpinBox()
        self.before_spin.setRange(0.25, 10.0)
        self.before_spin.setSingleStep(0.25)
        self.before_spin.setDecimals(2)
        self.before_spin.setSuffix(" d")
        self.before_spin.setToolTip("Days shown before the event start.")
        top.addWidget(self.before_spin)
        top.addWidget(QLabel("After"))
        self.after_spin = QDoubleSpinBox()
        self.after_spin.setRange(0.5, 30.0)
        self.after_spin.setSingleStep(0.5)
        self.after_spin.setDecimals(1)
        self.after_spin.setSuffix(" d")
        self.after_spin.setToolTip("Days shown after the event start.")
        top.addWidget(self.after_spin)
        self._span_timer = QTimer(self)
        self._span_timer.setSingleShot(True)
        self._span_timer.setInterval(600)
        self._span_timer.timeout.connect(self._apply_span)
        self.before_spin.valueChanged.connect(lambda _v: self._span_timer.start())
        self.after_spin.valueChanged.connect(lambda _v: self._span_timer.start())
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.controller.refresh)
        top.addWidget(self.refresh_btn)
        root.addLayout(top)

        checks = QHBoxLayout()
        checks.addWidget(QLabel("Panels:"))
        self.panel_checks: dict[str, QCheckBox] = {}
        for key, label in GEO_PANELS:
            check = QCheckBox(label)
            check.setChecked(key in self.panels)
            check.toggled.connect(self._on_panels_toggled)
            checks.addWidget(check)
            self.panel_checks[key] = check
        checks.addStretch(1)
        self.export_btn = QPushButton("Export Figure…")
        self.export_btn.setToolTip("Save the selected panels as an OriginPro-style figure (PNG, PDF, SVG, EPS, …).")
        self.export_btn.clicked.connect(self.export_figure)
        self.csv_btn = QPushButton("Export CSV…")
        self.csv_btn.setToolTip("Save every series as CSV, with where each value came from.")
        self.csv_btn.clicked.connect(self.export_csv)
        checks.addWidget(self.export_btn)
        checks.addWidget(self.csv_btn)
        root.addLayout(checks)

        self.figure = Figure(figsize=(9, 6), layout="constrained")
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.canvas)
        root.addWidget(self.scroll, 1)

        self.notes_label = QLabel("")
        self.notes_label.setWordWrap(True)
        root.addWidget(self.notes_label)
        self.setCentralWidget(central)

    def _sync_event(self) -> None:
        self.event_label.setText(self.controller.event_label() or "No event selected.")
        for spin, value in ((self.before_spin, self.controller.before), (self.after_spin, self.controller.after)):
            was = spin.blockSignals(True)
            spin.setValue(value.total_seconds() / 86400.0)
            spin.blockSignals(was)

    def _apply_span(self) -> None:
        self.controller.set_span(
            timedelta(days=float(self.before_spin.value())), timedelta(days=float(self.after_spin.value()))
        )

    def _on_panels_toggled(self, _checked: bool) -> None:
        self.panels = [key for key, check in self.panel_checks.items() if check.isChecked()]
        save_panel_choice(self.panels)
        self.render()

    # -------------------------------------------------------------- render
    def render(self) -> None:
        context = self.controller.context
        self.figure.clear()
        rows = total_rows(self.panels)
        self.canvas.setMinimumHeight(max(300, ROW_HEIGHT_PX * max(rows, 1)))
        if context is None or rows == 0:
            ax = self.figure.add_subplot(111)
            ax.set_axis_off()
            ax.text(
                0.5, 0.5,
                "No geomagnetic response loaded yet." if context is None else "Choose at least one panel.",
                ha="center", va="center", transform=ax.transAxes,
            )
            self._apply_theme()
            self.canvas.draw_idle()
            self.notes_label.setText("")
            return
        axes = self.figure.subplots(rows, 1, sharex=True, squeeze=False)[:, 0]
        draw_geomagnetic_panels(
            axes, context, self.panels,
            radio_interval=self.controller.radio_interval,
            markers=self.controller.all_markers(),
        )
        self._apply_theme()
        self.canvas.draw_idle()
        self.notes_label.setText("  ·  ".join(context.notes))

    def _apply_theme(self) -> None:
        theme = _get_theme()
        if theme is None or not hasattr(theme, "apply_mpl"):
            return
        for ax in self.figure.axes:
            try:
                theme.apply_mpl(self.figure, ax)
            except Exception:
                pass

    def drawn_axes(self) -> list[Any]:
        return list(self.figure.axes)

    # -------------------------------------------------------------- export
    def _default_stem(self) -> str:
        context = self.controller.context
        if context is None:
            return "geomagnetic_response"
        return f"geomagnetic_response_{context.event_start:%Y%m%d_%H%M}"

    def export_figure(self) -> None:
        context = self.controller.context
        if context is None:
            QMessageBox.information(self, "Export Figure", "Load the geomagnetic response first.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export Geomagnetic Response", f"{self._default_stem()}.png", FIGURE_EXPORT_FILTERS
        )
        if not path:
            return
        try:
            figure = geomagnetic_figure(
                context, self.panels,
                radio_interval=self.controller.radio_interval,
                markers=self.controller.all_markers(),
            )
            save_figure(figure, path, tight=True)
        except Exception as exc:
            QMessageBox.critical(self, "Export Figure", f"Could not save the figure:\n{exc}")
            return
        self.statusBar().showMessage(f"Saved {path}", 5000)

    def export_csv(self) -> None:
        context = self.controller.context
        if context is None:
            QMessageBox.information(self, "Export CSV", "Load the geomagnetic response first.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export Geomagnetic Response CSV", f"{self._default_stem()}.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(context_csv_rows(context))
        except OSError as exc:
            QMessageBox.critical(self, "Export CSV", f"Could not save the CSV:\n{exc}")
            return
        self.statusBar().showMessage(f"Saved {path}", 5000)
