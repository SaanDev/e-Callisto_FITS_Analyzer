"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
import platform
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from matplotlib.figure import Figure
from matplotlib.path import Path
from matplotlib.ticker import FuncFormatter, ScalarFormatter
from matplotlib.widgets import LassoSelector, RectangleSelector
from mpl_toolkits.axes_grid1 import make_axes_locatable
from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QThread, QTimer, QUrl, Slot
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QDesktopServices,
    QFontDatabase,
    QIcon,
    QImage,
    QPainter,
    QPalette,
    QPdfWriter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedLayout,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from src.Backend.analysis_log import append_csv_log, append_txt_summary, build_log_row
from src.Backend.analysis_session import (
    from_legacy_max_intensity,
    normalize_session as normalize_analysis_session,
    to_project_payload as analysis_session_to_project_payload,
    validate_session_for_source,
)
from src.Backend.annotations import make_annotation, normalize_annotations, toggle_all_visibility
from src.Backend.fits_io import build_combined_header, extract_ut_start_sec, load_callisto_fits
from src.Backend.presets import (
    PRESET_SCHEMA_VERSION,
    build_preset,
    delete_preset,
    dump_presets_json,
    parse_presets_json,
    upsert_preset,
)
from src.Backend.project_session import ProjectFormatError, read_project, write_project
from src.Backend.provenance import build_provenance_payload, write_provenance_files
from src.Backend.recovery_manager import (
    DEFAULT_MAX_SNAPSHOTS,
    latest_snapshot_path,
    load_recovery_snapshot,
    save_recovery_snapshot,
)
from src.Backend.rfi_filters import clean_rfi, config_dict as rfi_config_dict
from src.Backend.update_checker import GITHUB_REPO
from src.UI.accelerated_plot_widget import AcceleratedPlotWidget
from src.UI.callisto_downloader import CallistoDownloaderApp
from src.UI.dialogs.analyze_dialog import AnalyzeDialog
from src.UI.dialogs.batch_processing_dialog import BatchProcessingDialog
from src.UI.dialogs.bug_report_dialog import BugReportDialog
from src.UI.dialogs.combine_dialogs import CombineFrequencyDialog, CombineTimeDialog
from src.UI.dialogs.max_intensity_dialog import MaxIntensityPlotDialog
from src.UI.dialogs.rfi_control_dialog import RFIControlDialog
from src.UI.fits_header_viewer import FitsHeaderViewerDialog
from src.UI.goes_xrs_gui import MainWindow as GoesXrsWindow
from src.UI.gui_shared import MplCanvas, _install_linux_msgbox_fixer, pick_export_path, resource_path
from src.UI.gui_workers import DownloaderImportWorker, UpdateCheckWorker, UpdateDownloadWorker
from src.UI.mpl_style import style_axes
from src.UI.utils.cme_helper_client import CMEHelperClient
from src.version import APP_NAME, APP_ORG, APP_VERSION

class MainWindow(QMainWindow):
    # Convert digit differences (e.g., Ihot - Icold) to dB.
    DB_SCALE = 2500.0 / 256.0 / 25.4
    HW_DEFAULT_TICK_FONT_PX = 14
    HW_DEFAULT_AXIS_FONT_PX = 16
    HW_DEFAULT_TITLE_FONT_PX = 22

    def __init__(self, theme=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.theme = QApplication.instance().property("theme_manager") if QApplication.instance() else None
        if self.theme and hasattr(self.theme, "themeChanged"):
            self.theme.themeChanged.connect(self._on_theme_changed)
        if self.theme and hasattr(self.theme, "viewModeChanged"):
            self.theme.viewModeChanged.connect(lambda _mode: self._sync_view_mode_actions())
        self._ui_settings = QSettings(APP_ORG, APP_NAME)
        self._max_auto_clean_isolated = bool(
            self._ui_settings.value("processing/max_auto_clean_isolated", True, type=bool)
        )
        self._view_mode = (
            self._normalize_view_mode(self.theme.view_mode())
            if (self.theme and hasattr(self.theme, "view_mode"))
            else self._normalize_view_mode(self._ui_settings.value("ui/view_mode", "modern"))
        )
        self._cme_helper_client = CMEHelperClient(theme_manager=self.theme, parent=self)
        self._cme_viewer = None

        #Linux Messagebox Fix
        _install_linux_msgbox_fixer()

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        #self.resize(1000, 700)
        self.setMinimumSize(1000, 700)

        self.use_utc = False
        self.ut_start_sec = None
        self.use_db = False  # False = Digits (default), True = dB

        # --- Undo / Redo ---
        self._undo_stack = []
        self._redo_stack = []
        # Limit only applies to full data-state snapshots (zoom/pan history is lightweight)
        self._max_undo = 30  # prevent memory blow-up
        self._max_history_entries = 5000  # guardrail for view-history spam

        # --- View (zoom/pan) "home" + history ---
        self._home_view = None
        self._pan_start_view = None

        # --- Graph Properties (non-colormap) ---
        self.graph_title_override = ""  # empty = use context-aware default title
        self.graph_font_family = ""  # empty = use Matplotlib default

        self.tick_font_px = 11
        self.axis_label_font_px = 12
        self.title_font_px = 14
        self._hw_default_font_sizes_active = True

        self._colorbar_label_text = ""

        self.current_cmap_name = "Custom"
        self.lasso_active = False

        self.noise_vmin = None
        self.noise_vmax = None

        self.current_display_data = None
        self._current_plot_source_data = None

        # --- Graph Properties: style flags ---
        self.title_bold = False
        self.title_italic = False

        self.axis_bold = False
        self.axis_italic = False

        self.ticks_bold = False
        self.ticks_italic = False

        self.remove_titles = False

        self._build_toolbar()
        self._refresh_toolbar_icons()

        # Debounce timer for smooth slider updates
        self.noise_smooth_timer = QTimer()
        self.noise_smooth_timer.setInterval(12)
        self.noise_smooth_timer.setSingleShot(True)
        self.noise_smooth_timer.timeout.connect(self.update_noise_live)
        self.noise_commit_timer = QTimer()
        self.noise_commit_timer.setInterval(80)
        self.noise_commit_timer.setSingleShot(True)
        self.noise_commit_timer.timeout.connect(self._commit_noise_live_update)

        self._noise_undo_pending = False
        self._noise_slider_drag_active = False
        self._noise_preview_active = False
        self._noise_base_data = None
        self._noise_base_source_id = None

        self._import_thread = None
        self._import_worker = None
        self._update_thread = None
        self._update_worker = None
        self._update_download_thread = None
        self._update_download_worker = None
        self._update_download_progress_dialog = None
        self._import_progress_dialog = None
        self._goes_window = None
        self._batch_processing_dialog = None
        self._bug_report_dialog = None

        # Processing audit + derived state
        self._processing_log = []
        self._last_time_sync_context = {}
        self._active_preset_snapshot = None

        # RFI state
        self._rfi_dialog = None
        self._rfi_config = rfi_config_dict(
            enabled=True,
            kernel_time=3,
            kernel_freq=3,
            channel_z_threshold=6.0,
            percentile_clip=99.5,
            masked_channel_indices=[],
            applied=False,
        )
        self._rfi_preview_data = None
        self._rfi_preview_masked = []

        # Annotation state
        self._annotations = []
        self._annotations_visible = True
        self._annotation_mode = None
        self._annotation_click_points = []
        self._annotation_pending_text = ""
        self._annotation_mpl_cid = None
        self._annotation_artists = []
        self._annotation_style_defaults = {"color": "#00d4ff", "line_width": 1.5}

        # Autosave / crash-recovery state
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(180000)
        self._autosave_timer.timeout.connect(self._perform_autosave)
        self._autosave_timer.start()
        self._previous_clean_exit = bool(self._ui_settings.value("runtime/clean_exit", True, type=bool))
        self._ui_settings.setValue("runtime/clean_exit", False)

        # Canvas
        self.canvas = MplCanvas(self, width=10, height=6)
        style_axes(self.canvas.ax)

        # Hardware-accelerated plotting canvas (full graph area when enabled)
        self.accel_canvas = AcceleratedPlotWidget(self)
        self.use_hw_live_preview = bool(self.accel_canvas.is_available)
        if self.accel_canvas.is_available:
            self.accel_canvas.mousePositionChanged.connect(self.on_accel_mouse_motion_status)
            self.accel_canvas.viewInteractionFinished.connect(self._on_accel_view_interaction_finished)
            self.accel_canvas.rectZoomFinished.connect(self._on_accel_rect_zoom_finished)
            self.accel_canvas.lassoFinished.connect(self._on_accel_lasso_finished)
            self.accel_canvas.driftPointAdded.connect(self._on_accel_drift_point_added)
            self.accel_canvas.driftCaptureFinished.connect(self._on_accel_drift_capture_finished)

        self.canvas.mpl_connect("scroll_event", self.on_scroll_zoom)
        self._cid_press = self.canvas.mpl_connect("button_press_event", self.on_mouse_press)
        self._cid_release = self.canvas.mpl_connect("button_release_event", self.on_mouse_release)
        self._cid_motion = self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)
        self._cid_motion_status = self.canvas.mpl_connect("motion_notify_event", self.on_mouse_motion_status)

        self._apply_mpl_theme()
        self._apply_accel_theme()

        self._panning = False
        self._last_pan_xy = None

        # --- Navigation state (pan/scroll lock + rectangle zoom) ---
        self.nav_locked = False  # False = normal pan + scroll zoom
        self.rect_zoom_active = False  # True only while RectangleSelector is active
        self._rect_selector = None  # RectangleSelector instance

        # Colorbar
        self.current_colorbar = None
        self.current_cax = None

        # Statusbar
        self.setStatusBar(QStatusBar())
        # Permanent label on right side for cursor coordinates
        self.cursor_label = QLabel("")
        self.cursor_label.setStyleSheet("padding-right: 8px;")
        self.statusBar().addPermanentWidget(self.cursor_label)

        # =========================
        # LEFT SIDEBAR (CROSS-PLATFORM SAFE)
        # Put this whole block where you currently build:
        #   slider_group, units_group_box, graph_group, main_layout, container
        #
        # Required imports (add if missing):
        #   from PySide6.QtWidgets import QScrollArea, QFrame
        # =========================

        # -------------------------
        # Noise clipping sliders
        # -------------------------
        self.lower_slider = QSlider(Qt.Horizontal)
        self.lower_slider.setRange(-100, 100)
        self.lower_slider.setValue(0)

        self.upper_slider = QSlider(Qt.Horizontal)
        self.upper_slider.setRange(-100, 100)
        self.upper_slider.setValue(0)

        slider_group = QGroupBox("Noise Clipping Thresholds")
        self.slider_group = slider_group
        slider_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        slider_layout = QVBoxLayout(slider_group)
        slider_layout.setContentsMargins(12, 12, 12, 12)
        slider_layout.setSpacing(8)

        lbl_low = QLabel("Lower Threshold")
        lbl_low.setAlignment(Qt.AlignLeft)
        slider_layout.addWidget(lbl_low)
        slider_layout.addWidget(self.lower_slider)

        lbl_high = QLabel("Upper Threshold")
        lbl_high.setAlignment(Qt.AlignLeft)
        slider_layout.addWidget(lbl_high)
        slider_layout.addWidget(self.upper_slider)

        # -------------------------
        # Units Group (Intensity + Time in one row)
        # -------------------------
        self.units_group_box = QGroupBox("Units")
        self.units_group_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        units_layout = QVBoxLayout(self.units_group_box)
        units_layout.setContentsMargins(12, 12, 12, 12)
        units_layout.setSpacing(10)

        # ---- Horizontal container for Intensity + Time ----
        units_row = QHBoxLayout()
        units_row.setSpacing(24)

        # ===== Intensity column =====
        intensity_col = QVBoxLayout()
        intensity_col.setSpacing(6)

        intensity_label = QLabel("Intensity")
        intensity_label.setProperty("section", True)

        self.units_digits_radio = QRadioButton("Digits")
        self.units_db_radio = QRadioButton("dB")
        self.units_digits_radio.setChecked(True)

        self.units_group = QButtonGroup(self)
        self.units_group.addButton(self.units_digits_radio)
        self.units_group.addButton(self.units_db_radio)

        intensity_col.addWidget(intensity_label)
        intensity_col.addWidget(self.units_digits_radio)
        intensity_col.addWidget(self.units_db_radio)
        intensity_col.addStretch(1)

        # ===== Time column =====
        time_col = QVBoxLayout()
        time_col.setSpacing(6)

        time_label = QLabel("Time")
        time_label.setProperty("section", True)

        self.time_sec_radio = QRadioButton("Seconds")
        self.time_ut_radio = QRadioButton("UT")
        self.time_sec_radio.setChecked(True)

        self.time_group = QButtonGroup(self)
        self.time_group.addButton(self.time_sec_radio)
        self.time_group.addButton(self.time_ut_radio)

        time_col.addWidget(time_label)
        time_col.addWidget(self.time_sec_radio)
        time_col.addWidget(self.time_ut_radio)
        time_col.addStretch(1)

        # ---- Add both columns to the row ----
        units_row.addLayout(intensity_col, 1)
        units_row.addLayout(time_col, 1)

        # ---- Add row to Units group ----
        units_layout.addLayout(units_row)
        units_layout.addStretch(1)

        # -------------------------
        # Graph Properties Group
        # -------------------------
        def _section_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("SectionLabel")
            return lbl

        def _spin_row(label_text: str, spin: QSpinBox) -> QWidget:
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            label = QLabel(label_text)
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

            spin.setMinimumWidth(90)
            spin.setMaximumWidth(110)
            spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            row.addWidget(label, 1)
            row.addWidget(spin, 0, Qt.AlignRight)
            return w

        def _style_row(label_text: str, cb_bold: QCheckBox, cb_italic: QCheckBox) -> QWidget:
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            label = QLabel(label_text)
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

            row.addWidget(label, 1)
            row.addWidget(cb_bold, 0)
            row.addWidget(cb_italic, 0)
            return w

        self.graph_group = QGroupBox("Graph Properties")
        self.graph_group.setEnabled(False)
        self.graph_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        graph_layout = QVBoxLayout(self.graph_group)
        graph_layout.setContentsMargins(12, 12, 12, 12)
        graph_layout.setSpacing(8)

        # Appearance
        graph_layout.addWidget(_section_label("Appearance"))

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems([
            "Custom", "viridis", "plasma", "inferno", "magma",
            "cividis", "turbo", "RdYlBu", "jet", "cubehelix",
        ])
        graph_layout.addWidget(QLabel("Colormap"))
        graph_layout.addWidget(self.cmap_combo)

        self.font_combo = QComboBox()
        self.font_combo.addItem("Default")
        for f in sorted(QFontDatabase.families()):
            self.font_combo.addItem(f)

        graph_layout.addWidget(QLabel("Font family"))
        graph_layout.addWidget(self.font_combo)

        graph_layout.addSpacing(8)

        # Text
        graph_layout.addWidget(_section_label("Text"))

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Custom title (leave empty for default)")
        self.title_edit.setMinimumHeight(20)
        graph_layout.addWidget(QLabel("Graph title"))
        graph_layout.addWidget(self.title_edit)

        self.remove_titles_chk = QCheckBox("Remove Titles")
        graph_layout.addWidget(self.remove_titles_chk)

        graph_layout.addSpacing(8)

        # Font sizes
        graph_layout.addWidget(_section_label("Font sizes"))

        self.tick_font_spin = QSpinBox()
        self.tick_font_spin.setRange(6, 60)
        self.tick_font_spin.setValue(self.tick_font_px)
        graph_layout.addWidget(_spin_row("Tick labels (px)", self.tick_font_spin))

        self.axis_font_spin = QSpinBox()
        self.axis_font_spin.setRange(6, 60)
        self.axis_font_spin.setValue(self.axis_label_font_px)
        graph_layout.addWidget(_spin_row("Axis labels (px)", self.axis_font_spin))

        self.title_font_spin = QSpinBox()
        self.title_font_spin.setRange(6, 80)
        self.title_font_spin.setValue(self.title_font_px)
        graph_layout.addWidget(_spin_row("Title (px)", self.title_font_spin))

        graph_layout.addSpacing(8)

        # Text style
        graph_layout.addWidget(_section_label("Text style"))

        self.title_bold_chk = QCheckBox("Bold")
        self.title_italic_chk = QCheckBox("Italic")
        graph_layout.addWidget(_style_row("Title", self.title_bold_chk, self.title_italic_chk))

        self.axis_bold_chk = QCheckBox("Bold")
        self.axis_italic_chk = QCheckBox("Italic")
        graph_layout.addWidget(_style_row("Axis labels", self.axis_bold_chk, self.axis_italic_chk))

        self.ticks_bold_chk = QCheckBox("Bold")
        self.ticks_italic_chk = QCheckBox("Italic")
        graph_layout.addWidget(_style_row("Tick labels", self.ticks_bold_chk, self.ticks_italic_chk))

        graph_layout.addStretch(1)

        self.analysis_summary_group = QGroupBox("Analysis Summary")
        self.analysis_summary_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        analysis_summary_layout = QVBoxLayout(self.analysis_summary_group)
        analysis_summary_layout.setContentsMargins(12, 12, 12, 12)
        analysis_summary_layout.setSpacing(6)
        self.analysis_summary_label = QLabel("No analysis session loaded.")
        self.analysis_summary_label.setWordWrap(True)
        analysis_summary_layout.addWidget(self.analysis_summary_label)

        # -------------------------
        # Build the LEFT PANEL as a widget, then put it in a ScrollArea
        # This is the key fix for Windows (no overlaps, no clipping).
        # -------------------------
        side_panel_widget = QWidget()
        side_panel_layout = QVBoxLayout(side_panel_widget)
        side_panel_layout.setContentsMargins(10, 10, 10, 10)
        side_panel_layout.setSpacing(12)

        side_panel_layout.addWidget(slider_group)
        side_panel_layout.addWidget(self.units_group_box)
        side_panel_layout.addWidget(self.graph_group)
        side_panel_layout.addWidget(self.analysis_summary_group)
        side_panel_layout.addStretch(1)

        # Consistent width for all groups (better on Windows DPI scaling)
        SIDEBAR_W = 250
        slider_group.setMaximumWidth(SIDEBAR_W)
        self.units_group_box.setMaximumWidth(SIDEBAR_W)
        self.graph_group.setMaximumWidth(SIDEBAR_W)
        self.analysis_summary_group.setMaximumWidth(SIDEBAR_W)

        self.side_scroll = QScrollArea()
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QFrame.NoFrame)
        self.side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.side_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.side_scroll.setMinimumWidth(SIDEBAR_W + 16)  # room for scrollbar
        self.side_scroll.setMaximumWidth(SIDEBAR_W + 28)
        self.side_scroll.setWidget(side_panel_widget)

        self.sidebar_toggle_btn = QPushButton("◀")
        self.sidebar_toggle_btn.setObjectName("SidebarToggleButton")
        self.sidebar_toggle_btn.setToolTip("Collapse sidebar")
        self.sidebar_toggle_btn.setFixedSize(12, 22)
        self.sidebar_toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.sidebar_toggle_btn.setFocusPolicy(Qt.NoFocus)
        self.sidebar_toggle_btn.clicked.connect(self.toggle_left_sidebar)

        self.sidebar_toggle_strip = QWidget()
        self.sidebar_toggle_strip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.sidebar_toggle_strip.setFixedWidth(12)
        sidebar_toggle_layout = QVBoxLayout(self.sidebar_toggle_strip)
        sidebar_toggle_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_toggle_layout.setSpacing(0)
        sidebar_toggle_layout.addStretch(1)
        sidebar_toggle_layout.addWidget(self.sidebar_toggle_btn, 0, Qt.AlignHCenter)
        sidebar_toggle_layout.addStretch(1)
        self._sidebar_collapsed = False

        # -------------------------
        # Style (safe sizes, no tiny max-heights)
        # -------------------------
        if not (self.theme and hasattr(self.theme, "set_view_mode")):
            sidebar_style = self._classic_sidebar_qss()
            slider_group.setStyleSheet(sidebar_style)
            self.units_group_box.setStyleSheet(sidebar_style)
            self.graph_group.setStyleSheet(sidebar_style)
            self.analysis_summary_group.setStyleSheet(sidebar_style)

        # -------------------------
        # Main layout with scrollable sidebar + canvas
        # -------------------------
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(0)

        self.plot_stack_host = QWidget()
        self.plot_stack = QStackedLayout(self.plot_stack_host)
        self.plot_stack.setContentsMargins(0, 0, 0, 0)
        self.plot_stack.setSpacing(0)
        self.plot_stack.addWidget(self.canvas)
        self.plot_stack.addWidget(self.accel_canvas)
        self.plot_stack.setCurrentWidget(self.accel_canvas if self.use_hw_live_preview else self.canvas)

        main_layout.addWidget(self.side_scroll, 0)
        main_layout.addWidget(self.sidebar_toggle_strip, 0)
        main_layout.addWidget(self.plot_stack_host, 1)
        self._main_layout = main_layout

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)
        self._set_sidebar_collapsed(False)

        # ----- Menu Bar -----
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("File")

        # --- Open ---
        self.open_action = QAction("Open", self)
        file_menu.addAction(self.open_action)

        file_menu.addSeparator()

        # --- Project (session) ---
        self.open_project_action = QAction("Open Project...", self)
        self.open_project_action.setShortcut("Ctrl+Shift+O")
        file_menu.addAction(self.open_project_action)

        self.save_project_action = QAction("Save Project", self)
        self.save_project_action.setShortcut("Ctrl+S")
        file_menu.addAction(self.save_project_action)

        self.save_project_as_action = QAction("Save Project As...", self)
        self.save_project_as_action.setShortcut("Ctrl+Shift+S")
        file_menu.addAction(self.save_project_as_action)

        self.recover_last_session_action = QAction("Recover Last Session...", self)
        file_menu.addAction(self.recover_last_session_action)

        file_menu.addSeparator()

        # --- Save As (disabled for main window) ---
        self.save_action = QAction("Save As", self)
        self.save_action.setEnabled(False)
        file_menu.addAction(self.save_action)

        # --- Export As submenu ---
        export_menu = QMenu("Export As", self)
        file_menu.addMenu(export_menu)

        self.export_figure_action = QAction("Export Figure", self)
        export_menu.addAction(self.export_figure_action)
        self.export_figure_action.triggered.connect(self.export_figure)

        self.export_fits_action = QAction("Export to FIT", self)
        export_menu.addAction(self.export_fits_action)
        self.export_fits_action.triggered.connect(self.export_to_fits)

        self.export_provenance_action = QAction("Export Provenance Report...", self)
        export_menu.addAction(self.export_provenance_action)
        self.export_provenance_action.triggered.connect(self.export_provenance_report)

        self.export_analysis_log_action = QAction("Export Analysis Log...", self)
        export_menu.addAction(self.export_analysis_log_action)
        self.export_analysis_log_action.triggered.connect(self.export_analysis_log)

        # Edit Menu
        edit_menu = menubar.addMenu("Edit")

        self.undo_action = QAction("Undo", self)
        self.undo_action.setShortcut("Ctrl+Z")
        edit_menu.addAction(self.undo_action)

        self.redo_action = QAction("Redo", self)
        self.redo_action.setShortcut("Ctrl+Shift+Z")
        edit_menu.addAction(self.redo_action)

        edit_menu.addSeparator()

        self.reset_to_raw_action = QAction("Reset to Raw", self)
        self.reset_to_raw_action.setEnabled(False)
        edit_menu.addAction(self.reset_to_raw_action)
        self.reset_to_raw_action.triggered.connect(self.reset_to_raw)

        edit_menu.addSeparator()

        reset_action = QAction("Reset All", self)
        edit_menu.addAction(reset_action)
        reset_action.triggered.connect(self.reset_all)

        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)

        # Download Menu
        download_menu = menubar.addMenu("Download")

        # --- Launch Downloader ---
        launch_downloader_action = QAction("Launch FITS Downloader", self)
        download_menu.addAction(launch_downloader_action)
        launch_downloader_action.triggered.connect(self.launch_downloader)

        # Solar Events Menu
        solar_events_menu = menubar.addMenu("Solar Events")

        # CMEs submenu
        cmes_submenu = solar_events_menu.addMenu("CMEs")
        soho_lasco_action = QAction("SOHO/LASCO CME Catalog", self)
        soho_lasco_action.triggered.connect(self.open_cme_viewer)
        cmes_submenu.addAction(soho_lasco_action)

        # Flares submenu
        flares_submenu = solar_events_menu.addMenu("Flares")
        goes_flux_action = QAction("GOES X-Ray Flux", self)
        goes_flux_action.triggered.connect(self.open_goes_xrs_window)
        flares_submenu.addAction(goes_flux_action)

        # Radio submenu
        radio_submenu = solar_events_menu.addMenu("Radio Bursts")
        radio_action = QAction("e-CALLISTO", self)
        radio_action.triggered.connect(self.launch_downloader)
        radio_submenu.addAction(radio_action)

        solar_events_menu.addSeparator()
        self.sync_time_window_action = QAction("Sync Current Time Window", self)
        self.sync_time_window_action.triggered.connect(self.sync_current_time_window_to_solar_events)
        solar_events_menu.addAction(self.sync_time_window_action)

        # FITS View Menu
        fits_view_menu = menubar.addMenu("FITS View")
        self.view_fits_header_action = QAction("View FITS Header", self)
        self.view_fits_header_action.setEnabled(False)
        fits_view_menu.addAction(self.view_fits_header_action)
        self.view_fits_header_action.triggered.connect(self.open_fits_header_viewer)


        # View Menu
        view_menu = menubar.addMenu("View")
        theme_menu = view_menu.addMenu("Theme")
        mode_menu = view_menu.addMenu("Mode")

        self.theme_action_system = QAction("System", self, checkable=True)
        self.theme_action_light = QAction("Light", self, checkable=True)
        self.theme_action_dark = QAction("Dark", self, checkable=True)
        self.mode_action_classic = QAction("Classic", self, checkable=True)
        self.mode_action_modern = QAction("Modern", self, checkable=True)

        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for a in (self.theme_action_system, self.theme_action_light, self.theme_action_dark):
            theme_group.addAction(a)
            theme_menu.addAction(a)
        view_mode_group = QActionGroup(self)
        view_mode_group.setExclusive(True)
        for a in (self.mode_action_classic, self.mode_action_modern):
            view_mode_group.addAction(a)
            mode_menu.addAction(a)

        processing_menu = menubar.addMenu("Processing")
        hw_menu = processing_menu.addMenu("Hardware Acceleration")
        self.hw_live_preview_action = QAction("Enable", self, checkable=True)
        self.hw_live_preview_action.setChecked(self.use_hw_live_preview)
        self.hw_live_preview_action.setEnabled(bool(self.accel_canvas.is_available))
        hw_menu.addAction(self.hw_live_preview_action)

        rfi_menu = processing_menu.addMenu("RFI Cleaning")
        self.rfi_open_action = QAction("Open RFI Panel", self)
        self.rfi_apply_action = QAction("Apply RFI", self)
        self.rfi_reset_action = QAction("Reset RFI", self)
        rfi_menu.addAction(self.rfi_open_action)
        rfi_menu.addAction(self.rfi_apply_action)
        rfi_menu.addAction(self.rfi_reset_action)

        ann_menu = processing_menu.addMenu("Annotations")
        self.ann_add_polygon_action = QAction("Add Polygon", self)
        self.ann_add_line_action = QAction("Add Line", self)
        self.ann_add_text_action = QAction("Add Text", self)
        self.ann_toggle_visibility_action = QAction("Toggle Visibility", self)
        self.ann_delete_last_action = QAction("Delete Last", self)
        self.ann_clear_action = QAction("Clear All", self)
        ann_menu.addAction(self.ann_add_polygon_action)
        ann_menu.addAction(self.ann_add_line_action)
        ann_menu.addAction(self.ann_add_text_action)
        ann_menu.addSeparator()
        ann_menu.addAction(self.ann_toggle_visibility_action)
        ann_menu.addAction(self.ann_delete_last_action)
        ann_menu.addAction(self.ann_clear_action)

        presets_menu = processing_menu.addMenu("Presets")
        self.preset_save_action = QAction("Save Current as Preset...", self)
        self.preset_apply_action = QAction("Apply Preset...", self)
        self.preset_delete_action = QAction("Delete Preset...", self)
        presets_menu.addAction(self.preset_save_action)
        presets_menu.addAction(self.preset_apply_action)
        presets_menu.addAction(self.preset_delete_action)

        max_menu = processing_menu.addMenu("Maximum Intensity")
        self.max_auto_clean_isolated_action = QAction("Auto-Clean Isolated Burst Outliers", self, checkable=True)
        self.max_auto_clean_isolated_action.setChecked(bool(getattr(self, "_max_auto_clean_isolated", True)))
        max_menu.addAction(self.max_auto_clean_isolated_action)

        analysis_menu = processing_menu.addMenu("Analysis Session")
        self.open_restored_analysis_action = QAction("Open Restored Analysis", self)
        analysis_menu.addAction(self.open_restored_analysis_action)

        batch_menu = processing_menu.addMenu("Batch Processing")
        self.open_batch_processing_action = QAction("Open Batch Processor", self)
        batch_menu.addAction(self.open_batch_processing_action)

        # Set initial checks from saved mode
        if self.theme:
            m = self.theme.mode()
            self.theme_action_system.setChecked(m == "system")
            self.theme_action_light.setChecked(m == "light")
            self.theme_action_dark.setChecked(m == "dark")
        else:
            self.theme_action_system.setChecked(True)
            self.theme_action_light.setEnabled(False)
            self.theme_action_dark.setEnabled(False)
        self._sync_view_mode_actions()

        # Connect changes
        self.theme_action_system.triggered.connect(lambda: self.theme and self.theme.set_mode("system"))
        self.theme_action_light.triggered.connect(lambda: self.theme and self.theme.set_mode("light"))
        self.theme_action_dark.triggered.connect(lambda: self.theme and self.theme.set_mode("dark"))
        self.mode_action_classic.triggered.connect(lambda checked: checked and self.set_view_mode("classic"))
        self.mode_action_modern.triggered.connect(lambda checked: checked and self.set_view_mode("modern"))
        self.hw_live_preview_action.toggled.connect(self.set_hardware_live_preview_enabled)

        # About Menu
        about_menu = menubar.addMenu("About")
        self.check_updates_action = QAction("Check for Updates...", self)
        self.check_updates_action.setMenuRole(QAction.NoRole)
        about_menu.addAction(self.check_updates_action)
        self.report_bug_action = QAction("Report a Bug...", self)
        self.report_bug_action.setMenuRole(QAction.NoRole)
        about_menu.addAction(self.report_bug_action)
        about_menu.addSeparator()
        about_action = QAction("About", self)
        about_action.setMenuRole(QAction.NoRole)
        about_menu.addAction(about_action)
        self.check_updates_action.triggered.connect(self.check_for_app_updates)
        self.report_bug_action.triggered.connect(self.open_bug_report_dialog)
        about_action.triggered.connect(self.show_about_dialog)

        # (OPTIONAL) Connect them later like:
        # open_action.triggered.connect(self.open_file)

        self.setCentralWidget(container)

        # Signals
        self.lower_slider.valueChanged.connect(self.schedule_noise_update)
        self.upper_slider.valueChanged.connect(self.schedule_noise_update)
        self.lower_slider.sliderPressed.connect(self._on_noise_slider_pressed)
        self.upper_slider.sliderPressed.connect(self._on_noise_slider_pressed)
        self.lower_slider.sliderReleased.connect(self._on_noise_slider_released)
        self.upper_slider.sliderReleased.connect(self._on_noise_slider_released)
        self.cmap_combo.currentTextChanged.connect(self.change_cmap)
        self.open_action.triggered.connect(self.load_file)
        self.open_project_action.triggered.connect(self.open_project)
        self.save_project_action.triggered.connect(self.save_project)
        self.save_project_as_action.triggered.connect(self.save_project_as)
        self.recover_last_session_action.triggered.connect(self.recover_last_session)
        self.units_digits_radio.toggled.connect(
            lambda checked: checked and self.set_units_mode(False)
        )
        self.units_db_radio.toggled.connect(
            lambda checked: checked and self.set_units_mode(True)
        )

        self.time_sec_radio.toggled.connect(
            lambda checked: checked and self.set_axis_to_seconds()
        )
        self.time_ut_radio.toggled.connect(
            lambda checked: checked and self.set_axis_to_utc()
        )

        # Keep existing colormap live behavior
        self.cmap_combo.currentTextChanged.connect(self.change_cmap)

        self.rfi_open_action.triggered.connect(self.open_rfi_panel)
        self.rfi_apply_action.triggered.connect(self.apply_rfi_now)
        self.rfi_reset_action.triggered.connect(self.reset_rfi)

        self.ann_add_polygon_action.triggered.connect(self.start_annotation_polygon)
        self.ann_add_line_action.triggered.connect(self.start_annotation_line)
        self.ann_add_text_action.triggered.connect(self.start_annotation_text)
        self.ann_toggle_visibility_action.triggered.connect(self.toggle_annotations_visibility)
        self.ann_delete_last_action.triggered.connect(self.delete_last_annotation)
        self.ann_clear_action.triggered.connect(self.clear_annotations)

        self.preset_save_action.triggered.connect(self.save_current_preset)
        self.preset_apply_action.triggered.connect(self.apply_saved_preset)
        self.preset_delete_action.triggered.connect(self.delete_saved_preset)
        self.max_auto_clean_isolated_action.toggled.connect(self.set_max_auto_clean_isolated_enabled)
        self.open_restored_analysis_action.triggered.connect(self.open_restored_analysis_windows)
        self.open_batch_processing_action.triggered.connect(self.open_batch_processing_window)

        # Real-time graph properties (non-colormap)
        self.title_edit.textChanged.connect(self.apply_graph_properties_live)
        self.font_combo.currentTextChanged.connect(self.apply_graph_properties_live)
        self.tick_font_spin.valueChanged.connect(self._on_tick_font_spin_changed)
        self.axis_font_spin.valueChanged.connect(self._on_axis_font_spin_changed)
        self.title_font_spin.valueChanged.connect(self._on_title_font_spin_changed)

        # Real-time style toggles
        self.remove_titles_chk.toggled.connect(self.apply_graph_properties_live)

        self.title_bold_chk.toggled.connect(self.apply_graph_properties_live)
        self.title_italic_chk.toggled.connect(self.apply_graph_properties_live)

        self.axis_bold_chk.toggled.connect(self.apply_graph_properties_live)
        self.axis_italic_chk.toggled.connect(self.apply_graph_properties_live)

        self.ticks_bold_chk.toggled.connect(self.apply_graph_properties_live)
        self.ticks_italic_chk.toggled.connect(self.apply_graph_properties_live)

        # Data placeholders
        self.raw_data = None
        self.freqs = None
        self.time = None
        self.filename = ""
        self.current_plot_type = "Raw"  # or "NoiseReduced" or "Isolated"

        # ----- Project/session save state -----
        self._project_path = None
        self._project_dirty = False
        self._loading_project = False
        self._max_intensity_state = None  # populated after Max-Intensity dialog closes
        self._analysis_session = None
        self._max_intensity_dialog = None
        self._analyze_dialog = None
        self._analysis_sync_guard = False
        self._pending_analysis_restore = {"open_max": False, "open_analyzer": False, "warning": ""}

        # FITS export metadata
        self._fits_header0 = None  # primary header template
        self._fits_source_path = None  # original single-file path (if any)

        self._is_combined = False
        self._combined_mode = None  # "time" or "frequency"
        self._combined_sources = []  # list of source files used to combine

        self.lasso = None
        self.lasso_mask = None
        self.noise_reduced_data = None

        if not (self.theme and hasattr(self.theme, "set_view_mode")):
            self._apply_view_mode_styling()

        self.noise_reduced_original = None  # backup before lasso

        # Ensure project actions reflect initial state
        self._sync_project_actions()
        self.set_hardware_live_preview_enabled(self.use_hw_live_preview)
        self._refresh_analysis_summary_panel()
        QTimer.singleShot(300, self._prompt_recovery_if_needed)

    def _normalize_view_mode(self, mode) -> str:
        text = str(mode or "").strip().lower()
        if text in {"classic", "modern"}:
            return text
        return "modern"

    def _sync_view_mode_actions(self):
        if self.theme and hasattr(self.theme, "view_mode"):
            self._view_mode = self._normalize_view_mode(self.theme.view_mode())
        classic = getattr(self, "mode_action_classic", None)
        modern = getattr(self, "mode_action_modern", None)
        if classic is not None:
            blocked = classic.blockSignals(True)
            classic.setChecked(self._view_mode == "classic")
            classic.blockSignals(blocked)
        if modern is not None:
            blocked = modern.blockSignals(True)
            modern.setChecked(self._view_mode == "modern")
            modern.blockSignals(blocked)

    def set_view_mode(self, mode: str):
        normalized = self._normalize_view_mode(mode)
        if self.theme and hasattr(self.theme, "set_view_mode"):
            self.theme.set_view_mode(normalized)
            self._view_mode = self._normalize_view_mode(self.theme.view_mode())
        else:
            if normalized == getattr(self, "_view_mode", "modern"):
                return
            self._view_mode = normalized
            if hasattr(self, "_ui_settings") and self._ui_settings is not None:
                self._ui_settings.setValue("ui/view_mode", self._view_mode)
            self._apply_view_mode_styling()
        self._sync_view_mode_actions()

        status = self.statusBar()
        if status is not None:
            status.showMessage(f"UI mode: {self._view_mode.capitalize()}", 2500)

    def _apply_view_mode_styling(self):
        mode = getattr(self, "_view_mode", "modern")
        dark = self._is_dark_ui()
        if mode == "classic":
            main_qss = self._classic_main_qss()
            sidebar_qss = self._classic_sidebar_qss()
        else:
            main_qss = self._modern_main_qss(dark)
            sidebar_qss = self._modern_sidebar_qss(dark)

        self.setStyleSheet(main_qss)
        for widget in (
                getattr(self, "slider_group", None),
                getattr(self, "units_group_box", None),
                getattr(self, "graph_group", None),
                getattr(self, "analysis_summary_group", None),
        ):
            if widget is not None:
                widget.setStyleSheet(sidebar_qss)

    def _classic_main_qss(self) -> str:
        return """
        QLabel {
            font-size: 13px;
        }
        QGroupBox {
            font-weight: bold;
            font-size: 14px;
        }
        QPushButton#SidebarToggleButton {
            font-size: 10px;
            padding: 0px;
            min-width: 12px;
            max-width: 12px;
        }
        """

    def _classic_sidebar_qss(self) -> str:
        return """
        QGroupBox {
            font-weight: bold;
        }
        QLabel {
            font-size: 12px;
        }
        QLabel[section="true"] {
            font-weight: bold;
            color: #444;
            margin-top: 6px;
        }
        QLabel#SectionLabel {
            font-weight: bold;
            color: #555;
            margin-top: 8px;
            margin-bottom: 4px;
        }

        QLineEdit, QComboBox {
            min-height: 24px;
            padding: 4px 6px;
            font-size: 12px;
        }

        QSpinBox {
            min-height: 32px;
            min-width: 90px;
            padding-left: 6px;
            font-size: 12px;
        }

        QCheckBox {
            spacing: 6px;
            font-size: 12px;
            margin-top: 10px;
            margin-bottom: 10px;
            margin-right: 10px;
        }
        """

    def _modern_main_qss(self, dark: bool) -> str:
        if dark:
            window_bg = "#151b23"
            surface_bg = "#1d2631"
            border = "#334154"
            text = "#e8edf5"
            muted = "#a7b3c5"
            hover = "#2b384a"
            pressed = "#36485f"
            accent = "#58a6ff"
        else:
            window_bg = "#eef2f7"
            surface_bg = "#ffffff"
            border = "#d4dde9"
            text = "#1f2937"
            muted = "#6b7c93"
            hover = "#edf3ff"
            pressed = "#dde9ff"
            accent = "#0f7ae5"

        return f"""
        QMainWindow {{
            background-color: {window_bg};
        }}
        QMenuBar {{
            background-color: {surface_bg};
            border-bottom: 1px solid {border};
            padding: 2px 6px;
        }}
        QMenuBar::item {{
            color: {text};
            padding: 6px 10px;
            border-radius: 6px;
            background: transparent;
        }}
        QMenuBar::item:selected {{
            background: {hover};
        }}
        QMenu {{
            background-color: {surface_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 8px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 6px 16px 6px 10px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background: {hover};
        }}
        QMenu::separator {{
            height: 1px;
            background: {border};
            margin: 5px 8px;
        }}
        QToolBar {{
            background-color: {surface_bg};
            border: none;
            border-bottom: 1px solid {border};
            spacing: 6px;
            padding: 6px 8px;
        }}
        QToolButton {{
            border: 1px solid transparent;
            border-radius: 10px;
            padding: 4px;
            background: transparent;
        }}
        QToolButton:hover {{
            background: {hover};
            border-color: {border};
        }}
        QToolButton:pressed {{
            background: {pressed};
        }}
        QToolButton:disabled {{
            color: {muted};
        }}
        QStatusBar {{
            background-color: {surface_bg};
            border-top: 1px solid {border};
        }}
        QStatusBar QLabel {{
            color: {muted};
        }}
        QProgressBar {{
            border: 1px solid {border};
            border-radius: 6px;
            background-color: {window_bg};
            text-align: center;
            min-height: 18px;
        }}
        QProgressBar::chunk {{
            background-color: {accent};
            border-radius: 5px;
        }}
        QPushButton#SidebarToggleButton {{
            font-size: 10px;
            padding: 0px;
            min-width: 12px;
            max-width: 12px;
            border: 1px solid {border};
            border-radius: 6px;
            color: {text};
            background-color: {surface_bg};
        }}
        QPushButton#SidebarToggleButton:hover {{
            background-color: {hover};
        }}
        QPushButton#SidebarToggleButton:pressed {{
            background-color: {pressed};
        }}
        """

    def _modern_sidebar_qss(self, dark: bool) -> str:
        if dark:
            panel_bg = "#1d2631"
            field_bg = "#141b24"
            border = "#3a485d"
            text = "#e8edf5"
            muted = "#a7b3c5"
            accent = "#58a6ff"
        else:
            panel_bg = "#ffffff"
            field_bg = "#f7faff"
            border = "#d4dde9"
            text = "#1f2937"
            muted = "#6b7c93"
            accent = "#0f7ae5"

        return f"""
        QGroupBox {{
            font-weight: 600;
            font-size: 13px;
            border: 1px solid {border};
            border-radius: 10px;
            margin-top: 12px;
            padding: 10px;
            background-color: {panel_bg};
            color: {text};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: {text};
        }}
        QLabel {{
            font-size: 12px;
            color: {text};
        }}
        QLabel[section="true"] {{
            font-weight: 600;
            color: {muted};
            margin-top: 8px;
        }}
        QLabel#SectionLabel {{
            font-weight: 600;
            color: {muted};
            margin-top: 10px;
            margin-bottom: 4px;
        }}
        QLineEdit, QComboBox, QSpinBox {{
            min-height: 30px;
            border: 1px solid {border};
            border-radius: 8px;
            padding: 4px 8px;
            font-size: 12px;
            background-color: {field_bg};
            color: {text};
            selection-background-color: {accent};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QComboBox QAbstractItemView {{
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px;
            background-color: {panel_bg};
            color: {text};
            selection-background-color: {accent};
        }}
        QSpinBox {{
            min-width: 90px;
        }}
        QCheckBox, QRadioButton {{
            spacing: 6px;
            font-size: 12px;
            color: {text};
            margin-top: 6px;
            margin-bottom: 6px;
            margin-right: 8px;
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            border-radius: 3px;
            background: {border};
        }}
        QSlider::handle:horizontal {{
            width: 14px;
            margin: -5px 0;
            border-radius: 7px;
            background: {accent};
        }}
        """

    def _is_dark_ui(self) -> bool:
        # Prefer theme manager if available
        theme = getattr(self, "theme", None)
        if theme is not None:
            flag = getattr(theme, "is_dark", None)
            try:
                if callable(flag):
                    return bool(flag())
                return bool(flag)
            except Exception:
                pass

        # Fallback: infer from palette
        app = QApplication.instance()
        if not app:
            return False
        return app.palette().color(QPalette.Window).lightness() < 128

    def _icon(self, filename: str) -> QIcon:
        folder = "icons_dark" if self._is_dark_ui() else "icons"

        rels = [
            os.path.join("assets", folder, filename),
            os.path.join("assets", "icons", filename),  # fallback to light icons
        ]

        bases = []

        if getattr(sys, "frozen", False):
            bases.append(os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "Resources")))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(os.path.abspath(meipass))

        here = os.path.abspath(os.path.dirname(__file__))
        bases.extend([
            os.path.abspath(os.path.join(here, "..", "..")),  # project root (src/UI -> root)
            os.path.abspath(os.path.join(here, "..", "..", "..")),  # one more up (safe)
            os.path.abspath(os.getcwd()),
            os.path.abspath(os.path.join(os.getcwd(), "..")),
        ])

        seen = set()
        uniq_bases = []
        for b in bases:
            if b and b not in seen:
                seen.add(b)
                uniq_bases.append(b)

        for b in uniq_bases:
            for rel in rels:
                p = os.path.normpath(os.path.join(b, rel))
                if os.path.exists(p):
                    icon = self._load_icon_file(p)
                    if not icon.isNull():
                        return icon

        for rel in rels:
            try:
                p = resource_path(rel)
                if os.path.exists(p):
                    icon = self._load_icon_file(p)
                    if not icon.isNull():
                        return icon
            except Exception:
                pass

        # Avoid spamming the console with the same missing icon message
        if not hasattr(self, "_missing_icons"):
            self._missing_icons = set()
        if filename not in self._missing_icons:
            self._missing_icons.add(filename)
            print(f"⚠️ Icon not found: {filename}")

        return QIcon()

    def _load_icon_file(self, path: str) -> QIcon:
        icon = QIcon(path)
        if not icon.isNull():
            return icon

        if not str(path).lower().endswith(".svg"):
            return QIcon()

        try:
            from PySide6.QtSvg import QSvgRenderer
        except Exception:
            return QIcon()

        try:
            renderer = QSvgRenderer(path)
            if not renderer.isValid():
                return QIcon()

            size = renderer.defaultSize()
            w = max(48, int(size.width())) if size.isValid() else 64
            h = max(48, int(size.height())) if size.isValid() else 64

            pixmap = QPixmap(w, h)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()

            icon = QIcon(pixmap)
            return icon if not icon.isNull() else QIcon()
        except Exception:
            return QIcon()

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar", self)
        tb.setMovable(False)
        tb.setIconSize(QSize(36, 36))
        self.addToolBar(tb)

        # --- Actions (toolbar) ---
        self.tb_open = QAction(self._icon("open.svg"), "Open / Load", self)
        self.tb_open.setShortcut("Ctrl+O")
        self.tb_open.triggered.connect(self.load_file)
        tb.addAction(self.tb_open)

        self.tb_download = QAction(self._icon("download.svg"), "Download", self)
        self.tb_download.triggered.connect(self.launch_downloader)
        tb.addAction(self.tb_download)

        self.tb_export = QAction(self._icon("export.svg"), "Export", self)
        self.tb_export.setShortcut("Ctrl+E")
        self.tb_export.triggered.connect(self.export_figure)
        tb.addAction(self.tb_export)

        self.tb_export_fits = QAction(self._icon("export_fits.svg"), "Export as FITS", self)
        self.tb_export_fits.setShortcut("Ctrl+F")
        self.tb_export_fits.triggered.connect(self.export_to_fits)
        tb.addAction(self.tb_export_fits)

        tb.addSeparator()

        self.tb_undo = QAction(self._icon("undo.svg"), "Undo", self)
        self.tb_undo.triggered.connect(self.undo)
        tb.addAction(self.tb_undo)

        self.tb_redo = QAction(self._icon("redo.svg"), "Redo", self)
        self.tb_redo.triggered.connect(self.redo)
        tb.addAction(self.tb_redo)

        tb.addSeparator()

        self.tb_drift = QAction(self._icon("drift.svg"), "Estimate Drift Rate", self)
        self.tb_drift.triggered.connect(self.activate_drift_tool)
        tb.addAction(self.tb_drift)

        self.tb_isolate = QAction(self._icon("isolate.svg"), "Isolate Burst", self)
        self.tb_isolate.triggered.connect(self.activate_lasso)
        tb.addAction(self.tb_isolate)

        self.tb_max = QAction(self._icon("max.svg"), "Plot Maximum Intensities", self)
        self.tb_max.triggered.connect(self.plot_max_intensities)
        tb.addAction(self.tb_max)

        self.tb_zoom = QAction(self._icon("zoom.svg"), "Rectangular Zooming", self)
        self.tb_zoom.triggered.connect(self.rectangular_zoom)
        tb.addAction(self.tb_zoom)

        self.tb_lock = QAction(self._icon("lock.svg"), "Lock zooming and panning", self)
        self.tb_lock.triggered.connect(self.lock)
        tb.addAction(self.tb_lock)

        self.tb_unlock = QAction(self._icon("unlock.svg"), "Unlock zooming and panning", self)
        self.tb_unlock.triggered.connect(self.unlock)
        tb.addAction(self.tb_unlock)

        tb.addSeparator()

        self.tb_reset_sel = QAction(self._icon("reset_selection.svg"), "Reset Selection", self)
        self.tb_reset_sel.triggered.connect(self.reset_selection)
        tb.addAction(self.tb_reset_sel)

        self.tb_reset_all = QAction(self._icon("reset_all.svg"), "Reset All", self)
        self.tb_reset_all.triggered.connect(self.reset_all)
        tb.addAction(self.tb_reset_all)

        # Initial enable/disable states
        self._sync_toolbar_enabled_states()
        self._sync_nav_actions()

    def _sync_toolbar_enabled_states(self):
        has_file = getattr(self, "raw_data", None) is not None
        has_noise = getattr(self, "noise_reduced_data", None) is not None
        has_undo = len(getattr(self, "_undo_stack", [])) > 0
        has_redo = len(getattr(self, "_redo_stack", [])) > 0
        filename = getattr(self, "filename", "")

        can_reset_view = False
        try:
            cur_view = self._capture_view()
            home_view = getattr(self, "_home_view", None)
            if cur_view and home_view:
                can_reset_view = not self._views_close(cur_view, home_view)
        except Exception:
            can_reset_view = False

        # Always allowed
        self.tb_open.setEnabled(True)
        self.tb_download.setEnabled(True)

        # Needs a plot / filename
        self.tb_export.setEnabled(bool(filename))
        self.tb_export_fits.setEnabled(bool(filename))

        # Undo/redo availability
        self.tb_undo.setEnabled(has_undo)
        self.tb_redo.setEnabled(has_redo)
        act = getattr(self, "undo_action", None)
        if act is not None:
            act.setEnabled(has_undo)
        act = getattr(self, "redo_action", None)
        if act is not None:
            act.setEnabled(has_redo)
        act = getattr(self, "reset_to_raw_action", None)
        if act is not None:
            act.setEnabled(has_file)

        # Tools that require processed data
        self.tb_drift.setEnabled(has_noise)
        self.tb_isolate.setEnabled(has_noise)
        self.tb_max.setEnabled(has_noise)
        self.tb_reset_sel.setEnabled(has_noise or can_reset_view)
        self.tb_reset_all.setEnabled(has_file)
        self._sync_fits_view_actions()
        self._sync_nav_actions()

    def _show_plot_canvas(self):
        stack = getattr(self, "plot_stack", None)
        if stack is not None:
            stack.setCurrentWidget(self.canvas)
        self._noise_preview_active = False

    def _hardware_mode_enabled(self) -> bool:
        return bool(
            getattr(self, "use_hw_live_preview", False)
            and getattr(self, "accel_canvas", None) is not None
            and self.accel_canvas.is_available
        )

    def _show_accel_canvas(self):
        if not self._hardware_mode_enabled():
            return False
        accel = getattr(self, "accel_canvas", None)
        stack = getattr(self, "plot_stack", None)
        if accel is None or stack is None or not bool(accel.is_available):
            return False
        stack.setCurrentWidget(accel)
        self._noise_preview_active = True
        return True

    def _apply_accel_theme(self):
        accel = getattr(self, "accel_canvas", None)
        if accel is None or not bool(accel.is_available):
            return
        accel.set_dark(self._is_dark_ui())
        accel.set_time_mode(self.use_utc, self.ut_start_sec)

    def set_hardware_live_preview_enabled(self, enabled: bool):
        mpl_view = None
        try:
            ax = self.canvas.ax
            if ax is not None and ax.images is not None and len(ax.images) > 0:
                mpl_view = {"xlim": ax.get_xlim(), "ylim": ax.get_ylim()}
        except Exception:
            mpl_view = None

        accel_available = bool(getattr(self, "accel_canvas", None) and self.accel_canvas.is_available)
        self.use_hw_live_preview = bool(enabled) and accel_available

        act = getattr(self, "hw_live_preview_action", None)
        if act is not None and act.isChecked() != self.use_hw_live_preview:
            was_blocked = act.blockSignals(True)
            act.setChecked(self.use_hw_live_preview)
            act.blockSignals(was_blocked)

        if not self.use_hw_live_preview:
            self.noise_commit_timer.stop()
            self._noise_undo_pending = False
            self._noise_slider_drag_active = False
            self._sync_mpl_view_from_accel()
            self._show_plot_canvas()
            self.accel_canvas.stop_interaction_capture()
        else:
            self._apply_accel_theme()
            self.accel_canvas.set_navigation_locked(self.nav_locked)
            self._refresh_accel_plot(view=mpl_view, preserve_view=False)
            self._show_accel_canvas()
        self._sync_toolbar_enabled_states()

    def _sync_mpl_view_from_accel(self):
        if not self._hardware_mode_enabled():
            return
        try:
            view = self.accel_canvas.get_view()
            if not view:
                return
            self.canvas.ax.set_xlim(view["xlim"])
            self.canvas.ax.set_ylim(view["ylim"])
            self.canvas.draw_idle()
        except Exception:
            pass

    def _refresh_accel_plot(self, data=None, title=None, view=None, preserve_view=True):
        if not bool(getattr(self, "accel_canvas", None) and self.accel_canvas.is_available):
            return False
        if self.time is None or self.freqs is None or len(self.time) == 0 or len(self.freqs) == 0:
            return False

        if data is None:
            data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
        if data is None:
            return False

        if view is None and preserve_view:
            view = self.accel_canvas.get_view()

        display_data = self._intensity_for_display(data)
        self.current_display_data = display_data
        self._current_plot_source_data = np.asarray(data)

        plot_type = self._normalize_plot_type(title if title is not None else self.current_plot_type)
        if self.remove_titles:
            plot_title = ""
            x_label = ""
            y_label = ""
        else:
            plot_title = self.graph_title_override or self._default_graph_title(plot_type)
            x_label = "Time [UT]" if (self.use_utc and self.ut_start_sec is not None) else "Time [s]"
            y_label = "Frequency [MHz]"

        # Hardware canvas uses a Cartesian Y axis (increasing upward), so use
        # top-to-bottom frequency extent to keep the spectrum orientation
        # consistent with the previous display.
        extent = [0, self.time[-1], self.freqs[0], self.freqs[-1]]
        cbar_label = "" if self.remove_titles else ("Intensity [Digits]" if not self.use_db else "Intensity [dB]")
        tick_font_px = self.tick_font_px
        axis_label_font_px = self.axis_label_font_px
        title_font_px = self.title_font_px
        if getattr(self, "_hw_default_font_sizes_active", False):
            tick_font_px = self.HW_DEFAULT_TICK_FONT_PX
            axis_label_font_px = self.HW_DEFAULT_AXIS_FONT_PX
            title_font_px = self.HW_DEFAULT_TITLE_FONT_PX
        self.accel_canvas.set_text_style(
            font_family=self.graph_font_family,
            tick_font_px=tick_font_px,
            axis_label_font_px=axis_label_font_px,
            title_font_px=title_font_px,
            title_bold=self.title_bold,
            title_italic=self.title_italic,
            axis_bold=self.axis_bold,
            axis_italic=self.axis_italic,
            ticks_bold=self.ticks_bold,
            ticks_italic=self.ticks_italic,
        )
        self.accel_canvas.update_image(
            display_data,
            extent=extent,
            cmap=self.get_current_cmap(),
            title=plot_title,
            x_label=x_label,
            y_label=y_label,
            colorbar_label=cbar_label,
            view=view,
        )
        self.accel_canvas.set_time_mode(self.use_utc, self.ut_start_sec)
        return True

    def _apply_mpl_theme(self):
        """
        Ensure Matplotlib canvas (figure, axes, and colorbar) matches the current Qt theme.
        Call this AFTER you finish setting titles/labels/ticks.
        """
        if not hasattr(self, "canvas"):
            return

        fig = getattr(self.canvas, "figure", None) or getattr(self.canvas, "fig", None)
        ax = getattr(self.canvas, "ax", None)
        if fig is None or ax is None:
            return

        # 1) Preferred: use your theme manager if available
        if self.theme and hasattr(self.theme, "apply_mpl"):
            try:
                self.theme.apply_mpl(fig, ax, getattr(self, "current_colorbar", None))
            except Exception:
                pass

        # 2) Fallback: enforce readable colors from Qt palette (covers white bg / black text issues)
        app = QApplication.instance()
        if app:
            pal = app.palette()
            win_bg = pal.color(QPalette.Window).name()
            base_bg = pal.color(QPalette.Base).name()
            fg = pal.color(QPalette.WindowText).name()
            mid = pal.color(QPalette.Mid).name()

            fig.set_facecolor(win_bg)
            ax.set_facecolor(base_bg)

            for spine in ax.spines.values():
                spine.set_color(fg)

            ax.tick_params(axis="both", colors=fg, which="both")
            ax.xaxis.label.set_color(fg)
            ax.yaxis.label.set_color(fg)
            ax.title.set_color(fg)

            # If you use grids elsewhere, keep their color readable without forcing grid on
            ax.grid(False)
            ax.set_axisbelow(True)

            cbar = getattr(self, "current_colorbar", None)
            if cbar is not None:
                cax = cbar.ax
                cax.set_facecolor(base_bg)
                cax.tick_params(colors=fg)
                cax.yaxis.label.set_color(fg)
                for spine in cax.spines.values():
                    spine.set_color(fg)
                try:
                    cbar.outline.set_edgecolor(mid)
                except Exception:
                    pass

        self.canvas.draw_idle()

    def _refresh_toolbar_icons(self):
        # Only run after toolbar actions exist
        for attr, fname in (
                ("tb_open", "open.svg"),
                ("tb_export", "export.svg"),
                ("tb_export_fits","export_fits.svg"),
                ("tb_undo", "undo.svg"),
                ("tb_redo", "redo.svg"),
                ("tb_download", "download.svg"),
                ("tb_drift", "drift.svg"),
                ("tb_isolate", "isolate.svg"),
                ("tb_max", "max.svg"),
                ("tb_zoom", "zoom.svg"),
                ("tb_lock", "lock.svg"),
                ("tb_unlock", "unlock.svg"),
                ("tb_reset_sel", "reset_selection.svg"),
                ("tb_reset_all", "reset_all.svg"),
        ):
            act = getattr(self, attr, None)
            if act is not None:
                act.setIcon(self._icon(fname))

    def _on_theme_changed(self, dark: bool):
        self._sync_view_mode_actions()
        self._refresh_toolbar_icons()

        # If you already added MPL theme syncing earlier, keep it too:
        if hasattr(self, "_apply_mpl_theme"):
            self._apply_mpl_theme()
        self._apply_accel_theme()

    def toggle_left_sidebar(self):
        self._set_sidebar_collapsed(not bool(getattr(self, "_sidebar_collapsed", False)))

    def _set_sidebar_collapsed(self, collapsed: bool):
        self._sidebar_collapsed = bool(collapsed)
        if getattr(self, "side_scroll", None) is None or getattr(self, "sidebar_toggle_btn", None) is None:
            return

        self.side_scroll.setVisible(not self._sidebar_collapsed)
        if self._sidebar_collapsed:
            self.sidebar_toggle_btn.setText("▶")
            self.sidebar_toggle_btn.setToolTip("Expand sidebar")
        else:
            self.sidebar_toggle_btn.setText("◀")
            self.sidebar_toggle_btn.setToolTip("Collapse sidebar")

        layout = getattr(self, "_main_layout", None)
        if layout is not None:
            layout.setSpacing(0)

    def _normalize_plot_type(self, title: str | None) -> str:
        txt = str(title or "").strip()
        if not txt:
            return "Raw"

        lowered = txt.lower()
        raw_aliases = {
            "raw",
            "raw data",
            "dynamic spectrum",
            "combined time",
            "combined time plot",
            "raw data (combined frequency)",
        }
        if lowered in raw_aliases:
            return "Raw"

        if lowered in {"background subtracted", "background-subtracted"}:
            return "Background Subtracted"

        return txt

    def set_max_auto_clean_isolated_enabled(self, enabled: bool):
        self._max_auto_clean_isolated = bool(enabled)
        try:
            self._ui_settings.setValue("processing/max_auto_clean_isolated", self._max_auto_clean_isolated)
        except Exception:
            pass
        state = "enabled" if self._max_auto_clean_isolated else "disabled"
        self.statusBar().showMessage(f"Isolated max-intensity auto-clean {state}.", 3000)

    def _default_graph_title(self, plot_type: str | None = None) -> str:
        normalized = self._normalize_plot_type(plot_type if plot_type is not None else self.current_plot_type)
        base = str(getattr(self, "filename", "") or "").strip() or "Untitled"

        return f"{base}-{normalized}"

    def _current_graph_title_for_export(self) -> str:
        ax = getattr(getattr(self, "canvas", None), "ax", None)
        if ax is not None:
            try:
                live_title = str(ax.get_title() or "").strip()
                if live_title:
                    return live_title
            except Exception:
                pass

        override = str(getattr(self, "graph_title_override", "") or "").strip()
        if override:
            return override

        return self._default_graph_title(self.current_plot_type)

    def _sanitize_export_stem(self, text: str) -> str:
        stem = str(text or "").strip()
        if not stem:
            stem = "export"

        stem = re.sub(r"\s+", " ", stem)
        stem = re.sub(r"[\\\\/:*?\"<>|]+", "_", stem)
        stem = stem.strip(" .")
        return stem or "export"

    def apply_graph_properties_live(self, *_):
        """
        Apply graph styling changes (title, font family, font sizes, bold/italic flags)
        to the CURRENT plot without rebuilding the image.
        """

        ax = getattr(self.canvas, "ax", None)
        if ax is None:
            return

        # Must have an image already
        if not ax.images or len(ax.images) == 0:
            return

        # -----------------------------
        # 1) READ UI STATE FIRST
        # -----------------------------
        self.remove_titles = self.remove_titles_chk.isChecked()

        self.title_bold = self.title_bold_chk.isChecked()
        self.title_italic = self.title_italic_chk.isChecked()

        self.axis_bold = self.axis_bold_chk.isChecked()
        self.axis_italic = self.axis_italic_chk.isChecked()

        self.ticks_bold = self.ticks_bold_chk.isChecked()
        self.ticks_italic = self.ticks_italic_chk.isChecked()

        self.graph_title_override = self.title_edit.text().strip()

        font_choice = self.font_combo.currentText()
        self.graph_font_family = "" if font_choice == "Default" else font_choice

        self.tick_font_px = int(self.tick_font_spin.value())
        self.axis_label_font_px = int(self.axis_font_spin.value())
        self.title_font_px = int(self.title_font_spin.value())

        # Helpers
        def _weight(bold: bool) -> str:
            return "bold" if bold else "normal"

        def _style(italic: bool) -> str:
            return "italic" if italic else "normal"

        fontfam = self.graph_font_family if self.graph_font_family else None

        # -----------------------------
        # 2) APPLY TITLES / LABELS
        # -----------------------------
        if self.remove_titles:
            ax.set_title("")
            ax.set_xlabel("")
            ax.set_ylabel("")
        else:
            # Default title if custom is empty
            title_text = self.graph_title_override or self._default_graph_title(self.current_plot_type)

            ax.set_title(
                title_text,
                fontsize=self.title_font_px,
                fontfamily=fontfam,
                fontweight=_weight(self.title_bold),
                fontstyle=_style(self.title_italic),
            )

            # Always set axis labels explicitly so toggles apply immediately
            xlab = "Time [UT]" if (self.use_utc and self.ut_start_sec is not None) else "Time [s]"
            ylab = "Frequency [MHz]"

            ax.set_xlabel(
                xlab,
                fontsize=self.axis_label_font_px,
                fontfamily=fontfam,
                fontweight=_weight(self.axis_bold),
                fontstyle=_style(self.axis_italic),
            )
            ax.set_ylabel(
                ylab,
                fontsize=self.axis_label_font_px,
                fontfamily=fontfam,
                fontweight=_weight(self.axis_bold),
                fontstyle=_style(self.axis_italic),
            )

        # -----------------------------
        # 3) APPLY TICK LABEL STYLE
        # -----------------------------
        for lbl in ax.get_xticklabels():
            lbl.set_fontsize(self.tick_font_px)
            lbl.set_fontweight(_weight(self.ticks_bold))
            lbl.set_fontstyle(_style(self.ticks_italic))
            if fontfam:
                lbl.set_fontfamily(fontfam)

        for lbl in ax.get_yticklabels():
            lbl.set_fontsize(self.tick_font_px)
            lbl.set_fontweight(_weight(self.ticks_bold))
            lbl.set_fontstyle(_style(self.ticks_italic))
            if fontfam:
                lbl.set_fontfamily(fontfam)

        # Colorbar style (ticks + title)
        if self.current_colorbar is not None:
            try:
                cax = self.current_colorbar.ax
                fontfam = (self.graph_font_family if self.graph_font_family else None)

                # ---- ticks ----
                cax.tick_params(labelsize=self.tick_font_px)
                for lbl in cax.get_yticklabels():
                    lbl.set_fontsize(self.tick_font_px)
                    lbl.set_fontweight(_weight(self.ticks_bold))
                    lbl.set_fontstyle(_style(self.ticks_italic))
                    if fontfam:
                        lbl.set_fontfamily(fontfam)

                # ---- title/label ----
                if self.remove_titles:
                    # Hide the colorbar label, but DO NOT erase the stored text
                    self.current_colorbar.set_label("")
                    cax.set_ylabel("")  # extra safety for some backends
                    cax.yaxis.label.set_text("")
                else:
                    # Restore label text + style
                    label_text = getattr(self, "_colorbar_label_text", "")
                    if not label_text:
                        label_text = cax.get_ylabel()  # fallback

                    self.current_colorbar.set_label(
                        label_text,
                        fontsize=self.axis_label_font_px,
                        fontfamily=fontfam,
                        fontweight=_weight(self.axis_bold),
                        fontstyle=_style(self.axis_italic),
                    )

                    # Force styling on the underlying label object too
                    ylab = cax.yaxis.label
                    ylab.set_fontsize(self.axis_label_font_px)
                    ylab.set_fontweight(_weight(self.axis_bold))
                    ylab.set_fontstyle(_style(self.axis_italic))
                    if fontfam:
                        ylab.set_fontfamily(fontfam)

            except Exception:
                pass

        # -----------------------------
        # 4) UI: disable title inputs when Remove Titles is on
        # -----------------------------
        disable = self.remove_titles
        self.title_edit.setEnabled(not disable)
        self.title_bold_chk.setEnabled(not disable)
        self.title_italic_chk.setEnabled(not disable)
        self.axis_bold_chk.setEnabled(not disable)
        self.axis_italic_chk.setEnabled(not disable)

        self.canvas.draw_idle()
        self._refresh_accel_plot(preserve_view=True)

    def _on_tick_font_spin_changed(self, _value):
        self._hw_default_font_sizes_active = False
        self.apply_graph_properties_live()

    def _on_axis_font_spin_changed(self, _value):
        self._hw_default_font_sizes_active = False
        self.apply_graph_properties_live()

    def _on_title_font_spin_changed(self, _value):
        self._hw_default_font_sizes_active = False
        self.apply_graph_properties_live()

    def _analysis_source_context(self) -> dict:
        shape = None
        if getattr(self, "raw_data", None) is not None:
            try:
                shape = [int(self.raw_data.shape[0]), int(self.raw_data.shape[1])]
            except Exception:
                shape = None
        return {
            "filename": str(getattr(self, "filename", "") or ""),
            "is_combined": bool(getattr(self, "_is_combined", False)),
            "combined_mode": getattr(self, "_combined_mode", None),
            "combined_sources": list(getattr(self, "_combined_sources", []) or []),
            "shape": shape,
        }

    def _session_to_legacy_max_state(self, session: dict | None) -> dict | None:
        normalized = normalize_analysis_session(session)
        if normalized is None:
            return None
        max_block = dict(normalized.get("max_intensity") or {})
        t = max_block.get("time_channels")
        f = max_block.get("freqs")
        if t is None or f is None:
            return None
        analyzer = dict(normalized.get("analyzer") or {})
        return {
            "time_channels": np.asarray(t, dtype=float),
            "freqs": np.asarray(f, dtype=float),
            "fundamental": bool(max_block.get("fundamental", True)),
            "harmonic": bool(max_block.get("harmonic", False)),
            "analyzer": analyzer,
            "source_filename": str((normalized.get("source") or {}).get("filename") or self.filename or ""),
        }

    def _dialog_alive(self, dialog) -> bool:
        if dialog is None:
            return False
        try:
            _ = dialog.windowTitle()
            return True
        except Exception:
            return False

    def _refresh_analysis_summary_panel(self):
        lbl = getattr(self, "analysis_summary_label", None)
        if lbl is None:
            return

        session = normalize_analysis_session(getattr(self, "_analysis_session", None))
        if session is None:
            lbl.setText("No analysis session loaded.")
            return

        analyzer = dict(session.get("analyzer") or {})
        fit = dict(analyzer.get("fit_params") or {})
        shock = dict(analyzer.get("shock_summary") or {})
        fold = int(analyzer.get("fold", shock.get("fold", 1) or 1))

        lines = ["Session restored and synced."]
        if fit.get("a") is not None and fit.get("b") is not None:
            try:
                lines.append(f"Fit: f(t) = {float(fit['a']):.2f} * t^{float(fit['b']):.2f}")
            except Exception:
                pass
        if fit.get("r2") is not None:
            try:
                lines.append(f"R2: {float(fit['r2']):.4f}")
            except Exception:
                pass
        lines.append(f"Fold: {fold}")
        if shock.get("avg_shock_speed_km_s") is not None:
            try:
                lines.append(f"Avg shock speed: {float(shock['avg_shock_speed_km_s']):.2f} km/s")
            except Exception:
                pass
        if shock.get("avg_shock_height_rs") is not None:
            try:
                lines.append(f"Avg shock height: {float(shock['avg_shock_height_rs']):.3f} Rs")
            except Exception:
                pass
        lbl.setText("\n".join(lines))

    def _close_analysis_windows(self):
        if self._dialog_alive(getattr(self, "_analyze_dialog", None)):
            try:
                self._analyze_dialog.close()
            except Exception:
                pass
        self._analyze_dialog = None

        if self._dialog_alive(getattr(self, "_max_intensity_dialog", None)):
            try:
                self._max_intensity_dialog.close()
            except Exception:
                pass
        self._max_intensity_dialog = None

    def _clear_analysis_session_state(self, *, close_windows: bool = True):
        if close_windows:
            self._close_analysis_windows()
        self._analysis_session = None
        self._max_intensity_state = None
        self._pending_analysis_restore = {"open_max": False, "open_analyzer": False, "warning": ""}
        self._refresh_analysis_summary_panel()

    def _current_dynamic_spectrum_source_data(self) -> np.ndarray | None:
        data = getattr(self, "_current_plot_source_data", None)
        if data is not None:
            arr = np.asarray(data)
            if arr.ndim == 2 and arr.shape[1] > 0:
                return arr

        if self.current_display_data is not None:
            try:
                arr = np.asarray(self.current_display_data, dtype=float)
                if arr.ndim == 2 and arr.shape[1] > 0:
                    if self.use_db:
                        cold_digits, _ = self._db_hot_cold_digits()
                        return (arr / float(self.DB_SCALE)) + cold_digits
                    return arr
            except Exception:
                pass

        if self.noise_reduced_data is not None:
            arr = np.asarray(self.noise_reduced_data)
            if arr.ndim == 2 and arr.shape[1] > 0:
                return arr

        if self.raw_data is not None:
            arr = np.asarray(self.raw_data)
            if arr.ndim == 2 and arr.shape[1] > 0:
                return arr

        return None

    def _is_isolated_burst_plot(self) -> bool:
        plot_type = self._normalize_plot_type(getattr(self, "current_plot_type", ""))
        txt = str(plot_type or "").strip().lower()
        return txt in {"isolated burst", "isolated"}

    def _auto_filter_isolated_maxima(
        self,
        time_channels: np.ndarray,
        max_freqs: np.ndarray,
        source_data: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, int]:
        vals = np.nanmax(np.asarray(source_data, dtype=float), axis=0)
        finite = np.isfinite(vals)
        positive = vals[finite & (vals > 0)]

        if positive.size > 0:
            base = float(np.nanpercentile(positive, 10))
            threshold = max(1e-12, base * 0.15)
            valid = finite & (vals > threshold)
            if int(np.count_nonzero(valid)) < 3:
                valid = finite & (vals > 0)
        else:
            valid = finite & (vals > 0)

        if int(np.count_nonzero(valid)) < 3:
            return time_channels, max_freqs, 0

        removed = int(time_channels.size - int(np.count_nonzero(valid)))
        return time_channels[valid], max_freqs[valid], max(0, removed)

    def _build_analysis_seed_from_current_data(self) -> dict | None:
        if self.freqs is None:
            return None
        data = self._current_dynamic_spectrum_source_data()
        if data is None:
            return None
        data = np.asarray(data)
        if data.ndim != 2 or data.shape[1] == 0:
            return None
        nx = int(data.shape[1])
        time_channels = np.arange(nx, dtype=float)
        freqs = np.asarray(self.freqs, dtype=float)
        if freqs.ndim != 1 or len(freqs) == 0:
            return None
        peak_indices = np.argmax(data, axis=0)
        max_freqs = freqs[peak_indices]
        auto_removed = 0
        auto_outlier_cleaned = False

        if self._is_isolated_burst_plot() and bool(getattr(self, "_max_auto_clean_isolated", True)):
            time_channels, max_freqs, auto_removed = self._auto_filter_isolated_maxima(
                time_channels,
                max_freqs,
                data,
            )
            auto_outlier_cleaned = bool(auto_removed > 0)

        payload = {
            "source": self._analysis_source_context(),
            "max_intensity": {
                "time_channels": time_channels,
                "freqs": max_freqs,
                "fundamental": True,
                "harmonic": False,
            },
            "analyzer": {"fold": 1},
            "ui": {
                "restore_max_window": True,
                "restore_analyzer_window": False,
                "auto_outlier_cleaned": bool(auto_outlier_cleaned),
                "auto_removed_count": int(auto_removed),
            },
        }
        return normalize_analysis_session(payload)

    def _analysis_session_with_context(self, payload: dict | None) -> dict | None:
        normalized = normalize_analysis_session(payload)
        if normalized is None:
            return None
        normalized["source"] = self._analysis_source_context()
        return normalize_analysis_session(normalized)

    def _best_available_analysis_session(self) -> dict | None:
        session = self._analysis_session_with_context(getattr(self, "_analysis_session", None))
        if session is not None:
            return session

        legacy = getattr(self, "_max_intensity_state", None)
        if isinstance(legacy, dict):
            session = normalize_analysis_session(
                {
                    "source": self._analysis_source_context(),
                    "max_intensity": {
                        "time_channels": legacy.get("time_channels"),
                        "freqs": legacy.get("freqs"),
                        "fundamental": bool(legacy.get("fundamental", True)),
                        "harmonic": bool(legacy.get("harmonic", False)),
                    },
                    "analyzer": dict(legacy.get("analyzer") or {}),
                    "ui": {"restore_max_window": True, "restore_analyzer_window": bool(legacy.get("analyzer"))},
                }
            )
            if session is not None:
                return session
        return None

    def _open_or_focus_max_dialog(
        self,
        session: dict | None = None,
        auto_open_analyzer: bool = False,
        *,
        prefer_current_plot: bool = False,
    ):
        if not self._dialog_alive(getattr(self, "_max_intensity_dialog", None)):
            self._max_intensity_dialog = None
        if not self._dialog_alive(getattr(self, "_analyze_dialog", None)):
            self._analyze_dialog = None

        candidate = None
        if prefer_current_plot:
            candidate = self._build_analysis_seed_from_current_data()

        if candidate is None:
            candidate = self._analysis_session_with_context(session) or self._analysis_session_with_context(
                getattr(self, "_analysis_session", None)
            )
        if candidate is None:
            candidate = self._build_analysis_seed_from_current_data()

        if candidate is None:
            self.statusBar().showMessage("Analysis window requires a plotted dynamic spectrum.", 4000)
            return None

        ui_block = dict(candidate.get("ui") or {})
        auto_outlier_mode = bool(
            (prefer_current_plot and self._is_isolated_burst_plot() and bool(getattr(self, "_max_auto_clean_isolated", True)))
            or ui_block.get("auto_outlier_cleaned", False)
        )

        max_block = dict(candidate.get("max_intensity") or {})
        time_channels = max_block.get("time_channels")
        freqs = max_block.get("freqs")
        if time_channels is None or freqs is None:
            self.statusBar().showMessage("Analysis session has no max-intensity vectors.", 4000)
            return None

        if self._max_intensity_dialog is None:
            dialog = MaxIntensityPlotDialog(
                time_channels,
                freqs,
                self.filename,
                parent=self,
                session=candidate,
                auto_outlier_mode=auto_outlier_mode,
            )
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.sessionChanged.connect(lambda payload: self._on_analysis_session_changed(payload, source="max"))
            dialog.requestOpenAnalyzer.connect(lambda payload: self._open_or_focus_analyzer_dialog(payload))
            dialog.finished.connect(lambda _code: setattr(self, "_max_intensity_dialog", None))
            self._max_intensity_dialog = dialog
            dialog.show()
        else:
            try:
                self._max_intensity_dialog.set_auto_outlier_mode(auto_outlier_mode)
                self._max_intensity_dialog.restore_session(candidate, emit_change=False)
            except Exception:
                pass
            self._max_intensity_dialog.show()

        self._max_intensity_dialog.raise_()
        self._max_intensity_dialog.activateWindow()
        self._on_analysis_session_changed(candidate, source="max", log_message=False, mark_dirty=False)
        if auto_outlier_mode:
            removed = int((ui_block.get("auto_removed_count", 0) or 0))
            if removed > 0:
                self.statusBar().showMessage(
                    f"Auto-cleaned isolated burst maxima: removed {removed} outlier columns.",
                    4500,
                )
            else:
                self.statusBar().showMessage("Auto-cleaned isolated burst maxima.", 3000)

        if auto_open_analyzer:
            QTimer.singleShot(0, lambda: self._open_or_focus_analyzer_dialog(candidate))
        return self._max_intensity_dialog

    def _open_or_focus_analyzer_dialog(self, session: dict | None = None):
        if not self._dialog_alive(getattr(self, "_analyze_dialog", None)):
            self._analyze_dialog = None

        candidate = self._analysis_session_with_context(session) or self._analysis_session_with_context(
            getattr(self, "_analysis_session", None)
        )
        if candidate is None:
            candidate = self._build_analysis_seed_from_current_data()
        if candidate is None:
            self.statusBar().showMessage("Analyzer requires max-intensity data first.", 4000)
            return None

        max_block = dict(candidate.get("max_intensity") or {})
        analyzer_state = dict(candidate.get("analyzer") or {})
        time_channels = max_block.get("time_channels")
        freqs = max_block.get("freqs")
        if time_channels is None or freqs is None:
            self.statusBar().showMessage("Analyzer requires max-intensity vectors.", 4000)
            return None

        if self._analyze_dialog is None:
            dialog = AnalyzeDialog(
                time_channels,
                freqs,
                self.filename,
                fundamental=bool(max_block.get("fundamental", True)),
                harmonic=bool(max_block.get("harmonic", False)),
                parent=self,
                session={"max_intensity": max_block, "analyzer": analyzer_state},
            )
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.sessionChanged.connect(lambda payload: self._on_analysis_session_changed(payload, source="analyzer"))
            dialog.finished.connect(lambda _code: setattr(self, "_analyze_dialog", None))
            self._analyze_dialog = dialog
            dialog.show()
        else:
            try:
                self._analyze_dialog.restore_session({"max_intensity": max_block, "analyzer": analyzer_state}, emit_change=False)
            except Exception:
                pass
            self._analyze_dialog.show()

        self._analyze_dialog.raise_()
        self._analyze_dialog.activateWindow()
        return self._analyze_dialog

    def _on_analysis_session_changed(
        self,
        payload: dict | None,
        *,
        source: str = "",
        log_message: bool = True,
        mark_dirty: bool = True,
    ):
        if self._analysis_sync_guard:
            return

        session = self._analysis_session_with_context(payload)
        if session is None:
            return

        self._analysis_sync_guard = True
        try:
            self._analysis_session = session
            self._max_intensity_state = self._session_to_legacy_max_state(session)
            self._refresh_analysis_summary_panel()

            if source == "analyzer" and self._dialog_alive(getattr(self, "_max_intensity_dialog", None)):
                try:
                    self._max_intensity_dialog.restore_session(session, emit_change=False)
                except Exception:
                    pass
            elif source == "max" and self._dialog_alive(getattr(self, "_analyze_dialog", None)):
                try:
                    self._analyze_dialog.restore_session(session, emit_change=False)
                except Exception:
                    pass
        finally:
            self._analysis_sync_guard = False

        if mark_dirty:
            self._mark_project_dirty()
        if log_message:
            source_tag = source or "session"
            self._log_operation(f"Updated analysis session ({source_tag}).")

    def _apply_pending_analysis_restore(self):
        pending = dict(getattr(self, "_pending_analysis_restore", {}) or {})
        warning = str(pending.get("warning", "") or "").strip()
        open_max = bool(pending.get("open_max", False))
        open_analyzer = bool(pending.get("open_analyzer", False))

        if warning:
            self.statusBar().showMessage(warning, 5500)
        if open_max:
            self._open_or_focus_max_dialog(self._analysis_session, auto_open_analyzer=open_analyzer)
        elif open_analyzer:
            self._open_or_focus_analyzer_dialog(self._analysis_session)

        self._pending_analysis_restore = {"open_max": False, "open_analyzer": False, "warning": ""}

    def open_restored_analysis_windows(self):
        session = self._best_available_analysis_session()
        if session is None:
            self.statusBar().showMessage("No saved analysis session is available.", 4000)
            return
        self._open_or_focus_max_dialog(session, auto_open_analyzer=True)

    def _reset_feature_state_for_new_data(self):
        self._clear_analysis_session_state(close_windows=True)
        self._reset_annotation_mode()
        self._rfi_preview_data = None
        self._rfi_preview_masked = []
        self._rfi_config = rfi_config_dict(
            enabled=True,
            kernel_time=3,
            kernel_freq=3,
            channel_z_threshold=6.0,
            percentile_clip=99.5,
            masked_channel_indices=[],
            applied=False,
        )
        if self._rfi_dialog is not None:
            try:
                self._rfi_dialog.set_masked_channels([])
            except Exception:
                pass

        self._annotations = []
        self._annotations_visible = True
        self._annotation_style_defaults = {"color": "#00d4ff", "line_width": 1.5}
        self._active_preset_snapshot = None
        self._render_annotations()

    def _reset_runtime_state_for_loaded_data(self):
        self.noise_reduced_data = None
        self.noise_reduced_original = None
        self.lasso_mask = None
        self.noise_vmin = None
        self.noise_vmax = None
        self.current_display_data = None
        self._current_plot_source_data = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._reset_feature_state_for_new_data()

    def _apply_loaded_dataset(
        self,
        *,
        data,
        freqs,
        time,
        filename: str,
        header0=None,
        source_path: str | None = None,
        ut_start_sec: float | None = None,
        combined_mode: str | None = None,
        combined_sources: list[str] | None = None,
        plot_title: str = "Raw",
        log_message: str | None = None,
    ):
        self.raw_data = data
        self._invalidate_noise_cache()
        self.freqs = freqs
        self.time = time
        self.filename = str(filename or "")

        self._fits_header0 = header0.copy() if header0 is not None else None
        self._fits_source_path = source_path

        self._is_combined = bool(combined_mode)
        self._combined_mode = combined_mode
        self._combined_sources = list(combined_sources or [])

        if ut_start_sec is None and header0 is not None:
            ut_start_sec = extract_ut_start_sec(header0)
        self.ut_start_sec = ut_start_sec

        self._reset_runtime_state_for_loaded_data()
        self.plot_data(self.raw_data, title=plot_title)

        self._project_path = None
        self._mark_project_dirty()
        if log_message:
            self._log_operation(str(log_message))

    def load_file(self):
        if not self._maybe_prompt_save_dirty():
            return
        initial_dir = os.path.dirname(self.filename) if self.filename else ""
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.ExistingFiles)
        dialog.setNameFilter("FITS files (*.fit *.fits *.fit.gz *.fits.gz)")
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)

        if dialog.exec():
            file_paths = dialog.selectedFiles()
        else:
            return

        if not file_paths:
            return

        if len(file_paths) == 1:
            file_path = file_paths[0]
            res = load_callisto_fits(file_path, memmap=False)
            self._apply_loaded_dataset(
                data=res.data,
                freqs=res.freqs,
                time=res.time,
                filename=os.path.basename(file_path),
                header0=res.header0,
                source_path=file_path,
                ut_start_sec=extract_ut_start_sec(res.header0),
                combined_mode=None,
                combined_sources=[],
                plot_title="Raw",
                log_message=f"Loaded FITS file: {os.path.basename(file_path)}",
            )
            return

        from src.Backend.burst_processor import (
            are_time_combinable,
            are_frequency_combinable,
            combine_time,
            combine_frequency,
        )

        try:
            if are_time_combinable(file_paths):
                combined = combine_time(file_paths)
            elif are_frequency_combinable(file_paths):
                combined = combine_frequency(file_paths)
            else:
                error_msg = (
                    "The selected FITS files cannot be combined.\n\n"
                    "Valid combinations are:\n"
                    "1. Frequency Combine:\n"
                    "   • Same station\n"
                    "   • Same date\n"
                    "   • Same timestamp (HHMMSS)\n"
                    "   • Different receiver IDs\n"
                    "   • Matching time arrays\n\n"
                    "2. Time Combine:\n"
                    "   • Same station\n"
                    "   • Same receiver ID\n"
                    "   • Same date\n"
                    "   • Different timestamps (continuous time segments)\n"
                    "   • Matching frequency arrays\n\n"
                    "Your selection does not match either rule.\n"
                    "Please choose files that follow one of the above patterns."
                )
                QMessageBox.warning(self, "Invalid Combination Selection", error_msg)
                return

            self.load_combined_into_main(combined)
            self.statusBar().showMessage(f"Loaded {len(file_paths)} files (combined)", 5000)
            self._log_operation(f"Loaded combined FITS set ({len(file_paths)} files).")
        except Exception as e:
            QMessageBox.critical(self, "Combine Error", f"An error occurred while combining files:\n{e}")
            return

    def _db_hot_cold_digits(self) -> tuple[float, float]:
        # For Y-factor style conversion use Icold=vmin and Ihot=vmax from scrollbars.
        cold = float(self.lower_slider.value())
        hot = float(self.upper_slider.value())
        if cold > hot:
            cold, hot = hot, cold
        return cold, hot

    def _intensity_for_display(self, data):
        if data is None:
            return None
        if not self.use_db:
            return data
        cold_digits, _ = self._db_hot_cold_digits()
        return (np.asarray(data, dtype=float) - cold_digits) * self.DB_SCALE

    def _intensity_range_for_display(self, vmin, vmax):
        if vmin is None or vmax is None:
            return vmin, vmax
        if not self.use_db:
            return vmin, vmax
        cold_digits, _ = self._db_hot_cold_digits()
        return ((vmin - cold_digits) * self.DB_SCALE, (vmax - cold_digits) * self.DB_SCALE)

    def update_units(self):
        self.set_units_mode(bool(self.units_db_radio.isChecked()))

    def set_units_mode(self, use_db: bool):
        self.use_db = bool(use_db)

        if self.raw_data is None:
            return

        self._mark_project_dirty()

        # Choose which data to replot
        data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data

        # Replot with new unit selection
        self.plot_data(data, title=self.current_plot_type, keep_view=True)

    def combine_frequency_files(self, file_paths):
        file_paths = sorted(file_paths)
        data_list = []
        freq_list = []
        time_array = None

        for path in file_paths:
            res = load_callisto_fits(path, memmap=False)
            data_list.append(res.data)
            freq_list.append(res.freqs)
            time_array = res.time

        combined_data = np.concatenate(data_list, axis=0)
        combined_freqs = np.concatenate(freq_list)
        combined_time = time_array

        return combined_data, combined_freqs, combined_time

    def combine_time_files(self, file_paths):
        file_paths = sorted(file_paths)
        data_list = []
        time_list = []
        freqs = None

        for path in file_paths:
            res = load_callisto_fits(path, memmap=False)
            data_list.append(res.data)
            time_list.append(res.time)
            freqs = res.freqs

        combined_data = np.concatenate(data_list, axis=1)

        fixed_time = []
        offset = 0

        for t in time_list:
            t = np.array(t)
            fixed_time.append(t + offset)
            offset += t[-1]

        combined_time = np.concatenate(fixed_time)

        combined_freqs = freqs
        return combined_data, combined_freqs, combined_time

    def load_fits_into_main(self, file_path):
        res = load_callisto_fits(file_path, memmap=False)
        filename = os.path.basename(file_path)
        self._apply_loaded_dataset(
            data=res.data,
            freqs=res.freqs,
            time=res.time,
            filename=filename,
            header0=res.header0,
            source_path=file_path,
            ut_start_sec=extract_ut_start_sec(res.header0),
            combined_mode=None,
            combined_sources=[],
            plot_title="Raw",
            log_message=f"Loaded FITS into main: {filename}",
        )

    def load_combined_into_main(self, combined):
        data = combined["data"]
        freqs = combined["freqs"]
        time = combined["time"]
        filename = combined.get("filename", "Combined")
        combine_type = combined.get("combine_type", None)
        sources = combined.get("sources", [])

        hdr0 = combined.get("header0", None)
        if hdr0 is None:
            hdr0 = build_combined_header(
                None,
                mode=combine_type or "combined",
                sources=sources,
                data_shape=getattr(data, "shape", (0, 0)),
                freqs=freqs,
                time=time,
            )
        self._apply_loaded_dataset(
            data=data,
            freqs=freqs,
            time=time,
            filename=filename,
            header0=hdr0,
            source_path=None,
            ut_start_sec=combined.get("ut_start_sec", None),
            combined_mode=combine_type,
            combined_sources=sources,
            plot_title="Raw",
            log_message="Loaded combined dataset into main window.",
        )

    def schedule_noise_update(self):
        if self.raw_data is None:
            return
        self.noise_smooth_timer.start()

    def _on_noise_slider_pressed(self):
        self._noise_slider_drag_active = True

    def _on_noise_slider_released(self):
        self._noise_slider_drag_active = False
        if self.raw_data is None:
            return
        self.noise_smooth_timer.stop()
        self.update_noise_live()

    def _invalidate_noise_cache(self):
        self._noise_base_data = None
        self._noise_base_source_id = None

    def _ensure_noise_base_data(self):
        if self.raw_data is None:
            return None

        source_id = id(self.raw_data)
        if self._noise_base_data is not None and self._noise_base_source_id == source_id:
            return self._noise_base_data

        arr = np.asarray(self.raw_data, dtype=np.float32)
        row_mean = arr.mean(axis=1, keepdims=True, dtype=np.float32)
        self._noise_base_data = arr - row_mean
        self._noise_base_source_id = source_id
        return self._noise_base_data

    def _compute_noise_reduced(self, low: float, high: float):
        base = self._ensure_noise_base_data()
        if base is None:
            return None
        return np.clip(base, low, high).astype(np.float32, copy=False)

    def _update_live_preview_canvas(self, data):
        if not self._hardware_mode_enabled():
            return False
        ok = self._refresh_accel_plot(data=data, title="Background Subtracted", preserve_view=True)
        if ok:
            self._show_accel_canvas()
        return ok

    def _commit_noise_live_update(self):
        self.noise_commit_timer.stop()

        if self._noise_slider_drag_active:
            return

        if self.raw_data is None or self.noise_reduced_data is None:
            self._noise_undo_pending = False
            if self._hardware_mode_enabled():
                self._show_accel_canvas()
            else:
                self._show_plot_canvas()
            return

        self.plot_data(self.noise_reduced_data, title="Background Subtracted")
        self._noise_undo_pending = False

    def update_noise_live(self):
        if self.raw_data is None:
            return

        if not self._noise_undo_pending:
            self._push_undo_state()
            self._noise_undo_pending = True

        low = self.lower_slider.value()
        high = self.upper_slider.value()
        if low > high:
            low, high = high, low

        data = self._compute_noise_reduced(low, high)
        if data is None:
            return

        self.noise_reduced_data = data
        self.noise_reduced_original = data.copy()

        self.noise_vmin = float(np.nanmin(data)) if data.size else None
        self.noise_vmax = float(np.nanmax(data)) if data.size else None
        self.current_plot_type = "Background Subtracted"

        if self._update_live_preview_canvas(data):
            self.noise_commit_timer.start()
            if not self._noise_slider_drag_active:
                self._commit_noise_live_update()
        else:
            self.plot_data(data, title="Background Subtracted")
            self._noise_undo_pending = False

        # enable tools
        self._sync_toolbar_enabled_states()

    def plot_data(self, data, title="Raw", keep_view=False, restore_view=None):
        view = restore_view if restore_view is not None else (self._capture_view() if keep_view else None)
        QTimer.singleShot(0, lambda: self._plot_data_internal(data, title, view))

    def _capture_view(self):
        """Save current zoom/pan limits (only if a plot exists)."""
        if self._hardware_mode_enabled():
            try:
                return self.accel_canvas.get_view()
            except Exception:
                return None

        try:
            ax = self.canvas.ax
            if ax is None or ax.images is None or len(ax.images) == 0:
                return None
            return {
                "xlim": ax.get_xlim(),
                "ylim": ax.get_ylim(),
            }
        except Exception:
            return None

    def _restore_view(self, view):
        """Restore zoom/pan limits safely."""
        if not view:
            return

        if self._hardware_mode_enabled():
            try:
                self.accel_canvas.set_view(view)
            except Exception:
                pass
            return

        try:
            ax = self.canvas.ax
            ax.set_xlim(view["xlim"])
            ax.set_ylim(view["ylim"])
        except Exception:
            pass

    def _plot_data_internal(self, data, title="Raw", view=None):

        self._stop_rect_zoom()

        if self.time is None or self.freqs is None:
            print("Time or frequency data not loaded. Skipping plot.")
            return

        if not hasattr(self.canvas, 'ax') or self.canvas.ax is None:
            print("Canvas not ready yet")
            return

        self.canvas.ax.clear()
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)

        # Remove old colorbar axis safely
        try:
            if self.current_cax:
                self.current_cax.remove()
                self.current_cax = None
            if self.current_colorbar:
                self.current_colorbar.remove()
                self.current_colorbar = None
        except Exception as e:
            print("Error removing previous colorbar:", e)

        # Define colormap
        # Choose cmap
        if self.current_cmap_name == "Custom":
            colors = [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')]
            cmap = mcolors.LinearSegmentedColormap.from_list('custom_RdYlBu', colors)
        else:
            cmap = plt.get_cmap(self.current_cmap_name)

        # x-axis always in seconds, UT formatting handled separately
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]

        # Prepare colorbar axis
        divider = make_axes_locatable(self.canvas.ax)
        cax = divider.append_axes("right", size="5%", pad=0.1)
        self.current_cax = cax

        # Show image (convert units for display if needed)
        display_data = self._intensity_for_display(data)

        self.current_display_data = display_data
        self._current_plot_source_data = np.asarray(data)

        im = self.canvas.ax.imshow(display_data, aspect='auto', extent=extent, cmap=cmap)

        self.current_colorbar = self.canvas.figure.colorbar(im, cax=cax)

        label = "Intensity [Digits]" if not self.use_db else "Intensity [dB]"
        self.current_colorbar.set_label(label)

        # Store label string so apply_graph_properties_live can re-style it
        self._colorbar_label_text = label

        plot_type = self._normalize_plot_type(title)
        self.canvas.ax.set_ylabel("Frequency [MHz]")
        self.canvas.ax.set_title(self._default_graph_title(plot_type), fontsize=14)

        # Save full-extent limits as the "home" view (used for Reset Selection after zoom/pan)
        self._home_view = {
            "xlim": self.canvas.ax.get_xlim(),
            "ylim": self.canvas.ax.get_ylim(),
        }

        self.format_axes()  # Format x-axis based on user selection (seconds/UT)
        self._restore_view(view)

        # Apply graph properties (title/font/sizes) after plot rebuild
        self.apply_graph_properties_live()

        # Now force the MPL colors/background to match the theme (dark/light)
        self._apply_mpl_theme()
        style_axes(self.canvas.ax)

        self.graph_group.setEnabled(True)
        self.canvas.draw_idle()

        try:
            self._refresh_accel_plot(data=data, title=plot_type, view=view, preserve_view=(view is not None))
        except Exception:
            pass
        self._render_annotations()

        self.current_plot_type = plot_type
        if self._hardware_mode_enabled():
            self._show_accel_canvas()
        else:
            self._show_plot_canvas()
        self._sync_toolbar_enabled_states()
        self.statusBar().showMessage(f"Loaded: {self.filename}", 5000)

    def on_mouse_motion_status(self, event):
        """Show time, frequency and intensity under cursor in status bar."""
        in_axes = event.inaxes == self.canvas.ax and event.xdata is not None and event.ydata is not None
        x = float(event.xdata) if in_axes else 0.0
        y = float(event.ydata) if in_axes else 0.0
        self._update_cursor_label_from_xy(x, y, in_axes)

    def on_accel_mouse_motion_status(self, x: float, y: float, inside: bool):
        self._update_cursor_label_from_xy(float(x), float(y), bool(inside))

    def _update_cursor_label_from_xy(self, x: float, y: float, inside: bool):
        if not inside:
            self.cursor_label.setText("t = 0.00   |   f = 0.00 MHz   |   I = 0.00")
            return

        if self.current_display_data is None or self.time is None or self.freqs is None:
            return

        time_arr = np.array(self.time)
        freq_arr = np.array(self.freqs)
        if time_arr.size == 0 or freq_arr.size == 0:
            return

        idx_x = int(np.argmin(np.abs(time_arr - float(x))))
        idx_y = int(np.argmin(np.abs(freq_arr - float(y))))

        ny, nx = self.current_display_data.shape
        if idx_x < 0 or idx_x >= nx or idx_y < 0 or idx_y >= ny:
            return

        t_val = time_arr[idx_x]
        f_val = freq_arr[idx_y]
        intensity = self.current_display_data[idx_y, idx_x]

        if self.use_utc and self.ut_start_sec is not None:
            total_seconds = self.ut_start_sec + t_val
            hours = int(total_seconds // 3600) % 24
            minutes = int((total_seconds % 3600) // 60)
            seconds = int(total_seconds % 60)
            time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d} UT"
        else:
            time_str = f"{t_val:.2f} s"

        unit_label = "dB" if self.use_db else "Digits"
        msg = (
            f"t = {time_str}   |   "
            f"f = {f_val:.2f} MHz   |   "
            f"I = {intensity:.2f} {unit_label}"
        )
        self.cursor_label.setText(msg)

    def change_cmap(self, name):
        self._push_undo_state()
        self.current_cmap_name = name
        if self.raw_data is None:
            return
        data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
        self.plot_data(data, title=self.current_plot_type, keep_view=True)

    def get_current_cmap(self):
        if self.current_cmap_name == "Custom":
            colors = [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')]
            return mcolors.LinearSegmentedColormap.from_list('custom_RdYlBu', colors)
        else:
            return plt.get_cmap(self.current_cmap_name)

    def _on_accel_view_interaction_finished(self, prev_view, new_view):
        if not self._hardware_mode_enabled():
            return
        if prev_view and new_view and (not self._views_close(prev_view, new_view)):
            self._push_undo_view(prev_view)
        self._sync_toolbar_enabled_states()

    def _on_accel_rect_zoom_finished(self, prev_view, new_view):
        if not self._hardware_mode_enabled():
            return
        self.rect_zoom_active = False
        if prev_view and new_view and (not self._views_close(prev_view, new_view)):
            self._push_undo_view(prev_view)
        self.statusBar().showMessage("Zoomed to selected region (still locked).", 2500)
        self._sync_toolbar_enabled_states()

    def _on_accel_lasso_finished(self, verts):
        if not self._hardware_mode_enabled():
            return
        if self._annotation_mode == "polygon":
            self._on_annotation_polygon_finished(list(verts or []))
            self.lasso_active = False
            return
        if not self.lasso_active:
            return
        self.on_lasso_select(list(verts))

    def _on_accel_drift_point_added(self, x: float, y: float):
        self.drift_points.append((float(x), float(y)))
        self.accel_canvas.show_drift_points(self.drift_points, with_segments=False)

    def _on_accel_drift_capture_finished(self, points):
        self.drift_points = [(float(x), float(y)) for (x, y) in (points or [])]
        self.finish_drift_estimation()

    def on_scroll_zoom(self, event):
        """Smooth zoom using mouse scroll wheel."""
        if self._hardware_mode_enabled():
            return

        if self.lasso_active:
            return

        if getattr(self, "nav_locked", False) or getattr(self, "rect_zoom_active", False):
            return

        ax = self.canvas.ax

        # Mouse pointer must be inside the plot
        if event.inaxes != ax:
            return
        if event.xdata is None or event.ydata is None:
            return

        prev_view = self._capture_view()

        # Zoom factor
        base_scale = 1.15  # smooth and gentle zoom

        # Zoom IN
        if event.button == "up":
            scale_factor = 1 / base_scale
        # Zoom OUT
        elif event.button == "down":
            scale_factor = base_scale
        else:
            return

        # Current axis limits
        x_min, x_max = ax.get_xlim()
        y_min, y_max = ax.get_ylim()

        x_range = (x_max - x_min)
        y_range = (y_max - y_min)

        # Mouse location in data coords
        xdata = event.xdata
        ydata = event.ydata

        # Compute new ranges
        new_x_range = x_range * scale_factor
        new_y_range = y_range * scale_factor

        # Shift around mouse cursor
        x_shift = (xdata - x_min) * (1 - scale_factor)
        y_shift = (ydata - y_min) * (1 - scale_factor)

        new_x_min = x_min + x_shift
        new_x_max = new_x_min + new_x_range

        new_y_min = y_min + y_shift
        new_y_max = new_y_min + new_y_range

        # Apply limits
        ax.set_xlim(new_x_min, new_x_max)
        ax.set_ylim(new_y_min, new_y_max)

        if prev_view:
            self._push_undo_view(prev_view)

        self.canvas.draw_idle()
        self._sync_toolbar_enabled_states()

    def on_mouse_press(self, event):
        """
        Start panning with LEFT mouse button inside the main axes.
        (No modifier keys needed.)
        """
        if self._hardware_mode_enabled():
            return

        if getattr(self, "nav_locked", False) or getattr(self, "rect_zoom_active", False):
            return

        if self.lasso_active:
            return

        # Only react if we click inside the image axes
        if event.inaxes != self.canvas.ax:
            return

        # Left button = start pan
        if event.button == 1 and event.xdata is not None and event.ydata is not None:
            self._panning = True
            self._last_pan_xy = (event.xdata, event.ydata)
            self._pan_start_view = self._capture_view()

    def on_mouse_move(self, event):
        """
        Perform the pan movement while the left mouse button is held.
        """
        if self._hardware_mode_enabled():
            return

        if getattr(self, "nav_locked", False) or getattr(self, "rect_zoom_active", False):
            return

        if self.lasso_active:
            return

        # Only act if we are in panning mode and pointer is on data
        if not self._panning or event.xdata is None or event.ydata is None:
            return

        ax = self.canvas.ax

        # Previous mouse position in data coordinates
        x_prev, y_prev = self._last_pan_xy

        # Current mouse position
        x_curr, y_curr = event.xdata, event.ydata

        # Difference
        dx = x_prev - x_curr
        dy = y_prev - y_curr

        # Current limits
        x_min, x_max = ax.get_xlim()
        y_min, y_max = ax.get_ylim()

        # Shift both axes
        ax.set_xlim(x_min + dx, x_max + dx)
        ax.set_ylim(y_min + dy, y_max + dy)

        self.canvas.draw_idle()

        # Update last mouse position
        self._last_pan_xy = (x_curr, y_curr)

    def on_mouse_release(self, event):
        """
        Stop panning when the mouse button is released.
        """
        if self._hardware_mode_enabled():
            return

        self._panning = False
        self._last_pan_xy = None
        if getattr(self, "_pan_start_view", None):
            try:
                cur_view = self._capture_view()
                if cur_view and (not self._views_close(cur_view, self._pan_start_view)):
                    self._push_undo_view(self._pan_start_view)
            except Exception:
                pass
            finally:
                self._pan_start_view = None
                self._sync_toolbar_enabled_states()


    def activate_drift_tool(self):
        self.statusBar().showMessage("Click multiple points along the burst. Right-click or double-click to finish.",
                                     8000)
        self.drift_points = []
        if self._hardware_mode_enabled():
            self.accel_canvas.begin_drift_capture()
            self.accel_canvas.show_drift_points([], with_segments=False)
            return
        self.drift_click_cid = self.canvas.mpl_connect("button_press_event", self.on_drift_point_click)

    def on_drift_point_click(self, event):
        if not event.inaxes:
            return

        # Right-click or double-click to finish
        if event.button == 3 or event.dblclick:
            self.finish_drift_estimation()
            return

        self.drift_points.append((event.xdata, event.ydata))
        self.canvas.ax.plot(event.xdata, event.ydata, 'w*')
        self.canvas.draw()

    def finish_drift_estimation(self):
        if self._hardware_mode_enabled():
            self.accel_canvas.stop_interaction_capture()
        else:
            self.canvas.mpl_disconnect(self.drift_click_cid)

        if len(self.drift_points) < 2:
            self.statusBar().showMessage("Need at least two points to estimate drift.", 4000)
            return

        drift_rates = []
        for i in range(len(self.drift_points) - 1):
            x1, y1 = self.drift_points[i]
            x2, y2 = self.drift_points[i + 1]
            if abs(x2 - x1) < 1e-12:
                continue
            drift = (y2 - y1) / (x2 - x1)
            drift_rates.append(drift)
            # Draw line between points
            if not self._hardware_mode_enabled():
                self.canvas.ax.plot([x1, x2], [y1, y2], linestyle='--', color='lime')

        if not drift_rates:
            self.statusBar().showMessage("Need points with different time values to estimate drift.", 4000)
            return

        avg_drift = np.mean(drift_rates)
        if self._hardware_mode_enabled():
            self.accel_canvas.show_drift_points(self.drift_points, with_segments=True)
        else:
            self.canvas.ax.legend(["Drift Segments"])
            self.canvas.draw()

        self.statusBar().showMessage(
            f"Average Drift Rate: {avg_drift:.4f} MHz/s, Start Frequency: {y1: .3f}, End Frequency: {y2: .3f}, Duration: {x2 - x1: .3f} s",
            0)

    def activate_lasso(self):
        if self.noise_reduced_data is None:
            QMessageBox.warning(self, "Error", "Please apply background substraction before isolating a burst.")
            return

        if self._hardware_mode_enabled():
            self.lasso_active = True
            self.accel_canvas.begin_lasso_capture()
            self.statusBar().showMessage("Press, drag around the burst, and release to isolate.", 5000)
            return

        self.canvas.mpl_disconnect(self._cid_press)
        self.canvas.mpl_disconnect(self._cid_motion)
        self.canvas.mpl_disconnect(self._cid_release)

        self.lasso_active = True

        # Disconnect old lasso
        if self.lasso:
            try:
                self.lasso.disconnect_events()
            except Exception:
                pass
            self.lasso = None

        self.canvas.ax.set_title("Draw around the burst")
        self.canvas.draw()

        self.lasso = LassoSelector(self.canvas.ax, onselect=self.on_lasso_select)

    def on_lasso_select(self, verts):

        if self.noise_reduced_data is None:
            print("Lasso used before data was prepared. Ignoring.")
            return

        verts_arr = np.asarray(verts, dtype=float)
        if verts_arr.ndim != 2 or verts_arr.shape[0] < 3 or verts_arr.shape[1] != 2:
            print("Invalid lasso selection. Ignoring.")
            return
        if not np.allclose(verts_arr[0], verts_arr[-1]):
            verts_arr = np.vstack([verts_arr, verts_arr[0]])

        path = Path(verts_arr, closed=True)

        ny, nx = self.noise_reduced_data.shape
        y = np.linspace(self.freqs[0], self.freqs[-1], ny)
        x = np.linspace(0, self.time[-1], nx)
        X, Y = np.meshgrid(x, y)

        coords = np.column_stack((X.flatten(), Y.flatten()))
        mask = path.contains_points(coords).reshape(ny, nx)

        self.lasso_mask = mask  # store for use later

        # Safely disconnect the lasso tool
        if self.lasso:
            try:
                self.lasso.disconnect_events()
            except Exception:
                pass
            self.lasso = None

        if not self._hardware_mode_enabled():
            self._cid_press = self.canvas.mpl_connect("button_press_event", self.on_mouse_press)
            self._cid_motion = self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)
            self._cid_release = self.canvas.mpl_connect("button_release_event", self.on_mouse_release)
        self.lasso_active = False
        # Defer drawing to avoid crash during event handling
        QTimer.singleShot(0, lambda: self._plot_isolated_burst(mask))

    def _plot_isolated_burst(self, mask):
        self._push_undo_state()
        # Build isolated data array
        burst_isolated = np.zeros_like(self.noise_reduced_data)
        burst_isolated[mask] = self.noise_reduced_data[mask]

        # Clear figure
        self.canvas.ax.clear()
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)
        style_axes(self.canvas.ax)

        # Remove old colorbar safely
        try:
            if self.current_colorbar:
                self.current_colorbar.remove()
        except Exception:
            pass
        self.current_colorbar = None

        try:
            if self.current_cax:
                self.current_cax.remove()
        except Exception:
            pass
        self.current_cax = None

        # Restore extent
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]

        # Get SAME colormap user selected
        cmap = self.get_current_cmap()

        # === CREATE COLORBAR AXIS HERE (must exist before colorbar) ===
        divider = make_axes_locatable(self.canvas.ax)
        cax = divider.append_axes("right", size="5%", pad=0.1)
        self.current_cax = cax

        # Plot with fixed vmin/vmax from noise reduction (converted to display units if needed)
        display_burst = self._intensity_for_display(burst_isolated)
        vmin, vmax = self._intensity_range_for_display(self.noise_vmin, self.noise_vmax)

        im = self.canvas.ax.imshow(
            display_burst,
            aspect='auto',
            extent=extent,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
        )

        self.current_display_data = display_burst
        self._current_plot_source_data = np.asarray(burst_isolated)

        # Create new colorbar
        self.current_colorbar = self.canvas.figure.colorbar(im, cax=cax)
        self._colorbar_label_text = "Intensity [Digits]" if not self.use_db else "Intensity [dB]"
        self.current_colorbar.set_label(self._colorbar_label_text)

        # Labels
        self.canvas.ax.set_title("Isolated Burst")
        self.canvas.ax.set_ylabel("Frequency [MHz]")

        # Save full-extent limits as the "home" view (used for Reset Selection after zoom/pan)
        self._home_view = {
            "xlim": self.canvas.ax.get_xlim(),
            "ylim": self.canvas.ax.get_ylim(),
        }

        self.format_axes()

        # Keep graph styling consistent (fonts) then apply theme colors
        self.apply_graph_properties_live()
        self._apply_mpl_theme()

        self.canvas.draw_idle()
        self._refresh_accel_plot(data=burst_isolated, title="Isolated Burst", preserve_view=False)
        if self._hardware_mode_enabled():
            self._show_accel_canvas()
        else:
            self._show_plot_canvas()

        # Replace display data with isolated data
        self.noise_reduced_data = burst_isolated
        self.current_plot_type = "Isolated Burst"

        self.statusBar().showMessage("Burst isolated using lasso", 4000)

    def plot_max_intensities(self):
        # Ensure any active lasso from the main plot is fully disconnected
        if getattr(self, "lasso", None):
            try:
                self.lasso.disconnect_events()
            except Exception:
                pass
            self.lasso = None

        self._open_or_focus_max_dialog(auto_open_analyzer=False, prefer_current_plot=True)

    def _pixmap_to_rgba_array(self, pixmap: QPixmap) -> np.ndarray:
        image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        return self._qimage_to_rgba_array(image)

    def _qimage_to_rgba_array(self, image: QImage) -> np.ndarray:
        width = image.width()
        height = image.height()
        ptr = image.bits()
        ptr.setsize(image.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, image.bytesPerLine()))
        return arr[:, : width * 4].reshape((height, width, 4)).copy()

    def _export_hardware_visible_plot(self, file_path: str, ext_final: str) -> None:
        ext = str(ext_final or "").lower()
        plot_item = self.accel_canvas.export_plot_item()
        if plot_item is None:
            raise RuntimeError("Hardware plot is not available for export.")

        try:
            import pyqtgraph.exporters as pg_exporters
        except Exception:
            pg_exporters = None

        if pg_exporters is not None:
            raster_exts = {"png", "tif", "tiff", "jpg", "jpeg", "bmp", "webp"}
            if ext in raster_exts:
                exporter = pg_exporters.ImageExporter(plot_item)
                try:
                    params = exporter.parameters()
                    width = max(1, int(self.accel_canvas.width() * self.accel_canvas.devicePixelRatioF()))
                    params["width"] = max(width, 1400)
                except Exception:
                    pass
                exporter.export(file_path)
                return

            if ext == "svg":
                exporter = pg_exporters.SVGExporter(plot_item)
                exporter.export(file_path)
                return

            if ext in {"pdf", "eps"}:
                temp_png = None
                try:
                    fd, temp_png = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    exporter = pg_exporters.ImageExporter(plot_item)
                    try:
                        params = exporter.parameters()
                        width = max(1, int(self.accel_canvas.width() * self.accel_canvas.devicePixelRatioF()))
                        params["width"] = max(width, 1800)
                    except Exception:
                        pass
                    exporter.export(temp_png)

                    img = QImage(temp_png)
                    if img.isNull():
                        raise RuntimeError("Failed to capture hardware plot image.")

                    if ext == "pdf":
                        writer = QPdfWriter(file_path)
                        writer.setResolution(300)
                        painter = QPainter(writer)
                        target = writer.pageLayout().paintRectPixels(writer.resolution())
                        scaled = img.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        x = target.x() + (target.width() - scaled.width()) // 2
                        y = target.y() + (target.height() - scaled.height()) // 2
                        painter.drawImage(x, y, scaled)
                        painter.end()
                        return

                    rgba = self._qimage_to_rgba_array(img.convertToFormat(QImage.Format_RGBA8888))
                    fig = Figure(figsize=(rgba.shape[1] / 300.0, rgba.shape[0] / 300.0), dpi=300)
                    ax = fig.add_axes([0, 0, 1, 1])
                    ax.imshow(rgba)
                    ax.axis("off")
                    fig.savefig(file_path, dpi=300, bbox_inches="tight", pad_inches=0, format="eps")
                    return
                finally:
                    if temp_png:
                        try:
                            os.remove(temp_png)
                        except Exception:
                            pass

        # Last-resort fallback (can be blank on some OpenGL drivers)
        pixmap = self.accel_canvas.grab()
        if pixmap.isNull():
            raise RuntimeError("Could not capture the accelerated plot image.")
        if not pixmap.save(file_path, ext.upper() if ext else "PNG"):
            raise RuntimeError(f"Failed to save image as {ext}.")

    def _pick_export_path_for_figure(self, caption: str, default_name: str, filters: str, default_filter: str = None):
        """
        Hardware-acceleration mode uses native save dialog directly because
        some Linux/OpenGL combinations can hang with the non-native dialog.
        """
        if self._hardware_mode_enabled():
            path, chosen_filter = QFileDialog.getSaveFileName(
                self,
                caption,
                default_name,
                filters,
                default_filter or "",
            )
            if not path:
                return "", ""

            ext = os.path.splitext(path)[1].lstrip(".").lower()
            if not ext:
                ext = _ext_from_filter(chosen_filter) or "png"
                path = f"{path}.{ext}"
            return path, ext

        return pick_export_path(
            self,
            caption,
            default_name,
            filters,
            default_filter=default_filter,
        )

    def export_figure(self):

        if not self.filename:
            QMessageBox.warning(self, "No File Loaded", "Load a FITS file before exporting.")
            return

        formats = "PNG (*.png);;PDF (*.pdf);;EPS (*.eps);;SVG (*.svg);;TIFF (*.tiff)"

        if self._hardware_mode_enabled():
            base_title = str(self.graph_title_override or self._default_graph_title(self.current_plot_type)).strip()
            default_name = self._sanitize_export_stem(base_title)
        else:
            default_name = self._sanitize_export_stem(self._current_graph_title_for_export())

        file_path, ext = self._pick_export_path_for_figure(
            "Export Figure",
            default_name,
            formats,
            default_filter="PNG (*.png)",
        )

        if not file_path:
            return

        if sys.platform.startswith("win") and file_path.lower().startswith("c:\\program files"):
            QMessageBox.warning(
                self,
                "Permission Denied",
                "Windows does not allow saving files inside Program Files.\n"
                "Please choose another folder such as Documents or Desktop."
            )
            return

        def normalize_ext(ext_value: str) -> str:
            """
            Accepts values like: 'png', '.png', 'PNG (*.png)'
            Returns: 'png'
            """
            if not ext_value:
                return "png"
            s = str(ext_value).strip().lower()
            if s.startswith("."):
                s = s[1:]
            m = re.search(r"\*\.(\w+)", s)
            if m:
                return m.group(1).lower()
            return s

        try:
            root, current_ext = os.path.splitext(file_path)

            # If user did not type an extension, add one based on returned ext
            if current_ext == "":
                ext_final = normalize_ext(ext)
                file_path = f"{file_path}.{ext_final}"
            else:
                ext_final = current_ext.lower().lstrip(".")

            if self._hardware_mode_enabled():
                self._export_hardware_visible_plot(file_path, ext_final)
            else:
                self.canvas.figure.savefig(
                    file_path,
                    dpi=300,
                    bbox_inches="tight",
                    format=ext_final
                )

            QMessageBox.information(self, "Export Complete", f"Figure saved:\n{file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"An error occurred:\n{e}")

    def _recommend_bitpix_for_export(self, data: np.ndarray) -> int:
        """
        Recommend a FITS BITPIX (8/16/32) based on the data range.

        CALLISTO raw files are typically 8-bit, but combined/processed data can exceed that.
        JavaViewer cannot read BITPIX=-64, so we avoid float64 exports.
        """
        try:
            arr = np.asarray(data)
            if arr.size == 0:
                return 16
            finite = np.isfinite(arr)
            if not np.any(finite):
                return 16
            mn = float(np.nanmin(arr))
            mx = float(np.nanmax(arr))
        except Exception:
            return 16

        if 0.0 <= mn and mx <= 255.0:
            return 8
        if -32768.0 <= mn and mx <= 32767.0:
            return 16
        return 32

    def _cast_data_for_bitpix(self, data: np.ndarray, bitpix: int) -> np.ndarray:
        arr = np.asarray(data)
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

        if int(bitpix) == 8:
            out = np.rint(arr)
            out = np.clip(out, 0, 255)
            return out.astype(np.uint8, copy=False)

        if int(bitpix) == 16:
            info = np.iinfo(np.int16)
            out = np.rint(arr)
            out = np.clip(out, info.min, info.max)
            return out.astype(np.int16, copy=False)

        if int(bitpix) == 32:
            info = np.iinfo(np.int32)
            out = np.rint(arr)
            out = np.clip(out, info.min, info.max)
            return out.astype(np.int32, copy=False)

        raise ValueError(f"Unsupported BITPIX: {bitpix}")

    def _sanitize_primary_header_for_export(self, hdr: fits.Header) -> fits.Header:
        # Structural/scaling keywords are determined from the data by Astropy.
        for key in ("SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "EXTEND", "BSCALE", "BZERO", "BLANK"):
            try:
                hdr.remove(key, ignore_missing=True, remove_all=True)
            except Exception:
                pass
        return hdr

    def _axis_kind_from_name(self, name: str) -> str | None:
        n = str(name or "").strip().lower()
        if n in ("freq", "frequency", "freqs", "frequency_mhz", "freq_mhz"):
            return "freq"
        if n in ("time", "times", "time_s", "time_sec", "seconds", "sec"):
            return "time"
        if "freq" in n:
            return "freq"
        if n.startswith("time"):
            return "time"
        return None

    def _update_axis_table_hdu(self, hdu, freqs: np.ndarray, times: np.ndarray) -> bool:
        data = getattr(hdu, "data", None)
        if data is None:
            return False
        dtype = getattr(data, "dtype", None)
        names = list(getattr(dtype, "names", []) or [])
        if not names:
            return False

        axis_map: dict[str, np.ndarray] = {}
        for name in names:
            kind = self._axis_kind_from_name(name)
            if kind == "freq":
                axis_map[name] = freqs
            elif kind == "time":
                axis_map[name] = times

        if not axis_map:
            extname = str(hdu.header.get("EXTNAME", "")).lower()
            if "freq" in extname and len(names) == 1:
                axis_map[names[0]] = freqs
            elif "time" in extname and len(names) == 1:
                axis_map[names[0]] = times

        if not axis_map:
            return False

        old_rows = int(data.shape[0]) if hasattr(data, "shape") and len(data.shape) > 0 else 0
        row_lengths = []
        for name, axis in axis_map.items():
            field_dtype = dtype.fields[name][0]
            if field_dtype.shape == ():
                row_lengths.append(len(axis))

        if row_lengths:
            nrows = row_lengths[0]
            for length in row_lengths[1:]:
                if length != nrows:
                    nrows = max(row_lengths)
                    break
        else:
            nrows = old_rows if old_rows > 0 else 1

        new_descr = []
        for name in names:
            field_dtype = dtype.fields[name][0]
            base = field_dtype.base
            if name in axis_map:
                if field_dtype.shape == ():
                    new_descr.append((name, base))
                else:
                    new_descr.append((name, base, (len(axis_map[name]),)))
            else:
                if field_dtype.shape == ():
                    new_descr.append((name, field_dtype))
                else:
                    new_descr.append((name, base, field_dtype.shape))

        new_dtype = np.dtype(new_descr)
        new_data = np.zeros(nrows, dtype=new_dtype)

        for name, axis in axis_map.items():
            axis_arr = np.asarray(axis)
            target = new_data[name]
            if target.ndim == 1:
                axis_cast = axis_arr.astype(target.dtype, copy=False)
                if axis_cast.shape[0] < nrows:
                    pad = np.zeros(nrows, dtype=target.dtype)
                    pad[:axis_cast.shape[0]] = axis_cast
                    axis_cast = pad
                elif axis_cast.shape[0] > nrows:
                    axis_cast = axis_cast[:nrows]
                new_data[name] = axis_cast
            else:
                vec_len = target.shape[1]
                axis_cast = axis_arr.astype(target.dtype, copy=False)
                if axis_cast.shape[0] < vec_len:
                    pad = np.zeros(vec_len, dtype=target.dtype)
                    pad[:axis_cast.shape[0]] = axis_cast
                    axis_cast = pad
                elif axis_cast.shape[0] > vec_len:
                    axis_cast = axis_cast[:vec_len]
                new_data[name][:] = axis_cast

        for name in names:
            if name in axis_map:
                continue
            try:
                old_col = data[name]
                if old_col.shape == new_data[name].shape:
                    new_data[name] = old_col
                else:
                    if old_col.size > 0:
                        new_data[name][0] = old_col[0]
                        if new_data[name].shape[0] > 1:
                            new_data[name][1:] = new_data[name][0]
            except Exception:
                pass

        hdu.data = new_data
        try:
            hdu.update_header()
        except Exception:
            pass
        return True

    def _build_export_hdul_from_template(
        self,
        template_hdul: fits.HDUList,
        primary: fits.PrimaryHDU,
        freqs: np.ndarray,
        times: np.ndarray,
    ) -> tuple[fits.HDUList, bool]:
        new_hdus = [primary]
        updated_any = False
        for hdu in template_hdul[1:]:
            new_hdu = hdu.copy()
            if isinstance(new_hdu, (fits.BinTableHDU, fits.TableHDU)):
                if self._update_axis_table_hdu(new_hdu, freqs, times):
                    updated_any = True
            new_hdus.append(new_hdu)
        return fits.HDUList(new_hdus), updated_any

    def export_to_fits(self):
        if self.raw_data is None or self.freqs is None or self.time is None:
            QMessageBox.warning(self, "No Data", "Load a FITS file before exporting.")
            return

        # Pick exactly what is currently shown
        data_to_save = getattr(self, "current_display_data", None)
        if data_to_save is None:
            data_to_save = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data

        # Choose BITPIX for compatibility (JavaViewer does not support BITPIX=-64)
        rec_bitpix = self._recommend_bitpix_for_export(data_to_save)
        bitpix_items = [
            f"Auto (Recommended: {rec_bitpix})",
            "8 (unsigned byte)",
            "16 (signed int16)",
            "32 (signed int32)",
        ]
        chosen, ok = QInputDialog.getItem(
            self,
            "Export FITS - BITPIX",
            "Choose BITPIX for the exported FITS:",
            bitpix_items,
            0,
            False,
        )
        if not ok:
            return

        bitpix = rec_bitpix
        try:
            if isinstance(chosen, str) and chosen.strip().startswith("8"):
                bitpix = 8
            elif isinstance(chosen, str) and chosen.strip().startswith("16"):
                bitpix = 16
            elif isinstance(chosen, str) and chosen.strip().startswith("32"):
                bitpix = 32
        except Exception:
            bitpix = rec_bitpix

        try:
            export_data = self._cast_data_for_bitpix(data_to_save, bitpix)
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not convert data for BITPIX={bitpix}:\n{e}")
            return

        default_name = f"{self._sanitize_export_stem(self._current_graph_title_for_export())}.fit"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export to FITS",
            default_name,
            "FITS files (*.fit *.fits *.fit.gz *.fits.gz)"
        )
        if not save_path:
            return

        # Ensure extension
        lower = save_path.lower()
        if not (lower.endswith(".fit") or lower.endswith(".fits") or lower.endswith(".fit.gz") or lower.endswith(
                ".fits.gz")):
            save_path += ".fit"

        # Header template
        if self._fits_header0 is not None:
            hdr0 = self._fits_header0.copy()
        else:
            hdr0 = fits.Header()

        hdr0 = self._sanitize_primary_header_for_export(hdr0)

        # Mark what this file is
        hdr0["HISTORY"] = "Exported by e-CALLISTO FITS Analyzer"
        hdr0["HISTORY"] = f"Export plot type: {self.current_plot_type}"
        hdr0["HISTORY"] = f"Units shown: {'dB' if getattr(self, 'use_db', False) else 'Digits'}"

        if self._is_combined:
            hdr0["COMBINED"] = True
            if self._combined_mode:
                hdr0["COMBMETH"] = str(self._combined_mode)
            hdr0["NFILES"] = len(self._combined_sources) if self._combined_sources else 0
            if self._combined_sources:
                hdr0["HISTORY"] = f"First source: {os.path.basename(self._combined_sources[0])}"
                hdr0["HISTORY"] = f"Last source: {os.path.basename(self._combined_sources[-1])}"
        else:
            hdr0["COMBINED"] = False
            if self._fits_source_path:
                hdr0["HISTORY"] = f"Source: {os.path.basename(self._fits_source_path)}"

        # Save BUNIT if you want the file to be self-describing
        hdr0["BUNIT"] = "dB" if getattr(self, "use_db", False) else "Digits"

        primary = fits.PrimaryHDU(data=export_data, header=hdr0)
        try:
            primary.header["BSCALE"] = 1
            primary.header["BZERO"] = 0
        except Exception:
            pass
        try:
            primary.header["DATAMIN"] = float(np.nanmin(export_data))
            primary.header["DATAMAX"] = float(np.nanmax(export_data))
        except Exception:
            pass

        freqs = np.asarray(self.freqs, dtype=np.float32)
        times = np.asarray(self.time, dtype=np.float32)

        hdul = None
        updated_any = False
        template_path = self._fits_source_path
        if not template_path and self._combined_sources:
            template_path = self._combined_sources[0]
        if template_path and os.path.exists(template_path):
            try:
                with fits.open(template_path, memmap=False) as tmpl:
                    hdul, updated_any = self._build_export_hdul_from_template(tmpl, primary, freqs, times)
            except Exception:
                hdul = None
                updated_any = False

        if hdul is not None and self._is_combined and not updated_any:
            hdul = None

        if hdul is None:
            try:
                cols = fits.ColDefs([
                    fits.Column(name="FREQUENCY", format=f"{freqs.size}E", array=[freqs]),
                    fits.Column(name="TIME", format=f"{times.size}E", array=[times]),
                ])
                axis_hdu = fits.BinTableHDU.from_columns(cols)
                axis_hdu.header["EXTNAME"] = "AXIS"
                hdul = fits.HDUList([primary, axis_hdu])
            except Exception:
                hdul = fits.HDUList([primary])

        try:
            hdul[0].header["EXTEND"] = True if len(hdul) > 1 else False
        except Exception:
            pass

        try:
            hdul.writeto(save_path, overwrite=True, output_verify="silentfix")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not write FITS file:\n{e}")
            return

        self.statusBar().showMessage(f"Exported FITS (BITPIX={bitpix}): {os.path.basename(save_path)}", 5000)

    def reset_all(self):
        self.noise_smooth_timer.stop()
        self.noise_commit_timer.stop()
        self._noise_undo_pending = False
        self._noise_slider_drag_active = False
        if self._hardware_mode_enabled():
            self._show_accel_canvas()
        else:
            self._show_plot_canvas()
        try:
            self.accel_canvas.clear()
        except Exception:
            pass

        # Safely remove colorbar
        try:
            if self.current_colorbar and self.current_colorbar.ax:
                self.current_colorbar.remove()
        except Exception:
            pass
        self.current_colorbar = None

        try:
            if self.current_cax:
                self.current_cax.remove()
        except Exception:
            pass
        self.current_cax = None

        # Clear canvas
        self.canvas.ax.clear()
        self.canvas.draw()

        # Clear data
        self.raw_data = None
        self._invalidate_noise_cache()
        self.freqs = None
        self.time = None
        self.filename = ""
        self.noise_reduced_data = None
        self.noise_reduced_original = None
        self.lasso_mask = None
        self.current_plot_type = "Raw"
        self.current_display_data = None
        self._current_plot_source_data = None
        self.noise_vmin = None
        self.noise_vmax = None
        self._rfi_preview_data = None
        self._rfi_preview_masked = []
        self._annotations = []
        self._annotation_artists = []

        # FITS metadata / provenance
        self._fits_header0 = None
        self._fits_source_path = None
        self._is_combined = False
        self._combined_mode = None
        self._combined_sources = []
        self.ut_start_sec = None
        self._home_view = None
        self._pan_start_view = None

        # Reset GUI
        self.statusBar().showMessage("All reset", 4000)

        # Tool bar
        self._sync_toolbar_enabled_states()
        self.graph_group.setEnabled(False)

        if self.canvas.ax:
            self.canvas.ax.set_xlim(0, 1)
            self.canvas.ax.set_ylim(1, 0)

        self._clear_analysis_session_state(close_windows=True)
        self._active_preset_snapshot = None
        self._set_project_clean(None)

        print("Application reset to initial state.")

    def reset_to_raw(self):
        if self.raw_data is None:
            QMessageBox.warning(self, "No Data", "Load a FITS file first.")
            return

        had_processed = (
            self.noise_reduced_data is not None
            or self.noise_reduced_original is not None
            or self.lasso_mask is not None
            or self.lasso_active
            or bool(getattr(self, "lasso", None))
            or self.lower_slider.value() != 0
            or self.upper_slider.value() != 0
        )
        if had_processed:
            self._push_undo_state()

        self.noise_smooth_timer.stop()
        self.noise_commit_timer.stop()
        self._noise_undo_pending = False
        self._noise_slider_drag_active = False
        if self._hardware_mode_enabled():
            self._show_accel_canvas()
        else:
            self._show_plot_canvas()

        if getattr(self, "lasso", None):
            try:
                self.lasso.disconnect_events()
            except Exception:
                pass
            self.lasso = None
        self.lasso_active = False
        try:
            self.accel_canvas.stop_interaction_capture()
            self.accel_canvas.clear_overlays()
        except Exception:
            pass

        # Ensure pan handlers are restored if lasso activation had disconnected them.
        try:
            self.canvas.mpl_disconnect(self._cid_press)
        except Exception:
            pass
        try:
            self.canvas.mpl_disconnect(self._cid_motion)
        except Exception:
            pass
        try:
            self.canvas.mpl_disconnect(self._cid_release)
        except Exception:
            pass
        self._cid_press = self.canvas.mpl_connect("button_press_event", self.on_mouse_press)
        self._cid_motion = self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)
        self._cid_release = self.canvas.mpl_connect("button_release_event", self.on_mouse_release)

        self.noise_reduced_data = None
        self.noise_reduced_original = None
        self.lasso_mask = None
        self.noise_vmin = None
        self.noise_vmax = None
        self._rfi_preview_data = None
        self._rfi_preview_masked = []
        if isinstance(self._rfi_config, dict):
            self._rfi_config["applied"] = False

        self.lower_slider.blockSignals(True)
        self.upper_slider.blockSignals(True)
        try:
            self.lower_slider.setValue(0)
            self.upper_slider.setValue(0)
        finally:
            self.lower_slider.blockSignals(False)
            self.upper_slider.blockSignals(False)

        self.plot_data(self.raw_data, title="Raw")
        if had_processed:
            self._mark_project_dirty()
        self.statusBar().showMessage("Reset to raw", 3000)
        self._sync_toolbar_enabled_states()

    def _clear_drift_overlays(self, keep_view: bool = True) -> bool:
        had_drift_points = bool(getattr(self, "drift_points", []))
        self.drift_points = []

        if self._hardware_mode_enabled():
            try:
                self.accel_canvas.stop_interaction_capture()
            except Exception:
                pass
            try:
                self.accel_canvas.show_drift_points([], with_segments=False)
                self.accel_canvas.clear_overlays()
            except Exception:
                pass
            return had_drift_points

        # Matplotlib path
        cid = getattr(self, "drift_click_cid", None)
        if cid is not None:
            try:
                self.canvas.mpl_disconnect(cid)
            except Exception:
                pass
            self.drift_click_cid = None

        if had_drift_points and self.raw_data is not None:
            data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
            self.plot_data(data, title=self.current_plot_type, keep_view=keep_view)
            return True

        try:
            legend = self.canvas.ax.get_legend()
            if legend is not None:
                legend.remove()
                self.canvas.draw_idle()
        except Exception:
            pass
        return had_drift_points

    def check_for_app_updates(self):
        if self._update_thread is not None:
            self.statusBar().showMessage("Update check is already running...", 3000)
            return

        self.statusBar().showMessage("Checking for updates...", 0)
        if hasattr(self, "check_updates_action"):
            self.check_updates_action.setEnabled(False)

        self._update_thread = QThread(self)
        self._update_worker = UpdateCheckWorker(APP_VERSION)
        self._update_worker.moveToThread(self._update_thread)

        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.finished.connect(self._on_update_check_finished)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_thread.finished.connect(self._on_update_check_thread_finished)
        self._update_thread.finished.connect(self._update_worker.deleteLater)
        self._update_thread.finished.connect(self._update_thread.deleteLater)
        self._update_thread.start()

    def _on_update_check_thread_finished(self):
        self._update_worker = None
        self._update_thread = None
        if hasattr(self, "check_updates_action"):
            self.check_updates_action.setEnabled(True)

    def _release_notes_preview(self, notes: str, limit: int = 1400) -> str:
        raw = re.sub(r"\r\n?", "\n", str(notes or "")).strip()
        if not raw:
            return ""

        section = self._extract_whats_new_section(raw)
        clean = self._markdown_to_plain_text(section)
        if len(clean) <= limit:
            return clean
        return f"{clean[:limit].rstrip()}..."

    def _extract_whats_new_section(self, markdown_text: str) -> str:
        """
        Return the "What's New" section (if present) from markdown release notes.
        Fallback: return the original markdown text.
        """
        text = re.sub(r"\r\n?", "\n", str(markdown_text or "")).strip()
        if not text:
            return ""

        lines = text.split("\n")
        start = None
        start_level = None

        for i, line in enumerate(lines):
            m = re.match(r"^\s*(#{1,6})\s*(.+?)\s*$", line)
            if not m:
                continue
            heading_text = m.group(2).strip().lower()
            heading_text = re.sub(r"[^a-z0-9 ]+", " ", heading_text)
            heading_text = re.sub(r"\s+", " ", heading_text).strip()
            if "what s new" in heading_text or "whats new" in heading_text:
                start = i
                start_level = len(m.group(1))
                break

        if start is None:
            return text

        end = len(lines)
        for j in range(start + 1, len(lines)):
            m = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", lines[j])
            if not m:
                continue
            level = len(m.group(1))
            if level <= int(start_level or 6):
                end = j
                break

        return "\n".join(lines[start:end]).strip()

    def _markdown_to_plain_text(self, markdown_text: str) -> str:
        """
        Convert common markdown patterns into readable plain text.
        """
        text = re.sub(r"\r\n?", "\n", str(markdown_text or ""))
        if not text:
            return ""

        # Remove fenced code blocks fully.
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        # Inline code.
        text = re.sub(r"`([^`]*)`", r"\1", text)
        # Images.
        text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", text)
        # Links -> keep link text only.
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # Headings.
        text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
        # Emphasis / strong.
        text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
        text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
        # Block quotes.
        text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
        # Horizontal rules.
        text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
        # Normalize bullets.
        text = re.sub(r"^\s*[-*+]\s+", "- ", text, flags=re.MULTILINE)

        # Collapse extra blank lines and trim.
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _format_bytes(self, n_bytes: int) -> str:
        value = float(max(0, int(n_bytes)))
        units = ["B", "KB", "MB", "GB", "TB"]
        unit_index = 0
        while value >= 1024.0 and unit_index < (len(units) - 1):
            value /= 1024.0
            unit_index += 1
        if unit_index == 0:
            return f"{int(value)} {units[unit_index]}"
        return f"{value:.1f} {units[unit_index]}"

    def _suggest_update_filename(self, url: str, latest_version: str | None = None) -> str:
        try:
            parsed = urlparse(str(url or "").strip())
            basename = unquote(os.path.basename(parsed.path))
            if basename and basename not in {".", "/"}:
                return basename
        except Exception:
            pass

        version = str(latest_version or APP_VERSION).strip() or APP_VERSION
        if sys.platform.startswith("win"):
            ext = ".exe"
        elif sys.platform == "darwin":
            ext = ".dmg"
        elif sys.platform.startswith("linux"):
            ext = ".deb"
        else:
            ext = ".bin"
        return f"e-CALLISTO_FITS_Analyzer_v{version}{ext}"

    def _default_download_dir(self) -> str:
        candidates = [
            QStandardPaths.writableLocation(QStandardPaths.DownloadLocation),
            os.path.expanduser("~/Downloads"),
            os.path.expanduser("~"),
            os.getcwd(),
        ]
        for path in candidates:
            p = str(path or "").strip()
            if p and os.path.isdir(p):
                return p
        return os.getcwd()

    def _open_update_url(self, url: str) -> None:
        text = str(url or "").strip()
        if not text:
            return
        if not QDesktopServices.openUrl(QUrl(text)):
            QMessageBox.warning(self, "Open URL Failed", f"Could not open URL:\n{text}")

    def _show_update_download_progress_dialog(self):
        self._close_update_download_progress_dialog()
        dlg = QProgressDialog("Downloading update...", "Cancel", 0, 0, self)
        dlg.setWindowTitle("Downloading Update")
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        dlg.canceled.connect(self._cancel_update_download)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._update_download_progress_dialog = dlg

    def _close_update_download_progress_dialog(self):
        dlg = getattr(self, "_update_download_progress_dialog", None)
        if dlg is None:
            return
        try:
            dlg.close()
            dlg.deleteLater()
        except Exception:
            pass
        self._update_download_progress_dialog = None

    def _cancel_update_download(self):
        worker = getattr(self, "_update_download_worker", None)
        if worker is None:
            return
        try:
            worker.request_cancel()
        except Exception:
            pass

    @Slot(int, int)
    def _on_update_download_progress(self, downloaded_bytes: int, total_bytes: int):
        dlg = getattr(self, "_update_download_progress_dialog", None)
        if dlg is None:
            return

        downloaded = max(0, int(downloaded_bytes))
        total = max(0, int(total_bytes))
        if total > 0:
            if dlg.minimum() != 0 or dlg.maximum() != total:
                dlg.setRange(0, total)
            dlg.setValue(min(downloaded, total))
            percent = (100.0 * downloaded / total) if total > 0 else 0.0
            dlg.setLabelText(
                f"Downloading update... {percent:.1f}% "
                f"({self._format_bytes(downloaded)} / {self._format_bytes(total)})"
            )
        else:
            dlg.setRange(0, 0)
            if downloaded > 0:
                dlg.setLabelText(f"Downloading update... {self._format_bytes(downloaded)}")
            else:
                dlg.setLabelText("Downloading update...")

    @Slot(str)
    def _on_update_download_finished(self, path: str):
        self._close_update_download_progress_dialog()
        out_path = str(path or "").strip()
        self.statusBar().showMessage("Update downloaded successfully.", 5000)

        msg = QMessageBox(self)
        msg.setWindowTitle("Update Downloaded")
        msg.setIcon(QMessageBox.Information)
        msg.setText("The update was downloaded successfully.")
        msg.setInformativeText(
            f"Saved to:\n{out_path}\n\n"
            "Close the app and run this installer/package to complete the update."
        )
        open_file_btn = msg.addButton("Open File", QMessageBox.ActionRole)
        open_folder_btn = msg.addButton("Open Folder", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Ok)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == open_file_btn and out_path:
            url = QUrl.fromLocalFile(out_path)
            if not QDesktopServices.openUrl(url):
                QMessageBox.warning(self, "Open File Failed", f"Could not open file:\n{out_path}")
        elif clicked == open_folder_btn and out_path:
            folder = os.path.dirname(out_path) or out_path
            url = QUrl.fromLocalFile(folder)
            if not QDesktopServices.openUrl(url):
                QMessageBox.warning(self, "Open Folder Failed", f"Could not open folder:\n{folder}")

    @Slot(str)
    def _on_update_download_failed(self, message: str):
        self._close_update_download_progress_dialog()
        self.statusBar().showMessage("Update download failed.", 5000)
        QMessageBox.critical(
            self,
            "Update Download Failed",
            f"Could not download the update.\n\n{str(message or 'Unknown error.')}",
        )

    @Slot()
    def _on_update_download_cancelled(self):
        self._close_update_download_progress_dialog()
        self.statusBar().showMessage("Update download cancelled.", 5000)

    def _on_update_download_thread_finished(self):
        self._update_download_worker = None
        self._update_download_thread = None

    def _start_update_download(self, result):
        if self._update_download_thread is not None:
            QMessageBox.information(self, "Update Download", "An update download is already in progress.")
            return

        download_url = str(getattr(result, "download_url", "") or "").strip()
        if not download_url:
            QMessageBox.warning(self, "Update Download", "No download URL is available for this update.")
            return

        default_name = self._suggest_update_filename(
            download_url,
            latest_version=getattr(result, "latest_version", None),
        )
        default_path = os.path.join(self._default_download_dir(), default_name)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Update Installer",
            default_path,
            "Installer files (*.exe *.msi *.dmg *.pkg *.deb *.appimage *.zip *.tar.gz);;All files (*)",
        )
        if not path:
            return

        save_path = str(path).strip()
        if not save_path:
            return
        if os.path.isdir(save_path):
            QMessageBox.warning(self, "Invalid Path", "Please choose a file path, not a folder.")
            return

        self._show_update_download_progress_dialog()
        self._update_download_thread = QThread(self)
        self._update_download_worker = UpdateDownloadWorker(download_url, save_path)
        self._update_download_worker.moveToThread(self._update_download_thread)

        self._update_download_thread.started.connect(self._update_download_worker.run)
        self._update_download_worker.progress.connect(self._on_update_download_progress)
        self._update_download_worker.finished.connect(self._on_update_download_finished)
        self._update_download_worker.failed.connect(self._on_update_download_failed)
        self._update_download_worker.cancelled.connect(self._on_update_download_cancelled)

        self._update_download_worker.finished.connect(self._update_download_thread.quit)
        self._update_download_worker.failed.connect(self._update_download_thread.quit)
        self._update_download_worker.cancelled.connect(self._update_download_thread.quit)
        self._update_download_thread.finished.connect(self._on_update_download_thread_finished)
        self._update_download_thread.finished.connect(self._update_download_worker.deleteLater)
        self._update_download_thread.finished.connect(self._update_download_thread.deleteLater)
        self._update_download_thread.start()

    def _show_update_available_dialog(self, result):
        lines = [
            f"Current version: v{result.current_version}",
            f"Latest version: v{result.latest_version}",
        ]
        if result.published_at:
            lines.append(f"Published: {result.published_at[:10]}")

        notes_preview = self._release_notes_preview(result.notes)

        msg = QMessageBox(self)
        msg.setWindowTitle("Update Available")
        msg.setIcon(QMessageBox.Information)
        msg.setText(f"A newer version of {APP_NAME} is available.")
        msg.setInformativeText("\n".join(lines))
        if notes_preview:
            msg.setDetailedText(notes_preview)

        download_btn = msg.addButton("Download", QMessageBox.AcceptRole)
        notes_btn = None
        if result.release_url and result.release_url != result.download_url:
            notes_btn = msg.addButton("Release Page", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Close)

        msg.exec()
        clicked = msg.clickedButton()
        if clicked == download_btn and result.download_url:
            self._start_update_download(result)
        elif notes_btn is not None and clicked == notes_btn and result.release_url:
            self._open_update_url(result.release_url)

    def _on_update_check_finished(self, result):
        now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._ui_settings.setValue("updates/last_checked_at", now_utc)

        if getattr(result, "is_error", False):
            self.statusBar().showMessage("Update check failed.", 5000)
            QMessageBox.warning(
                self,
                "Update Check Failed",
                f"Could not check for updates.\n\n{result.error or 'Unknown error.'}",
            )
            return

        if getattr(result, "update_available", False):
            self._ui_settings.setValue("updates/last_seen_version", result.latest_version or "")
            self.statusBar().showMessage(
                f"Update available: v{result.latest_version}",
                5000,
            )
            self._show_update_available_dialog(result)
            return

        self.statusBar().showMessage("You are already using the latest version.", 5000)
        QMessageBox.information(
            self,
            "Up to Date",
            f"You are using the latest version.\n\nCurrent: v{APP_VERSION}",
        )

    def show_about_dialog(self):
        QMessageBox.information(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME} version {APP_VERSION}.\n\n"
            "Developed by Sahan S Liyanage\n\n"
            "Astronomical and Space Science Unit\n"
            "University of Colombo, Sri Lanka\n\n"
            "2026©Copyright, All Rights Reserved."
        )

    def _bug_report_default_dir(self) -> str:
        candidates = [
            QStandardPaths.writableLocation(QStandardPaths.DownloadLocation),
            os.path.expanduser("~/Downloads"),
            os.path.expanduser("~"),
            os.getcwd(),
        ]
        for path in candidates:
            p = str(path or "").strip()
            if p and os.path.isdir(p):
                return p
        return os.getcwd()

    def _build_bug_report_context(self) -> dict:
        env = {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "qt_binding": "PySide6",
        }
        summary = {
            "filename": str(getattr(self, "filename", "") or ""),
            "plot_type": str(getattr(self, "current_plot_type", "") or ""),
            "cmap": str(getattr(self, "current_cmap_name", "") or ""),
            "use_db": bool(getattr(self, "use_db", False)),
            "use_utc": bool(getattr(self, "use_utc", False)),
        }
        session = {
            "is_combined": bool(getattr(self, "_is_combined", False)),
            "combined_mode": getattr(self, "_combined_mode", None),
            "source_path": str(getattr(self, "_fits_source_path", "") or ""),
            "project_path": str(getattr(self, "_project_path", "") or ""),
        }

        return {
            "summary": summary,
            "environment": env,
            "session": session,
            "operation_log": list(getattr(self, "_processing_log", []) or []),
            "processing": dict((self._build_provenance_context() or {}).get("processing") or {}),
            "rfi": dict(getattr(self, "_rfi_config", {}) or {}),
        }

    def _on_bug_report_dialog_destroyed(self, *_):
        self._bug_report_dialog = None

    def open_bug_report_dialog(self):
        try:
            alive = self._bug_report_dialog is not None
            if alive:
                _ = self._bug_report_dialog.windowTitle()
        except Exception:
            alive = False

        if not alive:
            self._bug_report_dialog = BugReportDialog(
                repo=GITHUB_REPO,
                context_provider=self._build_bug_report_context,
                provenance_provider=lambda: build_provenance_payload(self._build_provenance_context()),
                default_dir_provider=self._bug_report_default_dir,
                parent=self,
            )
            self._bug_report_dialog.setAttribute(Qt.WA_DeleteOnClose, True)
            self._bug_report_dialog.destroyed.connect(self._on_bug_report_dialog_destroyed)

        self._bug_report_dialog.show()
        self._bug_report_dialog.raise_()
        self._bug_report_dialog.activateWindow()

    def reset_selection(self):
        had_drift_points = self._clear_drift_overlays(keep_view=True)

        if self.noise_reduced_original is not None:
            self._push_undo_state()
            self.noise_reduced_data = self.noise_reduced_original.copy()
            if self.time is not None and self.freqs is not None:
                self.plot_data(self.noise_reduced_data, title="Background Subtracted")
            self.lasso_mask = None
            self.lasso = None
            if had_drift_points:
                self.statusBar().showMessage("Selection reset (drift markers cleared)", 4000)
            else:
                self.statusBar().showMessage("Selection Reset", 4000)
            print("Lasso selection reset. Original noise-reduced data restored.")
            self._sync_toolbar_enabled_states()
        else:
            # If no selection exists, treat this as a "reset view" (home) action after zoom/pan.
            view_reset = False
            try:
                cur_view = self._capture_view()
                home_view = getattr(self, "_home_view", None)
                if cur_view and home_view and (not self._views_close(cur_view, home_view)):
                    self._push_undo_view(cur_view)
                    self._restore_view(home_view)
                    if self._hardware_mode_enabled():
                        self._show_accel_canvas()
                    else:
                        self.canvas.draw_idle()
                    view_reset = True
                    if had_drift_points:
                        self.statusBar().showMessage("View reset (drift markers cleared)", 2500)
                    else:
                        self.statusBar().showMessage("View reset", 2500)
                elif had_drift_points:
                    self.statusBar().showMessage("Drift markers reset", 2500)
                else:
                    print("No noise-reduced backup found. Reset skipped.")
            finally:
                self._sync_toolbar_enabled_states()

    def open_combine_freq_window(self):
        dialog = CombineFrequencyDialog(self)
        dialog.exec()

    def open_combine_time_window(self):
        dialog = CombineTimeDialog(self)
        dialog.exec()

    def _on_batch_processing_dialog_destroyed(self, *_):
        self._batch_processing_dialog = None

    def open_batch_processing_window(self):
        try:
            alive = self._batch_processing_dialog is not None
            if alive:
                _ = self._batch_processing_dialog.windowTitle()
        except Exception:
            alive = False

        if not alive:
            self._batch_processing_dialog = BatchProcessingDialog(
                cmap_name_provider=lambda: str(self.current_cmap_name or "Custom"),
                cold_digits_provider=lambda: float(self._db_hot_cold_digits()[0]),
                parent=self,
            )
            self._batch_processing_dialog.setAttribute(Qt.WA_DeleteOnClose, True)
            self._batch_processing_dialog.destroyed.connect(self._on_batch_processing_dialog_destroyed)

        self._batch_processing_dialog.show()
        self._batch_processing_dialog.raise_()
        self._batch_processing_dialog.activateWindow()

    def _set_checked_if_exists(self, attr_name: str, checked: bool):
        obj = getattr(self, attr_name, None)
        if obj is None:
            return
        try:
            was_blocked = obj.blockSignals(True)
            obj.setChecked(checked)
        except Exception:
            pass
        finally:
            try:
                obj.blockSignals(was_blocked)
            except Exception:
                pass

    def set_axis_to_seconds(self):
        self.use_utc = False

        # Old Graph-menu actions (may not exist anymore)
        self._set_checked_if_exists("xaxis_sec_action", True)
        self._set_checked_if_exists("xaxis_ut_action", False)

        # If you are using radio buttons, store them as these names (optional)
        self._set_checked_if_exists("xaxis_sec_radio", True)
        self._set_checked_if_exists("xaxis_ut_radio", False)
        self._set_checked_if_exists("time_sec_radio", True)
        self._set_checked_if_exists("time_ut_radio", False)

        if self.raw_data is not None:
            self._mark_project_dirty()
            data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
            self.plot_data(data, title=self.current_plot_type, keep_view=True)

    def set_axis_to_utc(self):
        self.use_utc = True

        # Old Graph-menu actions (may not exist anymore)
        self._set_checked_if_exists("xaxis_sec_action", False)
        self._set_checked_if_exists("xaxis_ut_action", True)

        # If you are using radio buttons, store them as these names (optional)
        self._set_checked_if_exists("xaxis_sec_radio", False)
        self._set_checked_if_exists("xaxis_ut_radio", True)
        self._set_checked_if_exists("time_sec_radio", False)
        self._set_checked_if_exists("time_ut_radio", True)

        if self.raw_data is not None:
            self._mark_project_dirty()
            data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
            self.plot_data(data, title=self.current_plot_type, keep_view=True)

    def format_axes(self):
        if self.use_utc and self.ut_start_sec is not None:
            def format_func(x, pos):
                # Show seconds when viewing a short span (e.g. short files or zoomed-in region)
                try:
                    x0, x1 = self.canvas.ax.get_xlim()
                    span = abs(float(x1) - float(x0))
                except Exception:
                    span = None

                show_seconds = (span is not None) and (span <= 5 * 60)

                total_seconds = float(self.ut_start_sec) + float(x)
                total_seconds_i = int(round(total_seconds))

                hours = int(total_seconds_i // 3600) % 24
                minutes = int((total_seconds_i % 3600) // 60)
                seconds = int(total_seconds_i % 60)

                if show_seconds:
                    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                return f"{hours:02d}:{minutes:02d}"

            self.canvas.ax.xaxis.set_major_formatter(FuncFormatter(format_func))
            self.canvas.ax.set_xlabel("Time [UT]")
        else:
            self.canvas.ax.xaxis.set_major_formatter(ScalarFormatter())
            self.canvas.ax.set_xlabel("Time [s]")

        self.canvas.ax.figure.canvas.draw()
        if getattr(self, "accel_canvas", None) is not None and self.accel_canvas.is_available:
            self.accel_canvas.set_time_mode(self.use_utc, self.ut_start_sec)

    def _show_import_progress_dialog(self, total_steps: int):
        self._close_import_progress_dialog()

        parent = self
        downloader = getattr(self, "downloader_dialog", None)
        if downloader is not None:
            try:
                if downloader.isVisible():
                    parent = downloader
            except Exception:
                pass

        dlg = QProgressDialog("Downloading selected FITS files...", "", 0, max(1, int(total_steps)), parent)
        dlg.setWindowTitle("Importing FITS Files")
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

        self._import_progress_dialog = dlg

    def _close_import_progress_dialog(self):
        dlg = getattr(self, "_import_progress_dialog", None)
        if dlg is None:
            return
        try:
            dlg.close()
            dlg.deleteLater()
        except Exception:
            pass
        self._import_progress_dialog = None

    @Slot(str)
    def _on_import_progress_text(self, text: str):
        dlg = getattr(self, "_import_progress_dialog", None)
        if dlg is None:
            return
        dlg.setLabelText(str(text or "Importing FITS files..."))

    @Slot(int, int)
    def _on_import_progress_range(self, minimum: int, maximum: int):
        dlg = getattr(self, "_import_progress_dialog", None)
        if dlg is None:
            return
        mn = int(minimum)
        mx = max(int(maximum), mn)
        dlg.setRange(mn, mx)
        if dlg.value() < mn:
            dlg.setValue(mn)

    @Slot(int)
    def _on_import_progress_value(self, value: int):
        dlg = getattr(self, "_import_progress_dialog", None)
        if dlg is None:
            return
        v = int(value)
        if v > dlg.maximum():
            dlg.setMaximum(v)
        dlg.setValue(v)

    def _cleanup_import_worker(self):
        thread = getattr(self, "_import_thread", None)
        if thread is not None:
            try:
                thread.deleteLater()
            except Exception:
                pass
        self._import_thread = None
        self._import_worker = None

    def _emit_downloader_import_success(self):
        dlg = getattr(self, "downloader_dialog", None)
        if dlg is None:
            return
        try:
            dlg.import_success.emit()
        except Exception:
            pass

    def _load_single_import_payload(self, payload: dict):
        data = payload.get("data", None)
        freqs = payload.get("freqs", None)
        time = payload.get("time", None)
        if data is None or freqs is None or time is None:
            raise ValueError("Imported FITS payload is incomplete.")

        hdr0 = payload.get("header0", None)
        self._apply_loaded_dataset(
            data=data,
            freqs=freqs,
            time=time,
            filename=str(payload.get("filename", "") or "Imported"),
            header0=hdr0,
            source_path=payload.get("source_path", None),
            ut_start_sec=payload.get("ut_start_sec", None),
            combined_mode=None,
            combined_sources=[],
            plot_title="Raw",
        )

    @Slot(object)
    def _on_downloader_import_finished(self, payload):
        self._close_import_progress_dialog()

        try:
            kind = payload.get("kind", "") if isinstance(payload, dict) else ""

            if kind == "single":
                self._load_single_import_payload(payload)
                self._emit_downloader_import_success()
                return

            if kind == "combined":
                combined = payload.get("combined", None) if isinstance(payload, dict) else None
                if combined is None:
                    QMessageBox.critical(self, "Import Failed", "Combined FITS payload is missing.")
                    return
                self.load_combined_into_main(combined)
                self._emit_downloader_import_success()
                return

            if kind == "invalid":
                QMessageBox.warning(
                    self,
                    "Invalid Selection",
                    "Selected files cannot be time-combined or frequency-combined.\n"
                    "Please ensure they are consecutive in time or adjacent in frequency."
                )
                return

            QMessageBox.critical(self, "Import Failed", "Unexpected import result from downloader.")
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", f"Could not import downloaded files:\n{e}")

    @Slot(str)
    def _on_downloader_import_failed(self, message: str):
        self._close_import_progress_dialog()
        QMessageBox.critical(self, "Import Failed", str(message or "Import failed."))

    def process_imported_files(self, urls):
        if not urls:
            QMessageBox.warning(self, "No Files", "No files were received from the downloader.")
            return

        if not self._maybe_prompt_save_dirty():
            return

        if self._import_thread is not None and self._import_thread.isRunning():
            QMessageBox.information(self, "Import In Progress", "Another FITS import is already running.")
            return

        self._show_import_progress_dialog(len(urls))

        self._import_thread = QThread(self)
        self._import_worker = DownloaderImportWorker(urls)
        self._import_worker.moveToThread(self._import_thread)

        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.progress_text.connect(self._on_import_progress_text)
        self._import_worker.progress_range.connect(self._on_import_progress_range)
        self._import_worker.progress_value.connect(self._on_import_progress_value)
        self._import_worker.finished.connect(self._on_downloader_import_finished)
        self._import_worker.failed.connect(self._on_downloader_import_failed)

        self._import_worker.finished.connect(self._import_thread.quit)
        self._import_worker.failed.connect(self._import_thread.quit)
        self._import_worker.finished.connect(self._import_worker.deleteLater)
        self._import_worker.failed.connect(self._import_worker.deleteLater)
        self._import_thread.finished.connect(self._cleanup_import_worker)

        self._import_thread.start()

    def _stop_rect_zoom(self):
        """Remove rectangle zoom selector safely (if active)."""
        sel = getattr(self, "_rect_selector", None)
        if sel is not None:
            try:
                sel.set_active(False)
                sel.disconnect_events()
            except Exception:
                pass
            self._rect_selector = None
        self.rect_zoom_active = False
        if self._hardware_mode_enabled():
            try:
                self.accel_canvas.cancel_rect_zoom()
                self.accel_canvas.set_navigation_locked(self.nav_locked)
            except Exception:
                pass

    def _on_rect_zoom_select(self, eclick, erelease):
        """Callback when the user finishes drawing the rectangle."""
        if eclick.inaxes != self.canvas.ax or erelease.inaxes != self.canvas.ax:
            self._stop_rect_zoom()
            return

        x0, y0 = eclick.xdata, eclick.ydata
        x1, y1 = erelease.xdata, erelease.ydata
        if x0 is None or x1 is None or y0 is None or y1 is None:
            self._stop_rect_zoom()
            return

        xmin, xmax = sorted([x0, x1])
        ymin, ymax = sorted([y0, y1])

        # Ignore tiny rectangles (prevents accidental clicks)
        if abs(xmax - xmin) < 1e-6 or abs(ymax - ymin) < 1e-6:
            self._stop_rect_zoom()
            return

        ax = self.canvas.ax
        prev_view = self._capture_view()
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        if prev_view:
            self._push_undo_view(prev_view)
        self.canvas.draw_idle()

        self._stop_rect_zoom()
        self.statusBar().showMessage("Zoomed to selected region (still locked).", 2500)
        self._sync_toolbar_enabled_states()

    def _sync_nav_actions(self):
        """Enable/disable Lock/Unlock/Zoom actions to match current state."""
        locked = bool(getattr(self, "nav_locked", False))
        has_plot = getattr(self, "raw_data", None) is not None

        # Zoom rectangle allowed only when locked AND a plot exists
        self.tb_zoom.setEnabled(locked and has_plot)

        # Lock and unlock behave like mutually exclusive buttons
        self.tb_lock.setEnabled((not locked) and has_plot)
        self.tb_unlock.setEnabled(locked and has_plot)

    def lock(self):
        """Disable scroll zoom + panning. Rectangle zoom becomes available."""
        if getattr(self, "raw_data", None) is None:
            self.statusBar().showMessage("Load a FITS file first.", 2500)
            return

        self.nav_locked = True
        self._panning = False
        self._last_pan_xy = None
        self._stop_rect_zoom()
        if self._hardware_mode_enabled():
            self.accel_canvas.set_navigation_locked(True)

        self._sync_nav_actions()
        self.statusBar().showMessage("Navigation locked. Use Rectangle Zoom if needed.", 3000)

    def unlock(self):
        """Enable scroll zoom + panning again."""
        if getattr(self, "raw_data", None) is None:
            self.statusBar().showMessage("Load a FITS file first.", 2500)
            return

        self.nav_locked = False
        self._panning = False
        self._last_pan_xy = None
        self._stop_rect_zoom()
        if self._hardware_mode_enabled():
            self.accel_canvas.set_navigation_locked(False)

        self._sync_nav_actions()
        self.statusBar().showMessage("Navigation unlocked. Pan and scroll zoom enabled.", 3000)

    def rectangular_zoom(self):
        """
        Start rectangle zoom tool.
        Requirement: only allowed when lock is active.
        """
        if getattr(self, "raw_data", None) is None:
            self.statusBar().showMessage("Load a FITS file first.", 2500)
            return

        if not getattr(self, "nav_locked", False):
            self.statusBar().showMessage("Click Lock first to enable Rectangle Zoom.", 3500)
            return

        # Do not conflict with lasso
        if getattr(self, "lasso_active", False):
            self.statusBar().showMessage("Finish the lasso tool first.", 3000)
            return

        # Stop any existing rectangle selector
        self._stop_rect_zoom()

        if self._hardware_mode_enabled():
            self.rect_zoom_active = True
            self.accel_canvas.start_rect_zoom_once()
            self.statusBar().showMessage("Drag a rectangle on the plot to zoom.", 4000)
            return

        ax = self.canvas.ax
        self.rect_zoom_active = True

        # Create rectangle selector
        self._rect_selector = RectangleSelector(
            ax,
            self._on_rect_zoom_select,
            useblit=True,
            button=[1],  # left mouse only
            interactive=False
        )

        self.statusBar().showMessage("Drag a rectangle on the plot to zoom.", 4000)

    def launch_downloader(self):
        self.downloader_dialog = CallistoDownloaderApp()
        self.downloader_dialog.import_request.connect(self.process_imported_files)

        self.import_success_signal = lambda: self.downloader_dialog.accept()
        self.downloader_dialog.import_success.connect(self.import_success_signal)

        self.downloader_dialog.exec()

    def open_goes_xrs_window(self):
        try:
            alive = self._goes_window is not None
            if alive:
                _ = self._goes_window.windowTitle()
        except Exception:
            alive = False
        if not alive:
            self._goes_window = GoesXrsWindow()
        self._goes_window.show()
        self._goes_window.raise_()
        self._goes_window.activateWindow()

        window = self._current_time_window_utc()
        if window and hasattr(self._goes_window, "set_time_window"):
            try:
                self._goes_window.set_time_window(window[0], window[1], auto_plot=True)
            except Exception:
                pass

    def open_soho_lasco_window(self):
        self.open_cme_viewer()

    def _capture_state(self):
        """Capture the current application state for Undo/Redo."""
        state = {
            "raw_data": None if self.raw_data is None else self.raw_data.copy(),
            "noise_reduced_data": None if self.noise_reduced_data is None else self.noise_reduced_data.copy(),
            "noise_reduced_original": None if self.noise_reduced_original is None else self.noise_reduced_original.copy(),
            "lasso_mask": None if self.lasso_mask is None else self.lasso_mask.copy(),
            "freqs": None if self.freqs is None else self.freqs.copy(),
            "time": None if self.time is None else self.time.copy(),
            "filename": self.filename,
            "current_plot_type": self.current_plot_type,
            "lower_slider": self.lower_slider.value(),
            "upper_slider": self.upper_slider.value(),
            "use_db": self.use_db,
            "use_utc": self.use_utc,
            "cmap": self.current_cmap_name,
            "view": self._capture_view(),
        }
        return state

    def _entry_kind(self, entry) -> str:
        if isinstance(entry, dict) and entry.get("kind") in ("state", "view"):
            return entry["kind"]
        return "state"

    def _entry_state(self, entry):
        if self._entry_kind(entry) != "state":
            return None
        if isinstance(entry, dict) and "state" in entry:
            return entry["state"]
        return entry

    def _entry_view(self, entry):
        kind = self._entry_kind(entry)
        if kind == "view":
            return entry.get("view") if isinstance(entry, dict) else None
        st = self._entry_state(entry)
        if isinstance(st, dict):
            return st.get("view")
        return None

    def _count_state_entries(self, stack) -> int:
        n = 0
        for e in stack:
            if self._entry_kind(e) == "state":
                n += 1
        return n

    def _trim_history(self):
        # Enforce limit on heavy (state) snapshots by dropping the oldest state entry,
        # along with any older view entries before it.
        while self._count_state_entries(self._undo_stack) > self._max_undo:
            drop_to = None
            for i, e in enumerate(self._undo_stack):
                if self._entry_kind(e) == "state":
                    drop_to = i
                    break
            if drop_to is None:
                break
            del self._undo_stack[: drop_to + 1]

        # Guardrail to prevent unbounded growth from view-history spam
        if len(self._undo_stack) > self._max_history_entries:
            extra = len(self._undo_stack) - self._max_history_entries
            del self._undo_stack[:extra]

        if len(self._redo_stack) > self._max_history_entries:
            extra = len(self._redo_stack) - self._max_history_entries
            del self._redo_stack[:extra]

    def _views_close(self, a, b, tol: float = 1e-6) -> bool:
        if not a or not b:
            return False
        for key in ("xlim", "ylim"):
            try:
                a0, a1 = a.get(key)
                b0, b1 = b.get(key)
            except Exception:
                return False
            try:
                a0 = float(a0)
                a1 = float(a1)
                b0 = float(b0)
                b1 = float(b1)
            except Exception:
                return False
            if abs(a0 - b0) > tol or abs(a1 - b1) > tol:
                return False
        return True

    def _push_undo_view(self, view):
        if not view:
            return
        # Avoid pushing duplicate consecutive view entries
        if self._undo_stack and self._entry_kind(self._undo_stack[-1]) == "view":
            last_view = self._entry_view(self._undo_stack[-1])
            if last_view and self._views_close(last_view, view):
                return

        self._undo_stack.append({"kind": "view", "view": view})
        self._redo_stack.clear()
        self._trim_history()

    def _push_undo_state(self):
        self._undo_stack.append({"kind": "state", "state": self._capture_state()})
        self._redo_stack.clear()
        self._trim_history()
        self._mark_project_dirty()

    def _restore_state(self, state):
        """Restore a previously captured application state."""
        self.raw_data = state["raw_data"]
        self._invalidate_noise_cache()
        self.noise_reduced_data = state["noise_reduced_data"]
        self.noise_reduced_original = state["noise_reduced_original"]
        self.lasso_mask = state["lasso_mask"]
        self.freqs = state["freqs"]
        self.time = state["time"]
        self.filename = state["filename"]
        self.current_plot_type = state["current_plot_type"]
        self.use_db = state["use_db"]
        self.use_utc = state["use_utc"]
        self.current_cmap_name = state["cmap"]

        self.lower_slider.blockSignals(True)
        self.upper_slider.blockSignals(True)
        self.lower_slider.setValue(state["lower_slider"])
        self.upper_slider.setValue(state["upper_slider"])
        self.lower_slider.blockSignals(False)
        self.upper_slider.blockSignals(False)

        if self.raw_data is not None:
            data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
            self.plot_data(data, title=self.current_plot_type, restore_view=state.get("view"))

    def undo(self):
        if not self._undo_stack:
            self.statusBar().showMessage("Nothing to undo", 2000)
            return
        entry = self._undo_stack.pop()
        kind = self._entry_kind(entry)

        if kind == "view":
            cur_view = self._capture_view()
            if cur_view:
                self._redo_stack.append({"kind": "view", "view": cur_view})
                self._trim_history()

            view = self._entry_view(entry)
            if view:
                self._restore_view(view)
                self.canvas.draw_idle()
        else:
            current = {"kind": "state", "state": self._capture_state()}
            self._redo_stack.append(current)
            self._trim_history()

            state = self._entry_state(entry)
            if state:
                self._restore_state(state)

        self.statusBar().showMessage("Undo", 2000)
        self._sync_toolbar_enabled_states()

    def redo(self):
        if not self._redo_stack:
            self.statusBar().showMessage("Nothing to redo", 2000)
            return
        entry = self._redo_stack.pop()
        kind = self._entry_kind(entry)

        if kind == "view":
            cur_view = self._capture_view()
            if cur_view:
                self._undo_stack.append({"kind": "view", "view": cur_view})
                self._trim_history()

            view = self._entry_view(entry)
            if view:
                self._restore_view(view)
                self.canvas.draw_idle()
        else:
            current = {"kind": "state", "state": self._capture_state()}
            self._undo_stack.append(current)
            self._trim_history()

            state = self._entry_state(entry)
            if state:
                self._restore_state(state)

        self.statusBar().showMessage("Redo", 2000)
        self._sync_toolbar_enabled_states()

    # -----------------------------
    # Project/session Save + Load
    # -----------------------------

    def _log_operation(self, message: str) -> None:
        msg = str(message or "").strip()
        if not msg:
            return
        self._processing_log.append(
            {
                "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "msg": msg,
            }
        )
        if len(self._processing_log) > 500:
            self._processing_log = self._processing_log[-500:]

    def _perform_autosave(self):
        if getattr(self, "raw_data", None) is None:
            return
        if not getattr(self, "_project_dirty", False):
            return

        try:
            meta, arrays = self._capture_project_payload()
            save_recovery_snapshot(
                meta=meta,
                arrays=arrays,
                source_project_path=getattr(self, "_project_path", None),
                reason="timer",
                max_snapshots=DEFAULT_MAX_SNAPSHOTS,
            )
            self.statusBar().showMessage("Autosaved recovery snapshot.", 2500)
        except Exception as e:
            self.statusBar().showMessage(f"Autosave failed: {e}", 4000)

    def _load_snapshot_path(self, path: str, *, mark_dirty: bool = True) -> bool:
        if not path:
            return False
        try:
            payload = load_recovery_snapshot(path)
        except Exception as e:
            QMessageBox.warning(self, "Recovery Failed", f"Could not open recovery snapshot:\n{e}")
            return False

        self._apply_project_payload(payload.meta, payload.arrays)
        self._set_project_clean(None)
        if mark_dirty:
            self._mark_project_dirty()
        self.statusBar().showMessage(f"Recovered session from {os.path.basename(path)}", 5000)
        self._log_operation(f"Recovered snapshot: {os.path.basename(path)}")
        return True

    def _prompt_recovery_if_needed(self):
        # Prevent blocking recovery prompts in automated/headless runs.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if os.environ.get("E_CALLISTO_DISABLE_RECOVERY_PROMPT", "").strip() == "1":
            return
        clean_exit = bool(getattr(self, "_previous_clean_exit", True))
        if clean_exit:
            return

        path = latest_snapshot_path()
        if not path:
            return

        resp = QMessageBox.question(
            self,
            "Recover Last Session",
            "An unclean exit was detected. Recover the latest autosave snapshot?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self._load_snapshot_path(path, mark_dirty=True)

    def recover_last_session(self):
        path = latest_snapshot_path()
        if not path:
            QMessageBox.information(self, "Recover Last Session", "No recovery snapshot was found.")
            return
        self._load_snapshot_path(path, mark_dirty=True)

    def _ensure_rfi_dialog(self):
        if self._rfi_dialog is not None:
            return self._rfi_dialog
        self._rfi_dialog = RFIControlDialog(initial=self._rfi_config, parent=self)
        self._rfi_dialog.previewRequested.connect(self._preview_rfi_from_dialog)
        self._rfi_dialog.applyRequested.connect(self._apply_rfi_from_dialog)
        self._rfi_dialog.resetRequested.connect(self.reset_rfi)
        return self._rfi_dialog

    def _rfi_source_data(self):
        if self.noise_reduced_data is not None:
            return self.noise_reduced_data
        return self.raw_data

    def _preview_rfi_from_dialog(self, cfg: dict):
        self._rfi_config.update(dict(cfg or {}))
        src = self._rfi_source_data()
        if src is None:
            QMessageBox.information(self, "RFI Cleaning", "Load a FITS file first.")
            return

        try:
            result = clean_rfi(
                src,
                kernel_time=int(self._rfi_config.get("kernel_time", 3)),
                kernel_freq=int(self._rfi_config.get("kernel_freq", 3)),
                channel_z_threshold=float(self._rfi_config.get("channel_z_threshold", 6.0)),
                percentile_clip=float(self._rfi_config.get("percentile_clip", 99.5)),
                enabled=bool(self._rfi_config.get("enabled", True)),
            )
            self._rfi_preview_data = result.data
            self._rfi_preview_masked = list(result.masked_channel_indices)
            self._rfi_config = rfi_config_dict(
                enabled=bool(self._rfi_config.get("enabled", True)),
                kernel_time=int(self._rfi_config.get("kernel_time", 3)),
                kernel_freq=int(self._rfi_config.get("kernel_freq", 3)),
                channel_z_threshold=float(self._rfi_config.get("channel_z_threshold", 6.0)),
                percentile_clip=float(self._rfi_config.get("percentile_clip", 99.5)),
                masked_channel_indices=self._rfi_preview_masked,
                applied=False,
            )
            if self._rfi_dialog is not None:
                self._rfi_dialog.set_masked_channels(self._rfi_preview_masked)

            self.plot_data(self._rfi_preview_data, title=self.current_plot_type, keep_view=True)
            self.statusBar().showMessage("RFI preview updated.", 3000)
        except Exception as e:
            QMessageBox.warning(self, "RFI Preview Failed", str(e))

    def _apply_rfi_from_dialog(self, cfg: dict):
        self._rfi_config.update(dict(cfg or {}))
        self.apply_rfi_now()

    def open_rfi_panel(self):
        if self.raw_data is None:
            QMessageBox.information(self, "RFI Cleaning", "Load a FITS file first.")
            return
        dlg = self._ensure_rfi_dialog()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def apply_rfi_now(self):
        src = self._rfi_source_data()
        if src is None:
            QMessageBox.information(self, "Apply RFI", "Load a FITS file first.")
            return

        cfg = dict(self._rfi_config or {})
        if self._rfi_preview_data is None:
            self._preview_rfi_from_dialog(cfg)
        if self._rfi_preview_data is None:
            return

        self._push_undo_state()
        self.noise_reduced_data = np.asarray(self._rfi_preview_data, dtype=np.float32).copy()
        self.noise_reduced_original = self.noise_reduced_data.copy()
        self.current_plot_type = "RFI Cleaned"
        self._rfi_config = rfi_config_dict(
            enabled=bool(cfg.get("enabled", True)),
            kernel_time=int(cfg.get("kernel_time", 3)),
            kernel_freq=int(cfg.get("kernel_freq", 3)),
            channel_z_threshold=float(cfg.get("channel_z_threshold", 6.0)),
            percentile_clip=float(cfg.get("percentile_clip", 99.5)),
            masked_channel_indices=list(self._rfi_preview_masked),
            applied=True,
        )
        if self._rfi_dialog is not None:
            self._rfi_dialog.set_masked_channels(self._rfi_preview_masked)

        self.plot_data(self.noise_reduced_data, title="RFI Cleaned", keep_view=True)
        self._mark_project_dirty()
        self._log_operation(
            f"Applied RFI cleaning (kT={self._rfi_config['kernel_time']}, "
            f"kF={self._rfi_config['kernel_freq']}, "
            f"z>{self._rfi_config['channel_z_threshold']}, "
            f"pct={self._rfi_config['percentile_clip']})."
        )

    def reset_rfi(self):
        self._rfi_preview_data = None
        self._rfi_preview_masked = []
        self._rfi_config = rfi_config_dict(
            enabled=True,
            kernel_time=3,
            kernel_freq=3,
            channel_z_threshold=6.0,
            percentile_clip=99.5,
            masked_channel_indices=[],
            applied=False,
        )
        if self._rfi_dialog is not None:
            try:
                self._rfi_dialog.enabled_chk.setChecked(True)
                self._rfi_dialog.kernel_time_spin.setValue(3)
                self._rfi_dialog.kernel_freq_spin.setValue(3)
                self._rfi_dialog.z_thresh_spin.setValue(6.0)
                self._rfi_dialog.percentile_spin.setValue(99.5)
                self._rfi_dialog.set_masked_channels([])
            except Exception:
                pass
        if self.raw_data is not None:
            base = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
            self.plot_data(base, title=self.current_plot_type, keep_view=True)
        self.statusBar().showMessage("RFI settings reset.", 2500)

    def _annotation_disconnect_mpl(self):
        if self._annotation_mpl_cid is not None:
            try:
                self.canvas.mpl_disconnect(self._annotation_mpl_cid)
            except Exception:
                pass
            self._annotation_mpl_cid = None

    def _reset_annotation_mode(self):
        self._annotation_mode = None
        self._annotation_click_points = []
        self._annotation_pending_text = ""
        self.lasso_active = False
        self._annotation_disconnect_mpl()
        if self._hardware_mode_enabled():
            try:
                self.accel_canvas.stop_interaction_capture()
            except Exception:
                pass
        else:
            try:
                self.canvas.mpl_disconnect(self._cid_press)
            except Exception:
                pass
            try:
                self.canvas.mpl_disconnect(self._cid_motion)
            except Exception:
                pass
            try:
                self.canvas.mpl_disconnect(self._cid_release)
            except Exception:
                pass
            self._cid_press = self.canvas.mpl_connect("button_press_event", self.on_mouse_press)
            self._cid_motion = self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)
            self._cid_release = self.canvas.mpl_connect("button_release_event", self.on_mouse_release)

    def _render_annotations(self):
        # Matplotlib overlay
        try:
            ax = self.canvas.ax
            stale = getattr(self, "_annotation_artists", None) or []
            for artist in stale:
                try:
                    artist.remove()
                except Exception:
                    pass
            artists = []
            for ann in self._annotations:
                if not ann.get("visible", True):
                    continue
                kind = ann.get("kind")
                pts = ann.get("points") or []
                color = ann.get("color", "#00d4ff")
                lw = float(ann.get("line_width", 1.5))
                if kind in {"polygon", "line"} and len(pts) >= 2:
                    xs = [float(p[0]) for p in pts]
                    ys = [float(p[1]) for p in pts]
                    if kind == "polygon":
                        xs.append(xs[0]); ys.append(ys[0])
                    line = ax.plot(xs, ys, color=color, linewidth=lw, alpha=0.95)[0]
                    artists.append(line)
                elif kind == "text" and len(pts) >= 1:
                    x, y = pts[0]
                    text_artist = ax.text(
                        float(x),
                        float(y),
                        str(ann.get("text", "")),
                        color=color,
                        fontsize=10,
                        ha="left",
                        va="bottom",
                    )
                    artists.append(text_artist)
            self._annotation_artists = artists
            self.canvas.draw_idle()
        except Exception:
            pass

        # Hardware overlay
        try:
            self.accel_canvas.set_annotations(self._annotations if self._annotations_visible else [])
        except Exception:
            pass

    def _add_annotation(self, ann: dict):
        self._annotations.append(ann)
        self._render_annotations()
        self._mark_project_dirty()
        self._log_operation(f"Added annotation: {ann.get('kind')}")

    def start_annotation_polygon(self):
        if self.raw_data is None:
            QMessageBox.information(self, "Annotations", "Load a FITS file first.")
            return
        self._reset_annotation_mode()
        self._annotation_mode = "polygon"
        if self._hardware_mode_enabled():
            self.lasso_active = True
            self.accel_canvas.begin_lasso_capture()
            self.statusBar().showMessage("Draw polygon annotation and release mouse.", 5000)
            return

        try:
            self.canvas.mpl_disconnect(self._cid_press)
            self.canvas.mpl_disconnect(self._cid_motion)
            self.canvas.mpl_disconnect(self._cid_release)
        except Exception:
            pass
        self._annotation_mpl_cid = self.canvas.mpl_connect("button_press_event", self._on_annotation_mpl_click)
        self.statusBar().showMessage(
            "Click points for polygon. Right-click to finish.", 5000
        )

    def start_annotation_line(self):
        if self.raw_data is None:
            QMessageBox.information(self, "Annotations", "Load a FITS file first.")
            return
        if self._hardware_mode_enabled():
            self._show_plot_canvas()
        self._reset_annotation_mode()
        self._annotation_mode = "line"
        self._annotation_click_points = []
        try:
            self.canvas.mpl_disconnect(self._cid_press)
            self.canvas.mpl_disconnect(self._cid_motion)
            self.canvas.mpl_disconnect(self._cid_release)
        except Exception:
            pass
        self._annotation_mpl_cid = self.canvas.mpl_connect("button_press_event", self._on_annotation_mpl_click)
        self.statusBar().showMessage("Click start and end points for line annotation.", 5000)

    def start_annotation_text(self):
        if self.raw_data is None:
            QMessageBox.information(self, "Annotations", "Load a FITS file first.")
            return
        if self._hardware_mode_enabled():
            self._show_plot_canvas()
        txt, ok = QInputDialog.getText(self, "Add Text Annotation", "Annotation text:")
        if not ok or not str(txt).strip():
            return
        self._reset_annotation_mode()
        self._annotation_mode = "text"
        self._annotation_pending_text = str(txt).strip()
        try:
            self.canvas.mpl_disconnect(self._cid_press)
            self.canvas.mpl_disconnect(self._cid_motion)
            self.canvas.mpl_disconnect(self._cid_release)
        except Exception:
            pass
        self._annotation_mpl_cid = self.canvas.mpl_connect("button_press_event", self._on_annotation_mpl_click)
        self.statusBar().showMessage("Click where to place text annotation.", 5000)

    def _on_annotation_mpl_click(self, event):
        if event.inaxes != self.canvas.ax:
            return
        if event.xdata is None or event.ydata is None:
            return

        x = float(event.xdata)
        y = float(event.ydata)

        if self._annotation_mode == "line":
            self._annotation_click_points.append([x, y])
            if len(self._annotation_click_points) >= 2:
                ann = make_annotation(
                    kind="line",
                    points=self._annotation_click_points[:2],
                    color=self._annotation_style_defaults["color"],
                    line_width=self._annotation_style_defaults["line_width"],
                    visible=self._annotations_visible,
                )
                self._add_annotation(ann)
                self._reset_annotation_mode()
            return

        if self._annotation_mode == "text":
            ann = make_annotation(
                kind="text",
                points=[[x, y]],
                text=self._annotation_pending_text,
                color=self._annotation_style_defaults["color"],
                line_width=self._annotation_style_defaults["line_width"],
                visible=self._annotations_visible,
            )
            self._add_annotation(ann)
            self._reset_annotation_mode()
            return

        if self._annotation_mode == "polygon":
            if event.button == 3 and len(self._annotation_click_points) >= 3:
                ann = make_annotation(
                    kind="polygon",
                    points=self._annotation_click_points,
                    color=self._annotation_style_defaults["color"],
                    line_width=self._annotation_style_defaults["line_width"],
                    visible=self._annotations_visible,
                )
                self._add_annotation(ann)
                self._reset_annotation_mode()
                return
            self._annotation_click_points.append([x, y])

    def _on_annotation_polygon_finished(self, verts):
        if self._annotation_mode != "polygon":
            return
        if not verts or len(verts) < 3:
            self._reset_annotation_mode()
            return
        ann = make_annotation(
            kind="polygon",
            points=verts,
            color=self._annotation_style_defaults["color"],
            line_width=self._annotation_style_defaults["line_width"],
            visible=self._annotations_visible,
        )
        self._add_annotation(ann)
        self._reset_annotation_mode()

    def toggle_annotations_visibility(self):
        self._annotations_visible = not bool(self._annotations_visible)
        self._annotations = toggle_all_visibility(self._annotations, self._annotations_visible)
        self._render_annotations()
        self._mark_project_dirty()
        state = "shown" if self._annotations_visible else "hidden"
        self.statusBar().showMessage(f"Annotations {state}.", 2500)

    def delete_last_annotation(self):
        if not self._annotations:
            self.statusBar().showMessage("No annotations to delete.", 2500)
            return
        self._annotations.pop()
        self._render_annotations()
        self._mark_project_dirty()
        self.statusBar().showMessage("Deleted last annotation.", 2500)

    def clear_annotations(self):
        self._annotations = []
        self._render_annotations()
        self._mark_project_dirty()
        self.statusBar().showMessage("Cleared annotations.", 2500)

    def _preset_settings_payload(self) -> dict:
        return {
            "lower_slider": int(self.lower_slider.value()),
            "upper_slider": int(self.upper_slider.value()),
            "use_db": bool(self.use_db),
            "use_utc": bool(self.use_utc),
            "cmap": str(self.current_cmap_name or "Custom"),
            "graph": {
                "remove_titles": bool(getattr(self, "remove_titles", False)),
                "title_bold": bool(getattr(self, "title_bold", False)),
                "title_italic": bool(getattr(self, "title_italic", False)),
                "axis_bold": bool(getattr(self, "axis_bold", False)),
                "axis_italic": bool(getattr(self, "axis_italic", False)),
                "ticks_bold": bool(getattr(self, "ticks_bold", False)),
                "ticks_italic": bool(getattr(self, "ticks_italic", False)),
                "title_override": str(getattr(self, "graph_title_override", "")),
                "font_family": str(getattr(self, "graph_font_family", "")),
                "tick_font_px": int(getattr(self, "tick_font_px", 11)),
                "axis_label_font_px": int(getattr(self, "axis_label_font_px", 12)),
                "title_font_px": int(getattr(self, "title_font_px", 14)),
            },
            "rfi": dict(self._rfi_config or {}),
            "annotation_style_defaults": dict(self._annotation_style_defaults or {}),
        }

    def _load_global_presets(self) -> list[dict]:
        raw = self._ui_settings.value("processing/presets_json", "", type=str)
        return parse_presets_json(raw)

    def _save_global_presets(self, presets: list[dict]):
        self._ui_settings.setValue("processing/presets_json", dump_presets_json(presets))

    def save_current_preset(self):
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not str(name).strip():
            return
        preset = build_preset(str(name).strip(), self._preset_settings_payload())
        presets = self._load_global_presets()
        merged, replaced = upsert_preset(presets, preset)
        self._save_global_presets(merged)
        self._active_preset_snapshot = preset
        self._mark_project_dirty()
        verb = "Updated" if replaced else "Saved"
        self.statusBar().showMessage(f"{verb} preset '{preset['name']}'.", 3000)

    def _apply_preset_payload(self, preset: dict) -> bool:
        try:
            version = int((preset or {}).get("version", PRESET_SCHEMA_VERSION))
        except Exception:
            version = PRESET_SCHEMA_VERSION
        if version != PRESET_SCHEMA_VERSION:
            QMessageBox.warning(
                self,
                "Preset Version Unsupported",
                f"Preset version {version} is not supported by this build.",
            )
            return False

        settings = dict((preset or {}).get("settings") or {})
        self._active_preset_snapshot = dict(preset)

        try:
            self.lower_slider.blockSignals(True)
            self.upper_slider.blockSignals(True)
            self.lower_slider.setValue(int(settings.get("lower_slider", self.lower_slider.value())))
            self.upper_slider.setValue(int(settings.get("upper_slider", self.upper_slider.value())))
        finally:
            self.lower_slider.blockSignals(False)
            self.upper_slider.blockSignals(False)

        self.set_units_mode(bool(settings.get("use_db", False)))
        if bool(settings.get("use_utc", False)):
            self.set_axis_to_utc()
        else:
            self.set_axis_to_seconds()

        cmap = str(settings.get("cmap") or "Custom")
        self.current_cmap_name = cmap
        self.cmap_combo.setCurrentText(cmap)

        self._rfi_config = dict(settings.get("rfi") or self._rfi_config)
        self._annotation_style_defaults = dict(settings.get("annotation_style_defaults") or self._annotation_style_defaults)

        graph = dict(settings.get("graph") or {})
        self.remove_titles_chk.setChecked(bool(graph.get("remove_titles", self.remove_titles_chk.isChecked())))
        self.title_bold_chk.setChecked(bool(graph.get("title_bold", self.title_bold_chk.isChecked())))
        self.title_italic_chk.setChecked(bool(graph.get("title_italic", self.title_italic_chk.isChecked())))
        self.axis_bold_chk.setChecked(bool(graph.get("axis_bold", self.axis_bold_chk.isChecked())))
        self.axis_italic_chk.setChecked(bool(graph.get("axis_italic", self.axis_italic_chk.isChecked())))
        self.ticks_bold_chk.setChecked(bool(graph.get("ticks_bold", self.ticks_bold_chk.isChecked())))
        self.ticks_italic_chk.setChecked(bool(graph.get("ticks_italic", self.ticks_italic_chk.isChecked())))
        self.title_edit.setText(str(graph.get("title_override", self.title_edit.text())))
        self.font_combo.setCurrentText(str(graph.get("font_family", self.font_combo.currentText() or "Default")))
        self.tick_font_spin.setValue(int(graph.get("tick_font_px", self.tick_font_spin.value())))
        self.axis_font_spin.setValue(int(graph.get("axis_label_font_px", self.axis_font_spin.value())))
        self.title_font_spin.setValue(int(graph.get("title_font_px", self.title_font_spin.value())))

        if self.raw_data is not None:
            data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
            self.plot_data(data, title=self.current_plot_type, keep_view=True)

        self._mark_project_dirty()
        self._log_operation(f"Applied preset: {preset.get('name', 'Unnamed')}")
        return True

    def apply_saved_preset(self):
        presets = self._load_global_presets()
        if not presets:
            QMessageBox.information(self, "Apply Preset", "No presets have been saved yet.")
            return
        names = [p["name"] for p in presets]
        choice, ok = QInputDialog.getItem(self, "Apply Preset", "Choose preset:", names, 0, False)
        if not ok or not choice:
            return
        selected = next((p for p in presets if p["name"] == choice), None)
        if not selected:
            return
        self._apply_preset_payload(selected)

    def delete_saved_preset(self):
        presets = self._load_global_presets()
        if not presets:
            QMessageBox.information(self, "Delete Preset", "No presets are available.")
            return
        names = [p["name"] for p in presets]
        choice, ok = QInputDialog.getItem(self, "Delete Preset", "Choose preset:", names, 0, False)
        if not ok or not choice:
            return
        updated, removed = delete_preset(presets, str(choice))
        if removed:
            self._save_global_presets(updated)
            self.statusBar().showMessage(f"Deleted preset '{choice}'.", 2500)

    def _extract_observation_date(self) -> date | None:
        hdr = getattr(self, "_fits_header0", None)
        for key in ("DATE-OBS", "DATEOBS"):
            try:
                raw = str(hdr.get(key, "")).strip()
            except Exception:
                raw = ""
            if raw:
                try:
                    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
                except Exception:
                    try:
                        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
                    except Exception:
                        pass

        # Fallback: parse YYYYMMDD from filename
        m = re.search(r"(\d{8})", str(getattr(self, "filename", "")))
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y%m%d").date()
            except Exception:
                pass
        return None

    def _current_time_window_utc(self) -> tuple[datetime, datetime] | None:
        if self.time is None:
            return None
        if self.ut_start_sec is None:
            return None
        obs_date = self._extract_observation_date()
        if obs_date is None:
            return None

        view = self._capture_view() or {}
        xlim = view.get("xlim")
        if not xlim:
            return None
        x0 = float(min(xlim[0], xlim[1]))
        x1 = float(max(xlim[0], xlim[1]))
        if x1 <= x0:
            return None

        base = datetime(
            obs_date.year,
            obs_date.month,
            obs_date.day,
            tzinfo=timezone.utc,
        )
        start_dt = base + timedelta(seconds=float(self.ut_start_sec) + x0)
        end_dt = base + timedelta(seconds=float(self.ut_start_sec) + x1)
        return start_dt, end_dt

    def _sync_window_to_goes(self, start_dt: datetime, end_dt: datetime, *, auto_plot: bool = True) -> bool:
        if self._goes_window is None:
            return False
        if not hasattr(self._goes_window, "set_time_window"):
            return False
        try:
            return bool(self._goes_window.set_time_window(start_dt, end_dt, auto_plot=auto_plot))
        except Exception:
            return False

    def _sync_window_to_cme(self, target_dt: datetime, *, auto_search: bool = True) -> bool:
        if self._cme_viewer is None:
            return False
        if not hasattr(self._cme_viewer, "set_target_datetime"):
            return False
        try:
            self._cme_viewer.set_target_datetime(
                target_dt,
                auto_search=auto_search,
                auto_select_nearest=True,
            )
            return True
        except Exception:
            return False

    def sync_current_time_window_to_solar_events(self):
        window = self._current_time_window_utc()
        if not window:
            self.statusBar().showMessage("Time sync skipped: current UTC context is unavailable.", 4000)
            return
        start_dt, end_dt = window
        mid_dt = start_dt + (end_dt - start_dt) / 2

        goes_ok = self._sync_window_to_goes(start_dt, end_dt, auto_plot=True)
        cme_ok = self._sync_window_to_cme(mid_dt, auto_search=True)

        self._last_time_sync_context = {
            "start_utc": start_dt.isoformat(timespec="seconds"),
            "end_utc": end_dt.isoformat(timespec="seconds"),
            "target_utc": mid_dt.isoformat(timespec="seconds"),
            "goes_synced": bool(goes_ok),
            "cme_synced": bool(cme_ok),
        }
        self._log_operation("Synced current time window to Solar Events panels.")
        if not goes_ok and not cme_ok:
            self.statusBar().showMessage("No open GOES/CME windows to sync.", 4000)
        else:
            self.statusBar().showMessage("Synced current time window to Solar Events.", 4000)

    def _build_provenance_context(self) -> dict:
        shape = None
        if self.raw_data is not None:
            try:
                shape = [int(self.raw_data.shape[0]), int(self.raw_data.shape[1])]
            except Exception:
                shape = None
        freq_range = None
        if self.freqs is not None and len(self.freqs) > 0:
            freq_range = [float(np.nanmin(self.freqs)), float(np.nanmax(self.freqs))]
        time_range = None
        if self.time is not None and len(self.time) > 0:
            time_range = [float(np.nanmin(self.time)), float(np.nanmax(self.time))]

        context = {
            "app": {
                "name": APP_NAME,
                "version": APP_VERSION,
                "platform": platform.platform(),
            },
            "data_source": {
                "filename": self.filename,
                "is_combined": bool(self._is_combined),
                "combined_mode": self._combined_mode,
                "shape": shape,
                "freq_range_mhz": freq_range,
                "time_range_s": time_range,
                "sources": list(self._combined_sources or ([self._fits_source_path] if self._fits_source_path else [])),
            },
            "processing": {
                "plot_type": self.current_plot_type,
                "use_db": bool(self.use_db),
                "use_utc": bool(self.use_utc),
                "slider_low": int(self.lower_slider.value()),
                "slider_high": int(self.upper_slider.value()),
                "cmap": self.current_cmap_name,
                "graph": self._preset_settings_payload().get("graph", {}),
                "active_preset": dict(self._active_preset_snapshot or {}),
            },
            "rfi": dict(self._rfi_config or {}),
            "annotations": normalize_annotations(self._annotations),
            "max_intensity": self._max_intensity_state,
            "time_sync": dict(self._last_time_sync_context or {}),
            "operation_log": list(self._processing_log or []),
        }
        return context

    def export_provenance_report(self):
        if self.raw_data is None:
            QMessageBox.information(self, "Export Provenance", "Load a FITS file first.")
            return
        default_stem = self._sanitize_export_stem(self._current_graph_title_for_export())
        default_name = f"{default_stem}_provenance"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Provenance Report",
            default_name,
            "Provenance JSON (*.json);;Markdown (*.md);;All Files (*)",
        )
        if not path:
            return
        stem = os.path.splitext(path)[0]
        payload = build_provenance_payload(self._build_provenance_context())
        try:
            json_path, md_path = write_provenance_files(stem, payload)
            self.statusBar().showMessage(
                f"Provenance exported: {os.path.basename(json_path)}, {os.path.basename(md_path)}",
                5000,
            )
            self._log_operation("Exported provenance report (JSON + Markdown).")
        except Exception as e:
            QMessageBox.critical(self, "Export Provenance Failed", str(e))

    def _analysis_station_name(self) -> str:
        name = str(getattr(self, "filename", "") or "")
        if not name:
            return ""
        token = os.path.basename(name).split("_")[0]
        return token.strip()

    def _analysis_date_obs(self) -> str:
        obs_date = self._extract_observation_date()
        if obs_date is None:
            return ""
        return obs_date.isoformat()

    def export_analysis_log(self):
        if self.raw_data is None:
            QMessageBox.information(self, "Export Analysis Log", "Load a FITS file first.")
            return

        default_dir = ""
        if getattr(self, "_project_path", None):
            default_dir = os.path.dirname(self._project_path)
        elif getattr(self, "_fits_source_path", None):
            default_dir = os.path.dirname(self._fits_source_path)

        stem = self._sanitize_export_stem(self._current_graph_title_for_export())
        default_name = f"{stem}"
        if default_dir:
            default_name = os.path.join(default_dir, default_name)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Analysis Log",
            default_name,
            "CSV (*.csv);;Text (*.txt);;All Files (*)",
        )
        if not path:
            return

        stem_path = os.path.splitext(path)[0]
        csv_path = f"{stem_path}_analysis_log.csv"
        txt_path = f"{stem_path}_analysis_log.txt"

        session = self._analysis_session_with_context(getattr(self, "_analysis_session", None))
        if session is None and isinstance(getattr(self, "_max_intensity_state", None), dict):
            session = normalize_analysis_session(
                {
                    "source": self._analysis_source_context(),
                    "max_intensity": {
                        "time_channels": self._max_intensity_state.get("time_channels"),
                        "freqs": self._max_intensity_state.get("freqs"),
                        "fundamental": bool(self._max_intensity_state.get("fundamental", True)),
                        "harmonic": bool(self._max_intensity_state.get("harmonic", False)),
                    },
                    "analyzer": dict(self._max_intensity_state.get("analyzer") or {}),
                }
            )

        fits_sources = list(getattr(self, "_combined_sources", []) or [])
        if not fits_sources and getattr(self, "_fits_source_path", None):
            fits_sources = [self._fits_source_path]

        row = build_log_row(
            project_path=getattr(self, "_project_path", None),
            fits_primary=getattr(self, "_fits_source_path", None) or getattr(self, "filename", ""),
            fits_sources=fits_sources,
            combined_mode=getattr(self, "_combined_mode", None),
            station=self._analysis_station_name(),
            date_obs=self._analysis_date_obs(),
            session=session,
        )

        try:
            append_csv_log(csv_path, row)
            append_txt_summary(txt_path, row)
        except Exception as e:
            QMessageBox.critical(self, "Export Analysis Log Failed", str(e))
            return

        analyzer = dict((session or {}).get("analyzer") or {})
        fit = dict(analyzer.get("fit_params") or {})
        if not fit:
            self.statusBar().showMessage(
                "Analysis log exported, but analyzer fit state was missing (blank fields written).",
                5000,
            )
        else:
            self.statusBar().showMessage(
                f"Analysis log exported: {os.path.basename(csv_path)}, {os.path.basename(txt_path)}",
                5000,
            )
        self._log_operation("Exported analysis log (CSV + TXT).")

    def _mark_project_dirty(self):
        if getattr(self, "_loading_project", False):
            return
        if getattr(self, "raw_data", None) is None:
            return
        self._project_dirty = True
        if getattr(self, "_autosave_timer", None) is not None and not self._autosave_timer.isActive():
            self._autosave_timer.start()
        self._sync_project_actions()

    def _set_project_clean(self, path: str | None):
        self._project_path = path
        self._project_dirty = False
        self._sync_project_actions()

    def _sync_project_actions(self):
        has_data = getattr(self, "raw_data", None) is not None

        for name, enabled in (
            ("save_project_action", has_data),
            ("save_project_as_action", has_data),
        ):
            act = getattr(self, name, None)
            if act is not None:
                act.setEnabled(bool(enabled))

    def _sync_fits_view_actions(self):
        has_data = getattr(self, "raw_data", None) is not None
        act = getattr(self, "view_fits_header_action", None)
        if act is not None:
            act.setEnabled(bool(has_data))

    def _maybe_prompt_save_dirty(self) -> bool:
        if not getattr(self, "_project_dirty", False):
            return True

        resp = QMessageBox.question(
            self,
            "Unsaved Project",
            "You have unsaved project changes.\n\nSave before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

        if resp == QMessageBox.StandardButton.Save:
            return bool(self.save_project())
        if resp == QMessageBox.StandardButton.Discard:
            return True
        return False

    def _project_default_filename(self) -> str:
        base = "project"
        if getattr(self, "filename", ""):
            base = os.path.splitext(os.path.basename(self.filename))[0] or base
        return f"{base}.efaproj"

    def _capture_project_payload(self):
        state = self._capture_state()

        arrays = {
            "raw_data": state["raw_data"],
            "noise_reduced_data": state["noise_reduced_data"],
            "noise_reduced_original": state["noise_reduced_original"],
            "lasso_mask": state["lasso_mask"],
            "freqs": state["freqs"],
            "time": state["time"],
        }

        header_txt = None
        try:
            if getattr(self, "_fits_header0", None) is not None:
                header_txt = self._fits_header0.tostring(sep="\n", endcard=True, padding=False)
        except Exception:
            header_txt = None

        graph = {
            "remove_titles": bool(getattr(self, "remove_titles", False)),
            "title_bold": bool(getattr(self, "title_bold", False)),
            "title_italic": bool(getattr(self, "title_italic", False)),
            "axis_bold": bool(getattr(self, "axis_bold", False)),
            "axis_italic": bool(getattr(self, "axis_italic", False)),
            "ticks_bold": bool(getattr(self, "ticks_bold", False)),
            "ticks_italic": bool(getattr(self, "ticks_italic", False)),
            "title_override": str(getattr(self, "graph_title_override", "")),
            "font_family": str(getattr(self, "graph_font_family", "")),
            "tick_font_px": int(getattr(self, "tick_font_px", 11)),
            "axis_label_font_px": int(getattr(self, "axis_label_font_px", 12)),
            "title_font_px": int(getattr(self, "title_font_px", 14)),
        }

        meta = {
            "filename": state["filename"],
            "current_plot_type": state["current_plot_type"],
            "lower_slider": int(state["lower_slider"]),
            "upper_slider": int(state["upper_slider"]),
            "use_db": bool(state["use_db"]),
            "use_utc": bool(state["use_utc"]),
            "ut_start_sec": self.ut_start_sec,
            "cmap": state["cmap"],
            "view": state["view"],
            "noise_vmin": self.noise_vmin,
            "noise_vmax": self.noise_vmax,
            "fits_header": header_txt,
            "fits_source_path": getattr(self, "_fits_source_path", None),
            "is_combined": bool(getattr(self, "_is_combined", False)),
            "combined_mode": getattr(self, "_combined_mode", None),
            "combined_sources": list(getattr(self, "_combined_sources", []) or []),
            "graph": graph,
            "rfi": dict(getattr(self, "_rfi_config", {}) or {}),
            "annotations": normalize_annotations(getattr(self, "_annotations", [])),
            "active_preset": dict(getattr(self, "_active_preset_snapshot", {}) or {}),
            "processing_log": list(getattr(self, "_processing_log", []) or []),
            "time_sync": dict(getattr(self, "_last_time_sync_context", {}) or {}),
        }

        # Canonical analysis session (v2.1)
        session = self._analysis_session_with_context(getattr(self, "_analysis_session", None))
        if session is None:
            session = normalize_analysis_session(
                {
                    "source": self._analysis_source_context(),
                    "max_intensity": {
                        "time_channels": (getattr(self, "_max_intensity_state", {}) or {}).get("time_channels"),
                        "freqs": (getattr(self, "_max_intensity_state", {}) or {}).get("freqs"),
                        "fundamental": bool((getattr(self, "_max_intensity_state", {}) or {}).get("fundamental", True)),
                        "harmonic": bool((getattr(self, "_max_intensity_state", {}) or {}).get("harmonic", False)),
                    },
                    "analyzer": dict((getattr(self, "_max_intensity_state", {}) or {}).get("analyzer") or {}),
                    "ui": {"restore_max_window": True, "restore_analyzer_window": True},
                }
            )

        if session is not None:
            session_meta, session_arrays = analysis_session_to_project_payload(session)
            if session_meta is not None:
                meta["analysis_session"] = session_meta
            arrays.update(session_arrays)

            # Legacy compatibility payload retained for older builds.
            legacy = self._session_to_legacy_max_state(session)
            if legacy is not None:
                meta["max_intensity"] = {
                    "present": True,
                    "fundamental": bool(legacy.get("fundamental", True)),
                    "harmonic": bool(legacy.get("harmonic", False)),
                    "analyzer": legacy.get("analyzer"),
                }
                arrays.update({
                    "max_time_channels": legacy.get("time_channels"),
                    "max_freqs": legacy.get("freqs"),
                })

        return meta, arrays

    def _apply_project_payload(self, meta: dict, arrays: dict):
        self._loading_project = True
        try:
            # Clear undo/redo stacks on project load
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._close_analysis_windows()

            self.raw_data = arrays.get("raw_data", None)
            self._invalidate_noise_cache()
            self.noise_reduced_data = arrays.get("noise_reduced_data", None)
            self.noise_reduced_original = arrays.get("noise_reduced_original", None)
            self.lasso_mask = arrays.get("lasso_mask", None)
            self.freqs = arrays.get("freqs", None)
            self.time = arrays.get("time", None)

            # Copies avoid read-only arrays from np.load
            for name in ("raw_data", "noise_reduced_data", "noise_reduced_original", "lasso_mask", "freqs", "time"):
                val = getattr(self, name, None)
                if val is not None:
                    try:
                        setattr(self, name, val.copy())
                    except Exception:
                        pass

            self.filename = meta.get("filename", "") or ""
            self.current_plot_type = self._normalize_plot_type(meta.get("current_plot_type", "Raw"))

            self.use_db = bool(meta.get("use_db", False))
            self.use_utc = bool(meta.get("use_utc", False))
            self.ut_start_sec = meta.get("ut_start_sec", None)
            self.current_cmap_name = meta.get("cmap", "Custom") or "Custom"

            self.noise_vmin = meta.get("noise_vmin", None)
            self.noise_vmax = meta.get("noise_vmax", None)

            self._fits_source_path = meta.get("fits_source_path", None)
            self._is_combined = bool(meta.get("is_combined", False))
            self._combined_mode = meta.get("combined_mode", None)
            self._combined_sources = list(meta.get("combined_sources", []) or [])
            self._rfi_config = dict(meta.get("rfi") or self._rfi_config)
            self._annotations = normalize_annotations(meta.get("annotations", []))
            self._active_preset_snapshot = dict(meta.get("active_preset") or {})
            self._processing_log = list(meta.get("processing_log") or [])
            self._last_time_sync_context = dict(meta.get("time_sync") or {})

            header_txt = meta.get("fits_header", None)
            self._fits_header0 = None
            if header_txt:
                try:
                    self._fits_header0 = fits.Header.fromstring(header_txt, sep="\n")
                except Exception:
                    self._fits_header0 = None

            # Restore widgets without triggering live updates
            try:
                self.lower_slider.blockSignals(True)
                self.upper_slider.blockSignals(True)
                self.lower_slider.setValue(int(meta.get("lower_slider", self.lower_slider.value())))
                self.upper_slider.setValue(int(meta.get("upper_slider", self.upper_slider.value())))
            finally:
                self.lower_slider.blockSignals(False)
                self.upper_slider.blockSignals(False)

            # Units radios
            try:
                self.units_digits_radio.blockSignals(True)
                self.units_db_radio.blockSignals(True)
                self.units_db_radio.setChecked(bool(self.use_db))
                self.units_digits_radio.setChecked(not bool(self.use_db))
            finally:
                self.units_digits_radio.blockSignals(False)
                self.units_db_radio.blockSignals(False)

            # Time-axis radios
            try:
                self.time_sec_radio.blockSignals(True)
                self.time_ut_radio.blockSignals(True)
                self.time_ut_radio.setChecked(bool(self.use_utc))
                self.time_sec_radio.setChecked(not bool(self.use_utc))
            finally:
                self.time_sec_radio.blockSignals(False)
                self.time_ut_radio.blockSignals(False)

            # Colormap combo
            try:
                self.cmap_combo.blockSignals(True)
                if self.current_cmap_name:
                    self.cmap_combo.setCurrentText(self.current_cmap_name)
            finally:
                self.cmap_combo.blockSignals(False)

            # Graph properties
            graph = meta.get("graph", {}) or {}
            self._hw_default_font_sizes_active = False
            try:
                self.remove_titles_chk.blockSignals(True)
                self.title_bold_chk.blockSignals(True)
                self.title_italic_chk.blockSignals(True)
                self.axis_bold_chk.blockSignals(True)
                self.axis_italic_chk.blockSignals(True)
                self.ticks_bold_chk.blockSignals(True)
                self.ticks_italic_chk.blockSignals(True)
                self.title_edit.blockSignals(True)
                self.font_combo.blockSignals(True)
                self.tick_font_spin.blockSignals(True)
                self.axis_font_spin.blockSignals(True)
                self.title_font_spin.blockSignals(True)

                self.remove_titles_chk.setChecked(bool(graph.get("remove_titles", False)))
                self.title_bold_chk.setChecked(bool(graph.get("title_bold", False)))
                self.title_italic_chk.setChecked(bool(graph.get("title_italic", False)))
                self.axis_bold_chk.setChecked(bool(graph.get("axis_bold", False)))
                self.axis_italic_chk.setChecked(bool(graph.get("axis_italic", False)))
                self.ticks_bold_chk.setChecked(bool(graph.get("ticks_bold", False)))
                self.ticks_italic_chk.setChecked(bool(graph.get("ticks_italic", False)))

                self.title_edit.setText(str(graph.get("title_override", "")) or "")

                font_family = str(graph.get("font_family", "")) or ""
                self.font_combo.setCurrentText(font_family if font_family else "Default")

                self.tick_font_spin.setValue(int(graph.get("tick_font_px", self.tick_font_spin.value())))
                self.axis_font_spin.setValue(int(graph.get("axis_label_font_px", self.axis_font_spin.value())))
                self.title_font_spin.setValue(int(graph.get("title_font_px", self.title_font_spin.value())))
            finally:
                self.remove_titles_chk.blockSignals(False)
                self.title_bold_chk.blockSignals(False)
                self.title_italic_chk.blockSignals(False)
                self.axis_bold_chk.blockSignals(False)
                self.axis_italic_chk.blockSignals(False)
                self.ticks_bold_chk.blockSignals(False)
                self.ticks_italic_chk.blockSignals(False)
                self.title_edit.blockSignals(False)
                self.font_combo.blockSignals(False)
                self.tick_font_spin.blockSignals(False)
                self.axis_font_spin.blockSignals(False)
                self.title_font_spin.blockSignals(False)

            # Analysis session restore (canonical + legacy fallback)
            self._analysis_session = None
            self._max_intensity_state = None
            pending_open_max = False
            pending_open_analyzer = False
            pending_warning = ""

            analysis_meta = meta.get("analysis_session")
            loaded_session = None
            if isinstance(analysis_meta, dict):
                session_payload = dict(analysis_meta)
                max_block = dict(session_payload.get("max_intensity") or {})
                max_block["time_channels"] = arrays.get("analysis_time_channels", None)
                max_block["freqs"] = arrays.get("analysis_freqs", None)
                session_payload["max_intensity"] = max_block
                loaded_session = normalize_analysis_session(session_payload)

            if loaded_session is None:
                loaded_session = from_legacy_max_intensity(meta, arrays)

            if loaded_session is not None:
                current_shape = None
                if self.raw_data is not None:
                    try:
                        current_shape = (int(self.raw_data.shape[0]), int(self.raw_data.shape[1]))
                    except Exception:
                        current_shape = None

                ok, reason = validate_session_for_source(loaded_session, current_shape=current_shape)
                loaded_session = self._analysis_session_with_context(loaded_session)
                self._analysis_session = loaded_session
                self._max_intensity_state = self._session_to_legacy_max_state(loaded_session)

                if ok:
                    ui_block = dict((loaded_session or {}).get("ui") or {})
                    pending_open_max = bool(ui_block.get("restore_max_window", True))
                    pending_open_analyzer = bool(ui_block.get("restore_analyzer_window", False))
                else:
                    pending_warning = f"Analysis restore loaded with warning: {reason}"

            self._pending_analysis_restore = {
                "open_max": bool(pending_open_max),
                "open_analyzer": bool(pending_open_analyzer),
                "warning": str(pending_warning or ""),
            }
            self._refresh_analysis_summary_panel()

            if self._rfi_dialog is not None:
                try:
                    self._rfi_dialog.enabled_chk.setChecked(bool(self._rfi_config.get("enabled", True)))
                    self._rfi_dialog.kernel_time_spin.setValue(int(self._rfi_config.get("kernel_time", 3)))
                    self._rfi_dialog.kernel_freq_spin.setValue(int(self._rfi_config.get("kernel_freq", 3)))
                    self._rfi_dialog.z_thresh_spin.setValue(float(self._rfi_config.get("channel_z_threshold", 6.0)))
                    self._rfi_dialog.percentile_spin.setValue(float(self._rfi_config.get("percentile_clip", 99.5)))
                    self._rfi_dialog.set_masked_channels(self._rfi_config.get("masked_channel_indices", []))
                except Exception:
                    pass

            # Redraw
            if self.raw_data is not None:
                data = self.noise_reduced_data if self.noise_reduced_data is not None else self.raw_data
                self.plot_data(data, title=self.current_plot_type, restore_view=meta.get("view"))
                self.graph_group.setEnabled(True)
                self._sync_toolbar_enabled_states()
                QTimer.singleShot(0, self._render_annotations)
                QTimer.singleShot(0, self._apply_pending_analysis_restore)
        finally:
            self._loading_project = False
            self._sync_project_actions()

    def save_project(self) -> bool:
        if getattr(self, "raw_data", None) is None:
            QMessageBox.information(self, "Save Project", "Load a FITS file first.")
            return False

        if not getattr(self, "_project_path", None):
            return bool(self.save_project_as())

        try:
            meta, arrays = self._capture_project_payload()
            write_project(self._project_path, meta=meta, arrays=arrays)
        except Exception as e:
            QMessageBox.critical(self, "Save Project Failed", f"Could not save project:\n{e}")
            return False

        self._set_project_clean(self._project_path)
        self.statusBar().showMessage(f"Project saved: {os.path.basename(self._project_path)}", 5000)
        self._log_operation(f"Saved project: {os.path.basename(self._project_path)}")
        return True

    def save_project_as(self) -> bool:
        if getattr(self, "raw_data", None) is None:
            QMessageBox.information(self, "Save Project", "Load a FITS file first.")
            return False

        start_dir = ""
        if getattr(self, "_project_path", None):
            start_dir = os.path.dirname(self._project_path)
        elif getattr(self, "_fits_source_path", None):
            start_dir = os.path.dirname(self._fits_source_path)

        default_name = os.path.join(start_dir, self._project_default_filename()) if start_dir else self._project_default_filename()

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project As",
            default_name,
            "e-CALLISTO Project (*.efaproj)",
        )
        if not path:
            return False
        if not path.lower().endswith(".efaproj"):
            path += ".efaproj"

        try:
            meta, arrays = self._capture_project_payload()
            write_project(path, meta=meta, arrays=arrays)
        except Exception as e:
            QMessageBox.critical(self, "Save Project Failed", f"Could not save project:\n{e}")
            return False

        self._set_project_clean(path)
        self.statusBar().showMessage(f"Project saved: {os.path.basename(path)}", 5000)
        self._log_operation(f"Saved project: {os.path.basename(path)}")
        return True

    def open_project(self):
        if not self._maybe_prompt_save_dirty():
            return

        start_dir = ""
        if getattr(self, "_project_path", None):
            start_dir = os.path.dirname(self._project_path)
        elif getattr(self, "_fits_source_path", None):
            start_dir = os.path.dirname(self._fits_source_path)

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            start_dir,
            "e-CALLISTO Project (*.efaproj)",
        )
        if not path:
            return

        try:
            payload = read_project(path)
        except ProjectFormatError as e:
            QMessageBox.critical(self, "Open Project Failed", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Open Project Failed", f"Could not open project:\n{e}")
            return

        self._apply_project_payload(payload.meta, payload.arrays)
        self._set_project_clean(path)
        self.statusBar().showMessage(f"Project loaded: {os.path.basename(path)}", 5000)
        self._log_operation(f"Loaded project: {os.path.basename(path)}")

    def open_fits_header_viewer(self):
        if getattr(self, "raw_data", None) is None:
            QMessageBox.information(self, "FITS Header", "Load a FITS file first.")
            return

        hdr = getattr(self, "_fits_header0", None)
        if hdr is None:
            hdr = fits.Header()

        base = "fits"
        if getattr(self, "filename", ""):
            base = os.path.splitext(os.path.basename(self.filename))[0] or base
        default_name = f"{base}_header.txt"

        title = "FITS Header"
        if getattr(self, "filename", ""):
            title = f"FITS Header — {self.filename}"

        self._fits_header_viewer = FitsHeaderViewerDialog(
            hdr,
            title=title,
            default_name=default_name,
            parent=self,
        )
        self._fits_header_viewer.show()

    def closeEvent(self, event):
        if not self._maybe_prompt_save_dirty():
            event.ignore()
            return
        try:
            self.noise_smooth_timer.stop()
            self.noise_commit_timer.stop()
        except Exception:
            pass
        try:
            if self._update_thread is not None:
                self._update_thread.quit()
                self._update_thread.wait(500)
        except Exception:
            pass
        try:
            if self._update_download_thread is not None:
                if self._update_download_worker is not None:
                    self._update_download_worker.request_cancel()
                self._update_download_thread.quit()
                self._update_download_thread.wait(800)
        except Exception:
            pass
        try:
            if self._cme_helper_client is not None:
                self._cme_helper_client.shutdown()
        except Exception:
            pass
        try:
            if self._batch_processing_dialog is not None:
                self._batch_processing_dialog.force_shutdown(timeout_ms=2000)
                self._batch_processing_dialog.close()
        except Exception:
            pass
        try:
            if self._bug_report_dialog is not None:
                self._bug_report_dialog.close()
        except Exception:
            pass
        try:
            if self._autosave_timer is not None:
                self._autosave_timer.stop()
        except Exception:
            pass
        try:
            self._close_analysis_windows()
        except Exception:
            pass
        try:
            self._ui_settings.setValue("runtime/clean_exit", True)
        except Exception:
            pass
        self._close_update_download_progress_dialog()
        super().closeEvent(event)


    def open_cme_viewer(self):
        from src.UI.soho_lasco_viewer import CMEViewer  # import here, not at top
        try:
            alive = self._cme_viewer is not None
            if alive:
                _ = self._cme_viewer.windowTitle()
        except Exception:
            alive = False
        if not alive:
            self._cme_viewer = CMEViewer(parent=self, helper_client=self._cme_helper_client)
        self._cme_viewer.show()
        self._cme_viewer.raise_()
        self._cme_viewer.activateWindow()

        window = self._current_time_window_utc()
        if window and hasattr(self._cme_viewer, "set_target_datetime"):
            try:
                mid = window[0] + (window[1] - window[0]) / 2
                self._cme_viewer.set_target_datetime(mid, auto_search=True, auto_select_nearest=True)
            except Exception:
                pass
