"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The event-figure composer (src/ui/app/event_figure_composer.py), opened from
File → Export As → Event Figure….

It assembles one publication figure of the whole event from what the
application already holds — the radio spectrum, SWAVES, the GOES overlay, the
event catalogs, the geomagnetic response and images loaded in the Solar Image
Analysis or GCS windows — and fetches what is missing on request (GOES proton
flux, GOES X-rays, Helioviewer thumbnails), on a worker thread. The drawing
itself is :func:`src.backend.common.event_figure.build_event_figure`.

The panels can be switched on and off, reordered and given height ratios; the
thumbnails are chosen by time and source; the page follows a journal column
width. The preview redraws shortly after each change. The composer's settings
are the project's ``composer`` part.
"""

from __future__ import annotations

import io
import threading
import weakref
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from PySide6.QtCore import QDateTime, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.backend.common.event_figure import (
    CUSTOM_SIZE,
    PANEL_LABELS,
    SIZE_PRESETS,
    EventFigureData,
    EventFigureSpec,
    GeoBlockSpec,
    PanelSpec,
    SepSeries,
    ThumbnailImage,
    ThumbnailSpec,
    auto_thumbnail_times,
    build_event_figure,
)
from src.backend.common.figure_export import FIGURE_EXPORT_FILTERS, save_figure
from src.backend.solar.helioviewer import THUMBNAIL_SOURCES, thumbnail_source
from src.backend.space_weather.geomagnetic_figures import GEO_PANELS

PREVIEW_DEBOUNCE_MS = 400
#: Short checkbox labels for the geomagnetic panel groups.
GEO_SHORT_LABELS = {"dst": "Dst", "kp": "Kp", "imf": "IMF", "plasma": "Plasma", "derived": "Ey/β/Mach", "indices": "AE"}
#: A loaded frame this close to a requested thumbnail time is used instead of Helioviewer.
LOADED_TOLERANCE_S = 1800.0

_LIVE: set[tuple[Any, Any]] = set()


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _qdt(value: datetime) -> QDateTime:
    value = _naive_utc(value)
    return QDateTime(value.year, value.month, value.day, value.hour, value.minute, value.second, 0, Qt.UTC)


def _py(qdt: QDateTime) -> datetime:
    """A UTC editor's value as naive UTC."""
    return _naive_utc(qdt.toPython())


def thumb_key(source: str, when: datetime) -> str:
    return f"{source}|{when:%Y%m%dT%H%M}"


# --------------------------------------------------------------------- state


class ComposerState(QObject):
    """The composer's settings and fetched data; the project's ``composer`` part."""

    modified = Signal()
    restored = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.spec = EventFigureSpec()
        #: True once the user has opened and changed the composer (or a project set it).
        self.touched = False
        self.sep: SepSeries | None = None
        self.goes: list[tuple[str, Any, Any]] = []
        self.thumbs: dict[str, ThumbnailImage] = {}

    def set_spec(self, spec: EventFigureSpec) -> None:
        self.spec = spec
        self.touched = True
        self.modified.emit()

    def project_has_content(self) -> bool:
        return bool(self.touched)

    def project_capture(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return {"spec": self.spec.to_json()}, {}

    def project_restore(self, meta: dict[str, Any], arrays: dict[str, Any]) -> None:
        self.spec = EventFigureSpec.from_json(meta.get("spec"))
        self.touched = True
        self.restored.emit()

    def project_clear(self) -> None:
        self.spec = EventFigureSpec()
        self.touched = False
        self.sep = None
        self.goes = []
        self.thumbs = {}
        self.restored.emit()


# --------------------------------------------------------------------- data


def _alive(widget: Any) -> Any:
    if widget is None:
        return None
    try:
        widget.windowTitle()
    except (RuntimeError, AttributeError):
        return None
    return widget


def loaded_thumbnail(main: Any, when: datetime) -> ThumbnailImage | None:
    """A frame already loaded in the Solar Image Analysis or GCS window, if one is near."""
    for name in ("_solar_data_analysis_window", "_gcs_window"):
        window = _alive(getattr(main, name, None))
        if window is None or not hasattr(window, "thumbnail_near"):
            continue
        try:
            thumb = window.thumbnail_near(when, tolerance_s=LOADED_TOLERANCE_S)
        except Exception:
            thumb = None
        if thumb is not None:
            return thumb
    return None


def radio_x_range(main: Any) -> tuple[float, float] | None:
    getter = getattr(main, "_event_strip_x_range", None)
    try:
        return getter() if callable(getter) else None
    except Exception:
        return None


def collect_event_figure_data(main: Any, spec: EventFigureSpec, state: ComposerState) -> EventFigureData:
    """Everything the main window (and the fetched extras) can contribute to ``spec``."""
    from src.ui.app.event_context_strip import build_event_markers

    data = EventFigureData()
    try:
        base = main._swaves_base_utc()
    except Exception:
        base = None
    data.base_utc = _naive_utc(base)
    data.x_range = radio_x_range(main)

    if getattr(main, "raw_data", None) is not None:
        try:
            graph = main._origin_spectrum_graph(
                main._current_dynamic_spectrum_source_data(),
                plot_type=main.current_plot_type,
                view=None,
                levels=main._current_dynamic_spectrum_levels(),
                include_goes=False,
                include_swaves=False,
            )
            data.spectrum = replace(graph, title="", view=None, events=None) if graph is not None else None
        except Exception:
            data.spectrum = None
    try:
        if main._swaves_active():
            data.swaves = main._origin_swaves_panel()
    except Exception:
        data.swaves = None

    goes = []
    try:
        payload = getattr(main, "_goes_overlay_payload", None)
        for key, item in main._goes_overlay_visible_series(payload, selected_channels=("xrsa", "xrsb")) or []:
            goes.append((str(main._goes_payload_field(item, "display_label", key)),
                         main._goes_payload_field(item, "x_seconds", []),
                         main._goes_payload_field(item, "flux_wm2", [])))
    except Exception:
        goes = []
    data.goes = goes or list(state.goes)
    data.sep = state.sep

    start, end = spec.start, spec.end
    if (start is None or end is None) and data.base_utc is not None and data.x_range is not None:
        start = data.base_utc + timedelta(seconds=data.x_range[0])
        end = data.base_utc + timedelta(seconds=data.x_range[1])

    controller = getattr(main, "_event_context", None)
    if controller is not None and controller.result is not None and data.base_utc is not None and start and end:
        x0 = (start - data.base_utc).total_seconds()
        x1 = (end - data.base_utc).total_seconds()
        markers = build_event_markers(controller.result, controller.sources, data.base_utc, x0, x1)
        data.events = markers if markers.lanes else None

    geomagnetic = getattr(main, "_geomagnetic", None)
    if geomagnetic is not None and geomagnetic.context is not None:
        data.geomagnetic = geomagnetic.context
        data.geo_markers = geomagnetic.all_markers()
        data.radio_interval = geomagnetic.radio_interval

    thumbs = []
    for request in spec.thumbnails:
        image = loaded_thumbnail(main, request.time) or state.thumbs.get(thumb_key(request.source, request.time))
        if image is not None:
            thumbs.append(image)
    data.thumbnails = thumbs
    return data


def missing_thumbnails(main: Any, spec: EventFigureSpec, state: ComposerState) -> list[ThumbnailSpec]:
    """Requested thumbnails with neither a loaded frame nor a fetched image."""
    out = []
    for request in spec.thumbnails:
        if loaded_thumbnail(main, request.time) is None and thumb_key(request.source, request.time) not in state.thumbs:
            out.append(request)
    return out


# ------------------------------------------------------------------- fetching


class ComposerFetchWorker(QObject):
    """Fetches GOES proton and X-ray flux and Helioviewer thumbnails off the GUI thread."""

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, *, base_utc: datetime | None, start: datetime | None, end: datetime | None,
                 need_sep: bool, need_goes: bool, thumbnails: list[ThumbnailSpec], cache_dir: Path):
        super().__init__()
        self.base_utc, self.start, self.end = base_utc, start, end
        self.need_sep, self.need_goes = need_sep, need_goes
        self.thumbnails = list(thumbnails)
        self.cache_dir = Path(cache_dir)
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        out: dict[str, Any] = {"thumbs": {}, "errors": []}
        try:
            if self.need_sep and self.start and self.end:
                self.progress.emit("Fetching GOES proton flux…")
                try:
                    from src.backend.space_weather.sep_proton import load_sep_proton_range

                    sep = load_sep_proton_range(self.start, self.end)
                    out["sep"] = SepSeries(
                        tuple(_naive_utc(t) for t in sep.times), sep.low_flux, sep.high_flux,
                        sep.low_channel_label, sep.high_channel_label, sep.units or "pfu", sep.spacecraft,
                    )
                except Exception as exc:
                    out["errors"].append(f"GOES protons: {exc}".splitlines()[0])
            if self.need_goes and self.start and self.end and self.base_utc is not None and not self._cancel.is_set():
                self.progress.emit("Fetching GOES X-ray flux…")
                try:
                    from src.backend.space_weather.goes_overlay import fetch_goes_overlay

                    utc = timezone.utc
                    payload = fetch_goes_overlay(
                        start_utc=self.start.replace(tzinfo=utc), end_utc=self.end.replace(tzinfo=utc),
                        base_utc=self.base_utc.replace(tzinfo=utc), cache_dir=self.cache_dir / "goes",
                        cancel_cb=self._cancel.is_set,
                    )
                    out["goes"] = [(s.display_label, s.x_seconds, s.flux_wm2) for s in payload.series.values()]
                except Exception as exc:
                    out["errors"].append(f"GOES X-rays: {exc}".splitlines()[0])
            for request in self.thumbnails:
                if self._cancel.is_set():
                    break
                pair = thumbnail_source(request.source)
                if pair is None:
                    continue
                self.progress.emit(f"Fetching {request.source} near {request.time:%H:%M}…")
                try:
                    from matplotlib import image as mpimg

                    from src.backend.solar.helioviewer import fetch_preview_cached

                    preview = fetch_preview_cached(pair[0], pair[1], request.time, cache_dir=self.cache_dir, size_px=512)
                    # Helioviewer's PNG is already scaled for display: used as it is.
                    pixels = mpimg.imread(io.BytesIO(preview.png_bytes), format="png")
                    out["thumbs"][thumb_key(request.source, request.time)] = ThumbnailImage(
                        preview.date, request.source, pixels, "helioviewer"
                    )
                except Exception as exc:
                    out["errors"].append(f"{request.source}: {exc}".splitlines()[0])
        except Exception as exc:  # reported, never raised into Qt's event loop
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        self.finished.emit(out)


# --------------------------------------------------------------------- window


class EventFigureComposer(QMainWindow):
    """Build, preview and export the event figure."""

    def __init__(self, main: Any, state: ComposerState, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Event Figure Composer")
        # Weak: the main window keeps this window and its state; a strong
        # reference back would leave a cycle for the garbage collector.
        self._main_ref = weakref.ref(main)
        self.state = state
        self._syncing = False
        self._thread: QThread | None = None
        self._worker: ComposerFetchWorker | None = None
        self.figure = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DEBOUNCE_MS)
        self._preview_timer.timeout.connect(self.render_preview)
        self._build_ui()
        if not state.touched:
            self._seed_defaults()
        self._load_spec(state.spec)
        state.restored.connect(self._on_state_restored)
        self.render_preview()
        try:
            from src.ui.common.gui_shared import fit_window_to_screen

            fit_window_to_screen(self, 1300, 900)
        except Exception:
            self.resize(1300, 900)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        controls = QWidget()
        form = QVBoxLayout(controls)
        form.setContentsMargins(6, 6, 6, 6)

        panels_box = QGroupBox("Event panels (top to bottom)")
        panels_layout = QVBoxLayout(panels_box)
        self.panel_table = QTableWidget(0, 2)
        self.panel_table.setHorizontalHeaderLabels(["Panel", "Height"])
        self.panel_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.panel_table.verticalHeader().setVisible(False)
        self.panel_table.setMinimumHeight(len(PANEL_LABELS) * 32 + 30)
        self.panel_table.itemChanged.connect(lambda _item: self._changed())
        panels_layout.addWidget(self.panel_table)
        move_row = QHBoxLayout()
        self.up_btn = QPushButton("Move up")
        self.down_btn = QPushButton("Move down")
        self.up_btn.clicked.connect(lambda: self._move_panel(-1))
        self.down_btn.clicked.connect(lambda: self._move_panel(1))
        move_row.addWidget(self.up_btn)
        move_row.addWidget(self.down_btn)
        panels_layout.addLayout(move_row)
        form.addWidget(panels_box)

        range_box = QGroupBox("Time range (UT)")
        range_layout = QFormLayout(range_box)
        self.data_range_check = QCheckBox("Use the radio data's range")
        self.data_range_check.toggled.connect(self._on_range_mode)
        self.start_edit = QDateTimeEdit()
        self.end_edit = QDateTimeEdit()
        for edit in (self.start_edit, self.end_edit):
            edit.setTimeSpec(Qt.UTC)
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            edit.setCalendarPopup(True)
            edit.dateTimeChanged.connect(lambda _v: self._changed())
        range_layout.addRow(self.data_range_check)
        range_layout.addRow("Start", self.start_edit)
        range_layout.addRow("End", self.end_edit)
        form.addWidget(range_box)

        thumbs_box = QGroupBox("Thumbnails")
        thumbs_layout = QVBoxLayout(thumbs_box)
        self.thumb_table = QTableWidget(0, 3)
        self.thumb_table.setHorizontalHeaderLabels(["Time (UT)", "Source", "Image"])
        self.thumb_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.thumb_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.thumb_table.verticalHeader().setVisible(False)
        thumbs_layout.addWidget(self.thumb_table)
        thumb_buttons = QHBoxLayout()
        self.add_thumb_btn = QPushButton("Add")
        self.remove_thumb_btn = QPushButton("Remove")
        self.auto_thumb_btn = QPushButton("Three across the range")
        self.add_thumb_btn.clicked.connect(self._add_thumbnail)
        self.remove_thumb_btn.clicked.connect(self._remove_thumbnail)
        self.auto_thumb_btn.clicked.connect(self._auto_thumbnails)
        for button in (self.add_thumb_btn, self.remove_thumb_btn, self.auto_thumb_btn):
            thumb_buttons.addWidget(button)
        thumbs_layout.addLayout(thumb_buttons)
        form.addWidget(thumbs_box)

        geo_box = QGroupBox("Geomagnetic block (its own time axis)")
        geo_layout = QVBoxLayout(geo_box)
        self.geo_check = QCheckBox("Show the geomagnetic response")
        self.geo_check.toggled.connect(lambda _on: self._changed())
        geo_layout.addWidget(self.geo_check)
        geo_row = QHBoxLayout()
        self.geo_panel_checks: dict[str, QCheckBox] = {}
        for key, label in GEO_PANELS:
            check = QCheckBox(GEO_SHORT_LABELS.get(key, label))
            check.setToolTip(label)
            check.toggled.connect(lambda _on: self._changed())
            geo_row.addWidget(check)
            self.geo_panel_checks[key] = check
        geo_layout.addLayout(geo_row)
        form.addWidget(geo_box)

        page_box = QGroupBox("Page")
        page_layout = QFormLayout(page_box)
        self.size_combo = QComboBox()
        self.size_combo.addItems(list(SIZE_PRESETS) + [CUSTOM_SIZE])
        self.size_combo.currentTextChanged.connect(lambda _t: self._changed())
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(4.0, 40.0)
        self.width_spin.setSuffix(" cm")
        self.width_spin.valueChanged.connect(lambda _v: self._changed())
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(0.0, 60.0)
        self.height_spin.setSuffix(" cm")
        self.height_spin.setSpecialValueText("automatic")
        self.height_spin.valueChanged.connect(lambda _v: self._changed())
        self.font_spin = QDoubleSpinBox()
        self.font_spin.setRange(0.5, 2.0)
        self.font_spin.setSingleStep(0.1)
        self.font_spin.valueChanged.connect(lambda _v: self._changed())
        self.title_edit = QLineEdit()
        self.title_edit.textChanged.connect(lambda _t: self._changed())
        page_layout.addRow("Width", self.size_combo)
        page_layout.addRow("Custom width", self.width_spin)
        page_layout.addRow("Height", self.height_spin)
        page_layout.addRow("Font scale", self.font_spin)
        page_layout.addRow("Title", self.title_edit)
        form.addWidget(page_box)

        actions = QHBoxLayout()
        self.fetch_btn = QPushButton("Fetch missing data")
        self.fetch_btn.setToolTip("Download what the chosen panels and thumbnails need and is not loaded yet.")
        self.fetch_btn.clicked.connect(self.fetch_missing)
        self.export_btn = QPushButton("Export…")
        self.export_btn.clicked.connect(self.export_figure)
        actions.addWidget(self.fetch_btn)
        actions.addWidget(self.export_btn)
        form.addLayout(actions)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        form.addWidget(self.status_label)
        form.addStretch(1)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setWidget(controls)
        controls_scroll.setMinimumWidth(380)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(False)
        self.preview_scroll.setAlignment(Qt.AlignCenter)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(controls_scroll)
        splitter.addWidget(self.preview_scroll)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    # -------------------------------------------------------------- spec <-> UI
    def _seed_defaults(self) -> None:
        """A first figure from what is loaded: panels with data, three thumbnails."""
        spec = EventFigureSpec()
        data = collect_event_figure_data(self.main, spec, self.state)
        has = {"spectrum": data.spectrum is not None, "swaves": data.swaves is not None,
               "goes": bool(data.goes), "sep": False, "events": data.events is not None}
        for panel in spec.panels:
            panel.enabled = has.get(panel.key, False) or (panel.key == "spectrum")
        if data.base_utc is not None and data.x_range is not None:
            start = data.base_utc + timedelta(seconds=data.x_range[0])
            end = data.base_utc + timedelta(seconds=data.x_range[1])
            default_source = "AIA 193"
            spec.thumbnails = [ThumbnailSpec(when, default_source) for when in auto_thumbnail_times(start, end, 3)]
        spec.geomagnetic = GeoBlockSpec(enabled=data.geomagnetic is not None)
        self.state.spec = spec

    @property
    def main(self) -> Any:
        return self._main_ref()

    @Slot()
    def _on_state_restored(self) -> None:
        self._load_spec(self.state.spec)

    def _load_spec(self, spec: EventFigureSpec) -> None:
        self._syncing = True
        try:
            self.panel_table.setRowCount(0)
            for panel in spec.panels:
                self._append_panel_row(panel)
            use_data = spec.start is None or spec.end is None
            self.data_range_check.setChecked(use_data)
            start, end = spec.start, spec.end
            if use_data:
                data = collect_event_figure_data(self.main, EventFigureSpec(panels=[]), self.state)
                if data.base_utc is not None and data.x_range is not None:
                    start = data.base_utc + timedelta(seconds=data.x_range[0])
                    end = data.base_utc + timedelta(seconds=data.x_range[1])
            now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
            self.start_edit.setDateTime(_qdt(start or now - timedelta(hours=1)))
            self.end_edit.setDateTime(_qdt(end or now))
            self._on_range_mode(use_data)
            self.thumb_table.setRowCount(0)
            for thumb in spec.thumbnails:
                self._append_thumb_row(thumb)
            self.geo_check.setChecked(spec.geomagnetic.enabled)
            for key, check in self.geo_panel_checks.items():
                check.setChecked(key in spec.geomagnetic.panels)
            self.size_combo.setCurrentText(spec.size_preset if spec.size_preset in SIZE_PRESETS else CUSTOM_SIZE)
            self.width_spin.setValue(spec.custom_width_cm)
            self.height_spin.setValue(spec.custom_height_cm or 0.0)
            self.font_spin.setValue(spec.font_scale)
            self.title_edit.setText(spec.title)
        finally:
            self._syncing = False
        self._refresh_thumb_status()

    def _append_panel_row(self, panel: PanelSpec) -> None:
        row = self.panel_table.rowCount()
        self.panel_table.insertRow(row)
        item = QTableWidgetItem(PANEL_LABELS.get(panel.key, panel.key))
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        item.setCheckState(Qt.Checked if panel.enabled else Qt.Unchecked)
        item.setData(Qt.UserRole, panel.key)
        self.panel_table.setItem(row, 0, item)
        spin = QDoubleSpinBox()
        spin.setRange(0.2, 10.0)
        spin.setSingleStep(0.1)
        spin.setValue(float(panel.height_ratio))
        spin.valueChanged.connect(lambda _v: self._changed())
        self.panel_table.setCellWidget(row, 1, spin)

    def _append_thumb_row(self, thumb: ThumbnailSpec) -> None:
        row = self.thumb_table.rowCount()
        self.thumb_table.insertRow(row)
        edit = QDateTimeEdit(_qdt(thumb.time))
        edit.setTimeSpec(Qt.UTC)
        edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        edit.dateTimeChanged.connect(lambda _v: self._changed())
        self.thumb_table.setCellWidget(row, 0, edit)
        combo = QComboBox()
        combo.addItems([label for label, _inst, _chan in THUMBNAIL_SOURCES])
        if combo.findText(thumb.source) >= 0:
            combo.setCurrentText(thumb.source)
        combo.currentTextChanged.connect(lambda _t: self._changed())
        self.thumb_table.setCellWidget(row, 1, combo)
        self.thumb_table.setItem(row, 2, QTableWidgetItem(""))

    def current_spec(self) -> EventFigureSpec:
        panels = []
        for row in range(self.panel_table.rowCount()):
            item = self.panel_table.item(row, 0)
            spin = self.panel_table.cellWidget(row, 1)
            panels.append(PanelSpec(str(item.data(Qt.UserRole)), item.checkState() == Qt.Checked, float(spin.value())))
        thumbs = []
        for row in range(self.thumb_table.rowCount()):
            edit = self.thumb_table.cellWidget(row, 0)
            combo = self.thumb_table.cellWidget(row, 1)
            thumbs.append(ThumbnailSpec(_py(edit.dateTime()), combo.currentText()))
        use_data = self.data_range_check.isChecked()
        preset = self.size_combo.currentText()
        return EventFigureSpec(
            panels=panels,
            start=None if use_data else _py(self.start_edit.dateTime()),
            end=None if use_data else _py(self.end_edit.dateTime()),
            thumbnails=thumbs,
            geomagnetic=GeoBlockSpec(
                self.geo_check.isChecked(),
                tuple(key for key, check in self.geo_panel_checks.items() if check.isChecked()),
            ),
            size_preset=preset,
            custom_width_cm=float(self.width_spin.value()),
            custom_height_cm=float(self.height_spin.value()) or None,
            font_scale=float(self.font_spin.value()),
            title=self.title_edit.text().strip(),
        )

    def _changed(self) -> None:
        if self._syncing:
            return
        self.state.set_spec(self.current_spec())
        self.width_spin.setEnabled(self.size_combo.currentText() == CUSTOM_SIZE)
        self._refresh_thumb_status()
        self._preview_timer.start()

    def _on_range_mode(self, use_data: bool) -> None:
        self.start_edit.setEnabled(not use_data)
        self.end_edit.setEnabled(not use_data)
        self._changed()

    def _move_panel(self, step: int) -> None:
        row = self.panel_table.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.panel_table.rowCount():
            return
        spec = self.current_spec()
        spec.panels[row], spec.panels[target] = spec.panels[target], spec.panels[row]
        self._load_spec(spec)
        self.panel_table.setCurrentCell(target, 0)
        self._changed()

    def _range(self) -> tuple[datetime, datetime]:
        return _py(self.start_edit.dateTime()), _py(self.end_edit.dateTime())

    def _add_thumbnail(self) -> None:
        start, end = self._range()
        self._append_thumb_row(ThumbnailSpec(start + (end - start) / 2, "AIA 193"))
        self._changed()

    def _remove_thumbnail(self) -> None:
        row = self.thumb_table.currentRow()
        if row < 0:
            row = self.thumb_table.rowCount() - 1
        if row >= 0:
            self.thumb_table.removeRow(row)
            self._changed()

    def _auto_thumbnails(self) -> None:
        start, end = self._range()
        source = "AIA 193"
        if self.thumb_table.rowCount():
            source = self.thumb_table.cellWidget(0, 1).currentText()
        self.thumb_table.setRowCount(0)
        for when in auto_thumbnail_times(start, end, 3):
            self._append_thumb_row(ThumbnailSpec(when, source))
        self._changed()

    def _refresh_thumb_status(self) -> None:
        for row in range(self.thumb_table.rowCount()):
            edit = self.thumb_table.cellWidget(row, 0)
            combo = self.thumb_table.cellWidget(row, 1)
            when = _py(edit.dateTime())
            loaded = loaded_thumbnail(self.main, when)
            if loaded is not None:
                text = f"loaded: {loaded.label}"
            elif thumb_key(combo.currentText(), when) in self.state.thumbs:
                text = "Helioviewer (cached)"
            else:
                text = "missing — Fetch"
            item = self.thumb_table.item(row, 2)
            if item is not None:
                item.setText(text)

    # -------------------------------------------------------------- preview
    def build_figure(self) -> Any:
        spec = self.current_spec()
        data = collect_event_figure_data(self.main, spec, self.state)
        return build_event_figure(spec, data)

    def render_preview(self) -> None:
        try:
            self.figure = self.build_figure()
        except Exception as exc:
            self.status_label.setText(f"Preview failed: {exc}")
            return
        canvas = FigureCanvas(self.figure)
        width, height = self.figure.get_size_inches()
        dpi = 100
        canvas.setFixedSize(int(width * dpi), int(height * dpi))
        self.preview_scroll.setWidget(canvas)
        canvas.draw()
        self.canvas = canvas

    # ------------------------------------------------------------- fetching
    def is_fetching(self) -> bool:
        return self._thread is not None

    def fetch_missing(self) -> None:
        if self._thread is not None:
            return
        spec = self.current_spec()
        data = collect_event_figure_data(self.main, spec, self.state)
        enabled = {panel.key for panel in spec.enabled_panels()}
        start, end = spec.start, spec.end
        if (start is None or end is None) and data.base_utc is not None and data.x_range is not None:
            start = data.base_utc + timedelta(seconds=data.x_range[0])
            end = data.base_utc + timedelta(seconds=data.x_range[1])
        need_sep = "sep" in enabled and data.sep is None
        need_goes = "goes" in enabled and not data.goes
        thumbs = missing_thumbnails(self.main, spec, self.state)
        if not (need_sep or need_goes or thumbs):
            self.status_label.setText("Nothing is missing.")
            return
        from src.ui.space_weather.geomagnetic_controller import space_weather_cache_dir

        worker = ComposerFetchWorker(
            base_utc=data.base_utc, start=start, end=end, need_sep=need_sep, need_goes=need_goes,
            thumbnails=thumbs, cache_dir=space_weather_cache_dir(),
        )
        self._start_worker(worker)

    def _start_worker(self, worker: ComposerFetchWorker) -> None:
        thread = QThread()  # never parented to a widget (thread-teardown rule)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.status_label.setText)
        worker.finished.connect(self._on_fetched)
        worker.failed.connect(self._on_fetch_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        _LIVE.add((thread, worker))
        self._thread, self._worker = thread, worker
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching…")
        thread.start()

    @Slot(object)
    def _on_fetched(self, result: object) -> None:
        result = dict(result or {})
        if result.get("sep") is not None:
            self.state.sep = result["sep"]
        if result.get("goes"):
            self.state.goes = list(result["goes"])
        self.state.thumbs.update(result.get("thumbs") or {})
        errors = result.get("errors") or []
        self.status_label.setText("Fetched." if not errors else "Fetched, with problems: " + "; ".join(errors))
        self._refresh_thumb_status()
        self.render_preview()

    @Slot(str)
    def _on_fetch_failed(self, message: str) -> None:
        self.status_label.setText(f"Fetch failed: {message}")

    @Slot()
    def _on_thread_finished(self) -> None:
        thread = self._thread
        if thread is not None:
            thread.wait()
            _LIVE.discard((thread, self._worker))
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch missing data")

    # --------------------------------------------------------------- export
    def export_figure(self) -> None:
        default = "event_figure.pdf"
        path, _filter = QFileDialog.getSaveFileName(self, "Export Event Figure", default, FIGURE_EXPORT_FILTERS)
        if not path:
            return
        try:
            save_figure(self.build_figure(), path, tight=True)
        except Exception as exc:
            QMessageBox.critical(self, "Export Event Figure", f"Could not save the figure:\n{exc}")
            return
        self.status_label.setText(f"Saved {path}")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._preview_timer.stop()
        worker, thread = self._worker, self._thread
        if worker is not None:
            worker.request_cancel()
        if thread is not None:
            thread.quit()
            thread.wait(5000)
        super().closeEvent(event)
