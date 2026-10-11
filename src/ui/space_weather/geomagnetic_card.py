"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The main window's "Geomagnetic Response" sidebar card
(src/ui/space_weather/geomagnetic_card.py).

A summary, never a pop-up: which event the response is for, the Dst minimum
and how long after the event it came, the Kp maximum and its G level, the
lowest Bz and the fastest wind, the data status, and buttons to refresh, open
the full window, or go back to the loaded data's event after a burst-list or
downloader selection overrode it.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout

from src.ui.space_weather.geomagnetic_controller import GeomagneticController

_DASH = "—"


def _fmt_time(value: Any) -> str:
    return f"{value:%m-%d %H:%M}" if value is not None else ""


def summary_lines(context: Any) -> dict[str, str]:
    """The card's value texts for a context (or dashes)."""
    out = {"dst": _DASH, "kp": _DASH, "bz": _DASH, "v": _DASH, "storm": _DASH}
    if context is None:
        return out
    s = context.summary
    if s.dst_min_nt is not None:
        delay = f", +{s.dst_min_delay_h:.0f} h" if s.dst_min_delay_h is not None and s.dst_min_delay_h >= 0 else ""
        out["dst"] = f"{s.dst_min_nt:.0f} nT at {_fmt_time(s.dst_min_time)}{delay}"
    if s.kp_max is not None:
        out["kp"] = f"{s.kp_max:.1f} ({s.g_scale}) at {_fmt_time(s.kp_max_time)}"
    if s.bz_min_nt is not None:
        out["bz"] = f"{s.bz_min_nt:.1f} nT at {_fmt_time(s.bz_min_time)}"
    if s.v_max_kms is not None:
        out["v"] = f"{s.v_max_kms:.0f} km/s at {_fmt_time(s.v_max_time)}"
    if s.storm_class:
        out["storm"] = s.storm_class.capitalize() + (" storm" if s.storm_class not in ("quiet",) else "")
    return out


class GeomagneticCard(QGroupBox):
    """Sidebar summary bound to a :class:`GeomagneticController`."""

    openWindowRequested = Signal()

    def __init__(self, controller: GeomagneticController, parent=None):
        super().__init__("Geomagnetic Response", parent)
        self.controller = controller
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        self.event_label = QLabel("Load data or select an event.")
        self.event_label.setWordWrap(True)
        self.event_label.setToolTip("The event the response is shown for: −1 d to +7 d around its start, by default.")
        layout.addWidget(self.event_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        self.value_labels: dict[str, QLabel] = {}
        for row, (key, title, tip) in enumerate(
            (
                ("dst", "Dst min", "Lowest Dst after the event, when, and hours after the event start."),
                ("kp", "Kp max", "Highest Kp after the event and its NOAA G level."),
                ("bz", "Bz min", "Most southward IMF Bz (GSM) after the event."),
                ("v", "V max", "Fastest solar wind after the event."),
                ("storm", "Storm", "Dst storm class: weak (≤ −30), moderate (≤ −50), intense (≤ −100), super (≤ −250 nT)."),
            )
        ):
            name = QLabel(title)
            name.setToolTip(tip)
            value = QLabel(_DASH)
            value.setWordWrap(True)
            value.setToolTip(tip)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self.value_labels[key] = value
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Fetch the response for this event again.")
        self.refresh_btn.clicked.connect(controller.refresh)
        self.open_btn = QPushButton("Open window")
        self.open_btn.setToolTip("Open the Geomagnetic Response window with every panel.")
        self.open_btn.clicked.connect(self.openWindowRequested.emit)
        buttons.addWidget(self.refresh_btn)
        buttons.addWidget(self.open_btn)
        layout.addLayout(buttons)

        self.use_loaded_btn = QPushButton("Use loaded data")
        self.use_loaded_btn.setToolTip("Go back to the event of the loaded FITS data.")
        self.use_loaded_btn.clicked.connect(controller.use_loaded_data)
        layout.addWidget(self.use_loaded_btn)

        controller.contextChanged.connect(self._on_context)
        controller.eventChanged.connect(self._on_event)
        controller.statusChanged.connect(self.status_label.setText)
        controller.busyChanged.connect(self._on_busy)
        self._on_event()
        self._on_context(controller.context)

    def refresh_state(self) -> None:
        """Re-apply what the card shows and hides.

        The sidebar section re-shows every control when it is opened, so the
        main window calls this afterwards (and once at start-up).
        """
        self._on_event()
        self._on_busy(self.controller.is_busy())

    def _on_event(self) -> None:
        label = self.controller.event_label()
        self.event_label.setText(label or "Load data or select an event.")
        self.use_loaded_btn.setVisible(self.controller.is_overridden())
        self.refresh_btn.setEnabled(self.controller.current_choice() is not None and not self.controller.is_busy())

    def _on_context(self, context: Any) -> None:
        for key, text in summary_lines(context).items():
            self.value_labels[key].setText(text)

    def _on_busy(self, busy: bool) -> None:
        self.refresh_btn.setEnabled(not busy and self.controller.current_choice() is not None)
        self.refresh_btn.setText("Loading…" if busy else "Refresh")
