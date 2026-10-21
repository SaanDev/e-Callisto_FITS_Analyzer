"""Capture the full app's main window in named states with data loaded.

Like ``capture_reference.py`` (throwaway settings, the real theme manager,
a real window), but loads fixture files and drives the window through states,
saving for each: the window image, the widget layout, and the hardware plot's
geometry (view range, axis ticks and labels, colour levels).

usage: python lite/tools/capture_states.py <out_dir> <light|dark>
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
scratch = Path(tempfile.mkdtemp(prefix="callisto_states_"))
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

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setWindowIcon(QIcon(str(REPO / "assets" / "branding" / "icon.ico")))

from src.backend.radio.burst_processor import combine_compatible  # noqa: E402
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


def plot_dump():
    """Geometry of the hardware canvas, straight from pyqtgraph."""
    acc = win.accel_canvas
    info = {}
    plot = getattr(acc, "_plot", None)
    if plot is None:
        return info
    vb = plot.getViewBox()
    (x0, x1), (y0, y1) = vb.viewRange()
    info["view_range"] = [x0, x1, y0, y1]
    scene_rect = vb.sceneBoundingRect()
    origin = acc.mapTo(win, QtCore.QPoint(0, 0))
    info["canvas_origin"] = [origin.x(), origin.y(), acc.width(), acc.height()]
    info["viewbox_rect"] = [scene_rect.x(), scene_rect.y(), scene_rect.width(), scene_rect.height()]
    axes = {}
    for name in ("left", "bottom", "top", "right"):
        axis = plot.getAxis(name)
        if axis is None or not axis.isVisible():
            continue
        r = axis.sceneBoundingRect()
        lo, hi = (x0, x1) if name in ("bottom", "top") else (y0, y1)
        size = r.width() if name in ("bottom", "top") else r.height()
        ticks = []
        for spacing, values in axis.tickValues(lo, hi, size):
            strings = axis.tickStrings(values, axis.autoSIPrefixScale * axis.scale, spacing)
            ticks.append({"spacing": spacing, "values": list(map(float, values)), "strings": list(strings)})
        custom = getattr(axis, "_tickLevels", None)
        axes[name] = {
            "rect": [r.x(), r.y(), r.width(), r.height()],
            "label": axis.labelText,
            "units": axis.labelUnits,
            "ticks": ticks,
            "custom_ticks": [[[float(v), s] for v, s in level] for level in custom] if custom else None,
            "style": {k: str(v) for k, v in axis.style.items()},
        }
    info["axes"] = axes

    def rect_of(item):
        g = item.geometry()
        return [g.x(), g.y(), g.width(), g.height()]

    def axis_internals(axis):
        br = axis.label.boundingRect()
        return {
            "geometry": rect_of(axis),
            "scene_pos": [axis.scenePos().x(), axis.scenePos().y()],
            "textWidth": axis.textWidth,
            "textHeight": axis.textHeight,
            "fixedWidth": axis.fixedWidth,
            "fixedHeight": axis.fixedHeight,
            "label_visible": axis.label.isVisible(),
            "label_rect": [br.x(), br.y(), br.width(), br.height()],
            "label_pos": [axis.label.pos().x(), axis.label.pos().y()],
            "label_html": axis.label.toHtml()[-300:],
            "tickLength": axis.style["tickLength"],
            "tickTextOffset": list(axis.style["tickTextOffset"]),
            "visible": axis.isVisible(),
        }

    internals = {
        "graphics_size": [acc._graphics.width(), acc._graphics.height()],
        "ci_geometry": rect_of(acc._graphics.ci),
        "ci_margins": list(acc._graphics.ci.layout.getContentsMargins()),
        "ci_spacing": [acc._graphics.ci.layout.horizontalSpacing(), acc._graphics.ci.layout.verticalSpacing()],
        "plot_geometry": rect_of(plot),
        "plot_margins": list(plot.layout.getContentsMargins()),
        "plot_spacing": [plot.layout.horizontalSpacing(), plot.layout.verticalSpacing()],
        "title_geometry": rect_of(plot.titleLabel),
        "title_item_rect": list(plot.titleLabel.item.boundingRect().getRect()),
        "title_item_pos": [plot.titleLabel.item.pos().x(), plot.titleLabel.item.pos().y()],
        "title_visible": plot.titleLabel.isVisible(),
        "viewbox_geometry": rect_of(vb),
        "axes": {n: axis_internals(plot.getAxis(n)) for n in ("left", "bottom", "top", "right")},
        "layout_cells": [],
    }
    lay = plot.layout
    for r in range(lay.rowCount()):
        for c in range(lay.columnCount()):
            it = lay.itemAt(r, c)
            if it is not None:
                g = it.geometry()
                internals["layout_cells"].append([r, c, type(it).__name__, g.x(), g.y(), g.width(), g.height()])
    bar = getattr(acc, "_color_bar", None)
    if bar is not None:
        internals["colorbar"] = {
            "geometry": rect_of(bar),
            "margins": list(bar.layout.getContentsMargins()),
            "bar_rect": list(bar.bar.sceneBoundingRect().getRect()),
            "viewbox_geometry": rect_of(bar.getViewBox()),
            "axes": {n: axis_internals(bar.getAxis(n)) for n in ("left", "bottom", "top", "right")},
        }
    info["internals"] = internals
    info["title"] = plot.titleLabel.text if plot.titleLabel is not None else ""
    image = getattr(acc, "_image", None)
    if image is not None:
        info["image_rect"] = [image.mapRectToView(image.boundingRect()).getRect()]
        info["levels"] = list(map(float, image.levels)) if image.levels is not None else None
    bar = getattr(acc, "_color_bar", None)
    if bar is not None:
        r = bar.sceneBoundingRect()
        info["colorbar_rect"] = [r.x(), r.y(), r.width(), r.height()]
        try:
            info["colorbar_levels"] = list(map(float, bar.levels()))
        except Exception:
            pass
    return info


def render_scene():
    """The canvas scene, drawn with the raster engine.

    The hardware canvas is an OpenGL viewport, which QWidget.grab() leaves
    blank (and the screen is unavailable when the PC is locked).
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    gv = win.accel_canvas._graphics
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
        # Still paint the canvas: pyqtgraph sizes its axes from the text it
        # last drew, so skipping a state would change the ones after it.
        render_scene()
        return
    from PySide6.QtGui import QPainter
    shot = win.grab()
    img = render_scene()
    gv = win.accel_canvas._graphics
    origin = gv.mapTo(win, QtCore.QPoint(0, 0))
    painter = QPainter(shot)
    painter.drawImage(origin, img)
    painter.end()
    shot.save(str(out_dir / f"state_{name}_{theme_mode}.png"))
    win.side_scroll.widget().grab().save(str(out_dir / f"state_{name}_sidebar_{theme_mode}.png"))
    (out_dir / f"state_{name}_{theme_mode}.json").write_text(
        json.dumps({"layout": layout_dump(), "plot": plot_dump(), "status": win.statusBar().currentMessage(),
                    "cursor": win.cursor_label.text(), "title": win.windowTitle()}, indent=1),
        encoding="utf-8",
    )
    print("captured", name)


gauri = [str(FIX / "INDIA-GAURI_20261007_041500_02.fit.gz"), str(FIX / "INDIA-GAURI_20261007_043000_02.fit.gz")]

capture("empty")

win.load_fits_into_main(gauri[0])
capture("single")

win.load_combined_into_main(combine_compatible(gauri))
capture("combined_time")

win.background_method_combo.setCurrentIndex(0)
win.background_subtract_btn.click()
capture("bg_mean")

win.units_db_radio.setChecked(True)
capture("units_db")

win.time_ut_radio.setChecked(True)
capture("time_ut")

win.axis_log_radio.setChecked(True)
capture("axis_log")

# A project the full app saved (parity/make_project_reference.py), opened
# over the log axis: its spectrum, thresholds, colormap, Graph Properties,
# view, light curve and annotations.
project = REPO / "lite" / "parity" / "out" / "projects" / "processed.efaproj"
if project.exists():
    win.open_project_path(str(project))
    capture("project_processed")

win.close()
