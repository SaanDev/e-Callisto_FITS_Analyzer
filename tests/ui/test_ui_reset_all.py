"""
e-CALLISTO FITS Analyzer
Reset All must return the main window to the state it has right after start-up:
every sidebar readout, control, toolbar and menu action, both plot canvases and
the event-context, geomagnetic and composer state. The test records the fresh
window, uses it (load, process, restyle, open the event tools), resets, and
compares. Nothing touches the network: the geomagnetic loader is stubbed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QScrollBar,
    QWidget,
)

from src.backend.space_weather import event_catalogs as ec
from src.backend.space_weather.geomagnetic_context import GeomagneticContext, GeoSeries

BASE = datetime(2024, 5, 10, 6, 30)


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 10):
    for _ in range(times):
        _app().processEvents()


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))


def _path(widget: QWidget, root: QWidget) -> str:
    parts = []
    node = widget
    while node is not None and node is not root:
        parent = node.parentWidget()
        name = node.objectName() or type(node).__name__
        if parent is not None:
            same = [child for child in parent.children() if type(child) is type(node)]
            name = f"{name}[{same.index(node) if node in same else -1}]"
        parts.append(name)
        node = parent
    return "/".join(reversed(parts))


def _owned_by_another_window(obj, window) -> bool:
    """True inside a separate window (a tool window); menus do not count."""
    node = obj.parent()
    while node is not None and node is not window:
        if isinstance(node, QWidget) and node.isWindow() and not isinstance(node, QMenu):
            return True
        node = node.parent()
    return False


def _noise(key: str) -> bool:
    # Scroll ranges and combo pop-ups size themselves lazily; the status bar
    # carries the "All reset" message by design.
    return any(token in key for token in ("QScrollBar", "QListView", "QStatusBar", "QFrame[0]/"))


def _widget_state(window) -> dict:
    state = {}
    for widget in window.findChildren(QWidget):
        if isinstance(widget, QScrollBar) or widget.window() is not window:
            continue
        key = _path(widget, window)
        if _noise(key):
            continue
        shown = not widget.isHidden()
        if isinstance(widget, QLabel):
            state[key] = (widget.text(), widget.toolTip(), shown)
        elif isinstance(widget, QLineEdit):
            state[key] = (widget.text(), widget.isEnabled(), shown)
        elif isinstance(widget, QComboBox):
            state[key] = (widget.currentText(), widget.count(), widget.isEnabled(), shown)
        elif isinstance(widget, QAbstractSpinBox):
            state[key] = (widget.text(), widget.isEnabled(), shown)
        elif isinstance(widget, QAbstractSlider):
            state[key] = (widget.value(), widget.minimum(), widget.maximum(), widget.isEnabled(), shown)
        elif isinstance(widget, QGroupBox):
            state[key] = (widget.title(), widget.isChecked(), widget.isEnabled(), shown)
        elif isinstance(widget, QAbstractButton):
            state[key] = (widget.text(), widget.isChecked(), widget.isEnabled(), shown)
    for action in window.findChildren(QAction):
        if _owned_by_another_window(action, window):
            continue  # a closed tool window's action, until Qt deletes the window
        if action.text():
            state[f"action:{action.text()}"] = (action.isChecked(), action.isEnabled(), action.isVisible())
    return state


def _plot_state(window) -> dict:
    fig = window.canvas.figure
    acc = window.accel_canvas
    return {
        "mpl_axes": len(fig.axes),
        "mpl_text": [t.get_text() for ax in fig.axes for t in (ax.title, ax._left_title, ax._right_title) if t.get_text()]
        + [t.get_text() for ax in fig.axes for t in ax.texts] + [t.get_text() for t in fig.texts],
        "mpl_limits": [(ax.get_xlim(), ax.get_ylim(), ax.get_xlabel(), ax.get_ylabel()) for ax in fig.axes],
        "accel_title": acc._plot.titleLabel.text,
        "accel_text": (acc._title, acc._x_label, acc._y_label, acc._colorbar_label),
        "accel_axes": (acc._plot.getAxis("left").labelText, acc._plot.getAxis("bottom").labelText),
        "accel_colorbar": None if acc._color_bar is None else tuple(acc._color_bar.levels()),
        "accel_range": [[round(v, 3) for v in r] for r in acc._viewbox.viewRange()],
        "accel_strip": acc.has_event_strip,
    }


def _model_state(window) -> dict:
    geo = window._geomagnetic
    return {
        "geo": (geo.current_choice(), geo.context, geo.radio_interval, geo.before, geo.after, list(geo.markers)),
        "event_context": (window._event_context.range, window._event_context.result),
        "composer": (window._composer_state.touched, window._composer_state.spec.to_json()),
        "undo": (len(window._undo_stack), len(window._redo_stack)),
        "swaves": window._swaves_payload,
        "project": (window._project_path, window._project_dirty, window._project_has_content()),
        "tool_windows": (window._composer_window, window._geomagnetic_window),
        "graph_title_override": window.graph_title_override,
    }


def _fits(tmp_path):
    from tests.helpers.learmonth import write_test_callisto_fit

    return write_test_callisto_fit(
        tmp_path / "BIR_20240510_063000_01.fit",
        data=(np.arange(40 * 60) % 200).reshape(40, 60).astype(np.uint8),
        freqs=np.linspace(80.0, 20.0, 40),
        time=np.linspace(0.0, 1800.0, 60),
        date_obs="2024/05/10",
        time_obs="06:30:00",
    )


def _stub_geomagnetic_loader(event, *, before, after, **_kwargs):
    context = GeomagneticContext(event_start=event, start=event - before, end=event + after)
    hours = np.arange(np.datetime64(context.start, "h"), np.datetime64(context.end, "h"), np.timedelta64(1, "h"))
    context.series["dst"] = GeoSeries("dst", hours.astype("datetime64[s]"), np.full(hours.size, -30.0))
    return context


def _use_the_window(window, tmp_path) -> None:
    """Load a file and touch what a session usually touches."""
    window._geomagnetic._loader = _stub_geomagnetic_loader
    window.open_fits_paths([str(_fits(tmp_path))])
    _flush(20)
    window.title_edit.setText("My custom title")
    window.apply_background_subtraction()
    _flush(10)
    window.set_units_mode(True)
    window.set_axis_to_utc()
    window.cmap_combo.setCurrentIndex(min(3, window.cmap_combo.count() - 1))

    controller = window._event_context
    controller.result = ec.EventContextResult(
        BASE - timedelta(hours=6), BASE + timedelta(hours=7),
        events={ec.SOURCE_GOES: [ec.CatalogEvent(ec.KIND_FLARE, ec.SOURCE_GOES, datetime(2024, 5, 10, 6, 27),
                                                 datetime(2024, 5, 10, 6, 54), datetime(2024, 5, 10, 7, 6),
                                                 "X3.9", {"class": "X3.9"})]},
        errors={},
    )
    controller._result_key = controller.request_key()
    window.event_strip_action.setChecked(True)
    _flush(20)
    window._on_external_event_selected(datetime(2024, 5, 9, 12, 0), "Burst list: test", "burst_list")
    window.open_geomagnetic_window()
    window.open_event_figure_composer()
    _flush(30)


def test_reset_all_returns_the_window_to_its_start_up_state(tmp_path, monkeypatch):
    _app()
    from src.ui.app.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_prompt_recovery_if_needed", lambda self: None, raising=False)
    window = MainWindow(theme=None)
    window.resize(1400, 900)
    window.show()
    _flush(20)
    widgets, plots, models = _widget_state(window), _plot_state(window), _model_state(window)

    _use_the_window(window, tmp_path)
    # The session really did change things, or the comparison proves nothing.
    assert window.accel_canvas._title == "My custom title"
    assert window._event_strip_ax is not None and window.accel_canvas.has_event_strip
    assert window._undo_stack
    assert window._geomagnetic.context is not None
    assert "BIR" in window.timeline_status_label.text()

    window.reset_all()
    _flush(20)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    _flush(10)

    try:
        assert _plot_state(window) == plots
        assert _model_state(window) == models
        after = _widget_state(window)
        differences = {key: (widgets.get(key), after.get(key)) for key in set(widgets) | set(after)
                       if widgets.get(key) != after.get(key)}
        assert differences == {}
    finally:
        window.close()


def test_opening_a_sidebar_section_keeps_conditional_controls_hidden():
    # A section re-shows its controls when opened; the window then re-hides
    # the ones that do not apply (here: nothing is loaded or overridden).
    _app()
    from src.ui.app.main_window import MainWindow
    from src.ui.widgets.collapsible_sections import section_for

    window = MainWindow(theme=None)
    window.show()
    _flush()
    try:
        card = window.geomagnetic_card
        assert card.use_loaded_btn.isHidden()
        section = section_for(card)
        section.setExpanded(False)
        section.setExpanded(True)
        _flush()
        assert card.use_loaded_btn.isHidden()
        assert window.lower_value_sub_label.isHidden()
    finally:
        window.close()
