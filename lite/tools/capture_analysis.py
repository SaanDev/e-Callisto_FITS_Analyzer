"""Capture the full app's main window with the analysis tools in use.

Like ``capture_states.py`` (throwaway settings, the real theme manager, a
real window): loads a fixture, subtracts the background, then drives the
hardware canvas through the ruler, drift-rate, light-curve, annotation and
lasso tools with synthetic mouse events at fixed plot coordinates, the same
ones ``tests/screenshots.rs`` replays in Lite. For each state it saves the
window, the sidebar, the layout and the status bar text.

usage: python lite/tools/capture_analysis.py <out_dir> <light|dark> [state ...]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
FIX = REPO / "lite" / "parity" / "fixtures"

out_dir = Path(sys.argv[1])
theme_mode = sys.argv[2]
only = set(sys.argv[3:])
out_dir.mkdir(parents=True, exist_ok=True)

for key in (
    "E_CALLISTO_DISABLE_GEOMAGNETIC_AUTOFETCH",
    "E_CALLISTO_DISABLE_EVENT_CONTEXT_AUTOFETCH",
    "E_CALLISTO_DISABLE_RECOVERY_PROMPT",
    "E_CALLISTO_DISABLE_SAVE_PROMPT",
):
    os.environ[key] = "1"
scratch = Path(tempfile.mkdtemp(prefix="callisto_analysis_"))
os.environ["ECALLISTO_SPACE_WEATHER_CACHE"] = str(scratch / "sw_cache")
os.environ["ECALLISTO_PROJECTS_CACHE"] = str(scratch / "projects_cache")

from PySide6 import QtCore  # noqa: E402

_RealSettings = QtCore.QSettings
_INI = str(scratch / "settings.ini")


class _IsolatedSettings(_RealSettings):
    def __init__(self, *args, **kwargs):
        super().__init__(_INI, _RealSettings.IniFormat)


QtCore.QSettings = _IsolatedSettings
seed = _IsolatedSettings()
seed.setValue("ui/theme_mode", theme_mode)
seed.setValue("ui/view_mode", "modern")
seed.sync()

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setWindowIcon(QIcon(str(REPO / "assets" / "branding" / "icon.ico")))

from src.ui.app.main_window import MainWindow  # noqa: E402
from src.ui.common.mpl_style import apply_origin_style  # noqa: E402
from src.ui.common.theme_manager import AppTheme  # noqa: E402

MainWindow._prompt_recovery_if_needed = lambda self: None
apply_origin_style()
theme = AppTheme(app)
app.setProperty("theme_manager", theme)
win = MainWindow(theme=theme)
win.resize(1600, 1000)
win.move(40, 40)
win.show()
acc = win.accel_canvas


def pump(seconds: float = 1.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def widget_text(w):
    for attr in ("text", "title", "currentText", "placeholderText"):
        fn = getattr(w, attr, None)
        if callable(fn):
            try:
                value = fn()
            except TypeError:
                continue
            if isinstance(value, str) and value:
                return value
    return ""


def layout_dump():
    rows = []
    for w in [win, *win.findChildren(QWidget)]:
        if not w.isVisibleTo(win) or w.width() <= 0 or w.height() <= 0:
            continue
        p = w.mapTo(win, QtCore.QPoint(0, 0)) if w is not win else QtCore.QPoint(0, 0)
        rows.append({"class": type(w).__name__, "name": w.objectName(), "text": widget_text(w),
                     "rect": [p.x(), p.y(), w.width(), w.height()], "enabled": w.isEnabled()})
    return rows


def overlay_dump():
    """The overlay items on the plot, with their pens, data and scene positions."""
    from PySide6.QtGui import QColor
    gv = acc._graphics
    origin = gv.mapTo(win, QtCore.QPoint(0, 0))
    out = {"view_origin": [origin.x(), origin.y()], "view_range": [list(r) for r in acc._viewbox.viewRange()]}
    r = acc._viewbox.sceneBoundingRect()
    out["viewbox_rect"] = [r.x(), r.y(), r.width(), r.height()]

    def scene_xy(xs, ys):
        pts = [acc._viewbox.mapViewToScene(QPointF(float(x), float(y))) for x, y in zip(xs, ys)]
        return [[p.x(), p.y()] for p in pts]

    def pen_info(pen):
        return {"color": pen.color().name(QColor.NameFormat.HexArgb), "width": pen.widthF(),
                "style": int(pen.style().value), "cap": int(pen.capStyle().value),
                "join": int(pen.joinStyle().value), "cosmetic": pen.isCosmetic()}

    items = []
    for item in acc._plot.items:
        name = type(item).__name__
        if name in ("ImageItem",):
            continue
        row = {"class": name, "z": item.zValue(), "visible": item.isVisible()}
        try:
            if name == "PlotDataItem":
                xs, ys = item.getData()
                xs = [] if xs is None else [float(v) for v in xs]
                ys = [] if ys is None else [float(v) for v in ys]
                row.update({"x": xs, "y": ys, "scene": scene_xy(xs, ys), "pen": pen_info(item.opts.get("pen"))})
            elif name == "ScatterPlotItem":
                xs = [float(v) for v in item.data["x"]]
                ys = [float(v) for v in item.data["y"]]
                row.update({"x": xs, "y": ys, "scene": scene_xy(xs, ys), "size": float(item.opts["size"]),
                            "pen": pen_info(item.opts["pen"]),
                            "brush": item.opts["brush"].color().name(QColor.NameFormat.HexArgb)})
            elif name == "TextItem":
                t = item.textItem
                br = t.boundingRect()
                sb = t.sceneBoundingRect()
                f = t.font()
                row.update({
                    "text": t.toPlainText(), "pos": [float(item.pos().x()), float(item.pos().y())],
                    "scene_pos": [item.scenePos().x(), item.scenePos().y()],
                    "text_rect": [br.x(), br.y(), br.width(), br.height()],
                    "text_scene_rect": [sb.x(), sb.y(), sb.width(), sb.height()],
                    "font": [f.family(), f.pixelSize(), f.pointSizeF(), f.bold(), f.italic()],
                    "color": t.defaultTextColor().name(QColor.NameFormat.HexArgb),
                })
        except Exception as exc:  # pragma: no cover - diagnostics only
            row["error"] = repr(exc)
        items.append(row)
    out["items"] = items
    return out


def render_scene():
    """The canvas scene, drawn with the raster engine (see capture_states.py)."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    gv = acc._graphics
    dpr = win.devicePixelRatioF()
    img = QImage(int(round(gv.width() * dpr)), int(round(gv.height() * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(gv.backgroundBrush().color())
    painter = QPainter(img)
    gv.scene().render(painter, QRectF(0, 0, gv.width(), gv.height()), gv.mapToScene(gv.viewport().rect()).boundingRect())
    painter.end()
    return img


def capture(name: str) -> None:
    pump(1.2)
    if only and name not in only:
        render_scene()
        return
    from PySide6.QtGui import QPainter
    shot = win.grab()
    img = render_scene()
    gv = acc._graphics
    origin = gv.mapTo(win, QtCore.QPoint(0, 0))
    painter = QPainter(shot)
    painter.drawImage(origin, img)
    painter.end()
    shot.save(str(out_dir / f"analysis_{name}_{theme_mode}.png"))
    win.side_scroll.widget().grab().save(str(out_dir / f"analysis_{name}_sidebar_{theme_mode}.png"))
    (out_dir / f"analysis_{name}_{theme_mode}.json").write_text(
        json.dumps({"layout": layout_dump(), "overlays": overlay_dump(), "status": win.statusBar().currentMessage(),
                    "ruler": win.measurement_readout.text(), "title": win.windowTitle()}, indent=1),
        encoding="utf-8",
    )
    print("captured", name)


# ------------------------------------------------------------ synthetic mouse


class _Click:
    """What `_on_scene_mouse_clicked` reads from pyqtgraph's MouseClickEvent."""

    def __init__(self, pos, button, double=False):
        self._pos, self._button, self._double = pos, button, double

    def scenePos(self):
        return self._pos

    def button(self):
        return self._button

    def double(self):
        return self._double


class _SceneEvent:
    """What `_handle_scene_event` reads from a QGraphicsSceneMouseEvent."""

    def __init__(self, kind, pos, button=Qt.MouseButton.NoButton):
        self._kind, self._pos, self._button = kind, pos, button

    def type(self):
        return self._kind

    def scenePos(self):
        return self._pos

    def button(self):
        return self._button


def scene_pos(x, y):
    """Plot coordinates (seconds, MHz) to the scene position the mouse would be at."""
    return acc._viewbox.mapViewToScene(QPointF(float(x), acc._axis_y_scalar(float(y))))


def click(x, y, button=Qt.MouseButton.LeftButton, double=False):
    acc._on_scene_mouse_clicked(_Click(scene_pos(x, y), button, double))
    pump(0.05)


def double_click(x, y):
    # Qt delivers a click, then a double-click event, for one double-click.
    click(x, y)
    click(x, y, double=True)


def move(x, y):
    acc._on_scene_mouse_moved(scene_pos(x, y))
    pump(0.02)


def scene_event(kind, x, y, button=Qt.MouseButton.NoButton):
    acc._handle_scene_event(_SceneEvent(kind, scene_pos(x, y), button))
    pump(0.02)


# ------------------------------------------------------------------ the states

LEFT, RIGHT = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton

# The same points, in seconds and MHz, are in tests/screenshots.rs.
RULER = [(150.0, 80.0), (420.0, 45.0)]
DRIFT = [(100.0, 85.0), (220.0, 70.0), (380.0, 52.0), (560.0, 38.0)]
LIGHT_CURVES = [62.0, 47.3, 81.0]
POLYGON = [(120.0, 85.0), (300.0, 75.0), (250.0, 50.0)]
POLYGON_CURSOR = (140.0, 45.0)
LINE = [(500.0, 85.0), (800.0, 40.0)]
TEXT = (600.0, 70.0)
LASSO = [(100.0, 85.0), (300.0, 88.0), (500.0, 70.0), (520.0, 45.0), (350.0, 35.0), (150.0, 50.0)]

win.load_fits_into_main(str(FIX / "INDIA-GAURI_20261007_041500_02.fit.gz"))
# The plot is drawn on the next event-loop turn; subtracting before it would
# keep the empty canvas' view.
pump(1.0)
win.background_method_combo.setCurrentIndex(0)
win.background_subtract_btn.click()
capture("base")

win.activate_ruler_tool()
click(*RULER[0])
capture("ruler_pending")
click(*RULER[1])
capture("ruler")
win.clear_ruler_measurement()

win.activate_drift_tool()
for x, y in DRIFT[:-1]:
    click(x, y)
capture("drift_points")
double_click(*DRIFT[-1])
capture("drift")
acc._clear_drift_overlay()

win.plot_light_curve_at_frequency(LIGHT_CURVES[0])
capture("light_curve")
settings = win._light_curve_settings_payload()
settings.update({"mode": "multiple"})
win._set_light_curve_settings(settings)
for f in LIGHT_CURVES[1:]:
    win.plot_light_curve_at_frequency(f)
capture("light_curves")
settings.update({"line_style": "dashed", "line_width": 3.0, "opacity": 0.6, "show_frequency_label": False})
win._set_light_curve_settings(settings)
capture("light_curves_dashed")
settings.update({"line_style": "dotted", "line_width": 1.0, "opacity": 1.0, "vertical_scale": 2.5})
win._set_light_curve_settings(settings)
capture("light_curves_dotted")
win.clear_light_curve()

win.start_annotation_polygon()
for x, y in POLYGON:
    click(x, y)
move(*POLYGON_CURSOR)
capture("annotation_capture")
click(*POLYGON_CURSOR, button=RIGHT)
win.start_annotation_line()
for x, y in LINE:
    click(x, y)
# Add Text, with the dialog answered.
TEXT_STYLE = {"color": "#ffcc00", "font_family": "", "font_size": 16, "font_bold": True, "font_italic": False}
win._open_text_annotation_dialog = lambda **_kw: {"text": "Type III burst", **TEXT_STYLE}
win.start_annotation_text()
click(*TEXT)
capture("annotations")
win.clear_annotations()

win.activate_lasso()
scene_event(QEvent.Type.GraphicsSceneMousePress, *LASSO[0], LEFT)
for x, y in LASSO[1:]:
    scene_event(QEvent.Type.GraphicsSceneMouseMove, x, y)
capture("lasso_drawing")
scene_event(QEvent.Type.GraphicsSceneMouseRelease, *LASSO[-1], LEFT)
capture("isolated")

# ------------------------------------------------------------------ the dialogs


def dialog_layout(top):
    """Each visible widget's rectangle, font, size hints and policies."""
    from PySide6.QtCore import QPoint
    rows = []
    for w in [top, *top.findChildren(QWidget)]:
        if not w.isVisibleTo(top) or w.width() <= 0 or w.height() <= 0:
            continue
        p = w.mapTo(top, QPoint(0, 0)) if w is not top else QPoint(0, 0)
        font = w.font()
        sp = w.sizePolicy()
        rows.append({"class": type(w).__name__, "name": w.objectName(), "text": widget_text(w),
                     "rect": [p.x(), p.y(), w.width(), w.height()], "enabled": w.isEnabled(),
                     "font": [font.family(), round(font.pointSizeF() * 96 / 72, 2), font.weight()],
                     "hint": [w.sizeHint().width(), w.sizeHint().height()],
                     "min_hint": [w.minimumSizeHint().width(), w.minimumSizeHint().height()],
                     "policy": [sp.horizontalPolicy().name, sp.verticalPolicy().name]})
    return rows


def capture_dialog(name: str, widget) -> None:
    widget.show()
    pump(0.8)
    if only and name not in only:
        return
    widget.grab().save(str(out_dir / f"{name}_{theme_mode}.png"))
    info = {"title": widget.windowTitle(), "size": [widget.width(), widget.height()],
            "layout": dialog_layout(widget)}
    (out_dir / f"{name}_{theme_mode}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("captured", name)


from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QColorDialog, QInputDialog  # noqa: E402

from src.ui.radio.dialogs.annotation_text_dialog import TextAnnotationDialog  # noqa: E402
from src.ui.radio.dialogs.light_curve_frequency_dialog import LightCurveFrequencyDialog  # noqa: E402

limits = win._light_curve_frequency_limits()
dlg = LightCurveFrequencyDialog(limits[0], limits[1], initial_frequency_mhz=None, parent=win)
capture_dialog("dialog_lc_frequency", dlg)
dlg.close()

win._set_light_curve_settings(win._default_light_curve_settings())
dlg = win._open_or_focus_light_curve_settings_dialog()
capture_dialog("dialog_lc_settings", dlg)
dlg.close()

dlg = TextAnnotationDialog(title="Add Text Annotation", initial_style=win._text_annotation_style_defaults(), parent=win)
capture_dialog("dialog_text_annotation", dlg)
dlg.close()

dlg = TextAnnotationDialog(title="Edit Text Label", initial_text="Type III burst",
                           initial_style={"color": "#ffcc00", "font_family": "", "font_size": 16,
                                          "font_bold": True, "font_italic": False}, parent=win)
capture_dialog("dialog_text_annotation_edit", dlg)
dlg.close()

dlg = QColorDialog(QColor("#00e5ff"), win)
dlg.setWindowTitle("Choose light curve color")
capture_dialog("dialog_color", dlg)
dlg.close()

dlg = QInputDialog(win)
dlg.setWindowTitle("Move Text Label")
dlg.setLabelText("Choose label:")
dlg.setComboBoxItems(["#1 Type III burst @ (600.00, 70.00)", "#3 Second label @ (100.00, 40.00)"])
dlg.setComboBoxEditable(False)
capture_dialog("dialog_choose_label", dlg)
dlg.close()

# ------------------------------------------------------------ the analysis windows

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QDialog  # noqa: E402


def grab_with_scene(widget, views=()):
    """`widget.grab()`, with each pyqtgraph view's scene rendered in (an
    OpenGL viewport grabs blank)."""
    shot = widget.grab()
    dpr = widget.devicePixelRatioF()
    for gv in views:
        img = QImage(int(round(gv.width() * dpr)), int(round(gv.height() * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(gv.backgroundBrush().color())
        painter = QPainter(img)
        gv.scene().render(painter, QRectF(0, 0, gv.width(), gv.height()), gv.mapToScene(gv.viewport().rect()).boundingRect())
        painter.end()
        painter = QPainter(shot)
        painter.drawImage(gv.mapTo(widget, QtCore.QPoint(0, 0)), img)
        painter.end()
    return shot


def capture_window(name: str, widget, views=()) -> None:
    widget.show()
    pump(1.0)
    if only and name not in only:
        return
    grab_with_scene(widget, views).save(str(out_dir / f"{name}_{theme_mode}.png"))
    info = {"title": widget.windowTitle(), "size": [widget.width(), widget.height()],
            "layout": dialog_layout(widget)}
    status = getattr(widget, "status", None)
    if status is not None:
        info["status"] = status.currentMessage()
    (out_dir / f"{name}_{theme_mode}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("captured", name)


def capture_modal(name: str, open_fn) -> None:
    """A dialog shown with exec(): captured when it opens, then cancelled."""
    real_exec = QDialog.exec

    def fake_exec(self):
        capture_dialog(name, self)
        self.close()
        return int(QDialog.DialogCode.Rejected)

    QDialog.exec = fake_exec
    try:
        open_fn()
    finally:
        QDialog.exec = real_exec


# Maximum intensities of the isolated burst (auto-cleaned), then the ridge.
win.plot_max_intensities()
mx = win._max_intensity_dialog
capture_window("window_max", mx)
mx.track_ridge()
capture_window("window_max_ridge", mx)
mx.restore_per_column_maxima()
capture_modal("dialog_ridge_settings", mx.edit_ridge_settings)

# The Analyzer, from the Maximum Intensities window.
mx.open_analyze_window()
an = win._analyze_dialog
capture_window("window_analyzer", an)
an.plot_max()
capture_window("window_analyzer_max", an)
an.plot_fit()
capture_window("window_analyzer_fit", an)
an.extra_plot_combo.setCurrentIndex(0)
an.plot_extra()
capture_window("window_analyzer_extra", an)

# Type II band-splitting: points on both lanes, the fits, the results, BvR.
UPPER = [(300.0, 80.0), (340.0, 70.0), (380.0, 62.0), (420.0, 56.0)]
LOWER = [(300.0, 60.0), (340.0, 52.0), (380.0, 46.0), (420.0, 42.0)]
t2 = win._open_or_focus_type_ii_dialog()
views = (t2.plot_widget,)
capture_window("window_type_ii", t2, views)


def t2_click(x, y):
    pos = t2.plot_item.vb.mapViewToScene(QPointF(float(x), float(y)))
    t2._on_plot_clicked(_Click(pos, LEFT))
    pump(0.05)


t2.add_points_button.setChecked(True)
for x, y in UPPER:
    t2_click(x, y)
t2.band_combo.setCurrentIndex(1)
for x, y in LOWER:
    t2_click(x, y)
capture_window("window_type_ii_points", t2, views)
t2._fit_both_bands()
capture_window("window_type_ii_fits", t2, views)
t2._calculate_parameters()
capture_window("window_type_ii_results", t2, views)
t2.bvr_button.setChecked(True)
capture_window("window_type_ii_bvr", t2, views)
t2.bvr_button.setChecked(False)
settings = t2._open_or_focus_settings_dialog()
capture_dialog("dialog_type_ii_settings", settings)
settings.close()

win.close()
