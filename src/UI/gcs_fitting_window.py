"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Three-viewpoint GCS CME fitting (src/UI/gcs_fitting_window.py).

A standalone window that views the Sun from three places at once and fits one
Graduated Cylindrical Shell against all of them simultaneously.

Why three, and why one set of controls
--------------------------------------
GCS is ill posed from a single vantage: a wide CME pointed at the observer and a
narrow one travelling across the sky project almost identically, so direction
trades off against height and width and a single-view fit looks convincing while
meaning very little. Two well-separated views break that; a third — the classic
STEREO-A / SOHO / STEREO-B triad — over-constrains it. That is also why there is
exactly one set of parameter sliders: a single shell must satisfy every
viewpoint at once, and per-panel controls would let the three drift into three
different CMEs.

Layout
------
The images are the point, so they are placed first and the controls take what is
left. Three squares side by side can never be taller than a third of the width,
so in the **Equal** layout the images take exactly that and the control cards
fill the band beneath them. The **Focus** layout enlarges one viewpoint — about
half as large again on a laptop screen — with the other two stacked beside it
and the controls in a column on the right. A splitter between images and
controls is set to that ideal on every resize until the user drags it.

Each image is an exact square with no axes or title row around it: that is what
keeps the Sun round without letterboxing and gives the whole square to the data.

What is shared and what is not
------------------------------
Shared: the model, the wireframe style, the view mode and the time. Per channel:
the source, the date range, the colormap and the contrast. The time is shared as
a *timestamp*, not an index, because three spacecraft rarely share a cadence.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from PySide6.QtCore import QDateTime, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDateTimeEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from src.Backend.gcs_model import (
    MIN_USEFUL_SEPARATION_DEG,
    GCSParameters,
    GCSViewpoint,
    apply_apex_drag,
    gcs_mesh,
    handle_positions_arcsec,
    observer_separation_deg,
    refine_gcs,
    wireframe_arcsec,
)
from src.Backend.helioviewer_jp2 import DEFAULT_MAX_FRAMES, default_viewpoints, validate_range
from src.UI.gcs_viewpoint_panel import (
    DEFAULT_DIFFERENCE_MODE,
    DIFFERENCE_MODES,
    RANGE_FORMAT,
    GCSViewpointPanel,
)


def _utc_now() -> datetime:
    """Naive UTC now, matching the convention the analyzer's date editors use."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


#: Panel labels, in the order they appear.
PANEL_LABELS: tuple[str, ...] = ("A", "B", "C")

#: Layout modes of the image stage.
LAYOUT_EQUAL = "equal"
LAYOUT_FOCUS = "focus"

#: Toolbar labels for the view modes; the full names live in the tooltips.
VIEW_BUTTON_LABELS = {"raw": "Raw", "running": "Running diff", "base": "Base diff"}

#: How far either side of a handed-over time the initial event range reaches.
DEFAULT_EVENT_HALF_WIDTH = timedelta(hours=1)

#: Space the control cards need below the images before the images give way.
DECK_MIN_HEIGHT = 250
#: Width range of the control column in the Focus layout.
DECK_COLUMN_MIN_WIDTH = 370
DECK_COLUMN_MAX_WIDTH = 470


# --- Image stage ----------------------------------------------------------------


class GCSImageStage(QWidget):
    """Places the three viewpoint images as large as the space allows.

    Manual geometry rather than a box layout: each image has to be an exact
    square — the canvas keeps the Sun round by staying square, and letterboxing
    inside it would waste the very space this exists to use — and as large as
    fits, which stretch factors cannot express.
    """

    GAP = 6
    layoutChanged = Signal()

    def __init__(self, panels: list[GCSViewpointPanel], parent: QWidget | None = None):
        super().__init__(parent)
        self._panels = list(panels)
        for panel in self._panels:
            panel.setParent(self)
            panel.show()
        self._mode = LAYOUT_EQUAL
        self._focus = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(240, 120)

    def mode(self) -> str:
        return self._mode

    def focus_index(self) -> int:
        return self._focus

    def set_layout(self, mode: str, focus: int | None = None) -> None:
        self._mode = LAYOUT_FOCUS if mode == LAYOUT_FOCUS else LAYOUT_EQUAL
        if focus is not None:
            self._focus = max(0, min(int(focus), len(self._panels) - 1))
        self._relayout()
        self.layoutChanged.emit()

    def ideal_height(self, width: int) -> int:
        """Stage height at which images of this width leave no space unused."""
        gap = self.GAP
        if self._mode == LAYOUT_EQUAL:
            return max(1, (int(width) - 2 * gap) // 3)
        # One large square L beside a column of two squares (L - gap) / 2 wide:
        # the pair is 1.5 L + gap / 2 across.
        return max(1, int((int(width) - gap / 2) / 1.5))

    def ideal_width(self, height: int) -> int:
        gap = self.GAP
        if self._mode == LAYOUT_EQUAL:
            return 3 * int(height) + 2 * gap
        return int(1.5 * int(height) + gap / 2)

    def geometry_for(self, width: int, height: int) -> list[QRect]:
        """Square rectangles for the panels, in panel order, centred in the stage."""
        gap = self.GAP
        width, height = max(1, int(width)), max(1, int(height))
        if self._mode == LAYOUT_EQUAL:
            side = max(1, min((width - 2 * gap) // 3, height))
            x0 = (width - (3 * side + 2 * gap)) // 2
            y0 = (height - side) // 2
            return [QRect(x0 + index * (side + gap), y0, side, side) for index in range(len(self._panels))]
        large = max(3, min(height, int((width - gap / 2) / 1.5)))
        small = max(1, (large - gap) // 2)
        x0 = (width - (large + gap + small)) // 2
        y0 = (height - large) // 2
        rects: list[QRect] = [QRect()] * len(self._panels)
        rects[self._focus] = QRect(x0, y0, large, large)
        others = [index for index in range(len(self._panels)) if index != self._focus]
        for row, index in enumerate(others):
            rects[index] = QRect(x0 + large + gap, y0 + row * (small + gap), small, small)
        return rects

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        for panel, rect in zip(self._panels, self.geometry_for(self.width(), self.height())):
            panel.setGeometry(rect)


# --- Control deck -----------------------------------------------------------------


class GCSCard(QFrame):
    """A titled group of controls in the deck."""

    def __init__(self, title: str, body: QWidget, *, scroll_body: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("GCSCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame#GCSCard { border: 1px solid palette(mid); border-radius: 6px; }"
            "QLabel#GCSCardTitle { font-weight: 600; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        heading = QLabel(title)
        heading.setObjectName("GCSCardTitle")
        layout.addWidget(heading)
        self.body = body
        self._scroll: QScrollArea | None = None
        if scroll_body:
            # Only for content taller than a row of cards can offer (the fits
            # table and plot): it scrolls in place rather than overlapping itself.
            self._scroll = QScrollArea()
            self._scroll.setWidgetResizable(True)
            self._scroll.setFrameShape(QFrame.NoFrame)
            self._scroll.setWidget(body)
            layout.addWidget(self._scroll, 1)
        else:
            layout.addWidget(body, 1)

    def set_stacked(self, stacked: bool) -> None:
        """In a scrolling column the whole deck scrolls, so an inner scroll area
        would only nest one scrollbar inside another; let it take its full height."""
        if self._scroll is None:
            return
        self._scroll.setMinimumHeight(self.body.sizeHint().height() if stacked else 0)


class GCSControlDeck(QScrollArea):
    """The control cards: a row beneath the images, or a column beside them."""

    ROW = "row"
    COLUMN = "column"

    def __init__(self, cards: list[tuple[GCSCard, int]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._cards = list(cards)
        self._container = QWidget()
        self.setWidget(self._container)
        self._orientation: str | None = None
        self.arrange(self.ROW)

    def orientation(self) -> str | None:
        return self._orientation

    def content_minimum_height(self) -> int:
        """Height the cards need to show without scrolling, scrollbar included."""
        height = self._container.minimumSizeHint().height()
        if self._orientation == self.ROW and self._container.minimumSizeHint().width() > self.viewport().width():
            height += self.horizontalScrollBar().sizeHint().height()
        return height + 2 * self.frameWidth()

    def arrange(self, orientation: str) -> None:
        if orientation == self._orientation:
            return
        old = self._container.layout()
        if old is not None:
            while old.count():
                old.takeAt(0)
            QWidget().setLayout(old)  # hands the old layout to a throwaway owner
        row = orientation == self.ROW
        layout = QHBoxLayout() if row else QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for card, stretch in self._cards:
            layout.addWidget(card, stretch if row else 0)
            card.set_stacked(not row)
        if not row:
            layout.addStretch(1)
        self._container.setLayout(layout)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if row else Qt.ScrollBarAlwaysOff)
        self._orientation = orientation


# --- Window -------------------------------------------------------------------------


class GCSFittingWindow(QMainWindow):
    """Fit one GCS shell against up to three simultaneous viewpoints."""

    #: Emitted with the fitted parameters when the user sends them onward.
    parametersCommitted = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        target_time: datetime | None = None,
        event_range: tuple[datetime, datetime] | None = None,
        cache_dir: Any = None,
        theme: Any = None,
        initial: GCSParameters | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("GCS CME Fitting — Three Viewpoints")
        self.theme = theme
        self._params = initial
        self._clicks: dict[str, list[tuple[float, float]]] = {key: [] for key in PANEL_LABELS}
        self._last_refinement: Any = None
        self._mesh: Any = None
        self._mesh_key: tuple[float, float, float] | None = None
        self._shared_time: datetime | None = None
        self._time_axis: list[datetime] = []
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._advance_frame)
        #: Recorded fits, keyed by the shared time they were committed at.
        self._fits: dict[datetime, Any] = {}
        self._wireframe_colour = (255, 140, 40)
        #: Whether the user has dragged the splitter, per layout. Until they do,
        #: every resize re-fits it so the images leave no space unused.
        self._user_split = {LAYOUT_EQUAL: False, LAYOUT_FOCUS: False}
        self._split_pending = False

        if event_range is None:
            centre = (target_time or _utc_now()).replace(tzinfo=None, microsecond=0, second=0)
            event_range = (centre - DEFAULT_EVENT_HALF_WIDTH, centre + DEFAULT_EVENT_HALF_WIDTH)
        start, end = event_range

        self._build_ui(cache_dir=cache_dir, start=start, end=end)
        self._apply_default_sources(start, force=True)
        self._apply_event_range_to_channels()
        self._sync_all()

        try:
            from src.UI.gui_shared import fit_window_to_screen

            fit_window_to_screen(self, 1600, 1000)
        except Exception:
            self.resize(1600, 1000)

    # ------------------------------------------------------------------- UI
    def _build_ui(self, *, cache_dir: Any, start: datetime, end: datetime) -> None:
        self.panels: list[GCSViewpointPanel] = []
        for label, source in zip(PANEL_LABELS, default_viewpoints(start)):
            panel = GCSViewpointPanel(label, source_key=source.key, cache_dir=cache_dir, theme=self.theme)
            panel.framesChanged.connect(self._on_panel_frames_changed)
            panel.statusChanged.connect(self._on_panel_status)
            panel.busyChanged.connect(lambda _busy: self._sync_busy())
            panel.hovered.connect(lambda x, y, key=label: self._on_hover(key, x, y))
            panel.max_frames_provider = lambda: int(self.max_frames_spin.value())
            panel.canvas.set_click_callback(
                lambda x, y, button, key=label: self._on_canvas_click(key, x, y, button)
            )
            panel.canvas.set_gcs_handle_callback(
                lambda name, x, y, done, key=label: self._on_handle(key, name, x, y, done)
            )
            self.panels.append(panel)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)
        root.addWidget(self._build_toolbar())

        self.stage = GCSImageStage(self.panels)
        self.deck = GCSControlDeck(
            [
                (self._build_event_card(start, end), 5),
                (self._build_model_card(), 4),
                (self._build_display_card(), 3),
                (self._build_kinematics_card(), 5),
            ]
        )
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.stage)
        self.splitter.addWidget(self.deck)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        root.addWidget(self.splitter, 1)

        self.status_label = QLabel("Set the event range and press Load all to fetch the three viewpoints.")
        self.status_label.setWordWrap(False)
        self.coord_label = QLabel("")
        self.coord_label.setToolTip("Helioprojective position under the cursor.")
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.coord_label)

        self._install_shortcuts()
        self._sync_transport()
        self._sync_layout_buttons()

    def _segmented(self, labels: list[tuple[str, str]], *, checked: str) -> tuple[QWidget, QButtonGroup]:
        """A row of exclusive toggle buttons, returned with their group."""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, (key, text) in enumerate(labels):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(key == checked)
            button.setProperty("segment_key", key)
            button.setCursor(Qt.PointingHandCursor)
            group.addButton(button, index)
            row.addWidget(button)
        return holder, group

    def _build_toolbar(self) -> QWidget:
        """Playback, time, view mode, layout and overlays — one row above the images."""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        # Transport, matching the analyzer's playback bar so the two feel the same.
        self.rewind_btn = QPushButton()
        self.prev_btn = QPushButton()
        self.play_btn = QPushButton()
        self.pause_btn = QPushButton()
        self.next_btn = QPushButton()
        transport = (
            (self.rewind_btn, QStyle.SP_MediaSkipBackward, "First frame (Home)", self._rewind),
            (self.prev_btn, QStyle.SP_MediaSeekBackward, "Previous frame (←)", self.previous_frame),
            (self.play_btn, QStyle.SP_MediaPlay, "Play (Space)", self.play),
            (self.pause_btn, QStyle.SP_MediaPause, "Pause (Space)", self.pause),
            (self.next_btn, QStyle.SP_MediaSeekForward, "Next frame (→)", self.next_frame),
        )
        for button, icon, tip, slot in transport:
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(tip)
            button.setFixedWidth(30)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            row.addWidget(button)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 30)
        self.fps_spin.setValue(4)
        self.fps_spin.setSuffix(" fps")
        self.fps_spin.setToolTip("Playback speed.")
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        row.addWidget(self.fps_spin)

        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setRange(0, 0)
        self.time_slider.setMinimumWidth(80)
        self.time_slider.setToolTip(
            "Step through the event. Every panel snaps to its own frame nearest this\n"
            "time, so instruments with different cadences stay simultaneous."
        )
        self.time_slider.valueChanged.connect(self._on_time_changed)
        self.time_label = QLabel("—")
        self.time_label.setMinimumWidth(118)
        row.addSpacing(4)
        row.addWidget(self.time_slider, 1)
        row.addWidget(self.time_label)

        row.addSpacing(6)
        row.addWidget(QLabel("View"))
        # Short labels keep the toolbar narrow enough for a 1280-pixel screen.
        view_holder, self._difference_group = self._segmented(
            [(key, VIEW_BUTTON_LABELS[key]) for key, _label in DIFFERENCE_MODES],
            checked=DEFAULT_DIFFERENCE_MODE,
        )
        tips = {
            "raw": "Raw: the frames as downloaded.",
            "running": (
                "Running difference: each frame minus the one before it — the usual way to\n"
                "make a faint CME front visible. The default whenever frames load."
            ),
            "base": "Base difference: each frame minus the first of the sequence — total change since the start.",
        }
        for button in self._difference_group.buttons():
            key = button.property("segment_key")
            button.setToolTip(tips[key])
            button.toggled.connect(lambda on, mode=key: self._on_difference_mode(mode) if on else None)
        row.addWidget(view_holder)

        row.addSpacing(6)
        layout_holder, self._layout_group = self._segmented(
            [(LAYOUT_EQUAL, "Equal")] + [(f"focus:{i}", label) for i, label in enumerate(PANEL_LABELS)],
            checked=LAYOUT_EQUAL,
        )
        for button in self._layout_group.buttons():
            key = str(button.property("segment_key"))
            if key == LAYOUT_EQUAL:
                button.setToolTip("Layout: three equal images, controls beneath (0).")
            else:
                index = int(key.split(":")[1])
                button.setToolTip(
                    f"Layout: enlarge panel {PANEL_LABELS[index]}; the other two stack beside it and\n"
                    f"the controls move to the right ({index + 1})."
                )
                button.setFixedWidth(30)
            button.toggled.connect(lambda on, value=key: self._on_layout_button(value) if on else None)
        row.addWidget(layout_holder)

        row.addSpacing(6)
        self.limb_check = QCheckBox("Solar limb")
        self.limb_check.setChecked(True)
        self.limb_check.setToolTip(
            "Outline the photosphere in every image. Behind a coronagraph's occulter\n"
            "the disk itself is never seen; the outline shows where the Sun is and how\n"
            "large, the reference a CME's height is judged against."
        )
        self.limb_check.toggled.connect(self._on_limb_toggled)
        self.axes_check = QCheckBox("Axes")
        self.axes_check.setChecked(False)
        self.axes_check.setToolTip("Show arcsec axes around each image (costs image space).")
        self.axes_check.toggled.connect(self._on_axes_toggled)
        row.addWidget(self.limb_check)
        row.addWidget(self.axes_check)
        return bar

    def _build_event_card(self, start: datetime, end: datetime) -> GCSCard:
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        self.event_start_edit = QDateTimeEdit(QDateTime(start))
        self.event_end_edit = QDateTimeEdit(QDateTime(end))
        for edit, which in ((self.event_start_edit, "start"), (self.event_end_edit, "end")):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat(RANGE_FORMAT)
            edit.setToolTip(
                f"Event {which} (UTC). Changing it sets every channel's range; each channel\n"
                "can then be adjusted on its own below."
            )
            edit.dateTimeChanged.connect(lambda _v: self._apply_event_range_to_channels())
        grid.addWidget(QLabel("Event"), 0, 0)
        grid.addWidget(self.event_start_edit, 0, 1)
        grid.addWidget(QLabel("→"), 0, 2, Qt.AlignCenter)
        grid.addWidget(self.event_end_edit, 0, 3)

        self.max_frames_spin = QSpinBox()
        self.max_frames_spin.setRange(2, 200)
        self.max_frames_spin.setValue(DEFAULT_MAX_FRAMES)
        self.max_frames_spin.setPrefix("≤ ")
        self.max_frames_spin.setSuffix(" frames")
        self.max_frames_spin.setToolTip(
            "Cap per channel. A range with more frames than this is thinned evenly\n"
            "across the event rather than cut short."
        )
        self.load_all_btn = QPushButton("Load all")
        self.load_all_btn.setToolTip("Load every channel's frames for its date range.")
        self.load_all_btn.clicked.connect(self._fetch_all)
        grid.addWidget(self.max_frames_spin, 1, 1)
        grid.addWidget(self.load_all_btn, 1, 3)

        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        rule.setFrameShadow(QFrame.Sunken)
        grid.addWidget(rule, 2, 0, 1, 4)

        row = 3
        for panel in self.panels:
            tag = QLabel(panel.label)
            tag.setStyleSheet("font-weight: 600;")
            grid.addWidget(tag, row, 0)
            grid.addWidget(panel.source_combo, row, 1, 1, 2)
            status_row = QHBoxLayout()
            status_row.setSpacing(4)
            status_row.addWidget(panel.frames_label, 1)
            panel.fetch_btn.setFixedWidth(58)
            status_row.addWidget(panel.fetch_btn)
            grid.addLayout(status_row, row, 3)
            grid.addWidget(panel.start_edit, row + 1, 1)
            grid.addWidget(QLabel("→"), row + 1, 2, Qt.AlignCenter)
            grid.addWidget(panel.end_edit, row + 1, 3)
            row += 2
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setRowStretch(row, 1)
        card = GCSCard("Event & channels", body)
        card.setMinimumWidth(360)
        return card

    def _build_model_card(self) -> GCSCard:
        from src.UI.solar_measure_tools import GCSParameterPanel

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        # One parameter set for every panel — see the module docstring.
        self.gcs_panel = GCSParameterPanel()
        self.gcs_panel.parametersChanged.connect(self._on_parameters)
        self.gcs_panel.refineRequested.connect(self._on_refine)
        self.gcs_panel.commitRequested.connect(self._on_commit)
        self.gcs_panel.commit_gcs_btn.setText("Commit GCS")
        self.gcs_panel.commit_gcs_btn.setToolTip(
            "Record this fit at the current time. Commit across several times to build\n"
            "a de-projected 3-D height-time profile."
        )
        layout.addWidget(self.gcs_panel)

        extras = QHBoxLayout()
        self.clear_points_btn = QPushButton("Clear points")
        self.clear_points_btn.setToolTip("Remove the front points clicked for Refine fit.")
        self.clear_points_btn.clicked.connect(self._clear_points)
        self.send_btn = QPushButton("Send to analyzer")
        self.send_btn.setToolTip("Push the current parameters back to the Solar Image Analysis window.")
        self.send_btn.clicked.connect(self._on_send)
        extras.addWidget(self.clear_points_btn)
        extras.addWidget(self.send_btn)
        layout.addLayout(extras)
        layout.addStretch(1)
        card = GCSCard("GCS model", body)
        card.setMinimumWidth(280)
        return card

    def _build_display_card(self) -> GCSCard:
        from src.UI.solar_measure_tools import GCSParameterSlider

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.width_slider = GCSParameterSlider("Width", 2.6, minimum=0.5, maximum=8.0, unit=" px", decimals=1)
        self.opacity_slider = GCSParameterSlider("Opacity", 100.0, minimum=10.0, maximum=100.0, unit="%", decimals=0)
        # 1 = coarsest, 8 = every ring. Defaulting well short of the maximum: at a
        # heavy pen width a full-density mesh fills in and reads as a solid blob.
        self.density_slider = GCSParameterSlider("Density", 5.0, minimum=1.0, maximum=8.0, unit="", decimals=0)
        self.density_slider.setToolTip(
            "How fine the drawn mesh is. Higher is denser; back it off when a thick\n"
            "pen starts filling the shell in instead of outlining it."
        )
        wire_heading = QHBoxLayout()
        wire_heading.addWidget(QLabel("Wireframe"))
        wire_heading.addStretch(1)
        self.colour_btn = QPushButton("Colour…")
        self.colour_btn.clicked.connect(self._pick_colour)
        wire_heading.addWidget(self.colour_btn)
        layout.addLayout(wire_heading)
        for slider in (self.width_slider, self.opacity_slider, self.density_slider):
            layout.addWidget(slider)
        self.width_slider.valueChanged.connect(lambda _v: self._apply_style())
        self.opacity_slider.valueChanged.connect(lambda _v: self._apply_style())
        self.density_slider.valueChanged.connect(lambda _v: self._sync_all())

        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        rule.setFrameShadow(QFrame.Sunken)
        layout.addWidget(rule)

        # One set of colormap/contrast controls on screen at a time, switched per
        # viewpoint: three full sets would not fit beside the other cards.
        picker = QHBoxLayout()
        picker.addWidget(QLabel("Image"))
        picker_holder, self._contrast_group = self._segmented(
            [(str(i), label) for i, label in enumerate(PANEL_LABELS)], checked="0"
        )
        picker.addWidget(picker_holder)
        picker.addStretch(1)
        layout.addLayout(picker)
        self.contrast_stack = QStackedWidget()
        for panel in self.panels:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(3)
            map_row = QHBoxLayout()
            map_row.addWidget(QLabel("Colormap"))
            map_row.addWidget(panel.colormap_combo, 1)
            page_layout.addLayout(map_row)
            page_layout.addWidget(panel.low_slider)
            page_layout.addWidget(panel.high_slider)
            self.contrast_stack.addWidget(page)
        for button in self._contrast_group.buttons():
            index = int(button.property("segment_key"))
            button.setFixedWidth(30)
            button.setToolTip(f"Colormap and contrast for panel {PANEL_LABELS[index]}.")
            button.toggled.connect(lambda on, i=index: self.contrast_stack.setCurrentIndex(i) if on else None)
        layout.addWidget(self.contrast_stack)
        layout.addStretch(1)
        card = GCSCard("Display", body)
        card.setMinimumWidth(250)
        return card

    def _build_kinematics_card(self) -> GCSCard:
        # TrackingPanel's existing "gcs" source — table, 3-D height-time plot, fit
        # order and CSV export — embedded rather than reimplemented.
        from src.UI.solar_measure_tools import TrackingPanel

        self.tracking_panel = TrackingPanel(self)
        self.tracking_panel.set_source("gcs")
        self.tracking_panel.fit_btn.clicked.connect(self._on_fit_kinematics)
        self.tracking_panel.clear_btn.clicked.connect(self._on_clear_fits)
        card = GCSCard("Kinematics", self.tracking_panel, scroll_body=True)
        card.setMinimumWidth(240)
        return card

    def _install_shortcuts(self) -> None:
        """Playback and layout keys, active while an image has focus.

        Scoped to the image stage so Space, the arrows and the digits keep their
        ordinary meaning in the date editors and spin boxes.
        """
        bindings = (
            ("Space", self.toggle_play),
            ("Left", self.previous_frame),
            ("Right", self.next_frame),
            ("Home", self._rewind),
            ("0", lambda: self.set_layout_mode(LAYOUT_EQUAL)),
            ("1", lambda: self.set_layout_mode(LAYOUT_FOCUS, 0)),
            ("2", lambda: self.set_layout_mode(LAYOUT_FOCUS, 1)),
            ("3", lambda: self.set_layout_mode(LAYOUT_FOCUS, 2)),
        )
        self._shortcuts = []
        for key, slot in bindings:
            shortcut = QShortcut(QKeySequence(key), self.stage)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    # --------------------------------------------------------------- layout
    def layout_mode(self) -> str:
        return self.stage.mode()

    def set_layout_mode(self, mode: str, focus: int | None = None) -> None:
        """Equal: three images, controls beneath. Focus: one enlarged, controls beside."""
        self.stage.set_layout(mode, focus)
        focus_mode = self.stage.mode() == LAYOUT_FOCUS
        self.splitter.setOrientation(Qt.Horizontal if focus_mode else Qt.Vertical)
        self.deck.arrange(GCSControlDeck.COLUMN if focus_mode else GCSControlDeck.ROW)
        self._sync_layout_buttons()
        self._fit_splitter(force=True)

    def _on_layout_button(self, key: str) -> None:
        if key == LAYOUT_EQUAL:
            self.set_layout_mode(LAYOUT_EQUAL)
        else:
            self.set_layout_mode(LAYOUT_FOCUS, int(key.split(":")[1]))

    def _sync_layout_buttons(self) -> None:
        wanted = LAYOUT_EQUAL if self.stage.mode() == LAYOUT_EQUAL else f"focus:{self.stage.focus_index()}"
        for button in self._layout_group.buttons():
            if str(button.property("segment_key")) == wanted and not button.isChecked():
                was = button.blockSignals(True)
                button.setChecked(True)
                button.blockSignals(was)

    def _on_splitter_moved(self, _position: int, _index: int) -> None:
        self._user_split[self.stage.mode()] = True

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._schedule_split()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._schedule_split()

    def _schedule_split(self) -> None:
        # Coalesced: a window drag delivers a burst of resizes.
        if not self._split_pending:
            self._split_pending = True
            QTimer.singleShot(0, lambda: self._fit_splitter(force=False))

    def _fit_splitter(self, *, force: bool) -> None:
        """Give the images exactly the space they can use, the controls the rest."""
        self._split_pending = False
        mode = self.stage.mode()
        if self._user_split.get(mode) and not force:
            return
        if force:
            self._user_split[mode] = False
        handle = self.splitter.handleWidth()
        if mode == LAYOUT_EQUAL:
            total = self.splitter.height()
            if total <= 0:
                return
            # The cards' real minimum, not a guess: a few pixels short and the
            # whole deck grows a scrollbar for the sake of one slider row.
            needed = max(DECK_MIN_HEIGHT, self.deck.content_minimum_height())
            images = self.stage.ideal_height(self.splitter.width())
            images = max(120, min(images, total - handle - needed))
            self.splitter.setSizes([images, max(1, total - handle - images)])
        else:
            total = self.splitter.width()
            if total <= 0:
                return
            # Wide enough for the controls, and no wider than it takes for the
            # enlarged image to fill the stage's full height.
            spare = total - handle - self.stage.ideal_width(self.splitter.height())
            deck = max(DECK_COLUMN_MIN_WIDTH, min(DECK_COLUMN_MAX_WIDTH, spare))
            self.splitter.setSizes([max(1, total - handle - deck), deck])

    # ------------------------------------------------------------- playback
    def _frame_count(self) -> int:
        return len(self._time_axis)

    def _sync_transport(self) -> None:
        """Enable the transport only when there is a sequence to move through."""
        many = self._frame_count() > 1
        playing = self._play_timer.isActive()
        for button in (self.rewind_btn, self.prev_btn, self.next_btn):
            button.setEnabled(many)
        self.play_btn.setEnabled(many and not playing)
        self.pause_btn.setEnabled(playing)
        self.time_slider.setEnabled(many)

    def _on_fps_changed(self, value: int) -> None:
        self._play_timer.setInterval(int(1000 / max(1, int(value))))

    def play(self) -> None:
        if self._frame_count() <= 1:
            return
        self._play_timer.setInterval(int(1000 / max(1, int(self.fps_spin.value()))))
        self._play_timer.start()
        self._sync_transport()

    def pause(self) -> None:
        self._play_timer.stop()
        self._sync_transport()

    def toggle_play(self) -> None:
        if self._play_timer.isActive():
            self.pause()
        else:
            self.play()

    def _advance_frame(self) -> None:
        """Playback tick: wrap at the end rather than stopping dead."""
        if self._frame_count() <= 1:
            self.pause()
            return
        nxt = self.time_slider.value() + 1
        self.time_slider.setValue(0 if nxt > self.time_slider.maximum() else nxt)

    def next_frame(self) -> None:
        self.time_slider.setValue(min(self.time_slider.value() + 1, self.time_slider.maximum()))

    def previous_frame(self) -> None:
        self.time_slider.setValue(max(self.time_slider.value() - 1, 0))

    def _rewind(self) -> None:
        self.time_slider.setValue(0)

    # ------------------------------------------------------------- geometry
    def parameters(self) -> GCSParameters:
        """The one shared fit, seeded from whichever panel has frames first."""
        if self._params is None:
            observer = next((panel.observer for panel in self.panels if panel.observer is not None), None)
            lon = (observer.lon_deg + 90.0) if observer is not None else 90.0
            self._params = GCSParameters(
                lon_deg=((lon + 180.0) % 360.0) - 180.0,
                lat_deg=0.0,
                tilt_deg=0.0,
                height_rsun=8.0,
                alpha_deg=30.0,
                kappa=0.30,
            )
        return self._params

    def _panel(self, key: str) -> GCSViewpointPanel:
        return self.panels[PANEL_LABELS.index(key)]

    def _active_panels(self) -> list[GCSViewpointPanel]:
        return [panel for panel in self.panels if panel.observer is not None]

    def _cached_mesh(self, params: GCSParameters) -> Any:
        key = (params.height_rsun, params.alpha_deg, params.kappa)
        if self._mesh is None or self._mesh_key != key:
            self._mesh = gcs_mesh(params)
            self._mesh_key = key
        return self._mesh

    # ------------------------------------------------------------- rendering
    def _sync_all(self) -> None:
        """Redraw the one shell on every panel that has a usable observer."""
        params = self.parameters()
        self.gcs_panel.show_parameters(params)
        mesh = self._cached_mesh(params)
        # Slider is "density", the backend wants a ring stride, so invert.
        density = int(round(self.density_slider.value()))
        stride = max(1, 9 - density)

        for panel in self.panels:
            canvas = panel.canvas
            if panel.observer is None:
                canvas.clear_gcs_overlay()
                continue
            x, y, _ = wireframe_arcsec(
                params, panel.observer, mesh=mesh, ring_stride=stride, n_longitudinal=max(4, 12 - stride)
            )
            canvas.set_gcs_overlay(x, y)
            canvas.set_gcs_handles(handle_positions_arcsec(params, panel.observer))
            points = self._clicks[panel.label] if panel.label in self._clicks else []
            canvas.set_measurement_overlay(
                [point[0] for point in points], [point[1] for point in points], connect=False
            )
        self._apply_style()
        self._refresh_status()

    def _apply_style(self) -> None:
        for panel in self.panels:
            panel.canvas.set_gcs_style(
                width=self.width_slider.value(),
                color=self._wireframe_colour,
                opacity=self.opacity_slider.value() / 100.0,
            )

    def _pick_colour(self) -> None:
        from PySide6.QtGui import QColor

        chosen = QColorDialog.getColor(QColor(*self._wireframe_colour), self, "Wireframe colour")
        if chosen.isValid():
            self._wireframe_colour = (chosen.red(), chosen.green(), chosen.blue())
            self._apply_style()

    def _on_limb_toggled(self, on: bool) -> None:
        for panel in self.panels:
            panel.set_limb_visible(bool(on))

    def _on_axes_toggled(self, on: bool) -> None:
        for panel in self.panels:
            panel.set_axes_visible(bool(on))

    def _on_hover(self, key: str, x: Any, y: Any) -> None:
        """Cursor position in helioprojective terms: arcsec, solar radii, position angle."""
        if x is None or y is None:
            self.coord_label.setText("")
            return
        panel = self._panel(key)
        radius = math.hypot(float(x), float(y))
        # Position angle counter-clockwise from solar north: east (left) is 90°.
        angle = (math.degrees(math.atan2(-float(x), float(y))) + 360.0) % 360.0
        text = f"{key}  Tx {float(x):+.0f}″  Ty {float(y):+.0f}″  ·  PA {angle:.0f}°"
        if panel.observer is not None and panel.observer.rsun_arcsec:
            text += f"  ·  {radius / panel.observer.rsun_arcsec:.2f} R☉"
        self.coord_label.setText(text)

    # ------------------------------------------------------------------ time
    def _on_panel_frames_changed(self, panel: Any) -> None:
        # Every load opens the same way, whichever mode the view was left in.
        if getattr(panel, "frames", None):
            self._set_mode_buttons(DEFAULT_DIFFERENCE_MODE)
            for other in self.panels:
                if other is not panel and other.frames:
                    other.set_difference_mode(DEFAULT_DIFFERENCE_MODE)
        self._rebuild_time_axis()
        self._sync_all()

    def _rebuild_time_axis(self) -> None:
        """Span every panel's frames, so the slider covers the whole event."""
        stamps: list[datetime] = []
        for panel in self.panels:
            stamps.extend(panel.times())
        if not stamps:
            self._time_axis = []
            self.time_slider.setRange(0, 0)
            self.time_label.setText("—")
            self.pause()
            self._sync_transport()
            return
        self._time_axis = sorted(set(stamps))
        was = self.time_slider.blockSignals(True)
        self.time_slider.setRange(0, len(self._time_axis) - 1)
        if self._shared_time is not None:
            nearest = min(
                range(len(self._time_axis)),
                key=lambda i: abs((self._time_axis[i] - self._shared_time).total_seconds()),
            )
            self.time_slider.setValue(nearest)
        self.time_slider.blockSignals(was)
        self._sync_transport()
        self._on_time_changed(self.time_slider.value())

    def _on_time_changed(self, index: int) -> None:
        axis = self._time_axis
        if not axis:
            return
        when = axis[max(0, min(int(index), len(axis) - 1))]
        self._shared_time = when
        self.time_label.setText(f"{when:%Y-%m-%d %H:%M:%S}")
        for panel in self.panels:
            panel.set_shared_time(when)
        self._sync_all()

    def _difference_mode(self) -> str:
        """The view mode the toolbar currently has selected."""
        for button in self._difference_group.buttons():
            if button.isChecked():
                return str(button.property("segment_key"))
        return "raw"

    def _set_mode_buttons(self, mode: str) -> None:
        # Keep the toolbar in step when the mode is set from code, so it can never
        # disagree with what the panels are actually showing.
        for button in self._difference_group.buttons():
            wanted = str(button.property("segment_key")) == mode
            if button.isChecked() != wanted:
                was = button.blockSignals(True)
                button.setChecked(wanted)
                button.blockSignals(was)

    def _on_difference_mode(self, mode: str) -> None:
        self._set_mode_buttons(mode)
        for panel in self.panels:
            panel.set_difference_mode(mode)
        self._sync_all()

    # ----------------------------------------------------------------- event
    def event_range(self) -> tuple[datetime, datetime]:
        return (
            self.event_start_edit.dateTime().toPython().replace(tzinfo=None),
            self.event_end_edit.dateTime().toPython().replace(tzinfo=None),
        )

    def _apply_event_range_to_channels(self) -> None:
        """The event range is the master: setting it sets every channel's range."""
        start, end = self.event_range()
        for panel in self.panels:
            panel.set_date_range(start, end)
        self._apply_default_sources(start, force=False)

    def _apply_default_sources(self, when: datetime, *, force: bool) -> None:
        """Grey out sources that did not exist then, and replace any chosen one
        that did not with the classic triad's choice for that panel."""
        for panel, fallback in zip(self.panels, default_viewpoints(when)):
            panel.update_availability(when)
            if force or not panel.source_available(when):
                panel.select_source(fallback.key)

    def _fetch_all(self) -> None:
        start, end = self.event_range()
        problem = validate_range(start, end)
        if problem:
            self._set_status(f"Event range: {problem}")
            return
        self._apply_default_sources(start, force=False)
        started = sum(1 for panel in self.panels if panel.start_fetch())
        self._set_status(
            f"Loading JPEG2000 frames for {started} channel(s), {start:%Y-%m-%d %H:%M} → {end:%H:%M} UTC…"
            if started
            else "Nothing to load — every channel is busy, has an invalid range, or has no data then."
        )
        self._sync_busy()

    def _sync_busy(self) -> None:
        """Reflect in-flight loads in the controls, so a slow load looks busy
        rather than broken."""
        busy = any(panel.is_fetching() for panel in self.panels)
        self.load_all_btn.setEnabled(not busy)
        self.load_all_btn.setText("Loading…" if busy else "Load all")

    # ------------------------------------------------------------ interaction
    def _on_parameters(self, params: GCSParameters) -> None:
        self._params = params
        self._sync_all()

    def _on_handle(self, key: str, name: str, x: float, y: float, _finished: bool) -> None:
        if name != "apex":
            return
        panel = self._panel(key)
        if panel.observer is None:
            return
        # Dragging on any panel moves the one shared shell: steer from whichever
        # view shows the front most clearly.
        self._params = apply_apex_drag(self.parameters(), panel.observer, (x, y))
        self._sync_all()

    def _on_canvas_click(self, key: str, x: float, y: float, button: str) -> None:
        canvas = self._panel(key).canvas
        if canvas.gcs_drag_active():
            return
        if button == "right":
            if self._clicks[key]:
                self._clicks[key].pop()
        elif button == "left":
            self._clicks[key].append((float(x), float(y)))
        else:
            return
        self._sync_all()

    def _clear_points(self) -> None:
        for key in self._clicks:
            self._clicks[key] = []
        self._sync_all()

    # --------------------------------------------------------------- fitting
    def _viewpoints(self) -> list[GCSViewpoint]:
        out: list[GCSViewpoint] = []
        for panel in self.panels:
            if panel.observer is None:
                continue
            out.append(
                GCSViewpoint(
                    panel.observer,
                    np.asarray(self._clicks[panel.label], dtype=float).reshape(-1, 2),
                    panel.observer.label or panel.label,
                )
            )
        return out

    def _on_refine(self) -> None:
        try:
            result = refine_gcs(self._viewpoints(), self.parameters())
        except ValueError as exc:
            self._set_status(f"GCS refine: {exc}")
            return
        self._last_refinement = result
        self._params = result.parameters
        self._sync_all()
        self._set_status(result.message)

    def _on_commit(self) -> None:
        """Record the current fit at the current shared time."""
        from src.UI.solar_measure_tools import GCSFitEntry

        when = self._shared_time
        if when is None:
            self._set_status("Nothing to commit — load a channel first.")
            return
        result = self._last_refinement
        sigma = result.sigma if result is not None else {}
        active = self._active_panels()
        separations = [
            observer_separation_deg(a.observer, b.observer)
            for index, a in enumerate(active)
            for b in active[index + 1 :]
        ]
        params = self.parameters()
        self._fits[when] = GCSFitEntry(
            when=when,
            apex_height_rsun=params.height_rsun,
            lon_deg=params.lon_deg,
            lat_deg=params.lat_deg,
            tilt_deg=params.tilt_deg,
            alpha_deg=params.alpha_deg,
            kappa=params.kappa,
            rms_arcsec=float(getattr(result, "rms_arcsec", float("nan"))),
            n_points=sum(len(points) for points in self._clicks.values()),
            n_viewpoints=len(active),
            separation_deg=max(separations) if separations else 0.0,
            lon_err_deg=float(sigma.get("lon_deg", float("nan"))),
            lat_err_deg=float(sigma.get("lat_deg", float("nan"))),
            height_err_rsun=float(sigma.get("height_rsun", float("nan"))),
            refined=result is not None,
        )
        self._refresh_fits()
        self._set_status(
            f"Committed {when:%H:%M:%S} → apex {params.height_rsun:.2f} R☉ "
            f"({len(self._fits)} frame(s) recorded)."
        )

    def _refresh_fits(self) -> None:
        self.tracking_panel.refresh_gcs({index: entry for index, entry in enumerate(self._fits.values())})

    def _on_clear_fits(self) -> None:
        self._fits.clear()
        self._refresh_fits()
        self._set_status("Recorded GCS fits cleared.")

    def _on_fit_kinematics(self) -> None:
        """Fit apex height(t) over the recorded fits — a true 3-D radial speed."""
        from src.Backend.coronagraph import RSUN_KM, fit_height_time

        entries = sorted(self._fits.values(), key=lambda item: item[0])
        if len(entries) < 2:
            self._set_status("A height-time fit needs commits at two or more times.")
            return
        times = [entry[0] for entry in entries]
        if max(times) <= min(times):
            self._set_status("Every commit shares one time — there is no speed to fit.")
            return
        seconds = [(when - times[0]).total_seconds() for when in times]
        try:
            fit = fit_height_time(
                seconds,
                [float(entry[1]) * RSUN_KM for entry in entries],
                order=self.tracking_panel.fit_order(),
            )
        except ValueError as exc:
            self._set_status(f"Fit: {exc}")
            return
        self.tracking_panel.show_fit(fit)
        # "3-D" is the whole point: this height is de-projected, so the speed is
        # the real radial one rather than a plane-of-sky lower bound.
        self._set_status(
            f"3-D (de-projected) kinematics over {len(entries)} commit(s): "
            + self.tracking_panel.fit_summary(fit, noun="commits")
        )

    def _on_send(self) -> None:
        """Push the parameters back to the Solar Image Analysis window."""
        self.parametersCommitted.emit(self.parameters())
        measure = getattr(self.parent(), "_measure", None)
        if measure is not None and hasattr(measure, "set_gcs_parameters"):
            measure.set_gcs_parameters(self.parameters())
            self._set_status("Sent to the Solar Image Analysis window.")
            return
        self._set_status("Parameters ready — no analyzer window is listening.")

    # ---------------------------------------------------------------- status
    def _on_panel_status(self, text: str) -> None:
        self._set_status(text)
        self._sync_busy()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(str(text))

    def _refresh_status(self) -> None:
        active = self._active_panels()
        clicks = sum(len(points) for points in self._clicks.values())
        if len(active) < 2:
            self._set_status(
                f"{len(active)} viewpoint(s) · {clicks} point(s) clicked — "
                "one view cannot constrain the propagation direction, so it is held fixed. "
                "Load at least one more channel."
            )
            return
        separations = [
            observer_separation_deg(a.observer, b.observer)
            for index, a in enumerate(active)
            for b in active[index + 1 :]
        ]
        summary = ", ".join(f"{value:.0f}°" for value in separations)
        note = ""
        if max(separations) < MIN_USEFUL_SEPARATION_DEG:
            note = " — too close together to constrain longitude; it is held fixed"
        message = f"{len(active)} viewpoints · separations {summary} · {clicks} point(s) clicked{note}"
        stuck = [panel.label for panel in self.panels if panel.frames and not panel.can_difference()]
        if stuck and self._difference_mode() != "raw":
            message += (
                f" — panel {', '.join(stuck)} has one frame, so it cannot difference; "
                "widen its date range and load again"
            )
        self._set_status(message)

    # ----------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._play_timer.stop()
        for panel in self.panels:
            panel.shutdown()
        super().closeEvent(event)
