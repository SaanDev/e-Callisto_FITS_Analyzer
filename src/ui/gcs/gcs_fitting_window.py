"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Three-viewpoint GCS CME fitting (src/ui/gcs/gcs_fitting_window.py).

A standalone window that views the Sun from three places at once and fits one
Graduated Cylindrical Shell against all of them simultaneously.

Why three, and why one set of controls
--------------------------------------
GCS is ill posed from a single vantage: a wide CME pointed at the observer and a
narrow one travelling across the sky project almost identically, so direction
trades off against height and width and a single-view fit looks convincing while
meaning very little. Two well-separated views break that; a third — the classic
STEREO-B / SOHO / STEREO-A triad, the default from left to right —
over-constrains it. That is also why there is
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
The shell is drawn on every image with a usable projection, even one outside the
time tolerance; the tolerance only decides which views take part in the fit.
Playback with fits recorded draws the recorded shells instead of the working model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from src.backend.gcs.gcs_model import (
    MIN_USEFUL_SEPARATION_DEG,
    GCSParameters,
    GCSViewpoint,
    apply_apex_drag,
    gcs_mesh,
    handle_positions_arcsec,
    interpolate_parameters,
    observer_separation_deg,
    refine_gcs,
    refine_plan,
    wireframe_arcsec,
)
from src.backend.solar.helioviewer_jp2 import DEFAULT_MAX_FRAMES, default_viewpoints, validate_range
from src.backend.gcs.shock_model import (
    ShockParameters,
    apply_shock_apex_drag,
    interpolate_shock_parameters,
    refine_shock,
    shock_handle_positions_arcsec,
    shock_refine_plan,
    shock_wireframe_arcsec,
)
from src.ui.gcs.gcs_viewpoint_panel import (
    DEFAULT_DIFFERENCE_MODE,
    DIFFERENCE_MODES,
    RANGE_FORMAT,
    GCSViewpointPanel,
    _qdatetime_utc,
)
from src.ui.gcs.gcs_window_actions import GCSWindowActions
from src.ui.gcs.gcs_window_exports import GCSWindowExports


def _utc_now() -> datetime:
    """Naive UTC now, matching the convention the analyzer's date editors use."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _as_utc(value: datetime) -> datetime:
    """Normalize external timestamps before placing UTC wall times in Qt editors."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


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

#: The two models fitted side by side: the flux rope, and the shock it drives.
GCS_MODEL = "gcs"
SHOCK_MODEL = "shock"
MODEL_LABELS = {GCS_MODEL: "GCS", SHOCK_MODEL: "Shock"}
#: How a refine names the parameters it fitted, per model.
REFINE_PARAMETER_NAMES = {
    GCS_MODEL: {"height_rsun": "height", "lon_deg": "longitude", "lat_deg": "latitude",
                "tilt_deg": "tilt", "alpha_deg": "α", "kappa": "κ"},
    SHOCK_MODEL: {"height_rsun": "height", "lon_deg": "longitude", "lat_deg": "latitude",
                  "kappa": "κ", "epsilon": "ε", "alpha": "b/c", "tilt_deg": "tilt"},
}
#: The shock wireframe's colour: sky blue, which stays distinct from the orange
#: GCS shell for colour-blind viewers too (the Okabe-Ito pair).
DEFAULT_SHOCK_COLOUR = (86, 180, 233)


def _empty_clicks() -> dict[str, list[tuple[float, float]]]:
    return {key: [] for key in PANEL_LABELS}


@dataclass
class ModelTrack:
    """Everything one fitted model owns, so the flux rope and shock never mix.

    Front points are model specific — a shock front is not the ejecta front — as
    are the refinement, the recorded fits behind the kinematics, and the fits
    archived when the event changes.
    """

    key: str
    #: Current parameters; ``None`` until first needed.
    params: Any = None
    #: Front points on the displayed frame of each panel.
    clicks: dict[str, list[tuple[float, float]]] = field(default_factory=_empty_clicks)
    #: Points kept per (panel, frame id), restored when that frame is shown again.
    frame_points: dict[tuple[str, int], list[tuple[float, float]]] = field(default_factory=dict)
    last_refinement: Any = None
    #: Every left click as (panel, frame id, point), oldest first, so the most
    #: recent one can be undone wherever it was clicked.
    click_order: list[tuple[str, int | None, tuple[float, float]]] = field(default_factory=list)
    #: Recorded fits and their observation provenance, keyed by shared time.
    fits: dict[datetime, Any] = field(default_factory=dict)
    provenance: dict[datetime, Any] = field(default_factory=dict)
    archived_fits: dict[datetime, Any] = field(default_factory=dict)
    archived_provenance: dict[datetime, Any] = field(default_factory=dict)


def _gcs_track_attribute(name: str) -> property:
    """A window attribute that is the GCS track's ``name`` (the pre-shock spelling)."""
    return property(
        lambda self: getattr(self._tracks[GCS_MODEL], name),
        lambda self, value: setattr(self._tracks[GCS_MODEL], name, value),
        doc=f"The GCS model's ``{name}``; the shock's lives in ``_tracks[SHOCK_MODEL]``.",
    )


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


class RoomyCheckBox(QCheckBox):
    """A check box whose size hint leaves room for its whole label.

    Under the app theme a check box reports a width a few pixels short of the
    label it draws, so in a row of them each label is clipped by its neighbour.
    Two spaces' width of slack covers it and follows the font.
    """

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        hint = super().sizeHint()
        return QSize(hint.width() + self.fontMetrics().horizontalAdvance("  "), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return self.sizeHint()


class LetterButton(QPushButton):
    """A one-letter segment button (A, B, C), as narrow as its letter allows.

    A fixed 30 px clipped the letter to nothing under the app theme, which pads
    every push button 13 px a side. Leaving the width to the style instead gives
    80 px under Fusion, its minimum for any button with text. So the width is the
    letter plus whatever padding and frame the active style puts around a
    button's contents, and it follows the theme when that changes.
    """

    MIN_WIDTH = 30

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        height = super().sizeHint().height()
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.rect = QRect(0, 0, 100, max(1, height))
        contents = self.style().subElementRect(QStyle.SE_PushButtonContents, option, self)
        chrome = option.rect.width() - contents.width()
        return QSize(max(self.MIN_WIDTH, self.fontMetrics().horizontalAdvance(self.text()) + chrome + 4), height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return self.sizeHint()


class GCSCard(QFrame):
    """A titled group of controls in the deck."""

    def __init__(
        self,
        title: str,
        body: QWidget,
        *,
        scroll_body: bool = False,
        header: QWidget | None = None,
        parent: QWidget | None = None,
    ):
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
        self.title_label = heading
        if header is None:
            layout.addWidget(heading)
        else:
            # Controls that belong to the whole card sit on its title row, which
            # costs no height the card would not spend on the title anyway.
            title_row = QHBoxLayout()
            title_row.setSpacing(6)
            title_row.addWidget(heading)
            title_row.addStretch(1)
            title_row.addWidget(header)
            layout.addLayout(title_row)
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


class GCSFittingWindow(GCSWindowActions, GCSWindowExports, QMainWindow):
    """Fit one GCS shell against up to three simultaneous viewpoints."""

    # The GCS model's state, under the names it had before the shock model.
    _params = _gcs_track_attribute("params")
    _clicks = _gcs_track_attribute("clicks")
    _frame_points = _gcs_track_attribute("frame_points")
    _last_refinement = _gcs_track_attribute("last_refinement")
    _fits = _gcs_track_attribute("fits")
    _fit_provenance = _gcs_track_attribute("provenance")
    _archived_fits = _gcs_track_attribute("archived_fits")
    _archived_provenance = _gcs_track_attribute("archived_provenance")

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
        self._tracks: dict[str, ModelTrack] = {key: ModelTrack(key) for key in (GCS_MODEL, SHOCK_MODEL)}
        #: The model the handles, front points, Refine, Commit and kinematics act on.
        self._editing = GCS_MODEL
        self._params = initial
        self._displayed_frames: dict[str, int | None] = {key: None for key in PANEL_LABELS}
        self._mesh: Any = None
        self._mesh_key: tuple[float, float, float] | None = None
        self._shared_time: datetime | None = None
        self._requested_time = _as_utc(target_time) if target_time is not None else None
        self._updating_event = False
        self._last_event_range: tuple[datetime, datetime] | None = None
        self._time_axis: list[datetime] = []
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._advance_frame)
        #: Per model, how the shell on screen came from its recorded fits (for the
        #: status bar); dropped as soon as that model is edited.
        self._recorded_notes: dict[str, str] = {}
        self._wireframe_colour = (255, 140, 40)
        self._shock_colour = DEFAULT_SHOCK_COLOUR
        #: Whether the user has dragged the splitter, per layout. Until they do,
        #: every resize re-fits it so the images leave no space unused.
        self._user_split = {LAYOUT_EQUAL: False, LAYOUT_FOCUS: False}
        self._split_pending = False

        if event_range is None:
            centre = _as_utc(target_time or _utc_now()).replace(microsecond=0, second=0)
            event_range = (centre - DEFAULT_EVENT_HALF_WIDTH, centre + DEFAULT_EVENT_HALF_WIDTH)
        elif self._requested_time is None:
            self._requested_time = _as_utc(event_range[0]) + (
                _as_utc(event_range[1]) - _as_utc(event_range[0])
            ) / 2
        start, end = (_as_utc(value) for value in event_range)

        self._build_ui(cache_dir=cache_dir, start=start, end=end)
        self._apply_default_sources(start, force=True)
        self._apply_event_range_to_channels()
        self._sync_all()

        try:
            from src.ui.common.gui_shared import fit_window_to_screen

            fit_window_to_screen(self, 1600, 1000)
        except Exception:
            self.resize(1600, 1000)

    # ------------------------------------------------------------------- UI
    def _build_ui(self, *, cache_dir: Any, start: datetime, end: datetime) -> None:
        self.panels: list[GCSViewpointPanel] = []
        for label, source in zip(PANEL_LABELS, default_viewpoints()):
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
        self._build_menus()
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
            button = LetterButton(text) if len(text) == 1 else QPushButton(text)
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
            (self.play_btn, QStyle.SP_MediaPlay,
             "Play (Space). With fits recorded, the shell follows them frame by frame.", self.play),
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
            "UTC time. Check each panel's offset; frames outside the allowed gap\n"
            "are excluded from fitting."
        )
        self.time_slider.valueChanged.connect(self._on_time_changed)
        self.time_label = QLabel("—")
        self.time_label.setToolTip("Shared target time in UTC. Each image caption reports its actual observation time.")
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
        self.banner_check = QCheckBox("Banner")
        self.banner_check.setChecked(True)
        self.banner_check.setToolTip(
            "Show the information banner across the top of each image: instrument,\n"
            "observation time, offset from the shared time and difference reference (B)."
        )
        self.banner_check.toggled.connect(self._on_banner_toggled)
        row.addWidget(self.limb_check)
        row.addWidget(self.axes_check)
        row.addWidget(self.banner_check)
        return bar

    def _build_event_card(self, start: datetime, end: datetime) -> GCSCard:
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        self.event_start_edit = QDateTimeEdit()
        self.event_end_edit = QDateTimeEdit()
        for edit, which in ((self.event_start_edit, "start"), (self.event_end_edit, "end")):
            edit.setTimeSpec(Qt.UTC)
            edit.setDateTime(_qdatetime_utc(start if which == "start" else end))
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

        self.sync_tolerance_spin = QDoubleSpinBox()
        self.sync_tolerance_spin.setRange(0.0, 60.0)
        self.sync_tolerance_spin.setDecimals(1)
        self.sync_tolerance_spin.setSingleStep(0.5)
        self.sync_tolerance_spin.setValue(5.0)
        self.sync_tolerance_spin.setSuffix(" min")
        self.sync_tolerance_spin.setToolTip(
            "Maximum absolute offset from the shared UTC time for each fitted image.\n"
            "The 5-minute default is a workflow choice, not a scientific accuracy guarantee.\n"
            "Use a smaller gap for a rapidly evolving CME; 0 requires exact timestamps."
        )
        self.sync_tolerance_spin.valueChanged.connect(self._on_sync_tolerance_changed)
        grid.addWidget(QLabel("Max time offset"), 2, 0, 1, 2)
        grid.addWidget(self.sync_tolerance_spin, 2, 3)

        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        rule.setFrameShadow(QFrame.Sunken)
        grid.addWidget(rule, 3, 0, 1, 4)

        row = 4
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
        card = GCSCard("Event & channels (UTC)", body)
        card.setMinimumWidth(360)
        return card

    def _build_model_card(self) -> GCSCard:
        from src.ui.gcs.shock_parameter_panel import ShockParameterPanel
        from src.ui.solar.solar_measure_tools import GCSParameterPanel

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Both models are drawn together; one is edited at a time, which decides
        # what the handles, clicks, Refine, Commit and the kinematics card act on.
        # Radio buttons on the card's title row: under the app theme a push button
        # is 46 px tall, and every pixel the deck gains is taken from the images.
        edit_holder = QWidget()
        edit_row = QHBoxLayout(edit_holder)
        edit_row.setContentsMargins(0, 0, 0, 0)
        edit_row.setSpacing(8)
        edit_row.addWidget(QLabel("Edit"))
        self._model_group = QButtonGroup(self)
        self._model_group.setExclusive(True)
        for index, (key, text) in enumerate(((GCS_MODEL, "GCS flux rope"), (SHOCK_MODEL, "Shock"))):
            radio = QRadioButton(text)
            radio.setChecked(key == GCS_MODEL)
            radio.setProperty("segment_key", key)
            self._model_group.addButton(radio, index)
            edit_row.addWidget(radio)
        tips = {
            GCS_MODEL: "Fit the flux rope (the CME ejecta) with the Graduated Cylindrical Shell.",
            SHOCK_MODEL: (
                "Fit the shock the CME drives — the faint outer envelope — with a spheroid or\n"
                "ellipsoid, in PyThea's parameters. Its front points and records are separate."
            ),
        }
        for button in self._model_group.buttons():
            key = str(button.property("segment_key"))
            button.setToolTip(tips[key])
            button.toggled.connect(lambda on, value=key: self.set_editing_model(value) if on else None)

        # One parameter set per model for every panel — see the module docstring.
        self.gcs_panel = GCSParameterPanel()
        self.gcs_panel.parametersChanged.connect(self._on_parameters)
        self.gcs_panel.refineRequested.connect(self._on_refine)
        self.gcs_panel.commitRequested.connect(self._on_commit)
        self.gcs_panel.commit_gcs_btn.setText("Commit GCS")
        self.gcs_panel.commit_gcs_btn.setToolTip(
            "Record this fit at the current time. Commit across several times to build\n"
            "a de-projected 3-D height-time profile."
        )
        self.shock_panel = ShockParameterPanel()
        self.shock_panel.parametersChanged.connect(self._on_shock_parameters)
        self.shock_panel.refineRequested.connect(self._on_refine)
        self.shock_panel.commitRequested.connect(self._on_commit)
        self.shock_panel.commit_btn.setToolTip(
            "Record this shock fit at the current time. Commit across several times to build\n"
            "a de-projected 3-D height-time profile of the shock apex."
        )
        self.model_stack = QStackedWidget()
        self.model_stack.addWidget(self.gcs_panel)
        self.model_stack.addWidget(self.shock_panel)
        layout.addWidget(self.model_stack)

        interaction = QHBoxLayout()
        self.pick_points_check = RoomyCheckBox("Pick front points")
        self.pick_points_check.setChecked(True)
        self.pick_points_check.setToolTip(
            "Left-click the front to add a constraint for the edited model; right-click removes that\n"
            "panel's last point, and Undo point the last one clicked anywhere. Turn off to navigate\n"
            "without adding points. Points belong to the displayed frame."
        )
        self.wireframe_check = RoomyCheckBox("GCS")
        self.wireframe_check.setChecked(True)
        self.wireframe_check.setToolTip("Show the GCS shell. Hide it temporarily to inspect the observed front.")
        self.wireframe_check.toggled.connect(lambda _on: self._sync_all())
        # Off until the shock is first edited, so a GCS-only fit is never cluttered.
        self.shock_check = RoomyCheckBox("Shock")
        self.shock_check.setChecked(False)
        self.shock_check.setToolTip("Show the shock model in every view, alongside the GCS shell.")
        self.shock_check.toggled.connect(lambda _on: self._sync_all())
        interaction.setSpacing(12)
        interaction.addWidget(self.pick_points_check, 1)
        interaction.addWidget(self.wireframe_check)
        interaction.addWidget(self.shock_check)
        layout.addLayout(interaction)

        extras = QHBoxLayout()
        self.undo_point_btn = QPushButton("Undo point")
        self.undo_point_btn.setToolTip(
            "Remove the front point clicked last for the edited model, in whichever panel\n"
            "it was clicked (Ctrl+Z). Only points on the displayed frames can be undone."
        )
        self.undo_point_btn.clicked.connect(self._undo_last_point)
        self.clear_points_btn = QPushButton("Clear points")
        self.clear_points_btn.setToolTip("Remove all of the edited model's front points on the displayed frames.")
        self.clear_points_btn.clicked.connect(self._clear_points)
        extras.addWidget(self.undo_point_btn)
        extras.addWidget(self.clear_points_btn)
        layout.addLayout(extras)
        layout.addStretch(1)
        card = GCSCard("Model", body, header=edit_holder)
        card.title_label.setToolTip(
            "Align → pick front → refine → record, for each model.\n"
            "Fit the same feature in independent, near-simultaneous views. GCS describes the flux rope;\n"
            "the shock model describes the outer shock envelope. Heights are measured from Sun centre.\n"
            "Refinement errors are formal and do not include uncertainty from timing, front selection\n"
            "or model assumptions. See Help → Fitting workflow for details."
        )
        card.setMinimumWidth(280)
        return card

    def _build_display_card(self) -> GCSCard:
        from src.ui.solar.solar_measure_tools import GCSParameterSlider

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.width_slider = GCSParameterSlider("Width", 2.6, minimum=0.5, maximum=8.0, unit=" px", decimals=1)
        self.opacity_slider = GCSParameterSlider("Opacity", 100.0, minimum=10.0, maximum=100.0, unit="%", decimals=0)
        # 1 = coarsest, 8 = every ring. Defaulting well short of the maximum: at a
        # heavy pen width a full-density mesh fills in and reads as a solid blob.
        self.density_slider = GCSParameterSlider("Density", 5.0, minimum=1.0, maximum=8.0, unit="", decimals=0)
        for slider, decimals in ((self.width_slider, 1), (self.opacity_slider, 0), (self.density_slider, 0)):
            slider.readout.setDecimals(decimals)
        self.density_slider.setToolTip(
            "How fine the drawn mesh is. Higher is denser; back it off when a thick\n"
            "pen starts filling the shell in instead of outlining it."
        )
        wire_heading = QHBoxLayout()
        wire_heading.addWidget(QLabel("Wireframe"))
        wire_heading.addStretch(1)
        self.colour_btn = QPushButton("GCS…")
        self.colour_btn.setToolTip("Colour of the GCS shell.")
        self.colour_btn.clicked.connect(self._pick_colour)
        self.shock_colour_btn = QPushButton("Shock…")
        self.shock_colour_btn.setToolTip("Colour of the shock model.")
        self.shock_colour_btn.clicked.connect(self._pick_shock_colour)
        wire_heading.addWidget(self.colour_btn)
        wire_heading.addWidget(self.shock_colour_btn)
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
        from src.ui.solar.solar_measure_tools import TrackingPanel

        self.tracking_panel = TrackingPanel(self)
        self.tracking_panel.set_source("gcs")
        self.tracking_panel.auto_advance_check.hide()
        self.tracking_panel.table.setMinimumHeight(90)
        self.tracking_panel.table.setMaximumHeight(130)
        self.tracking_panel.table.setMinimumWidth(240)
        self.tracking_panel.plot.setFixedHeight(160)
        self.tracking_panel.fit_btn.setText("Fit height–time")
        self.tracking_panel.clear_btn.setText("Clear fits")
        self.tracking_panel.table.setToolTip("Recorded fits. Use Fit → Restore recorded model to revisit the current time.")
        self.tracking_panel.fit_order_combo.currentIndexChanged.connect(self._sync_kinematics_buttons)
        self.tracking_panel.table.cellDoubleClicked.connect(self._restore_fit_row)
        self.tracking_panel.fit_btn.setToolTip("Fit recorded model apex heights over time; errors are formal fit errors.")
        self.tracking_panel.plot.setLabel("bottom", "t (s since first recorded fit)")
        self.tracking_panel.fit_btn.clicked.connect(self._on_fit_kinematics)
        self.tracking_panel.clear_btn.clicked.connect(self._on_clear_fits)
        card = GCSCard("Kinematics · GCS", self.tracking_panel, scroll_body=True)
        card.setMinimumWidth(240)
        self.kinematics_card = card
        return card

    def _show_kinematics(self, key: str) -> None:
        """Point the kinematics card at one model's recorded fits."""
        panel = self.tracking_panel
        panel.set_source(key)
        # set_source restores the analyzer's longer button texts.
        panel.fit_btn.setText("Fit height–time")
        panel.clear_btn.setText("Clear fits")
        self.kinematics_card.title_label.setText(f"Kinematics · {MODEL_LABELS[key]}")
        self._refresh_fits()

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
            ("B", self.banner_check.toggle),
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
        if any(track.fits for track in self._tracks.values()):
            self._follow_recorded_fits()
            self._sync_all()

    def pause(self) -> None:
        was_playing = self._play_timer.isActive()
        self._play_timer.stop()
        self._sync_transport()
        if was_playing and self._recorded_notes:
            # The shell stays where playback left it and is what the sliders now
            # edit; only the playing mark goes from the status bar.
            self._refresh_status()

    def _follow_recorded_fits(self) -> None:
        """Make each model the shell its recorded fits give for the shared time.

        Called whenever the shared time moves — playback, the step buttons and
        keys, the slider — so the grid on screen is the fitted evolution for the
        images shown rather than wherever the sliders were last left. A model with
        nothing recorded keeps its working parameters.
        """
        self._recorded_notes = {}
        if self._shared_time is None:
            return
        for key in (GCS_MODEL, SHOCK_MODEL):
            found = self._recorded_shell(self._shared_time, key)
            if found is None:
                continue
            params, note = found
            track = self._tracks[key]
            if params != track.params:
                track.params = params
                track.last_refinement = None
            self._recorded_notes[key] = note

    def _forget_recorded_note(self, key: str | None = None) -> None:
        """A model was edited, so its shell is no longer the recorded one (all models if None)."""
        if key is None:
            self._recorded_notes = {}
        else:
            self._recorded_notes.pop(key, None)

    def _recorded_shell(self, when: datetime, key: str = GCS_MODEL) -> tuple[Any, str] | None:
        """A model's recorded shell at ``when``, and a note saying how it was found.

        The fit recorded for the images on screen when there is one. Between two
        recorded times the two fits are interpolated in time — a display of the
        evolution, not a fit. Before the first and after the last, the nearest
        fit is held.
        """
        fits = self._tracks[key].fits
        if not fits:
            return None
        exact = self._recorded_time_for_current_frames(key)
        if exact is None and when in fits:
            exact = when
        if exact is not None:
            return self._recorded_parameters(exact, key), f"recorded fit {exact:%H:%M:%S}"
        times = sorted(fits)
        if when < times[0]:
            return self._recorded_parameters(times[0], key), f"recorded fit {times[0]:%H:%M:%S} held (before first fit)"
        if when > times[-1]:
            return self._recorded_parameters(times[-1], key), f"recorded fit {times[-1]:%H:%M:%S} held (after last fit)"
        before = max(stamp for stamp in times if stamp < when)
        after = min(stamp for stamp in times if stamp > when)
        fraction = (when - before).total_seconds() / (after - before).total_seconds()
        interpolate = interpolate_parameters if key == GCS_MODEL else interpolate_shock_parameters
        params = interpolate(self._recorded_parameters(before, key), self._recorded_parameters(after, key), fraction)
        return params, f"interpolated between recorded fits {before:%H:%M:%S} and {after:%H:%M:%S}"

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

    def shock_parameters(self) -> ShockParameters:
        """The one shared shock, first aimed along the GCS shell's direction."""
        track = self._tracks[SHOCK_MODEL]
        if track.params is None:
            from src.ui.gcs.shock_parameter_panel import default_shock

            gcs = self.parameters()
            track.params = default_shock(gcs.lon_deg, gcs.lat_deg)
        return track.params

    def _model_parameters(self, key: str) -> Any:
        return self.parameters() if key == GCS_MODEL else self.shock_parameters()

    def _track(self, key: str | None = None) -> ModelTrack:
        """A model's state; the edited model's by default."""
        return self._tracks[key or self._editing]

    def editing_model(self) -> str:
        return self._editing

    def set_editing_model(self, key: str) -> None:
        """Choose the model the handles, front points, Refine, Commit and kinematics act on.

        The chosen model is shown if it was hidden: editing an invisible model
        would move nothing on screen.
        """
        if key not in self._tracks:
            raise ValueError(f"Unknown model: {key}")
        self._editing = key
        for button in self._model_group.buttons():
            wanted = str(button.property("segment_key")) == key
            if button.isChecked() != wanted:
                was = button.blockSignals(True)
                button.setChecked(wanted)
                button.blockSignals(was)
        self.model_stack.setCurrentWidget(self.gcs_panel if key == GCS_MODEL else self.shock_panel)
        check = self.wireframe_check if key == GCS_MODEL else self.shock_check
        was = check.blockSignals(True)
        check.setChecked(True)
        check.blockSignals(was)
        self._show_kinematics(key)
        self._sync_all()

    def _panel(self, key: str) -> GCSViewpointPanel:
        return self.panels[PANEL_LABELS.index(key)]

    def _active_panels(self) -> list[GCSViewpointPanel]:
        return [panel for panel in self.panels if panel.observer is not None and panel.is_synchronized()]

    def _sync_tolerance_seconds(self) -> float:
        control = getattr(self, "sync_tolerance_spin", None)
        return float(control.value()) * 60.0 if control is not None else 300.0

    def _on_sync_tolerance_changed(self, _value: float = 0.0) -> None:
        self._forget_refinements()
        for panel in self.panels:
            panel.set_sync_tolerance(self._sync_tolerance_seconds())
        self._sync_all()

    def _synchronization_summary(self) -> str:
        excluded = [panel.label for panel in self.panels if panel.frames and not panel.is_synchronized()]
        if excluded:
            return f" · {', '.join(excluded)} outside time tolerance; excluded from fitting"
        offsets = [abs(panel.time_offset_seconds()) for panel in self._active_panels()
                   if panel.time_offset_seconds() is not None]
        return f" · max |Δt| {max(offsets):.0f} s" if offsets else ""

    def _forget_refinements(self) -> None:
        """Refinement errors describe the inputs they were computed from; drop them all."""
        for track in self._tracks.values():
            track.last_refinement = None

    def _cached_mesh(self, params: GCSParameters) -> Any:
        key = (params.height_rsun, params.alpha_deg, params.kappa)
        if self._mesh is None or self._mesh_key != key:
            self._mesh = gcs_mesh(params)
            self._mesh_key = key
        return self._mesh

    # ------------------------------------------------------------- rendering
    def _sync_all(self) -> None:
        """Redraw both models on every panel that has a usable observer."""
        params = self.parameters()
        shock = self.shock_parameters()
        self.gcs_panel.show_parameters(params)
        self.shock_panel.show_parameters(shock)
        if shock.model != getattr(self, "_shock_layout_model", shock.model):
            # An ellipsoid shows two more slider rows; let the images give them room.
            self._schedule_split()
        self._shock_layout_model = shock.model
        points_by_panel = self._track().clicks

        for panel in self.panels:
            canvas = panel.canvas
            if panel.observer is None:
                canvas.clear_model_overlays()
                canvas.set_measurement_overlay([], [], connect=False)
                continue
            # The models go on every image that can be projected, whatever its
            # offset from the shared time, so the views can always be compared
            # against them. Only views inside the time tolerance take part in the
            # fit, so only they offer the edited model's handle and front points.
            synchronized = panel.is_synchronized()
            gcs_xy, shock_xy = self._panel_wireframes(panel)
            if gcs_xy is not None:
                canvas.set_gcs_overlay(*gcs_xy)
                canvas.set_gcs_handles(
                    handle_positions_arcsec(params, panel.observer),
                    visible=synchronized and self._editing == GCS_MODEL,
                )
            else:
                canvas.clear_gcs_overlay()
            if shock_xy is not None:
                canvas.set_gcs_overlay(*shock_xy, layer=SHOCK_MODEL)
                canvas.set_gcs_handles(
                    shock_handle_positions_arcsec(shock, panel.observer),
                    visible=synchronized and self._editing == SHOCK_MODEL,
                    layer=SHOCK_MODEL,
                )
            else:
                canvas.clear_gcs_overlay(SHOCK_MODEL)
            points = points_by_panel.get(panel.label, []) if synchronized else []
            canvas.set_measurement_overlay(
                [point[0] for point in points], [point[1] for point in points], connect=False
            )
        self._apply_style()
        self._refresh_status()
        if hasattr(self, "menu_actions"):
            self._sync_menu_actions()

    def _panel_wireframes(self, panel: GCSViewpointPanel) -> tuple[Any, Any]:
        """Both models projected onto ``panel`` in arcsec, ``None`` for a hidden model.

        What the panel draws and what the exports draw, from one place.
        """
        if panel.observer is None:
            return None, None
        # Slider is "density", the backend wants a ring stride, so invert.
        stride = max(1, 9 - int(round(self.density_slider.value())))
        gcs_check = getattr(self, "wireframe_check", None)
        shock_check = getattr(self, "shock_check", None)
        gcs_xy = shock_xy = None
        if gcs_check is None or gcs_check.isChecked():
            params = self.parameters()
            x, y, _ = wireframe_arcsec(
                params,
                panel.observer,
                mesh=self._cached_mesh(params),
                ring_stride=stride,
                n_longitudinal=max(4, 12 - stride),
            )
            gcs_xy = (x, y)
        if shock_check is not None and shock_check.isChecked():
            shock_xy = shock_wireframe_arcsec(
                self.shock_parameters(), panel.observer, ring_stride=stride, n_meridians=max(4, 12 - stride)
            )
        return gcs_xy, shock_xy

    def _apply_style(self) -> None:
        # Width and opacity are shared; each model keeps its own colour.
        width = self.width_slider.value()
        opacity = self.opacity_slider.value() / 100.0
        for panel in self.panels:
            panel.canvas.set_gcs_style(width=width, color=self._wireframe_colour, opacity=opacity)
            panel.canvas.set_gcs_style(width=width, color=self._shock_colour, opacity=opacity, layer=SHOCK_MODEL)

    def _pick_colour(self) -> None:
        from PySide6.QtGui import QColor

        chosen = QColorDialog.getColor(QColor(*self._wireframe_colour), self, "GCS wireframe colour")
        if chosen.isValid():
            self._wireframe_colour = (chosen.red(), chosen.green(), chosen.blue())
            self._apply_style()

    def _pick_shock_colour(self) -> None:
        from PySide6.QtGui import QColor

        chosen = QColorDialog.getColor(QColor(*self._shock_colour), self, "Shock wireframe colour")
        if chosen.isValid():
            self._shock_colour = (chosen.red(), chosen.green(), chosen.blue())
            self._apply_style()

    def _on_limb_toggled(self, on: bool) -> None:
        for panel in self.panels:
            panel.set_limb_visible(bool(on))

    def _on_axes_toggled(self, on: bool) -> None:
        for panel in self.panels:
            panel.set_axes_visible(bool(on))

    def _on_banner_toggled(self, on: bool) -> None:
        for panel in self.panels:
            panel.set_caption_visible(bool(on))
        if hasattr(self, "menu_actions"):
            self._sync_menu_actions()

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
        # A reload/source edit changes the images to which annotations belong.
        label = panel.label
        self._displayed_frames[label] = None
        for track in self._tracks.values():
            track.clicks[label] = []
            track.frame_points = {key: points for key, points in track.frame_points.items() if key[0] != label}
            track.click_order = [entry for entry in track.click_order if entry[0] != label]
            track.last_refinement = None
        panel.set_sync_tolerance(self._sync_tolerance_seconds())
        # Every load opens the same way, whichever mode the view was left in.
        if getattr(panel, "frames", None):
            self._set_mode_buttons(DEFAULT_DIFFERENCE_MODE)
            for other in self.panels:
                if other is not panel and other.frames:
                    other.set_difference_mode(DEFAULT_DIFFERENCE_MODE)
        if not self._updating_event:
            self._rebuild_time_axis()
            self._sync_all()

    def _rebuild_time_axis(self) -> None:
        """Span every panel's frames, so the slider covers the whole event."""
        stamps: list[datetime] = []
        for panel in self.panels:
            stamps.extend(panel.times())
        if not stamps:
            self._time_axis = []
            self._shared_time = None
            self._forget_recorded_note()
            self.time_slider.setRange(0, 0)
            self.time_label.setText("—")
            self.pause()
            self._sync_transport()
            return
        self._time_axis = sorted(set(stamps))
        was = self.time_slider.blockSignals(True)
        self.time_slider.setRange(0, len(self._time_axis) - 1)
        anchor = self._requested_time or self._shared_time
        if anchor is not None:
            nearest = min(
                range(len(self._time_axis)),
                key=lambda i: abs((self._time_axis[i] - anchor).total_seconds()),
            )
            self.time_slider.setValue(nearest)
        else:
            self.time_slider.setValue(0)
        self.time_slider.blockSignals(was)
        self._sync_transport()
        self._on_time_changed(self.time_slider.value(), user_initiated=False)

    def _on_time_changed(self, index: int, *, user_initiated: bool = True) -> None:
        axis = self._time_axis
        if not axis:
            return
        when = axis[max(0, min(int(index), len(axis) - 1))]
        if user_initiated:
            self._requested_time = when
        moved = when != self._shared_time
        if moved:
            self._forget_refinements()
        self._shared_time = when
        self.time_label.setText(f"{when:%Y-%m-%d %H:%M:%S}")
        for panel in self.panels:
            previous = self._displayed_frames[panel.label]
            if previous is not None:
                for track in self._tracks.values():
                    track.frame_points[(panel.label, previous)] = list(track.clicks[panel.label])
            panel.set_shared_time(when)
            frame = panel.current_frame()
            current = id(frame) if frame is not None else None
            if current != previous:
                # Points belong to the image they were clicked on, per model.
                for track in self._tracks.values():
                    track.clicks[panel.label] = list(track.frame_points.get((panel.label, current), []))
                    track.last_refinement = None
            self._displayed_frames[panel.label] = current
        # Stepping, the slider and playback move the fitted shells with the
        # images. A reload that keeps the time must not overwrite a model being
        # edited, so only an actual move follows the records.
        if moved or self._play_timer.isActive():
            self._follow_recorded_fits()
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
        self._forget_refinements()
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
        previous = self._last_event_range
        self.pause()
        self._updating_event = True
        try:
            for panel in self.panels:
                panel.set_date_range(start, end)
            self._apply_default_sources(start, force=False)
        finally:
            self._updating_event = False
        if validate_range(start, end) is None:
            # Preserve work when the range is adjusted within an event. A jump
            # to a different event must never mix its height-time fit with this one.
            if previous is not None and (end < previous[0] or start > previous[1]):
                for track in self._tracks.values():
                    track.archived_fits.update(track.fits)
                    track.archived_provenance.update(track.provenance)
                    track.fits = {when: entry for when, entry in track.archived_fits.items()
                                  if start <= when <= end}
                    track.provenance = {when: track.archived_provenance[when]
                                        for when in track.fits if when in track.archived_provenance}
                    for when in track.fits:
                        track.archived_fits.pop(when, None)
                        track.archived_provenance.pop(when, None)
                self._forget_recorded_note()
                self._refresh_fits()
            self._last_event_range = (start, end)
            if self._requested_time is not None and not start <= self._requested_time <= end:
                self._requested_time = start + (end - start) / 2
        self._forget_refinements()
        self._rebuild_time_axis()
        self._sync_all()

    def set_time_window(self, start: datetime, end: datetime, *, target_time: datetime | None = None) -> None:
        """Adopt the event selected in the parent without starting a network load."""
        start, end = _as_utc(start), _as_utc(end)
        problem = validate_range(start, end)
        if problem:
            self._set_status(f"Event range: {problem}")
            return
        self._requested_time = _as_utc(target_time) if target_time is not None else start + (end - start) / 2
        changed = self.event_range() != (start, end)
        for editor, value in ((self.event_start_edit, start), (self.event_end_edit, end)):
            was = editor.blockSignals(True)
            editor.setDateTime(_qdatetime_utc(value))
            editor.blockSignals(was)
        if changed:
            self._apply_event_range_to_channels()
            self._set_status("Event synchronized from the analyzer. Load channels for this UTC range.")
        else:
            self._rebuild_time_axis()

    def set_target_time(self, when: datetime) -> None:
        """Follow a newly selected parent image, preserving an event that contains it."""
        when = _as_utc(when)
        start, end = self.event_range()
        if start <= when <= end:
            self._requested_time = when
            self._rebuild_time_axis()
        else:
            self.set_time_window(when - DEFAULT_EVENT_HALF_WIDTH, when + DEFAULT_EVENT_HALF_WIDTH,
                                 target_time=when)

    def _apply_default_sources(self, when: datetime, *, force: bool) -> None:
        """Grey out sources that did not exist then, and put a panel whose chosen
        source did not back on its default.

        The defaults never change with the date: panel A stays on STEREO-B COR2
        even for an event after the spacecraft was lost, and reports that it has
        no data when loaded, so the left-to-right order is always B, SOHO, A.
        """
        for panel, default in zip(self.panels, default_viewpoints()):
            panel.update_availability(when)
            if force or not panel.source_available(when):
                panel.select_source(default.key)

    def _fetch_all(self) -> None:
        start, end = self.event_range()
        problem = validate_range(start, end)
        if problem:
            self._set_status(f"Event range: {problem}")
            return
        self._apply_default_sources(start, force=False)
        started = sum(1 for panel in self.panels if panel.start_fetch())
        # Each panel's own refusal reached the status bar and is about to be
        # overwritten; keep the "no data then" ones, which the defaults can cause.
        empty = [
            f"{panel.label}: {panel.source().label} has no data then"
            for panel in self.panels
            if panel.source() is not None
            and not any(panel.source_available(when) for when in panel.date_range())
        ]
        message = (
            f"Loading JPEG2000 frames for {started} channel(s), {start:%Y-%m-%d %H:%M} → {end:%H:%M} UTC…"
            if started
            else "Nothing to load — every channel is busy, has an invalid range, or has no data then."
        )
        self._set_status(message + "".join(f" · {note}" for note in empty))
        self._sync_busy()

    def _sync_busy(self) -> None:
        """Reflect in-flight loads in the controls, so a slow load looks busy
        rather than broken."""
        busy = any(panel.is_fetching() for panel in self.panels)
        self.load_all_btn.setEnabled(not busy)
        self.load_all_btn.setText("Loading…" if busy else "Load all")
        if hasattr(self, "menu_actions"):
            self._sync_menu_actions()

    # ------------------------------------------------------------ interaction
    def _on_parameters(self, params: GCSParameters) -> None:
        self.pause()
        self._params = params
        self._last_refinement = None
        self._forget_recorded_note(GCS_MODEL)
        self._sync_all()

    def _on_shock_parameters(self, params: ShockParameters) -> None:
        self.pause()
        track = self._tracks[SHOCK_MODEL]
        track.params = params
        track.last_refinement = None
        self._forget_recorded_note(SHOCK_MODEL)
        self._sync_all()

    def _on_handle(self, key: str, name: str, x: float, y: float, _finished: bool) -> None:
        # Handles outside the GCS layer arrive as "<layer>:<name>".
        model, _, handle = name.rpartition(":")
        model = model or GCS_MODEL
        if handle != "apex" or model not in self._tracks:
            return
        panel = self._panel(key)
        if panel.observer is None or not panel.is_synchronized():
            return
        self.pause()
        # Dragging on any panel moves the one shared model: steer from whichever
        # view shows the front most clearly.
        track = self._tracks[model]
        if model == GCS_MODEL:
            track.params = apply_apex_drag(self.parameters(), panel.observer, (x, y))
        else:
            track.params = apply_shock_apex_drag(self.shock_parameters(), panel.observer, (x, y))
        track.last_refinement = None
        self._forget_recorded_note(model)
        self._sync_all()

    def _on_canvas_click(self, key: str, x: float, y: float, button: str) -> None:
        panel = self._panel(key)
        canvas = panel.canvas
        picking = getattr(self, "pick_points_check", None)
        if (picking is not None and not picking.isChecked()) or panel not in self._active_panels():
            return
        if canvas.gcs_drag_active():
            return
        self.pause()
        track = self._track()
        frame = self._displayed_frames[key]
        if button == "right":
            if track.clicks[key]:
                point = track.clicks[key].pop()
                self._drop_click_entry(track, key, frame, point)
        elif button == "left":
            point = (float(x), float(y))
            track.clicks[key].append(point)
            track.click_order.append((key, frame, point))
        else:
            return
        track.last_refinement = None
        self._sync_all()

    @staticmethod
    def _drop_click_entry(track: ModelTrack, key: str, frame: int | None, point: tuple[float, float]) -> None:
        """Forget the latest history entry for one removed point."""
        for position in range(len(track.click_order) - 1, -1, -1):
            if track.click_order[position] == (key, frame, point):
                del track.click_order[position]
                return

    def _last_point_entry(self) -> int | None:
        """Position in the edited model's history of its latest point still on screen."""
        track = self._track()
        for position in range(len(track.click_order) - 1, -1, -1):
            key, frame, point = track.click_order[position]
            if frame == self._displayed_frames.get(key) and point in track.clicks.get(key, ()):
                return position
        return None

    def _undo_last_point(self) -> None:
        """Remove the edited model's most recently clicked front point, in any panel.

        Only points on the displayed frames are candidates: one clicked on a frame
        that is no longer shown stays with that frame and can be undone there.
        """
        self.pause()
        position = self._last_point_entry()
        if position is None:
            self._set_status("No front point to undo on the displayed frames.")
            return
        track = self._track()
        key, _frame, point = track.click_order.pop(position)
        points = track.clicks[key]
        del points[len(points) - 1 - points[::-1].index(point)]
        track.last_refinement = None
        self._sync_all()
        subject = "front point" if self._editing == GCS_MODEL else "shock front point"
        self._set_status(f"Removed the last {subject}, from panel {key}.")

    def _clear_points(self) -> None:
        """Clear the edited model's points on the displayed frames."""
        track = self._track()
        for key in track.clicks:
            track.clicks[key] = []
            current = self._displayed_frames[key]
            if current is not None:
                track.frame_points.pop((key, current), None)
            track.click_order = [entry for entry in track.click_order if entry[:2] != (key, current)]
        track.last_refinement = None
        self._sync_all()

    # --------------------------------------------------------------- fitting
    def _viewpoints(self, model: str | None = None) -> list[GCSViewpoint]:
        """The synchronized views with a model's front points (the edited model's by default)."""
        from src.backend.solar.coronagraph_composite import INSTRUMENT_FOV_RSUN

        clicks = self._track(model).clicks
        out: list[GCSViewpoint] = []
        for panel in self._active_panels():
            frame = panel.current_frame()
            instrument = str(getattr(frame, "instrument", "") or "").upper()
            detector = str(getattr(frame, "detector", "") or "").upper()
            fov = INSTRUMENT_FOV_RSUN.get((instrument, detector))
            out.append(
                GCSViewpoint(
                    panel.observer,
                    np.asarray(clicks[panel.label], dtype=float).reshape(-1, 2),
                    panel.observer.label or panel.label,
                    fov_rsun=fov,
                )
            )
        return out

    def _refine_plan(self, model: str) -> tuple[tuple[str, ...], tuple[str, ...], int]:
        """What refining ``model`` fits now: ``(free, next stage, points)``.

        Every parameter once the points support it; before that, the best
        constrained ones, so Refine is useful from the second point on.
        """
        viewpoints = self._viewpoints(model)
        if model == GCS_MODEL:
            return refine_plan(viewpoints)
        return shock_refine_plan(viewpoints, self.shock_parameters().model)

    def _refine_scope(self, model: str, free: tuple[str, ...], upcoming: tuple[str, ...], points: int) -> str:
        names = REFINE_PARAMETER_NAMES[model]
        text = f" · fitted {', '.join(names[name] for name in free)}"
        if upcoming:
            needed = len(free) + len(upcoming) + 1 - points
            text += (
                f" · {needed} more point{'s' if needed != 1 else ''} would also fit "
                f"{' and '.join(names[name] for name in upcoming)}"
            )
        return text

    def _on_refine(self) -> None:
        """Refine the edited model against its own front points.

        A least-squares refine needs more points than free parameters, so with
        few points it fits only what they can constrain (see ``refine_plan``)
        and says which parameters that was and what more points would add.
        """
        self.pause()
        model = self._editing
        track = self._track(model)
        name = "GCS" if model == GCS_MODEL else "Shock"
        track.last_refinement = None
        if not self._active_panels():
            self._set_status(f"{name} refine: load the channels first — no image is inside the time tolerance.")
            return
        free, upcoming, points = self._refine_plan(model)
        if not free:
            front = "CME" if model == GCS_MODEL else "shock"
            self._set_status(
                f"{name} refine: click at least 2 points along the {front} front on the images "
                f"(left-click adds, right-click removes the panel's last) — {points} so far."
            )
            return
        try:
            if model == GCS_MODEL:
                result = refine_gcs(self._viewpoints(model), self.parameters(), free=free)
            else:
                result = refine_shock(self._viewpoints(model), self.shock_parameters(), free=free)
        except ValueError as exc:
            self._set_status(f"{name} refine: {exc}")
            return
        if not result.converged:
            self._set_status(f"{name} refine did not converge; retained the previous parameters. {result.message}")
            return
        track.last_refinement = result
        track.params = result.parameters
        self._forget_recorded_note(model)
        self._sync_all()
        self._set_status(result.message + self._refine_scope(model, free, upcoming, points))

    def _on_commit(self) -> None:
        """Record the edited model's current fit at the current shared time."""
        from src.ui.solar.solar_measure_tools import GCSFitEntry, ShockFitEntry

        self.pause()
        model = self._editing
        track = self._track(model)
        when = self._shared_time
        active = self._active_panels()
        if when is None or not active:
            self._set_status("Nothing to commit — load a dated channel within the synchronization tolerance.")
            return
        recorded_time = self._recorded_time_for_current_frames(model)
        if recorded_time is not None and recorded_time != when:
            self._set_status(
                f"These same observations are already recorded at {recorded_time:%Y-%m-%d %H:%M:%S} UTC. "
                "Return to that time to update the fit; repeated images are not independent height-time samples."
            )
            return
        params = self._model_parameters(model)
        result = track.last_refinement
        if result is not None and (not result.converged or result.parameters != params):
            result = None
            track.last_refinement = None
        sigma = result.sigma if result is not None else {}
        separations = [
            observer_separation_deg(a.observer, b.observer)
            for index, a in enumerate(active)
            for b in active[index + 1 :]
        ]
        common = dict(
            when=when,
            apex_height_rsun=params.height_rsun,
            lon_deg=params.lon_deg,
            lat_deg=params.lat_deg,
            tilt_deg=params.tilt_deg,
            kappa=params.kappa,
            rms_arcsec=float(getattr(result, "rms_arcsec", float("nan"))),
            n_points=int(result.n_points) if result is not None else sum(len(track.clicks[p.label]) for p in active),
            n_viewpoints=int(result.n_viewpoints) if result is not None else len(active),
            separation_deg=float(result.separation_deg) if result is not None else (max(separations) if separations else 0.0),
            lon_err_deg=float(sigma.get("lon_deg", float("nan"))),
            lat_err_deg=float(sigma.get("lat_deg", float("nan"))),
            height_err_rsun=float(sigma.get("height_rsun", float("nan"))),
            refined=result is not None,
        )
        if model == GCS_MODEL:
            track.fits[when] = GCSFitEntry(alpha_deg=params.alpha_deg, **common)
        else:
            track.fits[when] = ShockFitEntry(
                epsilon=params.epsilon, alpha=params.alpha, model=params.model, **common
            )
        track.provenance[when] = self._current_provenance(model)
        self._forget_recorded_note(model)
        self._refresh_fits()
        subject = "" if model == GCS_MODEL else "shock "
        self._set_status(
            f"Committed {subject}{when:%H:%M:%S} → apex {params.height_rsun:.2f} R☉ "
            f"({len(track.fits)} frame(s) recorded)."
        )

    def _recorded_time_for_current_frames(self, model: str | None = None) -> datetime | None:
        """Find a model's record for the same actual images, independent of slider time.

        Adjacent times in the union timeline can snap every panel to the same
        observation tuple. Counting both would invent an independent time sample.
        """
        def signature(provenance: dict[str, Any]) -> tuple:
            return tuple(sorted(
                (frame["panel"], frame.get("source_key"), frame.get("instrument"),
                 frame.get("observation_time_utc"))
                for frame in provenance.get("frames", ()) if frame.get("included_in_fit")
            ))

        track = self._track(model)
        if not track.fits:
            return None
        current = signature(self._current_provenance(model))
        if not current:
            return None
        for when in sorted(track.fits):
            if signature(track.provenance.get(when, {})) == current:
                return when
        return None

    def _refresh_fits(self) -> None:
        """Show the edited model's recorded fits in the kinematics card."""
        entries = dict(enumerate(sorted(self._track().fits.values(), key=lambda entry: entry[0])))
        if self._editing == GCS_MODEL:
            self.tracking_panel.refresh_gcs(entries)
        else:
            self.tracking_panel.refresh_shock(entries)
        self._sync_kinematics_buttons()

    def _sync_kinematics_buttons(self, _index: int = 0) -> None:
        fits = self._track().fits
        self.tracking_panel.fit_btn.setEnabled(len(fits) >= self.tracking_panel.fit_order() + 1)
        self.tracking_panel.clear_btn.setEnabled(bool(fits))
        heights = [entry.apex_height_rsun for entry in fits.values()]
        if heights and max(heights) - min(heights) < 0.01:
            # Polynomial roundoff in a constant-height series otherwise makes
            # autorange show 15-digit ticks and visually exaggerate numerical noise.
            centre = float(np.mean(heights))
            padding = max(0.05, abs(centre) * 0.01)
            self.tracking_panel.plot.setYRange(centre - padding, centre + padding, padding=0)
        if hasattr(self, "menu_actions"):
            self._sync_menu_actions()

    def _on_clear_fits(self) -> None:
        track = self._track()
        track.fits.clear()
        track.provenance.clear()
        self._forget_recorded_note(self._editing)
        self._refresh_fits()
        self._set_status(f"Recorded {'GCS' if self._editing == GCS_MODEL else 'shock'} fits cleared.")

    def _on_fit_kinematics(self) -> None:
        """Fit the edited model's apex height(t) for model-dependent radial kinematics."""
        from src.backend.solar.coronagraph import RSUN_KM, fit_height_time

        entries = sorted(self._track().fits.values(), key=lambda item: item[0])
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
        subject = "" if self._editing == GCS_MODEL else "shock-apex "
        self._set_status(
            f"3-D model-dependent (de-projected) {subject}kinematics over {len(entries)} commit(s): "
            + self.tracking_panel.fit_summary(fit, noun="commits")
        )

    # ---------------------------------------------------------------- status
    def _on_panel_status(self, text: str) -> None:
        self._set_status(text)
        self._sync_busy()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(str(text))
        self.status_label.setToolTip(str(text))

    def _refresh_status(self) -> None:
        active = self._active_panels()
        track = self._track()
        clicks = sum(len(track.clicks[panel.label]) for panel in active)
        can_commit = bool(active) and self._shared_time is not None
        self.gcs_panel.commit_gcs_btn.setEnabled(can_commit)
        self.shock_panel.commit_btn.setEnabled(can_commit)
        # Refine is available whenever there is an image to fit against: with too
        # few points it says what to click rather than sitting greyed out.
        for button in (self.gcs_panel.refine_btn, self.shock_panel.refine_btn):
            button.setEnabled(bool(active))
        self.clear_points_btn.setEnabled(any(track.clicks.values()))
        self.undo_point_btn.setEnabled(self._last_point_entry() is not None)
        synchronization = self._synchronization_summary()
        # Which recorded shells are drawn leads: they change with every step.
        notes = [f"{'Shell' if key == GCS_MODEL else 'Shock'}: {self._recorded_notes[key]}"
                 for key in (GCS_MODEL, SHOCK_MODEL) if key in self._recorded_notes]
        playback = ""
        if notes:
            playback = ("▶ " if self._play_timer.isActive() else "") + " · ".join(notes) + " · "
        point_text = "point(s) clicked" if self._editing == GCS_MODEL else "shock point(s) clicked"
        if not active:
            self._set_status(playback + "No synchronized viewpoint with valid WCS. "
                             "Set the UTC event range and load channels." + synchronization)
            return
        if len(active) < 2:
            self._set_status(
                playback + f"{len(active)} viewpoint(s) · {clicks} {point_text} — "
                "one view cannot constrain the propagation direction, so it is held fixed. "
                "Load at least one more channel." + synchronization
            )
            return
        separations = [
            observer_separation_deg(a.observer, b.observer)
            for index, a in enumerate(active)
            for b in active[index + 1 :]
        ]
        summary = ", ".join(f"{value:.0f}°" for value in separations)
        note = ""
        if not any(MIN_USEFUL_SEPARATION_DEG <= value <= 180.0 - MIN_USEFUL_SEPARATION_DEG
                   for value in separations):
            note = " — viewing geometry cannot constrain direction; it is held fixed"
        elif sum(bool(track.clicks[panel.label]) for panel in active) < 2:
            note = " — add front points in separated views to refine direction"
        message = f"{len(active)} viewpoints · separations {summary} · {clicks} {point_text}{note}{synchronization}"
        stuck = [panel.label for panel in self.panels if panel.frames and not panel.can_difference()]
        if stuck and self._difference_mode() != "raw":
            message += (
                f" — panel {', '.join(stuck)} has one frame, so it cannot difference; "
                "widen its date range and load again"
            )
        self._set_status(playback + message)

    # ----------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._play_timer.stop()
        for panel in self.panels:
            panel.shutdown()
        super().closeEvent(event)
